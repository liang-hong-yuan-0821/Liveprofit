"""Bind a fully specified report to one order fill and one fee ledger trade."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("account_fill_reports", sa.Column("executed_at", sa.DateTime(timezone=True)))
    op.create_check_constraint(
        "ck_account_fill_report_execution_time", "account_fill_reports",
        "executed_at IS NULL OR (executed_at <= captured_at AND "
        "(market <> 'CN' OR (executed_at AT TIME ZONE 'Asia/Shanghai')::date = fill_trade_date))",
    )
    op.create_unique_constraint("uq_order_fill_event_portfolio_identity", "order_fill_events",
                                ["id", "portfolio_id"])
    op.create_table(
        "account_fill_postings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("portfolio_id", UUID(as_uuid=True),
                  sa.ForeignKey("portfolios.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("report_id", UUID(as_uuid=True), nullable=False),
        sa.Column("fill_event_id", UUID(as_uuid=True), nullable=False),
        sa.Column("ledger_movement_id", UUID(as_uuid=True), nullable=False),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.clock_timestamp()),
        sa.ForeignKeyConstraint(["report_id", "portfolio_id"],
                                ["account_fill_reports.id", "account_fill_reports.portfolio_id"],
                                ondelete="RESTRICT", name="fk_fill_posting_report_portfolio"),
        sa.ForeignKeyConstraint(["fill_event_id", "portfolio_id"],
                                ["order_fill_events.id", "order_fill_events.portfolio_id"],
                                ondelete="RESTRICT", name="fk_fill_posting_event_portfolio"),
        sa.ForeignKeyConstraint(["ledger_movement_id", "portfolio_id"],
                                ["account_ledger_movements.id", "account_ledger_movements.portfolio_id"],
                                ondelete="RESTRICT", name="fk_fill_posting_ledger_portfolio"),
        sa.UniqueConstraint("report_id", name="uq_fill_posting_report"),
        sa.UniqueConstraint("fill_event_id", name="uq_fill_posting_event"),
        sa.UniqueConstraint("ledger_movement_id", name="uq_fill_posting_ledger"),
    )
    op.execute("""CREATE FUNCTION validate_account_fill_posting() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            r account_fill_reports%ROWTYPE;
            f order_fill_events%ROWTYPE;
            m account_ledger_movements%ROWTYPE;
            o suggested_orders%ROWTYPE;
            signed_quantity numeric;
        BEGIN
            SELECT * INTO r FROM account_fill_reports WHERE id = NEW.report_id;
            SELECT * INTO f FROM order_fill_events WHERE id = NEW.fill_event_id;
            SELECT * INTO m FROM account_ledger_movements WHERE id = NEW.ledger_movement_id;
            IF r.id IS NULL OR f.id IS NULL OR m.id IS NULL OR
               r.portfolio_id <> NEW.portfolio_id OR f.portfolio_id <> NEW.portfolio_id OR
               m.portfolio_id <> NEW.portfolio_id THEN
                RAISE EXCEPTION 'fill posting identity mismatch';
            END IF;
            SELECT * INTO o FROM suggested_orders WHERE id = f.order_id;
            IF o.id IS NULL OR o.portfolio_id <> NEW.portfolio_id OR
               r.order_id IS NULL OR r.order_id <> f.order_id OR
               r.market <> 'CN' OR o.market <> r.market OR o.symbol <> r.symbol OR
               o.side <> r.side OR
               r.executed_at IS NULL OR r.fee IS NULL OR
               f.event_type <> 'CONFIRM' OR f.reverses_fill_id IS NOT NULL OR
               f.quantity <> r.quantity OR f.fill_price <> r.fill_price OR
               f.fill_trade_date <> r.fill_trade_date OR
               m.kind <> 'TRADE' OR m.supersedes_id IS NOT NULL OR
               m.effective_at IS DISTINCT FROM r.executed_at OR
               m.fill_price IS DISTINCT FROM r.fill_price OR m.fee IS DISTINCT FROM r.fee OR
               jsonb_array_length(m.holdings_delta) <> 1 OR
               jsonb_typeof(m.holdings_delta->0) IS DISTINCT FROM 'array' OR
               jsonb_array_length(m.holdings_delta->0) <> 3 OR
               m.holdings_delta->0->>0 IS DISTINCT FROM r.market OR
               m.holdings_delta->0->>1 IS DISTINCT FROM r.symbol THEN
                RAISE EXCEPTION 'fill posting values disagree';
            END IF;
            IF EXISTS (SELECT 1 FROM account_fill_report_resolutions WHERE report_id = r.id) OR
               EXISTS (SELECT 1 FROM order_fill_events WHERE reverses_fill_id = f.id) OR
               EXISTS (SELECT 1 FROM account_ledger_movements WHERE supersedes_id = m.id) THEN
                RAISE EXCEPTION 'resolved report or reversed trade cannot be newly posted';
            END IF;
            signed_quantity := CASE WHEN r.side = 'BUY' THEN r.quantity ELSE -r.quantity END;
            IF (m.holdings_delta->0->>2)::numeric IS DISTINCT FROM signed_quantity OR
               m.cash_delta IS DISTINCT FROM -signed_quantity * r.fill_price - r.fee THEN
                RAISE EXCEPTION 'fill posting trade cash or quantity disagree';
            END IF;
            RETURN NEW;
        END; $$""")
    op.execute("CREATE TRIGGER account_fill_posting_validate BEFORE INSERT ON account_fill_postings "
               "FOR EACH ROW EXECUTE FUNCTION validate_account_fill_posting()")
    op.execute("""CREATE FUNCTION reject_account_fill_posting_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'account fill posting history is immutable';
        END; $$""")
    op.execute("CREATE TRIGGER account_fill_posting_immutable BEFORE UPDATE OR DELETE ON "
               "account_fill_postings FOR EACH ROW EXECUTE FUNCTION reject_account_fill_posting_mutation()")
    op.execute("CREATE TRIGGER account_fill_posting_no_truncate BEFORE TRUNCATE ON "
               "account_fill_postings FOR EACH STATEMENT EXECUTE FUNCTION reject_account_fill_posting_mutation()")


def downgrade() -> None:
    op.drop_table("account_fill_postings")
    op.execute("DROP FUNCTION validate_account_fill_posting()")
    op.execute("DROP FUNCTION reject_account_fill_posting_mutation()")
    op.drop_constraint("uq_order_fill_event_portfolio_identity", "order_fill_events", type_="unique")
    op.drop_constraint("ck_account_fill_report_execution_time", "account_fill_reports", type_="check")
    op.drop_column("account_fill_reports", "executed_at")
