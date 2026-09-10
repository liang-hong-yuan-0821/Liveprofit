"""
板块层 — 板块新闻分析师
扫描全市场行业板块和概念板块，从"信息面 + 资金面"做横向对比：
行业涨跌排名、资金流向、概念热度、板块轮动判断。
"""
import logging
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from AI.dataflows import interface as dataflow
from AI.utils.prompts import DEFAULT_PROMPTS, system_message
from AI.templates import load_output_format
from AI.sectorAgents.analysts.structured_list import extract_sector_structured_list

logger = logging.getLogger(__name__)


def create_sector_news_analyst(llm, toolkit, enable_structured_list=False):

    def node(state):
        current_date = state["trade_date"]
        logger.info(f"[板块新闻分析] 开始分析 @ {current_date}")

        # 组装市场层上下文摘要
        sector_ctx = _build_sector_market_context(state)

        count = state.get("sector_news_tool_call_count", 0)

        # 直接调用 dataflows 函数获取数据
        industry_perf = dataflow.get_industry_sector_performance(days=10)
        fund_flow = dataflow.get_sector_fund_flow(days=5)
        concept_heat = dataflow.get_concept_board_heat(days=10)
        policy_news = dataflow.get_industry_policy_news(current_date)

        output_format = load_output_format("sector", "sector_news_analyst")

        prompt = ChatPromptTemplate.from_messages([
                system_message(
                    state.get("_current_node_id"),
                    lambda: DEFAULT_PROMPTS["sector:Sector News Analyst"].replace(
                        "{output_format}", output_format
                    ),
                ),
            MessagesPlaceholder(variable_name="messages"),
        ])

        prompt = prompt.partial(
            current_date=current_date,
            sector_market_context=sector_ctx,
            industry_perf=industry_perf,
            fund_flow=fund_flow,
            concept_heat=concept_heat,
            policy_news=policy_news,
        )
        result = llm.invoke(prompt.format_messages(messages=state["messages"]))
        report = result.content
        shortlist = _extract_sector_shortlist(report)

        logger.info(f"[板块新闻分析] 报告完成，长度: {len(report)}")
        # 仅全市场模式启用结构化清单（单票模式保持行为与现状完全一致，不多打名单接口）
        structured = extract_sector_structured_list(report) if enable_structured_list else []
        return {
            "messages": [result],
            "sector_news_report": report,
            "sector_shortlist": shortlist,
            "sector_shortlist_structured": structured,
            "sector_news_tool_call_count": count + 1,
        }

    return node


def _build_sector_market_context(state) -> str:
    """组装市场层中与板块分析相关的上下文摘要

    优先消费完整结构化字段（market_regime + market_event_calendar），
    避免截断丢失关键结论。完整报告作为补充参考。
    """
    parts = []

    # 优先：结构化结论字段（完整，不截断）
    market_regime = state.get("market_regime", "")
    event_calendar = state.get("market_event_calendar", "")
    if market_regime and len(market_regime) > 10:
        parts.append(f"## 大盘环境判定（完整）\n{market_regime}")
    if event_calendar and len(event_calendar) > 10:
        parts.append(f"## 资金日历（完整）\n{event_calendar}")

    # 补充：完整报告的前段（参考用）
    cn_tech = state.get("cn_tech_report", "")
    cn_news = state.get("cn_news_report", "")
    intl_news = state.get("international_news_report", "")

    if not market_regime and cn_tech and len(cn_tech) > 20:
        parts.append(f"## 大盘环境（参考）\n{cn_tech[:800]}")
    if not event_calendar and cn_news and len(cn_news) > 20:
        parts.append(f"## 资金日历（参考）\n{cn_news[:500]}")
    if intl_news and len(intl_news) > 20:
        parts.append(f"## 国际宏观\n{intl_news[:500]}")

    return "\n\n".join(parts) if parts else "（市场层数据暂不可用）"


def _extract_sector_shortlist(report: str) -> str:
    """从完整报告中提取候选板块速览结构化文本"""
    if not report or len(report) < 50:
        return report or ""

    import re
    # 尝试提取 ``` 代码块内容
    code_block = re.search(r'```\s*\n(.*?)\n```', report, re.DOTALL)
    if code_block:
        return code_block.group(1).strip()[:1200]

    # 尝试提取 ## 〇 段落
    shortlist_section = re.search(
        r'##\s*〇[、，\s]*候选板块速览.*?\n(.*?)(?=\n##\s|\Z)',
        report, re.DOTALL
    )
    if shortlist_section:
        return shortlist_section.group(1).strip()[:1200]

    # 兜底：返回报告前 1200 字
    return report[:1200]
