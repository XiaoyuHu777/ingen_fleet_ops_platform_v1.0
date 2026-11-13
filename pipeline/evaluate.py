"""Evaluate a signed YOLOS ONNX bundle directly from its manifest on CPU."""

import argparse
import io
import json
import platform
import resource
import subprocess
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

import numpy as np
import onnxruntime as ort  # type: ignore[import-untyped]
from numpy.typing import NDArray
from PIL import Image, ImageDraw

from agent.artifacts import (
    MAX_BUNDLE_BYTES,
    InputContract,
    OutputContract,
    canonical_json,
    read_archive,
    sha256,
)
from agent.evaluation import (
    SLICE_NAMES,
    CaseErrors,
    Evaluation,
    SliceMetrics,
    read_policy,
)
from agent.verify import BUNDLE_MEMBERS, load_trust, verify_bundle
from pipeline.detection_metrics import Detection, errors, mean_average_precision
from pipeline.evaluation_report import write_report

WARMUPS = 5
EVALUATOR_PROFILE = "cpu-1thread-5warmup-v1"


@dataclass
class Frame:
    id: str
    slice: str
    path: Path
    labels: list[Detection]
    predictions: list[Detection]
    latency_ms: float
    errors: CaseErrors


def preprocess(rgb: NDArray[np.uint8], contract: InputContract) -> NDArray[np.float32]:
    """Apply the manifest's FP32 half-pixel RGB contract without PyTorch."""
    height, width = rgb.shape[:2]
    out_h, out_w = contract.shape[2:]
    y = np.clip(
        (np.arange(out_h, dtype=np.float32) + 0.5) * (height / out_h) - 0.5,
        0,
        height - 1,
    )
    x = np.clip(
        (np.arange(out_w, dtype=np.float32) + 0.5) * (width / out_w) - 0.5, 0, width - 1
    )
    y0, x0 = y.astype(int), x.astype(int)
    y1, x1 = np.minimum(y0 + 1, height - 1), np.minimum(x0 + 1, width - 1)
    wy = (y - y0).astype(np.float32)[:, None, None]
    wx = (x - x0).astype(np.float32)[None, :, None]
    pixels = rgb.astype(np.float32) / np.float32(contract.uint8_scale)
    top = pixels[y0[:, None], x0] * (1 - wx) + pixels[y0[:, None], x1] * wx
    bottom = pixels[y1[:, None], x0] * (1 - wx) + pixels[y1[:, None], x1] * wx
    normalized = (top * (1 - wy) + bottom * wy) - np.asarray(
        contract.mean, dtype=np.float32
    )
    normalized /= np.asarray(contract.std, dtype=np.float32)
    return np.ascontiguousarray(normalized.transpose(2, 0, 1)[None])


def decode(
    raw: list[NDArray[np.float32]], contract: OutputContract, width: int, height: int
) -> list[Detection]:
    logits, boxes = raw
    for value, shape in (
        (logits, contract.logits_shape),
        (boxes, contract.boxes_shape),
    ):
        if (
            value.shape != shape
            or value.dtype != np.float32
            or not np.isfinite(value).all()
        ):
            raise ValueError("ONNX outputs violate manifest shape, dtype or finiteness")
    scores = np.exp(logits[0] - logits[0].max(axis=1, keepdims=True))
    scores /= scores.sum(axis=1, keepdims=True)
    classes = sorted(
        int(key) for key, value in contract.class_map.items() if value != "N/A"
    )
    if (
        not classes
        or not 0 <= contract.no_object_index < scores.shape[1]
        or any(
            not 0 <= category < scores.shape[1] or category == contract.no_object_index
            for category in classes
        )
    ):
        raise ValueError("Manifest class map is inconsistent with ONNX logits")
    best = scores[:, classes].argmax(axis=1)
    detections = []
    for row, ((cx, cy, bw, bh), best_index) in enumerate(
        zip(boxes[0], best, strict=True)
    ):
        category = classes[int(best_index)]
        score = float(scores[row, category])
        if score <= float(scores[row, contract.no_object_index]):
            continue
        left, right = np.clip((cx - bw / 2, cx + bw / 2), 0, 1) * width
        top, bottom = np.clip((cy - bh / 2, cy + bh / 2), 0, 1) * height
        if left < right and top < bottom:
            detections.append(
                Detection(
                    category,
                    (float(left), float(top), float(right), float(bottom)),
                    score,
                )
            )
    return sorted(detections, key=lambda item: -item.score)[:100]


def summarize(frames: list[Frame]) -> SliceMetrics:
    metrics = mean_average_precision(
        {frame.id: frame.labels for frame in frames},
        {frame.id: frame.predictions for frame in frames},
    )
    latency = [frame.latency_ms for frame in frames]
    return SliceMetrics(
        images=len(frames),
        boxes=sum(len(frame.labels) for frame in frames),
        map=metrics[0],
        ap50=metrics[1],
        false_positive_image_rate=sum(
            frame.errors.false_positives > 0 for frame in frames
        )
        / len(frames),
        p50_ms=float(np.percentile(latency, 50)),
        p95_ms=float(np.percentile(latency, 95)),
    )


def render_overlay(
    frame: Frame, path: Path, class_map: dict[str, str], threshold: float
) -> None:
    with Image.open(frame.path) as source:
        image = source.convert("RGB")
    draw = ImageDraw.Draw(image)
    for items, color, prefix in (
        (frame.labels, "#39f477", "GT"),
        (
            [item for item in frame.predictions if item.score >= threshold],
            "#ffad3d",
            "P",
        ),
    ):
        for box in items:
            draw.rectangle(box.box, outline=color, width=3)
            text = f"{prefix} {class_map.get(str(box.category), str(box.category))}"
            if prefix == "P":
                text += f" {box.score:.2f}"
            left, top = box.box[:2]
            draw.text(
                (left + 2, max(0, top - 13)),
                text,
                fill=color,
                stroke_width=2,
                stroke_fill="#111111",
            )
    image.save(path, quality=88)


def host_profile() -> str:
    cpu = platform.processor()
    if platform.system() == "Darwin":
        cpu = (
            subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                text=True,
                capture_output=True,
                check=False,
            ).stdout.strip()
            or cpu
        )
    elif Path("/proc/cpuinfo").is_file():
        cpu = next(
            (
                line.split(":", 1)[1].strip()
                for line in Path("/proc/cpuinfo").read_text().splitlines()
                if line.startswith("model name")
            ),
            cpu,
        )
    host = f"{platform.system()} {platform.release()} {platform.machine()} {cpu}"
    return f"{EVALUATOR_PROFILE}/{host}/ORT {ort.__version__}"


def evaluate(
    bundle: Path, trust: Path, golden: Path, policy_path: Path, output: Path
) -> Evaluation:
    policy, policy_digest = read_policy(policy_path)
    with bundle.open("rb") as stream:
        data = stream.read(MAX_BUNDLE_BYTES + 1)
    verified = verify_bundle(data, load_trust(trust))
    manifest = verified.manifest
    payload = read_archive(data, BUNDLE_MEMBERS)["payload.tar"]
    graph = read_archive(payload, set(manifest.files))["model.onnx"]
    raw_labels = (golden / "labels.json").read_bytes()
    dataset_digest = sha256(raw_labels)
    if dataset_digest != policy.dataset_digest:
        raise ValueError("Dataset digest does not match the active gate policy")
    labels = json.loads(raw_labels)
    if (
        labels["schema_version"] != 1
        or manifest.outputs.score_threshold != policy.score_threshold
    ):
        raise ValueError(
            "Dataset schema or manifest operating threshold is incompatible with policy"
        )

    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(graph, options, providers=["CPUExecutionProvider"])
    inputs = session.get_inputs()
    if len(inputs) != 1 or (inputs[0].name, inputs[0].type, inputs[0].shape) != (
        manifest.inputs.name,
        "tensor(float)",
        list(manifest.inputs.shape),
    ):
        raise ValueError("ONNX input violates manifest name, shape or dtype")

    frames = []
    image_ids = set()
    provenance = {}
    for index, row in enumerate(labels["images"]):
        path = golden / row["path"]
        raw = path.read_bytes()
        if sha256(raw) != row["sha256"] or row["id"] in image_ids:
            raise ValueError(f"Image hash mismatch or duplicate id: {row['id']}")
        image_ids.add(row["id"])
        provenance[row["id"]] = (row["source"], row["transform"])
        with Image.open(io.BytesIO(raw)) as image:
            width, height = image.size
            if (width, height) != (row["width"], row["height"]):
                raise ValueError(f"Image dimensions differ from labels: {row['id']}")
            rgb = np.asarray(image.convert("RGB"))
        truth = [
            Detection(box["category_id"], tuple(box["xyxy"])) for box in row["boxes"]
        ]
        if any(
            manifest.outputs.class_map.get(str(box.category), "N/A") == "N/A"
            for box in truth
        ):
            raise ValueError(f"Manifest class map omits ground truth: {row['id']}")
        tensor = {manifest.inputs.name: preprocess(rgb, manifest.inputs)}
        names = list(manifest.outputs.names)
        if index == 0:
            for _ in range(WARMUPS):
                session.run(names, tensor)
        start = perf_counter()
        result = session.run(names, tensor)
        elapsed = (perf_counter() - start) * 1000
        predictions = decode(result, manifest.outputs, width, height)
        missed, false_positives = errors(
            truth, predictions, manifest.outputs.score_threshold
        )
        frames.append(
            Frame(
                row["id"],
                row["slice"],
                path,
                truth,
                predictions,
                elapsed,
                CaseErrors(missed=missed, false_positives=false_positives),
            )
        )

    profile = host_profile()
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (
        1 if platform.system() == "Darwin" else 1024
    )
    output.mkdir(parents=True, exist_ok=True)
    overlays = output / "overlays"
    overlays.mkdir(exist_ok=True)
    per_slice = {}
    known_cases = {
        frame.id: frame.errors for frame in frames if frame.id in policy.known_cases
    }
    overall = summarize(frames)
    cases = []
    for slice_name in SLICE_NAMES:
        selected = [frame for frame in frames if frame.slice == slice_name]
        per_slice[slice_name] = summarize(selected)
        ranked = sorted(
            selected,
            key=lambda f: (
                -(f.errors.missed + f.errors.false_positives),
                -f.errors.missed,
                f.id,
            ),
        )
        for frame in ranked[:2]:
            overlay = f"overlays/{frame.id}.jpg"
            render_overlay(
                frame,
                output / overlay,
                manifest.outputs.class_map,
                policy.score_threshold,
            )
            source, transform = provenance[frame.id]
            cases.append(
                {
                    "image": frame.id,
                    "slice": frame.slice,
                    **frame.errors.model_dump(),
                    "overlay": overlay,
                    "source": source,
                    "transform": transform,
                    "sha256": sha256((output / overlay).read_bytes()),
                }
            )
    report = {
        "artifact_digest": verified.digest,
        "dataset_digest": dataset_digest,
        "dataset_label_version": labels["label_version"],
        "policy_digest": policy_digest,
        "benchmark_profile": profile,
        "peak_rss_bytes": rss,
        "slices": {name: value.model_dump() for name, value in per_slice.items()},
        "overall": overall.model_dump(),
        "worst_cases": cases,
        "known_cases": {
            name: value.model_dump() for name, value in known_cases.items()
        },
        "image_errors": {frame.id: frame.errors.model_dump() for frame in frames},
    }
    content = (
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode()
    (output / "report.json").write_bytes(content)
    evidence = Evaluation(
        schema_version=1,
        artifact_digest=verified.digest,
        dataset_digest=dataset_digest,
        report_sha256=sha256(content),
        benchmark_profile=profile,
        peak_rss_bytes=rss,
        slices=per_slice,
        overall=overall,
        known_cases=known_cases,
    )
    (output / "evidence.json").write_bytes(canonical_json(evidence) + b"\n")
    write_report(output, report)
    return evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--trust", type=Path, required=True)
    parser.add_argument("--golden", type=Path, default=Path("eval/golden"))
    parser.add_argument("--policy", type=Path, default=Path("policy/gate.yaml"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate(args.bundle, args.trust, args.golden, args.policy, args.out)
    print(
        f"Evaluated sha256:{result.artifact_digest} on sha256:{result.dataset_digest}"
    )
