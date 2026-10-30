"""Fleet registry v1. Authentication and transactions are enforced on every call."""

import hashlib
import hmac
import json
import secrets
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from services.registry.models import DesiredState, Enrollment, ReportedState, Robot
from services.registry.schemas import (
    DesiredOut,
    DesiredUpdate,
    Digest,
    EnrollmentOut,
    ErrorEnvelope,
    Heartbeat,
    RobotCreate,
    RobotList,
    RobotOut,
    RobotPatch,
)

router = APIRouter(
    prefix="/v1",
    responses={
        code: {"model": ErrorEnvelope, "description": description}
        for code, description in {
            401: "Missing or invalid credential",
            404: "Not found",
            409: "Request conflict",
            413: "Body exceeds limit",
            422: "Invalid request",
            500: "Internal server error",
        }.items()
    },
)
bearer = HTTPBearer(auto_error=False)
Credential = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]


def session_for(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        yield session


DB = Annotated[Session, Depends(session_for)]


def fail(status: int, code: str, message: str) -> None:
    raise HTTPException(status_code=status, detail={"code": code, "message": message})


def operator(request: Request, credential: Credential) -> None:
    configured = request.app.state.settings.operator_api_key
    if (
        configured is None
        or credential is None
        or not hmac.compare_digest(
            credential.credentials, configured.get_secret_value()
        )
    ):
        fail(401, "unauthorized", "Valid operator credential required")


def robot_auth(robot_id: UUID, credential: Credential, db: DB) -> None:
    token = credential.credentials if credential else ""
    digest = hashlib.sha256(token.encode()).hexdigest()
    enrollment = db.scalar(
        select(Enrollment).where(
            Enrollment.robot_id == robot_id, Enrollment.token_hash == digest
        )
    )
    if (
        not token
        or enrollment is None
        or enrollment.revoked_at is not None
        or not hmac.compare_digest(digest, enrollment.token_hash)
    ):
        fail(401, "unauthorized", "Valid robot credential required")


def get_robot(db: Session, robot_id: UUID) -> Robot:
    robot = db.get(Robot, robot_id)
    if robot is None:
        fail(404, "not_found", "Robot not found")
        raise AssertionError("unreachable")
    return robot


def utc(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def to_robot(db: Session, robot: Robot) -> RobotOut:
    reported = db.get(ReportedState, robot.id)
    desired = db.get(DesiredState, robot.id)
    assert reported is not None and desired is not None
    return RobotOut.model_validate(
        {
            "id": robot.id,
            "platform": robot.platform,
            "hardware_profile": robot.hardware_profile,
            "site": robot.site,
            "cohort_labels": robot.cohort_labels,
            "safe_update_window": robot.safe_update_window,
            "created_at": utc(robot.created_at),
            "updated_at": utc(robot.updated_at),
            "reported_state": {
                "current_digest": reported.current_digest,
                "previous_digest": reported.previous_digest,
                "agent_version": reported.agent_version,
                "last_seen": utc(reported.last_seen),
                "health_summary": reported.health_summary,
                "heartbeat_sequence": reported.heartbeat_sequence,
            },
            "desired_state": {
                "target_digest": desired.target_digest,
                "set_by": desired.set_by,
                "reason": desired.reason,
                "set_at": utc(desired.set_at),
            },
        }
    )


def desired_out(desired: DesiredState) -> DesiredOut:
    return DesiredOut(
        target_digest=desired.target_digest,
        set_by=desired.set_by,
        reason=desired.reason,
        set_at=utc(desired.set_at),
    )


@router.post(
    "/robots",
    response_model=EnrollmentOut,
    status_code=201,
    dependencies=[Depends(operator)],
    responses={200: {"model": EnrollmentOut, "description": "Idempotent replay"}},
)
def enroll(
    payload: RobotCreate,
    response: Response,
    db: DB,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> EnrollmentOut:
    fingerprint = hashlib.sha256(
        json.dumps(
            payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    if idempotency_key:
        existing = db.scalar(
            select(Robot).where(Robot.idempotency_key == idempotency_key)
        )
        if existing:
            if existing.enrollment_request_hash != fingerprint:
                fail(
                    409,
                    "idempotency_conflict",
                    "Idempotency key used for another request",
                )
            response.status_code = 200
            return EnrollmentOut(robot=to_robot(db, existing), credential=None)
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    robot = Robot(
        id=uuid4(),
        **payload.model_dump(mode="json"),
        idempotency_key=idempotency_key,
        enrollment_request_hash=fingerprint if idempotency_key else None,
        created_at=now,
        updated_at=now,
    )
    try:
        db.add(robot)
        db.flush()
        db.add(
            Enrollment(
                robot_id=robot.id,
                token_hash=hashlib.sha256(token.encode()).hexdigest(),
                issued_at=now,
            )
        )
        db.add(
            ReportedState(robot_id=robot.id, health_summary={}, heartbeat_sequence=0)
        )
        db.add(DesiredState(robot_id=robot.id))
        db.commit()
    except IntegrityError:
        db.rollback()
        if idempotency_key:
            existing = db.scalar(
                select(Robot).where(Robot.idempotency_key == idempotency_key)
            )
            if existing and existing.enrollment_request_hash == fingerprint:
                response.status_code = 200
                return EnrollmentOut(robot=to_robot(db, existing), credential=None)
            if existing:
                fail(
                    409,
                    "idempotency_conflict",
                    "Idempotency key used for another request",
                )
        raise
    return EnrollmentOut(robot=to_robot(db, robot), credential=token)


@router.get("/robots", response_model=RobotList, dependencies=[Depends(operator)])
def list_robots(
    db: DB,
    platform: str | None = None,
    site: str | None = None,
    cohort: str | None = None,
    version: Digest | None = None,
    health: str | None = None,
    cursor: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> RobotList:
    query = select(Robot).join(ReportedState, ReportedState.robot_id == Robot.id)
    if platform is not None:
        query = query.where(Robot.platform == platform)
    if site is not None:
        query = query.where(Robot.site == site)
    if version is not None:
        query = query.where(ReportedState.current_digest == version)
    if health is not None:
        query = query.where(
            ReportedState.health_summary["status"].as_string() == health
        )
    if cohort is not None:
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            from sqlalchemy import cast
            from sqlalchemy.dialects.postgresql import JSONB

            query = query.where(cast(Robot.cohort_labels, JSONB).contains([cohort]))
        else:
            from sqlalchemy import func

            labels = func.json_each(Robot.cohort_labels).table_valued("value")
            query = query.where(
                select(labels.c.value).where(labels.c.value == cohort).exists()
            )
    if cursor is not None:
        query = query.where(Robot.id > cursor)
    robots = list(db.scalars(query.order_by(Robot.id).limit(limit + 1)))
    return RobotList(
        items=[to_robot(db, r) for r in robots[:limit]],
        next_cursor=str(robots[limit - 1].id) if len(robots) > limit else None,
    )


@router.get(
    "/robots/{robot_id}", response_model=RobotOut, dependencies=[Depends(operator)]
)
def read_robot(robot_id: UUID, db: DB) -> RobotOut:
    return to_robot(db, get_robot(db, robot_id))


@router.patch(
    "/robots/{robot_id}", response_model=RobotOut, dependencies=[Depends(operator)]
)
def patch_robot(robot_id: UUID, payload: RobotPatch, db: DB) -> RobotOut:
    robot = get_robot(db, robot_id)
    changes = payload.model_dump(exclude_unset=True, mode="json")
    for field in ("platform", "site", "hardware_profile", "cohort_labels"):
        if field in changes and changes[field] is None:
            fail(422, "validation_error", f"{field} cannot be null")
    for field, value in changes.items():
        setattr(robot, field, value)
    if changes:
        robot.updated_at = datetime.now(UTC)
    db.commit()
    return to_robot(db, robot)


@router.post(
    "/robots/{robot_id}/heartbeat",
    response_model=RobotOut,
    dependencies=[Depends(robot_auth)],
)
def heartbeat(robot_id: UUID, payload: Heartbeat, db: DB) -> RobotOut:
    result = db.execute(
        update(ReportedState)
        .where(
            ReportedState.robot_id == robot_id,
            ReportedState.heartbeat_sequence < payload.sequence,
        )
        .values(
            heartbeat_sequence=payload.sequence,
            current_digest=payload.current_digest,
            previous_digest=payload.previous_digest,
            agent_version=payload.agent_version,
            health_summary=payload.health_summary,
            last_seen=datetime.now(UTC),
        )
    )
    if result.rowcount != 1:  # type: ignore[attr-defined]
        fail(409, "heartbeat_replay", "Heartbeat sequence must increase")
    db.commit()
    return to_robot(db, get_robot(db, robot_id))


@router.get(
    "/robots/{robot_id}/desired-state",
    response_model=DesiredOut,
    dependencies=[Depends(robot_auth)],
    responses={304: {"description": "Desired state unchanged"}},
)
def get_desired(
    robot_id: UUID,
    db: DB,
    response: Response,
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
) -> Any:
    desired = db.get(DesiredState, robot_id)
    if desired is None:
        fail(404, "not_found", "Desired state not found")
    assert desired is not None
    payload = desired_out(desired)
    tag = '"' + hashlib.sha256(payload.model_dump_json().encode()).hexdigest() + '"'
    headers = {"ETag": tag, "Cache-Control": "private, no-cache"}
    if if_none_match and (
        "*" in if_none_match.split(",")
        or tag in [x.strip().removeprefix("W/") for x in if_none_match.split(",")]
    ):
        return Response(status_code=304, headers=headers)
    response.headers.update(headers)
    return payload


@router.put(
    "/robots/{robot_id}/desired-state",
    response_model=DesiredOut,
    dependencies=[Depends(operator)],
)
def set_desired(robot_id: UUID, payload: DesiredUpdate, db: DB) -> DesiredOut:
    get_robot(db, robot_id)
    desired = db.get(DesiredState, robot_id)
    assert desired is not None
    desired.target_digest, desired.set_by, desired.reason, desired.set_at = (
        payload.target_digest,
        payload.set_by,
        payload.reason,
        datetime.now(UTC),
    )
    db.commit()
    return desired_out(desired)


@router.post(
    "/robots/{robot_id}/revoke", status_code=204, dependencies=[Depends(operator)]
)
def revoke(robot_id: UUID, db: DB) -> Response:
    get_robot(db, robot_id)
    db.execute(
        update(Enrollment)
        .where(Enrollment.robot_id == robot_id, Enrollment.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    db.commit()
    return Response(status_code=204)
