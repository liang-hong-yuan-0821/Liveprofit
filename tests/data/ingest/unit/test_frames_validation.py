# test-catalog-begin
# {
#   "purpose": "数据采集 / frames_validation：Unit checks for single-day market frame consistency before PG writes.",
#   "keywords": [
#     "数据采集",
#     "每日",
#     "复权因子",
#     "个股分析",
#     "frames_validation",
#     "daily",
#     "factor",
#     "stock"
#   ],
#   "covers": [
#     "db/instrument/ingest/frames.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

"""Unit checks for single-day market frame consistency before PG writes."""

import pandas as pd
import pytest

from db.instrument.ingest import frames

DAY = "20260922"


def _response():
    return {
        ("stock", "daily"): pd.DataFrame({
            "ts_code": ["000001.SZ", "000002.SZ"], "trade_date": [DAY, DAY],
        }),
        ("stock", "factor"): pd.DataFrame({
            "ts_code": ["000001.SZ", "000002.SZ", "000003.SZ"],
            "trade_date": [DAY] * 3, "adj_factor": [1.2, 1.1, 1.0],
        }),
        ("fund", "daily"): pd.DataFrame({
            "ts_code": ["510300.SH"], "trade_date": [DAY],
        }),
        ("fund", "factor"): pd.DataFrame({
            "ts_code": ["510300.SH"], "trade_date": [DAY], "adj_factor": [1.0],
        }),
    }


def _fetch(monkeypatch, response):
    monkeypatch.setattr(frames, "_pull_market_frame",
                        lambda _provider, _day, market, kind, _codes: response[(market, kind)])
    return frames.fetch_day_frames(object(), DAY, ["000001.SZ", "000002.SZ"],
                                   ["510300.SH"])


def test_suspended_stock_factor_can_exceed_daily_codes(monkeypatch):
    result = _fetch(monkeypatch, _response())
    assert len(result["daily"]) == 3
    assert len(result["factor"]) == 4


@pytest.mark.parametrize("mutation, message", [
    ("missing_stock_factor", "股票日线代码缺少同日复权因子"),
    ("wrong_day", "返回日期不匹配"),
    ("duplicate", "代码/日期重复"),
    ("zero_factor", "复权因子非正"),
    ("empty_stocks", "股票日线整日为空"),
    ("empty_funds", "基金日线整日为空"),
    ("missing_fund_factor", "基金日线代码缺少同日复权因子"),
])
def test_partial_or_invalid_response_is_rejected_before_write(monkeypatch, mutation, message):
    response = _response()
    if mutation == "missing_stock_factor":
        response[("stock", "factor")] = response[("stock", "factor")].iloc[[0, 2]]
    elif mutation == "wrong_day":
        response[("stock", "factor")].loc[0, "trade_date"] = "20260923"
    elif mutation == "duplicate":
        response[("stock", "factor")] = pd.concat([
            response[("stock", "factor")], response[("stock", "factor")].iloc[[0]],
        ])
    elif mutation == "zero_factor":
        response[("stock", "factor")].loc[0, "adj_factor"] = 0.0
    elif mutation == "empty_funds":
        response[("fund", "daily")] = response[("fund", "daily")].iloc[0:0]
    elif mutation == "missing_fund_factor":
        response[("fund", "factor")] = response[("fund", "factor")].iloc[0:0]
    else:
        response[("stock", "daily")] = response[("stock", "daily")].iloc[0:0]

    with pytest.raises(frames.StoreFetchError, match=message):
        _fetch(monkeypatch, response)
