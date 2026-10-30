# Week 2 — Fleet Registry

[← Project home](../README.md) · [← Week 1](../week-01/README.md)

Week 2 builds on the Week 1 foundation to track robot identity, deployment,
credentials, reported state, and desired artifact state through a versioned API.
The Week 2 work is available in [PR #1](https://github.com/XiaoyuHu777/ingen_fleet_ops_platform_v1.0/pull/1); check the PR for its current merge status.

## Review the deliverables

| Item | What to review |
| --- | --- |
| Requirements and scope | [Week 2 delivery map](../docs/W02_Delivery_Map.md) |
| API and security | [API contract and threat model](../docs/W02_API_Contract.md) · [OpenAPI snapshot](../docs/openapi-v1.json) |
| Database | [Models](../services/registry/models.py) · [Alembic migration](../migrations/versions/0001_fleet_registry.py) |
| Registry API | [Endpoints and authentication](../services/registry/api_v1.py) · [Request and response schemas](../services/registry/schemas.py) |
| Test evidence | [Test report](../docs/W02_Test_Report.md) · [PostgreSQL EXPLAIN](../docs/W02_Postgres_Explain.txt) · [GitHub Actions run](https://github.com/XiaoyuHu777/ingen_fleet_ops_platform_v1.0/actions/runs/41067296504) |
| Reflection | [Week 2 log](../weekly/Wk-02-Log.md) |

## What was added

- Separate desired and reported state so a target does not imply the robot has
  applied it; heartbeat sequence numbers reject duplicate or older reports.
- Enrollment with a one-time robot credential, operator/robot role separation,
  credential revocation, filtered listing and conditional ETag polling.
- Absolute and daily local-time update-window metadata, with no update or
  safety enforcement in the registry.
- Contract, integration and migration tests, with actual PostgreSQL evidence.

The Week 2 API is a local prototype. It does not yet implement artifact signing
or verification, a robot agent, rollout, automatic rollback, a production
identity system, or enforcement of safe-update conditions. Its first PR
establishes the OpenAPI baseline; the breaking-change comparison was skipped
because no earlier baseline existed on main.

The shared implementation lives in the root `services/`, `migrations/`, `tests/`,
and `infra/` directories. See [project setup](../README.md#start-locally) to run
it and [the weekly review index](../weekly/README.md) for all weekly materials.
