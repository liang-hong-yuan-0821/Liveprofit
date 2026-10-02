# test-catalog-begin
# {
#   "purpose": "量化研究 / trial_registry",
#   "keywords": [
#     "量化研究",
#     "trial_registry"
#   ],
#   "covers": [
#     "backend/modules/quant_research/domain/trial_registry.py",
#     "backend/modules/quant_strategy/domain/templates.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

from collections import Counter

from backend.modules.quant_research.domain.trial_registry import preregistered_trials
from backend.modules.quant_strategy.domain.templates import TEMPLATES


def test_pre_registered_grid_is_stable_and_complete():
    first = preregistered_trials()
    assert first == preregistered_trials()
    assert len(first) == len({trial.trial_id for trial in first}) == 76
    counts = Counter(trial.family for trial in first)
    assert all(counts[template_id] == 4 for template_id in TEMPLATES)
    assert all(counts[family] == 12 for family in (
        "stock_medium_momentum", "stock_short_reversion",
        "etf_dual_momentum", "etf_defensive_allocation",
    ))
    assert all({trial.entry_variant for trial in first if trial.family == template_id}
               == {"BASELINE", "VARIANT"} for template_id in TEMPLATES)
    assert all({trial.management_policy for trial in first if trial.family == template_id}
               == {"CORRECTED_LEGACY", "MACD_MEAN_REVERSION" if template_id == "macd_rsi_reversal_v1"
                   else "TREND_3ATR"} for template_id in TEMPLATES)


def test_new_grid_parameters_match_predeclared_values():
    trials = preregistered_trials()
    defensive = [dict(t.parameters) for t in trials if t.family == "etf_defensive_allocation"]
    assert {item["vol_target"] for item in defensive} == {"0.04", "0.06", "0.08"}
    assert {item["cov_window"] for item in defensive} == {20, 60}
    assert {item["trend_ma"] for item in defensive} == {60, 120}
    assert len({tuple(sorted(item.items())) for item in defensive}) == 12


def test_arc_trial_freezes_rolling_neckline_variant():
    cells = [trial for trial in preregistered_trials()
             if trial.family == "arc_bottom_75a_v1"]
    for trial in cells:
        params = dict(trial.parameters)
        assert params.get("rolling_neckline", 0) == (1 if trial.entry_variant == "VARIANT" else 0)
        source, _ = TEMPLATES[trial.family].render(params)
        assert ("arc_neckline_40" in source) == (trial.entry_variant == "VARIANT")


def test_macd_trial_freezes_prior_five_day_oversold_variant():
    cells = [trial for trial in preregistered_trials()
             if trial.family == "macd_rsi_reversal_v1"]
    for trial in cells:
        params = dict(trial.parameters)
        assert params.get("five_day_oversold", 0) == (1 if trial.entry_variant == "VARIANT" else 0)


def test_volume_trial_freezes_median_baseline_variant():
    cells = [trial for trial in preregistered_trials()
             if trial.family == "volume_surge_confirm_v1"]
    for trial in cells:
        params = dict(trial.parameters)
        assert params.get("median_volume_baseline", 0) == (1 if trial.entry_variant == "VARIANT" else 0)


def test_pullback_trial_freezes_prior_low_variant():
    cells = [trial for trial in preregistered_trials()
             if trial.family == "trend_pullback_v1"]
    for trial in cells:
        params = dict(trial.parameters)
        assert params.get("prior_five_low_stop", 0) == (1 if trial.entry_variant == "VARIANT" else 0)


def test_pre_cross_trial_freezes_confirmed_variant():
    cells = [trial for trial in preregistered_trials()
             if trial.family == "ma5_pre_cross_v1"]
    for trial in cells:
        params = dict(trial.parameters)
        assert params.get("confirmed_cross", 0) == (1 if trial.entry_variant == "VARIANT" else 0)


def test_boll_trial_freezes_bandwidth_variant():
    cells = [trial for trial in preregistered_trials()
             if trial.family == "boll_volume_breakout_v1"]
    for trial in cells:
        params = dict(trial.parameters)
        assert params.get("low_bandwidth_quartile", 0) == (1 if trial.entry_variant == "VARIANT" else 0)


def test_ma_trend_trial_freezes_benchmark_variant():
    cells = [trial for trial in preregistered_trials()
             if trial.family == "ma_trend_cross_v1"]
    for trial in cells:
        params = dict(trial.parameters)
        assert params.get("benchmark_filter", 0) == (1 if trial.entry_variant == "VARIANT" else 0)
