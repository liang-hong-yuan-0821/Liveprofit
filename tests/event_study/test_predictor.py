"""
预测模块测试（方案 3.7：模板匹配 + 向量检索加权融合；T3：路由/as_of/元数据）

验证合并加权规则：
- 模板内事件权重 1.0
- 向量补充事件权重 = 相似度 × 0.5（相似度 < 0.5 不纳入）
- 方向按加权平均 CAR 符号判定
- 事件库无样本时返回中性 + 明确提示

T3 增量（方案第三章）：
- 防前视：announced_at > as_of 排除、边界 == as_of 包含（模板 + 向量双通道）
- 同作用域过滤：sector/stock 仅命中目标引用（并集）、同名不误命中、
  空引用 = 空路由（零样本，不落到全量事件）、market 含历史 NULL 行
- 样本元数据：no_sample / coverage_missing / api_unavailable 可区分；
  contaminated 污染提示；缺省参数（不传 as_of/event_scope）保持旧行为
- 非法路由（作用域 / 引用格式）抛 ValueError（不静默降级为空样本）

真实库用例使用独立测试库（建库 → init_schema），PG 不可达/无建库权限时
整体 skip；其余用例为纯单测（FakeConn），无真实依赖。
"""

import json
from datetime import datetime

import pytest

from AI.eventStudy.prediction import predictor, similarity_search

_PRED_TEST_DB = "liveprofit_predictor_test"
_AS_OF = "2026-08-31T10:00:00+08:00"
_EMB = [0.1] * 1024
_VEC_LITERAL = "[" + ",".join(["0.1"] * 1024) + "]"


# ==================== 假连接桩 ====================

class FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class FakeConn:
    """按 SQL 子串分派返回结果的最小连接桩（executed 记录 SQL 契约）。"""

    def __init__(self, matcher):
        self._matcher = matcher
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        rows = self._matcher(sql, params)
        return FakeCursor(rows if rows is not None else [])

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def _conn_with(template_cars, supplement_impacts, asset_rows=None, registered=True):
    """模板（car）/ 补充影响（car, direction, contaminated）/ 覆盖探测分派桩。"""
    def matcher(sql, params=None):
        if "SELECT ei.cumulative_abnormal_return, ei.direction" in sql:
            event_id = params[0]
            return supplement_impacts.get(event_id, [])
        if "FROM event_impacts" in sql:  # 模板匹配查询
            return [(c, False) for c in template_cars]
        if "SELECT 1 FROM assets WHERE ticker" in sql:  # 资产覆盖探测
            return [(1,)] if registered else []
        if "SELECT asset_id FROM assets" in sql:
            return asset_rows or [(1,)]
        return None

    return FakeConn(matcher)


def _tpl_calls(conn):
    return [c for c in conn.executed if "FROM event_impacts" in c[0]
            and "ei.direction" not in c[0]]


# ==================== 合并加权（既有行为） ====================

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


# ==================== 缺省参数兼容旧调用 ====================

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


# ==================== 同作用域过滤（build_scope_filter 单测） ====================

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


# ==================== 覆盖状态与污染元数据（FakeConn） ====================

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


# ==================== 真实库（独立测试库；PG 不可达则 skip） ====================

def _with_dbname(dsn: str, dbname: str) -> str:
    """把 libpq 关键字串 / URL 串的库名替换为 dbname。"""
    if dsn.startswith(("postgres://", "postgresql://")):
        main, _, query = dsn.partition("?")
        head, _, _ = main.rpartition("/")
        out = f"{head}/{dbname}"
        return f"{out}?{query}" if query else out
    parts = [p for p in dsn.split() if not p.startswith("dbname=")]
    parts.append(f"dbname={dbname}")
    return " ".join(parts)


@pytest.fixture(scope="module")
def predict_db():
    """建独立测试库并切 AI 侧 DSN（config.PG_CONNECTION_STRING 为文档注入点）。"""
    import psycopg

    from AI.eventStudy.collectors import config as es_config
    from AI.eventStudy.db import connection as db_connection

    base = es_config.pg_dsn()
    try:
        admin = psycopg.connect(base, connect_timeout=5, autocommit=True)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"PostgreSQL 不可达，跳过预测器入库测试: {e}")
    try:
        admin.execute(f'DROP DATABASE IF EXISTS "{_PRED_TEST_DB}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{_PRED_TEST_DB}"')
    except Exception as e:  # noqa: BLE001
        admin.close()
        pytest.skip(f"无法创建测试库（权限不足），跳过预测器入库测试: {e}")

    original = es_config.PG_CONNECTION_STRING
    es_config.PG_CONNECTION_STRING = _with_dbname(base, _PRED_TEST_DB)
    conn = db_connection.get_connection()
    assert db_connection.init_schema(conn), "测试库建表失败"
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()
        es_config.PG_CONNECTION_STRING = original
        try:
            admin.execute(f'DROP DATABASE IF EXISTS "{_PRED_TEST_DB}" WITH (FORCE)')
        finally:
            admin.close()


@pytest.fixture()
def db(predict_db):
    """逐用例清空事件/影响/预测表（保留 assets 4 指数与行业码表种子）。"""
    predict_db.execute("TRUNCATE events, event_impacts, predictions RESTART IDENTITY")
    predict_db.commit()
    yield predict_db


def _insert_event(conn, title, announced_at, scope="market", refs=None,
                  status="approved", event_type="宏观", event_subtype="CPI",
                  event_condition="超预期", embedding=None):
    row = conn.execute(
        "INSERT INTO events (title, content, announced_at, importance, status, "
        "  event_type, event_subtype, event_condition, event_scope, "
        "  affected_scope_refs, embedding) "
        "VALUES (%s, '', %s::timestamptz, 3, %s, %s, %s, %s, %s, %s::jsonb, %s::vector) "
        "RETURNING event_id",
        (title, announced_at, status, event_type, event_subtype, event_condition,
         scope, None if refs is None else json.dumps(refs, ensure_ascii=False), embedding),
    ).fetchone()
    return int(row[0])


def _insert_impact(conn, event_id, car, ticker="000300.SH",
                   window_type="post_event_5d", contaminated=False):
    asset_id = conn.execute(
        "SELECT asset_id FROM assets WHERE ticker = %s", (ticker,)
    ).fetchone()[0]
    direction = 1 if car > 0 else (-1 if car < 0 else 0)
    conn.execute(
        "INSERT INTO event_impacts (event_id, asset_id, window_type, window_days, "
        "  cumulative_abnormal_return, direction, is_contaminated) "
        "VALUES (%s, %s, %s, 5, %s, %s, %s)",
        (event_id, asset_id, window_type, car, direction, contaminated),
    )


def _predict(conn, monkeypatch, as_of=None, event_scope=None, scope_refs=(),
             ticker="000300.SH", labels=("宏观", "CPI", "超预期")):
    """模板通道确定性断言。

    向量通道保持可用（encode_text 返回有效向量）：测试库中事件 embedding 均为
    NULL → 检索零候选且无错误（不污染覆盖状态）；模板样本数断言不受影响。
    """
    monkeypatch.setattr(predictor, "encode_text", lambda text: _EMB)
    return predictor.predict_impact(
        conn, "查询事件", ticker, event_type=labels[0], event_subtype=labels[1],
        event_condition=labels[2], as_of=as_of, event_scope=event_scope,
        scope_refs=scope_refs,
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


# ==================== m14：排除候选事件自身（自相关污染） ====================

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
        "INSERT INTO events (title, content, announced_at, importance, status, "
        "  event_type, event_subtype, event_condition, event_scope, affected_scope_refs) "
        "VALUES ('迁移前旧事件', '', '2026-08-01T09:00:00+08:00'::timestamptz, 3, "
        "        'approved', '宏观', 'CPI', '超预期', NULL, NULL)"
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
