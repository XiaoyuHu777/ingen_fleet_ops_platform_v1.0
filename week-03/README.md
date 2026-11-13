# Week 3 — Model Packaging and Signed Artifact Registry

[Project home](../README.md) · [Week 2](../week-02/README.md) · [Weekly index](../weekly/README.md)

Week 3 takes a pinned YOLOS-Tiny PyTorch checkpoint through deterministic ONNX
export, CPU parity and latency checks, Ed25519 signing, and publication to a
MinIO-backed artifact registry. PostgreSQL records lifecycle events for review.

| Deliverable | Start here |
| --- | --- |
| Packaging pipeline | [CLI](../pipeline/package.py), [sample manifest](../docs/W03_Manifest_Example.json), [preprocessing contract](../docs/W03_Artifact_Contract.md#preprocessing-and-outputs) |
| Signing and verification | [Signer](../pipeline/sign.py), [agent verifier](../agent/verify.py), [trust and rotation design](../docs/W03_Artifact_Contract.md#bundle-and-trust) |
| Artifact registry | [API](../services/registry/artifact_api.py), [migration](../migrations/versions/0002_signed_artifacts.py), [publication, Range, lifecycle and audit contract](../docs/W03_Artifact_Contract.md#api-and-state) |
| First artifact | [YOLOS-Tiny model card](../docs/W03_Model_Card.md) |
| Demo and evidence | [44-second demo](W03_Demo_Recording.mp4), [validation report](../docs/W03_Test_Report.md), [reproduction commands](../docs/W03_Artifact_Contract.md#build-sign-and-run-the-demo) |
| Reflection | [Week 3 log](../weekly/Wk-03-Log.md) |

The demo shows byte-identical builds, signed publication, two HTTP 206
responses, and rejection after one ONNX byte is changed. The example artifact
is published for evaluation. Week 4 adds the labeled evaluation gate and
approval policy.
