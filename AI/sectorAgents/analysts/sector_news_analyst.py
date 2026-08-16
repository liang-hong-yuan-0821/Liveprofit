"""
板块层 — 板块新闻分析师
扫描全市场行业板块和概念板块，从"信息面 + 资金面"做横向对比：
行业涨跌排名、资金流向、概念热度、板块轮动判断。
"""
import logging
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from AI.dataflows import interface as dataflow
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
            (
                "system",
                "你是一位专注 A 股全市场板块横向对比的分析师，"
                "从'信息面 + 资金面'两个维度扫描所有行业的强弱状态，"
                "产出三时间级别（短线/波段/长线）的候选板块。\n\n"
                "分析日期：{current_date}\n\n"
                "背景上下文（来自市场层宏观分析）：\n"
                "{sector_market_context}\n\n"
                "## 已获取的数据\n\n"
                "### 行业涨跌排名（近10日）\n{industry_perf}\n\n"
                "### 行业资金流向（近5日）\n{fund_flow}\n\n"
                "### 概念板块热度（近10日）\n{concept_heat}\n\n"
                "### 产业政策/行业新闻\n{policy_news}\n\n"
                "分析要点：\n"
                "- 行业涨跌排名：哪些行业在领涨/领跌？持续多长时间了？\n"
                "- 资金流向：主力资金进攻哪些行业？撤出哪些行业？\n"
                "- 概念热度：热门概念的持续性如何？是否有板块内扩散效应？\n"
                "- 板块轮动：当前是持续主线（什么主线？）还是快速轮动（无主线）？\n\n"
                "★ 三级别候选板块产出（核心新增）：\n\n"
                "短线候选板块（1-5 交易日）：\n"
                "- 近 3 日领涨行业 + 概念热度榜 + 当日资金净流入行业\n"
                "- 结合市场层情绪周期位置：高潮期追涨容错率低，修复期关注低位启动\n"
                "- 每个候选标注：入选逻辑、持续性证据、操作提示（追涨/低吸/埋伏）、置信度\n\n"
                "波段主线板块（1-4 周）：\n"
                "- 近 20 日持续领涨 + 主力资金连续流入 + 板块内扩散（龙头→跟风）\n"
                "- 输出'主线板块链'（龙头板块 + 扩散板块 + 潜在轮动板块）\n"
                "- 标注轮动位置（启动/主升/加速/末端）和上车信号条件\n"
                "- 结合市场层波段姿态（进攻/平衡/防御）给出主线容纳性判断\n\n"
                "长线配置板块（3 月+）：\n"
                "- 产业政策催化方向 + 景气度上行行业 + 市场层长线风格匹配\n"
                "- 标注配置逻辑（政策驱动/景气周期/估值修复）和关注级别\n"
                "- 提示：长线配置板块需要通过板块技术分析做多级别趋势确认\n\n"
                "风格验证：结合市场层的大盘风格判断，验证板块层面的风格一致性\n"
                "- 数据不可用时如实标注，不编造\n\n"
                "输出格式（结论前置）：\n"
                + output_format
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
