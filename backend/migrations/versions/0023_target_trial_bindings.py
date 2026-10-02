"""Immutable association between a published strategy version and a trial cell."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "quant_target_trial_bindings",
        sa.Column("strategy_version_id", UUID(as_uuid=True),
                  sa.ForeignKey("quant_strategy_versions.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("trial_id", sa.String(128), nullable=False),
        sa.Column("definition_hash", sa.String(64), nullable=False),
        sa.Column("family_id", sa.String(64), nullable=False),
        sa.Column("asset_scope", sa.String(16), nullable=False),
        sa.Column("scanner_source_hash", sa.String(64), nullable=False),
        sa.Column("lifecycle_policy_version_id", UUID(as_uuid=True),
                  sa.ForeignKey("lifecycle_policy_versions.id", ondelete="RESTRICT")),
        sa.Column("trial_spec", JSONB, nullable=False),
        sa.Column("management_config", JSONB, nullable=False),
        sa.Column("bound_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.clock_timestamp()),
        sa.CheckConstraint("length(trim(trial_id)) > 0 AND length(trim(family_id)) > 0",
                           name="ck_target_binding_identity"),
        sa.CheckConstraint("asset_scope IN ('CN_STOCK','CN_ETF')",
                           name="ck_target_binding_scope"),
        sa.CheckConstraint("definition_hash ~ '^[0-9a-f]{64}$' AND scanner_source_hash ~ '^[0-9a-f]{64}$'",
                           name="ck_target_binding_hashes"),
    )
    op.execute("CREATE TRIGGER target_binding_immutable BEFORE UPDATE OR DELETE "
               "ON quant_target_trial_bindings FOR EACH ROW "
               "EXECUTE FUNCTION reject_allocation_mutation()")


def downgrade():
    op.drop_table("quant_target_trial_bindings")
