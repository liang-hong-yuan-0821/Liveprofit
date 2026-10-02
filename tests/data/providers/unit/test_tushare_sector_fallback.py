# test-catalog-begin
# {
#   "purpose": "数据源 / tushare_sector_fallback：Eastmoney's original DC K-line is a narrow fallback for proxy gaps.",
#   "keywords": [
#     "数据源",
#     "板块分析",
#     "tushare_sector_fallback",
#     "sector"
#   ],
#   "covers": [
#     "AI/dataflows/providers/cn/tushare.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

"""Eastmoney's original DC K-line is a narrow fallback for proxy gaps."""

from unittest.mock import Mock

from AI.dataflows.providers.cn.tushare import TushareProvider


def test_sector_fallback_parses_exact_dc_ohlc(monkeypatch):
    response = Mock()
    response.json.return_value = {"data": {
        "code": "BK0165", "market": 90,
        "klines": ["2026-09-23,18598.91,18598.71,18624.84,18524.81,14907664,30715904681.00,0.54,-0.03,-6.30,1.39"],
    }}
    request = Mock(return_value=response)
    monkeypatch.setattr("requests.get", request)

    frame = TushareProvider.__new__(TushareProvider).get_sector_daily_fallback_df(
        "dc", "BK0165.DC", "20260923", "20260923",
    )

    assert frame.to_dict("records") == [{
        "trade_date": "20260923", "open": "18598.91", "close": "18598.71",
        "high": "18624.84", "low": "18524.81", "vol": "14907664",
        "amount": "30715904681.00", "swing": "0.54", "pct_change": "-0.03",
        "change": "-6.30", "turnover_rate": "1.39", "ts_code": "BK0165.DC",
    }]
    assert request.call_args.kwargs["params"]["secid"] == "90.BK0165"


def test_sector_fallback_rejects_wrong_board_and_malformed_klines(monkeypatch):
    response = Mock()
    response.json.return_value = {"data": {"code": "BK9999", "market": 90, "klines": []}}
    monkeypatch.setattr("requests.get", Mock(return_value=response))
    provider = TushareProvider.__new__(TushareProvider)
    assert provider.get_sector_daily_fallback_df("dc", "BK0165.DC", "20260923", "20260923") is None
    response.json.return_value = {"data": {"code": "BK0165", "market": 90, "klines": ["2026-09-23,1,2"]}}
    assert provider.get_sector_daily_fallback_df("dc", "BK0165.DC", "20260923", "20260923") is None
