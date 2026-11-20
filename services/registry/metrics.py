"""Prometheus fleet gauges from accepted heartbeats and enrolled robot inventory."""

import hmac
from collections import Counter
from datetime import UTC, datetime

from fastapi import APIRouter, Request, Response
from sqlalchemy import literal, select

from services.registry.api_v1 import DB, Credential, fail, utc
from services.registry.models import Artifact, ReportedState, Robot

router = APIRouter(include_in_schema=False)

AGE = "ingen_registry_robot_heartbeat_age_seconds"
RECEIVED = "ingen_registry_robot_heartbeat_received"
VERSIONS = "ingen_registry_active_versions"


def _labels(**values: str) -> str:
    labels = []
    for name, value in values.items():
        escaped = value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')
        labels.append(f'{name}="{escaped}"')
    return "{" + ",".join(labels) + "}"


@router.get("/metrics")
def metrics(request: Request, credential: Credential, db: DB) -> Response:
    token: bytes = request.app.state.metrics_token
    if not token:
        fail(404, "not_found", "Metrics are disabled")
    if not credential or not hmac.compare_digest(
        credential.credentials.encode(), token
    ):
        fail(401, "unauthorized", "Valid metrics credential required")

    # One database snapshot serves all three gauge families, including inactive
    # agents and Registry restarts. Only accepted heartbeats update last_seen.
    rows = db.execute(
        select(
            Robot.id,
            Robot.site,
            ReportedState.last_seen,
            ReportedState.current_digest,
            Artifact.name,
            Artifact.version,
        )
        .outerjoin(ReportedState, ReportedState.robot_id == Robot.id)
        .outerjoin(
            Artifact,
            ReportedState.current_digest == literal("sha256:") + Artifact.digest,
        )
        .order_by(Robot.id)
    ).all()
    now = datetime.now(UTC)
    ages = [
        f"# HELP {AGE} Seconds since the last accepted heartbeat.",
        f"# TYPE {AGE} gauge",
    ]
    received = [
        f"# HELP {RECEIVED} Whether a robot has sent an accepted heartbeat.",
        f"# TYPE {RECEIVED} gauge",
    ]
    versions = [
        f"# HELP {VERSIONS} Registered robots by site and last reported artifact.",
        f"# TYPE {VERSIONS} gauge",
    ]
    counts: Counter[tuple[str, str, str, str]] = Counter()
    for robot_id, site, last_seen, digest, model, version in rows:
        labels = _labels(robot_id=str(robot_id), site=site)
        last_seen = utc(last_seen)
        if last_seen is not None:
            ages.append(
                f"{AGE}{labels} {max(0, (now - last_seen).total_seconds()):.3f}"
            )
        received.append(f"{RECEIVED}{labels} {int(last_seen is not None)}")
        counts[
            site, digest or "unassigned", model or "unknown", version or "unknown"
        ] += 1
    for (site, digest, model, version), count in sorted(counts.items()):
        labels = _labels(site=site, digest=digest, model=model, version=version)
        versions.append(f"{VERSIONS}{labels} {count}")

    return Response(
        "\n".join(ages + received + versions) + "\n",
        media_type="text/plain; version=0.0.4",
        headers={"Cache-Control": "no-store"},
    )
