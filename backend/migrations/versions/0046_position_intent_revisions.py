"""Keep immutable local write history for lifecycle intent definitions and status."""

import hashlib
import json
import os
from pathlib import Path

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import UUID


revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A writer between the baseline SELECT and trigger installation would have
    # no revision. Hold this lock through the transactional migration.
    op.execute("LOCK TABLE position_intents IN SHARE ROW EXCLUSIVE MODE")
    op.create_table(
        "position_intent_revisions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("intent_id", UUID(as_uuid=True),
                  sa.ForeignKey("position_intents.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("previous_revision_id", UUID(as_uuid=True),
                  sa.ForeignKey("position_intent_revisions.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("baseline_origin", sa.String(16), nullable=False),
        sa.Column("lifecycle_id", UUID(as_uuid=True), nullable=False),
        sa.Column("source_signal_id", sa.BigInteger(), nullable=True),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("target_shares", sa.Numeric(20, 4), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("intent_revision", sa.Integer(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("intent_id", "revision_no", name="uq_position_intent_revision_no"),
        sa.UniqueConstraint("previous_revision_id", name="uq_position_intent_revision_previous"),
        sa.CheckConstraint("revision_no > 0", name="ck_position_intent_revision_positive"),
        sa.CheckConstraint("baseline_origin IN ('MIGRATED','LIVE')",
                           name="ck_position_intent_revision_origin"),
        sa.CheckConstraint("(revision_no = 1 AND previous_revision_id IS NULL) OR "
                           "(revision_no > 1 AND previous_revision_id IS NOT NULL)",
                           name="ck_position_intent_revision_link"),
    )
    op.execute("""INSERT INTO position_intent_revisions (
        id, intent_id, revision_no, previous_revision_id, baseline_origin,
        lifecycle_id, source_signal_id, trade_date, target_shares, reason_code,
        state_version, status, intent_revision, recorded_at)
        SELECT gen_random_uuid(), id, 1, NULL, 'MIGRATED', lifecycle_id,
               source_signal_id, trade_date, target_shares, reason_code,
               state_version, status, revision, clock_timestamp()
        FROM position_intents""")
    op.execute("""CREATE FUNCTION append_position_intent_revision() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE previous_id uuid;
        DECLARE previous_no integer;
        DECLARE origin text;
        BEGIN
            IF TG_OP = 'UPDATE' THEN
                IF NEW.id IS DISTINCT FROM OLD.id OR
                   NEW.lifecycle_id IS DISTINCT FROM OLD.lifecycle_id THEN
                    RAISE EXCEPTION 'position intent identity is immutable';
                END IF;
                IF NEW.source_signal_id IS NOT DISTINCT FROM OLD.source_signal_id AND
                   NEW.trade_date IS NOT DISTINCT FROM OLD.trade_date AND
                   NEW.target_shares IS NOT DISTINCT FROM OLD.target_shares AND
                   NEW.reason_code IS NOT DISTINCT FROM OLD.reason_code AND
                   NEW.state_version IS NOT DISTINCT FROM OLD.state_version AND
                   NEW.status IS NOT DISTINCT FROM OLD.status AND
                   NEW.revision IS NOT DISTINCT FROM OLD.revision THEN
                    RETURN NEW;
                END IF;
            END IF;
            SELECT id, revision_no, baseline_origin INTO previous_id, previous_no, origin
              FROM position_intent_revisions
              WHERE intent_id = NEW.id ORDER BY revision_no DESC LIMIT 1;
            IF TG_OP = 'UPDATE' AND previous_id IS NULL THEN
                RAISE EXCEPTION 'position intent revision baseline is missing';
            END IF;
            INSERT INTO position_intent_revisions (
                id, intent_id, revision_no, previous_revision_id, baseline_origin,
                lifecycle_id, source_signal_id, trade_date, target_shares, reason_code,
                state_version, status, intent_revision, recorded_at)
            VALUES (gen_random_uuid(), NEW.id, COALESCE(previous_no, 0) + 1,
                previous_id, COALESCE(origin, 'LIVE'), NEW.lifecycle_id,
                NEW.source_signal_id, NEW.trade_date, NEW.target_shares, NEW.reason_code,
                NEW.state_version, NEW.status, NEW.revision, clock_timestamp());
            RETURN NEW;
        END; $$""")
    op.execute("""CREATE TRIGGER position_intent_revision_append
        AFTER INSERT OR UPDATE ON position_intents
        FOR EACH ROW EXECUTE FUNCTION append_position_intent_revision()""")
    op.execute("""CREATE FUNCTION validate_position_intent_revision_insert() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE parent position_intents%ROWTYPE;
        DECLARE previous_id uuid;
        DECLARE previous_no integer;
        DECLARE previous_origin text;
        BEGIN
            IF pg_trigger_depth() <> 2 THEN
                RAISE EXCEPTION 'position intent revisions require parent-row mutation';
            END IF;
            SELECT * INTO parent FROM position_intents WHERE id = NEW.intent_id;
            IF parent.id IS NULL OR
               NEW.lifecycle_id IS DISTINCT FROM parent.lifecycle_id OR
               NEW.source_signal_id IS DISTINCT FROM parent.source_signal_id OR
               NEW.trade_date IS DISTINCT FROM parent.trade_date OR
               NEW.target_shares IS DISTINCT FROM parent.target_shares OR
               NEW.reason_code IS DISTINCT FROM parent.reason_code OR
               NEW.state_version IS DISTINCT FROM parent.state_version OR
               NEW.status IS DISTINCT FROM parent.status OR
               NEW.intent_revision IS DISTINCT FROM parent.revision THEN
                RAISE EXCEPTION 'position intent revision differs from current parent';
            END IF;
            SELECT id, revision_no, baseline_origin
              INTO previous_id, previous_no, previous_origin
              FROM position_intent_revisions
              WHERE intent_id = NEW.intent_id ORDER BY revision_no DESC LIMIT 1;
            IF NEW.revision_no <> COALESCE(previous_no, 0) + 1 OR
               NEW.previous_revision_id IS DISTINCT FROM previous_id OR
               NEW.baseline_origin IS DISTINCT FROM COALESCE(previous_origin, 'LIVE') THEN
                RAISE EXCEPTION 'position intent revision predecessor is invalid';
            END IF;
            RETURN NEW;
        END; $$""")
    op.execute("""CREATE TRIGGER position_intent_revision_validate_insert
        BEFORE INSERT ON position_intent_revisions
        FOR EACH ROW EXECUTE FUNCTION validate_position_intent_revision_insert()""")
    op.execute("""CREATE FUNCTION reject_position_intent_revision_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'position intent revision history is immutable';
        END; $$""")
    op.execute("""CREATE TRIGGER position_intent_revision_immutable
        BEFORE UPDATE OR DELETE ON position_intent_revisions
        FOR EACH ROW EXECUTE FUNCTION reject_position_intent_revision_mutation()""")
    op.execute("""CREATE TRIGGER position_intent_revision_no_truncate
        BEFORE TRUNCATE ON position_intent_revisions
        FOR EACH STATEMENT EXECUTE FUNCTION reject_position_intent_revision_mutation()""")


def downgrade() -> None:
    op.execute("LOCK TABLE position_intents IN ACCESS EXCLUSIVE MODE")
    op.execute("LOCK TABLE position_intent_revisions IN ACCESS EXCLUSIVE MODE")
    rows = list(op.get_bind().execute(text(
        "SELECT row_to_json(r)::text FROM position_intent_revisions r "
        "ORDER BY intent_id, revision_no"
    )).scalars())
    if rows:
        raw_path = os.environ.get("LIVEPROFIT_INTENT_REVISION_EXPORT_PATH", "")
        export_path = Path(raw_path)
        if not raw_path or not export_path.is_absolute():
            raise RuntimeError("absolute intent revision export path required for downgrade")
        body = ("\n".join(rows) + "\n").encode("utf-8")
        header = json.dumps({
            "format": "liveprofit_position_intent_revisions_v1",
            "rows": len(rows), "sha256": hashlib.sha256(body).hexdigest(),
        }, sort_keys=True).encode("utf-8") + b"\n"
        payload = header + body
        with export_path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if export_path.read_bytes() != payload:
            raise RuntimeError("intent revision export readback mismatch")
    op.execute("DROP TRIGGER position_intent_revision_no_truncate ON position_intent_revisions")
    op.execute("DROP TRIGGER position_intent_revision_immutable ON position_intent_revisions")
    op.execute("DROP TRIGGER position_intent_revision_validate_insert ON position_intent_revisions")
    op.execute("DROP FUNCTION validate_position_intent_revision_insert()")
    op.execute("DROP FUNCTION reject_position_intent_revision_mutation()")
    op.execute("DROP TRIGGER position_intent_revision_append ON position_intents")
    op.execute("DROP FUNCTION append_position_intent_revision()")
    op.drop_table("position_intent_revisions")
