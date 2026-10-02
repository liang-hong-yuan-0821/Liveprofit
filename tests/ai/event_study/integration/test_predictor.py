# test-catalog-begin
# {
#   "purpose": "事件研究 / predictor（事件预测）：Event-study checks against explicitly isolated PostgreSQL databases.",
#   "keywords": [
#     "事件研究",
#     "数据完整性",
#     "数据库",
#     "历史审计",
#     "市场分析",
#     "事件预测",
#     "表结构",
#     "板块分析",
#     "个股分析",
#     "predictor",
#     "coverage",
#     "db",
#     "history",
#     "market",
#     "schema",
#     "sector",
#     "stock"
#   ],
#   "covers": [
#     "AI/eventStudy/db/connection.py",
#     "AI/eventStudy/prediction/predictor.py",
#     "AI/eventStudy/prediction/similarity_search.py",
#     "AI/eventStudy/processing/event_vectorizer.py",
#     "AI/eventStudy/review/news_dao.py",
#     "backend/modules/daily_research/application/quant_pipeline.py"
#   ],
#   "environment": [
#     "db"
#   ]
# }
# test-catalog-end

"""Event-study checks against explicitly isolated PostgreSQL databases."""
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

pytestmark = pytest.mark.requires_db

def test_assessment_candidates_freeze_history_and_keep_disputes_from_hiding_acceptance(db, monkeypatch):
    published_at = datetime(2026, 8, 31, 1, 0, tzinfo=timezone.utc)
    available_at = datetime(2026, 8, 31, 2, 0, tzinfo=timezone.utc)
    news = news_dao.persist_news(db, {
        "source": "test", "source_item_id": "snapshot-1", "title": "原始来源标题",
        "raw_content": "原始来源正文", "published_at": published_at,
    }, first_seen_at=available_at)
    replayed_news = news_dao.persist_news(db, {
        "source": "test", "source_item_id": "snapshot-1", "title": "原始来源标题",
        "raw_content": "原始来源正文", "published_at": published_at,
    }, first_seen_at=available_at)
    revised_news = news_dao.persist_news(db, {
        "source": "test", "source_item_id": "snapshot-1", "title": "修订来源标题",
        "raw_content": "修订来源正文", "published_at": published_at,
    }, first_seen_at=available_at)
    assert replayed_news.news_id == news.news_id and not replayed_news.inserted
    assert revised_news.source_revision == 2 and revised_news.inserted
    event_id, *_ = news_dao.get_or_create_event(
        db, canonical_key="snapshot-event-1", title="当前投影标题", content="当前投影摘要",
        announced_at=published_at, first_seen_at=available_at, status="approved",
    )
    labels = {
        "fact": {
            "title": "冻结的事实标题", "fact_summary": "冻结的事实摘要",
            "identity": {"entity": "甲公司", "action": "回购", "reference_period": "2026"},
            "event_type": "公司行为", "first_published_at": published_at.isoformat(),
        },
        "targets": [{
            "target": "stock:000001.SZ", "scope": "stock", "scope_refs": ["stock:000001.SZ"],
            "horizons": [{
                "trading_days": 1, "direction": "bullish", "strength": 0.8, "confidence": 0.8,
            }],
        }],
    }
    accepted = news_dao.append_assessment(
        db, news_id=news.news_id, event_id=event_id, fact_key="fact-1", novelty="new",
        review_status="accepted", labels=labels, evidence=[], model_version="test",
        prompt_version="test", operation_id="snapshot-accepted", available_at=available_at,
        expected_revision=0,
    )
    disputed_at = datetime(2026, 9, 1, 2, 0, tzinfo=timezone.utc)
    disputed = news_dao.append_assessment(
        db, news_id=news.news_id, event_id=event_id, fact_key="fact-1", novelty="update",
        review_status="disputed", labels={**labels, "fact": {**labels["fact"], "title": "未确认标题"}},
        evidence=[], model_version="test", prompt_version="test", operation_id="snapshot-disputed",
        available_at=disputed_at, expected_revision=1,
    )
    disputed_replay = news_dao.append_assessment(
        db, news_id=news.news_id, event_id=event_id, fact_key="fact-1", novelty="update",
        review_status="disputed", labels={**labels, "fact": {**labels["fact"], "title": "未确认标题"}},
        evidence=[], model_version="test", prompt_version="test", operation_id="snapshot-disputed",
        available_at=disputed_at, expected_revision=1,
    )
    assert disputed.revision == 2 and disputed.inserted
    assert disputed_replay.assessment_id == disputed.assessment_id and not disputed_replay.inserted
    assert db.execute(
        "SELECT supersedes_id FROM event_assessment WHERE assessment_id = %s",
        (disputed.assessment_id,),
    ).fetchone()[0] == accepted.assessment_id

    db.execute("SAVEPOINT rollback_assessment")
    news_dao.append_assessment(
        db, news_id=news.news_id, event_id=event_id, fact_key="rolled-back-fact", novelty="new",
        review_status="accepted", labels=labels, evidence=[], model_version="test",
        prompt_version="test", operation_id="snapshot-rollback", expected_revision=0,
    )
    db.execute("ROLLBACK TO SAVEPOINT rollback_assessment")
    assert db.execute(
        "SELECT count(*) FROM event_assessment WHERE event_id = %s AND fact_key = 'rolled-back-fact'",
        (event_id,),
    ).fetchone()[0] == 0
    from AI.eventStudy.processing import event_vectorizer

    monkeypatch.setattr(event_vectorizer, "get_model", lambda: object())
    monkeypatch.setattr(event_vectorizer, "encode_texts", lambda texts: [[0.1] * 1024 for _ in texts])
    assert event_vectorizer.vectorize_unembedded_assessments(db) == 2
    assert event_vectorizer.vectorize_unembedded_assessments(db) == 0

    legacy_event_id = _insert_event(
        db, "未结构化的旧事件", datetime(2026, 8, 30, tzinfo=timezone.utc),
    )
    assert legacy_event_id != event_id
    assert news_dao.count_unassessed_legacy_events(
        db, as_of=datetime.now(timezone.utc), lookback_days=90,
    ) == 1
    db.execute(
        "UPDATE events SET title = '后来修改的标题', content = '后来修改的摘要', "
        "event_type = '后来修改的类型', status = 'ignored' WHERE event_id = %s", (event_id,),
    )

    now = datetime.now(timezone.utc)
    current = news_dao.list_event_candidates(
        db, as_of=now, lookback_days=90,
        include_disputed=False, query_embedding=_EMB,
    )
    assert len(current) == 1
    assert current[0]["assessment_id"] == accepted.assessment_id
    assert current[0]["event_title"] == "冻结的事实标题"
    assert current[0]["event_type"] == "公司行为"
    assert current[0]["total_count"] == 1
    assert current[0]["vector_similarity"] == pytest.approx(1.0)

    from backend.modules.daily_research.application.quant_pipeline import (
        _build_candidate_rows,
        _event_adjustments,
    )

    strategy_version_id = uuid.uuid4()
    event_adjustments = _event_adjustments(
        current, as_of=now, universe={"000001.SZ"}, industries={}, sectors={},
    )
    scored, candidate_count = _build_candidate_rows(
        [SimpleNamespace(
            id=1, strategy_version_id=strategy_version_id, ts_code="000001.SZ",
            score=Decimal(10), reason="测试策略信号",
        )],
        strategies={strategy_version_id: {"name": "测试策略", "version_no": 1}},
        event_adjustments=event_adjustments,
    )
    assert candidate_count == 1
    assert scored[0]["event_drivers"][0]["assessment_id"] == str(accepted.assessment_id)
    assert scored[0]["event_score"] > 0

    with_disputed = news_dao.list_event_candidates(
        db, as_of=now, lookback_days=90,
        include_disputed=True, query_embedding=_EMB,
    )
    assert len(with_disputed) == 1
    assert with_disputed[0]["assessment_id"] == accepted.assessment_id

    historical = news_dao.list_event_candidates(
        db, as_of=datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc),
        news_cutoff_at=datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc),
        lookback_days=90, query_embedding=_EMB,
    )
    assert len(historical) == 1
    assert historical[0]["event_title"] == "冻结的事实标题"
    assert historical[0]["has_embedding"] is False
    assert historical[0]["vector_similarity"] is None

    news_dao.append_assessment(
        db, news_id=news.news_id, event_id=event_id, fact_key="fact-1", novelty="update",
        review_status="retracted", labels=labels, evidence=[], model_version="test",
        prompt_version="test", operation_id="snapshot-retracted",
        available_at=datetime(2026, 9, 3, tzinfo=timezone.utc), expected_revision=2,
    )
    news_dao.refresh_event_projection_status(db, event_id=event_id)
    assert db.execute("SELECT status FROM events WHERE event_id = %s", (event_id,)).fetchone()[0] == "ignored"
    assert news_dao.list_event_candidates(
        db, as_of=datetime(2026, 9, 4, tzinfo=timezone.utc), lookback_days=90,
    ) == []

def test_schema_upgrade_preserves_existing_event_and_impact_rows(db):
    """Legacy events/impacts survive the additive daily-research schema upgrade."""
    from AI.eventStudy.db import connection as db_connection

    event_id = _insert_event(
        db, "旧结构中的正式事件", datetime(2026, 8, 30, tzinfo=timezone.utc),
    )
    _insert_impact(db, event_id, 0.012)
    db.commit()

    db.execute("DROP TABLE event_assessment CASCADE")
    db.execute("DROP TABLE news CASCADE")
    for column in ("canonical_key", "review_origin", "first_seen_at", "merged_into_event_id"):
        db.execute(f"ALTER TABLE events DROP COLUMN {column} CASCADE")
    db.commit()

    assert db_connection.init_schema(db), "旧结构升级失败"
    legacy = db.execute(
        "SELECT title, canonical_key, review_origin, first_seen_at, merged_into_event_id "
        "FROM events WHERE event_id = %s", (event_id,),
    ).fetchone()
    assert legacy == ("旧结构中的正式事件", f"legacy:{event_id}", "human", None, None)
    assert db.execute(
        "SELECT count(*) FROM event_impacts WHERE event_id = %s", (event_id,),
    ).fetchone()[0] == 1
    assert db.execute(
        "SELECT count(*) FROM assets WHERE ticker = '000300.SH'",
    ).fetchone()[0] == 1
    assert db.execute("SELECT to_regclass('public.news'), to_regclass('public.event_assessment')").fetchone() == (
        "news", "event_assessment",
    )

def test_as_of_excludes_future_and_includes_boundary(db, monkeypatch):
    """模板通道防前视：announced_at > as_of 排除；边界 == as_of 包含。"""
    at_boundary = _insert_event(db, "时点事件", _AS_OF)
    before = _insert_event(db, "时点前事件", "2026-08-30T10:00:00+08:00")
    future = _insert_event(db, "时点后事件", "2026-08-31T10:00:01+08:00")
    for event_id, car in ((at_boundary, 0.02), (before, 0.01), (future, 0.5)):
        _insert_impact(db, event_id, car)
    db.commit()

    result = _predict(db, monkeypatch, as_of=_AS_OF)
    assert result["template_stats"]["sample_count"] == 2  # 未来样本（0.5）被排除
    assert result["template_stats"]["avg_car"] == pytest.approx(0.015)
    md = result["sample_metadata"]
    assert md["as_of"] == _AS_OF and md["history_match_status"] == "ok"

    # 未传 as_of（旧行为）：不做时间过滤，未来样本也纳入
    no_filter = _predict(db, monkeypatch)
    assert no_filter["template_stats"]["sample_count"] == 3

def test_as_of_applies_to_vector_channel(db, monkeypatch):
    """向量通道同样防前视：未来相似事件（announced_at > as_of）排除、边界包含。"""
    at_boundary = _insert_event(db, "时点事件", _AS_OF, event_type="地缘",
                                event_subtype=None, event_condition=None,
                                embedding=_VEC_LITERAL)
    future = _insert_event(db, "时点后事件", "2026-08-31T10:00:01+08:00",
                           event_type="地缘", event_subtype=None,
                           event_condition=None, embedding=_VEC_LITERAL)
    _insert_impact(db, at_boundary, 0.03)
    _insert_impact(db, future, 0.6)
    db.commit()

    monkeypatch.setattr(predictor, "encode_text", lambda text: _EMB)
    result = predictor.predict_impact(db, "查询事件", "000300.SH", as_of=_AS_OF)
    assert [s["event_id"] for s in result["supplement_events"]] == [at_boundary]
    assert result["sample_metadata"]["supplement_sample_count"] == 1

    no_filter = predictor.predict_impact(db, "查询事件", "000300.SH")
    assert {s["event_id"] for s in no_filter["supplement_events"]} == {at_boundary, future}

def test_exclude_event_id_removes_self_from_both_channels(db, monkeypatch):
    """评审 m14：候选事件自身不得作为历史样本（模板/向量同时排除）。

    预取按候选事件取统计时，候选事件已在库中且有 event_impacts 行；不排除会把
    「事件自身已实现的影响」当作历史样本（自相关污染、样本偏乐观）。
    """
    self_event = _insert_event(db, "候选事件自身", _AS_OF, embedding=_VEC_LITERAL)
    other = _insert_event(db, "历史同类事件", "2026-08-30T10:00:00+08:00",
                          embedding=_VEC_LITERAL)
    _insert_impact(db, self_event, 0.80)
    _insert_impact(db, other, 0.02)
    db.commit()

    monkeypatch.setattr(predictor, "encode_text", lambda text: _EMB)

    def _run(exclude):
        return predictor.predict_impact(
            db, "查询事件", "000300.SH", event_type="宏观", event_subtype="CPI",
            event_condition="超预期", as_of=_AS_OF, exclude_event_id=exclude)

    included = _run(None)  # 不排除（旧行为）：自身极端样本进入统计
    assert included["template_stats"]["sample_count"] == 2
    assert included["template_stats"]["avg_car"] == pytest.approx(0.41)
    assert {s["event_id"] for s in included["supplement_events"]} == {
        self_event, other}

    excluded = _run(self_event)
    assert excluded["template_stats"]["sample_count"] == 1
    assert excluded["template_stats"]["avg_car"] == pytest.approx(0.02)
    assert [s["event_id"] for s in excluded["supplement_events"]] == [other]

def test_scope_isolation_sector_route(db, monkeypatch):
    """同作用域过滤：仅命中目标引用（并集）、同名不误命中、不跨层串样本。"""
    e_electronic = _insert_event(db, "同名政策", "2026-08-01T09:00:00+08:00",
                                 "sector", ["SW:801080"])
    e_both = _insert_event(db, "同名政策", "2026-08-02T09:00:00+08:00",
                           "sector", ["SW:801080", "CONCEPT:BK1753.DC"])
    e_computer = _insert_event(db, "同名政策", "2026-08-03T09:00:00+08:00",
                               "sector", ["SW:801750"])
    e_market = _insert_event(db, "同名政策", "2026-08-04T09:00:00+08:00", "market", [])
    for event_id in (e_electronic, e_both, e_computer, e_market):
        _insert_impact(db, event_id, 0.01)
    db.commit()

    def _ids(*scope_refs):
        return _predict(db, monkeypatch, event_scope="sector",
                        scope_refs=list(scope_refs))

    only_electronic = _ids("801080")
    assert only_electronic["template_stats"]["sample_count"] == 2  # 电子 + 电子∪概念
    only_concept = _ids("CONCEPT:BK1753.DC")
    assert only_concept["template_stats"]["sample_count"] == 1
    union = _ids("SW:801080", "SW:801750")
    assert union["template_stats"]["sample_count"] == 3
    assert _predict(db, monkeypatch, event_scope="sector",
                    scope_refs=["SW:801760"])["template_stats"]["sample_count"] == 0

    # market 作用域：仅市场事件（+ 历史 NULL 行），不借用行业样本
    market = _predict(db, monkeypatch, event_scope="market")
    assert market["template_stats"]["sample_count"] == 1
    assert market["sample_metadata"]["event_scope"] == "market"

def test_scope_isolation_stock_route_and_missing_coverage(db, monkeypatch):
    """个股作用域仅命中个股引用；未登记资产 → coverage_missing（不跨层借用）。"""
    stock_ev = _insert_event(db, "个股公告", "2026-08-01T09:00:00+08:00",
                             "stock", ["stock:600519.SH"])
    _insert_impact(db, stock_ev, 0.04)
    sector_ev = _insert_event(db, "行业政策", "2026-08-02T09:00:00+08:00",
                              "sector", ["SW:801080"])
    _insert_impact(db, sector_ev, 0.02)
    db.commit()

    # 个股路由（资产 000300.SH 已登记）：仅个股事件参与
    res = _predict(db, monkeypatch, event_scope="stock", scope_refs=["600519.SH"])
    assert res["template_stats"]["sample_count"] == 1
    assert res["sample_metadata"]["scope_refs"] == ["stock:600519.SH"]
    # 行业路由：仅行业事件
    res = _predict(db, monkeypatch, event_scope="sector", scope_refs=["801080"])
    assert res["template_stats"]["sample_count"] == 1
    # 未登记资产（行业指数无覆盖）：零样本 + coverage_missing，不借用沪深300 CAR
    res = _predict(db, monkeypatch, ticker="801080.SI", event_scope="sector",
                   scope_refs=["801080"])
    assert res["template_stats"]["sample_count"] == 0
    assert res["sample_metadata"]["history_match_status"] == "coverage_missing"

def test_empty_scope_refs_is_empty_route_real_db(db, monkeypatch):
    """空目标引用 = 空路由：零样本（不落到全量事件），状态 no_sample。"""
    for i, refs in enumerate((["SW:801080"], ["SW:801750"])):
        event_id = _insert_event(db, f"政策{i}", f"2026-08-0{i + 1}T09:00:00+08:00",
                                 "sector", refs)
        _insert_impact(db, event_id, 0.01)
    db.commit()

    res = _predict(db, monkeypatch, event_scope="sector", scope_refs=[])
    assert res["template_stats"]["sample_count"] == 0
    assert res["sample_metadata"]["history_match_status"] == "no_sample"

def test_real_db_contamination_metadata(db, monkeypatch):
    event_id = _insert_event(db, "污染样本", "2026-08-01T09:00:00+08:00")
    _insert_impact(db, event_id, 0.02, contaminated=True)
    clean_id = _insert_event(db, "干净样本", "2026-08-02T09:00:00+08:00")
    _insert_impact(db, clean_id, 0.01)
    db.commit()

    result = _predict(db, monkeypatch, as_of=_AS_OF)
    md = result["sample_metadata"]
    assert md["sample_count"] == 2
    assert md["contaminated"] is True and md["contaminated_sample_count"] == 1
    assert md["history_match_status"] == "ok"
    assert "污染" in result["note"]

def test_legacy_rows_visible_only_to_market_route(db, monkeypatch):
    """未回标旧事件（event_scope NULL）只在市场层可见，不误入 sector/stock 路由。"""
    db.execute(
        "INSERT INTO events (title, content, announced_at, importance, status, canonical_key, "
        "  event_type, event_subtype, event_condition, event_scope, affected_scope_refs) "
        "VALUES ('迁移前旧事件', '', '2026-08-01T09:00:00+08:00'::timestamptz, 3, "
        "        'approved', 'legacy:predictor-test', '宏观', 'CPI', '超预期', NULL, NULL)"
    )
    db.commit()
    legacy_id = db.execute(
        "SELECT event_id FROM events WHERE title = '迁移前旧事件'"
    ).fetchone()[0]
    _insert_impact(db, legacy_id, 0.03)
    db.commit()

    assert _predict(db, monkeypatch, event_scope="market")["template_stats"]["sample_count"] == 1
    assert _predict(db, monkeypatch, event_scope="sector",
                    scope_refs=["SW:801080"])["template_stats"]["sample_count"] == 0

def test_scope_isolation_vector_channel(db, monkeypatch):
    """向量通道同样按作用域过滤：不同作用域的同名相似事件不串层。"""
    in_route = _insert_event(db, "相似事件A", "2026-08-01T09:00:00+08:00", "sector",
                             ["SW:801080"], event_type="地缘", event_subtype=None,
                             event_condition=None, embedding=_VEC_LITERAL)
    out_route = _insert_event(db, "相似事件B", "2026-08-02T09:00:00+08:00", "sector",
                              ["SW:801750"], event_type="地缘", event_subtype=None,
                              event_condition=None, embedding=_VEC_LITERAL)
    market_ev = _insert_event(db, "相似事件C", "2026-08-03T09:00:00+08:00", "market",
                              [], event_type="地缘", event_subtype=None,
                              event_condition=None, embedding=_VEC_LITERAL)
    for event_id in (in_route, out_route, market_ev):
        _insert_impact(db, event_id, 0.02)
    db.commit()

    monkeypatch.setattr(predictor, "encode_text", lambda text: _EMB)
    res = predictor.predict_impact(db, "查询事件", "000300.SH",
                                   event_scope="sector", scope_refs=["SW:801080"])
    assert [s["event_id"] for s in res["supplement_events"]] == [in_route]
    assert res["sample_metadata"]["supplement_sample_count"] == 1
