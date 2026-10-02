# test-catalog-begin
# {
#   "purpose": "数据源 / tushare_etf_basic：Research ETF universe includes delisted entries and explicit tracking metadata.",
#   "keywords": [
#     "数据源",
#     "ETF",
#     "持仓生命周期",
#     "tushare_etf_basic",
#     "etf",
#     "lifecycle"
#   ],
#   "covers": [
#     "AI/dataflows/providers/cn/tushare.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

"""Research ETF universe includes delisted entries and explicit tracking metadata."""

from unittest.mock import MagicMock

import pandas as pd

from AI.dataflows.providers.cn.tushare import TushareProvider


def _provider(by_status):
    provider = TushareProvider.__new__(TushareProvider)
    provider.name = "Tushare"
    provider.connected = True
    provider.api = MagicMock()
    provider.api.etf_basic.side_effect = lambda **kw: by_status[kw["list_status"]]
    provider._api_call = lambda fn, timeout=None, **kw: fn(**kw)
    return provider


def _row(code, status, index):
    return {"ts_code": code, "index_code": index, "exchange": "SH", "etf_type": "股票型",
            "list_date": "20120528", "list_status": status}


def test_etf_catalog_queries_all_statuses_and_preserves_delisted():
    provider = _provider({
        "L": pd.DataFrame([_row("510300.SH", "L", "000300.SH")]),
        "D": pd.DataFrame([_row("510301.SH", "D", "000300.SH")]),
        "P": pd.DataFrame([_row("510302.SH", "P", "000300.SH")]),
    })
    result = provider.get_etf_basic_df()
    assert result["ts_code"].tolist() == ["510300.SH", "510301.SH", "510302.SH"]
    assert result["list_date"].tolist() == ["2012-05-28"] * 3
    assert [call.kwargs["list_status"] for call in provider.api.etf_basic.call_args_list] == ["L", "D", "P"]


def test_missing_or_conflicting_etf_lifecycle_is_unavailable():
    frames = {
        "L": pd.DataFrame([_row("510300.SH", "L", "000300.SH")]),
        "D": pd.DataFrame([_row("510301.SH", "D", "000300.SH")]),
        "P": pd.DataFrame([_row("510302.SH", "P", "000300.SH")]),
    }
    frames["D"].loc[0, "ts_code"] = "510300.SH"
    assert _provider(frames).get_etf_basic_df() is None
    frames["D"] = pd.DataFrame()
    assert _provider(frames).get_etf_basic_df() is None


def test_unknown_listing_date_is_retained_without_certifying_that_etf():
    frames = {
        "L": pd.DataFrame([_row("510300.SH", "L", "000300.SH"),
                           _row("510301.SH", "L", "000300.SH")]),
        "D": pd.DataFrame([_row("510302.SH", "D", "000300.SH")]),
        "P": pd.DataFrame([_row("510303.SH", "P", "000300.SH")]),
    }
    frames["L"].loc[1, "list_date"] = None
    result = _provider(frames).get_etf_basic_df()
    assert len(result) == 4
    assert pd.isna(result.loc[result.ts_code.eq("510301.SH"), "list_date"]).all()
    assert result.loc[result.ts_code.eq("510300.SH"), "list_date"].iloc[0] == "2012-05-28"
