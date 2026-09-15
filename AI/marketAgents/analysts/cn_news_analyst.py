"""
市场层 — Layer 1: 中国新闻分析师
聚焦 A 股市场微观结构和资金日历事件：
IPO 抽血、限售解禁抛压、期指交割日效应、两融余额、季节效应。

数据来源（T5）：`AI/dataflows/market_features.build_cn_event_calendar_features`
（结构化日历特征：窗口压力分级 + 驱动 + 关键日期 + 缺失标注，只给日历事实）。

T6 改造（方案第十一章）：删除过渡期 `_SLOT_SECTIONS` 分槽映射，特征证据块收敛
为单一 `{calendar_features}` 槽位；输出格式锚点（含 JSON 结论块）走 partial 值注入。
"""

import logging

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from AI.dataflows import market_features as mf
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

        # 特征层：结构化资金日历特征（含 as_of_date / data_quality / 未来数据防护）。
        # 构建/渲染异常统一降级为 features={} + 「不可用」证据块（不阻断全图，
        # 与 `international_news_analyst` 同口径）；`features={}` 经 `node_data_quality`
        # 落 degradation_level=insufficient → 门控判据 (b) → caution（评审 m17）
        try:
            features = mf.build_cn_event_calendar_features(current_date)
        except Exception as e:
            logger.warning(f"[中国新闻分析] 特征构建异常（按数据缺失处理）: {e}")
            features = {}
        try:
            evidence = mf.format_cn_event_calendar_evidence(features)
        except Exception as e:  # 渲染异常同样落「不可用」块（format 对空输入恒不抛）
            logger.warning(f"[中国新闻分析] 特征渲染异常: {e}")
            evidence = mf.format_cn_event_calendar_evidence({})
        logger.info(
            f"[中国新闻分析] 特征层证据就绪：as_of={features.get('as_of_date')}，"
            f"降级级别={(features.get('data_quality') or {}).get('degradation_level')}，"
            f"缺失={features.get('missing_inputs')}"
        )

        # 构建日期说明
        if date_correction:
            date_line = (
                f"分析日期：{current_date}\n"
                f"⚠️ 原始请求 {requested_date}，校正为 {current_date}（{date_correction}）。"
                f"请在报告中如实标注实际数据日期。\n"
            )
        else:
            date_line = f"分析日期：{current_date}\n"

        output_format = load_output_format("market", "cn_news_analyst")

        prompt = ChatPromptTemplate.from_messages([
                system_message(
                    state.get("_current_node_id"),
                    lambda: DEFAULT_PROMPTS["market:CN News Analyst"]
                    .replace("{date_line}", date_line),
                ),
            MessagesPlaceholder(variable_name="messages"),
        ])

        # `{output_format}` 以 partial 值注入（而非文本替换注入）：输出格式模板含
        # 字面 JSON 花括号，文本替换会把模板内容并入 langchain 模板再解析 → KeyError。
        prompt = prompt.partial(calendar_features=evidence, output_format=output_format)
        result = llm.invoke(prompt.format_messages(messages=state["messages"]))
        report = result.content

        # 结构化输出（第十二章 State 表）：三级别资金日历（level/score/drivers/
        # key_dates/confidence）+ 节点级 data_quality 子字段（判据 (b)）。
        calendar = mf.parse_market_event_calendar(report)
        if isinstance(calendar, dict):
            calendar["data_quality"] = mf.node_data_quality(features, calendar)
            if not calendar.get("as_of_date"):
                calendar["as_of_date"] = features.get("as_of_date")

        logger.info(
            f"[中国新闻分析] 报告完成，长度: {len(report)}；"
            f"级别={ {k: (calendar.get(k) or {}).get('level') for k in ('short_term', 'wave', 'long_term')} }；"
            f"解析={calendar.get('parse_status')}"
        )
        return {
            "messages": [result],
            "cn_news_report": report,
            "market_event_calendar": calendar,
            "cn_news_tool_call_count": count + 1,
        }

    return node
