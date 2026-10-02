"""组合开放风险、订单预留与净值熔断的纯计算模型。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation


RISK_FACTS_UNAVAILABLE = "BUY_REJECTED_RISK_FACTS"
PORTFOLIO_OPEN_RISK_LIMIT = "BUY_REJECTED_PORTFOLIO_OPEN_RISK"
SECTOR_OPEN_RISK_LIMIT = "BUY_REJECTED_SECTOR_OPEN_RISK"
DAILY_NEW_RISK_LIMIT = "BUY_REJECTED_DAILY_NEW_RISK"
PORTFOLIO_CIRCUIT_BREAKER = "BUY_REJECTED_RISK_CIRCUIT"


def _dec(value) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("风险事实不是有效数值") from None
    if not result.is_finite():
        raise ValueError("风险事实不是有限数值")
    return result


@dataclass
class PortfolioRiskState:
    total_assets: Decimal
    portfolio_limit: Decimal
    sector_limit: Decimal
    daily_limit: Decimal
    portfolio_open_risk: Decimal
    sector_open_risk: dict[str, Decimal]
    daily_new_risk: Decimal
    reserved_cash: Decimal
    reserved_sell_quantity: dict[str, Decimal]
    block_code: str | None = None
    drawdown_pct: Decimal | None = None
    full_drawdown_exit_required: bool = False

    @classmethod
    def build(
        cls,
        *,
        total_assets,
        risk: dict,
        positions: list[dict],
        closes: dict[str, Decimal],
        industry_map: dict[str, dict],
        pending_orders: list[dict],
        valuation_date: date | None = None,
    ) -> "PortfolioRiskState":
        assets = _dec(total_assets)
        required = (
            "max_portfolio_open_risk_pct", "max_sector_open_risk_pct",
            "max_daily_new_risk_pct", "max_drawdown_pct", "max_daily_loss_pct",
            "risk_facts_as_of",
        )
        if assets <= 0 or any(risk.get(key) is None for key in required):
            return cls.unavailable(assets)
        try:
            portfolio_limit = assets * _dec(risk["max_portfolio_open_risk_pct"])
            sector_limit = assets * _dec(risk["max_sector_open_risk_pct"])
            daily_limit = assets * _dec(risk["max_daily_new_risk_pct"])
            nav = _dec(risk.get("net_asset_value"))
            peak = _dec(risk.get("peak_net_asset_value"))
            day_start = _dec(risk.get("day_start_net_asset_value"))
            if min(nav, peak, day_start) <= 0:
                raise ValueError
            drawdown_limit = _dec(risk["max_drawdown_pct"])
            daily_loss_limit = _dec(risk["max_daily_loss_pct"])
            if drawdown_limit <= 0 or daily_loss_limit <= 0:
                raise ValueError
            if valuation_date is not None and str(risk["risk_facts_as_of"])[:10] != valuation_date.isoformat():
                raise ValueError
        except ValueError:
            return cls.unavailable(assets)
        drawdown = max(Decimal(0), (peak - nav) / peak)
        daily_loss = max(Decimal(0), (day_start - nav) / day_start)
        block = None
        if drawdown >= drawdown_limit / 2 or daily_loss >= daily_loss_limit:
            block = PORTFOLIO_CIRCUIT_BREAKER

        state = cls(
            assets, portfolio_limit, sector_limit, daily_limit,
            Decimal(0), {}, Decimal(0), Decimal(0), {}, block,
            drawdown, drawdown >= drawdown_limit,
        )
        for position in positions:
            symbol = position["symbol"]
            stop = position.get("active_stop_price")
            close = closes.get(symbol)
            bucket = industry_map.get(symbol)
            if stop is None or close is None or bucket is None:
                state.block_code = RISK_FACTS_UNAVAILABLE
                continue
            loss = max(Decimal(0), _dec(close) - _dec(stop)) * _dec(position["quantity"])
            state.portfolio_open_risk += loss
            code = str(bucket["industry_code"])
            state.sector_open_risk[code] = state.sector_open_risk.get(code, Decimal(0)) + loss

        for order in pending_orders:
            side = str(order.get("side", "")).upper()
            symbol = str(order.get("symbol") or "")
            try:
                qty = _dec(order.get("remaining_quantity"))
                if qty <= 0 or not symbol:
                    raise ValueError
            except ValueError:
                state.block_code = RISK_FACTS_UNAVAILABLE
                continue
            if side == "SELL":
                state.reserved_sell_quantity[symbol] = state.reserved_sell_quantity.get(symbol, Decimal(0)) + qty
                continue
            if side != "BUY":
                state.block_code = RISK_FACTS_UNAVAILABLE
                continue
            try:
                entry, stop = _dec(order.get("order_entry_price")), _dec(order.get("order_stop_price"))
                reserved_cash = _dec(order.get("reserved_cash", entry * qty))
                if reserved_cash < entry * qty:
                    raise ValueError
            except ValueError:
                state.block_code = RISK_FACTS_UNAVAILABLE
                continue
            industry = order.get("industry_code")
            if qty <= 0 or not industry or not (Decimal(0) < stop < entry):
                state.block_code = RISK_FACTS_UNAVAILABLE
                continue
            risk_amount = (entry - stop) * qty
            state.reserved_cash += reserved_cash
            state.portfolio_open_risk += risk_amount
            state.daily_new_risk += risk_amount
            code = str(industry)
            state.sector_open_risk[code] = state.sector_open_risk.get(code, Decimal(0)) + risk_amount

        if state.block_code is None and state.portfolio_open_risk > state.portfolio_limit:
            state.block_code = PORTFOLIO_OPEN_RISK_LIMIT
        if state.block_code is None and any(value > state.sector_limit for value in state.sector_open_risk.values()):
            state.block_code = SECTOR_OPEN_RISK_LIMIT
        if state.block_code is None and state.daily_new_risk > state.daily_limit:
            state.block_code = DAILY_NEW_RISK_LIMIT
        return state

    @classmethod
    def unavailable(cls, assets: Decimal) -> "PortfolioRiskState":
        return cls(
            assets, Decimal(0), Decimal(0), Decimal(0), Decimal(0), {},
            Decimal(0), Decimal(0), {}, RISK_FACTS_UNAVAILABLE,
        )

    def capacities(self, *, risk_per_share: Decimal, industry_code: str) -> list[tuple[Decimal, str]]:
        if self.block_code:
            return [(Decimal(0), self.block_code)]
        return [
            (max(Decimal(0), self.portfolio_limit - self.portfolio_open_risk) / risk_per_share,
             PORTFOLIO_OPEN_RISK_LIMIT),
            (max(Decimal(0), self.sector_limit - self.sector_open_risk.get(industry_code, Decimal(0))) / risk_per_share,
             SECTOR_OPEN_RISK_LIMIT),
            (max(Decimal(0), self.daily_limit - self.daily_new_risk) / risk_per_share,
             DAILY_NEW_RISK_LIMIT),
        ]

    def accept(self, *, risk_amount: Decimal, industry_code: str) -> None:
        self.portfolio_open_risk += risk_amount
        self.daily_new_risk += risk_amount
        self.sector_open_risk[industry_code] = self.sector_open_risk.get(industry_code, Decimal(0)) + risk_amount
