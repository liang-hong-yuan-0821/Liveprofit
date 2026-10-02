# test-catalog-begin
# {
#   "purpose": "行情服务 / market_refresh_reads（行情刷新）：Read-date semantics and interactive factor gates on isolated PG/Redis.",
#   "keywords": [
#     "行情服务",
#     "行情数据",
#     "复权因子",
#     "历史审计",
#     "市场分析",
#     "行情刷新",
#     "个股分析",
#     "market_refresh_reads",
#     "factor",
#     "history",
#     "market",
#     "refresh",
#     "stock"
#   ],
#   "covers": [
#     "backend/bootstrap/settings.py",
#     "backend/modules/market_data/infrastructure/calendar_adapter.py",
#     "db/instrument/db.py",
#     "db/instrument/ingest/guard.py"
#   ],
#   "environment": [
#     "db",
#     "redis"
#   ]
# }
# test-catalog-end

"""Read-date semantics and interactive factor gates on isolated PG/Redis."""
from contextlib import contextmanager
from datetime import date, datetime, timezone

import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from backend.bootstrap.settings import CoreSettings
from backend.modules.market_data.infrastructure.calendar_adapter import FakeCalendar, MarketCalendarAdapter
from tests.support.python.contract_env import _test_db_url
from tests.backend.market_data.support.market_data import _seed_concept_tree, _seed_sector_daily, _seed_stock_bars, _seed_bars


@contextmanager
def market_sql():
    engine = create_engine(_test_db_url(CoreSettings().resolved_database_url()))
    assert engine.url.database == "liveprofit_contract_test"
    try:
        with engine.begin() as conn:
            yield conn
    finally:
        engine.dispose()


def test_latest_and_exact_history_filter_candidates_and_count_full_members(client):
    _seed_sector_daily(client)
    _seed_concept_tree(client)
    client.http.app.state.market_calendar = FakeCalendar(last_day=date(2026, 9, 7))
    with market_sql() as conn:
        conn.execute(text("UPDATE market.instrument SET list_date='2000-01-01',list_status='L' WHERE instrument_type='stock'"))
        conn.execute(text("UPDATE market.instrument SET list_date='2027-01-01' WHERE ts_code='600050.SH'"))
        conn.execute(text("UPDATE market.instrument SET delist_date='2026-09-07',list_status='D' WHERE ts_code='600051.SH'"))
        conn.execute(text("INSERT INTO market.sector(source,sector_code,name) VALUES ('dc','EMPTY','No data'),('dc','OLD','Old high heat')"))
        conn.execute(text("INSERT INTO market.sector_daily(source,sector_code,trade_date,open,high,low,close,pct_chg) VALUES ('dc','OLD','2026-09-06',1,2,1,2,999)"))
        conn.execute(text("INSERT INTO market.sector_member(source,sector_code,ts_code) VALUES ('dc','BK1754','600001.SH')"))
        conn.execute(text("INSERT INTO market.trade_status_daily(ts_code,trade_date,is_suspended,source) VALUES ('600000.SH','2026-09-07',true,'tushare'),('300002.SZ','2026-09-07',true,'tushare')"))
    url = "/api/v1/market-data/concepts/tree?market=CN&interval=1d&limit=3"
    response = client.http.get(url)
    assert response.status_code == 200
    latest = response.json()["data"]
    assert latest["requested_as_of"] is None
    assert latest["date_mode"] == "LATEST"
    assert latest["as_of"] == "2026-09-07"
    assert [row["sector_code"] for row in latest["items"]] == ["BK1753", "BK1754", "BK1755"]
    assert [row["heat_window_rows"] for row in latest["items"]] == [11, 5, 1]
    assert latest["coverage"]["boards"] == dict(expected_count=5, available_count=3, exempt_count=0, missing_count=2)
    # More than 100 members and one stock in two sectors count before display truncation.
    assert latest["coverage"]["members"] == dict(expected_count=104, available_count=101, exempt_count=2, missing_count=1)
    assert len(latest["items"][0]["members"]) == 100
    assert latest["items"][0]["member_total"] == 103
    historical = client.http.get(url + "&as_of=2026-09-08").json()["data"]
    assert historical["date_mode"] == "HISTORICAL"
    assert historical["requested_as_of"] == historical["as_of"] == "2026-09-08"
    assert historical["items"] == []
    assert historical["coverage"]["boards"]["available_count"] == 0
    hot = client.http.get("/api/v1/market-data/concepts/hot?market=CN&interval=1d&limit=3").json()["data"]
    assert hot["coverage"]["members"] is None
    assert len(hot["items"]) == 3


def test_stock_auto_reads_never_fetch_and_interactive_cooldown_protects_qfq(client):
    _seed_stock_bars(client)
    calls = []
    def fetcher(symbol, start, end):
        calls.append((symbol, start, end))
        # Deliberately partial data keeps ensure eligible for a read-side coverage check.
        return pd.DataFrame({"trade_date": ["2026-09-04"], "ma_bfq_5": [15.]})
    client.http.app.state.stock_factor_fetcher = fetcher
    with market_sql() as conn:
        conn.execute(text("INSERT INTO market.factor_daily(ts_code,trade_date,ma_qfq_5) VALUES ('600519.SH','2026-09-04',999)"))
    url = "/api/v1/market-data/stocks/600519.SH/bars?market=CN&interval=1d&from=2026-09-01&to=2026-09-07"
    for _ in range(100):
        assert client.http.get(url + "&factor_policy=cache_only").status_code == 200
    assert calls == []
    assert client.http.get(url).status_code == 200
    assert len(calls) == 1
    for _ in range(10):
        assert client.http.get(url).status_code == 200
    assert len(calls) == 1
    with market_sql() as conn:
        assert conn.execute(text("SELECT ma_qfq_5,ma_bfq_5 FROM market.factor_daily WHERE ts_code='600519.SH' AND trade_date='2026-09-04'" )).one() == (999., 15.)
    assert client.http.get(url + "&factor_policy=unexpected").status_code == 422


def test_stock_factor_lock_precedes_redis_and_gate_failure_keeps_cache(client, monkeypatch):
    from db.instrument.db import get_connection
    from db.instrument.ingest.guard import IngestGuard
    _seed_stock_bars(client)
    called = []
    monkeypatch.setattr(client.http.app.state, "stock_factor_fetcher", lambda *args: called.append("source"), raising=False)
    monkeypatch.setattr(client.http.app.state, "market_factor_gate", lambda *args: called.append("gate") or True, raising=False)
    url = "/api/v1/market-data/stocks/600519.SH/bars?market=CN&interval=1d&from=2026-09-01&to=2026-09-07"
    with get_connection() as conn:
        assert conn.info.dbname == "liveprofit_contract_test"
        with IngestGuard(conn):
            assert client.http.get(url).status_code == 200
    assert called == []
    def unavailable(*args):
        raise ConnectionError("test Redis unavailable")
    monkeypatch.setattr(client.http.app.state, "market_factor_gate", unavailable)
    assert client.http.get(url).status_code == 200
    assert called == []


def test_chart_market_session_and_publication_are_independent(client, monkeypatch):
    _seed_bars(client, trade_date=date(2026, 9, 22))
    monkeypatch.setattr(client.http.app.state, "market_calendar", MarketCalendarAdapter())
    clock = type("Clock", (), {"now": lambda _: datetime(2026, 9, 23, 3, 30, tzinfo=timezone.utc)})()
    monkeypatch.setattr(client.http.app.state, "market_clock", clock, raising=False)
    result = client.http.get("/api/v1/market-data/indices/000001.SH/bars?market=CN&interval=1d&from=2026-09-01&to=2026-09-23").json()["data"]
    assert result["market_session_status"] == "BREAK"
    assert result["freshness_status"] == "FRESH"  # Today's daily data is not yet due.
