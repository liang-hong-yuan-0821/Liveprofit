"""Freeze the local order-to-intent identity at each future fill insertion."""

import hashlib
import json
import os
from pathlib import Path

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import UUID


revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Keep old writes from falling between the legacy baseline and trigger.
    op.execute("LOCK TABLE order_fill_events IN SHARE ROW EXCLUSIVE MODE")
    op.add_column("order_fill_events", sa.Column(
        "lifecycle_id_at_fill", UUID(as_uuid=True), nullable=True))
    op.add_column("order_fill_events", sa.Column(
        "intent_id_at_fill", UUID(as_uuid=True), nullable=True))
    op.add_column("order_fill_events", sa.Column(
        "binding_origin", sa.String(16), nullable=False,
        server_default=sa.text("'MIGRATED'")))
    op.create_check_constraint(
        "ck_order_fill_binding_origin", "order_fill_events",
        "binding_origin IN ('MIGRATED','LIVE')")
    op.execute("""CREATE FUNCTION capture_order_fill_intent_binding() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE bound_lifecycle uuid;
        DECLARE bound_intent uuid;
        BEGIN
            SELECT lifecycle_id, intent_id INTO bound_lifecycle, bound_intent
              FROM suggested_orders WHERE id = NEW.order_id FOR SHARE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'fill order binding is missing';
            END IF;
            NEW.lifecycle_id_at_fill := bound_lifecycle;
            NEW.intent_id_at_fill := bound_intent;
            NEW.binding_origin := 'LIVE';
            RETURN NEW;
        END; $$""")
    op.execute("""CREATE TRIGGER order_fill_intent_binding_capture
        BEFORE INSERT ON order_fill_events
        FOR EACH ROW EXECUTE FUNCTION capture_order_fill_intent_binding()""")
    op.execute("""CREATE FUNCTION reject_order_fill_event_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'order fill events are immutable';
        END; $$""")
    op.execute("""CREATE TRIGGER order_fill_event_immutable
        BEFORE UPDATE OR DELETE ON order_fill_events
        FOR EACH ROW EXECUTE FUNCTION reject_order_fill_event_mutation()""")
    op.alter_column("order_fill_events", "binding_origin",
                    server_default=sa.text("'LIVE'"))


def downgrade() -> None:
    op.execute("LOCK TABLE order_fill_events IN ACCESS EXCLUSIVE MODE")
    rows = list(op.get_bind().execute(text(
        "SELECT row_to_json(e)::text FROM order_fill_events e ORDER BY id"
    )).scalars())
    if rows:
        raw_path = os.environ.get("LIVEPROFIT_FILL_INTENT_BINDING_EXPORT_PATH", "")
        export_path = Path(raw_path)
        if not raw_path or not export_path.is_absolute():
            raise RuntimeError("absolute fill intent binding export path required for downgrade")
        body = ("\n".join(rows) + "\n").encode("utf-8")
        header = json.dumps({
            "format": "liveprofit_fill_intent_bindings_v1",
            "rows": len(rows), "sha256": hashlib.sha256(body).hexdigest(),
        }, sort_keys=True).encode("utf-8") + b"\n"
        payload = header + body
        with export_path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if export_path.read_bytes() != payload:
            raise RuntimeError("fill binding export readback mismatch")
    op.execute("DROP TRIGGER order_fill_event_immutable ON order_fill_events")
    op.execute("DROP FUNCTION reject_order_fill_event_mutation()")
    op.execute("DROP TRIGGER order_fill_intent_binding_capture ON order_fill_events")
    op.execute("DROP FUNCTION capture_order_fill_intent_binding()")
    op.drop_constraint("ck_order_fill_binding_origin", "order_fill_events", type_="check")
    op.drop_column("order_fill_events", "binding_origin")
    op.drop_column("order_fill_events", "intent_id_at_fill")
    op.drop_column("order_fill_events", "lifecycle_id_at_fill")
