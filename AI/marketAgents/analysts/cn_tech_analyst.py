"""
市场层 — Layer 1: 中国技术分析师 ★ 主战场
A 股大盘技术面全景分析：7 指数量价、市场宽度（情绪温度计）、
资金流向（北向/主力）、风格因子（大小盘/成长价值）。

数据来源（T5）：`AI/dataflows/market_features.build_cn_technical_features`
（结构化特征 dict + Markdown 证据块）。本节点只消费技术/宽度/资金/估值/流动性
特征，**不接收事件、CAR、新闻原文或信息报告输入**（技术隔离，见方案第十章：
技术节点 Prompt 组装不含事件/新闻报告内容）。

T6 改造（方案第十一章）：
- 删除过渡期 `_SLOT_SECTIONS` 分槽映射，特征证据块收敛为单一
  `{technical_features}` 槽位；
- 节点**不再派生 `risk_gate`**：第十二章有序规则表门控由市场子图末尾的
  `derive_risk_gate` 纯代码节点（`market_layer_graph.py`）消费
  `global_risk_assessment`/`market_regime`/`market_data_quality` 派生；
- 输出格式锚点（含 JSON 结论块）走 partial 值注入，见下方注释。
"""

import logging

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from AI.dataflows import market_features as mf
from AI.templates import load_output_format
from AI.utils.prompts import DEFAULT_PROMPTS, system_message

logger = logging.getLogger(__name__)


def create_cn_tech_analyst(llm, toolkit):

    def node(state):
        current_date = state["trade_date"]
        requested_date = state.get("requested_trade_date", current_date)
        date_correction = state.get("date_correction", "")
        logger.info(f"[中国技术分析] 开始分析 @ {current_date}")

        count = state.get("cn_tech_tool_call_count", 0)

        # 特征层：结构化技术特征（含 as_of_date / data_quality / 未来数据防护）。
        # 构建/渲染异常统一降级为 features={} + 「不可用」证据块（不阻断全图，
        # 与 `international_news_analyst` 同口径）；`features={}` 经 `node_data_quality`
        # 落 degradation_level=insufficient → 门控判据 (b) → caution（评审 m17）
        try:
            features = mf.build_cn_technical_features(current_date)
        except Exception as e:
            logger.warning(f"[中国技术分析] 特征构建异常（按数据缺失处理）: {e}")
            features = {}
        try:
            evidence = mf.format_cn_technical_evidence(features)
        except Exception as e:  # 渲染异常同样落「不可用」块（format 对空输入恒不抛）
            logger.warning(f"[中国技术分析] 特征渲染异常: {e}")
            evidence = mf.format_cn_technical_evidence({})
        logger.info(
            f"[中国技术分析] 特征层证据就绪：as_of={features.get('as_of_date')}，"
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

        output_format = load_output_format("market", "cn_tech_analyst")

        prompt = ChatPromptTemplate.from_messages([
                system_message(
                    state.get("_current_node_id"),
                    lambda: DEFAULT_PROMPTS["market:CN Tech Analyst"]
                    .replace("{date_line}", date_line),
                ),
            MessagesPlaceholder(variable_name="messages"),
        ])

        # `{output_format}` 以 partial 值注入（而非文本替换注入）：输出格式模板含
        # 字面 JSON 花括号，文本替换会把模板内容并入 langchain 模板再解析 → KeyError。
        prompt = prompt.partial(technical_features=evidence, output_format=output_format)
        result = llm.invoke(prompt.format_messages(messages=state["messages"]))
        report = result.content

        # 结构化输出（第十二章 State 表）：dict 含三级别 level/证据/确认/失效/置信度、
        # style、sentiment_cycle 与节点级 data_quality 子字段（判据 (b)/(d)）。
        regime = mf.parse_market_regime(report)
        if isinstance(regime, dict):
            regime["data_quality"] = mf.node_data_quality(features, regime)
            if not regime.get("as_of_date"):
                regime["as_of_date"] = features.get("as_of_date")

        logger.info(
            f"[中国技术分析] 报告完成，长度: {len(report)}；"
            f"级别={ {k: (regime.get(k) or {}).get('level') for k in ('short_term', 'wave', 'long_term')} }；"
            f"解析={regime.get('parse_status')}"
        )
        return {
            "messages": [result],
            "cn_tech_report": report,
            "market_regime": regime,
            "cn_tech_tool_call_count": count + 1,
        }

    return node
