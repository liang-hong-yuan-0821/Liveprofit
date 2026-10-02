"""Verify and read 0051 local baselines without granting execution authority."""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.modules.quant_strategy.infrastructure.lifecycle_operation_models import (
    LifecycleBusinessCommand,
    PositionLifecycleOperation,
    QuantExecutionOperationSource,
)


class LifecycleHistoryIntegrityError(ValueError):
    """Stored bytes, business fields or the promised baseline set disagree."""


@dataclass(frozen=True)
class LocalMigrationBaseline:
    command_id: uuid.UUID
    portfolio_id: uuid.UUID
    operations: tuple[dict, ...]
    unbound_order_results: tuple[dict, ...]
    origin: str = "UNKNOWN_PRIOR"
    prior_history_known: bool = False
    continuous_history_known: bool = False


def _normalize(value):
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {key: _normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    return value


def _canonical(value):
    return json.dumps(_normalize(value), sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _hash(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _require(condition, reason):
    if not condition:
        raise LifecycleHistoryIntegrityError(reason)


def _verify_row_image(image):
    _require(isinstance(image, dict) and isinstance(image.get("__raw_row_json__"), str),
             "migration physical row original is missing")
    raw = json.loads(image["__raw_row_json__"], parse_float=Decimal)
    _require(isinstance(raw, dict), "migration physical row original is not an object")
    _require(_canonical(_normalize(raw)) == _canonical({key: value for key, value in image.items() if key != "__raw_row_json__"}),
             "migration row interpretation differs from original")


def _verify_baseline(head, operations, sources):
    """No mutable current row is used as a historical certificate."""
    request = {"schema_version": 1, "migration": "0051", "portfolio_id": str(head["portfolio_id"])}
    _require(head["command_kind"] == "MIGRATED_BASELINE" and head["selector_version"] == "migration:0051",
             "not a 0051 migration baseline")
    _require(bytes(head["canonical_request"]) == _canonical(request) and head["request_hash"] == _hash(request),
             "migration request fields or bytes disagree")
    steps, unbound = head["expected_steps"], head["expected_unbound_orders"]
    _require(head["manifest_hash"] == _hash({"steps": steps, "unbound_orders": unbound}),
             "migration manifest hash differs")
    _require(len(steps) == len(operations) == head["expected_step_count"], "migration operation set is incomplete")
    _require([step["operation_id"] for step in steps] == [str(row["id"]) for row in operations],
             "migration operation identities differ")
    operation_ids = {row["id"] for row in operations}
    _require(len(operation_ids) == len(operations), "duplicate migration operations")
    for source in sources:
        _require(source["business_command_id"] == head["id"] and
                 (source["operation_id"] is None or source["operation_id"] in operation_ids),
                 "migration source attribution differs")
        _require(source["source_content_hash"] == _hash(source["source_snapshot"]),
                 "migration source content hash differs")
        if source["operation_id"] is not None:
            _require(source["scope"] == "LIFECYCLE" and source["source_type"] == "ORDER",
                     "migration lifecycle source type differs")
            _verify_row_image(source["source_snapshot"])
            _require(str(source["order_id"]) == source["source_snapshot"]["id"] and
                     source["source_revision"] == source["source_snapshot"]["revision"],
                     "migration order source identity differs")
        else:
            _require(source["scope"] == "COMMAND" and source["source_type"] == "UNBOUND_ORDER_RESULT" and
                     source["result_outcome"] == "BLOCKED" and source["result_reason_code"] == "UNKNOWN_PRIOR",
                     "migration unbound result differs")
            result = source["source_snapshot"]
            _verify_row_image(result["before_row"])
            _verify_row_image(result["after_row"])
            _require(result["before_row"] == result["after_row"] and
                     str(source["order_id"]) == result["before_row"]["id"] and
                     result["before_row"]["lifecycle_id"] is None and
                     source["source_revision"] == result["before_row"]["revision"],
                     "migration unbound result row differs")
    for step, row in zip(steps, operations):
        _require(row["business_command_id"] == head["id"] and row["portfolio_id"] == head["portfolio_id"] and
                 str(row["lifecycle_id"]) == step["lifecycle_id"] and row["command_step_no"] == step["command_step_no"] and
                 row["operation_kind"] == step["operation_kind"] == "MIGRATED_BASELINE" and row["operation_seq"] == 1 and
                 row["reason_code"] == "UNKNOWN_PRIOR" and row["before_snapshot"] is None and
                 row["change_count"] == 0 and row["initial_input_hash"] is None,
                 "migration operation attribution or shape differs")
        payload = {column.name: row[column.name] for column in PositionLifecycleOperation.__table__.columns
                   if column.name not in ("recorded_at", "canonical_payload", "operation_hash")}
        _require(bytes(row["canonical_payload"]) == _canonical(payload) and row["operation_hash"] == _hash(payload),
                 "migration operation fields or bytes disagree")
        _require(row["before_hash"] == _hash(None) and row["after_hash"] == _hash(row["after_snapshot"]),
                 "migration snapshot hash differs")
        scoped = [source for source in sources if source["operation_id"] == row["id"]]
        scoped.sort(key=lambda source: (source["source_role"], source["source_ordinal"]))
        _require(row["source_count"] == len(scoped) and row["sources_sha256"] == _hash(scoped) and
                 row["changes_sha256"] == _hash([]), "migration source set differs")
        original_rows = row["after_snapshot"]["evidence"]["migration_rows"]
        _verify_row_image(original_rows["lifecycle"])
        for key in ("position_trailing_stops", "position_expectations", "position_intents", "suggested_orders", "position_daily_facts"):
            for original in original_rows[key]:
                _verify_row_image(original)
    command_sources = [source for source in sources if source["operation_id"] is None]
    _require(len(unbound) == len(command_sources) == head["expected_unbound_order_count"],
             "migration unbound result set is incomplete")
    by_order = {str(source["order_id"]): source for source in command_sources}
    _require(len(by_order) == len(command_sources) and set(by_order) == {row["order_id"] for row in unbound},
             "migration unbound order identities differ")
    for expected in unbound:
        actual = by_order[expected["order_id"]]
        _require(actual["source_revision"] == expected["original_revision"] and actual["result_outcome"] in expected["allowed_results"],
                 "migration unbound outcome differs")


def load_migration_baseline(session: Session, portfolio_id: uuid.UUID) -> LocalMigrationBaseline | None:
    """Immutable historical read. None means no baseline, never a clean account."""
    command_table = LifecycleBusinessCommand.__table__
    head = session.execute(select(command_table).where(
        command_table.c.portfolio_id == portfolio_id,
        command_table.c.request_key == "migration:0051:baseline")).mappings().one_or_none()
    if head is None:
        return None
    operation_table = PositionLifecycleOperation.__table__
    source_table = QuantExecutionOperationSource.__table__
    operations = [dict(row) for row in session.execute(select(operation_table).where(
        operation_table.c.business_command_id == head["id"]).order_by(operation_table.c.command_step_no)).mappings()]
    sources = [dict(row) for row in session.execute(select(source_table).where(
        source_table.c.business_command_id == head["id"]).order_by(source_table.c.source_role, source_table.c.source_ordinal)).mappings()]
    try:
        _verify_baseline(head, operations, sources)
    except (KeyError, TypeError, OverflowError, json.JSONDecodeError) as exc:
        raise LifecycleHistoryIntegrityError("malformed migration baseline") from exc
    return LocalMigrationBaseline(head["id"], portfolio_id, tuple(operations),
                                  tuple(source for source in sources if source["operation_id"] is None))
