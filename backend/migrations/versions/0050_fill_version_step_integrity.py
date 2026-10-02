"""Harden locally captured fill version steps without inventing old history.

Trigger ancestry and the transaction-local fill ID establish local causality,
not broker provenance or authorization of a database caller. Roles allowed to
change/disable triggers remain outside this integrity boundary.
"""

import sqlalchemy as sa
from alembic import op

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None

_CONSTRAINT = "ck_fill_lifecycle_version_step_shape"
_STRICT_SHAPE = (
    "(origin = 'LOCAL_CAUSAL' AND version_before IS NOT NULL "
    "AND version_after IS NOT NULL AND version_before >= 1 "
    "AND version_after = version_before + 1) OR "
    "(origin = 'UNATTRIBUTED' AND version_before IS NULL "
    "AND version_after IS NULL)"
)
_LEGACY_SHAPE = (
    "(origin = 'LOCAL_CAUSAL' AND version_before >= 1 "
    "AND version_after = version_before + 1) OR "
    "(origin = 'UNATTRIBUTED' AND version_before IS NULL "
    "AND version_after IS NULL)"
)


def _lock_tables() -> None:
    op.execute("LOCK TABLE position_lifecycle_states IN SHARE ROW EXCLUSIVE MODE")
    op.execute("LOCK TABLE order_fill_events IN SHARE ROW EXCLUSIVE MODE")
    op.execute("LOCK TABLE fill_lifecycle_version_steps IN SHARE ROW EXCLUSIVE MODE")


def upgrade() -> None:
    _lock_tables()
    invalid_count = op.get_bind().scalar(sa.text(f"""
        SELECT count(*)
          FROM fill_lifecycle_version_steps s
          LEFT JOIN order_fill_events f ON f.id = s.fill_event_id
          LEFT JOIN position_lifecycle_states l ON l.id = s.lifecycle_id
         WHERE NOT ({_STRICT_SHAPE})
            OR f.id IS NULL OR l.id IS NULL
            OR f.binding_origin IS DISTINCT FROM 'LIVE'
            OR f.lifecycle_id_at_fill IS DISTINCT FROM s.lifecycle_id
            OR f.portfolio_id IS DISTINCT FROM l.portfolio_id
    """))
    if invalid_count:
        raise RuntimeError(
            "fill version step integrity preflight failed: "
            f"{invalid_count} invalid row(s); resolve historical evidence before 0050")
    op.drop_constraint(_CONSTRAINT, "fill_lifecycle_version_steps", type_="check")
    op.create_check_constraint(_CONSTRAINT, "fill_lifecycle_version_steps", _STRICT_SHAPE)
    op.execute("""CREATE FUNCTION validate_fill_lifecycle_version_step_insert() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE current_version integer;
        DECLARE current_fill_id text;
        DECLARE parent order_fill_events%ROWTYPE;
        BEGIN
            -- Both accepted insertions originate in one of the existing parent
            -- row triggers. A direct INSERT (including ORM add) has depth 1.
            IF pg_trigger_depth() <> 2 THEN
                RAISE EXCEPTION 'fill version steps require parent-row capture';
            END IF;
            IF NEW.origin = 'LOCAL_CAUSAL' THEN
                current_fill_id := NULLIF(current_setting('liveprofit.fill_event_id', true), '');
                SELECT state_version INTO current_version
                  FROM position_lifecycle_states WHERE id = NEW.lifecycle_id;
                IF current_fill_id IS NULL OR
                   current_fill_id::uuid IS DISTINCT FROM NEW.fill_event_id OR
                   current_version IS DISTINCT FROM NEW.version_after THEN
                    RAISE EXCEPTION 'fill version step differs from lifecycle capture';
                END IF;
            ELSIF NEW.origin = 'UNATTRIBUTED' THEN
                SELECT * INTO parent FROM order_fill_events WHERE id = NEW.fill_event_id;
                IF parent.id IS NULL OR parent.binding_origin IS DISTINCT FROM 'LIVE' OR
                   parent.lifecycle_id_at_fill IS DISTINCT FROM NEW.lifecycle_id THEN
                    RAISE EXCEPTION 'unattributed fill version step differs from parent fill';
                END IF;
            END IF;
            RETURN NEW;
        END; $$""")
    op.execute("""CREATE TRIGGER fill_lifecycle_version_step_validate_insert
        BEFORE INSERT ON fill_lifecycle_version_steps FOR EACH ROW
        EXECUTE FUNCTION validate_fill_lifecycle_version_step_insert()""")
    op.execute("""CREATE FUNCTION validate_fill_lifecycle_version_step_parent() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE parent order_fill_events%ROWTYPE;
        DECLARE lifecycle_portfolio uuid;
        BEGIN
            -- LOCAL_CAUSAL is captured before its fill is inserted. Check its
            -- frozen parent at commit, without comparing to the final version:
            -- several fills may legitimately advance this lifecycle in one tx.
            SELECT * INTO parent FROM order_fill_events WHERE id = NEW.fill_event_id;
            SELECT portfolio_id INTO lifecycle_portfolio
              FROM position_lifecycle_states WHERE id = NEW.lifecycle_id;
            IF parent.id IS NULL OR parent.binding_origin IS DISTINCT FROM 'LIVE' OR
               parent.lifecycle_id_at_fill IS DISTINCT FROM NEW.lifecycle_id OR
               lifecycle_portfolio IS NULL OR
               parent.portfolio_id IS DISTINCT FROM lifecycle_portfolio THEN
                RAISE EXCEPTION 'fill version step parent identity mismatch';
            END IF;
            RETURN NEW;
        END; $$""")
    op.execute("""CREATE CONSTRAINT TRIGGER fill_lifecycle_version_step_validate_parent
        AFTER INSERT ON fill_lifecycle_version_steps DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION validate_fill_lifecycle_version_step_parent()""")
    op.execute("""CREATE TRIGGER fill_lifecycle_version_step_no_truncate
        BEFORE TRUNCATE ON fill_lifecycle_version_steps FOR EACH STATEMENT
        EXECUTE FUNCTION reject_fill_lifecycle_version_step_mutation()""")


def downgrade() -> None:
    # Only remove this revision's guards; every recorded step remains intact.
    _lock_tables()
    op.execute("DROP TRIGGER fill_lifecycle_version_step_no_truncate ON fill_lifecycle_version_steps")
    op.execute("DROP TRIGGER fill_lifecycle_version_step_validate_parent ON fill_lifecycle_version_steps")
    op.execute("DROP FUNCTION validate_fill_lifecycle_version_step_parent()")
    op.execute("DROP TRIGGER fill_lifecycle_version_step_validate_insert ON fill_lifecycle_version_steps")
    op.execute("DROP FUNCTION validate_fill_lifecycle_version_step_insert()")
    op.drop_constraint(_CONSTRAINT, "fill_lifecycle_version_steps", type_="check")
    op.create_check_constraint(_CONSTRAINT, "fill_lifecycle_version_steps", _LEGACY_SHAPE)
