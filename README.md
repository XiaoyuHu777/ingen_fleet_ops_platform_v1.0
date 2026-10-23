# InGen Fleet Operations Platform — Week 1

One shared control-plane foundation for robot model operations. Aido Rover is the
primary fleet target; Fari checks whether the architecture generalises. The Week 1
implementation contains infrastructure and an empty registry, with no robot CRUD,
model publication, agent, rollout, or cloud resources yet.

## Start locally

Requirements: Docker Engine/Desktop with Compose v2+ and GNU/BSD Make. Run all
commands from this repository's root. First-time image downloads need internet.

```sh
cp .env.example .env  # first setup only; preserve an existing .env
make up
make ps
curl -i http://127.0.0.1:8000/healthz
curl -i http://127.0.0.1:8000/readyz
```

`make up` builds the registry and starts PostgreSQL, MinIO, and the registry.
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
python3 scripts/install_tools.py  # checksum-verifies local tflint/gitleaks binaries
make terraform-check
make secret-scan
python3 scripts/smoke_stack.py    # requires make up; stops/restores MinIO and PostgreSQL
```

`make check` runs Ruff, Black, strict mypy, and pytest with branch-aware coverage
(minimum 85%). The smoke test checks HTTP 503 on each dependency outage, HTTP 200
on `/healthz`, and HTTP 200 on recovery. It never removes volumes or writes fleet
data. It briefly interrupts only this project's two dependencies.

Terraform **1.11.3** is the tested tool version. `infra/terraform` is a no-resource,
no-provider scaffold: format, init, validate and tflint require no Azure credentials
and create no billable resources. Never run cloud apply as part of Week 1 setup.
The committed `infra/terraform/terraform.tfvars.example` contains only the
non-secret `platform_name` input; real `.tfvars` files are ignored.

`.github/workflows/ci.yml` runs on every push and pull request: the same Python
checks, Terraform formatting/validation/tflint, gitleaks working-tree/history
scans, and real-stack smoke checks. **A workflow definition is not evidence of a
green remote run.** Remote CI must be verified after an authorised GitHub publish.

## Layout and scope

`services/registry/` is implemented. The following paths reserve later boundaries:
`services/controller/`, `agent/`, `pipeline/`, `policy/`, `sim/`, `infra/terraform/`,
`infra/local/`, `tests/`, `docs/`, and `scripts/`. Architecture decisions are in
`docs/adr/`; weekly records are in `weekly/` when prepared.

Public commits must exclude company source documents, credentials, signing private
keys, proprietary model weights, and customer footage. Only sanitised engineering
artifacts belong in this repository. Local validation evidence is kept separately
from the implementation; no GitHub publication is claimed by this local build.

## Primary references

- [Docker Compose health-aware startup](https://docs.docker.com/compose/how-tos/startup-order/)
- [uv project locking](https://docs.astral.sh/uv/guides/projects/)
- [Pydantic Settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)
- [Psycopg database connections](https://www.psycopg.org/psycopg3/docs/basic/usage.html)
- [MinIO pinned server release](https://github.com/minio/minio/releases/tag/RELEASE.2025-10-15T17-29-55Z)

MinIO is retained for the personal plan's local-stack parity. This local prototype
does not establish a production support/security policy for the archived upstream
server project; production storage and provider choices need review before cloud use.
