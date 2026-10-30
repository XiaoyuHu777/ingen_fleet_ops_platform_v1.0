"""End-to-end registry tests using a disposable database."""

import os
import subprocess
import sys
from collections.abc import Iterator
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from services.registry.app import create_app
from services.registry.config import Settings
from services.registry.models import Base, Enrollment


@pytest.fixture
def client() -> Iterator[TestClient]:
    settings = Settings(
        _env_file=None,
        postgres_db="test",
        postgres_user="test",
        postgres_password=SecretStr("test"),
        minio_root_user="test",
        minio_root_password=SecretStr("test"),
        operator_api_key=SecretStr("test-operator"),
    )
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    app = create_app(settings)
    app.state.session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with TestClient(app) as c:
        yield c
    engine.dispose()


OP = {"Authorization": "Bearer test-operator"}
BODY = {
    "platform": "Rover",
    "site": "east",
    "hardware_profile": {"cpu": "arm"},
    "cohort_labels": ["pilot"],
    "safe_update_window": {
        "start": "2026-11-01T00:00:00Z",
        "end": "2026-11-01T02:00:00Z",
    },
}
DIGEST = "sha256:" + "a" * 64


def enroll(c: TestClient) -> tuple[str, dict[str, str]]:
    r = c.post("/v1/robots", json=BODY, headers=OP)
    assert r.status_code == 201, r.text
    return r.json()["robot"]["id"], {
        "Authorization": "Bearer " + r.json()["credential"]
    }


def test_enrollment_idempotency_auth_and_hash(client: TestClient) -> None:
    assert client.post("/v1/robots", json=BODY).status_code == 401
    headers = {**OP, "Idempotency-Key": "key-1"}
    first = client.post("/v1/robots", json=BODY, headers=headers)
    assert first.status_code == 201, first.text
    token = first.json()["credential"]
    with cast(FastAPI, client.app).state.session_factory() as db:
        record = db.scalar(select(Enrollment))
        assert (
            record is not None
            and token != record.token_hash
            and len(record.token_hash) == 64
        )
    retry = client.post("/v1/robots", json=BODY, headers=headers)
    assert retry.status_code == 200 and retry.json()["credential"] is None
    assert retry.json()["robot"]["id"] == first.json()["robot"]["id"]
    assert (
        client.post(
            "/v1/robots", json={**BODY, "site": "other"}, headers=headers
        ).status_code
        == 409
    )
    assert (
        client.get(
            "/v1/robots", headers={"Authorization": f"Bearer {token}"}
        ).status_code
        == 401
    )


def test_heartbeat_etag_replay_and_revocation(client: TestClient) -> None:
    robot_id, agent = enroll(client)
    url = f"/v1/robots/{robot_id}"
    poll = client.get(url + "/desired-state", headers=agent)
    assert poll.status_code == 200, poll.text
    statements: list[str] = []
    engine = cast(FastAPI, client.app).state.session_factory.kw["bind"]

    def record(
        conn: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        statements.append(statement.lower())

    event.listen(engine, "before_cursor_execute", record)
    try:
        idle = client.get(
            url + "/desired-state",
            headers={**agent, "If-None-Match": poll.headers["etag"]},
        )
        assert idle.status_code == 304 and not idle.content
        assert all(
            not s.lstrip().startswith(("insert", "update", "delete"))
            for s in statements
        )
    finally:
        event.remove(engine, "before_cursor_execute", record)
    desired = client.put(
        url + "/desired-state",
        json={"target_digest": DIGEST, "set_by": "operator", "reason": "test"},
        headers=OP,
    )
    assert desired.status_code == 200, desired.text
    assert (
        client.get(
            url + "/desired-state",
            headers={**agent, "If-None-Match": poll.headers["etag"]},
        ).status_code
        == 200
    )
    heartbeat = {
        "sequence": 1,
        "agent_version": "0.1",
        "current_digest": DIGEST,
        "health_summary": {"status": "healthy"},
    }
    assert (
        client.post(url + "/heartbeat", json=heartbeat, headers=agent).status_code
        == 200
    )
    assert (
        client.post(url + "/heartbeat", json=heartbeat, headers=agent).status_code
        == 409
    )
    assert client.post(url + "/revoke", headers=OP).status_code == 204
    assert client.get(url + "/desired-state", headers=agent).status_code == 401
    assert (
        client.post(
            url + "/heartbeat", json={**heartbeat, "sequence": 2}, headers=agent
        ).status_code
        == 401
    )


def test_list_filter_page_patch_and_errors(client: TestClient) -> None:
    robot_id, agent = enroll(client)
    assert (
        client.post("/v1/robots", json={**BODY, "site": "west"}, headers=OP).status_code
        == 201
    )
    page = client.get("/v1/robots?limit=1", headers=OP).json()
    assert len(page["items"]) == 1 and page["next_cursor"]
    assert (
        len(
            client.get(
                "/v1/robots", params={"cursor": page["next_cursor"]}, headers=OP
            ).json()["items"]
        )
        == 1
    )
    assert (
        len(
            client.get(
                "/v1/robots?site=east&cohort=pilot&platform=Rover", headers=OP
            ).json()["items"]
        )
        == 1
    )
    assert len(client.get("/v1/robots?cohort=missing", headers=OP).json()["items"]) == 0
    url = f"/v1/robots/{robot_id}"
    assert (
        client.patch(url, headers=OP, json={"site": "north"}).json()["site"] == "north"
    )
    assert client.patch(url, headers=OP, json={"site": None}).status_code == 422
    response = client.get(
        "/v1/robots/not-a-uuid", headers={**OP, "X-Request-ID": "abc"}
    )
    assert (
        response.status_code == 422 and response.json()["error"]["request_id"] == "abc"
    )
    assert client.get(url, headers=agent).status_code == 401
    assert (
        client.post(url + "/heartbeat", headers=agent, content=b"x" * 2049).status_code
        == 413
    )
    assert client.patch(url, headers=OP, content=b"x" * 65537).status_code == 413
    assert (
        client.get(
            "/v1/robots/00000000-0000-0000-0000-000000000000", headers=OP
        ).status_code
        == 404
    )


def test_error_envelope_and_chunked_size_limit(client: TestClient) -> None:
    missing = client.get("/v1/missing", headers={"X-Request-ID": "test-404"})
    assert missing.status_code == 404
    assert missing.json()["error"]["request_id"] == "test-404"
    wrong_method = client.delete("/v1/robots", headers=OP)
    assert wrong_method.status_code == 405
    assert "error" in wrong_method.json()
    response = client.request(
        "GET", "/v1/robots", headers=OP, content=iter([b"x" * 33000, b"x" * 33000])
    )
    assert response.status_code == 413


def test_authentication_is_fail_closed(client: TestClient) -> None:
    app = cast(FastAPI, client.app)
    app.state.settings.operator_api_key = None
    assert client.get("/v1/robots", headers=OP).status_code == 401
    app.state.settings.operator_api_key = SecretStr("test-operator")
    robot_id, agent = enroll(client)
    assert (
        client.get(
            f"/v1/robots/{robot_id}/desired-state",
            headers={"Authorization": "Basic abc"},
        ).status_code
        == 401
    )
    assert (
        client.get(f"/v1/robots/{robot_id}/desired-state", headers=OP).status_code
        == 401
    )
    assert (
        client.get(f"/v1/robots/{robot_id}/desired-state", headers=agent).status_code
        == 200
    )


def test_contract_responses_from_openapi(client: TestClient) -> None:
    """Check every v1 operation against the schemas served by this application."""
    from jsonschema import Draft202012Validator, FormatChecker  # type: ignore[import-untyped]

    document = client.get("/openapi.json").json()
    seen: set[tuple[str, str]] = set()

    def check(path: str, method: str, response: object) -> None:
        from httpx import Response

        assert isinstance(response, Response)
        operation = document["paths"][path][method]
        seen.add((path, method))
        declaration = operation["responses"][str(response.status_code)]
        if "content" not in declaration:
            assert not response.content
            return
        schema = declaration["content"]["application/json"]["schema"]
        validator = Draft202012Validator(
            {**schema, "components": document["components"]},
            format_checker=FormatChecker(),
        )
        validator.validate(response.json())

    path = "/v1/robots"
    create = client.post(path, json=BODY, headers={**OP, "Idempotency-Key": "oas"})
    check(path, "post", create)
    check(
        path,
        "post",
        client.post(path, json=BODY, headers={**OP, "Idempotency-Key": "oas"}),
    )
    check(path, "get", client.get(path, headers=OP))
    check(path, "get", client.get(path))
    robot_id = create.json()["robot"]["id"]
    agent = {"Authorization": "Bearer " + create.json()["credential"]}
    item = "/v1/robots/{robot_id}"
    url = f"/v1/robots/{robot_id}"
    check(item, "get", client.get(url, headers=OP))
    check(item, "patch", client.patch(url, json={"site": "north"}, headers=OP))
    check(item, "patch", client.patch(url, json={"site": None}, headers=OP))
    desired_path = item + "/desired-state"
    desired_url = url + "/desired-state"
    check(
        desired_path,
        "put",
        client.put(
            desired_url,
            json={"target_digest": DIGEST, "set_by": "test", "reason": "test"},
            headers=OP,
        ),
    )
    desired = client.get(desired_url, headers=agent)
    check(desired_path, "get", desired)
    check(
        desired_path,
        "get",
        client.get(
            desired_url, headers={**agent, "If-None-Match": desired.headers["etag"]}
        ),
    )
    heartbeat_path = item + "/heartbeat"
    hb = {"sequence": 1, "agent_version": "1", "health_summary": {"status": "ok"}}
    check(
        heartbeat_path, "post", client.post(url + "/heartbeat", json=hb, headers=agent)
    )
    check(
        heartbeat_path, "post", client.post(url + "/heartbeat", json=hb, headers=agent)
    )
    check(item + "/revoke", "post", client.post(url + "/revoke", headers=OP))
    expected = {
        (p, m)
        for p, operations in document["paths"].items()
        if p.startswith("/v1/")
        for m in operations
        if m in {"get", "post", "patch", "put", "delete"}
    }
    assert seen == expected


@pytest.mark.parametrize(
    "window",
    [
        {"start": "2026-11-01T00:00:00", "end": "2026-11-01T02:00:00Z"},
        {"start": "2026-11-01T02:00:00Z", "end": "2026-11-01T02:00:00Z"},
        {"start": "2026-11-01T03:00:00Z", "end": "2026-11-01T02:00:00Z"},
        {"start": 1, "end": 2},
    ],
)
def test_invalid_safe_update_windows(
    client: TestClient, window: dict[str, object]
) -> None:
    assert (
        client.post(
            "/v1/robots", json={**BODY, "safe_update_window": window}, headers=OP
        ).status_code
        == 422
    )


def test_safe_update_window_normalizes_offsets(client: TestClient) -> None:
    window = {"start": "2026-11-01T02:00:00+02:00", "end": "2026-11-01T04:00:00+02:00"}
    response = client.post(
        "/v1/robots", json={**BODY, "safe_update_window": window}, headers=OP
    )
    assert response.status_code == 201
    assert response.json()["robot"]["safe_update_window"] == BODY["safe_update_window"]
    robot_id = response.json()["robot"]["id"]
    assert (
        client.patch(
            f"/v1/robots/{robot_id}", json={"safe_update_window": None}, headers=OP
        ).json()["safe_update_window"]
        is None
    )


@pytest.mark.parametrize(
    "window",
    [
        {"start_local": "03:00", "end_local": "03:30", "timezone": "UTC"},
        {
            "start_local": "22:00",
            "end_local": "06:00",
            "timezone": "America/New_York",
        },
    ],
)
def test_daily_window_round_trip_and_patch(
    client: TestClient, window: dict[str, str]
) -> None:
    created = client.post(
        "/v1/robots", json={**BODY, "safe_update_window": window}, headers=OP
    )
    assert created.status_code == 201, created.text
    robot = created.json()["robot"]
    assert robot["safe_update_window"] == window
    response = client.get(f"/v1/robots/{robot['id']}", headers=OP)
    assert response.json()["safe_update_window"] == window
    absolute = {"start": "2027-01-01T00:00:00Z", "end": "2027-01-01T01:00:00Z"}
    patch = client.patch(
        f"/v1/robots/{robot['id']}",
        json={"safe_update_window": absolute},
        headers=OP,
    )
    assert patch.status_code == 200
    assert patch.json()["safe_update_window"] == absolute


@pytest.mark.parametrize(
    "window",
    [
        {"start_local": "03:00", "end_local": "03:00", "timezone": "UTC"},
        {"start_local": "24:00", "end_local": "01:00", "timezone": "UTC"},
        {"start_local": "3:00", "end_local": "04:00", "timezone": "UTC"},
        {"start_local": "03:00", "end_local": "04:00", "timezone": "Mars/Olympus"},
        {"start_local": "03:00", "end_local": "04:00", "timezone": "localtime"},
        {"start_local": "03:00", "end_local": "04:00", "timezone": "../UTC"},
        {"start_local": "03:00", "end_local": "04:00"},
    ],
)
def test_invalid_daily_windows(client: TestClient, window: dict[str, str]) -> None:
    result = client.post(
        "/v1/robots", json={**BODY, "safe_update_window": window}, headers=OP
    )
    assert result.status_code == 422


def test_timezone_database_fallback_without_system_zoneinfo() -> None:
    # A slim runtime may not contain /usr/share/zoneinfo. Ensure the pinned
    # tzdata package supplies IANA data independently of host configuration.
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from zoneinfo import ZoneInfo; assert ZoneInfo('America/New_York')",
        ],
        env={**os.environ, "PYTHONTZPATH": ""},
        check=True,
    )
