# test-catalog-begin
# {
#   "purpose": "证券数据 / suspension_status_db：Suspension ambiguity cannot leave a stale trusted status behind.",
#   "keywords": [
#     "证券数据",
#     "每日",
#     "数据库",
#     "新闻",
#     "事件推送",
#     "状态",
#     "个股分析",
#     "停牌",
#     "suspension_status_db",
#     "daily",
#     "db",
#     "news",
#     "sse",
#     "status",
#     "stock",
#     "suspension"
#   ],
#   "covers": [
#     "db/instrument/audit_history.py",
#     "db/instrument/dao/trade_status.py",
#     "db/instrument/db.py",
#     "db/instrument/ingest/suspension_evidence.py"
#   ],
#   "environment": [
#     "db"
#   ]
# }
# test-catalog-end

"""Suspension ambiguity cannot leave a stale trusted status behind."""

import json

import pytest

from db.instrument.audit_history import _SAMPLE_SQL, _STOCK_SQL
from db.instrument.dao.trade_status import remove_ambiguous_tushare_status
from db.instrument.db import get_connection
from db.instrument.ingest.suspension_evidence import (
    _official_notice_url,
    import_evidence,
)


def test_official_notice_url_accepts_sse_listing_notice_only():
    assert _official_notice_url(
        "https://www.sse.com.cn/disclosure/announcement/listing/c/c_20180522_4559446.shtml"
    )
    assert not _official_notice_url("https://www.sse.com.cn/unrelated/page.shtml")
    assert not _official_notice_url("https://www.sse.com.cn/disclosure/announcement/listing/x.shtml?x=1")
    assert not _official_notice_url("https://www.sse.com.cn/disclosure/announcement/listing/../x.shtml")
    assert not _official_notice_url("http://www.sse.com.cn/disclosure/announcement/listing/x.shtml")


def test_official_notice_url_accepts_szse_news_evidence_only():
    assert _official_notice_url("https://www.szse.cn/aboutus/trends/news/t20181108_557449.html")
    assert _official_notice_url("https://www.szse.cn/disclosure/notice/t20201028_582723.html")
    assert not _official_notice_url("https://www.szse.cn/other/t20181108_557449.html")
    assert not _official_notice_url("https://www.szse.cn/aboutus/trends/news/t20181108_557449.html?x=1")


def test_official_notice_url_accepts_sse_listing_stock_notice():
    assert _official_notice_url(
        "https://www.sse.com.cn/disclosure/announcement/listing/stock/c/c_20190517_72734475.shtml"
    )
    assert not _official_notice_url(
        "https://www.sse.com.cn/disclosure/announcement/listing/other/c/c_20190517_72734475.shtml"
    )


def test_official_notice_url_accepts_sse_delisted_company_pdf():
    assert _official_notice_url(
        "https://www.sse.com.cn/services/information/delisting/c/600677_20210417_1.pdf"
    )
    assert not _official_notice_url(
        "https://www.sse.com.cn/services/information/delisting/c/600677_20210417_1.pdf?x=1"
    )
    assert not _official_notice_url(
        "https://www.sse.com.cn/services/information/other/c/600677_20210417_1.pdf"
    )


def test_fresh_ambiguous_events_remove_old_vendor_status(clean_market_state):
    with get_connection() as conn:
        assert conn.info.dbname == "liveprofit_instrument_test"
        conn.execute(
            "INSERT INTO market.trade_status_daily "
            "(ts_code,trade_date,is_suspended,is_st,market_board,source) VALUES "
            "('000001.SZ','2020-01-02',TRUE,FALSE,'MAIN','tushare')"
        )
        assert remove_ambiguous_tushare_status(
            conn, "2020-01-02", {"000001.SZ"},
        ) == 1
        assert conn.execute(
            "SELECT count(*) FROM market.trade_status_daily "
            "WHERE ts_code='000001.SZ' AND trade_date='2020-01-02'"
        ).fetchone()[0] == 0


def test_legacy_null_scope_does_not_hide_missing_daily(clean_market_state):
    with get_connection() as conn:
        assert conn.info.dbname == "liveprofit_instrument_test"
        conn.execute(
            "INSERT INTO market.instrument "
            "(ts_code,name,instrument_type,list_date) VALUES "
            "('000001.SZ','stock','stock','2020-01-01')"
        )
        conn.execute(
            "INSERT INTO market.trade_status_daily "
            "(ts_code,trade_date,is_suspended,is_st,market_board,source) VALUES "
            "('000001.SZ','2020-01-02',TRUE,FALSE,'MAIN','tushare')"
        )
        days = ["2020-01-02"]
        counts = conn.execute(_STOCK_SQL, (days,)).fetchone()
        assert counts[1:3] == (0, 1)
        assert conn.execute(_SAMPLE_SQL, (days, 5)).fetchall()[0][1] == "000001.SZ"


def test_official_full_day_evidence_explains_missing_bar(clean_market_state, tmp_path):
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps([{
        "ts_code": "000001.SZ", "trade_date": "2020-01-02",
        "scope": "full_day", "published_on": "2020-01-03",
        "source_url": "https://static.cninfo.com.cn/example.pdf",
    }]), encoding="utf-8")
    with get_connection() as conn:
        assert conn.info.dbname == "liveprofit_instrument_test"
        conn.execute(
            "INSERT INTO market.instrument "
            "(ts_code,name,instrument_type,list_date) VALUES "
            "('000001.SZ','stock','stock','2020-01-01')"
        )
        conn.commit()
        assert import_evidence(conn, path) == 1
        assert conn.execute(_STOCK_SQL, (["2020-01-02"],)).fetchone()[1:3] == (1, 0)
        assert conn.execute(_SAMPLE_SQL, (["2020-01-02"], 5)).fetchall() == []


def test_evidence_rejects_scope_disagreeing_with_bar(clean_market_state, tmp_path):
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps([{
        "ts_code": "000001.SZ", "trade_date": "2020-01-02",
        "scope": "full_day", "published_on": "2020-01-03",
        "source_url": "https://static.cninfo.com.cn/example.pdf",
    }]), encoding="utf-8")
    with get_connection() as conn:
        assert conn.info.dbname == "liveprofit_instrument_test"
        conn.execute(
            "INSERT INTO market.instrument "
            "(ts_code,name,instrument_type,list_date) VALUES "
            "('000001.SZ','stock','stock','2020-01-01')"
        )
        conn.execute(
            "INSERT INTO market.instrument_daily "
            "(ts_code,trade_date,open,high,low,close,source) VALUES "
            "('000001.SZ','2020-01-02',1,1,1,1,'test')"
        )
        conn.commit()
        with pytest.raises(ValueError, match="disagrees with daily bar"):
            import_evidence(conn, path)
