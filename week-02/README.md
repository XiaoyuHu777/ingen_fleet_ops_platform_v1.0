# Week 2 — Fleet Registry

[← Project home](../README.md) · [← Week 1](../week-01/README.md)

Week 2 builds on the Week 1 foundation to track robot identity, deployment,
credentials, reported state, and desired artifact state through a versioned API.
The Week 2 work is available in [PR #1](https://github.com/XiaoyuHu777/ingen_fleet_ops_platform_v1.0/pull/1); check the PR for its current merge status.

## Tasks and evidence

| Week 2 task | Result and evidence | Status |
| --- | --- | --- |
| Model robots and state | [Robot, enrollment, reported and desired models](../services/registry/models.py) | Implemented and tested |
| Migrate database and index operator queries | [Migration](../migrations/versions/0001_fleet_registry.py) · [PostgreSQL EXPLAIN](../docs/W02_Postgres_Explain.txt) | PostgreSQL tests passed; EXPLAIN demonstrates index eligibility under the test setting |
| Build the `/v1` registry API | [Endpoints](../services/registry/api_v1.py) · [Schemas](../services/registry/schemas.py) · [Contract](../docs/W02_API_Contract.md) | Implemented and tested |
| Protect robot and operator endpoints | [Authentication and threat model](../docs/W02_API_Contract.md#security-and-threat-model) · [Tests](../tests/test_registry.py) | Prototype implemented and tested; production identity remains future work |
| Check Fari schema generality | [Update-window contract](../docs/W02_API_Contract.md#data-representations) | Windows can be recorded; safety enforcement is future work |
| Validate API and compatibility | [Generated contract tests](../tests/test_openapi_generated.py) · [Test report](../docs/W02_Test_Report.md) | PostgreSQL and contract tests passed; first OpenAPI comparison skipped without an earlier baseline |
| Record weekly delivery and reflection | [Delivery map](../docs/W02_Delivery_Map.md) · [Week 2 log](../weekly/Wk-02-Log.md) | Prepared for review; see [PR #1](https://github.com/XiaoyuHu777/ingen_fleet_ops_platform_v1.0/pull/1) for merge status |

The implementation run passed **58 tests with 93.18% branch coverage**:
[GitHub Actions evidence](https://github.com/XiaoyuHu777/ingen_fleet_ops_platform_v1.0/actions/runs/41067296504).
The query-plan evidence establishes index eligibility, not production performance.

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
