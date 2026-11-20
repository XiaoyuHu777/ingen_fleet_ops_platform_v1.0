"""Robot agent configuration and interpretation of registered update windows."""

import os
from dataclasses import dataclass
from datetime import datetime, time
from math import isfinite
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID
from zoneinfo import ZoneInfo

from services.registry.schemas import DailySafeUpdateWindow, SafeUpdateWindow


@dataclass(frozen=True)
class Settings:
    robot_id: str
    registry_url: str
    credential_file: Path
    state_dir: Path
    trust_dir: Path
    safe_flag: Path
    poll_seconds: float = 5
    heartbeat_seconds: float = 5
    write_delay: float = 0
    degraded_digest: str | None = None
    metrics_bind: tuple[str, int] = ("127.0.0.1", 9101)

    @classmethod
    def from_env(cls) -> "Settings":
        def value(name: str, default: str = "") -> str:
            return os.environ.get("INGEN_AGENT_" + name, default)

        robot_id = str(UUID(value("ROBOT_ID")))
        url = value("REGISTRY_URL").rstrip("/")
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("INGEN_AGENT_REGISTRY_URL requires an HTTP(S) service URL")
        state_dir = Path(value("STATE_DIR", "/var/lib/ingen-agent"))
        poll = float(value("POLL_SECONDS", "5"))
        heartbeat = float(value("HEARTBEAT_SECONDS", "5"))
        delay = float(value("WRITE_DELAY_MS", "0")) / 1000
        if not all(isfinite(n) for n in (poll, heartbeat, delay)) or (
            poll <= 0 or heartbeat <= 0 or delay < 0
        ):
            raise ValueError("Invalid agent polling, heartbeat, or disk-delay interval")
        metrics = value("METRICS_BIND", "127.0.0.1:9101")
        host, _, port = metrics.rpartition(":")
        if not host or not port.isdecimal() or not 1 <= int(port) <= 65535:
            raise ValueError("INGEN_AGENT_METRICS_BIND requires host:port")
        return cls(
            robot_id=robot_id,
            registry_url=url,
            credential_file=Path(
                value("CREDENTIAL_FILE", "/etc/ingen-agent/credential")
            ),
            state_dir=state_dir,
            trust_dir=Path(value("TRUST_DIR", "/etc/ingen-agent/trust")),
            safe_flag=Path(value("SAFE_FLAG", str(state_dir / "safe-to-update"))),
            poll_seconds=poll,
            heartbeat_seconds=heartbeat,
            write_delay=delay,
            degraded_digest=value("DEGRADED_DIGEST").removeprefix("sha256:") or None,
            metrics_bind=(host, int(port)),
        )


def in_window(
    window: DailySafeUpdateWindow | SafeUpdateWindow | None, now: datetime
) -> bool:
    if window is None:
        return True
    if isinstance(window, SafeUpdateWindow):
        return window.start <= now < window.end
    local = now.astimezone(ZoneInfo(window.timezone)).time()
    start = time.fromisoformat(window.start_local)
    end = time.fromisoformat(window.end_local)
    if start < end:
        return start <= local < end
    return local >= start or local < end
