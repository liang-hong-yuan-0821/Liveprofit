"""Read-only original-request replay checks for unbound order-status commands.

This content check does not certify DB capture/sealing, completeness against
business tables, execution permission, or continuous lifecycle history. LIVE
0051 closes inserts; 0052 supplies a separately gated unbound-only coordinator.
Bound commands need the operation-chain reader.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.modules.quant_strategy.application.errors import LifecycleInvalidStateError
from backend.modules.quant_strategy.application.lifecycle_command_selection import (
    RESULTS,
    SELECTOR_VERSION,
    OrderStatusCommandRequest,
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


class LifecycleCommandReplayConflictError(ValueError):
    """The same portfolio request key belongs to a different original request."""


# Frozen schema 1 (0051). Do not derive historical columns from evolving ORM
# metadata or recover missing historical fields from today's business row.
ORDER_IMAGE_COLUMNS_V1 = frozenset({
    "id", "portfolio_id", "position_id", "lifecycle_id", "intent_id", "source_signal_id",
    "rule_certificate_id", "rule_authorized_at", "decision_at", "market", "symbol", "industry_code",
    "side", "quantity", "filled_quantity", "limit_price", "stop_price", "reserved_cash", "reserved_risk",
    "reason_code", "status", "revision", "earliest_execution_trade_date", "created_at", "updated_at",
})


@dataclass(frozen=True)
class OrderStatusCommandReplay:
    command_id: uuid.UUID
    portfolio_id: uuid.UUID
    order_id: uuid.UUID
    outcome: str
    reason_code: str
    original_revision: int
    result_revision: int
    change_count: int
    database_sealing_verified: bool = False
    continuous_history_known: bool = False
    execution_authorized: bool = False


def _change_payload(rows):
    # Captured time belongs to the persisted evidence, not to request identity.
    for row in rows:
        _require(isinstance(row.get("captured_at"), datetime) and row["captured_at"].utcoffset() is not None,
                 "change captured time must be timezone-aware")
    return [{key: value.astimezone(timezone.utc).isoformat(timespec="microseconds") if isinstance(value, datetime) else value
             for key, value in row.items()} for row in rows]


def _order_image(image, portfolio_id, order_id):
    _verify_row_image(image)
    _require(set(image) == ORDER_IMAGE_COLUMNS_V1 | {"__raw_row_json__"}, "order physical schema 1 fields are incomplete or unknown")
    _require(image.get("id") == str(order_id) and image.get("portfolio_id") == str(portfolio_id)
             and image.get("lifecycle_id") is None, "unbound order image attribution differs")
    _require(type(image.get("revision")) is int and image["revision"] > 0,
             "unbound order image revision is invalid")


def _verify_unbound_replay(head, request, sources, changes):
    """Verify the versioned single-unbound-order result, without mutable reads."""
    _require(head["command_kind"] == "ORDER_STATUS_CHANGED" and head["request_schema_version"] == 1
             and head["selector_version"] == SELECTOR_VERSION, "unsupported order command schema")
    canonical = request.canonical_request()
    _require(bytes(head["canonical_request"]) == canonical and head["request_hash"] == _hash_request(canonical),
             "original request bytes or digest differ")
    expected = [{"order_id": str(request.order_id), "original_revision": request.expected_revision,
                 "allowed_results": list(RESULTS)}]
    _require(head["expected_steps"] == [] and head["expected_step_count"] == 0
             and head["expected_unbound_orders"] == expected and head["expected_unbound_order_count"] == 1,
             "single-order selector manifest differs")
    _require(head["manifest_hash"] == _hash({"steps": [], "unbound_orders": expected}),
             "command manifest digest differs")
    _require(len(sources) == 1, "unbound result must occur exactly once")
    source = sources[0]
    _require(source["business_command_id"] == head["id"] and source["operation_id"] is None
             and source["scope"] == "COMMAND" and source["source_type"] == "UNBOUND_ORDER_RESULT"
             and source["source_role"] == "ORDER_STATUS_RESULT" and source["source_ordinal"] == 1
             and source["order_id"] == request.order_id and source["result_schema_version"] == 1
             and source["result_outcome"] in RESULTS, "unbound result identity or schema differs")
    identity_columns = ("fill_event_id", "report_id", "resolution_id", "posting_id", "ledger_movement_id",
                        "daily_revision_id", "intent_revision_id", "allocation_batch_id", "order_initial_input_id",
                        "risk_event_id", "admission_event_id", "rule_certificate_id", "task_id_snapshot",
                        "signal_id_snapshot", "source_ref_snapshot")
    _require(all(source[name] is None for name in identity_columns), "unbound result has additional identities")
    result = source["source_snapshot"]
    _require(isinstance(result, dict) and set(result) == {
        "schema_version", "outcome", "reason_code", "before_row", "after_row", "missing_sources", "change_count", "changes_sha256"},
        "unsupported unbound result shape")
    _require(source["source_content_hash"] == _hash(result), "unbound result digest differs")
    _require(type(result["schema_version"]) is int and result["schema_version"] == source["result_schema_version"]
             and result["outcome"] == source["result_outcome"] and result["reason_code"] == source["result_reason_code"],
             "unbound result metadata differs from hashed snapshot")
    before, after = result["before_row"], result["after_row"]
    _order_image(before, head["portfolio_id"], request.order_id)
    _order_image(after, head["portfolio_id"], request.order_id)
    _require(before["revision"] == request.expected_revision and source["source_revision"] == after["revision"],
             "unbound result revision differs")
    _require(isinstance(result["missing_sources"], list)
             and all(isinstance(item, str) and item.strip() for item in result["missing_sources"])
             and len(set(result["missing_sources"])) == len(result["missing_sources"]),
             "unbound missing-source evidence is malformed")
    _require(isinstance(source["result_reason_code"], str) and source["result_reason_code"].strip(),
             "unbound result reason is missing")
    _require(type(result["change_count"]) is int and result["change_count"] == len(changes)
             and result["changes_sha256"] == _hash(_change_payload(changes)), "unbound change set digest differs")
    cursor = before
    for sequence, change in enumerate(changes, 1):
        _require(change["business_command_id"] == head["id"] and change["operation_id"] is None
                 and change["scope"] == "COMMAND" and change["entity_type"] == "ORDER"
                 and change["row_id"] == request.order_id and change["binding_side"] == "NONE"
                 and change["change_kind"] == "UPDATE" and change["change_seq"] == sequence,
                 "unbound change identity, sequence or scope differs")
        _order_image(change["before_row"], head["portfolio_id"], request.order_id)
        _order_image(change["after_row"], head["portfolio_id"], request.order_id)
        _require(change["before_row"] == cursor, "unbound physical change chain is discontinuous")
        old, new = change["before_row"], change["after_row"]
        ignored = {"__raw_row_json__", "status", "revision", "updated_at"}
        _require(_canonical({key: value for key, value in old.items() if key not in ignored})
                 == _canonical({key: value for key, value in new.items() if key not in ignored})
                 and new["revision"] == old["revision"] + 1,
                 "order-status change alters unrelated fields or skips a revision")
        cursor = change["after_row"]
    _require(cursor == after, "unbound result differs from final captured row")
    outcome = source["result_outcome"]
    if outcome == "NO_STATE_CHANGE":
        _require(not changes and before == after and after.get("status") == request.status,
                 "unchanged order-status result contains a state change")
    elif outcome == "APPLIED":
        _require(bool(changes) and after["revision"] > before["revision"]
                 and after.get("status") == request.status and not result["missing_sources"],
                 "applied order-status result lacks its requested effect or evidence")
    return OrderStatusCommandReplay(head["id"], head["portfolio_id"], request.order_id,
                                   outcome, source["result_reason_code"], before["revision"],
                                   after["revision"], len(changes))


def _hash_request(canonical):
    return hashlib.sha256(canonical).hexdigest()


def load_order_status_command_replay(session: Session, portfolio_id: uuid.UUID,
                                    request: OrderStatusCommandRequest) -> OrderStatusCommandReplay | None:
    """Check before participant selection. None means this key was not recorded.

    Same-key/different-request conflicts precede all participant reads. The
    narrowly supported result schema never confers replay execution permission.
    No writes, autoflush, commit, locks, or mutable current-order reads occur.
    """
    if not isinstance(portfolio_id, uuid.UUID) or not isinstance(request, OrderStatusCommandRequest):
        raise TypeError("portfolio UUID and typed original request are required")
    if session.new or session.dirty or session.deleted:
        raise LifecycleInvalidStateError("命令重放须在业务写入及参与者选择前检查")
    with session.no_autoflush:
        if session.scalar(text("SELECT pg_current_xact_id_if_assigned()::text")) is not None:
            raise LifecycleInvalidStateError("命令重放检查前已有写入或行锁，须回滚重试")
        commands = LifecycleBusinessCommand.__table__
        head = session.execute(select(commands).where(
            commands.c.portfolio_id == portfolio_id, commands.c.request_key == request.request_key)).mappings().one_or_none()
        if head is None:
            return None
        if bytes(head["canonical_request"]) != request.canonical_request():
            raise LifecycleCommandReplayConflictError("同一组合请求键对应不同原始请求")
        try:
            _require(head["request_key"] == request.request_key, "command request key differs")
            if head["command_seq"] == 1:
                _require(head["previous_command_id"] is None and head["previous_manifest_hash"] is None,
                         "first command has a predecessor")
            else:
                previous = session.execute(select(commands).where(
                    commands.c.id == head["previous_command_id"], commands.c.portfolio_id == portfolio_id)).mappings().one_or_none()
                _require(previous is not None and previous["command_seq"] + 1 == head["command_seq"]
                         and head["previous_manifest_hash"] == previous["manifest_hash"]
                         and previous["manifest_hash"] == _hash({"steps": previous["expected_steps"],
                                                                "unbound_orders": previous["expected_unbound_orders"]}),
                         "command predecessor or manifest digest differs")
            sources, changes = [], []
            for model, destination, ordering in (
                (QuantExecutionOperationSource, sources, "source_ordinal"),
                (QuantExecutionOperationChange, changes, "change_seq"),
            ):
                table = model.__table__
                destination.extend(dict(row) for row in session.execute(select(table).where(
                    table.c.business_command_id == head["id"]).order_by(table.c[ordering])).mappings())
            for model in (PositionLifecycleOperation, SuggestedOrderInitialInput, PositionLifecycleInitialInput):
                table = model.__table__
                _require(session.execute(select(table.c.business_command_id).where(
                    table.c.business_command_id == head["id"]).limit(1)).first() is None,
                    "unbound status command contains unexpected operation or creation input")
            return _verify_unbound_replay(head, request, sources, changes)
        except (KeyError, TypeError, OverflowError, ValueError) as exc:
            if isinstance(exc, LifecycleHistoryIntegrityError):
                raise
            raise LifecycleHistoryIntegrityError("malformed unbound command replay") from exc
