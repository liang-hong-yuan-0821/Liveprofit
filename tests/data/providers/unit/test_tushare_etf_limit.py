# test-catalog-begin
# {
#   "purpose": "数据源 / tushare_etf_limit：ETF limit evidence must be dated, complete and exchange-specific.",
#   "keywords": [
#     "数据源",
#     "ETF",
#     "来源证据",
#     "tushare_etf_limit",
#     "etf",
#     "source"
#   ],
#   "covers": [
#     "AI/dataflows/providers/cn/tushare.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

"""ETF limit evidence must be dated, complete and exchange-specific."""

from unittest.mock import MagicMock

import pandas as pd
import pytest

from AI.dataflows.providers.cn.tushare import TushareProvider


def _provider(frame):
    value = TushareProvider.__new__(TushareProvider)
    value.name = "Tushare"
    value.connected = True
    value.api = MagicMock()
    value.api.etf_limit.return_value = frame
    value._api_call = lambda fn, timeout=None, **kwargs: fn(**kwargs)
    return value


def _frame():
    return pd.DataFrame({
        "ts_code": ["518880.SH", "510300.SH"],
        "trade_date": ["20260924", "20260924"],
        "asset_type": ["ETF", "ETF"], "exchange": ["SSE", "SSE"],
        "up_limit": [10.8, 4.5], "down_limit": [9.2, 3.7],
    })


def test_etf_limit_uses_single_day_and_validates_price_bounds():
    provider = _provider(_frame())
    frame = provider.get_etf_limit_df("2026-09-24")
    assert frame["ts_code"].tolist() == ["510300.SH", "518880.SH"]
    assert frame["trade_date"].tolist() == ["2026-09-24"] * 2
    assert provider.api.etf_limit.call_args.kwargs == {
        "trade_date": "20260924",
        "fields": "ts_code,trade_date,asset_type,exchange,up_limit,down_limit",
    }


@pytest.mark.parametrize("column,value", [
    ("trade_date", "20260923"), ("asset_type", "STK"),
    ("up_limit", 0), ("down_limit", 20),
])
def test_etf_limit_rejects_wrong_source_or_values(column, value):
    frame = _frame()
    frame.loc[0, column] = value
    assert _provider(frame).get_etf_limit_df("20260924") is None
