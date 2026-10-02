"""PositionPlanner（plan 4.3.1）：全量信号落库后一次全局有序规划建议订单。

按 score DESC, ts_code ASC, id ASC 流式读取当前 attempt 的可行动 signal，
将资金、盈亏比、行业与整手约束转换为建议订单并回写对应 signal 行：
- 风险手数 shares_risk = floor(total_assets × risk_per_trade_pct / (entry−stop) / 100) × 100；
- 现金/总仓位/单票/行业上限取最小值 → 整手向下取整 → 用最终股数及
  order_cost_price 重算 notional/风险并再次断言全部上限；
- 每一余量先扣已有持仓市值与已接受 BUY（按 ts_code 与 {source,industry_code} 聚合），
  不把建议卖出所得计作现金；unallocated 资产不参与可买现金；
- SELL_ALL 卖出现有全部数量（含零股）；SELL_PARTIAL 为 floor(shares×ratio/100)×100，
  不足一手不生成订单；无持仓卖出信号为 SELL_REJECTED_NO_POSITION；
- 不写 portfolio_positions，不接券商，不把卖出建议净额结算到同批买入。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace

from backend.modules.quant_strategy.application.execution_constraints import (
    ExecutionConstraintEvaluator,
    ExecutionPolicy,
)
from backend.modules.quant_strategy.application.portfolio_risk import PortfolioRiskState
from backend.modules.investment_workspace.domain.risk_profiles import PROFILES, profile_budget_violations
from backend.modules.quant_strategy.domain.instrument_rules import InstrumentTradingRule

LOT_SIZE = Decimal(100)
ELIGIBLE = "ELIGIBLE"
BUY_REJECTED_RISK_GATE = "BUY_REJECTED_RISK_GATE"  # 预留：AI 层接入后启用
BUY_REJECTED_RR = "BUY_REJECTED_RR"
BUY_REJECTED_PRICE_RANGE = "BUY_REJECTED_PRICE_RANGE"
BUY_REJECTED_LOT_SIZE = "BUY_REJECTED_LOT_SIZE"
BUY_REJECTED_CASH = "BUY_REJECTED_CASH"
BUY_REJECTED_TOTAL_LIMIT = "BUY_REJECTED_TOTAL_LIMIT"
BUY_REJECTED_SINGLE_STOCK_LIMIT = "BUY_REJECTED_SINGLE_STOCK_LIMIT"
BUY_REJECTED_SECTOR_LIMIT = "BUY_REJECTED_SECTOR_LIMIT"
BUY_REJECTED_INDUSTRY_BUCKET = "BUY_REJECTED_INDUSTRY_BUCKET"
PORTFOLIO_VALUE_INCONSISTENT = "PORTFOLIO_VALUE_INCONSISTENT"
PORTFOLIO_ALREADY_OVER_LIMIT = "PORTFOLIO_ALREADY_OVER_LIMIT"
SELL_REJECTED_NO_POSITION = "SELL_REJECTED_NO_POSITION"
SELLABLE_QUANTITY_UNKNOWN = "SELLABLE_QUANTITY_UNKNOWN"
SELL_PARTIAL_REJECTED_LOT_SIZE = "SELL_PARTIAL_REJECTED_LOT_SIZE"
STALE_POSITION_VALUATION = "STALE_POSITION_VALUATION"
BUY_REJECTED_STALE_VALUATION = "BUY_REJECTED_STALE_VALUATION"
RISK_GATE_NOT_ENABLED = "AI 风险门控未接入；组合开放风险与熔断已启用"


@dataclass
class PlanSummary:
    suggested_buy_orders: int = 0
    suggested_sell_orders: int = 0
    buy_rejections: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    valued_at: datetime | None = None
    portfolio_open_risk: Decimal = Decimal(0)
    daily_new_risk: Decimal = Decimal(0)
    full_drawdown_exit_required: bool = False


@dataclass(frozen=True)
class ReductionPlan:
    code: str | None
    quantity: Decimal = Decimal(0)
    price: Decimal = Decimal(0)
    fees: Decimal = Decimal(0)
    slippage: Decimal = Decimal(0)
    earliest_execution_trade_date: date | None = None


def plan_buy_target(*, symbol: str, max_quantity: Decimal, entry: Decimal,
                    stop: Decimal, take: Decimal | None, market: dict,
                    valuation_date: date, max_notional: Decimal | None = None,
                    strategy_version_id: uuid.UUID | None = None,
                    entry_lower: Decimal | None = None, entry_upper: Decimal | None = None,
                    **account_context) -> dict:
    """Plan an explicitly raw-price target through the ordinary BUY planner.

    This adapter never supplies guessed market facts or a synthetic profit
    target. The returned projection is suitable for a caller's transaction;
    no account or order is mutated here.
    """
    if (entry_lower is None) != (entry_upper is None):
        raise ValueError("entry interval requires both raw-price bounds")
    if entry_lower is not None and (
        not isinstance(entry_lower, Decimal) or not isinstance(entry_upper, Decimal)
        or not entry_lower.is_finite() or not entry_upper.is_finite() or not 0 < entry_lower <= entry_upper
    ):
        raise ValueError("entry interval must contain finite positive raw prices")
    if not isinstance(max_quantity, Decimal) or not max_quantity.is_finite() or max_quantity < 0:
        raise ValueError("BUY target quantity must be finite and nonnegative")
    if max_notional is not None and (not isinstance(max_notional, Decimal)
                                    or not max_notional.is_finite() or max_notional < 0):
        raise ValueError("BUY family budget must be finite and nonnegative")
    if (str(market.get("trade_date"))[:10] != valuation_date.isoformat()
            or any(type(market.get(key)) is not bool for key in ("is_suspended", "is_st"))):
        return {"order_status": "BUY_MARKET_FACTS_UNAVAILABLE"}
    try:
        for key in ("raw_close", "adv20_amount", "up_limit"):
            value = _dec(market.get(key))
            if not value.is_finite() or value <= 0:
                raise ValueError
        for value in (entry, stop, *(() if take is None else (take,))):
            if not _dec(value).is_finite():
                raise ValueError
    except (ValueError, TypeError):
        return {"order_status": "BUY_MARKET_FACTS_UNAVAILABLE"}
    signal = SimpleNamespace(
        id=uuid.uuid4(), signal_kind="BUY", ts_code=symbol,
        entry_price=entry, stop_loss=stop, take_profit=take,
        valuation_price=market["raw_close"], max_quantity=max_quantity,
        max_notional=max_notional, entry_lower=entry_lower, entry_upper=entry_upper,
        # Lifecycle prices already use raw basis; prevent a second qfq mapping.
        execution_market={**market, "qfq_close": market["raw_close"]},
    )

    class Projection:
        fields: dict

        def list_actionable(self, task_id, attempt_no):
            return (signal,)

        def update_order_fields(self, signal_id, fields):
            self.fields = fields

    projection = Projection()
    PositionPlanner(projection).plan(
        task_id=signal.id, attempt_no=1, valuation_date=valuation_date,
        strategy_version_id=strategy_version_id,
        **account_context,
    )
    return projection.fields


def plan_reduction(*, target_quantity: Decimal, actual_quantity: Decimal,
                   available_quantity: Decimal, reserved_quantity: Decimal,
                   market: dict, execution: ExecutionConstraintEvaluator,
                   lot_size: Decimal = LOT_SIZE) -> ReductionPlan:
    """Shared reduction gate. Sell reservations consume quantity, never cash.

The caller supplies settlement-aware availability for the execution session.
Only a complete liquidation may include an odd lot.
"""
    values = (target_quantity, actual_quantity, available_quantity, reserved_quantity, lot_size)
    if any(not isinstance(v, Decimal) or not v.is_finite() or v < 0 for v in values) or lot_size <= 0:
        raise ValueError("reduction quantities must be finite and nonnegative with a positive lot")
    if target_quantity > actual_quantity:
        raise ValueError("a reduction cannot increase the position")
    if (type(market.get("is_suspended")) is not bool
            or market.get("raw_close") is None or market.get("down_limit") is None):
        return ReductionPlan("SELL_MARKET_FACTS_UNAVAILABLE")
    try:
        date.fromisoformat(str(market.get("trade_date"))[:10])
        close, lower = _dec(market["raw_close"]), _dec(market["down_limit"])
        if not close.is_finite() or not lower.is_finite() or min(close, lower) <= 0:
            return ReductionPlan("SELL_MARKET_FACTS_UNAVAILABLE")
    except (ValueError, TypeError):
        return ReductionPlan("SELL_MARKET_FACTS_UNAVAILABLE")
    available = max(Decimal(0), min(available_quantity, actual_quantity) - reserved_quantity)
    desired = max(Decimal(0), actual_quantity - target_quantity - reserved_quantity)
    code, price, slip, earliest = execution.evaluate_sell(market=market, available_quantity=available)
    if code:
        return ReductionPlan(code, earliest_execution_trade_date=earliest)
    if price <= 0:
        return ReductionPlan("SELL_PRICE_UNAVAILABLE", earliest_execution_trade_date=earliest)
    quantity = min(desired, available)
    if target_quantity != 0:
        quantity = (quantity / lot_size).to_integral_value(rounding="ROUND_FLOOR") * lot_size
    if quantity <= 0:
        return ReductionPlan("SELL_NO_EXECUTABLE_DELTA", earliest_execution_trade_date=earliest)
    return ReductionPlan(None, quantity, price, execution.fees(quantity * price, side="SELL"),
                         slip * quantity, earliest)


def _dec(value) -> Decimal:
    if value is None:
        raise ValueError("空值不能转 Decimal")
    try:
        return Decimal(str(value))
    except InvalidOperation:
        raise ValueError(f"非法数值: {value}") from None


class PositionPlanner:
    def __init__(self, signals_repo, *, clock=None) -> None:
        self._signals = signals_repo

    def plan(
        self,
        *,
        task_id: uuid.UUID,
        attempt_no: int,
        portfolio_snapshot: dict,
        positions: list[dict],
        closes: dict[str, Decimal],
        industry_map: dict[str, dict],
        industry_bucket_available: bool,
        risk_gate: str | None = None,
        execution_policy_snapshot: dict | None = None,
        pending_orders: list[dict] | None = None,
        valuation_date: date | None = None,
        lifecycle_managed_symbols: set[str] | None = None,
        owner_versions: dict[str, str | None] | None = None,
        strategy_version_id: uuid.UUID | None = None,
        admission_block_code: str | None = None,
        instrument_rules: dict[str, InstrumentTradingRule] | None = None,
    ) -> PlanSummary:
        summary = PlanSummary(valued_at=datetime.now(timezone.utc))
        execution = ExecutionConstraintEvaluator(ExecutionPolicy.from_snapshot(execution_policy_snapshot))
        total_assets = _dec(portfolio_snapshot["total_assets"])
        available_cash = _dec(portfolio_snapshot["available_cash"])
        risk = portfolio_snapshot["risk"]
        risk_per_trade = _dec(risk["risk_per_trade_pct"])
        rr_min = _dec(risk["min_risk_reward_ratio"])
        max_total_pct = _dec(risk["max_total_position_pct"])
        max_single_pct = _dec(risk["max_single_stock_pct"])
        max_sector_pct = _dec(risk["max_sector_pct"])

        if risk_gate is not None:
            summary.warnings.append(f"risk_gate={risk_gate}")
        else:
            summary.warnings.append(RISK_GATE_NOT_ENABLED)

        # 持仓估值：首选 effective-date close；缺失按 average_cost 展示降级并整体新 BUY fail-closed
        holdings_value: dict[str, Decimal] = {}
        stale = False
        for p in positions:
            qty = _dec(p["quantity"])
            ts = p["symbol"]
            close = closes.get(ts)
            if close is None:
                stale = True
                close = _dec(p["average_cost"])
            holdings_value[ts] = holdings_value.get(ts, Decimal(0)) + qty * close
        if stale:
            summary.warnings.append(STALE_POSITION_VALUATION)

        existing_market_value = sum(holdings_value.values(), Decimal(0))
        unallocated = total_assets - available_cash - existing_market_value
        new_buy_blocked: str | None = None
        profile = portfolio_snapshot.get("risk_profile")
        excess = profile_budget_violations(profile, risk)
        if excess:
            new_buy_blocked = "BUY_REJECTED_PROFILE_BUDGET"
            summary.warnings.append("风险档预算不匹配：" + ",".join(excess))
        if portfolio_snapshot.get("risk_pause_event_id") is not None:
            summary.warnings.append("账户全回撤暂停：" + str(portfolio_snapshot["risk_pause_event_id"]))
        if unallocated < 0:
            new_buy_blocked = PORTFOLIO_VALUE_INCONSISTENT
            summary.warnings.append("unallocated_assets<0：拒绝全部新 BUY")
        elif stale:
            # 持仓估值缺失：average_cost 仅作展示降级，整体新 BUY fail-closed
            new_buy_blocked = BUY_REJECTED_STALE_VALUATION
        elif total_assets > 0 and existing_market_value > total_assets * max_total_pct:
            new_buy_blocked = PORTFOLIO_ALREADY_OVER_LIMIT
            summary.warnings.append("已有市值超过总仓位上限：拒绝全部新 BUY")
        if portfolio_snapshot.get("risk_pause_event_id") is not None:
            new_buy_blocked = "BUY_REJECTED_PORTFOLIO_PAUSED"
        if portfolio_snapshot.get("account_reconciliation_required") is True:
            new_buy_blocked = "BUY_REJECTED_ACCOUNT_RECONCILIATION"
            summary.warnings.append("账户事实尚未认证或有未处置成交报告：拒绝新 BUY")

        cash_remaining = available_cash
        single_used: dict[str, Decimal] = dict(holdings_value)
        sector_used: dict[tuple, Decimal] = {}
        unknown_sector_used = Decimal(0)
        for p in positions:
            bucket = industry_map.get(p["symbol"])
            qty = _dec(p["quantity"])
            close = closes.get(p["symbol"])
            if close is None:
                close = _dec(p["average_cost"])
            mv = qty * close
            if bucket is None:
                unknown_sector_used += mv
            else:
                key = (bucket["industry_code"],)
                sector_used[key] = sector_used.get(key, Decimal(0)) + mv

        if unknown_sector_used > 0:
            # 已持仓可能属于任意行业，无法证明新买入不突破行业上限。
            industry_bucket_available = False
            if new_buy_blocked is None:
                new_buy_blocked = BUY_REJECTED_INDUSTRY_BUCKET
            summary.warnings.append("持仓行业归属缺失：拒绝新 BUY，保留卖出建议")

        reservations = (portfolio_snapshot.get("pending_orders", [])
                        if pending_orders is None else pending_orders)
        # Pending BUY notionals consume exposure as well as cash/open risk.
        # They are not assets yet, so do not include them in unallocated NAV.
        for order in reservations:
            if str(order.get("side", "")).upper() != "BUY":
                continue
            try:
                qty = _dec(order.get("remaining_quantity"))
                entry = _dec(order.get("order_entry_price"))
                if not qty.is_finite() or not entry.is_finite() or qty <= 0 or entry <= 0:
                    raise ValueError
            except ValueError:
                new_buy_blocked = new_buy_blocked or "BUY_REJECTED_RISK_FACTS"
                continue
            symbol, industry = order.get("symbol"), order.get("industry_code")
            if not symbol or not industry:
                new_buy_blocked = new_buy_blocked or "BUY_REJECTED_RISK_FACTS"
                continue
            notional = qty * entry
            existing_market_value += notional
            single_used[symbol] = single_used.get(symbol, Decimal(0)) + notional
            key = (industry,)
            sector_used[key] = sector_used.get(key, Decimal(0)) + notional

        risk_state = PortfolioRiskState.build(
            total_assets=total_assets,
            risk=risk,
            positions=positions,
            closes=closes,
            industry_map=industry_map,
            pending_orders=reservations,
            valuation_date=valuation_date,
        )
        summary.full_drawdown_exit_required = risk_state.full_drawdown_exit_required
        if risk_state.full_drawdown_exit_required:
            summary.warnings.append("PORTFOLIO_FULL_DRAWDOWN_EXIT_REQUIRED")
        cash_remaining -= risk_state.reserved_cash
        if risk_state.reserved_cash:
            summary.warnings.append(f"未完成买单现金预留={risk_state.reserved_cash}")
        if risk_state.block_code and new_buy_blocked is None:
            new_buy_blocked = risk_state.block_code

        for signal in self._signals.list_actionable(task_id, attempt_no):
            if signal.signal_kind == "HOLDING":
                if signal.ts_code in (lifecycle_managed_symbols or set()):
                    self._signals.update_order_fields(signal.id, {"order_status": "MANAGED_BY_LIFECYCLE"})
                    continue
                self._plan_sell(signal, positions, closes, summary, execution, risk_state)
                continue
            # BUY
            if admission_block_code is not None:
                self._reject(signal, "BUY_REJECTED_ADMISSION", summary)
                if admission_block_code not in summary.warnings:
                    summary.warnings.append(admission_block_code)
                continue
            if signal.ts_code in (lifecycle_managed_symbols or set()):
                self._reject(signal, "BUY_REJECTED_MANAGED", summary)
                continue
            if owner_versions is not None and signal.ts_code in owner_versions:
                signal_version = getattr(signal, "strategy_version_id", None) or strategy_version_id
                owner = owner_versions[signal.ts_code]
                if owner is None or signal_version is None or owner != str(signal_version):
                    self._reject(signal, "BUY_REJECTED_OWNER", summary)
                    continue
            if new_buy_blocked:
                self._reject(signal, new_buy_blocked, summary)
                continue
            if risk_gate == "block":
                self._reject(signal, BUY_REJECTED_RISK_GATE, summary)
                continue
            accepted, notional, cash_used = self._plan_buy(
                signal,
                summary,
                total_assets=total_assets,
                risk_per_trade=risk_per_trade,
                rr_min=rr_min,
                max_total_pct=max_total_pct,
                max_single_pct=max_single_pct,
                risk_profile=profile,
                max_sector_pct=max_sector_pct,
                existing_market_value=existing_market_value,
                cash_remaining=cash_remaining,
                single_used=single_used,
                sector_used=sector_used,
                industry_map=industry_map,
                industry_bucket_available=industry_bucket_available,
                execution=execution,
                risk_state=risk_state,
                instrument_rule=(instrument_rules.get(signal.ts_code) if instrument_rules is not None else None),
                require_instrument_rule=instrument_rules is not None,
                valuation_date=valuation_date,
            )
            if accepted and notional is not None:
                cash_remaining -= cash_used
                existing_market_value += notional
        summary.portfolio_open_risk = risk_state.portfolio_open_risk
        summary.daily_new_risk = risk_state.daily_new_risk
        return summary

    # ---- 内部 ----

    @staticmethod
    def _market(signal, *, fallback_raw: Decimal, fallback_qfq: Decimal | None = None) -> dict:
        market = getattr(signal, "execution_market", None)
        if market:
            return dict(market)
        trade_date = getattr(signal, "signal_trade_date", None) or datetime.now(timezone.utc).date()
        return {
            "trade_date": trade_date.isoformat(),
            "raw_close": fallback_raw,
            "qfq_close": fallback_qfq if fallback_qfq is not None else fallback_raw,
            # Missing execution-market evidence must not create synthetic BUY
            # liquidity. SELL uses this fallback only for price and status.
            "raw_amount": None,
            "is_suspended": False,
            "is_st": False,
            "up_limit": None,
            "down_limit": None,
        }

    def _plan_sell(self, signal, positions: list[dict], closes, summary: PlanSummary, execution, risk_state) -> None:
        holding = next((p for p in positions if p["symbol"] == signal.ts_code), None)
        if holding is None:
            self._reject(signal, SELL_REJECTED_NO_POSITION, summary, kind="sell")
            return
        qty = _dec(holding["quantity"])
        close = closes.get(signal.ts_code)
        valuation = close if close is not None else _dec(holding["average_cost"])
        # Position balance does not prove exchange-settled, unrestricted shares.
        # The current account snapshot has no certified sellable balance yet.
        raw_available = holding.get("available_quantity")
        try:
            available = _dec(raw_available)
        except (ValueError, TypeError):
            self._reject(signal, SELLABLE_QUANTITY_UNKNOWN, summary, kind="sell")
            return
        if not available.is_finite() or available < 0 or available > qty:
            self._reject(signal, SELLABLE_QUANTITY_UNKNOWN, summary, kind="sell")
            return
        reserved = risk_state.reserved_sell_quantity.get(signal.ts_code, Decimal(0))
        if signal.action == "SELL_ALL":
            target = Decimal(0)
        else:
            requested = (qty * _dec(signal.sell_ratio) / LOT_SIZE).to_integral_value(rounding="ROUND_FLOOR") * LOT_SIZE
            target = qty - requested
        reduction = plan_reduction(
            target_quantity=target, actual_quantity=qty, available_quantity=available,
            reserved_quantity=reserved, market=self._market(signal, fallback_raw=valuation), execution=execution,
        )
        fields = {
            "order_status": (SELL_PARTIAL_REJECTED_LOT_SIZE
                             if reduction.code == "SELL_NO_EXECUTABLE_DELTA" and signal.action == "SELL_PARTIAL"
                             else reduction.code or ELIGIBLE),
            "available_sell_quantity": max(Decimal(0), available - reserved),
            "earliest_execution_trade_date": reduction.earliest_execution_trade_date,
            "execution_policy_version": execution.policy.version,
        }
        if reduction.code is None:
            fields.update(shares=reduction.quantity, notional=reduction.quantity * reduction.price,
                          valuation_price=valuation, order_entry_price=reduction.price,
                          order_cost_price=reduction.price, estimated_fees=reduction.fees,
                          estimated_slippage=reduction.slippage)
            summary.suggested_sell_orders += 1
            risk_state.reserved_sell_quantity[signal.ts_code] = reserved + reduction.quantity
        self._signals.update_order_fields(signal.id, fields)

    def _plan_buy(self, signal, summary, **ctx) -> tuple[bool, Decimal | None, Decimal]:
        """规划 BUY，返回 (是否接受, gross notional, 含费用现金占用)。"""
        total_assets = ctx["total_assets"]
        rule = ctx["instrument_rule"]
        if ctx["require_instrument_rule"] and (
            not isinstance(rule, InstrumentTradingRule) or rule.symbol != signal.ts_code
        ):
            self._reject(signal, "BUY_REJECTED_INSTRUMENT_RULE", summary)
            return False, None, Decimal(0)
        entry = _dec(signal.entry_price)
        stop = _dec(signal.stop_loss)
        if signal.take_profit is None:
            # T3 must define rule-policy admission against account constraints.
            # Never invent a target or silently bypass the account's RR floor.
            self._reject(signal, BUY_REJECTED_RR, summary)
            return False, None, Decimal(0)
        take = _dec(signal.take_profit)
        if not (0 < stop < entry < take):
            self._reject(signal, BUY_REJECTED_RR, summary)
            return False, None, Decimal(0)
        if (take - entry) / (entry - stop) < ctx["rr_min"]:
            self._reject(signal, BUY_REJECTED_RR, summary)
            return False, None, Decimal(0)
        close = _dec(signal.valuation_price) if signal.valuation_price is not None else entry
        market = self._market(signal, fallback_raw=close, fallback_qfq=close)
        if ctx["require_instrument_rule"] and (
            type(ctx["valuation_date"]) is not date
            or str(market.get("trade_date"))[:10] != ctx["valuation_date"].isoformat()
        ):
            self._reject(signal, "BUY_REJECTED_INSTRUMENT_RULE", summary)
            return False, None, Decimal(0)
        try:
            decision = ctx["execution"].evaluate_buy(
                entry=entry, stop=stop, take=take, market=market,
                symbol=signal.ts_code, instrument_rule=rule,
            )
        except ValueError:
            self._reject(signal, "BUY_REJECTED_INSTRUMENT_RULE", summary)
            return False, None, Decimal(0)
        if not decision.eligible:
            fields = {
                "earliest_execution_trade_date": decision.earliest_execution_trade_date,
                "execution_policy_version": ctx["execution"].policy.version,
            }
            if decision.prices is not None:
                fields.update({
                    "order_entry_price": decision.prices.entry,
                    "order_stop_price": decision.prices.stop,
                    "order_take_price": decision.prices.take,
                })
            self._reject(signal, decision.code, summary, fields=fields)
            return False, None, Decimal(0)
        prices = decision.prices
        order_cost, order_stop, order_take = prices.entry, prices.stop, prices.take
        normalized_fields = {
            "order_entry_price": order_cost,
            "order_stop_price": order_stop,
            "order_take_price": order_take,
            "earliest_execution_trade_date": decision.earliest_execution_trade_date,
            "execution_policy_version": ctx["execution"].policy.version,
        }
        lower, upper = getattr(signal, "entry_lower", None), getattr(signal, "entry_upper", None)
        if (lower is None) != (upper is None):
            self._reject(signal, BUY_REJECTED_PRICE_RANGE, summary, fields=normalized_fields)
            return False, None, Decimal(0)
        if lower is not None and (
            not isinstance(lower, Decimal) or not isinstance(upper, Decimal)
            or not lower.is_finite() or not upper.is_finite()
            or not 0 < lower <= order_cost <= upper
        ):
            self._reject(signal, BUY_REJECTED_PRICE_RANGE, summary, fields=normalized_fields)
            return False, None, Decimal(0)
        # The upper bound is a fill ceiling after slippage. Use its highest
        # executable tick for every reservation; an opening fill above it is
        # rejected by the execution proxy, never financed from this plan.
        reservation_price = order_cost
        if upper is not None:
            tick = rule.price_tick if rule is not None else ctx["execution"].policy.tick_size
            reservation_price = (upper / tick).to_integral_value(rounding="ROUND_FLOOR") * tick
            if reservation_price < order_cost:
                self._reject(signal, BUY_REJECTED_PRICE_RANGE, summary, fields=normalized_fields)
                return False, None, Decimal(0)
        # 信号价只代表策略形态。实际建议买入成本抬高后必须重新满足严格三价关系、
        # 最低盈亏比与风险预算，避免跳空时成本已越过目标价仍被标为 ELIGIBLE。
        if not (0 < order_stop < reservation_price < order_take):
            self._reject(signal, BUY_REJECTED_PRICE_RANGE, summary, fields=normalized_fields)
            return False, None, Decimal(0)
        if (order_take - reservation_price) / (reservation_price - order_stop) < ctx["rr_min"]:
            self._reject(signal, BUY_REJECTED_RR, summary, fields=normalized_fields)
            return False, None, Decimal(0)

        # 行业风险桶：SW2021；行业不可用/未知行业 → 拒绝（绝不按零暴露绕过上限）
        bucket = ctx["industry_map"].get(signal.ts_code)
        if not ctx["industry_bucket_available"] or bucket is None:
            self._reject(signal, BUY_REJECTED_INDUSTRY_BUCKET, summary, fields=normalized_fields)
            return False, None, Decimal(0)
        sector_key = (bucket["industry_code"],)

        def floor_quantity(capacity: Decimal) -> Decimal:
            whole = int(capacity.to_integral_value(rounding="ROUND_FLOOR"))
            if rule is not None:
                return Decimal(rule.floor_buy_quantity(whole))
            return Decimal(max(0, whole // 100 * 100))

        minimum_quantity = Decimal(rule.min_buy_quantity if rule is not None else 100)

        # 风险手数
        risk_budget = total_assets * ctx["risk_per_trade"]
        shares_risk = floor_quantity(risk_budget / (reservation_price - order_stop))
        # 现金上限（每笔及累计）
        shares_cash = floor_quantity(ctx["cash_remaining"] / reservation_price)
        # 总仓位上限
        total_remaining = ctx["max_total_pct"] * total_assets - ctx["existing_market_value"]
        shares_total = floor_quantity(total_remaining / reservation_price)
        # 单票上限
        is_etf = rule is not None and rule.asset_type == "etf"
        single_cap = PROFILES[ctx["risk_profile"]].single_etf if is_etf else ctx["max_single_pct"]
        single_reject_code = ("BUY_REJECTED_SINGLE_ETF_LIMIT" if is_etf
                              else BUY_REJECTED_SINGLE_STOCK_LIMIT)
        single_remaining = single_cap * total_assets - ctx["single_used"].get(signal.ts_code, Decimal(0))
        shares_single = floor_quantity(single_remaining / reservation_price)
        # 行业上限
        sector_remaining = ctx["max_sector_pct"] * total_assets - ctx["sector_used"].get(sector_key, Decimal(0))
        shares_sector = floor_quantity(sector_remaining / reservation_price)
        risk_per_share = reservation_price - order_stop
        liquidity_shares = decision.max_liquidity_shares
        if upper is not None:
            adv20 = _dec(market["adv20_amount"])
            max_notional = adv20 * Decimal(1000) * ctx["execution"].policy.max_participation_rate
            liquidity_shares = min(liquidity_shares, floor_quantity(max_notional / reservation_price))
        risk_capacities = [
            (floor_quantity(capacity), code)
            for capacity, code in ctx["risk_state"].capacities(
                risk_per_share=risk_per_share, industry_code=str(bucket["industry_code"])
            )
        ]

        candidates = [
            (shares_risk, BUY_REJECTED_RR),
            (shares_cash, BUY_REJECTED_CASH),
            (shares_total, BUY_REJECTED_TOTAL_LIMIT),
            (shares_single, single_reject_code),
            (shares_sector, BUY_REJECTED_SECTOR_LIMIT),
            (liquidity_shares, "BUY_REJECTED_LIQUIDITY"),
            *risk_capacities,
        ]
        max_quantity = getattr(signal, "max_quantity", None)
        if max_quantity is not None:
            maximum = _dec(max_quantity)
            if not maximum.is_finite() or maximum < 0:
                raise ValueError("BUY target quantity must be finite and nonnegative")
            candidates.append((floor_quantity(maximum),
                               BUY_REJECTED_LOT_SIZE))
        max_notional = getattr(signal, "max_notional", None)
        if max_notional is not None:
            ceiling = _dec(max_notional)
            if not ceiling.is_finite() or ceiling < 0:
                raise ValueError("BUY family budget must be finite and nonnegative")
            candidates.append((floor_quantity(ceiling / reservation_price),
                               "BUY_REJECTED_FAMILY_BUDGET"))
        shares, reject_code = min(candidates, key=lambda c: c[0])
        if shares < minimum_quantity:
            self._reject(
                signal,
                reject_code if reject_code != BUY_REJECTED_RR else BUY_REJECTED_LOT_SIZE,
                summary,
                fields=normalized_fields,
            )
            return False, None, Decimal(0)
        # 现金约束按毛额 + 预计费用复核；费用使现金不足时按整手继续缩量。
        while shares >= minimum_quantity:
            candidate_notional = shares * reservation_price
            candidate_fees = ctx["execution"].fees(candidate_notional, side="BUY")
            if candidate_notional + candidate_fees <= ctx["cash_remaining"]:
                break
            shares = floor_quantity(shares - 1)
        if shares < minimum_quantity:
            self._reject(signal, BUY_REJECTED_CASH, summary, fields=normalized_fields)
            return False, None, Decimal(0)
        notional = shares * reservation_price
        fees = ctx["execution"].fees(notional, side="BUY")
        cash_used = notional + fees
        # 用最终股数及 order_cost_price 重算 notional/风险并再次断言全部上限
        if cash_used > ctx["cash_remaining"] or notional > total_remaining or notional > single_remaining or notional > sector_remaining:
            self._reject(signal, reject_code, summary, fields=normalized_fields)
            return False, None, Decimal(0)
        self._signals.update_order_fields(signal.id, {
            "order_status": ELIGIBLE,
            "shares": shares,
            "notional": notional,
            "order_cost_price": reservation_price,
            "order_entry_price": order_cost,
            "order_stop_price": order_stop,
            "order_take_price": order_take,
            "valuation_price": close,
            "earliest_execution_trade_date": decision.earliest_execution_trade_date,
            "estimated_fees": fees,
            # Baseline signal-price slippage estimate. Interval stress is
            # represented by order_cost_price/notional/reserved risk instead.
            "estimated_slippage": prices.slippage_per_share * shares,
            "execution_policy_version": ctx["execution"].policy.version,
            "risk_bucket": {"source": "SW2021", "industry_code": bucket["industry_code"], "industry_name": bucket.get("name")},
        })
        ctx["single_used"][signal.ts_code] = ctx["single_used"].get(signal.ts_code, Decimal(0)) + notional
        ctx["sector_used"][sector_key] = ctx["sector_used"].get(sector_key, Decimal(0)) + notional
        ctx["risk_state"].accept(
            risk_amount=(reservation_price - order_stop) * shares,
            industry_code=str(bucket["industry_code"]),
        )
        summary.suggested_buy_orders += 1
        return True, notional, cash_used

    def _reject(self, signal, code: str, summary: PlanSummary, *, kind: str = "buy", fields: dict | None = None) -> None:
        self._signals.update_order_fields(signal.id, {"order_status": code, **(fields or {})})
        if kind == "buy":
            summary.buy_rejections[code] = summary.buy_rejections.get(code, 0) + 1
