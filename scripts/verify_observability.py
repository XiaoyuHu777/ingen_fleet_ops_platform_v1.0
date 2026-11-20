"""Assert the provisioned Grafana panels report expected live fleet state."""

import argparse
import json
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen

DASHBOARD_UID = "ingen-fleet-health"
DATASOURCE_UID = "ingen-prometheus"


def get_json(grafana: str, path: str) -> dict[str, Any]:
    with urlopen(grafana.rstrip("/") + path, timeout=5) as response:
        return dict(json.load(response))


def read_dashboard(grafana: str) -> tuple[dict[str, Any], dict[int, list[str]]]:
    document = get_json(grafana, f"/api/dashboards/uid/{DASHBOARD_UID}")
    dashboard = document["dashboard"]
    defaults = {
        v["name"]: v["current"]["value"] for v in dashboard["templating"]["list"]
    }
    queries = {
        panel["id"]: [
            target["expr"].replace("${stale_seconds}", defaults["stale_seconds"])
            for target in panel["targets"]
        ]
        for panel in dashboard["panels"]
    }
    return {
        "uid": dashboard["uid"],
        "stale_after_seconds": int(defaults["stale_seconds"]),
    }, queries


def query(grafana: str, expression: str) -> list[dict[str, Any]]:
    params = urlencode({"query": expression})
    path = f"/api/datasources/proxy/uid/{DATASOURCE_UID}/api/v1/query?{params}"
    result = get_json(grafana, path)
    if result["status"] != "success":
        raise RuntimeError(f"Grafana data source query failed: {result['status']}")
    return list(result["data"]["result"])


def live_panels(grafana: str) -> dict[str, Any]:
    summary, queries = read_dashboard(grafana)
    data = {
        panel: [query(grafana, expr) for expr in expressions]
        for panel, expressions in queries.items()
    }
    summary.update(
        registered=int(data[1][0][0]["value"][1]),
        late_count=int(data[2][0][0]["value"][1]),
        agent_targets_up=int(data[3][0][0]["value"][1]),
        versions=sorted(
            [{**d["metric"], "robots": int(d["value"][1])} for d in data[4][0]],
            key=lambda d: d["digest"],
        ),
        late_robots=sorted(
            [
                {
                    "robot_id": d["metric"]["robot_id"],
                    "site": d["metric"]["site"],
                    "age_seconds": float(d["value"][1]),
                }
                for d in data[5][0]
            ],
            key=lambda d: d["robot_id"],
        ),
        never_reported=sorted(d["metric"]["robot_id"] for d in data[6][0]),
        failure_counters_reporting=[bool(result) for result in data[7]],
    )
    return summary


def matches(summary: dict[str, Any], args: argparse.Namespace) -> bool:
    if summary["registered"] != args.expected_robots:
        return False
    if args.expected_late is not None and summary["late_count"] != args.expected_late:
        return False
    if (
        args.expected_agents is not None
        and summary["agent_targets_up"] != args.expected_agents
    ):
        return False
    if args.expected_agents and not all(summary["failure_counters_reporting"]):
        return False
    versions = {v["digest"]: v["robots"] for v in summary["versions"]}
    if any(versions.get(digest) != int(count) for digest, count in args.version_count):
        return False
    if args.stale_robot and args.stale_robot not in {
        r["robot_id"] for r in summary["late_robots"]
    }:
        return False
    if args.never_robot and args.never_robot not in summary["never_reported"]:
        return False
    return True


def _json_log(path: Path) -> list[dict[str, Any]]:
    records = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                records.append(record)
    return records


def correlate(agent_log: Path, registry_log: Path) -> dict[str, Any]:
    registry = {
        (r["request-id"], r["robot-id"], r["status"]): r
        for r in _json_log(registry_log)
        if r.get("robot-id") not in (None, "-") and r.get("status") in (200, 206, 304)
    }
    for agent in _json_log(agent_log):
        key = (agent.get("request-id"), agent.get("robot-id"), agent.get("status"))
        if key in registry:
            request = registry[key]
            return {
                "request_id": key[0],
                "robot_id": key[1],
                "status": key[2],
                "agent_event": agent.get("event"),
                "registry_method": request["method"],
                "registry_path": request["path"],
                "registry_duration_ms": request["duration_ms"],
            }
    raise RuntimeError("No authenticated request and response pair found in both logs")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grafana", default="http://127.0.0.1:3005")
    parser.add_argument("--expected-robots", type=int, required=True)
    parser.add_argument("--expected-agents", type=int)
    parser.add_argument("--expected-late", type=int)
    parser.add_argument(
        "--version-count",
        nargs=2,
        action="append",
        default=[],
        metavar=("DIGEST", "COUNT"),
    )
    parser.add_argument("--stale-robot")
    parser.add_argument("--never-robot")
    parser.add_argument("--agent-log", type=Path)
    parser.add_argument("--registry-log", type=Path)
    parser.add_argument("--wait", type=float, default=30)
    args = parser.parse_args()
    if bool(args.agent_log) != bool(args.registry_log):
        parser.error("--agent-log and --registry-log must be provided together")

    deadline = time.monotonic() + args.wait
    while True:
        try:
            summary = live_panels(args.grafana)
            if matches(summary, args):
                if args.agent_log:
                    summary["correlated_request"] = correlate(
                        args.agent_log, args.registry_log
                    )
                print(json.dumps(summary, indent=2))
                return
            diagnostic = json.dumps(summary)
        except (URLError, RuntimeError) as error:
            diagnostic = str(error)
        if time.monotonic() >= deadline:
            raise SystemExit(f"Grafana verification failed: {diagnostic}")
        time.sleep(1)


if __name__ == "__main__":
    main()
