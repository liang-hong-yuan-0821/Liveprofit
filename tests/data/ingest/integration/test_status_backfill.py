# test-catalog-begin
# {
#   "purpose": "数据采集 / status_backfill",
#   "keywords": [
#     "数据采集",
#     "批次",
#     "交易日历",
#     "来源证据",
#     "ST状态",
#     "状态",
#     "停牌",
#     "status_backfill",
#     "batch",
#     "calendar",
#     "ingest",
#     "source",
#     "st",
#     "status",
#     "suspension"
#   ],
#   "covers": [
#     "db/instrument/dao/quant_inputs.py",
#     "db/instrument/db.py",
#     "db/instrument/ingest/guard.py",
#     "db/instrument/ingest/status_backfill.py",
#     "db/instrument/ingest/stock_factors.py"
#   ],
#   "environment": [
#     "db"
#   ]
# }
# test-catalog-end

from datetime import date
from unittest.mock import MagicMock

import pytest

from db.instrument.ingest import status_backfill
from db.instrument.ingest.guard import IngestSessionLost
from db.instrument.db import get_connection


def test_status_backfill_uses_verified_calendar_and_rechecks_after_write(monkeypatch):
    days = [date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 23)]
    monkeypatch.setattr(status_backfill, "_year_days", lambda *args: days)
    pending = iter([[days[2], days[1]], [days[1]]])
    monkeypatch.setattr(status_backfill, "missing_status_days", lambda *args: next(pending))
    monkeypatch.setattr(status_backfill, "missing_status_codes", lambda *args: ["000001.SZ"])
    monkeypatch.setattr(status_backfill, "verified_st_observations", lambda *args: None)
    collect = MagicMock(return_value={"status": "SUCCESS", "rows": 10})
    monkeypatch.setattr(status_backfill, "collect_stock_status_day", collect)

    report = status_backfill.backfill_status(
        MagicMock(), MagicMock(), start=days[0], end=days[-1], max_days=1,
    )

    collect.assert_called_once()
    assert collect.call_args.args[2] == "20260923"
    assert collect.call_args.kwargs["active_codes"] == ["000001.SZ"]
    assert report["pending_before"] == 2
    assert report["pending_after"] == 1
    assert report["next_dates"] == ["2026-09-22"]


def test_lost_ingest_session_stops_without_reconnecting(monkeypatch):
    day = date(2026, 9, 23)
    monkeypatch.setattr(status_backfill, "_year_days", lambda *args: [day])
    monkeypatch.setattr(status_backfill, "missing_status_days", lambda *args: [day])
    monkeypatch.setattr(status_backfill, "missing_status_codes", lambda *args: ["000001.SZ"])
    monkeypatch.setattr(status_backfill, "verified_st_observations", lambda *args: None)
    monkeypatch.setattr(status_backfill, "collect_stock_status_day",
                        MagicMock(side_effect=IngestSessionLost("lost")))
    conn = MagicMock()

    with pytest.raises(IngestSessionLost):
        status_backfill.backfill_status(conn, MagicMock(), start=day, end=day)
    conn.rollback.assert_not_called()


def test_repeated_unavailable_source_stops_bounded_batch(monkeypatch):
    days = [date(2026, 9, day) for day in range(1, 9)]
    monkeypatch.setattr(status_backfill, "_year_days", lambda *args: days)
    monkeypatch.setattr(status_backfill, "missing_status_days", lambda *args: days)
    monkeypatch.setattr(status_backfill, "missing_status_codes", lambda *args: ["000001.SZ"])
    monkeypatch.setattr(status_backfill, "verified_st_observations", lambda *args: None)
    collect = MagicMock(return_value={"status": "UNAVAILABLE", "rows": 0})
    monkeypatch.setattr(status_backfill, "collect_stock_status_day", collect)
    report = status_backfill.backfill_status(MagicMock(), MagicMock(), start=days[0], end=days[-1])
    assert collect.call_count == 5
    assert report["pending_after"] == 8


def test_partially_covered_day_cannot_starve_untouched_days(clean_market_state):
    with get_connection() as conn:
        assert conn.info.dbname == "liveprofit_instrument_test"
        conn.execute(
            "INSERT INTO market.instrument (ts_code,name,instrument_type,list_date) VALUES "
            "('000001.SZ','a','stock','2020-01-01'),"
            "('000002.SZ','b','stock','2020-01-01')"
        )
        conn.execute(
            "INSERT INTO market.trade_status_daily "
            "(ts_code,trade_date,is_suspended,suspension_scope,is_st,market_board,source) "
            "VALUES ('000001.SZ','2020-01-03',FALSE,'none',FALSE,'MAIN','tushare')"
        )
        pending = status_backfill.missing_status_days(
            conn, [date(2020, 1, 2), date(2020, 1, 3)],
        )
        assert pending == [date(2020, 1, 2), date(2020, 1, 3)]
        assert status_backfill.missing_status_codes(conn, date(2020, 1, 3)) == [
            "000002.SZ",
        ]


def test_verified_st_source_requires_complete_active_day(clean_market_state):
    from uuid import uuid4

    batch_id = uuid4()
    day = date(2016, 1, 4)
    with get_connection() as conn:
        assert conn.info.dbname == "liveprofit_instrument_test"
        conn.execute(
            "INSERT INTO market.instrument (ts_code,name,instrument_type,list_date) VALUES "
            "('000001.SZ','a','stock','2010-01-01'),"
            "('000002.SZ','b','stock','2010-01-01')"
        )
        conn.execute(
            "INSERT INTO market.stock_st_source_batch "
            "(id,source,source_year,observed_at,raw_sha256,expected_days,observed_days,status) "
            "VALUES (%s,'baostock_kline',2016,now(),%s,2,2,'VERIFIED')",
            (batch_id, "0" * 64),
        )
        conn.execute(
            "INSERT INTO market.stock_st_source_observation "
            "(batch_id,ts_code,trade_date,is_st,reported_trading) VALUES "
            "(%s,'000001.SZ',%s,TRUE,TRUE)", (batch_id, day),
        )
        assert status_backfill.verified_st_observations(conn, day) is None
        conn.execute(
            "INSERT INTO market.stock_st_source_observation "
            "(batch_id,ts_code,trade_date,is_st,reported_trading) VALUES "
            "(%s,'000002.SZ',%s,FALSE,TRUE)", (batch_id, day),
        )
        frame = status_backfill.verified_st_observations(conn, day)
        assert frame is not None
        assert dict(zip(frame["ts_code"], frame["is_st"])) == {
            "000001.SZ": True, "000002.SZ": False,
        }


def test_composite_source_closes_status_gap_and_is_trusted_for_suspension(clean_market_state):
    from db.instrument.dao.quant_inputs import read_stock_quant_facts

    day = date(2016, 1, 4)
    with get_connection() as conn:
        assert conn.info.dbname == "liveprofit_instrument_test"
        conn.execute(
            "INSERT INTO market.instrument (ts_code,name,instrument_type,list_date) "
            "VALUES ('000001.SZ','a','stock','2010-01-01')"
        )
        conn.execute(
            "INSERT INTO market.trade_status_daily "
            "(ts_code,trade_date,is_suspended,suspension_scope,is_st,market_board,source) "
            "VALUES ('000001.SZ',%s,TRUE,'full_day',FALSE,'MAIN','tushare+baostock')",
            (day,),
        )
        assert status_backfill.missing_status_days(conn, [day]) == []
        facts = read_stock_quant_facts(conn, ["000001.SZ"], day, ["atr_qfq"])
        assert facts["status_valid"] == {"000001.SZ"}
        assert facts["suspended"] == {"000001.SZ"}


def test_scoped_trading_source_keeps_mixed_provenance_on_write(clean_market_state):
    import pandas as pd
    from db.instrument.ingest.stock_factors import collect_stock_status_day
    provider = MagicMock()
    status = pd.DataFrame({
        "ts_code": ["000001.SZ"], "trade_date": ["2022-01-04"],
        "is_suspended": [True], "suspension_scope": ["full_day"],
        "is_st": [True], "market_board": ["MAIN"],
    })
    status.attrs.update(st_source="tushare", uses_independent_trading=True)
    provider.get_full_market_trade_status_df.return_value = status
    observed = pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["2022-01-04"],
                             "reported_trading": [False]})
    with get_connection() as conn:
        assert conn.info.dbname == "liveprofit_instrument_test"
        result = collect_stock_status_day(conn, provider, "20220104",
                                          active_codes=["000001.SZ"], no_trade_observations=observed)
        assert result["status"] == "SUCCESS" and result["source"] == "tushare+baostock"
        assert conn.execute("SELECT source FROM market.trade_status_daily").fetchone() == ("tushare+baostock",)
        assert provider.get_full_market_trade_status_df.call_args.kwargs["no_trade_observations"] is observed
