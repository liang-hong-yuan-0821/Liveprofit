"""
市场层 — Layer 1: 中国新闻分析师
聚焦 A 股市场微观结构和资金日历事件：
IPO 抽血、限售解禁抛压、期指交割日效应、两融余额、季节效应。
"""

import logging
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from AI.dataflows import interface as dataflow
from AI.templates import load_output_format
from AI.utils.prompts import DEFAULT_PROMPTS, system_message

logger = logging.getLogger(__name__)


def create_cn_news_analyst(llm, toolkit):

    def node(state):
        current_date = state["trade_date"]
        requested_date = state.get("requested_trade_date", current_date)
        date_correction = state.get("date_correction", "")
        logger.info(f"[中国新闻分析] 开始分析 @ {current_date}")

        count = state.get("cn_news_tool_call_count", 0)

        # 构建日期说明
        if date_correction:
            date_line = (
                f"分析日期：{current_date}\n"
                f"⚠️ 原始请求 {requested_date}，校正为 {current_date}（{date_correction}）。"
                f"请在报告中如实标注实际数据日期。\n"
            )
        else:
            date_line = f"分析日期：{current_date}\n"

        # 直接调用 dataflows 函数获取数据（使用纯日期，不做拼接）
        ipo_calendar = dataflow.get_ipo_calendar(current_date)
        share_unlock = dataflow.get_share_unlock_calendar(current_date)
        futures_expiry = dataflow.get_futures_expiry_calendar(current_date)
        margin_balance = dataflow.get_margin_trading_balance(current_date)

        output_format = load_output_format("market", "cn_news_analyst")

        prompt = ChatPromptTemplate.from_messages([
                system_message(
                    state.get("_current_node_id"),
                    lambda: DEFAULT_PROMPTS["market:CN News Analyst"]
                    .replace("{date_line}", date_line)
                    .replace("{output_format}", output_format),
                ),
            MessagesPlaceholder(variable_name="messages"),
        ])

        prompt = prompt.partial(
            ipo_calendar=ipo_calendar,
            share_unlock=share_unlock,
            futures_expiry=futures_expiry,
            margin_balance=margin_balance,
        )
        result = llm.invoke(prompt.format_messages(messages=state["messages"]))
        report = result.content
        calendar = _extract_event_calendar(report)

        logger.info(f"[中国新闻分析] 报告完成，长度: {len(report)}")
        return {
            "messages": [result],
            "cn_news_report": report,
            "market_event_calendar": calendar,
            "cn_news_tool_call_count": count + 1,
        }

    return node


def _extract_event_calendar(report: str) -> str:
    """从完整报告中提取事件日历速览结构化文本"""
    if not report or len(report) < 50:
        return report or ""

    import re
    # 尝试提取 ``` 代码块内容
    code_block = re.search(r'```\s*\n(.*?)\n```', report, re.DOTALL)
    if code_block:
        return code_block.group(1).strip()[:500]

    # 尝试提取 ## 〇 段落
    regime_section = re.search(
        r'##\s*〇[、，\s]*事件日历速览.*?\n(.*?)(?=\n##\s|\Z)',
        report, re.DOTALL
    )
    if regime_section:
        return regime_section.group(1).strip()[:500]

    # 兜底：返回报告前 500 字
    return report[:500]
