"""Exercise real dependencies; restore each stopped service even on failure."""

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ["docker", "compose", "--env-file", ".env", "-f", "infra/local/compose.yaml"]


def registry_url() -> str:
    port = os.environ.get("REGISTRY_PORT", "8000")
    env_file = ROOT / ".env"
    if env_file.exists() and "REGISTRY_PORT" not in os.environ:
        for line in env_file.read_text().splitlines():
            if line.startswith("REGISTRY_PORT="):
                port = line.split("=", 1)[1].strip()
    return f"http://127.0.0.1:{int(port)}"


def fetch(path: str) -> tuple[int, dict[str, object]]:
    request = urllib.request.Request(
        registry_url() + path, headers={"X-Request-ID": "week1-stack-smoke"}
    )
    try:
        response = urllib.request.urlopen(request, timeout=10)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, json.load(response)


def await_ready() -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            if fetch("/readyz")[0] == 200:
                return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(1)
    raise RuntimeError("Registry did not recover readiness within 60 seconds")


def main() -> None:
    await_ready()
    assert fetch("/healthz")[0] == 200
    print(json.dumps({"stage": "baseline", "readyz": fetch("/readyz")}))
    for service, dependency in (("minio", "object_store"), ("postgres", "database")):
        subprocess.run([*COMPOSE, "stop", service], cwd=ROOT, check=True)
        try:
            status, body = fetch("/readyz")
            assert status == 503, (service, status, body)
            assert body["dependencies"][dependency] == "unavailable"
            assert fetch("/healthz")[0] == 200
            print(json.dumps({"stage": f"{service}_stopped", "readyz": [status, body]}))
        finally:
            subprocess.run([*COMPOSE, "start", service], cwd=ROOT, check=True)
            await_ready()
        print(json.dumps({"stage": f"{service}_restored", "readyz": fetch("/readyz")}))
    print(
        "PASS: readiness detects each unavailable dependency; liveness stays available."
    )


if __name__ == "__main__":
    main()
