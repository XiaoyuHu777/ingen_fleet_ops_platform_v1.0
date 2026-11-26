"""Reconcile authenticated desired state and report independent heartbeats."""

import json
import logging
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from random import uniform
from threading import Event, Lock
from time import monotonic
from typing import Any
from uuid import uuid4

import httpx

from agent.artifacts import (
    ArtifactError,
    Manifest,
    archive,
    read_archive,
    read_manifest,
    sha256,
)
from agent.download import STRONG_TAG, DownloadError, StagedDownload
from agent.settings import Settings, in_window
from agent.smoke import smoke
from agent.storage import (
    SIGNED_ENVELOPE,
    SIGNED_MANIFEST,
    Deployment,
    Slot,
    SmokeResult,
    State,
    atomic_json,
    atomic_write,
    installed_manifest,
    read_json,
)
from agent.verify import BUNDLE_MEMBERS, load_trust, verify_bundle
from services.registry.schemas import DesiredOut, RobotOut

VERSION = "0.6.0"
FAILURES = ("download", "verification", "smoke_test")
LOGGER = logging.getLogger("ingen-agent")
STATE_ERRORS = (OSError, ValueError, httpx.HTTPError)


class Agent:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.state = State(settings.state_dir, settings.robot_id)
        self.state.cleanup()
        credential = settings.credential_file.read_text().strip()
        if not re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", credential):
            raise ValueError("Robot credential file is empty or invalid")
        self.http = httpx.Client(
            base_url=settings.registry_url,
            headers={
                "Authorization": f"Bearer {credential}",
                "Accept-Encoding": "identity",
            },
            timeout=10,
            follow_redirects=False,
        )
        self.robot_path = f"/v1/robots/{settings.robot_id}"
        self.desired: DesiredOut | None = None
        self.etag: str | None = None
        self.registration: tuple[RobotOut, float] | None = None
        self.sequence_path = self.state.root / "heartbeat-sequence"
        self.sequence = (
            int(self.sequence_path.read_text()) if self.sequence_path.is_file() else 0
        )
        self.failure_lock = Lock()
        self.failures = dict.fromkeys(FAILURES, 0)
        self.loaded: object | None = None
        self.attempt_path = self.state.root / "activation-attempt.json"
        attempt = read_json(self.attempt_path)
        self.rejected_etag: str | None = attempt["etag"] if attempt else None
        self.smoke_failed = self.rejected_etag is not None
        current = self.state.deployment.current
        if attempt and current and attempt["digest"] == current.digest:
            self.clear_attempt()
        self.last_heartbeat: float | None = None

    def log(self, event: str, request_id: str = "-", **fields: Any) -> None:
        LOGGER.info(
            json.dumps(
                {
                    "event": event,
                    "request-id": request_id,
                    "robot-id": self.settings.robot_id,
                    **fields,
                },
                separators=(",", ":"),
            )
        )

    @contextmanager
    def request(
        self,
        event: str,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
        **fields: Any,
    ) -> Iterator[httpx.Response]:
        request_id = str(uuid4())
        started = monotonic()
        status = None
        try:
            with self.http.stream(
                method,
                path,
                headers={"X-Request-ID": request_id, **(headers or {})},
                json=body,
            ) as response:
                status = response.status_code
                yield response
        finally:
            self.log(
                event,
                request_id,
                status=status,
                duration_ms=round((monotonic() - started) * 1000, 2),
                **fields,
            )

    def safe_flag(self) -> bool:
        try:
            return self.settings.safe_flag.read_text().strip() == "true"
        except (OSError, UnicodeError):
            return False

    def may_activate(self) -> bool:
        registration = self.registration
        if registration is None or not self.safe_flag():
            return False
        robot, received = registration
        if monotonic() - received > max(2 * self.settings.heartbeat_seconds, 5):
            return False
        window = robot.safe_update_window
        if robot.platform.casefold() == "fari" and window is None:
            return False
        return in_window(window, datetime.now(UTC))

    def health(self, result: SmokeResult | None, current: str | None) -> dict[str, Any]:
        simulated = current is not None and current == self.settings.degraded_digest
        degraded = simulated or self.smoke_failed
        with self.failure_lock:
            error_count = sum(self.failures.values())
            smoke_test_failure_count = self.failures["smoke_test"]
        return {
            "status": "degraded" if degraded else "healthy" if current else "starting",
            "status_reason": (
                "simulated_digest" if simulated else "smoke_test" if degraded else None
            ),
            "inference_latency_ms": result.inference_latency_ms if result else None,
            "synthetic_detection_rate": (
                result.synthetic_detection_rate if result else None
            ),
            "error_count": error_count,
            "smoke_test_failure_count": smoke_test_failure_count,
            "safe_to_update": self.safe_flag(),
        }

    def heartbeat(self) -> None:
        self.sequence += 1
        # Reserve before sending. A lost response cannot cause sequence reuse.
        atomic_write(self.sequence_path, str(self.sequence).encode("ascii"))
        deployment = self.state.deployment
        current, previous = deployment.current, deployment.previous
        with self.request(
            "heartbeat",
            "POST",
            f"{self.robot_path}/heartbeat",
            body={
                "sequence": self.sequence,
                "agent_version": VERSION,
                "current_digest": f"sha256:{current.digest}" if current else None,
                "previous_digest": f"sha256:{previous.digest}" if previous else None,
                "health_summary": self.health(
                    deployment.smoke, current.digest if current else None
                ),
            },
        ) as response:
            response.raise_for_status()
            robot = RobotOut.model_validate_json(response.read())
            if str(robot.id) != self.settings.robot_id:
                raise ValueError("Heartbeat returned registration for another robot")
            received = monotonic()
            self.registration = robot, received
            self.last_heartbeat = received

    def poll(self) -> DesiredOut:
        headers = {"If-None-Match": self.etag} if self.etag else {}
        with self.request(
            "desired_poll",
            "GET",
            f"{self.robot_path}/desired-state",
            headers=headers,
        ) as response:
            tag = response.headers.get("ETag", "")
            if response.status_code == 304:
                cached = self.desired
                if cached is None or self.etag != tag:
                    self.etag, self.desired = None, None
                    raise ValueError(
                        "Conditional poll returned no matching cached state"
                    )
                return cached
            response.raise_for_status()
            if response.status_code != 200 or not STRONG_TAG.fullmatch(tag):
                raise ValueError("Desired state response is missing its ETag")
            desired = DesiredOut.model_validate_json(response.read())
            self.etag, self.desired = tag, desired
            return desired

    def download(self, digest: str, staged: StagedDownload) -> tuple[str, int]:
        offset, tag, total = staged.resume()
        if tag is not None and total is not None and offset == total:
            return tag, total
        headers = (
            {"Range": f"bytes={offset}-", "If-Range": tag} if offset and tag else {}
        )
        with self.request(
            "bundle_download",
            "GET",
            f"/v1/artifacts/{digest}/download",
            headers=headers,
            digest=digest,
            range_offset=offset,
        ) as response:
            tag = staged.accept(response, offset, tag, total, self.settings.write_delay)
            validated_offset = offset if response.status_code == 206 else 0
        length = staged.part.stat().st_size
        self.log(
            "download_complete",
            digest=digest,
            bytes=length,
            resumed_bytes=validated_offset,
        )
        return tag, length

    def failed(self, category: str, error: BaseException, digest: str) -> None:
        with self.failure_lock:
            self.failures[category] += 1
        self.log(
            f"{category}_failed",
            digest=digest,
            error=getattr(error, "code", type(error).__name__),
        )

    def prepare_download(
        self, digest: str, staged: StagedDownload
    ) -> tuple[Slot, Manifest] | None:
        try:
            tag, length = self.download(digest, staged)
        except STATE_ERRORS as error:
            self.failed("download", error, digest)
            raise
        if not self.may_activate():
            return None
        try:
            bundle = staged.complete(tag, length)
            artifact = verify_bundle(
                bundle, load_trust(self.settings.trust_dir), digest
            )
            contents = read_archive(bundle, BUNDLE_MEMBERS)
            files = read_archive(contents["payload.tar"], set(artifact.manifest.files))
            files.setdefault("manifest.json", contents["manifest.json"])
            files[SIGNED_MANIFEST] = contents["manifest.json"]
            files[SIGNED_ENVELOPE] = contents["signature.json"]
        except (ArtifactError, ValueError, OSError) as error:
            self.failed("verification", error, digest)
            staged.discard()
            raise

        try:
            candidate = self.state.prepare(digest, files)
        except (OSError, ValueError) as error:
            self.failed("verification", error, digest)
            raise
        try:
            with self.request(
                "approval_recheck",
                "GET",
                f"/v1/artifacts/{digest}/download",
                headers={"Range": "bytes=0-0", "If-Range": tag},
                digest=digest,
            ) as response:
                staged.confirm_response(response, tag, length)
        except STATE_ERRORS as error:
            if isinstance(error, DownloadError):
                staged.discard()
            self.failed("download", error, digest)
            raise
        return candidate, artifact.manifest

    def authorize_retained(self, slot: Slot) -> Manifest:
        digest = slot.digest
        directory = self.state.models / digest
        try:
            raw = installed_manifest(directory)
            manifest = read_manifest(raw)
            payload = archive(
                {name: (directory / name).read_bytes() for name in manifest.files}
            )
            bundle = archive(
                {
                    "manifest.json": raw,
                    "payload.tar": payload,
                    "signature.json": (directory / SIGNED_ENVELOPE).read_bytes(),
                }
            )
            artifact = verify_bundle(
                bundle, load_trust(self.settings.trust_dir), digest
            )
        except (OSError, ValueError) as error:
            self.failed("verification", error, digest)
            raise

        try:
            with self.request(
                "cached_approval_recheck",
                "HEAD",
                f"/v1/artifacts/{digest}/download",
                digest=digest,
            ) as response:
                response.raise_for_status()
                expected = f'"{sha256(bundle)}"', len(bundle)
                if (
                    response.status_code != 200
                    or StagedDownload.headers(response) != expected
                ):
                    raise DownloadError(
                        "Approved representation differs from the retained model"
                    )
        except STATE_ERRORS as error:
            self.failed("download", error, digest)
            raise
        self.log("cached_artifact_verified", digest=digest, slot=slot.letter)
        return artifact.manifest

    def reconcile(self) -> None:
        desired = self.poll()
        if self.rejected_etag is not None and self.rejected_etag != self.etag:
            self.clear_attempt()
        target = desired.target_digest
        digest = target.removeprefix("sha256:") if target else None
        # Bound disk usage to the active/previous models and one desired bundle.
        for pattern in ("*.part", "*.json"):
            for old in self.state.staging.glob(pattern):
                if old.stem != digest:
                    StagedDownload(self.state.staging, old.stem).discard()
        deployment = self.state.deployment
        current, previous = deployment.current, deployment.previous
        if (
            digest is None
            or (current and current.digest == digest)
            or self.rejected_etag
        ):
            return
        staged = StagedDownload(self.state.staging, digest)
        if (
            previous
            and previous.digest == digest
            and (self.state.models / digest / SIGNED_ENVELOPE).is_file()
        ):
            if not self.may_activate():
                return
            candidate = previous
            manifest = self.authorize_retained(candidate)
        else:
            prepared = self.prepare_download(digest, staged)
            if prepared is None:
                return
            candidate, manifest = prepared
        # Recheck the target and local gate after the download and verification.
        if self.poll() != desired or not self.may_activate():
            self.state.cleanup()
            return

        assert self.etag is not None
        atomic_json(self.attempt_path, {"etag": self.etag, "digest": digest})
        self.rejected_etag = self.etag
        if not self.may_activate():
            self.clear_attempt()
            self.state.cleanup()
            return
        try:
            self.state.swap(candidate)
        except OSError as error:
            raise RuntimeError("Could not write activation pointers") from error
        try:
            model, result = smoke(self.state.active / "model.onnx", manifest)
        except Exception as error:
            # Handle loader/inference failures without a network request.
            try:
                self.state.restore()
            except OSError as rollback_error:
                raise RuntimeError(
                    "Could not restore committed model"
                ) from rollback_error
            self.smoke_failed = True
            self.failed("smoke_test", error, digest)
            self.log(
                "local_rollback",
                digest=digest,
                current_digest=current.digest if current else None,
            )
        else:
            try:
                self.state.commit(Deployment(candidate, current, result))
            except OSError as error:
                raise RuntimeError("Could not persist the deployed model") from error
            self.loaded = model
            self.clear_attempt()
            self.log(
                "artifact_activated",
                digest=digest,
                slot=candidate.letter,
                previous_digest=current.digest if current else None,
            )
        self.state.cleanup()
        staged.discard()

    def clear_attempt(self) -> None:
        atomic_json(self.attempt_path, None)
        self.rejected_etag = None
        self.smoke_failed = False

    def load_current(self) -> None:
        current = self.state.deployment.current
        if current is None:
            return
        try:
            raw = installed_manifest(self.state.active)
            if sha256(raw) != current.digest:
                raise ValueError(
                    "Installed manifest does not match the committed digest"
                )
            manifest = read_manifest(raw)
            path = self.state.active / "model.onnx"
            if sha256(path.read_bytes()) != manifest.files["model.onnx"]:
                raise ValueError(
                    "Installed model does not match the committed manifest"
                )
            self.loaded, _result = smoke(path, manifest)
        except Exception as error:
            self.smoke_failed = True
            self.failed("smoke_test", error, current.digest)

    def run_heartbeats(self, stop: Event) -> None:
        self.run_periodic(stop, self.settings.heartbeat_seconds, self.heartbeat)

    def run_reconciliation(self, stop: Event) -> None:
        self.load_current()
        self.run_periodic(stop, self.settings.poll_seconds, self.reconcile, jitter=True)

    def run_periodic(
        self,
        stop: Event,
        interval: float,
        operation: Callable[[], None],
        jitter: bool = False,
    ) -> None:
        failures = 0
        while not stop.is_set():
            delay = interval
            try:
                operation()
                failures = 0
            except STATE_ERRORS as error:
                failures = min(failures + 1, 8)
                self.log(
                    "agent_operation_failed",
                    operation=operation.__name__,
                    error=getattr(error, "code", type(error).__name__),
                )
                if jitter:
                    delay = max(interval, min(30, interval * 2**failures))
            stop.wait(delay * uniform(0.8, 1.2) if jitter else delay)

    def metrics(self) -> bytes:
        labels = f'robot_id="{self.settings.robot_id}"'
        messages = []
        with self.failure_lock:
            failures = self.failures.copy()
        for category, total in failures.items():
            name = f"ingen_agent_{category}_failures_total"
            messages.append(f"# TYPE {name} counter\n{name}{{{labels}}} {total}")
        seen = self.last_heartbeat
        age = str(monotonic() - seen) if seen is not None else "+Inf"
        name = "ingen_agent_heartbeat_age_seconds"
        messages.append(f"# TYPE {name} gauge\n{name}{{{labels}}} {age}")
        current = self.state.deployment.current
        digest = f"sha256:{current.digest}" if current else "unassigned"
        name = "ingen_agent_active_version"
        messages.append(f'# TYPE {name} gauge\n{name}{{{labels},digest="{digest}"}} 1')
        return ("\n".join(messages) + "\n").encode("ascii")
