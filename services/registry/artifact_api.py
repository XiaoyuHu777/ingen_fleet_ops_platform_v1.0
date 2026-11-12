"""Publish and serve signed bundles and track their deployment lifecycle."""

import re
from datetime import UTC, datetime
from typing import Annotated, Literal

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
)
from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from agent.artifacts import (
    MAX_BUNDLE_BYTES,
    ArtifactError,
    HexDigest,
    ModelName,
    SemVer,
    sha256,
)
from agent.verify import load_trust, verify_bundle
from services.registry.api_v1 import DB, Credential, fail, is_operator, operator
from services.registry.artifact_store import MEDIA_TYPE, ArtifactStore, StoreUnavailable
from services.registry.models import Artifact, ArtifactAudit, Enrollment
from services.registry.schemas import ErrorEnvelope

State = Literal["published", "evaluated", "approved", "deprecated", "revoked"]
ArtifactDigest = Annotated[HexDigest, Path()]
TRANSITIONS = {
    "published": {"evaluated", "revoked"},
    "evaluated": {"approved", "revoked"},
    "approved": {"deprecated", "revoked"},
    "deprecated": {"revoked"},
    "revoked": set(),
}
operator_only = [Depends(operator)]

router = APIRouter(
    prefix="/v1/artifacts",
    tags=["artifacts"],
    responses={
        status: {"model": ErrorEnvelope, "description": description}
        for status, description in (
            (401, "Valid credential required"),
            (404, "Artifact not found"),
            (409, "Artifact conflict"),
            (422, "Invalid request or bundle"),
            (503, "Artifact storage or trust unavailable"),
        )
    },
)


class ArtifactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    digest: HexDigest
    name: ModelName
    version: SemVer
    signing_key_id: HexDigest
    bundle_sha256: HexDigest
    size_bytes: int
    state: State
    published_at: datetime

    @field_validator("published_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        return (
            value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        )


class ArtifactList(BaseModel):
    items: list[ArtifactOut]
    next_after: HexDigest | None = None


class LifecycleChange(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    expected_state: State
    state: State
    reason: Annotated[str, StringConstraints(min_length=1, max_length=1000)]


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    artifact_digest: HexDigest
    from_state: State | None
    to_state: State
    actor: str
    reason: str
    occurred_at: datetime

    @field_validator("occurred_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        return (
            value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        )


class AuditList(BaseModel):
    items: list[AuditOut]
    next_after: int | None = None


def find_artifact(db: Session, digest: str) -> Artifact:
    row = db.get(Artifact, digest)
    if row is None:
        fail(404, "artifact_not_found", "Artifact not found")
    return row


def verified_publication(
    db: Session,
    store: ArtifactStore,
    trusted: dict[str, Ed25519PublicKey],
    bundle: bytes,
) -> tuple[ArtifactOut, bool]:
    try:
        verified = verify_bundle(bundle, trusted)
    except ArtifactError as error:
        fail(422, error.code, str(error))
    existing = db.get(Artifact, verified.digest)
    if existing is not None:
        return ArtifactOut.model_validate(existing), False

    row = Artifact(
        digest=verified.digest,
        name=verified.manifest.name,
        version=verified.manifest.version,
        signing_key_id=verified.key_id,
        bundle_sha256=sha256(bundle),
        size_bytes=len(bundle),
        state="published",
    )
    db.add(row)
    try:
        # Reserve this digest and name/version before touching immutable storage.
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = db.get(Artifact, verified.digest)
        if existing is not None:
            return ArtifactOut.model_validate(existing), False
        fail(
            409,
            "version_conflict",
            "Model name and version already bind another digest",
        )

    try:
        recovered = store.existing(row.digest)
        if recovered is None:
            store.put(row.digest, bundle)
        else:
            # A successful object write can survive a rolled-back database transaction.
            original = verify_bundle(recovered, trusted, row.digest)
            row.signing_key_id = original.key_id
            row.bundle_sha256 = sha256(recovered)
            row.size_bytes = len(recovered)
    except StoreUnavailable:
        fail(503, "object_store_unavailable", "Artifact storage is unavailable")
    except ArtifactError:
        fail(
            409,
            "storage_conflict",
            "Existing object failed verification and was preserved",
        )

    db.add(
        ArtifactAudit(
            artifact_digest=row.digest,
            to_state="published",
            actor="operator",
            reason="Published a verified signed bundle",
        )
    )
    db.commit()
    return ArtifactOut.model_validate(row), True


@router.post(
    "",
    dependencies=operator_only,
    status_code=201,
    responses={
        200: {"model": ArtifactOut, "description": "Existing artifact"},
        413: {"model": ErrorEnvelope, "description": "Bundle exceeds upload limit"},
        415: {"model": ErrorEnvelope, "description": "Unsupported content type"},
    },
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {MEDIA_TYPE: {"schema": {"type": "string", "format": "binary"}}},
        }
    },
)
async def publish_artifact(request: Request, response: Response, db: DB) -> ArtifactOut:
    if (
        request.headers.get("Content-Type", "").partition(";")[0].strip().lower()
        != MEDIA_TYPE
    ):
        fail(415, "unsupported_media_type", f"Content-Type must be {MEDIA_TYPE}")
    length = request.headers.get("Content-Length")
    if length is not None and length.isascii() and length.isdecimal():
        if len(length) > 10 or int(length) > MAX_BUNDLE_BYTES:
            fail(413, "body_too_large", "Bundle exceeds the 64 MiB upload limit")

    directory = request.app.state.settings.artifact_trust_dir
    try:
        trusted = await run_in_threadpool(load_trust, directory) if directory else {}
    except (ValueError, OSError):
        fail(503, "trust_unavailable", "Artifact trust directory could not be loaded")
    if not trusted:
        fail(503, "trust_unavailable", "No artifact signing keys are trusted")

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_BUNDLE_BYTES:
            fail(413, "body_too_large", "Bundle exceeds the 64 MiB upload limit")
        body.extend(chunk)
    result, created = await run_in_threadpool(
        verified_publication, db, request.app.state.artifact_store, trusted, bytes(body)
    )
    response.status_code = 201 if created else 200
    return result


@router.get("", dependencies=operator_only)
def list_artifacts(
    db: DB,
    name: Annotated[ModelName | None, Query()] = None,
    version: Annotated[SemVer | None, Query()] = None,
    state: State | None = None,
    after: Annotated[HexDigest | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ArtifactList:
    query = select(Artifact).order_by(Artifact.digest).limit(limit + 1)
    for column, value in (
        (Artifact.name, name),
        (Artifact.version, version),
        (Artifact.state, state),
    ):
        if value is not None:
            query = query.where(column == value)
    if after is not None:
        query = query.where(Artifact.digest > after)
    items = db.scalars(query).all()
    return ArtifactList(
        items=[ArtifactOut.model_validate(row) for row in items[:limit]],
        next_after=items[limit - 1].digest if len(items) > limit else None,
    )


@router.patch("/{digest}/lifecycle", dependencies=operator_only)
def change_lifecycle(
    digest: ArtifactDigest, change: LifecycleChange, db: DB
) -> ArtifactOut:
    row = find_artifact(db, digest)
    if (
        row.state != change.expected_state
        or change.state not in TRANSITIONS[change.expected_state]
    ):
        fail(
            409,
            "invalid_transition",
            "Current state or lifecycle transition is invalid",
        )
    changed = db.execute(
        update(Artifact)
        .where(Artifact.digest == digest, Artifact.state == change.expected_state)
        .values(state=change.state)
        .returning(Artifact.digest)
    ).scalar_one_or_none()
    if changed is None:
        fail(409, "invalid_transition", "Artifact state changed concurrently")
    db.add(
        ArtifactAudit(
            artifact_digest=digest,
            from_state=change.expected_state,
            to_state=change.state,
            actor="operator",
            reason=change.reason,
        )
    )
    db.commit()
    return ArtifactOut.model_validate(row)


@router.get("/{digest}/audit", dependencies=operator_only)
def artifact_audit(
    digest: ArtifactDigest,
    db: DB,
    after: Annotated[int, Query(ge=0, le=2_147_483_647)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> AuditList:
    find_artifact(db, digest)
    events = db.scalars(
        select(ArtifactAudit)
        .where(ArtifactAudit.artifact_digest == digest, ArtifactAudit.id > after)
        .order_by(ArtifactAudit.id)
        .limit(limit + 1)
    ).all()
    return AuditList(
        items=[AuditOut.model_validate(event) for event in events[:limit]],
        next_after=events[limit - 1].id if len(events) > limit else None,
    )


def byte_range(value: str | None, size: int) -> tuple[int, int] | None:
    if value is None or value.partition("=")[0].lower() != "bytes":
        return None
    match = re.fullmatch(r"bytes=([0-9]{0,20})-([0-9]{0,20})", value, re.IGNORECASE)
    if match is None:
        raise ValueError("Expected a single byte range")
    first, last = match.groups()
    if not first:
        if not last or int(last) == 0:
            raise ValueError("Empty suffix range")
        return max(0, size - int(last)), size - 1
    start = int(first)
    end = min(int(last), size - 1) if last else size - 1
    if start > end:
        raise ValueError("Unsatisfiable byte range")
    return start, end


@router.get(
    "/{digest}/download",
    response_class=Response,
    responses={
        200: {
            "content": {MEDIA_TYPE: {"schema": {"type": "string", "format": "binary"}}}
        },
        206: {
            "description": "Partial content",
            "content": {MEDIA_TYPE: {"schema": {"type": "string", "format": "binary"}}},
        },
        403: {"model": ErrorEnvelope, "description": "Artifact unavailable to robots"},
        410: {"model": ErrorEnvelope, "description": "Artifact revoked"},
        416: {
            "model": ErrorEnvelope,
            "description": "Requested range cannot be served",
        },
    },
)
def download_artifact(
    digest: ArtifactDigest,
    request: Request,
    credential: Credential,
    db: DB,
    requested: Annotated[str | None, Header(alias="Range")] = None,
    if_range: Annotated[str | None, Header()] = None,
) -> Response:
    privileged = is_operator(request, credential)
    if not privileged:
        token = credential.credentials if credential else ""
        enrollment = db.get(Enrollment, sha256(token.encode())) if token else None
        if enrollment is None or enrollment.revoked_at is not None:
            fail(401, "unauthorized", "Valid operator or robot credential required")
    row = find_artifact(db, digest)
    if row.state == "revoked":
        fail(410, "artifact_revoked", "Artifact has been revoked")
    if not privileged and row.state != "approved":
        fail(
            403, "artifact_not_approved", "Robots can download only approved artifacts"
        )

    etag = f'"{row.bundle_sha256}"'
    headers = {
        "Accept-Ranges": "bytes",
        "ETag": etag,
        "Cache-Control": "private, no-store",
    }
    try:
        interval = (
            byte_range(requested, row.size_bytes) if if_range in (None, etag) else None
        )
    except ValueError as error:
        raise HTTPException(
            status_code=416,
            detail={"code": "invalid_range", "message": str(error)},
            headers={**headers, "Content-Range": f"bytes */{row.size_bytes}"},
        ) from error
    start, end = interval or (0, row.size_bytes - 1)
    store: ArtifactStore = request.app.state.artifact_store
    try:
        data = store.read(row.digest, start, end - start + 1)
    except StoreUnavailable:
        fail(503, "object_store_unavailable", "Artifact storage is unavailable")
    if interval is not None:
        headers["Content-Range"] = f"bytes {start}-{end}/{row.size_bytes}"
    return Response(
        data,
        status_code=206 if interval is not None else 200,
        headers=headers,
        media_type=MEDIA_TYPE,
    )
