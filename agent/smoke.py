"""Load a verified model and exercise the signed CPU inference contract."""

import json
from pathlib import Path
from time import perf_counter

import numpy as np
import onnxruntime as ort  # type: ignore[import-untyped]
from numpy.typing import NDArray

from agent.artifacts import InputContract, Manifest, OutputContract, sha256
from agent.storage import SmokeResult


def fixtures() -> list[NDArray[np.uint8]]:
    """Use the five synthetic images defined by the Week 3 parity check."""
    y, x = np.indices((73, 109), dtype=np.int32)
    gradient = np.stack((3 * x + y, x + 5 * y, 7 * x - y), axis=-1).astype(np.uint8)
    checker = np.where((x // 7 + y // 5) % 2, 255, 0).astype(np.uint8)
    return [
        np.zeros_like(gradient),
        gradient,
        gradient // 12,
        np.stack((checker, 255 - checker, checker // 2), axis=-1),
        np.flip(gradient, axis=1).copy(),
    ]


def fixture_digest(images: list[NDArray[np.uint8]]) -> str:
    shapes = json.dumps([image.shape for image in images]).encode("ascii")
    return sha256(shapes + b"".join(image.tobytes() for image in images))


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
    result: NDArray[np.float32] = np.ascontiguousarray(
        normalized.transpose(2, 0, 1)[None]
    )
    return result


def detection_count(raw: list[NDArray[np.float32]], expected: OutputContract) -> int:
    logits, boxes = raw
    for tensor, shape in (
        (logits, expected.logits_shape),
        (boxes, expected.boxes_shape),
    ):
        if (
            tensor.shape != shape
            or tensor.dtype != np.float32
            or not np.isfinite(tensor).all()
        ):
            raise ValueError("Fixture output violates signed shape or dtype")
    if np.any((boxes < 0) | (boxes > 1)):
        raise ValueError("Fixture boxes violate signed normalized box coordinates")
    named = [int(index) for index, name in expected.class_map.items() if name != "N/A"]
    no_object = expected.no_object_index
    if (
        not named
        or not 0 <= no_object < logits.shape[2]
        or any(
            not 0 <= index < logits.shape[2] or index == no_object for index in named
        )
    ):
        raise ValueError("Signed class map is inconsistent with fixture logits")
    scores = np.exp(logits[0] - logits[0].max(axis=1, keepdims=True))
    scores /= scores.sum(axis=1, keepdims=True)
    best = scores[:, named].max(axis=1)
    return int(
        np.count_nonzero(
            (best >= expected.score_threshold)
            & (best > scores[:, no_object])
            & (boxes[0, :, 2:] > 0).all(axis=1)
        )
    )


def smoke(path: Path, manifest: Manifest) -> tuple[object, SmokeResult]:
    images = fixtures()
    if (
        len(images) != manifest.parity.fixture_count
        or fixture_digest(images) != manifest.parity.fixture_sha256
    ):
        raise ValueError("Synthetic fixtures differ from the signed manifest")
    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(
        path.read_bytes(), options, providers=["CPUExecutionProvider"]
    )
    latencies = []
    detections = []
    for image in images:
        pixels = preprocess(image, manifest.inputs)
        started = perf_counter()
        outputs = session.run(
            list(manifest.outputs.names), {manifest.inputs.name: pixels}
        )
        latencies.append((perf_counter() - started) * 1000)
        detections.append(detection_count(outputs, manifest.outputs))
    if (
        manifest.smoke_detections is not None
        and detections != manifest.smoke_detections
    ):
        raise ValueError("Fixture detection counts differ from the signed manifest")
    return session, SmokeResult(
        inference_latency_ms=float(np.percentile(latencies, 95)),
        synthetic_detection_rate=sum(count > 0 for count in detections) / len(images),
    )
