# test-catalog-begin
# {
#   "purpose": "组合回撤命令修改前全集选择、固定活动成员及真实锁保持；只验证scope，不认证命令封口。",
#   "keywords": ["量化策略", "持仓生命周期", "命令", "全组合", "回撤", "未绑定订单", "并发", "参与范围选择"],
#   "covers": [
#     "backend/modules/quant_strategy/application/lifecycle_portfolio_selection.py",
#     "backend/modules/investment_workspace/infrastructure/models.py",
#     "backend/modules/quant_strategy/infrastructure/lifecycle_models.py"
#   ],
#   "environment": ["db"],
#   "related_tests": ["tests/backend/quant_strategy/integration/test_portfolio_drawdown_actions.py"]
# }
# test-catalog-end
"""Portfolio scope only, in independently created random databases."""
import hashlib
import json
import re
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from alembic import command
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from backend.modules.investment_workspace.infrastructure.models import (
    Portfolio,
    PortfolioPosition,
)
from backend.modules.quant_strategy.application.errors import (
    FillValidationError,
    LifecycleInvalidStateError,
    LifecycleNotFoundError,
    LifecycleRevisionConflictError,
)
from backend.modules.quant_strategy.application.lifecycle_portfolio_selection import (
    PortfolioDrawdownCommandRequest,
    select_portfolio_drawdown_command,
)
from backend.modules.quant_strategy.infrastructure.lifecycle_models import (
    PositionIntent,
    PositionLifecycleState,
    SuggestedOrder,
)
from tests.backend.quant_strategy.support import (
    lifecycle_operation_schema as schema_support,
)

operation_db = schema_support.operation_db
_seed = schema_support._seed


def _setup(operation_db):
    config, engine = operation_db
    portfolio, lifecycles, original = _seed(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        assert session.scalar(text("SELECT current_database()")).startswith("liveprofit_lc_history_0051_test_")
        session.get(SuggestedOrder, original[1]).side = "BUY"
        position = PortfolioPosition(portfolio_id=portfolio, market="CN", symbol="000001.SZ",
                                     quantity=Decimal(200), average_cost=Decimal("10.1234"))
        session.add(position)
        session.flush()
        session.get(PositionLifecycleState, lifecycles[1]).position_id = position.id
        intents = [PositionIntent(lifecycle_id=identity, trade_date=date(2026, 9, 24),
                                  target_shares=Decimal(100), reason_code="TEST", state_version=2,
                                  status="ACTIVE", revision=1) for identity in lifecycles]
        session.add_all(intents)
        session.flush()
        orders = [SuggestedOrder(portfolio_id=portfolio, market="CN", symbol="000001.SZ", side="BUY",
                                 quantity=Decimal(100), filled_quantity=Decimal(0), limit_price=Decimal("10.1234"),
                                 reason_code="TEST", status=status, revision=1)
                  for status in ("PROPOSED", "EXECUTING", "PARTIALLY_FILLED", "RECONCILIATION_REQUIRED",
                                 "CANCELLED", "FILLED")]
        orders[2].filled_quantity = Decimal(50)
        orders[5].filled_quantity = Decimal(100)
        bound_sell = SuggestedOrder(portfolio_id=portfolio, market="CN", symbol="000001.SZ", side="SELL",
                                    quantity=Decimal(100), limit_price=Decimal(10), reason_code="TEST",
                                    lifecycle_id=lifecycles[1], intent_id=intents[1].id, status="PROPOSED", revision=1)
        session.add_all((*orders, bound_sell))
        session.commit()
        identities = {"portfolio": portfolio, "lifecycles": lifecycles, "original": original,
                      "unbound": [r.id for r in orders], "sell": bound_sell.id, "position": position.id,
                      "intents": [r.id for r in intents]}
    command.upgrade(config, "0053")
    return engine, identities


def _request(ids, key="portfolio-scope"):
    return PortfolioDrawdownCommandRequest(ids["portfolio"], date(2026, 9, 24), key)


def test_full_scope_keeps_closed_pending_buy_and_every_unbound_result(operation_db):
    engine, ids = _setup(operation_db)
    with sessionmaker(bind=engine)() as session:
        counts = tuple(session.scalar(text(f"SELECT count(*) FROM {table}")) for table in (
            "lifecycle_business_commands", "position_lifecycle_operations", "quant_execution_operation_changes"))
        selected = select_portfolio_drawdown_command(session, _request(ids))
        assert selected.scope_only and selected.has_history_participants
        assert [r.lifecycle_id for r in selected.lifecycles] == sorted(ids["lifecycles"])
        assert {r.entity_id for r in selected.unbound_orders} == set(ids["unbound"][:4])
        scopes = {r.lifecycle_id: r for r in selected.lifecycles}
        assert [r.entity_id for r in scopes[ids["lifecycles"][0]].orders] == [ids["original"][1]]
        assert [r.entity_id for r in scopes[ids["lifecycles"][1]].orders] == [ids["sell"]]
        assert len(scopes[ids["lifecycles"][0]].stops) == len(scopes[ids["lifecycles"][0]].expectations) == 1
        assert len(selected.positions) == 1
        manifest = selected.manifest()
        assert [r["command_step_no"] for r in manifest["steps"]] == [1, 2]
        assert len({r["operation_id"] for r in manifest["steps"]}) == 2
        assert all(r["allowed_results"] == ["APPLIED", "NO_STATE_CHANGE", "BLOCKED"]
                   for r in manifest["unbound_orders"])
        assert selected.request_hash == hashlib.sha256(_request(ids).canonical_request()).hexdigest()
        assert counts == tuple(session.scalar(text(f"SELECT count(*) FROM {table}")) for table in (
            "lifecycle_business_commands", "position_lifecycle_operations", "quant_execution_operation_changes"))
        assert session.get(SuggestedOrder, ids["unbound"][0]).status == "PROPOSED"
        session.rollback()
    with sessionmaker(bind=engine)() as session:
        assert session.get(SuggestedOrder, ids["unbound"][0]).status == "PROPOSED"


def test_closed_owner_without_pending_buy_is_excluded_but_cannot_be_reactivated(operation_db):
    engine, ids = _setup(operation_db)
    with engine.begin() as connection:
        connection.execute(text("UPDATE suggested_orders SET side='SELL' WHERE id=:id"),
                           {"id": ids["original"][1]})
    with sessionmaker(bind=engine)() as session:
        selected = select_portfolio_drawdown_command(session, _request(ids))
        assert [r.lifecycle_id for r in selected.lifecycles] == [ids["lifecycles"][1]]
        with engine.connect() as other:
            other.execute(text("SET LOCAL lock_timeout='100ms'"))
            with pytest.raises(DBAPIError, match="lock timeout"):
                other.execute(text("UPDATE position_lifecycle_states SET closed_at=NULL,phase='ENTRY_PENDING' WHERE id=:id"),
                              {"id": ids["lifecycles"][0]})
            other.rollback()
        session.rollback()


def test_foreign_account_order_cannot_reference_selected_lifecycle(operation_db):
    engine, ids = _setup(operation_db)
    with sessionmaker(bind=engine)() as session:
        foreign = Portfolio(name=f"foreign-order-{uuid.uuid4().hex}")
        session.add(foreign)
        session.flush()
        session.add(SuggestedOrder(portfolio_id=foreign.id, lifecycle_id=ids["lifecycles"][1],
                                  market="CN", symbol="000001.SZ", side="BUY", quantity=Decimal(100),
                                  limit_price=Decimal(10), reason_code="TEST", status="PROPOSED", revision=1))
        session.commit()
        with pytest.raises(LifecycleInvalidStateError, match="其他组合订单引用"):
            select_portfolio_drawdown_command(session, _request(ids))


def test_orm_refresh_original_numeric_precision_and_stable_request_identity(operation_db):
    engine, ids = _setup(operation_db)
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        stale = session.get(SuggestedOrder, ids["unbound"][0])
        with engine.begin() as other:
            other.execute(text("UPDATE suggested_orders SET revision=2,limit_price=10.9876 WHERE id=:id"),
                          {"id": stale.id})
        session.execute(text("SET LOCAL TIME ZONE 'Asia/Shanghai'"))
        selected = select_portfolio_drawdown_command(session, _request(ids))
        row = next(r for r in selected.unbound_orders if r.entity_id == stale.id)
        assert row.revision == stale.revision == 2
        raw = json.loads(row.before_json, parse_float=Decimal)
        assert raw["limit_price"] == Decimal("10.9876")
        assert raw["updated_at"].endswith("+00:00")
        session.rollback()
        second = select_portfolio_drawdown_command(session, _request(ids))
        assert selected.canonical_request == second.canonical_request
        assert selected.request_hash == second.request_hash
        assert selected.manifest_hash() != second.manifest_hash()  # New preallocated operation IDs.


@pytest.mark.parametrize("prior", ["dirty", "flushed", "raw_sql", "row_lock"])
def test_selection_rejects_prior_mutation_without_auto_flush(operation_db, prior):
    engine, ids = _setup(operation_db)
    with sessionmaker(bind=engine)() as session:
        if prior in {"dirty", "flushed"}:
            session.get(SuggestedOrder, ids["unbound"][0]).quantity += 1
            if prior == "flushed":
                session.flush()
        elif prior == "raw_sql":
            session.execute(text("UPDATE suggested_orders SET quantity=quantity+1 WHERE id=:id"),
                            {"id": ids["unbound"][0]})
        else:
            session.execute(text("SELECT id FROM suggested_orders WHERE id=:id FOR UPDATE"),
                            {"id": ids["unbound"][0]})
        with pytest.raises(LifecycleInvalidStateError):
            select_portfolio_drawdown_command(session, _request(ids))
        session.rollback()
        assert session.get(SuggestedOrder, ids["unbound"][0]).quantity == 100


@pytest.mark.parametrize("mode", ["AUTOCOMMIT", "REPEATABLE READ", "SERIALIZABLE"])
def test_scope_requires_real_read_committed_transaction(operation_db, mode):
    engine, ids = _setup(operation_db)
    isolated = create_engine(engine.url, isolation_level=mode)
    try:
        with (sessionmaker(bind=isolated)() as session,
              pytest.raises(LifecycleInvalidStateError, match="AUTOCOMMIT|READ COMMITTED")):
            select_portfolio_drawdown_command(session, _request(ids))
    finally:
        isolated.dispose()


@pytest.mark.parametrize("table,where,assignment", [
    ("portfolios", "id", "version=version+1"),
    ("position_lifecycle_states", "id", "target_shares=target_shares+1"),
    ("suggested_orders", "id", "status='PROPOSED'"),
    ("position_intents", "id", "target_shares=target_shares+1"),
    ("position_trailing_stops", "lifecycle_id", "high_water_mark=high_water_mark+1"),
    ("position_expectations", "lifecycle_id", "window_trading_days=window_trading_days+1"),
    ("portfolio_positions", "id", "quantity=quantity+1"),
])
def test_fixed_and_excluded_rows_remain_locked_until_caller_finishes(operation_db, table, where, assignment):
    engine, ids = _setup(operation_db)
    identity = {"portfolios": ids["portfolio"], "position_lifecycle_states": ids["lifecycles"][0],
                "suggested_orders": ids["unbound"][4], "position_intents": ids["intents"][0],
                "position_trailing_stops": ids["lifecycles"][0], "position_expectations": ids["lifecycles"][0],
                "portfolio_positions": ids["position"]}[table]
    with sessionmaker(bind=engine)() as session:
        select_portfolio_drawdown_command(session, _request(ids))
        with engine.connect() as other:
            other.execute(text("SET LOCAL lock_timeout='100ms'"))
            with pytest.raises(DBAPIError, match="lock timeout"):
                other.execute(text(f"UPDATE {table} SET {assignment} WHERE {where}=:id"), {"id": identity})
            other.rollback()
        session.rollback()
        with engine.begin() as other:
            other.execute(text("SET LOCAL lock_timeout='100ms'"))
            assert other.execute(text(f"UPDATE {table} SET {assignment} WHERE {where}=:id"),
                                 {"id": identity}).rowcount == 1


def test_parent_fk_blocks_new_unbound_order_after_selection(operation_db):
    engine, ids = _setup(operation_db)
    with sessionmaker(bind=engine)() as session:
        select_portfolio_drawdown_command(session, _request(ids))
        with sessionmaker(bind=engine)() as other:
            other.execute(text("SET LOCAL lock_timeout='100ms'"))
            other.add(SuggestedOrder(portfolio_id=ids["portfolio"], market="CN", symbol="000002.SZ", side="BUY",
                                     quantity=Decimal(100), limit_price=Decimal(10), reason_code="TEST",
                                     status="PROPOSED", revision=1))
            with pytest.raises(DBAPIError, match="lock timeout"):
                other.flush()


def test_application_role_can_select_scope_without_audit_write_permissions(operation_db):
    engine, ids = _setup(operation_db)
    role = f"lc_portfolio_scope_test_{uuid.uuid4().hex[:12]}"
    created = False
    try:
        with engine.begin() as connection:
            assert connection.scalar(text("SELECT current_database()")).startswith("liveprofit_lc_history_0051_test_")
            connection.execute(text(f'CREATE ROLE "{role}" NOLOGIN'))
            created = True
            connection.execute(text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
            connection.execute(text(f'GRANT SELECT ON ALL TABLES IN SCHEMA public TO "{role}"'))
            for table in ("portfolios", "quant_strategy_versions", "lifecycle_policy_versions",
                          "position_lifecycle_states", "suggested_orders", "position_trailing_stops",
                          "position_expectations", "position_intents", "portfolio_positions"):
                connection.execute(text(f'GRANT UPDATE (id) ON {table} TO "{role}"'))
        with sessionmaker(bind=engine)() as session:
            session.execute(text(f'SET LOCAL ROLE "{role}"'))
            selected = select_portfolio_drawdown_command(session, _request(ids))
            assert selected.has_history_participants and selected.scope_only
            for table in ("lifecycle_business_commands", "position_lifecycle_operations",
                          "quant_execution_operation_sources", "quant_execution_operation_changes"):
                assert not session.scalar(text("SELECT has_table_privilege(current_user,:table,'INSERT')"),
                                          {"table": table})
            with pytest.raises(DBAPIError, match="permission denied"):
                session.execute(text("INSERT INTO quant_execution_operation_changes DEFAULT VALUES"))
            session.rollback()
    finally:
        if created:
            with engine.begin() as connection:
                connection.execute(text(f'DROP OWNED BY "{role}"'))
                connection.execute(text(f'DROP ROLE "{role}"'))


def test_global_lock_order_and_uuid_sorting(operation_db):
    engine, ids = _setup(operation_db)
    locks = []

    def observe(_conn, _cursor, statement, _parameters, _context, _many):
        if "FOR UPDATE" in statement:
            table = re.search(r"FROM (\w+)", statement).group(1)
            locks.append(table)
            if table != "portfolios":
                assert f"ORDER BY {table}.id" in statement

    event.listen(engine, "before_cursor_execute", observe)
    try:
        with sessionmaker(bind=engine)() as session:
            select_portfolio_drawdown_command(session, _request(ids))
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert locks == ["portfolios", "quant_strategy_versions", "lifecycle_policy_versions",
                     "position_lifecycle_states", "suggested_orders", "position_trailing_stops",
                     "position_expectations", "position_intents", "portfolio_positions"]


@pytest.mark.parametrize("race", ["owner", "removed_order", "removed_position"])
def test_inventory_change_during_locking_requires_whole_transaction_retry(operation_db, race):
    engine, ids = _setup(operation_db)
    changed = False

    def interleave(_conn, _cursor, statement, _parameters, _context, _many):
        nonlocal changed
        if changed:
            return
        if race == "owner":
            trigger = "SELECT position_lifecycle_states.id, position_lifecycle_states.strategy_version_id" in statement
        else:
            # Move a pre-existing child out of the account. Updating an existing
            # portfolio FK to NULL would violate NOT NULL; removing a row has no
            # parent FK acquisition and reaches the inventory retry boundary.
            trigger = f"SELECT {'suggested_orders' if race == 'removed_order' else 'portfolio_positions'}.id" in statement
        if not trigger:
            return
        changed = True
        with engine.begin() as other:
            other.execute(text("SET LOCAL lock_timeout='500ms'"))
            if race == "owner":
                other.execute(text("UPDATE position_lifecycle_states SET strategy_version_id=:version WHERE id=:id"),
                              {"version": foreign_version, "id": ids["lifecycles"][1]})
            elif race == "removed_order":
                other.execute(text("DELETE FROM suggested_orders WHERE id=:id"), {"id": spare_order})
            else:
                # Must be an unreferenced position to avoid lifecycle FK lock
                # acquisition masking the inventory check under test.
                other.execute(text("DELETE FROM portfolio_positions WHERE id=:id"), {"id": spare})

    if race == "owner":
        # Owner changes need a real second version; NOT NULL/FKs must not stop
        # the mutation before it reaches the selector's owner recheck.
        with engine.begin() as connection:
            foreign_version = connection.scalar(text("""
                INSERT INTO quant_strategy_versions (id,strategy_id,version_no,status,source_code,source_hash)
                SELECT gen_random_uuid(),strategy_id,2,status,source_code,source_hash
                FROM quant_strategy_versions LIMIT 1 RETURNING id
            """))
    elif race == "removed_order":
        # Upgrade baselines preserve old unbound orders with FK references.
        # Create an independent legacy-writer row after migration so DELETE
        # reaches the scope check instead of failing that history-preservation FK.
        with sessionmaker(bind=engine, expire_on_commit=False)() as session:
            row = SuggestedOrder(portfolio_id=ids["portfolio"], market="CN", symbol="000009.SZ", side="BUY",
                                 quantity=Decimal(100), limit_price=Decimal(10), reason_code="TEST",
                                 status="CANCELLED", revision=1)
            session.add(row)
            session.commit()
            spare_order = row.id
    elif race == "removed_position":
        with sessionmaker(bind=engine, expire_on_commit=False)() as session:
            row = PortfolioPosition(portfolio_id=ids["portfolio"], market="CN", symbol="000002.SZ",
                                    quantity=Decimal(100), average_cost=Decimal(10))
            session.add(row)
            session.commit()
            spare = row.id

    event.listen(engine, "after_cursor_execute", interleave)
    try:
        with sessionmaker(bind=engine)() as session:
            with pytest.raises(LifecycleRevisionConflictError, match="取锁时变化"):
                select_portfolio_drawdown_command(session, _request(ids))
            session.rollback()
    finally:
        event.remove(engine, "after_cursor_execute", interleave)
    assert changed


@pytest.mark.parametrize("corruption", ["foreign_lifecycle", "wrong_symbol", "foreign_intent", "foreign_position"])
def test_inconsistent_ownership_rejects_whole_scope(operation_db, corruption):
    engine, ids = _setup(operation_db)
    with sessionmaker(bind=engine)() as session:
        if corruption == "wrong_symbol":
            session.get(SuggestedOrder, ids["sell"]).symbol = "000002.SZ"
        elif corruption == "foreign_intent":
            session.get(SuggestedOrder, ids["sell"]).intent_id = ids["intents"][0]
        elif corruption == "foreign_position":
            session.get(PortfolioPosition, ids["position"]).symbol = "000002.SZ"
        else:
            foreign = Portfolio(name=f"foreign-{uuid.uuid4().hex}")
            session.add(foreign)
            session.flush()
            owner = session.get(PositionLifecycleState, ids["lifecycles"][1])
            foreign_lifecycle = PositionLifecycleState(
                portfolio_id=foreign.id, market=owner.market, symbol=owner.symbol,
                strategy_version_id=owner.strategy_version_id,
                lifecycle_policy_version_id=owner.lifecycle_policy_version_id,
                phase="ENTRY_PENDING", state_version=1, target_shares=Decimal(100))
            session.add(foreign_lifecycle)
            session.flush()
            session.get(SuggestedOrder, ids["unbound"][0]).lifecycle_id = foreign_lifecycle.id
        session.commit()
        with pytest.raises(LifecycleInvalidStateError, match="归属|证券"):
            select_portfolio_drawdown_command(session, _request(ids))


def test_empty_portfolio_and_unmanaged_position_do_not_claim_live_history(operation_db):
    engine, _ = _setup(operation_db)
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        portfolio = Portfolio(name=f"empty-{uuid.uuid4().hex}")
        session.add(portfolio)
        session.commit()
        request = PortfolioDrawdownCommandRequest(portfolio.id, date(2026, 9, 24), "empty")
        selected = select_portfolio_drawdown_command(session, request)
        assert selected.manifest() == {"steps": [], "unbound_orders": []}
        assert not selected.has_history_participants and selected.positions == ()
        session.rollback()
        session.add(PortfolioPosition(portfolio_id=portfolio.id, market="CN", symbol="000002.SZ",
                                      quantity=Decimal(100), average_cost=Decimal(10)))
        session.commit()
        selected = select_portfolio_drawdown_command(session, request)
        assert not selected.has_history_participants and len(selected.positions) == 1


def test_missing_portfolio_rejected(operation_db):
    _, engine = operation_db
    with sessionmaker(bind=engine)() as session, pytest.raises(LifecycleNotFoundError):
        select_portfolio_drawdown_command(session, PortfolioDrawdownCommandRequest(
            uuid.uuid4(), date(2026, 9, 24), "missing"))


@pytest.mark.parametrize("field,value", [
    ("portfolio_id", "not-uuid"), ("valuation_date", "2026-09-24"),
    ("valuation_date", datetime(2026, 9, 24, tzinfo=timezone.utc)),
    ("request_key", " "), ("request_key", "a" * 129), ("request_key", None),
])
def test_request_rejects_malformed_identity_and_date(field, value):
    values = {"portfolio_id": uuid.uuid4(), "valuation_date": date(2026, 9, 24), "request_key": "request"}
    values[field] = value
    with pytest.raises(FillValidationError):
        PortfolioDrawdownCommandRequest(**values)
