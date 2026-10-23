# Week 1 Local Validation Report

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
gitleaks to exit 1. The fixtures were kept outside this repository and its history.
These results establish local rejection behaviour only.

## Reproducible storage fixture

MinIO is built from official release RELEASE.2025-10-15T17-29-55Z, with its source
commit and archive SHA-256 pinned in infra/local/minio.Dockerfile. This replaces
unavailable legacy Docker Hub/Quay images. The build completed and authenticated
object-store readiness passed. It is a local development fixture; the upstream
archive and source-distribution status require a maintenance review before any
production use. No Azure resources were applied.

## Remaining submission evidence

The GitHub Actions workflow defines all required gates, including real-stack
smoke tests and source/history secret scans. It has not run on GitHub yet.
An actual green run and deliberately broken remote branch tests remain required
after publication to the intended repository. Local success does not satisfy
that remote-CI requirement.

One upstream Starlette TestClient/HTTPX deprecation warning remains visible;
the pinned adapter works and the tests pass. Performance and safety targets for
the later 20-agent rollout and automatic rollback have not been measured here.
