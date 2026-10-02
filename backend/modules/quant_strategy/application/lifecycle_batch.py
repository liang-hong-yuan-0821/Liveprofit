"""Consume a joint batch for deferred lifecycle BUYs in the caller's transaction."""
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select

from backend.modules.quant_strategy.domain.portfolio_targets import PortfolioTargetIntent, TargetLeg
from backend.modules.quant_strategy.infrastructure.allocation_models import AllocationBatch, AllocationMember, AllocationOutcome
from backend.modules.quant_strategy.infrastructure.lifecycle_models import (
    PositionDailyFact, PositionLifecycleState, PositionIntent, SuggestedOrder,
)
from backend.modules.investment_workspace.infrastructure.models import PortfolioPosition
from .errors import FillValidationError
from .family_batch import FamilyBatchService
from .planning_account import lock_portfolio
from .position_lifecycle_manager import LifecycleDecision, PositionLifecycleManager


def _intent(payload):
    values = dict(payload)
    values["strategy_version_id"] = UUID(values["strategy_version_id"])
    for field in ("evaluation_as_of", "decision_date", "valid_until"):
        values[field] = date.fromisoformat(values[field])
    values["legs"] = tuple(TargetLeg(
        leg["ts_code"], Decimal(leg["family_weight"]),
        Decimal(leg["entry_lower"]) if leg["entry_lower"] is not None else None,
        Decimal(leg["entry_upper"]) if leg["entry_upper"] is not None else None,
    ) for leg in values["legs"])
    return PortfolioTargetIntent(**values)


class LifecycleBatchService:
    def __init__(self, session):
        self.session = session

    def materialize(self, *, daily_id: UUID, batch_id: UUID, buy_context: dict):
        """At most one outcome for the deferred day, even after rejection.

        Does not commit; caller must commit/rollback outcome and order together.
        Replay returns the original result, never recreates a cancelled order.
        """
        portfolio_id = self.session.scalar(select(PositionLifecycleState.portfolio_id).join(
            PositionDailyFact, PositionDailyFact.lifecycle_id == PositionLifecycleState.id,
        ).where(PositionDailyFact.id == daily_id))
        if portfolio_id is None:
            raise FillValidationError("延期日事实不存在")
        portfolio = lock_portfolio(self.session, portfolio_id)
        lifecycle = self.session.scalar(select(PositionLifecycleState).join(
            PositionDailyFact, PositionDailyFact.lifecycle_id == PositionLifecycleState.id,
        ).where(PositionDailyFact.id == daily_id).with_for_update(of=PositionLifecycleState)
            .execution_options(populate_existing=True))
        daily = self.session.scalar(select(PositionDailyFact).where(PositionDailyFact.id == daily_id)
            .with_for_update().execution_options(populate_existing=True))
        saved = daily.planning_result or {}
        if saved.get("stage") == "BATCH_COMPLETED":
            if saved.get("batch_id") != str(batch_id):
                raise FillValidationError("延期日事实已由另一批次处理")
            return self.session.get(SuggestedOrder, UUID(saved["order_id"])) if saved.get("order_id") else None
        if saved.get("stage") != "AWAITING_BATCH":
            raise FillValidationError("日事实不是待批次加仓")
        batch = self.session.get(AllocationBatch, batch_id)
        if batch is None or batch.portfolio_id != portfolio_id or batch.valuation_date != daily.trade_date:
            raise FillValidationError("批次账户或估值日不匹配")
        members = list(self.session.scalars(select(AllocationMember).where(AllocationMember.batch_id == batch_id)))
        if lifecycle.strategy_version_id not in {m.strategy_version_id for m in members}:
            raise FillValidationError("批次不包含冻结持仓版本")

        def reject(code, **audit):
            daily.planning_result = {**saved, "stage": "BATCH_COMPLETED", "batch_id": str(batch_id),
                                     "order_id": None, "projection": {"order_status": code}, **audit}
            self.session.flush()
            return None

        if (lifecycle.closed_at is not None or lifecycle.state_version != daily.state_version_after
                or lifecycle.target_shares != daily.final_target_shares):
            return reject("BUY_REJECTED_STALE_LIFECYCLE")
        outcome = self.session.get(AllocationOutcome, batch_id)
        if outcome is not None and outcome.status == "BLOCKED":
            return reject("BUY_REJECTED_BATCH_TERMINAL")
        if (daily.price_basis != "raw" or buy_context.get("valuation_date") != daily.trade_date
                or buy_context.get("asset_scope") != batch.asset_scope):
            raise FillValidationError("批次执行上下文不匹配")
        closes = buy_context.get("closes", {})
        if (lifecycle.symbol not in closes
                or Decimal(str(closes[lifecycle.symbol])) != Decimal(str(daily.input_payload.get("close")))):
            raise FillValidationError("生命周期原价与批次估值不一致")
        if batch.status != "PROJECTED":
            return reject("BUY_REJECTED_BATCH")
        # Recompute all family budgets using current qualification and exposure.
        # The original audit is immutable and conveys no reservation rights.
        fresh = FamilyBatchService(self.session).project(
            portfolio_id=portfolio_id, request_key=f"consume:{batch_id}:{daily_id}", asset_scope=batch.asset_scope,
            expected_version_ids=tuple(m.strategy_version_id for m in members),
            intents=tuple(_intent(m.intent) for m in members), valuation_date=daily.trade_date,
            allow_replay=False, closes=closes, protective_targets={s: Decimal(v) for s, v in batch.result["protective_targets"].items()},
        )
        audit = {"allocation_batch_id": str(fresh.id)}
        if fresh.status != "PROJECTED":
            return reject("BUY_REJECTED_BATCH_ADMISSION", **audit)
        target = next((t for t in fresh.result["allocation"]["targets"] if t["symbol"] == lifecycle.symbol), None)
        if (target is None or target["strategy_version_id"] != str(lifecycle.strategy_version_id)
                or Decimal(target["max_add_notional"]) <= 0):
            return reject("BUY_REJECTED_FAMILY_BUDGET", **audit)
        position = self.session.scalar(select(PortfolioPosition).where(PortfolioPosition.id == lifecycle.position_id)
            .with_for_update().execution_options(populate_existing=True))
        if position is None or position.quantity <= 0 or position.quantity >= daily.final_target_shares:
            return reject("BUY_REJECTED_TARGET_ALREADY_MET", **audit)
        member = next(m for m in members if m.strategy_version_id == lifecycle.strategy_version_id)
        leg = next((leg for leg in _intent(member.intent).legs if leg.ts_code == lifecycle.symbol), None)
        if leg is None:
            return reject("BUY_REJECTED_FAMILY_BUDGET", **audit)
        fields = dict(saved["decision"])
        for field in ("target_exposure_pct", "target_shares", "high_water_mark", "active_stop_price"):
            if fields[field] is not None:
                fields[field] = Decimal(fields[field])
        decision = LifecycleDecision(**fields)
        _intent_row, order = PositionLifecycleManager(self.session)._materialize_delta(
            lifecycle, position, decision, daily.trade_date, daily.input_payload,
            PositionIntent=PositionIntent, SuggestedOrder=SuggestedOrder, portfolio=portfolio, daily=daily,
            buy_context={**buy_context, "max_add_notional": Decimal(target["max_add_notional"]),
                         "entry_lower": leg.entry_lower, "entry_upper": leg.entry_upper},
        )
        daily.planning_result = {**(daily.planning_result or saved), "stage": "BATCH_COMPLETED",
            "batch_id": str(batch_id), **audit, "order_id": str(order.id) if order else None}
        self.session.flush()
        return order
