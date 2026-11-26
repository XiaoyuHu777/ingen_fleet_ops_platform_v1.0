"""Robot reconciliation tests exercise durable state across process lifetimes."""

import json
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, Thread
from typing import Any
from uuid import uuid4

import httpx
import pytest
from test_artifacts import make_signed

from agent.artifacts import ArtifactError, archive, canonical_json, read_archive, sha256
from agent.download import CHUNK, DownloadError, StagedDownload
from agent.runtime import Agent
from agent.settings import Settings, in_window
from agent.storage import SmokeResult
from agent.verify import BUNDLE_MEMBERS, Signature
from pipeline.sign import generate_key
from services.registry.schemas import DailySafeUpdateWindow

RESULT = SmokeResult(inference_latency_ms=12.5, synthetic_detection_rate=0.4)
CREDENTIAL = "robot-test-credential"


class Interrupted(httpx.SyncByteStream):
    def __init__(self, bundle: bytes) -> None:
        self.bundle = bundle

    def __iter__(self) -> Iterator[bytes]:
        yield self.bundle[: 2 * CHUNK]
        raise httpx.RemoteProtocolError("Link disconnected mid-download")


class Blocked(httpx.SyncByteStream):
    def __init__(self, bundle: bytes, started: Event, resume: Event) -> None:
        self.bundle = bundle
        self.started = started
        self.resume = resume

    def __iter__(self) -> Iterator[bytes]:
        yield self.bundle[:CHUNK]
        self.started.set()
        assert self.resume.wait(5)
        yield self.bundle[CHUNK:]


class Registry:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.desired: str | None = None
        self.revision = 0
        self.bundles: dict[str, bytes] = {}
        self.calls: list[httpx.Request] = []
        self.poll_statuses: list[int] = []
        self.sequence = 0
        self.report: dict[str, Any] = {}
        self.platform = "Fari"
        self.window: dict[str, Any] | None = {
            "start": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
            "end": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        }
        self.stream: httpx.SyncByteStream | None = None
        self.full_on_resume = False
        self.invalid_resume: str | None = None
        self.offline = False
        self.lose_heartbeat_response = False
        self.refuse_approval = False

    def activate(self, bundle: tuple[bytes, str]) -> str:
        data, digest = bundle
        self.bundles[digest] = data
        self.desired = digest
        self.revision += 1
        return digest

    def build_agent(self) -> Agent:
        agent = Agent(self.settings)
        headers = dict(agent.http.headers)
        agent.http.close()
        agent.http = httpx.Client(
            base_url=self.settings.registry_url,
            headers=headers,
            transport=httpx.MockTransport(self),
        )
        return agent

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        assert request.headers["Authorization"] == f"Bearer {CREDENTIAL}"
        assert request.headers["X-Request-ID"]
        if self.offline:
            raise httpx.ConnectError("Registry offline", request=request)
        if request.url.path.endswith("/desired-state"):
            return self.poll(request)
        if request.url.path.endswith("/heartbeat"):
            return self.heartbeat(request)
        assert request.url.path.endswith("/download")
        digest = request.url.path.split("/")[-2]
        return self.download(request, self.bundles[digest])

    def poll(self, request: httpx.Request) -> httpx.Response:
        target = f"sha256:{self.desired}" if self.desired else None
        body = json.dumps(
            {
                "target_digest": target,
                "set_by": "test-op",
                "reason": str(self.revision),
                "set_at": None,
            }
        ).encode()
        tag = f'"{sha256(body)}"'
        status = 304 if request.headers.get("If-None-Match") == tag else 200
        self.poll_statuses.append(status)
        return httpx.Response(
            status, headers={"ETag": tag}, content=body if status == 200 else b""
        )

    def heartbeat(self, request: httpx.Request) -> httpx.Response:
        self.report = json.loads(request.content)
        sequence = self.report["sequence"]
        if sequence <= self.sequence:
            return httpx.Response(409)
        self.sequence = sequence
        if self.lose_heartbeat_response:
            self.lose_heartbeat_response = False
            raise httpx.ReadTimeout("Response lost after acceptance", request=request)
        reported = self.report.copy()
        reported.pop("sequence")
        return httpx.Response(
            200,
            json={
                "id": self.settings.robot_id,
                "platform": self.platform,
                "site": "test-site",
                "hardware_profile": {},
                "cohort_labels": [],
                "safe_update_window": self.window,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
                "desired_state": {
                    "target_digest": None,
                    "set_by": None,
                    "reason": None,
                    "set_at": None,
                },
                "reported_state": {
                    **reported,
                    "heartbeat_sequence": sequence,
                    "last_seen": "2026-01-01T00:00:00Z",
                },
            },
        )

    def download(self, request: httpx.Request, bundle: bytes) -> httpx.Response:
        tag = f'"{sha256(bundle)}"'
        headers = {"ETag": tag, "Content-Length": str(len(bundle))}
        if request.method == "HEAD":
            return httpx.Response(403 if self.refuse_approval else 200, headers=headers)
        requested = request.headers.get("Range")
        if requested == "bytes=0-0":
            if self.refuse_approval:
                return httpx.Response(403)
            headers.update(
                {"Content-Range": f"bytes 0-0/{len(bundle)}", "Content-Length": "1"}
            )
            return httpx.Response(206, headers=headers, content=bundle[:1])
        if requested:
            offset = int(requested.removeprefix("bytes=").removesuffix("-"))
            assert request.headers["If-Range"] == tag
            invalid, self.invalid_resume = self.invalid_resume, None
            if invalid == "416":
                return httpx.Response(416)
            if not self.full_on_resume:
                start = offset + 1 if invalid == "offset" else offset
                headers["Content-Range"] = (
                    f"bytes {start}-{len(bundle)-1}/{len(bundle)}"
                )
                headers["Content-Length"] = str(len(bundle) - offset)
                if invalid == "etag":
                    headers["ETag"] = f'"{"b" * 64}"'
                return httpx.Response(
                    206, headers=headers, stream=httpx.ByteStream(bundle[offset:])
                )
        stream, self.stream = self.stream, None
        return httpx.Response(
            200, headers=headers, stream=stream or httpx.ByteStream(bundle)
        )


@pytest.fixture
def registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Registry:
    keys = tmp_path / "keys"
    generate_key(keys)
    credential = tmp_path / "credential"
    credential.write_text(CREDENTIAL)
    flag = tmp_path / "safe"
    flag.write_text("true\n")
    settings = Settings(
        robot_id=str(uuid4()),
        registry_url="http://test-registry",
        credential_file=credential,
        safe_flag=flag,
        trust_dir=keys / "public",
        state_dir=tmp_path / "agent",
    )
    monkeypatch.setattr("agent.runtime.smoke", lambda *_: (object(), RESULT))
    return Registry(settings)


def package(
    registry: Registry, tmp_path: Path, version: str, fill: bytes = b"M"
) -> tuple[bytes, str]:
    return make_signed(
        tmp_path, registry.settings.trust_dir.parent, fill * 120_000, version
    )


def test_fari_waits_for_window_and_local_flag_with_304(
    tmp_path: Path, registry: Registry
) -> None:
    digest = registry.activate(package(registry, tmp_path, "1.0.0"))
    window = registry.window
    registry.window = None
    agent = registry.build_agent()
    agent.heartbeat()
    agent.reconcile()
    assert not agent.state.active.exists()
    registry.window = window
    registry.settings.safe_flag.write_text("false")
    agent.heartbeat()
    assert registry.report["health_summary"]["safe_to_update"] is False
    agent.reconcile()
    assert not agent.state.active.exists()
    assert StagedDownload(agent.state.staging, digest).part.is_file()
    registry.settings.safe_flag.write_text("true")
    agent.reconcile()
    assert registry.poll_statuses == [200, 304, 304, 304]
    assert agent.state.active.readlink().as_posix() == "slots/A"
    agent.heartbeat()
    assert registry.report["current_digest"] == f"sha256:{digest}"
    assert registry.report["health_summary"] == {
        "status": "healthy",
        "status_reason": None,
        "inference_latency_ms": 12.5,
        "synthetic_detection_rate": 0.4,
        "error_count": 0,
        "smoke_test_failure_count": 0,
        "safe_to_update": True,
    }


def test_daily_fari_window_spans_midnight_and_repeated_hour() -> None:
    window = DailySafeUpdateWindow(
        start_local="22:00", end_local="03:00", timezone="America/New_York"
    )
    assert in_window(window, datetime(2026, 11, 1, 5, 30, tzinfo=UTC))
    assert in_window(window, datetime(2026, 11, 1, 6, 30, tzinfo=UTC))
    assert not in_window(window, datetime(2026, 11, 1, 8, tzinfo=UTC))


def test_resume_and_stage_isolation_after_interrupted_transfer(
    tmp_path: Path, registry: Registry
) -> None:
    bundle = package(registry, tmp_path, "1.0.0")
    digest = registry.activate(bundle)
    registry.stream = Interrupted(bundle[0])
    agent = registry.build_agent()
    agent.heartbeat()
    with pytest.raises(httpx.RemoteProtocolError):
        agent.reconcile()
    part = StagedDownload(agent.state.staging, digest).part
    assert part.stat().st_size == 2 * CHUNK
    assert not agent.state.active.exists()
    assert list(agent.state.models.iterdir()) == []
    assert list(agent.state.slots.iterdir()) == []
    agent.http.close()

    restarted = registry.build_agent()
    restarted.heartbeat()
    restarted.reconcile()
    ranges = [r.headers["Range"] for r in registry.calls if "Range" in r.headers]
    assert ranges == [f"bytes={2 * CHUNK}-", "bytes=0-0"]
    assert restarted.state.deployment.current is not None
    assert restarted.state.deployment.current.digest == digest
    assert not part.exists()


@pytest.mark.parametrize("response", ["full", "offset", "etag", "416"])
def test_resume_handles_full_or_invalid_range(
    tmp_path: Path, registry: Registry, response: str
) -> None:
    bundle = package(registry, tmp_path, "1.0.0")
    digest = registry.activate(bundle)
    registry.stream = Interrupted(bundle[0])
    agent = registry.build_agent()
    agent.heartbeat()
    with pytest.raises(httpx.HTTPError):
        agent.reconcile()
    if response == "full":
        registry.full_on_resume = True
    else:
        registry.invalid_resume = response
        with pytest.raises(DownloadError):
            agent.reconcile()
        assert not StagedDownload(agent.state.staging, digest).part.exists()
        assert not agent.state.active.exists()
    agent.reconcile()
    assert agent.state.deployment.current is not None
    assert agent.state.deployment.current.digest == digest


@pytest.mark.parametrize("error", ["invalid_signature", "digest_mismatch"])
def test_bad_signature_and_wrong_digest_never_activate(
    tmp_path: Path, registry: Registry, error: str
) -> None:
    signed, digest = package(registry, tmp_path, "1.0.0")
    if error == "invalid_signature":
        files = read_archive(signed, BUNDLE_MEMBERS)
        signature = Signature.model_validate_json(files["signature.json"])
        first = "0" if signature.signature[0] != "0" else "1"
        signature.signature = first + signature.signature[1:]
        files["signature.json"] = canonical_json(signature)
        signed = archive(files)
    else:
        digest = "f" * 64
    registry.activate((signed, digest))
    agent = registry.build_agent()
    agent.heartbeat()
    with pytest.raises(ArtifactError) as failure:
        agent.reconcile()
    assert failure.value.code == error
    assert not agent.state.active.exists()
    assert list(agent.state.models.iterdir()) == []
    assert not StagedDownload(agent.state.staging, digest).part.exists()
    labels = f'{{robot_id="{registry.settings.robot_id}"}}'
    assert (
        f"ingen_agent_verification_failures_total{labels} 1" in agent.metrics().decode()
    )


def test_ab_and_local_rollback_preserve_previous_without_controller(
    tmp_path: Path, registry: Registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    prior = registry.activate(package(registry, tmp_path, "1.0.0", b"A"))
    agent = registry.build_agent()
    agent.heartbeat()
    agent.reconcile()
    assert agent.state.active.readlink().as_posix() == "slots/A"
    current = registry.activate(package(registry, tmp_path, "1.1.0", b"B"))
    agent.reconcile()
    assert agent.state.active.readlink().as_posix() == "slots/B"
    assert (agent.state.slots / "A" / "model.onnx").read_bytes() == b"A" * 120_000
    agent.heartbeat()
    assert registry.report["current_digest"] == f"sha256:{current}"
    assert registry.report["previous_digest"] == f"sha256:{prior}"

    failed = registry.activate(package(registry, tmp_path, "1.2.0", b"C"))
    network_calls = []

    def fail_after_swap(path: Path, _manifest: object) -> None:
        assert path.read_bytes() == b"C" * 120_000
        assert agent.state.active.readlink().as_posix() == "slots/A"
        registry.offline = True
        network_calls.append(len(registry.calls))
        raise ValueError("fixture result differs")

    monkeypatch.setattr("agent.runtime.smoke", fail_after_swap)
    agent.reconcile()
    assert len(registry.calls) == network_calls[0]
    assert agent.state.active.readlink().as_posix() == "slots/B"
    assert (agent.state.active / "model.onnx").read_bytes() == b"B" * 120_000
    assert (agent.state.slots / "A" / "model.onnx").read_bytes() == b"A" * 120_000
    assert not (agent.state.models / failed).exists()
    assert agent.failures["smoke_test"] == 1

    registry.offline = False
    agent.heartbeat()
    assert registry.report["health_summary"]["status"] == "degraded"
    requests = len(registry.calls)
    agent.reconcile()
    assert len(registry.calls) == requests + 1
    assert registry.poll_statuses[-1] == 304


def test_recover_interrupted_activation_and_latch_target_after_restart(
    tmp_path: Path, registry: Registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    prior = registry.activate(package(registry, tmp_path, "1.0.0", b"A"))
    agent = registry.build_agent()
    agent.heartbeat()
    agent.reconcile()
    registry.activate(package(registry, tmp_path, "1.1.0", b"B"))

    def interrupted(*_: object) -> None:
        raise SystemExit("simulated process exit before smoke could complete")

    monkeypatch.setattr("agent.runtime.smoke", interrupted)
    with pytest.raises(SystemExit):
        agent.reconcile()
    assert agent.state.active.readlink().as_posix() == "slots/B"
    agent.http.close()
    registry.offline = True
    restarted = registry.build_agent()
    assert restarted.state.active.readlink().as_posix() == "slots/A"
    assert restarted.state.deployment.current is not None
    assert restarted.state.deployment.current.digest == prior
    registry.offline = False
    restarted.heartbeat()
    assert registry.report["health_summary"]["status"] == "degraded"
    before = len(registry.calls)
    restarted.reconcile()
    assert len(registry.calls) == before + 1


def test_loaded_bytes_still_require_current_download_approval(
    tmp_path: Path, registry: Registry
) -> None:
    registry.activate(package(registry, tmp_path, "1.0.0"))
    agent = registry.build_agent()
    registry.settings.safe_flag.write_text("false")
    agent.heartbeat()
    agent.reconcile()
    registry.settings.safe_flag.write_text("true")
    registry.refuse_approval = True
    with pytest.raises(httpx.HTTPStatusError) as error:
        agent.reconcile()
    assert error.value.response.status_code == 403
    assert not agent.state.active.exists()


def test_heartbeat_sequence_increases_after_accepted_response_is_lost(
    registry: Registry,
) -> None:
    registry.lose_heartbeat_response = True
    agent = registry.build_agent()
    with pytest.raises(httpx.ReadTimeout):
        agent.heartbeat()
    assert registry.sequence == 1
    agent.http.close()
    restarted = registry.build_agent()
    restarted.heartbeat()
    assert registry.sequence == 2
    assert restarted.sequence_path.read_text() == "2"


def test_heartbeat_does_not_wait_for_bundle_io(
    tmp_path: Path, registry: Registry
) -> None:
    bundle = package(registry, tmp_path, "1.0.0")
    registry.activate(bundle)
    started, resume = Event(), Event()
    registry.stream = Blocked(bundle[0], started, resume)
    agent = registry.build_agent()
    agent.heartbeat()
    worker = Thread(target=agent.reconcile)
    worker.start()
    try:
        assert started.wait(5)
        agent.heartbeat()
        assert registry.sequence == 2
        assert not agent.state.active.exists()
    finally:
        resume.set()
        worker.join(5)
    assert agent.state.active.is_symlink()


def test_degraded_digest_reports_week_six_signal(
    tmp_path: Path, registry: Registry
) -> None:
    digest = registry.activate(package(registry, tmp_path, "1.0.0"))
    registry.settings = replace(registry.settings, degraded_digest=digest)
    agent = registry.build_agent()
    agent.heartbeat()
    agent.reconcile()
    agent.heartbeat()
    assert registry.report["current_digest"] == f"sha256:{digest}"
    assert registry.report["health_summary"]["status"] == "degraded"
    assert registry.report["health_summary"]["status_reason"] == "simulated_digest"


@pytest.mark.parametrize("retained", ["intact", "tampered", "unapproved", "legacy"])
def test_controller_rollback_to_retained_slot(
    tmp_path: Path, registry: Registry, retained: str
) -> None:
    baseline = package(registry, tmp_path, "1.0.0", b"A")
    baseline_digest = registry.activate(baseline)
    agent = registry.build_agent()
    agent.heartbeat()
    agent.reconcile()
    candidate_digest = registry.activate(package(registry, tmp_path, "1.1.0", b"B"))
    agent.reconcile()
    agent.http.close()

    model_dir = agent.state.models / baseline_digest
    if retained == "tampered":
        (model_dir / "model.onnx").write_bytes(b"modified")
    if retained == "legacy":
        (model_dir / ".signature.json").unlink()
        (model_dir / ".manifest.json").unlink()
    registry.refuse_approval = retained == "unapproved"
    registry.activate(baseline)
    agent = registry.build_agent()
    agent.heartbeat()
    registry.calls.clear()
    if retained == "tampered":
        with pytest.raises(ArtifactError, match="Payload archive SHA-256 differs"):
            agent.reconcile()
        current = agent.state.deployment.current
        assert current is not None and current.digest == candidate_digest
        assert not any(r.url.path.endswith("/download") for r in registry.calls)
        return
    if retained == "unapproved":
        with pytest.raises(httpx.HTTPStatusError) as error:
            agent.reconcile()
        assert error.value.response.status_code == 403
        assert agent.state.active.readlink().as_posix() == "slots/B"
        return

    agent.reconcile()
    downloads = [r for r in registry.calls if r.url.path.endswith("/download")]
    if retained == "legacy":
        assert [request.method for request in downloads] == ["GET", "GET"]
        assert (model_dir / ".signature.json").is_file()
    else:
        assert [request.method for request in downloads] == ["HEAD"]
    assert agent.state.active.readlink().as_posix() == "slots/A"
    agent.heartbeat()
    assert registry.report["current_digest"] == f"sha256:{baseline_digest}"
    assert registry.report["previous_digest"] == f"sha256:{candidate_digest}"
