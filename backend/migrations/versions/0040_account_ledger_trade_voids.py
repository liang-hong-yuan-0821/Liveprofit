"""Allow immutable diagnostic voids of recorded account trades."""

from alembic import op


revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


OLD_CHECK = (
    "kind IN ('CASH_FLOW','TRADE','CORPORATE_ACTION','ADJUSTMENT') AND "
    "jsonb_typeof(holdings_delta) = 'array' AND fee >= 0 AND "
    "length(trim(source_ref)) > 0 AND payload_sha256 ~ '^[0-9a-f]{64}$'"
)
NEW_CHECK = (
    "kind IN ('CASH_FLOW','TRADE','CORPORATE_ACTION','ADJUSTMENT','VOID') AND "
    "jsonb_typeof(holdings_delta) = 'array' AND fee >= 0 AND "
    "length(trim(source_ref)) > 0 AND payload_sha256 ~ '^[0-9a-f]{64}$' AND "
    "(kind <> 'VOID' OR (supersedes_id IS NOT NULL AND cash_delta = 0 "
    "AND holdings_delta = '[]'::jsonb AND fill_price IS NULL "
    "AND fee = 0 AND reason IS NOT NULL AND length(trim(reason)) > 0))"
)


def upgrade() -> None:
    op.drop_constraint("ck_account_ledger_movement_values", "account_ledger_movements", type_="check")
    op.create_check_constraint("ck_account_ledger_movement_values", "account_ledger_movements", NEW_CHECK)
    op.execute("""CREATE FUNCTION validate_account_ledger_trade_void() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE predecessor account_ledger_movements%ROWTYPE;
        BEGIN
            IF NEW.kind <> 'VOID' THEN
                RETURN NEW;
            END IF;
            SELECT * INTO predecessor FROM account_ledger_movements WHERE id = NEW.supersedes_id;
            IF NOT FOUND OR predecessor.kind <> 'TRADE'
                OR predecessor.portfolio_id <> NEW.portfolio_id
                OR predecessor.effective_at <> NEW.effective_at THEN
                RAISE EXCEPTION 'void predecessor must be a matching trade';
            END IF;
            RETURN NEW;
        END; $$""")
    op.execute("CREATE TRIGGER account_ledger_validate_trade_void BEFORE INSERT ON "
               "account_ledger_movements FOR EACH ROW EXECUTE FUNCTION validate_account_ledger_trade_void()")


def downgrade() -> None:
    op.execute("DROP TRIGGER account_ledger_validate_trade_void ON account_ledger_movements")
    op.execute("DROP FUNCTION validate_account_ledger_trade_void()")
    op.drop_constraint("ck_account_ledger_movement_values", "account_ledger_movements", type_="check")
    op.create_check_constraint("ck_account_ledger_movement_values", "account_ledger_movements", OLD_CHECK)
