"""
市场层 — Layer 0b: 国际新闻影响分析师
把上游已确认事件映射为“全球风险 → 中国资产 → A 股行业”的条件化传导结论，
输出结构化 `global_risk_assessment`。不依赖 ticker，仅使用 trade_date。

T6 改造（方案第六章 / 第十一章）：
- 事件类证据**只消费** `international_events`（结构化）；`international_event_report`
  仅作展示/调试，不注入本节点提示词；
- 已删除固定事件研究调用 `_load_event_study_data`（历史统计由国际事件提取节点
  在 LLM 前预取）；本节点**不注册、不调用事件研究工具**；
- 风险价格证据来自特征层 `build_global_risk_features`（缺失过半时
  `risk_appetite`/`systemic_risk` 强制 `insufficient`，见
  `market_features.parse_global_risk_assessment` 的 `coverage` 参数）；
- 输出 `global_risk_assessment`（第六章字段：`as_of_date`/`risk_appetite`/
  `systemic_risk`/`confidence`/`evidence`/`event_transmissions`/`data_quality`）。
"""

import logging
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from AI.dataflows import market_features as mf
from AI.templates import load_output_format
from AI.utils.prompts import DEFAULT_PROMPTS, system_message

logger = logging.getLogger(__name__)

# 无结构化事件输入时的提示（不得凭常识补写事件事实）
_NO_EVENTS_BLOCK = (
    "（无结构化事件输入：本轮未产出 `international_events`，"
    "不得凭常识补写事件事实或历史统计。）"
)


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

        # 事件类证据：只消费上游结构化事件（全文报告仅展示/调试，不进提示词）
        events = state.get("international_events") or []
        try:
            events_block = mf.format_events_summary(events) or _NO_EVENTS_BLOCK
        except Exception as e:  # 渲染异常不阻断影响分析（评审 m17）
            logger.warning(f"[国际新闻影响分析] 结构化事件渲染异常: {e}")
            events_block = _NO_EVENTS_BLOCK
        logger.info(f"[国际新闻影响分析] 结构化事件输入 {len(events)} 条")

        # 风险价格证据：特征层（缺失/降级由特征层标注；异常不阻断影响分析）。
        # 构建与渲染**都**入 try（评审 m17）：仅构建入 try 时渲染异常仍会打断节点，
        # 渲染失败回退 features={} →「特征不可用」块 → 判据 (b) → caution。
        try:
            features = mf.build_global_risk_features(current_date)
        except Exception as e:
            logger.warning(f"[国际新闻影响分析] 风险价格特征构建异常（按缺失处理）: {e}")
            features = {}
        try:
            risk_block = mf.format_global_risk_evidence(features)
        except Exception as e:  # 渲染异常同样落「不可用」块
            logger.warning(f"[国际新闻影响分析] 风险价格特征渲染异常: {e}")
            risk_block = mf.format_global_risk_evidence({})
        coverage = ((features.get("derived_metrics") or {}).get("coverage")
                    if isinstance(features, dict) else None)
        logger.info(
            f"[国际新闻影响分析] 风险价格特征就绪：as_of={features.get('as_of_date')}，"
            f"覆盖度={(coverage or {}).get('ratio')}"
            f"（{(coverage or {}).get('status')}，缺失 {(coverage or {}).get('missing')}）"
        )

        output_format = load_output_format("market", "international_news_analyst")

        prompt = ChatPromptTemplate.from_messages([
                system_message(
                    state.get("_current_node_id"),
                    lambda: DEFAULT_PROMPTS["market:International News Analyst"]
                    .replace("{date_line}", date_line),
                ),
            MessagesPlaceholder(variable_name="messages"),
        ])

        # `{output_format}` 以 partial 值注入（而非文本替换注入）：输出格式模板含
        # 字面 JSON 花括号，文本替换会把模板内容并入 langchain 模板再解析 → KeyError。
        prompt = prompt.partial(
            international_events=events_block,
            global_risk_features=risk_block,
            output_format=output_format,
        )
        result = llm.invoke(prompt.format_messages(messages=state["messages"]))
        report = result.content

        # 结构化输出（第十二章 State 表）：核心风险价格缺失过半时由解析层强制
        # `insufficient`（第六章判定约束），节点级 data_quality 子字段标注判据 (d)。
        assessment = mf.parse_global_risk_assessment(report, coverage=coverage)
        if isinstance(assessment, dict):
            extra = None
            if (assessment.get("systemic_risk") == "insufficient"
                    or assessment.get("risk_appetite") == "信息不足"):
                extra = {"degradation_level": "insufficient"}
            assessment["data_quality"] = mf.node_data_quality(
                features, assessment, insufficient_keys=(), extra=extra)
            if not assessment.get("as_of_date"):
                assessment["as_of_date"] = features.get("as_of_date")

        logger.info(
            f"[国际新闻影响分析] 报告完成，长度: {len(report)}；"
            f"风险偏好={assessment.get('risk_appetite')}，"
            f"系统性风险={assessment.get('systemic_risk')}，"
            f"置信度={assessment.get('confidence')}；"
            f"解析={assessment.get('parse_status')}"
        )
        return {
            "messages": [result],
            "international_news_report": report,
            "global_risk_assessment": assessment,
            "international_news_tool_call_count": count + 1,
        }

    return node
