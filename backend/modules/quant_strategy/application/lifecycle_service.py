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

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from backend.modules.investment_workspace.infrastructure.models import Portfolio, PortfolioPosition
from backend.modules.investment_workspace.infrastructure.account_ledger_models import AccountLedgerBaselineRow
from backend.modules.analysis.infrastructure.models import AnalysisTask
from backend.modules.quant_strategy.application.errors import (
    FillValidationError,
    LifecycleInvalidStateError,
    LifecycleNotFoundError,
    LifecycleRevisionConflictError,
)
from backend.modules.quant_strategy.domain.management_policies import ManagementPolicy
from backend.modules.quant_strategy.domain.family_management import validate_family_policy
from backend.modules.quant_strategy.application.portfolio_policy_contract import (
    portfolio_policy_config, validate_portfolio_policy_config,
)
from backend.modules.quant_strategy.application.lifecycle_intent_replay import CLOSING_EXIT_REASONS
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

ACTIVE_ORDER_STATUSES = {"PROPOSED", "EXECUTING", "PARTIALLY_FILLED", "RECONCILIATION_REQUIRED"}


class LifecyclePolicyService:
    def __init__(self, session) -> None:
        self._session = session

    def list_versions(self) -> list[LifecyclePolicyVersion]:
        return list(self._session.execute(
            select(LifecyclePolicyVersion).order_by(
                LifecyclePolicyVersion.policy_key, LifecyclePolicyVersion.version_no.desc()
            )
        ).scalars())

    def publish_management(self, policy: ManagementPolicy) -> LifecyclePolicyVersion:
        """Store a typed policy in the existing immutable lifecycle version ledger."""
        if not isinstance(policy, ManagementPolicy):
            raise FillValidationError("management policy must be validated before publication")
        config = {"management_policy": policy.to_config()}
        return self.publish(policy_key=policy.policy_id, required_fields=[], config=config)

    def publish_portfolio_trial(self, prepared) -> LifecyclePolicyVersion:
        """Publish the preregistered holding rule in the existing version ledger."""
        config = portfolio_policy_config(prepared)
        return self.publish(policy_key=prepared.spec.management_policy,
                            required_fields=[], config=config)

    @staticmethod
    def read_management(version: LifecyclePolicyVersion) -> ManagementPolicy:
        config = version.config or {}
        if not isinstance(config, dict) or set(config) != {"management_policy"}:
            raise FillValidationError("lifecycle version does not contain a typed management policy")
        try:
            policy = ManagementPolicy.from_config(config["management_policy"])
        except (TypeError, ValueError) as exc:
            raise FillValidationError("stored management policy is invalid") from exc
        if policy.policy_id != version.policy_key:
            raise FillValidationError("management policy key does not match the frozen version")
        return policy

    @staticmethod
    def read_family(version: LifecyclePolicyVersion, template_id: str) -> ManagementPolicy | None:
        if "management_policy" not in (version.config or {}):
            return None
        policy = LifecyclePolicyService.read_management(version)
        try:
            validate_family_policy(policy, template_id)
        except ValueError as exc:
            raise FillValidationError(str(exc)) from exc
        return policy

    def publish(self, *, policy_key: str, required_fields: list[str], config: dict) -> LifecyclePolicyVersion:
        if not isinstance(config, dict):
            raise FillValidationError("lifecycle config must be an object")
        if "management_policy" in config:
            if set(config) != {"management_policy"} or required_fields:
                raise FillValidationError("typed management policy cannot contain legacy config or required fields")
            try:
                typed = ManagementPolicy.from_config(config["management_policy"])
            except (TypeError, ValueError) as exc:
                raise FillValidationError("management policy config is invalid") from exc
            if typed.policy_id != policy_key:
                raise FillValidationError("management policy key mismatch")
        if "portfolio_trial" in config:
            try:
                validate_portfolio_policy_config(policy_key, required_fields, config)
            except (TypeError, ValueError) as exc:
                raise FillValidationError("portfolio trial policy config is invalid") from exc
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
            .order_by(LifecyclePolicyVersion.version_no.desc()).limit(1).with_for_update().execution_options(populate_existing=True)
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
        qty, price = _d(quantity), _d(fill_price)
        if qty <= 0 or price <= 0:
            raise FillValidationError("成交数量与价格必须大于 0")
        existing = self._by_idempotency(idempotency_key)
        if existing is not None:
            self._require_fill_replay(existing, event_type="CONFIRM", order_id=order_id,
                                      quantity=qty, fill_price=price,
                                      fill_trade_date=fill_trade_date, source=source, note=note)
            return existing, self._get_order(existing.order_id)
        order = self._lock_order(order_id, expected_revision)
        self._ensure_legacy_fill_allowed(order, allow_protective_sell=True)
        try:
            event = self._stage_confirm_fill_locked(
                order, qty=qty, price=price, fill_trade_date=fill_trade_date,
                idempotency_key=idempotency_key, source=source, note=note,
                fee=Decimal(0))
            self._session.commit()
        except IntegrityError:
            self._session.rollback()
            replay = self._by_idempotency(idempotency_key)
            if replay is not None:
                self._require_fill_replay(replay, event_type="CONFIRM", order_id=order_id,
                                          quantity=qty, fill_price=price,
                                          fill_trade_date=fill_trade_date, source=source, note=note)
                return replay, self._get_order(replay.order_id)
            raise
        return event, order

    def _stage_confirm_fill_locked(
        self, order: SuggestedOrder, *, qty: Decimal, price: Decimal,
        fill_trade_date: date, idempotency_key: str, source: str,
        note: str | None, fee: Decimal,
    ) -> OrderFillEvent:
        """Stage an already checked fill under the caller's portfolio/order locks."""
        if order.status not in ACTIVE_ORDER_STATUSES:
            raise LifecycleInvalidStateError(f"订单状态 {order.status} 不允许确认成交")
        remaining = _d(order.quantity) - _d(order.filled_quantity)
        if qty > remaining:
            raise FillValidationError("成交数量超过订单剩余量")
        actual_fee = _d(fee)
        if actual_fee < 0 or actual_fee != actual_fee.quantize(Decimal("0.0001")):
            raise FillValidationError("成交费用必须为非负四位小数")
        event_id = uuid.uuid4()
        self._session.execute(text(
            "SELECT set_config('liveprofit.fill_event_id', :fill_id, true)"),
            {"fill_id": str(event_id)})
        try:
            self._apply_delta(order, qty, price, fee=actual_fee)
            self._session.flush()
            event = OrderFillEvent(
                id=event_id, order_id=order.id, portfolio_id=order.portfolio_id,
                position_id=order.position_id, event_type="CONFIRM", quantity=qty,
                fill_price=price, fill_trade_date=fill_trade_date, source=source,
                idempotency_key=idempotency_key, note=note,
            )
            self._session.add(event)
            self._session.flush()
        finally:
            if self._session.is_active:
                self._session.execute(text(
                    "SELECT set_config('liveprofit.fill_event_id', '', true)"))
        self._initialize_lifecycle_from_first_fill(order, event, qty, price)
        return event

    def _initialize_lifecycle_from_first_fill(
        self, order: SuggestedOrder, event: OrderFillEvent, quantity: Decimal, price: Decimal,
    ) -> PositionLifecycleState | None:
        """Create lifecycle state only from a real first BUY fill with an explicit policy."""
        # Only BUY enters new-risk lifecycle initialization. Protective SELL and
        # corrections follow the fill ledger; they do not run a legacy evaluator.
        if order.side != "BUY":
            return None
        active = self._session.execute(
            select(PositionLifecycleState).where(
                PositionLifecycleState.portfolio_id == order.portfolio_id,
                PositionLifecycleState.market == order.market,
                PositionLifecycleState.symbol == order.symbol,
                PositionLifecycleState.closed_at.is_(None),
            ).with_for_update().execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if order.lifecycle_id is not None:
            linked = self._session.execute(select(PositionLifecycleState).where(
                PositionLifecycleState.id == order.lifecycle_id,
            ).with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
            if (linked is None or linked.closed_at is not None or active is None
                    or linked.id != active.id):
                raise LifecycleInvalidStateError("订单生命周期与活动持仓不一致")
            linked_policy = self._session.get(LifecyclePolicyVersion,
                                               linked.lifecycle_policy_version_id,
                                               populate_existing=True)
            if linked_policy is None or "portfolio_trial" in (linked_policy.config or {}):
                raise LifecycleInvalidStateError("组合试验政策尚未接入真实成交生命周期")
        if active is not None:
            active_policy = self._session.get(LifecyclePolicyVersion,
                                              active.lifecycle_policy_version_id,
                                              populate_existing=True)
            if active_policy is None or "portfolio_trial" in (active_policy.config or {}):
                raise LifecycleInvalidStateError("组合试验政策尚未接入真实成交生命周期")
        if order.source_signal_id is None:
            return None
        signal = self._session.get(QuantExecutionSignal, order.source_signal_id)
        task = self._session.get(AnalysisTask, signal.task_id) if signal is not None else None
        snapshot = (task.request_params or {}).get("execution_snapshot", {}) if task is not None else {}
        strategy_snapshot = snapshot.get("strategy", {})
        if signal is not None and signal.strategy_version_id is not None:
            signal_version = self._session.scalar(select(QuantStrategyVersion).where(
                QuantStrategyVersion.id == signal.strategy_version_id,
            ).with_for_update(read=True).execution_options(populate_existing=True))
            if signal_version is None:
                raise LifecycleInvalidStateError("成交信号策略版本不存在")
            if signal_version.lifecycle_policy_version_id is not None:
                signal_policy = self._session.scalar(select(LifecyclePolicyVersion).where(
                    LifecyclePolicyVersion.id == signal_version.lifecycle_policy_version_id,
                ).with_for_update(read=True).execution_options(populate_existing=True))
                if signal_policy is None or "portfolio_trial" in (signal_policy.config or {}):
                    raise LifecycleInvalidStateError("组合试验政策尚未接入真实成交生命周期")
            if (strategy_snapshot.get("version_id") is not None
                    and str(signal.strategy_version_id) != str(strategy_snapshot["version_id"])):
                raise LifecycleInvalidStateError("成交信号与策略快照版本不一致")
        if strategy_snapshot.get("version_id") is not None:
            try:
                snapshot_version_id = uuid.UUID(str(strategy_snapshot["version_id"]))
            except (TypeError, ValueError):
                raise LifecycleInvalidStateError("策略快照版本标识无效") from None
            snapshot_version = self._session.scalar(select(QuantStrategyVersion).where(
                QuantStrategyVersion.id == snapshot_version_id,
            ).with_for_update(read=True).execution_options(populate_existing=True))
            if snapshot_version is None:
                raise LifecycleInvalidStateError("策略快照版本不存在")
            if snapshot_version.lifecycle_policy_version_id is not None:
                snapshot_policy = self._session.scalar(select(LifecyclePolicyVersion).where(
                    LifecyclePolicyVersion.id == snapshot_version.lifecycle_policy_version_id,
                ).with_for_update(read=True).execution_options(populate_existing=True))
                if snapshot_policy is None or "portfolio_trial" in (snapshot_policy.config or {}):
                    raise LifecycleInvalidStateError("组合试验政策尚未接入真实成交生命周期")
        policy_snapshot = strategy_snapshot.get("lifecycle_policy")
        if policy_snapshot:
            try:
                strategy_version_id = uuid.UUID(str(strategy_snapshot["version_id"]))
                policy_version_id = uuid.UUID(str(policy_snapshot["id"]))
            except (KeyError, TypeError, ValueError):
                raise LifecycleInvalidStateError("生命周期策略快照缺少有效版本标识") from None
            version = self._session.scalar(select(QuantStrategyVersion).where(
                QuantStrategyVersion.id == strategy_version_id,
            ).with_for_update(read=True).execution_options(populate_existing=True))
            policy = self._session.scalar(select(LifecyclePolicyVersion).where(
                LifecyclePolicyVersion.id == policy_version_id,
            ).with_for_update(read=True).execution_options(populate_existing=True))
            archived_entry_continuation = (
                policy is not None and policy.status == "ARCHIVED" and active is not None
                and active.strategy_version_id == strategy_version_id
                and active.lifecycle_policy_version_id == policy_version_id
                and self._is_original_entry_order(active, order)
            )
            if (
                version is None or policy is None
                or (policy.status != "PUBLISHED" and not archived_entry_continuation)
                or version.lifecycle_policy_version_id != policy.id
                or policy.content_hash != policy_snapshot.get("content_hash")
            ):
                raise LifecycleInvalidStateError("生命周期策略快照与已发布版本不一致")
            config = dict(policy.config or {})
            if "portfolio_trial" in config:
                raise LifecycleInvalidStateError("组合试验政策尚未接入真实成交生命周期")
        if active is not None:
            order.lifecycle_id = active.id
            return active
        if not policy_snapshot:
            return None
        if order.stop_price is None or price <= _d(order.stop_price):
            raise LifecycleInvalidStateError("首笔成交价必须高于冻结初始止损")

        template_id = config.get("template_id") or version.template_id
        if not template_id:
            raise LifecycleInvalidStateError("生命周期策略必须显式声明 template_id")
        management = LifecyclePolicyService.read_family(policy, template_id)
        exposure = management.initial_exposure if management else Decimal("0.50")
        planned_capacity = _d(signal.shares) if signal.shares is not None else _d(order.quantity) / exposure
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
        target = (capacity * exposure / Decimal(100)).to_integral_value(
            rounding="ROUND_FLOOR"
        ) * Decimal(100)
        reward = None if management else _d(config.get(
            "reward_multiple", (version.template_params or {}).get("reward_multiple", "2")
        ))
        if reward is not None and reward <= 0:
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
            target_exposure_pct=exposure, target_shares=target,
            phase="INITIALIZED" if quantity >= target else "ENTRY_PENDING",
            profit_take_price=price + reward * (price - _d(order.stop_price)) if reward is not None else None,
            state_version=1,
            arc_neckline_price=_d(lifecycle_seed["arc_neckline_price"])
            if lifecycle_seed.get("arc_neckline_price") is not None else None,
        )
        self._session.add(lifecycle)
        self._session.flush()
        order.lifecycle_id = lifecycle.id

        trailing = config.get("trailing_stop")
        if management is not None and management.trailing_atr_multiple is not None:
            self._session.add(PositionTrailingStop(
                id=uuid.uuid4(), lifecycle_id=lifecycle.id,
                initial_stop_price=_d(order.stop_price), high_water_mark=price,
                active_stop_price=_d(order.stop_price), phase="PROTECT",
                config_snapshot={"mode": "HIGHEST_CLOSE_ATR", "multiple": str(management.trailing_atr_multiple)},
            ))
        if trailing is not None:
            LifecyclePolicyService._validate_trailing(config)
            self._session.add(PositionTrailingStop(
                id=uuid.uuid4(), lifecycle_id=lifecycle.id,
                initial_stop_price=_d(order.stop_price), high_water_mark=price,
                active_stop_price=_d(order.stop_price), phase="PROTECT",
                config_snapshot={k: str(trailing[k]) for k in ("b", "a", "d")},
            ))
        if template_id == "ma5_pre_cross_v1" and not (version.template_params or {}).get("confirmed_cross"):
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
        qty, price = _d(quantity), _d(fill_price)
        if qty <= 0 or price <= 0:
            raise FillValidationError("更正后的数量与价格必须大于 0")
        existing = self._by_idempotency(idempotency_key)
        if existing is not None:
            self._require_fill_replay(existing, event_type="CORRECT", reverses_fill_id=fill_id,
                                      quantity=qty, fill_price=price,
                                      fill_trade_date=fill_trade_date, note=note)
            return existing, self._get_order(existing.order_id)
        order_id = self._session.scalar(select(OrderFillEvent.order_id).where(OrderFillEvent.id == fill_id))
        if order_id is None:
            raise LifecycleNotFoundError("成交事件不存在")
        order = self._lock_order(order_id, expected_revision, allow_terminal=True)
        self._ensure_legacy_fill_allowed(order)
        original = self._lock_fill(fill_id)
        self._ensure_not_reversed(fill_id)
        self._ensure_initial_fill_replay_supported(original.id)
        projected = _d(order.filled_quantity) - _d(original.quantity) + qty
        if projected < 0 or projected > _d(order.quantity):
            raise FillValidationError("更正后的累计成交量超出订单范围")
        # A corrected broker-facing order is not an unsubmitted proposal even
        # when the revised cumulative quantity becomes zero.
        order.status = "RECONCILIATION_REQUIRED"
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
                self._require_fill_replay(replay, event_type="CORRECT", reverses_fill_id=fill_id,
                                          quantity=qty, fill_price=price,
                                          fill_trade_date=fill_trade_date, note=note)
                return replay, self._get_order(replay.order_id)
            raise
        return event, order

    def void_fill(
        self, fill_id: uuid.UUID, *, idempotency_key: str, expected_revision: int,
        fill_trade_date: date, note: str | None = None,
    ) -> tuple[OrderFillEvent, SuggestedOrder]:
        existing = self._by_idempotency(idempotency_key)
        if existing is not None:
            self._require_fill_replay(existing, event_type="VOID", reverses_fill_id=fill_id,
                                      fill_trade_date=fill_trade_date, note=note)
            return existing, self._get_order(existing.order_id)
        order_id = self._session.scalar(select(OrderFillEvent.order_id).where(OrderFillEvent.id == fill_id))
        if order_id is None:
            raise LifecycleNotFoundError("成交事件不存在")
        order = self._lock_order(order_id, expected_revision, allow_terminal=True)
        self._ensure_legacy_fill_allowed(order)
        original = self._lock_fill(fill_id)
        self._ensure_not_reversed(fill_id)
        self._ensure_initial_fill_replay_supported(original.id)
        order.status = "RECONCILIATION_REQUIRED"
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
                self._require_fill_replay(replay, event_type="VOID", reverses_fill_id=fill_id,
                                          fill_trade_date=fill_trade_date, note=note)
                return replay, self._get_order(replay.order_id)
            raise
        return event, order

    def set_order_status(self, order_id: uuid.UUID, *, status: str, expected_revision: int) -> SuggestedOrder:
        allowed = {"EXECUTING", "REJECTED", "CANCELLED", "RECONCILIATION_REQUIRED", "SUPERSEDED"}
        if status not in allowed:
            raise FillValidationError("不支持的订单状态")
        order = self._lock_order(order_id, expected_revision)
        if order.side == "BUY" and status == "EXECUTING":
            raise FillValidationError("BUY执行需要当日行情、资格与品种规则复核；通用状态接口不可放行")
        if status in {"CANCELLED", "REJECTED", "SUPERSEDED"} and (
                order.status != "PROPOSED" or order.filled_quantity != 0):
            raise FillValidationError("在途或已成交订单需要可信券商终态回执，不可由通用状态接口释放预留")
        if status == "EXECUTING" and (order.status != "PROPOSED" or order.filled_quantity != 0):
            raise FillValidationError("仅未成交的建议SELL可进入执行中")
        if status == "RECONCILIATION_REQUIRED" and order.status not in {
                "PROPOSED", "EXECUTING", "PARTIALLY_FILLED", "RECONCILIATION_REQUIRED"}:
            raise FillValidationError("终态订单不可重新进入对账")
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

    @staticmethod
    def _require_fill_replay(
        existing: OrderFillEvent, *, event_type: str,
        order_id: uuid.UUID | None = None, reverses_fill_id: uuid.UUID | None = None,
        quantity: Decimal | None = None, fill_price: Decimal | None = None,
        fill_trade_date: date, source: str | None = None, note: str | None,
    ) -> None:
        if (existing.event_type != event_type
                or (order_id is not None and existing.order_id != order_id)
                or existing.reverses_fill_id != reverses_fill_id
                or (quantity is not None and existing.quantity != quantity)
                or (fill_price is not None and existing.fill_price != fill_price)
                or existing.fill_trade_date != fill_trade_date
                or (source is not None and existing.source != source)
                or existing.note != note):
            raise FillValidationError("idempotency_key 已用于不同成交请求")

    def _get_order(self, order_id: uuid.UUID) -> SuggestedOrder:
        order = self._session.get(SuggestedOrder, order_id)
        if order is None:
            raise LifecycleNotFoundError("建议订单不存在")
        return order

    def _lock_order(self, order_id: uuid.UUID, expected_revision: int, *, allow_terminal: bool = False) -> SuggestedOrder:
        from .planning_account import lock_portfolio
        portfolio_id = self._session.scalar(select(SuggestedOrder.portfolio_id).where(SuggestedOrder.id == order_id))
        if portfolio_id is None:
            raise LifecycleNotFoundError("建议订单不存在")
        lock_portfolio(self._session, portfolio_id)
        order = self._session.execute(
            select(SuggestedOrder).where(SuggestedOrder.id == order_id).with_for_update().execution_options(populate_existing=True)
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
            select(OrderFillEvent).where(OrderFillEvent.id == fill_id).with_for_update().execution_options(populate_existing=True)
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

    def _ensure_initial_fill_replay_supported(self, fill_id: uuid.UUID) -> None:
        if self._session.scalar(select(PositionLifecycleState.id).where(
                PositionLifecycleState.initial_fill_id == fill_id).limit(1)) is not None:
            raise LifecycleInvalidStateError("首笔成交已冻结生命周期，修订需完整事件重放")

    def _ensure_legacy_fill_allowed(self, order: SuggestedOrder, *,
                                    allow_protective_sell: bool = False) -> None:
        """The old projection omits fees and the audited account movement."""
        if self._session.scalar(select(AccountLedgerBaselineRow.id).where(
                AccountLedgerBaselineRow.portfolio_id == order.portfolio_id)) is not None:
            raise LifecycleInvalidStateError("组合已建立账本基线，旧成交入口缺费用与账本同事务记录")
        from .planning_account import requires_account_reconciliation
        if (not allow_protective_sell or order.side == "BUY") and requires_account_reconciliation(
                self._session, order.portfolio_id):
            raise LifecycleInvalidStateError("账户存在未认证成交报告，旧成交入口不得增加风险")

    def _apply_delta(
        self, order: SuggestedOrder, signed_qty: Decimal, price: Decimal, *,
        advance_version: bool = True, fee: Decimal = Decimal(0),
    ) -> None:
        portfolio = self._session.execute(
            select(Portfolio).where(Portfolio.id == order.portfolio_id).with_for_update().execution_options(populate_existing=True)
        ).scalar_one()
        position = self._session.execute(
            select(PortfolioPosition).where(
                PortfolioPosition.portfolio_id == order.portfolio_id,
                PortfolioPosition.market == order.market,
                PortfolioPosition.symbol == order.symbol,
            ).with_for_update().execution_options(populate_existing=True)
        ).scalar_one_or_none()
        direction = Decimal(1) if order.side == "BUY" else Decimal(-1)
        position_delta = direction * signed_qty
        cash_delta = -direction * signed_qty * price - fee
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
                quantity=new_qty, average_cost=price + fee / new_qty,
                active_stop_price=order.stop_price,
            )
            self._session.add(position)
            self._session.flush()
            order.position_id = position.id
        else:
            old_cost = _d(position.average_cost)
            if order.side == "BUY" and new_qty > 0:
                position.average_cost = ((old_qty * old_cost) + (signed_qty * price) + fee) / new_qty
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
            was_reconciliation_required = order.status == "RECONCILIATION_REQUIRED"
            order.status = "RECONCILIATION_REQUIRED" if was_reconciliation_required else (
                "FILLED" if order.filled_quantity == order.quantity else
                "PARTIALLY_FILLED" if order.filled_quantity > 0 else "PROPOSED"
            )
            order.revision += 1
            order.updated_at = datetime.now(timezone.utc)
        if advance_version and order.lifecycle_id is not None:
            lifecycle = self._session.execute(
                select(PositionLifecycleState).where(PositionLifecycleState.id == order.lifecycle_id).with_for_update().execution_options(populate_existing=True)
            ).scalar_one_or_none()
            if lifecycle is not None:
                self._advance_initial_entry(lifecycle, order, signed_qty)
                lifecycle.position_id = order.position_id
                lifecycle.state_version += 1
                lifecycle.updated_at = datetime.now(timezone.utc)
                if order.intent_id is not None:
                    intent = self._session.execute(
                        select(PositionIntent).where(PositionIntent.id == order.intent_id).with_for_update().execution_options(populate_existing=True)
                    ).scalar_one_or_none()
                    if intent is not None:
                        was_reconciliation_required = (
                            was_reconciliation_required or intent.status == "RECONCILIATION_REQUIRED"
                        )
                        if was_reconciliation_required:
                            order.status = "RECONCILIATION_REQUIRED"
                        reached = new_qty == _d(intent.target_shares)
                        successor = self._session.scalar(select(PositionIntent).where(
                            PositionIntent.lifecycle_id == lifecycle.id,
                            PositionIntent.id != intent.id,
                            PositionIntent.status.in_(("ACTIVE", "EXECUTING", "RECONCILIATION_REQUIRED")),
                        ).with_for_update().execution_options(populate_existing=True))
                        if successor is not None:
                            # The old target was completed at its own time. A
                            # late revision cannot reopen it beside the new
                            # target; quarantine the successor and its orders.
                            successor.status = "RECONCILIATION_REQUIRED"
                            successor.revision += 1
                            successor.updated_at = datetime.now(timezone.utc)
                            for pending in self._session.scalars(select(SuggestedOrder).where(
                                    SuggestedOrder.intent_id == successor.id,
                                    SuggestedOrder.status.in_(ACTIVE_ORDER_STATUSES),
                            ).with_for_update().execution_options(populate_existing=True)):
                                if pending.status != "RECONCILIATION_REQUIRED":
                                    pending.status = "RECONCILIATION_REQUIRED"
                                    pending.revision += 1
                                    pending.updated_at = datetime.now(timezone.utc)
                            # Preserve the historical terminal old intent.
                            return
                        # A late fill is real, but it does not reconcile the
                        # conflicting target or certify a broker terminal state.
                        intent.status = (
                            "RECONCILIATION_REQUIRED" if was_reconciliation_required
                            else "COMPLETED" if reached else "EXECUTING"
                        )
                        intent.revision += 1
                        if reached and not was_reconciliation_required:
                            if intent.reason_code in {"TEMPLATE_CONFIRM_ADD", "ADD_AT_R"}:
                                lifecycle.confirmation_completed = True
                                lifecycle.phase = "CONFIRMED"
                            elif intent.reason_code == "PROFIT_TARGET_TRIM":
                                lifecycle.profit_trim_completed = True
                                lifecycle.phase = "PROFIT_PROTECTED"
                            elif intent.reason_code in CLOSING_EXIT_REASONS and new_qty == 0:
                                lifecycle.phase = "CLOSED"
                                lifecycle.closed_at = datetime.now(timezone.utc)

    def _is_original_entry_order(
        self, lifecycle: PositionLifecycleState, order: SuggestedOrder,
    ) -> bool:
        """Match an existing entry to its unrevised first-confirmation anchor."""
        if (lifecycle.initial_fill_id is None or order.lifecycle_id != lifecycle.id
                or lifecycle.portfolio_id != order.portfolio_id
                or lifecycle.market != order.market or lifecycle.symbol != order.symbol):
            return False
        initial = self._session.get(OrderFillEvent, lifecycle.initial_fill_id)
        return (
            initial is not None and initial.event_type == "CONFIRM"
            and initial.order_id == order.id and initial.portfolio_id == order.portfolio_id
            and self._session.scalar(select(OrderFillEvent.id).where(
                OrderFillEvent.reverses_fill_id == initial.id,
            ).limit(1)) is None
        )

    def _advance_initial_entry(
        self, lifecycle: PositionLifecycleState, order: SuggestedOrder,
        signed_qty: Decimal,
    ) -> None:
        """Complete only the original pending entry, within this fill's version step.

        The new confirmation is not in the immutable fill journal yet. Its
        quantity is added to the effective prior events, then compared with the
        locked order projection and the original policy target. Account holdings
        may include other orders and cannot prove that this entry is complete.
        """
        if (signed_qty <= 0 or order.side != "BUY" or order.intent_id is not None
                or order.status == "RECONCILIATION_REQUIRED"
                or lifecycle.phase != "ENTRY_PENDING" or lifecycle.closed_at is not None
                or lifecycle.initial_fill_id is None or lifecycle.risk_capacity_shares is None):
            return
        if not self._is_original_entry_order(lifecycle, order):
            return
        version = self._session.scalar(select(QuantStrategyVersion).where(
            QuantStrategyVersion.id == lifecycle.strategy_version_id,
        ).with_for_update(read=True).execution_options(populate_existing=True))
        policy = self._session.scalar(select(LifecyclePolicyVersion).where(
            LifecyclePolicyVersion.id == lifecycle.lifecycle_policy_version_id,
        ).with_for_update(read=True).execution_options(populate_existing=True))
        if (policy is None or version is None
                or "portfolio_trial" in (policy.config or {})
                or version.lifecycle_policy_version_id != policy.id):
            return
        template_id = (policy.config or {}).get("template_id") or version.template_id
        management = LifecyclePolicyService.read_family(policy, template_id)
        initial_exposure = management.initial_exposure if management else Decimal("0.50")
        initial_target = (_d(lifecycle.risk_capacity_shares) * initial_exposure / Decimal(100)
                          ).to_integral_value(rounding="ROUND_FLOOR") * Decimal(100)
        if (initial_target <= 0 or _d(lifecycle.target_exposure_pct) != initial_exposure
                or _d(lifecycle.target_shares) != initial_target):
            return
        # A later target or an unresolved order owns the next transition. A late
        # fill remains real but must not overwrite that target's phase.
        if self._session.scalar(select(PositionIntent.id).where(
                PositionIntent.lifecycle_id == lifecycle.id,
                PositionIntent.status.in_(("ACTIVE", "EXECUTING", "RECONCILIATION_REQUIRED")),
        ).limit(1).with_for_update()) is not None:
            return
        if self._session.scalar(select(SuggestedOrder.id).where(
                SuggestedOrder.portfolio_id == order.portfolio_id,
                SuggestedOrder.market == order.market, SuggestedOrder.symbol == order.symbol,
                SuggestedOrder.id != order.id, SuggestedOrder.status.in_(ACTIVE_ORDER_STATUSES),
        ).limit(1).with_for_update()) is not None:
            return
        events = list(self._session.scalars(select(OrderFillEvent).where(
            OrderFillEvent.order_id == order.id,
        )))
        reversed_ids = {event.reverses_fill_id for event in events if event.reverses_fill_id is not None}
        effective_quantity = sum((
            _d(event.quantity) for event in events
            if event.event_type in {"CONFIRM", "CORRECT"} and event.id not in reversed_ids
        ), Decimal(0)) + signed_qty
        if effective_quantity != _d(order.filled_quantity) or effective_quantity > initial_target:
            # Preserve the reported fill and surface an unresolved entry excess
            # or projection mismatch; neither is a successful entry completion.
            order.status = "RECONCILIATION_REQUIRED"
        elif effective_quantity == initial_target:
            lifecycle.phase = "INITIALIZED"


class LifecycleStateService:
    """Idempotent active-intent and per-day fact primitives consumed by N6."""

    def __init__(self, session) -> None:
        self._session = session

    def get_or_create_intent(
        self, lifecycle_id: uuid.UUID, *, trade_date: date, target_shares,
        reason_code: str, source_signal_id: int | None = None,
    ) -> tuple[PositionIntent, bool]:
        from .planning_account import lock_portfolio

        portfolio_id = self._session.scalar(select(PositionLifecycleState.portfolio_id).where(
            PositionLifecycleState.id == lifecycle_id
        ))
        if portfolio_id is None:
            raise LifecycleNotFoundError("持仓生命周期不存在")
        lock_portfolio(self._session, portfolio_id)
        lifecycle = self._session.execute(
            select(PositionLifecycleState).where(PositionLifecycleState.id == lifecycle_id).with_for_update().execution_options(populate_existing=True)
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
            ).with_for_update().execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if active is not None and active.target_shares == target and active.reason_code == reason_code:
            return active, False
        bound_orders = list(self._session.scalars(select(SuggestedOrder).where(
            SuggestedOrder.lifecycle_id == lifecycle_id,
            SuggestedOrder.status.in_(ACTIVE_ORDER_STATUSES),
        ).with_for_update().execution_options(populate_existing=True)))
        if bound_orders:
            raise LifecycleInvalidStateError("活动建议单尚未协调，禁止新建绑定意图")
        if active is not None:
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
