# test-catalog-begin
# {
#   "purpose": "数据采集 / stock_st_source_db（数据来源）：Verified BaoStock facts are immutable and require full session coverage.",
#   "keywords": [
#     "数据采集",
#     "数据库",
#     "来源证据",
#     "ST状态",
#     "个股分析",
#     "stock_st_source_db",
#     "db",
#     "source",
#     "st",
#     "stock"
#   ],
#   "covers": [
#     "backend/modules/quant_research/infrastructure/source_reader.py",
#     "db/instrument/dao/quant_inputs.py",
#     "db/instrument/dao/trade_status.py",
#     "db/instrument/db.py",
#     "db/instrument/ingest/status_backfill.py",
#     "db/instrument/ingest/stock_st_source.py"
#   ],
#   "environment": [
#     "db"
#   ]
# }
# test-catalog-end

"""Verified BaoStock facts are immutable and require full session coverage."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import exchange_calendars as xc
import pandas as pd
import pytest

from db.instrument.db import get_connection
from db.instrument.ingest.stock_st_source import import_stock_st_source
from db.instrument.ingest.status_backfill import verified_st_observations


def _source_files(tmp_path, *, omit_last: bool = False):
    days = [session.date() for session in xc.get_calendar(
        "XSHG", start="2016-01-01", end="2016-12-31",
    ).sessions]
    observations = days[:-1] if omit_last else days
    raw = tmp_path / "baostock.parquet"
    pd.DataFrame({
        "ts_code": ["000001.SZ"] * len(observations),
        "trade_date": observations,
        "trading": [True] * len(observations),
        "is_st": [False] * len(observations),
        "close": ["10.0000"] * len(observations),
    }).to_parquet(raw, index=False)
    report = tmp_path / "report.json"
    report.write_text(json.dumps({
        "year": 2016,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "baostock.query_history_k_data_plus.daily.unadjusted",
        "expected_stock_days": len(days),
        "observed_stock_days": len(observations),
        "failed_codes": [],
        "missing_count": 0 if not omit_last else 1,
        "st_conflict_count": 0,
        "raw_parquet_sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
    }), encoding="utf-8")
    return report, raw, len(days)


@pytest.mark.usefixtures("clean_market_state")
def test_complete_st_source_import_is_verified_and_idempotent(tmp_path):
    report, raw, expected = _source_files(tmp_path)
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO market.instrument "
            "(ts_code,name,instrument_type,list_status,list_date,data_source) "
            "VALUES ('000001.SZ','sample','stock','L','1991-04-03','test')"
        )
        conn.commit()
        first = import_stock_st_source(conn, report, raw)
        second = import_stock_st_source(conn, report, raw)
        batches = conn.execute(
            "SELECT status,observed_days FROM market.stock_st_source_batch"
        ).fetchall()
        observed = conn.execute(
            "SELECT count(*) FROM market.stock_st_source_observation"
        ).fetchone()[0]
    assert first["status"] == "VERIFIED" and first["rows"] == expected
    assert second["already_present"] is True
    assert batches == [("VERIFIED", expected)]
    assert observed == expected


@pytest.mark.usefixtures("clean_market_state")
def test_st_source_rejects_missing_session_before_writing(tmp_path):
    report, raw, _ = _source_files(tmp_path, omit_last=True)
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO market.instrument "
            "(ts_code,name,instrument_type,list_status,list_date,data_source) "
            "VALUES ('000001.SZ','sample','stock','L','1991-04-03','test')"
        )
        conn.commit()
        with pytest.raises(ValueError, match="ST_SOURCE_REPORT_NOT_COMPLETE"):
            import_stock_st_source(conn, report, raw)
        assert conn.execute("SELECT count(*) FROM market.stock_st_source_batch").fetchone()[0] == 0


@pytest.mark.usefixtures("clean_market_state")
@pytest.mark.parametrize("column,value", [("is_st", True), ("trading", False)])
def test_new_verified_batch_cannot_silently_revise_historical_st(tmp_path, column, value):
    report, raw, _ = _source_files(tmp_path)
    with get_connection() as conn:
        assert conn.info.dbname == "liveprofit_instrument_test"
        conn.execute(
            "INSERT INTO market.instrument "
            "(ts_code,name,instrument_type,list_status,list_date,data_source) "
            "VALUES ('000001.SZ','sample','stock','L','1991-04-03','test')"
        )
        conn.commit()
        import_stock_st_source(conn, report, raw)
        revised = pd.read_parquet(raw)
        revised.loc[0, column] = value
        revised_raw = tmp_path / "revised.parquet"
        revised.to_parquet(revised_raw, index=False)
        revised_report = json.loads(report.read_text(encoding="utf-8"))
        revised_report["raw_parquet_sha256"] = hashlib.sha256(revised_raw.read_bytes()).hexdigest()
        revised_path = tmp_path / "revised.json"
        revised_path.write_text(json.dumps(revised_report), encoding="utf-8")
        with pytest.raises(ValueError, match="ST_SOURCE_POST_WRITE_MISMATCH"):
            import_stock_st_source(conn, revised_path, revised_raw)
        assert conn.execute(
            "SELECT count(*) FROM market.stock_st_source_batch WHERE status='VERIFIED'"
        ).fetchone()[0] == 1


@pytest.mark.usefixtures("clean_market_state")
def test_independent_st_source_quarantines_reported_vendor_conflict(tmp_path):
    report, raw, _ = _source_files(tmp_path)
    documented = json.loads(report.read_text(encoding="utf-8"))
    documented["st_conflict_count"] = 1
    documented["st_conflict_examples"] = [["000001.SZ", "2016-01-04", True, False]]
    report.write_text(json.dumps(documented), encoding="utf-8")
    with get_connection() as conn:
        assert conn.info.dbname == "liveprofit_instrument_test"
        conn.execute(
            "INSERT INTO market.instrument "
            "(ts_code,name,instrument_type,list_status,list_date,data_source) "
            "VALUES ('000001.SZ','sample','stock','L','1991-04-03','test')"
        )
        conn.execute(
            "INSERT INTO market.trade_status_daily "
            "(ts_code,trade_date,is_suspended,suspension_scope,is_st,market_board,source) "
            "VALUES ('000001.SZ','2016-01-04',FALSE,'none',TRUE,'MAIN','tushare')"
        )
        conn.commit()
        result = import_stock_st_source(conn, report, raw)
        assert result["status"] == "VERIFIED"
        assert result["quarantined_conflicts"] == 1
        assert conn.execute(
            "SELECT incumbent_is_st,vendor_is_st FROM market.stock_st_source_conflict"
        ).fetchall() == [(True, False)]
        conflicted = verified_st_observations(conn, pd.Timestamp("2016-01-04").date())
        assert conflicted is not None and conflicted["st_conflict"].tolist() == [True]
        clear = verified_st_observations(conn, pd.Timestamp("2016-01-05").date())
        assert clear is not None and clear["st_conflict"].tolist() == [False]
        from db.instrument.ingest.status_backfill import missing_status_codes
        from db.instrument.dao.trade_status import remove_ambiguous_tushare_status
        assert conn.execute("SELECT count(*) FROM market.trade_status_effective").fetchone()[0] == 0
        assert missing_status_codes(conn, pd.Timestamp("2016-01-04").date()) == ["000001.SZ"]
        assert remove_ambiguous_tushare_status(conn, "2016-01-04", {"000001.SZ"}) == 0
        assert conn.execute("SELECT is_st FROM market.trade_status_daily").fetchone() == (True,)
        from db.instrument.dao.quant_inputs import read_stock_quant_facts
        from backend.modules.quant_research.infrastructure.source_reader import read_research_source
        day = pd.Timestamp("2016-01-04").date()
        assert read_stock_quant_facts(conn, ["000001.SZ"], day, ["atr_qfq"])["status_valid"] == set()
        assert read_research_source(conn, start=day, end=day)["trade_status"].empty


@pytest.mark.usefixtures("clean_market_state")
def test_unreported_st_conflict_cannot_be_verified(tmp_path):
    report, raw, _ = _source_files(tmp_path)
    with get_connection() as conn:
        assert conn.info.dbname == "liveprofit_instrument_test"
        conn.execute(
            "INSERT INTO market.instrument "
            "(ts_code,name,instrument_type,list_status,list_date,data_source) "
            "VALUES ('000001.SZ','sample','stock','L','1991-04-03','test')"
        )
        conn.execute(
            "INSERT INTO market.trade_status_daily "
            "(ts_code,trade_date,is_suspended,suspension_scope,is_st,market_board,source) "
            "VALUES ('000001.SZ','2016-01-04',FALSE,'none',TRUE,'MAIN','tushare')"
        )
        conn.commit()
        with pytest.raises(ValueError, match="ST_SOURCE_CONFLICT_REPORT_MISMATCH"):
            import_stock_st_source(conn, report, raw)
        assert conn.execute(
            "SELECT count(*) FROM market.stock_st_source_batch WHERE status='VERIFIED'"
        ).fetchone()[0] == 0


@pytest.mark.usefixtures("clean_market_state")
def test_scoped_source_can_prove_no_trade_without_claiming_full_st(tmp_path):
    from db.instrument.ingest.status_backfill import verified_no_trade_observations
    report, raw, _ = _source_files(tmp_path)
    frame = pd.read_parquet(raw)
    frame.loc[0, "trading"] = False
    frame.to_parquet(raw, index=False)
    content = json.loads(report.read_text(encoding="utf-8"))
    content["scope_codes"] = ["000001.SZ"]
    content["raw_parquet_sha256"] = hashlib.sha256(raw.read_bytes()).hexdigest()
    report.write_text(json.dumps(content), encoding="utf-8")
    with get_connection() as conn:
        assert conn.info.dbname == "liveprofit_instrument_test"
        conn.execute("INSERT INTO market.instrument (ts_code,name,instrument_type,list_date) VALUES "
                     "('000001.SZ','a','stock','1991-04-03'),('000002.SZ','b','stock','1991-04-03')")
        conn.commit()
        imported = import_stock_st_source(conn, report, raw)
        assert imported["status"] == "VERIFIED"
        day = pd.Timestamp("2016-01-04").date()
        assert verified_st_observations(conn, day) is None
        no_trade = verified_no_trade_observations(conn, day)
        assert no_trade.ts_code.tolist() == ["000001.SZ"]
        assert no_trade.reported_trading.tolist() == [False]
        assert verified_no_trade_observations(conn, pd.Timestamp("2016-01-05").date()) is None
        content.pop("scope_codes")
        report.write_text(json.dumps(content), encoding="utf-8")
        with pytest.raises(ValueError, match="ST_SOURCE_COVERAGE_MISMATCH"):
            import_stock_st_source(conn, report, raw)


@pytest.mark.usefixtures("clean_market_state")
@pytest.mark.parametrize("scope", [[], "000001.SZ", ["000001.SZ", "000001.SZ"], ["000999.SZ"]])
def test_invalid_source_scope_never_becomes_full_universe(tmp_path, scope):
    report, raw, _ = _source_files(tmp_path)
    content = json.loads(report.read_text(encoding="utf-8"))
    content["scope_codes"] = scope
    report.write_text(json.dumps(content), encoding="utf-8")
    with get_connection() as conn:
        assert conn.info.dbname == "liveprofit_instrument_test"
        conn.execute("INSERT INTO market.instrument (ts_code,name,instrument_type,list_date) "
                     "VALUES ('000001.SZ','a','stock','1991-04-03')")
        conn.commit()
        with pytest.raises(ValueError, match="ST_SOURCE_INVALID_SCOPE"):
            import_stock_st_source(conn, report, raw)
        assert conn.execute("SELECT count(*) FROM market.stock_st_source_batch").fetchone()[0] == 0
