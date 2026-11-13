# Week 4 validation

| Check | Observed result |
| --- | --- |
| pytest with PostgreSQL 16.15 | 88 passed. Branch-aware Registry coverage 89.11% against the 85% requirement. SQLite and PostgreSQL migration tests exercised schema parity and immutable evaluation and audit records. |
| Ruff, Black, mypy, OpenAPI snapshot | Passed locally. Generated negative tests cover the new API's auth and required fields. |
| Terraform and TFLint | Format, initialization, validation and TFLint passed locally. |
| Dataset from empty cache | 64 source JPEGs downloaded. All 140 final images matched the frozen SHA-256 manifest. The current original pages supplied each photo’s license, creator and title. |
| Preprocessing and metrics | RGB values and half-pixel edge behavior tested independently. BGR is rejected by the manifest contract and a swapped channel fixture differs from the required RGB tensor. A separate full ONNX run injected the channel swap before normalization. Its mAP was 22.90% overall and 23.45% on Fari indoor, below that slice’s 27% floor. Detection tests cover false positives, class mismatches, duplicate matches and empty labels. |
| Gate state and authorization | Evaluator-only submission, idempotent replay, operator approval and approval-time recheck against a newer baseline exercised by tests. A single known-case false positive blocks an otherwise identical candidate. |
| Local Compose | PostgreSQL, MinIO and Registry reported healthy. Alembic reported `0003_evaluation_gate` at head. |
| Real signed artifact decisions | Baseline evaluated and locally approved. Deliberately degraded artifact rejected with a low-light floor violation, a low-light regression and one known-case false positive above its cap. The rejected candidate remained published. |
| Robot access | Local Fari received 206 for the approved baseline and 403 for the rejected candidate. |
| Audit | Decisions and local operator approval were read back through HTTP and linked by evaluation ID. |

[Registry evidence](W04_Evaluation_Report/registry-evidence.json) contains the
API readback, approval and rejection reasons, artifact and dataset digests and
download statuses. The [report](W04_Evaluation_Report/README.md) links the
measured inference metrics and overlays.

The injected RGB/BGR check used the baseline artifact and the same frozen
dataset. Artifact SHA-256 `0d29ccb5c62fc76be1e9502359c5d2be4867a91b1dfa3034926b683ce8e4b523`.
Dataset SHA-256 `f75b1e7ad2afd597b90530b236caee30d8192bd325634e30421513e2b127cadc`.
