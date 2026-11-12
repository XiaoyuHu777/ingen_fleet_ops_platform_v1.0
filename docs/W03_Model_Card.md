# Model card — YOLOS-Tiny 1.0.0

## Artifact identity

| Item | Value |
| --- | --- |
| Registry name and version | `yolos-tiny`, `1.0.0` |
| Manifest SHA-256 | `0d29ccb5c62fc76be1e9502359c5d2be4867a91b1dfa3034926b683ce8e4b523` |
| Source commit | `0d669d9d8af32e25381b0fc27827ba48f430da11` |
| Registry status during demonstration | `published` in the local PostgreSQL and MinIO stack |
| Runtime | FP32 ONNX, opset 17, static batch size 1, 256 × 320 RGB |
| License | Apache-2.0 for the published checkpoint, with license and attribution included in the payload |
| Evidence | [Signed manifest contents](W03_Manifest_Example.json) and [validation report](W03_Test_Report.md) |

## Intended use and limitations

YOLOS-Tiny is a lightweight object detector with a COCO class map. This export
exercises the Rover artifact delivery path and provides a baseline for
subsequent offline evaluation. Out-of-scope uses include collision avoidance,
safety interlocks, physical actuation, and operational surveillance decisions.
The checkpoint's original processor preserves aspect ratio at a larger
resolution. The Week 3 export stretches both axes to a static shape, which
can distort small or thin objects. The behavior under motion blur, low light,
camera noise, weather and unseen classes needs labeled evaluation on
representative data.

The output consists of class logits and normalized center-format boxes. The
example profile uses a score threshold of 0.5. The Week 4 gate will determine
task quality and an operating threshold. The [artifact contract](W03_Artifact_Contract.md#preprocessing-and-outputs)
defines the tensor layouts, normalization, resize coordinates, class
selection and conversion back to source-image coordinates.

## Data and licensing

The upstream checkpoint was pre-trained on ImageNet-1k and fine-tuned for
object detection on COCO 2017. Week 3 performs conversion and parity checking
with no additional training. The upstream checkpoint and configuration
are pinned at revision `da86128da961944dd8e33bb7c1baea46ed0a4753` and both
downloads are checked by SHA-256. The checkpoint license and the source
datasets' terms are separate. [Pinned model card](https://huggingface.co/hustvl/yolos-tiny/tree/da86128da961944dd8e33bb7c1baea46ed0a4753)
· [ImageNet](https://www.image-net.org/download.php) · [COCO terms](https://cocodataset.org/#termsofuse)

The parity set comprises five generated RGB arrays representing a blank
image, a channel-asymmetric gradient, a dark gradient, a checkerboard and a
mirrored gradient. It contains no camera footage or annotations. The manifest
records the fixture hash and the benchmark host.

## Observed metrics

| Measurement | Result |
| --- | --- |
| Maximum absolute error in logits | 0.0003242493 |
| Maximum absolute error in normalized boxes | 0.0000854433 |
| Parity acceptance threshold | Strictly below 0.001 on every output across all five fixtures |
| ONNX Runtime 1.22.1 CPU latency | p50 62.40 ms, p95 64.83 ms |
| Benchmark setup | Apple M4 Pro, Darwin arm64, one runtime thread, 5 warmups and 25 timed inferences |

The tolerance allows FP32 runtime variation while keeping coordinate error
below 0.32 pixels on the 320-pixel input axis for any single normalized box
component. These measurements cover numerical parity and ONNX inference
latency. Labeled task metrics and Rover hardware measurements are pending
Week 4 evaluation and on-device validation.
