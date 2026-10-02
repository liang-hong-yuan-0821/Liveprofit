# test-catalog-begin
# {
#   "purpose": "投资工作区 / fill_posting_stage（成交、入账）：Isolated software staging only; no production posting entry is registered.",
#   "keywords": [
#     "投资工作区",
#     "费用",
#     "成交",
#     "不可变历史",
#     "账户账本",
#     "持仓生命周期",
#     "订单",
#     "重放",
#     "版本修订",
#     "来源证据",
#     "fill_posting_stage",
#     "fee",
#     "fill",
#     "immutable",
#     "ledger",
#     "lifecycle",
#     "order",
#     "replay",
#     "revision",
#     "source"
#   ],
#   "covers": [
#     "backend/modules/investment_workspace/application/account_ledger_store.py",
#     "backend/modules/investment_workspace/application/account_observations.py",
#     "backend/modules/investment_workspace/application/fill_evidence.py",
#     "backend/modules/investment_workspace/application/fill_posting_stage.py",
#     "backend/modules/investment_workspace/application/fill_reports.py",
#     "backend/modules/investment_workspace/application/fill_resolution_reviews.py",
#     "backend/modules/investment_workspace/application/fill_reviews.py",
#     "backend/modules/investment_workspace/application/reconciliation.py",
#     "backend/modules/investment_workspace/infrastructure/account_ledger_models.py",
#     "backend/modules/investment_workspace/infrastructure/fill_report_models.py",
#     "backend/modules/investment_workspace/infrastructure/models.py",
#     "backend/modules/quant_strategy/application/lifecycle_fill_resolution_inventory.py",
#     "backend/modules/quant_strategy/application/lifecycle_persisted_diagnosis.py",
#     "backend/modules/quant_strategy/application/lifecycle_persisted_fill_inputs.py",
#     "backend/modules/quant_strategy/application/lifecycle_persisted_revision_impact.py",
#     "backend/modules/quant_strategy/application/lifecycle_replay_inventory.py",
#     "backend/modules/quant_strategy/application/lifecycle_service.py",
#     "backend/modules/quant_strategy/application/position_lifecycle_manager.py",
#     "backend/modules/quant_strategy/infrastructure/lifecycle_models.py",
#     "backend/modules/quant_strategy/infrastructure/models.py"
#   ],
#   "environment": [
#     "db"
#   ]
# }
# test-catalog-end

"""Isolated software staging only; no production posting entry is registered."""

from tests.backend.investment_workspace.support.fill_posting_stage import (
    BASELINE,
    EXECUTED,
    CAPTURED,
    SECRET,
    VOID_SECRET,
    _seed,
    _post_and_review_void,
    _post_and_review_correction,
)

import base64
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hmac
import json
import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from backend.modules.investment_workspace.application.account_ledger_store import AccountLedgerStore
from backend.modules.investment_workspace.application.account_observations import AccountObservationService
from backend.modules.investment_workspace.application.fill_evidence import AccountFillEvidenceService
from backend.modules.investment_workspace.application.fill_posting_stage import AccountFillPostingStage
from backend.modules.investment_workspace.application.fill_resolution_reviews import (
    AccountFillResolutionReviewService, resolution_review_payload,
)
from backend.modules.investment_workspace.application.fill_reports import AccountFillReportService
from backend.modules.investment_workspace.application.fill_reviews import AccountFillReviewService, review_payload
from backend.modules.investment_workspace.application.reconciliation import AccountObservation, ObservedHolding
from backend.modules.investment_workspace.infrastructure.account_ledger_models import AccountLedgerMovementRow
from backend.modules.investment_workspace.infrastructure.fill_report_models import (
    AccountFillPostingRow, AccountFillPostingVoidRow, AccountFillPostingCorrectionRow,
    AccountFillReportRow, AccountFillReportReviewRow,
    AccountFillReportResolutionRow, AccountFillResolutionReviewRow,
)
from backend.modules.investment_workspace.infrastructure.models import Portfolio, PortfolioPosition
from backend.modules.quant_strategy.infrastructure.lifecycle_models import (
    LifecyclePolicyVersion, OrderFillEvent, PositionIntent, PositionIntentRevision,
    PositionLifecycleState,
    SuggestedOrder,
)
from backend.modules.quant_strategy.infrastructure.models import QuantStrategy, QuantStrategyVersion
from backend.modules.quant_strategy.application.lifecycle_service import LifecycleOrderService
from backend.modules.quant_strategy.application.lifecycle_replay_inventory import inventory_lifecycle_replay
from backend.modules.quant_strategy.application.lifecycle_fill_resolution_inventory import inventory_effective_fill_chain
from backend.modules.quant_strategy.application.lifecycle_persisted_fill_inputs import load_local_fill_replay_inputs
from backend.modules.quant_strategy.application.lifecycle_persisted_revision_impact import load_local_lifecycle_revision_impact
from backend.modules.quant_strategy.application.lifecycle_persisted_diagnosis import diagnose_persisted_lifecycle
from backend.modules.quant_strategy.application.position_lifecycle_manager import LifecycleStateInput






def test_reviewed_fill_stage_is_atomic_and_fee_aware_but_has_no_public_entry(env, monkeypatch):
    factory = env["session_factory"]
    portfolio_id, order_id, report_id = _seed(factory, monkeypatch)
    with factory() as writer:
        posting, created = AccountFillPostingStage(writer)._stage_reviewed_report(
            portfolio_id, report_id, expected_order_revision=1)
        assert created and posting.id is not None
        assert writer.get(Portfolio, portfolio_id).available_cash == Decimal(1049)
        assert writer.get(SuggestedOrder, order_id).filled_quantity == Decimal(5)
        with factory() as reader:
            assert reader.get(Portfolio, portfolio_id).available_cash == Decimal(1000)
            assert reader.scalar(select(func.count()).select_from(AccountFillPostingRow)) == 0
        writer.rollback()
    with factory() as reader:
        assert reader.get(Portfolio, portfolio_id).available_cash == Decimal(1000)
        assert reader.scalar(select(func.count()).select_from(AccountFillPostingRow)) == 0
        assert reader.scalar(select(func.count()).select_from(OrderFillEvent)) == 0
        assert reader.scalar(select(func.count()).select_from(AccountLedgerMovementRow)) == 0
    with factory() as writer:
        posting, created = AccountFillPostingStage(writer)._stage_reviewed_report(
            portfolio_id, report_id, expected_order_revision=1)
        assert created
        writer.commit()
        posting_id = posting.id
    with factory() as reader:
        account = reader.get(Portfolio, portfolio_id)
        position = reader.scalar(select(PortfolioPosition).where(
            PortfolioPosition.portfolio_id == portfolio_id))
        order = reader.get(SuggestedOrder, order_id)
        posted = reader.get(AccountFillPostingRow, posting_id)
        assert account.available_cash == Decimal(1049)
        assert position.quantity == Decimal(5)
        assert order.filled_quantity == Decimal(5)
        assert posted.report_id == report_id
        assert reader.get(OrderFillEvent, posted.fill_event_id).quantity == Decimal(5)
        assert reader.get(AccountLedgerMovementRow, posted.ledger_movement_id).fee == Decimal(1)
        balance = AccountLedgerStore(reader).replay_current(portfolio_id)
        assert balance.cash == Decimal(1049)
        assert balance.holdings == (("CN", "000001.SZ", Decimal(5)),)
        replay, created = AccountFillPostingStage(reader)._stage_reviewed_report(
            portfolio_id, report_id, expected_order_revision=1)
        assert not created and replay.id == posting_id


def test_local_fill_replay_mapping_remains_source_uncertified(env, monkeypatch):
    factory = env["session_factory"]
    portfolio_id, order_id, report_id = _seed(factory, monkeypatch)
    with factory() as session:
        posting, _ = AccountFillPostingStage(session)._stage_reviewed_report(
            portfolio_id, report_id, expected_order_revision=1)
        strategy = QuantStrategy(name=f"mapping-{portfolio_id}")
        session.add(strategy)
        session.flush()
        policy = LifecyclePolicyVersion(
            policy_key="mapping-test", version_no=1, status="PUBLISHED",
            required_fields=[], config={}, content_hash="a" * 64)
        session.add(policy)
        session.flush()
        version = QuantStrategyVersion(
            strategy_id=strategy.id, version_no=1, status="PUBLISHED",
            source_code="pass", source_hash="b" * 64)
        session.add(version)
        session.flush()
        lifecycle = PositionLifecycleState(
            portfolio_id=portfolio_id, market="CN", symbol="000001.SZ",
            strategy_version_id=version.id, lifecycle_policy_version_id=policy.id,
            initial_fill_id=posting.fill_event_id, initial_fill_price=Decimal("10"),
            initial_stop_price=Decimal("8"), risk_capacity_shares=Decimal("5"),
            target_exposure_pct=Decimal("0.1"), target_shares=Decimal("5"),
            phase="ACTIVE")
        session.add(lifecycle)
        session.commit()
        lifecycle_id, fill_id = lifecycle.id, posting.fill_event_id
    with factory() as session:
        mapped = load_local_fill_replay_inputs(
            session, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id)
        assert mapped.status == "LOCAL_CANDIDATE"
        assert len(mapped.fills) == 1
        assert mapped.fills[0].event_id == fill_id
        assert mapped.fills[0].executed_at == EXECUTED
        assert mapped.fills[0].side == "SELL"
        assert mapped.fills[0].fee == Decimal("1")
        assert f"FILL_SOURCE_UNCERTIFIED:{fill_id}" in mapped.issues
        assert f"INITIAL_COST_SOURCE_UNCERTIFIED:{fill_id}" in mapped.issues
        assert "FILL_RESOLUTION_SOURCE_UNCERTIFIED" in mapped.issues
        assert not session.new and not session.dirty
        unchanged = load_local_lifecycle_revision_impact(
            session, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id)
        assert unchanged.status == "NO_REVISION"
        assert unchanged.paths == () and unchanged.earliest_impact_at is None
    # A valid posting without a currently verifiable review must no longer
    # enter replay merely because the report and ledger amounts still agree.
    monkeypatch.setenv("LIVEPROFIT_ACCOUNT_FILL_REVIEW_KEYS", json.dumps({
        "auditor": base64.b64encode(b"revoked-review-key-32-bytes-xxxxxxxx").decode(),
    }))
    with factory() as session:
        unsigned_stream = load_local_fill_replay_inputs(
            session, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id)
        assert unsigned_stream.status == "UNKNOWN" and unsigned_stream.fills == ()
        assert "FILL_REVISION_REPORT_REVIEW_INVALID" in unsigned_stream.issues
        unsigned_impact = load_local_lifecycle_revision_impact(
            session, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id)
        assert unsigned_impact.status == "UNKNOWN"
        assert unsigned_impact.paths == () and unsigned_impact.earliest_impact_at is None
        assert "FILL_REVISION_REPORT_REVIEW_INVALID" in unsigned_impact.issues
    monkeypatch.setenv("LIVEPROFIT_ACCOUNT_FILL_REVIEW_KEYS", json.dumps({
        "auditor": base64.b64encode(SECRET).decode(),
    }))
    with factory() as session:
        mismatched, _ = AccountFillReportService(session).record(
            portfolio_id, order_id=order_id, market="CN", symbol="000002.SZ",
            side="SELL", fill_trade_date=date(2026, 9, 28),
            executed_at=EXECUTED + timedelta(minutes=2), captured_at=CAPTURED,
            quantity="1", fill_price="10", fee="0",
            source_type="MANUAL_REPORT", source_ref="wrong-symbol-trade-row")
        session.commit()
        mismatched_id = mismatched.id
    with factory() as session:
        mismatched_stream = load_local_fill_replay_inputs(
            session, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id)
        assert mismatched_stream.status == "UNKNOWN" and mismatched_stream.fills == ()
        assert f"FILL_REPORT_IDENTITY_MISMATCH:{mismatched_id}" in mismatched_stream.issues
        mismatched_impact = load_local_lifecycle_revision_impact(
            session, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id)
        assert mismatched_impact.status == "UNKNOWN"
        assert mismatched_impact.paths == () and mismatched_impact.earliest_impact_at is None
        assert "FILL_REVISION_REPORT_IDENTITY_MISMATCH" in mismatched_impact.issues
    with factory() as session:
        pending, _ = AccountFillReportService(session).record(
            portfolio_id, order_id=order_id, market="CN", symbol="000001.SZ",
            side="SELL", fill_trade_date=date(2026, 9, 28),
            executed_at=EXECUTED + timedelta(minutes=1), captured_at=CAPTURED,
            quantity="1", fill_price="10", fee="0",
            source_type="MANUAL_REPORT", source_ref="unposted-trade-row")
        session.commit()
        pending_id = pending.id
    with factory() as session:
        pending_stream = load_local_fill_replay_inputs(
            session, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id)
        assert pending_stream.status == "UNKNOWN" and pending_stream.fills == ()
        assert f"FILL_REPORT_UNPOSTED:{pending_id}" in pending_stream.issues
        pending_impact = load_local_lifecycle_revision_impact(
            session, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id)
        assert pending_impact.status == "UNKNOWN"
        assert pending_impact.paths == () and pending_impact.earliest_impact_at is None
        assert f"FILL_REPORT_UNPOSTED:{pending_id}" in pending_impact.issues
    with factory() as session:
        unbound, _ = AccountFillReportService(session).record(
            portfolio_id, order_id=None, market="CN", symbol="000001.SZ",
            side="SELL", fill_trade_date=date(2026, 9, 28),
            executed_at=EXECUTED + timedelta(minutes=3), captured_at=CAPTURED,
            quantity="1", fill_price="10", fee="0",
            source_type="MANUAL_REPORT", source_ref="unbound-trade-row")
        session.commit()
        unbound_id = unbound.id
    with factory() as session:
        ambiguous_stream = load_local_fill_replay_inputs(
            session, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id)
        assert ambiguous_stream.status == "UNKNOWN" and ambiguous_stream.fills == ()
        assert f"FILL_REPORT_OWNERSHIP_AMBIGUOUS:{unbound_id}" in ambiguous_stream.issues
        ambiguous_impact = load_local_lifecycle_revision_impact(
            session, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id)
        assert ambiguous_impact.status == "UNKNOWN"
        assert ambiguous_impact.paths == () and ambiguous_impact.earliest_impact_at is None
        assert f"FILL_REPORT_OWNERSHIP_AMBIGUOUS:{unbound_id}" in ambiguous_impact.issues
    with factory() as cached:
        assert cached.get(SuggestedOrder, order_id).symbol == "000001.SZ"
        with factory() as writer:
            writer.get(SuggestedOrder, order_id).symbol = "999999.SZ"
            writer.commit()
        stale = load_local_fill_replay_inputs(
            cached, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id)
        assert stale.status == "UNKNOWN" and stale.fills == ()
        assert any(issue.startswith("INITIAL_ORDER_IDENTITY_MISMATCH:")
                   for issue in stale.issues)
        stale_impact = load_local_lifecycle_revision_impact(
            cached, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id)
        assert stale_impact.status == "UNKNOWN"
        assert stale_impact.paths == () and stale_impact.earliest_impact_at is None
        assert any(issue.startswith("INITIAL_ORDER_IDENTITY_MISMATCH:")
                   for issue in stale_impact.issues)


def test_initial_fill_cannot_be_reused_by_another_lifecycle(env, monkeypatch):
    factory = env["session_factory"]
    portfolio_id, order_id, report_id = _seed(factory, monkeypatch)
    with factory() as session:
        posting, _ = AccountFillPostingStage(session)._stage_reviewed_report(
            portfolio_id, report_id, expected_order_revision=1)
        strategy = QuantStrategy(name=f"anchor-owner-{portfolio_id}")
        policy = LifecyclePolicyVersion(
            policy_key=f"anchor-owner-{portfolio_id}", version_no=1,
            status="PUBLISHED", required_fields=[], config={},
            content_hash="a" * 64)
        session.add_all((strategy, policy))
        session.flush()
        version = QuantStrategyVersion(
            strategy_id=strategy.id, version_no=1, status="PUBLISHED",
            source_code="pass", source_hash="b" * 64)
        session.add(version)
        session.flush()
        old = PositionLifecycleState(
            portfolio_id=portfolio_id, market="CN", symbol="000001.SZ",
            strategy_version_id=version.id,
            lifecycle_policy_version_id=policy.id,
            initial_fill_id=posting.fill_event_id,
            initial_fill_price=Decimal(10),
            initial_stop_price=Decimal(8), risk_capacity_shares=Decimal(5),
            target_exposure_pct=Decimal("0.5"), target_shares=Decimal(5),
            phase="CLOSED", closed_at=CAPTURED)
        session.add(old)
        session.flush()
        session.get(SuggestedOrder, order_id).lifecycle_id = old.id
        old_id, fill_id = old.id, posting.fill_event_id
        version_id, policy_id = version.id, policy.id
        session.commit()
    with factory() as session:
        current = PositionLifecycleState(
            portfolio_id=portfolio_id, market="CN", symbol="000001.SZ",
            strategy_version_id=version_id,
            lifecycle_policy_version_id=policy_id,
            initial_fill_id=fill_id,
            initial_fill_price=Decimal(10),
            initial_stop_price=Decimal(8), risk_capacity_shares=Decimal(5),
            target_exposure_pct=Decimal("0.5"), target_shares=Decimal(5),
            phase="ACTIVE")
        session.add(current)
        with pytest.raises(IntegrityError) as error:
            session.flush()
        assert "uq_position_lifecycle_initial_fill" in str(error.value)
        session.rollback()
    with factory() as session:
        assert session.get(PositionLifecycleState, old_id).initial_fill_id == fill_id
        assert session.get(SuggestedOrder, order_id).lifecycle_id == old_id
        assert session.scalar(select(func.count()).select_from(PositionLifecycleState).where(
            PositionLifecycleState.portfolio_id == portfolio_id)) == 1


def test_stage_rejects_production_account_drift_without_losing_report(env, monkeypatch):
    factory = env["session_factory"]
    portfolio_id, _, report_id = _seed(factory, monkeypatch)
    with factory() as session:
        session.get(Portfolio, portfolio_id).available_cash = Decimal(999)
        session.commit()
    with factory() as session:
        with pytest.raises(ValueError, match="production account differ"):
            AccountFillPostingStage(session)._stage_reviewed_report(
                portfolio_id, report_id, expected_order_revision=1)
        session.rollback()
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(AccountFillPostingRow)) == 0
        assert session.scalar(select(func.count()).select_from(AccountLedgerMovementRow)) == 0


def test_stage_is_closed_outside_isolated_test_context(env, monkeypatch):
    factory = env["session_factory"]
    portfolio_id, _, report_id = _seed(factory, monkeypatch)
    monkeypatch.delenv("PYTEST_CURRENT_TEST")
    with factory() as session:
        with pytest.raises(ValueError, match="production fill projection is disabled"):
            AccountFillPostingStage(session)._stage_reviewed_report(
                portfolio_id, report_id, expected_order_revision=1)
        session.rollback()
    with factory() as session:
        assert session.get(Portfolio, portfolio_id).available_cash == Decimal(1000)
        assert session.scalar(select(func.count()).select_from(AccountFillPostingRow)) == 0
        assert session.scalar(select(func.count()).select_from(OrderFillEvent)) == 0
        assert session.scalar(select(func.count()).select_from(AccountLedgerMovementRow)) == 0


def test_mid_stage_failure_rolls_back_ledger_and_all_projections(env, monkeypatch):
    factory = env["session_factory"]
    portfolio_id, order_id, report_id = _seed(factory, monkeypatch)

    def fail_after_ledger(*args, **kwargs):
        raise ValueError("injected projection failure")

    with monkeypatch.context() as patch:
        patch.setattr(LifecycleOrderService, "_stage_confirm_fill_locked", fail_after_ledger)
        with factory() as session:
            with pytest.raises(ValueError, match="injected projection failure"):
                AccountFillPostingStage(session)._stage_reviewed_report(
                    portfolio_id, report_id, expected_order_revision=1)
            assert session.scalar(select(func.count()).select_from(AccountLedgerMovementRow)) == 1
            session.rollback()
    with factory() as session:
        assert session.get(Portfolio, portfolio_id).available_cash == Decimal(1000)
        assert session.get(SuggestedOrder, order_id).filled_quantity == 0
        assert session.scalar(select(func.count()).select_from(AccountLedgerMovementRow)) == 0
        assert session.scalar(select(func.count()).select_from(OrderFillEvent)) == 0
        assert session.scalar(select(func.count()).select_from(AccountFillPostingRow)) == 0


def test_unknown_fee_and_over_order_reports_remain_without_projection(env, monkeypatch):
    factory = env["session_factory"]
    portfolio_id, order_id, _ = _seed(factory, monkeypatch)
    with factory() as session:
        reports = AccountFillReportService(session)
        reviews = AccountFillReviewService(session)
        for source_ref, quantity, fee in (
            ("unknown-fee", "5", None),
            ("over-order", "11", "1"),
        ):
            artifact, _ = AccountFillEvidenceService(session).capture(
                portfolio_id, source_ref=f"statement-{source_ref}",
                media_type="text/csv",
                raw_bytes=(
                    f"account,trade,000001.SZ,SELL,{quantity},10,{fee or 'UNKNOWN'}"
                ).encode("ascii"))
            report, _ = reports.record(
                portfolio_id, order_id=order_id, market="CN", symbol="000001.SZ",
                side="SELL", fill_trade_date=date(2026, 9, 28),
                executed_at=EXECUTED, captured_at=CAPTURED,
                quantity=quantity, fill_price="10", fee=fee,
                source_type="MANUAL_REPORT", source_ref=source_ref,
                evidence_sha256=artifact.content_sha256,
                evidence_uri=f"review-archive://statement-{source_ref}")
            signature = hmac.new(SECRET, review_payload(
                portfolio_id=portfolio_id, report_id=report.id,
                report_sha256=report.payload_sha256,
                evidence_sha256=artifact.content_sha256,
                review_ref=f"review-{source_ref}", reviewed_by="auditor",
                reason="fixture account and statement inspected"), "sha256").hexdigest()
            reviews.record(portfolio_id, report.id, review_ref=f"review-{source_ref}",
                           reviewed_by="auditor", reason="fixture account and statement inspected",
                           review_signature=signature)
        session.commit()
        report_ids = {row.source_ref: row.id for row in session.scalars(
            select(AccountFillReportRow).where(AccountFillReportRow.portfolio_id == portfolio_id))}
    with factory() as session:
        stage = AccountFillPostingStage(session)
        with pytest.raises(ValueError, match="not complete"):
            stage._stage_reviewed_report(
                portfolio_id, report_ids["unknown-fee"], expected_order_revision=1)
        with pytest.raises(ValueError, match="exceeds order remaining"):
            stage._stage_reviewed_report(
                portfolio_id, report_ids["over-order"], expected_order_revision=1)
        session.rollback()
    with factory() as session:
        assert session.get(Portfolio, portfolio_id).available_cash == Decimal(1000)
        assert session.get(SuggestedOrder, order_id).filled_quantity == 0
        assert session.scalar(select(func.count()).select_from(AccountFillReportRow)) == 3
        assert session.scalar(select(func.count()).select_from(AccountLedgerMovementRow)) == 0
        assert session.scalar(select(func.count()).select_from(OrderFillEvent)) == 0
        assert session.scalar(select(func.count()).select_from(AccountFillPostingRow)) == 0




def test_reviewed_void_stage_reverses_latest_fill_atomically(env, monkeypatch):
    factory = env["session_factory"]
    portfolio_id, order_id, report_id, resolution_id = _post_and_review_void(factory, monkeypatch)
    with factory() as writer:
        void, created = AccountFillPostingStage(writer)._stage_reviewed_void(
            portfolio_id, resolution_id, expected_order_revision=2)
        assert created
        assert writer.get(Portfolio, portfolio_id).available_cash == Decimal(1000)
        assert writer.get(SuggestedOrder, order_id).filled_quantity == 0
        with factory() as reader:
            assert reader.get(Portfolio, portfolio_id).available_cash == Decimal(1049)
            assert reader.scalar(select(func.count()).select_from(AccountFillPostingVoidRow)) == 0
        writer.rollback()
    with factory() as writer:
        void, created = AccountFillPostingStage(writer)._stage_reviewed_void(
            portfolio_id, resolution_id, expected_order_revision=2)
        assert created
        writer.commit()
        void_id = void.id
    with factory() as reader:
        void = reader.get(AccountFillPostingVoidRow, void_id)
        posting = reader.scalar(select(AccountFillPostingRow).where(
            AccountFillPostingRow.report_id == report_id))
        assert void.posting_id == posting.id
        assert reader.get(OrderFillEvent, void.fill_event_id).reverses_fill_id == posting.fill_event_id
        assert reader.get(AccountLedgerMovementRow, void.ledger_movement_id).supersedes_id == posting.ledger_movement_id
        effective = inventory_effective_fill_chain(
            reader, portfolio_id=portfolio_id, market="CN", symbol="000001.SZ",
            order_ids=(order_id,))
        assert effective.status == "PROVISIONAL"
        assert effective.effective_fill_event_ids == ()
        assert effective.report_paths == ((report_id,),)
        assert reader.get(Portfolio, portfolio_id).available_cash == Decimal(1000)
        assert reader.get(SuggestedOrder, order_id).filled_quantity == 0
        assert reader.get(SuggestedOrder, order_id).status == "RECONCILIATION_REQUIRED"
        assert reader.scalar(select(PortfolioPosition.quantity).where(
            PortfolioPosition.portfolio_id == portfolio_id)) == Decimal(10)
        balance = AccountLedgerStore(reader).replay_current(portfolio_id)
        assert balance.cash == Decimal(1000)
        assert balance.holdings == (("CN", "000001.SZ", Decimal(10)),)
        replay, created = AccountFillPostingStage(reader)._stage_reviewed_void(
            portfolio_id, resolution_id, expected_order_revision=2)
        assert not created and replay.id == void_id


def test_reviewed_void_stage_rejects_drift_and_production_like_context(env, monkeypatch):
    factory = env["session_factory"]
    portfolio_id, order_id, _, resolution_id = _post_and_review_void(factory, monkeypatch)
    monkeypatch.delenv("PYTEST_CURRENT_TEST")
    with factory() as session:
        with pytest.raises(ValueError, match="production fill projection is disabled"):
            AccountFillPostingStage(session)._stage_reviewed_void(
                portfolio_id, resolution_id, expected_order_revision=2)
        session.rollback()
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "isolated void stage")
    with factory() as session:
        session.get(Portfolio, portfolio_id).available_cash = Decimal(1048)
        session.commit()
    with factory() as session:
        with pytest.raises(ValueError, match="production account differ"):
            AccountFillPostingStage(session)._stage_reviewed_void(
                portfolio_id, resolution_id, expected_order_revision=2)
        session.rollback()
    with factory() as session:
        assert session.get(SuggestedOrder, order_id).filled_quantity == Decimal(5)
        assert session.scalar(select(func.count()).select_from(AccountFillPostingVoidRow)) == 0


def test_reviewed_void_rechecks_original_signature_and_later_recorded_movement(env, monkeypatch):
    factory = env["session_factory"]
    portfolio_id, _, _, resolution_id = _post_and_review_void(factory, monkeypatch)
    monkeypatch.setenv("LIVEPROFIT_ACCOUNT_FILL_REVIEW_KEYS", json.dumps({
        "void-auditor": base64.b64encode(VOID_SECRET).decode(),
    }))
    with factory() as session:
        with pytest.raises(ValueError, match="reviewer is not configured"):
            AccountFillPostingStage(session)._stage_reviewed_void(
                portfolio_id, resolution_id, expected_order_revision=2)
        session.rollback()
    monkeypatch.setenv("LIVEPROFIT_ACCOUNT_FILL_REVIEW_KEYS", json.dumps({
        "auditor": base64.b64encode(SECRET).decode(),
        "void-auditor": base64.b64encode(VOID_SECRET).decode(),
    }))
    with factory() as session:
        AccountLedgerStore(session).append_cash_flow(
            portfolio_id, effective_at=BASELINE + timedelta(hours=12),
            amount="1", source_type="MANUAL_ENTRY", source_ref="late-recorded-early-effective")
        session.commit()
    with factory() as session:
        with pytest.raises(ValueError, match="later recorded ledger movement"):
            AccountFillPostingStage(session)._stage_reviewed_void(
                portfolio_id, resolution_id, expected_order_revision=2)
        session.rollback()
        assert session.scalar(select(func.count()).select_from(AccountFillPostingVoidRow)) == 0


def test_reviewed_buy_void_restores_pre_fill_rounded_cost(env, monkeypatch):
    factory = env["session_factory"]
    portfolio_id, order_id = uuid.uuid4(), uuid.uuid4()
    with factory() as session:
        session.add(Portfolio(id=portfolio_id, name=f"buy-void-{portfolio_id}",
                              version=1, total_assets=Decimal(2000),
                              available_cash=Decimal(1000)))
        session.add(PortfolioPosition(portfolio_id=portfolio_id, market="CN",
                                      symbol="000001.SZ", quantity=Decimal(2),
                                      average_cost=Decimal("1.0000")))
        session.add(SuggestedOrder(id=order_id, portfolio_id=portfolio_id,
                                   market="CN", symbol="000001.SZ", side="BUY",
                                   quantity=Decimal(1), limit_price=Decimal("1.0002"),
                                   reason_code="TEST_BUY_VOID", status="PROPOSED", revision=1))
        session.commit()
    with factory() as session:
        observation, _ = AccountObservationService(session).record(AccountObservation(
            portfolio_id=portfolio_id, trade_date=BASELINE.date(),
            captured_at=BASELINE, cash="1000",
            holdings=(ObservedHolding("CN", "000001.SZ", "2", "2"),),
            complete_holdings=True, source_ref="buy-opening"),
            source_type="MANUAL_IMPORT")
        AccountLedgerStore(session).establish_baseline(portfolio_id, observation.id)
        artifact, _ = AccountFillEvidenceService(session).capture(
            portfolio_id, source_ref="buy-statement", media_type="text/csv",
            raw_bytes=b"account,trade,000001.SZ,BUY,1,1.0002,0.1")
        report, _ = AccountFillReportService(session).record(
            portfolio_id, order_id=order_id, market="CN", symbol="000001.SZ",
            side="BUY", fill_trade_date=date(2026, 9, 28),
            executed_at=EXECUTED, captured_at=CAPTURED,
            quantity="1", fill_price="1.0002", fee="0.1",
            source_type="MANUAL_REPORT", source_ref="buy-trade-row",
            evidence_sha256=artifact.content_sha256,
            evidence_uri="review-archive://buy-statement")
        session.commit()
        report_id, report_sha, evidence_sha = report.id, report.payload_sha256, artifact.content_sha256
    monkeypatch.setenv("LIVEPROFIT_ACCOUNT_FILL_REVIEW_KEYS", json.dumps({
        "auditor": base64.b64encode(SECRET).decode(),
        "void-auditor": base64.b64encode(VOID_SECRET).decode(),
    }))
    signature = hmac.new(SECRET, review_payload(
        portfolio_id=portfolio_id, report_id=report_id,
        report_sha256=report_sha, evidence_sha256=evidence_sha,
        review_ref="buy-review", reviewed_by="auditor",
        reason="buy fixture inspected"), "sha256").hexdigest()
    with factory() as session:
        AccountFillReviewService(session).record(
            portfolio_id, report_id, review_ref="buy-review", reviewed_by="auditor",
            reason="buy fixture inspected", review_signature=signature)
        AccountFillPostingStage(session)._stage_reviewed_report(
            portfolio_id, report_id, expected_order_revision=1)
        session.commit()
    with factory() as session:
        position = session.scalar(select(PortfolioPosition).where(
            PortfolioPosition.portfolio_id == portfolio_id))
        assert position.average_cost == Decimal("1.0334")
        artifact, _ = AccountFillEvidenceService(session).capture(
            portfolio_id, source_ref="buy-void-statement", media_type="text/csv",
            raw_bytes=b"account,trade,000001.SZ,BUY,1,1.0002,0.1,VOID")
        resolution, _ = AccountFillReportService(session).resolve(
            portfolio_id, report_id, action="VOID", replacement_report_id=None,
            reason="buy row revoked", captured_at=CAPTURED,
            source_type="MANUAL_REPORT", source_ref="buy-void-row",
            evidence_sha256=artifact.content_sha256,
            evidence_uri="review-archive://buy-void-statement")
        session.commit()
        resolution_id, resolution_sha, void_evidence_sha = (
            resolution.id, resolution.payload_sha256, artifact.content_sha256)
    void_signature = hmac.new(VOID_SECRET, resolution_review_payload(
        portfolio_id=portfolio_id, resolution_id=resolution_id,
        resolution_sha256=resolution_sha, evidence_sha256=void_evidence_sha,
        review_ref="buy-void-review", reviewed_by="void-auditor",
        reason="buy revocation inspected"), "sha256").hexdigest()
    with factory() as session:
        AccountFillResolutionReviewService(session).record(
            portfolio_id, resolution_id, review_ref="buy-void-review",
            reviewed_by="void-auditor", reason="buy revocation inspected",
            review_signature=void_signature)
        AccountFillPostingStage(session)._stage_reviewed_void(
            portfolio_id, resolution_id, expected_order_revision=2)
        session.commit()
    with factory() as session:
        position = session.scalar(select(PortfolioPosition).where(
            PortfolioPosition.portfolio_id == portfolio_id))
        assert position.quantity == Decimal(2)
        assert position.average_cost == Decimal("1.0000")
        assert session.get(Portfolio, portfolio_id).available_cash == Decimal(1000)


def test_reviewed_void_rolls_back_after_ledger_append_and_fill_date_is_immutable(env, monkeypatch):
    factory = env["session_factory"]
    portfolio_id, _, _, resolution_id = _post_and_review_void(factory, monkeypatch)

    def fail_projection(*args, **kwargs):
        raise ValueError("injected void projection failure")

    with monkeypatch.context() as patch:
        patch.setattr(LifecycleOrderService, "_apply_delta", fail_projection)
        with factory() as session:
            with pytest.raises(ValueError, match="injected void projection failure"):
                AccountFillPostingStage(session)._stage_reviewed_void(
                    portfolio_id, resolution_id, expected_order_revision=2)
            assert session.scalar(select(func.count()).select_from(AccountLedgerMovementRow)) == 2
            session.rollback()
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(AccountLedgerMovementRow)) == 1
        assert session.scalar(select(func.count()).select_from(AccountFillPostingVoidRow)) == 0
        void, _ = AccountFillPostingStage(session)._stage_reviewed_void(
            portfolio_id, resolution_id, expected_order_revision=2)
        session.commit()
        void_id = void.id
    with factory() as session:
        void = session.get(AccountFillPostingVoidRow, void_id)
        with pytest.raises(DBAPIError, match="order fill events are immutable"):
            session.execute(text("UPDATE order_fill_events SET fill_trade_date=:wrong WHERE id=:id"),
                            {"wrong": date(2026, 9, 29), "id": void.fill_event_id})
        session.rollback()




def test_reviewed_correction_reverses_and_reposts_in_one_transaction(env, monkeypatch):
    factory = env["session_factory"]
    portfolio_id, order_id, report_id, replacement_id, resolution_id = (
        _post_and_review_correction(factory, monkeypatch))
    with factory() as reader:
        pending = inventory_effective_fill_chain(
            reader, portfolio_id=portfolio_id, market="CN", symbol="000001.SZ",
            order_ids=(order_id,))
        assert pending.status == "UNKNOWN"
        assert pending.issues == ("FILL_REVISION_REPORT_UNPOSTED",)
    with factory() as writer:
        correction, created = AccountFillPostingStage(writer)._stage_reviewed_correction(
            portfolio_id, resolution_id, expected_order_revision=2)
        assert created
        assert writer.get(Portfolio, portfolio_id).available_cash == Decimal("1043.5")
        with factory() as reader:
            assert reader.get(Portfolio, portfolio_id).available_cash == Decimal(1049)
            assert reader.scalar(select(func.count()).select_from(AccountFillPostingCorrectionRow)) == 0
        writer.rollback()
    with factory() as writer:
        correction, created = AccountFillPostingStage(writer)._stage_reviewed_correction(
            portfolio_id, resolution_id, expected_order_revision=2)
        assert created
        writer.commit()
        correction_id = correction.id
    with factory() as reader:
        row = reader.get(AccountFillPostingCorrectionRow, correction_id)
        original = reader.scalar(select(AccountFillPostingRow).where(
            AccountFillPostingRow.report_id == report_id))
        replacement = reader.scalar(select(AccountFillPostingRow).where(
            AccountFillPostingRow.report_id == replacement_id))
        assert row.original_posting_id == original.id
        assert row.replacement_posting_id == replacement.id
        effective = inventory_effective_fill_chain(
            reader, portfolio_id=portfolio_id, market="CN", symbol="000001.SZ",
            order_ids=(order_id,))
        assert effective.status == "PROVISIONAL"
        assert effective.effective_fill_event_ids == (replacement.fill_event_id,)
        assert effective.report_paths == ((report_id, replacement_id),)
        assert reader.get(OrderFillEvent, row.void_fill_event_id).reverses_fill_id == original.fill_event_id
        assert reader.get(AccountLedgerMovementRow, row.void_ledger_movement_id).supersedes_id == original.ledger_movement_id
        assert reader.get(Portfolio, portfolio_id).available_cash == Decimal("1043.5")
        assert reader.get(SuggestedOrder, order_id).filled_quantity == Decimal(4)
        assert reader.get(SuggestedOrder, order_id).status == "RECONCILIATION_REQUIRED"
        assert reader.scalar(select(PortfolioPosition.quantity).where(
            PortfolioPosition.portfolio_id == portfolio_id)) == Decimal(6)
        balance = AccountLedgerStore(reader).replay_current(portfolio_id)
        assert balance.cash == Decimal("1043.5")
        assert balance.holdings == (("CN", "000001.SZ", Decimal(6)),)
        replay, created = AccountFillPostingStage(reader)._stage_reviewed_correction(
            portfolio_id, resolution_id, expected_order_revision=2)
        assert not created and replay.id == correction_id
    with factory() as reader:
        reader.add(OrderFillEvent(
            order_id=order_id, portfolio_id=portfolio_id, event_type="CONFIRM",
            quantity=Decimal(1), fill_price=Decimal(10),
            fill_trade_date=date(2026, 9, 28), source="MANUAL",
            idempotency_key=f"unbound-{uuid.uuid4()}"))
        reader.flush()
        incomplete = inventory_effective_fill_chain(
            reader, portfolio_id=portfolio_id, market="CN", symbol="000001.SZ",
            order_ids=(order_id,))
        assert incomplete.status == "UNKNOWN"
        assert incomplete.issues == ("FILL_REVISION_ORDER_EVENT_SET_MISMATCH",)
        reader.rollback()
    with factory() as reader, factory() as writer:
        assert inventory_effective_fill_chain(
            reader, portfolio_id=portfolio_id, market="CN", symbol="000001.SZ",
            order_ids=(order_id,)).status == "PROVISIONAL"
        writer.execute(text("SET LOCAL lock_timeout = '100ms'"))
        with pytest.raises(DBAPIError, match="lock timeout"):
            writer.execute(text("UPDATE suggested_orders SET symbol='000002.SZ' WHERE id=:id"),
                           {"id": order_id})
        writer.rollback()
        reader.rollback()


def test_applied_correction_with_stale_lifecycle_anchor_stays_unknown(env, monkeypatch):
    factory = env["session_factory"]
    portfolio_id, order_id, report_id, replacement_id, resolution_id = (
        _post_and_review_correction(factory, monkeypatch))
    # Apply the signed correction before the lifecycle exists. The stage must
    # remain closed to corrections of an already linked lifecycle.
    with factory() as session:
        AccountFillPostingStage(session)._stage_reviewed_correction(
            portfolio_id, resolution_id, expected_order_revision=2)
        session.commit()
    with factory() as session:
        original = session.scalar(select(AccountFillPostingRow).where(
            AccountFillPostingRow.report_id == report_id))
        replacement = session.scalar(select(AccountFillPostingRow).where(
            AccountFillPostingRow.report_id == replacement_id))
        effective = inventory_effective_fill_chain(
            session, portfolio_id=portfolio_id, market="CN", symbol="000001.SZ",
            order_ids=(order_id,))
        assert effective.status == "PROVISIONAL"
        assert effective.effective_fill_event_ids == (replacement.fill_event_id,)
        strategy = QuantStrategy(name=f"stale-correction-{portfolio_id}")
        session.add(strategy)
        session.flush()
        policy = LifecyclePolicyVersion(
            policy_key=f"stale-correction-{portfolio_id}", version_no=1,
            status="PUBLISHED", required_fields=[], config={}, content_hash="a" * 64)
        session.add(policy)
        session.flush()
        version = QuantStrategyVersion(
            strategy_id=strategy.id, version_no=1, status="PUBLISHED",
            source_code="pass", source_hash="b" * 64)
        session.add(version)
        session.flush()
        position = session.scalar(select(PortfolioPosition).where(
            PortfolioPosition.portfolio_id == portfolio_id))
        # Simulate stale imported linkage after the pre-link correction: the
        # initial anchor still names the superseded original fill.
        lifecycle = PositionLifecycleState(
            portfolio_id=portfolio_id, position_id=position.id,
            market="CN", symbol="000001.SZ",
            strategy_version_id=version.id, lifecycle_policy_version_id=policy.id,
            initial_fill_id=original.fill_event_id, initial_fill_price=Decimal(10),
            initial_stop_price=Decimal(8), risk_capacity_shares=Decimal(10),
            target_exposure_pct=Decimal("0.5"), target_shares=Decimal(5),
            phase="ACTIVE")
        session.add(lifecycle)
        session.commit()
        lifecycle_id = lifecycle.id
    def persisted_snapshot():
        with factory() as session:
            return (
                session.get(Portfolio, portfolio_id).available_cash,
                session.get(PositionLifecycleState, lifecycle_id).initial_fill_id,
                tuple((row.id, row.event_type, row.lifecycle_id_at_fill,
                       row.intent_id_at_fill, row.binding_origin)
                      for row in session.scalars(select(OrderFillEvent).where(
                          OrderFillEvent.portfolio_id == portfolio_id)
                          .order_by(OrderFillEvent.id))),
                tuple((row.report_id, row.fill_event_id) for row in session.scalars(
                    select(AccountFillPostingRow).where(
                        AccountFillPostingRow.portfolio_id == portfolio_id)
                    .order_by(AccountFillPostingRow.report_id))),
                tuple((row.id, row.kind, row.supersedes_id) for row in session.scalars(
                    select(AccountLedgerMovementRow).where(
                        AccountLedgerMovementRow.portfolio_id == portfolio_id)
                    .order_by(AccountLedgerMovementRow.id))),
            )
    before = persisted_snapshot()
    with factory() as session:
        mapped = load_local_fill_replay_inputs(
            session, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id)
        assert mapped.status == "UNKNOWN" and mapped.fills == ()
        assert "FILL_EFFECTIVE_SET_REQUIRES_LIFECYCLE_REPLAY" in mapped.issues
        impact = load_local_lifecycle_revision_impact(
            session, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id)
        assert impact.status == "LOCAL_IMPACT"
        assert len(impact.paths) == 1
        path = impact.paths[0]
        assert path.kind == "INITIAL_ANCHOR_CORRECT"
        assert path.root_report_id == report_id
        assert path.root_fill_event_id == original.fill_event_id
        assert path.report_ids == (report_id, replacement_id)
        assert path.terminal_report_id == replacement_id
        assert path.terminal_fill_event_id == replacement.fill_event_id
        assert impact.earliest_impact_at == EXECUTED
        original_event = session.get(OrderFillEvent, original.fill_event_id)
        replacement_event = session.get(OrderFillEvent, replacement.fill_event_id)
        assert (original_event.binding_origin, replacement_event.binding_origin) == ("LIVE", "LIVE")
        assert original_event.lifecycle_id_at_fill is None
        assert replacement_event.lifecycle_id_at_fill is None
        assert any(issue.startswith("FILL_EXECUTION_ORDER_UNCERTIFIED:")
                   for issue in impact.issues)
        assert "BROKER_FILL_SET_UNCERTIFIED" in impact.issues
        assert "LOCAL_CAUSAL_HISTORY_NOT_REPLAYED" in impact.issues
        assert not session.new and not session.dirty
        diagnosed = diagnose_persisted_lifecycle(
            session, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id,
            baseline_as_of=BASELINE, baseline_quantity=Decimal(10),
            baseline_total_cost=Decimal(100), baseline_source_ref="fixture:opening-holding",
            seed_state=LifecycleStateInput(
                template_id="ma_trend_cross_v1", initial_fill_price=Decimal(10),
                initial_stop_price=Decimal(8), risk_capacity_shares=Decimal(10),
                target_exposure_pct=Decimal("0.5")),
            seed_target_shares=Decimal(5), seed_trailing=None,
            seed_expectation=None, management_policy=None,
            calendar_dates=(date(2026, 9, 28),), calendar_source_ref="fixture:calendar",
            corporate_action_dates=(), corporate_action_source_ref="fixture:actions")
        assert diagnosed.status == "UNKNOWN" and diagnosed.diff is None
        assert "FILL_EFFECTIVE_SET_REQUIRES_LIFECYCLE_REPLAY" in diagnosed.issues
        assert not session.new and not session.dirty
    assert persisted_snapshot() == before


def test_reviewed_correction_rejects_unknown_fee_without_partial_commit(env, monkeypatch):
    factory = env["session_factory"]
    portfolio_id, order_id, _, replacement_id, resolution_id = (
        _post_and_review_correction(factory, monkeypatch, replacement_fee=None))
    monkeypatch.delenv("PYTEST_CURRENT_TEST")
    with factory() as session:
        with pytest.raises(ValueError, match="production fill projection is disabled"):
            AccountFillPostingStage(session)._stage_reviewed_correction(
                portfolio_id, resolution_id, expected_order_revision=2)
        session.rollback()
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "isolated correction stage")
    with factory() as session:
        with pytest.raises(ValueError, match="not complete"):
            AccountFillPostingStage(session)._stage_reviewed_correction(
                portfolio_id, resolution_id, expected_order_revision=2)
        session.rollback()
    with factory() as session:
        assert session.get(Portfolio, portfolio_id).available_cash == Decimal(1049)
        assert session.get(SuggestedOrder, order_id).filled_quantity == Decimal(5)
        assert session.get(AccountFillReportRow, replacement_id) is not None
        assert session.scalar(select(func.count()).select_from(AccountFillPostingCorrectionRow)) == 0
        assert session.scalar(select(func.count()).select_from(AccountFillPostingVoidRow)) == 0
        assert session.scalar(select(func.count()).select_from(AccountLedgerMovementRow)) == 1


def test_reviewed_correction_rejects_lifecycle_linked_original_fill(env, monkeypatch):
    factory = env["session_factory"]
    portfolio_id, order_id, _, _, resolution_id = _post_and_review_correction(
        factory, monkeypatch)
    actual_lock = LifecycleOrderService._lock_order

    def linked_order(self, *args, **kwargs):
        order = actual_lock(self, *args, **kwargs)
        order.lifecycle_id = uuid.uuid4()
        return order

    with monkeypatch.context() as patch:
        patch.setattr(LifecycleOrderService, "_lock_order", linked_order)
        with factory() as session:
            with pytest.raises(ValueError, match="lifecycle-linked fill reversal"):
                AccountFillPostingStage(session)._stage_reviewed_correction(
                    portfolio_id, resolution_id, expected_order_revision=2)
            session.rollback()
    with factory() as session:
        assert session.get(Portfolio, portfolio_id).available_cash == Decimal(1049)
        assert session.get(SuggestedOrder, order_id).filled_quantity == Decimal(5)
        assert session.scalar(select(func.count()).select_from(AccountFillPostingCorrectionRow)) == 0


def test_persisted_initial_fill_anchor_blocks_correction_before_writes(env, monkeypatch):
    factory = env["session_factory"]
    portfolio_id, order_id, _, _, resolution_id = _post_and_review_correction(
        factory, monkeypatch)
    with factory() as session:
        posting = session.scalar(select(AccountFillPostingRow).where(
            AccountFillPostingRow.portfolio_id == portfolio_id))
        strategy = QuantStrategy(name=f"anchor-{portfolio_id}")
        session.add(strategy)
        session.flush()
        policy = LifecyclePolicyVersion(
            policy_key="anchor-test", version_no=1, status="PUBLISHED",
            required_fields=[], config={}, content_hash="a" * 64)
        session.add(policy)
        session.flush()
        version = QuantStrategyVersion(
            strategy_id=strategy.id, version_no=1, status="PUBLISHED",
            source_code="pass", source_hash="b" * 64)
        session.add(version)
        session.flush()
        session.add(PositionLifecycleState(
            portfolio_id=portfolio_id, market="CN", symbol="000001.SZ",
            strategy_version_id=version.id, lifecycle_policy_version_id=policy.id,
            initial_fill_id=posting.fill_event_id, initial_fill_price=Decimal("10"),
            initial_stop_price=Decimal("8"), risk_capacity_shares=Decimal("5"),
            target_exposure_pct=Decimal("0.1"), target_shares=Decimal("5"),
            phase="ACTIVE"))
        session.commit()
    with factory() as session:
        lifecycle = session.scalar(select(PositionLifecycleState).where(
            PositionLifecycleState.portfolio_id == portfolio_id))
        inventory = inventory_lifecycle_replay(session, portfolio_id, lifecycle.id)
        assert inventory.fill_event_ids == (posting.fill_event_id,)
        assert f"FILL_SOURCE_UNCERTIFIED:{posting.fill_event_id}" in inventory.issues
        assert f"FILL_REPORT_REVISION_PRESENT:{posting.fill_event_id}" in inventory.issues
        mapped = load_local_fill_replay_inputs(
            session, portfolio_id=portfolio_id, lifecycle_id=lifecycle.id)
        assert mapped.status == "UNKNOWN" and mapped.fills == ()
        assert f"FILL_REPORT_REVISION_PRESENT:{posting.fill_event_id}" in mapped.issues
        assert "FILL_REVISION_REPORT_UNPOSTED" in mapped.issues
        assert not any(item.startswith("FILL_REPORT_BINDING_MISSING:")
                       for item in inventory.issues)
        assert not any(item.startswith("FILL_REPORT_LEDGER_MISMATCH:")
                       for item in inventory.issues)
        assert not session.new and not session.dirty
        session.rollback()
    with factory() as session:
        lifecycle = session.scalar(select(PositionLifecycleState).where(
            PositionLifecycleState.portfolio_id == portfolio_id))
        session.get(Portfolio, portfolio_id).available_cash = Decimal("1")
        with pytest.raises(ValueError, match="clean read-only session"):
            inventory_lifecycle_replay(session, portfolio_id, lifecycle.id)
        session.rollback()
    with factory() as session:
        assert session.get(Portfolio, portfolio_id).available_cash == Decimal("1049")
    with factory() as session:
        with pytest.raises(ValueError, match="lifecycle-linked fill reversal"):
            AccountFillPostingStage(session)._stage_reviewed_correction(
                portfolio_id, resolution_id, expected_order_revision=2)
        session.rollback()
    with factory() as session:
        assert session.get(Portfolio, portfolio_id).available_cash == Decimal(1049)
        assert session.get(SuggestedOrder, order_id).filled_quantity == Decimal(5)
        assert session.scalar(select(func.count()).select_from(AccountFillPostingCorrectionRow)) == 0
        assert session.scalar(select(func.count()).select_from(AccountLedgerMovementRow)) == 1


@pytest.mark.parametrize("action", ("VOID", "CORRECT"))
def test_noninitial_lifecycle_fill_revision_keeps_persisted_facts(
        env, monkeypatch, action):
    """A signed revision stays declarative until lifecycle replay can post it."""
    factory = env["session_factory"]
    portfolio_id = uuid.uuid4()
    review_keys = {"auditor": SECRET, "void-auditor": VOID_SECRET}
    monkeypatch.setenv("LIVEPROFIT_ACCOUNT_FILL_REVIEW_KEYS", json.dumps({
        reviewer: base64.b64encode(key).decode()
        for reviewer, key in review_keys.items()
    }))

    def signed_report(session, order, *, source_ref, executed_at, quantity, fee):
        artifact, _ = AccountFillEvidenceService(session).capture(
            portfolio_id, source_ref=f"{source_ref}-evidence", media_type="text/csv",
            raw_bytes=f"account,{source_ref},{order.side},{quantity},10,{fee}".encode())
        report, _ = AccountFillReportService(session).record(
            portfolio_id, order_id=order.id, market="CN", symbol="000001.SZ",
            side=order.side, fill_trade_date=executed_at.date(),
            executed_at=executed_at, captured_at=CAPTURED,
            quantity=quantity, fill_price="10", fee=fee,
            source_type="MANUAL_REPORT", source_ref=source_ref,
            evidence_sha256=artifact.content_sha256,
            evidence_uri=f"review-archive://{source_ref}-evidence")
        signature = hmac.new(SECRET, review_payload(
            portfolio_id=portfolio_id, report_id=report.id,
            report_sha256=report.payload_sha256,
            evidence_sha256=artifact.content_sha256,
            review_ref=f"{source_ref}-review", reviewed_by="auditor",
            reason="isolated fixture inspected"), "sha256").hexdigest()
        AccountFillReviewService(session).record(
            portfolio_id, report.id, review_ref=f"{source_ref}-review",
            reviewed_by="auditor", reason="isolated fixture inspected",
            review_signature=signature)
        return report

    with factory() as session:
        portfolio = Portfolio(
            id=portfolio_id, name=f"linked-revision-{portfolio_id}",
            version=1, total_assets=Decimal(1000),
            available_cash=Decimal(1000))
        position = PortfolioPosition(
            portfolio_id=portfolio_id, market="CN", symbol="000001.SZ",
            quantity=Decimal(0), average_cost=Decimal(0))
        strategy = QuantStrategy(name=f"linked-revision-{portfolio_id}")
        policy = LifecyclePolicyVersion(
            policy_key=f"linked-revision-{portfolio_id}", version_no=1,
            status="PUBLISHED", required_fields=[], config={},
            content_hash="a" * 64)
        session.add_all((portfolio, position, strategy, policy))
        session.flush()
        version = QuantStrategyVersion(
            strategy_id=strategy.id, version_no=1, status="PUBLISHED",
            source_code="pass", source_hash="b" * 64)
        session.add(version)
        session.flush()
        observation, _ = AccountObservationService(session).record(
            AccountObservation(
                portfolio_id=portfolio_id, trade_date=BASELINE.date(),
                captured_at=BASELINE, cash="1000", holdings=(),
                complete_holdings=True, source_ref="opening-empty"),
            source_type="MANUAL_IMPORT")
        AccountLedgerStore(session).establish_baseline(portfolio_id, observation.id)
        first_order = SuggestedOrder(
            portfolio_id=portfolio_id, market="CN", symbol="000001.SZ",
            side="BUY", quantity=Decimal(10), limit_price=Decimal(10),
            reason_code="INITIAL_ENTRY", status="PROPOSED", revision=1)
        session.add(first_order)
        session.flush()
        first_report = signed_report(
            session, first_order, source_ref="linked-initial-buy",
            executed_at=EXECUTED, quantity="10", fee="1")
        first_posting, _ = AccountFillPostingStage(session)._stage_reviewed_report(
            portfolio_id, first_report.id, expected_order_revision=1)
        session.refresh(position)
        lifecycle = PositionLifecycleState(
            portfolio_id=portfolio_id, position_id=position.id,
            market="CN", symbol="000001.SZ",
            strategy_version_id=version.id, lifecycle_policy_version_id=policy.id,
            initial_fill_id=first_posting.fill_event_id,
            initial_fill_price=Decimal(10), initial_stop_price=Decimal(8),
            risk_capacity_shares=Decimal(20), target_exposure_pct=Decimal("0.5"),
            target_shares=Decimal(10), phase="ACTIVE")
        session.add(lifecycle)
        session.flush()
        intent = PositionIntent(
            lifecycle_id=lifecycle.id, trade_date=EXECUTED.date() + timedelta(days=1),
            target_shares=Decimal(5), reason_code="PROFIT_TARGET_TRIM",
            state_version=1, status="ACTIVE", revision=1)
        session.add(intent)
        session.flush()
        second_order = SuggestedOrder(
            portfolio_id=portfolio_id, position_id=position.id,
            lifecycle_id=lifecycle.id, intent_id=intent.id,
            market="CN", symbol="000001.SZ", side="SELL",
            quantity=Decimal(5), limit_price=Decimal(10),
            reason_code=intent.reason_code, status="PROPOSED", revision=1)
        session.add(second_order)
        session.flush()
        second_at = EXECUTED + timedelta(days=1)
        second_report = signed_report(
            session, second_order, source_ref="linked-second-sell",
            executed_at=second_at, quantity="5", fee="1")
        second_posting, _ = AccountFillPostingStage(session)._stage_reviewed_report(
            portfolio_id, second_report.id, expected_order_revision=1)
        second_fill = session.get(OrderFillEvent, second_posting.fill_event_id)
        session.refresh(second_fill)
        assert (second_fill.lifecycle_id_at_fill, second_fill.intent_id_at_fill) == (
            lifecycle.id, intent.id)
        assert second_fill.id != lifecycle.initial_fill_id
        session.refresh(lifecycle)
        session.refresh(intent)
        assert (lifecycle.phase, intent.status) == ("PROFIT_PROTECTED", "COMPLETED")

        replacement_id = None
        if action == "CORRECT":
            replacement = signed_report(
                session, second_order, source_ref="linked-second-correction",
                executed_at=second_at, quantity="4", fee="0.5")
            replacement_id = replacement.id
        notice, _ = AccountFillEvidenceService(session).capture(
            portfolio_id, source_ref=f"linked-{action.lower()}-notice",
            media_type="text/csv", raw_bytes=f"signed {action} notice".encode())
        resolution, _ = AccountFillReportService(session).resolve(
            portfolio_id, second_report.id, action=action,
            replacement_report_id=replacement_id,
            reason="later statement revision", captured_at=CAPTURED,
            source_type="MANUAL_REPORT", source_ref=f"linked-{action.lower()}-resolution",
            evidence_sha256=notice.content_sha256,
            evidence_uri=f"review-archive://linked-{action.lower()}-notice")
        resolution_signature = hmac.new(VOID_SECRET, resolution_review_payload(
            portfolio_id=portfolio_id, resolution_id=resolution.id,
            resolution_sha256=resolution.payload_sha256,
            evidence_sha256=notice.content_sha256,
            review_ref=f"linked-{action.lower()}-review",
            reviewed_by="void-auditor", reason="isolated revision inspected"),
            "sha256").hexdigest()
        AccountFillResolutionReviewService(session).record(
            portfolio_id, resolution.id,
            review_ref=f"linked-{action.lower()}-review",
            reviewed_by="void-auditor", reason="isolated revision inspected",
            review_signature=resolution_signature)
        session.commit()
        lifecycle_id, intent_id, order_id, resolution_id = (
            lifecycle.id, intent.id, second_order.id, resolution.id)

    def persisted_snapshot():
        with factory() as session:
            portfolio = session.get(Portfolio, portfolio_id)
            position = session.scalar(select(PortfolioPosition).where(
                PortfolioPosition.portfolio_id == portfolio_id))
            lifecycle = session.get(PositionLifecycleState, lifecycle_id)
            intent = session.get(PositionIntent, intent_id)
            order = session.get(SuggestedOrder, order_id)
            return (
                (portfolio.available_cash, portfolio.version,
                 position.quantity, position.average_cost),
                (lifecycle.initial_fill_id, lifecycle.phase,
                 lifecycle.state_version, lifecycle.target_shares, lifecycle.closed_at),
                (intent.status, intent.revision, intent.target_shares),
                (order.status, order.filled_quantity, order.revision,
                 order.lifecycle_id, order.intent_id),
                tuple((row.id, row.payload_sha256) for row in session.scalars(
                    select(AccountFillReportRow).where(
                        AccountFillReportRow.portfolio_id == portfolio_id)
                    .order_by(AccountFillReportRow.id))),
                tuple((row.report_id, row.review_signature) for row in session.scalars(
                    select(AccountFillReportReviewRow).where(
                        AccountFillReportReviewRow.portfolio_id == portfolio_id)
                    .order_by(AccountFillReportReviewRow.report_id))),
                tuple((row.id, row.payload_sha256) for row in session.scalars(
                    select(AccountFillReportResolutionRow).where(
                        AccountFillReportResolutionRow.portfolio_id == portfolio_id)
                    .order_by(AccountFillReportResolutionRow.id))),
                tuple((row.resolution_id, row.review_signature) for row in session.scalars(
                    select(AccountFillResolutionReviewRow).where(
                        AccountFillResolutionReviewRow.portfolio_id == portfolio_id)
                    .order_by(AccountFillResolutionReviewRow.resolution_id))),
                tuple((row.report_id, row.fill_event_id, row.ledger_movement_id)
                      for row in session.scalars(select(AccountFillPostingRow).where(
                          AccountFillPostingRow.portfolio_id == portfolio_id)
                          .order_by(AccountFillPostingRow.report_id))),
                tuple((row.id, row.event_type, row.reverses_fill_id)
                      for row in session.scalars(select(OrderFillEvent).where(
                          OrderFillEvent.portfolio_id == portfolio_id)
                          .order_by(OrderFillEvent.id))),
                tuple((row.id, row.kind, row.supersedes_id, row.cash_delta)
                      for row in session.scalars(select(AccountLedgerMovementRow).where(
                          AccountLedgerMovementRow.portfolio_id == portfolio_id)
                          .order_by(AccountLedgerMovementRow.id))),
                tuple(session.scalars(select(PositionIntentRevision.id).where(
                    PositionIntentRevision.intent_id == intent_id)
                    .order_by(PositionIntentRevision.revision_no))),
                tuple(session.scalars(select(AccountFillPostingVoidRow.id).where(
                    AccountFillPostingVoidRow.portfolio_id == portfolio_id))),
                tuple(session.scalars(select(AccountFillPostingCorrectionRow.id).where(
                    AccountFillPostingCorrectionRow.portfolio_id == portfolio_id))),
            )

    before = persisted_snapshot()
    with factory() as session:
        impact = load_local_lifecycle_revision_impact(
            session, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id)
        assert impact.status == "UNKNOWN"
        assert impact.paths == () and impact.earliest_impact_at is None
        assert ("FILL_REVISION_VOID_NOT_APPLIED" if action == "VOID"
                else "FILL_REVISION_REPORT_UNPOSTED") in impact.issues
        assert not session.new and not session.dirty
    assert persisted_snapshot() == before
    with factory() as session:
        stage = AccountFillPostingStage(session)
        with pytest.raises(ValueError, match="lifecycle-linked fill reversal"):
            if action == "VOID":
                stage._stage_reviewed_void(
                    portfolio_id, resolution_id, expected_order_revision=2)
            else:
                stage._stage_reviewed_correction(
                    portfolio_id, resolution_id, expected_order_revision=2)
        session.rollback()
    assert persisted_snapshot() == before
