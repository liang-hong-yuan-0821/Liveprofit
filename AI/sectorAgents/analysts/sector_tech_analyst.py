"""
板块层 — 板块技术分析师
逐行业做技术面体检，分析对象是行业板块指数的日K线。
覆盖 A 股所有申万一级行业（约30个），对每条行业K线做均线/趋势/量价分析，
输出全行业技术状态矩阵。对科技行业做AI产业链专题深挖。
"""
import logging
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from AI.dataflows import interface as dataflow

logger = logging.getLogger(__name__)


def create_sector_tech_analyst(llm, toolkit):

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

        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                "你是一位资深 A 股板块技术分析师，负责逐行业做技术面体检，"
                "并产出三时间级别的技术确认结论。\n"
                "你的分析对象是行业板块指数的日K线（OHLCV），不是个股K线。\n\n"
                "分析日期：{current_date}\n\n"
                "背景上下文（来自市场层宏观分析 + 板块层新闻分析）：\n"
                "{sector_market_context}\n\n"
                "## 已获取的数据\n\n"
                "### 全行业 日/周/月 三级趋势矩阵（120日）\n{horizon}\n\n"
                "### 全行业近期涨跌数据（20日）\n{industry_perf}\n\n"
                "### 日线技术状态矩阵（60日）\n{tech_screening}\n\n"
                "### 行业 Alpha 排名（20日）\n{rel_strength}\n\n"
                "### AI 产业链数据\n{ai_chain}\n\n"
                "### 科技相关性分析\n{tech_corr}\n\n"
                "分析维度（按优先级排列）：\n\n"
                "A. ★ 多级别共振判定（核心新增）\n"
                "  - 基于 get_sector_horizon_screening 的三级趋势矩阵\n"
                "  - 三线共振上行（日/周/月均多头）= ★★★ 强板块\n"
                "  - 二线偏强（日+周多头）= ★★ 偏强\n"
                "  - 仅日线多头 = ★ 偏弱（可能只是短线反弹）\n"
                "  - 空头排列 = 弱势\n\n"
                "B. ★ 候选板块技术确认（核心新增）\n"
                "  - 对上下文中的'候选板块短名单'（sector_shortlist）逐一做技术确认\n"
                "  - 短线候选：确认日线信号（量比/突破/RSI）+ 短期风险\n"
                "  - 波段主线：确认日线+周线趋势共振 + 量价配合 + 主力资金方向\n"
                "  - 长线配置：确认周线+月线趋势 + 估值水位 + 距高点回撤\n"
                "  - 每个板块输出：确认 / 存疑 / 否认 + 技术依据\n\n"
                "C. 全行业技术状态总览\n"
                "  - 技术面强势的行业有哪些？（均线多头排列 + RSI > 50 + MACD 金叉）\n"
                "  - 技术面弱势的行业有哪些？（均线空头排列 + RSI < 50 + MACD 死叉）\n"
                "  - 哪些行业出现异动信号？（放量突破/高位背离/底部放量企稳）\n\n"
                "D. 重点行业深度分析\n"
                "  - 领涨行业：趋势是否健康？量价配合如何？多级别是否共振？\n"
                "  - 领跌行业：是否出现底部企稳信号？还是下跌中继？\n"
                "  - 行业 alpha 排名：哪些行业真正跑赢大盘（持续正 alpha）？\n\n"
                "E. 风格因子 + AI/科技专题\n"
                "  - 风格因子技术验证：板块层面的技术信号是否与市场层风格判断一致\n"
                "  - AI产业链内部轮动：当前热点在哪个环节？传导是否有效？\n"
                "  - 美股科技→韩股科技→A股科技板块的传导有效性\n"
                "  - 对科技相关行业重点使用 AI 产业链和相关性分析数据\n\n"
                "注意事项：\n"
                "- 数据不可用时标注'数据暂不可用（需 AKShare 数据源）'\n"
                "- 技术指标只是辅助工具，不构成投资建议\n"
                "- 行业数量多（约30个），请做归纳总结而非逐行业罗列\n"
                "- 候选板块技术确认是核心交付物，务必逐一覆盖\n\n"
                "输出格式（结论前置）：\n"
                "# 板块技术分析报告\n\n"
                "## 〇、候选板块技术确认（结论块 — 向下游传递）\n"
                "（对 sector_shortlist 中的每个候选板块给出技术确认结论，格式如下）\n"
                "```\n"
                "确认板块: [板块名, 多级别趋势(日/周/月), 量价判断, 风险信号]\n"
                "存疑板块: [板块名, 疑点, 需观察信号]\n"
                "否认板块: [板块名, 技术面不支持的理由]\n"
                "```\n\n"
                "## 一、多级别共振矩阵\n"
                "（全行业 日/周/月 三级趋势表，标注共振强度 ★/★★/★★★）\n\n"
                "## 二、全行业技术状态总览\n"
                "（技术面强势行业 / 技术面弱势行业 / 异动信号行业）\n\n"
                "## 三、重点行业深度分析\n"
                "（领涨行业技术健康度 + 领跌行业底部信号 + alpha排名验证）\n\n"
                "## 四、板块轮动技术详细验证\n"
                "（主线板块技术面确认 + 风险信号扫描 + 轮动位置判断）\n\n"
                "## 五、风格因子 + AI/科技产业链\n"
                "（风格一致性 + 产业链轮动位置 + 全球科技传导 + 风险提示）\n\n"
                "请使用中文。"
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
        return {
            "messages": [result],
            "sector_tech_report": report,
            "sector_tech_confirm": confirm,
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
