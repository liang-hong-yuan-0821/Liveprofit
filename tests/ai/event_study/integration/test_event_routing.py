# test-catalog-begin
# {
#   "purpose": "事件研究 / event_routing（事件路由）：Event-study checks against explicitly isolated PostgreSQL databases.",
#   "keywords": [
#     "事件研究",
#     "幂等",
#     "迁移",
#     "事件路由",
#     "表结构",
#     "持久化",
#     "event_routing",
#     "idempotent",
#     "migration",
#     "routing",
#     "schema",
#     "store"
#   ],
#   "covers": [
#     "AI/eventStudy/api/schemas.py",
#     "AI/eventStudy/db/connection.py",
#     "AI/eventStudy/review/review_dao.py"
#   ],
#   "environment": [
#     "db"
#   ]
# }
# test-catalog-end

"""Event-study checks against explicitly isolated PostgreSQL databases."""
import json

from datetime import datetime, timedelta, timezone

import pytest

from pydantic import ValidationError

from AI.eventStudy.api.schemas import PredictRequest

from AI.eventStudy.db import connection as db_connection

from AI.eventStudy.review import review_dao
from tests.ai.event_study.support.event_routing import (
    _CST,
    _ROUTE_TEST_DB,
    _AS_OF,
    _FakeCursor,
    _FakeConn,
    _route_row,
    _query,
    _with_dbname,
    routing_db,
    conn,
    _insert_event,
    _ids,
    _api_main,
    _ApiConn,
    _api_predict,
)

pytestmark = pytest.mark.requires_db

def test_init_schema_idempotent_and_route_columns(routing_db):
    """迁移可重复执行；路由列、B-tree 与 GIN 索引、行业码表就位。"""
    assert db_connection.init_schema(routing_db) is True
    assert db_connection.init_schema(routing_db) is True

    cols = {
        r[0] for r in routing_db.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'events'"
        ).fetchall()
    }
    assert {"event_scope", "affected_scope_refs"} <= cols

    indexdefs = {
        r[0]: (r[1] or "").lower() for r in routing_db.execute(
            "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'events'"
        ).fetchall()
    }
    assert "using gin" in indexdefs["idx_events_scope_refs"]
    assert "using btree" in indexdefs["idx_events_scope_announced"]
    assert "event_scope, announced_at" in indexdefs["idx_events_scope_announced"]

    codes = [
        r[0] for r in routing_db.execute(
            "SELECT industry_code FROM market.industry WHERE source = 'SW2021' "
            "ORDER BY industry_code"
        ).fetchall()
    ]
    assert len(codes) == 31 and codes[0] == "801010" and codes[-1] == "801980"

def test_migration_normalizes_legacy_rows(routing_db):
    """历史行（NULL）归一 market + []，重复迁移结果不变（确定性）。"""
    routing_db.execute("TRUNCATE events RESTART IDENTITY")
    routing_db.execute(
        "INSERT INTO events (title, announced_at, status, event_scope, affected_scope_refs) "
        "VALUES ('迁移前旧事件', '2026-08-01T09:00:00+08:00'::timestamptz, 'approved', NULL, NULL)"
    )
    routing_db.commit()

    assert db_connection.init_schema(routing_db) is True
    first = routing_db.execute(
        "SELECT event_scope, affected_scope_refs FROM events WHERE title = '迁移前旧事件'"
    ).fetchone()
    assert first == ("market", [])

    assert db_connection.init_schema(routing_db) is True
    second = routing_db.execute(
        "SELECT event_scope, affected_scope_refs FROM events WHERE title = '迁移前旧事件'"
    ).fetchone()
    assert second == ("market", [])

    # §归一后的旧行在市场层可见（不落空：旧事件仍参与路由）
    assert _ids(routing_db, "market") == [1]

def test_route_query_scope_isolation(conn):
    """同名事件按作用域/目标精确隔离（不误命中、不串层）。"""
    _insert_event(conn, "同名政策", "2026-08-01T09:00:00+08:00", "market", [])
    _insert_event(conn, "同名政策", "2026-08-02T09:00:00+08:00", "sector", ["SW:801080"])
    _insert_event(conn, "同名政策", "2026-08-03T09:00:00+08:00", "sector", ["SW:801750"])
    _insert_event(conn, "同名政策", "2026-08-04T09:00:00+08:00", "stock", ["stock:600519.SH"])
    conn.commit()

    assert _ids(conn, "market") == [1]
    assert _ids(conn, "sector", ["SW:801080"]) == [2]
    assert _ids(conn, "sector", ["SW:801750"]) == [3]
    assert _ids(conn, "stock", ["stock:600519.SH"]) == [4]
    # 跨层不串：sector 事件不因「同为 sector 层或代码形态相似」被 stock 路由命中
    assert _ids(conn, "stock", ["stock:801080.SH"]) == []

def test_route_query_bare_codes_and_isolation_by_code(conn):
    """裸代码可查询；不同代码互不命中（SW:801080 与 SW:801750 严格区分）。"""
    _insert_event(conn, "电子行业政策", "2026-08-02T09:00:00+08:00", "sector", ["SW:801080"])
    _insert_event(conn, "计算机行业政策", "2026-08-03T09:00:00+08:00", "sector", ["SW:801750"])
    conn.commit()

    assert _ids(conn, "sector", ["801080"]) == [1]
    assert _ids(conn, "sector", ["801750"]) == [2]
    assert _ids(conn, "sector", ["SW:801080"]) == [1]
    assert _ids(conn, "sector", ["SW:801760"]) == []  # 相邻代码不命中

def test_route_query_multi_target_is_union(conn):
    """route 多目标为并集：命中任一目标引用即属该路由（单目标等价包含查询）。"""
    _insert_event(conn, "电子政策", "2026-08-01T09:00:00+08:00", "sector", ["SW:801080"])
    _insert_event(conn, "电子+概念", "2026-08-02T09:00:00+08:00", "sector",
                  ["SW:801080", "CONCEPT:BK1753.DC"])
    _insert_event(conn, "计算机政策", "2026-08-03T09:00:00+08:00", "sector", ["SW:801750"])
    conn.commit()

    assert _ids(conn, "sector", ["SW:801080"]) == [2, 1]  # 降序：近事优先
    assert _ids(conn, "sector", ["CONCEPT:BK1753.DC"]) == [2]
    assert _ids(conn, "sector", ["SW:801080", "SW:801750"]) == [3, 2, 1]

def test_route_query_excludes_ignored_and_future(conn):
    """status/时点过滤：ignored 与 announced_at > as_of 的事件排除（防前视）。"""
    as_of = "2026-08-31T10:00:00+08:00"
    _insert_event(conn, "边界前", "2026-08-31T09:59:59+08:00", "market", [])
    _insert_event(conn, "恰在时点", as_of, "market", [])
    _insert_event(conn, "时点之后", "2026-08-31T10:00:01+08:00", "market", [])
    _insert_event(conn, "被忽略", "2026-08-30T09:00:00+08:00", "market", [], status="ignored")
    _insert_event(conn, "未来行业事件", "2026-09-02T09:00:00+08:00", "sector", ["SW:801080"])
    conn.commit()

    assert [r["title"] for r in _query(conn, "market", as_of=as_of)] == ["恰在时点", "边界前"]
    assert _ids(conn, "sector", ["SW:801080"], as_of=as_of) == []

def test_route_query_limit_and_order(conn):
    for i in range(3):
        _insert_event(conn, f"市场事件{i}", f"2026-08-0{i + 1}T09:00:00+08:00", "market", [])
    conn.commit()

    assert [r["title"] for r in _query(conn, "market", limit=2)] == ["市场事件2", "市场事件1"]
    assert len(_query(conn, "market", limit=3)) == 3

def test_route_query_returns_normalized_fields(conn):
    _insert_event(conn, "电子政策", "2026-08-02T09:00:00+08:00", "sector", ["SW:801080"])
    # 历史 NULL 行（迁移前形态）在读取侧同样归一出 event_scope/refs
    _insert_event(conn, "历史行", "2026-08-03T09:00:00+08:00", None, None)
    conn.commit()
    row = _query(conn, "sector", ["SW:801080"])[0]
    assert row["event_scope"] == "sector"
    assert row["affected_scope_refs"] == ["SW:801080"]
    assert row["announced_at"].startswith("2026-08-02T09:00:00")
    assert set(row) >= {
        "event_id", "title", "content", "event_type", "event_subtype",
        "event_condition", "announced_at", "trading_day", "importance",
        "event_scope", "affected_scope_refs", "source_url",
    }

def test_insert_event_persists_normalized_route_fields(conn):
    """写入即归一（大小写/裸码/前缀统一），随后可被同路由查询命中。"""
    draft = {"title": "电子行业政策", "content": "", "announced_at": "2026-08-02T09:00:00+08:00",
             "source_url": None, "importance_hint": None}
    review = {"event_scope": "sector", "affected_scope_refs": ["801080", "BK1753.DC"],
              "importance": 4}
    event_id = review_dao._insert_event(conn, draft, review, "approved")

    stored = conn.execute(
        "SELECT event_scope, affected_scope_refs FROM events WHERE event_id = %s", (event_id,)
    ).fetchone()
    assert stored == ("sector", ["SW:801080", "CONCEPT:BK1753.DC"])
    assert _ids(conn, "sector", ["SW:801080"]) == [event_id]
    assert _ids(conn, "sector", ["CONCEPT:BK1753.DC"]) == [event_id]

def test_check_scope_fields_against_real_store_tables(conn):
    """存在性校验对真实 store 表：行业码表 / stock_basic / concept(source=dc)。"""
    ok = {
        "sector": [["801080"], ["CONCEPT:BK1753.DC"], ["SW:801080", "CONCEPT:BK1753.DC"]],
        "stock": [["600519.SH"], ["000001.SZ"]],
        "market": [[]],
    }
    for scope, ref_lists in ok.items():
        for refs in ref_lists:
            assert review_dao.check_scope_fields(
                {"event_scope": scope, "affected_scope_refs": refs}, conn
            ) is None, (scope, refs)

    missing = {
        "sector": [["SW:999999"], ["CONCEPT:BK9999.DC"], ["SW:801080", "SW:999999"]],
        "stock": [["600519.SZ"], ["300750.SZ"]],
    }
    for scope, ref_lists in missing.items():
        for refs in ref_lists:
            msg = review_dao.check_scope_fields(
                {"event_scope": scope, "affected_scope_refs": refs}, conn
            )
            assert msg is not None and "目标引用不存在" in msg, (scope, refs)

    # 同花顺来源的概念代码不算命中（存在性锁定 source='dc'）
    msg = review_dao.check_scope_fields(
        {"event_scope": "sector", "affected_scope_refs": ["CONCEPT:BK9001.DC"]}, conn
    )
    assert msg is not None and "目标引用不存在" in msg

def test_approve_event_validates_scope_and_persists(conn, monkeypatch):
    """approve_event：幻化引用拦为行级失败（不落库），合法引用落库后可被路由命中。"""
    monkeypatch.setattr(review_dao, "_delete_draft", lambda draft_id: None)
    monkeypatch.setattr(review_dao, "_log_review", lambda *a, **kw: None)
    draft = {"draft_id": 7, "title": "电子行业政策", "content": "...",
             "announced_at": "2026-08-02T09:00:00+08:00", "source_url": None,
             "importance_hint": None}
    monkeypatch.setattr(review_dao, "get_pending_event", lambda draft_id: dict(draft))

    with pytest.raises(review_dao.EventScopeValidationError):
        review_dao.approve_event(
            conn, 7, {"event_scope": "sector", "affected_scope_refs": ["SW:999999"]}
        )
    assert conn.execute("SELECT count(*) FROM events").fetchone()[0] == 0

    event_id = review_dao.approve_event(
        conn, 7, {"event_scope": "sector", "affected_scope_refs": ["801080"], "importance": 4}
    )
    assert _ids(conn, "sector", ["SW:801080"]) == [event_id]
