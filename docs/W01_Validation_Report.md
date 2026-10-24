# Week 1 Validation Report

Verified 23 October 2026 for the 26 October Week 1 review.

## Scope

The individual SDE plan and mentor clarification determine this delivery. The
implementation is an empty registry and its local dependencies. The five-platform
brief, three ADRs, platform design document, and weekly log accompany this code.
Full robot APIs, model signing, evaluation, agents, fleet simulation, rollout,
observability, and Azure deployment remain in their assigned later weeks.

## Observed local results

| Check | Result |
|---|---|
| Python environment | Python 3.11.14, uv 0.9.6, frozen lockfile |
| Python tests | 18 passed |
| Branch-aware coverage | 89.61 percent against an 85 percent gate |
| Ruff and Black | Passed |
| Strict mypy | Passed |
| Terraform format, init and validate | Passed on Terraform 1.11.3 |
| tflint | Passed on 0.65.0 |
| gitleaks working-tree scan | Passed on 8.30.1 |
| Local stack | PostgreSQL, MinIO and registry healthy |
| Request logging | Observed JSON lines include request-id; supplied valid X-Request-ID is returned and correlated |
| Readiness deadline | Parallel probes, four-second HTTP response budget, at most four blocking workers; slow-probe and capacity tests passed |

Real dependency interruption and recovery produced these responses:

| Condition | readiness | liveness |
|---|---:|---:|
| Both dependencies available | 200 | 200 |
| MinIO stopped | 503 | 200 |
| MinIO restored | 200 | 200 |
| PostgreSQL stopped | 503 | 200 |
| PostgreSQL restored | 200 | 200 |

The smoke script restores dependencies in a finally block. The original
PostgreSQL named volume and practice table were retained. HTTP probe cancellation
does not forcibly kill a worker thread; capacity remains reserved until the
underlying operation ends. Socket and query timeouts provide additional limits.

## Rejection checks

In isolated local fixtures, an intentionally unformatted Terraform file caused
format checking to exit 3, and an explicitly fake token-shaped value caused
gitleaks to exit 1. The same failures were then verified on independent remote
branches, as recorded below. No fixture was merged into main; no real credential
was used.

## Reproducible storage fixture

MinIO is built from official release RELEASE.2025-10-15T17-29-55Z, with its source
commit and archive SHA-256 pinned in infra/local/minio.Dockerfile. This replaces
unavailable legacy Docker Hub/Quay images. The build completed and authenticated
object-store readiness passed. It is a local development fixture; the upstream
archive and source-distribution status require a maintenance review before any
production use. No Azure resources were applied.

## Observed GitHub Actions evidence

Verified 23 October 2026, America/New_York (24 October UTC), in
[the public project repository](https://github.com/XiaoyuHu777/ingen_fleet_ops_platform_v1.0).

| Case | Commit | Actual run and result |
|---|---|---|
| Clean main | `4e6b69a705f9e38ada96263cbcbce21406fb5255` | [40047956211](https://github.com/XiaoyuHu777/ingen_fleet_ops_platform_v1.0/actions/runs/40047956211): success |
| Terraform format rejection | `b6a1d1a40768f3bd0a4c711fd7168ae6eeb6c84a` | [40048051402](https://github.com/XiaoyuHu777/ingen_fleet_ops_platform_v1.0/actions/runs/40048051402): failed only at Terraform format, validate and tflint |
| Synthetic secret rejection | `b0dfa5e04409ce769b085edd0967237dc4be8074` | [40048051816](https://github.com/XiaoyuHu777/ingen_fleet_ops_platform_v1.0/actions/runs/40048051816): failed only at Scan working tree and committed history |

Main passed Ruff, Black, strict mypy, all 18 tests, 89.61 percent branch-aware
coverage, Terraform formatting/validation/tflint, and both secret scans. A clean
Ubuntu runner built and started PostgreSQL, MinIO and the registry, observed
readiness 200, then 503 for each stopped dependency and 200 on each recovery.
Liveness remained 200. The `python-coverage` artifact was uploaded successfully.

Each negative branch starts from that same clean main commit and changes only
its intended fixture. The format fixture removes alignment whitespace from a
valid HCL declaration; `terraform fmt -check` exits 3. The secret fixture is an
explicitly fake, randomly shaped value; gitleaks reports one finding and exits 1.
Both runs passed all preceding gates; cleanup succeeded. The secret run stops at
the directory scan, so its history scan is not claimed as executed remotely.
An independent local scan of the same fixture confirms history rejection too.

History scanning uses `--log-opts="--full-history HEAD"`: all ancestors of the
checked-out commit are scanned, while an unmerged negative branch cannot poison
a clean main check. The temporary verification refs are disposable; their run
and commit links retain the rejection evidence. They must never be merged.

The first main run passed its engineering checks but failed in the action's
post-job uv cache save: Makefile uses a project-local cache, leaving the action's
default cache path unused. Disabling that redundant cache save corrected the
workflow without changing any quality gate. The successful run above uses this
fix. Subsequent documentation commits are checked by the same workflow; the
README badge links to the latest main result.

## Limits

One upstream Starlette TestClient/HTTPX deprecation warning remains visible;
the pinned adapter works and the tests pass. Performance and safety targets for
the later 20-agent rollout and automatic rollback have not been measured here.
