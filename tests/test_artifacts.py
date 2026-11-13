"""Exercise signature failure modes, publication retries, lifecycle and resume."""

from collections.abc import Iterator
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker  # type: ignore[import-untyped]
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agent.artifacts import (
    ArtifactError,
    Benchmark,
    InputContract,
    Manifest,
    OutputContract,
    Parity,
    Provenance,
    archive,
    canonical_json,
    read_archive,
    sha256,
)
from agent.verify import BUNDLE_MEMBERS, load_trust, verify_bundle
from pipeline.sign import generate_key, sign_package
from services.registry.app import create_app
from services.registry.artifact_store import MEDIA_TYPE, StoreUnavailable
from services.registry.config import Settings
from services.registry.models import Base

OPERATOR = {"Authorization": "Bearer artifact-test-operator"}
UPLOAD = {**OPERATOR, "Content-Type": MEDIA_TYPE}


class MemoryStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.fail_write = False
        self.fail_read = False
        self.writes = 0

    def existing(self, digest: str) -> bytes | None:
        return self.objects.get(digest)

    def put(self, digest: str, data: bytes) -> None:
        if self.fail_write:
            raise StoreUnavailable("temporary storage error")
        assert digest not in self.objects
        self.objects[digest] = data
        self.writes += 1

    def read(self, digest: str, offset: int, length: int) -> bytes:
        if self.fail_read:
            raise StoreUnavailable("temporary storage error")
        return self.objects[digest][offset : offset + length]


def make_signed(
    tmp_path: Path, keys: Path, graph: bytes = b"test graph", version: str = "1.0.0"
) -> tuple[bytes, str]:
    package = tmp_path / uuid4().hex
    package.mkdir()
    payload = archive({"model.onnx": graph})
    graph_hash = sha256(graph)
    inputs = InputContract(
        name="pixel_values",
        shape=(1, 3, 16, 16),
        dtype="float32",
        layout="NCHW",
        color_order="RGB",
        resize="bilinear_half_pixel_clamp_no_antialias",
        uint8_scale=255,
        mean=(0.485, 0.456, 0.406),
        std=(0.229, 0.224, 0.225),
    )
    parity = Parity(
        fixture_sha256="a" * 64,
        fixture_count=5,
        tolerance=0.001,
        max_abs_error={"logits": 0.0001, "pred_boxes": 0.0001},
    )
    manifest = Manifest(
        format_version=1,
        name="test-detector",
        version=version,
        license="Apache-2.0",
        training_data=["test fixtures"],
        inputs=inputs,
        outputs=OutputContract(
            names=("logits", "pred_boxes"),
            logits_shape=(1, 1, 2),
            boxes_shape=(1, 1, 4),
            dtype="float32",
            box_format="normalized_cxcywh",
            class_map={"0": "object"},
            no_object_index=1,
            score_threshold=0.5,
        ),
        parity=parity,
        cpu_latency=Benchmark(
            model_sha256=graph_hash,
            fixture_sha256=parity.fixture_sha256,
            input_contract_sha256=sha256(canonical_json(inputs)),
            onnxruntime="test",
            host="test CPU",
            provider="CPUExecutionProvider",
            threads=1,
            warmups=1,
            runs=2,
            p50_ms=1.0,
            p95_ms=1.5,
        ),
        provenance=Provenance(
            git_commit="b" * 40,
            pipeline_version="1",
            opset=17,
            seed=2026,
            libraries={"onnxruntime": "test"},
            checkpoint_source="test",
            checkpoint_revision="c" * 40,
            checkpoint_sha256="d" * 64,
            model_config_sha256="e" * 64,
        ),
        files={"model.onnx": graph_hash},
        payload_sha256=sha256(payload),
    )
    (package / "manifest.json").write_bytes(canonical_json(manifest))
    (package / "payload.tar").write_bytes(payload)
    destination = package / "artifact.tar"
    digest = sign_package(package, keys / "signing.pem", destination)
    return destination.read_bytes(), digest


@pytest.fixture
def keys(tmp_path: Path) -> Path:
    directory = tmp_path / "keys"
    generate_key(directory)
    return directory


@pytest.fixture
def artifact_client(keys: Path) -> Iterator[tuple[TestClient, MemoryStore]]:
    settings = Settings(
        _env_file=None,
        postgres_db="test",
        postgres_user="test",
        postgres_password=SecretStr("test"),
        minio_root_user="test",
        minio_root_password=SecretStr("test"),
        operator_api_key=SecretStr("artifact-test-operator"),
        artifact_trust_dir=keys / "public",
    )
    store = MemoryStore()
    app = create_app(settings, artifact_store=store)
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    app.state.session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        with TestClient(app) as client:
            yield client, store
    finally:
        engine.dispose()


def test_verifier_rejects_tampering_unsigned_wrong_key_and_wrong_target(
    tmp_path: Path,
    keys: Path,
) -> None:
    signed, digest = make_signed(tmp_path, keys)
    trusted = load_trust(keys / "public")
    assert verify_bundle(signed, trusted, digest).digest == digest

    other = tmp_path / "other"
    generate_key(other)
    files = read_archive(signed, BUNDLE_MEMBERS)
    tampered = {
        **files,
        "payload.tar": files["payload.tar"].replace(b"test graph", b"test Graph"),
    }
    damaged_signature = files["signature.json"].replace(
        b'"signature":"', b'"signature":"00'
    )
    changed_manifest = files["manifest.json"].replace(
        b'"version":"1.0.0"', b'"version":"2.0.0"'
    )
    invalid_cases: list[tuple[bytes, dict[str, Ed25519PublicKey], str, str]] = [
        (signed, load_trust(other / "public"), digest, "wrong_key"),
        (signed, trusted, "0" * 64, "digest_mismatch"),
        (signed[:-1] + b"X", trusted, digest, "invalid_bundle"),
        (archive(tampered), trusted, digest, "payload_hash_mismatch"),
        (
            archive({**files, "manifest.json": changed_manifest}),
            trusted,
            sha256(changed_manifest),
            "invalid_signature",
        ),
        (
            archive({**files, "signature.json": damaged_signature}),
            trusted,
            digest,
            "invalid_signature",
        ),
        (
            archive(
                {key: value for key, value in files.items() if key != "signature.json"}
            ),
            trusted,
            digest,
            "unsigned",
        ),
    ]
    for data, trust, expected, code in invalid_cases:
        with pytest.raises(ArtifactError) as error:
            verify_bundle(data, trust, expected)
        assert error.value.code == code


def test_publish_replay_version_collision_filters_and_storage_recovery(
    tmp_path: Path, keys: Path, artifact_client: tuple[TestClient, MemoryStore]
) -> None:
    client, store = artifact_client
    body, digest = make_signed(tmp_path, keys)
    conflict, _ = make_signed(tmp_path, keys, b"different graph")
    second, second_digest = make_signed(tmp_path, keys, b"second graph", "1.1.0")
    third, third_digest = make_signed(tmp_path, keys, b"third graph", "1.2.0")

    assert client.post("/v1/artifacts", content=body).status_code == 401
    assert (
        client.post("/v1/artifacts", content=body, headers=OPERATOR).status_code == 415
    )
    assert (
        client.post(
            "/v1/artifacts",
            content=body,
            headers={**UPLOAD, "Content-Length": str(65 * 1024**2)},
        ).status_code
        == 413
    )
    assert (
        client.post(
            "/v1/artifacts", content=b"broken archive", headers=UPLOAD
        ).status_code
        == 422
    )
    store.fail_write = True
    assert client.post("/v1/artifacts", content=body, headers=UPLOAD).status_code == 503
    assert client.get("/v1/artifacts", headers=OPERATOR).json()["items"] == []
    store.fail_write = False

    created = client.post("/v1/artifacts", content=body, headers=UPLOAD)
    assert created.status_code == 201, created.text
    assert created.json()["digest"] == digest
    assert client.post("/v1/artifacts", content=body, headers=UPLOAD).status_code == 200
    assert (
        client.post("/v1/artifacts", content=conflict, headers=UPLOAD).status_code
        == 409
    )
    assert store.writes == 1

    store.objects[second_digest] = second
    assert (
        client.post("/v1/artifacts", content=second, headers=UPLOAD).status_code == 201
    )
    assert store.writes == 1
    store.objects[third_digest] = b"unverifiable orphan"
    result = client.post("/v1/artifacts", content=third, headers=UPLOAD)
    assert result.status_code == 409
    assert store.objects[third_digest] == b"unverifiable orphan"

    listing = client.get(
        "/v1/artifacts",
        params={"name": "test-detector", "state": "published", "limit": 1},
        headers=OPERATOR,
    ).json()
    assert [item["digest"] for item in listing["items"]] == [min(digest, second_digest)]
    page2 = client.get(
        "/v1/artifacts", params={"after": listing["next_after"]}, headers=OPERATOR
    ).json()
    assert [item["digest"] for item in page2["items"]] == [max(digest, second_digest)]
    assert page2["next_after"] is None
    assert (
        client.get(
            "/v1/artifacts", params={"version": "1.2.0"}, headers=OPERATOR
        ).json()["items"]
        == []
    )
    audit = client.get(f"/v1/artifacts/{digest}/audit", headers=OPERATOR)
    assert len(audit.json()["items"]) == 1


@pytest.mark.parametrize("range_value", ["bytes=-0", "bytes=20-10", "bytes=0-1,3-4"])
def test_invalid_range(
    tmp_path: Path,
    keys: Path,
    artifact_client: tuple[TestClient, MemoryStore],
    range_value: str,
) -> None:
    client, _ = artifact_client
    body, digest = make_signed(tmp_path, keys)
    assert client.post("/v1/artifacts", content=body, headers=UPLOAD).status_code == 201
    result = client.get(
        f"/v1/artifacts/{digest}/download", headers={**OPERATOR, "Range": range_value}
    )
    assert result.status_code == 416
    assert result.headers["Content-Range"] == f"bytes */{len(body)}"


def test_range_download_and_lifecycle(
    tmp_path: Path, keys: Path, artifact_client: tuple[TestClient, MemoryStore]
) -> None:
    client, store = artifact_client
    body, digest = make_signed(tmp_path, keys)
    publication = client.post("/v1/artifacts", content=body, headers=UPLOAD)
    assert publication.status_code == 201
    url = f"/v1/artifacts/{digest}"
    enrolled = client.post(
        "/v1/robots",
        headers={**OPERATOR, "Idempotency-Key": "artifact-test"},
        json={"platform": "Rover", "site": "lab", "hardware_profile": {}},
    ).json()
    robot = {"Authorization": f"Bearer {enrolled['credential']}"}
    assert client.get(url + "/download").status_code == 401
    assert client.get(url + "/download", headers=robot).status_code == 403
    assert (
        client.get("/v1/artifacts/" + "0" * 64 + "/audit", headers=OPERATOR).status_code
        == 404
    )

    def transition(current: str, target: str) -> int:
        result = client.patch(
            url + "/lifecycle",
            headers=OPERATOR,
            json={
                "expected_state": current,
                "state": target,
                "reason": "test evidence",
            },
        )
        if result.is_success:
            assert result.json()["state"] == target
        return int(result.status_code)

    assert transition("published", "approved") == 409
    assert transition("published", "evaluated") == 200
    assert transition("published", "evaluated") == 409
    assert transition("evaluated", "approved") == 200
    first = client.get(url + "/download", headers={**robot, "Range": "bytes=0-399"})
    assert first.status_code == 206
    assert first.headers["content-range"] == f"bytes 0-399/{len(body)}"
    second = client.get(
        url + "/download",
        headers={**robot, "Range": "bytes=400-", "If-Range": first.headers["etag"]},
    )
    assert second.status_code == 206 and first.content + second.content == body
    suffix = client.get(url + "/download", headers={**robot, "Range": "bytes=-64"})
    assert suffix.content == body[-64:]
    full = client.get(
        url + "/download", headers={**robot, "Range": "bytes=1-", "If-Range": '"old"'}
    )
    assert full.status_code == 200 and full.content == body
    store.fail_read = True
    assert client.get(url + "/download", headers=robot).status_code == 503
    store.fail_read = False
    robot_id = enrolled["robot"]["id"]
    assert (
        client.post(f"/v1/robots/{robot_id}/revoke", headers=OPERATOR).status_code
        == 204
    )
    assert client.get(url + "/download", headers=robot).status_code == 401
    assert transition("approved", "deprecated") == 200
    assert transition("deprecated", "revoked") == 200
    assert transition("revoked", "approved") == 409
    assert client.get(url + "/download", headers=OPERATOR).status_code == 410
    first_events = client.get(
        url + "/audit", params={"limit": 2}, headers=OPERATOR
    ).json()
    last_events = client.get(
        url + "/audit", params={"after": first_events["next_after"]}, headers=OPERATOR
    ).json()
    events = cast(list[dict[str, str]], first_events["items"] + last_events["items"])
    assert [event["to_state"] for event in events] == [
        "published",
        "evaluated",
        "approved",
        "deprecated",
        "revoked",
    ]
    assert all(
        event["actor"] == "operator" and event["occurred_at"].endswith("Z")
        for event in events
    )

    document = client.get("/openapi.json").json()
    collection = "/v1/artifacts"
    resource = collection + "/{digest}"
    for path, method, status, media_type, body_sample in (
        (collection, "post", "201", "application/json", publication.json()),
        (
            collection,
            "get",
            "200",
            "application/json",
            client.get(collection, headers=OPERATOR).json(),
        ),
        (
            resource + "/lifecycle",
            "patch",
            "200",
            "application/json",
            client.get(collection, headers=OPERATOR).json()["items"][0],
        ),
        (resource + "/audit", "get", "200", "application/json", last_events),
        (resource + "/download", "get", "206", MEDIA_TYPE, first.content),
    ):
        schema = document["paths"][path][method]["responses"][status]["content"][
            media_type
        ]["schema"]
        if media_type == MEDIA_TYPE:
            assert schema == {"type": "string", "format": "binary"}
            assert first.headers["Content-Type"] == media_type
        else:
            Draft202012Validator(
                {**schema, "components": document["components"]},
                format_checker=FormatChecker(),
            ).validate(body_sample)
