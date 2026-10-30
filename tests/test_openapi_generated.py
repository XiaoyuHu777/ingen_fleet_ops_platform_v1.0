"""Generate negative contract cases from the live OpenAPI document.

These deterministic cases cover missing credentials and required object fields.
They are not property-based fuzzing or a replacement for semantic integration tests.
"""

from collections.abc import Iterator
from functools import lru_cache
from typing import Any

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker  # type: ignore[import-untyped]
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from services.registry.app import create_app
from services.registry.config import Settings
from services.registry.models import Base

OPERATOR = {"Authorization": "Bearer test-operator"}
ROBOT = {"platform": "Rover", "site": "test", "hardware_profile": {}}


def settings() -> Settings:
    return Settings(
        _env_file=None,
        postgres_db="test",
        postgres_user="test",
        postgres_password=SecretStr("test"),
        minio_root_user="test",
        minio_root_password=SecretStr("test"),
        operator_api_key=SecretStr("test-operator"),
    )


@lru_cache(maxsize=1)
def openapi() -> dict[str, Any]:
    return create_app(settings()).openapi()


def resolve(schema: dict[str, Any]) -> dict[str, Any]:
    if "$ref" not in schema:
        return schema
    ref = schema["$ref"]
    prefix = "#/components/schemas/"
    if not ref.startswith(prefix):
        raise ValueError(f"Unsupported schema reference: {ref}")
    return dict(openapi()["components"]["schemas"][ref[len(prefix) :]])


def sample(schema: dict[str, Any]) -> Any:
    """Produce a minimal valid value for the current required field schemas.

    Fail loudly for unsupported future schemas instead of silently omitting tests.
    """
    schema = resolve(schema)
    if "anyOf" in schema:
        alternatives = schema["anyOf"]
        null = next((item for item in alternatives if item.get("type") == "null"), None)
        return None if null is not None else sample(alternatives[0])
    kind = schema.get("type")
    if kind == "object":
        props = schema.get("properties", {})
        return {name: sample(props[name]) for name in schema.get("required", [])}
    if kind == "string":
        if schema.get("format") == "date-time":
            return "2026-11-01T00:00:00Z"
        if schema.get("pattern"):
            raise ValueError(f"No generated sample for pattern {schema['pattern']}")
        return "x" * max(1, schema.get("minLength", 1))
    if kind == "integer":
        return max(1, schema.get("minimum", 1))
    if kind == "boolean":
        return False
    if kind == "array":
        return []
    if kind == "null":
        return None
    raise ValueError(f"No generated sample for schema: {schema}")


def operations() -> list[tuple[str, str]]:
    return [
        (path, method)
        for path, methods in openapi()["paths"].items()
        if path.startswith("/v1/")
        for method in methods
        if method in {"get", "post", "put", "patch", "delete"}
    ]


def required_cases() -> list[tuple[str, str, str]]:
    cases: list[tuple[str, str, str]] = []
    for path, method in operations():
        operation = openapi()["paths"][path][method]
        body = operation.get("requestBody", {})
        if not body:
            continue
        schema = resolve(body["content"]["application/json"]["schema"])
        cases.extend((path, method, name) for name in schema.get("required", []))
    return cases


@pytest.fixture
def client() -> Iterator[TestClient]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    app = create_app(settings())
    app.state.session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with TestClient(app) as test_client:
        yield test_client
    engine.dispose()


def validate_response(path: str, method: str, response: Any) -> None:
    document = openapi()
    declaration = document["paths"][path][method]["responses"][
        str(response.status_code)
    ]
    schema = declaration["content"]["application/json"]["schema"]
    Draft202012Validator(
        {**schema, "components": document["components"]}, format_checker=FormatChecker()
    ).validate(response.json())
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]


def path_for(path: str, robot_id: str) -> str:
    path = path.replace("{robot_id}", robot_id)
    if "{" in path:
        raise AssertionError(f"Add a generated example for new path parameter: {path}")
    return path


@pytest.mark.parametrize("path,method", operations(), ids=lambda value: str(value))
def test_generated_missing_auth(client: TestClient, path: str, method: str) -> None:
    # A syntactically valid UUID lets the request reach authentication.
    url = path_for(path, "00000000-0000-4000-8000-000000000001")
    response = client.request(method, url, json={})
    assert response.status_code == 401, response.text
    validate_response(path, method, response)


@pytest.mark.parametrize("path,method,field", required_cases())
def test_generated_missing_required_field(
    client: TestClient, path: str, method: str, field: str
) -> None:
    operation = openapi()["paths"][path][method]
    schema = resolve(operation["requestBody"]["content"]["application/json"]["schema"])
    body = sample(schema)
    del body[field]
    created = client.post("/v1/robots", json=ROBOT, headers=OPERATOR)
    assert created.status_code == 201, created.text
    robot_id = created.json()["robot"]["id"]
    agent = {"Authorization": "Bearer " + created.json()["credential"]}
    url = path_for(path, robot_id)
    # OpenAPI describes both principals as HTTPBearer. Try each and require one
    # to reach validation; the other must be rejected rather than gaining access.
    responses = [
        client.request(method, url, json=body, headers=h) for h in (OPERATOR, agent)
    ]
    assert sorted(response.status_code for response in responses) == [401, 422]
    for response in responses:
        validate_response(path, method, response)
