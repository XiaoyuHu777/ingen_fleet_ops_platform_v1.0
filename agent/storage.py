"""Durable agent state and atomic on-device model selection."""

import json
import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

SIGNED_MANIFEST = ".manifest.json"
SIGNED_ENVELOPE = ".signature.json"


def installed_manifest(directory: Path) -> bytes:
    signed = directory / SIGNED_MANIFEST
    # Week 5 installations stored the manifest at the top level.
    return (signed if signed.is_file() else directory / "manifest.json").read_bytes()


def sync_dir(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(path: Path, data: bytes) -> None:
    scratch = path.with_name("." + path.name + ".tmp")
    with scratch.open("wb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    os.replace(scratch, path)
    sync_dir(path.parent)


def atomic_json(path: Path, value: object) -> None:
    atomic_write(path, json.dumps(value, separators=(",", ":")).encode("ascii"))


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return None


def link(path: Path, target: str | None) -> None:
    if target is None:
        path.unlink(missing_ok=True)
    elif not path.is_symlink() or os.readlink(path) != target:
        scratch = path.with_name("." + path.name + ".tmp")
        scratch.unlink(missing_ok=True)
        scratch.symlink_to(target)
        os.replace(scratch, path)
    sync_dir(path.parent)


@dataclass(frozen=True)
class Slot:
    letter: str
    digest: str


@dataclass(frozen=True)
class SmokeResult:
    inference_latency_ms: float
    synthetic_detection_rate: float


@dataclass(frozen=True)
class Deployment:
    current: Slot | None = None
    previous: Slot | None = None
    smoke: SmokeResult | None = None


class State:
    def __init__(self, root: Path, robot_id: str) -> None:
        self.root = root
        self.staging = root / "staging"
        self.slots = root / "slots"
        self.models = root / "models"
        for directory in (self.staging, self.slots, self.models):
            directory.mkdir(parents=True, exist_ok=True)
        self.active = root / "active"
        identity_path = root / "robot-id"
        if not identity_path.exists():
            atomic_write(identity_path, robot_id.encode("ascii"))
        if identity_path.read_text() != robot_id:
            raise ValueError("The local state belongs to another robot")
        saved = read_json(root / "deployment.json") or {}
        self.deployment = Deployment(
            Slot(**saved["current"]) if saved.get("current") else None,
            Slot(**saved["previous"]) if saved.get("previous") else None,
            SmokeResult(**saved["smoke"]) if saved.get("smoke") else None,
        )
        self.restore()

    @staticmethod
    def slot_target(slot: Slot) -> str:
        return f"../models/{slot.digest}"

    def restore(self) -> None:
        """Restore committed pointers after a failed or interrupted swap."""
        current = self.deployment.current
        previous = self.deployment.previous
        for slot in (current, previous):
            if slot and not (self.models / slot.digest / "model.onnx").is_file():
                raise ValueError("A committed model is missing")
        link(self.active, f"slots/{current.letter}" if current else None)
        members = {slot.letter: slot for slot in (current, previous) if slot}
        for letter in ("A", "B"):
            member = members.get(letter)
            link(self.slots / letter, self.slot_target(member) if member else None)

    def prepare(self, digest: str, files: dict[str, bytes]) -> Slot:
        destination = self.models / digest
        scratch = self.staging / f"model-{uuid4()}"
        scratch.mkdir()
        try:
            if destination.exists():
                for name, data in files.items():
                    path = destination / name
                    if name in (SIGNED_MANIFEST, SIGNED_ENVELOPE) and not path.exists():
                        # Populate the signature metadata when reusing a Week 5 slot.
                        continue
                    if path.read_bytes() != data:
                        raise ValueError(
                            "Installed bytes differ from the verified bundle"
                        )
                for name in (SIGNED_MANIFEST, SIGNED_ENVELOPE):
                    if not (destination / name).exists():
                        atomic_write(destination / name, files[name])
            else:
                for name, data in files.items():
                    atomic_write(scratch / name, data)
                os.replace(scratch, destination)
                sync_dir(self.models)
                sync_dir(self.staging)
        finally:
            if scratch.exists():
                shutil.rmtree(scratch)
        current = self.deployment.current
        return Slot("B" if current and current.letter == "A" else "A", digest)

    def swap(self, candidate: Slot) -> None:
        link(self.slots / candidate.letter, self.slot_target(candidate))
        link(self.active, f"slots/{candidate.letter}")

    def commit(self, deployment: Deployment) -> None:
        atomic_json(self.root / "deployment.json", asdict(deployment))
        self.deployment = deployment

    def cleanup(self) -> None:
        retained = {
            slot.digest
            for slot in (self.deployment.current, self.deployment.previous)
            if slot
        }
        for directory in self.models.iterdir():
            if directory.name not in retained:
                shutil.rmtree(directory)
        for directory in self.staging.glob("model-*"):
            shutil.rmtree(directory)
        sync_dir(self.models)
        sync_dir(self.staging)
