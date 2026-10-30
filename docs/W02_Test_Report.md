# Week 2 test report

## Local execution (2026-10-30)

On macOS, Python 3.11.14, using the locked virtual environment:

```text
ruff check services tests scripts       All checks passed
black --check services tests scripts   18 files unchanged
mypy                                   Success: 15 source files
pytest                                 56 passed, 2 skipped, 1 warning
branch-aware registry coverage         91.74% (threshold: 85%)
```

The warning is a FastAPI/Starlette deprecation warning concerning the current
TestClient and httpx. It did not fail the tests. OpenAPI-generated tests derive 16 missing-authentication and missing-required-field
cases from the app's schema and validate error responses and request IDs. Separate
contract tests validate actual endpoint responses and cover the registered v1
operations. These deterministic cases are not fuzzing or concurrency tests. Tests exercise enrollment, retry and conflict, credential hashing,
role separation, request limits, response errors, filtering and pagination,
heartbeat replay rejection, ETag/304 without a write, immediate revocation, and
absolute/daily update-window validation. A subprocess checks timezone database
fallback when operating-system zone files are unavailable.
The migration test exercises upgrade and downgrade against SQLite while preserving
an unrelated table. SQLite is not evidence of PostgreSQL behavior.

## PostgreSQL, stack, and CI evidence

The two PostgreSQL integration tests were skipped locally: the Docker daemon and
PostgreSQL server were unavailable. They use a unique temporary schema and cover
Alembic migrations, PostgreSQL JSONB filtering, API round trips, and a ten-minute
last-seen index plan. The CI job provisions PostgreSQL and writes the observed
`EXPLAIN` output as an artifact. The test disables sequential scans to establish
that the index can serve the predicate; it is not a claim about planner choices or
performance under production data. No PostgreSQL result or CI success is claimed
here until the corresponding run completes. The Compose build, migration and
Week 1 dependency-failure smoke checks likewise require Docker.

Reproduce locally with `make setup`, `make check`, `make openapi-check`, and, for
the stack, `make up && make migrate && python3 scripts/smoke_stack.py`. For the
PostgreSQL-specific tests set `REGISTRY_TEST_DATABASE_URL` to a disposable test
database whose user can create schemas; optionally set `REGISTRY_EXPLAIN_OUTPUT`
to a writable file. Do not use a production database. See the README for setup
and the contract for the security and functional limitations.
