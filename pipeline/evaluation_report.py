"""Generate the human-readable report from the same JSON as gate evidence."""

from pathlib import Path
from typing import Any


def write_report(output: Path, report: dict[str, Any]) -> None:
    def ap(value: float | None) -> str:
        return "n/a" if value is None else f"{100 * value:.2f}%"

    lines = [
        "# Detection evaluation",
        "",
        f"- Artifact SHA-256 `{report['artifact_digest']}`",
        f"- Dataset SHA-256 `{report['dataset_digest']}`",
        f"- Dataset labels `{report['dataset_label_version']}`",
        f"- Policy SHA-256 `{report['policy_digest']}`",
        f"- Benchmark `{report['benchmark_profile']}`",
        f"- Peak process RSS {report['peak_rss_bytes'] / 1048576:.1f} MiB",
        "",
        "| Slice | Images | Boxes | mAP 50–95 | AP50 | Images with FP |"
        " p50 ms | p95 ms |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, row in {"overall": report["overall"], **report["slices"]}.items():
        lines.append(
            f"| {name} | {row['images']} | {row['boxes']} | {ap(row['map'])} | "
            f"{ap(row['ap50'])} | {ap(row['false_positive_image_rate'])} | "
            f"{row['p50_ms']:.2f} | {row['p95_ms']:.2f} |"
        )
    lines.extend(
        [
            "",
            "The runner ranks named foreground scores above the background score, "
            "caps each image at 100 detections and averages 101-point interpolated AP "
            "over IoU 0.50–0.95 and classes with ground truth. Empty frames use "
            "false-positive image rate at confidence 0.50. ONNX inference timing "
            "excludes decode, preprocessing and reporting. RSS is the OS-reported "
            "process high-water mark.",
            "",
            "## Worst cases",
            "",
            "Two cases per slice, ranked by misses plus false positives at "
            "confidence 0.50 "
            "and IoU 0.50. Green marks ground truth. Orange marks predictions.",
            "",
        ]
    )
    for row in report["worst_cases"]:
        lines.extend(
            [
                f"### {row['image']}",
                "",
                f"{row['missed']} missed and {row['false_positives']} false positives.",
                f"![{row['image']}]({row['overlay']})",
                "",
            ]
        )
        if source := row["source"]:
            license_id = source["license"]
            change = (
                "bounding boxes"
                if row["transform"] == "original"
                else f"{row['transform'].replace('_', ' ')} and bounding boxes"
            )
            lines.extend(
                [
                    f"COCO image {source['image_id']} by {source['creator']}. "
                    f"Changes include {change}. "
                    f"[{source['title']}]({source['flickr_page']}) · "
                    f"[{license_id}]({source['license_url']})",
                    "",
                ]
            )
        else:
            lines.extend(
                [
                    "Procedural empty scene with evaluation overlays. "
                    "[CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/)",
                    "",
                ]
            )
    (output / "README.md").write_text("\n".join(lines), encoding="utf-8")
