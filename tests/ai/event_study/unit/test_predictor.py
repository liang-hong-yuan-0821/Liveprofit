# test-catalog-begin
# {
#   "purpose": "事件研究 / predictor（事件预测）：Event-study mock/unit checks; no service fixtures execute here.",
#   "keywords": [
#     "事件研究",
#     "接口",
#     "数据完整性",
#     "事件预测",
#     "predictor",
#     "api",
#     "coverage"
#   ],
#   "covers": [
#     "AI/eventStudy/prediction/predictor.py",
#     "AI/eventStudy/prediction/similarity_search.py",
#     "AI/eventStudy/review/news_dao.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

"""Event-study mock/unit checks; no service fixtures execute here."""
import json

import uuid

from datetime import datetime, timezone

from decimal import Decimal

from types import SimpleNamespace

import pytest

from AI.eventStudy.prediction import predictor, similarity_search

from AI.eventStudy.review import news_dao
from tests.ai.event_study.support.predictor import (
    _PRED_TEST_DB,
    _AS_OF,
    _EMB,
    _VEC_LITERAL,
    FakeCursor,
    FakeConn,
    _conn_with,
    _tpl_calls,
    _with_dbname,
    predict_db,
    db,
    _insert_event,
    _insert_impact,
    _predict,
)

def test_predict_merge_weighted(monkeypatch):
    """模板 2 样本（权重 1.0）+ 向量补充 2 样本（相似度×0.5）加权融合。"""
    monkeypatch.setattr(predictor, "encode_text", lambda text: [0.1] * 1024)
    all_sims = [
        {"event_id": 101, "title": "类似事件A", "similarity": 0.6,
         "event_type": "宏观", "event_subtype": "CPI", "event_condition": "超预期",
         "announced_at": "2026-01-01", "importance": 4},
        {"event_id": 102, "title": "类似事件B", "similarity": 0.8,
         "event_type": "央行", "event_subtype": "降准", "event_condition": "利好",
         "announced_at": "2026-01-02", "importance": 5},
        {"event_id": 103, "title": "低相似事件", "similarity": 0.3,
         "event_type": "地缘", "event_subtype": None, "event_condition": None,
         "announced_at": "2026-01-03", "importance": 2},
    ]

    def fake_search(conn, emb, **kw):
        # 模拟真实检索层行为：相似度低于下限不返回
        min_sim = kw.get("min_similarity", 0.5)
        return [s for s in all_sims if s["similarity"] >= min_sim]

    monkeypatch.setattr(predictor.similarity_search, "search_similar_events", fake_search)
    conn = _conn_with(
        template_cars=[0.02, 0.01],
        supplement_impacts={
            101: [(0.04, 1, False)],
            102: [(-0.02, -1, False)],
            103: [(0.5, 1, False)],  # 相似度 0.3 应被过滤，不出现
        },
    )
    result = predictor.predict_impact(
        conn, "某宏观事件", "000300.SH", window_type="post_event_5d",
        event_type="宏观", event_subtype="CPI", event_condition="超预期",
    )
    # 加权 CAR = (0.02+0.01 + 0.3*0.04 + 0.4*(-0.02)) / (2 + 0.7)
    expected = (0.03 + 0.012 - 0.008) / 2.7
    pred = result["prediction"]
    assert pred["predicted_return"] == pytest.approx(expected, abs=1e-4)
    assert pred["predicted_direction"] == 1
    # 置信度：n=4, 胜率=(1.0*2+0.5*2)/4=0.75 → 0.5*0.2+0.5*0.5=0.35
    assert pred["confidence"] == pytest.approx(0.35, abs=1e-3)
    assert result["template_stats"]["sample_count"] == 2
    assert result["template_stats"]["win_rate"] == 1.0
    assert [s["event_id"] for s in result["supplement_events"]] == [101, 102]
    assert result["supplement_events"][0]["weight"] == pytest.approx(0.3)
    assert result["sample_metadata"]["history_match_status"] == "ok"

def test_predict_empty_library(monkeypatch):
    """事件库无模板样本且无相似事件 → 中性 + 明确提示 + no_sample。"""
    monkeypatch.setattr(predictor, "encode_text", lambda text: [0.1] * 1024)
    monkeypatch.setattr(
        predictor.similarity_search, "search_similar_events",
        lambda conn, emb, **kw: [],
    )
    conn = _conn_with(template_cars=[], supplement_impacts={})
    result = predictor.predict_impact(conn, "新事件", "000001.SH",
                                      event_type="宏观", event_subtype="PMI",
                                      event_condition="符合预期")
    assert result["prediction"]["predicted_direction"] == 0
    assert result["prediction"]["predicted_return"] is None
    assert result["prediction"]["confidence"] == 0.0
    assert "暂无相似" in result["note"]
    md = result["sample_metadata"]
    assert md["sample_count"] == 0
    assert md["history_match_status"] == "no_sample"
    assert md["unavailable_channels"] == []

def test_predict_template_undefined_uses_vector_only(monkeypatch):
    """模板标签全空 → 模板样本为空，仅向量补充。"""
    monkeypatch.setattr(predictor, "encode_text", lambda text: [0.1] * 1024)
    monkeypatch.setattr(
        predictor.similarity_search, "search_similar_events",
        lambda conn, emb, **kw: [
            {"event_id": 201, "title": "相似", "similarity": 0.9,
             "event_type": "宏观", "event_subtype": "CPI", "event_condition": "超预期",
             "announced_at": "2026-02-01", "importance": 4},
        ],
    )
    conn = _conn_with(template_cars=[], supplement_impacts={201: [(0.05, 1, False)]})
    result = predictor.predict_impact(conn, "新事件", "000688.SH")
    assert result["template_stats"]["sample_count"] == 0
    pred = result["prediction"]
    assert pred["predicted_return"] == pytest.approx(0.05)
    assert pred["predicted_direction"] == 1
    # 模板查询不应被触发（模板未定义）
    assert result["supplement_events"][0]["weight"] == pytest.approx(0.45)
    assert not _tpl_calls(conn), "模板未定义时不应发起模板查询"

def test_judge_near_zero_neutral():
    assert predictor._judge(0.002) == 1
    assert predictor._judge(-0.002) == -1
    assert predictor._judge(0.0005) == 0
    assert predictor._judge(-0.0009) == 0

def test_defaults_keep_legacy_behavior(monkeypatch):
    """不传 as_of / event_scope：模板与向量通道均不加时间/作用域过滤。"""
    captured = {}

    def fake_search(conn, emb, **kw):
        captured.update(kw)
        return []

    monkeypatch.setattr(predictor, "encode_text", lambda text: [0.1] * 1024)
    monkeypatch.setattr(predictor.similarity_search, "search_similar_events", fake_search)
    conn = _conn_with(template_cars=[0.02], supplement_impacts={})
    result = predictor.predict_impact(conn, "事件", "000300.SH",
                                      event_type="宏观", event_subtype="CPI",
                                      event_condition="超预期")
    sql, params = _tpl_calls(conn)[-1]
    assert "event_scope" not in sql and "announced_at" not in sql
    assert captured["before_ts"] is None and captured["scope"] is None
    md = result["sample_metadata"]
    assert md["as_of"] is None and md["event_scope"] is None and md["scope_refs"] == []
    assert md["history_match_status"] == "ok"

def test_build_scope_filter_grammar():
    assert similarity_search.build_scope_filter(None) == ("", [])
    assert similarity_search.build_scope_filter("") == ("", [])
    assert similarity_search.build_scope_filter("market") == (
        "(event_scope = %s OR event_scope IS NULL)", ["market"],
    )
    assert similarity_search.build_scope_filter("sector", ["801080"]) == (
        "event_scope = %s AND affected_scope_refs ?| %s::text[]",
        ["sector", ["SW:801080"]],
    )
    # 并集：多目标一次命中；裸码/大小写经同一归一化
    assert similarity_search.build_scope_filter("sector", "801080, bk1753.dc", qualifier="e") == (
        "e.event_scope = %s AND e.affected_scope_refs ?| %s::text[]",
        ["sector", ["SW:801080", "CONCEPT:BK1753.DC"]],
    )
    # 空路由：恒假谓词（零样本，不落到全量事件）
    assert similarity_search.build_scope_filter("stock", []) == (
        similarity_search.EMPTY_ROUTE_PREDICATE, [],
    )

@pytest.mark.parametrize("scope,refs", [
    ("GLOBAL", ()),
    ("sector", ["XYZ"]),
    ("sector", ["801080", "JUNK"]),  # 部分非法：不静默丢弃
    ("market", ["SW:801080"]),       # market 固定空引用
])
def test_build_scope_filter_rejects(scope, refs):
    with pytest.raises(ValueError):
        similarity_search.build_scope_filter(scope, refs)

def test_route_predicate_applied_to_both_channels(monkeypatch):
    """event_scope/scope_refs 同时作用于模板查询与向量检索（SQL/参数契约）。"""
    captured = {}

    def fake_search(conn, emb, **kw):
        captured.update(kw)
        return []

    monkeypatch.setattr(predictor, "encode_text", lambda text: [0.1] * 1024)
    monkeypatch.setattr(predictor.similarity_search, "search_similar_events", fake_search)
    conn = _conn_with(template_cars=[0.02], supplement_impacts={})
    predictor.predict_impact(
        conn, "事件", "000300.SH",
        event_type="宏观", event_subtype="CPI", event_condition="超预期",
        as_of=_AS_OF, event_scope="sector", scope_refs=["801080"],
    )
    sql, params = _tpl_calls(conn)[-1]
    assert "e.announced_at <= %s::timestamptz" in sql
    assert "e.event_scope = %s AND e.affected_scope_refs ?| %s::text[]" in sql
    assert params[-3:] == [_AS_OF, "sector", ["SW:801080"]]
    assert captured["before_ts"] == _AS_OF
    assert captured["scope"] == "sector" and captured["scope_refs"] == ["801080"]

def test_invalid_route_raises_before_any_query(monkeypatch):
    """非法路由 = 调用方编程错误：抛 ValueError，不静默降级为空样本。"""
    monkeypatch.setattr(predictor, "encode_text", lambda text: [0.1] * 1024)
    conn = _conn_with(template_cars=[0.02], supplement_impacts={})
    with pytest.raises(ValueError):
        predictor.predict_impact(conn, "事件", "000300.SH", event_type="宏观",
                                 event_scope="GLOBAL")
    with pytest.raises(ValueError):
        predictor.predict_impact(conn, "事件", "000300.SH", event_type="宏观",
                                 event_scope="sector", scope_refs=["JUNK"])
    assert conn.executed == []

def test_metadata_field_contract(monkeypatch):
    monkeypatch.setattr(predictor, "encode_text", lambda text: [0.1] * 1024)
    monkeypatch.setattr(predictor.similarity_search, "search_similar_events",
                        lambda conn, emb, **kw: [])
    conn = _conn_with(template_cars=[0.02], supplement_impacts={})
    md = predictor.predict_impact(conn, "事件", "000300.SH", event_type="宏观",
                                  event_subtype="CPI", event_condition="超预期",
                                  as_of=_AS_OF, event_scope="sector",
                                  scope_refs=["801080"])["sample_metadata"]
    assert set(md) == {
        "as_of", "event_scope", "scope_refs", "sample_count",
        "template_sample_count", "supplement_sample_count",
        "contaminated_sample_count", "contaminated",
        "history_match_status", "unavailable_channels", "reason",
    }
    assert md["as_of"] == _AS_OF
    assert md["event_scope"] == "sector" and md["scope_refs"] == ["SW:801080"]
    assert md["sample_count"] == 1 and md["template_sample_count"] == 1
    assert md["supplement_sample_count"] == 0
    assert md["contaminated"] is False and md["contaminated_sample_count"] == 0

def test_coverage_missing_vs_no_sample(monkeypatch):
    """已登记但无样本 → no_sample；未登记资产 → coverage_missing（首期仅 4 指数）。"""
    monkeypatch.setattr(predictor, "encode_text", lambda text: [0.1] * 1024)
    monkeypatch.setattr(predictor.similarity_search, "search_similar_events",
                        lambda conn, emb, **kw: [])
    registered = _conn_with(template_cars=[], supplement_impacts={}, registered=True)
    md = predictor.predict_impact(registered, "事件", "000300.SH",
                                  event_type="宏观")["sample_metadata"]
    assert md["history_match_status"] == "no_sample" and md["reason"]

    missing = _conn_with(template_cars=[], supplement_impacts={}, registered=False)
    md = predictor.predict_impact(missing, "事件", "801080.SI",
                                  event_type="宏观")["sample_metadata"]
    assert md["history_match_status"] == "coverage_missing"
    assert "801080.SI" in md["reason"]

def test_api_unavailable_distinguishable_from_no_sample(monkeypatch):
    """查询通道失败 → api_unavailable（含失败通道清单），不得误报冷启动。"""
    class BrokenConn(FakeConn):
        def execute(self, sql, params=None):
            raise RuntimeError("connection closed")

    conn = BrokenConn(lambda sql, params=None: [])
    monkeypatch.setattr(predictor, "encode_text", lambda text: [0.1] * 1024)
    result = predictor.predict_impact(conn, "事件", "000300.SH",
                                      event_type="宏观", event_subtype="CPI",
                                      event_condition="超预期")
    md = result["sample_metadata"]
    assert md["history_match_status"] == "api_unavailable"
    assert md["sample_count"] == 0
    # 模板查询与相似事件检索均失败（相似事件检索失败归属 vector 通道）
    assert set(md["unavailable_channels"]) == {"template", "vector"}
    assert "不可用" in md["reason"]

def test_supplement_channel_failure_recorded(monkeypatch):
    """相似样本影响查询失败 → api_unavailable 且 channels 标注 supplement。"""
    class BrokenImpactConn(FakeConn):
        def execute(self, sql, params=None):
            if "ei.direction" in sql:
                raise RuntimeError("impact query failed")
            return FakeCursor([])

    monkeypatch.setattr(predictor, "encode_text", lambda text: [0.1] * 1024)
    monkeypatch.setattr(
        predictor.similarity_search, "search_similar_events",
        lambda conn, emb, **kw: [
            {"event_id": 401, "title": "相似", "similarity": 0.9,
             "event_type": "地缘", "event_subtype": None, "event_condition": None,
             "announced_at": "2026-02-01", "importance": 4},
        ],
    )
    md = predictor.predict_impact(
        BrokenImpactConn(lambda sql, params=None: []), "事件", "000300.SH",
        event_type="宏观",
    )["sample_metadata"]
    assert md["history_match_status"] == "api_unavailable"
    assert md["unavailable_channels"] == ["supplement"]

def test_vector_model_unavailable_is_api_unavailable(monkeypatch):
    """向量模型不可用且无模板样本 → api_unavailable（附带说明），不误报 no_sample。"""
    monkeypatch.setattr(predictor, "encode_text", lambda text: [])
    conn = _conn_with(template_cars=[], supplement_impacts={}, registered=True)
    md = predictor.predict_impact(conn, "事件", "000300.SH",
                                  event_type="宏观")["sample_metadata"]
    assert md["history_match_status"] == "api_unavailable"
    assert md["unavailable_channels"] == ["vector"]
    # 模板通道有样本时状态仍为 ok（失败通道单独列出）
    conn2 = _conn_with(template_cars=[0.03], supplement_impacts={}, registered=True)
    md2 = predictor.predict_impact(conn2, "事件", "000300.SH",
                                   event_type="宏观")["sample_metadata"]
    assert md2["history_match_status"] == "ok"
    assert md2["unavailable_channels"] == ["vector"]

def test_contaminated_samples_flagged(monkeypatch):
    monkeypatch.setattr(predictor, "encode_text", lambda text: [])
    conn = _conn_with(template_cars=[], supplement_impacts={})

    def matcher(sql, params=None):
        if "FROM event_impacts" in sql:
            return [(0.02, True), (0.01, False)]
        if "SELECT 1 FROM assets WHERE ticker" in sql:
            return [(1,)]
        return None

    result = predictor.predict_impact(FakeConn(matcher), "事件", "000300.SH",
                                      event_type="宏观", event_subtype="CPI",
                                      event_condition="超预期")
    md = result["sample_metadata"]
    assert md["contaminated"] is True and md["contaminated_sample_count"] == 1
    assert md["history_match_status"] == "ok"
    assert "污染" in result["note"]

def test_supplement_contamination_counted(monkeypatch):
    monkeypatch.setattr(predictor, "encode_text", lambda text: [0.1] * 1024)
    monkeypatch.setattr(
        predictor.similarity_search, "search_similar_events",
        lambda conn, emb, **kw: [
            {"event_id": 301, "title": "相似", "similarity": 0.9,
             "event_type": "地缘", "event_subtype": None, "event_condition": None,
             "announced_at": "2026-02-01", "importance": 4},
        ],
    )
    conn = _conn_with(template_cars=[], supplement_impacts={301: [(0.05, 1, True)]})
    result = predictor.predict_impact(conn, "事件", "000300.SH")
    assert result["supplement_events"][0]["contaminated"] is True
    assert result["sample_metadata"]["contaminated_sample_count"] == 1

def test_exclude_event_id_pushed_into_both_channels(monkeypatch):
    """SQL 契约：候选 ID 下推到模板与向量两通道的排除条件（评审 m14）。"""
    monkeypatch.setattr(predictor, "encode_text", lambda text: _EMB)
    seen = {}

    def fake_search(conn, emb, **kw):
        seen.update(kw)
        return []

    monkeypatch.setattr(predictor.similarity_search, "search_similar_events",
                        fake_search)
    conn = _conn_with(template_cars=[0.02], supplement_impacts={})

    predictor.predict_impact(conn, "候选事件", "000300.SH", event_type="宏观",
                             event_subtype="CPI", event_condition="超预期",
                             as_of=_AS_OF, exclude_event_id=42)

    sql, params = _tpl_calls(conn)[0]
    assert "e.event_id != %s" in sql
    assert 42 in params
    assert seen["exclude_event_id"] == 42

    # 不传时不出现排除条件（旧行为不变）
    conn2 = _conn_with(template_cars=[0.02], supplement_impacts={})
    seen.clear()
    predictor.predict_impact(conn2, "候选事件", "000300.SH", event_type="宏观",
                             event_subtype="CPI", event_condition="超预期",
                             as_of=_AS_OF)
    sql2, _ = _tpl_calls(conn2)[0]
    assert "event_id !=" not in sql2
    assert seen["exclude_event_id"] is None

def test_as_of_bare_date_normalized_to_end_of_day():
    """裸日期 as_of 归一为当日 23:59:59.999999（Code Review 第 2 轮 finding 5）。"""
    assert predictor._as_of_sql_param("2026-01-15") == "2026-01-15 23:59:59.999999"
    assert predictor._as_of_sql_param("2026/01/15") == "2026-01-15 23:59:59.999999"
    assert predictor._as_of_sql_param("20260115") == "2026-01-15 23:59:59.999999"
    # 时分秒微秒全零的 datetime（`datetime.combine(d, time())` 日边界语义）→ 当日末
    assert predictor._as_of_sql_param(
        datetime(2026, 1, 15)) == "2026-01-15 23:59:59.999999"
    # 带时刻输入原样保留（对象直传，不扩大到当日末）
    dt = datetime(2026, 1, 15, 10, 0)
    assert predictor._as_of_sql_param(dt) is dt
    assert predictor._as_of_sql_param("2026-01-15T10:00:00") == "2026-01-15T10:00:00"
    # None / 空串 / 纯空白 → 不做时间过滤（调用方跳过该条件）
    assert predictor._as_of_sql_param(None) is None
    assert predictor._as_of_sql_param("") is None
    assert predictor._as_of_sql_param("   ") is None
