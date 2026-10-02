"""Link a reviewed resolution to both immutable reversal streams."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("account_fill_postings",
                  sa.Column("position_quantity_before", sa.Numeric(20, 4), nullable=True))
    op.add_column("account_fill_postings",
                  sa.Column("position_average_cost_before", sa.Numeric(20, 4), nullable=True))
    op.create_table(
        "account_fill_posting_voids",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("portfolio_id", UUID(as_uuid=True),
                  sa.ForeignKey("portfolios.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("resolution_id", UUID(as_uuid=True),
                  sa.ForeignKey("account_fill_report_resolutions.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("posting_id", UUID(as_uuid=True),
                  sa.ForeignKey("account_fill_postings.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("fill_event_id", UUID(as_uuid=True),
                  sa.ForeignKey("order_fill_events.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("ledger_movement_id", UUID(as_uuid=True),
                  sa.ForeignKey("account_ledger_movements.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.clock_timestamp()),
        sa.UniqueConstraint("resolution_id", name="uq_fill_posting_void_resolution"),
        sa.UniqueConstraint("posting_id", name="uq_fill_posting_void_posting"),
        sa.UniqueConstraint("fill_event_id", name="uq_fill_posting_void_event"),
        sa.UniqueConstraint("ledger_movement_id", name="uq_fill_posting_void_ledger"),
    )
    op.execute("""CREATE FUNCTION validate_fill_posting_void() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE r account_fill_report_resolutions%ROWTYPE;
        DECLARE p account_fill_postings%ROWTYPE;
        DECLARE original_fill order_fill_events%ROWTYPE;
        DECLARE void_fill order_fill_events%ROWTYPE;
        DECLARE original_ledger account_ledger_movements%ROWTYPE;
        DECLARE void_ledger account_ledger_movements%ROWTYPE;
        BEGIN
            SELECT * INTO r FROM account_fill_report_resolutions WHERE id = NEW.resolution_id;
            SELECT * INTO p FROM account_fill_postings WHERE id = NEW.posting_id;
            SELECT * INTO original_fill FROM order_fill_events WHERE id = p.fill_event_id;
            SELECT * INTO void_fill FROM order_fill_events WHERE id = NEW.fill_event_id;
            SELECT * INTO original_ledger FROM account_ledger_movements WHERE id = p.ledger_movement_id;
            SELECT * INTO void_ledger FROM account_ledger_movements WHERE id = NEW.ledger_movement_id;
            IF r.id IS NULL OR p.id IS NULL OR original_fill.id IS NULL OR
               void_fill.id IS NULL OR original_ledger.id IS NULL OR void_ledger.id IS NULL OR
               r.action <> 'VOID' OR r.report_id <> p.report_id OR
               r.portfolio_id <> NEW.portfolio_id OR p.portfolio_id <> NEW.portfolio_id OR
               original_fill.portfolio_id <> NEW.portfolio_id OR
               void_fill.portfolio_id <> NEW.portfolio_id OR
               original_ledger.portfolio_id <> NEW.portfolio_id OR
               void_ledger.portfolio_id <> NEW.portfolio_id OR
               original_fill.event_type <> 'CONFIRM' OR
               void_fill.event_type <> 'VOID' OR
               void_fill.reverses_fill_id <> original_fill.id OR
               void_fill.order_id <> original_fill.order_id OR
               void_fill.quantity <> original_fill.quantity OR
               void_fill.fill_price <> original_fill.fill_price OR
               void_fill.fill_trade_date <> original_fill.fill_trade_date OR
               void_fill.position_id IS DISTINCT FROM original_fill.position_id OR
               void_fill.source <> original_fill.source OR
               original_ledger.kind <> 'TRADE' OR void_ledger.kind <> 'VOID' OR
               void_ledger.supersedes_id <> original_ledger.id THEN
                RAISE EXCEPTION 'fill posting void identity mismatch';
            END IF;
            RETURN NEW;
        END; $$""")
    op.execute("CREATE TRIGGER fill_posting_void_validate BEFORE INSERT ON "
               "account_fill_posting_voids FOR EACH ROW EXECUTE FUNCTION validate_fill_posting_void()")
    op.execute("""CREATE FUNCTION reject_fill_posting_void_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'fill posting void history is immutable';
        END; $$""")
    op.execute("CREATE TRIGGER fill_posting_void_immutable BEFORE UPDATE OR DELETE ON "
               "account_fill_posting_voids FOR EACH ROW EXECUTE FUNCTION reject_fill_posting_void_mutation()")
    op.execute("CREATE TRIGGER fill_posting_void_no_truncate BEFORE TRUNCATE ON "
               "account_fill_posting_voids FOR EACH STATEMENT EXECUTE FUNCTION reject_fill_posting_void_mutation()")


def downgrade() -> None:
    op.drop_table("account_fill_posting_voids")
    op.drop_column("account_fill_postings", "position_average_cost_before")
    op.drop_column("account_fill_postings", "position_quantity_before")
    op.execute("DROP FUNCTION validate_fill_posting_void()")
    op.execute("DROP FUNCTION reject_fill_posting_void_mutation()")
