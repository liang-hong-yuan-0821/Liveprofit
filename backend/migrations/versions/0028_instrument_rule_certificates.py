"""Immutable, signed forward-use interpretations of captured exchange originals."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "quant_instrument_rule_certificates",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("observation_id", UUID(as_uuid=True), sa.ForeignKey(
            "quant_instrument_rule_observations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("rule_capture_id", UUID(as_uuid=True), sa.ForeignKey(
            "quant_instrument_rule_captures.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("identity_capture_id", UUID(as_uuid=True), sa.ForeignKey(
            "quant_instrument_rule_captures.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("asset_type", sa.String(16), nullable=False),
        sa.Column("identity_symbol", sa.String(32), nullable=False),
        sa.Column("identity_asset_type", sa.String(16), nullable=False),
        sa.Column("identity_locator", sa.Text, nullable=False),
        sa.Column("reviewer_id", sa.String(128), nullable=False),
        sa.Column("interpretation_note", sa.Text, nullable=False),
        sa.Column("review_signature", sa.String(64), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.clock_timestamp()),
        sa.UniqueConstraint("observation_id", name="uq_instrument_rule_certificate_observation"),
        sa.CheckConstraint("rule_capture_id <> identity_capture_id AND "
                           "length(trim(symbol)) > 0 AND asset_type IN ('stock','etf') AND "
                           "identity_symbol = symbol AND identity_asset_type = asset_type AND "
                           "length(trim(identity_locator)) > 0 AND "
                           "length(trim(reviewer_id)) > 0 AND length(trim(interpretation_note)) > 0 AND "
                           "review_signature ~ '^[0-9a-f]{64}$'",
                           name="ck_instrument_rule_certificate_values"),
    )
    op.create_index("ix_instrument_rule_certificate_symbol", "quant_instrument_rule_certificates",
                    ["symbol", "asset_type"])
    op.execute("CREATE TRIGGER instrument_rule_certificate_immutable BEFORE UPDATE OR DELETE "
               "ON quant_instrument_rule_certificates FOR EACH ROW "
               "EXECUTE FUNCTION reject_allocation_mutation()")
    op.execute("CREATE TRIGGER instrument_rule_certificate_no_truncate BEFORE TRUNCATE "
               "ON quant_instrument_rule_certificates FOR EACH STATEMENT "
               "EXECUTE FUNCTION reject_allocation_mutation()")


def downgrade() -> None:
    op.drop_table("quant_instrument_rule_certificates")
