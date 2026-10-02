# test-catalog-begin
# {
#   "purpose": "投资工作区 / lifecycle_persisted_resolved_accounting（持仓生命周期）：3ao local accounting candidates in the isolated workspace database.",
#   "keywords": [
#     "投资工作区",
#     "费用",
#     "成交",
#     "持仓生命周期",
#     "收益",
#     "lifecycle_persisted_resolved_accounting",
#     "fee",
#     "fill",
#     "lifecycle",
#     "returns"
#   ],
#   "covers": [
#     "backend/modules/investment_workspace/application/fill_posting_stage.py",
#     "backend/modules/investment_workspace/application/fill_reports.py",
#     "backend/modules/investment_workspace/infrastructure/fill_report_models.py",
#     "backend/modules/investment_workspace/infrastructure/models.py",
#     "backend/modules/quant_strategy/application/lifecycle_persisted_resolved_accounting.py",
#     "backend/modules/quant_strategy/infrastructure/lifecycle_models.py",
#     "backend/modules/quant_strategy/infrastructure/models.py"
#   ],
#   "environment": [
#     "db"
#   ]
# }
# test-catalog-end

"""3ao local accounting candidates in the isolated workspace database."""

from tests.backend.investment_workspace.support.lifecycle_persisted_resolved_accounting import (
    _anchor_existing_fill,
    _accounting,
)

import base64
import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select, text

from backend.modules.investment_workspace.application.fill_posting_stage import AccountFillPostingStage
from backend.modules.investment_workspace.application.fill_reports import AccountFillReportService
from backend.modules.investment_workspace.infrastructure.fill_report_models import AccountFillPostingRow
from backend.modules.investment_workspace.infrastructure.models import PortfolioPosition
from backend.modules.quant_strategy.application.lifecycle_persisted_resolved_accounting import (
    load_local_resolved_accounting,
)
from backend.modules.quant_strategy.infrastructure.lifecycle_models import (
    LifecyclePolicyVersion, PositionIntent, PositionLifecycleState, SuggestedOrder,
)
from backend.modules.quant_strategy.infrastructure.models import QuantStrategy, QuantStrategyVersion
from tests.backend.investment_workspace.support.fill_posting_stage import BASELINE, CAPTURED, EXECUTED, SECRET, VOID_SECRET, _post_and_review_correction, _post_and_review_void, _seed
from tests.backend.investment_workspace.support.lifecycle_persisted_diagnosis import _reviewed_fill
from tests.backend.investment_workspace.support.lifecycle_revision_impact_surface import _historical_corrected_anchor






def test_historical_signed_prelink_correction_uses_only_terminal_economics(
        env, monkeypatch):
    factory = env["session_factory"]
    portfolio_id, lifecycle_id, initial_id = _historical_corrected_anchor(
        factory, monkeypatch)
    with factory() as session:
        replay = _accounting(session, portfolio_id, lifecycle_id)
        assert replay.status == "PROVISIONAL_ACCOUNTING", replay.issues
        assert replay.accounting is not None
        assert replay.accounting.quantity == Decimal(6)
        assert replay.accounting.total_cost == Decimal(60)
        assert replay.accounting.realized_pnl == Decimal("3.5")
        assert replay.superseded_fill_event_ids == (initial_id,)
        assert len(replay.effective_fill_event_ids) == 1
        assert replay.accounting.event_ids == replay.effective_fill_event_ids
        assert "BROKER_FILL_SET_UNCERTIFIED" in replay.issues
        assert "BASELINE_SOURCE_UNCERTIFIED" in replay.issues
        assert not session.new and not session.dirty and not session.deleted


def test_unrevised_signed_fill_uses_explicit_baseline(env, monkeypatch):
    factory = env["session_factory"]
    portfolio_id, _, report_id = _seed(factory, monkeypatch)
    with factory() as session:
        AccountFillPostingStage(session)._stage_reviewed_report(
            portfolio_id, report_id, expected_order_revision=1)
        session.commit()
    lifecycle_id, initial_id = _anchor_existing_fill(factory, portfolio_id, report_id)
    with factory() as session:
        replay = _accounting(session, portfolio_id, lifecycle_id)
        assert replay.status == "PROVISIONAL_ACCOUNTING", replay.issues
        assert replay.effective_fill_event_ids == (initial_id,)
        assert replay.superseded_fill_event_ids == ()
        assert replay.accounting.quantity == Decimal(5)
        assert replay.accounting.total_cost == Decimal(50)
        assert replay.accounting.realized_pnl == Decimal(-1)
        assert not session.new and not session.dirty and not session.deleted


def test_historical_signed_prelink_void_excludes_original_economics(env, monkeypatch):
    factory = env["session_factory"]
    portfolio_id, _, report_id, resolution_id = _post_and_review_void(
        factory, monkeypatch)
    with factory() as session:
        AccountFillPostingStage(session)._stage_reviewed_void(
            portfolio_id, resolution_id, expected_order_revision=2)
        session.commit()
    lifecycle_id, initial_id = _anchor_existing_fill(factory, portfolio_id, report_id)
    with factory() as session:
        replay = _accounting(session, portfolio_id, lifecycle_id)
        assert replay.status == "PROVISIONAL_ACCOUNTING", replay.issues
        assert replay.effective_fill_event_ids == ()
        assert replay.superseded_fill_event_ids == (initial_id,)
        assert replay.accounting.event_ids == ()
        assert replay.accounting.quantity == Decimal(10)
        assert replay.accounting.total_cost == Decimal(100)
        assert replay.accounting.realized_pnl == Decimal(0)
        assert not session.new and not session.dirty and not session.deleted


def test_signed_but_unapplied_correction_returns_no_partial_accounting(
        env, monkeypatch):
    factory = env["session_factory"]
    portfolio_id, _, report_id, _, _ = _post_and_review_correction(
        factory, monkeypatch)
    lifecycle_id, _ = _anchor_existing_fill(factory, portfolio_id, report_id)
    with factory() as session:
        replay = _accounting(session, portfolio_id, lifecycle_id)
        assert replay.status == "UNKNOWN"
        assert replay.accounting is None
        assert replay.effective_fill_event_ids == ()
        assert replay.superseded_fill_event_ids == ()
        assert "FILL_REVISION_REPORT_UNPOSTED" in replay.issues


def test_corrupt_booked_fee_returns_no_partial_accounting(env, monkeypatch):
    factory = env["session_factory"]
    portfolio_id, lifecycle_id, _ = _historical_corrected_anchor(
        factory, monkeypatch)
    with factory() as session:
        posting = session.scalar(select(AccountFillPostingRow).where(
            AccountFillPostingRow.portfolio_id == portfolio_id).order_by(
                AccountFillPostingRow.report_id).limit(1))
        session.execute(text("ALTER TABLE account_ledger_movements "
                             "DISABLE TRIGGER account_ledger_immutable"))
        session.execute(text("UPDATE account_ledger_movements SET fee=fee+1 "
                             "WHERE id=:id"), {"id": posting.ledger_movement_id})
        session.execute(text("ALTER TABLE account_ledger_movements "
                             "ENABLE TRIGGER account_ledger_immutable"))
        session.commit()
    with factory() as session:
        replay = _accounting(session, portfolio_id, lifecycle_id)
        assert replay.status == "UNKNOWN"
        assert replay.accounting is None
        assert replay.effective_fill_event_ids == ()
        assert "FILL_REVISION_POSTING_FACT_MISMATCH" in replay.issues


def test_invalid_or_late_baseline_clears_accounting(env, monkeypatch):
    factory = env["session_factory"]
    portfolio_id, lifecycle_id, _ = _historical_corrected_anchor(
        factory, monkeypatch)
    with factory() as session:
        late = _accounting(session, portfolio_id, lifecycle_id,
                           baseline_as_of=EXECUTED)
        assert late.status == "UNKNOWN" and late.accounting is None
        assert late.effective_fill_event_ids == ()
        assert "RESOLVED_ACCOUNTING_BASELINE_OVERLAPS_REPORT_HISTORY" in late.issues
        overflow = _accounting(session, portfolio_id, lifecycle_id,
                               baseline_as_of=datetime.min.replace(
                                   tzinfo=timezone(timedelta(hours=14))))
        assert overflow.status == "UNKNOWN" and overflow.accounting is None
        assert "RESOLVED_ACCOUNTING_BASELINE_TIME_INVALID" in overflow.issues
        bad_cost = _accounting(session, portfolio_id, lifecycle_id,
                               baseline_total_cost=Decimal(-1))
        assert bad_cost.status == "UNKNOWN" and bad_cost.accounting is None
        assert "BASELINE_COST_INVALID" in bad_cost.issues


def test_baseline_between_superseded_original_and_later_replacement_is_rejected(
        env, monkeypatch):
    factory = env["session_factory"]
    record = AccountFillReportService.record
    calls = 0

    def moved_replacement(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            kwargs["executed_at"] = EXECUTED + timedelta(hours=1)
        return record(self, *args, **kwargs)

    # Both report digests and both manual signatures are genuinely created for
    # their respective execution times; no immutable report is rewritten.
    with monkeypatch.context() as patch:
        patch.setattr(AccountFillReportService, "record", moved_replacement)
        portfolio_id, _, original_report_id, _, resolution_id = (
            _post_and_review_correction(factory, monkeypatch))
    assert calls == 2
    with factory() as session:
        AccountFillPostingStage(session)._stage_reviewed_correction(
            portfolio_id, resolution_id, expected_order_revision=2)
        session.commit()
    lifecycle_id, _ = _anchor_existing_fill(
        factory, portfolio_id, original_report_id)
    with factory() as session:
        between = _accounting(
            session, portfolio_id, lifecycle_id,
            baseline_as_of=EXECUTED + timedelta(minutes=30),
            baseline_quantity=Decimal(5), baseline_total_cost=Decimal(50))
        assert between.status == "UNKNOWN"
        assert between.accounting is None
        assert between.effective_fill_event_ids == ()
        assert "RESOLVED_ACCOUNTING_BASELINE_OVERLAPS_REPORT_HISTORY" in between.issues


def test_two_effective_fills_at_same_instant_remain_unknown(env, monkeypatch):
    factory = env["session_factory"]
    portfolio_id, lifecycle_id, _ = _historical_corrected_anchor(
        factory, monkeypatch)
    review_key = b"accounting-tied-fill-review-key-32!"
    monkeypatch.setenv("LIVEPROFIT_ACCOUNT_FILL_REVIEW_KEYS", json.dumps({
        "auditor": base64.b64encode(SECRET).decode(),
        "void-auditor": base64.b64encode(VOID_SECRET).decode(),
        "replay-reviewer": base64.b64encode(review_key).decode(),
    }))
    with factory() as session:
        lifecycle = session.get(PositionLifecycleState, lifecycle_id)
        intent = PositionIntent(
            lifecycle_id=lifecycle_id, trade_date=EXECUTED.date(),
            target_shares=Decimal(5), reason_code="PROFIT_TARGET_TRIM",
            state_version=1, status="ACTIVE", revision=1)
        session.add(intent)
        session.flush()
        order = SuggestedOrder(
            portfolio_id=portfolio_id, position_id=lifecycle.position_id,
            lifecycle_id=lifecycle_id, intent_id=intent.id,
            market="CN", symbol="000001.SZ", side="SELL",
            quantity=Decimal(1), filled_quantity=Decimal(0),
            limit_price=Decimal(10), reason_code="PROFIT_TARGET_TRIM",
            status="PROPOSED", revision=1)
        session.add(order)
        session.flush()
        _reviewed_fill(
            session, portfolio_id=portfolio_id, order=order,
            day=EXECUTED.date(), executed_at=EXECUTED,
            source_ref="accounting-tied-sell", review_key=review_key,
            quantity="1", captured_at=CAPTURED)
        session.commit()
    with factory() as session:
        replay = _accounting(session, portfolio_id, lifecycle_id)
        assert replay.status == "UNKNOWN"
        assert replay.accounting is None
        assert replay.effective_fill_event_ids == ()
        assert any(issue.startswith("EVENT_ORDER_AMBIGUOUS:")
                   for issue in replay.issues)
