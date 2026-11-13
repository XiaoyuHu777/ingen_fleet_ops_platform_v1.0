"""Relational fleet registry state; credentials are stored only as hashes."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


json_type = JSON().with_variant(JSONB(), "postgresql")


class Robot(Base):
    __tablename__ = "robots"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    platform: Mapped[str] = mapped_column(String(128), nullable=False)
    hardware_profile: Mapped[dict[str, Any]] = mapped_column(json_type, nullable=False)
    site: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    cohort_labels: Mapped[list[str]] = mapped_column(json_type, nullable=False)
    safe_update_window: Mapped[dict[str, Any] | None] = mapped_column(json_type)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), unique=True)
    enrollment_request_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Enrollment(Base):
    __tablename__ = "enrollments"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    robot_id: Mapped[UUID] = mapped_column(
        ForeignKey("robots.id", ondelete="CASCADE"), nullable=False, index=True
    )
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReportedState(Base):
    __tablename__ = "reported_states"
    __table_args__ = (
        Index("ix_reported_states_current_digest", "current_digest"),
        Index("ix_reported_states_last_seen", "last_seen"),
    )

    robot_id: Mapped[UUID] = mapped_column(
        ForeignKey("robots.id", ondelete="CASCADE"), primary_key=True
    )
    current_digest: Mapped[str | None] = mapped_column(String(128))
    previous_digest: Mapped[str | None] = mapped_column(String(128))
    agent_version: Mapped[str | None] = mapped_column(String(128))
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    health_summary: Mapped[dict[str, Any]] = mapped_column(
        json_type, default=dict, nullable=False
    )
    heartbeat_sequence: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )


class DesiredState(Base):
    __tablename__ = "desired_states"

    robot_id: Mapped[UUID] = mapped_column(
        ForeignKey("robots.id", ondelete="CASCADE"), primary_key=True
    )
    target_digest: Mapped[str | None] = mapped_column(String(128))
    set_by: Mapped[str | None] = mapped_column(String(200))
    reason: Mapped[str | None] = mapped_column(String(1000))
    set_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_artifacts_name_version"),
        CheckConstraint(
            "state IN ('published', 'evaluated', 'approved', 'deprecated', 'revoked')",
            name="ck_artifacts_state",
        ),
        Index("ix_artifacts_state_digest", "state", "digest"),
    )

    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[str] = mapped_column(String(80), nullable=False)
    signing_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    bundle_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ArtifactAudit(Base):
    __tablename__ = "artifact_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    artifact_digest: Mapped[str] = mapped_column(
        ForeignKey("artifacts.digest", ondelete="RESTRICT"), nullable=False, index=True
    )
    from_state: Mapped[str | None] = mapped_column(String(16))
    to_state: Mapped[str] = mapped_column(String(16), nullable=False)
    actor: Mapped[str] = mapped_column(String(200), nullable=False)
    reason: Mapped[str] = mapped_column(String(1000), nullable=False)
    evaluation_id: Mapped[int | None] = mapped_column(Integer)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ArtifactEvaluation(Base):
    __tablename__ = "artifact_evaluations"
    __table_args__ = (
        UniqueConstraint(
            "artifact_digest",
            "policy_digest",
            "evidence_digest",
            name="uq_artifact_evaluations_evidence",
        ),
        Index("ix_artifact_evaluations_artifact_id", "artifact_digest", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    artifact_digest: Mapped[str] = mapped_column(
        ForeignKey("artifacts.digest", ondelete="RESTRICT"), nullable=False
    )
    policy_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    baseline_digest: Mapped[str | None] = mapped_column(
        ForeignKey("artifacts.digest", ondelete="RESTRICT")
    )
    evidence: Mapped[dict[str, Any]] = mapped_column(json_type, nullable=False)
    violations: Mapped[list[str]] = mapped_column(json_type, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
