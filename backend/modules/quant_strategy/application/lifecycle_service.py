"""Transactional suggested-order and fill journal service (N5).

No API call places a broker order.  These methods only record manual/broker
confirmations and atomically project confirmed fills into the real portfolio.
"""

from __future__ import annotations

import uuid
import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.modules.investment_workspace.infrastructure.models import Portfolio, PortfolioPosition
from backend.modules.analysis.infrastructure.models import AnalysisTask
from backend.modules.quant_strategy.application.errors import (
    FillValidationError,
    LifecycleInvalidStateError,
    LifecycleNotFoundError,
    LifecycleRevisionConflictError,
)
from backend.modules.quant_strategy.infrastructure.lifecycle_models import (
    OrderFillEvent,
    LifecyclePolicyVersion,
    PositionLifecycleState,
    PositionIntent,
    PositionDailyFact,
    PositionExpectation,
    PositionTrailingStop,
    SuggestedOrder,
)
from backend.modules.quant_strategy.infrastructure.models import QuantStrategyVersion
from backend.modules.quant_strategy.infrastructure.signals import QuantExecutionSignal

ACTIVE_ORDER_STATUSES = {"PROPOSED", "EXECUTING", "PARTIALLY_FILLED"}


class LifecyclePolicyService:
    def __init__(self, session) -> None:
        self._session = session

    def list_versions(self) -> list[LifecyclePolicyVersion]:
        return list(self._session.execute(
            select(LifecyclePolicyVersion).order_by(
                LifecyclePolicyVersion.policy_key, LifecyclePolicyVersion.version_no.desc()
            )
        ).scalars())

    def publish(self, *, policy_key: str, required_fields: list[str], config: dict) -> LifecyclePolicyVersion:
        self._validate_trailing(config)
        normalized = {
            "required_fields": sorted(set(required_fields)),
            "config": config,
        }
        digest = hashlib.sha256(
            json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        latest = self._session.execute(
            select(LifecyclePolicyVersion).where(LifecyclePolicyVersion.policy_key == policy_key)
            .order_by(LifecyclePolicyVersion.version_no.desc()).limit(1).with_for_update()
        ).scalar_one_or_none()
        if latest is not None and latest.content_hash == digest:
            return latest
        version = LifecyclePolicyVersion(
            id=uuid.uuid4(), policy_key=policy_key,
            version_no=(latest.version_no + 1) if latest else 1,
            status="PUBLISHED", required_fields=normalized["required_fields"], config=config,
            content_hash=digest, published_at=datetime.now(timezone.utc),
        )
        self._session.add(version)
        self._session.commit()
        return version

    @staticmethod
    def _validate_trailing(config: dict) -> None:
        trailing = config.get("trailing_stop")
        if trailing is None:
            return
        if not isinstance(trailing, dict) or not {"b", "a", "d"}.issubset(trailing):
            raise FillValidationError("trailing_stop 必须同时提供 b/a/d")
        b, a, d = (_d(trailing[k]) for k in ("b", "a", "d"))
        if not (Decimal(0) < b <= a and Decimal(0) < d < Decimal(1)):
            raise FillValidationError("移动止损必须满足 0<b≤a 且 0<d<1")


def _d(value) -> Decimal:
    value = Decimal(str(value))
    if not value.is_finite():
        raise FillValidationError("成交数值必须有限")
    return value


class LifecycleOrderService:
    def __init__(self, session) -> None:
        self._session = session

    def list_orders(self, portfolio_id: uuid.UUID) -> list[SuggestedOrder]:
        return list(self._session.execute(
            select(SuggestedOrder).where(SuggestedOrder.portfolio_id == portfolio_id)
            .order_by(SuggestedOrder.created_at.desc(), SuggestedOrder.id.desc())
        ).scalars())

    def list_fills(self, order_id: uuid.UUID) -> list[OrderFillEvent]:
        return list(self._session.execute(
            select(OrderFillEvent).where(OrderFillEvent.order_id == order_id)
            .order_by(OrderFillEvent.created_at.asc(), OrderFillEvent.id.asc())
        ).scalars())

    def confirm_fill(
        self, order_id: uuid.UUID, *, quantity, fill_price, fill_trade_date: date,
        idempotency_key: str, expected_revision: int, source: str = "MANUAL", note: str | None = None,
    ) -> tuple[OrderFillEvent, SuggestedOrder]:
        existing = self._by_idempotency(idempotency_key)
        if existing is not None:
            return existing, self._get_order(existing.order_id)
        qty, price = _d(quantity), _d(fill_price)
        if qty <= 0 or price <= 0:
            raise FillValidationError("成交数量与价格必须大于 0")
        order = self._lock_order(order_id, expected_revision)
        if order.status not in ACTIVE_ORDER_STATUSES:
            raise LifecycleInvalidStateError(f"订单状态 {order.status} 不允许确认成交")
        remaining = _d(order.quantity) - _d(order.filled_quantity)
        if qty > remaining:
            raise FillValidationError("成交数量超过订单剩余量")
        event = OrderFillEvent(
            order_id=order.id, portfolio_id=order.portfolio_id, position_id=order.position_id,
            event_type="CONFIRM", quantity=qty, fill_price=price, fill_trade_date=fill_trade_date,
            source=source, idempotency_key=idempotency_key, note=note,
        )
        try:
            self._apply_delta(order, qty, price)
            self._session.add(event)
            self._session.flush()
            event.position_id = order.position_id
            self._initialize_lifecycle_from_first_fill(order, event, qty, price)
            self._session.commit()
        except IntegrityError:
            self._session.rollback()
            replay = self._by_idempotency(idempotency_key)
            if replay is not None:
                return replay, self._get_order(replay.order_id)
            raise
        return event, order

    def _initialize_lifecycle_from_first_fill(
        self, order: SuggestedOrder, event: OrderFillEvent, quantity: Decimal, price: Decimal,
    ) -> PositionLifecycleState | None:
        """Create lifecycle state only from a real first BUY fill with an explicit policy."""
        if order.side != "BUY" or order.source_signal_id is None:
            return None
        active = self._session.execute(
            select(PositionLifecycleState).where(
                PositionLifecycleState.portfolio_id == order.portfolio_id,
                PositionLifecycleState.market == order.market,
                PositionLifecycleState.symbol == order.symbol,
                PositionLifecycleState.closed_at.is_(None),
            ).with_for_update()
        ).scalar_one_or_none()
        if active is not None:
            order.lifecycle_id = active.id
            return active

        signal = self._session.get(QuantExecutionSignal, order.source_signal_id)
        task = self._session.get(AnalysisTask, signal.task_id) if signal is not None else None
        snapshot = (task.request_params or {}).get("execution_snapshot", {}) if task is not None else {}
        strategy_snapshot = snapshot.get("strategy", {})
        policy_snapshot = strategy_snapshot.get("lifecycle_policy")
        if not policy_snapshot:
            return None
        try:
            strategy_version_id = uuid.UUID(str(strategy_snapshot["version_id"]))
            policy_version_id = uuid.UUID(str(policy_snapshot["id"]))
        except (KeyError, TypeError, ValueError):
            raise LifecycleInvalidStateError("生命周期策略快照缺少有效版本标识") from None
        version = self._session.get(QuantStrategyVersion, strategy_version_id)
        policy = self._session.get(LifecyclePolicyVersion, policy_version_id)
        if (
            version is None or policy is None or policy.status != "PUBLISHED"
            or version.lifecycle_policy_version_id != policy.id
            or policy.content_hash != policy_snapshot.get("content_hash")
        ):
            raise LifecycleInvalidStateError("生命周期策略快照与已发布版本不一致")
        if order.stop_price is None or price <= _d(order.stop_price):
            raise LifecycleInvalidStateError("首笔成交价必须高于冻结初始止损")

        config = dict(policy.config or {})
        template_id = config.get("template_id") or version.template_id
        if not template_id:
            raise LifecycleInvalidStateError("生命周期策略必须显式声明 template_id")
        planned_capacity = _d(signal.shares) if signal.shares is not None else _d(order.quantity) * Decimal(2)
        portfolio_snapshot = snapshot.get("portfolio", {})
        risk_snapshot = portfolio_snapshot.get("risk", {})
        if portfolio_snapshot.get("total_assets") is not None and risk_snapshot.get("risk_per_trade_pct") is not None:
            risk_budget = _d(portfolio_snapshot["total_assets"]) * _d(risk_snapshot["risk_per_trade_pct"])
            repriced_capacity = risk_budget / (price - _d(order.stop_price))
            capacity = min(planned_capacity, repriced_capacity)
        else:
            capacity = planned_capacity
        capacity = (capacity / Decimal(100)).to_integral_value(rounding="ROUND_FLOOR") * Decimal(100)
        if capacity <= 0:
            raise LifecycleInvalidStateError("冻结风险容量无效")
        target = (capacity * Decimal("0.50") / Decimal(100)).to_integral_value(
            rounding="ROUND_FLOOR"
        ) * Decimal(100)
        reward = _d(config.get(
            "reward_multiple", (version.template_params or {}).get("reward_multiple", "2")
        ))
        if reward <= 0:
            raise LifecycleInvalidStateError("生命周期 reward_multiple 必须大于 0")
        lifecycle_seed = (signal.execution_market or {}).get("lifecycle_seed") or {}
        if template_id == "arc_bottom_75a_v1" and lifecycle_seed.get("arc_neckline_price") is None:
            raise LifecycleInvalidStateError("圆弧底生命周期缺少成交前冻结颈线")
        lifecycle = PositionLifecycleState(
            id=uuid.uuid4(), portfolio_id=order.portfolio_id, position_id=order.position_id,
            market=order.market, symbol=order.symbol,
            strategy_version_id=version.id, lifecycle_policy_version_id=policy.id,
            initial_fill_id=event.id, initial_fill_price=price,
            initial_stop_price=_d(order.stop_price), risk_capacity_shares=capacity,
            target_exposure_pct=Decimal("0.50"), target_shares=target,
            phase="INITIALIZED" if quantity >= target else "ENTRY_PENDING",
            profit_take_price=price + reward * (price - _d(order.stop_price)),
            state_version=1,
            arc_neckline_price=_d(lifecycle_seed["arc_neckline_price"])
            if lifecycle_seed.get("arc_neckline_price") is not None else None,
        )
        self._session.add(lifecycle)
        self._session.flush()
        order.lifecycle_id = lifecycle.id

        trailing = config.get("trailing_stop")
        if trailing is not None:
            LifecyclePolicyService._validate_trailing(config)
            self._session.add(PositionTrailingStop(
                id=uuid.uuid4(), lifecycle_id=lifecycle.id,
                initial_stop_price=_d(order.stop_price), high_water_mark=price,
                active_stop_price=_d(order.stop_price), phase="PROTECT",
                config_snapshot={k: str(trailing[k]) for k in ("b", "a", "d")},
            ))
        if template_id == "ma5_pre_cross_v1":
            self._session.add(PositionExpectation(
                id=uuid.uuid4(), lifecycle_id=lifecycle.id,
                fill_trade_date=event.fill_trade_date,
                window_trading_days=int(config.get("confirmation_window_trading_days", 3)),
                observed_trading_days=0, status="PENDING",
            ))
        return lifecycle

    def correct_fill(
        self, fill_id: uuid.UUID, *, quantity, fill_price, fill_trade_date: date,
        idempotency_key: str, expected_revision: int, note: str | None = None,
    ) -> tuple[OrderFillEvent, SuggestedOrder]:
        existing = self._by_idempotency(idempotency_key)
        if existing is not None:
            return existing, self._get_order(existing.order_id)
        original = self._lock_fill(fill_id)
        self._ensure_not_reversed(fill_id)
        order = self._lock_order(original.order_id, expected_revision, allow_terminal=True)
        qty, price = _d(quantity), _d(fill_price)
        if qty <= 0 or price <= 0:
            raise FillValidationError("更正后的数量与价格必须大于 0")
        projected = _d(order.filled_quantity) - _d(original.quantity) + qty
        if projected < 0 or projected > _d(order.quantity):
            raise FillValidationError("更正后的累计成交量超出订单范围")
        self._apply_delta(order, -_d(original.quantity), _d(original.fill_price), advance_version=False)
        self._apply_delta(order, qty, price)
        event = OrderFillEvent(
            order_id=order.id, portfolio_id=order.portfolio_id, position_id=order.position_id,
            event_type="CORRECT", reverses_fill_id=original.id, quantity=qty, fill_price=price,
            fill_trade_date=fill_trade_date, source=original.source,
            idempotency_key=idempotency_key, note=note,
        )
        self._session.add(event)
        try:
            self._session.commit()
        except IntegrityError:
            self._session.rollback()
            replay = self._by_idempotency(idempotency_key)
            if replay is not None:
                return replay, self._get_order(replay.order_id)
            raise
        return event, order

    def void_fill(
        self, fill_id: uuid.UUID, *, idempotency_key: str, expected_revision: int,
        fill_trade_date: date, note: str | None = None,
    ) -> tuple[OrderFillEvent, SuggestedOrder]:
        existing = self._by_idempotency(idempotency_key)
        if existing is not None:
            return existing, self._get_order(existing.order_id)
        original = self._lock_fill(fill_id)
        self._ensure_not_reversed(fill_id)
        order = self._lock_order(original.order_id, expected_revision, allow_terminal=True)
        self._apply_delta(order, -_d(original.quantity), _d(original.fill_price))
        event = OrderFillEvent(
            order_id=order.id, portfolio_id=order.portfolio_id, position_id=order.position_id,
            event_type="VOID", reverses_fill_id=original.id, quantity=original.quantity,
            fill_price=original.fill_price, fill_trade_date=fill_trade_date,
            source=original.source, idempotency_key=idempotency_key, note=note,
        )
        self._session.add(event)
        try:
            self._session.commit()
        except IntegrityError:
            self._session.rollback()
            replay = self._by_idempotency(idempotency_key)
            if replay is not None:
                return replay, self._get_order(replay.order_id)
            raise
        return event, order

    def set_order_status(self, order_id: uuid.UUID, *, status: str, expected_revision: int) -> SuggestedOrder:
        allowed = {"EXECUTING", "REJECTED", "CANCELLED", "RECONCILIATION_REQUIRED", "SUPERSEDED"}
        if status not in allowed:
            raise FillValidationError("不支持的订单状态")
        order = self._lock_order(order_id, expected_revision)
        order.status = status
        order.revision += 1
        order.updated_at = datetime.now(timezone.utc)
        self._session.commit()
        return order

    def _by_idempotency(self, key: str) -> OrderFillEvent | None:
        if not key or len(key) > 128:
            raise FillValidationError("idempotency_key 长度必须为 1..128")
        return self._session.execute(
            select(OrderFillEvent).where(OrderFillEvent.idempotency_key == key)
        ).scalar_one_or_none()

    def _get_order(self, order_id: uuid.UUID) -> SuggestedOrder:
        order = self._session.get(SuggestedOrder, order_id)
        if order is None:
            raise LifecycleNotFoundError("建议订单不存在")
        return order

    def _lock_order(self, order_id: uuid.UUID, expected_revision: int, *, allow_terminal: bool = False) -> SuggestedOrder:
        order = self._session.execute(
            select(SuggestedOrder).where(SuggestedOrder.id == order_id).with_for_update()
        ).scalar_one_or_none()
        if order is None:
            raise LifecycleNotFoundError("建议订单不存在")
        if order.revision != expected_revision:
            raise LifecycleRevisionConflictError("建议订单 revision 已变化，请刷新后重试")
        if not allow_terminal and order.status not in ACTIVE_ORDER_STATUSES:
            raise LifecycleInvalidStateError(f"订单状态 {order.status} 不允许该操作")
        return order

    def _lock_fill(self, fill_id: uuid.UUID) -> OrderFillEvent:
        event = self._session.execute(
            select(OrderFillEvent).where(OrderFillEvent.id == fill_id).with_for_update()
        ).scalar_one_or_none()
        if event is None:
            raise LifecycleNotFoundError("成交事件不存在")
        if event.event_type == "VOID":
            raise LifecycleInvalidStateError("撤销事件不能再次冲销")
        return event

    def _ensure_not_reversed(self, fill_id: uuid.UUID) -> None:
        if self._session.execute(
            select(OrderFillEvent.id).where(OrderFillEvent.reverses_fill_id == fill_id)
        ).scalar_one_or_none() is not None:
            raise LifecycleInvalidStateError("成交事件已被更正或撤销")

    def _apply_delta(
        self, order: SuggestedOrder, signed_qty: Decimal, price: Decimal, *, advance_version: bool = True
    ) -> None:
        portfolio = self._session.execute(
            select(Portfolio).where(Portfolio.id == order.portfolio_id).with_for_update()
        ).scalar_one()
        position = self._session.execute(
            select(PortfolioPosition).where(
                PortfolioPosition.portfolio_id == order.portfolio_id,
                PortfolioPosition.market == order.market,
                PortfolioPosition.symbol == order.symbol,
            ).with_for_update()
        ).scalar_one_or_none()
        direction = Decimal(1) if order.side == "BUY" else Decimal(-1)
        position_delta = direction * signed_qty
        cash_delta = -direction * signed_qty * price
        old_qty = _d(position.quantity) if position is not None else Decimal(0)
        new_qty = old_qty + position_delta
        if new_qty < 0:
            raise FillValidationError("卖出成交超过实际持仓")
        if _d(portfolio.available_cash) + cash_delta < 0:
            raise FillValidationError("买入成交后可用现金为负")
        if position is None:
            if new_qty <= 0:
                raise FillValidationError("卖出成交缺少实际持仓")
            position = PortfolioPosition(
                portfolio_id=order.portfolio_id, market=order.market, symbol=order.symbol,
                quantity=new_qty, average_cost=price, active_stop_price=order.stop_price,
            )
            self._session.add(position)
            self._session.flush()
            order.position_id = position.id
        else:
            old_cost = _d(position.average_cost)
            if order.side == "BUY":
                if signed_qty > 0:
                    position.average_cost = ((old_qty * old_cost) + (signed_qty * price)) / new_qty
                elif new_qty > 0:
                    position.average_cost = ((old_qty * old_cost) + (signed_qty * price)) / new_qty
            position.quantity = new_qty
            order.position_id = position.id
        portfolio.available_cash = _d(portfolio.available_cash) + cash_delta
        if advance_version:
            portfolio.version += 1
            portfolio.updated_at = datetime.now(timezone.utc)
        order.filled_quantity = _d(order.filled_quantity) + signed_qty
        if order.filled_quantity < 0 or order.filled_quantity > order.quantity:
            raise FillValidationError("累计成交量超出订单范围")
        if advance_version:
            order.status = "FILLED" if order.filled_quantity == order.quantity else (
                "PARTIALLY_FILLED" if order.filled_quantity > 0 else "PROPOSED"
            )
            order.revision += 1
            order.updated_at = datetime.now(timezone.utc)
        if advance_version and order.lifecycle_id is not None:
            lifecycle = self._session.execute(
                select(PositionLifecycleState).where(PositionLifecycleState.id == order.lifecycle_id).with_for_update()
            ).scalar_one_or_none()
            if lifecycle is not None:
                lifecycle.position_id = order.position_id
                lifecycle.state_version += 1
                lifecycle.updated_at = datetime.now(timezone.utc)
                if order.intent_id is not None:
                    intent = self._session.execute(
                        select(PositionIntent).where(PositionIntent.id == order.intent_id).with_for_update()
                    ).scalar_one_or_none()
                    if intent is not None:
                        reached = new_qty == _d(intent.target_shares)
                        intent.status = "COMPLETED" if reached else "EXECUTING"
                        intent.revision += 1
                        if reached:
                            if intent.reason_code == "TEMPLATE_CONFIRM_ADD":
                                lifecycle.confirmation_completed = True
                                lifecycle.phase = "CONFIRMED"
                            elif intent.reason_code == "PROFIT_TARGET_TRIM":
                                lifecycle.profit_trim_completed = True
                                lifecycle.phase = "PROFIT_PROTECTED"
                            elif intent.reason_code in {
                                "INITIAL_STOP_LOSS", "TRAILING_STOP_LOSS", "EXPECTATION_TIMEOUT",
                                "MA_DEATH_CROSS", "PULLBACK_INVALID", "BREAKOUT_INVALID",
                                "MOMENTUM_REVERSAL_FAILED", "VOLUME_CONFIRM_INVALID", "ARC_BOTTOM_INVALID",
                            } and new_qty == 0:
                                lifecycle.phase = "CLOSED"
                                lifecycle.closed_at = datetime.now(timezone.utc)


class LifecycleStateService:
    """Idempotent active-intent and per-day fact primitives consumed by N6."""

    def __init__(self, session) -> None:
        self._session = session

    def get_or_create_intent(
        self, lifecycle_id: uuid.UUID, *, trade_date: date, target_shares,
        reason_code: str, source_signal_id: int | None = None,
    ) -> tuple[PositionIntent, bool]:
        lifecycle = self._session.execute(
            select(PositionLifecycleState).where(PositionLifecycleState.id == lifecycle_id).with_for_update()
        ).scalar_one_or_none()
        if lifecycle is None:
            raise LifecycleNotFoundError("持仓生命周期不存在")
        target = _d(target_shares)
        if target < 0:
            raise FillValidationError("目标数量不能为负")
        active = self._session.execute(
            select(PositionIntent).where(
                PositionIntent.lifecycle_id == lifecycle_id,
                PositionIntent.status.in_(("ACTIVE", "EXECUTING", "RECONCILIATION_REQUIRED")),
            ).with_for_update()
        ).scalar_one_or_none()
        if active is not None:
            if active.target_shares == target and active.reason_code == reason_code:
                return active, False
            if active.status != "ACTIVE":
                raise LifecycleInvalidStateError("已有执行中或待对账意图，禁止生成冲突目标")
            active.status = "SUPERSEDED"
            active.revision += 1
            active.updated_at = datetime.now(timezone.utc)
        intent = PositionIntent(
            id=uuid.uuid4(), lifecycle_id=lifecycle_id, source_signal_id=source_signal_id,
            trade_date=trade_date, target_shares=target, reason_code=reason_code,
            state_version=lifecycle.state_version, status="ACTIVE", revision=1,
        )
        self._session.add(intent)
        self._session.commit()
        return intent, True

    def get_or_create_daily_fact(
        self, lifecycle_id: uuid.UUID, *, trade_date: date, price_basis: str,
        data_as_of: datetime, input_payload: dict, rule_version: str,
        state_version_before: int, state_version_after: int, final_target_shares,
    ) -> tuple[PositionDailyFact, bool]:
        canonical = json.dumps(input_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        input_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        existing = self._session.execute(
            select(PositionDailyFact).where(
                PositionDailyFact.lifecycle_id == lifecycle_id,
                PositionDailyFact.trade_date == trade_date,
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.input_hash != input_hash or existing.rule_version != rule_version:
                raise LifecycleInvalidStateError("同一生命周期交易日已冻结不同事实")
            return existing, False
        fact = PositionDailyFact(
            id=uuid.uuid4(), lifecycle_id=lifecycle_id, trade_date=trade_date,
            price_basis=price_basis, data_as_of=data_as_of, input_payload=input_payload,
            input_hash=input_hash, rule_version=rule_version,
            state_version_before=state_version_before, state_version_after=state_version_after,
            final_target_shares=_d(final_target_shares),
        )
        self._session.add(fact)
        self._session.commit()
        return fact, True
