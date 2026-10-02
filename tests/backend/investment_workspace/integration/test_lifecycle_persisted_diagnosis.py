# test-catalog-begin
# {
#   "purpose": "投资工作区 / lifecycle_persisted_diagnosis（持仓生命周期）：Positive local replay remains provisional in the isolated quant test DB.",
#   "keywords": [
#     "投资工作区",
#     "每日",
#     "成交",
#     "交易意图",
#     "持仓生命周期",
#     "重放",
#     "止损",
#     "lifecycle_persisted_diagnosis",
#     "daily",
#     "fill",
#     "intent",
#     "lifecycle",
#     "replay",
#     "stop"
#   ],
#   "covers": [
#     "backend/modules/investment_workspace/application/account_ledger_store.py",
#     "backend/modules/investment_workspace/application/account_observations.py",
#     "backend/modules/investment_workspace/application/fill_evidence.py",
#     "backend/modules/investment_workspace/application/fill_posting_stage.py",
#     "backend/modules/investment_workspace/application/fill_reports.py",
#     "backend/modules/investment_workspace/application/fill_reviews.py",
#     "backend/modules/investment_workspace/application/reconciliation.py",
#     "backend/modules/investment_workspace/infrastructure/models.py",
#     "backend/modules/quant_strategy/application/lifecycle_persisted_diagnosis.py",
#     "backend/modules/quant_strategy/application/lifecycle_persisted_terminal.py",
#     "backend/modules/quant_strategy/application/position_lifecycle_manager.py",
#     "backend/modules/quant_strategy/infrastructure/lifecycle_models.py",
#     "backend/modules/quant_strategy/infrastructure/models.py"
#   ],
#   "environment": [
#     "db"
#   ]
# }
# test-catalog-end

"""Positive local replay remains provisional in the isolated quant test DB."""

from tests.backend.investment_workspace.support.lifecycle_persisted_diagnosis import (
    FIRST_DAY,
    SECOND_DAY,
    FIRST_AT,
    SECOND_AT,
    CAPTURED_AT,
    _reviewed_fill,
)

from tests.support.python.paths import PROJECT_ROOT

import hashlib
import base64
from concurrent.futures import ThreadPoolExecutor
import hmac
import json
from pathlib import Path
import time
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from backend.modules.investment_workspace.application.account_ledger_store import AccountLedgerStore
from backend.modules.investment_workspace.application.account_observations import AccountObservationService
from backend.modules.investment_workspace.application.fill_evidence import AccountFillEvidenceService
from backend.modules.investment_workspace.application.fill_posting_stage import AccountFillPostingStage
from backend.modules.investment_workspace.application.fill_reports import AccountFillReportService
from backend.modules.investment_workspace.application.fill_reviews import AccountFillReviewService, review_payload
from backend.modules.investment_workspace.application.reconciliation import AccountObservation
from backend.modules.investment_workspace.infrastructure.models import Portfolio, PortfolioPosition
from backend.modules.quant_strategy.application.lifecycle_persisted_diagnosis import diagnose_persisted_lifecycle
from backend.modules.quant_strategy.application.lifecycle_persisted_terminal import diagnose_persisted_terminal_marker
from backend.modules.quant_strategy.application.position_lifecycle_manager import LifecycleStateInput
from backend.modules.quant_strategy.application.position_lifecycle_manager import PositionLifecycleManager
from backend.modules.quant_strategy.infrastructure.lifecycle_models import (
    FillLifecycleVersionStep, LifecyclePolicyVersion, OrderFillEvent,
    PositionDailyFact, PositionIntent,
    PositionLifecycleState, SuggestedOrder,
)
from backend.modules.quant_strategy.infrastructure.models import QuantStrategy, QuantStrategyVersion






def test_complete_local_fill_intent_and_daily_stream_reaches_provisional_diff(env, monkeypatch):
    factory = env["session_factory"]
    portfolio_id = uuid.uuid4()
    review_key = b"local-replay-review-key-32-bytes!!"
    monkeypatch.setenv("LIVEPROFIT_ACCOUNT_FILL_REVIEW_KEYS", json.dumps({
        "replay-reviewer": base64.b64encode(review_key).decode(),
    }))
    with factory() as session:
        portfolio = Portfolio(
            id=portfolio_id, name=f"replay-{portfolio_id}",
            version=1, total_assets=Decimal(1000),
            available_cash=Decimal(1000))
        position = PortfolioPosition(
            id=uuid.uuid4(), portfolio_id=portfolio_id, market="CN",
            symbol="000001.SZ", quantity=Decimal(0),
            average_cost=Decimal(0))
        strategy = QuantStrategy(id=uuid.uuid4(), name=f"replay-{uuid.uuid4().hex}")
        policy = LifecyclePolicyVersion(
            id=uuid.uuid4(), policy_key=f"replay-{uuid.uuid4().hex}",
            version_no=1, status="PUBLISHED", required_fields=[], config={},
            content_hash="a" * 64)
        session.add_all((portfolio, position, strategy, policy))
        session.flush()
        version = QuantStrategyVersion(
            id=uuid.uuid4(), strategy_id=strategy.id, version_no=1,
            status="PUBLISHED", source_code="def strategy(context): return {}",
            source_hash="b" * 64, lifecycle_policy_version_id=policy.id)
        session.add(version)
        session.flush()
        observation, _ = AccountObservationService(session).record(
            AccountObservation(
                portfolio_id=portfolio_id, trade_date=date(2026, 9, 20),
                captured_at=datetime(2026, 9, 20, 8, tzinfo=timezone.utc),
                cash="1000", holdings=(), complete_holdings=True,
                source_ref="opening-empty"), source_type="MANUAL_IMPORT")
        AccountLedgerStore(session).establish_baseline(portfolio_id, observation.id)
        first_order = SuggestedOrder(
            id=uuid.uuid4(), portfolio_id=portfolio_id, market="CN",
            symbol="000001.SZ", side="BUY", quantity=Decimal(5),
            filled_quantity=Decimal(0), limit_price=Decimal(10),
            reason_code="INITIAL_ENTRY", status="PROPOSED", revision=1)
        session.add(first_order)
        session.flush()
        first = _reviewed_fill(
            session, portfolio_id=portfolio_id, order=first_order,
            day=FIRST_DAY, executed_at=FIRST_AT,
            source_ref="first", review_key=review_key)
        session.refresh(position)
        assert position.quantity == Decimal(5)
        assert position.average_cost == Decimal("10.2")
        lifecycle = PositionLifecycleState(
            id=uuid.uuid4(), portfolio_id=portfolio_id, position_id=position.id,
            market="CN", symbol="000001.SZ", strategy_version_id=version.id,
            lifecycle_policy_version_id=policy.id,
            initial_fill_id=first.id, initial_fill_price=Decimal(10),
            initial_stop_price=Decimal(9), risk_capacity_shares=Decimal(1000),
            target_exposure_pct=Decimal("0.5"), target_shares=Decimal(5),
            profit_take_price=Decimal(12), state_version=1, phase="ACTIVE")
        session.add(lifecycle)
        session.flush()
        intent = PositionIntent(
            id=uuid.uuid4(), lifecycle_id=lifecycle.id, trade_date=SECOND_DAY,
            target_shares=Decimal(10), reason_code="TEMPLATE_CONFIRM_ADD",
            state_version=1, status="ACTIVE", revision=1)
        session.add(intent)
        session.flush()
        second_order = SuggestedOrder(
            id=uuid.uuid4(), portfolio_id=portfolio_id, position_id=position.id,
            lifecycle_id=lifecycle.id, intent_id=intent.id,
            market="CN", symbol="000001.SZ", side="BUY",
            quantity=Decimal(5), filled_quantity=Decimal(0),
            limit_price=Decimal(10), reason_code="TEMPLATE_CONFIRM_ADD",
            status="PROPOSED", revision=1)
        session.add(second_order)
        session.flush()
        second = _reviewed_fill(
            session, portfolio_id=portfolio_id, order=second_order,
            day=SECOND_DAY, executed_at=SECOND_AT,
            source_ref="second", review_key=review_key)
        assert second.intent_id_at_fill == intent.id
        session.refresh(lifecycle)
        session.refresh(position)
        session.refresh(portfolio)
        assert lifecycle.state_version == 2
        assert position.quantity == Decimal(10)
        assert position.average_cost == Decimal("10.2")
        assert portfolio.available_cash == Decimal(898)
        fact = {
            "is_suspended": False, "close": "10.5", "high": "10.6",
            "ma5": "10.3", "prev_ma5": "10.0", "ma20": "10.2",
            "prev_ma20": "10.1", "ma60": "9.5", "macd": "0.3",
            "prev_macd": "0.2",
        }
        canonical = json.dumps(fact, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=False)
        session.add(PositionDailyFact(
            id=uuid.uuid4(), lifecycle_id=lifecycle.id, trade_date=SECOND_DAY,
            price_basis="raw", data_as_of=datetime(
                2026, 9, 22, 8, tzinfo=timezone.utc),
            input_payload=fact, input_hash=hashlib.sha256(
                canonical.encode("utf-8")).hexdigest(), planning_result=None,
            rule_version="fixture", state_version_before=2,
            state_version_after=2, final_target_shares=Decimal(10)))
        session.commit()
        lifecycle_id = lifecycle.id

    with factory() as session:
        diagnosed = diagnose_persisted_lifecycle(
            session, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id,
            baseline_as_of=datetime(2026, 9, 21, 1, tzinfo=timezone.utc),
            baseline_quantity=Decimal(0), baseline_total_cost=Decimal(0),
            baseline_source_ref="declared-empty-baseline",
            seed_state=LifecycleStateInput(
                template_id="ma_trend_cross_v1", initial_fill_price=Decimal(10),
                initial_stop_price=Decimal(9), risk_capacity_shares=Decimal(1000),
                target_exposure_pct=Decimal("0.5"),
                profit_take_price=Decimal(12)),
            seed_target_shares=Decimal(5), seed_trailing=None,
            seed_expectation=None, management_policy=None,
            calendar_dates=(SECOND_DAY,), calendar_source_ref="declared-calendar",
            corporate_action_dates=(),
            corporate_action_source_ref="declared-action-coverage")
        assert diagnosed.status == "UNKNOWN"
        assert diagnosed.diff is None
        assert "DAILY_POLICY_VERSION_ADVANCE_MISMATCH" in diagnosed.issues
        assert "BASELINE_SOURCE_UNCERTIFIED" in diagnosed.issues
        assert not session.new and not session.dirty

    exit_day = date(2026, 9, 23)
    with factory() as session:
        lifecycle = session.get(PositionLifecycleState, lifecycle_id)
        exit_intent = PositionIntent(
            id=uuid.uuid4(), lifecycle_id=lifecycle_id, trade_date=exit_day,
            target_shares=Decimal(0), reason_code="INITIAL_STOP_LOSS",
            state_version=lifecycle.state_version, status="ACTIVE", revision=1)
        session.add(exit_intent)
        session.flush()
        exit_order = SuggestedOrder(
            id=uuid.uuid4(), portfolio_id=portfolio_id,
            position_id=lifecycle.position_id, lifecycle_id=lifecycle_id,
            intent_id=exit_intent.id, market="CN", symbol="000001.SZ",
            side="SELL", quantity=Decimal(10), filled_quantity=Decimal(0),
            limit_price=Decimal(10), reason_code="INITIAL_STOP_LOSS",
            status="PROPOSED", revision=1)
        session.add(exit_order)
        session.flush()
        exit_fill = _reviewed_fill(
            session, portfolio_id=portfolio_id, order=exit_order,
            day=exit_day, executed_at=datetime(2026, 9, 23, 2, tzinfo=timezone.utc),
            source_ref="exit", review_key=review_key, quantity="10")
        assert exit_fill.intent_id_at_fill == exit_intent.id
        session.refresh(lifecycle)
        assert lifecycle.phase == "CLOSED" and lifecycle.closed_at is not None
        cancelled_order_id = uuid.uuid4()
        session.add(SuggestedOrder(
            id=cancelled_order_id, portfolio_id=portfolio_id,
            position_id=position.id, lifecycle_id=lifecycle_id,
            market="CN", symbol="000001.SZ", side="BUY",
            quantity=Decimal(1), filled_quantity=Decimal(0),
            limit_price=Decimal(10), reason_code="CANCELLED_REENTRY",
            status="CANCELLED", revision=1))
        session.commit()

    with factory() as session:
        terminal = diagnose_persisted_terminal_marker(
            session, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id,
            baseline_as_of=datetime(2026, 9, 21, 1, tzinfo=timezone.utc),
            baseline_quantity=Decimal(0), baseline_total_cost=Decimal(0),
            baseline_source_ref="declared-empty-baseline",
            corporate_action_dates=(),
            corporate_action_source_ref="declared-action-coverage")
        assert terminal.status == "PROVISIONAL_MATCH"
        assert terminal.closing_fill_event_id == exit_fill.id
        assert "LOCAL_TERMINAL_SOURCE_UNCERTIFIED" in terminal.issues
        assert "BASELINE_SOURCE_UNCERTIFIED" in terminal.issues
        with factory() as writer:
            writer.execute(text("SET LOCAL lock_timeout = '100ms'"))
            with pytest.raises(DBAPIError, match="lock timeout"):
                writer.execute(text("""UPDATE suggested_orders SET status = 'PROPOSED'
                    WHERE id = :id"""), {"id": cancelled_order_id})
            writer.rollback()
        action_unknown = diagnose_persisted_terminal_marker(
            session, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id,
            baseline_as_of=datetime(2026, 9, 21, 1, tzinfo=timezone.utc),
            baseline_quantity=Decimal(0), baseline_total_cost=Decimal(0),
            baseline_source_ref="declared-empty-baseline",
            corporate_action_dates=(SECOND_DAY,),
            corporate_action_source_ref="declared-action-coverage")
        assert action_unknown.status == "UNKNOWN"
        assert "TERMINAL_CORPORATE_ACTION_COVERAGE_UNKNOWN" in action_unknown.issues
        assert not session.new and not session.dirty

    with factory() as session:
        drifted = session.get(PortfolioPosition, position.id)
        drifted.quantity = Decimal(1)
        session.commit()
    with factory() as session:
        drift = diagnose_persisted_terminal_marker(
            session, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id,
            baseline_as_of=datetime(2026, 9, 21, 1, tzinfo=timezone.utc),
            baseline_quantity=Decimal(0), baseline_total_cost=Decimal(0),
            baseline_source_ref="declared-empty-baseline",
            corporate_action_dates=(),
            corporate_action_source_ref="declared-action-coverage")
        assert drift.status == "UNKNOWN"
        assert "CURRENT_TERMINAL_HOLDING_NONZERO" in drift.issues
    with factory() as session:
        session.get(PortfolioPosition, position.id).quantity = Decimal(0)
        pending_order_id = uuid.uuid4()
        session.add(SuggestedOrder(
            id=pending_order_id, portfolio_id=portfolio_id,
            position_id=position.id, lifecycle_id=lifecycle_id,
            market="CN", symbol="000001.SZ", side="BUY",
            quantity=Decimal(1), filled_quantity=Decimal(0),
            limit_price=Decimal(10), reason_code="UNRECONCILED_REENTRY",
            status="PROPOSED", revision=1))
        session.commit()
    with factory() as session:
        pending = diagnose_persisted_terminal_marker(
            session, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id,
            baseline_as_of=datetime(2026, 9, 21, 1, tzinfo=timezone.utc),
            baseline_quantity=Decimal(0), baseline_total_cost=Decimal(0),
            baseline_source_ref="declared-empty-baseline",
            corporate_action_dates=(),
            corporate_action_source_ref="declared-action-coverage")
        assert pending.status == "UNKNOWN"
        assert any(issue.startswith("TERMINAL_ACTIVE_ORDER:") for issue in pending.issues)
    with factory() as session:
        stale_order = session.get(SuggestedOrder, pending_order_id)
        stale_order.status = "CANCELLED"
        session.add(PositionIntent(
            id=uuid.uuid4(), lifecycle_id=lifecycle_id,
            trade_date=date(2026, 9, 24), target_shares=Decimal(1),
            reason_code="TEMPLATE_CONFIRM_ADD", state_version=3,
            status="ACTIVE", revision=1))
        session.commit()
    with factory() as session:
        unresolved = diagnose_persisted_terminal_marker(
            session, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id,
            baseline_as_of=datetime(2026, 9, 21, 1, tzinfo=timezone.utc),
            baseline_quantity=Decimal(0), baseline_total_cost=Decimal(0),
            baseline_source_ref="declared-empty-baseline",
            corporate_action_dates=(),
            corporate_action_source_ref="declared-action-coverage")
        assert unresolved.status == "UNKNOWN"
        assert any(issue.startswith("TERMINAL_ACTIVE_INTENT:") for issue in unresolved.issues)


def test_real_stop_loss_day_and_later_signed_terminal_fill_reach_provisional_match(
        env, monkeypatch):
    factory = env["session_factory"]
    portfolio_id = uuid.uuid4()
    review_key = b"local-replay-review-key-32-bytes!!"
    monkeypatch.setenv("LIVEPROFIT_ACCOUNT_FILL_REVIEW_KEYS", json.dumps({
        "replay-reviewer": base64.b64encode(review_key).decode(),
    }))
    terminal_day = date(2026, 9, 23)
    seed = LifecycleStateInput(
        template_id="ma_trend_cross_v1", initial_fill_price=Decimal(10),
        initial_stop_price=Decimal(9), risk_capacity_shares=Decimal(200),
        target_exposure_pct=Decimal("0.5"), profit_take_price=Decimal(12))
    with factory() as session:
        portfolio = Portfolio(
            id=portfolio_id, name=f"real-terminal-{portfolio_id}",
            version=1, total_assets=Decimal(5000),
            available_cash=Decimal(5000))
        position = PortfolioPosition(
            id=uuid.uuid4(), portfolio_id=portfolio_id, market="CN",
            symbol="000001.SZ", quantity=Decimal(0), average_cost=Decimal(0))
        strategy = QuantStrategy(
            id=uuid.uuid4(), name=f"real-terminal-{uuid.uuid4().hex}")
        policy = LifecyclePolicyVersion(
            id=uuid.uuid4(), policy_key=f"real-terminal-{uuid.uuid4().hex}",
            version_no=1, status="PUBLISHED", required_fields=[],
            config={"template_id": "ma_trend_cross_v1"},
            content_hash="e" * 64)
        session.add_all((portfolio, position, strategy, policy))
        session.flush()
        version = QuantStrategyVersion(
            id=uuid.uuid4(), strategy_id=strategy.id, version_no=1,
            status="PUBLISHED", source_code="def strategy(context): return {}",
            source_hash="f" * 64, lifecycle_policy_version_id=policy.id,
            template_id="ma_trend_cross_v1")
        session.add(version)
        session.flush()
        observation, _ = AccountObservationService(session).record(
            AccountObservation(
                portfolio_id=portfolio_id, trade_date=date(2026, 9, 20),
                captured_at=datetime(2026, 9, 20, 8, tzinfo=timezone.utc),
                cash="5000", holdings=(), complete_holdings=True,
                source_ref="real-terminal-opening"),
            source_type="MANUAL_IMPORT")
        AccountLedgerStore(session).establish_baseline(portfolio_id, observation.id)
        buy_order = SuggestedOrder(
            id=uuid.uuid4(), portfolio_id=portfolio_id, market="CN",
            symbol="000001.SZ", side="BUY", quantity=Decimal(100),
            filled_quantity=Decimal(0), limit_price=Decimal(10),
            reason_code="INITIAL_ENTRY", status="PROPOSED", revision=1)
        session.add(buy_order)
        session.flush()
        buy = _reviewed_fill(
            session, portfolio_id=portfolio_id, order=buy_order,
            day=FIRST_DAY, executed_at=FIRST_AT,
            source_ref="real-terminal-buy", review_key=review_key,
            quantity="100", captured_at=FIRST_AT.replace(hour=3))
        session.refresh(position)
        assert position.quantity == Decimal(100)
        lifecycle = PositionLifecycleState(
            id=uuid.uuid4(), portfolio_id=portfolio_id,
            position_id=position.id, market="CN", symbol="000001.SZ",
            strategy_version_id=version.id,
            lifecycle_policy_version_id=policy.id,
            initial_fill_id=buy.id, initial_fill_price=Decimal(10),
            initial_stop_price=Decimal(9), risk_capacity_shares=Decimal(200),
            target_exposure_pct=Decimal("0.5"), target_shares=Decimal(100),
            profit_take_price=Decimal(12), state_version=1, phase="ACTIVE")
        session.add(lifecycle)
        session.flush()
        stop_fact = {
            "is_suspended": False, "close": "8.8", "high": "9.0",
            "ma5": "10.3", "prev_ma5": "10.0", "ma20": "10.2",
            "prev_ma20": "10.1", "ma60": "9.5", "macd": "0.3",
            "prev_macd": "0.2",
        }
        daily, intent, generated_order, decision = (
            PositionLifecycleManager(session).process_day(
                lifecycle.id, trade_date=SECOND_DAY, fact=stop_fact,
                data_as_of=datetime(2026, 9, 22, 8, tzinfo=timezone.utc),
                commit=False))
        assert decision is not None and decision.data_available
        assert (decision.reason_code, decision.target_shares,
                decision.phase) == ("INITIAL_STOP_LOSS", Decimal(0), "EXIT_PENDING")
        assert (daily.state_version_before,
                daily.state_version_after) == (1, 2)
        assert daily.final_target_shares == Decimal(0)
        assert intent is not None and intent.trade_date == SECOND_DAY
        assert (intent.reason_code, intent.target_shares,
                intent.state_version) == ("INITIAL_STOP_LOSS", Decimal(0), 2)
        assert generated_order is None  # No authenticated sellability in stop_fact.
        assert lifecycle.last_processed_trade_date == SECOND_DAY
        session.commit()
        lifecycle_id, position_id, intent_id = lifecycle.id, position.id, intent.id

    with factory() as session:
        sell_order = SuggestedOrder(
            id=uuid.uuid4(), portfolio_id=portfolio_id, position_id=position_id,
            lifecycle_id=lifecycle_id, intent_id=intent_id,
            market="CN", symbol="000001.SZ", side="SELL",
            quantity=Decimal(100), filled_quantity=Decimal(0),
            limit_price=Decimal(10), reason_code="INITIAL_STOP_LOSS",
            status="PROPOSED", revision=1)
        session.add(sell_order)
        session.flush()
        sell = _reviewed_fill(
            session, portfolio_id=portfolio_id, order=sell_order,
            day=terminal_day,
            executed_at=datetime(2026, 9, 23, 2, tzinfo=timezone.utc),
            source_ref="real-terminal-sell", review_key=review_key,
            quantity="100")
        step = session.get(FillLifecycleVersionStep, sell.id)
        assert step is not None
        assert (step.lifecycle_id, step.version_before,
                step.version_after, step.origin) == (
                    lifecycle_id, 2, 3, "LOCAL_CAUSAL")
        lifecycle = session.get(PositionLifecycleState, lifecycle_id)
        session.refresh(lifecycle)
        assert lifecycle.phase == "CLOSED" and lifecycle.closed_at is not None
        assert lifecycle.last_processed_trade_date == SECOND_DAY
        assert session.get(PortfolioPosition, position_id).quantity == Decimal(0)
        assert session.get(PositionIntent, intent_id).status == "COMPLETED"
        assert session.scalar(text("""SELECT count(*) FROM position_daily_facts
            WHERE lifecycle_id = :id AND trade_date = :day"""), {
                "id": lifecycle_id, "day": terminal_day,
            }) == 0
        session.commit()

    def diagnose(session, *, terminal_dates=(SECOND_DAY, terminal_day)):
        return diagnose_persisted_lifecycle(
            session, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id,
            baseline_as_of=datetime(2026, 9, 21, 1, tzinfo=timezone.utc),
            baseline_quantity=Decimal(0), baseline_total_cost=Decimal(0),
            baseline_source_ref="declared-empty-baseline",
            seed_state=seed, seed_target_shares=Decimal(100),
            seed_trailing=None, seed_expectation=None,
            management_policy=None, calendar_dates=(SECOND_DAY,),
            calendar_source_ref="declared-daily-calendar",
            corporate_action_dates=(),
            corporate_action_source_ref="declared-action-coverage",
            terminal_open_session_date=terminal_day,
            terminal_calendar_dates=terminal_dates,
            terminal_calendar_source_ref="declared-terminal-calendar")

    with factory() as session:
        diagnosed = diagnose(session)
        assert diagnosed.status == "PROVISIONAL_MATCH", diagnosed.issues
        assert diagnosed.diff is not None
        fields = {field.field: field for field in diagnosed.diff.fields}
        assert fields["lifecycle.phase"].status == "MATCH"
        assert fields["lifecycle.phase"].expected == "CLOSED"
        assert fields["lifecycle.closed_at"].status == "MATCH"
        assert fields["lifecycle.last_processed_trade_date"].status == "MATCH"
        assert fields["lifecycle.last_processed_trade_date"].expected == SECOND_DAY
        assert fields["lifecycle.target_shares"].expected == Decimal(0)
        assert fields["lifecycle.target_exposure_pct"].expected == Decimal(0)
        assert fields["position.quantity"].status == "MATCH"
        assert "position.average_cost" not in fields
        assert "TERMINAL_CALENDAR_SOURCE_UNCERTIFIED" in diagnosed.issues
        assert "TERMINAL_FILL_SOURCE_UNCERTIFIED" in diagnosed.issues
        assert "BASELINE_SOURCE_UNCERTIFIED" in diagnosed.issues
        assert not session.new and not session.dirty

        missing_terminal_session = diagnose(session, terminal_dates=(SECOND_DAY,))
        assert missing_terminal_session.status == "UNKNOWN"
        assert missing_terminal_session.diff is None
        assert "TERMINAL_OPEN_SESSION_CALENDAR_UNKNOWN" in missing_terminal_session.issues
        assert not session.new and not session.dirty

    # The frozen original definition survives a later status revision, but a
    # superseded closing intent is no longer a completed terminal decision.
    with factory() as session:
        closing_intent = session.get(PositionIntent, intent_id)
        closing_intent.status = "SUPERSEDED"
        closing_intent.revision += 1
        session.commit()
    with factory() as session:
        stale_closing_intent = diagnose(session)
        assert stale_closing_intent.status == "UNKNOWN"
        assert stale_closing_intent.diff is None
        assert "TERMINAL_CLOSING_INTENT_NOT_COMPLETED" in stale_closing_intent.issues
        assert not session.new and not session.dirty


def test_real_stop_loss_and_two_signed_partial_sells_close_on_later_session(
        env, monkeypatch):
    factory = env["session_factory"]
    portfolio_id = uuid.uuid4()
    review_key = b"local-replay-review-key-32-bytes!!"
    monkeypatch.setenv("LIVEPROFIT_ACCOUNT_FILL_REVIEW_KEYS", json.dumps({
        "replay-reviewer": base64.b64encode(review_key).decode(),
    }))
    terminal_day = date(2026, 9, 23)
    seed = LifecycleStateInput(
        template_id="ma_trend_cross_v1", initial_fill_price=Decimal(10),
        initial_stop_price=Decimal(9), risk_capacity_shares=Decimal(200),
        target_exposure_pct=Decimal("0.5"), profit_take_price=Decimal(12))
    with factory() as session:
        portfolio = Portfolio(
            id=portfolio_id, name=f"partial-terminal-{portfolio_id}",
            version=1, total_assets=Decimal(5000),
            available_cash=Decimal(5000))
        position = PortfolioPosition(
            id=uuid.uuid4(), portfolio_id=portfolio_id, market="CN",
            symbol="000001.SZ", quantity=Decimal(0), average_cost=Decimal(0))
        strategy = QuantStrategy(
            id=uuid.uuid4(), name=f"partial-terminal-{uuid.uuid4().hex}")
        policy = LifecyclePolicyVersion(
            id=uuid.uuid4(), policy_key=f"partial-terminal-{uuid.uuid4().hex}",
            version_no=1, status="PUBLISHED", required_fields=[],
            config={"template_id": "ma_trend_cross_v1"},
            content_hash="c" * 64)
        session.add_all((portfolio, position, strategy, policy))
        session.flush()
        version = QuantStrategyVersion(
            id=uuid.uuid4(), strategy_id=strategy.id, version_no=1,
            status="PUBLISHED", source_code="def strategy(context): return {}",
            source_hash="d" * 64, lifecycle_policy_version_id=policy.id,
            template_id="ma_trend_cross_v1")
        session.add(version)
        session.flush()
        observation, _ = AccountObservationService(session).record(
            AccountObservation(
                portfolio_id=portfolio_id, trade_date=date(2026, 9, 20),
                captured_at=datetime(2026, 9, 20, 8, tzinfo=timezone.utc),
                cash="5000", holdings=(), complete_holdings=True,
                source_ref="partial-terminal-opening"),
            source_type="MANUAL_IMPORT")
        AccountLedgerStore(session).establish_baseline(portfolio_id, observation.id)
        buy_order = SuggestedOrder(
            id=uuid.uuid4(), portfolio_id=portfolio_id, market="CN",
            symbol="000001.SZ", side="BUY", quantity=Decimal(100),
            filled_quantity=Decimal(0), limit_price=Decimal(10),
            reason_code="INITIAL_ENTRY", status="PROPOSED", revision=1)
        session.add(buy_order)
        session.flush()
        buy = _reviewed_fill(
            session, portfolio_id=portfolio_id, order=buy_order,
            day=FIRST_DAY, executed_at=FIRST_AT,
            source_ref="partial-terminal-buy", review_key=review_key,
            quantity="100", captured_at=FIRST_AT.replace(hour=3))
        session.refresh(position)
        assert position.quantity == Decimal(100)
        lifecycle = PositionLifecycleState(
            id=uuid.uuid4(), portfolio_id=portfolio_id,
            position_id=position.id, market="CN", symbol="000001.SZ",
            strategy_version_id=version.id,
            lifecycle_policy_version_id=policy.id,
            initial_fill_id=buy.id, initial_fill_price=Decimal(10),
            initial_stop_price=Decimal(9), risk_capacity_shares=Decimal(200),
            target_exposure_pct=Decimal("0.5"), target_shares=Decimal(100),
            profit_take_price=Decimal(12), state_version=1, phase="ACTIVE")
        session.add(lifecycle)
        session.flush()
        stop_fact = {
            "is_suspended": False, "close": "8.8", "high": "9.0",
            "ma5": "10.3", "prev_ma5": "10.0", "ma20": "10.2",
            "prev_ma20": "10.1", "ma60": "9.5", "macd": "0.3",
            "prev_macd": "0.2",
        }
        daily, intent, generated_order, decision = (
            PositionLifecycleManager(session).process_day(
                lifecycle.id, trade_date=SECOND_DAY, fact=stop_fact,
                data_as_of=datetime(2026, 9, 22, 8, tzinfo=timezone.utc),
                commit=False))
        assert decision is not None and decision.data_available
        assert (decision.reason_code, decision.target_shares,
                decision.phase) == ("INITIAL_STOP_LOSS", Decimal(0), "EXIT_PENDING")
        assert (daily.state_version_before,
                daily.state_version_after) == (1, 2)
        assert daily.final_target_shares == Decimal(0)
        assert (intent.reason_code, intent.target_shares,
                intent.state_version) == ("INITIAL_STOP_LOSS", Decimal(0), 2)
        assert generated_order is None
        assert lifecycle.last_processed_trade_date == SECOND_DAY
        session.commit()
        lifecycle_id, position_id, intent_id = lifecycle.id, position.id, intent.id

    def diagnose(session):
        return diagnose_persisted_lifecycle(
            session, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id,
            baseline_as_of=datetime(2026, 9, 21, 1, tzinfo=timezone.utc),
            baseline_quantity=Decimal(0), baseline_total_cost=Decimal(0),
            baseline_source_ref="declared-empty-baseline",
            seed_state=seed, seed_target_shares=Decimal(100),
            seed_trailing=None, seed_expectation=None,
            management_policy=None, calendar_dates=(SECOND_DAY,),
            calendar_source_ref="declared-daily-calendar",
            corporate_action_dates=(),
            corporate_action_source_ref="declared-action-coverage",
            terminal_open_session_date=terminal_day,
            terminal_calendar_dates=(SECOND_DAY, terminal_day),
            terminal_calendar_source_ref="declared-terminal-calendar")

    with factory() as session:
        sell_order = SuggestedOrder(
            id=uuid.uuid4(), portfolio_id=portfolio_id, position_id=position_id,
            lifecycle_id=lifecycle_id, intent_id=intent_id,
            market="CN", symbol="000001.SZ", side="SELL",
            quantity=Decimal(100), filled_quantity=Decimal(0),
            limit_price=Decimal(10), reason_code="INITIAL_STOP_LOSS",
            status="PROPOSED", revision=1)
        session.add(sell_order)
        session.flush()
        partial = _reviewed_fill(
            session, portfolio_id=portfolio_id, order=sell_order,
            day=terminal_day,
            executed_at=datetime(2026, 9, 23, 2, tzinfo=timezone.utc),
            source_ref="partial-terminal-sell-40", review_key=review_key,
            quantity="40")
        step = session.get(FillLifecycleVersionStep, partial.id)
        assert step is not None
        assert (step.lifecycle_id, step.version_before,
                step.version_after, step.origin) == (
                    lifecycle_id, 2, 3, "LOCAL_CAUSAL")
        session.refresh(sell_order)
        lifecycle = session.get(PositionLifecycleState, lifecycle_id)
        session.refresh(lifecycle)
        assert (sell_order.status, sell_order.filled_quantity,
                sell_order.revision) == ("PARTIALLY_FILLED", Decimal(40), 2)
        assert (lifecycle.state_version, lifecycle.phase,
                lifecycle.closed_at) == (3, "EXIT_PENDING", None)
        assert session.get(PositionIntent, intent_id).status == "EXECUTING"
        assert session.get(PortfolioPosition, position_id).quantity == Decimal(60)
        assert session.get(Portfolio, portfolio_id).available_cash == Decimal(4398)
        assert session.scalar(text("""SELECT count(*) FROM position_daily_facts
            WHERE lifecycle_id = :id AND trade_date = :day"""), {
                "id": lifecycle_id, "day": terminal_day,
            }) == 0
        session.commit()
        sell_order_id, partial_id = sell_order.id, partial.id

    with factory() as session:
        pending = diagnose(session)
        assert pending.status == "UNKNOWN"
        assert pending.diff is None
        assert any(issue.startswith("INTENT_COMPLETION_NOT_PROVEN:")
                   for issue in pending.issues)
        assert not session.new and not session.dirty

    with factory() as session:
        sell_order = session.get(SuggestedOrder, sell_order_id)
        final = _reviewed_fill(
            session, portfolio_id=portfolio_id, order=sell_order,
            day=terminal_day,
            executed_at=datetime(2026, 9, 23, 3, tzinfo=timezone.utc),
            source_ref="partial-terminal-sell-60", review_key=review_key,
            quantity="60", expected_order_revision=2)
        assert final.id != partial_id
        assert final.intent_id_at_fill == intent_id
        step = session.get(FillLifecycleVersionStep, final.id)
        assert step is not None
        assert (step.lifecycle_id, step.version_before,
                step.version_after, step.origin) == (
                    lifecycle_id, 3, 4, "LOCAL_CAUSAL")
        session.refresh(sell_order)
        lifecycle = session.get(PositionLifecycleState, lifecycle_id)
        session.refresh(lifecycle)
        assert (sell_order.status, sell_order.filled_quantity,
                sell_order.revision) == ("FILLED", Decimal(100), 3)
        assert lifecycle.state_version == 4
        assert lifecycle.phase == "CLOSED" and lifecycle.closed_at is not None
        assert lifecycle.last_processed_trade_date == SECOND_DAY
        assert session.get(PositionIntent, intent_id).status == "COMPLETED"
        assert session.get(PortfolioPosition, position_id).quantity == Decimal(0)
        assert session.get(Portfolio, portfolio_id).available_cash == Decimal(4997)
        assert session.scalar(text("""SELECT count(*) FROM position_daily_facts
            WHERE lifecycle_id = :id AND trade_date = :day"""), {
                "id": lifecycle_id, "day": terminal_day,
            }) == 0
        session.commit()
        final_id = final.id

    with factory() as session:
        diagnosed = diagnose(session)
        assert diagnosed.status == "PROVISIONAL_MATCH", diagnosed.issues
        assert diagnosed.diff is not None
        fields = {field.field: field for field in diagnosed.diff.fields}
        assert fields["lifecycle.phase"].status == "MATCH"
        assert fields["lifecycle.phase"].expected == "CLOSED"
        assert fields["lifecycle.closed_at"].status == "MATCH"
        assert fields["lifecycle.last_processed_trade_date"].status == "MATCH"
        assert fields["lifecycle.last_processed_trade_date"].expected == SECOND_DAY
        assert fields["lifecycle.target_shares"].expected == Decimal(0)
        assert fields["lifecycle.target_exposure_pct"].expected == Decimal(0)
        assert fields["position.quantity"].status == "MATCH"
        assert "position.average_cost" not in fields
        assert "TERMINAL_FILL_SOURCE_UNCERTIFIED" in diagnosed.issues
        assert "BASELINE_SOURCE_UNCERTIFIED" in diagnosed.issues
        assert not session.new and not session.dirty
        marker = diagnose_persisted_terminal_marker(
            session, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id,
            baseline_as_of=datetime(2026, 9, 21, 1, tzinfo=timezone.utc),
            baseline_quantity=Decimal(0), baseline_total_cost=Decimal(0),
            baseline_source_ref="declared-empty-baseline",
            corporate_action_dates=(),
            corporate_action_source_ref="declared-action-coverage")
        assert marker.status == "PROVISIONAL_MATCH", marker.issues
        assert marker.closing_fill_event_id == final_id
        assert not session.new and not session.dirty


@pytest.mark.parametrize(("cross_day", "late_report"), [
    (False, False), (True, False), (True, True),
])
def test_round_lot_fills_feed_real_daily_planning_and_local_replay(
        env, monkeypatch, tmp_path, cross_day, late_report):
    factory = env["session_factory"]
    portfolio_id = uuid.uuid4()
    initial_day = date(2026, 9, 18) if cross_day else FIRST_DAY
    initial_at = datetime(2026, 9, 18, 2, tzinfo=timezone.utc) if cross_day else FIRST_AT
    opening_day = date(2026, 9, 17) if cross_day else date(2026, 9, 20)
    risk_capacity = Decimal(200) if cross_day else Decimal(400)
    initial_target = Decimal(100) if cross_day else Decimal(200)
    review_key = b"local-replay-review-key-32-bytes!!"
    monkeypatch.setenv("LIVEPROFIT_ACCOUNT_FILL_REVIEW_KEYS", json.dumps({
        "replay-reviewer": base64.b64encode(review_key).decode(),
    }))
    with factory() as session:
        portfolio = Portfolio(
            id=portfolio_id, name=f"round-lot-{portfolio_id}",
            version=1, total_assets=Decimal(5000),
            available_cash=Decimal(5000))
        position = PortfolioPosition(
            id=uuid.uuid4(), portfolio_id=portfolio_id, market="CN",
            symbol="000001.SZ", quantity=Decimal(0), average_cost=Decimal(0))
        strategy = QuantStrategy(id=uuid.uuid4(), name=f"round-lot-{uuid.uuid4().hex}")
        policy = LifecyclePolicyVersion(
            id=uuid.uuid4(), policy_key=f"round-lot-{uuid.uuid4().hex}",
            version_no=1, status="PUBLISHED", required_fields=[],
            config={"template_id": "ma_trend_cross_v1"}, content_hash="c" * 64)
        session.add_all((portfolio, position, strategy, policy))
        session.flush()
        version = QuantStrategyVersion(
            id=uuid.uuid4(), strategy_id=strategy.id, version_no=1,
            status="PUBLISHED", source_code="def strategy(context): return {}",
            source_hash="d" * 64, lifecycle_policy_version_id=policy.id,
            template_id="ma_trend_cross_v1")
        session.add(version)
        session.flush()
        observation, _ = AccountObservationService(session).record(
            AccountObservation(
                portfolio_id=portfolio_id, trade_date=opening_day,
                captured_at=datetime(opening_day.year, opening_day.month,
                                     opening_day.day, 8, tzinfo=timezone.utc),
                cash="5000", holdings=(), complete_holdings=True,
                source_ref="round-lot-opening"), source_type="MANUAL_IMPORT")
        AccountLedgerStore(session).establish_baseline(portfolio_id, observation.id)
        first_order = SuggestedOrder(
            id=uuid.uuid4(), portfolio_id=portfolio_id, market="CN",
            symbol="000001.SZ", side="BUY", quantity=Decimal(100),
            filled_quantity=Decimal(0), limit_price=Decimal(10),
            reason_code="INITIAL_ENTRY", status="PROPOSED", revision=1)
        session.add(first_order)
        session.flush()
        first = _reviewed_fill(
            session, portfolio_id=portfolio_id, order=first_order,
            day=initial_day, executed_at=initial_at,
            source_ref="round-first", review_key=review_key, quantity="100",
            captured_at=initial_at.replace(hour=3))
        lifecycle = PositionLifecycleState(
            id=uuid.uuid4(), portfolio_id=portfolio_id, position_id=position.id,
            market="CN", symbol="000001.SZ", strategy_version_id=version.id,
            lifecycle_policy_version_id=policy.id,
            initial_fill_id=first.id, initial_fill_price=Decimal(10),
            initial_stop_price=Decimal(9), risk_capacity_shares=risk_capacity,
            target_exposure_pct=Decimal("0.5"), target_shares=initial_target,
            profit_take_price=Decimal(12), state_version=1, phase="ACTIVE")
        session.add(lifecycle)
        session.flush()
        fact = {
            "is_suspended": False, "close": "10.5", "high": "10.6",
            "ma5": "10.3", "prev_ma5": "10.0", "ma20": "10.2",
            "prev_ma20": "10.1", "ma60": "9.5", "macd": "0.3",
            "prev_macd": "0.2",
        }
        if cross_day:
            first_daily, intent, first_day_order, first_decision = (
                PositionLifecycleManager(session).process_day(
                    lifecycle.id, trade_date=FIRST_DAY, fact=fact,
                    data_as_of=datetime(2026, 9, 21, 8, tzinfo=timezone.utc),
                    defer_buy=True, commit=False))
            assert first_decision is not None and first_decision.data_available
            assert first_day_order is None and intent is not None
            assert first_daily.state_version_before == 1
            assert first_daily.state_version_after == 2
        else:
            intent = PositionIntent(
                id=uuid.uuid4(), lifecycle_id=lifecycle.id, trade_date=SECOND_DAY,
                target_shares=Decimal(200), reason_code="TEMPLATE_CONFIRM_ADD",
                state_version=1, status="ACTIVE", revision=1)
            session.add(intent)
            session.flush()
        second_order = SuggestedOrder(
            id=uuid.uuid4(), portfolio_id=portfolio_id, position_id=position.id,
            lifecycle_id=lifecycle.id, intent_id=intent.id, market="CN",
            symbol="000001.SZ", side="BUY", quantity=Decimal(100),
            filled_quantity=Decimal(0), limit_price=Decimal(10),
            reason_code=intent.reason_code, status="PROPOSED", revision=1)
        session.add(second_order)
        session.flush()
        second = _reviewed_fill(
            session, portfolio_id=portfolio_id, order=second_order,
            day=FIRST_DAY if late_report else SECOND_DAY,
            executed_at=FIRST_AT if late_report else SECOND_AT,
            source_ref="round-second", review_key=review_key, quantity="100",
            captured_at=datetime(2026, 9, 22, 3, tzinfo=timezone.utc))
        assert session.get(FillLifecycleVersionStep, first.id) is None
        frozen = session.get(FillLifecycleVersionStep, second.id)
        assert frozen is not None
        expected_before = 2 if cross_day else 1
        assert (frozen.lifecycle_id, frozen.version_before,
                frozen.version_after, frozen.origin) == (
                    lifecycle.id, expected_before, expected_before + 1,
                    "LOCAL_CAUSAL")
        second_fact = ({**fact, "prev_ma5": "10.2"}
                       if cross_day else fact)
        daily, new_intent, new_order, decision = PositionLifecycleManager(session).process_day(
            lifecycle.id, trade_date=SECOND_DAY, fact=second_fact,
            data_as_of=datetime(2026, 9, 22, 8, tzinfo=timezone.utc),
            commit=False)
        assert decision is not None and decision.data_available
        assert new_intent is None or new_intent.id == intent.id, (
            decision.reason_code, decision.target_shares,
            new_intent.reason_code, new_intent.target_shares,
            intent.reason_code, intent.target_shares)
        assert new_order is None
        assert daily.state_version_before == expected_before + 1
        assert daily.state_version_after == expected_before + 2
        session.refresh(lifecycle)
        session.refresh(position)
        assert lifecycle.last_processed_trade_date == SECOND_DAY
        assert position.quantity == Decimal(200)
        assert position.average_cost == Decimal("10.01")
        session.commit()
        lifecycle_id = lifecycle.id

    with factory() as session:
        diagnosed = diagnose_persisted_lifecycle(
            session, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id,
            baseline_as_of=datetime(2026, 9, 18 if cross_day else 21, 1,
                                    tzinfo=timezone.utc),
            baseline_quantity=Decimal(0), baseline_total_cost=Decimal(0),
            baseline_source_ref="declared-empty-baseline",
            seed_state=LifecycleStateInput(
                template_id="ma_trend_cross_v1", initial_fill_price=Decimal(10),
                initial_stop_price=Decimal(9), risk_capacity_shares=risk_capacity,
                target_exposure_pct=Decimal("0.5"),
                profit_take_price=Decimal(12)),
            seed_target_shares=initial_target, seed_trailing=None,
            seed_expectation=None, management_policy=None,
            calendar_dates=(FIRST_DAY, SECOND_DAY) if cross_day else (SECOND_DAY,),
            calendar_source_ref="declared-calendar",
            corporate_action_dates=(),
            corporate_action_source_ref="declared-action-coverage")
        if late_report:
            assert diagnosed.status == "UNKNOWN" and diagnosed.diff is None
            assert any(issue.startswith("FILL_DAILY_VERSION_ORDER_CONFLICT:")
                       for issue in diagnosed.issues)
        else:
            assert diagnosed.status == "PROVISIONAL_MATCH", tuple(
                issue for issue in diagnosed.issues if "UNCERTIFIED" not in issue)
            assert diagnosed.diff is not None
            assert "BASELINE_SOURCE_UNCERTIFIED" in diagnosed.issues
            assert "FILL_RESOLUTION_SOURCE_UNCERTIFIED" in diagnosed.issues
            if cross_day:
                assert "VERSION_CHAIN_SOURCE_UNCERTIFIED" in diagnosed.issues

    # A direct fill INSERT at an already advanced lifecycle version is not
    # evidence that this fill caused that version transition.
    with factory() as session:
        direct_id = uuid.uuid4()
        session.execute(text("""INSERT INTO order_fill_events
            (id, order_id, portfolio_id, event_type, quantity, fill_price,
             fill_trade_date, source, idempotency_key)
            VALUES (:id, :order_id, :portfolio_id, 'CONFIRM', 1, 10,
                    '2026-09-22', 'MANUAL', :key)"""), {
            "id": direct_id, "order_id": second_order.id,
            "portfolio_id": portfolio_id, "key": f"unattributed-{direct_id}",
        })
        uncaused = session.get(FillLifecycleVersionStep, direct_id)
        assert uncaused is not None
        assert uncaused.origin == "UNATTRIBUTED"
        assert uncaused.version_before is None and uncaused.version_after is None
        session.rollback()

    # Downgrade must wait on the lifecycle table before taking a conflicting
    # fill-table lock; the writer holds a lifecycle row and then inserts fill.
    project_root = PROJECT_ROOT
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "backend" / "migrations"))
    config.cmd_opts = type("CmdOpts", (), {
        "x": ["db_url=" + factory.kw["bind"].url.render_as_string(
            hide_password=False)], "name": None})()
    export_path = tmp_path / "fill_version_steps.jsonl"
    monkeypatch.setenv("LIVEPROFIT_FILL_VERSION_STEPS_EXPORT_PATH", str(export_path))
    # This race exercises 0048 -> 0047 specifically. Remove the later 0049
    # uniqueness constraint before acquiring the writer's lifecycle lock;
    # otherwise its ALTER TABLE waits first and masks the 0048 lock order.
    command.downgrade(config, "0048")
    # Release the row lock before joining the downgrade worker on failures.
    with ThreadPoolExecutor(max_workers=1) as pool, factory() as writer:
        writer.execute(text("""SELECT id FROM position_lifecycle_states
            WHERE id = :id FOR UPDATE"""), {"id": lifecycle_id})
        future = pool.submit(command.downgrade, config, "0047")
        deadline = time.monotonic() + 3
        while True:
            with factory() as observer:
                waiting = observer.scalar(text("""SELECT count(*)
                    FROM pg_stat_activity WHERE datname = current_database()
                      AND wait_event_type = 'Lock'
                      AND query LIKE 'LOCK TABLE position_lifecycle_states%'"""))
            if waiting:
                break
            assert not future.done(), "downgrade completed before lifecycle lock contention"
            assert time.monotonic() < deadline, "downgrade never waited for lifecycle lock"
            time.sleep(0.02)
        writer.execute(text("SET LOCAL lock_timeout = '500ms'"))
        race_id = uuid.uuid4()
        writer.execute(text("""INSERT INTO order_fill_events
            (id, order_id, portfolio_id, event_type, quantity, fill_price,
             fill_trade_date, source, idempotency_key)
            VALUES (:id, :order_id, :portfolio_id, 'CONFIRM', 1, 10,
                    '2026-09-22', 'MANUAL', :key)"""), {
            "id": race_id, "order_id": second_order.id,
            "portfolio_id": portfolio_id, "key": f"downgrade-race-{race_id}",
        })
        writer.commit()
        future.result(timeout=10)
    assert export_path.is_file()
    assert str(race_id) in export_path.read_text(encoding="utf-8")
