"""Preserve sourced daily-input correction proposals without switching current."""

import hashlib
import json
import os
from pathlib import Path

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "position_daily_fact_input_proposals",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("daily_fact_id", UUID(as_uuid=True),
                  sa.ForeignKey("position_daily_facts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("base_revision_id", UUID(as_uuid=True),
                  sa.ForeignKey("position_daily_fact_revisions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("request_key", sa.String(128), nullable=False),
        sa.Column("proposed_price_basis", sa.String(16), nullable=False),
        sa.Column("proposed_data_as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("proposed_input_payload", JSONB(), nullable=False),
        sa.Column("canonical_input_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("proposed_input_hash", sa.String(64), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.clock_timestamp()),
        sa.UniqueConstraint("daily_fact_id", "request_key", name="uq_daily_fact_input_proposal_request"),
        sa.UniqueConstraint("daily_fact_id", "base_revision_id", name="uq_daily_fact_input_proposal_base"),
        sa.CheckConstraint("length(trim(request_key)) > 0", name="ck_daily_fact_input_request"),
        sa.CheckConstraint("length(trim(reason_code)) > 0", name="ck_daily_fact_input_reason"),
        sa.CheckConstraint("length(trim(source_ref)) > 0", name="ck_daily_fact_input_source"),
        sa.CheckConstraint("length(source_ref) <= 2048", name="ck_daily_fact_input_source_size"),
        sa.CheckConstraint("source_sha256 ~ '^[0-9a-f]{64}$'", name="ck_daily_fact_input_source_hash"),
        sa.CheckConstraint("proposed_input_hash ~ '^[0-9a-f]{64}$'", name="ck_daily_fact_input_payload_hash"),
        sa.CheckConstraint("jsonb_typeof(proposed_input_payload) = 'object'",
                           name="ck_daily_fact_input_payload_object"),
        sa.CheckConstraint("octet_length(canonical_input_bytes) BETWEEN 2 AND 1048576",
                           name="ck_daily_fact_input_canonical_size"),
    )
    op.execute("""CREATE FUNCTION validate_daily_fact_input_proposal() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE parent position_daily_facts%ROWTYPE;
        DECLARE base position_daily_fact_revisions%ROWTYPE;
        DECLARE latest_id uuid;
        BEGIN
            SELECT * INTO parent FROM position_daily_facts
              WHERE id = NEW.daily_fact_id FOR UPDATE;
            SELECT * INTO base FROM position_daily_fact_revisions
              WHERE id = NEW.base_revision_id;
            SELECT id INTO latest_id FROM position_daily_fact_revisions
              WHERE daily_fact_id = NEW.daily_fact_id
              ORDER BY revision_no DESC LIMIT 1;
            IF parent.id IS NULL OR base.id IS NULL OR
               base.daily_fact_id <> parent.id OR latest_id <> base.id OR
               base.input_hash IS DISTINCT FROM parent.input_hash OR
               base.price_basis IS DISTINCT FROM parent.price_basis OR
               base.data_as_of IS DISTINCT FROM parent.data_as_of OR
               base.input_payload IS DISTINCT FROM parent.input_payload OR
               (NEW.proposed_input_payload IS NOT DISTINCT FROM parent.input_payload AND
                NEW.proposed_price_basis = parent.price_basis AND
                NEW.proposed_data_as_of = parent.data_as_of) THEN
                RAISE EXCEPTION 'daily input proposal base or change is invalid';
            END IF;
            IF convert_from(NEW.canonical_input_bytes, 'UTF8')::jsonb
                    IS DISTINCT FROM NEW.proposed_input_payload OR
               encode(sha256(NEW.canonical_input_bytes), 'hex')
                    IS DISTINCT FROM NEW.proposed_input_hash THEN
                RAISE EXCEPTION 'daily input proposal payload digest is invalid';
            END IF;
            RETURN NEW;
        END; $$""")
    op.execute("""CREATE TRIGGER daily_fact_input_proposal_validate
        BEFORE INSERT ON position_daily_fact_input_proposals
        FOR EACH ROW EXECUTE FUNCTION validate_daily_fact_input_proposal()""")
    op.execute("""CREATE FUNCTION reject_daily_fact_input_proposal_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'daily input correction proposals are immutable';
        END; $$""")
    op.execute("""CREATE TRIGGER daily_fact_input_proposal_immutable
        BEFORE UPDATE OR DELETE ON position_daily_fact_input_proposals
        FOR EACH ROW EXECUTE FUNCTION reject_daily_fact_input_proposal_mutation()""")
    op.execute("""CREATE TRIGGER daily_fact_input_proposal_no_truncate
        BEFORE TRUNCATE ON position_daily_fact_input_proposals
        FOR EACH STATEMENT EXECUTE FUNCTION reject_daily_fact_input_proposal_mutation()""")


def downgrade() -> None:
    op.execute("LOCK TABLE position_daily_fact_input_proposals IN ACCESS EXCLUSIVE MODE")
    op.execute("LOCK TABLE position_daily_facts IN ACCESS EXCLUSIVE MODE")
    rows = list(op.get_bind().execute(text(
        "SELECT row_to_json(p)::text FROM position_daily_fact_input_proposals p "
        "ORDER BY daily_fact_id, recorded_at, id"
    )).scalars())
    if rows:
        raw_path = os.environ.get("LIVEPROFIT_DAILY_FACT_INPUT_PROPOSAL_EXPORT_PATH", "")
        export_path = Path(raw_path)
        if not raw_path or not export_path.is_absolute():
            raise RuntimeError("absolute daily input proposal export path required for downgrade")
        body = ("\n".join(rows) + "\n").encode("utf-8")
        header = json.dumps({
            "format": "liveprofit_daily_fact_input_proposals_v1",
            "rows": len(rows), "sha256": hashlib.sha256(body).hexdigest(),
        }, sort_keys=True).encode("utf-8") + b"\n"
        payload = header + body
        with export_path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if export_path.read_bytes() != payload:
            raise RuntimeError("daily input proposal export readback mismatch")
    op.drop_table("position_daily_fact_input_proposals")
    op.execute("DROP FUNCTION validate_daily_fact_input_proposal()")
    op.execute("DROP FUNCTION reject_daily_fact_input_proposal_mutation()")
