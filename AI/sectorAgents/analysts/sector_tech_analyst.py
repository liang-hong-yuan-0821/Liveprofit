"""
板块层 — 板块技术分析师
逐行业做技术面体检，分析对象是行业板块指数的日K线。
覆盖 A 股所有申万一级行业（约30个），对每条行业K线做均线/趋势/量价分析，
输出全行业技术状态矩阵。对科技行业做AI产业链专题深挖。
"""
import logging
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from AI.dataflows import interface as dataflow
from AI.dataflows import market_features as mf
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

        # 组装市场层上下文摘要（技术面结构化结论；不含事件/新闻文本）
        sector_ctx = _build_sector_market_context(state)
        # 优先消费结构化短名单（完整，仅板块名/代码，非新闻正文）
        shortlist = state.get("sector_shortlist", "")
        if shortlist and len(shortlist) > 10:
            sector_ctx += f"\n\n## 候选板块短名单（完整 — 需逐一做技术确认）\n{shortlist}"
        # 技术隔离（方案第十一章 / 评审 M7）：短名单缺失时**不注入**
        # `sector_news_report` 新闻正文——技术节点只接收技术面数据与结构化范围

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
    """组装市场层中与板块分析相关的上下文摘要（技术面确认用）

    T6 + 评审 M7：消费 `market_regime`/`market_event_calendar` 结构化 dict
    （紧凑渲染）；按方案第十一章技术隔离，**禁止事件/CAR/新闻原文进入技术判断**，
    结构化字段缺失时只标注「数据缺失」，不再以新闻报告正文（`cn_news_report` /
    `sector_news_report`）作降级参考。唯一允许的全文降级参考是技术面报告
    （`cn_tech_report`）。
    """
    parts = []

    # 优先：结构化结论字段（dict → 紧凑 Markdown；渲染失败降级空串，不阻塞节点）
    try:
        market_regime = mf.format_market_regime_summary(state.get("market_regime"))
        event_calendar = mf.format_market_event_calendar_summary(
            state.get("market_event_calendar"))
    except Exception as e:  # 评审 m17/第2轮 finding 4：嵌套字段类型异常不阻断上下文组装
        logger.warning(f"[板块技术分析] 市场上下文渲染失败（降级空串）: {e}")
        market_regime = event_calendar = ""
    if market_regime:
        parts.append(market_regime)
    if event_calendar:
        parts.append(event_calendar)

    # 补充：全文报告降级参考——仅技术面报告（事件/信息/新闻报告一律不注入）
    cn_tech = state.get("cn_tech_report", "")
    if not market_regime and cn_tech and len(cn_tech) > 20:
        parts.append(f"## 大盘环境（参考）\n{cn_tech[:800]}")
    if not event_calendar:
        # 降级只给「数据缺失」标注（评审 M7）：不注入 cn_news_report 正文
        parts.append("## 资金日历\n（数据缺失：market_event_calendar 结构化字段不可用；"
                     "事件/新闻文本不注入技术节点）")

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
