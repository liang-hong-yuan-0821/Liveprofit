# test-catalog-begin
# {
#   "purpose": "数据采集 / fund_factors：Fund factors must cover and match locally observed raw ETF bars.",
#   "keywords": [
#     "数据采集",
#     "K线",
#     "每日",
#     "历史审计",
#     "来源观测",
#     "公共组件",
#     "来源证据",
#     "fund_factors",
#     "bars",
#     "daily",
#     "history",
#     "observation",
#     "shared",
#     "source"
#   ],
#   "covers": [
#     "db/instrument/ingest/fund_factor_day_backfill.py",
#     "db/instrument/ingest/fund_factors.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

"""Fund factors must cover and match locally observed raw ETF bars."""

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from db.instrument.ingest.fund_factors import (
    _collect_fund_factor_day_unlocked,
    _collect_fund_factors_unlocked,
)

CODE = "510300.SH"


def _factor(dates=("2026-09-21", "2026-09-22"), closes=(4.1, 4.2)):
    return pd.DataFrame({
        "ts_code": [CODE] * len(dates), "trade_date": list(dates),
        "close": list(closes), "ma_bfq_5": [4.0] * len(dates),
        "ma_bfq_20": [3.9] * len(dates), "ma_bfq_60": [3.8] * len(dates),
        "ma_bfq_250": [3.7] * len(dates), "atr_bfq": [0.1] * len(dates),
    })


def _inputs(frame=None):
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = [
        (pd.Timestamp("2026-09-21").date(), 4.1),
        (pd.Timestamp("2026-09-22").date(), 4.2),
    ]
    provider = MagicMock()
    provider.get_fund_factor_df.return_value = _factor() if frame is None else frame
    return conn, provider


def test_complete_same_basis_bars_publish_once():
    conn, provider = _inputs()
    with patch("db.instrument.ingest.fund_factors.upsert_observed_bfq_factors", return_value=2) as write:
        result = _collect_fund_factors_unlocked(
            conn, provider, CODE, "2026-09-21", "2026-09-22",
        )
    assert result["status"] == "SUCCESS"
    assert result["bar_rows"] == 2
    assert write.call_args.args[1]["trade_date"].tolist() == ["2026-09-21", "2026-09-22"]
    conn.commit.assert_called_once()


@pytest.mark.parametrize("frame,reason", [
    (_factor(dates=("2026-09-21",), closes=(4.1,)), "FUND_FACTOR_BAR_COVERAGE_INCOMPLETE"),
    (_factor(closes=(4.1, 4.3)), "FUND_FACTOR_PRICE_BASIS_MISMATCH"),
    (_factor(dates=("2026-09-21", "2026-09-21")), "FUND_FACTOR_DUPLICATE_KEYS"),
    (_factor(dates=("2026-09-21", "2026-09-23")), "FUND_FACTOR_DATE_MISMATCH"),
])
def test_invalid_response_never_writes(frame, reason):
    conn, provider = _inputs(frame)
    with (patch("db.instrument.ingest.fund_factors.upsert_observed_bfq_factors") as write,
          pytest.raises(ValueError, match=reason)):
        _collect_fund_factors_unlocked(conn, provider, CODE, "2026-09-21", "2026-09-22")
    write.assert_not_called()
    conn.commit.assert_not_called()


def test_failed_write_rolls_back():
    conn, provider = _inputs()
    with (patch("db.instrument.ingest.fund_factors.upsert_observed_bfq_factors", side_effect=ValueError("COPY")),
          pytest.raises(ValueError, match="COPY")):
        _collect_fund_factors_unlocked(conn, provider, CODE, "2026-09-21", "2026-09-22")
    conn.rollback.assert_called_once()
    conn.commit.assert_not_called()


def test_symbol_backfill_reports_unverified_indicator_after_writing_observation():
    conn, provider = _inputs(_factor().assign(atr_bfq=None))
    from datetime import date
    conn.execute.return_value.fetchall.side_effect = [
        [(date(2026, 9, 21), 4.1), (date(2026, 9, 22), 4.2)],
        [(CODE, date(2012, 5, 28))], [(CODE, date(2012, 5, 28))],
    ]
    provider.get_fund_daily_df.return_value = None
    with patch("db.instrument.ingest.fund_factors.upsert_observed_bfq_factors", return_value=2):
        result = _collect_fund_factors_unlocked(conn, provider, CODE, "2026-09-21", "2026-09-22")
    assert result["status"] == "PARTIAL"
    assert result["incomplete_indicator_days"] == ["2026-09-21", "2026-09-22"]


def test_day_backfill_requeues_existing_rows_with_unverified_indicators():
    from datetime import date
    from db.instrument.ingest.fund_factor_day_backfill import _pending_days
    day = date(2026, 9, 21)
    conn = MagicMock()
    conn.execute.return_value.fetchall.side_effect = [
        [(day, CODE, True, 4.0, 3.9, 3.8, 3.7, None)],
        [(CODE, date(2012, 5, 28))],
    ]
    assert _pending_days(conn, day, day) == [day]
    conn.execute.return_value.fetchall.side_effect = [
        [(day, CODE, True, 4.0, 3.9, 3.8, None, 0.1)],
        [(CODE, date(2026, 8, 1))],
    ]
    assert _pending_days(conn, day, day) == []


def test_history_verification_budget_is_shared_and_cached():
    from db.instrument.ingest.fund_factor_day_backfill import _WarmupRequestBudget
    provider = MagicMock()
    provider.get_fund_daily_df.return_value = _factor()
    bounded = _WarmupRequestBudget(provider, 1)
    first = bounded.get_fund_daily_df(CODE, "2012-05-28", "2026-09-21")
    assert first is bounded.get_fund_daily_df(CODE, "2012-05-28", "2026-09-21")
    assert bounded.get_fund_daily_df(CODE, "2012-05-28", "2026-09-22") is None
    provider.get_fund_daily_df.assert_called_once()
    assert bounded.requests == 1


def _day_inputs(frame=None):
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = [
        (CODE, pd.Timestamp("2026-09-21").date(), 4.1),
        ("518880.SH", pd.Timestamp("2026-09-21").date(), 10.2),
    ]
    provider = MagicMock()
    provider.get_full_market_fund_factor_df.return_value = (
        _factor(dates=("2026-09-21",), closes=(4.1,)) if frame is None else frame
    )
    return conn, provider


def test_daily_partial_result_persists_only_observed_rows_and_reports_missing():
    conn, provider = _day_inputs()
    with patch("db.instrument.ingest.fund_factors.upsert_observed_bfq_factors", return_value=1) as write:
        result = _collect_fund_factor_day_unlocked(conn, provider, "2026-09-21")
    assert result["status"] == "PARTIAL"
    assert result["coverage"] == 0.5
    assert result["missing_codes"] == ["518880.SH"]
    assert write.call_args.args[1]["ts_code"].tolist() == [CODE]
    conn.commit.assert_called_once()


def test_daily_wrong_price_rejects_whole_day():
    conn, provider = _day_inputs(_factor(dates=("2026-09-21",), closes=(4.3,)))
    with (patch("db.instrument.ingest.fund_factors.upsert_observed_bfq_factors") as write,
          pytest.raises(ValueError, match="PRICE_BASIS_MISMATCH")):
        _collect_fund_factor_day_unlocked(conn, provider, "2026-09-21")
    write.assert_not_called()
    conn.commit.assert_not_called()


def test_daily_warmup_indicator_is_reported_without_filling_future_value():
    frame = _factor(dates=("2026-09-21",), closes=(4.1,))
    frame.loc[0, "ma_bfq_250"] = None
    conn, provider = _day_inputs(frame)
    day_bars = conn.execute.return_value.fetchall.return_value
    conn.execute.return_value.fetchall.side_effect = [
        day_bars, [(CODE, pd.Timestamp("2026-09-01").date())],
    ]
    with patch("db.instrument.ingest.fund_factors.upsert_observed_bfq_factors", return_value=1):
        result = _collect_fund_factor_day_unlocked(conn, provider, "2026-09-21")
    assert result["status"] == "PARTIAL"
    assert result["incomplete_indicator_codes"] == []
    assert result["warmup_indicator_codes"] == [CODE]
    assert result["missing_codes"] == ["518880.SH"]


def test_daily_only_proven_warmup_can_be_successful():
    frame = _factor(dates=("2026-09-21",), closes=(4.1,))
    frame.loc[0, "atr_bfq"] = None
    frame.loc[0, "ma_bfq_250"] = None
    conn, provider = _day_inputs(frame)
    conn.execute.return_value.fetchall.side_effect = [
        [(CODE, pd.Timestamp("2026-09-21").date(), 4.1)],
        [(CODE, pd.Timestamp("2026-09-01").date())],
    ]
    with patch("db.instrument.ingest.fund_factors.upsert_observed_bfq_factors", return_value=1):
        result = _collect_fund_factor_day_unlocked(conn, provider, "2026-09-21")
    assert result["status"] == "SUCCESS"
    assert result["warmup_indicator_codes"] == [CODE]
    assert result["incomplete_indicator_codes"] == []


def test_daily_atr_null_after_warmup_remains_partial():
    frame = _factor(dates=("2026-09-21",), closes=(4.1,))
    frame.loc[0, "atr_bfq"] = None
    conn, provider = _day_inputs(frame)
    conn.execute.return_value.fetchall.side_effect = [
        [(CODE, pd.Timestamp("2026-09-21").date(), 4.1)],
        [(CODE, pd.Timestamp("2026-08-01").date())],
    ]
    with patch("db.instrument.ingest.fund_factors.upsert_observed_bfq_factors", return_value=1):
        result = _collect_fund_factor_day_unlocked(conn, provider, "2026-09-21")
    assert result["status"] == "PARTIAL"
    assert result["incomplete_indicator_codes"] == [CODE]


@pytest.mark.parametrize("local_complete", [True, False])
def test_source_history_warmup_requires_all_local_dates_and_prices(local_complete):
    frame = _factor(dates=("2026-09-21",), closes=(4.1,))
    frame.loc[0, "ma_bfq_250"] = None
    conn, provider = _day_inputs(frame)
    first, last = pd.Timestamp("2026-09-18").date(), pd.Timestamp("2026-09-21").date()
    history = [(first, 4.0), (last, 4.1)]
    conn.execute.return_value.fetchall.side_effect = [
        [(CODE, last, 4.1)], [(CODE, pd.Timestamp("2024-01-01").date())],
        history if local_complete else history[1:],
    ]
    provider.get_fund_daily_df.return_value = pd.DataFrame({
        "ts_code": [CODE, CODE], "trade_date": [first.isoformat(), last.isoformat()],
        "close": [4.0, 4.1],
    })
    with patch("db.instrument.ingest.fund_factors.upsert_observed_bfq_factors", return_value=1):
        result = _collect_fund_factor_day_unlocked(conn, provider, "2026-09-21")
    assert result["status"] == ("SUCCESS" if local_complete else "PARTIAL")
    assert result["source_history_warmup_codes"] == ([CODE] if local_complete else [])
