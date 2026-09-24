"""Real isolated PostgreSQL verification; all data is synthetic and no source runs."""
from contextlib import contextmanager
from datetime import date, datetime, timezone

import psycopg
import pytest

from backend.modules.market_data.application.refresh_policy import KLINE_FACTOR_COLUMNS, RefreshPolicy, Resource
from backend.modules.market_data.infrastructure.refresh_repository import RefreshRepository
from db.instrument.ingest.incremental import INDEX_TARGETS, _is_cn_index_code


CN_CODES = sorted(code for code in INDEX_TARGETS if _is_cn_index_code(code))
NOW = datetime(2026, 9, 23, 2, tzinfo=timezone.utc)


@pytest.fixture
def connection(env):
    with psycopg.connect(env["psycopg_dsn"]) as conn:
        assert conn.info.dbname == "liveprofit_market_test", "Only the dedicated test database may be seeded"
        yield conn


def target(resource="CN_INDEX_BARS"):
    return RefreshPolicy().target(resource, NOW)


def read(conn, resource="CN_INDEX_BARS", expected=None):
    return RefreshRepository().snapshot(resource, expected or target(resource), conn=conn)


def bar(conn, code, day, *, open_=1.0, pct=1.0):
    conn.execute(
        "INSERT INTO market.instrument_daily(ts_code,trade_date,open,high,low,close,pct_chg,source,updated_at) "
        "VALUES (%s,%s,%s,2,1,1,%s,'test',now()) "
        "ON CONFLICT(ts_code,trade_date) DO UPDATE SET open=EXCLUDED.open,pct_chg=EXCLUDED.pct_chg,updated_at=now()",
        (code, day, open_, pct),
    )


def stock(conn, code, *, listed="2000-01-01", status="L", delisted=None):
    conn.execute(
        "INSERT INTO market.instrument(ts_code,name,instrument_type,list_status,list_date,delist_date) "
        "VALUES (%s,%s,'stock',%s,%s,%s)", (code, code, status, listed, delisted),
    )


def suspended(conn, code, day, source="tushare"):
    conn.execute(
        "INSERT INTO market.trade_status_daily(ts_code,trade_date,is_suspended,source) VALUES (%s,%s,true,%s) "
        "ON CONFLICT(ts_code,trade_date) DO UPDATE SET source=EXCLUDED.source",
        (code, day, source),
    )


def seed_index_window(conn, *, skip=()):
    for day in target().window_dates:
        for code in CN_CODES:
            if (code, day) not in skip:
                bar(conn, code, day)


def assert_identity(counts):
    assert counts["expected_count"] == counts["available_count"] + counts["exempt_count"] + counts["missing_count"]


def test_fixed_index_catalog_cold_start_and_empty_dynamic_catalog(connection):
    snapshot = read(connection)
    assert snapshot["expected_count"] == 11
    assert snapshot["missing_count"] == 11
    assert snapshot["window_coverage"]["expected_count"] == 33
    assert snapshot["block_reason"] is None
    assert len(snapshot["target_spec"]["units"]) == 33
    assert snapshot["freshness"] == "UNAVAILABLE"
    for resource in ("CN_STOCK_DAILY", "CN_SECTOR_DAILY"):
        result = read(connection, resource)
        assert result["block_reason"] == "CATALOG_UNAVAILABLE"
        assert result["freshness"] == "UNAVAILABLE"
        assert result["expected_count"] == 0
    assert read(connection, "US_INDEX_BARS")["expected_count"] == 3
    assert read(connection, "KR_INDEX_BARS")["expected_count"] == 1


def test_ten_of_eleven_and_invalid_ohlc_are_partial(connection):
    missing_code = CN_CODES[-1]
    seed_index_window(connection, skip={(missing_code, target().expected_trade_date)})
    result = read(connection)
    assert (result["available_count"], result["missing_count"]) == (10, 1)
    assert result["freshness"] == "PARTIAL"
    assert result["complete_through_date"] == "2026-09-21"
    bar(connection, missing_code, "2026-09-22", open_=None)
    invalid = read(connection)
    assert invalid["missing_units"][0]["reason"] == "INVALID_VALUES"
    bar(connection, missing_code, "2026-09-22", open_=float("nan"))
    assert read(connection)["missing_count"] == 1
    bar(connection, missing_code, "2026-09-22")
    assert read(connection)["freshness"] == "FRESH"
    assert_identity(result)
    assert_identity(result["window_coverage"])


def test_last_day_complete_does_not_hide_short_window_hole(connection):
    hole = (CN_CODES[0], date(2026, 9, 21))
    seed_index_window(connection, skip={hole})
    result = read(connection)
    assert (result["available_count"], result["missing_count"]) == (11, 0)
    assert (result["window_coverage"]["available_count"], result["window_coverage"]["missing_count"]) == (32, 1)
    assert result["freshness"] == "PARTIAL"
    assert result["complete_through_date"] == "2026-09-22"
    assert result["target_spec"]["units"] == [{"code": hole[0], "trade_date": "2026-09-21"}]
    before = RefreshRepository().verify_spec(result["target_spec"], conn=connection)
    assert (before["total"], before["completed"]) == (1, 0)
    bar(connection, *hole)
    after = RefreshRepository().verify_spec(result["target_spec"], conn=connection)
    assert (after["total"], after["completed"]) == (1, 1)
    assert after["freshness"] == "FRESH"
    assert read(connection)["coverage_digest"] != result["coverage_digest"]


def test_factors_need_every_required_column_independent_of_bars(connection):
    seed_index_window(connection)
    columns = ",".join(KLINE_FACTOR_COLUMNS)
    placeholders = ",".join(["%s"] * len(KLINE_FACTOR_COLUMNS))
    for day in target().window_dates:
        for code in CN_CODES:
            values = [1] * len(KLINE_FACTOR_COLUMNS)
            if code == CN_CODES[-1] and day == target().expected_trade_date:
                values[-1] = None
            connection.execute(
                f"INSERT INTO market.factor_daily(ts_code,trade_date,{columns},updated_at) "
                f"VALUES (%s,%s,{placeholders},now())", [code, day, *values],
            )
    assert read(connection)["freshness"] == "FRESH"
    factors = read(connection, "CN_INDEX_FACTORS")
    assert factors["missing_count"] == 1
    assert factors["window_coverage"]["available_count"] == 32
    assert factors["missing_units"][0]["reason"] == "INVALID_VALUES"


def test_stock_lifecycle_suspension_union_and_count_invariants(connection):
    stock(connection, "A.SZ")
    stock(connection, "B.SZ", status="P")
    stock(connection, "C.SZ", listed=None)
    stock(connection, "D.SZ", listed="2026-09-23")
    stock(connection, "E.SZ", status="D", delisted="2026-09-21")
    stock(connection, "F.SZ", listed="2026-09-22")
    connection.execute("INSERT INTO market.sector_member(source,sector_code,ts_code) VALUES ('dc','BK1','G.SZ'), ('dc','BK2','G.SZ')")
    for day in target().window_dates:
        bar(connection, "A.SZ", day)
        suspended(connection, "A.SZ", day)  # Valid quote wins over suspension.
        suspended(connection, "B.SZ", day)
        bar(connection, "C.SZ", day)  # Missing lifecycle is still missing, even with a quote.
    bar(connection, "E.SZ", "2026-09-18")
    bar(connection, "F.SZ", "2026-09-22")
    result = read(connection, "CN_STOCK_DAILY")
    assert (result["expected_count"], result["available_count"], result["exempt_count"], result["missing_count"]) == (4, 2, 1, 1)
    assert result["window_coverage"] == {"from": "2026-09-18", "to": "2026-09-22", "expected_count": 11,
                                         "available_count": 5, "exempt_count": 3, "missing_count": 3}
    assert {unit["code"] for unit in result["missing_units"]} == {"C.SZ"}
    assert all(unit["reason"] == "LIFECYCLE_UNKNOWN" for unit in result["missing_units"])
    assert result["block_reason"] == "CATALOG_INCOMPLETE"
    assert_identity(result)
    assert_identity(result["window_coverage"])


def test_explicit_untraded_statuses_are_excluded_until_list_date(connection):
    stock(connection, "G.SZ", listed=None, status="G")
    stock(connection, "U.SZ", listed=None, status="U")
    stock(connection, "FUTURE.SZ", listed="2026-09-22", status="G")

    result = read(connection, "CN_STOCK_DAILY")

    assert result["expected_count"] == 1
    assert result["missing_count"] == 1
    assert {unit["code"] for unit in result["missing_units"]} == {"FUTURE.SZ"}
    assert result["block_reason"] is None
    assert result["window_coverage"]["expected_count"] == 1




def test_cn_stock_universe_uses_listing_catalog_not_prelisting_sector_members(connection):
    stock(connection, "600000.SH")
    connection.execute(
        "INSERT INTO market.sector_member(source,sector_code,ts_code) "
        "VALUES ('dc','BK1','200011.SZ'),('dc','BK2','201872.SZ'),('dc','BK3','900901.SH'),"
        "('dc','BK4','301716.SZ'),('dc','BK5','920025.BJ')"
    )

    result = read(connection, "CN_STOCK_DAILY")

    assert result["expected_count"] == 1
    assert result["missing_count"] == 1
    assert {unit["code"] for unit in result["missing_units"]} == {"600000.SH"}
    assert result["block_reason"] is None
    assert result["target_spec"]["codes"] == ["600000.SH"]

    # Jobs already in Redis keep their frozen target, but B shares and
    # prelisting sector members are retired during durable re-verification.
    day = target("CN_STOCK_DAILY").expected_trade_date.isoformat()
    legacy_spec = {
        "resource": "CN_STOCK_DAILY",
        "target_trade_date": day,
        "codes": ["200011.SZ", "301716.SZ", "600000.SH"],
        "dates": [day],
        "units": [{"code": "200011.SZ", "trade_date": day},
                  {"code": "301716.SZ", "trade_date": day},
                  {"code": "600000.SH", "trade_date": day}],
    }
    pending = RefreshRepository().verify_spec(legacy_spec, conn=connection)
    assert pending["total"] == 3
    assert pending["completed"] == 2
    assert {unit["code"] for unit in pending["missing_units"]} == {"600000.SH"}
    bar(connection, "600000.SH", day)
    completed = RefreshRepository().verify_spec(legacy_spec, conn=connection)
    assert completed["completed"] == completed["total"] == 3
    assert completed["missing_units"] == []


def test_stock_pct_required_and_untrusted_or_absent_status_not_exempt(connection):
    stock(connection, "A.SZ")
    for day in target().window_dates:
        bar(connection, "A.SZ", day, pct=None)
        suspended(connection, "A.SZ", day, source="unknown")
    result = read(connection, "CN_STOCK_DAILY")
    assert result["available_count"] == result["exempt_count"] == 0
    assert result["missing_count"] == 1
    for day in target().window_dates:
        suspended(connection, "A.SZ", day)
    trusted = read(connection, "CN_STOCK_DAILY")
    assert trusted["freshness"] == "FRESH"
    assert trusted["exempt_count"] == 1
    # A new repository with no runtime state reconstructs the same durable fact.
    assert read(connection, "CN_STOCK_DAILY")["coverage_digest"] == trusted["coverage_digest"]


def test_sector_dictionary_not_observed_rows_defines_denominator(connection):
    connection.execute("INSERT INTO market.sector(source,sector_code,name) VALUES ('dc','BK1','One'),('dc','BK2','Two'),('ths','T1','Other')")
    for day in target().window_dates:
        connection.execute(
            "INSERT INTO market.sector_daily(source,sector_code,trade_date,open,high,low,close,pct_chg,updated_at) "
            "VALUES ('dc','BK1',%s,1,2,1,1,1,now())", (day,),
        )
    result = read(connection, "CN_SECTOR_DAILY")
    assert (result["expected_count"], result["available_count"], result["missing_count"]) == (2, 1, 1)
    assert len(result["missing_units"]) == 3
    assert all(unit["code"] == "BK2" for unit in result["missing_units"])
    assert RefreshRepository().concept_display_date(conn=connection) == date(2026, 9, 22)


def test_latest_observed_complete_through_and_history_gap_have_distinct_meanings(connection):
    seed_index_window(connection)
    bar(connection, CN_CODES[0], "2026-09-15")  # 16/17 missing, outside automatic window.
    bar(connection, CN_CODES[0], "2026-09-23")  # Real early publication, before ready_at.
    result = read(connection)
    assert result["latest_observed_date"] == "2026-09-23"
    assert result["expected_trade_date"] == result["complete_through_date"] == "2026-09-22"
    assert result["freshness"] == "FRESH"
    assert result["history_gap"] is True
    assert result["warnings"] == ["HISTORY_GAP"]
    assert result["target_spec"]["units"] == []
    assert result["target_spec"]["dates"] == ["2026-09-18", "2026-09-21", "2026-09-22"]


def test_unknown_calendar_retains_existing_dates_but_has_no_collection_spec(connection):
    bar(connection, CN_CODES[0], "2026-09-21")
    unknown = RefreshPolicy().target("CN_INDEX_BARS", datetime(2027, 1, 4, 12, tzinfo=timezone.utc))
    result = read(connection, expected=unknown)
    assert result["freshness"] == "UNKNOWN"
    assert result["latest_observed_date"] == "2026-09-21"
    assert result["complete_through_date"] is None
    assert result["expected_trade_date"] is None
    assert result["block_reason"] == "CALENDAR_UNAVAILABLE"
    assert not result["target_spec"]["units"]


def test_factory_read_has_no_provider_or_publisher_dependency(connection, monkeypatch):
    import db.instrument.ingest.incremental as incremental
    def forbidden(*args, **kwargs):
        raise AssertionError("Coverage reads must not collect")
    monkeypatch.setattr(incremental, "collect_incremental", forbidden)
    @contextmanager
    def factory():
        yield connection
    repo = RefreshRepository(factory)
    assert repo.snapshot("CN_INDEX_BARS", target())["freshness"] == "UNAVAILABLE"
    with pytest.raises(ValueError):
        repo.snapshot("US_INDEX_BARS", target())


def test_quant_refresh_uses_execution_universe_and_separate_component_thresholds(connection):
    from db.instrument.dao.instrument import list_active_cn_stocks
    from db.instrument.ingest.stock_factors import REQUIRED_QFQ
    from backend.modules.analysis.infrastructure.quant_execution_market_data import AllMarketUniverseBuilder

    codes = [f"{number:06d}.SZ" for number in range(1, 21)]
    day = target(Resource.CN_STOCK_QUANT_INPUTS).expected_trade_date
    for code in codes:
        stock(connection, code)
        bar(connection, code, day)
        qfq_values = [1.0] * len(REQUIRED_QFQ)
        if code != codes[0]:
            cols = ",".join(REQUIRED_QFQ)
            marks = ",".join(["%s"] * len(REQUIRED_QFQ))
            connection.execute(
                f"INSERT INTO market.factor_daily(ts_code,trade_date,{cols}) VALUES (%s,%s,{marks})",
                [code, day, *qfq_values],
            )
        if code != codes[1]:
            connection.execute(
                "INSERT INTO market.adj_factor(ts_code,trade_date,adj_factor) VALUES (%s,%s,1.0)",
                (code, day),
            )
        if code != codes[2]:
            connection.execute(
                "INSERT INTO market.trade_status_daily(ts_code,trade_date,is_suspended,is_st,market_board,source) "
                "VALUES (%s,%s,false,false,'MAIN','tushare')", (code, day),
            )

    expected_codes = list_active_cn_stocks(connection)
    assert AllMarketUniverseBuilder.list_active_cn_stocks(connection) == expected_codes == codes
    snapshot = read(connection, Resource.CN_STOCK_QUANT_INPUTS.value, target(Resource.CN_STOCK_QUANT_INPUTS))

    assert snapshot["freshness"] == "FRESH"
    assert snapshot["component_coverage"]["qfq"]["available_count"] == 19
    assert snapshot["component_coverage"]["adj_factor"]["available_count"] == 19
    assert snapshot["component_coverage"]["trade_status"]["available_count"] == 19
    assert snapshot["target_spec"]["universe_digest"] == RefreshRepository._sha_codes(codes)
    assert snapshot["target_spec"]["units"] == []
