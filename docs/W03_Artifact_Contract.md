# Week 3 artifact contract

The packaging CLI exports the pinned YOLOS-Tiny PyTorch checkpoint as a static
FP32 ONNX graph. The registry verifies a signed bundle before publishing it to
MinIO. PostgreSQL tracks the artifact digest, signing key, bundle SHA-256,
byte length, lifecycle state, and audit events.

## Build, sign and run the demo

Run from the project root with Python 3.11.14 and uv 0.9.6. The optional
`packaging` group has been exercised on Apple Silicon macOS and is used on
Linux x86-64 in CI. The registry image installs only its service dependencies.

```sh
uv sync --locked --group packaging
py=.venv/bin/python
mkdir -p .week3
"$py" -m pipeline.package fetch .week3/checkpoint
"$py" -m pipeline.package benchmark --checkpoint .week3/checkpoint --out .week3/benchmark.json

for build in first second; do
  "$py" -m pipeline.package build --checkpoint .week3/checkpoint \
    --benchmark .week3/benchmark.json --out ".week3/$build"
done
cmp .week3/first/manifest.json .week3/second/manifest.json
cmp .week3/first/payload.tar .week3/second/payload.tar

PYTHON="$py" scripts/gen_dev_keys.sh
"$py" -m pipeline.sign sign .week3/first --key .week3/keys/signing.pem \
  --out .week3/artifact.tar
"$py" -m agent.verify .week3/artifact.tar --trust .week3/keys/public

cp -n .env.example .env
# Set a development OPERATOR_API_KEY in .env.
make up
make migrate
"$py" -m scripts.artifact_smoke .week3/artifact.tar --trust .week3/keys/public
```

`fetch` verifies both downloaded files against pinned SHA-256 values. You can
also put those exact files in the checkpoint directory yourself. The optional
`--config` profile sets the semantic version, input shape, preprocessing
and thresholds. The signer and CLI refuse invalid inputs. Generate the dev
key once per directory. The Compose service mounts only the public key
directory read-only. The registry validates and reloads that trust set at
publication. The private key stays on the signing machine.

The benchmark captures ONNX Runtime CPU inference only. It uses one intra-op
and one inter-op thread, five warmups and 25 timed calls cycling through the
five fixtures. The timing report binds the ONNX bytes, raw fixture set, input
contract and runtime version. The build re-exports and reruns parity and
requires matching benchmark evidence. Keeping that measured report fixed makes
two builds reproducible from the same committed sources, checkpoint, profile
and software environment. Remeasuring latency or changing the source commit
changes the manifest and digest.

## Preprocessing and outputs

1. Decode to an upright image and supply HWC `uint8` pixels in red, green,
   blue order. Convert each sample to FP32 and divide by 255.
2. Resize each channel independently to 256 rows by 320 columns for this
   profile. Use bilinear interpolation without antialiasing. For output
   pixel coordinate `i` on either axis, sample at
   `(i + 0.5) * input_size / output_size - 0.5`. Clamp source coordinates
   to the edge before computing the bilinear combination of neighboring pixels.
   Stretch both axes independently. The model expects the same treatment
   for any aspect ratio.
3. Subtract channel means `[0.485, 0.456, 0.406]` and divide by channel
   standard deviations `[0.229, 0.224, 0.225]` in RGB order. Pack a
   contiguous FP32 NCHW tensor named `pixel_values`, shape `[1,3,256,320]`.
4. Read `logits` `[1,100,92]` and `pred_boxes` `[1,100,4]` in FP32.
   Apply a numerically stable softmax across all 92 logits per query.
   Choose the highest-scoring named class, ignoring `N/A` slots and the
   final no-object index 91 when choosing the class. Retain scores at
   least 0.5. The manifest maps the class indices. Decode boxes as
   normalized `[center_x,center_y,width,height]`. Convert to corner pairs,
   multiply horizontal coordinates by the original width and vertical
   coordinates by the original height, then clip to the original image
   bounds. The graph returns raw detections and has no embedded NMS.

## Bundle and trust

The uncompressed USTAR transport contains `manifest.json`, `payload.tar`
and a detached `signature.json`. The inner archive holds `model.onnx`,
the upstream license and the attribution notice. Both archives have sorted
regular-file entries, zero timestamps and owner/group IDs, and fixed modes.

- The artifact ID is the lowercase SHA-256 of the exact canonical
  `manifest.json` bytes. V1 uses ASCII-escaped JSON with sorted keys,
  compact separators and no final newline. The manifest hashes the inner
  archive and every contained file.
- The Ed25519 message is ASCII `ingen-fleet-artifact-manifest-v1`, one
  zero byte, then the 32 binary bytes of the manifest SHA-256. The
  signature envelope contains the algorithm, hex signature and key ID.
  The key ID is SHA-256 of the 32-byte raw Ed25519 public key.
- The verifier receives trusted public keys independently. When verifying a
  download, supply the expected digest from the desired-state record.
  CLI `--digest` accepts bare hex and `sha256:<hex>`. The API path
  and artifact record use bare lowercase hex. The verifier checks the target,
  trust, signature, archive and each file before a client can use the bundle.
- For a planned rotation, provision the replacement public key on the
  registry and robots alongside the old key, confirm receipt, then switch the
  signer and remove the old key after overlap. If a key is compromised,
  remove trust and revoke affected artifacts as soon as clients can receive
  the updated trust set. This prototype uses provisioned PEM files. ADR-0002
  describes authenticated distribution and the later rotation drill.

## API and state

Use `Authorization: Bearer …` with the development operator key for publication,
listing, state changes and audit. An operator can download non-revoked
artifacts for evaluation. A currently enrolled robot can download approved
artifacts using its robot credential. Authentication is checked on each
request, including retries and resumed downloads.

| Method and path | Behavior |
| --- | --- |
| `POST /v1/artifacts` | Raw signed bundle with `Content-Type: application/x-tar`, maximum 64 MiB. Returns 201 on first verified publication and 200 for an already recorded digest. A different digest for the same name and version returns 409. |
| `GET /v1/artifacts` | Optional exact filters `name`, `version`, `state`. Use `limit` and `after` with the returned `next_after` digest. |
| `GET /v1/artifacts/{digest}/download` | Full 200 or single byte-range 206 with `Content-Range` and a strong bundle ETag. Open-ended and suffix ranges are supported. A stale `If-Range` returns the full representation. An unsatisfiable or multiple byte range returns 416. Revoked artifacts return 410. |
| `PATCH /v1/artifacts/{digest}/lifecycle` | Accepts `expected_state`, `state` and a nonempty `reason`. A stale state or illegal transition returns 409. |
| `GET /v1/artifacts/{digest}/audit` | Ascending audit event IDs, with `limit` and `after` from `next_after`. Each event records actor, time, reason and transition. |

The normal progression is `published → evaluated → approved → deprecated →
revoked`. An operator can revoke directly from any earlier state. Revocation is
terminal. Week 3 exposes manual lifecycle transitions. The
[Week 4 gate contract](W04_Gate_Contract.md) describes the evidence and approval
workflow that supersedes manual promotion. Audit rows and state changes
commit in one transaction. The database rejects edits and deletes of audit events
(and truncation on PostgreSQL). The shared prototype operator credential
identifies the actor as `operator`.

Publication reserves the digest and model version in PostgreSQL before writing
`sha256/<digest>/artifact.tar` to MinIO. A retry can recover an existing verified
object left by a failed database commit. It preserves bytes if an existing
object fails verification. The MinIO fixture assumes the registry is the only
object writer. Artifact approval, registry revocation, and robot credential
revocation do not recall bytes a robot already downloaded. The future agent
must enforce trust and activation policy at the point of use.
