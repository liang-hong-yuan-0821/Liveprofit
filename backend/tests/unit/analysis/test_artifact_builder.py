"""Artifact 提取单测（§3.1.5 字段来源映射与摘要投影）。"""

from __future__ import annotations

from backend.modules.analysis.infrastructure.artifact_builder import build_artifact_from_state


def test_full_state_maps_four_sections():
    state = {
        "selected_layers": ["market", "sector", "stock"],
        "international_news_report": "全球风险偏好回暖",
        "cn_tech_report": "科技板块承压",
        "market_regime": "震荡市",
        "risk_gate": "通过：无系统性风险",
        "sector_news_report": "半导体景气",
        "sector_shortlist_structured": [{"code": "BK1036"}],
        "stock_tech_report": "技术面偏多",
        "news_report": "个股新闻",
        "fundamentals_report": "估值合理",
        "sentiment_report": "情绪偏多",
        "decision": "综合判断：买入评级，目标价上调。",
        "final_position_plan": {"000001.SZ": 0.1},
    }
    artifact = build_artifact_from_state(state)
    sections = {s["block"]: s for s in artifact.report_json["sections"]}
    assert sections["market"]["status"] == "AVAILABLE"
    assert "全球风险偏好回暖" in sections["market"]["content"]
    assert "风险门控" in sections["market"]["content"]
    assert sections["sector"]["status"] == "AVAILABLE"
    assert sections["stock"]["status"] == "AVAILABLE"
    assert sections["decision"]["status"] == "AVAILABLE"
    assert artifact.conclusion_summary == "综合判断：买入评级，目标价上调。"
    assert artifact.risk_flag is False  # 通过型风险门控不算风险
    assert artifact.decision is not None
    assert artifact.decision["final_position_plan"] == {"000001.SZ": 0.1}


def test_unrequested_layer_is_not_requested():
    state = {
        "selected_layers": ["market"],
        "international_news_report": "ok",
    }
    artifact = build_artifact_from_state(state)
    sections = {s["block"]: s for s in artifact.report_json["sections"]}
    assert sections["sector"]["status"] == "NOT_REQUESTED"
    assert sections["stock"]["status"] == "NOT_REQUESTED"
    # decision 无独立层级：无内容视为 UNAVAILABLE（行动区块缺内容需要关注）
    assert sections["decision"]["status"] == "UNAVAILABLE"


def test_missing_content_is_unavailable_with_reason():
    artifact = build_artifact_from_state({})
    sections = {s["block"]: s for s in artifact.report_json["sections"]}
    for block in ("market", "sector", "stock", "decision"):
        assert sections[block]["status"] == "UNAVAILABLE"
        assert sections[block]["unavailable_reason"]
        assert sections[block]["retryable"] is True


def test_summary_truncated_to_200_and_risk_flag():
    long_decision = "买" * 300
    state = {"decision": long_decision, "risk_gate": "禁止买入：流动性风险高"}
    artifact = build_artifact_from_state(state)
    assert len(artifact.conclusion_summary) == 200
    assert artifact.risk_flag is True
    assert artifact.risk_hint == "禁止买入：流动性风险高"


def test_unreliable_summary_is_none():
    artifact = build_artifact_from_state({"decision": None})
    assert artifact.conclusion_summary is None
    assert artifact.risk_flag is False


def test_non_dict_state_mapping():
    class FakeState:
        def __init__(self):
            self.decision = "中性判断"

    artifact = build_artifact_from_state(FakeState())
    sections = {s["block"]: s for s in artifact.report_json["sections"]}
    assert sections["decision"]["status"] == "AVAILABLE"
    assert artifact.conclusion_summary == "中性判断"
