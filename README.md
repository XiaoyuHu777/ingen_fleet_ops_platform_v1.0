# InGen Fleet Operations Platform

[![CI checks on main](https://github.com/XiaoyuHu777/ingen_fleet_ops_platform_v1.0/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/XiaoyuHu777/ingen_fleet_ops_platform_v1.0/actions/workflows/ci.yml?query=branch%3Amain)

One shared control plane for robot model operations. Aido Rover is the primary
fleet target; Fari checks whether the architecture generalises. Week 1 established
the local foundation. Week 2 adds a fleet registry with distinct reported and
desired state, per-robot credentials, and a versioned API. Artifact publication,
the robot agent, rollouts, and cloud deployment remain future work.

## Review by week

| Week | Focus | Start here |
| --- | --- | --- |
| **Week 1** | Architecture, local infrastructure, and CI foundation | [Week 1 review](week-01/README.md) — deliverables, decisions, validation, and the original code snapshot |
| **Week 2** | Fleet registry, API contract, and robot authentication | [Week 2 review](week-02/README.md) — implementation, migration, contract, tests, and weekly log |
| Week 3 onward | Future work | Review entries will be added as work is completed. |

Each review page links to that week's evidence. Source code remains in the
shared service tree because subsequent weeks build on the previous work.
Week 1's page also links to its fixed code snapshot.

## Start locally

Requirements: Docker Engine/Desktop with Compose v2+ and GNU/BSD Make. Run all
commands from this repository's root. First-time image downloads need internet.

```sh
cp .env.example .env  # first setup only; preserve an existing .env
# Set OPERATOR_API_KEY in .env to a newly generated, private development key.
make up
make migrate         # apply Alembic migrations; preserves existing volume data
make ps
curl -i http://127.0.0.1:8000/healthz
curl -i http://127.0.0.1:8000/readyz
```

`make up` builds the registry and starts PostgreSQL, MinIO, and the registry.
Set a strong random `OPERATOR_API_KEY` in the ignored `.env` before starting;
for example, generate one with `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`
and copy its output into `.env`. Do not commit or share the key. Changing the
key requires recreating the registry container (`make up`). `make migrate` must
be run after startup on a new database and whenever a new migration is delivered;
health checks only verify dependency connectivity, not that the schema is current.
MinIO is built from checksum-verified official release source because the old
community Docker Hub image is unavailable; the first build may take several minutes.
The API binds only to localhost; PostgreSQL and MinIO stay on the Compose network.
Change `REGISTRY_PORT` in `.env` if localhost port 8000 is already occupied.
The example passwords are disposable local development values, never deployment
credentials. Private `.env` files, virtual environments and downloaded tools are
ignored by Git and excluded from the Docker build context.

`/healthz` checks the API process. `/readyz` executes PostgreSQL `SELECT 1` and an
authenticated MinIO `ListBuckets` operation; either failure returns HTTP 503.
Probes run concurrently with a **4-second overall HTTP deadline**, configurable by
`READINESS_DEADLINE_SECONDS` (> 0, at most 4 seconds). Individual connect/read/query
timeouts default to 2 seconds via `DEPENDENCY_TIMEOUT_SECONDS`. A slow probe yields
HTTP 503 with a `timeout` dependency status without waiting for its blocking call.
A dedicated pool permits at most four actual blocking probes; cancelled requests
do not release worker capacity until the underlying call finishes. Saturation
returns 503 immediately and leaves `/healthz` responsive. Python cannot forcibly
stop an already running thread: socket/query timeouts remain necessary, and a
stuck DNS/library call can retain a slot and delay process exit. The response
deadline bounds HTTP waiting, not the lifetime of the underlying thread.
Responses omit internal connection errors.
`X-Request-ID` is returned to the caller; each registry JSON log line contains a
`request-id` field (`-` for startup/shutdown events outside a request).

```sh
make logs   # recent logs
make db     # PostgreSQL SQL console; use \q to exit
make migrate # apply pending Alembic migrations
make down   # removes this project's containers/network, preserves named volumes
```

The Compose project remains `ingen-fleet-ops`, retaining existing PostgreSQL
exercise data in `ingen-fleet-ops_postgres_data`. Do not use `down -v` unless you
deliberately want to delete local data. MinIO has a separate persistent named volume.

## Development and checks

Dependencies are exact-pinned in `pyproject.toml`, resolved with **uv 0.9.6**, and
fully locked in `uv.lock`. `.python-version` pins **Python 3.11.14**. Install uv
using its [official instructions](https://docs.astral.sh/uv/getting-started/installation/),
then run:

```sh
uv python install 3.11.14
make setup
make check
make openapi-check            # check the committed OpenAPI snapshot
python3 scripts/install_tools.py  # checksum-verifies local tflint/gitleaks binaries
make terraform-check
make secret-scan
python3 scripts/smoke_stack.py    # requires make up; stops/restores MinIO and PostgreSQL
```

`make check` runs Ruff, Black, strict mypy, and pytest with branch-aware coverage
(minimum 85%). PostgreSQL integration tests require `REGISTRY_TEST_DATABASE_URL`
pointing to an expendable test database; without it they are skipped. Never point
this variable at a production database. The test report records which checks were
actually run. The smoke test checks HTTP 503 on each dependency outage, HTTP 200
on `/healthz`, and HTTP 200 on recovery. It never removes volumes or writes fleet
data. It briefly interrupts only this project's two dependencies.

Terraform **1.11.3** is the tested tool version. `infra/terraform` is a no-resource,
no-provider scaffold: format, init, validate and tflint require no Azure credentials
and create no billable resources. Never run cloud apply as part of Week 1 setup.
The committed `infra/terraform/terraform.tfvars.example` contains only the
non-secret `platform_name` input; real `.tfvars` files are ignored.

`.github/workflows/ci.yml` runs on every push and pull request with Python,
Terraform, secret-scan, contract, and stack checks. The Week 1 [verified main run](https://github.com/XiaoyuHu777/ingen_fleet_ops_platform_v1.0/actions/runs/40047956211)
passed every gate and real dependency interruption/recovery test for that earlier
commit; it does not establish the result of the current changes. Independent
[Terraform-format](https://github.com/XiaoyuHu777/ingen_fleet_ops_platform_v1.0/actions/runs/40048051402)
and [fake-secret](https://github.com/XiaoyuHu777/ingen_fleet_ops_platform_v1.0/actions/runs/40048051816)
branches failed only their intended gates. Full SHAs and limits are recorded in
[the validation report](docs/W01_Validation_Report.md). The badge tracks current main.

## Layout and scope

`services/registry/` contains the Week 2 API and database models; `migrations/`
contains Alembic schema changes. `tests/`, `infra/local/`, `infra/terraform/`,
and `scripts/` support validation and local operation. `services/controller/`,
`agent/`, `pipeline/`, `policy/`, and `sim/` reserve later component boundaries.
Architecture decisions are in
`docs/adr/`; the [weekly review index](weekly/README.md) links each week’s
deliverables. The [Week 2 API contract and threat model](docs/W02_API_Contract.md)
describe every endpoint, authentication, curl examples, and current limitations.
The live OpenAPI specification is at `/openapi.json` and interactive reference at
`/docs`. Run `make migrate` before using the fleet endpoints.

Public commits must exclude company source documents, credentials, signing private
keys, proprietary model weights, and customer footage. Only sanitised engineering
artifacts belong in this repository. Validation evidence distinguishes observed
local and GitHub results from the later fleet and cloud targets.

## Primary references

- [Docker Compose health-aware startup](https://docs.docker.com/compose/how-tos/startup-order/)
- [uv project locking](https://docs.astral.sh/uv/guides/projects/)
- [Pydantic Settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)
- [Psycopg database connections](https://www.psycopg.org/psycopg3/docs/basic/usage.html)
- [MinIO pinned server release](https://github.com/minio/minio/releases/tag/RELEASE.2025-10-15T17-29-55Z)

MinIO is retained for the personal plan's local-stack parity. This local prototype
does not establish a production support/security policy for the archived upstream
server project; production storage and provider choices need review before cloud use.
