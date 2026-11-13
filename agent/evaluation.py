"""Versioned evaluation evidence and the gate's shared policy contract."""

from pathlib import Path
from typing import Annotated, Literal

import yaml  # type: ignore[import-untyped]
from pydantic import Field, model_validator

from agent.artifacts import Contract, HexDigest, Nonempty, Nonnegative, Positive, sha256

SLICE_NAMES = (
    "nominal",
    "low_light",
    "motion_blur",
    "sensor_noise",
    "small_thin",
    "empty_scene",
    "fari_indoor",
)
Fraction = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
Count = Annotated[int, Field(ge=0)]


class SliceMetrics(Contract):
    images: Annotated[int, Field(gt=0)]
    boxes: Count
    map: Fraction | None
    ap50: Fraction | None
    false_positive_image_rate: Fraction
    p50_ms: Positive
    p95_ms: Positive

    @model_validator(mode="after")
    def consistent_metrics(self) -> "SliceMetrics":
        if (
            (self.map is None) != (self.boxes == 0)
            or (self.ap50 is None) != (self.boxes == 0)
            or self.p50_ms > self.p95_ms
        ):
            raise ValueError(
                "Slice metrics are inconsistent with its labels or timings"
            )
        return self


class CaseErrors(Contract):
    missed: Count
    false_positives: Count


class Evaluation(Contract):
    schema_version: Literal[1]
    artifact_digest: HexDigest
    dataset_digest: HexDigest
    report_sha256: HexDigest
    benchmark_profile: Nonempty
    peak_rss_bytes: Annotated[int, Field(gt=0)]
    slices: dict[str, SliceMetrics]
    overall: SliceMetrics
    known_cases: dict[str, CaseErrors]

    @model_validator(mode="after")
    def complete(self) -> "Evaluation":
        if (
            set(self.slices) != set(SLICE_NAMES)
            or self.overall.images
            != sum(metric.images for metric in self.slices.values())
            or self.overall.boxes
            != sum(metric.boxes for metric in self.slices.values())
            or not 100 <= self.overall.images <= 200
            or self.slices["empty_scene"].boxes != 0
            or any(
                self.slices[name].boxes == 0
                for name in SLICE_NAMES
                if name != "empty_scene"
            )
        ):
            raise ValueError("Evaluation must cover all seven labeled slices")
        return self


class Rule(Contract):
    images: Annotated[int, Field(gt=0)]
    min_map: Fraction | None = None
    max_false_positive_image_rate: Fraction | None = None


class GatePolicy(Contract):
    dataset_digest: HexDigest
    score_threshold: Fraction
    max_map_drop: Fraction
    max_p95_increase: Nonnegative
    slices: dict[str, Rule]
    known_cases: dict[str, CaseErrors]

    @model_validator(mode="after")
    def complete(self) -> "GatePolicy":
        if (
            set(self.slices) != set(SLICE_NAMES)
            or not self.known_cases
            or self.score_threshold != 0.5
            or any(
                (rule.min_map is None) != (name == "empty_scene")
                or (rule.max_false_positive_image_rate is None)
                != (name != "empty_scene")
                for name, rule in self.slices.items()
            )
        ):
            raise ValueError(
                "Policy requires a floor for every slice and known-case limits"
            )
        return self


def read_policy(path: Path) -> tuple[GatePolicy, str]:
    raw = path.read_bytes()
    return GatePolicy.model_validate(yaml.safe_load(raw)), sha256(raw)


def violations(
    policy: GatePolicy, candidate: Evaluation, baseline: Evaluation | None
) -> list[str]:
    if candidate.dataset_digest != policy.dataset_digest:
        return ["Dataset SHA-256 differs from the active gate policy"]
    if baseline and (
        baseline.dataset_digest != candidate.dataset_digest
        or baseline.benchmark_profile != candidate.benchmark_profile
    ):
        return ["Re-evaluate the approved baseline on the same dataset and CPU profile"]
    failures = []
    for name, rule in policy.slices.items():
        score = candidate.slices[name]
        previous = baseline.slices[name] if baseline else None
        if score.images != rule.images:
            failures.append(f"{name} has {score.images} images, expected {rule.images}")
        if rule.min_map is not None and score.map is not None:
            if score.map < rule.min_map:
                failures.append(
                    f"{name} mAP {score.map:.4f} below floor {rule.min_map:.4f}"
                )
            if previous and previous.map is not None:
                drop = previous.map - score.map
                if round(drop, 12) > policy.max_map_drop:
                    failures.append(
                        f"{name} mAP dropped {100 * drop:.2f} pp "
                        f"({previous.map:.4f} to {score.map:.4f}), "
                        f"limit {100 * policy.max_map_drop:.2f} pp"
                    )
        if rule.max_false_positive_image_rate is not None:
            cap = rule.max_false_positive_image_rate
            if score.false_positive_image_rate > cap:
                failures.append(
                    f"{name} false-positive image rate "
                    f"{score.false_positive_image_rate:.4f} "
                    f"exceeds cap {cap:.4f}"
                )
            if previous:
                rise = (
                    score.false_positive_image_rate - previous.false_positive_image_rate
                )
                if round(rise, 12) > policy.max_map_drop:
                    failures.append(
                        f"{name} false-positive image rate rose {100 * rise:.2f} pp, "
                        f"limit {100 * policy.max_map_drop:.2f} pp"
                    )
    if baseline:
        ratio = candidate.overall.p95_ms / baseline.overall.p95_ms - 1
        if round(ratio, 12) > policy.max_p95_increase:
            failures.append(
                f"Overall p95 latency increased {100 * ratio:.2f}% "
                f"({baseline.overall.p95_ms:.2f} to "
                f"{candidate.overall.p95_ms:.2f} ms), "
                f"limit {100 * policy.max_p95_increase:.2f}%"
            )
    for image, limit in policy.known_cases.items():
        observed = candidate.known_cases.get(image)
        if observed is None:
            failures.append(f"Known failure {image} is missing from the evaluation")
            continue
        prior = baseline.known_cases.get(image, limit) if baseline else limit
        for metric in ("missed", "false_positives"):
            actual = getattr(observed, metric)
            ceiling = min(getattr(limit, metric), getattr(prior, metric))
            if actual > ceiling:
                failures.append(
                    f"Known failure {image} has {actual} {metric}, limit {ceiling}"
                )
    return failures
