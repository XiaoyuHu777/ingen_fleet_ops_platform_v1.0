"""Add immutable artifact records and append-only lifecycle audit events."""

import sqlalchemy as sa
from alembic import op

revision = "0002_signed_artifacts"
down_revision = "0001_fleet_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "artifacts",
        sa.Column("digest", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("version", sa.String(80), nullable=False),
        sa.Column("signing_key_id", sa.String(64), nullable=False),
        sa.Column("bundle_sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("name", "version", name="uq_artifacts_name_version"),
        sa.CheckConstraint(
            "state IN ('published', 'evaluated', 'approved', 'deprecated', 'revoked')",
            name="ck_artifacts_state",
        ),
    )
    op.create_index("ix_artifacts_state_digest", "artifacts", ["state", "digest"])
    op.create_table(
        "artifact_audit",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "artifact_digest",
            sa.String(64),
            sa.ForeignKey("artifacts.digest", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("from_state", sa.String(16)),
        sa.Column("to_state", sa.String(16), nullable=False),
        sa.Column("actor", sa.String(200), nullable=False),
        sa.Column("reason", sa.String(1000), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_artifact_audit_artifact_digest", "artifact_audit", ["artifact_digest"]
    )

    if op.get_context().dialect.name == "postgresql":
        op.execute("""
            CREATE FUNCTION reject_artifact_audit_change() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'artifact audit is append-only';
            END;
            $$
        """)
        op.execute("""
            CREATE TRIGGER artifact_audit_append_only
            BEFORE UPDATE OR DELETE OR TRUNCATE ON artifact_audit
            FOR EACH STATEMENT EXECUTE FUNCTION reject_artifact_audit_change()
        """)
    else:
        for operation in ("UPDATE", "DELETE"):
            op.execute(f"""
                CREATE TRIGGER artifact_audit_no_{operation.lower()}
                BEFORE {operation} ON artifact_audit
                BEGIN SELECT RAISE(ABORT, 'artifact audit is append-only'); END
            """)


def downgrade() -> None:
    op.drop_index("ix_artifact_audit_artifact_digest", table_name="artifact_audit")
    op.drop_table("artifact_audit")
    if op.get_context().dialect.name == "postgresql":
        op.execute("DROP FUNCTION reject_artifact_audit_change()")
    op.drop_index("ix_artifacts_state_digest", table_name="artifacts")
    op.drop_table("artifacts")
