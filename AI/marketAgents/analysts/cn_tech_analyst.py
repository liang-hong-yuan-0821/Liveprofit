"""
市场层 — Layer 1: 中国技术分析师 ★ 主战场
A 股大盘技术面全景分析：7 指数量价、市场宽度（情绪温度计）、
资金流向（北向/主力）、风格因子（大小盘/成长价值）。
"""

import logging
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from AI.dataflows import interface as dataflow

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

        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                "你是一位资深 A 股大盘技术分析师，充当'三级别市场环境判定器'角色。\n"
                "你的核心任务：同时判断短线（1-5日）、波段（1-4周）、长线（3月+）三个时间级别的市场环境，"
                "为下游板块层和个股层提供明确的交易级别建议。\n\n"
                + date_line + "\n"
                "## 已获取的数据\n\n"
                "### 7大指数长周期行情（120日）\n{market_overview}\n\n"
                "### 市场宽度（涨跌家数、涨停跌停统计）\n{market_breadth}\n\n"
                "### 资金流向（北向+主力）\n{fund_flow}\n\n"
                "分析维度（按优先级排列）：\n\n"
                "A. 指数量价分析\n"
                "  - 上证综指(000001)、深证成指(399001)、创业板指(399006)、科创50(000688)\n"
                "  - 日线趋势方向、MA5/10/20/60/120/250 均线系统、量能变化\n\n"
                "B. 市场宽度（情绪温度计）\n"
                "  - 涨跌家数比 → 市场赚钱效应\n"
                "  - 涨停/跌停家数 → 极端情绪信号\n"
                "  - 涨停梯队高度 → 判断情绪周期位置\n\n"
                "C. 资金流向\n"
                "  - 北向资金（沪股通+深股通）净流入/流出趋势 → 注意：2024-08-16 起仅日终汇总\n"
                "  - 主力资金净流入连续方向 → 机构动向\n\n"
                "D. 风格因子（定量+定性）\n"
                "  - 大盘 vs 小盘：对比上证50(000016) 与 中证1000(000852) 的相对强弱及60日趋势\n"
                "  - 成长 vs 价值：对比创业板指(399006) 与 上证红利(000015) 的相对强弱及60日趋势\n\n"
                "E. ★ 三级别环境判定（核心新增）\n"
                "  - 短线环境（1-5日）：\n"
                "    * 赚钱效应趋势（涨跌家数的方向和持续性）\n"
                "    * 量能状态（放量/缩量/平量）\n"
                "    * 涨停梯队高度与连板数量 → 判断情绪周期位置（冰点/修复/高潮/退潮）\n"
                "    * 综合给出短线适合度：适合 / 谨慎 / 回避\n"
                "  - 波段环境（1-4周）：\n"
                "    * 主要指数 20/60 日均线方向与形态（多头排列/空头排列/整理）\n"
                "    * 主力资金连续 N 日方向\n"
                "    * 主线板块容纳性：当前市场能否支撑 4 周级别主线行情？\n"
                "    * 综合给出波段姿态：进攻 / 平衡 / 防御\n"
                "  - 长线环境（3月+）：\n"
                "    * 指数年线（MA250）位置与方向\n"
                "    * 风格因子 60 日趋势方向\n"
                "    * 市场整体估值水位（定性：低估/合理/高估）\n"
                "    * 无风险利率/流动性方向性判断\n"
                "    * 综合给出长线窗口判断：配置窗口 / 等待窗口\n\n"
                "级别嵌套约束（重要）：\n"
                "- 短线判断必须基于波段结构——波段空头中的短线反弹要标注'逆波段方向的短线机会，容错率低'\n"
                "- 波段判断必须基于长线周期——长线空头中的波段反弹要标注'熊市反弹，持续性存疑'\n"
                "- 三个级别的结论不允许互相矛盾\n\n"
                "注意事项：\n"
                "- 数据不可用时标注'数据暂不可用（需 AKShare 数据源）'\n"
                "- 北向资金如果返回空，标注'北向资金日终汇总数据暂无'\n"
                "- ⚠️ 分析基于公开数据，不构成投资建议\n\n"
                "输出格式（结论前置）：\n"
                "# 中国市场技术分析报告\n\n"
                "## 〇、市场环境速览（结论块 — 向下游传递）\n"
                "（先输出结构化摘要，格式如下）\n"
                "```\n"
                "市场状态标签: <结构性行情/普涨/普跌/缩量观望/系统性风险>\n"
                "情绪周期位置: <冰点/修复/高潮/退潮>\n"
                "三级别判定:\n"
                "- 短线: <适合/谨慎/回避> 建议仓位<X成> — <一句话理由>\n"
                "- 波段: <进攻/平衡/防御> 主线容纳性<是/否> — <一句话理由>\n"
                "- 长线: <配置窗口/等待窗口> 风格方向<大盘/小盘>+<成长/价值> — <一句话理由>\n"
                "数据缺失: <如实标注>\n"
                "```\n\n"
                "## 一、主要指数量价分析\n"
                "（上证综指/深证成指/创业板指/科创50：趋势、均线、量能）\n\n"
                "## 二、市场宽度（情绪温度计）\n"
                "（涨跌家数比、涨停跌停统计、赚钱效应判断）\n\n"
                "## 三、资金流向\n"
                "（北向资金/主力资金；数据不可用时明确标注）\n\n"
                "## 四、风格因子\n"
                "（大盘 vs 小盘、成长 vs 价值偏向）\n\n"
                "## 五、三级别环境详细判定\n"
                "（短线/波段/长线各自的环境分析 + 级别嵌套约束说明）\n"
                "请使用中文。"
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
