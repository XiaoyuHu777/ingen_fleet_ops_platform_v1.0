"""Relational fleet registry state; credentials are stored only as hashes."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Index, String, func
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
