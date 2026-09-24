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

from backend.modules.quant_strategy.application.execution_constraints import (
    ExecutionConstraintEvaluator,
    ExecutionPolicy,
)
from backend.modules.quant_strategy.application.portfolio_risk import PortfolioRiskState

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
        if unallocated < 0:
            new_buy_blocked = PORTFOLIO_VALUE_INCONSISTENT
            summary.warnings.append("unallocated_assets<0：拒绝全部新 BUY")
        elif stale:
            # 持仓估值缺失：average_cost 仅作展示降级，整体新 BUY fail-closed
            new_buy_blocked = BUY_REJECTED_STALE_VALUATION
        elif total_assets > 0 and existing_market_value > total_assets * max_total_pct:
            new_buy_blocked = PORTFOLIO_ALREADY_OVER_LIMIT
            summary.warnings.append("已有市值超过总仓位上限：拒绝全部新 BUY")

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

        risk_state = PortfolioRiskState.build(
            total_assets=total_assets,
            risk=risk,
            positions=positions,
            closes=closes,
            industry_map=industry_map,
            pending_orders=pending_orders or portfolio_snapshot.get("pending_orders", []),
            valuation_date=valuation_date,
        )
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
                max_sector_pct=max_sector_pct,
                existing_market_value=existing_market_value,
                cash_remaining=cash_remaining,
                single_used=single_used,
                sector_used=sector_used,
                industry_map=industry_map,
                industry_bucket_available=industry_bucket_available,
                execution=execution,
                risk_state=risk_state,
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
            "raw_amount": Decimal("1000000000000"),
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
        available = _dec(holding.get("available_quantity", holding["quantity"]))
        available = max(Decimal(0), available - risk_state.reserved_sell_quantity.get(signal.ts_code, Decimal(0)))
        market = self._market(signal, fallback_raw=valuation)
        constraint, order_price, slippage_per_share, earliest = execution.evaluate_sell(
            market=market, available_quantity=available,
        )
        if constraint:
            self._signals.update_order_fields(signal.id, {
                "order_status": constraint,
                "available_sell_quantity": available,
                "earliest_execution_trade_date": earliest,
                "execution_policy_version": execution.policy.version,
            })
            return
        if signal.action == "SELL_ALL":
            shares = min(qty, available)  # 可用数量可含零股；T+1 冻结部分不得卖出
            notional = shares * order_price
            self._signals.update_order_fields(signal.id, {
                "order_status": ELIGIBLE,
                "shares": shares,
                "notional": notional,
                "valuation_price": valuation,
                "order_entry_price": order_price,
                "order_cost_price": order_price,
                "available_sell_quantity": available,
                "earliest_execution_trade_date": earliest,
                "estimated_fees": execution.fees(notional, side="SELL"),
                "estimated_slippage": slippage_per_share * shares,
                "execution_policy_version": execution.policy.version,
            })
            summary.suggested_sell_orders += 1
        elif signal.action == "SELL_PARTIAL":
            ratio = _dec(signal.sell_ratio)
            shares = min(
                (qty * ratio / LOT_SIZE).to_integral_value(rounding="ROUND_FLOOR") * LOT_SIZE,
                (available / LOT_SIZE).to_integral_value(rounding="ROUND_FLOOR") * LOT_SIZE,
            )
            if shares < LOT_SIZE:
                self._reject(signal, SELL_PARTIAL_REJECTED_LOT_SIZE, summary, kind="sell")
                return
            notional = shares * order_price
            self._signals.update_order_fields(signal.id, {
                "order_status": ELIGIBLE,
                "shares": shares,
                "notional": notional,
                "valuation_price": valuation,
                "order_entry_price": order_price,
                "order_cost_price": order_price,
                "available_sell_quantity": available,
                "earliest_execution_trade_date": earliest,
                "estimated_fees": execution.fees(notional, side="SELL"),
                "estimated_slippage": slippage_per_share * shares,
                "execution_policy_version": execution.policy.version,
            })
            summary.suggested_sell_orders += 1

    def _plan_buy(self, signal, summary, **ctx) -> tuple[bool, Decimal | None, Decimal]:
        """规划 BUY，返回 (是否接受, gross notional, 含费用现金占用)。"""
        total_assets = ctx["total_assets"]
        entry = _dec(signal.entry_price)
        stop = _dec(signal.stop_loss)
        take = _dec(signal.take_profit)
        if not (0 < stop < entry < take):
            self._reject(signal, BUY_REJECTED_RR, summary)
            return False, None, Decimal(0)
        if (take - entry) / (entry - stop) < ctx["rr_min"]:
            self._reject(signal, BUY_REJECTED_RR, summary)
            return False, None, Decimal(0)
        close = _dec(signal.valuation_price) if signal.valuation_price is not None else entry
        market = self._market(signal, fallback_raw=close, fallback_qfq=close)
        decision = ctx["execution"].evaluate_buy(entry=entry, stop=stop, take=take, market=market)
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
        # 信号价只代表策略形态。实际建议买入成本抬高后必须重新满足严格三价关系、
        # 最低盈亏比与风险预算，避免跳空时成本已越过目标价仍被标为 ELIGIBLE。
        if not (0 < order_stop < order_cost < order_take):
            self._reject(signal, BUY_REJECTED_PRICE_RANGE, summary, fields=normalized_fields)
            return False, None, Decimal(0)
        if (order_take - order_cost) / (order_cost - order_stop) < ctx["rr_min"]:
            self._reject(signal, BUY_REJECTED_RR, summary, fields=normalized_fields)
            return False, None, Decimal(0)

        # 行业风险桶：SW2021；行业不可用/未知行业 → 拒绝（绝不按零暴露绕过上限）
        bucket = ctx["industry_map"].get(signal.ts_code)
        if not ctx["industry_bucket_available"] or bucket is None:
            self._reject(signal, BUY_REJECTED_INDUSTRY_BUCKET, summary, fields=normalized_fields)
            return False, None, Decimal(0)
        sector_key = (bucket["industry_code"],)

        # 风险手数
        risk_budget = total_assets * ctx["risk_per_trade"]
        shares_risk = (risk_budget / (order_cost - order_stop) / LOT_SIZE).to_integral_value(rounding="ROUND_FLOOR") * LOT_SIZE
        # 现金上限（每笔及累计）
        shares_cash = (ctx["cash_remaining"] / order_cost / LOT_SIZE).to_integral_value(rounding="ROUND_FLOOR") * LOT_SIZE
        # 总仓位上限
        total_remaining = ctx["max_total_pct"] * total_assets - ctx["existing_market_value"]
        shares_total = (total_remaining / order_cost / LOT_SIZE).to_integral_value(rounding="ROUND_FLOOR") * LOT_SIZE
        # 单票上限
        single_remaining = ctx["max_single_pct"] * total_assets - ctx["single_used"].get(signal.ts_code, Decimal(0))
        shares_single = (single_remaining / order_cost / LOT_SIZE).to_integral_value(rounding="ROUND_FLOOR") * LOT_SIZE
        # 行业上限
        sector_remaining = ctx["max_sector_pct"] * total_assets - ctx["sector_used"].get(sector_key, Decimal(0))
        shares_sector = (sector_remaining / order_cost / LOT_SIZE).to_integral_value(rounding="ROUND_FLOOR") * LOT_SIZE
        risk_per_share = order_cost - order_stop
        risk_capacities = [
            ((capacity / LOT_SIZE).to_integral_value(rounding="ROUND_FLOOR") * LOT_SIZE, code)
            for capacity, code in ctx["risk_state"].capacities(
                risk_per_share=risk_per_share, industry_code=str(bucket["industry_code"])
            )
        ]

        candidates = [
            (shares_risk, BUY_REJECTED_RR),
            (shares_cash, BUY_REJECTED_CASH),
            (shares_total, BUY_REJECTED_TOTAL_LIMIT),
            (shares_single, BUY_REJECTED_SINGLE_STOCK_LIMIT),
            (shares_sector, BUY_REJECTED_SECTOR_LIMIT),
            (decision.max_liquidity_shares, "BUY_REJECTED_LIQUIDITY"),
            *risk_capacities,
        ]
        shares, reject_code = min(candidates, key=lambda c: c[0])
        if shares < LOT_SIZE:
            self._reject(
                signal,
                reject_code if reject_code != BUY_REJECTED_RR else BUY_REJECTED_LOT_SIZE,
                summary,
                fields=normalized_fields,
            )
            return False, None, Decimal(0)
        # 现金约束按毛额 + 预计费用复核；费用使现金不足时按整手继续缩量。
        while shares >= LOT_SIZE:
            candidate_notional = shares * order_cost
            candidate_fees = ctx["execution"].fees(candidate_notional, side="BUY")
            if candidate_notional + candidate_fees <= ctx["cash_remaining"]:
                break
            shares -= LOT_SIZE
        if shares < LOT_SIZE:
            self._reject(signal, BUY_REJECTED_CASH, summary, fields=normalized_fields)
            return False, None, Decimal(0)
        notional = shares * order_cost
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
            "order_cost_price": order_cost,
            "order_entry_price": order_cost,
            "order_stop_price": order_stop,
            "order_take_price": order_take,
            "valuation_price": close,
            "earliest_execution_trade_date": decision.earliest_execution_trade_date,
            "estimated_fees": fees,
            "estimated_slippage": prices.slippage_per_share * shares,
            "execution_policy_version": ctx["execution"].policy.version,
            "risk_bucket": {"source": "SW2021", "industry_code": bucket["industry_code"], "industry_name": bucket.get("name")},
        })
        ctx["single_used"][signal.ts_code] = ctx["single_used"].get(signal.ts_code, Decimal(0)) + notional
        ctx["sector_used"][sector_key] = ctx["sector_used"].get(sector_key, Decimal(0)) + notional
        ctx["risk_state"].accept(
            risk_amount=(order_cost - order_stop) * shares,
            industry_code=str(bucket["industry_code"]),
        )
        summary.suggested_buy_orders += 1
        return True, notional, cash_used

    def _reject(self, signal, code: str, summary: PlanSummary, *, kind: str = "buy", fields: dict | None = None) -> None:
        self._signals.update_order_fields(signal.id, {"order_status": code, **(fields or {})})
        if kind == "buy":
            summary.buy_rejections[code] = summary.buy_rejections.get(code, 0) + 1
