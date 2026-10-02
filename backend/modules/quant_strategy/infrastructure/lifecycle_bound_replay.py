"""Content checks for 0053 fixed-member bound status operations.

This reader never certifies execution, complete prior history or DB sealing.
It uses immutable records only, including their predecessor, without projections.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.modules.quant_strategy.application.errors import LifecycleInvalidStateError
from backend.modules.quant_strategy.application.lifecycle_command_selection import (
    OrderStatusCommandRequest,
)
from backend.modules.quant_strategy.infrastructure.lifecycle_command_replay import (
    LifecycleCommandReplayConflictError,
    _change_payload,
    _hash_request,
)
from backend.modules.quant_strategy.infrastructure.lifecycle_history_repository import (
    LifecycleHistoryIntegrityError,
    _canonical,
    _hash,
    _require,
    _verify_row_image,
)
from backend.modules.quant_strategy.infrastructure.lifecycle_operation_models import (
    LifecycleBusinessCommand,
    PositionLifecycleInitialInput,
    PositionLifecycleOperation,
    QuantExecutionOperationChange,
    QuantExecutionOperationSource,
    SuggestedOrderInitialInput,
)

SELECTOR = "bound-order-state:v1"
IGNORED = {"created_at", "updated_at", "source_signal_id", "strategy_version_id",
           "lifecycle_policy_version_id", "__raw_row_json__"}
# PHYSICAL_COLUMNS is frozen below from the 0053 physical schema; never resolve
# historical field requirements from evolving ORM metadata at runtime.
PHYSICAL_COLUMNS = {
    'lifecycle': frozenset(('id', 'portfolio_id', 'position_id', 'market', 'symbol', 'strategy_version_id', 'lifecycle_policy_version_id', 'initial_fill_id', 'initial_fill_price', 'initial_stop_price', 'risk_capacity_shares', 'target_exposure_pct', 'target_shares', 'phase', 'profit_take_price', 'profit_target_reached', 'confirmation_completed', 'profit_trim_completed', 'arc_neckline_price', 'state_version', 'last_processed_trade_date', 'closed_at', 'created_at', 'updated_at')),
    'trailing_stops': frozenset(('id', 'lifecycle_id', 'initial_stop_price', 'high_water_mark', 'active_stop_price', 'phase', 'config_snapshot', 'last_processed_trade_date', 'exit_intent_id', 'created_at', 'updated_at')),
    'expectations': frozenset(('id', 'lifecycle_id', 'fill_trade_date', 'window_trading_days', 'observed_trading_days', 'status', 'fulfilled_trade_date', 'last_processed_trade_date', 'created_at', 'updated_at')),
    'active_intents': frozenset(('id', 'lifecycle_id', 'source_signal_id', 'trade_date', 'target_shares', 'reason_code', 'state_version', 'status', 'revision', 'created_at', 'updated_at')),
    'active_orders': frozenset(('id', 'portfolio_id', 'position_id', 'lifecycle_id', 'intent_id', 'source_signal_id', 'rule_certificate_id', 'rule_authorized_at', 'decision_at', 'market', 'symbol', 'industry_code', 'side', 'quantity', 'filled_quantity', 'limit_price', 'stop_price', 'reserved_cash', 'reserved_risk', 'reason_code', 'status', 'revision', 'earliest_execution_trade_date', 'created_at', 'updated_at')),
    'positions': frozenset(('id', 'portfolio_id', 'market', 'symbol', 'quantity', 'average_cost', 'active_stop_price', 'created_at', 'updated_at')),
    "daily_facts": frozenset(('id', 'lifecycle_id', 'trade_date', 'price_basis', 'data_as_of', 'input_payload', 'input_hash', 'planning_result', 'rule_version', 'state_version_before', 'state_version_after', 'final_target_shares', 'created_at', 'updated_at')),
}


@dataclass(frozen=True)
class BoundOrderStatusReplay:
    command_id: uuid.UUID
    operation_id: uuid.UUID
    lifecycle_id: uuid.UUID
    order_id: uuid.UUID
    outcome: str
    reason_code: str
    operation_seq: int
    state_version: int
    original_revision: int
    result_revision: int
    change_count: int
    database_sealing_verified: bool = False
    continuous_history_known: bool = False
    execution_authorized: bool = False


def _same(a, b):
    return _canonical(a) == _canonical(b)


def _managed(image):
    state = {key: value for key, value in image.items() if key not in IGNORED}
    for name in ("closed_at", "rule_authorized_at", "decision_at"):
        if state.get(name) is not None:
            value = datetime.fromisoformat(state[name])
            _require(value.utcoffset() is not None, "bound business time is naive")
            state[name] = value.astimezone(timezone.utc).isoformat(timespec="microseconds")
    return state


def _state(state):
    _require(set(state) == {"lifecycle", "trailing_stops", "expectations", "active_intents", "active_orders"},
             "bound predecessor state schema differs")
    return {name: _managed(value) if name == "lifecycle" else [_managed(item) for item in value]
            for name,value in state.items()}


def _image(image, name):
    _verify_row_image(image)
    _require(set(image) == PHYSICAL_COLUMNS[name] | {"__raw_row_json__"}, "bound physical fields differ")
    _require(isinstance(image["id"], str), "bound physical identity is malformed")
    uuid.UUID(image["id"])
    return _managed(image)


def _snapshot(snapshot, portfolio, lifecycle, *, final):
    _require(isinstance(snapshot, dict) and set(snapshot) == {"managed_state", "account_observations", "evidence"},
             "bound snapshot schema differs")
    evidence = snapshot["evidence"]
    keys = {"schema_version", "origin", "rows"} | ({"command_result"} if final else set())
    _require(isinstance(evidence, dict) and set(evidence) == keys and type(evidence["schema_version"]) is int
             and evidence["schema_version"] == 1 and evidence["origin"] == "LOCAL_STATUS_CAPTURE",
             "bound snapshot evidence schema differs")
    rows = evidence["rows"]
    _require(isinstance(rows, dict) and set(rows) == {"lifecycle", "trailing_stops", "expectations", "active_intents", "active_orders"}, "bound row groups differ")
    state = {"lifecycle": _image(rows["lifecycle"], "lifecycle")}
    _require(rows["lifecycle"]["id"] == str(lifecycle) and rows["lifecycle"]["portfolio_id"] == str(portfolio),
             "bound lifecycle identity differs")
    for name in rows.keys()-{"lifecycle"}:
        _require(isinstance(rows[name], list), "bound member rows are not an array")
        state[name] = [_image(item, name) for item in rows[name]]
        ids = [item["id"] for item in rows[name]]
        _require(len(ids) == len(set(ids)) and all(item["lifecycle_id"] == str(lifecycle) for item in rows[name]),
                 "bound child identities differ or duplicate")
    _require(_same(state, snapshot["managed_state"]), "bound managed interpretation differs")
    observations = snapshot["account_observations"]
    _require(isinstance(observations, dict) and set(observations) == {"origin", "positions"}
             and observations["origin"] == "ACCOUNT_OBSERVED" and isinstance(observations["positions"], list),
             "bound account observations differ")
    for position in observations["positions"]:
        _image(position, "positions")
        _require(position["portfolio_id"] == str(portfolio) and position["market"] == rows["lifecycle"]["market"]
                 and position["symbol"] == rows["lifecycle"]["symbol"], "bound observed position attribution differs")
    return rows


def _operation_hash(operation):
    payload = {name: value for name, value in operation.items() if name not in {"recorded_at", "canonical_payload", "operation_hash"}}
    _require(bytes(operation["canonical_payload"]) == _canonical(payload) and operation["operation_hash"] == _hash(payload),
             "bound operation fields/bytes differ")
    _require(operation["after_hash"] == _hash(operation["after_snapshot"]), "bound operation after digest differs")


def _previous_snapshot(previous):
    """Rebuild the immediate predecessor; this does not certify older history."""
    snapshot = previous["after_snapshot"]
    if previous["operation_kind"] == "MIGRATED_BASELINE":
        _require(previous["operation_seq"] == 1 and previous["formula_version"] == "unknown:prior"
                 and previous["before_snapshot"] is None and set(snapshot) == {"managed_state","account_observations","evidence"},
                 "unsupported migration predecessor")
        evidence = snapshot["evidence"]
        _require(set(evidence) == {"origin","missing_sources","migration_rows"} and evidence["origin"] == "UNKNOWN_PRIOR"
                 and evidence["missing_sources"] == ["PRIOR_OPERATIONS","ORDER_CREATION_INPUTS"], "migration predecessor evidence differs")
        rows = evidence["migration_rows"]
        names = {"position_trailing_stops":"trailing_stops", "position_expectations":"expectations",
                 "position_intents":"active_intents", "suggested_orders":"active_orders", "position_daily_facts":"daily_facts"}
        _require(set(rows) == {"lifecycle",*names}, "migration predecessor row groups differ")
        state = {"lifecycle": _image(rows["lifecycle"],"lifecycle")}
        _require(rows["lifecycle"]["id"] == str(previous["lifecycle_id"])
                 and rows["lifecycle"]["portfolio_id"] == str(previous["portfolio_id"]), "migration predecessor identity differs")
        for original,name in names.items():
            _require(isinstance(rows[original],list), "migration predecessor rows are not arrays")
            members = []
            ids = []
            for item in rows[original]:
                value = _image(item,name)
                _require(item["lifecycle_id"] == str(previous["lifecycle_id"]), "migration predecessor child attribution differs")
                ids.append(item["id"])
                if name == "active_orders" and item["status"] not in {"PROPOSED","EXECUTING","PARTIALLY_FILLED","RECONCILIATION_REQUIRED"}:
                    continue
                if name == "active_intents" and item["status"] not in {"ACTIVE","EXECUTING","RECONCILIATION_REQUIRED"}:
                    continue
                members.append(value)
            _require(len(ids)==len(set(ids)), "duplicate migration predecessor members")
            if name != "daily_facts": state[name]=members
        _require(_same(state,_state(snapshot["managed_state"])), "migration predecessor physical/managed state differs")
        observations=snapshot["account_observations"]
        _require(set(observations)=={"origin","positions"} and observations["origin"]=="ACCOUNT_OBSERVED"
                 and isinstance(observations["positions"],list), "migration predecessor account shape differs")
        for position in observations["positions"]:
            _image(position,"positions")
            _require(position["portfolio_id"]==str(previous["portfolio_id"])
                     and position["market"]==rows["lifecycle"]["market"] and position["symbol"]==rows["lifecycle"]["symbol"],
                     "migration predecessor observed position attribution differs")
        lifecycle=rows["lifecycle"]
    else:
        _require(previous["operation_kind"]=="INTENT_OR_ORDER_CHANGED" and previous["formula_version"]==SELECTOR,
                 "unsupported bound predecessor operation")
        rows=_snapshot(snapshot,previous["portfolio_id"],previous["lifecycle_id"],final=True)
        result=snapshot["evidence"]["command_result"]
        _require(isinstance(result,dict) and set(result)=={"outcome","reason_code","requested_status","missing_sources"}
                 and result["outcome"]==previous["outcome"] and result["reason_code"]==previous["reason_code"],
                 "bound predecessor result differs")
        lifecycle=rows["lifecycle"]
    _require(type(lifecycle["state_version"]) is int and lifecycle["state_version"]==previous["state_version_after"],
             "predecessor state version differs")
    return lifecycle


def _verify_bound(head, request, operation, previous, sources, changes):
    canonical = request.canonical_request()
    _require(head["command_kind"] == "ORDER_STATUS_CHANGED" and head["selector_version"] == SELECTOR
             and type(head["request_schema_version"]) is int and head["request_schema_version"] == 1
             and bytes(head["canonical_request"]) == canonical and head["request_hash"] == _hash_request(canonical)
             and head["request_key"] == request.request_key, "bound request differs")
    expected = [{"operation_id": str(operation["id"]), "lifecycle_id": str(operation["lifecycle_id"]),
                 "command_step_no": 1, "operation_kind": "INTENT_OR_ORDER_CHANGED"}]
    _require(_same(head["expected_steps"], expected) and head["expected_step_count"] == 1
             and head["expected_unbound_orders"] == [] and head["expected_unbound_order_count"] == 0
             and head["manifest_hash"] == _hash({"steps": expected, "unbound_orders": []}), "bound manifest differs")
    _operation_hash(operation)
    _operation_hash(previous)
    previous_lifecycle = _previous_snapshot(previous)
    _require(operation["before_hash"] == _hash(operation["before_snapshot"])
             and operation["business_command_id"] == head["id"] and operation["portfolio_id"] == head["portfolio_id"]
             and operation["command_step_no"] == 1 and operation["operation_kind"] == "INTENT_OR_ORDER_CHANGED"
             and operation["formula_version"] == SELECTOR and operation["snapshot_schema_version"] == 1
             and operation["actor_type"] == head["actor_type"] and operation["actor_ref"] == head["actor_ref"]
             and operation["effective_at"] is None and operation["effective_trade_date"] is None
             and operation["initial_input_hash"] is None, "bound operation attribution differs")
    _require(operation["previous_operation_id"] == previous["id"] and previous["lifecycle_id"] == operation["lifecycle_id"]
             and previous["portfolio_id"] == head["portfolio_id"] and operation["operation_seq"] == previous["operation_seq"]+1
             and operation["previous_hash"] == previous["operation_hash"]
             and operation["policy_version_id"] == previous["policy_version_id"]
             and operation["policy_content_hash"] == previous["policy_content_hash"]
             and _same(_state(previous["after_snapshot"]["managed_state"]), operation["before_snapshot"]["managed_state"])
             and type(operation["state_version_before"]) is int
             and operation["state_version_before"] == operation["state_version_after"] == previous["state_version_after"],
             "bound predecessor/state continuity differs")
    before_rows = _snapshot(operation["before_snapshot"], head["portfolio_id"], operation["lifecycle_id"], final=False)
    after_rows = _snapshot(operation["after_snapshot"], head["portfolio_id"], operation["lifecycle_id"], final=True)
    _require(before_rows["lifecycle"]["strategy_version_id"] == previous_lifecycle["strategy_version_id"]
             and before_rows["lifecycle"]["lifecycle_policy_version_id"] == previous_lifecycle["lifecycle_policy_version_id"]
             == str(operation["policy_version_id"]), "bound frozen owner differs")
    _require(type(before_rows["lifecycle"]["state_version"]) is int
             and before_rows["lifecycle"]["state_version"] == operation["state_version_before"], "bound snapshot version differs")
    by_id = lambda rows: {item["id"]: item for item in rows}
    before, after = by_id(before_rows["active_orders"]), by_id(after_rows["active_orders"])
    _require(set(before) == set(after) and str(request.order_id) in before, "bound fixed order members differ")
    old, new = before[str(request.order_id)], after[str(request.order_id)]
    _require(type(old["revision"]) is int and old["revision"] == request.expected_revision
             and type(new["revision"]) is int, "bound source revision differs")
    for name in before_rows:
        if name != "active_orders":
            _require(_same(before_rows[name], after_rows[name]), "bound command changes non-order state")
    for identity in before:
        if identity != str(request.order_id):
            _require(_same(before[identity], after[identity]), "bound command changes unrelated order")
    _require(len(sources) == operation["source_count"] == 1 and operation["sources_sha256"] == _hash(sources),
             "bound source set differs")
    source = sources[0]
    identities = {name for name in source if name.endswith(("_id", "_id_snapshot"))}
    identities -= {"id", "business_command_id", "operation_id", "order_id"}
    _require(all(source[name] is None for name in identities) and source["source_ref_snapshot"] is None
             and source["business_command_id"] == head["id"] and source["operation_id"] == operation["id"]
             and source["scope"] == "LIFECYCLE" and source["source_type"] == "ORDER"
             and source["source_role"] == "ORDER_STATUS_INPUT" and source["source_ordinal"] == 1
             and source["order_id"] == request.order_id and source["source_revision"] == request.expected_revision
             and _same(source["source_snapshot"], old) and source["source_content_hash"] == _hash(old)
             and all(source[name] is None for name in ("result_outcome", "result_reason_code", "result_schema_version")),
             "bound source identity/image differs")
    _require(operation["change_count"] == len(changes) and operation["changes_sha256"] == _hash(_change_payload(changes)),
             "bound changes digest differs")
    cursor = old
    for sequence, change in enumerate(changes, 1):
        _require(change["business_command_id"] == head["id"] and change["operation_id"] == operation["id"]
                 and change["scope"] == "LIFECYCLE" and change["entity_type"] == "ORDER"
                 and change["row_id"] == request.order_id and change["binding_side"] == "BOTH"
                 and change["change_kind"] == "UPDATE" and change["change_seq"] == sequence, "bound change identity differs")
        _image(change["before_row"], "active_orders")
        _image(change["after_row"], "active_orders")
        a, b = change["before_row"], change["after_row"]
        ignored = {"__raw_row_json__", "status", "revision", "updated_at"}
        _require(_same(a, cursor) and _same({k:v for k,v in a.items() if k not in ignored},
                                           {k:v for k,v in b.items() if k not in ignored})
                 and type(b["revision"]) is int and b["revision"] == a["revision"]+1,
                 "bound row chain/permitted fields differ")
        cursor = b
    _require(_same(cursor, new), "bound final row differs")
    result = operation["after_snapshot"]["evidence"]["command_result"]
    _require(isinstance(result, dict) and set(result) == {"outcome", "reason_code", "requested_status", "missing_sources"}
             and result["outcome"] == operation["outcome"] and result["reason_code"] == operation["reason_code"]
             and result["requested_status"] == request.status and isinstance(result["missing_sources"], list)
             and all(isinstance(item,str) and item.strip() for item in result["missing_sources"])
             and len(set(result["missing_sources"])) == len(result["missing_sources"]), "bound result metadata differs")
    outcome = operation["outcome"]
    _require(outcome in {"APPLIED", "BLOCKED", "NO_STATE_CHANGE"}, "bound outcome differs")
    if outcome == "APPLIED":
        _require(bool(changes) and new["status"] == request.status and not result["missing_sources"], "bound applied effect differs")
    else:
        _require(not changes and _same(old,new), "bound blocked/unchanged has an effect")
        if outcome == "NO_STATE_CHANGE":
            _require(new["status"] == request.status, "bound unchanged status differs")
    return BoundOrderStatusReplay(head["id"], operation["id"], operation["lifecycle_id"], request.order_id,
                                  outcome, operation["reason_code"], operation["operation_seq"],
                                  operation["state_version_after"], old["revision"], new["revision"], len(changes))


def _read_bound(session, portfolio_id, request):
    table = LifecycleBusinessCommand.__table__
    head = session.execute(select(table).where(table.c.portfolio_id == portfolio_id,
                                               table.c.request_key == request.request_key)).mappings().one_or_none()
    if head is None:
        return None
    if bytes(head["canonical_request"]) != request.canonical_request():
        raise LifecycleCommandReplayConflictError("同一组合请求键对应不同原始请求")
    try:
        prior = session.execute(select(table).where(table.c.id == head["previous_command_id"])).mappings().one_or_none()
        _require(prior is not None and prior["portfolio_id"] == portfolio_id and prior["command_seq"]+1 == head["command_seq"]
                 and prior["manifest_hash"] == head["previous_manifest_hash"]
                 and prior["manifest_hash"] == _hash({"steps":prior["expected_steps"], "unbound_orders":prior["expected_unbound_orders"]}),
                 "bound command predecessor differs")
        ops = PositionLifecycleOperation.__table__
        operations = session.execute(select(ops).where(ops.c.business_command_id == head["id"])).mappings().all()
        _require(len(operations) == 1, "bound operation set differs")
        operation = dict(operations[0])
        previous = session.execute(select(ops).where(ops.c.id == operation["previous_operation_id"])).mappings().one_or_none()
        _require(previous is not None, "bound operation predecessor absent")
        sources, changes = [], []
        for model, destination, ordering in ((QuantExecutionOperationSource,sources,"source_ordinal"),
                                              (QuantExecutionOperationChange,changes,"change_seq")):
            child = model.__table__
            destination.extend(dict(row) for row in session.execute(select(child).where(
                child.c.business_command_id == head["id"]).order_by(child.c[ordering])).mappings())
        for model in (SuggestedOrderInitialInput,PositionLifecycleInitialInput):
            child = model.__table__
            _require(session.execute(select(child.c.business_command_id).where(child.c.business_command_id == head["id"])).first() is None,
                     "bound command has creation input")
        return _verify_bound(dict(head),request,operation,dict(previous),sources,changes)
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        if isinstance(exc, LifecycleHistoryIntegrityError):
            raise
        raise LifecycleHistoryIntegrityError("malformed bound command") from exc


def load_bound_order_status_replay(session: Session, portfolio_id: uuid.UUID, request: OrderStatusCommandRequest):
    if not isinstance(portfolio_id,uuid.UUID) or not isinstance(request,OrderStatusCommandRequest):
        raise TypeError("portfolio UUID and original typed request required")
    if session.new or session.dirty or session.deleted:
        raise LifecycleInvalidStateError("历史重放读取前已有未提交变更")
    with session.no_autoflush:
        if session.scalar(text("SELECT pg_current_xact_id_if_assigned()::text")) is not None:
            raise LifecycleInvalidStateError("历史重放读取前已有写入或行锁，须回滚重试")
        return _read_bound(session,portfolio_id,request)
