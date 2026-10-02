"""Bind a corrected report to reversal and replacement postings."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "account_fill_posting_corrections",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("portfolio_id", UUID(as_uuid=True),
                  sa.ForeignKey("portfolios.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("resolution_id", UUID(as_uuid=True),
                  sa.ForeignKey("account_fill_report_resolutions.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("original_posting_id", UUID(as_uuid=True),
                  sa.ForeignKey("account_fill_postings.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("void_fill_event_id", UUID(as_uuid=True),
                  sa.ForeignKey("order_fill_events.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("void_ledger_movement_id", UUID(as_uuid=True),
                  sa.ForeignKey("account_ledger_movements.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("replacement_posting_id", UUID(as_uuid=True),
                  sa.ForeignKey("account_fill_postings.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.clock_timestamp()),
        sa.UniqueConstraint("resolution_id", name="uq_fill_posting_correction_resolution"),
        sa.UniqueConstraint("original_posting_id", name="uq_fill_posting_correction_original"),
        sa.UniqueConstraint("void_fill_event_id", name="uq_fill_posting_correction_void_event"),
        sa.UniqueConstraint("void_ledger_movement_id", name="uq_fill_posting_correction_void_ledger"),
        sa.UniqueConstraint("replacement_posting_id", name="uq_fill_posting_correction_replacement"),
    )
    op.execute("""CREATE FUNCTION validate_fill_posting_correction() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE r account_fill_report_resolutions%ROWTYPE;
        DECLARE old_p account_fill_postings%ROWTYPE;
        DECLARE new_p account_fill_postings%ROWTYPE;
        DECLARE old_f order_fill_events%ROWTYPE;
        DECLARE void_f order_fill_events%ROWTYPE;
        DECLARE new_f order_fill_events%ROWTYPE;
        DECLARE old_m account_ledger_movements%ROWTYPE;
        DECLARE void_m account_ledger_movements%ROWTYPE;
        BEGIN
            SELECT * INTO r FROM account_fill_report_resolutions WHERE id = NEW.resolution_id;
            SELECT * INTO old_p FROM account_fill_postings WHERE id = NEW.original_posting_id;
            SELECT * INTO new_p FROM account_fill_postings WHERE id = NEW.replacement_posting_id;
            SELECT * INTO old_f FROM order_fill_events WHERE id = old_p.fill_event_id;
            SELECT * INTO void_f FROM order_fill_events WHERE id = NEW.void_fill_event_id;
            SELECT * INTO new_f FROM order_fill_events WHERE id = new_p.fill_event_id;
            SELECT * INTO old_m FROM account_ledger_movements WHERE id = old_p.ledger_movement_id;
            SELECT * INTO void_m FROM account_ledger_movements WHERE id = NEW.void_ledger_movement_id;
            IF r.id IS NULL OR old_p.id IS NULL OR new_p.id IS NULL OR
               old_f.id IS NULL OR void_f.id IS NULL OR new_f.id IS NULL OR
               old_m.id IS NULL OR void_m.id IS NULL OR
               r.action <> 'CORRECT' OR r.report_id <> old_p.report_id OR
               r.replacement_report_id <> new_p.report_id OR
               r.portfolio_id <> NEW.portfolio_id OR
               old_p.portfolio_id <> NEW.portfolio_id OR new_p.portfolio_id <> NEW.portfolio_id OR
               old_f.portfolio_id <> NEW.portfolio_id OR void_f.portfolio_id <> NEW.portfolio_id OR
               new_f.portfolio_id <> NEW.portfolio_id OR
               old_m.portfolio_id <> NEW.portfolio_id OR void_m.portfolio_id <> NEW.portfolio_id OR
               old_f.event_type <> 'CONFIRM' OR void_f.event_type <> 'VOID' OR
               new_f.event_type <> 'CONFIRM' OR new_f.order_id <> old_f.order_id OR
               void_f.reverses_fill_id <> old_f.id OR void_f.order_id <> old_f.order_id OR
               void_f.quantity <> old_f.quantity OR void_f.fill_price <> old_f.fill_price OR
               void_f.fill_trade_date <> old_f.fill_trade_date OR
               void_f.position_id IS DISTINCT FROM old_f.position_id OR
               void_f.source <> old_f.source OR
               old_m.kind <> 'TRADE' OR void_m.kind <> 'VOID' OR
               void_m.supersedes_id <> old_m.id THEN
                RAISE EXCEPTION 'fill posting correction identity mismatch';
            END IF;
            RETURN NEW;
        END; $$""")
    op.execute("CREATE TRIGGER fill_posting_correction_validate BEFORE INSERT ON "
               "account_fill_posting_corrections FOR EACH ROW "
               "EXECUTE FUNCTION validate_fill_posting_correction()")
    op.execute("""CREATE FUNCTION reject_fill_posting_correction_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'fill posting correction history is immutable';
        END; $$""")
    op.execute("CREATE TRIGGER fill_posting_correction_immutable BEFORE UPDATE OR DELETE ON "
               "account_fill_posting_corrections FOR EACH ROW "
               "EXECUTE FUNCTION reject_fill_posting_correction_mutation()")
    op.execute("CREATE TRIGGER fill_posting_correction_no_truncate BEFORE TRUNCATE ON "
               "account_fill_posting_corrections FOR EACH STATEMENT "
               "EXECUTE FUNCTION reject_fill_posting_correction_mutation()")


def downgrade() -> None:
    op.drop_table("account_fill_posting_corrections")
    op.execute("DROP FUNCTION validate_fill_posting_correction()")
    op.execute("DROP FUNCTION reject_fill_posting_correction_mutation()")
