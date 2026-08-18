"""
Code Review 回归测试（S4 缺口补齐）

覆盖：
- as_of 防前视偏差（模板通道 + 向量通道）
- review 数值 0 字段不被吞（M4）
- save=True 落库路径（M2：有样本落库 / 无样本不落库 + 回滚不毒化事务）
- 时区边界：tz-aware 输入下 resolve_t0（M3）
"""

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
            "importance", "status", "source_url"]
    by_col = dict(zip(cols, captured["params"]))
    assert by_col["actual_value"] == 0
    assert by_col["previous_value"] == 0


# ==================== M2：save 落库路径 ====================

def test_save_prediction_with_samples(monkeypatch):
    """有样本 + save=True → 落库 predictions。"""
    conn = FakeConn(lambda sql, params: None if "INSERT INTO predictions" not in sql else None)
    monkeypatch.setattr(predictor, "encode_text", lambda text: [0.1] * 1024)
    monkeypatch.setattr(predictor.similarity_search, "search_similar_events",
                        lambda conn, emb, **kw: [])

    def matcher(sql, params=None):
        if "FROM event_impacts" in sql:
            return [(0.02,), (0.01,)]
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
