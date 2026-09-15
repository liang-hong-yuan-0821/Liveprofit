"""
板块层 — 板块新闻分析师
扫描全市场行业板块和概念板块，从"信息面 + 资金面"做横向对比：
行业涨跌排名、资金流向、概念热度、板块轮动判断。

T4 改造（方案第三章）：消费板块事件预取（作用域 sector + 命中既有扫描范围），
与既有政策新闻流**分块注入**（`policy_news` 当期事实流 / `sector_event_prefetch`
历史统计），历史统计块不得引用政策新闻流文本冒充 CAR 证据；节点返回值新增
`sector_events`。系统提示词文本规则属 T6（`AI/utils/prompts.py`）。
"""
import logging
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from AI.dataflows import interface as dataflow
from AI.dataflows import market_features as mf
from AI.sectorAgents.analysts.sector_event_prefetch import (
    prefetch_sector_event_study, scan_names_from_text,
)
from AI.utils.event_prefetch_core import (
    SCOPE_SECTOR, candidates_to_events, degraded_result, render_prefetch_block,
)
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

        # 板块事件预取：命中既有扫描范围（短名单 + 已获取的行业/概念扫描数据），
        # 不新增打名单或全市场扫描接口；无命中目标时返回空列表
        scan_refs = sector_scan_refs(state, industry_perf, concept_heat)
        try:
            prefetch = prefetch_sector_event_study(
                scan_refs=scan_refs, raw_news=policy_news, trade_date=current_date,
            )
            # 渲染 + 结构化事件组装纳入 try（评审 M17 残留）：任一异常不得中断节点
            prefetch_block = render_prefetch_block(
                prefetch, heading="板块事件研究历史统计（预取）")
            sector_events = candidates_to_events(
                prefetch.get("candidates") or [],
                event_scope=SCOPE_SECTOR, trade_date=current_date,
                scope_refs=prefetch.get("scope_refs") or (),
            )
        except Exception as e:  # 预取/渲染异常兜底：仅缺历史统计，不影响新闻分析
            logger.warning(f"[板块新闻分析] 事件研究预取异常（跳过历史统计）: {e}")
            prefetch = degraded_result(
                event_scope=SCOPE_SECTOR, trade_date=current_date,
                reason=f"预取异常: {e}",
            )
            prefetch_block = ""
            sector_events = []
        logger.info(
            f"[板块新闻分析] 预取完成：状态={prefetch.get('status')} "
            f"候选={prefetch.get('candidate_count')} 目标={len(prefetch.get('scope_refs') or [])}"
        )

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

        prompt = prompt.partial(**build_sector_prompt_variables(
            current_date=current_date,
            sector_market_context=sector_ctx,
            industry_perf=industry_perf,
            fund_flow=fund_flow,
            concept_heat=concept_heat,
            policy_news=policy_news,
            event_prefetch_block=prefetch_block,
        ))
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
            "sector_events": sector_events,
            "sector_news_tool_call_count": count + 1,
        }

    return node


def sector_scan_refs(state, *scan_texts) -> list[str]:
    """板块扫描范围（既有范围，不新增打名单接口）。

    来源：① 候选板块短名单（`sector_shortlist_structured`，概念名）；
    ② 节点已获取的扫描数据文本中的板块名（行业涨跌排名 / 概念热度）。
    名称由 `resolve_sector_refs` 解析为路由引用，解析不到的条目不计入路由。
    """
    refs = [item for item in (state.get("sector_shortlist_structured") or []) if item]
    refs.extend(scan_names_from_text(*scan_texts))
    return list(dict.fromkeys(str(item).strip() for item in refs if str(item).strip()))


def build_sector_prompt_variables(*, current_date, sector_market_context, industry_perf,
                                  fund_flow, concept_heat, policy_news,
                                  event_prefetch_block) -> dict:
    """板块新闻 Prompt 变量（**分块注入**）。

    - `policy_news`：当期产业政策新闻事实流（既有输入，原文）
    - `sector_event_prefetch`：事件研究历史统计块（预取，结构化统计）
    两块独立注入；历史统计块不得引用政策新闻流文本冒充 CAR 证据。
    """
    return {
        "current_date": current_date,
        "sector_market_context": sector_market_context,
        "industry_perf": industry_perf,
        "fund_flow": fund_flow,
        "concept_heat": concept_heat,
        "policy_news": policy_news,
        "sector_event_prefetch": event_prefetch_block,
    }


def _build_sector_market_context(state) -> str:
    """组装市场层中与板块分析相关的上下文摘要

    T6：优先消费结构化字段（`market_regime` + `market_event_calendar`，dict 原地
    替换原文本），经 `market_features` 紧凑渲染器注入（空/缺失返回空串，不做
    `len(str) > 10` 判定）；字段缺失时以全文报告前段作补充参考。
    """
    parts = []

    # 优先：结构化结论字段（dict → 紧凑 Markdown；渲染失败降级空串，不阻塞节点）
    try:
        market_regime = mf.format_market_regime_summary(state.get("market_regime"))
        event_calendar = mf.format_market_event_calendar_summary(
            state.get("market_event_calendar"))
    except Exception as e:  # 评审 m17/第2轮 finding 4：嵌套字段类型异常不阻断上下文组装
        logger.warning(f"[板块新闻分析] 市场上下文渲染失败（降级空串）: {e}")
        market_regime = event_calendar = ""
    if market_regime:
        parts.append(market_regime)
    if event_calendar:
        parts.append(event_calendar)

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
