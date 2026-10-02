# test-catalog-begin
# {
#   "purpose": "证券数据 / research_source_db（数据来源）：Research reads are bounded and preserve actual ETF observation time.",
#   "keywords": [
#     "证券数据",
#     "数据库",
#     "ETF",
#     "来源证据",
#     "个股分析",
#     "research_source_db",
#     "db",
#     "etf",
#     "source",
#     "stock"
#   ],
#   "covers": [
#     "backend/modules/quant_research/infrastructure/source_reader.py",
#     "db/instrument/db.py"
#   ],
#   "environment": [
#     "db"
#   ]
# }
# test-catalog-end

"""Research reads are bounded and preserve actual ETF observation time."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from backend.modules.quant_research.infrastructure.source_reader import (
    read_research_source,
)
from db.instrument.db import get_connection


def test_source_reader_preserves_delisted_stock_and_as_of_etf_catalog(clean_market_state):
    with get_connection() as conn:
        assert conn.info.dbname == "liveprofit_instrument_test"
        conn.execute(
            "INSERT INTO market.instrument "
            "(ts_code, name, instrument_type, list_date, delist_date) VALUES "
            "('000001.SZ', 'stock', 'stock', '2020-01-01', '2020-01-06'), "
            "('510300.SH', 'fund', 'fund', '2012-05-28', NULL), "
            "('920000.BJ', 'bse', 'stock', '2020-12-23', NULL), "
            "('000300.SH', 'HS300', 'index', '2005-04-08', NULL)"
        )
        conn.execute(
            "INSERT INTO market.instrument_daily "
            "(ts_code, trade_date, open, high, low, close, amount, source) VALUES "
            "('000001.SZ', '2020-01-02', 10, 11, 9, 10, 123, 'tushare'), "
            "('510300.SH', '2020-01-02', 4, 5, 3, 4, 456, 'tushare'), "
            "('000300.SH', '2020-01-02', 4000, 4010, 3990, 4000, NULL, 'tushare')"
        )
        conn.execute(
            "INSERT INTO market.etf_catalog_observation "
            "(ts_code, observed_date, available_at, index_code, etf_type, "
            "list_date, list_status, source) VALUES "
            "('510300.SH', '2020-01-03', '2020-01-03T16:00:00+08:00', "
            "'000300.SH', '股票型', '2012-05-28', 'L', 'tushare')"
        )
        conn.execute(
            "INSERT INTO market.suspension_evidence "
            "(ts_code,trade_date,scope,source_url,published_on) VALUES "
            "('000001.SZ','2020-01-03','full_day',"
            "'https://static.cninfo.com.cn/example.pdf','2020-01-04')"
        )
        conn.commit()
        early = read_research_source(
            conn, start=date(2020, 1, 2), end=date(2020, 1, 2),
        )
        assert early["instrument"]["ts_code"].tolist() == ["000001.SZ", "510300.SH"]
        assert len(early["daily"]) == 2
        assert early["daily"]["amount"].tolist() == [123, 456]
        assert early["benchmark_daily"]["ts_code"].tolist() == ["000300.SH"]
        assert early["benchmark_daily"]["close"].tolist() == [4000]
        assert early["etf_catalog"].empty
        assert early["suspension_evidence"].empty
        late = read_research_source(
            conn, start=date(2020, 1, 2), end=date(2020, 1, 3),
        )
        assert late["etf_catalog"]["index_code"].tolist() == ["000300.SH"]
        assert late["suspension_evidence"].empty
        assert late["etf_catalog"].loc[0, "available_at"] == datetime(
            2020, 1, 3, 16, tzinfo=ZoneInfo("Asia/Shanghai"),
        )
        published = read_research_source(
            conn, start=date(2020, 1, 2), end=date(2020, 1, 4),
        )
        assert published["suspension_evidence"]["scope"].tolist() == ["full_day"]


def test_benchmark_reader_rejects_observations_outside_listing_interval(clean_market_state):
    with get_connection() as conn:
        assert conn.info.dbname == "liveprofit_instrument_test"
        conn.execute(
            "INSERT INTO market.instrument "
            "(ts_code, name, instrument_type, list_date, delist_date) VALUES "
            "('000300.SH', 'HS300', 'index', '2020-01-03', '2020-01-06')"
        )
        conn.execute(
            "INSERT INTO market.instrument_daily "
            "(ts_code, trade_date, open, high, low, close, source) VALUES "
            "('000300.SH', '2020-01-02', 10, 11, 9, 10, 'tushare'), "
            "('000300.SH', '2020-01-03', 10, 11, 9, 10, 'tushare'), "
            "('000300.SH', '2020-01-06', 10, 11, 9, 10, 'tushare')"
        )
        conn.commit()
        tables = read_research_source(conn, start=date(2020, 1, 2), end=date(2020, 1, 6))
        assert tables["benchmark_daily"]["trade_date"].tolist() == [date(2020, 1, 3)]
