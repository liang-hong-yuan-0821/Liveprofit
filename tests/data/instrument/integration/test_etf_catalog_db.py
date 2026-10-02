# test-catalog-begin
# {
#   "purpose": "证券数据 / etf_catalog_db：ETF catalog observations retain their real historical availability.",
#   "keywords": [
#     "证券数据",
#     "数据库",
#     "ETF",
#     "来源观测",
#     "etf_catalog_db",
#     "db",
#     "etf",
#     "observation"
#   ],
#   "covers": [
#     "db/instrument/dao/etf_catalog.py",
#     "db/instrument/db.py"
#   ],
#   "environment": [
#     "db"
#   ]
# }
# test-catalog-end

"""ETF catalog observations retain their real historical availability."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from db.instrument.dao.etf_catalog import insert_observation, query_as_of
from db.instrument.db import get_connection


def _row(observed_at: datetime, index_code: str) -> pd.DataFrame:
    return pd.DataFrame({
        "ts_code": ["510300.SH"],
        "observed_date": [observed_at.date()],
        "available_at": [observed_at],
        "index_code": [index_code],
        "exchange": ["SSE"],
        "etf_type": ["股票型"],
        "list_date": ["2012-05-28"],
        "list_status": ["L"],
        "source": ["tushare"],
    })


def test_observation_cannot_backfill_past_or_rewrite_same_day(pg_env):
    first = datetime(2026, 9, 24, 16, tzinfo=ZoneInfo("Asia/Shanghai"))
    later = datetime(2026, 9, 25, 16, tzinfo=ZoneInfo("Asia/Shanghai"))
    with get_connection() as conn:
        assert insert_observation(conn, _row(first, "000300.SH")) == 1
        conn.commit()
        assert query_as_of(conn, datetime(2026, 9, 23, 23, tzinfo=ZoneInfo("Asia/Shanghai"))).empty
        assert insert_observation(conn, _row(first, "WRONG.SH")) == 1
        conn.commit()
        assert query_as_of(conn, first)["index_code"].tolist() == ["000300.SH"]
        assert insert_observation(conn, _row(later, "000905.SH")) == 1
        conn.commit()
        assert query_as_of(conn, first)["index_code"].tolist() == ["000300.SH"]
        assert query_as_of(conn, later)["index_code"].tolist() == ["000905.SH"]
