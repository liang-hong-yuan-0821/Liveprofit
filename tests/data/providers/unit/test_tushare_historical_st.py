# test-catalog-begin
# {
#   "purpose": "数据源 / tushare_historical_st：Historical ST membership must come from its dated source, not today's name.",
#   "keywords": [
#     "数据源",
#     "ST状态",
#     "tushare_historical_st",
#     "st"
#   ],
#   "covers": [
#     "AI/dataflows/providers/cn/tushare.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

"""Historical ST membership must come from its dated source, not today's name."""

from unittest.mock import MagicMock

import pandas as pd

from AI.dataflows.providers.cn.tushare import TushareProvider


def _provider(response):
    provider = TushareProvider.__new__(TushareProvider)
    provider.name = "Tushare"
    provider.connected = True
    provider.api = MagicMock()
    provider.api.stock_st.return_value = response
    provider._api_call = lambda fn, timeout=None, **kwargs: fn(**kwargs)
    return provider


def test_historical_st_uses_single_day_and_normalizes_members():
    provider = _provider(pd.DataFrame({
        "ts_code": ["600001.SH", "000001.SZ"], "trade_date": ["20260921"] * 2,
        "type": ["ST", "ST"], "type_name": ["风险警示板"] * 2,
    }))
    result = provider.get_historical_st_df("2026-09-21")
    assert result["ts_code"].tolist() == ["000001.SZ", "600001.SH"]
    assert result["trade_date"].tolist() == ["2026-09-21"] * 2
    assert provider.api.stock_st.call_args.kwargs == {
        "trade_date": "20260921", "fields": "ts_code,trade_date,type,type_name",
    }


def test_empty_or_wrong_day_is_unknown():
    assert _provider(pd.DataFrame()).get_historical_st_df("2026-09-21") is None
    wrong = pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20260920"], "type": ["ST"]})
    assert _provider(wrong).get_historical_st_df("2026-09-21") is None
