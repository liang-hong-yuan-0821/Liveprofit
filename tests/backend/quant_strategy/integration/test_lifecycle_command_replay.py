# test-catalog-begin
# {
#   "purpose": "量化策略 / lifecycle_command_replay（持仓生命周期、命令、重放）：Read-only replay validation with synthetic facts in random isolated databases.",
#   "keywords": [
#     "量化策略",
#     "持仓生命周期",
#     "投资组合",
#     "重放",
#     "收益",
#     "lifecycle_command_replay",
#     "lifecycle",
#     "portfolio",
#     "replay",
#     "returns"
#   ],
#   "covers": [
#     "backend/modules/quant_strategy/application/errors.py",
#     "backend/modules/quant_strategy/application/lifecycle_command_selection.py",
#     "backend/modules/quant_strategy/infrastructure/lifecycle_command_replay.py",
#     "backend/modules/quant_strategy/infrastructure/lifecycle_history_repository.py",
#     "backend/modules/quant_strategy/infrastructure/lifecycle_operation_models.py"
#   ],
#   "environment": [
#     "db"
#   ]
# }
# test-catalog-end

"""Read-only replay validation with synthetic facts in random isolated databases.

Only the fixture (DB owner) bypasses 0051's closed INSERT triggers to construct
future/corrupt histories. This does not exercise or certify a LIVE writer.
"""
import hashlib
import json
import re
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from alembic import command
from sqlalchemy import event, insert, select, text
from sqlalchemy.orm import sessionmaker

from backend.modules.quant_strategy.application.errors import LifecycleInvalidStateError
from backend.modules.quant_strategy.application.lifecycle_command_selection import (
    OrderStatusCommandRequest,
)
from backend.modules.quant_strategy.infrastructure.lifecycle_command_replay import (
    LifecycleCommandReplayConflictError,
    _change_payload,
    _verify_unbound_replay,
    load_order_status_command_replay,
)
from backend.modules.quant_strategy.infrastructure.lifecycle_history_repository import (
    LifecycleHistoryIntegrityError,
    _hash,
    _normalize,
)
from backend.modules.quant_strategy.infrastructure.lifecycle_operation_models import (
    LifecycleBusinessCommand,
    QuantExecutionOperationChange,
    QuantExecutionOperationSource,
)
import tests.backend.quant_strategy.support.lifecycle_operation_schema as schema_tests

operation_db = schema_tests.operation_db


def _image(raw, status, revision):
    raw = re.sub(r'"status":"[^"]+"', f'"status":"{status}"', raw)
    raw = re.sub(r'"revision":\d+', f'"revision":{revision}', raw)
    view = _normalize(json.loads(raw, parse_float=Decimal))
    return dict(view, __raw_row_json__=raw)


def _fixture_history(engine, portfolio, order, outcome="APPLIED", statuses=None):
    # Caller has checked current_database via operation_db. Never enable the
    # bypass outside this synthetic test fixture or run it against a main DB.
    request = OrderStatusCommandRequest(order, "CANCELLED", 1, "replay-1")
    with engine.begin() as connection:
        assert connection.scalar(text("SELECT current_database()")).startswith("liveprofit_lc_history_0051_test_")
        raw = connection.scalar(text("SELECT row_to_json(o)::text FROM suggested_orders o WHERE id=:id"), {"id": order})
        previous = connection.execute(select(LifecycleBusinessCommand.__table__).where(
            LifecycleBusinessCommand.portfolio_id == portfolio)).mappings().one()
        cmd_id = uuid.uuid4()
        before = _image(raw, "CANCELLED" if outcome == "NO_STATE_CHANGE" else "PROPOSED", 1)
        cursor = before
        changes = []
        for seq, status in enumerate(statuses if statuses is not None else (["CANCELLED"] if outcome == "APPLIED" else []), 1):
            after = _image(raw, status, seq + 1)
            changes.append({'id': uuid.uuid4(), 'business_command_id': cmd_id, 'operation_id': None,
                                'change_seq': seq, 'scope': "COMMAND", 'entity_type': "ORDER", 'row_id': order,
                                'change_kind': "UPDATE", 'binding_side': "NONE", 'before_row': cursor,
                                'after_row': after, 'captured_at': datetime(2026, 10, 1, 1, 0, seq, tzinfo=timezone.utc)})
            cursor = after
        result = {'schema_version': 1, 'outcome': outcome, 'reason_code': 'TEST_RESULT',
                  'before_row': before, 'after_row': cursor,
                      'missing_sources': ["BROKER_TERMINAL_RECEIPT"] if outcome == "BLOCKED" else [],
                      'change_count': len(changes), 'changes_sha256': _hash(_change_payload(changes))}
        manifest = {"steps": [], "unbound_orders": [{"order_id": str(order), "original_revision": 1,
                                                    "allowed_results": ["APPLIED", "NO_STATE_CHANGE", "BLOCKED"]}]}
        head = {'id': cmd_id, 'portfolio_id': portfolio, 'request_key': request.request_key, 'command_seq': 2,
                    'previous_command_id': previous["id"], 'previous_manifest_hash': previous["manifest_hash"],
                    'command_kind': "ORDER_STATUS_CHANGED", 'request_schema_version': 1,
                    'canonical_request': request.canonical_request(), 'request_hash': hashlib.sha256(request.canonical_request()).hexdigest(),
                    'selector_version': "order-state:v1", 'expected_steps': [], 'expected_unbound_orders': manifest["unbound_orders"],
                    'manifest_hash': _hash(manifest), 'expected_step_count': 0, 'expected_unbound_order_count': 1,
                    'actor_type': "USER", 'actor_ref': "synthetic-test-only"}
        sources_table = QuantExecutionOperationSource.__table__
        source = {column.name: None for column in sources_table.columns}
        source.update(id=uuid.uuid4(), business_command_id=cmd_id, source_role="ORDER_STATUS_RESULT", source_ordinal=1,
                      scope="COMMAND", source_type="UNBOUND_ORDER_RESULT", order_id=order,
                      source_revision=cursor["revision"], source_snapshot=result, source_content_hash=_hash(result),
                      result_outcome=outcome, result_reason_code="TEST_RESULT", result_schema_version=1)
        for table, rows in ((LifecycleBusinessCommand.__table__, [head]), (sources_table, [source]),
                            (QuantExecutionOperationChange.__table__, changes)):
            connection.execute(text(f"ALTER TABLE {table.name} DISABLE TRIGGER lc_history_capture_closed"))
            if rows:
                connection.execute(insert(table), rows)
            connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            connection.execute(text(f"ALTER TABLE {table.name} ENABLE TRIGGER lc_history_capture_closed"))
    return request, head, source, changes


def _seed_history(operation_db, outcome="APPLIED", statuses=None):
    config, engine = operation_db
    portfolio, _, orders = schema_tests._seed(engine)
    with engine.begin() as connection:
        connection.execute(text("UPDATE suggested_orders SET lifecycle_id=NULL WHERE id=:id"), {"id": orders[0]})
    command.upgrade(config, "0051")
    request, head, source, changes = _fixture_history(engine, portfolio, orders[0], outcome, statuses)
    return engine, portfolio, orders[0], request, head, source, changes


@pytest.mark.parametrize("outcome", ["APPLIED", "NO_STATE_CHANGE", "BLOCKED"])
def test_replay_returns_verified_content_without_mutable_reads_or_authority(operation_db, outcome):
    engine, portfolio, order, request, *_ = _seed_history(operation_db, outcome)
    # Later business-row changes do not redefine the original request/result.
    with engine.begin() as connection:
        connection.execute(text("UPDATE suggested_orders SET revision=9 WHERE id=:id"), {"id": order})
    statements = []
    def record(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)
    event.listen(engine, "before_cursor_execute", record)
    try:
        with sessionmaker(bind=engine)() as session:
            replay = load_order_status_command_replay(session, portfolio, request)
            assert replay.outcome == outcome and replay.original_revision == 1
            assert replay.change_count == (1 if outcome == "APPLIED" else 0)
            assert not replay.execution_authorized and not replay.continuous_history_known
            assert not replay.database_sealing_verified
            assert session.scalar(text("SELECT pg_current_xact_id_if_assigned()::text")) is None
        assert not any("FROM suggested_orders" in sql or "FOR UPDATE" in sql or sql.startswith("INSERT") for sql in statements)
    finally:
        event.remove(engine, "before_cursor_execute", record)


def test_replay_checks_original_request_and_portfolio_before_scope(operation_db):
    engine, portfolio, order, request, *_ = _seed_history(operation_db)
    with sessionmaker(bind=engine)() as session:
        assert load_order_status_command_replay(session, uuid.uuid4(), request) is None
        assert load_order_status_command_replay(session, portfolio, OrderStatusCommandRequest(order, "CANCELLED", 1, "absent")) is None
        for altered in (OrderStatusCommandRequest(order, "REJECTED", 1, request.request_key),
                        OrderStatusCommandRequest(order, "CANCELLED", 2, request.request_key),
                        OrderStatusCommandRequest(uuid.uuid4(), "CANCELLED", 1, request.request_key)):
            with pytest.raises(LifecycleCommandReplayConflictError):
                load_order_status_command_replay(session, portfolio, altered)


def test_replay_retains_every_a_b_a_change(operation_db):
    engine, portfolio, _, request, head, source, changes = _seed_history(
        operation_db, statuses=["EXECUTING", "PROPOSED", "CANCELLED"])
    assert _verify_unbound_replay(head, request, [source], changes).change_count == 3
    with sessionmaker(bind=engine)() as session:
        assert load_order_status_command_replay(session, portfolio, request).change_count == 3
    # Re-hashing the shortened set cannot hide the physical chain discontinuity.
    shortened = [changes[0], dict(changes[2], change_seq=2)]
    forged = deepcopy(source)
    forged["source_snapshot"]["change_count"] = 2
    forged["source_snapshot"]["changes_sha256"] = _hash(_change_payload(shortened))
    forged["source_content_hash"] = _hash(forged["source_snapshot"])
    with pytest.raises(LifecycleHistoryIntegrityError, match="discontinuous"):
        _verify_unbound_replay(head, request, [forged], shortened)


def test_replay_refuses_writes_and_locks_in_the_reader_transaction(operation_db):
    engine, portfolio, order, request, *_ = _seed_history(operation_db)
    with sessionmaker(bind=engine)() as session:
        session.execute(text("SELECT id FROM suggested_orders WHERE id=:id FOR UPDATE"), {"id": order})
        with pytest.raises(LifecycleInvalidStateError):
            load_order_status_command_replay(session, portfolio, request)


@pytest.mark.parametrize("corruption", ["request_hash", "manifest", "missing_result", "extra_result", "source_hash",
    "revision", "changes_hash", "change_scope", "change_binding", "row_original", "final_row", "unchanged", "effect",
    "result_metadata", "revision_jump", "unrelated_field", "truncated_row", "integer_to_boolean"])
def test_replay_rejects_corrupt_or_forged_content(operation_db, corruption):
    _, _, _, request, head, source, changes = _seed_history(operation_db)
    head, source, changes = deepcopy((head, source, changes))
    sources = [source]
    if corruption == "request_hash":
        head["request_hash"] = "0" * 64
    elif corruption == "manifest":
        head["expected_unbound_orders"] = []
        head["expected_unbound_order_count"] = 0
        head["manifest_hash"] = _hash({"steps": [], "unbound_orders": []})
    elif corruption == "missing_result":
        sources = []
    elif corruption == "extra_result":
        sources.append(deepcopy(source))
    elif corruption == "source_hash":
        source["source_content_hash"] = "0" * 64
    elif corruption == "revision":
        source["source_revision"] += 1
    elif corruption == "changes_hash":
        source["source_snapshot"]["changes_sha256"] = "0" * 64
    elif corruption == "change_scope":
        changes[0]["operation_id"] = uuid.uuid4()
    elif corruption == "change_binding":
        changes[0]["before_row"]["lifecycle_id"] = str(uuid.uuid4())
    elif corruption == "row_original":
        source["source_snapshot"]["before_row"]["quantity"] = "999"
    elif corruption == "final_row":
        source["source_snapshot"]["after_row"] = source["source_snapshot"]["before_row"]
    elif corruption == "unchanged":
        source["result_outcome"] = "NO_STATE_CHANGE"
        source["source_snapshot"]["outcome"] = "NO_STATE_CHANGE"
    elif corruption == "effect":
        source["source_snapshot"]["missing_sources"] = ["BROKER_TERMINAL_RECEIPT"]
    elif corruption == "result_metadata":
        source["result_outcome"] = "BLOCKED"
    elif corruption in {"revision_jump", "unrelated_field"}:
        new = changes[0]["after_row"]
        if corruption == "revision_jump":
            new = _image(new["__raw_row_json__"], "CANCELLED", 3)
        else:
            raw = re.sub(r'"quantity":[^,}]+', '"quantity":999', new["__raw_row_json__"])
            new = _image(raw, "CANCELLED", 2)
        changes[0]["after_row"] = new
        source["source_snapshot"]["after_row"] = new
        source["source_revision"] = new["revision"]
    elif corruption == "truncated_row":
        def truncate(image):
            small = {key: image[key] for key in ("id", "portfolio_id", "lifecycle_id", "revision", "status")}
            return dict(small, __raw_row_json__=json.dumps(small))
        changes[0]["before_row"] = truncate(changes[0]["before_row"])
        changes[0]["after_row"] = truncate(changes[0]["after_row"])
        source["source_snapshot"]["before_row"] = changes[0]["before_row"]
        source["source_snapshot"]["after_row"] = changes[0]["after_row"]
    elif corruption == "integer_to_boolean":
        old_raw = re.sub(r'"source_signal_id":null', '"source_signal_id":1', changes[0]["before_row"]["__raw_row_json__"])
        new_raw = re.sub(r'"source_signal_id":null', '"source_signal_id":true', changes[0]["after_row"]["__raw_row_json__"])
        changes[0]["before_row"] = _image(old_raw, "PROPOSED", 1)
        changes[0]["after_row"] = _image(new_raw, "CANCELLED", 2)
        source["source_snapshot"]["before_row"] = changes[0]["before_row"]
        source["source_snapshot"]["after_row"] = changes[0]["after_row"]
    # Most counterexamples include an internally recomputed digest. A hash alone
    # cannot authenticate participant identity, effect, shape or row continuity.
    if corruption != "source_hash":
        source["source_content_hash"] = _hash(source["source_snapshot"])
    if corruption in {"change_scope", "change_binding", "revision_jump", "unrelated_field", "truncated_row", "integer_to_boolean"}:
        source["source_snapshot"]["changes_sha256"] = _hash(_change_payload(changes))
        source["source_content_hash"] = _hash(source["source_snapshot"])
    with pytest.raises(LifecycleHistoryIntegrityError):
        _verify_unbound_replay(head, request, sources, changes)
