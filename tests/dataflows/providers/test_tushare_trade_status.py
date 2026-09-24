"""Suspension source failures never become false suspension observations."""

from unittest.mock import MagicMock

import pandas as pd
import pytest

from AI.dataflows.providers.cn.tushare import TushareProvider


def provider(suspended):
    value = TushareProvider.__new__(TushareProvider)
    value.name = "Tushare"
    value.connected = True
    value.api = MagicMock()
    value._api_call = lambda fn, **kw: fn(**kw)
    value.api.stock_basic.return_value = pd.DataFrame({"ts_code": ["000001.SZ"], "name": ["平安银行"], "market": ["主板"]})
    value.api.stk_limit.return_value = pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20260922"], "up_limit": [11.], "down_limit": [9.]})
    value.api.suspend_d.return_value = suspended
    return value


@pytest.mark.parametrize("suspended", [
    None, pd.DataFrame(),
    pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20260921"], "suspend_type": ["S"]}),
    pd.DataFrame({"ts_code": ["000001.SZ"] * 6000, "trade_date": ["20260922"] * 6000, "suspend_type": ["S"] * 6000}),
    pd.DataFrame({"ts_code": [f"{i:06}.SZ" for i in range(5000)], "trade_date": ["20260922"] * 5000, "suspend_type": ["S"] * 5000}),
    pd.DataFrame({"ts_code": ["000001.SZ", "000001.SZ"], "trade_date": ["20260922"] * 2, "suspend_type": ["S"] * 2}),
    pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20260922"], "suspend_type": ["X"]}),
])
def test_unknown_suspension_response_is_unavailable(suspended):
    assert provider(suspended).get_full_market_trade_status_df("2026-09-22") is None


def test_successful_schema_bearing_empty_set_can_prove_false():
    frame = provider(pd.DataFrame(columns=["ts_code", "trade_date", "suspend_type"])).get_full_market_trade_status_df("20260922")
    assert frame is not None and not bool(frame.iloc[0]["is_suspended"])


def test_dated_suspension_is_observed_true():
    value = provider(pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20260922"], "suspend_type": ["S"]}))
    frame = value.get_full_market_trade_status_df("20260922")
    assert bool(frame.iloc[0]["is_suspended"])
    assert value.api.suspend_d.call_args.kwargs["trade_date"] == "20260922"


def test_resume_event_does_not_prove_suspension():
    frame = provider(pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20260922"], "suspend_type": ["R"]})).get_full_market_trade_status_df("20260922")
    assert frame is not None and not bool(frame.iloc[0]["is_suspended"])


def test_stale_limit_response_rejects_full_status():
    value = provider(pd.DataFrame(columns=["ts_code", "trade_date", "suspend_type"]))
    value.api.stk_limit.return_value["trade_date"] = "20260921"
    assert value.get_full_market_trade_status_df("20260922") is None
