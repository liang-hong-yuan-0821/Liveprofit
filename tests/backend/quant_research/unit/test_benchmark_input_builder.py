# test-catalog-begin
# {
#   "purpose": "量化研究 / benchmark_input_builder",
#   "keywords": [
#     "量化研究",
#     "交易日历",
#     "认证证书",
#     "来源观测",
#     "来源证据",
#     "个股分析",
#     "benchmark_input_builder",
#     "calendar",
#     "certificate",
#     "observation",
#     "source",
#     "stock"
#   ],
#   "covers": [
#     "backend/modules/quant_research/application/benchmark_input_builder.py",
#     "backend/modules/quant_research/application/dataset_builder.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pandas as pd
import pytest

from backend.modules.quant_research.application.benchmark_input_builder import build_benchmark_input
from backend.modules.quant_research.application.dataset_builder import DatasetQuality, ResearchDataset


def _snapshot():
    days = tuple(value.date() for value in pd.bdate_range("2025-01-02", periods=200))
    benchmark = pd.DataFrame([
        {"ts_code": "000300.SH", "trade_date": day, "close": 4000 + index,
         "source": "tushare"} for index, day in enumerate(days)
    ])
    quality = DatasetQuality(
        status="READY", issues=(), as_of=days[-1], source_rows={},
        certification_issues=("coverage_audit: historical completeness not verified",),
        instrument_types=("stock",),
    )
    return ResearchDataset({"benchmark_daily": benchmark}, quality), days


def test_complete_200_day_series_still_lacks_historical_source_certificate():
    dataset, days = _snapshot()
    result = build_benchmark_input(dataset, as_of=days[-1], trading_days=days, required_sessions=200)
    assert result.complete_local
    assert len(result.bars) == 200
    assert result.bars[0] == (days[0], Decimal(4000))
    assert result.bars[-1] == (days[-1], Decimal(4199))
    assert "benchmark_daily: source availability not verified" in result.certification_issues
    assert "coverage_audit: historical completeness not verified" in result.certification_issues


def test_only_requested_tail_is_required_but_a_missing_day_keeps_series_empty():
    dataset, days = _snapshot()
    dataset.tables["benchmark_daily"].drop(index=0, inplace=True)
    tail = build_benchmark_input(dataset, as_of=days[-1], trading_days=days, required_sessions=120)
    assert tail.complete_local and len(tail.bars) == 120
    dataset.tables["benchmark_daily"].drop(index=100, inplace=True)
    missing = build_benchmark_input(dataset, as_of=days[-1], trading_days=days, required_sessions=120)
    assert missing.bars == ()
    assert missing.missing_days == (days[100],)
    assert "benchmark_daily: required window incomplete" in missing.issues


@pytest.mark.parametrize("change,expected", [
    ("negative", "benchmark_daily: required window incomplete"),
    ("bool", "benchmark_daily: required window incomplete"),
    ("wrong_code", "benchmark_daily: invalid index identity"),
    ("null_code", "benchmark_daily: invalid index identity"),
    ("duplicate", "benchmark_daily: duplicate trade date"),
    ("future", "benchmark_daily: future market fact"),
    ("nonexchange", "benchmark_daily: nonexchange date"),
])
def test_invalid_observation_never_emits_partial_trial_series(change, expected):
    dataset, days = _snapshot()
    frame = dataset.tables["benchmark_daily"]
    if change in ("negative", "bool"):
        frame["close"] = frame["close"].astype(object)
        frame.loc[199, "close"] = -1 if change == "negative" else True
    elif change == "wrong_code":
        frame.loc[199, "ts_code"] = "000905.SH"
    elif change == "null_code":
        frame["ts_code"] = frame["ts_code"].astype("string")
        frame.loc[199, "ts_code"] = pd.NA
    elif change == "duplicate":
        frame.loc[200] = frame.loc[199]
    elif change == "future":
        frame.loc[200] = {"ts_code": "000300.SH", "trade_date": days[-1] + timedelta(days=1),
                          "close": 4200, "source": "tushare"}
    else:
        weekend = next(day for day in (days[0] + timedelta(days=step) for step in range(1, 10))
                       if day.weekday() >= 5)
        frame.loc[200] = {"ts_code": "000300.SH", "trade_date": weekend,
                          "close": 4200, "source": "tushare"}
    result = build_benchmark_input(dataset, as_of=days[-1], trading_days=days, required_sessions=200)
    assert result.bars == ()
    assert expected in result.issues


def test_fund_only_snapshot_and_calendar_mismatch_do_not_produce_stock_benchmark():
    dataset, days = _snapshot()
    fund = ResearchDataset(dataset.tables, replace(dataset.quality, instrument_types=("fund",)))
    result = build_benchmark_input(fund, as_of=days[-1], trading_days=days, required_sessions=120)
    assert result.bars == ()
    assert "dataset: stock universe not selected" in result.issues
    with pytest.raises(ValueError, match="calendar ending at as-of"):
        build_benchmark_input(dataset, as_of=days[-1], trading_days=days[:-1], required_sessions=120)
    with pytest.raises(ValueError, match="registered stock trial window"):
        build_benchmark_input(dataset, as_of=days[-1], trading_days=days, required_sessions=120.0)
