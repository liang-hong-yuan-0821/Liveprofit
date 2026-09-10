"""
板块层 — 板块技术分析师
逐行业做技术面体检，分析对象是行业板块指数的日K线。
覆盖 A 股所有申万一级行业（约30个），对每条行业K线做均线/趋势/量价分析，
输出全行业技术状态矩阵。对科技行业做AI产业链专题深挖。
"""
import logging
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from AI.dataflows import interface as dataflow
from AI.utils.prompts import DEFAULT_PROMPTS, system_message
from AI.templates import load_output_format
from AI.sectorAgents.analysts.structured_list import (
    extract_sector_structured_list,
    merge_sector_structured_lists,
)

logger = logging.getLogger(__name__)


def create_sector_tech_analyst(llm, toolkit, enable_structured_list=False):

    def node(state):
        current_date = state["trade_date"]
        logger.info(f"[板块技术分析] 开始分析 @ {current_date}")

        # 组装市场层上下文摘要 + 板块新闻分析结论
        sector_ctx = _build_sector_market_context(state)
        # 优先消费结构化短名单（完整），完整报告作为补充
        shortlist = state.get("sector_shortlist", "")
        if shortlist and len(shortlist) > 10:
            sector_ctx += f"\n\n## 候选板块短名单（完整 — 需逐一做技术确认）\n{shortlist}"
        else:
            news_report = state.get("sector_news_report", "")
            if news_report and len(news_report) > 20:
                sector_ctx += f"\n\n## 板块新闻分析结论（参考）\n{news_report[:1000]}"

        count = state.get("sector_tech_tool_call_count", 0)

        # 直接调用 dataflows 函数获取数据（全部调用，LLM自行判断哪些行业适用AI产业链数据）
        horizon = dataflow.get_sector_horizon_screening(days=120)
        industry_perf = dataflow.get_industry_sector_performance(days=20)
        tech_screening = dataflow.get_sector_technical_screening(days=60)
        rel_strength = dataflow.get_sector_relative_strength(days=20)
        ai_chain = dataflow.get_all_concept_boards(days=10)
        tech_corr = dataflow.analyze_tech_correlation(days=10)

        output_format = load_output_format("sector", "sector_tech_analyst")

        prompt = ChatPromptTemplate.from_messages([
                system_message(
                    state.get("_current_node_id"),
                    lambda: DEFAULT_PROMPTS["sector:Sector Tech Analyst"].replace(
                        "{output_format}", output_format
                    ),
                ),
            MessagesPlaceholder(variable_name="messages"),
        ])

        prompt = prompt.partial(
            current_date=current_date,
            sector_market_context=sector_ctx,
            horizon=horizon,
            industry_perf=industry_perf,
            tech_screening=tech_screening,
            rel_strength=rel_strength,
            ai_chain=ai_chain,
            tech_corr=tech_corr,
        )
        result = llm.invoke(prompt.format_messages(messages=state["messages"]))
        report = result.content
        confirm = _extract_sector_tech_confirm(report)

        logger.info(f"[板块技术分析] 报告完成，长度: {len(report)}")
        # 仅全市场模式启用（单票模式行为与现状一致）；与 news 分析师的清单合并（news 先执行，本节点在其后）
        structured = merge_sector_structured_lists(
            state.get("sector_shortlist_structured", []),
            extract_sector_structured_list(report) if enable_structured_list else [],
        )
        return {
            "messages": [result],
            "sector_tech_report": report,
            "sector_tech_confirm": confirm,
            "sector_shortlist_structured": structured,
            "sector_tech_tool_call_count": count + 1,
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


def _extract_sector_tech_confirm(report: str) -> str:
    """从完整报告中提取候选板块技术确认结构化文本"""
    if not report or len(report) < 50:
        return report or ""

    import re
    # 尝试提取 ``` 代码块内容
    code_block = re.search(r'```\s*\n(.*?)\n```', report, re.DOTALL)
    if code_block:
        return code_block.group(1).strip()[:800]

    # 尝试提取 ## 〇 段落
    confirm_section = re.search(
        r'##\s*〇[、，\s]*候选板块技术确认.*?\n(.*?)(?=\n##\s|\Z)',
        report, re.DOTALL
    )
    if confirm_section:
        return confirm_section.group(1).strip()[:800]

    # 兜底：返回报告前 800 字
    return report[:800]
