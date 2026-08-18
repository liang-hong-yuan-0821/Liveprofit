"""
市场层 — Layer 0b: 国际新闻影响分析师
基于上游事件提取结果（含历史案例），分析事件的传导链条和系统性风险。
由原 international_news_analyst 拆分而来，专注影响分析。
不依赖 ticker，仅使用 trade_date。

事件研究系统数据（2026-08-18）：节点执行时**提前请求**——构建 prompt 前
主动检索事件库历史相似事件影响，结果直接写进 prompt（与上游事件提取结果
同为静态注入，不依赖 LLM 工具循环）。
"""

import logging
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from AI.templates import load_output_format

logger = logging.getLogger(__name__)

EVENT_STUDY_TICKER = "000300.SH"      # 检索用的目标指数（市场基准：沪深300）
EVENT_STUDY_WINDOW = "post_event_5d"  # 检索窗口（事件后 5 日）
QUERY_TEXT_MAX_LEN = 500              # 检索文本上限（上游报告截取前 500 字）


def _load_event_study_data(event_report: str) -> str:
    """提前请求事件研究系统：检索历史相似事件影响，返回写入 prompt 的文本。

    调用失败/事件库无数据/无事件文本时返回明确提示，不阻塞报告生成。
    """
    if not event_report or event_report.startswith("（"):
        return "（无事件文本，跳过事件库检索）"
    try:
        from AI.eventStudy.integration.langgraph_tool import search_similar_events
        result = search_similar_events.invoke({
            "event_text": event_report[:QUERY_TEXT_MAX_LEN],
            "asset_ticker": EVENT_STUDY_TICKER,
            "window_type": EVENT_STUDY_WINDOW,
        })
        return result
    except Exception as e:
        logger.warning(f"事件研究数据提前请求失败: {e}")
        return f"事件研究系统暂不可用（{e}）。请基于自身知识继续分析。"


def create_international_news_analyst(llm, toolkit):

    def node(state):
        current_date = state["trade_date"]
        requested_date = state.get("requested_trade_date", current_date)
        date_correction = state.get("date_correction", "")
        logger.info(f"[国际新闻影响分析] 开始分析 @ {current_date}")

        count = state.get("international_news_tool_call_count", 0)

        # 构建日期说明
        if date_correction:
            date_line = (
                f"分析日期：{current_date}\n"
                f"⚠️ 原始请求 {requested_date}，校正为 {current_date}。"
                f"请在报告中如实标注实际数据日期。\n"
            )
        else:
            date_line = f"分析日期：{current_date}\n"

        # 读取上游事件提取结果（含事件列表 + 历史案例）
        event_report = state.get("international_event_report", "（事件提取数据暂不可用）")

        # 提前请求：检索事件库历史相似事件影响，结果直接写进 prompt
        event_study_data = _load_event_study_data(event_report)

        output_format = load_output_format("market", "international_news_analyst")

        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                "你是一位资深国际宏观策略分析师，专注于分析宏观事件的市场影响。\n\n"
                + date_line + "\n"
                "## 上游事件提取结果（含历史案例）\n"
                "{event_report}\n\n"
                "## 事件研究系统：历史相似事件影响数据\n"
                "{event_study_data}\n\n"
                "任务：基于上述事件提取结果，分析事件对全球金融市场的传导链条和系统性风险。\n\n"
                "分析要求：\n"
                "- 完整传导链条：事件 → 中间变量（利率/汇率/大宗商品）→ 受影响行业方向\n"
                "- 必须考虑存量资金下的'逻辑利好 vs 资金利空'背离（资金虹吸/跷跷板效应）\n"
                "- 历史案例类比需给出相似度 + 当时市场反应 + 对当下的参考意义\n"
                "- 事件研究系统数据为历史相似事件对 A 股指数的统计影响（平均 CAR/胜率/"
                "加权预测），可作定量参考；事件库暂无相似结果时按自身知识继续分析，不阻塞报告\n"
                "- 明确标注系统性风险等级（低/中/高）和流动性危机信号\n"
                "- 数据不可用时如实标注，不编造\n"
                "- ⚠️ 宏观事件解读属于定性分析，应明确标注'基于公开信息的方向性判断，不构成投资建议'\n\n"
                "输出格式（结论前置）：\n"
                + output_format
            ),
            MessagesPlaceholder(variable_name="messages"),
        ])

        prompt = prompt.partial(
            event_report=event_report,
            event_study_data=event_study_data,
        )
        result = llm.invoke(prompt.format_messages(messages=state["messages"]))
        report = result.content

        logger.info(f"[国际新闻影响分析] 报告完成，长度: {len(report)}")
        return {
            "messages": [result],
            "international_news_report": report,
            "international_news_tool_call_count": count + 1,
        }

    return node
