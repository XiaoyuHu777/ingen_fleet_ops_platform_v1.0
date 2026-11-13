# Evaluation and promotion contract

A verified signature establishes artifact integrity. The gate establishes
measured compatibility with a frozen golden dataset. Separate credentials
authenticate publication and approval, evaluation evidence submission, and
robot downloads.

## Evaluation

`pipeline/evaluate.py` verifies the signed bundle and binds the run to the
manifest SHA-256. It verifies the exact label manifest against the active
policy and checks every image checksum and dimension. The runner uses
NumPy to implement the signed RGB, FP32, half-pixel resize and normalization
contract. It loads the ONNX model with the CPU execution provider and checks
input and output names, shapes and types. The runner has no dependency on
training code or on PyTorch preprocessing.

The output directory contains `report.json` with aggregate and per-image
metrics, `evidence.json` for the registry, and `README.md` with two overlays
per slice. The evidence binds the report SHA-256 to both the model digest
and the golden dataset digest. One process per model measures OS
high-water RSS. ONNX inference uses one CPU thread and five warmups. The
sample latency excludes image decode, preprocessing and reporting.

For mAP, the runner ranks up to 100 detections per image from named
foreground classes whose score beats background. Matching is greedy by
score within each class and uses each ground-truth box at most once.
101-point interpolated AP is averaged over classes with labels and IoUs
from 0.50 to 0.95 in 0.05 steps. AP50 is also reported. The all-negative
slice uses the fraction of frames with any false positive at confidence
0.50 because it has no positive recall denominator. Misses and false
positives for known cases use IoU 0.50 and confidence 0.50.

## Policy and state

[gate.yaml](../policy/gate.yaml) pins the dataset, slice counts, operating
threshold, absolute floors and known-case limits. Absolute floors are
prototype values calibrated to the frozen baseline. The known-case list
comes from observed baseline errors and includes low light, motion blur,
thin targets, a cluttered Fari kitchen and a negative frame. Both missed
boxes and false positives are capped. An approved comparator can tighten
these caps.

The comparator is the most recently approved, currently approved artifact
of the same model name. Per-slice mAP may drop by at most 2 percentage points.
The empty-slice false-positive image rate may rise by at most 2 percentage
points, and overall p95 may rise by at most 10%. The baseline and candidate
must share the frozen dataset and CPU profile. A first artifact runs the
absolute and known-case checks. The gate blocks candidates when an existing
approved baseline lacks comparable passing evidence. Re-evaluate the
approved artifact with the current data and CPU profile to refresh that
evidence. Different datasets or CPU profiles require a new baseline run.

| Request | Principal | Effect |
| --- | --- | --- |
| `POST /v1/artifacts/{digest}/evaluations` | Evaluator bearer key | Submit `evidence.json`. Returns 201 for a recorded result with `passed` and `violations`, or 200 for an identical retry under the same policy. A passing first submission advances `published` to `evaluated`. A failed first submission keeps it `published`. |
| `GET /v1/artifacts/{digest}/evaluations/{id}` | Operator bearer key | Read the immutable decision, compared digest, exact submitted evidence and policy checksum. |
| `PATCH /v1/artifacts/{digest}/lifecycle` | Operator bearer key | Approve an evaluated artifact by providing the expected state and a review reason. The registry rechecks the latest passing evidence against the current approved comparator and current policy inside the same transaction. |
| `GET /v1/artifacts/{digest}/audit` | Operator bearer key | Read publication, evaluation attempts, approval and later lifecycle changes. A nullable `evaluation_id` links gate and approval events to the immutable decision. |
| `GET /v1/artifacts/{digest}/download` | Enrolled robot bearer key | Read approved bytes, including single HTTP ranges. A published or evaluated artifact receives 403 until approval. |

The evaluator key must differ from the operator key. Keep it in a trusted
evaluator or CI environment. The registry independently calculates the verdict
from authenticated metrics. The registry does not run inference itself.
PostgreSQL serializes evidence writes and lifecycle decisions per model using a
transaction-scoped advisory lock. State, decision and audit writes commit
together. The database rejects updates, deletes and truncation of decision
and audit rows. Missing policy returns 503. Stale state, missing evidence or
policy regression returns 409 on attempted approval.

## Week 3 migration

Apply Alembic revision `0003_evaluation_gate` before running Week 4. The
migration preserves previous lifecycle and audit records. Clients that used
the Week 3 manual `published → evaluated` transition must now submit evidence
through the evaluator endpoint. Existing evaluated artifacts must have a
passing Week 4 record before approval. Existing approved artifacts retain
their lifecycle status and must be evaluated under the new policy before
serving as comparators. The `PATCH` and `GET` resource paths and existing
payload fields remain available.
