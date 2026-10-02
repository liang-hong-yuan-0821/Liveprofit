"""Freeze future fill-caused lifecycle version steps in the same transaction.

The transaction-local fill ID is a causal link inside this database, not an
authentication or service authorization token; direct SQL can set it too.
"""

import hashlib
import json
import os
from pathlib import Path

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import UUID


revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("LOCK TABLE position_lifecycle_states IN SHARE ROW EXCLUSIVE MODE")
    op.execute("LOCK TABLE order_fill_events IN SHARE ROW EXCLUSIVE MODE")
    op.create_table(
        "fill_lifecycle_version_steps",
        sa.Column("fill_event_id", UUID(as_uuid=True), sa.ForeignKey(
            "order_fill_events.id", ondelete="RESTRICT", deferrable=True,
            initially="DEFERRED"), primary_key=True),
        sa.Column("lifecycle_id", UUID(as_uuid=True), sa.ForeignKey(
            "position_lifecycle_states.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("version_before", sa.Integer(), nullable=True),
        sa.Column("version_after", sa.Integer(), nullable=True),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.CheckConstraint(
            "(origin = 'LOCAL_CAUSAL' AND version_before >= 1 "
            "AND version_after = version_before + 1) OR "
            "(origin = 'UNATTRIBUTED' AND version_before IS NULL "
            "AND version_after IS NULL)",
            name="ck_fill_lifecycle_version_step_shape"),
    )
    op.execute("""CREATE FUNCTION capture_fill_lifecycle_version_advance() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE fill_id_text text;
        BEGIN
            fill_id_text := NULLIF(current_setting('liveprofit.fill_event_id', true), '');
            IF fill_id_text IS NULL THEN
                RETURN NEW;
            END IF;
            IF NEW.state_version != OLD.state_version + 1 THEN
                RAISE EXCEPTION 'fill lifecycle version must advance exactly once';
            END IF;
            INSERT INTO fill_lifecycle_version_steps
                (fill_event_id, lifecycle_id, version_before, version_after, origin)
            VALUES (fill_id_text::uuid, NEW.id,
                    OLD.state_version, NEW.state_version, 'LOCAL_CAUSAL');
            RETURN NEW;
        END; $$""")
    op.execute("""CREATE TRIGGER fill_lifecycle_version_advance_capture
        AFTER UPDATE OF state_version ON position_lifecycle_states FOR EACH ROW
        WHEN (NEW.state_version IS DISTINCT FROM OLD.state_version)
        EXECUTE FUNCTION capture_fill_lifecycle_version_advance()""")
    op.execute("""CREATE FUNCTION capture_fill_lifecycle_version_step() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE captured_lifecycle uuid;
        BEGIN
            IF NEW.binding_origin != 'LIVE' OR NEW.lifecycle_id_at_fill IS NULL THEN
                RETURN NEW;
            END IF;
            SELECT lifecycle_id INTO captured_lifecycle
              FROM fill_lifecycle_version_steps WHERE fill_event_id = NEW.id;
            IF FOUND THEN
                IF captured_lifecycle != NEW.lifecycle_id_at_fill THEN
                    RAISE EXCEPTION 'fill lifecycle version identity mismatch';
                END IF;
                RETURN NEW;
            END IF;
            INSERT INTO fill_lifecycle_version_steps
                (fill_event_id, lifecycle_id, version_before, version_after, origin)
            VALUES (NEW.id, NEW.lifecycle_id_at_fill, NULL, NULL, 'UNATTRIBUTED');
            RETURN NEW;
        END; $$""")
    op.execute("""CREATE TRIGGER fill_lifecycle_version_step_capture
        AFTER INSERT ON order_fill_events FOR EACH ROW
        EXECUTE FUNCTION capture_fill_lifecycle_version_step()""")
    op.execute("""CREATE FUNCTION reject_fill_lifecycle_version_step_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'fill lifecycle version steps are immutable';
        END; $$""")
    op.execute("""CREATE TRIGGER fill_lifecycle_version_step_immutable
        BEFORE UPDATE OR DELETE ON fill_lifecycle_version_steps FOR EACH ROW
        EXECUTE FUNCTION reject_fill_lifecycle_version_step_mutation()""")


def downgrade() -> None:
    op.execute("LOCK TABLE position_lifecycle_states IN ACCESS EXCLUSIVE MODE")
    op.execute("LOCK TABLE order_fill_events IN ACCESS EXCLUSIVE MODE")
    op.execute("LOCK TABLE fill_lifecycle_version_steps IN ACCESS EXCLUSIVE MODE")
    rows = list(op.get_bind().execute(text(
        "SELECT row_to_json(e)::text FROM fill_lifecycle_version_steps e "
        "ORDER BY fill_event_id"
    )).scalars())
    if rows:
        raw_path = os.environ.get("LIVEPROFIT_FILL_VERSION_STEPS_EXPORT_PATH", "")
        export_path = Path(raw_path)
        if not raw_path or not export_path.is_absolute():
            raise RuntimeError("absolute fill version step export path required for downgrade")
        body = ("\n".join(rows) + "\n").encode("utf-8")
        header = json.dumps({
            "format": "liveprofit_fill_lifecycle_version_steps_v1",
            "rows": len(rows), "sha256": hashlib.sha256(body).hexdigest(),
        }, sort_keys=True).encode("utf-8") + b"\n"
        payload = header + body
        with export_path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if export_path.read_bytes() != payload:
            raise RuntimeError("fill version step export readback mismatch")
    op.execute("DROP TRIGGER fill_lifecycle_version_step_immutable ON fill_lifecycle_version_steps")
    op.execute("DROP FUNCTION reject_fill_lifecycle_version_step_mutation()")
    op.execute("DROP TRIGGER fill_lifecycle_version_step_capture ON order_fill_events")
    op.execute("DROP FUNCTION capture_fill_lifecycle_version_step()")
    op.execute("DROP TRIGGER fill_lifecycle_version_advance_capture ON position_lifecycle_states")
    op.execute("DROP FUNCTION capture_fill_lifecycle_version_advance()")
    op.drop_table("fill_lifecycle_version_steps")
