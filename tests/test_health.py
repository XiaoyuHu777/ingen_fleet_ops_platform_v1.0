"""Probe behavior and HTTP/log correlation without requiring Docker in unit CI."""

import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from time import perf_counter
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from services.registry.app import create_app
from services.registry.checks import LiveDependencyChecks
from services.registry.config import Settings
from services.registry.logging import JsonFormatter, request_id
from services.registry.probes import ProbeRunner


class FakeChecks:
    def __init__(
        self, database_down: bool = False, object_store_down: bool = False
    ) -> None:
        self.database_down = database_down
        self.object_store_down = object_store_down
        self.calls: list[str] = []

    def database(self) -> None:
        self.calls.append("database")
        if self.database_down:
            raise ConnectionError("test database unavailable")

    def object_store(self) -> None:
        self.calls.append("object_store")
        if self.object_store_down:
            raise ConnectionError("test object store unavailable")


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        postgres_db="test",
        postgres_user="test",
        postgres_password=SecretStr("not-a-real-credential"),
        minio_root_user="test-user",
        minio_root_password=SecretStr("not-a-real-credential"),
    )


def test_health_does_not_require_dependencies(settings: Settings) -> None:
    checks = FakeChecks(database_down=True, object_store_down=True)
    client = TestClient(create_app(settings, checks))
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert checks.calls == []


def test_readiness_checks_both_dependencies(settings: Settings) -> None:
    checks = FakeChecks()
    response = TestClient(create_app(settings, checks)).get("/readyz")
    assert response.status_code == 200
    assert response.json()["dependencies"] == {
        "database": "ok",
        "object_store": "ok",
    }
    assert sorted(checks.calls) == ["database", "object_store"]


@pytest.mark.parametrize("failed_dependency", ["database", "object_store", "both"])
def test_readiness_fails_without_exposing_errors(
    settings: Settings, failed_dependency: str
) -> None:
    checks = FakeChecks(
        database_down=failed_dependency in {"database", "both"},
        object_store_down=failed_dependency in {"object_store", "both"},
    )
    response = TestClient(create_app(settings, checks)).get("/readyz")
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert "test database unavailable" not in response.text
    assert "test object store unavailable" not in response.text
    assert sorted(checks.calls) == ["database", "object_store"]


def test_request_id_is_returned_and_logged(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        response = TestClient(create_app(settings, FakeChecks())).get(
            "/healthz", headers={"X-Request-ID": "exercise-request-123"}
        )
    assert response.headers["X-Request-ID"] == "exercise-request-123"
    assert "GET /healthz status=200" in caplog.text
    assert request_id.get() == "-"


def test_invalid_request_id_is_replaced(settings: Settings) -> None:
    response = TestClient(create_app(settings, FakeChecks())).get(
        "/healthz", headers={"X-Request-ID": "unsafe value"}
    )
    assert response.headers["X-Request-ID"] != "unsafe value"
    assert len(response.headers["X-Request-ID"]) == 36


def test_json_formatter_is_single_line_and_always_has_request_id() -> None:
    formatter = JsonFormatter()
    record = logging.LogRecord("test", logging.INFO, "", 0, "line 1\nline 2", (), None)
    formatted = formatter.format(record)
    assert "\n" not in formatted
    assert json.loads(formatted)["request-id"] == "-"
    token = request_id.set("correlation-123")
    try:
        assert json.loads(formatter.format(record))["request-id"] == "correlation-123"
    finally:
        request_id.reset(token)


def test_config_rejects_unbounded_dependency_timeout(settings: Settings) -> None:
    with pytest.raises(ValidationError):
        Settings(**{**settings.model_dump(), "dependency_timeout_seconds": 0})


def test_live_database_probe_executes_a_real_query_contract(settings: Settings) -> None:
    with (
        patch("services.registry.checks.Minio"),
        patch("services.registry.checks.psycopg.connect") as connect,
    ):
        connection = connect.return_value.__enter__.return_value
        connection.execute.return_value.fetchone.return_value = (1,)
        LiveDependencyChecks(settings).database()
        connection.execute.assert_called_once_with("SELECT 1")
        assert connect.call_args.kwargs["connect_timeout"] == 2
        assert connect.call_args.kwargs["options"] == "-c statement_timeout=2000"


def test_live_database_probe_rejects_unexpected_response(settings: Settings) -> None:
    with (
        patch("services.registry.checks.Minio"),
        patch("services.registry.checks.psycopg.connect") as connect,
    ):
        connection = connect.return_value.__enter__.return_value
        connection.execute.return_value.fetchone.return_value = (0,)
        with pytest.raises(RuntimeError, match="Unexpected"):
            LiveDependencyChecks(settings).database()


def test_live_object_store_probe_uses_authenticated_s3_api(settings: Settings) -> None:
    with patch("services.registry.checks.Minio") as minio:
        probes = LiveDependencyChecks(settings)
        probes.object_store()
        minio.return_value.list_buckets.assert_called_once_with()
        assert minio.call_args.kwargs["secure"] is False
        assert minio.call_args.kwargs["access_key"] == settings.minio_root_user


def test_unresponsive_probe_has_http_deadline_and_does_not_block_liveness(
    settings: Settings,
) -> None:
    started = Event()
    release = Event()

    class BlockingChecks:
        def database(self) -> None:
            started.set()
            release.wait()

        def object_store(self) -> None:
            pass

    settings.readiness_deadline_seconds = 0.1
    with TestClient(create_app(settings, BlockingChecks())) as client:
        with ThreadPoolExecutor(max_workers=1) as requester:
            before = perf_counter()
            future = requester.submit(client.get, "/readyz")
            try:
                assert started.wait(timeout=1)
                health_started = perf_counter()
                assert client.get("/healthz").status_code == 200
                assert perf_counter() - health_started < 0.5
                response = future.result(timeout=1)
                assert response.status_code == 503
                assert response.json()["dependencies"] == {
                    "database": "timeout",
                    "object_store": "ok",
                }
                assert perf_counter() - before < 0.5
                # The response completed while the blocking worker is still stuck.
                assert not release.is_set()
            finally:
                release.set()


def test_readiness_runs_dependency_probes_in_parallel(settings: Settings) -> None:
    barrier = Barrier(2, timeout=1)

    class ConcurrentChecks:
        def database(self) -> None:
            barrier.wait()

        def object_store(self) -> None:
            barrier.wait()

    with TestClient(create_app(settings, ConcurrentChecks())) as client:
        assert client.get("/readyz").status_code == 200


def test_cancelled_worker_retains_capacity_until_it_actually_finishes() -> None:
    started = Event()
    release = Event()
    runner = ProbeRunner(max_workers=1)

    def blocked_probe() -> None:
        started.set()
        release.wait()

    async def scenario() -> None:
        task = asyncio.create_task(runner.run(blocked_probe))
        try:
            assert await asyncio.to_thread(started.wait, 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            with pytest.raises(RuntimeError, match="capacity exhausted"):
                await runner.run(lambda: None)
        finally:
            release.set()
            runner.close()

    asyncio.run(scenario())


def test_closed_probe_runner_releases_capacity_after_submission_failure() -> None:
    runner = ProbeRunner(max_workers=1)
    runner.close()
    for _ in range(2):
        with pytest.raises(RuntimeError, match="shutdown"):
            asyncio.run(runner.run(lambda: None))


@pytest.mark.parametrize("deadline", [0.0, 5.0])
def test_config_rejects_invalid_overall_deadline(
    settings: Settings, deadline: float
) -> None:
    with pytest.raises(ValidationError):
        Settings(**{**settings.model_dump(), "readiness_deadline_seconds": deadline})
