"""Frozen, deterministic baseline trial grid, independent of observed returns."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product

from backend.modules.quant_strategy.domain.templates import TEMPLATES

OLD_MANAGEMENT = ("CORRECTED_LEGACY", "FAMILY_POLICY")
OLD_ENTRY = ("BASELINE", "VARIANT")
NEW_POLICY_IDS = {
    "stock_medium_momentum": "STOCK_MEDIUM_TREND_EXIT",
    "stock_short_reversion": "STOCK_REVERSION_RULE_BASED",
    "etf_dual_momentum": "ETF_DUAL_MOMENTUM_3ATR",
    "etf_defensive_allocation": "ETF_DEFENSIVE_3ATR",
}
NEW_GRIDS: dict[str, dict[str, tuple]] = {
    "stock_medium_momentum": {"lookback": (60, 120), "slots": (10, 20), "benchmark_ma": (60, 120, 200)},
    "stock_short_reversion": {"reversal_days": (2, 3), "z_threshold": ("1.5", "2", "2.5"),
                              "exit_days": (3, 5)},
    "etf_dual_momentum": {"lookback": (60, 120, 180), "slots": (1, 2),
                          "rebalance": ("WEEKLY", "MONTHLY")},
    "etf_defensive_allocation": {"cov_window": (20, 60),
                                  "vol_target": ("0.04", "0.06", "0.08"),
                                  "trend_ma": (60, 120)},
}


@dataclass(frozen=True)
class TrialSpec:
    trial_id: str
    family: str
    entry_variant: str
    management_policy: str
    parameters: tuple[tuple[str, int | str], ...]


def preregistered_trials() -> tuple[TrialSpec, ...]:
    """Return exactly 28 corrected legacy cells plus 48 new-family cells."""
    trials = []
    for template_id in TEMPLATES:
        for entry, policy in product(OLD_ENTRY, OLD_MANAGEMENT):
            policy_id = (policy if policy == "CORRECTED_LEGACY" else
                         "MACD_MEAN_REVERSION" if template_id == "macd_rsi_reversal_v1" else "TREND_3ATR")
            parameters = ()
            if entry == "VARIANT":
                if template_id == "arc_bottom_75a_v1":
                    parameters = (("rolling_neckline", 1),)
                elif template_id == "macd_rsi_reversal_v1":
                    parameters = (("five_day_oversold", 1),)
                elif template_id == "volume_surge_confirm_v1":
                    parameters = (("median_volume_baseline", 1),)
                elif template_id == "trend_pullback_v1":
                    parameters = (("prior_five_low_stop", 1),)
                elif template_id == "ma5_pre_cross_v1":
                    parameters = (("confirmed_cross", 1),)
                elif template_id == "boll_volume_breakout_v1":
                    parameters = (("low_bandwidth_quartile", 1),)
                elif template_id == "ma_trend_cross_v1":
                    parameters = (("benchmark_filter", 1),)
            trials.append(TrialSpec(
                trial_id=f"{template_id}:{entry}:{policy}", family=template_id,
                entry_variant=entry, management_policy=policy_id, parameters=parameters,
            ))
    for family, grid in NEW_GRIDS.items():
        names = tuple(grid)
        for values in product(*(grid[name] for name in names)):
            parameters = tuple(zip(names, values))
            slug = ":".join(str(value) for value in values)
            trials.append(TrialSpec(
                trial_id=f"{family}:{slug}", family=family,
                entry_variant="GRID", management_policy=NEW_POLICY_IDS[family],
                parameters=parameters,
            ))
    if len(trials) != 76 or len({trial.trial_id for trial in trials}) != 76:
        raise RuntimeError("preregistered trial grid must contain exactly 76 unique cells")
    return tuple(trials)
