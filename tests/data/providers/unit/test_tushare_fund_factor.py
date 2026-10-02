# test-catalog-begin
# {
#   "purpose": "数据源 / tushare_fund_factor：The fund factor endpoint is a separate raw-price capability from stock qfq.",
#   "keywords": [
#     "数据源",
#     "每日",
#     "复权因子",
#     "历史审计",
#     "市场分析",
#     "tushare_fund_factor",
#     "daily",
#     "factor",
#     "history",
#     "market"
#   ],
#   "covers": [
#     "AI/dataflows/providers/base_provider.py",
#     "AI/dataflows/providers/cn/tushare.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

"""The fund factor endpoint is a separate raw-price capability from stock qfq."""

from unittest.mock import MagicMock

import pandas as pd
import pytest

from AI.dataflows.providers.base_provider import BaseStockDataProvider
from AI.dataflows.providers.cn.tushare import FUND_FACTOR_FIELDS, TushareProvider


def _provider(response):
    value = TushareProvider.__new__(TushareProvider)
    value.name = "Tushare"
    value.connected = True
    value.api = MagicMock()
    value.api.fund_factor_pro.return_value = response
    value._api_call = lambda fn, timeout=None, **kwargs: fn(**kwargs)
    return value


def test_fund_factor_uses_official_signature_and_normalizes_descending_rows():
    frame = pd.DataFrame({
        "ts_code": ["510300.SH", "510300.SH"],
        "trade_date": ["20260923", "20260922"],
        "close": [4.2, 4.1], "atr_bfq": [0.12, 0.11],
    })
    provider = _provider(frame)
    result = provider.get_fund_factor_df("510300.SH", "2026-09-22", "2026-09-23")
    assert result["trade_date"].tolist() == ["2026-09-22", "2026-09-23"]
    assert result["ts_code"].tolist() == ["510300.SH", "510300.SH"]
    assert result["atr_bfq"].tolist() == [0.11, 0.12]
    kwargs = provider.api.fund_factor_pro.call_args.kwargs
    assert kwargs == {
        "ts_code": "510300.SH", "start_date": "20260922", "end_date": "20260923",
        "fields": FUND_FACTOR_FIELDS,
    }


def test_missing_fund_data_remains_unavailable():
    assert _provider(None).get_fund_factor_df("510300.SH", "20260922", "20260923") is None
    wrong_code = pd.DataFrame({"ts_code": ["518880.SH"], "trade_date": ["20260923"]})
    assert _provider(wrong_code).get_fund_factor_df("510300.SH", "20260922", "20260923") is None


def test_base_provider_declares_unsupported_structured_capability():
    # No subclass override means a structured consumer sees None, never text.
    assert BaseStockDataProvider.get_fund_factor_df(
        MagicMock(name="fallback"), "510300.SH", "20260922", "20260923",
    ) is None


def test_full_market_fund_factors_query_one_date():
    provider = _provider(pd.DataFrame({
        "ts_code": ["518880.SH", "510300.SH"],
        "trade_date": ["20260921", "20260921"],
        "close": [10.2, 4.1], "atr_bfq": [0.2, 0.1],
    }))
    result = provider.get_full_market_fund_factor_df("2026-09-21")
    assert result["ts_code"].tolist() == ["510300.SH", "518880.SH"]
    assert result["trade_date"].tolist() == ["2026-09-21"] * 2
    assert provider.api.fund_factor_pro.call_args.kwargs == {
        "trade_date": "20260921", "fields": FUND_FACTOR_FIELDS,
    }


def _daily_history():
    frame = pd.DataFrame({column: [1.0, 1.0] for column in TushareProvider._STORE_DAILY_COLS})
    frame["ts_code"] = ["510300.SH", "510300.SH"]
    frame["trade_date"] = ["20260923", "20260922"]
    return frame


def test_fund_daily_history_is_symbol_scoped_and_ascending():
    provider = _provider(None)
    provider.api.fund_daily.return_value = _daily_history()
    result = provider.get_fund_daily_df("510300.SH", "2026-09-22", "2026-09-23")
    assert result["trade_date"].tolist() == ["2026-09-22", "2026-09-23"]
    assert provider.api.fund_daily.call_args.kwargs["ts_code"] == "510300.SH"
    assert BaseStockDataProvider.get_fund_daily_df(
        MagicMock(), "510300.SH", "2026-09-22", "2026-09-23",
    ) is None


@pytest.mark.parametrize("field,value", [
    ("ts_code", "518880.SH"), ("trade_date", "20260924"), ("close", 0),
])
def test_fund_history_rejects_wrong_identity_or_invalid_price(field, value):
    provider = _provider(None)
    frame = _daily_history()
    frame.loc[0, field] = value
    provider.api.fund_daily.return_value = frame
    assert provider.get_fund_daily_df("510300.SH", "2026-09-22", "2026-09-23") is None
