"""
LangGraph 工具封装（方案 3.10）

将相似事件检索/预测能力封装为 LangGraph 工具（进程内直接调用，不经过 HTTP），
注册进 international_news_analyst 的工具循环（market_layer_graph._get_tools）。

- Agent 需要历史案例时自行决定调用；事件库无相似结果时返回明确提示，
  Agent 按原有 LLM 知识逻辑继续分析，不影响输出
- 工具失败时捕获异常返回提示，不阻塞报告生成
- 日常调用只返回结果不落库（predictor save=False）
"""

import logging

from langchain_core.tools import tool
from typing import Annotated, Optional

logger = logging.getLogger(__name__)

DEFAULT_TICKER = "000300.SH"  # 沪深300（市场基准）


def _format_result(result: dict) -> str:
    """预测结果 → Agent 可读文本。"""
    pred = result.get("prediction", {})
    tpl = result.get("template_stats", {})
    direction = {1: "利好", -1: "利空", 0: "中性"}.get(pred.get("predicted_direction"), "未知")
    lines = [
        "## 事件研究系统：相似事件检索结果",
        f"- 预测方向: **{direction}**",
        f"- 预测幅度（加权平均 CAR）: "
        f"{pred['predicted_return'] * 100:.3f}%" if pred.get("predicted_return") is not None
        else "- 预测幅度: 无可用样本",
        f"- 置信度: {pred.get('confidence', 0)}",
    ]
    if tpl.get("sample_count"):
        lines.append(
            f"\n### 模板匹配基线（{tpl.get('event_type') or '—'}/{tpl.get('event_subtype') or '—'}/"
            f"{tpl.get('event_condition') or '—'}）\n"
            f"- 样本数: {tpl['sample_count']}，平均 CAR: {tpl['avg_car'] * 100:.3f}%，"
            f"胜率（上涨占比）: {(tpl['win_rate'] or 0) * 100:.1f}%"
        )
    supplement = result.get("supplement_events", [])
    if supplement:
        lines.append("\n### 语义相似事件补充（权重 = 相似度 × 0.5）")
        for s in supplement[:10]:
            lines.append(
                f"- {s['title'][:60]}（相似度 {s['similarity']:.2f}，"
                f"该事件对 {pred.get('asset_ticker')} {pred.get('window_type')} 影响 "
                f"{s['car'] * 100:+.3f}%）"
            )
    note = result.get("note", "")
    if note:
        lines.append(f"\n> {note}")
    return "\n".join(lines)


@tool
def search_similar_events(
    event_text: Annotated[str, "事件文本描述（标题/摘要，中文）"],
    asset_ticker: Annotated[str, "目标 A 股指数代码，如 000001.SH（上证指数）、000300.SH（沪深300）、000688.SH（科创50）、000698.SH（科创100）"] = DEFAULT_TICKER,
    window_type: Annotated[str, "影响窗口：pre_event_5d（事件前5日）/ event_day（当日）/ post_event_5d（事件后5日）"] = "post_event_5d",
) -> str:
    """
    检索事件研究系统历史事件库：对给定事件文本，输出语义相似历史事件及其
    对指定指数的历史影响统计（模板匹配平均CAR/胜率 + 向量相似事件加权补充），
    并给出对当前事件的预测方向与置信度。用于分析宏观事件的历史影响类比。

    注意：事件库覆盖不足时返回"事件库暂无相似事件"，此时按自身知识继续分析。
    """
    try:
        from AI.eventStudy.db.connection import get_connection
        from AI.eventStudy.prediction.predictor import predict_impact

        conn = get_connection()
        try:
            result = predict_impact(
                conn,
                new_event_text=event_text,
                asset_ticker=asset_ticker,
                window_type=window_type,
            )
        finally:
            conn.close()
        return _format_result(result)
    except Exception as e:
        # 工具失败不阻塞报告生成（3.10.3）
        logger.warning(f"事件研究工具调用失败: {e}")
        return f"事件研究系统暂不可用（{e}）。请基于自身知识继续分析。"
