# Week 3 validation report

## Reproducibility and live artifact flow

| Check | Observed result |
| --- | --- |
| Two independent builds using the same captured benchmark | `cmp` passed for `manifest.json` and `payload.tar`. Both returned manifest SHA-256 `0d29ccb5c62fc76be1e9502359c5d2be4867a91b1dfa3034926b683ce8e4b523`. |
| PyTorch to ONNX Runtime parity | Five fixed fixtures. Maximum absolute error was 0.0003242493 for logits and 0.0000854433 for boxes, below 0.001. |
| CPU inference latency | Apple M4 Pro, CPU execution provider, one thread, five warmups, 25 timed inferences. p50 62.40 ms and p95 64.83 ms. |
| Signed publication and replay | Real PostgreSQL and MinIO in an isolated Compose project returned HTTP 201, then 200 with the same immutable record and one audit event. |
| Interrupted-download recovery | Two MinIO-backed byte ranges returned HTTP 206. Their concatenation matched the registry's bundle hash and passed agent verification against the expected manifest digest. |
| One altered ONNX byte | The agent returned `payload_hash_mismatch`. A second upload of the altered bundle returned HTTP 422 with the same error. |
| Week 2 stack regression | Stopping either PostgreSQL or MinIO returned HTTP 503 from readiness. Liveness stayed up and readiness returned HTTP 200 after restoration. |

The [manifest](W03_Manifest_Example.json) binds this evidence to the exact model
and fixture set. The [44-second demo](../week-03/W03_Demo_Recording.mp4) uses
the output from the live local run, rendered as a terminal replay. Follow the
[artifact contract](W03_Artifact_Contract.md#build-sign-and-run-the-demo) to
reproduce it.

## Automated checks

The local suite passed **72 tests with 89.67% branch-aware registry coverage**,
including tests against a disposable PostgreSQL 16 database. Ruff, Black,
strict mypy for the service and verifier, the OpenAPI snapshot, Terraform
format/validate/tflint, and the history secret scan passed. The suite covers
the Week 2-to-Week 3 migration, append-only audit enforcement, signature
failures, authorization, version collision and replay, orphaned object
recovery, state transitions, response schemas and byte ranges.

Registry coverage measures the in-process test suite. The MinIO adapter's
real publish and Range path is exercised separately by the Compose smoke.
The GitHub workflow also re-exports the pinned checkpoint, compares both
builds, runs the real stack smoke, and checks compatibility against the
Week 2 OpenAPI baseline. [GitHub Actions](https://github.com/XiaoyuHu777/ingen_fleet_ops_platform_v1.0/actions/workflows/ci.yml?query=branch%3Aweek-03-signed-artifact-registry)
