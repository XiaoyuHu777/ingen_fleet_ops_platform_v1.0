"""Score class-aware detection boxes on the frozen non-crowd dataset."""

from collections import defaultdict
from dataclasses import dataclass
from typing import TypeAlias

Box: TypeAlias = tuple[float, float, float, float]


@dataclass(frozen=True)
class Detection:
    category: int
    box: Box
    score: float = 1.0


def iou(a: Box, b: Box) -> float:
    ax, ay, ar, ab = a
    bx, by, br, bb = b
    intersection = max(0.0, min(ar, br) - max(ax, bx)) * max(
        0.0, min(ab, bb) - max(ay, by)
    )
    return intersection / ((ar - ax) * (ab - ay) + (br - bx) * (bb - by) - intersection)


def average_precision(
    scored: list[tuple[str, Detection]],
    labels: dict[str, list[Detection]],
    limit: float,
) -> float:
    total = sum(map(len, labels.values()))
    matched: dict[str, set[int]] = defaultdict(set)
    precision: list[tuple[int, float]] = []
    true_positives = 0
    for image, prediction in sorted(scored, key=lambda pair: (-pair[1].score, pair[0])):
        overlaps = (
            (iou(prediction.box, target.box), index)
            for index, target in enumerate(labels.get(image, []))
            if index not in matched[image]
        )
        best, index = max(overlaps, default=(0.0, -1))
        if best >= limit:
            matched[image].add(index)
            true_positives += 1
        precision.append((true_positives, true_positives / (len(precision) + 1)))
    envelope = [0.0] * (total + 1)
    for count, value in precision:
        envelope[count] = max(envelope[count], value)
    for count in range(total - 1, -1, -1):
        envelope[count] = max(envelope[count], envelope[count + 1])
    return sum(envelope[(step * total + 99) // 100] for step in range(101)) / 101


def mean_average_precision(
    labels: dict[str, list[Detection]], predictions: dict[str, list[Detection]]
) -> tuple[float | None, float | None]:
    categories = sorted(
        {label.category for items in labels.values() for label in items}
    )
    if not categories:
        return None, None
    ap = []
    for category in categories:
        truth = {
            image: [item for item in items if item.category == category]
            for image, items in labels.items()
        }
        ranked = [
            (image, item)
            for image, items in predictions.items()
            for item in items
            if item.category == category
        ]
        ap.append(
            [
                average_precision(ranked, truth, value / 100)
                for value in range(50, 100, 5)
            ]
        )
    return sum(map(sum, ap)) / (10 * len(ap)), sum(row[0] for row in ap) / len(ap)


def errors(
    labels: list[Detection], predictions: list[Detection], threshold: float
) -> tuple[int, int]:
    missed = set(range(len(labels)))
    false_positives = 0
    for prediction in predictions:
        if prediction.score < threshold:
            break
        best, index = max(
            (
                (iou(prediction.box, labels[index].box), index)
                for index in missed
                if prediction.category == labels[index].category
            ),
            default=(0.0, -1),
        )
        if best >= 0.5:
            missed.remove(index)
        else:
            false_positives += 1
    return len(missed), false_positives
