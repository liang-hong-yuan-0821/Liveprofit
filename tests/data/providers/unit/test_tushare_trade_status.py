# test-catalog-begin
# {
#   "purpose": "数据源 / tushare_trade_status：Suspension source failures never become false suspension observations.",
#   "keywords": [
#     "数据源",
#     "每日",
#     "表结构",
#     "来源证据",
#     "ST状态",
#     "状态",
#     "个股分析",
#     "停牌",
#     "tushare_trade_status",
#     "daily",
#     "schema",
#     "source",
#     "st",
#     "status",
#     "stock",
#     "suspension"
#   ],
#   "covers": [
#     "AI/dataflows/providers/cn/tushare.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

"""Suspension source failures never become false suspension observations."""

from unittest.mock import MagicMock

import pandas as pd
import pytest

from AI.dataflows.providers.cn.tushare import TushareProvider


def provider(suspended):
    if suspended is not None and "suspend_timing" not in suspended.columns:
        suspended = suspended.assign(suspend_timing=None)
    value = TushareProvider.__new__(TushareProvider)
    value.name = "Tushare"
    value.connected = True
    value.api = MagicMock()
    value._api_call = lambda fn, **kw: fn(**kw)
    value.get_stock_basic_df = MagicMock(return_value=pd.DataFrame({
        "ts_code": ["000001.SZ"], "name": ["平安银行"], "market": ["主板"],
        "list_date": ["1991-04-03"], "delist_date": [None],
    }))
    value.api.stk_limit.return_value = pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20260922"], "up_limit": [11.], "down_limit": [9.]})
    value.api.daily.return_value = pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20260922"]})
    value.api.suspend_d.return_value = suspended
    value.api.stock_st.return_value = pd.DataFrame({
        "ts_code": ["600001.SH"], "trade_date": ["20260922"], "type": ["ST"],
    })
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


def test_verified_independent_st_observations_replace_empty_vendor_st_only():
    value = provider(pd.DataFrame(columns=["ts_code", "trade_date", "suspend_type", "suspend_timing"]))
    value.get_historical_st_df = MagicMock(return_value=None)
    observed = pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20260922"],
                             "is_st": [True], "reported_trading": [True], "st_conflict": [False]})
    result = value.get_full_market_trade_status_df("20260922", st_observations=observed)
    assert result is not None
    assert result.iloc[0]["is_st"]
    assert result.attrs["st_source"] == "baostock_kline"
    value.get_historical_st_df.assert_not_called()
    assert value.get_full_market_trade_status_df(
        "20260922", st_observations=observed.assign(trade_date="20260921"),
    ) is None


def test_independent_no_trade_and_empty_daily_can_resolve_full_day_pause():
    value = provider(pd.DataFrame(columns=["ts_code", "trade_date", "suspend_type", "suspend_timing"]))
    value.api.daily.return_value = pd.DataFrame(columns=["ts_code", "trade_date"])
    observed = pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20260922"],
                             "is_st": [True], "reported_trading": [False], "st_conflict": [False]})
    result = value.get_full_market_trade_status_df("20260922", st_observations=observed)
    assert result is not None
    assert result.iloc[0]["is_suspended"]
    assert result.iloc[0]["suspension_scope"] == "full_day"
    value.api.daily.return_value = pd.DataFrame({
        "ts_code": ["000001.SZ"], "trade_date": ["20260922"],
    })
    assert value.get_full_market_trade_status_df(
        "20260922", st_observations=observed,
    ) is None
    value.api.daily.return_value = pd.DataFrame(columns=["ts_code", "trade_date"])
    assert value.get_full_market_trade_status_df(
        "20260922", st_observations=observed.assign(reported_trading=True),
    ) is None
    value.api.suspend_d.return_value = pd.DataFrame({
        "ts_code": ["000001.SZ", "000001.SZ"],
        "trade_date": ["20260922", "20260922"],
        "suspend_type": ["S", "R"], "suspend_timing": [None, None],
    })
    assert value.get_full_market_trade_status_df(
        "20260922", st_observations=observed,
    ) is None


def test_independent_st_conflict_excludes_only_disputed_symbol():
    value = provider(pd.DataFrame(columns=["ts_code", "trade_date", "suspend_type"]))
    first = value.get_stock_basic_df.return_value
    value.get_stock_basic_df.return_value = pd.concat([
        first, first.assign(ts_code="000002.SZ"),
    ], ignore_index=True)
    bars = value.api.daily.return_value
    value.api.daily.return_value = pd.concat([
        bars, bars.assign(ts_code="000002.SZ"),
    ], ignore_index=True)
    observed = pd.DataFrame({
        "ts_code": ["000001.SZ", "000002.SZ"], "trade_date": ["20260922"] * 2,
        "is_st": [True, False], "reported_trading": [True, True],
        "st_conflict": [True, False],
    })
    result = value.get_full_market_trade_status_df("20260922", st_observations=observed)
    assert result is not None and result["ts_code"].tolist() == ["000002.SZ"]
    assert result.attrs["ambiguous_codes"] == ["000001.SZ"]
    for invalid in (observed.drop(columns="st_conflict"), observed.assign(st_conflict="false")):
        assert value.get_full_market_trade_status_df("20260922", st_observations=invalid) is None


def test_sh_sz_independent_st_keeps_bse_membership_on_vendor_source():
    value = provider(pd.DataFrame(columns=["ts_code", "trade_date", "suspend_type"]))
    first = value.get_stock_basic_df.return_value
    value.get_stock_basic_df.return_value = pd.concat([
        first, first.assign(ts_code="920001.BJ", market="北交所", list_date="2022-01-01"),
    ], ignore_index=True)
    bars = value.api.daily.return_value
    value.api.daily.return_value = pd.concat([
        bars, bars.assign(ts_code="920001.BJ"),
    ], ignore_index=True)
    observed = pd.DataFrame({
        "ts_code": ["000001.SZ"], "trade_date": ["20260922"],
        "is_st": [False], "reported_trading": [True], "st_conflict": [False],
    })
    value.api.stock_st.return_value["ts_code"] = "920001.BJ"
    result = value.get_full_market_trade_status_df("20260922", st_observations=observed)
    assert result is not None
    assert dict(zip(result.ts_code, result.is_st)) == {"000001.SZ": False, "920001.BJ": True}
    value.get_historical_st_df = MagicMock(return_value=None)
    result = value.get_full_market_trade_status_df("20260922", st_observations=observed)
    assert result.ts_code.tolist() == ["000001.SZ"]
    assert result.attrs["ambiguous_codes"] == ["920001.BJ"]


def test_scoped_no_trade_evidence_still_requires_vendor_st_and_empty_daily():
    value = provider(pd.DataFrame(columns=["ts_code", "trade_date", "suspend_type"]))
    value.api.daily.return_value = pd.DataFrame(columns=["ts_code", "trade_date"])
    observed = pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20260922"],
                             "reported_trading": [False]})
    result = value.get_full_market_trade_status_df("20260922", no_trade_observations=observed)
    assert result is not None and result.iloc[0].suspension_scope == "full_day"
    assert result.attrs["st_source"] == "tushare"
    assert result.attrs["uses_independent_trading"] is True
    for invalid in (observed.assign(trade_date="20260921"), observed.assign(reported_trading=True),
                    observed.assign(ts_code="000002.SZ"), pd.concat([observed, observed])):
        assert value.get_full_market_trade_status_df("20260922", no_trade_observations=invalid) is None
    value.api.daily.return_value = pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20260922"]})
    assert value.get_full_market_trade_status_df("20260922", no_trade_observations=observed) is None
    value.api.daily.return_value = pd.DataFrame(columns=["ts_code", "trade_date"])
    value.get_historical_st_df = MagicMock(return_value=None)
    assert value.get_full_market_trade_status_df("20260922", no_trade_observations=observed) is None


@pytest.mark.parametrize("events", [
    [("R", None)], [("S", "09:30-10:00")], [("S", "09:30-10:00"), ("R", None)],
])
def test_no_trade_source_conflict_with_timed_halt_or_resume_is_local(events):
    value = provider(pd.DataFrame({
        "ts_code": ["000001.SZ"] * len(events), "trade_date": ["20260922"] * len(events),
        "suspend_type": [item[0] for item in events], "suspend_timing": [item[1] for item in events],
    }))
    basics = value.get_stock_basic_df.return_value
    value.get_stock_basic_df.return_value = pd.concat([basics, basics.assign(ts_code="000002.SZ")])
    value.api.daily.return_value = pd.DataFrame({"ts_code": ["000002.SZ"], "trade_date": ["20260922"]})
    observed = pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20260922"],
                             "reported_trading": [False]})
    result = value.get_full_market_trade_status_df("20260922", no_trade_observations=observed)
    assert result.ts_code.tolist() == ["000002.SZ"]
    assert result.attrs["ambiguous_codes"] == ["000001.SZ"]


def test_dated_suspension_is_observed_true():
    value = provider(pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20260922"], "suspend_type": ["S"]}))
    value.api.daily.return_value = pd.DataFrame(columns=["ts_code", "trade_date"])
    frame = value.get_full_market_trade_status_df("20260922")
    assert bool(frame.iloc[0]["is_suspended"])
    assert value.api.suspend_d.call_args.kwargs["trade_date"] == "20260922"


def test_blank_halt_timing_requires_independent_daily_presence_check():
    value = provider(pd.DataFrame({
        "ts_code": ["000001.SZ"], "trade_date": ["20260922"],
        "suspend_type": ["S"], "suspend_timing": [None],
    }))
    with_bar = value.get_full_market_trade_status_df("20260922")
    assert with_bar is None
    value.api.daily.return_value = pd.DataFrame(columns=["ts_code", "trade_date"])
    without_bar = value.get_full_market_trade_status_df("20260922")
    assert without_bar.iloc[0]["suspension_scope"] == "full_day"


def test_untimed_s_with_bar_excludes_only_conflicting_symbol():
    value = provider(pd.DataFrame({
        "ts_code": ["000001.SZ"], "trade_date": ["20260922"],
        "suspend_type": ["S"], "suspend_timing": [None],
    }))
    basics = value.get_stock_basic_df.return_value
    value.get_stock_basic_df.return_value = pd.concat([
        basics, basics.assign(ts_code="000002.SZ"),
    ], ignore_index=True)
    value.api.daily.return_value = pd.concat([
        value.api.daily.return_value,
        value.api.daily.return_value.assign(ts_code="000002.SZ"),
    ], ignore_index=True)
    frame = value.get_full_market_trade_status_df("20260922")
    assert frame is not None and frame.ts_code.tolist() == ["000002.SZ"]
    assert frame.attrs["ambiguous_codes"] == ["000001.SZ"]


def test_missing_daily_source_cannot_certify_full_day_halt():
    value = provider(pd.DataFrame({
        "ts_code": ["000001.SZ"], "trade_date": ["20260922"],
        "suspend_type": ["S"], "suspend_timing": [None],
    }))
    value.api.daily.return_value = None
    assert value.get_full_market_trade_status_df("20260922") is None


def test_resume_event_does_not_prove_suspension():
    frame = provider(pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20260922"], "suspend_type": ["R"]})).get_full_market_trade_status_df("20260922")
    assert frame is not None and not bool(frame.iloc[0]["is_suspended"])


def test_conflicting_same_day_events_leave_only_that_code_unknown():
    value = provider(pd.DataFrame({
        "ts_code": ["000001.SZ", "000001.SZ"],
        "trade_date": ["20260922", "20260922"],
        "suspend_type": ["S", "R"],
    }))
    basics = value.get_stock_basic_df.return_value
    value.get_stock_basic_df.return_value = pd.concat([
        basics, basics.assign(ts_code="000002.SZ")], ignore_index=True,
    )
    limits = value.api.stk_limit.return_value
    value.api.stk_limit.return_value = pd.concat([
        limits, limits.assign(ts_code="000002.SZ")], ignore_index=True,
    )
    value.api.daily.return_value = pd.concat([
        value.api.daily.return_value,
        value.api.daily.return_value.assign(ts_code="000002.SZ"),
    ], ignore_index=True)
    frame = value.get_full_market_trade_status_df("20260922")
    assert frame is not None
    assert frame.ts_code.tolist() == ["000002.SZ"]
    assert frame.attrs["ambiguous_codes"] == ["000001.SZ"]


def test_same_day_resume_and_timed_halt_is_intraday_status():
    value = provider(pd.DataFrame({
        "ts_code": ["000001.SZ", "000001.SZ"],
        "trade_date": ["20260922", "20260922"],
        "suspend_type": ["R", "S"],
        "suspend_timing": [None, "9:30-9:40"],
    }))
    frame = value.get_full_market_trade_status_df("20260922")
    assert frame is not None and frame.ts_code.tolist() == ["000001.SZ"]
    assert frame.iloc[0]["suspension_scope"] == "intraday"
    assert frame.iloc[0]["suspend_timing"] == "9:30-9:40"
    assert frame.attrs["ambiguous_codes"] == []
    assert "suspend_timing" in value.api.suspend_d.call_args.kwargs["fields"]


@pytest.mark.parametrize("timing", [
    "09:59:32-10:09:32",
    "10:36:08-10:46:08,10:57:24-11:07:24",
    "09:33-09:43,11:10-11:10",
])
def test_second_precision_halt_with_bar_is_intraday(timing):
    value = provider(pd.DataFrame({
        "ts_code": ["000001.SZ", "000001.SZ"],
        "trade_date": ["20260922", "20260922"],
        "suspend_type": ["R", "S"],
        "suspend_timing": [None, timing],
    }))
    frame = value.get_full_market_trade_status_df("20260922")
    assert frame is not None and frame.ts_code.tolist() == ["000001.SZ"]
    assert frame.iloc[0]["suspension_scope"] == "intraday"
    assert frame.iloc[0]["suspend_timing"] == timing


@pytest.mark.parametrize("timing", [
    "09:59:60-10:09:32",
    "10:36:08-10:46:08,10:40:00-10:50:00",
    "11:10-11:10",
    "09:33-09:43,11:11-11:10",
    "09:33-09:43,09:40-09:40",
])
def test_invalid_second_precision_halt_remains_unknown(timing):
    value = provider(pd.DataFrame({
        "ts_code": ["000001.SZ", "000001.SZ"],
        "trade_date": ["20260922", "20260922"],
        "suspend_type": ["R", "S"],
        "suspend_timing": [None, timing],
    }))
    frame = value.get_full_market_trade_status_df("20260922")
    assert frame is None or frame.attrs["ambiguous_codes"] == ["000001.SZ"]


def test_stale_limit_response_rejects_full_status():
    value = provider(pd.DataFrame(columns=["ts_code", "trade_date", "suspend_type"]))
    value.api.stk_limit.return_value["trade_date"] = "20260921"
    assert value.get_full_market_trade_status_df("20260922") is None


def test_large_limit_response_with_all_active_codes_is_usable():
    value = provider(pd.DataFrame(columns=["ts_code", "trade_date", "suspend_type"]))
    extra = pd.DataFrame({
        "ts_code": [f"{i:06}.SZ" for i in range(100000, 106001)],
        "trade_date": ["20260922"] * 6001,
        "up_limit": [11.] * 6001,
        "down_limit": [9.] * 6001,
    })
    value.api.stk_limit.return_value = pd.concat(
        [value.api.stk_limit.return_value, extra], ignore_index=True,
    )
    frame = value.get_full_market_trade_status_df("20260922")
    assert frame is not None and frame["ts_code"].tolist() == ["000001.SZ"]


def test_missing_active_limit_keeps_status_but_limit_unknown():
    value = provider(pd.DataFrame(columns=["ts_code", "trade_date", "suspend_type"]))
    value.api.stk_limit.return_value = pd.DataFrame(
        columns=["ts_code", "trade_date", "up_limit", "down_limit"]
    )
    frame = value.get_full_market_trade_status_df("20260922")
    assert frame is not None and frame.ts_code.tolist() == ["000001.SZ"]
    assert pd.isna(frame.iloc[0]["up_limit"])


def test_missing_daily_and_suspension_excludes_only_unproven_symbol():
    value = provider(pd.DataFrame(columns=["ts_code", "trade_date", "suspend_type"]))
    basics = value.get_stock_basic_df.return_value
    value.get_stock_basic_df.return_value = pd.concat([
        basics, basics.assign(ts_code="000002.SZ")], ignore_index=True,
    )
    frame = value.get_full_market_trade_status_df("20260922")
    assert frame is not None and frame.ts_code.tolist() == ["000001.SZ"]
    assert frame.attrs["ambiguous_codes"] == ["000002.SZ"]


def test_bse_code_is_not_in_stock_universe_before_exchange_open():
    value = provider(pd.DataFrame(columns=["ts_code", "trade_date", "suspend_type"]))
    basics = value.get_stock_basic_df.return_value
    value.get_stock_basic_df.return_value = pd.concat([
        basics, basics.assign(ts_code="920000.BJ", list_date="2020-12-23"),
    ], ignore_index=True)
    for source in (value.api.stk_limit, value.api.daily, value.api.stock_st):
        if "ts_code" in source.return_value:
            source.return_value["trade_date"] = "20210104"
    value.api.suspend_d.return_value["trade_date"] = pd.Series(dtype=str)
    frame = value.get_full_market_trade_status_df("20210104")
    assert frame is not None and frame.ts_code.tolist() == ["000001.SZ"]


def test_pre_bse_legacy_suspend_event_cannot_discard_sh_sz_status_day():
    value = provider(pd.DataFrame({
        "ts_code": ["833994.BJ"], "trade_date": ["20181206"],
        "suspend_type": ["S"], "suspend_timing": [None],
    }))
    value.api.stk_limit.return_value["trade_date"] = "20181206"
    value.api.daily.return_value["trade_date"] = "20181206"
    value.api.stock_st.return_value["trade_date"] = "20181206"
    value.get_bse_mapping_df = MagicMock(return_value=None)
    frame = value.get_full_market_trade_status_df("20181206")
    assert frame is not None and frame.ts_code.tolist() == ["000001.SZ"]
    assert not bool(frame.iloc[0]["is_suspended"])
    value.get_bse_mapping_df.assert_not_called()


def test_bse_old_suspension_and_st_codes_resolve_to_current_identity():
    value = provider(pd.DataFrame({
        "ts_code": ["839680.BJ"], "trade_date": ["20250616"],
        "suspend_type": ["S"], "suspend_timing": [None],
    }))
    basics = value.get_stock_basic_df.return_value
    value.get_stock_basic_df.return_value = pd.concat([
        basics, basics.assign(ts_code="920680.BJ", list_date="2020-01-01"),
    ], ignore_index=True)
    value.api.stk_limit.return_value = pd.concat([
        value.api.stk_limit.return_value.assign(trade_date="20250616"),
        value.api.stk_limit.return_value.assign(ts_code="920680.BJ", trade_date="20250616"),
    ], ignore_index=True)
    value.api.daily.return_value["trade_date"] = "20250616"
    value.api.stock_st.return_value = pd.DataFrame({
        "ts_code": ["839680.BJ"], "trade_date": ["20250616"], "type": ["ST"],
    })
    value.get_bse_mapping_df = MagicMock(return_value=pd.DataFrame({
        "o_code": ["839680.BJ"], "n_code": ["920680.BJ"],
    }))
    frame = value.get_full_market_trade_status_df("20250616")
    assert frame is not None and frame.ts_code.tolist() == ["000001.SZ", "920680.BJ"]
    bse = frame.set_index("ts_code").loc["920680.BJ"]
    assert bool(bse["is_suspended"]) and bse["suspension_scope"] == "full_day"
    assert bool(bse["is_st"])


def test_bse_old_code_without_verified_mapping_is_unknown():
    value = provider(pd.DataFrame({
        "ts_code": ["839680.BJ"], "trade_date": ["20260922"],
        "suspend_type": ["S"],
    }))
    value.get_bse_mapping_df = MagicMock(return_value=None)
    assert value.get_full_market_trade_status_df("20260922") is None


def test_unrelated_legacy_bse_limit_row_does_not_require_identity_mapping():
    value = provider(pd.DataFrame(columns=["ts_code", "trade_date", "suspend_type"]))
    value.api.stk_limit.return_value = pd.concat([
        value.api.stk_limit.return_value,
        value.api.stk_limit.return_value.assign(ts_code="900901.BJ"),
    ], ignore_index=True)
    value.get_bse_mapping_df = MagicMock(return_value=None)
    frame = value.get_full_market_trade_status_df("20260922")
    assert frame is not None and frame.ts_code.tolist() == ["000001.SZ"]
    value.get_bse_mapping_df.assert_not_called()


def test_bse_alias_collision_rejects_status_day():
    value = provider(pd.DataFrame(columns=["ts_code", "trade_date", "suspend_type"]))
    value.api.stock_st.return_value = pd.concat([
        value.api.stock_st.return_value,
        value.api.stock_st.return_value.assign(ts_code="839680.BJ"),
        value.api.stock_st.return_value.assign(ts_code="920680.BJ"),
    ], ignore_index=True)
    value.get_bse_mapping_df = MagicMock(return_value=pd.DataFrame({
        "o_code": ["839680.BJ"], "n_code": ["920680.BJ"],
    }))
    assert value.get_full_market_trade_status_df("20260922") is None


def test_full_day_halt_verification_does_not_depend_on_st_source():
    value = provider(pd.DataFrame({
        "ts_code": ["601611.SH"], "trade_date": ["20160630"],
        "suspend_type": ["S"], "suspend_timing": [None],
    }))
    value.api.daily.return_value = pd.DataFrame(columns=["ts_code", "trade_date"])
    frame = value.get_verified_full_day_suspensions_df("20160630")
    assert frame.ts_code.tolist() == ["601611.SH"]
    value.api.suspend_d.return_value = pd.concat([
        value.api.suspend_d.return_value,
        value.api.suspend_d.return_value.assign(suspend_type="R"),
    ], ignore_index=True)
    assert value.get_verified_full_day_suspensions_df("20160630").empty


def test_pre_bse_legacy_event_cannot_discard_verified_sh_halt():
    value = provider(pd.DataFrame({
        "ts_code": ["601611.SH", "833994.BJ"],
        "trade_date": ["20180509", "20180509"],
        "suspend_type": ["S", "S"],
        "suspend_timing": [None, None],
    }))
    value.api.daily.return_value = pd.DataFrame(columns=["ts_code", "trade_date"])
    value.get_bse_mapping_df = MagicMock(return_value=None)
    frame = value.get_verified_full_day_suspensions_df("20180509")
    assert frame is not None and frame.ts_code.tolist() == ["601611.SH"]
    value.get_bse_mapping_df.assert_not_called()


def test_bse_opening_keeps_transferred_stock_without_920_alias():
    value = provider(pd.DataFrame({
        "ts_code": ["833994.BJ"], "trade_date": ["20211115"],
        "suspend_type": ["S"], "suspend_timing": [None],
    }))
    value.api.daily.return_value = pd.DataFrame({
        "ts_code": ["000001.SZ"], "trade_date": ["20211115"],
    })
    value.get_bse_mapping_df = MagicMock(return_value=pd.DataFrame({
        "o_code": ["839680.BJ"], "n_code": ["920680.BJ"],
    }))
    halts = value.get_verified_full_day_suspensions_df("20211115")
    assert halts is not None and halts.ts_code.tolist() == ["833994.BJ"]

    basics = value.get_stock_basic_df.return_value
    value.get_stock_basic_df.return_value = pd.concat([
        basics, basics.assign(ts_code="833994.BJ", name="翰博高新",
                              list_date="2020-07-27"),
    ], ignore_index=True)
    value.api.stk_limit.return_value["trade_date"] = "20211115"
    value.api.stock_st.return_value = pd.DataFrame({
        "ts_code": ["600001.SH"], "trade_date": ["20211115"],
        "type": ["ST"],
    })
    status = value.get_full_market_trade_status_df("20211115")
    assert status is not None
    bj = status.loc[status.ts_code.eq("833994.BJ")].iloc[0]
    assert bool(bj["is_suspended"]) and bj["suspension_scope"] == "full_day"


def test_unmapped_bj_event_outside_active_directory_stays_unknown():
    value = provider(pd.DataFrame({
        "ts_code": ["833994.BJ"], "trade_date": ["20211115"],
        "suspend_type": ["S"], "suspend_timing": [None],
    }))
    value.api.daily.return_value["trade_date"] = "20211115"
    value.api.stk_limit.return_value["trade_date"] = "20211115"
    value.api.stock_st.return_value["trade_date"] = "20211115"
    value.get_bse_mapping_df = MagicMock(return_value=pd.DataFrame({
        "o_code": ["839680.BJ"], "n_code": ["920680.BJ"],
    }))
    assert value.get_full_market_trade_status_df("20211115") is None


def test_historical_st_membership_overrides_current_name():
    value = provider(pd.DataFrame(columns=["ts_code", "trade_date", "suspend_type"]))
    value.get_stock_basic_df.return_value.loc[0, "name"] = "*ST平安"
    frame = value.get_full_market_trade_status_df("20260922")
    assert not bool(frame.iloc[0]["is_st"])
    value.api.stock_st.return_value.loc[0, "ts_code"] = "000001.SZ"
    frame = value.get_full_market_trade_status_df("20260922")
    assert bool(frame.iloc[0]["is_st"])


def test_historical_universe_includes_delisted_then_excludes_after_delisting():
    value = provider(pd.DataFrame(columns=["ts_code", "trade_date", "suspend_type"]))
    basics = value.get_stock_basic_df.return_value
    basics["delist_date"] = "2026-09-23"
    value.get_stock_basic_df.return_value = basics
    historical = value.get_full_market_trade_status_df("20260922")
    assert historical is not None and historical["ts_code"].tolist() == ["000001.SZ"]
    value.api.stk_limit.return_value["trade_date"] = "20260923"
    value.api.daily.return_value["trade_date"] = "20260923"
    value.api.stock_st.return_value["trade_date"] = "20260923"
    assert value.get_full_market_trade_status_df("20260923") is None
