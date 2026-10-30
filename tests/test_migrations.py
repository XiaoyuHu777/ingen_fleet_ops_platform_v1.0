"""Exercise Alembic and preserve pre-existing tables in an isolated database/schema."""

import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import sessionmaker

from services.registry.app import create_app
from services.registry.config import Settings
from services.registry.models import Base


def config_for(connection: Connection) -> Config:
    config = Config("alembic.ini")
    config.attributes["connection"] = connection
    return config


def exercise_migration(connection: Connection) -> None:
    connection.execute(text("CREATE TABLE existing_data (id integer PRIMARY KEY)"))
    connection.execute(text("INSERT INTO existing_data VALUES (42)"))
    connection.commit()
    config = config_for(connection)
    command.upgrade(config, "head")
    inspector = inspect(connection)
    assert {"robots", "enrollments", "reported_states", "desired_states"} <= set(
        inspector.get_table_names()
    )
    assert "ix_reported_states_last_seen" in {
        index["name"] for index in inspector.get_indexes("reported_states")
    }
    # Keep metadata and actual migration definitions in sync.
    diffs = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    assert not [diff for diff in diffs if diff[0] != "remove_table"]
    assert connection.execute(text("SELECT id FROM existing_data")).scalar_one() == 42
    connection.commit()
    command.downgrade(config, "base")
    assert "robots" not in inspect(connection).get_table_names()
    assert connection.execute(text("SELECT id FROM existing_data")).scalar_one() == 42
    connection.commit()


def test_upgrade_and_downgrade_preserve_unrelated_data() -> None:
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as connection:
        exercise_migration(connection)
    engine.dispose()


@pytest.fixture
def postgres_connection() -> Iterator[Connection]:
    url = os.environ.get("REGISTRY_TEST_DATABASE_URL")
    if not url:
        pytest.skip("REGISTRY_TEST_DATABASE_URL is not set")
    engine = create_engine(url)
    # Only the unique throwaway schema is dropped; never reset the target database.
    schema = f"registry_test_{uuid4().hex}"
    with engine.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.commit()
        try:
            connection.execute(text(f'SET search_path TO "{schema}"'))
            connection.commit()
            yield connection
        finally:
            connection.rollback()
            connection.execute(text("SET search_path TO public"))
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            connection.commit()
    engine.dispose()


def test_postgres_migration_and_last_seen_index(
    postgres_connection: Connection,
) -> None:
    config = config_for(postgres_connection)
    command.upgrade(config, "head")
    diffs = compare_metadata(
        MigrationContext.configure(postgres_connection), Base.metadata
    )
    assert not diffs
    # Prove that the index can satisfy the range predicate; this is not a latency
    # benchmark or a claim that PostgreSQL will choose it on an empty table.
    postgres_connection.execute(text("SET LOCAL enable_seqscan = off"))
    plan = postgres_connection.execute(
        text(
            "EXPLAIN SELECT * FROM reported_states "
            "WHERE last_seen < now() - interval '10 minutes'"
        )
    ).scalars()
    plan_text = "\n".join(plan)
    assert "ix_reported_states_last_seen" in plan_text
    output = os.environ.get("REGISTRY_EXPLAIN_OUTPUT")
    if output:
        from pathlib import Path

        Path(output).write_text(plan_text + "\n")
    postgres_connection.rollback()
    command.downgrade(config, "base")


def test_postgres_api_round_trip(postgres_connection: Connection) -> None:
    command.upgrade(config_for(postgres_connection), "head")
    settings = Settings(
        _env_file=None,
        postgres_db="test",
        postgres_user="test",
        postgres_password=SecretStr("test"),
        minio_root_user="test",
        minio_root_password=SecretStr("test"),
        operator_api_key=SecretStr("test-operator"),
    )
    app = create_app(settings)
    app.state.session_factory = sessionmaker(
        bind=postgres_connection, expire_on_commit=False
    )
    operator = {"Authorization": "Bearer test-operator", "Idempotency-Key": "pg-test"}
    body = {
        "platform": "Rover",
        "site": "east",
        "hardware_profile": {"cpu": "arm"},
        "cohort_labels": ["pilot"],
    }
    digest = "sha256:" + "a" * 64
    with TestClient(app) as client:
        created = client.post("/v1/robots", json=body, headers=operator)
        assert created.status_code == 201, created.text
        robot_id = created.json()["robot"]["id"]
        token = created.json()["credential"]
        agent = {"Authorization": f"Bearer {token}"}
        assert client.post("/v1/robots", json=body, headers=operator).status_code == 200
        url = f"/v1/robots/{robot_id}"
        heartbeat = {
            "sequence": 1,
            "agent_version": "1.0",
            "current_digest": digest,
            "health_summary": {"status": "healthy"},
        }
        result = client.post(url + "/heartbeat", json=heartbeat, headers=agent)
        assert result.status_code == 200, result.text
        assert (
            client.post(url + "/heartbeat", json=heartbeat, headers=agent).status_code
            == 409
        )
        listing = client.get(
            "/v1/robots",
            params={
                "site": "east",
                "cohort": "pilot",
                "version": digest,
                "health": "healthy",
            },
            headers=operator,
        )
        assert listing.status_code == 200, listing.text
        assert [item["id"] for item in listing.json()["items"]] == [robot_id]
        desired = client.put(
            url + "/desired-state",
            headers=operator,
            json={"target_digest": digest, "set_by": "operator", "reason": "test"},
        )
        assert desired.status_code == 200, desired.text
        poll = client.get(url + "/desired-state", headers=agent)
        assert poll.status_code == 200 and poll.json()["target_digest"] == digest
        assert (
            client.get(
                url + "/desired-state",
                headers={**agent, "If-None-Match": poll.headers["etag"]},
            ).status_code
            == 304
        )
        assert client.post(url + "/revoke", headers=operator).status_code == 204
        assert client.get(url + "/desired-state", headers=agent).status_code == 401
