# test-catalog-begin
# {
#   "purpose": "量化研究 / coverage_audit（完整性）",
#   "keywords": [
#     "量化研究",
#     "交易日历",
#     "数据完整性",
#     "ETF",
#     "复权因子",
#     "个股分析",
#     "停牌",
#     "coverage_audit",
#     "calendar",
#     "coverage",
#     "etf",
#     "factor",
#     "stock",
#     "suspension"
#   ],
#   "covers": [
#     "backend/modules/quant_research/application/coverage_audit.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

from datetime import date

import pandas as pd
import pytest

from backend.modules.quant_research.application.coverage_audit import (
    audit_historical_coverage,
)

DAYS = (date(2020, 1, 2), date(2020, 1, 3), date(2020, 1, 6))


def _tables():
    codes = ("000001.SZ", "000002.SZ")
    keys = [(code, day) for code in codes for day in DAYS]
    return {
        "instrument": pd.DataFrame([
            {"ts_code": "000001.SZ", "instrument_type": "stock",
             "list_date": date(1991, 1, 1), "delist_date": None},
            {"ts_code": "000002.SZ", "instrument_type": "stock",
             "list_date": date(1991, 1, 1), "delist_date": date(2020, 1, 6)},
        ]),
        "daily": pd.DataFrame([
            {"ts_code": code, "trade_date": day, "open": 10., "high": 11.,
             "low": 9., "close": 10.} for code, day in keys
        ]),
        "adj_factor": pd.DataFrame([
            {"ts_code": code, "trade_date": day, "adj_factor": 1.}
            for code, day in keys
        ]),
        "factor": pd.DataFrame([
            {"ts_code": code, "trade_date": day, "ma_qfq_5": 10.,
             "ma_qfq_20": 10., "ma_qfq_60": 10., "atr_qfq": 1.,
             "ma_bfq_5": 10., "ma_bfq_20": 10., "ma_bfq_60": 10.,
             "ma_bfq_250": 10., "atr_bfq": 1.} for code, day in keys
        ]),
        "trade_status": pd.DataFrame([
            {"ts_code": code, "trade_date": day, "is_st": False,
             "is_suspended": False, "suspension_scope": "none",
             "market_board": "MAIN", "source": "tushare"}
            for code, day in keys
        ]),
    }


def test_coverage_uses_listing_interval_and_independent_calendar():
    tables = _tables()
    audit = audit_historical_coverage(trading_days=DAYS, tables=tables)
    assert audit.expected_symbol_days == 5
    assert audit.complete
    tables["daily"] = tables["daily"].loc[
        ~((tables["daily"]["ts_code"] == "000001.SZ")
          & (tables["daily"]["trade_date"] == DAYS[-1]))
    ]
    audit = audit_historical_coverage(trading_days=DAYS, tables=tables)
    assert audit.missing_counts["daily"] == 1
    assert audit.sample_gaps[0].trade_date == DAYS[-1]


def test_stock_benchmark_coverage_uses_the_same_verified_calendar():
    tables = _tables()
    tables["benchmark_daily"] = pd.DataFrame([
        {"ts_code": "000300.SH", "trade_date": day, "close": 4000.}
        for day in DAYS
    ])
    complete = audit_historical_coverage(trading_days=DAYS, tables=tables)
    assert complete.complete and complete.missing_counts["benchmark_daily"] == 0
    tables["benchmark_daily"]["close"] = tables["benchmark_daily"]["close"].astype(object)
    tables["benchmark_daily"].loc[0, "close"] = True
    assert audit_historical_coverage(trading_days=DAYS, tables=tables).missing_counts["benchmark_daily"] == 1
    tables["benchmark_daily"].loc[0, "close"] = 4000.
    tables["benchmark_daily"] = tables["benchmark_daily"].iloc[:-1].copy()
    missing = audit_historical_coverage(trading_days=DAYS, tables=tables)
    assert not missing.complete and missing.missing_counts["benchmark_daily"] == 1
    assert missing.sample_gaps[0].ts_code == "000300.SH"
    tables["benchmark_daily"].loc[0, "close"] = -1
    assert audit_historical_coverage(trading_days=DAYS, tables=tables).missing_counts["benchmark_daily"] == 2
    tables["benchmark_daily"] = pd.concat([tables["benchmark_daily"], pd.DataFrame([
        {"ts_code": "000300.SH", "trade_date": date(2020, 1, 4), "close": 4000.},
    ])])
    with pytest.raises(ValueError, match="exchange date invalid"):
        audit_historical_coverage(trading_days=DAYS, tables=tables)


def test_trusted_stock_suspension_excuses_missing_bar_and_derived_values():
    tables = _tables()
    key = ("000001.SZ", DAYS[1])
    tables["daily"] = tables["daily"].loc[
        ~((tables["daily"]["ts_code"] == key[0])
          & (tables["daily"]["trade_date"] == key[1]))
    ]
    for name in ("adj_factor", "factor"):
        tables[name] = tables[name].loc[
            ~((tables[name]["ts_code"] == key[0])
              & (tables[name]["trade_date"] == key[1]))
        ]
    mask = (tables["trade_status"]["ts_code"] == key[0]) & (
        tables["trade_status"]["trade_date"] == key[1])
    tables["trade_status"].loc[mask, "is_suspended"] = True
    tables["trade_status"].loc[mask, "suspension_scope"] = "full_day"
    complete = audit_historical_coverage(trading_days=DAYS, tables=tables)
    assert complete.complete
    assert complete.not_applicable_counts["trusted_suspension"] == 1
    tables["trade_status"].loc[mask, "source"] = "tushare+baostock"
    assert audit_historical_coverage(trading_days=DAYS, tables=tables).complete
    tables["trade_status"].loc[mask, "suspension_scope"] = None
    unknown = audit_historical_coverage(trading_days=DAYS, tables=tables)
    assert unknown.missing_counts["daily"] == 1
    assert unknown.missing_counts["stock_trade_status"] == 1
    tables["trade_status"].loc[mask, "suspension_scope"] = "full_day"
    tables["trade_status"].loc[mask, "source"] = "current_catalog"
    audit = audit_historical_coverage(trading_days=DAYS, tables=tables)
    assert audit.missing_counts["daily"] == 1
    assert audit.missing_counts["stock_trade_status"] == 1
    assert audit.missing_counts["adj_factor"] == 0
    assert audit.missing_counts["factor"] == 0


def test_intraday_suspension_with_bar_still_requires_derived_values():
    tables = _tables()
    key = ("000001.SZ", DAYS[1])
    mask = (tables["trade_status"]["ts_code"] == key[0]) & (
        tables["trade_status"]["trade_date"] == key[1])
    tables["trade_status"].loc[mask, "is_suspended"] = True
    tables["trade_status"].loc[mask, "suspension_scope"] = "intraday"
    for name in ("adj_factor", "factor"):
        tables[name] = tables[name].loc[
            ~((tables[name]["ts_code"] == key[0])
              & (tables[name]["trade_date"] == key[1]))
        ]
    audit = audit_historical_coverage(trading_days=DAYS, tables=tables)
    assert audit.not_applicable_counts["trusted_suspension"] == 0
    assert audit.missing_counts["adj_factor"] == 1
    assert audit.missing_counts["factor"] == 1


def test_official_full_day_evidence_exempts_only_missing_bar():
    tables = _tables()
    key = ("000001.SZ", DAYS[1])
    tables["daily"] = tables["daily"].loc[
        ~((tables["daily"]["ts_code"] == key[0])
          & (tables["daily"]["trade_date"] == key[1]))
    ]
    tables["trade_status"] = tables["trade_status"].loc[
        ~((tables["trade_status"]["ts_code"] == key[0])
          & (tables["trade_status"]["trade_date"] == key[1]))
    ]
    tables["suspension_evidence"] = pd.DataFrame([
        {"ts_code": key[0], "trade_date": key[1], "scope": "full_day"},
    ])
    audit = audit_historical_coverage(trading_days=DAYS, tables=tables)
    assert audit.missing_counts["daily"] == 0
    assert audit.not_applicable_counts["trusted_suspension"] == 1
    assert audit.missing_counts["stock_trade_status"] == 1


def test_official_evidence_conflicting_with_bar_is_rejected():
    tables = _tables()
    tables["suspension_evidence"] = pd.DataFrame([
        {"ts_code": "000001.SZ", "trade_date": DAYS[1], "scope": "full_day"},
    ])
    with pytest.raises(ValueError, match="conflicts with daily bar"):
        audit_historical_coverage(trading_days=DAYS, tables=tables)


def test_current_bse_code_cannot_expand_pre_opening_stock_universe():
    tables = _tables()
    tables["instrument"] = pd.concat([tables["instrument"], pd.DataFrame([{
        "ts_code": "920000.BJ", "instrument_type": "stock",
        "list_date": date(2019, 1, 1), "delist_date": None,
    }])], ignore_index=True)
    audit = audit_historical_coverage(trading_days=DAYS, tables=tables)
    assert audit.expected_symbol_days == 5
    assert audit.complete


def test_factor_warmup_exemption_requires_calendar_from_listing():
    tables = _tables()
    tables["instrument"].loc[0, "list_date"] = DAYS[0]
    assert audit_historical_coverage(
        trading_days=DAYS, tables=tables,
    ).not_applicable_counts["factor_warmup"] == 0
    tables["factor"] = tables["factor"].loc[
        tables["factor"]["ts_code"] != "000001.SZ"
    ]
    audit = audit_historical_coverage(trading_days=DAYS, tables=tables)
    assert audit.complete
    assert audit.not_applicable_counts["factor_warmup"] == 3

    # If the supplied calendar starts years after listing, absent factor rows
    # cannot be called warmup without earlier verified sessions.
    tables["instrument"].loc[0, "list_date"] = date(1991, 1, 1)
    audit = audit_historical_coverage(trading_days=DAYS, tables=tables)
    assert audit.missing_counts["factor"] == 3


def test_etf_requires_its_own_trade_rules_and_full_factor_fields():
    tables = _tables()
    for frame in tables.values():
        frame["ts_code"] = frame["ts_code"].replace("000001.SZ", "510300.SH")
    tables["instrument"].loc[0, "instrument_type"] = "fund"
    tables["factor"].loc[0, "ma_bfq_250"] = None
    audit = audit_historical_coverage(trading_days=DAYS, tables=tables, sample_limit=2)
    assert audit.missing_counts["fund_trade_rules"] == 3
    assert audit.missing_counts["factor"] == 1
    assert len(audit.sample_gaps) == 2 and audit.sample_truncated


def test_bad_identity_or_calendar_rejected():
    tables = _tables()
    with pytest.raises(ValueError, match="calendar"):
        audit_historical_coverage(trading_days=(DAYS[0], DAYS[0]), tables=tables)
    tables["factor"] = pd.concat([tables["factor"], tables["factor"].iloc[[0]]])
    with pytest.raises(ValueError, match="duplicate historical"):
        audit_historical_coverage(trading_days=DAYS, tables=tables)
