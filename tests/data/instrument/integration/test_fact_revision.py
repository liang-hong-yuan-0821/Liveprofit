# test-catalog-begin
# {
#   "purpose": "证券数据 / fact_revision（版本修订）：Isolated market DB tests for local fact revision history.",
#   "keywords": [
#     "证券数据",
#     "历史审计",
#     "版本修订",
#     "事务回滚",
#     "表结构",
#     "fact_revision",
#     "history",
#     "revision",
#     "rollback",
#     "schema"
#   ],
#   "covers": [
#     "db/instrument/dao/fact_revision.py",
#     "db/instrument/db.py"
#   ],
#   "environment": [
#     "db"
#   ]
# }
# test-catalog-end

"""Isolated market DB tests for local fact revision history."""

from datetime import date, datetime, timedelta, timezone
import time

import pytest
from psycopg import Error as PsycopgError

from db.instrument.dao.fact_revision import read_fact_as_of
from db.instrument.db import get_connection, init_schema


DAY = date(2026, 9, 24)
CODE = "000001.SZ"


def _rows(conn, fact_table):
    return conn.execute(
        "SELECT id, operation, observed_at, payload FROM market.fact_revision "
        "WHERE fact_table=%s AND ts_code=%s AND trade_date=%s ORDER BY id",
        (fact_table, CODE, DAY),
    ).fetchall()


def test_insert_update_delete_history_and_asof_unknown(clean_market_state):
    with get_connection() as conn:
        assert conn.info.dbname == "liveprofit_instrument_test"
        conn.execute(
            "INSERT INTO market.instrument_daily "
            "(ts_code,trade_date,close,source,updated_at) VALUES (%s,%s,10,'tushare',now())",
            (CODE, DAY),
        )
        conn.commit()
        first = _rows(conn, "instrument_daily")
        assert len(first) == 1 and first[0][1] == "INSERT"
        assert first[0][3]["close"] == 10
        assert "updated_at" not in first[0][3]
        assert read_fact_as_of(
            conn, fact_table="instrument_daily", ts_code=CODE, trade_date=DAY,
            cutoff=first[0][2] - timedelta(microseconds=1),
        ).status == "UNKNOWN"
        conn.execute(
            "UPDATE market.instrument_daily SET updated_at=now() "
            "WHERE ts_code=%s AND trade_date=%s", (CODE, DAY),
        )
        assert len(_rows(conn, "instrument_daily")) == 1
        time.sleep(0.01)
        conn.execute(
            "UPDATE market.instrument_daily SET close=11 WHERE ts_code=%s AND trade_date=%s",
            (CODE, DAY),
        )
        conn.commit()
        second = _rows(conn, "instrument_daily")
        assert [row[1] for row in second] == ["INSERT", "UPDATE"]
        old = read_fact_as_of(conn, fact_table="instrument_daily", ts_code=CODE,
                              trade_date=DAY, cutoff=first[0][2])
        assert old.status == "PRESENT" and old.payload["close"] == 10
        now = read_fact_as_of(conn, fact_table="instrument_daily", ts_code=CODE,
                              trade_date=DAY, cutoff=datetime.now(timezone.utc))
        assert now.status == "PRESENT" and now.payload["close"] == 11
        conn.execute("DELETE FROM market.instrument_daily WHERE ts_code=%s AND trade_date=%s",
                     (CODE, DAY))
        conn.commit()
        assert read_fact_as_of(conn, fact_table="instrument_daily", ts_code=CODE,
                               trade_date=DAY, cutoff=datetime.now(timezone.utc)).status == "DELETED"


def test_all_core_tables_are_captured_and_rollback_is_atomic(clean_market_state):
    with get_connection() as conn:
        assert conn.info.dbname == "liveprofit_instrument_test"
        conn.execute("INSERT INTO market.adj_factor (ts_code,trade_date,adj_factor) "
                     "VALUES (%s,%s,1.2)", (CODE, DAY))
        conn.execute("INSERT INTO market.factor_daily (ts_code,trade_date,atr_bfq) "
                     "VALUES (%s,%s,0.5)", (CODE, DAY))
        conn.execute("INSERT INTO market.trade_status_daily "
                     "(ts_code,trade_date,is_suspended,is_st,source) "
                     "VALUES (%s,%s,false,false,'tushare')", (CODE, DAY))
        assert all(len(_rows(conn, table)) == 1 for table in (
            "adj_factor", "factor_daily", "trade_status_daily",
        ))
        conn.rollback()
        assert all(not _rows(conn, table) for table in (
            "adj_factor", "factor_daily", "trade_status_daily",
        ))
        assert conn.execute("SELECT count(*) FROM market.adj_factor").fetchone()[0] == 0


def test_revision_rows_cannot_be_rewritten(clean_market_state):
    with get_connection() as conn:
        assert conn.info.dbname == "liveprofit_instrument_test"
        conn.execute("INSERT INTO market.adj_factor (ts_code,trade_date,adj_factor) "
                     "VALUES (%s,%s,1.2)", (CODE, DAY))
        conn.commit()
        revision_id = _rows(conn, "adj_factor")[0][0]
        with pytest.raises(PsycopgError, match="immutable"):
            conn.execute("UPDATE market.fact_revision SET payload='{}'::jsonb WHERE id=%s",
                         (revision_id,))
        conn.rollback()
        assert _rows(conn, "adj_factor")[0][3]["adj_factor"] == 1.2


def test_key_rewrite_and_truncate_cannot_hide_history(clean_market_state):
    with get_connection() as conn:
        assert conn.info.dbname == "liveprofit_instrument_test"
        conn.execute("INSERT INTO market.adj_factor (ts_code,trade_date,adj_factor) "
                     "VALUES (%s,%s,1.2)", (CODE, DAY))
        conn.commit()
        with pytest.raises(PsycopgError, match="identity is immutable"):
            conn.execute("UPDATE market.adj_factor SET ts_code='000002.SZ' "
                         "WHERE ts_code=%s AND trade_date=%s", (CODE, DAY))
        conn.rollback()
        with pytest.raises(PsycopgError, match="cannot be truncated"):
            conn.execute("TRUNCATE market.adj_factor")
        conn.rollback()
        with pytest.raises(PsycopgError, match="cannot be truncated"):
            conn.execute("TRUNCATE market.fact_revision")
        conn.rollback()
        assert len(_rows(conn, "adj_factor")) == 1
        assert conn.execute("SELECT count(*) FROM market.adj_factor").fetchone()[0] == 1


def test_schema_reinitialization_keeps_one_capture_trigger(clean_market_state):
    assert init_schema() and init_schema()
    with get_connection() as conn:
        assert conn.info.dbname == "liveprofit_instrument_test"
        conn.execute("INSERT INTO market.adj_factor (ts_code,trade_date,adj_factor) "
                     "VALUES (%s,%s,1.2)", (CODE, DAY))
        conn.commit()
        assert len(_rows(conn, "adj_factor")) == 1


def test_asof_query_rejects_untrusted_identity(clean_market_state):
    with get_connection() as conn:
        for table in ("instrument_daily; DROP TABLE market.instrument_daily", "instrument"):
            with pytest.raises(ValueError, match="identity"):
                read_fact_as_of(conn, fact_table=table, ts_code=CODE, trade_date=DAY,
                                cutoff=datetime.now(timezone.utc))
        with pytest.raises(ValueError, match="timezone"):
            read_fact_as_of(conn, fact_table="adj_factor", ts_code=CODE,
                            trade_date=DAY, cutoff=datetime(2026, 9, 24))
