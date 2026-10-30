"""Create fleet registry tables without disturbing existing Week 1 data.

Revision ID: 0001_fleet_registry
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_fleet_registry"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    op.create_table(
        "robots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("platform", sa.String(128), nullable=False),
        sa.Column("hardware_profile", json_type, nullable=False),
        sa.Column("site", sa.String(200), nullable=False),
        sa.Column("cohort_labels", json_type, nullable=False),
        sa.Column("safe_update_window", json_type, nullable=True),
        sa.Column("idempotency_key", sa.String(200), unique=True),
        sa.Column("enrollment_request_hash", sa.String(64)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_robots_site", "robots", ["site"])
    op.create_table(
        "enrollments",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column(
            "robot_id",
            sa.Uuid(),
            sa.ForeignKey("robots.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "issued_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_enrollments_robot_id", "enrollments", ["robot_id"])
    op.create_table(
        "reported_states",
        sa.Column(
            "robot_id",
            sa.Uuid(),
            sa.ForeignKey("robots.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("current_digest", sa.String(128)),
        sa.Column("previous_digest", sa.String(128)),
        sa.Column("agent_version", sa.String(128)),
        sa.Column("last_seen", sa.DateTime(timezone=True)),
        sa.Column("health_summary", json_type, nullable=False),
        sa.Column(
            "heartbeat_sequence", sa.BigInteger(), server_default="0", nullable=False
        ),
    )
    op.create_index(
        "ix_reported_states_current_digest", "reported_states", ["current_digest"]
    )
    op.create_index("ix_reported_states_last_seen", "reported_states", ["last_seen"])
    op.create_table(
        "desired_states",
        sa.Column(
            "robot_id",
            sa.Uuid(),
            sa.ForeignKey("robots.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("target_digest", sa.String(128)),
        sa.Column("set_by", sa.String(200)),
        sa.Column("reason", sa.String(1000)),
        sa.Column("set_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_table("desired_states")
    op.drop_index("ix_reported_states_last_seen", table_name="reported_states")
    op.drop_index("ix_reported_states_current_digest", table_name="reported_states")
    op.drop_table("reported_states")
    op.drop_index("ix_enrollments_robot_id", table_name="enrollments")
    op.drop_table("enrollments")
    op.drop_index("ix_robots_site", table_name="robots")
    op.drop_table("robots")
