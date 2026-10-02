"""Keep an immutable local revision chain for each current lifecycle daily fact."""

import hashlib
import json
import os
from pathlib import Path

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "position_daily_fact_revisions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("daily_fact_id", UUID(as_uuid=True),
                  sa.ForeignKey("position_daily_facts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("previous_revision_id", UUID(as_uuid=True),
                  sa.ForeignKey("position_daily_fact_revisions.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("baseline_origin", sa.String(16), nullable=False),
        sa.Column("lifecycle_id", UUID(as_uuid=True), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("price_basis", sa.String(16), nullable=False),
        sa.Column("data_as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("input_payload", JSONB(), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("planning_result", JSONB(), nullable=True),
        sa.Column("rule_version", sa.String(64), nullable=False),
        sa.Column("state_version_before", sa.Integer(), nullable=False),
        sa.Column("state_version_after", sa.Integer(), nullable=False),
        sa.Column("final_target_shares", sa.Numeric(20, 4), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("daily_fact_id", "revision_no", name="uq_daily_fact_revision_no"),
        sa.UniqueConstraint("previous_revision_id", name="uq_daily_fact_revision_previous"),
        sa.CheckConstraint("revision_no > 0", name="ck_daily_fact_revision_positive"),
        sa.CheckConstraint("baseline_origin IN ('MIGRATED', 'LIVE')", name="ck_daily_fact_revision_origin"),
        sa.CheckConstraint("(revision_no = 1 AND previous_revision_id IS NULL) OR "
                           "(revision_no > 1 AND previous_revision_id IS NOT NULL)",
                           name="ck_daily_fact_revision_link"),
    )
    # The existing row is a migration baseline. Its original write history and
    # source publication time cannot be reconstructed from updated_at.
    op.execute("""INSERT INTO position_daily_fact_revisions (
        id, daily_fact_id, revision_no, previous_revision_id, baseline_origin, lifecycle_id,
        trade_date, price_basis, data_as_of, input_payload, input_hash,
        planning_result, rule_version, state_version_before, state_version_after,
        final_target_shares, recorded_at)
        SELECT gen_random_uuid(), id, 1, NULL, 'MIGRATED', lifecycle_id,
               trade_date, price_basis, data_as_of, input_payload, input_hash,
               planning_result, rule_version, state_version_before, state_version_after,
               final_target_shares, clock_timestamp()
        FROM position_daily_facts""")
    op.execute("""CREATE FUNCTION append_position_daily_fact_revision() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE previous_id uuid;
        DECLARE previous_no integer;
        DECLARE origin text;
        BEGIN
            IF TG_OP = 'UPDATE' THEN
                IF NEW.id IS DISTINCT FROM OLD.id OR
                   NEW.lifecycle_id IS DISTINCT FROM OLD.lifecycle_id OR
                   NEW.trade_date IS DISTINCT FROM OLD.trade_date THEN
                    RAISE EXCEPTION 'daily fact identity is immutable';
                END IF;
                IF NEW.price_basis IS NOT DISTINCT FROM OLD.price_basis AND
                   NEW.data_as_of IS NOT DISTINCT FROM OLD.data_as_of AND
                   NEW.input_payload IS NOT DISTINCT FROM OLD.input_payload AND
                   NEW.input_hash IS NOT DISTINCT FROM OLD.input_hash AND
                   NEW.planning_result IS NOT DISTINCT FROM OLD.planning_result AND
                   NEW.rule_version IS NOT DISTINCT FROM OLD.rule_version AND
                   NEW.state_version_before IS NOT DISTINCT FROM OLD.state_version_before AND
                   NEW.state_version_after IS NOT DISTINCT FROM OLD.state_version_after AND
                   NEW.final_target_shares IS NOT DISTINCT FROM OLD.final_target_shares THEN
                    RETURN NEW;
                END IF;
            END IF;
            SELECT id, revision_no, baseline_origin INTO previous_id, previous_no, origin
              FROM position_daily_fact_revisions
              WHERE daily_fact_id = NEW.id ORDER BY revision_no DESC LIMIT 1;
            IF TG_OP = 'UPDATE' AND previous_id IS NULL THEN
                RAISE EXCEPTION 'daily fact revision baseline is missing';
            END IF;
            INSERT INTO position_daily_fact_revisions (
                id, daily_fact_id, revision_no, previous_revision_id, baseline_origin, lifecycle_id,
                trade_date, price_basis, data_as_of, input_payload, input_hash,
                planning_result, rule_version, state_version_before, state_version_after,
                final_target_shares, recorded_at)
            VALUES (gen_random_uuid(), NEW.id, COALESCE(previous_no, 0) + 1,
                previous_id, COALESCE(origin, 'LIVE'), NEW.lifecycle_id, NEW.trade_date, NEW.price_basis,
                NEW.data_as_of, NEW.input_payload, NEW.input_hash,
                NEW.planning_result, NEW.rule_version, NEW.state_version_before,
                NEW.state_version_after, NEW.final_target_shares, clock_timestamp());
            RETURN NEW;
        END; $$""")
    op.execute("""CREATE TRIGGER daily_fact_revision_append
        AFTER INSERT OR UPDATE ON position_daily_facts
        FOR EACH ROW EXECUTE FUNCTION append_position_daily_fact_revision()""")
    op.execute("""CREATE FUNCTION validate_daily_fact_revision_insert() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE parent position_daily_facts%ROWTYPE;
        DECLARE previous_id uuid;
        DECLARE previous_no integer;
        DECLARE previous_origin text;
        BEGIN
            -- A revision can only be created by the parent row's trigger.
            -- Direct INSERT starts at trigger depth 1, including ORM add().
            IF pg_trigger_depth() <> 2 THEN
                RAISE EXCEPTION 'daily fact revisions require parent-row mutation';
            END IF;
            SELECT * INTO parent FROM position_daily_facts WHERE id = NEW.daily_fact_id;
            IF parent.id IS NULL OR
               NEW.lifecycle_id IS DISTINCT FROM parent.lifecycle_id OR
               NEW.trade_date IS DISTINCT FROM parent.trade_date OR
               NEW.price_basis IS DISTINCT FROM parent.price_basis OR
               NEW.data_as_of IS DISTINCT FROM parent.data_as_of OR
               NEW.input_payload IS DISTINCT FROM parent.input_payload OR
               NEW.input_hash IS DISTINCT FROM parent.input_hash OR
               NEW.planning_result IS DISTINCT FROM parent.planning_result OR
               NEW.rule_version IS DISTINCT FROM parent.rule_version OR
               NEW.state_version_before IS DISTINCT FROM parent.state_version_before OR
               NEW.state_version_after IS DISTINCT FROM parent.state_version_after OR
               NEW.final_target_shares IS DISTINCT FROM parent.final_target_shares THEN
                RAISE EXCEPTION 'daily fact revision differs from current parent';
            END IF;
            SELECT id, revision_no, baseline_origin
              INTO previous_id, previous_no, previous_origin
              FROM position_daily_fact_revisions
              WHERE daily_fact_id = NEW.daily_fact_id ORDER BY revision_no DESC LIMIT 1;
            IF NEW.revision_no <> COALESCE(previous_no, 0) + 1 OR
               NEW.previous_revision_id IS DISTINCT FROM previous_id OR
               NEW.baseline_origin IS DISTINCT FROM COALESCE(previous_origin, 'LIVE') THEN
                RAISE EXCEPTION 'daily fact revision predecessor is invalid';
            END IF;
            RETURN NEW;
        END; $$""")
    op.execute("""CREATE TRIGGER daily_fact_revision_validate_insert
        BEFORE INSERT ON position_daily_fact_revisions
        FOR EACH ROW EXECUTE FUNCTION validate_daily_fact_revision_insert()""")
    op.execute("""CREATE FUNCTION reject_daily_fact_revision_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'daily fact revision history is immutable';
        END; $$""")
    op.execute("""CREATE TRIGGER daily_fact_revision_immutable
        BEFORE UPDATE OR DELETE ON position_daily_fact_revisions
        FOR EACH ROW EXECUTE FUNCTION reject_daily_fact_revision_mutation()""")
    op.execute("""CREATE TRIGGER daily_fact_revision_no_truncate
        BEFORE TRUNCATE ON position_daily_fact_revisions
        FOR EACH STATEMENT EXECUTE FUNCTION reject_daily_fact_revision_mutation()""")


def downgrade() -> None:
    # The export is a verified JSONL snapshot of every revision before DDL
    # removes this table. The operator chooses an absolute, nonexistent path.
    # Freeze the current rows before reading the history. Otherwise a writer
    # could append a revision after the SELECT and before DROP TRIGGER.
    op.execute("LOCK TABLE position_daily_facts IN ACCESS EXCLUSIVE MODE")
    op.execute("LOCK TABLE position_daily_fact_revisions IN ACCESS EXCLUSIVE MODE")
    rows = list(op.get_bind().execute(text(
        "SELECT row_to_json(r)::text FROM position_daily_fact_revisions r "
        "ORDER BY daily_fact_id, revision_no"
    )).scalars())
    if rows:
        raw_path = os.environ.get("LIVEPROFIT_DAILY_FACT_REVISION_EXPORT_PATH", "")
        export_path = Path(raw_path)
        if not raw_path or not export_path.is_absolute():
            raise RuntimeError("absolute daily fact revision export path required for downgrade")
        body = ("\n".join(rows) + "\n").encode("utf-8")
        header = json.dumps({
            "format": "liveprofit_daily_fact_revisions_v1",
            "rows": len(rows), "sha256": hashlib.sha256(body).hexdigest(),
        }, sort_keys=True).encode("utf-8") + b"\n"
        payload = header + body
        with export_path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if export_path.read_bytes() != payload:
            raise RuntimeError("daily fact revision export readback mismatch")
    op.execute("DROP TRIGGER daily_fact_revision_no_truncate ON position_daily_fact_revisions")
    op.execute("DROP TRIGGER daily_fact_revision_immutable ON position_daily_fact_revisions")
    op.execute("DROP TRIGGER daily_fact_revision_validate_insert ON position_daily_fact_revisions")
    op.execute("DROP FUNCTION validate_daily_fact_revision_insert()")
    op.execute("DROP FUNCTION reject_daily_fact_revision_mutation()")
    op.execute("DROP TRIGGER daily_fact_revision_append ON position_daily_facts")
    op.execute("DROP FUNCTION append_position_daily_fact_revision()")
    op.drop_table("position_daily_fact_revisions")
