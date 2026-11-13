"""Compare evaluator evidence and serialize lifecycle decisions per model."""

from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session
from yaml import YAMLError  # type: ignore[import-untyped]

from agent.artifacts import canonical_json, sha256
from agent.evaluation import Evaluation, GatePolicy, read_policy, violations
from services.registry.api_v1 import fail
from services.registry.models import Artifact, ArtifactAudit, ArtifactEvaluation


def lock_model(db: Session, digest: str) -> Artifact:
    dialect = db.get_bind().dialect.name
    if dialect == "sqlite":
        db.execute(text("BEGIN IMMEDIATE"))
    name = db.scalar(select(Artifact.name).where(Artifact.digest == digest))
    if name is None:
        fail(404, "artifact_not_found", "Artifact not found")
    if dialect == "postgresql":
        # All writes that affect the baseline or its evidence take this same lock.
        key = int.from_bytes(
            bytes.fromhex(sha256(name.encode()))[:8], "big", signed=True
        )
        db.execute(select(func.pg_advisory_xact_lock(key)))
    row = db.get(Artifact, digest, populate_existing=True)
    assert row is not None
    return row


def active_policy(path: Path | None) -> tuple[GatePolicy, str]:
    if path is None:
        fail(503, "gate_unavailable", "Evaluation policy is not configured")
    try:
        return read_policy(path)
    except (OSError, ValueError, YAMLError):
        fail(503, "gate_unavailable", "Evaluation policy could not be loaded")


def latest_evaluation(db: Session, digest: str) -> ArtifactEvaluation | None:
    return db.scalar(
        select(ArtifactEvaluation)
        .where(ArtifactEvaluation.artifact_digest == digest)
        .order_by(ArtifactEvaluation.id.desc())
        .limit(1)
    )


def compare(
    db: Session, row: Artifact, policy: GatePolicy, evidence: Evaluation
) -> tuple[str | None, list[str]]:
    baseline_digest = db.scalar(
        select(Artifact.digest)
        .join(ArtifactAudit, ArtifactAudit.artifact_digest == Artifact.digest)
        .where(
            Artifact.name == row.name,
            Artifact.state == "approved",
            ArtifactAudit.to_state == "approved",
            ArtifactAudit.from_state == "evaluated",
        )
        .order_by(ArtifactAudit.id.desc())
        .limit(1)
    )
    baseline = None
    if baseline_digest is not None and baseline_digest != row.digest:
        prior = latest_evaluation(db, baseline_digest)
        if prior is None or prior.violations:
            return baseline_digest, [
                "Approved baseline lacks passing evaluation evidence"
            ]
        baseline = Evaluation.model_validate(prior.evidence)
        if policy.known_cases.keys() - baseline.known_cases.keys():
            return baseline_digest, [
                "Re-evaluate the approved baseline on the current known cases"
            ]
    return baseline_digest, violations(policy, evidence, baseline)


def record(
    db: Session,
    row: Artifact,
    policy: GatePolicy,
    policy_digest: str,
    evidence: Evaluation,
) -> tuple[ArtifactEvaluation, bool]:
    evidence_digest = sha256(canonical_json(evidence))
    replay = db.scalar(
        select(ArtifactEvaluation).where(
            ArtifactEvaluation.artifact_digest == row.digest,
            ArtifactEvaluation.policy_digest == policy_digest,
            ArtifactEvaluation.evidence_digest == evidence_digest,
        )
    )
    if replay is not None:
        return replay, False
    if row.state not in {"published", "evaluated", "approved"}:
        fail(409, "invalid_transition", "Artifact is unavailable for evaluation")
    baseline, failures = compare(db, row, policy, evidence)
    decision = ArtifactEvaluation(
        artifact_digest=row.digest,
        policy_digest=policy_digest,
        evidence_digest=evidence_digest,
        baseline_digest=baseline,
        evidence=evidence.model_dump(mode="json"),
        violations=failures,
    )
    db.add(decision)
    db.flush()
    previous = row.state
    if not failures and previous == "published":
        row.state = "evaluated"
    # Preserve the public lifecycle vocabulary and audit unsuccessful attempts too.
    verdict = "rejected" if failures else "passed"
    db.add(
        ArtifactAudit(
            artifact_digest=row.digest,
            from_state=previous,
            to_state=row.state,
            actor="evaluator",
            evaluation_id=decision.id,
            reason=f"Gate {verdict}. Evidence sha256:{evidence_digest}.",
        )
    )
    db.commit()
    return decision, True


def approval_evidence(
    db: Session, row: Artifact, policy: GatePolicy, policy_digest: str
) -> ArtifactEvaluation:
    latest = latest_evaluation(db, row.digest)
    if latest is None or latest.violations or latest.policy_digest != policy_digest:
        fail(
            409,
            "gate_evidence_required",
            "A passing evaluation under the active policy is required",
        )
    _, failures = compare(db, row, policy, Evaluation.model_validate(latest.evidence))
    if failures:
        fail(409, "gate_rejected", " ".join(failures)[:2000])
    return latest
