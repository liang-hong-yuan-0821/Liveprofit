"""A 股人工建议订单的价格映射、规范化、费用与可交易性约束。

策略只产生 qfq 信号三价；本模块从执行期原始行情生成独立的 raw 订单三价，
不覆盖策略信号。所有结果仅是人工建议，不代表成交。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR

from backend.modules.quant_strategy.domain.instrument_rules import InstrumentTradingRule


BUY_REJECTED_SUSPENDED = "BUY_REJECTED_SUSPENDED"
BUY_REJECTED_ST = "BUY_REJECTED_ST"
BUY_REJECTED_LIMIT_UP = "BUY_REJECTED_LIMIT_UP"
BUY_REJECTED_LIQUIDITY = "BUY_REJECTED_LIQUIDITY"
SELL_REJECTED_SUSPENDED = "SELL_REJECTED_SUSPENDED"
SELL_REJECTED_LIMIT_DOWN = "SELL_REJECTED_LIMIT_DOWN"
SELL_REJECTED_T1 = "SELL_REJECTED_T1"
EXECUTION_CALENDAR_UNAVAILABLE = "EXECUTION_CALENDAR_UNAVAILABLE"


def _dec(value, default: str = "0") -> Decimal:
    if value is None:
        return Decimal(default)
    return Decimal(str(value))


def next_execution_session(value: date, *, calendar, market: str = "CN") -> date | None:
    schedule = calendar.schedule(market)
    if not schedule.available or not schedule.supported_from <= value <= schedule.supported_through:
        return None
    return min((session.trade_date for session in schedule.sessions
                if value < session.trade_date <= schedule.supported_through), default=None)


@dataclass(frozen=True)
class ExecutionPolicy:
    version: str = "cn_execution_v1"
    tick_size: Decimal = Decimal("0.01")
    slippage_bps: Decimal = Decimal("10")
    commission_rate: Decimal = Decimal("0.0003")
    minimum_commission: Decimal = Decimal("5")
    transfer_fee_rate: Decimal = Decimal("0.00001")
    stamp_duty_rate: Decimal = Decimal("0.0005")
    max_participation_rate: Decimal = Decimal("0.05")

    @classmethod
    def from_snapshot(cls, value: dict | None) -> "ExecutionPolicy":
        value = value or {}
        return cls(
            version=str(value.get("version") or "cn_execution_v1"),
            tick_size=_dec(value.get("tick_size"), "0.01"),
            slippage_bps=_dec(value.get("slippage_bps"), "10"),
            commission_rate=_dec(value.get("commission_rate"), "0.0003"),
            minimum_commission=_dec(value.get("minimum_commission"), "5"),
            transfer_fee_rate=_dec(value.get("transfer_fee_rate"), "0.00001"),
            stamp_duty_rate=_dec(value.get("stamp_duty_rate"), "0.0005"),
            max_participation_rate=_dec(value.get("max_participation_rate"), "0.05"),
        )

    def to_snapshot(self) -> dict[str, str]:
        return {
            "version": self.version,
            "tick_size": format(self.tick_size, "f"),
            "slippage_bps": format(self.slippage_bps, "f"),
            "commission_rate": format(self.commission_rate, "f"),
            "minimum_commission": format(self.minimum_commission, "f"),
            "transfer_fee_rate": format(self.transfer_fee_rate, "f"),
            "stamp_duty_rate": format(self.stamp_duty_rate, "f"),
            "max_participation_rate": format(self.max_participation_rate, "f"),
        }


@dataclass(frozen=True)
class NormalizedPrices:
    entry: Decimal
    stop: Decimal
    take: Decimal
    mapping_ratio: Decimal
    slippage_per_share: Decimal


class PriceBasisMapper:
    @staticmethod
    def qfq_to_raw(value, *, qfq_close, raw_close) -> Decimal:
        qfq = _dec(qfq_close)
        raw = _dec(raw_close)
        if qfq <= 0 or raw <= 0:
            raise ValueError("价格口径映射基准必须为正数")
        return _dec(value) * raw / qfq


class OrderPriceNormalizer:
    def __init__(self, policy: ExecutionPolicy) -> None:
        self.policy = policy

    def _ceil_tick(self, value: Decimal, *, tick_size: Decimal | None = None) -> Decimal:
        tick = tick_size or self.policy.tick_size
        return (value / tick).to_integral_value(rounding=ROUND_CEILING) * tick

    def _floor_tick(self, value: Decimal, *, tick_size: Decimal | None = None) -> Decimal:
        tick = tick_size or self.policy.tick_size
        return (value / tick).to_integral_value(rounding=ROUND_FLOOR) * tick

    def buy_prices(self, entry, stop, take, *, qfq_close, raw_close,
                   tick_size: Decimal | None = None) -> NormalizedPrices:
        qfq = _dec(qfq_close)
        raw = _dec(raw_close)
        ratio = raw / qfq
        mapped_entry = PriceBasisMapper.qfq_to_raw(entry, qfq_close=qfq, raw_close=raw)
        mapped_stop = PriceBasisMapper.qfq_to_raw(stop, qfq_close=qfq, raw_close=raw)
        mapped_take = PriceBasisMapper.qfq_to_raw(take, qfq_close=qfq, raw_close=raw)
        slipped = mapped_entry * (Decimal(1) + self.policy.slippage_bps / Decimal(10000))
        normalized_entry = self._ceil_tick(max(raw, slipped), tick_size=tick_size)
        return NormalizedPrices(
            entry=normalized_entry,
            stop=self._floor_tick(mapped_stop, tick_size=tick_size),
            take=self._floor_tick(mapped_take, tick_size=tick_size),
            mapping_ratio=ratio,
            slippage_per_share=max(Decimal(0), normalized_entry - mapped_entry),
        )

    def sell_price(self, *, raw_close) -> tuple[Decimal, Decimal]:
        raw = _dec(raw_close)
        slipped = raw * (Decimal(1) - self.policy.slippage_bps / Decimal(10000))
        normalized = self._floor_tick(slipped)
        return normalized, max(Decimal(0), raw - normalized)


@dataclass(frozen=True)
class ConstraintDecision:
    code: str | None
    prices: NormalizedPrices | None
    max_liquidity_shares: Decimal | None
    earliest_execution_trade_date: date | None

    @property
    def eligible(self) -> bool:
        return self.code is None


class ExecutionConstraintEvaluator:
    def __init__(self, policy: ExecutionPolicy, *, calendar=None) -> None:
        self.policy = policy
        self.normalizer = OrderPriceNormalizer(policy)
        if calendar is None:
            from backend.modules.market_data.infrastructure.calendar_adapter import MarketCalendarAdapter
            calendar = MarketCalendarAdapter()
        self.calendar = calendar

    @staticmethod
    def _blocks_next_day(market: dict) -> bool:
        # Decisions here target the next trading day. An observed intraday
        # pause on the signal day does not make that whole day untradeable.
        # Legacy rows without scope remain conservative.
        return bool(market.get("is_suspended")) and market.get("suspension_scope") != "intraday"

    def evaluate_buy(self, *, entry, stop, take, market: dict, symbol: str | None = None,
                     instrument_rule: InstrumentTradingRule | None = None) -> ConstraintDecision:
        trade_date = date.fromisoformat(str(market["trade_date"])[:10])
        earliest = next_execution_session(trade_date, calendar=self.calendar)
        if earliest is None:
            return ConstraintDecision(EXECUTION_CALENDAR_UNAVAILABLE, None, None, None)
        if instrument_rule is not None:
            instrument_rule.validate()
            if not (symbol == instrument_rule.symbol
                    and instrument_rule.effective_from <= earliest
                    and (instrument_rule.effective_through is None
                         or earliest <= instrument_rule.effective_through)
                    and instrument_rule.published_on < trade_date):
                raise ValueError("instrument rule is not known and effective for execution")
        if self._blocks_next_day(market):
            return ConstraintDecision(BUY_REJECTED_SUSPENDED, None, None, earliest)
        if bool(market.get("is_st")):
            return ConstraintDecision(BUY_REJECTED_ST, None, None, earliest)
        raw_close = _dec(market.get("raw_close"))
        up_limit = _dec(market.get("up_limit")) if market.get("up_limit") is not None else None
        if up_limit is not None and raw_close >= up_limit:
            return ConstraintDecision(BUY_REJECTED_LIMIT_UP, None, None, earliest)
        prices = self.normalizer.buy_prices(
            entry, stop, take,
            qfq_close=market.get("qfq_close"), raw_close=raw_close,
            tick_size=instrument_rule.price_tick if instrument_rule else None,
        )
        # A persisted pre-ADV20 signal must not regain BUY eligibility through
        # the old single-day amount. Both amounts use Tushare's 千元 unit.
        value = market.get("adv20_amount")
        try:
            amount = _dec(value)
        except (InvalidOperation, ValueError, TypeError):
            amount = Decimal(0)
        if not amount.is_finite() or amount <= 0:
            return ConstraintDecision(BUY_REJECTED_LIQUIDITY, prices, Decimal(0), earliest)
        max_notional = amount * Decimal(1000) * self.policy.max_participation_rate
        raw_shares = int((max_notional / prices.entry).to_integral_value(rounding=ROUND_FLOOR))
        max_shares = (Decimal(instrument_rule.floor_buy_quantity(raw_shares)) if instrument_rule
                      else Decimal(raw_shares // 100 * 100))
        if max_shares < (instrument_rule.min_buy_quantity if instrument_rule else 100):
            return ConstraintDecision(BUY_REJECTED_LIQUIDITY, prices, max_shares, earliest)
        return ConstraintDecision(None, prices, max_shares, earliest)

    def evaluate_sell(self, *, market: dict, available_quantity) -> tuple[str | None, Decimal, Decimal, date | None]:
        trade_date = date.fromisoformat(str(market["trade_date"])[:10])
        earliest = next_execution_session(trade_date, calendar=self.calendar)
        if earliest is None:
            return EXECUTION_CALENDAR_UNAVAILABLE, Decimal(0), Decimal(0), None
        if self._blocks_next_day(market):
            return SELL_REJECTED_SUSPENDED, Decimal(0), Decimal(0), earliest
        raw_close = _dec(market.get("raw_close"))
        down_limit = _dec(market.get("down_limit")) if market.get("down_limit") is not None else None
        if down_limit is not None and raw_close <= down_limit:
            return SELL_REJECTED_LIMIT_DOWN, Decimal(0), Decimal(0), earliest
        available = _dec(available_quantity)
        if available <= 0:
            return SELL_REJECTED_T1, Decimal(0), Decimal(0), earliest
        price, slippage = self.normalizer.sell_price(raw_close=raw_close)
        return None, price, slippage, earliest

    def fees(self, notional: Decimal, *, side: str) -> Decimal:
        commission = max(self.policy.minimum_commission, notional * self.policy.commission_rate)
        transfer = notional * self.policy.transfer_fee_rate
        stamp = notional * self.policy.stamp_duty_rate if side.upper() == "SELL" else Decimal(0)
        return (commission + transfer + stamp).quantize(Decimal("0.01"), rounding=ROUND_CEILING)
