# test-catalog-begin
# {
#   "purpose": "投资工作区 / account_ledger_store（账户、账本）",
#   "keywords": [
#     "投资工作区",
#     "现金",
#     "费用",
#     "流程",
#     "幂等",
#     "不可变历史",
#     "账户账本",
#     "来源观测",
#     "投资组合",
#     "仓位管理",
#     "持久化",
#     "account_ledger_store",
#     "cash",
#     "fee",
#     "flow",
#     "idempotent",
#     "immutable",
#     "ledger",
#     "observation",
#     "portfolio",
#     "position",
#     "store"
#   ],
#   "covers": [
#     "backend/modules/investment_workspace/application/account_ledger_store.py",
#     "backend/modules/investment_workspace/application/account_observations.py",
#     "backend/modules/investment_workspace/application/errors.py",
#     "backend/modules/investment_workspace/application/portfolios.py",
#     "backend/modules/investment_workspace/application/reconciliation.py",
#     "backend/modules/investment_workspace/domain/values.py",
#     "backend/modules/investment_workspace/infrastructure/account_ledger_models.py",
#     "backend/modules/investment_workspace/infrastructure/models.py",
#     "backend/modules/investment_workspace/infrastructure/repositories.py"
#   ],
#   "environment": [
#     "db"
#   ]
# }
# test-catalog-end

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from backend.modules.investment_workspace.application.account_ledger_store import AccountLedgerStore
from backend.modules.investment_workspace.application.account_observations import AccountObservationService
from backend.modules.investment_workspace.application.errors import PortfolioLedgerRequiredError
from backend.modules.investment_workspace.application.portfolios import PortfolioService
from backend.modules.investment_workspace.application.reconciliation import AccountObservation, ObservedHolding
from backend.modules.investment_workspace.domain.values import InstrumentRef
from backend.modules.investment_workspace.infrastructure.account_ledger_models import (
    AccountLedgerBaselineRow, AccountLedgerMovementRow,
)
from backend.modules.investment_workspace.infrastructure.models import Portfolio
from backend.modules.investment_workspace.infrastructure.repositories import SqlAlchemyWorkspaceUnitOfWork


CAPTURED = datetime(2026, 9, 28, 8, tzinfo=timezone.utc)


def _seed(factory, *, complete=True):
    with factory() as session:
        portfolio_id = uuid.uuid4()
        session.add(Portfolio(id=portfolio_id, name=f"ledger-{portfolio_id}", version=1,
                              total_assets=Decimal("1000"), available_cash=Decimal("1000")))
        session.commit()
    with factory() as session:
        observation, _ = AccountObservationService(session).record(AccountObservation(
            portfolio_id=portfolio_id, trade_date=CAPTURED.date(), captured_at=CAPTURED,
            cash="1000", holdings=(ObservedHolding("CN", "000001.SZ", "10", "10"),),
            complete_holdings=complete, source_ref="opening-snapshot",
        ), source_type="MANUAL_IMPORT")
        session.commit()
        return portfolio_id, observation.id


def test_baseline_and_cash_flow_are_immutable_idempotent_and_do_not_mutate_projection(env):
    factory = env["session_factory"]
    portfolio_id, observation_id = _seed(factory)
    with factory() as session:
        store = AccountLedgerStore(session)
        baseline, created = store.establish_baseline(portfolio_id, observation_id)
        assert created
        flow, created = store.append_cash_flow(
            portfolio_id, effective_at=CAPTURED + timedelta(days=1), amount="50.00",
            source_type="MANUAL_ENTRY", source_ref="deposit-001")
        assert created
        session.commit()
        baseline_id, flow_id = baseline.id, flow.id
    with factory() as session:
        store = AccountLedgerStore(session)
        replay, created = store.establish_baseline(portfolio_id, observation_id)
        assert not created and replay.id == baseline_id
        replay, created = store.append_cash_flow(
            portfolio_id, effective_at=CAPTURED + timedelta(days=1), amount="50",
            source_type="MANUAL_ENTRY", source_ref="deposit-001")
        assert not created and replay.id == flow_id
        balance = store.replay_current(portfolio_id)
        assert balance.cash == Decimal("1050")
        assert balance.holdings == (("CN", "000001.SZ", Decimal("10")),)
        assert balance.external_flow_total == Decimal("50")
        assert session.scalar(select(func.count()).select_from(AccountLedgerMovementRow)) == 1
        portfolio = session.get(Portfolio, portfolio_id)
        assert portfolio.available_cash == Decimal("1000") and portfolio.version == 1
    with factory() as session:
        with pytest.raises(DBAPIError, match="account ledger history is immutable"):
            session.execute(text("UPDATE account_ledger_movements SET cash_delta=999 WHERE id=:id"),
                            {"id": flow_id})
        session.rollback()


def test_cash_flow_correction_replays_and_reports_negative_balance(env):
    factory = env["session_factory"]
    portfolio_id, observation_id = _seed(factory)
    with factory() as session:
        store = AccountLedgerStore(session)
        store.establish_baseline(portfolio_id, observation_id)
        first, _ = store.append_cash_flow(
            portfolio_id, effective_at=CAPTURED + timedelta(days=1), amount="100",
            source_type="MANUAL_ENTRY", source_ref="flow-1")
        session.commit()
        first_id = first.id
    with factory() as session:
        replacement, created = AccountLedgerStore(session).append_cash_flow(
            portfolio_id, effective_at=CAPTURED + timedelta(days=1), amount="80",
            source_type="MANUAL_ENTRY", source_ref="flow-1-correction",
            supersedes_id=first_id, reason="入金回单更正")
        assert created and replacement.supersedes_id == first_id
        session.commit()
    with factory() as session:
        balance = AccountLedgerStore(session).replay_current(portfolio_id)
        assert balance.cash == Decimal("1080")
        assert balance.effective_event_ids == (replacement.id,)
        historical_at = CAPTURED + timedelta(days=1, hours=1)
        store = AccountLedgerStore(session)
        known_then = store.replay_at(portfolio_id, effective_as_of=historical_at,
                                     recorded_as_of=first.recorded_at)
        known_now = store.replay_at(portfolio_id, effective_as_of=historical_at,
                                    recorded_as_of=replacement.recorded_at)
        assert known_then.cash == Decimal("1100")
        assert known_then.external_flow_event_ids == (first_id,)
        assert known_now.cash == Decimal("1080")
        assert known_now.external_flow_event_ids == (replacement.id,)
        baseline = session.scalar(select(AccountLedgerBaselineRow).where(
            AccountLedgerBaselineRow.portfolio_id == portfolio_id))
        with pytest.raises(ValueError, match="baseline was not available"):
            store.replay_at(portfolio_id, effective_as_of=historical_at,
                            recorded_as_of=baseline.recorded_at - timedelta(microseconds=1))
    with factory() as session:
        with pytest.raises(ValueError, match="replay changed"):
            AccountLedgerStore(session).append_cash_flow(
                portfolio_id, effective_at=CAPTURED + timedelta(days=1), amount="90",
                source_type="MANUAL_ENTRY", source_ref="flow-1-correction",
                supersedes_id=first_id, reason="入金回单更正")
        session.rollback()
        with pytest.raises(ValueError, match="forked"):
            AccountLedgerStore(session).append_cash_flow(
                portfolio_id, effective_at=CAPTURED + timedelta(days=1), amount="70",
                source_type="MANUAL_ENTRY", source_ref="flow-1-fork",
                supersedes_id=first_id, reason="重复修订")
        session.rollback()
        anomalous, created = AccountLedgerStore(session).append_cash_flow(
            portfolio_id, effective_at=CAPTURED + timedelta(days=1), amount="-2000",
            source_type="MANUAL_ENTRY", source_ref="withdraw-too-much")
        assert created
        session.commit()
    with factory() as session:
        balance = AccountLedgerStore(session).replay_current(portfolio_id)
        assert balance.cash == Decimal("-920")
        assert balance.issues == (f"NEGATIVE_CASH:{anomalous.id}",)
        assert session.scalar(select(func.count()).select_from(AccountLedgerMovementRow)) == 3


def test_incomplete_or_cross_portfolio_observation_cannot_be_baseline(env):
    factory = env["session_factory"]
    partial_id, partial_observation = _seed(factory, complete=False)
    other_id, other_observation = _seed(factory)
    with factory() as session:
        store = AccountLedgerStore(session)
        with pytest.raises(ValueError, match="complete observation"):
            store.establish_baseline(partial_id, partial_observation)
        with pytest.raises(ValueError, match="does not belong"):
            store.establish_baseline(partial_id, other_observation)
        assert session.scalar(select(func.count()).select_from(AccountLedgerBaselineRow)) == 0


def test_actual_trade_with_explicit_fee_replays_without_changing_portfolio(env):
    factory = env["session_factory"]
    portfolio_id, observation_id = _seed(factory)
    with factory() as session:
        store = AccountLedgerStore(session)
        store.establish_baseline(portfolio_id, observation_id)
        buy, created = store.append_trade(
            portfolio_id, effective_at=CAPTURED + timedelta(days=1),
            market="CN", symbol="000001.SZ", quantity_delta="5", fill_price="10",
            fee="1", source_type="MANUAL_ENTRY", source_ref="fill-buy-1")
        assert created and buy.cash_delta == Decimal("-51")
        sell, created = store.append_trade(
            portfolio_id, effective_at=CAPTURED + timedelta(days=1, hours=1),
            market="CN", symbol="000001.SZ", quantity_delta="-3", fill_price="12",
            fee="0.5", source_type="MANUAL_ENTRY", source_ref="fill-sell-1")
        assert created and sell.cash_delta == Decimal("35.5")
        session.commit()
    with factory() as session:
        store = AccountLedgerStore(session)
        balance = store.replay_current(portfolio_id)
        assert balance.cash == Decimal("984.5")
        assert balance.holdings == (("CN", "000001.SZ", Decimal("12")),)
        assert balance.external_flow_total == 0
        repeat, created = store.append_trade(
            portfolio_id, effective_at=CAPTURED + timedelta(days=1),
            market="CN", symbol="000001.SZ", quantity_delta="5.0000", fill_price="10.00",
            fee="1.0000", source_type="MANUAL_ENTRY", source_ref="fill-buy-1")
        assert not created and repeat.id == buy.id
        assert session.get(Portfolio, portfolio_id).available_cash == Decimal("1000")


def test_trade_requires_explicit_fee_and_preserves_overfilled_fact_as_anomaly(env):
    factory = env["session_factory"]
    portfolio_id, observation_id = _seed(factory)
    with factory() as session:
        store = AccountLedgerStore(session)
        store.establish_baseline(portfolio_id, observation_id)
        with pytest.raises(ValueError, match="numeric"):
            store.append_trade(
                portfolio_id, effective_at=CAPTURED + timedelta(days=1),
                market="CN", symbol="000001.SZ", quantity_delta="1", fill_price="10",
                fee=None, source_type="MANUAL_ENTRY", source_ref="fee-missing")
        oversell, created = store.append_trade(
            portfolio_id, effective_at=CAPTURED + timedelta(days=1),
            market="CN", symbol="000001.SZ", quantity_delta="-11", fill_price="10",
            fee="0", source_type="MANUAL_ENTRY", source_ref="oversell")
        assert created
        overbuy, created = store.append_trade(
            portfolio_id, effective_at=CAPTURED + timedelta(days=1, hours=1),
            market="CN", symbol="000001.SZ", quantity_delta="200", fill_price="10",
            fee="0", source_type="MANUAL_ENTRY", source_ref="overbuy")
        assert created
        session.commit()
    with factory() as session:
        balance = AccountLedgerStore(session).replay_current(portfolio_id)
        assert balance.holdings == (("CN", "000001.SZ", Decimal("199")),)
        assert balance.cash == Decimal("-890")
        assert balance.issues == (
            f"NEGATIVE_HOLDING:CN:000001.SZ:{oversell.id}",
            f"NEGATIVE_CASH:{overbuy.id}",
        )


def test_trade_void_is_immutable_idempotent_and_diagnostic_only(env):
    factory = env["session_factory"]
    portfolio_id, observation_id = _seed(factory)
    with factory() as session:
        store = AccountLedgerStore(session)
        store.establish_baseline(portfolio_id, observation_id)
        trade, _ = store.append_trade(
            portfolio_id, effective_at=CAPTURED + timedelta(days=1),
            market="CN", symbol="000001.SZ", quantity_delta="5",
            fill_price="10", fee="1", source_type="MANUAL_ENTRY", source_ref="void-target")
        session.commit()
        trade_id = trade.id
    with factory() as session:
        store = AccountLedgerStore(session)
        void, created = store.void_trade(
            portfolio_id, trade_movement_id=trade_id, reason="券商撤销成交",
            source_type="MANUAL_ENTRY", source_ref="void-target-revoked")
        assert created and void.effective_at == CAPTURED + timedelta(days=1)
        session.commit()
        void_id = void.id
    with factory() as session:
        store = AccountLedgerStore(session)
        balance = store.replay_current(portfolio_id)
        assert balance.cash == Decimal("1000")
        assert balance.holdings == (("CN", "000001.SZ", Decimal("10")),)
        assert balance.effective_event_ids == (void_id,)
        assert balance.external_flow_total == 0
        assert session.get(AccountLedgerMovementRow, trade_id).fee == Decimal("1")
        assert session.get(Portfolio, portfolio_id).available_cash == Decimal("1000")
        repeat, created = store.void_trade(
            portfolio_id, trade_movement_id=trade_id, reason="券商撤销成交",
            source_type="MANUAL_ENTRY", source_ref="void-target-revoked")
        assert not created and repeat.id == void_id
        with pytest.raises(ValueError, match="changed content"):
            store.void_trade(portfolio_id, trade_movement_id=trade_id,
                             reason="不同原因", source_type="MANUAL_ENTRY",
                             source_ref="void-target-revoked")
        with pytest.raises(ValueError, match="forked"):
            store.void_trade(portfolio_id, trade_movement_id=trade_id,
                             reason="重复撤销", source_type="MANUAL_ENTRY",
                             source_ref="void-target-fork")
        assert session.scalar(select(func.count()).select_from(AccountLedgerMovementRow)) == 2


def test_trade_void_database_rejects_invalid_shape_and_predecessor(env):
    factory = env["session_factory"]
    portfolio_id, observation_id = _seed(factory)
    with factory() as session:
        store = AccountLedgerStore(session)
        store.establish_baseline(portfolio_id, observation_id)
        trade, _ = store.append_trade(
            portfolio_id, effective_at=CAPTURED + timedelta(days=1),
            market="CN", symbol="000001.SZ", quantity_delta="1",
            fill_price="10", fee="1", source_type="MANUAL_ENTRY", source_ref="db-void-trade")
        flow, _ = store.append_cash_flow(
            portfolio_id, effective_at=CAPTURED + timedelta(days=1), amount="5",
            source_type="MANUAL_ENTRY", source_ref="db-void-flow")
        session.commit()
        sql = text("""INSERT INTO account_ledger_movements
            (id, portfolio_id, baseline_id, recorded_at, effective_at, kind,
             cash_delta, holdings_delta, fill_price, fee, supersedes_id, reason,
             source_type, source_ref, payload_sha256)
            SELECT :id, portfolio_id, baseline_id, clock_timestamp(),
                   effective_at + (:hours * interval '1 hour'), 'VOID',
                   :cash, '[]'::jsonb, NULL, :fee, :predecessor, '测试撤销',
                   'MANUAL_ENTRY', :source_ref, repeat('0', 64)
            FROM account_ledger_movements WHERE id = :trade_id""")
        cases = (
            (flow.id, 0, "0", "0", "void-bad-predecessor", "matching trade"),
            (trade.id, 1, "0", "0", "void-bad-time", "matching trade"),
            (trade.id, 0, "1", "0", "void-bad-cash", "ck_account_ledger_movement_values"),
            (trade.id, 0, "0", "1", "void-bad-fee", "ck_account_ledger_movement_values"),
        )
        for predecessor, hours, cash, fee, source_ref, message in cases:
            with pytest.raises(DBAPIError, match=message):
                with session.begin_nested():
                    session.execute(sql, {
                        "id": uuid.uuid4(), "trade_id": trade.id,
                        "predecessor": predecessor, "hours": hours,
                        "cash": cash, "fee": fee, "source_ref": source_ref,
                    })
        assert session.scalar(select(func.count()).select_from(AccountLedgerMovementRow)) == 2


def test_company_action_split_dividend_and_correction_are_not_external_flows(env):
    factory = env["session_factory"]
    portfolio_id, observation_id = _seed(factory)
    with factory() as session:
        store = AccountLedgerStore(session)
        store.establish_baseline(portfolio_id, observation_id)
        split, created = store.append_corporate_action(
            portfolio_id, effective_at=CAPTURED + timedelta(days=1),
            cash_delta="0", holdings_delta=[("CN", "000001.SZ", "10")],
            source_type="ISSUER_NOTICE", source_ref="split-1")
        assert created
        dividend, created = store.append_corporate_action(
            portfolio_id, effective_at=CAPTURED + timedelta(days=1, hours=1),
            cash_delta="5", holdings_delta=[],
            source_type="ISSUER_NOTICE", source_ref="dividend-1")
        assert created
        session.commit()
        split_id, dividend_id = split.id, dividend.id
    with factory() as session:
        store = AccountLedgerStore(session)
        replay, created = store.append_corporate_action(
            portfolio_id, effective_at=CAPTURED + timedelta(days=1),
            cash_delta="0.0000", holdings_delta=[("CN", "000001.SZ", "10.0000")],
            source_type="ISSUER_NOTICE", source_ref="split-1")
        assert not created and replay.id == split_id
        corrected, created = store.append_corporate_action(
            portfolio_id, effective_at=CAPTURED + timedelta(days=1),
            cash_delta="0", holdings_delta=[("CN", "000001.SZ", "9")],
            source_type="ISSUER_NOTICE", source_ref="split-1-corrected",
            supersedes_id=split_id, reason="notice corrected")
        assert created
        with pytest.raises(ValueError, match="duplicate"):
            store.append_corporate_action(
                portfolio_id, effective_at=CAPTURED + timedelta(days=1),
                cash_delta="0", holdings_delta=[
                    ("CN", "000001.SZ", "1"), ("CN", "000001.SZ", "1")],
                source_type="ISSUER_NOTICE", source_ref="bad-duplicate")
        with pytest.raises(ValueError, match="must change"):
            store.append_corporate_action(
                portfolio_id, effective_at=CAPTURED + timedelta(days=1),
                cash_delta="0", holdings_delta=[],
                source_type="ISSUER_NOTICE", source_ref="bad-empty")
        session.commit()
    with factory() as session:
        balance = AccountLedgerStore(session).replay_current(portfolio_id)
        assert balance.cash == Decimal("1005")
        assert balance.holdings == (("CN", "000001.SZ", Decimal("19")),)
        assert balance.external_flow_total == 0
        assert set(balance.effective_event_ids) == {dividend_id, corrected.id}
        assert session.get(Portfolio, portfolio_id).available_cash == Decimal("1000")


def test_reasoned_adjustment_is_diagnostic_and_not_an_external_flow(env):
    factory = env["session_factory"]
    portfolio_id, observation_id = _seed(factory)
    with factory() as session:
        store = AccountLedgerStore(session)
        store.establish_baseline(portfolio_id, observation_id)
        adjustment, created = store.append_adjustment(
            portfolio_id, effective_at=CAPTURED + timedelta(days=1),
            cash_delta="-7", holdings_delta=[("CN", "000001.SZ", "-2")],
            reason="statement discrepancy", source_type="MANUAL_ENTRY", source_ref="adjust-1")
        assert created
        replay, created = store.append_adjustment(
            portfolio_id, effective_at=CAPTURED + timedelta(days=1),
            cash_delta="-7.0000", holdings_delta=[("CN", "000001.SZ", "-2.0000")],
            reason="statement discrepancy", source_type="MANUAL_ENTRY", source_ref="adjust-1")
        assert not created and replay.id == adjustment.id
        with pytest.raises(ValueError, match="reason"):
            store.append_adjustment(
                portfolio_id, effective_at=CAPTURED + timedelta(days=1),
                cash_delta="1", holdings_delta=[], reason=" ",
                source_type="MANUAL_ENTRY", source_ref="invalid-reason")
        with pytest.raises(ValueError, match="must change"):
            store.append_adjustment(
                portfolio_id, effective_at=CAPTURED + timedelta(days=1),
                cash_delta="0", holdings_delta=[], reason="empty",
                source_type="MANUAL_ENTRY", source_ref="empty-adjustment")
        with pytest.raises(ValueError, match="changed content"):
            store.append_adjustment(
                portfolio_id, effective_at=CAPTURED + timedelta(days=1),
                cash_delta="-8", holdings_delta=[("CN", "000001.SZ", "-2")],
                reason="statement discrepancy", source_type="MANUAL_ENTRY", source_ref="adjust-1")
        session.commit()
    with factory() as session:
        balance = AccountLedgerStore(session).replay_current(portfolio_id)
        assert balance.cash == Decimal("993")
        assert balance.holdings == (("CN", "000001.SZ", Decimal("8")),)
        assert balance.external_flow_total == 0
        assert session.get(Portfolio, portfolio_id).available_cash == Decimal("1000")


def test_legacy_direct_account_edits_are_blocked_after_baseline(env):
    factory = env["session_factory"]
    portfolio_id, observation_id = _seed(factory)
    with factory() as session:
        AccountLedgerStore(session).establish_baseline(portfolio_id, observation_id)
        session.commit()
    with SqlAlchemyWorkspaceUnitOfWork(factory) as uow:
        service = PortfolioService(uow)
        dto = service.get(portfolio_id)
        with pytest.raises(PortfolioLedgerRequiredError):
            service.upsert_position(portfolio_id, InstrumentRef("CN", "000001.SZ"),
                                    20, 10, expected_revision=dto.version)
        with pytest.raises(PortfolioLedgerRequiredError):
            service.remove_position(portfolio_id, InstrumentRef("CN", "000001.SZ"),
                                    expected_revision=dto.version)
        with pytest.raises(PortfolioLedgerRequiredError):
            service.update_account(
                portfolio_id, name=dto.name, total_assets=dto.total_assets,
                available_cash=dto.available_cash - 1,
                risk_per_trade_pct=dto.risk_per_trade_pct,
                min_risk_reward_ratio=dto.min_risk_reward_ratio,
                max_total_position_pct=dto.max_total_position_pct,
                max_single_stock_pct=dto.max_single_stock_pct,
                max_sector_pct=dto.max_sector_pct,
                max_portfolio_open_risk_pct=dto.max_portfolio_open_risk_pct,
                max_sector_open_risk_pct=dto.max_sector_open_risk_pct,
                max_daily_new_risk_pct=dto.max_daily_new_risk_pct,
                max_drawdown_pct=dto.max_drawdown_pct,
                max_daily_loss_pct=dto.max_daily_loss_pct,
                net_asset_value=dto.net_asset_value,
                peak_net_asset_value=dto.peak_net_asset_value,
                day_start_net_asset_value=dto.day_start_net_asset_value,
                risk_facts_as_of=dto.risk_facts_as_of,
                expected_version=dto.version,
            )
        assert service.get(portfolio_id).version == dto.version


def test_baseline_creation_serializes_with_legacy_position_edit(env):
    factory = env["session_factory"]
    portfolio_id, observation_id = _seed(factory)
    with factory() as establishing:
        AccountLedgerStore(establishing).establish_baseline(portfolio_id, observation_id)
        with SqlAlchemyWorkspaceUnitOfWork(factory) as competing:
            competing.session.execute(text("SET LOCAL lock_timeout = '100ms'"))
            with pytest.raises(DBAPIError, match="lock timeout"):
                PortfolioService(competing).upsert_position(
                    portfolio_id, InstrumentRef("CN", "000001.SZ"),
                    20, 10, expected_revision=1)
            competing.rollback()
        establishing.commit()
    with SqlAlchemyWorkspaceUnitOfWork(factory) as after_commit:
        with pytest.raises(PortfolioLedgerRequiredError):
            PortfolioService(after_commit).upsert_position(
                portfolio_id, InstrumentRef("CN", "000001.SZ"),
                20, 10, expected_revision=1)
