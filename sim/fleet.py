"""Run an isolated Compose fleet and collect Registry and Linux fault evidence."""

import argparse
import secrets
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from agent.artifacts import sha256
from agent.evaluation import Evaluation
from sim.runtime import Faults, Robot, Runtime, write_json

SITES = ("lab-east", "lab-central", "lab-west")


def publish(
    client: httpx.Client, bundle: Path, evaluation: Path, evaluator_key: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    evidence = Evaluation.model_validate_json(
        (evaluation / "evidence.json").read_bytes()
    )
    if evidence.report_sha256 != sha256((evaluation / "report.json").read_bytes()):
        raise ValueError("Evaluation report hash does not match evidence")
    response = client.post(
        "/v1/artifacts",
        content=bundle.read_bytes(),
        headers={"Content-Type": "application/x-tar"},
    )
    response.raise_for_status()
    published = response.json()
    digest = published["digest"]
    if digest != evidence.artifact_digest:
        raise ValueError("Submitted evaluation describes a different signed artifact")
    response = client.post(
        f"/v1/artifacts/{digest}/evaluations",
        json=evidence.model_dump(mode="json"),
        headers={"Authorization": f"Bearer {evaluator_key}"},
    )
    response.raise_for_status()
    decision = response.json()
    if not decision["passed"]:
        raise RuntimeError(
            f"Week 4 gate rejected simulator artifact: {decision['violations']}"
        )
    response = client.patch(
        f"/v1/artifacts/{digest}/lifecycle",
        json={
            "expected_state": "evaluated",
            "state": "approved",
            "reason": "Isolated Week 5 simulation",
        },
    )
    response.raise_for_status()
    return response.json(), decision


def enroll(
    client: httpx.Client, directory: Path, cohort: str, count: int
) -> list[Robot]:
    robots = []
    now = datetime.now(UTC)
    for index in range(count):
        service = f"agent-{index + 1:02d}"
        site = SITES[index % len(SITES)]
        platform = "Fari" if site == "lab-central" else "rover"
        response = client.post(
            "/v1/robots",
            json={
                "platform": platform,
                "site": site,
                "cohort_labels": [cohort],
                "hardware_profile": {"simulated": True, "instance": service},
                "safe_update_window": {
                    "start": (now - timedelta(hours=1)).isoformat(),
                    "end": (now + timedelta(hours=1)).isoformat(),
                },
            },
        )
        response.raise_for_status()
        row = response.json()
        # The private run directory is owner-only on the host. Each container
        # receives a read-only bind mount of its own config as its non-root UID.
        config = directory / service
        config.mkdir(mode=0o755)
        token = config / "credential"
        token.write_text(row["credential"] + "\n")
        token.chmod(0o444)
        (config / "safe.flag").write_text("true\n")
        robots.append(Robot(service, site, row["robot"]["id"]))
    return robots


def state(
    client: httpx.Client, robots: list[Robot], cohort: str
) -> list[dict[str, Any]]:
    response = client.get("/v1/robots", params={"cohort": cohort, "limit": 100})
    response.raise_for_status()
    rows = {row["id"]: row for row in response.json()["items"]}
    return [
        {
            **asdict(robot),
            "platform": rows[robot.robot_id]["platform"],
            **rows[robot.robot_id]["reported_state"],
        }
        for robot in robots
    ]


def assign(client: httpx.Client, robot: Robot, digest: str) -> None:
    response = client.put(
        f"/v1/robots/{robot.robot_id}/desired-state",
        json={
            "target_digest": digest,
            "set_by": "week-05-simulator",
            "reason": "Isolated signed artifact deploy",
        },
    )
    response.raise_for_status()


def wait_for_heartbeats(client: httpx.Client, robots: list[Robot], cohort: str) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if all(row["heartbeat_sequence"] > 0 for row in state(client, robots, cohort)):
            return
        time.sleep(0.5)
    raise TimeoutError("One or more agents did not send initial heartbeats")


def exercise_crash(
    runtime: Runtime,
    robot: Robot,
    digest: str,
    size: int,
    faults: Faults,
    registry_ip: str | None,
) -> dict[str, Any]:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        partial = runtime.partial(robot, digest)
        if 0 < partial["part_bytes"] < size:
            break
        time.sleep(0.2)
    else:
        raise TimeoutError("No incomplete staged artifact observed before SIGKILL")
    runtime.call("kill", "--signal", "SIGKILL", robot.service)
    stopped = runtime.partial(robot, digest, stopped=True)
    if not (partial["part_bytes"] <= stopped["part_bytes"] < size):
        raise RuntimeError(f"Crash did not leave partial bytes staged: {stopped}")
    if stopped["active"] is not None or stopped["slot_files"]:
        raise RuntimeError(f"Partial file escaped staging: {stopped}")
    runtime.restart_after_crash(robot, faults, registry_ip)
    return {
        "service": robot.service,
        "signal": "SIGKILL",
        "while_running": partial,
        "after_crash": stopped,
        "bundle_bytes": size,
    }


def matching_requests(runtime: Runtime, robots: list[Robot]) -> list[dict[str, Any]]:
    registry = {
        row.get("request-id"): row
        for row in runtime.logs("registry")
        if row.get("request-id")
    }
    for robot in robots:
        for agent in runtime.logs(robot.service):
            request = agent.get("request-id")
            if (
                request in registry
                and registry[request].get("robot-id") == robot.robot_id
            ):
                return [
                    {
                        key: row.get(key)
                        for key in (
                            "event",
                            "message",
                            "request-id",
                            "robot-id",
                            "method",
                            "path",
                            "status",
                            "status_code",
                        )
                        if key in row
                    }
                    for row in (agent, registry[request])
                ]
    return []


def wait_for_target(
    client: httpx.Client,
    robots: list[Robot],
    summary: dict[str, Any],
    live: Path,
    started: float,
    timeout: float,
    degraded_robot: str | None,
) -> list[dict[str, Any]]:
    expected = summary["target_digest"]
    last_count = -1
    while (elapsed := time.monotonic() - started) <= timeout:
        rows = state(client, robots, summary["cohort"])
        count = sum(
            row["current_digest"] == expected
            and row["health_summary"].get("status")
            == ("degraded" if row["service"] == degraded_robot else "healthy")
            for row in rows
        )
        checkpoint = {"elapsed_seconds": round(elapsed, 3), "converged": count}
        if count != last_count:
            summary["timeline"].append(checkpoint)
            print(f"{elapsed:6.2f}s  {count}/{len(robots)} converged", flush=True)
            last_count = count
        write_json(
            live,
            {
                **summary,
                **checkpoint,
                "robots": rows,
                "phase": "Converged" if count == len(robots) else "Reconciling",
            },
        )
        if count == len(robots):
            summary["convergence_seconds"] = checkpoint["elapsed_seconds"]
            return rows
        time.sleep(0.4)
    raise TimeoutError(
        f"Only {last_count}/{len(robots)} converged in {timeout:g} seconds"
    )


def run(args: argparse.Namespace) -> None:
    directory = args.run_dir.resolve()
    directory.mkdir(mode=0o700, parents=True)
    operator_key = secrets.token_urlsafe(32)
    evaluator_key = secrets.token_urlsafe(32)
    stack_environment = {
        "POSTGRES_DB": "fleet_sim",
        "POSTGRES_USER": "fleet_sim",
        "POSTGRES_PASSWORD": secrets.token_urlsafe(32),
        "MINIO_ROOT_USER": "fleet_sim",
        "MINIO_ROOT_PASSWORD": secrets.token_urlsafe(32),
        "OPERATOR_API_KEY": operator_key,
        "EVALUATOR_API_KEY": evaluator_key,
    }
    if args.observability:
        token = directory / "prometheus-registry.token"
        token.write_text(secrets.token_urlsafe(32) + "\n")
        token.chmod(0o444)
    runtime = Runtime(
        args.project,
        directory,
        args.trust.resolve(),
        args.port,
        args.observability,
        stack_environment,
    )
    runtime.require_new_project()
    cohort = f"w05-{secrets.token_hex(5)}"
    count = 20 if args.scenario == "convergence" else 1
    degraded_robot = f"agent-{args.degraded_agent:02d}" if args.degraded_agent else None
    faults = Faults(
        args.loss_percent,
        args.latency_ms,
        args.outage_seconds,
        (
            args.write_delay_ms
            if args.write_delay_ms is not None
            else (35 if args.scenario == "crash" else 0)
        ),
    )
    live = directory / "live.json"
    try:
        runtime.start_registry()
        with (
            httpx.Client(
                base_url=f"http://127.0.0.1:{args.port}",
                headers={"Authorization": f"Bearer {operator_key}"},
                timeout=60,
            ) as client,
            ThreadPoolExecutor(max_workers=8) as pool,
        ):
            artifact, decision = publish(
                client,
                args.bundle,
                args.evaluation,
                evaluator_key,
            )
            digest = "sha256:" + artifact["digest"]
            robots = enroll(client, directory, cohort, count)
            runtime.configure(robots, faults, degraded_robot, digest)
            runtime.start_agents(robots)
            netem_initial = {}
            registry_ip = (
                runtime.registry_ip()
                if any((faults.loss_percent, faults.latency_ms, faults.outage_seconds))
                else None
            )
            if registry_ip:
                list(
                    pool.map(
                        lambda robot: runtime.configure_network(robot, registry_ip),
                        robots,
                    )
                )
                netem_initial = dict(
                    zip(
                        (robot.service for robot in robots),
                        pool.map(
                            lambda robot: runtime.netem(
                                robot, faults.loss_percent, faults.latency_ms
                            ),
                            robots,
                        ),
                        strict=True,
                    )
                )
            runtime.allow_agents(robots)
            wait_for_heartbeats(client, robots, cohort)
            summary = {
                "scenario": args.scenario,
                "cohort": cohort,
                "robot_count": count,
                "target_digest": digest,
                "target_version": artifact["version"],
                "gate_evaluation_id": decision["id"],
                "gate_passed": decision["passed"],
                "artifact_state": artifact["state"],
                "degraded_agent": degraded_robot,
                "injection": {
                    **asdict(faults),
                    "network_direction": "agent egress to Registry",
                    "initial_qdisc": netem_initial,
                },
                "timeline": [],
            }
            write_json(
                live,
                {
                    **summary,
                    "robots": state(client, robots, cohort),
                    "phase": "Ready",
                    "converged": 0,
                },
            )
            print(
                f"Ready: {count} agents, target {digest}, "
                f"netem loss {faults.loss_percent:g}%",
                flush=True,
            )
            time.sleep(args.start_delay_seconds)
            if faults.outage_seconds:
                list(pool.map(lambda robot: runtime.netem(robot, 100, 0), robots))
            summary["desired_state_issued_utc"] = datetime.now(UTC).isoformat()
            started = time.monotonic()
            list(pool.map(lambda robot: assign(client, robot, digest), robots))
            if faults.outage_seconds:
                time.sleep(max(0, faults.outage_seconds - (time.monotonic() - started)))
                list(
                    pool.map(
                        lambda robot: runtime.netem(
                            robot, faults.loss_percent, faults.latency_ms
                        ),
                        robots,
                    )
                )
                summary["outage_restored_after_seconds"] = round(
                    time.monotonic() - started, 3
                )
            crash = None
            if args.scenario == "crash":
                crash = exercise_crash(
                    runtime,
                    robots[0],
                    artifact["digest"],
                    artifact["size_bytes"],
                    faults,
                    registry_ip,
                )
            summary["robots"] = wait_for_target(
                client, robots, summary, live, started, args.timeout, degraded_robot
            )
            for row in summary["robots"]:
                row["current_version"] = artifact["version"]
            if crash:
                crash["resumed_requests"] = [
                    {
                        key: row[key]
                        for key in (
                            "request-id",
                            "robot-id",
                            "range_offset",
                            "status",
                            "status_code",
                        )
                        if key in row
                    }
                    for row in runtime.logs(robots[0].service)
                    if row.get("range_offset") == crash["after_crash"]["part_bytes"]
                    and row.get("status", row.get("status_code")) == 206
                ]
                if not crash["resumed_requests"]:
                    raise RuntimeError(
                        f"Agent never logged HTTP 206 from persisted offset: {crash}"
                    )
                summary["crash"] = crash
            if registry_ip:
                summary["injection"]["final_qdisc"] = dict(
                    zip(
                        (robot.service for robot in robots),
                        pool.map(runtime.read_netem, robots),
                        strict=True,
                    )
                )
            summary["log_correlation"] = matching_requests(runtime, robots)
            write_json(args.report, summary)
            print(f"Verified evidence written to {args.report}", flush=True)
    finally:
        if not args.keep:
            runtime.call("down", "--volumes")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario", choices=("convergence", "crash"), default="convergence"
    )
    parser.add_argument(
        "--run-dir", type=Path, required=True, help="New private directory for this run"
    )
    parser.add_argument(
        "--project", required=True, help="Dedicated Docker Compose project name"
    )
    parser.add_argument("--port", type=int, default=8015)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument(
        "--evaluation",
        type=Path,
        required=True,
        help="Directory with Week 4 report.json and evidence.json",
    )
    parser.add_argument("--trust", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--loss-percent", type=float, default=0)
    parser.add_argument("--latency-ms", type=float, default=0)
    parser.add_argument("--outage-seconds", type=float, default=0)
    parser.add_argument("--write-delay-ms", type=float)
    parser.add_argument(
        "--degraded-agent",
        type=int,
        help="1-based agent index to degrade on the target digest",
    )
    parser.add_argument("--start-delay-seconds", type=float, default=0)
    parser.add_argument("--timeout", type=float, default=240)
    parser.add_argument("--observability", action="store_true")
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Keep this run's Compose project available for inspection",
    )
    args = parser.parse_args()
    if (
        not 0 <= args.loss_percent <= 100
        or args.latency_ms < 0
        or args.outage_seconds < 0
        or args.start_delay_seconds < 0
        or (args.write_delay_ms is not None and args.write_delay_ms < 0)
        or args.timeout <= 0
        or (
            args.degraded_agent is not None
            and not 1
            <= args.degraded_agent
            <= (20 if args.scenario == "convergence" else 1)
        )
    ):
        parser.error(
            "Fault values must be nonnegative, loss ≤ 100%, timeout > 0, "
            "and degraded agent in the fleet"
        )
    try:
        run(args)
    except subprocess.CalledProcessError as error:
        if error.stderr:
            print(error.stderr, end="", flush=True)
        raise


if __name__ == "__main__":
    main()
