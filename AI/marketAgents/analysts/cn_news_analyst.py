"""
市场层 — Layer 1: 中国新闻分析师
聚焦 A 股市场微观结构和资金日历事件：
IPO 抽血、限售解禁抛压、期指交割日效应、两融余额、季节效应。
"""

import logging
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from AI.dataflows import interface as dataflow
from AI.templates import load_output_format

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
            (
                "system",
                "你是一位专注 A 股市场微观结构的分析师，聚焦资金日历事件对三个时间级别资金面的影响。\n\n"
                + date_line + "\n"
                "## 已获取的数据\n\n"
                "### IPO日历\n{ipo_calendar}\n\n"
                "### 限售股解禁日历\n{share_unlock}\n\n"
                "### 期指/期权交割日\n{futures_expiry}\n\n"
                "### 两融余额\n{margin_balance}\n\n"
                "分析要点：\n"
                "- 大盘 IPO/新股上市 → 打新资金抽血/虹吸效应，标注大市值新股\n"
                "- 限售股解禁 → 潜在抛压来源，标注解禁市值规模\n"
                "- 期货/期权交割日 → 到期日效应，警惕尾盘异常波动\n"
                "- 两融余额变化 → 杠杆资金松紧信号\n"
                "- 季节效应 → 季末排名调仓冲击、长假前避险效应\n"
                "- 数据不可用时如实标注，不编造\n\n"
                "三时间级别分析框架：\n\n"
                "短线日历（未来 1-5 交易日）：\n"
                "- 近 5 日 IPO 抽血强度（大市值新股数量）\n"
                "- 近 5 日限售股解禁抛压（解禁市值规模）\n"
                "- 期货/期权交割日临近程度\n"
                "- 两融余额异动（单日大增/大减）\n"
                "- 短线风险评级（高/中/低）+ 关键时点清单\n\n"
                "波段日历（未来 1-4 周 ≈ 20 交易日）：\n"
                "- 解禁高峰窗口（集中解禁期）\n"
                "- 季报/年报披露窗口（业绩雷/惊喜）\n"
                "- 重大政策会议/事件窗口\n"
                "- 季末调仓冲击\n"
                "- 波段风险评级（高/中/低）+ 关键窗口清单\n\n"
                "长线日历（未来 1-3 月 ≈ 60 交易日）：\n"
                "- 宏观数据发布窗口（CPI/PMI/社融等）\n"
                "- 流动性政策预期（降准/降息窗口）\n"
                "- 年报季/分红季\n"
                "- 长线风险评级（高/中/低）+ 关键窗口清单\n\n"
                "每个级别输出：事件密度（高/中/低）+ 资金面压力评分（1-5）\n\n"
                "输出格式（结论前置）：\n"
                + output_format
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
