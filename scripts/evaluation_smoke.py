"""Verify gate decisions through HTTP against the isolated Week 4 stack."""

import argparse
import json
import os
from pathlib import Path
from typing import Any

import httpx

from agent.artifacts import sha256
from agent.evaluation import Evaluation


def verify(
    url: str, baseline: Path, degraded: Path, reports: Path, output: Path
) -> None:
    operator = {"Authorization": f"Bearer {os.environ['OPERATOR_API_KEY']}"}
    evaluator = {"Authorization": f"Bearer {os.environ['EVALUATOR_API_KEY']}"}
    with httpx.Client(base_url=url, timeout=60.0) as client:

        def publish(bundle: Path) -> dict[str, Any]:
            response = client.post(
                "/v1/artifacts",
                headers={**operator, "Content-Type": "application/x-tar"},
                content=bundle.read_bytes(),
            )
            response.raise_for_status()
            return response.json()

        def submit(artifact: str, report: Path) -> dict[str, Any]:
            evidence = Evaluation.model_validate_json(
                (report / "evidence.json").read_bytes()
            )
            assert evidence.artifact_digest == artifact
            assert evidence.report_sha256 == sha256(
                (report / "report.json").read_bytes()
            )
            path = f"/v1/artifacts/{artifact}/evaluations"
            assert (
                client.post(
                    path, headers=operator, json=evidence.model_dump(mode="json")
                ).status_code
                == 401
            )
            response = client.post(
                path, headers=evaluator, json=evidence.model_dump(mode="json")
            )
            response.raise_for_status()
            decision = response.json()
            readback = client.get(f"{path}/{decision['id']}", headers=operator)
            readback.raise_for_status()
            assert readback.json() == decision
            return decision

        def lifecycle(artifact: str, current: str, target: str) -> httpx.Response:
            return client.patch(
                f"/v1/artifacts/{artifact}/lifecycle",
                headers=operator,
                json={
                    "expected_state": current,
                    "state": target,
                    "reason": "Week 4 local integration approval",
                },
            )

        def audit(artifact: str) -> list[dict[str, Any]]:
            response = client.get(f"/v1/artifacts/{artifact}/audit", headers=operator)
            response.raise_for_status()
            return response.json()["items"]

        base = publish(baseline)
        before = lifecycle(base["digest"], "published", "evaluated")
        assert before.status_code == 409
        accepted = submit(base["digest"], reports / "baseline")
        assert accepted["passed"], accepted["violations"]
        if base["state"] != "approved":
            approval = lifecycle(base["digest"], "evaluated", "approved")
            approval.raise_for_status()
            assert approval.json()["state"] == "approved"
        failed = publish(degraded)
        rejected = submit(failed["digest"], reports / "degraded")
        assert rejected["baseline_digest"] == base["digest"]
        assert not rejected["passed"], rejected
        assert any(
            "low_light mAP dropped" in message for message in rejected["violations"]
        )
        overall_delta = abs(
            accepted["evidence"]["overall"]["map"]
            - rejected["evidence"]["overall"]["map"]
        )
        assert overall_delta <= 0.02, overall_delta
        denied = lifecycle(failed["digest"], "evaluated", "approved")
        assert denied.status_code == 409
        robot = client.post(
            "/v1/robots",
            headers=operator,
            json={
                "platform": "Fari",
                "site": "local-evaluation",
                "hardware_profile": {},
            },
        )
        robot.raise_for_status()
        robot_auth = {"Authorization": f"Bearer {robot.json()['credential']}"}
        downloads = {
            key: client.get(
                f"/v1/artifacts/{row['digest']}/download",
                headers={**robot_auth, "Range": "bytes=0-63"},
            ).status_code
            for key, row in (("baseline", base), ("degraded", failed))
        }
        assert downloads == {"baseline": 206, "degraded": 403}
        baseline_audit, degraded_audit = audit(base["digest"]), audit(failed["digest"])
        assert any(
            event["to_state"] == "approved"
            and event["actor"] == "operator"
            and event["evaluation_id"] == accepted["id"]
            for event in baseline_audit
        )
        assert degraded_audit[-1]["evaluation_id"] == rejected["id"]
        assert degraded_audit[-1]["to_state"] == "published"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "baseline": accepted,
                "degraded": rejected,
                "baseline_audit": baseline_audit,
                "degraded_audit": degraded_audit,
                "manual_evaluation_status": before.status_code,
                "failed_approval_status": denied.status_code,
                "robot_download_status": downloads,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    print("Baseline passed and was approved. Degraded artifact was rejected.")
    for reason in rejected["violations"]:
        print(reason)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("degraded", type=Path)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--reports", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    verify(args.url, args.baseline, args.degraded, args.reports, args.out)
