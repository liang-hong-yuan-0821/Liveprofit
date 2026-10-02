# test-catalog-begin
# {
#   "purpose": "量化策略 / lifecycle_operation_schema（持仓生命周期、表结构）：3ar-1 DDL and baselines: random isolated database, no real providers/LLM.",
#   "keywords": [
#     "量化策略",
#     "导出",
#     "历史审计",
#     "持仓生命周期",
#     "订单",
#     "投资组合",
#     "版本修订",
#     "表结构",
#     "来源证据",
#     "未绑定",
#     "lifecycle_operation_schema",
#     "export",
#     "history",
#     "lifecycle",
#     "order",
#     "portfolio",
#     "revision",
#     "schema",
#     "source",
#     "unbound"
#   ],
#   "covers": [
#     "backend/bootstrap/settings.py",
#     "backend/migrations/versions/0050_fill_version_step_integrity.py",
#     "backend/migrations/versions/0051_lifecycle_operation_schema.py",
#     "backend/modules/investment_workspace/infrastructure/models.py",
#     "backend/modules/quant_strategy/infrastructure/lifecycle_history_repository.py",
#     "backend/modules/quant_strategy/infrastructure/lifecycle_models.py",
#     "backend/modules/quant_strategy/infrastructure/lifecycle_operation_models.py",
#     "backend/modules/quant_strategy/infrastructure/models.py",
#     "backend/shared/db.py"
#   ],
#   "environment": [
#     "db"
#   ]
# }
# test-catalog-end

"""3ar-1 DDL and baselines: random isolated database, no real providers/LLM."""

from tests.backend.quant_strategy.support.lifecycle_operation_schema import (
    ROOT,
    TABLES,
    LINKS,
    operation_db,
    _seed,
    _business_rows,
    _shape_table,
    _required_values,
)

from tests.support.python.paths import PROJECT_ROOT
import hashlib
import json
import uuid
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    String,
    Table,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from backend.bootstrap.settings import CoreSettings
from backend.modules.investment_workspace.infrastructure.models import Portfolio
from backend.modules.quant_strategy.infrastructure.lifecycle_history_repository import (
    LifecycleHistoryIntegrityError,
    _verify_baseline,
    load_migration_baseline,
)
from backend.modules.quant_strategy.infrastructure.lifecycle_models import (
    LifecyclePolicyVersion,
    PositionExpectation,
    PositionLifecycleState,
    PositionTrailingStop,
    SuggestedOrder,
)
from backend.modules.quant_strategy.infrastructure.lifecycle_operation_models import (
    _tables,
)
from backend.modules.quant_strategy.infrastructure.models import (
    QuantStrategy,
    QuantStrategyVersion,
)









def test_schema_empty_upgrade_downgrade_and_model_parity(operation_db):
    config, engine = operation_db
    command.upgrade(config, "0051")
    inspector = inspect(engine)
    for model in _tables:
        actual = {column["name"]: column for column in inspector.get_columns(model.name)}
        assert set(actual) == set(model.columns.keys())
        for column in model.columns:
            assert actual[column.name]["nullable"] == column.nullable
            assert actual[column.name]["type"]._type_affinity == column.type._type_affinity
            if isinstance(column.type, Numeric):
                assert actual[column.name]["type"].precision is None
        actual_fk = {(tuple(item["constrained_columns"]), item["referred_table"], tuple(item["referred_columns"]))
                     for item in inspector.get_foreign_keys(model.name)}
        model_fk = {(tuple(c.name for c in fk.columns), next(iter(fk.elements)).column.table.name,
                     tuple(item.column.name for item in fk.elements)) for fk in model.foreign_key_constraints}
        assert actual_fk == model_fk
    for name in LINKS:
        from backend.shared.db import Base

        model = Base.metadata.tables[name]
        actual_checks = {item["name"] for item in inspector.get_check_constraints(name)}
        assert f"ck_{name}_operation_link" in actual_checks
        assert f"ck_{name}_operation_link" in {constraint.name for constraint in model.constraints}
        assert any(item["referred_table"] == "position_lifecycle_operations" and
                   item["constrained_columns"] == ["operation_id", "business_command_id"]
                   for item in inspector.get_foreign_keys(name))
    with engine.connect() as connection:
        for table in TABLES:
            assert connection.scalar(text(f"SELECT count(*) FROM {table}")) == 0
    command.downgrade(config, "0050")
    assert not set(TABLES).intersection(inspect(engine).get_table_names())
    for table in LINKS:
        assert "operation_id" not in {col["name"] for col in inspect(engine).get_columns(table)}
    command.upgrade(config, "0051")


def test_old_baseline_preserves_facts_and_requires_verified_export(operation_db, tmp_path, monkeypatch):
    config, engine = operation_db
    portfolio, lifecycles, _orders = _seed(engine)
    before = _business_rows(engine)
    command.upgrade(config, "0051")
    assert _business_rows(engine) == before
    with engine.connect() as connection:
        commands = connection.execute(text("SELECT * FROM lifecycle_business_commands")).mappings().all()
        operations = connection.execute(text("SELECT * FROM position_lifecycle_operations ORDER BY command_step_no")).mappings().all()
        assert len(commands) == 1 and commands[0]["portfolio_id"] == portfolio
        assert commands[0]["expected_step_count"] == 2
        assert commands[0]["captured_transaction_id"].isdigit()
        assert {row["lifecycle_id"] for row in operations} == set(lifecycles)
        for row in operations:
            assert (row["operation_kind"], row["reason_code"], row["operation_seq"], row["before_snapshot"]) == (
                "MIGRATED_BASELINE", "UNKNOWN_PRIOR", 1, None)
            assert row["change_count"] == 0 and row["initial_input_hash"] is None
            assert row["after_snapshot"]["evidence"]["origin"] == "UNKNOWN_PRIOR"
            assert row["after_snapshot"]["account_observations"]["origin"] == "ACCOUNT_OBSERVED"
            assert hashlib.sha256(bytes(row["canonical_payload"])).hexdigest() == row["operation_hash"]
        first = next(row for row in operations if row["lifecycle_id"] == lifecycles[0])
        assert first["state_version_after"] == 4
        assert first["after_snapshot"]["managed_state"]["lifecycle"]["initial_fill_price"] == "10.1234"
        assert len(first["after_snapshot"]["managed_state"]["active_orders"]) == 1
        assert len(first["after_snapshot"]["evidence"]["migration_rows"]["suggested_orders"]) == 2
        raw_stop = first["after_snapshot"]["evidence"]["migration_rows"]["position_trailing_stops"][0]["__raw_row_json__"]
        assert json.loads(raw_stop)["config_snapshot"] == {"multiple": 1.5, "nested": [True, 2.5]}
        assert '"initial_stop_price":9.1234' in raw_stop
        assert connection.scalar(text("SELECT count(*) FROM quant_execution_operation_sources")) == 2
        assert connection.scalar(text("SELECT count(*) FROM suggested_order_initial_inputs")) == 0
        assert connection.scalar(text("SELECT count(*) FROM position_lifecycle_initial_inputs")) == 0
    monkeypatch.delenv("LIVEPROFIT_LIFECYCLE_HISTORY_EXPORT_PATH", raising=False)
    with pytest.raises(RuntimeError, match="absolute lifecycle history export path"):
        command.downgrade(config, "0050")
    assert _business_rows(engine) == before
    assert set(TABLES).issubset(inspect(engine).get_table_names())
    export = tmp_path / "history.jsonl"
    monkeypatch.setenv("LIVEPROFIT_LIFECYCLE_HISTORY_EXPORT_PATH", str(export))
    command.downgrade(config, "0050")
    lines = export.read_bytes().splitlines(keepends=True)
    header = json.loads(lines[0])
    assert header["sha256"] == hashlib.sha256(b"".join(lines[1:])).hexdigest()
    assert header["counts"] == dict(zip(TABLES, (1, 2, 2, 0, 0, 0)))
    assert len(lines) == 6
    exported_ops = [json.loads(line)["row_json"] for line in lines[1:]
                    if json.loads(line)["table"] == "position_lifecycle_operations"]
    assert any(json.loads(row)["after_snapshot"]["evidence"]["migration_rows"]["position_trailing_stops"]
               for row in exported_ops)
    assert _business_rows(engine) == before


@pytest.mark.parametrize("table", TABLES)
def test_history_refuses_insert_update_delete_and_truncate(operation_db, table):
    config, engine = operation_db
    _seed(engine)
    command.upgrade(config, "0051")
    for statement, reason in (
        (f"INSERT INTO {table} DEFAULT VALUES", "completed command coordinator"),
        (f"TRUNCATE {table} CASCADE", "history is immutable"),
    ):
        with pytest.raises(DBAPIError, match=reason), engine.begin() as connection:
            connection.execute(text(statement))
    # Only tables with baseline rows exercise row-level immutability.
    if table in TABLES[:3]:
        for statement in (f"UPDATE {table} SET id=id", f"DELETE FROM {table}"):
            with pytest.raises(DBAPIError, match="history is immutable"), engine.begin() as connection:
                connection.execute(text(statement))






def test_source_shapes_reject_wrong_identity_scope_result_and_missing_revision(operation_db):
    config, engine = operation_db
    command.upgrade(config, "0051")
    with engine.begin() as connection:
        table = _shape_table(connection, "quant_execution_operation_sources")
        base = _required_values(table) | {"source_type": "ORDER", "scope": "LIFECYCLE", "operation_id": uuid.uuid4(),
                                            "order_id": uuid.uuid4(), "source_revision": 1, "source_snapshot": {}}
        connection.execute(table.insert().values(**base))
        result = base | {"id": uuid.uuid4(), "source_type": "UNBOUND_ORDER_RESULT", "scope": "COMMAND", "operation_id": None,
                             "result_outcome": "BLOCKED", "result_reason_code": "MISSING_SOURCE", "result_schema_version": 1}
        connection.execute(table.insert().values(**result))
        for changes in (
            {"order_id": None}, {"report_id": uuid.uuid4()}, {"scope": "COMMAND"},
            {"source_revision": None}, {"source_snapshot": None}, {"source_type": "TYPO"},
            {"result_outcome": "BLOCKED"},
        ):
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(table.insert().values(**(base | changes)))
        for changes in ({"result_outcome": None}, {"result_reason_code": None}, {"result_schema_version": None}):
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(table.insert().values(**(result | changes)))


def test_order_input_three_kinds_presence_and_decimal_precision(operation_db):
    config, engine = operation_db
    command.upgrade(config, "0051")
    with engine.begin() as connection:
        table = _shape_table(connection, "suggested_order_initial_inputs")
        base = _required_values(table)
        for name in list(base):
            if name.endswith("presence"):
                base[name] = "ABSENT"
        base.update(source_kind="MANUAL_PROTECTION", required_fields_snapshot=[])
        connection.execute(table.insert().values(**base))
        entry = base | {"order_id": uuid.uuid4(), "source_kind": "SIGNAL_ENTRY", "strategy_version_id": uuid.uuid4(),
                            "policy_version_id": uuid.uuid4(), "policy_content_hash": "a" * 64, "template_id": "TEST",
                            "management_policy_snapshot": {}, "source_signal_id_snapshot": 1,
                            "source_task_id_snapshot": uuid.uuid4(), "initial_stop_price": Decimal(9), "initial_exposure": Decimal(".5")}
        connection.execute(table.insert().values(**entry))
        lifecycle = entry | {"order_id": uuid.uuid4(), "source_kind": "LIFECYCLE_INTENT", "source_lifecycle_id": uuid.uuid4(),
                                "source_signal_id_snapshot": None, "source_task_id_snapshot": None, "initial_stop_price": None,
                                "initial_exposure": None}
        connection.execute(table.insert().values(**lifecycle))
        exact = Decimal("1234567890123456789012345.1234567890123456789012345")
        precise = entry | {"order_id": uuid.uuid4(), "total_assets": exact, "total_assets_presence": "VALUE"}
        connection.execute(table.insert().values(**precise))
        assert connection.scalar(table.select().with_only_columns(table.c.total_assets).where(table.c.order_id == precise["order_id"])) == exact
        for changes in (
            {"source_task_id_snapshot": None}, {"initial_stop_price": None}, {"initial_exposure": None},
            {"policy_content_hash": None}, {"total_assets": Decimal(10)}, {"total_assets_presence": "VALUE"},
            {"ma5_confirmed_cross_presence": "VALUE", "ma5_confirmed_cross_raw": 2},
            {"order_quantity": Decimal("NaN")},
            {"total_assets": Decimal("Infinity"), "total_assets_presence": "VALUE"},
            {"management_policy_snapshot": []}, {"management_policy_snapshot": True},
        ):
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(table.insert().values(**(entry | changes)))
        with pytest.raises(DBAPIError), connection.begin_nested():
            connection.execute(table.insert().values(**entry).values(management_policy_snapshot=text("'null'::jsonb")))


def test_operation_shape_rejects_nullable_versions_and_false_no_change(operation_db):
    config, engine = operation_db
    command.upgrade(config, "0051")
    with engine.begin() as connection:
        table = _shape_table(connection, "position_lifecycle_operations")
        snapshot = {"managed_state": {"lifecycle": {"state_version": 1}}, "account_observations": {}, "evidence": {}}
        base = _required_values(table) | {"operation_kind": "MIGRATED_BASELINE", "outcome": "BLOCKED",
                                             "after_snapshot": snapshot, "state_version_after": 1,
                                             "before_hash": "a" * 64, "after_hash": "a" * 64}
        connection.execute(table.insert().values(**base))
        initial = base | {"id": uuid.uuid4(), "operation_kind": "INITIALIZE", "outcome": "APPLIED", "initial_input_hash": "a" * 64,
                              "before_snapshot": {**snapshot, "managed_state": {"lifecycle": None}}}
        connection.execute(table.insert().values(**initial))
        for changes in (
            {"state_version_after": None}, {"before_snapshot": None}, {"initial_input_hash": None},
            {"outcome": "NO_STATE_CHANGE"}, {"operation_kind": "CORRECTION_APPLIED"},
            {"after_snapshot": {**snapshot, "extra": True}},
            {"state_version_after": 2}, {"before_snapshot": snapshot},
        ):
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(table.insert().values(**(initial | changes)))
        regular = base | {"operation_seq": 2, "previous_operation_id": uuid.uuid4(), "previous_hash": "a" * 64,
                          "operation_kind": "DAILY_EVALUATED", "before_snapshot": snapshot, "state_version_before": 1}
        connection.execute(table.insert().values(**regular))
        with pytest.raises(DBAPIError), connection.begin_nested():
            connection.execute(table.insert().values(**(regular | {"state_version_before": 99})))


def test_unbound_orders_without_lifecycle_have_unknown_prior_row_images(operation_db):
    config, engine = operation_db
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        portfolio = Portfolio(name="only-orders")
        session.add(portfolio)
        session.flush()
        order = SuggestedOrder(portfolio_id=portfolio.id, market="CN", symbol="000001.SZ", side="SELL",
                               quantity=Decimal(100), limit_price=Decimal(10), reason_code="TEST", status="PROPOSED", revision=3)
        session.add(order)
        session.commit()
        order_id, portfolio_id = order.id, portfolio.id
    before = _business_rows(engine)
    command.upgrade(config, "0051")
    with engine.connect() as connection:
        head = connection.execute(text("SELECT * FROM lifecycle_business_commands")).mappings().one()
        result = connection.execute(text("SELECT * FROM quant_execution_operation_sources")).mappings().one()
        assert head["portfolio_id"] == portfolio_id
        assert head["expected_step_count"] == 0 and head["expected_unbound_order_count"] == 1
        assert result["order_id"] == order_id and result["operation_id"] is None and result["scope"] == "COMMAND"
        assert result["source_type"] == "UNBOUND_ORDER_RESULT" and result["result_reason_code"] == "UNKNOWN_PRIOR"
        assert result["source_snapshot"]["before_row"]["revision"] == 3
        assert result["source_snapshot"]["before_row"] == result["source_snapshot"]["after_row"]
        assert connection.scalar(text("SELECT count(*) FROM position_lifecycle_operations")) == 0
        assert connection.scalar(text("SELECT count(*) FROM suggested_order_initial_inputs")) == 0
    assert _business_rows(engine) == before


def test_bad_legacy_policy_refuses_upgrade_and_preserves_schema_and_facts(operation_db):
    config, engine = operation_db
    _seed(engine)
    with engine.begin() as connection:
        connection.execute(text("UPDATE lifecycle_policy_versions SET content_hash='unknown-prior'"))
    before = _business_rows(engine)
    with pytest.raises(DBAPIError):
        command.upgrade(config, "0051")
    assert not set(TABLES).intersection(inspect(engine).get_table_names())
    assert _business_rows(engine) == before
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0050"


def test_change_images_must_be_objects(operation_db):
    config, engine = operation_db
    command.upgrade(config, "0051")
    with engine.begin() as connection:
        table = _shape_table(connection, "quant_execution_operation_changes")
        base = _required_values(table) | {"scope": "COMMAND", "entity_type": "ORDER", "binding_side": "NONE",
                                          "change_kind": "INSERT", "after_row": {"id": "test"}}
        connection.execute(table.insert().values(**base))
        for image in ([], True, text("'null'::jsonb")):
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(table.insert().values(**(base | {"after_row": image})))


def test_composite_identities_reject_cross_portfolio_and_cross_lifecycle(operation_db):
    config, engine = operation_db
    _seed(engine)
    _seed(engine)
    command.upgrade(config, "0051")
    with engine.begin() as connection:
        rows = connection.execute(text("SELECT * FROM position_lifecycle_operations ORDER BY portfolio_id, command_step_no")).mappings().all()
        first = rows[0]
        other = next(row for row in rows if row["portfolio_id"] != first["portfolio_id"])
        sibling = next(row for row in rows if row["portfolio_id"] == first["portfolio_id"] and row["id"] != first["id"])
        table = _shape_table(connection, "position_lifecycle_operations", temporary=False)
        for cols, parent, targets in (
            ("lifecycle_id,portfolio_id", "position_lifecycle_states", "id,portfolio_id"),
            ("business_command_id,portfolio_id", "lifecycle_business_commands", "id,portfolio_id"),
            ("previous_operation_id,lifecycle_id", "position_lifecycle_operations", "id,lifecycle_id"),
        ):
            connection.execute(text(f"ALTER TABLE shape ADD FOREIGN KEY ({cols}) REFERENCES {parent} ({targets})"))
        base = dict(first)
        for changes in ({"portfolio_id": other["portfolio_id"]}, {"business_command_id": other["business_command_id"]},
                        {"operation_seq": 2, "operation_kind": "DAILY_EVALUATED", "previous_operation_id": sibling["id"],
                         "previous_hash": sibling["operation_hash"], "before_snapshot": first["after_snapshot"],
                         "state_version_before": first["state_version_after"]}):
            with pytest.raises(DBAPIError, match="foreign key"), connection.begin_nested():
                connection.execute(table.insert().values(**(base | changes)))


def test_repository_recomputes_fields_and_keeps_unknown_after_legacy_writes(operation_db):
    config, engine = operation_db
    portfolio, lifecycles, _ = _seed(engine)
    command.upgrade(config, "0051")
    with sessionmaker(bind=engine)() as session:
        baseline = load_migration_baseline(session, portfolio)
        assert baseline is not None and baseline.origin == "UNKNOWN_PRIOR"
        assert not baseline.prior_history_known and not baseline.continuous_history_known
        assert load_migration_baseline(session, uuid.uuid4()) is None
    with engine.begin() as connection:
        connection.execute(text("UPDATE position_lifecycle_states SET target_shares=300 WHERE id=:id"), {"id": lifecycles[1]})
    with sessionmaker(bind=engine)() as session:
        baseline = load_migration_baseline(session, portfolio)
        assert next(row for row in baseline.operations if row["lifecycle_id"] == lifecycles[1])["after_snapshot"]["managed_state"]["lifecycle"]["target_shares"] == "200.0000"
        head = dict(session.execute(text("SELECT * FROM lifecycle_business_commands WHERE portfolio_id=:id"), {"id": portfolio}).mappings().one())
        operations = [dict(row) for row in session.execute(text("SELECT * FROM position_lifecycle_operations ORDER BY command_step_no")).mappings()]
        sources = [dict(row) for row in session.execute(text("SELECT * FROM quant_execution_operation_sources ORDER BY source_role,source_ordinal")).mappings()]
    changed = deepcopy(operations)
    changed[0]["state_version_after"] += 1
    with pytest.raises(LifecycleHistoryIntegrityError, match="fields or bytes"):
        _verify_baseline(head, changed, sources)
    changed_sources = deepcopy(sources)
    changed_sources[0]["source_snapshot"]["quantity"] = "999"
    with pytest.raises(LifecycleHistoryIntegrityError, match="content hash"):
        _verify_baseline(head, operations, changed_sources)
