# Week 4 evaluation report

The overall mAP gap was 1.12 percentage points at full precision. Both bundles were evaluated with ONNX Runtime 1.22.1 on Apple M4 Pro CPU
against [140 frozen images](../../eval/golden/DATASET.md). The release candidate
uses the same detector architecture and checkpoint with the blue-channel
normalization mean changed from 0.406 to 0.455. Both artifacts were signed
and published to the isolated PostgreSQL and MinIO stack. The baseline
passed and received a separate local operator approval. The candidate
remained published after the gate rejected it.

| Measurement | Baseline | Degraded | Gate |
| --- | ---: | ---: | --- |
| Overall mAP 50–95 | 27.80% | 26.68% | Context for the slice decisions |
| Low-light mAP 50–95 | 42.27% | 31.86% | **Rejected.** Full-precision drop 10.42 points against a 2.00-point limit, and below the 35.00% absolute floor. |
| Fari indoor mAP 50–95 | 33.32% | 32.82% | Within the 2.00-point regression limit. |
| Known failure `small_thin-189820` | 34 false positives | 35 false positives | **Rejected.** Limit 34. |
| CPU inference p95 | 63.66 ms | 64.00 ms | Within the 10% increase limit |
| Peak process RSS | 296.8 MiB | 329.8 MiB | Recorded for review |
| Robot Range response | 206 | 403 | Only the approved baseline was available to the enrolled Fari |

- [Baseline metrics and worst cases](baseline/README.md) · [JSON](baseline/report.json)
- [Degraded metrics and worst cases](degraded/README.md) · [JSON](degraded/report.json)
- [Registry decisions, evaluation IDs and append-only audit events](registry-evidence.json)
- [Gate contract and failure behavior](../W04_Gate_Contract.md)

The two reports identify the artifact and dataset by full SHA-256. The
registry records link each result to its report SHA-256. Displayed percentages
are rounded independently. The gate computes deltas from full precision.
