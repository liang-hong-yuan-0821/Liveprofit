# test-catalog-begin
# {
#   "purpose": "数据采集 / etf_catalog：An ETF classification may be used only after its real observation time.",
#   "keywords": [
#     "数据采集",
#     "ETF",
#     "来源观测",
#     "etf_catalog",
#     "etf",
#     "observation"
#   ],
#   "covers": [
#     "db/instrument/dao/etf_catalog.py",
#     "db/instrument/ingest/etf_catalog.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

"""An ETF classification may be used only after its real observation time."""

from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from db.instrument.dao.etf_catalog import query_as_of
from db.instrument.ingest.etf_catalog import _collect_etf_catalog_unlocked


def _catalog():
    return pd.DataFrame({
        "ts_code": ["510300.SH", "513100.SH"],
        "index_code": ["000300.SH", "NDX.GI"],
        "exchange": ["SSE", "SSE"],
        "etf_type": ["股票型", "QDII"],
        "list_date": ["2012-05-28", "2013-05-15"],
        "list_status": ["L", "L"],
    })


def test_catalog_records_actual_observation_time_without_backdating():
    conn, provider = MagicMock(), MagicMock()
    provider.get_etf_basic_df.return_value = _catalog()
    moment = datetime(2026, 9, 24, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    with patch("db.instrument.ingest.etf_catalog.insert_observation", return_value=2) as insert:
        result = _collect_etf_catalog_unlocked(conn, provider, observed_at=moment)
    frame = insert.call_args.args[1]
    assert result["rows"] == 2
    assert frame["observed_date"].tolist() == [moment.date()] * 2
    assert frame["available_at"].tolist() == [pd.Timestamp(moment).tz_convert("UTC")] * 2
    conn.commit.assert_called_once()


def test_conflicting_catalog_cannot_be_saved():
    conn, provider = MagicMock(), MagicMock()
    provider.get_etf_basic_df.return_value = pd.concat([_catalog(), _catalog().iloc[[0]]])
    with (patch("db.instrument.ingest.etf_catalog.insert_observation") as insert,
          pytest.raises(ValueError, match="INVALID_IDENTITY")):
        _collect_etf_catalog_unlocked(conn, provider)
    insert.assert_not_called()
    conn.commit.assert_not_called()


def test_catalog_query_requires_explicit_instant_and_returns_latest_observation():
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = [
        ("510300.SH", "000300.SH", "SSE", "股票型", "2012-05-28", "L",
         datetime(2026, 9, 24, 8, 0, tzinfo=ZoneInfo("UTC"))),
    ]
    with pytest.raises(ValueError, match="timezone"):
        query_as_of(conn, datetime.fromisoformat("2026-09-24T00:00:00"))
    cutoff = datetime(2026, 9, 24, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    result = query_as_of(conn, cutoff)
    assert result["ts_code"].tolist() == ["510300.SH"]
    assert conn.execute.call_args.args[1] == (cutoff,)
