"""Persist immutable evaluation decisions for the promotion gate."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003_evaluation_gate"
down_revision = "0002_signed_artifacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    document = sa.JSON().with_variant(JSONB(), "postgresql")
    op.create_table(
        "artifact_evaluations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "artifact_digest",
            sa.String(64),
            sa.ForeignKey("artifacts.digest", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("policy_digest", sa.String(64), nullable=False),
        sa.Column("evidence_digest", sa.String(64), nullable=False),
        sa.Column(
            "baseline_digest",
            sa.String(64),
            sa.ForeignKey("artifacts.digest", ondelete="RESTRICT"),
        ),
        sa.Column("evidence", document, nullable=False),
        sa.Column("violations", document, nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "artifact_digest",
            "policy_digest",
            "evidence_digest",
            name="uq_artifact_evaluations_evidence",
        ),
    )
    op.create_index(
        "ix_artifact_evaluations_artifact_id",
        "artifact_evaluations",
        ["artifact_digest", "id"],
    )
    op.add_column("artifact_audit", sa.Column("evaluation_id", sa.Integer()))
    if op.get_context().dialect.name == "postgresql":
        op.execute("""
            CREATE TRIGGER artifact_evaluations_append_only
            BEFORE UPDATE OR DELETE OR TRUNCATE ON artifact_evaluations
            FOR EACH STATEMENT EXECUTE FUNCTION reject_artifact_audit_change()
        """)
    else:
        for operation in ("UPDATE", "DELETE"):
            op.execute(f"""
                CREATE TRIGGER artifact_evaluations_no_{operation.lower()}
                BEFORE {operation} ON artifact_evaluations
                BEGIN SELECT RAISE(ABORT, 'artifact evaluations are append-only'); END
            """)


def downgrade() -> None:
    op.drop_index(
        "ix_artifact_evaluations_artifact_id", table_name="artifact_evaluations"
    )
    op.drop_table("artifact_evaluations")
    op.drop_column("artifact_audit", "evaluation_id")
