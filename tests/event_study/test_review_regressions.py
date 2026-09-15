"""
Code Review 回归测试（S4 缺口补齐）

覆盖：
- as_of 防前视偏差（模板通道 + 向量通道）
- review 数值 0 字段不被吞（M4）
- save=True 落库路径（M2：有样本落库 / 无样本不落库 + 回滚不毒化事务）
- 时区边界：tz-aware 输入下 resolve_t0（M3）
"""

import json
from datetime import date, datetime, timezone, timedelta

import pytest

from AI.eventStudy.prediction import predictor
from AI.eventStudy.review import review_dao
from AI.eventStudy.processing import event_study

CST = timezone(timedelta(hours=8))


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class FakeConn:
    def __init__(self, matcher):
        self._matcher = matcher
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        rows = self._matcher(sql, params)
        return FakeCursor(rows if rows is not None else [])

    def cursor(self):
        """上下文管理器（executemany 用）。"""
        return _CursorCtx(self)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class _CursorCtx:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def executemany(self, sql, records):
        self._conn.execute(sql, records)


# ==================== as_of 防前视（双通道） ====================

def test_as_of_applied_to_template_and_vector(monkeypatch):
    """as_of 必须同时作用于模板查询与向量检索（3.7.3）。"""
    conn = FakeConn(lambda sql, params: [])
    captured = {}

    def fake_search(conn, emb, **kw):
        captured["before_ts"] = kw.get("before_ts")
        return []

    monkeypatch.setattr(predictor, "encode_text", lambda text: [0.1] * 1024)
    monkeypatch.setattr(predictor.similarity_search, "search_similar_events", fake_search)

    as_of = "2026-08-01T00:00:00+08:00"
    predictor.predict_impact(conn, "事件", "000001.SH",
                             event_type="宏观", event_subtype="CPI",
                             event_condition="超预期", as_of=as_of)
    # 向量通道
    assert captured["before_ts"] == as_of
    # 模板通道：SQL 含 announced_at < 条件且参数含 as_of
    tpl_calls = [c for c in conn.executed if "FROM event_impacts" in c[0]]
    assert tpl_calls, "模板查询未被触发"
    assert "announced_at <" in tpl_calls[0][0]
    assert as_of in (tpl_calls[0][1] or [])


def test_as_of_not_set_without_arg(monkeypatch):
    """未传 as_of 时两个通道都不加时间过滤。"""
    conn = FakeConn(lambda sql, params: [])
    captured = {}

    def fake_search(conn, emb, **kw):
        captured["before_ts"] = kw.get("before_ts")
        return []

    monkeypatch.setattr(predictor, "encode_text", lambda text: [0.1] * 1024)
    monkeypatch.setattr(predictor.similarity_search, "search_similar_events", fake_search)
    predictor.predict_impact(conn, "事件", "000001.SH",
                             event_type="宏观", event_subtype="CPI",
                             event_condition="超预期")
    assert captured["before_ts"] is None
    tpl_calls = [c for c in conn.executed if "FROM event_impacts" in c[0]]
    assert "announced_at <" not in tpl_calls[0][0]


# ==================== M4：review 数值 0 不被吞 ====================

def test_insert_event_keeps_zero_values():
    """actual_value=0 / previous_value=0 是合法值，不应被 or 兜底吞掉。"""
    draft = {"title": "T", "content": "", "announced_at": "2026-08-01T08:00:00+08:00",
             "source_url": None, "importance_hint": None}
    review = {"actual_value": 0, "previous_value": 0, "importance": 4}
    captured = {}

    def matcher(sql, params=None):
        if "INSERT INTO events" in sql:
            captured["params"] = params
            return [(42,)]
        return None

    conn = FakeConn(matcher)
    event_id = review_dao._insert_event(conn, draft, review, "approved")
    assert event_id == 42
    cols = ["title", "content", "event_type", "event_subtype", "event_condition",
            "announced_at", "expected_value", "actual_value", "previous_value",
            "importance", "status", "source_url", "event_scope", "affected_scope_refs"]
    by_col = dict(zip(cols, captured["params"]))
    assert by_col["actual_value"] == 0
    assert by_col["previous_value"] == 0
    # 路由字段默认链：未给作用域 → market + []（JSON 字符串写入 jsonb 列）
    assert by_col["event_scope"] == "market"
    assert json.loads(by_col["affected_scope_refs"]) == []


# ==================== M2：save 落库路径 ====================

def test_save_prediction_with_samples(monkeypatch):
    """有样本 + save=True → 落库 predictions。"""
    conn = FakeConn(lambda sql, params: None if "INSERT INTO predictions" not in sql else None)
    monkeypatch.setattr(predictor, "encode_text", lambda text: [0.1] * 1024)
    monkeypatch.setattr(predictor.similarity_search, "search_similar_events",
                        lambda conn, emb, **kw: [])

    def matcher(sql, params=None):
        if "FROM event_impacts" in sql:
            # 模板查询行形态：(car, is_contaminated) —— T3 起附带污染标记
            return [(0.02, False), (0.01, False)]
        if "SELECT asset_id FROM assets" in sql:
            return [(1,)]
        if "INSERT INTO predictions" in sql:
            return [(1,)]
        return None

    conn = FakeConn(matcher)
    result = predictor.predict_impact(conn, "事件", "000300.SH",
                                      event_type="宏观", event_subtype="CPI",
                                      event_condition="超预期", save=True)
    assert result["prediction"]["predicted_return"] is not None
    assert any("INSERT INTO predictions" in s for s, _ in conn.executed)


def test_save_prediction_without_samples_skips_insert(monkeypatch):
    """无样本 + save=True → 不落库（NOT NULL 保护），也不抛异常。"""
    conn = FakeConn(lambda sql, params: [])
    monkeypatch.setattr(predictor, "encode_text", lambda text: [0.1] * 1024)
    monkeypatch.setattr(predictor.similarity_search, "search_similar_events",
                        lambda conn, emb, **kw: [])
    result = predictor.predict_impact(conn, "事件", "000300.SH",
                                      event_type="宏观", event_subtype="CPI",
                                      event_condition="超预期", save=True)
    assert result["prediction"]["predicted_return"] is None
    assert not any("INSERT INTO predictions" in s for s, _ in conn.executed)


def test_save_prediction_failure_rolls_back():
    """落库失败时回滚，不毒化事务（M2）。"""
    class BrokenConn(FakeConn):
        def execute(self, sql, params=None):
            if "INSERT INTO predictions" in sql:
                raise RuntimeError("NotNullViolation 模拟")
            if "SELECT asset_id FROM assets" in sql:
                return FakeCursor([(1,)])
            return FakeCursor([])

    conn = BrokenConn(lambda sql, params: [])
    rolled_back = []

    def fake_rollback():
        rolled_back.append(True)

    conn.rollback = fake_rollback
    predictor._save_prediction(conn, {
        "asset_ticker": "000300.SH", "window_type": "post_event_5d",
        "predicted_direction": 1, "predicted_return": 0.01, "confidence": 0.5,
    }, None, [])
    assert rolled_back, "失败后应回滚"


# ==================== M3：时区边界（tz-aware 输入） ====================

def test_resolve_t0_timezone_aware():
    """tz-aware 输入（如 PG 返回的 timestamptz）：盘中 +08 事件 → 下一交易日。

    10:00+08 在 UTC 会话下若被误读为 02:00 会错判为盘前；时区感知实现应
    正确判为盘中。
    """
    announced = datetime(2026, 8, 18, 10, 0, tzinfo=CST)  # 周二 10:00+08
    trade_days = {date(2026, 8, 18), date(2026, 8, 19)}
    assert event_study.resolve_t0(announced, trade_days) == date(2026, 8, 19)


def test_resolve_t0_utc_naive_input():
    """UTC 无时区输入按数值直接判定（与 +08 口径一致，由入库约定保证）。"""
    announced = datetime(2026, 8, 18, 8, 0)  # 视为 +08 的 08:00 盘前
    trade_days = {date(2026, 8, 18)}
    assert event_study.resolve_t0(announced, trade_days) == date(2026, 8, 18)


# ==================== M1：confirm_impacts 幂等（SQL 层） ====================

def test_confirm_impacts_uses_conflict_target():
    """落表 SQL 必须带 ON CONFLICT (event_id, asset_id, window_type)。"""
    from AI.eventStudy.processing import impact_writer
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(impact_writer, "read_impact_draft",
                        lambda event_id: {"event_id": 1, "t0": "2026-08-01",
                                          "assets": {"000001.SH": {
                                              "pre_event_5d": {"window_days": 5,
                                                               "cumulative_abnormal_return": 0.01,
                                                               "t_stat": 2.0, "direction": 1,
                                                               "is_contaminated": False, "error": None},
                                              "event_day": {"window_days": 1,
                                                            "cumulative_abnormal_return": 0.0,
                                                            "t_stat": 0.1, "direction": 0,
                                                            "is_contaminated": False, "error": None},
                                              "post_event_5d": {"window_days": 5,
                                                                "cumulative_abnormal_return": 0.02,
                                                                "t_stat": 2.5, "direction": 1,
                                                                "is_contaminated": False, "error": None},
                                          }}})
    monkeypatch.setattr(impact_writer, "delete_impact_draft", lambda event_id: None)
    monkeypatch.setattr(review_dao, "_log_review", lambda *a, **kw: None)

    captured = {}

    def matcher(sql, params=None):
        if "SELECT asset_id FROM assets" in sql:
            return [(1,)]
        if "INSERT INTO event_impacts" in sql:
            captured["sql"] = sql
            return []
        return None

    conn = FakeConn(matcher)
    review_dao.confirm_impacts(conn, 1, ["000001.SH"])
    assert "ON CONFLICT (event_id, asset_id, window_type)" in captured["sql"]
    monkeypatch.undo()


# ==================== M5：as_of 同日边界（日期形态 → 当日末） ====================

def test_as_of_date_only_normalized_to_end_of_day():
    """日期形态 as_of 绑定当日 23:59:59.999999（评审 M5）。

    直接绑定 'YYYY-MM-DD' 会被 PG 解析为当日 00:00，把同日（如 09:30）已公布
    事件整片排除——与 `announced_at <= as_of`（边界包含）的防前视口径矛盾。
    """
    conn = FakeConn(lambda sql, params: [])
    review_dao.list_approved_events_for_route(
        conn, review_dao.EventRoute(scope="market"), as_of="2026-08-18")
    sql, params = conn.executed[0]
    assert "announced_at <= %s::timestamptz" in sql
    assert params[0] == "2026-08-18 23:59:59.999999"

    # 同日 09:30 事件落在时点上界之内 → 可见（修复前上界 = 00:00 会漏取）
    bound = datetime.strptime(params[0], "%Y-%m-%d %H:%M:%S.%f")
    assert datetime(2026, 8, 18, 9, 30) <= bound


def test_as_of_param_normalization_forms():
    """date 对象 / 其它日期形态同口径；带时刻输入原样保留（不扩大到当日末）。"""
    assert review_dao._as_of_param(date(2026, 8, 18)) == "2026-08-18 23:59:59.999999"
    assert review_dao._as_of_param("2026/08/18") == "2026-08-18 23:59:59.999999"
    assert review_dao._as_of_param("2026.08.18") == "2026-08-18 23:59:59.999999"
    assert review_dao._as_of_param("20260818") == "2026-08-18 23:59:59.999999"
    # 时分秒微秒全零的 datetime（`datetime.combine(d, time())` 的日边界语义）→ 当日末
    assert review_dao._as_of_param(datetime(2026, 8, 18)) == "2026-08-18 23:59:59.999999"
    assert review_dao._as_of_param(
        datetime(2026, 8, 18, 0, 0, 0, 0)) == "2026-08-18 23:59:59.999999"
    # None / 空串 / 纯空白 → None（不做时间过滤，调用方跳过该条件）
    assert review_dao._as_of_param(None) is None
    assert review_dao._as_of_param("") is None
    assert review_dao._as_of_param("   ") is None
    stamp = datetime(2026, 8, 18, 9, 30)
    assert review_dao._as_of_param(stamp) == stamp.isoformat()
    assert review_dao._as_of_param("2026-08-18T09:30:00+08:00") == "2026-08-18T09:30:00+08:00"


def test_as_of_none_or_blank_skips_time_filter():
    """as_of 为 None/空串/纯空白 → 跳过 `announced_at <= as_of`（不做时间过滤）。

    与 predictor `_as_of_sql_param` 语义一致；不得退化成绑定 None/空串（PG 侧
    类型与语义都不确定）。非空 as_of 仍按日期形态归一为当日末并绑定。
    """
    for value in (None, "", "   "):
        conn = FakeConn(lambda sql, params: [])
        review_dao.list_approved_events_for_route(
            conn, review_dao.EventRoute(scope="market"), as_of=value)
        sql, params = conn.executed[0]
        assert "announced_at <= %s::timestamptz" not in sql
        assert params == ("market", 20)  # 参数表不含时点

    conn = FakeConn(lambda sql, params: [])
    review_dao.list_approved_events_for_route(
        conn, review_dao.EventRoute(scope="market"), as_of="2026-08-18")
    sql, params = conn.executed[0]
    assert "announced_at <= %s::timestamptz" in sql
    assert params == ("2026-08-18 23:59:59.999999", "market", 20)


# ==================== M6：合法重复引用不得判非法 ====================

def test_duplicate_scope_refs_deduped_not_rejected():
    """重复引用静默去重；「格式非法」的唯一判据是无法识别的引用项（评审 M6）。

    修复前用「去重后条数变少」反推格式非法——同一目标写两次（多来源表单/拼接）
    会被整行拦下（REVIEW_ROW_FAILED），合法输入误伤。
    """
    scope, refs = review_dao.resolve_scope_fields({
        "event_scope": "sector",
        "affected_scope_refs": ["SW:801080", "801080", "801080.SI", "SW:801080"],
    })
    assert scope == "sector"
    assert refs == ["SW:801080"]

    # 无法识别的引用项 → 仍须拦截
    with pytest.raises(review_dao.EventScopeValidationError):
        review_dao.resolve_scope_fields({
            "event_scope": "sector",
            "affected_scope_refs": ["SW:801080", "garbage"],
        })


def test_route_query_accepts_duplicate_refs():
    """预取路由查询同样只按「无法识别项」判非法，重复引用去重后执行（评审 M6）。"""
    conn = FakeConn(lambda sql, params: [])
    review_dao.list_approved_events_for_route(
        conn, review_dao.EventRoute(scope="sector", scope_refs=("SW:801080", "801080")),
        as_of=None)
    sql, params = conn.executed[0]
    assert "affected_scope_refs ?| %s::text[]" in sql
    # as_of=None → 时点条件整条跳过，参数表只剩 (scope, refs, limit)
    assert "announced_at <= %s::timestamptz" not in sql
    assert params[1] == ["SW:801080"]  # 去重后的并集引用（单目标）

    bad = FakeConn(lambda sql, params: [])
    with pytest.raises(ValueError):
        review_dao.list_approved_events_for_route(
            bad, review_dao.EventRoute(scope="sector", scope_refs=("SW:801080", "garbage")),
            as_of=None)


def test_raw_ref_items_skips_none_and_blank():
    """`None` 与空白一起跳过（评审残留）：`None` 经 str() 成 "None" 会被判为
    未识别引用，把空占位项误伤成「格式非法」而整行拦截。"""
    assert review_dao._raw_ref_items(["SW:801080", None, "", "  "]) == ["SW:801080"]
    scope, refs = review_dao.resolve_scope_fields({
        "event_scope": "sector",
        "affected_scope_refs": ["SW:801080", None, ""],
    })
    assert scope == "sector"
    assert refs == ["SW:801080"]


# ==================== M6 残留：Streamlit 提交前拦截未识别引用项 ====================

def test_review_app_row_target_refs_flags_unrecognized():
    """表格「目标」列 → (归一引用, 未识别项)：静默丢项不得落库（纯函数层面）。

    `normalize_scope_refs` 会静默丢弃未识别项；提交前不比对归一前后，会落库一个
    「少目标」的事件，与 backend `resolve_scope_fields` 的拦截语义分叉。
    """
    from AI.eventStudy.review import review_app

    refs, unrecognized = review_app.row_target_refs("SW:801080, garbage, 801080")
    assert refs == ["SW:801080"]
    assert unrecognized == ["garbage"]

    # 合法重复引用（同一目标写两次）不是未识别项
    refs, unrecognized = review_app.row_target_refs("SW:801080, 801080")
    assert refs == ["SW:801080"]
    assert unrecognized == []

    # 空目标（market 行）不产生未识别项——缺目标由既有前置拦截负责
    assert review_app.row_target_refs("") == ([], [])
    assert review_app.row_target_refs(None) == ([], [])
