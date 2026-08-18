"""
预测模块测试（方案 3.7：模板匹配 + 向量检索加权融合）

验证合并加权规则：
- 模板内事件权重 1.0
- 向量补充事件权重 = 相似度 × 0.5（相似度 < 0.5 不纳入）
- 方向按加权平均 CAR 符号判定
- 事件库无样本时返回中性 + 明确提示
"""

import pytest

from AI.eventStudy.prediction import predictor


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class FakeConn:
    """按 SQL 子串分派返回结果的最小连接桩。"""

    def __init__(self, matcher):
        self._matcher = matcher

    def execute(self, sql, params=None):
        rows = self._matcher(sql, params)
        return FakeCursor(rows if rows is not None else [])

    def commit(self):
        pass

    def close(self):
        pass


def _conn_with(template_cars, supplement_impacts, asset_rows=None):
    def matcher(sql, params=None):
        if "SELECT ei.cumulative_abnormal_return, ei.direction" in sql:
            event_id = params[0]
            return supplement_impacts.get(event_id, [])
        if "FROM event_impacts" in sql:  # 模板匹配查询
            return [(c,) for c in template_cars]
        if "SELECT asset_id FROM assets" in sql:
            return asset_rows or [(1,)]
        return None

    return FakeConn(matcher)


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
            101: [(0.04, 1)],
            102: [(-0.02, -1)],
            103: [(0.5, 1)],  # 相似度 0.3 应被过滤，不出现
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


def test_predict_empty_library(monkeypatch):
    """事件库无模板样本且无相似事件 → 中性 + 明确提示。"""
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
    conn = _conn_with(template_cars=[], supplement_impacts={201: [(0.05, 1)]})
    result = predictor.predict_impact(conn, "新事件", "000688.SH")
    assert result["template_stats"]["sample_count"] == 0
    pred = result["prediction"]
    assert pred["predicted_return"] == pytest.approx(0.05)
    assert pred["predicted_direction"] == 1
    # 模板查询不应被触发（模板未定义）
    assert result["supplement_events"][0]["weight"] == pytest.approx(0.45)


def test_judge_near_zero_neutral():
    assert predictor._judge(0.002) == 1
    assert predictor._judge(-0.002) == -1
    assert predictor._judge(0.0005) == 0
    assert predictor._judge(-0.0009) == 0
