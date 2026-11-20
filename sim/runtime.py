"""Compose runtime and Linux fault injection for the isolated fleet."""

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
STATE = "/var/lib/ingen-agent"
CONFIG = "/etc/ingen-sim"


def write_json(target: Path, data: Any) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", dir=target.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(json.dumps(data, indent=2) + "\n")
    temporary.replace(target)


@dataclass(frozen=True)
class Robot:
    service: str
    site: str
    robot_id: str


@dataclass(frozen=True)
class Faults:
    loss_percent: float
    latency_ms: float
    outage_seconds: float
    write_delay_ms: float


class Runtime:
    def __init__(
        self,
        project: str,
        directory: Path,
        trust: Path,
        port: int,
        observability: bool,
        stack_environment: dict[str, str],
    ) -> None:
        self.project = project
        self.directory = directory
        self.trust = trust
        self.overlay = directory / "agents.json"
        self.env = {
            **{
                key: value
                for key, value in os.environ.items()
                if key not in stack_environment
            },
            "ARTIFACT_PUBLIC_KEYS_DIR": str(trust),
            "REGISTRY_PORT": str(port),
            "W05_RUN_DIR": str(directory),
        }
        env_file = directory / "stack.env"
        env_file.write_text(
            "".join(f"{key}={value}\n" for key, value in stack_environment.items())
        )
        env_file.chmod(0o600)
        self.command = [
            "docker",
            "compose",
            "--project-name",
            project,
            "--env-file",
            str(env_file),
            "-f",
            "infra/local/compose.yaml",
            "-f",
            str(self.overlay),
        ]
        if observability:
            self.command.extend(["-f", "observability/compose.yaml"])
        self.observability = observability
        self.image = f"{project}-agent:local"
        write_json(self.overlay, {"services": {}})

    def require_new_project(self) -> None:
        label = f"label=com.docker.compose.project={self.project}"
        for resource in ("container", "network", "volume"):
            result = subprocess.run(
                [
                    "docker",
                    resource,
                    "ls",
                    "--quiet",
                    "--filter",
                    label,
                    *(["--all"] if resource == "container" else []),
                ],
                cwd=ROOT,
                env=self.env,
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
            if result.stdout.strip():
                raise ValueError(f"Compose project {self.project!r} already exists")

    def call(self, *args: str, capture: bool = False) -> str:
        result = subprocess.run(
            [*self.command, *args],
            cwd=ROOT,
            env=self.env,
            check=True,
            capture_output=capture,
            text=True,
            timeout=600,
        )
        return result.stdout or ""

    def start_registry(self) -> None:
        self.call("up", "-d", "--build", "--wait", "registry")
        self.call("exec", "-T", "registry", "alembic", "upgrade", "head")

    def configure(
        self,
        robots: list[Robot],
        faults: Faults,
        degraded_robot: str | None,
        digest: str,
    ) -> None:
        services = {}
        for index, robot in enumerate(robots):
            environment = {
                "INGEN_AGENT_ROBOT_ID": robot.robot_id,
                "INGEN_AGENT_REGISTRY_URL": "http://registry:8000",
                "INGEN_AGENT_CREDENTIAL_FILE": f"{CONFIG}/credential",
                "INGEN_AGENT_TRUST_DIR": "/etc/ingen-agent/trust",
                "INGEN_AGENT_STATE_DIR": STATE,
                "INGEN_AGENT_SAFE_FLAG": f"{CONFIG}/safe.flag",
                "INGEN_AGENT_METRICS_BIND": "0.0.0.0:9101",
                "INGEN_AGENT_POLL_SECONDS": "3",
                "INGEN_AGENT_HEARTBEAT_SECONDS": "2",
                "INGEN_AGENT_WRITE_DELAY_MS": str(faults.write_delay_ms),
            }
            if robot.service == degraded_robot:
                environment["INGEN_AGENT_DEGRADED_DIGEST"] = digest
            service = {
                "image": self.image,
                "entrypoint": [
                    "sh",
                    "-ec",
                    f"while [ ! -f {CONFIG}/start ]; do sleep 0.2; done; "
                    "exec python -m agent",
                ],
                "environment": environment,
                "volumes": [
                    {
                        "type": "bind",
                        "source": str(self.directory / robot.service),
                        "target": CONFIG,
                        "read_only": True,
                    },
                    {
                        "type": "bind",
                        "source": str(self.trust),
                        "target": "/etc/ingen-agent/trust",
                        "read_only": True,
                    },
                    f"{robot.service}-data:{STATE}",
                ],
                "networks": {"default": {"aliases": ["fleet-agent"]}},
                "cap_add": ["NET_ADMIN"],
            }
            if index == 0:
                service["build"] = {
                    "context": str(ROOT),
                    "dockerfile": "agent/Dockerfile",
                }
            services[robot.service] = service
        write_json(
            self.overlay,
            {
                "services": services,
                "volumes": {f"{r.service}-data": {} for r in robots},
            },
        )

    def start_agents(self, robots: list[Robot]) -> None:
        self.call("build", robots[0].service)
        self.call("up", "-d", "--no-build", *(robot.service for robot in robots))
        if self.observability:
            self.call("up", "-d", "prometheus", "grafana")

    def allow_agents(self, robots: list[Robot]) -> None:
        for robot in robots:
            (self.directory / robot.service / "start").touch()

    def restart_after_crash(
        self, robot: Robot, faults: Faults, registry_ip: str | None
    ) -> None:
        (self.directory / robot.service / "start").unlink()
        self.call("start", robot.service)
        if registry_ip:
            # A stopped container gets a new network namespace on restart.
            self.configure_network(robot, registry_ip)
            self.netem(robot, faults.loss_percent, faults.latency_ms)
        self.allow_agents([robot])

    def registry_ip(self) -> str:
        return self.call(
            "exec",
            "-T",
            "registry",
            "python",
            "-c",
            "import socket; print(socket.gethostbyname(socket.gethostname()))",
            capture=True,
        ).strip()

    def tc(self, robot: Robot, *arguments: str) -> str:
        return self.call(
            "exec",
            "-T",
            "--user",
            "root",
            robot.service,
            "tc",
            *arguments,
            capture=True,
        )

    def configure_network(self, robot: Robot, registry_ip: str) -> None:
        # Egress TCP packets to the local Registry alone are selected. The other
        # bands leave Docker DNS and incoming Prometheus scrapes usable.
        self.tc(
            robot,
            "qdisc",
            "replace",
            "dev",
            "eth0",
            "root",
            "handle",
            "1:",
            "prio",
            "bands",
            "3",
            "priomap",
            *("0",) * 16,
        )
        self.tc(
            robot,
            "filter",
            "replace",
            "dev",
            "eth0",
            "parent",
            "1:",
            "protocol",
            "ip",
            "pref",
            "10",
            "u32",
            "match",
            "ip",
            "dst",
            f"{registry_ip}/32",
            "flowid",
            "1:3",
        )

    def netem(self, robot: Robot, loss: float, latency: float) -> list[dict[str, Any]]:
        self.tc(
            robot,
            "qdisc",
            "replace",
            "dev",
            "eth0",
            "parent",
            "1:3",
            "handle",
            "30:",
            "netem",
            "delay",
            f"{latency:g}ms",
            "loss",
            f"{loss:g}%",
        )
        return self.read_netem(robot)

    def read_netem(self, robot: Robot) -> list[dict[str, Any]]:
        return [
            row
            for row in json.loads(
                self.tc(robot, "-s", "-j", "qdisc", "show", "dev", "eth0")
            )
            if row["kind"] == "netem"
        ]

    def partial(
        self, robot: Robot, digest: str, *, stopped: bool = False
    ) -> dict[str, Any]:
        script = (
            "import json, pathlib, sys; root=pathlib.Path(sys.argv[1]); "
            "part=root/'staging'/(sys.argv[2]+'.part'); active=root/'active'; "
            "slots=root/'slots'; "
            "print(json.dumps(dict(part_path=str(part), "
            "part_bytes=part.stat().st_size if part.is_file() else 0, "
            "active=str(active.readlink()) if active.is_symlink() else None, "
            "slot_files=[str(p.relative_to(root)) "
            "for p in slots.rglob('*') if p.is_file()])))"
        )
        if stopped:
            command = [
                "run",
                "--rm",
                "--no-deps",
                "--entrypoint",
                "python",
                robot.service,
            ]
        else:
            command = ["exec", "-T", robot.service, "python"]
        snapshot = json.loads(
            self.call(*command, "-c", script, STATE, digest, capture=True)
        )
        if not isinstance(snapshot, dict):
            raise ValueError("Agent state inspection did not produce a JSON object")
        return snapshot

    def logs(self, *services: str) -> list[dict[str, Any]]:
        output = self.call(
            "logs", "--no-color", "--no-log-prefix", *services, capture=True
        )
        rows = []
        for line in output.splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
        return rows
