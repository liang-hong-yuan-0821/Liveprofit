"""Actual lock/DAO/commit tests, ONLY liveprofit_instrument_test; fake sources."""

from datetime import date
from unittest.mock import MagicMock

import pandas as pd
import pytest

from AI.dataflows.providers.base_provider import ProviderNetworkAccessDenied
from db.instrument.ingest import (
    backfill,
    incremental,
    sector_daily,
    sectors,
    stock_factors,
)
from db.instrument.ingest.guard import (
    IngestBusy,
    IngestGuard,
    IngestSessionLost,
    is_ingest_lock_available,
)
from db.instrument.ingest.refresh import (
    RefreshSpec,
    collect_refresh,
    initialize_catalog,
)


@pytest.fixture
def conn(pg_env, clean_market_state):
    import psycopg
    with psycopg.connect(pg_env["psycopg_dsn"]) as value:
        assert value.info.dbname == "liveprofit_instrument_test"
        yield value


@pytest.fixture(autouse=True)
def no_source_sleep(monkeypatch):
    monkeypatch.setattr("db.instrument.ingest.frames.time.sleep", lambda _: None)


def bars(code, day="2026-09-22"):
    return pd.DataFrame({"ts_code": [code], "trade_date": [day], "open": [10.],
                         "high": [11.], "low": [9.], "close": [10.], "pct_chg": [1.]})


def test_all_public_entrypoints_lock_before_any_source(conn, pg_env):
    import psycopg
    provider = MagicMock()
    factory = MagicMock(return_value=provider)
    spec = RefreshSpec("CN_INDEX_BARS", "2026-09-22", ("000001.SH",))
    entries = [
        lambda c: incremental.collect_incremental(c, factory),
        lambda c: backfill.run_backfill(c, "2026-09-22", "2026-09-22", provider_factory=factory),
        lambda c: backfill.backfill_index_history(c, provider, "2026-09-22", "2026-09-22"),
        lambda c: backfill.run_stock_factor_backfill(c, "2026-09-22", "2026-09-22", provider_factory=factory),
        lambda c: sectors.collect_sectors(c, provider),
        lambda c: sector_daily.collect_sector_daily_incremental(c, provider),
        lambda c: stock_factors.collect_stock_quant_day(c, provider, "20260922", ["000001.SZ"]),
        lambda c: stock_factors.collect_stock_status_day(c, provider, "20260922"),
        lambda c: collect_refresh(c, spec, provider=provider),
        lambda c: initialize_catalog(c, provider),
    ]
    with IngestGuard(conn):
        conn.commit()  # session lock must survive commits
        with psycopg.connect(pg_env["psycopg_dsn"]) as second:
            assert not is_ingest_lock_available(second)
            for entry in entries:
                with pytest.raises(IngestBusy):
                    entry(second)
        factory.assert_not_called()
        assert provider.mock_calls == []
    with psycopg.connect(pg_env["psycopg_dsn"]) as second:
        assert is_ingest_lock_available(second)


def test_index_bootstrap_targeted_retry_and_notification_failure(conn, monkeypatch):
    provider = MagicMock(name="source")
    provider.name = "Tushare"
    provider.get_index_data_df.side_effect = lambda c, *_: bars(c)
    progress = []
    changed = MagicMock(side_effect=RuntimeError("redis unavailable"))
    spec = RefreshSpec("CN_INDEX_BARS", "2026-09-22", ("000001.SH", "399001.SZ"))
    with IngestGuard(conn, changed=changed) as guard:
        result = collect_refresh(conn, spec, progress.append, guard, provider=provider)
        assert guard.acquired
        assert result["completed"] == 2
        assert collect_refresh(conn, spec, guard=guard, provider=provider)["completed"] == 2
    assert provider.get_index_data_df.call_count == 2
    provider.get_index_factor_df.assert_not_called()
    provider.get_sector_daily_df.assert_not_called()
    provider.get_full_market_daily_df.assert_not_called()
    assert [e.completed for e in progress] == [1, 2]
    assert conn.execute("SELECT count(*) FROM market.instrument").fetchone()[0] == 2
    assert conn.execute("SELECT count(*) FROM market.instrument_daily").fetchone()[0] == 2
    factory = MagicMock(side_effect=AssertionError("complete data must not even construct a source"))
    monkeypatch.setattr("db.instrument.ingest.refresh._providers_from_env", factory)
    assert collect_refresh(conn, spec)["completed"] == 2
    factory.assert_not_called()


def test_lease_lost_after_first_unit_stops_next_source(conn):
    from db.instrument.ingest.guard import IngestOwnershipLost
    provider = MagicMock()
    provider.name = "Tushare"
    provider.get_index_data_df.side_effect = lambda c, *_: bars(c)
    owned = [True]
    def progress(event):
        owned[0] = False
    spec = RefreshSpec("CN_INDEX_BARS", "2026-09-22", ("000001.SH", "399001.SZ"))
    with IngestGuard(conn, fence=lambda: owned[0]) as guard, pytest.raises(IngestOwnershipLost):
        collect_refresh(conn, spec, progress, guard, provider=provider)
    assert provider.get_index_data_df.call_count == 1
    assert conn.execute("SELECT count(*) FROM market.instrument_daily").fetchone()[0] == 1


def test_sqlstate_connection_failure_is_fatal_even_if_socket_not_yet_closed(conn):
    from psycopg.errors import ConnectionFailure
    provider = MagicMock()
    provider.get_index_data_df.side_effect = ConnectionFailure("simulated SQLSTATE 08006")
    spec = RefreshSpec("CN_INDEX_BARS", "2026-09-22", ("000001.SH", "399001.SZ"))
    with pytest.raises(IngestSessionLost):
        collect_refresh(conn, spec, provider=provider)
    assert provider.get_index_data_df.call_count == 1


def test_fourth_failure_preserves_first_three_and_retry_skips_them(conn):
    codes = ("000001.SH", "399001.SZ", "399006.SZ", "000688.SH")
    provider = MagicMock()
    provider.name = "Tushare"
    provider.get_index_data_df.side_effect = lambda c, *_: None if c == codes[-1] else bars(c)
    spec = RefreshSpec("CN_INDEX_BARS", "2026-09-22", codes)
    result = collect_refresh(conn, spec, provider=provider)
    assert result["completed"] == 3 and result["failed"] == 1
    assert conn.execute("SELECT count(*) FROM market.instrument_daily").fetchone()[0] == 3
    provider.get_index_data_df.reset_mock()
    provider.get_index_data_df.side_effect = lambda c, *_: bars(c)
    assert collect_refresh(conn, spec, provider=provider)["completed"] == 4
    assert provider.get_index_data_df.call_args.args[0] == codes[-1]
    assert provider.get_index_data_df.call_count == 1


def test_disconnected_writer_stops_before_next_source(conn):
    provider = MagicMock()
    provider.name = "Tushare"
    def kill(code, *_):
        conn.close()
        return bars(code)
    provider.get_index_data_df.side_effect = kill
    spec = RefreshSpec("CN_INDEX_BARS", "2026-09-22", ("000001.SH", "399001.SZ"))
    with pytest.raises(IngestSessionLost):
        collect_refresh(conn, spec, provider=provider)
    assert provider.get_index_data_df.call_count == 1


def test_three_missing_sectors_only_three_source_calls(conn):
    codes = tuple(f"BK{i:04}.DC" for i in range(1000))
    conn.execute("INSERT INTO market.sector(source,sector_code,name) SELECT 'dc', c, c FROM unnest(%s::text[]) c", (list(codes),))
    conn.execute("INSERT INTO market.sector_daily(source,sector_code,trade_date,open,high,low,close,pct_chg) SELECT 'dc',c,'2026-09-22',10,11,9,10,1 FROM unnest(%s::text[]) c", (list(codes[:-3]),))
    conn.commit()
    provider = MagicMock()
    provider.get_sector_daily_df.side_effect = lambda source, c, *_: bars(c).rename(columns={"pct_chg": "pct_change"})
    spec = RefreshSpec("CN_SECTOR_DAILY", "2026-09-22", codes)
    result = collect_refresh(conn, spec, provider=provider)
    assert result["completed"] == 1000
    assert [c.args[1] for c in provider.get_sector_daily_df.call_args_list] == list(codes[-3:])


def test_source_network_access_denial_stops_the_refresh_immediately(conn):
    code = "BK0001.DC"
    conn.execute("INSERT INTO market.sector(source,sector_code,name) VALUES ('dc',%s,%s)", (code, code))
    conn.commit()
    provider = MagicMock()
    provider.get_sector_daily_df.return_value = None
    provider._network_access_error = ProviderNetworkAccessDenied("blocked by local policy")
    spec = RefreshSpec("CN_SECTOR_DAILY", "2026-09-22", (code,))

    with pytest.raises(ProviderNetworkAccessDenied):
        collect_refresh(conn, spec, provider=provider)

    assert provider.get_sector_daily_df.call_count == 1
    assert conn.execute("SELECT count(*) FROM market.sector_daily WHERE sector_code=%s", (code,)).fetchone()[0] == 0


def test_observed_status_preserves_limits_and_bfq_preserves_qfq(conn):
    from db.instrument.dao.factor_daily import upsert_observed_bfq_factors
    from db.instrument.dao.trade_status import upsert_observed_trade_status
    conn.execute("INSERT INTO market.trade_status_daily(ts_code,trade_date,is_suspended,is_st,market_board,up_limit,down_limit,source) VALUES ('000001.SZ','2026-09-22',false,false,'主板',11,9,'tushare')")
    conn.execute("INSERT INTO market.factor_daily(ts_code,trade_date,ma_qfq_5,ma_bfq_5) VALUES ('000001.SZ','2026-09-22',99,10)")
    status = pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": "2026-09-22", "is_suspended": True,
                               "is_st": False, "market_board": "主板", "source": "tushare", "updated_at": pd.Timestamp.now(),
                               "up_limit": None, "down_limit": None}])
    upsert_observed_trade_status(conn, status)
    upsert_observed_bfq_factors(conn, pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": "2026-09-22", "ma_bfq_5": 12, "ma_qfq_5": None}]))
    conn.commit()
    assert conn.execute("SELECT is_suspended,up_limit,down_limit FROM market.trade_status_daily").fetchone() == (True, 11, 9)
    assert conn.execute("SELECT ma_bfq_5,ma_qfq_5 FROM market.factor_daily").fetchone() == (12, 99)


def test_stock_status_independent_of_qfq_and_unknown_never_writes(conn):
    provider = MagicMock()
    provider.get_full_market_trade_status_df.return_value = pd.DataFrame([
        {"ts_code": "000001.SZ", "trade_date": "2026-09-22", "is_suspended": True, "is_st": False, "market_board": "主板"}])
    assert stock_factors.collect_stock_status_day(conn, provider, "2026-09-22")["rows"] == 1
    provider.get_full_market_technical_factor_df.assert_not_called()
    assert conn.execute("SELECT count(*) FROM market.ingest_state").fetchone()[0] == 0
    provider.get_full_market_trade_status_df.return_value = None
    assert stock_factors.collect_stock_status_day(conn, provider, "2026-09-22")["status"] == "UNAVAILABLE"
    assert conn.execute("SELECT count(*) FROM market.trade_status_daily WHERE is_suspended").fetchone()[0] == 1


def test_quant_refresh_group_writes_qfq_and_status_in_one_verified_operation(conn):
    from backend.modules.market_data.application.refresh_policy import Resource
    from db.instrument.ingest.stock_factors import REQUIRED_QFQ

    code, day = "000001.SZ", "2026-09-22"
    conn.execute(
        "INSERT INTO market.instrument(ts_code,name,instrument_type,list_status) "
        "VALUES (%s,'fixture','stock','L')", (code,),
    )
    conn.commit()
    factor = {"ts_code": code, "trade_date": day, **{column: 10.0 for column in REQUIRED_QFQ}}
    status = {"ts_code": code, "trade_date": day, "is_suspended": False,
              "is_st": False, "market_board": "主板", "up_limit": 11.0, "down_limit": 9.0}
    provider = MagicMock()
    provider.name = "Tushare"
    provider.get_full_market_technical_factor_df.return_value = pd.DataFrame([factor])
    provider.get_full_market_trade_status_df.return_value = pd.DataFrame([status])
    spec = RefreshSpec(
        Resource.CN_STOCK_QUANT_INPUTS.value, day, (code,),
        component_thresholds={"daily": 1.0, "qfq": 0.95,
                              "adj_factor": 0.95, "trade_status": 0.95},
        operation_units=({"operation": "qfq_status", "trade_date": day},),
    )

    changed = []
    with IngestGuard(conn, changed=changed.append) as guard:
        result = collect_refresh(conn, spec, guard=guard, provider=provider)

    assert result["status"] == "SUCCESS"
    assert (result["total"], result["processed"], result["completed"]) == (1, 1, 1)
    assert set(changed) == {Resource.CN_STOCK_QUANT_INPUTS.value, Resource.CN_STOCK_DAILY.value}
    assert conn.execute(
        "SELECT count(*) FROM market.factor_daily WHERE ts_code=%s AND trade_date=%s",
        (code, day),
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT count(*) FROM market.trade_status_daily WHERE ts_code=%s AND trade_date=%s",
        (code, day),
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT count(*) FROM market.ingest_state WHERE resource IN ('stock_factor_qfq','stock_trade_status')"
    ).fetchone()[0] == 2


def test_public_stock_daily_refresh_invalidates_quant_coverage(conn):
    from backend.modules.market_data.application.refresh_policy import Resource

    code, day = "000001.SZ", "2026-09-22"
    conn.execute(
        "INSERT INTO market.instrument(ts_code,name,instrument_type,list_status) "
        "VALUES (%s,'fixture','stock','L')", (code,),
    )
    conn.commit()
    provider = MagicMock()
    provider.name = "Tushare"
    provider.get_full_market_daily_df.return_value = bars(code, day)
    spec = RefreshSpec(Resource.CN_STOCK_DAILY.value, day, (code,))
    changed = []

    with IngestGuard(conn, changed=changed.append) as guard:
        result = collect_refresh(conn, spec, guard=guard, provider=provider)

    assert result["status"] == "SUCCESS"
    assert set(changed) == {Resource.CN_STOCK_DAILY.value, Resource.CN_STOCK_QUANT_INPUTS.value}


def test_quant_refresh_invalid_status_rolls_back_qfq_and_ingest_state(conn):
    from backend.modules.market_data.application.refresh_policy import Resource
    from db.instrument.ingest.stock_factors import REQUIRED_QFQ

    code, day = "000001.SZ", "2026-09-22"
    conn.execute(
        "INSERT INTO market.instrument(ts_code,name,instrument_type,list_status) "
        "VALUES (%s,'fixture','stock','L')", (code,),
    )
    conn.commit()
    factor = {"ts_code": code, "trade_date": day, **{column: 10.0 for column in REQUIRED_QFQ}}
    status = {"ts_code": code, "trade_date": day, "is_suspended": "false",
              "is_st": False, "market_board": "主板", "up_limit": 11.0, "down_limit": 9.0}
    provider = MagicMock()
    provider.name = "Tushare"
    provider.get_full_market_technical_factor_df.return_value = pd.DataFrame([factor])
    provider.get_full_market_trade_status_df.return_value = pd.DataFrame([status])
    spec = RefreshSpec(
        Resource.CN_STOCK_QUANT_INPUTS.value, day, (code,),
        component_thresholds={"daily": 1.0, "qfq": 0.95,
                              "adj_factor": 0.95, "trade_status": 0.95},
        operation_units=({"operation": "qfq_status", "trade_date": day},),
    )

    result = collect_refresh(conn, spec, provider=provider)

    assert result["status"] == "PARTIAL"
    assert result["completed"] == 0
    assert conn.execute("SELECT count(*) FROM market.factor_daily WHERE ts_code=%s", (code,)).fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM market.trade_status_daily WHERE ts_code=%s", (code,)).fetchone()[0] == 0
    assert conn.execute(
        "SELECT count(*) FROM market.ingest_state WHERE resource IN ('stock_factor_qfq','stock_trade_status')"
    ).fetchone()[0] == 0


def test_quant_daily_gap_refreshes_status_before_deciding_whether_to_fetch_daily(conn):
    from backend.modules.market_data.application.refresh_policy import Resource
    from db.instrument.ingest.stock_factors import REQUIRED_QFQ

    code, day = "000001.SZ", "2026-09-22"
    conn.execute(
        "INSERT INTO market.instrument(ts_code,name,instrument_type,list_status) "
        "VALUES (%s,'fixture','stock','L')", (code,),
    )
    factor_columns = ",".join(REQUIRED_QFQ)
    factor_values = ",".join(["%s"] * len(REQUIRED_QFQ))
    conn.execute(
        f"INSERT INTO market.factor_daily(ts_code,trade_date,{factor_columns}) "
        f"VALUES (%s,%s,{factor_values})", (code, day, *([10.0] * len(REQUIRED_QFQ))),
    )
    conn.execute(
        "INSERT INTO market.adj_factor(ts_code,trade_date,adj_factor) VALUES (%s,%s,1)", (code, day),
    )
    conn.execute(
        "INSERT INTO market.trade_status_daily(ts_code,trade_date,is_suspended,is_st,market_board,source) "
        "VALUES (%s,%s,false,false,'主板','tushare')", (code, day),
    )
    conn.commit()
    provider = MagicMock()
    provider.name = "Tushare"
    provider.get_full_market_technical_factor_df.return_value = pd.DataFrame([{
        "ts_code": code, "trade_date": day, **{column: 11.0 for column in REQUIRED_QFQ},
    }])
    provider.get_full_market_trade_status_df.return_value = pd.DataFrame([{
        "ts_code": code, "trade_date": day, "is_suspended": False, "is_st": False,
        "market_board": "主板", "up_limit": 11.0, "down_limit": 9.0,
    }])
    provider.get_full_market_daily_df.return_value = pd.DataFrame([{
        "ts_code": code, "trade_date": day, "open": 10.0, "high": 11.0,
        "low": 9.0, "close": 10.0, "pct_chg": 1.0,
    }])
    spec = RefreshSpec(
        Resource.CN_STOCK_QUANT_INPUTS.value, day, (code,),
        component_missing_units=({"component": "daily", "code": code,
                                  "trade_date": day, "reason": "MISSING_ROW"},),
        component_thresholds={"daily": 1.0, "qfq": 0.95,
                              "adj_factor": 0.95, "trade_status": 0.95},
        operation_units=(
            {"operation": "daily", "code": code, "trade_date": day},
            {"operation": "qfq_status", "trade_date": day, "force_refresh": True},
        ),
    )

    result = collect_refresh(conn, spec, provider=provider)

    assert result["status"] == "SUCCESS"
    assert (result["total"], result["processed"], result["completed"]) == (2, 2, 2)
    provider.get_full_market_technical_factor_df.assert_called_once_with(day.replace("-", ""))
    provider.get_full_market_trade_status_df.assert_called_once_with(day.replace("-", ""))
    provider.get_full_market_daily_df.assert_called_once_with(day.replace("-", ""), "stock")
    assert conn.execute(
        "SELECT count(*) FROM market.instrument_daily WHERE ts_code=%s AND trade_date=%s", (code, day),
    ).fetchone()[0] == 1


def test_catalog_initialization_stock_and_dc_only(conn):
    provider = MagicMock()
    provider.get_stock_basic_df.return_value = pd.DataFrame([{"ts_code": "000001.SZ", "name": "平安银行", "list_status": "L", "list_date": "19910403"}])
    provider.get_trade_cal.return_value = pd.DataFrame({"trade_date": ["20260922"]})
    provider.get_concept_list_df.return_value = pd.DataFrame({"ts_code": ["BK0001.DC"], "name": ["概念"]})
    provider.get_concept_members_df.return_value = pd.DataFrame({"sector_code": ["BK0001.DC"], "ts_code": ["000001.SZ"]})
    result = initialize_catalog(conn, provider)
    assert result["stocks"] == 1 and result["sectors"]["dc"]["members"] == 1
    provider.get_concept_list_df.assert_called_once_with("dc")
    provider.get_fund_basic_df.assert_not_called()
    provider.get_full_market_technical_factor_df.assert_not_called()
    provider.get_full_market_daily_df.assert_not_called()
    assert conn.execute("SELECT count(*) FROM market.instrument_daily").fetchone()[0] == 0


def test_catalog_stock_only_repair_skips_existing_dc_member_refresh(conn):
    provider = MagicMock()
    provider.get_stock_basic_df.return_value = pd.DataFrame([{
        "ts_code": "920025.BJ", "name": "Example", "list_status": "L",
        "list_date": "20260101", "exchange": "BSE", "market": "BJ", "area": "北京",
    }])

    result = initialize_catalog(conn, provider, refresh_sectors=False)

    assert result == {"stocks": 1, "sectors": {}}
    provider.get_stock_basic_df.assert_called_once_with()
    provider.get_concept_list_df.assert_not_called()
    provider.get_concept_members_df.assert_not_called()
    assert conn.execute(
        "SELECT list_status,list_date FROM market.instrument WHERE ts_code='920025.BJ'"
    ).fetchone() == ("L", date(2026, 1, 1))
    assert conn.execute(
        "SELECT exchange,market,area FROM market.stock_info WHERE ts_code='920025.BJ'"
    ).fetchone() == ("BSE", "BJ", "北京")


def test_daily_job_releases_market_lock_before_returning_shared_connection(conn, pg_env, monkeypatch):
    import psycopg

    from AI.eventStudy.scheduler.daily_job import step_collect_market
    with psycopg.connect(pg_env["psycopg_dsn"]) as second:
        def collect(write_conn, *args, guard=None, **kwargs):
            assert write_conn is conn and guard.conn is conn
            assert not is_ingest_lock_available(second)
            return {"daily": {}}
        monkeypatch.setattr(incremental, "collect_incremental", collect)
        step_collect_market(conn, changed=lambda _: None)
        # The daily_job PG connection is still alive for subsequent AI work,
        # but another collector can now acquire the market-only session lock.
        assert not conn.closed
        assert is_ingest_lock_available(second)
