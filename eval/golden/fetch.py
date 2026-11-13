"""Materialize the frozen COCO and synthetic evaluation media."""

import argparse
import hashlib
import io
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import sleep
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

import numpy as np
from PIL import Image

ROOT = Path(__file__).parent
COCO = "https://s3.amazonaws.com/images.cocodataset.org/val2017/"


def transform(original: bytes | None, variant: str, seed: int) -> bytes:
    if original is None:
        # Procedural camera backgrounds with no objects from the COCO taxonomy.
        rng = np.random.default_rng(seed)
        y, x = np.indices((288, 384))
        base = 95 + rng.integers(-30, 31) + x / 13 + y / 16
        base += 12 * np.sin(x / (12 + seed % 11)) + 8 * np.cos(y / (17 + seed % 13))
        rgb = base[:, :, None] + rng.integers(-35, 36, size=3)
        rgb += rng.normal(0, 2, (288, 384, 3))
        pixels = np.clip(np.rint(rgb), 0, 255).astype(np.uint8)
    elif variant == "original":
        return original
    else:
        with Image.open(io.BytesIO(original)) as image:
            pixels = np.asarray(image.convert("RGB"))
        values = pixels.astype(np.float64)
        if variant == "low_light":
            values *= 0.22
        elif variant == "motion_blur":
            # Uniform 13-tap horizontal PSF, center aligned, clamp at image edges.
            width = pixels.shape[1]
            padded = np.pad(values, ((0, 0), (6, 6), (0, 0)), mode="edge")
            values = sum(padded[:, shift : shift + width] for shift in range(13)) / 13
        elif variant == "sensor_noise":
            values += np.random.default_rng(seed).normal(0, 18, pixels.shape)
        else:
            raise ValueError(f"Unknown transformation {variant}")
        pixels = np.clip(np.rint(values), 0, 255).astype(np.uint8)
    # Uncompressed PPM keeps the exact RGB bytes portable across encoders.
    height, width = pixels.shape[:2]
    return f"P6\n{width} {height}\n255\n".encode() + pixels.tobytes()


def write_verified(target: Path, data: bytes, expected: str) -> None:
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError(f"Image checksum mismatch: {target.name}")
    with NamedTemporaryFile(dir=target.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(data)
            stream.close()
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)


def source_bytes(target: Path, expected: str) -> bytes:
    if not re.fullmatch(r"[0-9]{12}\.jpg", target.name):
        raise ValueError("Invalid COCO source filename")
    if target.is_file():
        cached = target.read_bytes()
        if hashlib.sha256(cached).hexdigest() == expected:
            return cached
    for attempt in range(3):
        try:
            with urlopen(
                COCO + target.name + "?week4=original", timeout=45
            ) as response:
                data = response.read(8 * 1024 * 1024 + 1)
            write_verified(target, data, expected)
            return data
        except URLError:
            if attempt == 2:
                raise
            sleep(attempt + 1)
    raise AssertionError("Unreachable")


def fetch(root: Path, source_cache: Path) -> None:
    raw = (root / "labels.json").read_bytes()
    expected = (root / "DATASET.sha256").read_text().split()[0]
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("Dataset label manifest checksum mismatch")
    manifest = json.loads(raw)
    image_dir = root / "images"
    image_dir.mkdir(exist_ok=True)
    source_cache.mkdir(parents=True, exist_ok=True)

    # One worker owns each original and its derived frames.
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in manifest["images"]:
        source = row["source"]
        key = source["file_name"] if source else row["id"]
        grouped.setdefault(key, []).append(row)

    def materialize(rows: list[dict[str, Any]]) -> None:
        original = None
        for row in rows:
            target = root / row["path"]
            if target.parent != image_dir:
                raise ValueError(
                    "Image path must stay under the dataset image directory"
                )
            if (
                target.is_file()
                and hashlib.sha256(target.read_bytes()).hexdigest() == row["sha256"]
            ):
                continue
            if (source := row["source"]) and original is None:
                original = source_bytes(
                    source_cache / source["file_name"], source["sha256"]
                )
            content = transform(original, row["transform"], row["seed"])
            write_verified(target, content, row["sha256"])

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(materialize, grouped.values()))
    print(f"Verified {len(manifest['images'])} image files in {image_dir}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--source-cache", type=Path, default=Path(".week4/coco"))
    args = parser.parse_args()
    fetch(args.root, args.source_cache)
