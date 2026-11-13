# Week 4 Evaluation Gate and Failure Case Regression Suite

[Project home](../README.md) · [Week 3](../week-03/README.md) · [Weekly index](../weekly/README.md)

| Deliverable | Evidence |
| --- | --- |
| Golden detection dataset | [Dataset card and source](../eval/golden/DATASET.md), [versioned labels](../eval/golden/labels.json), [SHA-256](../eval/golden/DATASET.sha256) |
| Evaluation and promotion | [Runner](../pipeline/evaluate.py), [policy and known cases](../policy/gate.yaml), [API, state and failure behavior](../docs/W04_Gate_Contract.md), [migration](../migrations/versions/0003_evaluation_gate.py) |
| Baseline and deliberately degraded artifact | [Measured reports, worst-case overlays and Registry evidence](../docs/W04_Evaluation_Report/README.md) |
| Fari gap analysis | [Two-page analysis](../docs/W04_Fari_Gap_Analysis.docx) |
| Validation | [Test report](../docs/W04_Test_Report.md), [CI](../.github/workflows/ci.yml) |
| Reflection | [Week 4 decision log](../weekly/Wk-04-Log.md) |

## Reproduce the engineering checks

Run from a clean, committed checkout. The Week 3 packaging CLI records the
source commit and expects the builder and verifier sources to be clean.
Use a fresh local test database and object store for the workflow below.
An existing registry may already bind `yolos-tiny:1.0.0` to a previous
build digest.

```sh
make setup
cp .env.example .env  # only on the fresh validation checkout
# For a shared environment generate separate private operator and evaluator keys.
PYTHON=.venv/bin/python scripts/gen_dev_keys.sh
make up
make migrate
.venv/bin/python -m eval.golden.fetch
.venv/bin/python -m pipeline.package fetch .week3/checkpoint

for model in baseline degraded; do
  config=pipeline/yolos-tiny.json
  if [ "$model" = degraded ]; then config=pipeline/yolos-tiny-degraded.json; fi
  .venv/bin/python -m pipeline.package benchmark --checkpoint .week3/checkpoint \
    --config "$config" --out ".week4/$model-benchmark.json"
  .venv/bin/python -m pipeline.package build --checkpoint .week3/checkpoint \
    --config "$config" --benchmark ".week4/$model-benchmark.json" \
    --out ".week4/$model"
  .venv/bin/python -m pipeline.sign sign ".week4/$model" \
    --key .week3/keys/signing.pem --out ".week4/$model.tar"
  .venv/bin/python -m pipeline.evaluate ".week4/$model.tar" \
    --trust .week3/keys/public --out ".week4/reports/$model"
done

set -a
. ./.env
set +a
.venv/bin/python -m scripts.evaluation_smoke .week4/baseline.tar \
  .week4/degraded.tar --url "http://127.0.0.1:${REGISTRY_PORT:-8000}" \
  --reports .week4/reports --out .week4/reports/registry-evidence.json
make check
make openapi-check
```

The HTTP check confirms local test approval, gate rejection and the
resulting robot download permissions. CPU timings vary across runs.
