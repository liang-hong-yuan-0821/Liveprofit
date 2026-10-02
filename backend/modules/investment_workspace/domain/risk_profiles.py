"""Frozen upper budgets for a portfolio's explicit admission risk profile."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Mapping


@dataclass(frozen=True)
class RiskProfileLimits:
    per_trade: Decimal
    open_risk: Decimal
    total_exposure: Decimal
    single_stock: Decimal
    single_etf: Decimal
    drawdown: Decimal

    @property
    def sector_open_risk(self) -> Decimal:
        return self.open_risk / 2

    @property
    def daily_new_risk(self) -> Decimal:
        return self.open_risk / 2

    def account_caps(self) -> dict[str, Decimal]:
        return {
            "risk_per_trade_pct": self.per_trade,
            "max_portfolio_open_risk_pct": self.open_risk,
            "max_total_position_pct": self.total_exposure,
            "max_single_stock_pct": self.single_stock,
            "max_drawdown_pct": self.drawdown,
            "max_sector_open_risk_pct": self.sector_open_risk,
            "max_daily_new_risk_pct": self.daily_new_risk,
        }


PROFILES = {
    "CONSERVATIVE": RiskProfileLimits(Decimal("0.0025"), Decimal("0.02"), Decimal("0.50"),
                                      Decimal("0.05"), Decimal("0.20"), Decimal("0.08")),
    "BALANCED": RiskProfileLimits(Decimal("0.005"), Decimal("0.04"), Decimal("0.75"),
                                  Decimal("0.08"), Decimal("0.25"), Decimal("0.15")),
    "AGGRESSIVE": RiskProfileLimits(Decimal("0.0075"), Decimal("0.06"), Decimal("0.90"),
                                    Decimal("0.10"), Decimal("0.30"), Decimal("0.25")),
}


def profile_budget_violations(profile: str | None, risk: Mapping[str, object]) -> tuple[str, ...]:
    """Return over-budget or unproved fields; lower account limits are allowed."""
    limits = PROFILES.get(profile) if isinstance(profile, str) else None
    if limits is None:
        return ("risk_profile",)
    caps = limits.account_caps()
    violations = []
    for field, cap in caps.items():
        try:
            value = Decimal(str(risk[field]))
        except (KeyError, InvalidOperation, TypeError, ValueError):
            violations.append(field)
            continue
        if not value.is_finite() or not 0 < value <= cap:
            violations.append(field)
    return tuple(violations)
