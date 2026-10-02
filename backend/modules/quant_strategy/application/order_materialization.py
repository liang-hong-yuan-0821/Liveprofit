"""Shared signal-to-order persistence; caller holds the portfolio row lock."""
import uuid
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
from decimal import Decimal
from sqlalchemy import select
from backend.modules.investment_workspace.infrastructure.models import PortfolioPosition
from backend.modules.quant_strategy.infrastructure.lifecycle_models import SuggestedOrder
from backend.modules.quant_strategy.domain.instrument_rules import InstrumentTradingRule
from backend.modules.quant_strategy.infrastructure.instrument_rule_certificates import (
    InstrumentRuleCertificateRepository, LiveRuleAuthorization,
)
from .management_runtime import resolve_management_snapshot
from .planning_account import requires_account_reconciliation


def initial_exposure_quantity(*, planned_shares: Decimal, initial_exposure: Decimal,
                              rule: InstrumentTradingRule | None = None) -> Decimal:
    """Never round a partial first entry up past its planned risk budget."""
    if (not isinstance(planned_shares, Decimal) or not planned_shares.is_finite()
            or planned_shares < 0 or not isinstance(initial_exposure, Decimal)
            or not initial_exposure.is_finite() or not 0 < initial_exposure <= 1):
        raise ValueError("invalid initial exposure quantity")
    requested = int((planned_shares * initial_exposure).to_integral_value(rounding="ROUND_FLOOR"))
    if rule is not None:
        rule.validate()
        return Decimal(rule.floor_buy_quantity(requested))
    return Decimal(requested // 100 * 100)


def materialize_signal_orders(session, *, portfolio_id, rows, strategy_snapshots, industry_map,
                              instrument_rules: dict[str, InstrumentTradingRule] | None = None,
                              rule_authorizations: dict[str, LiveRuleAuthorization] | None = None,
                              calendar=None):
    from .execution import StrategySnapshotInvalidError
    existing = set(session.scalars(
        select(SuggestedOrder.source_signal_id).where(
            SuggestedOrder.source_signal_id.in_([r.id for r in rows])
        )
    )) if rows else set()
    position_ids = {
        (p.market, p.symbol): p.id for p in session.scalars(
            select(PortfolioPosition).where(PortfolioPosition.portfolio_id == portfolio_id)
        )
    }
    orders = []
    account_reconciliation_required = (
        any(row.signal_kind == "BUY" for row in rows)
        and requires_account_reconciliation(session, portfolio_id)
    )
    if (instrument_rules is not None or rule_authorizations is not None) and calendar is None:
        from backend.modules.market_data.infrastructure.calendar_adapter import MarketCalendarAdapter
        calendar = MarketCalendarAdapter()
    for signal in rows:
        if signal.id in existing:
            continue
        side = "BUY" if signal.signal_kind == "BUY" else "SELL"
        if side == "BUY" and account_reconciliation_required:
            signal.order_status = "BUY_REJECTED_ACCOUNT_RECONCILIATION"
            continue
        shares = Decimal(str(signal.shares))
        rule = instrument_rules.get(signal.ts_code) if side == "BUY" and instrument_rules is not None else None
        certificate_id = None
        rule_authorized_at = None
        if side == "BUY":
            try:
                if rule_authorizations is None:
                    raise ValueError("certified rule authorization required")
                market = signal.execution_market or {}
                decision_date = date.fromisoformat(str(market.get("trade_date"))[:10])
                execution_date = signal.earliest_execution_trade_date
                authorization = rule_authorizations.get(signal.ts_code)
                if not isinstance(authorization, LiveRuleAuthorization):
                    raise ValueError("certified rule authorization missing")
                current = InstrumentRuleCertificateRepository(session).resolve_live_now(
                    symbol=signal.ts_code, decision_date=decision_date,
                    execution_date=execution_date)
                if (current.certificate_id != authorization.certificate_id
                        or current.rule != authorization.rule
                        or (rule is not None and rule != current.rule)):
                    raise ValueError("certified rule authorization drift")
                certificate_id, rule = current.certificate_id, current.rule
                rule_authorized_at = current.authorized_at
                schedule = calendar.schedule("CN")
                sessions = tuple(item.trade_date for item in schedule.sessions)
                next_date = min((day for day in sessions if day > decision_date), default=None)
                if (not isinstance(rule, InstrumentTradingRule) or rule.symbol != signal.ts_code
                        or rule.asset_type != "stock"
                        or type(execution_date) is not date
                        or not schedule.available or decision_date not in sessions
                        or execution_date != next_date
                        or not rule.effective_from <= execution_date
                        or (rule.effective_through is not None and execution_date > rule.effective_through)
                        or rule.published_on >= decision_date):
                    raise ValueError
                rule.validate()
            except (ValueError, TypeError):
                signal.order_status = "BUY_REJECTED_INSTRUMENT_RULE"
                continue
        lifecycle_policy = strategy_snapshots.get(str(signal.strategy_version_id), {}).get("lifecycle_policy")
        if side == "BUY" and lifecycle_policy is not None:
            management = resolve_management_snapshot(strategy_snapshots[str(signal.strategy_version_id)])
            initial_exposure = management.initial_exposure if management else Decimal(str(
                lifecycle_policy.get("config", {}).get("initial_exposure_pct", "0.50")
            ))
            if management is None and initial_exposure not in {Decimal("0.50")}:
                raise StrategySnapshotInvalidError("生命周期首仓比例必须为 0.50")
            shares = initial_exposure_quantity(
                planned_shares=shares, initial_exposure=initial_exposure, rule=rule,
            )
        price = signal.order_cost_price or signal.order_entry_price or signal.valuation_price
        if price is None or shares <= 0:
            signal.order_status = "BUY_REJECTED_INITIAL_LOT" if side == "BUY" else "SELL_REJECTED_NO_POSITION"
            continue
        price = Decimal(str(price))
        stop = Decimal(str(signal.order_stop_price)) if signal.order_stop_price is not None else None
        fees = Decimal(str(signal.estimated_fees or 0))
        reserved_cash = (shares * price + fees) if side == "BUY" else Decimal(0)
        reserved_risk = (
            max(Decimal(0), price - stop) * shares if side == "BUY" and stop is not None else Decimal(0)
        )
        bucket = industry_map.get(signal.ts_code) or {}
        decision_at = datetime.now(timezone.utc)
        if side == "BUY" and (
                decision_at.astimezone(ZoneInfo("Asia/Shanghai")).date() != decision_date):
            signal.order_status = "BUY_REJECTED_INSTRUMENT_RULE"
            continue
        order = SuggestedOrder(
            id=uuid.uuid4(), portfolio_id=portfolio_id,
            position_id=(uuid.UUID(str(position_ids[("CN", signal.ts_code)]))
                         if position_ids.get(("CN", signal.ts_code)) else None),
            source_signal_id=signal.id, market="CN", symbol=signal.ts_code,
            rule_certificate_id=certificate_id,
            rule_authorized_at=rule_authorized_at,
            decision_at=decision_at,
            industry_code=bucket.get("industry_code"), side=side, quantity=shares,
            filled_quantity=Decimal(0), limit_price=price, stop_price=stop,
            reserved_cash=reserved_cash, reserved_risk=reserved_risk,
            reason_code=(signal.reason or signal.action or "QUANT_SIGNAL")[:64],
            status="PROPOSED", revision=1,
            earliest_execution_trade_date=signal.earliest_execution_trade_date,
        )
        session.add(order)
        orders.append(order)

    return orders
