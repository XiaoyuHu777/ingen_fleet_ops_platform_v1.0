"""Version 1 manifest and archive format shared by the builder and verifier."""

import hashlib
import io
import json
import tarfile
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

MAX_BUNDLE_BYTES = 64 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
HexDigest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
ModelName = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")]


def check_prerelease(value: str) -> str:
    prerelease = value.partition("+")[0].partition("-")[2]
    if any(
        part.isdecimal() and len(part) > 1 and part.startswith("0")
        for part in prerelease.split(".")
    ):
        raise ValueError("Numeric prerelease identifiers cannot have leading zeroes")
    return value


SemVer = Annotated[
    str,
    StringConstraints(
        max_length=80,
        pattern=(
            r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
            r"(-([0-9A-Za-z-]+\.)*[0-9A-Za-z-]+)?"
            r"(\+([0-9A-Za-z-]+\.)*[0-9A-Za-z-]+)?$"
        ),
    ),
    AfterValidator(check_prerelease),
]
FileName = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
]
Nonnegative = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Positive = Annotated[float, Field(gt=0, allow_inf_nan=False)]
Nonempty = Annotated[str, StringConstraints(min_length=1, max_length=512)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class InputContract(Contract):
    name: Literal["pixel_values"]
    shape: tuple[Literal[1], Literal[3], int, int]
    dtype: Literal["float32"]
    layout: Literal["NCHW"]
    color_order: Literal["RGB"]
    resize: Literal["bilinear_half_pixel_clamp_no_antialias"]
    uint8_scale: Literal[255]
    mean: tuple[Nonnegative, Nonnegative, Nonnegative]
    std: tuple[Positive, Positive, Positive]


class OutputContract(Contract):
    names: tuple[Literal["logits"], Literal["pred_boxes"]]
    logits_shape: tuple[Literal[1], int, int]
    boxes_shape: tuple[Literal[1], int, Literal[4]]
    dtype: Literal["float32"]
    box_format: Literal["normalized_cxcywh"]
    class_map: dict[str, str]
    no_object_index: int
    score_threshold: Annotated[float, Field(gt=0, lt=1)]


class Parity(Contract):
    fixture_sha256: HexDigest
    fixture_count: Annotated[int, Field(gt=0)]
    max_abs_error: dict[str, Nonnegative]
    tolerance: Positive


class Benchmark(Contract):
    model_sha256: HexDigest
    fixture_sha256: HexDigest
    input_contract_sha256: HexDigest
    onnxruntime: Nonempty
    host: Nonempty
    provider: Literal["CPUExecutionProvider"]
    threads: Literal[1]
    warmups: Annotated[int, Field(gt=0)]
    runs: Annotated[int, Field(gt=0)]
    p50_ms: Positive
    p95_ms: Positive


class Provenance(Contract):
    git_commit: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
    pipeline_version: Literal["1"]
    opset: Literal[17]
    seed: Literal[2026]
    libraries: dict[str, str]
    checkpoint_source: Nonempty
    checkpoint_revision: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
    checkpoint_sha256: HexDigest
    model_config_sha256: HexDigest


class Manifest(Contract):
    format_version: Literal[1]
    name: ModelName
    version: SemVer
    license: Nonempty
    training_data: list[Nonempty]
    inputs: InputContract
    outputs: OutputContract
    parity: Parity
    cpu_latency: Benchmark
    provenance: Provenance
    files: dict[FileName, HexDigest]
    payload_sha256: HexDigest

    @model_validator(mode="after")
    def validate_evidence(self) -> "Manifest":
        if (
            self.files.get("model.onnx") != self.cpu_latency.model_sha256
            or self.parity.fixture_sha256 != self.cpu_latency.fixture_sha256
            or sha256(canonical_json(self.inputs))
            != self.cpu_latency.input_contract_sha256
            or self.provenance.libraries.get("onnxruntime")
            != self.cpu_latency.onnxruntime
            or self.cpu_latency.p50_ms > self.cpu_latency.p95_ms
        ):
            raise ValueError("Benchmark evidence does not match the artifact")
        if (
            set(self.parity.max_abs_error) != set(self.outputs.names)
            or max(self.parity.max_abs_error.values()) >= self.parity.tolerance
        ):
            raise ValueError("Parity must pass for every output")
        return self


class ArtifactError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(value: BaseModel) -> bytes:
    return json.dumps(
        value.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def archive(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:", format=tarfile.USTAR_FORMAT) as tar:
        for name, data in sorted(files.items()):
            member = tarfile.TarInfo(name)
            member.size = len(data)
            member.mode = 0o644
            tar.addfile(member, io.BytesIO(data))
    return output.getvalue()


def read_archive(data: bytes, allowed: set[str]) -> dict[str, bytes]:
    if len(data) > MAX_BUNDLE_BYTES:
        raise ArtifactError("invalid_bundle", "Archive exceeds the bundle size limit")
    files: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tar:
            for member in tar:
                if (
                    member.type != tarfile.REGTYPE
                    or member.name not in allowed
                    or member.name in files
                    or not 0 <= member.size <= len(data)
                ):
                    raise ArtifactError("invalid_bundle", "Unexpected archive member")
                stream = tar.extractfile(member)
                assert stream is not None
                files[member.name] = stream.read()
    except (tarfile.TarError, EOFError, OverflowError) as error:
        raise ArtifactError("invalid_bundle", "Invalid tar archive") from error
    return files


def read_manifest(data: bytes) -> Manifest:
    if len(data) > MAX_MANIFEST_BYTES:
        raise ArtifactError("invalid_manifest", "Manifest exceeds the size limit")
    try:
        manifest = Manifest.model_validate_json(data)
    except ValidationError as error:
        raise ArtifactError("invalid_manifest", "Invalid manifest schema") from error
    if canonical_json(manifest) != data:
        raise ArtifactError("invalid_manifest", "Manifest must use canonical JSON")
    return manifest


def verify_payload(manifest: Manifest, payload: bytes) -> None:
    if sha256(payload) != manifest.payload_sha256:
        raise ArtifactError("payload_hash_mismatch", "Payload archive SHA-256 differs")
    files = read_archive(payload, set(manifest.files))
    if files.keys() != manifest.files.keys() or "model.onnx" not in files:
        raise ArtifactError("invalid_bundle", "Payload files differ from the manifest")
    for name, expected in manifest.files.items():
        if sha256(files[name]) != expected:
            raise ArtifactError("file_hash_mismatch", f"File SHA-256 differs: {name}")
