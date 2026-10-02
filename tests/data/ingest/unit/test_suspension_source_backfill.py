# test-catalog-begin
# {
#   "purpose": "数据采集 / suspension_source_backfill（数据来源）：Verified vendor suspensions persist independently of missing ST status.",
#   "keywords": [
#     "数据采集",
#     "来源证据",
#     "停牌",
#     "suspension_source_backfill",
#     "source",
#     "suspension"
#   ],
#   "covers": [
#     "db/instrument/ingest/suspension_source_backfill.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

"""Verified vendor suspensions persist independently of missing ST status."""

from datetime import date
from unittest.mock import MagicMock

import pandas as pd
import pytest

from db.instrument.ingest.suspension_source_backfill import collect_suspension_source_day


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


def test_replaces_only_valid_active_no_bar_observations():
    conn = MagicMock()
    conn.execute.side_effect = [
        _Rows([("601611.SH",), ("000001.SZ",)]),
        _Rows([("000001.SZ",)]),
        MagicMock(), MagicMock(),
    ]
    provider = MagicMock()
    provider.get_verified_full_day_suspensions_df.return_value = pd.DataFrame({
        "ts_code": ["601611.SH", "000001.SZ", "920001.BJ"],
        "trade_date": ["2016-06-30"] * 3,
    })

    result = collect_suspension_source_day(conn, provider, date(2016, 6, 30))

    assert result["status"] == "PARTIAL"
    assert result["rows"] == 1
    assert result["bar_conflicts"] == ["000001.SZ"]
    assert result["outside_stock_pool"] == ["920001.BJ"]
    writes = [call.args[0] for call in conn.execute.call_args_list]
    assert any("DELETE FROM market.suspension_source_daily" in sql for sql in writes)
    assert sum("INSERT INTO market.suspension_source_daily" in sql for sql in writes) == 1
    conn.commit.assert_called_once()


def test_unavailable_source_cannot_erase_old_observations():
    conn = MagicMock()
    provider = MagicMock()
    provider.get_verified_full_day_suspensions_df.return_value = None

    result = collect_suspension_source_day(conn, provider, date(2016, 6, 30))

    assert result["status"] == "UNAVAILABLE"
    conn.execute.assert_not_called()
    conn.commit.assert_not_called()


def test_verified_empty_source_retracts_stale_day_observations():
    conn = MagicMock()
    provider = MagicMock()
    provider.get_verified_full_day_suspensions_df.return_value = pd.DataFrame(
        columns=["ts_code", "trade_date"])

    result = collect_suspension_source_day(conn, provider, date(2016, 6, 30))

    assert result["status"] == "SUCCESS" and result["rows"] == 0
    assert "DELETE FROM market.suspension_source_daily" in conn.execute.call_args.args[0]
    conn.commit.assert_called_once()


def test_wrong_day_fails_before_mutation():
    conn = MagicMock()
    provider = MagicMock()
    provider.get_verified_full_day_suspensions_df.return_value = pd.DataFrame({
        "ts_code": ["601611.SH"], "trade_date": ["2016-06-29"],
    })

    with pytest.raises(ValueError, match="SUSPENSION_SOURCE_INVALID_KEYS"):
        collect_suspension_source_day(conn, provider, date(2016, 6, 30))
    conn.execute.assert_not_called()
