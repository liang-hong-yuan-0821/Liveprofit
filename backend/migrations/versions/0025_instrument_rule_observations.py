"""Immutable source artifacts and dated instrument rule observations."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "quant_instrument_rule_sources",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("source_uri", sa.Text, nullable=False),
        sa.Column("media_type", sa.String(128), nullable=False),
        sa.Column("raw_content", sa.LargeBinary, nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("published_on", sa.Date, nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.clock_timestamp()),
        sa.UniqueConstraint("source_uri", "content_sha256", name="uq_instrument_rule_source_content"),
        sa.CheckConstraint("length(trim(source_uri)) > 0 AND length(trim(media_type)) > 0 "
                           "AND octet_length(raw_content) > 0", name="ck_instrument_rule_source_body"),
        sa.CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'",
                           name="ck_instrument_rule_source_hash"),
    )
    op.create_table(
        "quant_instrument_rule_observations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("source_id", UUID(as_uuid=True),
                  sa.ForeignKey("quant_instrument_rule_sources.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("asset_type", sa.String(16), nullable=False),
        sa.Column("effective_from", sa.Date, nullable=False),
        sa.Column("effective_through", sa.Date, nullable=True),
        sa.Column("price_tick", sa.Numeric(18, 8), nullable=False),
        sa.Column("min_buy_quantity", sa.Integer, nullable=False),
        sa.Column("buy_quantity_step", sa.Integer, nullable=False),
        sa.Column("max_buy_quantity", sa.Integer, nullable=False),
        sa.Column("roundtrip_days", sa.SmallInteger, nullable=False),
        sa.Column("rule_sha256", sa.String(64), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.clock_timestamp()),
        sa.UniqueConstraint("source_id", "symbol", "effective_from",
                            name="uq_instrument_rule_source_symbol_from"),
        sa.CheckConstraint("length(trim(symbol)) > 0 AND asset_type IN ('stock','etf')",
                           name="ck_instrument_rule_identity"),
        sa.CheckConstraint("effective_through IS NULL OR effective_through >= effective_from",
                           name="ck_instrument_rule_dates"),
        sa.CheckConstraint("price_tick > 0 AND min_buy_quantity > 0 AND buy_quantity_step > 0 "
                           "AND max_buy_quantity >= min_buy_quantity AND roundtrip_days IN (0,1)",
                           name="ck_instrument_rule_values"),
        sa.CheckConstraint("rule_sha256 ~ '^[0-9a-f]{64}$'",
                           name="ck_instrument_rule_hash"),
    )
    op.create_index("ix_instrument_rule_symbol_dates", "quant_instrument_rule_observations",
                    ["symbol", "effective_from", "effective_through"])
    for table in ("quant_instrument_rule_sources", "quant_instrument_rule_observations"):
        op.execute(f"CREATE TRIGGER instrument_rule_immutable BEFORE UPDATE OR DELETE ON {table} "
                   "FOR EACH ROW EXECUTE FUNCTION reject_allocation_mutation()")
        op.execute(f"CREATE TRIGGER instrument_rule_no_truncate BEFORE TRUNCATE ON {table} "
                   "FOR EACH STATEMENT EXECUTE FUNCTION reject_allocation_mutation()")


def downgrade() -> None:
    op.drop_table("quant_instrument_rule_observations")
    op.drop_table("quant_instrument_rule_sources")
