"""
市场层 — Layer 1: 中国技术分析师 ★ 主战场
A 股大盘技术面全景分析：7 指数量价、市场宽度（情绪温度计）、
资金流向（北向/主力）、风格因子（大小盘/成长价值）。
"""

import logging
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from AI.dataflows import interface as dataflow
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

        # 直接调用 dataflows 函数获取数据
        market_overview = dataflow.get_china_market_overview(current_date, days=120)
        market_breadth = dataflow.get_market_breadth(current_date)
        fund_flow = dataflow.get_market_fund_flow(current_date)

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
                    .replace("{date_line}", date_line)
                    .replace("{output_format}", output_format),
                ),
            MessagesPlaceholder(variable_name="messages"),
        ])

        prompt = prompt.partial(
            market_overview=market_overview,
            market_breadth=market_breadth,
            fund_flow=fund_flow,
        )
        result = llm.invoke(prompt.format_messages(messages=state["messages"]))
        report = result.content
        regime = _extract_market_regime(report)

        logger.info(f"[中国技术分析] 报告完成，长度: {len(report)}")
        return {
            "messages": [result],
            "cn_tech_report": report,
            "market_regime": regime,
            "risk_gate": derive_risk_gate(regime),
            "cn_tech_tool_call_count": count + 1,
        }

    return node


def _extract_market_regime(report: str) -> str:
    """从完整报告中提取市场环境速览结构化文本"""
    if not report or len(report) < 50:
        return report or ""

    # 尝试提取 ``` 代码块内容
    import re
    code_block = re.search(r'```\s*\n(.*?)\n```', report, re.DOTALL)
    if code_block:
        return code_block.group(1).strip()[:800]

    # 尝试提取 ## 〇 段落（到下一个 ## 或文末）
    regime_section = re.search(
        r'##\s*〇[、，\s]*市场环境速览.*?\n(.*?)(?=\n##\s|\Z)',
        report, re.DOTALL
    )
    if regime_section:
        return regime_section.group(1).strip()[:800]

    # 兜底：返回报告前 800 字
    return report[:800]


def derive_risk_gate(regime: str) -> str:
    """从 market_regime 文本按规则派生机器可读熔断开关。

    判定规则（对结论块模板标签的规则匹配）：
    - block：市场状态标签 = 系统性风险，或 短线 = 回避
    - caution：短线 = 谨慎，或 波段 = 防御
    - normal：其余情况（含市场层未运行、提取失败——fail-open，宁漏勿错）
    """
    if not regime or len(regime) < 10:
        return "normal"

    import re
    status_match = re.search(r"市场状态标签\s*[：:]\s*[<（(]?\s*(\S+)", regime)
    status = status_match.group(1) if status_match else ""
    short_match = re.search(r"短线\s*[：:]\s*[<（(]?\s*(\S+)", regime)
    short_term = short_match.group(1) if short_match else ""
    wave_match = re.search(r"波段\s*[：:]\s*[<（(]?\s*(\S+)", regime)
    wave = wave_match.group(1) if wave_match else ""

    if "系统性风险" in status or "回避" in short_term:
        return "block"
    if "谨慎" in short_term or "防御" in wave:
        return "caution"
    return "normal"
