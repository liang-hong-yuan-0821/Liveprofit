"""Artifact 提取（T6 首版）：AI 内核 final_state → AnalysisArtifact（§3.1.5 DTO 区域映射）。

只提取已定义字段；完整 State 另存 task-scoped artifact storage，与结构化摘要分离。
字段来源按方案 §3.1.5：market 取 international_news_report/cn_tech_report/market_regime/risk_gate；
sector 取 sector_*_report/sector_shortlist_structured/rotation_*；stock 取 stock_*_report/news_report/
fundamentals_report/sentiment_report；decision 取 decision/final_position_plan/stock_results。
首版保守提取：无法形成可靠摘要时 conclusion_summary=None，前端不截取正文兜底。
"""

from __future__ import annotations

import json
from typing import Any

from backend.modules.analysis.application.contracts import AnalysisArtifact

_BLOCK_SOURCES: dict[str, tuple[str, ...]] = {
    "market": ("international_news_report", "cn_tech_report", "market_regime"),
    "sector": ("sector_news_report", "sector_tech_report", "sector_rotation_report",
               "sector_shortlist_structured", "rotation_analysis"),
    "stock": ("stock_tech_report", "news_report", "fundamentals_report", "sentiment_report"),
    "decision": ("decision", "final_position_plan"),
}

_BLOCK_TITLES = {
    "market": "市场环境",
    "sector": "板块分析",
    "stock": "个股研究",
    "decision": "交易决策",
}


def build_artifact_from_state(final_state: Any) -> AnalysisArtifact:
    state = _as_mapping(final_state)
    sections = []
    selected_layers = set(state.get("selected_layers") or [])

    for block, sources in _BLOCK_SOURCES.items():
        content_parts = [_stringify(state.get(key)) for key in sources if _non_empty(state.get(key))]
        risk_gate = _stringify(state.get("risk_gate"))
        if block == "market" and risk_gate:
            content_parts.append(f"风险门控：{risk_gate}")
        content = "\n\n".join(content_parts) or None
        if content is not None:
            status = "AVAILABLE"
        elif selected_layers and block != "decision" and block not in selected_layers:
            status = "NOT_REQUESTED"
        else:
            status = "UNAVAILABLE"
        sections.append(
            {
                "block": block,
                "status": status,
                "title": _BLOCK_TITLES[block],
                "summary": content[:200] if content else None,
                "content": content,
                "charts": None,
                # 不可用原因/可重试仅属 UNAVAILABLE；NOT_REQUESTED 不得携带（避免误导性"重试区块"）
                "unavailable_reason": "该区块无可展示内容" if (status == "UNAVAILABLE" and not content) else None,
                "retryable": True if (status == "UNAVAILABLE" and not content) else None,
            }
        )

    decision_text = _stringify(state.get("decision") or state.get("signal"))
    conclusion_summary = decision_text[:200] if decision_text else None
    risk_gate_text = _stringify(state.get("risk_gate"))
    risk_flag = bool(risk_gate_text) and (
        any(marker in risk_gate_text for marker in ("禁止", "高风险", "风险预警", "触发风控", "risk"))
        and not any(safe in risk_gate_text for safe in ("通过", "无风险", "低风险", "风险可控"))
    )
    decision_payload = None
    if state.get("decision") is not None or state.get("signal") is not None or state.get("final_position_plan") is not None:
        try:
            decision_payload = {
                "decision": state.get("decision") or state.get("signal"),
                "final_position_plan": state.get("final_position_plan"),
                "stock_results": state.get("stock_results"),
            }
            # round-trip：default=str 把 LangChain 对象（如 HumanMessage）转成字符串，
            # 校验结果必须回写，否则原始对象进入 JSONB 写库时 json.dumps（无 default）会抛
            # "Object of type HumanMessage is not JSON serializable"
            decision_payload = json.loads(json.dumps(decision_payload, ensure_ascii=False, default=str))
        except (TypeError, ValueError):
            decision_payload = None

    return AnalysisArtifact(
        report_json={"sections": sections, "selected_layers": sorted(selected_layers)},
        conclusion_summary=conclusion_summary,
        risk_flag=risk_flag,
        risk_hint=risk_gate_text[:512] if risk_flag and risk_gate_text else None,
        decision=decision_payload,
        artifact_uri=None,
        checksum=None,
        duration_ms=None,
    )


def _as_mapping(state: Any) -> dict:
    if state is None:
        return {}
    if isinstance(state, dict):
        return state
    # LangGraph State 常见为 dict-like：兼容 .__dict__ / dataclass
    if hasattr(state, "__dict__"):
        return dict(state.__dict__)
    return {}


def _non_empty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) > 0
    return True


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)
