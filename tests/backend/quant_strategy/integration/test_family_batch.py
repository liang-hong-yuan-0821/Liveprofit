# test-catalog-begin
# {
#   "purpose": "量化策略 / family_batch（批次）：Real PG tests exclusively using conftest's liveprofit_quant_strategy_test.",
#   "keywords": [
#     "量化策略",
#     "批次",
#     "现金",
#     "并发",
#     "策略族",
#     "成交",
#     "不可变历史",
#     "迁移",
#     "订单",
#     "重放",
#     "复用",
#     "family_batch",
#     "batch",
#     "cash",
#     "concurrent",
#     "family",
#     "fill",
#     "immutable",
#     "migration",
#     "order",
#     "replay",
#     "share"
#   ],
#   "covers": [
#     "backend/modules/investment_workspace/application/fill_reports.py",
#     "backend/modules/investment_workspace/infrastructure/models.py",
#     "backend/modules/quant_strategy/application/family_batch.py",
#     "backend/modules/quant_strategy/application/strategy_admission.py",
#     "backend/modules/quant_strategy/domain/portfolio_targets.py",
#     "backend/modules/quant_strategy/infrastructure/admission_models.py",
#     "backend/modules/quant_strategy/infrastructure/allocation_models.py",
#     "backend/modules/quant_strategy/infrastructure/lifecycle_models.py",
#     "backend/modules/quant_strategy/infrastructure/models.py"
#   ],
#   "environment": [
#     "db"
#   ]
# }
# test-catalog-end

"""Real PG tests exclusively using conftest's liveprofit_quant_strategy_test."""

from tests.backend.quant_strategy.support.family_batch import (
    seed,
    args,
)
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import select, text, inspect
from sqlalchemy.exc import DBAPIError

from backend.modules.investment_workspace.infrastructure.models import Portfolio, PortfolioPosition
from backend.modules.investment_workspace.application.fill_reports import AccountFillReportService
from backend.modules.quant_strategy.infrastructure.models import QuantStrategy, QuantStrategyVersion
from backend.modules.quant_strategy.infrastructure.lifecycle_models import SuggestedOrder
from backend.modules.quant_strategy.infrastructure.admission_models import StrategyAdmissionEvent
from backend.modules.quant_strategy.infrastructure.allocation_models import AllocationBatch, AllocationMember
from backend.modules.quant_strategy.application.family_batch import FamilyBatchService, CN_TIME
from backend.modules.quant_strategy.application.strategy_admission import StrategyAdmissionService
from backend.modules.quant_strategy.domain.portfolio_targets import PortfolioTargetIntent, TargetLeg






def test_joint_ranking_audit_and_order_independent_replay(env):
    account, versions = seed(env)
    request = args(account, versions)
    with env["session_factory"]() as session:
        service = FamilyBatchService(session)
        batch = service.project(**request)
        allocation = batch.result["allocation"]
        assert batch.status == "PROJECTED"
        assert allocation["family_budgets"] == [["high", "4000.00"], ["low", "4000.00"]]
        assert allocation["targets"][0]["family_id"] == "high"
        assert D(allocation["targets"][0]["max_add_notional"]) == 4000
        assert allocation["conflicts"] == [["000001.SZ", "low", "RANKING_CONFLICT"]]
        assert D(allocation["uncommitted_capacity"]) == 4000
        session.commit()
        replay = service.project(**{**request, "intents": tuple(reversed(request["intents"])),
                                    "expected_version_ids": tuple(reversed(versions))})
        assert replay.id == batch.id
        members = list(session.scalars(select(AllocationMember).where(AllocationMember.batch_id == batch.id)))
        assert len(members) == 2 and all(m.admission_event_id for m in members)
        assert session.scalar(select(SuggestedOrder.id).where(SuggestedOrder.portfolio_id == account)) is None
        with pytest.raises(ValueError, match="different inputs"):
            service.project(**{**request, "closes": {"000001.SZ": D(11)}})


@pytest.mark.parametrize("case", ["missing_profile", "suspended", "family_mismatch"])
def test_one_ineligible_member_blocks_whole_batch_without_reallocating(env, case):
    account, versions = seed(env, profile=None if case == "missing_profile" else "BALANCED")
    request = args(account, versions)
    with env["session_factory"]() as session:
        if case == "suspended":
            StrategyAdmissionService(session).record_restriction(versions[0], family_id="low", asset_scope="CN_STOCK",
                risk_profile="BALANCED", state="SUSPENDED", reason="fixture suspend", request_key="suspend", expected_revision=1)
            session.commit()
        if case == "family_mismatch":
            from dataclasses import replace
            request["intents"] = (replace(request["intents"][0], family_id="fake"), request["intents"][1])
        batch = FamilyBatchService(session).project(**request)
        assert batch.status == "BLOCKED" and batch.result["allocation"] is None
        assert batch.result["blocks"]
        session.commit()


def test_unverified_fill_report_blocks_new_family_allocation(env):
    account, versions = seed(env)
    with env["session_factory"]() as session:
        captured_at = datetime.now(timezone.utc)
        AccountFillReportService(session).record(
            account, order_id=None, market="CN", symbol="000001.SZ", side="BUY",
            fill_trade_date=captured_at.astimezone(CN_TIME).date(),
            captured_at=captured_at, quantity="1", fill_price="10", fee=None,
            source_type="MANUAL_REPORT", source_ref="unmatched-report")
        session.commit()
    with env["session_factory"]() as session:
        batch = FamilyBatchService(session).project(**args(account, versions))
        assert batch.status == "BLOCKED"
        assert batch.result["allocation"] is None
        assert {"code": "BUY_REJECTED_ACCOUNT_RECONCILIATION"} in batch.result["blocks"]
        session.commit()


def test_unknown_owner_and_pending_order_consume_capacity(env):
    account, versions = seed(env)
    with env["session_factory"]() as session:
        session.add(PortfolioPosition(portfolio_id=account, market="CN", symbol="000001.SZ", quantity=D(300), average_cost=D(9)))
        session.add(SuggestedOrder(id=uuid4(), portfolio_id=account, market="CN", symbol="000001.SZ",
            side="BUY", quantity=D(200), filled_quantity=D(50), limit_price=D(10), stop_price=D(9),
            reserved_cash=D(2000), reserved_risk=D(200), reason_code="MANUAL", status="PARTIALLY_FILLED", revision=1))
        session.commit()
        batch = FamilyBatchService(session).project(**args(account, versions, same_symbol=False))
        allocation = batch.result["allocation"]
        targets = {t["symbol"]: t for t in allocation["targets"]}
        assert targets["000001.SZ"]["family_id"] is None
        assert D(targets["000001.SZ"]["max_add_notional"]) == 0
        assert D(targets["000002.SZ"]["max_add_notional"]) == 3500
        assert D(batch.result["exposures"][0]["pending_buy_notional"]) == 1500
        session.commit()


def test_missing_member_is_not_an_empty_cash_family(env):
    account, versions = seed(env)
    request = args(account, versions)
    with env["session_factory"]() as session:
        with pytest.raises(ValueError, match="complete unique"):
            FamilyBatchService(session).project(**{**request, "intents": request["intents"][:1]})
        assert session.scalar(select(AllocationBatch.id).where(AllocationBatch.portfolio_id == account)) is None


def test_missing_holding_valuation_blocks_instead_of_using_cost(env):
    account, versions = seed(env)
    with env["session_factory"]() as session:
        session.add(PortfolioPosition(portfolio_id=account, market="CN", symbol="600000.SH", quantity=D(100), average_cost=D(9)))
        session.commit()
        batch = FamilyBatchService(session).project(**args(account, versions))
        assert batch.status == "BLOCKED"
        assert {"symbol": "600000.SH", "code": "VALUATION_UNAVAILABLE"} in batch.result["blocks"]
        session.commit()


def test_concurrent_batch_requests_share_one_immutable_result(env):
    account, versions = seed(env)
    barrier = Barrier(2)
    def project(_):
        with env["session_factory"]() as session:
            barrier.wait(timeout=5)
            batch = FamilyBatchService(session).project(**args(account, versions))
            session.commit()
            return batch.id
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(project, range(2)))
    assert ids[0] == ids[1]
    with env["session_factory"]() as session:
        with pytest.raises(DBAPIError, match="immutable"):
            session.execute(text("UPDATE quant_allocation_batches SET status='BLOCKED' WHERE id=:id"), {"id": ids[0]})
        session.rollback()


def test_allocation_orm_matches_migration_constraints(env):
    with env["session_factory"]() as session:
        inspector = inspect(session.connection())
        for model in (AllocationBatch, AllocationMember):
            table = model.__table__
            for cls, method in (("CheckConstraint", inspector.get_check_constraints), ("UniqueConstraint", inspector.get_unique_constraints)):
                assert {c.name for c in table.constraints if c.__class__.__name__ == cls} == {c["name"] for c in method(table.name)}
            assert {c.name for c in table.columns if not c.nullable} == {c["name"] for c in inspector.get_columns(table.name) if not c["nullable"]}


def test_nonmember_owner_family_evidence_after_decision_stays_unknown(env):
    from backend.modules.quant_strategy.infrastructure.lifecycle_models import LifecyclePolicyVersion, PositionLifecycleState
    account, versions = seed(env)
    with env["session_factory"]() as session:
        strategy = QuantStrategy(id=uuid4(), name=str(uuid4()), version=1)
        session.add(strategy)
        session.flush()
        owner = QuantStrategyVersion(id=uuid4(), strategy_id=strategy.id, version_no=1, status="PUBLISHED",
            source_code="def strategy(context): return {}", source_hash="b"*64, version=1)
        policy = LifecyclePolicyVersion(id=uuid4(), policy_key=str(uuid4()), version_no=1, status="PUBLISHED", content_hash="c"*64)
        session.add_all([owner, policy])
        session.flush()
        session.add(StrategyAdmissionEvent(strategy_version_id=owner.id, family_id="low", asset_scope="CN_STOCK",
            risk_profile="BALANCED", revision=1, state="EXPERIMENTAL", reason="future fixture", request_key="future",
            recorded_at=datetime.now(timezone.utc)+timedelta(days=1)))
        session.add(PortfolioPosition(portfolio_id=account, market="CN", symbol="600000.SH", quantity=D(100), average_cost=D(10)))
        session.add(PositionLifecycleState(portfolio_id=account, market="CN", symbol="600000.SH",
            strategy_version_id=owner.id, lifecycle_policy_version_id=policy.id))
        session.commit()
        request = args(account, versions)
        request["closes"]["600000.SH"] = D(10)
        batch = FamilyBatchService(session).project(**request)
        exposure = next(e for e in batch.result["exposures"] if e["symbol"] == "600000.SH")
        assert exposure["family_id"] is None and exposure["strategy_version_id"] is None
        assert D(exposure["held_notional"]) == 1000
        session.commit()
