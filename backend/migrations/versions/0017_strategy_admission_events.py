"""Append-only scoped qualification facts; publication alone grants no admission."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "strategy_admission_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("strategy_version_id", UUID(as_uuid=True), sa.ForeignKey("quant_strategy_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("family_id", sa.String(64), nullable=False),
        sa.Column("asset_scope", sa.String(16), nullable=False),
        sa.Column("risk_profile", sa.String(16), nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("request_key", sa.String(128), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.text("clock_timestamp()"), nullable=False),
        sa.Column("evidence_ref", sa.Text), sa.Column("evidence_sha256", sa.String(64)),
        sa.Column("evidence_completed_at", sa.DateTime(timezone=True)),
        sa.Column("valid_until", sa.DateTime(timezone=True)),
        sa.Column("net_expectancy_lower_bound", sa.Numeric(24, 12)),
        sa.UniqueConstraint("strategy_version_id", "asset_scope", "risk_profile", "revision", name="uq_admission_revision"),
        sa.UniqueConstraint("strategy_version_id", "asset_scope", "risk_profile", "request_key", name="uq_admission_request"),
        sa.CheckConstraint("revision > 0 AND length(trim(family_id)) > 0 AND length(trim(reason)) > 0 AND length(trim(request_key)) > 0", name="ck_admission_identity"),
        sa.CheckConstraint("state IN ('EXPERIMENTAL','VALIDATED','SHADOW','ADVISORY','SUSPENDED','RETIRED')", name="ck_admission_state"),
        sa.CheckConstraint("asset_scope IN ('CN_STOCK','CN_ETF')", name="ck_admission_scope"),
        sa.CheckConstraint("risk_profile IN ('CONSERVATIVE','BALANCED','AGGRESSIVE')", name="ck_admission_profile"),
        sa.CheckConstraint("state NOT IN ('VALIDATED','SHADOW','ADVISORY') OR (evidence_ref IS NOT NULL AND length(trim(evidence_ref)) > 0 AND evidence_sha256 IS NOT NULL AND evidence_sha256 ~ '^[0-9a-f]{64}$' AND evidence_completed_at IS NOT NULL AND evidence_completed_at <= recorded_at)", name="ck_admission_evidence"),
        sa.CheckConstraint("state != 'ADVISORY' OR (valid_until IS NOT NULL AND valid_until > recorded_at AND net_expectancy_lower_bound IS NOT NULL AND net_expectancy_lower_bound > 0 AND net_expectancy_lower_bound != 'NaN'::numeric)", name="ck_admission_advisory"),
    )
    op.create_index("ix_admission_asof", "strategy_admission_events", ["strategy_version_id", "asset_scope", "risk_profile", "recorded_at"])
    op.execute("""CREATE FUNCTION reject_admission_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'strategy admission history is append-only'; END; $$""")
    op.execute("CREATE TRIGGER admission_immutable BEFORE UPDATE OR DELETE ON strategy_admission_events FOR EACH ROW EXECUTE FUNCTION reject_admission_mutation()")


def downgrade():
    op.drop_table("strategy_admission_events")
    op.execute("DROP FUNCTION reject_admission_mutation()")
