# Week 2 delivery map

This map tracks the individualized SDE plan's Week 2 scope. Product engineering
materials supply design context; their separate team sprints and production
requirements are not evidence that those systems have been built here.

| Plan requirement | Implementation and evidence | Verification status |
| --- | --- | --- |
| Robot, enrollment, reported and desired state; database migrations and indexes | `services/registry/models.py`, `migrations/versions/0001_fleet_registry.py` | SQLite and PostgreSQL migration tests passed; [PostgreSQL EXPLAIN evidence](W02_Postgres_Explain.txt) records index eligibility. Site and current digest are in separate tables and indexed separately for the join. |
| Versioned registry, filtering, cursor pagination, heartbeats, conditional desired-state reads | `services/registry/api_v1.py`, `services/registry/schemas.py`, `docs/W02_API_Contract.md` | TestClient tests cover key behaviors, including a 304 without a write; PostgreSQL API integration passed in GitHub Actions. |
| Separate operator and robot credentials, hash at rest, immediate revocation | API and contract threat model | Local tests cover role separation, hashing, replay and next-request rejection. Bearer authentication is a local prototype, not the platform's production identity system. |
| Explicit contract, errors, bounded bodies, schema-based tests, API compatibility gate | `docs/W02_API_Contract.md`, `docs/openapi-v1.json`, `tests/test_registry.py`, `tests/test_openapi_generated.py`, `.github/workflows/ci.yml` | Generated negative cases cover missing authentication and required fields; response-schema checks and snapshot check run locally. The first PR establishes a baseline; the comparison was skipped and remains to be exercised on a later PR. |
| Fari generality check | `safe_update_window` in the registry contract | Absolute and daily local-time windows are configuration metadata, not safety interlocks. A future agent/controller must check actual interaction and alert conditions and define local-time and daylight-saving handling before execution. |
| Weekly log, test report and review organization | `weekly/Wk-02-Log.md`, `docs/W02_Test_Report.md`, `weekly/README.md` | Local and GitHub Actions results are recorded separately. |

The registry stores an artifact digest, not a verified bundle. Signing,
artifact publication, offline reconciliation, controller leases, compatibility
constraints, staged rollout, rollback and production identity integration remain
future work. Week 1 ADR-0003 describes proposed agent/controller protocol, not
features already implemented by this Week 2 registry. Later changes to a
published `/v1` contract require review and a documented migration path.
