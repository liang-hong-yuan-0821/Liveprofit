# test-catalog-begin
# {
#   "purpose": "事件研究 / event_routing（事件路由）：Event-study mock/unit checks; no service fixtures execute here.",
#   "keywords": [
#     "事件研究",
#     "市场分析",
#     "事件路由",
#     "板块分析",
#     "event_routing",
#     "market",
#     "routing",
#     "sector"
#   ],
#   "covers": [
#     "AI/eventStudy/api/schemas.py",
#     "AI/eventStudy/db/connection.py",
#     "AI/eventStudy/review/review_dao.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

"""Event-study mock/unit checks; no service fixtures execute here."""
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

def test_normalize_scope_converges_three_values():
    assert review_dao.normalize_scope(" market ") == "market"
    assert review_dao.normalize_scope("Sector") == "sector"
    assert review_dao.normalize_scope("STOCK") == "stock"
    assert review_dao.normalize_scope("GLOBAL") is None
    assert review_dao.normalize_scope("") is None
    assert review_dao.normalize_scope(None) is None

def test_normalize_scope_ref_grammar():
    # 行业：显式前缀 / 裸码 / .SI 后缀 / 大小写
    assert review_dao.normalize_scope_ref("SW:801080") == "SW:801080"
    assert review_dao.normalize_scope_ref(" sw:801080 ") == "SW:801080"
    assert review_dao.normalize_scope_ref("SW:801080.SI") == "SW:801080"
    assert review_dao.normalize_scope_ref("801080") == "SW:801080"
    assert review_dao.normalize_scope_ref("801080.SI") == "SW:801080"
    # 概念：东财 dc 代码（.TI 同花顺不在体系内）
    assert review_dao.normalize_scope_ref("CONCEPT:BK1753.DC") == "CONCEPT:BK1753.DC"
    assert review_dao.normalize_scope_ref("bk1753.dc") == "CONCEPT:BK1753.DC"
    assert review_dao.normalize_scope_ref("BK1753.DC") == "CONCEPT:BK1753.DC"
    assert review_dao.normalize_scope_ref("CONCEPT:BK1753.TI") is None
    # 个股：必须带交易所后缀（沪/深/北）
    assert review_dao.normalize_scope_ref("stock:600519.SH") == "stock:600519.SH"
    assert review_dao.normalize_scope_ref("600519.SH") == "stock:600519.SH"
    assert review_dao.normalize_scope_ref("000001.sz") == "stock:000001.SZ"
    assert review_dao.normalize_scope_ref("830799.BJ") == "stock:830799.BJ"
    # 非法：前缀与形态不符 / 裸股票码缺后缀（不猜交易所）/ 无法识别
    assert review_dao.normalize_scope_ref("stock:801080") is None
    assert review_dao.normalize_scope_ref("600519") is None
    assert review_dao.normalize_scope_ref("600519.XX") is None
    assert review_dao.normalize_scope_ref("XYZ") is None
    assert review_dao.normalize_scope_ref("") is None
    assert review_dao.normalize_scope_ref(None) is None

def test_normalize_scope_refs_dedupes_and_preserves_order():
    refs = review_dao.normalize_scope_refs(
        ["801080", "SW:801080", " bk1753.dc ", "BK1753.DC", "stock:600519.SH"]
    )
    assert refs == ["SW:801080", "CONCEPT:BK1753.DC", "stock:600519.SH"]
    # 字符串输入按中英文逗号/分号/换行切分；空项丢弃
    assert review_dao.normalize_scope_refs("SW:801080，\nBK1753.DC；") == [
        "SW:801080", "CONCEPT:BK1753.DC",
    ]
    assert review_dao.normalize_scope_refs(None) == []
    assert review_dao.normalize_scope_refs("  ") == []

def test_resolve_scope_fields_happy_paths():
    assert review_dao.resolve_scope_fields({}) == ("market", [])
    assert review_dao.resolve_scope_fields({"event_scope": "market", "affected_scope_refs": []}) == (
        "market", [],
    )
    assert review_dao.resolve_scope_fields(
        {"event_scope": "sector", "affected_scope_refs": ["801080", "BK1753.DC"]}
    ) == ("sector", ["SW:801080", "CONCEPT:BK1753.DC"])
    assert review_dao.resolve_scope_fields(
        {"event_scope": "stock", "affected_scope_refs": ["600519.SH"]}
    ) == ("stock", ["stock:600519.SH"])

@pytest.mark.parametrize("review,fragment", [
    ({"event_scope": "GLOBAL"}, "作用域非法"),
    ({"event_scope": "market", "affected_scope_refs": ["SW:801080"]}, "market 作用域不允许目标引用"),
    ({"event_scope": "sector", "affected_scope_refs": []}, "至少需要一个目标引用"),
    ({"event_scope": "stock", "affected_scope_refs": []}, "至少需要一个目标引用"),
    ({"event_scope": "stock", "affected_scope_refs": ["SW:801080"]}, "stock 作用域仅支持个股引用"),
    ({"event_scope": "sector", "affected_scope_refs": ["600519.SH"]}, "sector 作用域不支持个股引用"),
    ({"event_scope": "sector", "affected_scope_refs": ["600519"]}, "目标引用格式非法"),
    ({"event_scope": "sector", "affected_scope_refs": ["XYZ"]}, "目标引用格式非法"),
    ({"event_scope": "sector", "affected_scope_refs": ["SW:8010%02d" % i for i in range(21)]}, "超出上限"),
])
def test_resolve_scope_fields_rejects(review, fragment):
    with pytest.raises(review_dao.EventScopeValidationError) as exc:
        review_dao.resolve_scope_fields(review)
    assert fragment in str(exc.value)

def test_resolve_scope_fields_existence_check():
    class Existence:
        def __init__(self, missing):
            self.missing = set(missing)

        def missing_refs(self, refs):
            return [r for r in refs if r in self.missing]

    ok = review_dao.resolve_scope_fields(
        {"event_scope": "sector", "affected_scope_refs": ["801080"]},
        Existence([]),
    )
    assert ok == ("sector", ["SW:801080"])
    with pytest.raises(review_dao.EventScopeValidationError) as exc:
        review_dao.resolve_scope_fields(
            {"event_scope": "sector", "affected_scope_refs": ["801080", "801890"]},
            Existence(["SW:801890"]),
        )
    assert "目标引用不存在" in str(exc.value) and "SW:801890" in str(exc.value)

def test_normalize_scope_fields_is_lenient():
    """ignore 路径 / 表单回退：非法作用域与引用不拦，落 market + []。"""
    assert review_dao.normalize_scope_fields({}) == ("market", [])
    assert review_dao.normalize_scope_fields({"event_scope": "GLOBAL"}) == ("market", [])
    assert review_dao.normalize_scope_fields(
        {"event_scope": "market", "affected_scope_refs": ["SW:801080"]}
    ) == ("market", [])
    assert review_dao.normalize_scope_fields(
        {"event_scope": "sector", "affected_scope_refs": ["801080", "JUNK"]}
    ) == ("sector", ["SW:801080"])

def test_check_scope_fields_returns_message_without_raising():
    assert review_dao.check_scope_fields({"event_scope": "market"}) is None
    assert "作用域非法" in review_dao.check_scope_fields({"event_scope": "GLOBAL"})

    class BrokenConn:
        def execute(self, sql, params=None):
            raise RuntimeError("码表未迁移")

    # 存在性数据源不可用 → 同样拦为行级失败（不静默放行）
    msg = review_dao.check_scope_fields(
        {"event_scope": "sector", "affected_scope_refs": ["801080"]}, BrokenConn()
    )
    assert msg is not None and "存在性数据源不可用" in msg

def test_validation_error_is_not_draft_not_found():
    """行级失败异常类型独立：EventScopeValidationError 不是 ValueError 子类。"""
    assert not issubclass(review_dao.EventScopeValidationError, ValueError)
    assert issubclass(review_dao.EventScopeValidationError, Exception)

def test_route_query_market_sql_contract():
    conn = _FakeConn([_route_row(event_scope=None, affected_scope_refs=None)])
    rows = _query(conn, "market")
    sql, params = conn.executed[-1]
    assert "status = 'approved'" in sql
    assert "announced_at <= %s::timestamptz" in sql
    assert "event_scope = %s OR event_scope IS NULL" in sql
    assert "ORDER BY announced_at DESC" in sql
    assert params == (_AS_OF, "market", 20)
    # 历史 NULL 行读出即归一（下层消费方不需要再判空）
    assert rows[0]["event_scope"] == "market"
    assert rows[0]["affected_scope_refs"] == []

def test_route_query_sector_sql_contract_and_bare_code_normalization():
    conn = _FakeConn([_route_row(event_scope="sector", affected_scope_refs=["SW:801080"])])
    rows = _query(conn, "sector", ["801080"])
    sql, params = conn.executed[-1]
    assert "event_scope = %s" in sql
    assert "affected_scope_refs ?| %s::text[]" in sql  # 数组命中（并集）
    assert "@>" not in sql
    assert params == (_AS_OF, "sector", ["SW:801080"], 20)
    assert rows[0]["affected_scope_refs"] == ["SW:801080"]

def test_route_query_datetime_as_of_normalized():
    from datetime import datetime, timedelta, timezone

    conn = _FakeConn([])
    # 时分秒微秒全零（`datetime.combine(d, time())` 日边界语义）→ 归一为当日末（M5 口径统一）
    as_of = datetime(2026, 8, 31, 0, 0, tzinfo=timezone(timedelta(hours=8)))
    _query(conn, "market", (), as_of=as_of)
    assert conn.executed[-1][1][0] == "2026-08-31 23:59:59.999999"

    # 带时刻 → 原样保留（不扩大到当日末；tz-aware 保留偏移）
    stamp = datetime(2026, 8, 31, 10, 0, tzinfo=timezone(timedelta(hours=8)))
    _query(conn, "market", (), as_of=stamp)
    assert conn.executed[-1][1][0] == stamp.isoformat()

    # None / 空串 → 跳过时间条件（动态 SQL：条件整条不出现）
    for value in (None, "", "   "):
        conn = _FakeConn([])
        _query(conn, "market", (), as_of=value)
        sql, params = conn.executed[-1]
        assert "announced_at <= %s::timestamptz" not in sql
        assert params == ("market", 20)

def test_route_query_empty_targets_is_not_a_route():
    conn = _FakeConn([])
    assert _query(conn, "sector", ()) == []
    assert _query(conn, "stock", []) == []
    assert conn.executed == []  # 不落到全量事件，且不发查询

@pytest.mark.parametrize("scope,refs,limit", [
    ("GLOBAL", (), 20),
    ("sector", ["XYZ"], 20),
    ("market", (), 0),
])
def test_route_query_rejects_programmer_errors(scope, refs, limit):
    conn = _FakeConn([])
    with pytest.raises(ValueError):
        _query(conn, scope, refs, limit=limit)
    assert conn.executed == []

def test_predict_request_defaults_keep_legacy_calls():
    """as_of / event_scope 均可缺省，旧调用方字段集不变（兼容）。"""
    req = PredictRequest(event_text="事件", asset_ticker="000300.SH")
    assert req.as_of is None and req.event_scope is None
    assert req.window_type == "post_event_5d"
    assert req.save is False and req.event_id is None
    legacy = PredictRequest(**{
        "event_text": "事件", "asset_ticker": "000300.SH", "event_type": "宏观",
        "event_subtype": "CPI", "event_condition": "超预期", "save": True, "event_id": 3,
    })
    assert legacy.event_scope is None and legacy.as_of is None

@pytest.mark.parametrize("scope", ["market", "sector", "stock"])
def test_predict_request_accepts_three_scopes(scope):
    assert PredictRequest(event_text="e", asset_ticker="000300.SH",
                          event_scope=scope).event_scope == scope

def test_predict_request_rejects_invalid_scope():
    with pytest.raises(ValidationError):
        PredictRequest(event_text="e", asset_ticker="000300.SH", event_scope="GLOBAL")

def test_predict_endpoint_defaults_as_of_to_event_time(monkeypatch):
    """as_of 缺省 = 事件自身时间：按 event_id 取 announced_at 后传给预测器。"""
    announced = datetime(2026, 8, 31, 10, 0, tzinfo=_CST)
    conn, captured, result = _api_predict(
        monkeypatch, announced_at=announced, event_id=7, event_scope="market",
    )
    assert captured["as_of"] == announced.isoformat()
    assert captured["event_scope"] == "market"
    assert captured["asset_ticker"] == "000300.SH"
    assert "sample_metadata" in result  # 样本元数据随响应透传（供预取器降级）
    assert [p for _, p in conn.executed] == [(7,)]

def test_predict_endpoint_explicit_as_of_wins_over_event_time(monkeypatch):
    conn, captured, _ = _api_predict(
        monkeypatch, announced_at=datetime(2026, 8, 31, 10, 0, tzinfo=_CST),
        event_id=7, as_of="2026-01-01T00:00:00+08:00",
    )
    assert captured["as_of"] == "2026-01-01T00:00:00+08:00"
    assert conn.executed == [], "显式 as_of 时不应查询事件时间"

def test_predict_endpoint_without_as_of_or_event_id_keeps_no_filter(monkeypatch):
    """两者均缺省 → as_of=None（不限定时间）与 event_scope=None（不限定作用域）。"""
    conn, captured, _ = _api_predict(monkeypatch)
    assert captured["as_of"] is None and captured["event_scope"] is None
    assert conn.executed == []

def test_predict_endpoint_unknown_event_id_fails_loudly(monkeypatch):
    """event_id 无对应事件 → 404（不静默丢弃时间过滤，避免混入未来样本）。"""
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        _api_predict(monkeypatch, announced_at=None, event_id=999)
    assert exc.value.status_code == 404
    assert "as_of" in exc.value.detail

def test_predict_endpoint_invalid_window_type_422(monkeypatch):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        _api_predict(monkeypatch, window_type="post_event_3d")
    assert exc.value.status_code == 422
