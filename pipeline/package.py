"""Export and package the pinned, open-licensed YOLOS-Tiny checkpoint on CPU."""

import argparse
import io
import json
import platform
import random
import subprocess
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from time import perf_counter
from typing import Annotated
from urllib.request import urlopen

import numpy as np
import onnx
import onnxruntime as ort
import torch
from pydantic import Field
from transformers import YolosConfig, YolosForObjectDetection

from agent.artifacts import (
    Benchmark,
    Contract,
    InputContract,
    Manifest,
    ModelName,
    OutputContract,
    Parity,
    Positive,
    Provenance,
    SemVer,
    archive,
    canonical_json,
    sha256,
)

SOURCE = "https://huggingface.co/hustvl/yolos-tiny"
REVISION = "da86128da961944dd8e33bb7c1baea46ed0a4753"
CHECKPOINT = {
    "config.json": "a171607bf33c3c85c2cda36e63f681e8b7fd101396b3f0f7a4aa40214020b86e",
    "model.safetensors": (
        "5a6a017a20cb522dd347271fa5bd670467e456176aaccd940090e50985ac6e74"
    ),
}
SEED = 2026
OPSET = 17
WARMUPS = 5
RUNS = 25
OUTPUTS = ("logits", "pred_boxes")
PIPELINE_DIR = Path(__file__).resolve().parent
TRAINING_DATA = [
    "ImageNet-1k pretraining: https://www.image-net.org/download.php",
    "COCO 2017 detection fine-tuning: https://cocodataset.org/#download",
]


class Profile(Contract):
    name: ModelName
    version: SemVer
    inputs: InputContract
    score_threshold: Annotated[float, Field(gt=0, lt=1)]
    parity_tolerance: Positive


class Detector(torch.nn.Module):
    def __init__(self, model: YolosForObjectDetection) -> None:
        super().__init__()
        self.model = model

    def forward(self, pixel_values: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        output = self.model(pixel_values=pixel_values)
        return output.logits, output.pred_boxes


@dataclass
class Export:
    graph: bytes
    fixture_inputs: list[np.ndarray]
    session: ort.InferenceSession
    parity: Parity
    outputs: OutputContract

    def run(self, pixels: np.ndarray) -> list[np.ndarray]:
        return self.session.run(list(OUTPUTS), {"pixel_values": pixels})


def fetch_checkpoint(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name, digest in CHECKPOINT.items():
        path = directory / name
        if path.is_file() and sha256(path.read_bytes()) == digest:
            continue
        with urlopen(f"{SOURCE}/resolve/{REVISION}/{name}", timeout=60) as response:
            data = response.read(32 * 1024 * 1024)
        if sha256(data) != digest:
            raise ValueError(f"Pinned checkpoint SHA-256 mismatch: {name}")
        path.write_bytes(data)


def fixtures() -> list[np.ndarray]:
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


def preprocess(pixels: np.ndarray, contract: InputContract) -> torch.Tensor:
    tensor = torch.from_numpy(pixels).permute(2, 0, 1).unsqueeze(0).float()
    tensor = torch.nn.functional.interpolate(
        tensor / contract.uint8_scale,
        size=contract.shape[2:],
        mode="bilinear",
        align_corners=False,
        antialias=False,
    )
    mean = tensor.new_tensor(contract.mean).reshape(1, 3, 1, 1)
    std = tensor.new_tensor(contract.std).reshape(1, 3, 1, 1)
    return ((tensor - mean) / std).contiguous()


def export(profile: Profile, checkpoint: Path) -> Export:
    for name, digest in CHECKPOINT.items():
        if sha256((checkpoint / name).read_bytes()) != digest:
            raise ValueError(f"Pinned checkpoint SHA-256 mismatch: {name}")
    height, width = profile.inputs.shape[2:]
    if any(size < 16 or size > 640 or size % 16 for size in (height, width)):
        raise ValueError("YOLOS dimensions must be multiples of 16 between 16 and 640")

    random.seed(SEED)
    np.random.seed(SEED)
    torch.manual_seed(SEED)
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    config = YolosConfig.from_json_file(str(checkpoint / "config.json"))
    model, loading = YolosForObjectDetection.from_pretrained(
        str(checkpoint),
        config=config,
        local_files_only=True,
        use_safetensors=True,
        attn_implementation="eager",
        output_loading_info=True,
    )
    if any(loading.values()):
        raise ValueError(f"Checkpoint failed to load exactly: {loading}")
    detector = Detector(model.eval()).eval()

    images = fixtures()
    inputs = [preprocess(image, profile.inputs) for image in images]
    fixture_description = json.dumps([image.shape for image in images]).encode("ascii")
    fixture_sha = sha256(
        fixture_description + b"".join(image.tobytes() for image in images)
    )
    with torch.inference_mode():
        output = io.BytesIO()
        torch.onnx.export(
            detector,
            (inputs[0],),
            output,
            input_names=[profile.inputs.name],
            output_names=list(OUTPUTS),
            opset_version=OPSET,
            dynamo=False,
        )
        graph = output.getvalue()
        onnx.checker.check_model(graph, full_check=True)
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        session = ort.InferenceSession(
            graph, options, providers=["CPUExecutionProvider"]
        )
        errors = dict.fromkeys(OUTPUTS, 0.0)
        for pixels in inputs:
            pytorch = detector(pixels)
            actual = session.run(list(OUTPUTS), {profile.inputs.name: pixels.numpy()})
            for name, expected, result in zip(OUTPUTS, pytorch, actual, strict=True):
                if result.shape != tuple(expected.shape) or result.dtype != np.float32:
                    raise ValueError(f"ONNX output contract differs: {name}")
                error = float(np.max(np.abs(expected.numpy() - result)))
                if not np.isfinite(error) or error >= profile.parity_tolerance:
                    raise ValueError(
                        f"Parity failed for {name}: max absolute error {error}"
                    )
                errors[name] = max(errors[name], error)

    return Export(
        graph=graph,
        fixture_inputs=[pixels.numpy() for pixels in inputs],
        session=session,
        parity=Parity(
            fixture_sha256=fixture_sha,
            fixture_count=len(inputs),
            max_abs_error=errors,
            tolerance=profile.parity_tolerance,
        ),
        outputs=OutputContract(
            names=OUTPUTS,
            logits_shape=(1, config.num_detection_tokens, config.num_labels + 1),
            boxes_shape=(1, config.num_detection_tokens, 4),
            dtype="float32",
            box_format="normalized_cxcywh",
            class_map={str(label): name for label, name in config.id2label.items()},
            no_object_index=config.num_labels,
            score_threshold=profile.score_threshold,
        ),
    )


def benchmark(exported: Export, inputs: InputContract) -> Benchmark:
    images = exported.fixture_inputs
    for index in range(WARMUPS):
        exported.run(images[index % len(images)])
    timings = []
    for index in range(RUNS):
        start = perf_counter()
        exported.run(images[index % len(images)])
        timings.append((perf_counter() - start) * 1000)
    host = " ".join((platform.system(), platform.release(), platform.machine()))
    if platform.system() == "Darwin":
        cpu = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        if cpu:
            host = f"{host} ({cpu})"
    return Benchmark(
        model_sha256=sha256(exported.graph),
        fixture_sha256=exported.parity.fixture_sha256,
        input_contract_sha256=sha256(canonical_json(inputs)),
        onnxruntime=version("onnxruntime"),
        host=host,
        provider="CPUExecutionProvider",
        threads=1,
        warmups=WARMUPS,
        runs=RUNS,
        p50_ms=round(float(np.percentile(timings, 50)), 6),
        p95_ms=round(float(np.percentile(timings, 95)), 6),
    )


def build(
    profile: Profile, exported: Export, evidence: Benchmark, destination: Path
) -> str:
    model_sha = sha256(exported.graph)
    if (
        evidence.model_sha256 != model_sha
        or evidence.fixture_sha256 != exported.parity.fixture_sha256
        or evidence.input_contract_sha256 != sha256(canonical_json(profile.inputs))
        or evidence.onnxruntime != version("onnxruntime")
    ):
        raise ValueError(
            "Benchmark evidence does not match this ONNX graph and fixture set"
        )

    files = {
        "model.onnx": exported.graph,
        "LICENSE.txt": (PIPELINE_DIR / "YOLOS-LICENSE.txt").read_bytes(),
        "NOTICE.txt": (PIPELINE_DIR / "YOLOS-NOTICE.txt").read_bytes(),
    }
    payload = archive(files)
    repo = PIPELINE_DIR.parent
    changed = subprocess.check_output(
        ["git", "-C", str(repo), "status", "--porcelain", "--", "agent", "pipeline"],
        text=True,
    )
    if changed:
        raise ValueError("Commit the builder and verifier sources before packaging")
    commit = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    manifest = Manifest(
        format_version=1,
        name=profile.name,
        version=profile.version,
        license="Apache-2.0",
        training_data=TRAINING_DATA,
        inputs=profile.inputs,
        outputs=exported.outputs,
        parity=exported.parity,
        cpu_latency=evidence,
        provenance=Provenance(
            git_commit=commit,
            pipeline_version="1",
            opset=OPSET,
            seed=SEED,
            libraries={
                name: version(name)
                for name in (
                    "numpy",
                    "onnx",
                    "onnxruntime",
                    "torch",
                    "transformers",
                    "safetensors",
                )
            },
            checkpoint_source=SOURCE,
            checkpoint_revision=REVISION,
            checkpoint_sha256=CHECKPOINT["model.safetensors"],
            model_config_sha256=CHECKPOINT["config.json"],
        ),
        files={name: sha256(data) for name, data in files.items()},
        payload_sha256=sha256(payload),
    )
    destination.mkdir(parents=True, exist_ok=True)
    manifest_bytes = canonical_json(manifest)
    (destination / "payload.tar").write_bytes(payload)
    (destination / "manifest.json").write_bytes(manifest_bytes)
    return sha256(manifest_bytes)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("fetch")
    fetch.add_argument("checkpoint", type=Path)
    for name in ("benchmark", "build"):
        command = commands.add_parser(name)
        command.add_argument("--checkpoint", required=True, type=Path)
        command.add_argument(
            "--config", type=Path, default=PIPELINE_DIR / "yolos-tiny.json"
        )
        command.add_argument("--out", required=True, type=Path)
        if name == "build":
            command.add_argument("--benchmark", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "fetch":
        fetch_checkpoint(args.checkpoint)
        return
    profile = Profile.model_validate_json(args.config.read_bytes())
    exported = export(profile, args.checkpoint)
    if args.command == "benchmark":
        evidence = benchmark(exported, profile.inputs)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_bytes(canonical_json(evidence) + b"\n")
        print(f"Parity max absolute error: {exported.parity.max_abs_error}")
        print(f"CPU p50 {evidence.p50_ms:.3f} ms, p95 {evidence.p95_ms:.3f} ms")
    else:
        evidence = Benchmark.model_validate_json(args.benchmark.read_bytes())
        digest = build(profile, exported, evidence, args.out)
        print(f"packaged sha256:{digest}")


if __name__ == "__main__":
    main()
