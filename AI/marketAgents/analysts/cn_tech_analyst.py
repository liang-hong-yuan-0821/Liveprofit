"""
市场层 — Layer 1: 中国技术分析师 ★ 主战场
A 股大盘技术面全景分析：7 指数量价、市场宽度（情绪温度计）、
资金流向（北向/主力）、风格因子（大小盘/成长价值）。
直接边模式（无工具循环），在节点内完成工具调用+分析。
"""

import logging
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import ToolMessage, HumanMessage

logger = logging.getLogger(__name__)


def create_cn_tech_analyst(llm, toolkit):

    def node(state):
        current_date = state["trade_date"]
        logger.info(f"[中国技术分析] 开始分析 @ {current_date}")

        tools = [
            toolkit.get_china_market_overview,
            toolkit.get_market_breadth,
            toolkit.get_market_fund_flow,
        ]
        count = state.get("cn_tech_tool_call_count", 0)

        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                "你是一位资深 A 股大盘技术分析师，充当'市场环境温度计'角色。\n\n"
                "分析日期：{current_date}\n"
                "可用工具：{tool_names}\n\n"
                "工作流程：\n"
                "1. 调用 get_china_market_overview 获取 7 大指数近期行情\n"
                "2. 调用 get_market_breadth 获取涨跌家数和涨停跌停统计\n"
                "3. 调用 get_market_fund_flow 获取北向资金和主力资金流向\n"
                "4. 综合生成大盘技术分析报告（不要重复调用工具）\n\n"
                "分析维度：\n"
                "A. 指数量价分析\n"
                "  - 上证综指(000001)、深证成指(399001)、创业板指(399006)、科创50(000688)\n"
                "  - 趋势方向、均线系统(MA5/10/20/60)、量能变化\n"
                "B. 市场宽度（情绪温度计）\n"
                "  - 涨跌家数比 → 市场赚钱效应\n"
                "  - 涨停/跌停家数 → 极端情绪信号\n"
                "C. 资金流向\n"
                "  - 北向资金（沪股通+深股通）净流入/流出 → 注意：2024-08-16 起仅日终汇总，无盘中实时\n"
                "  - 主力资金净流入 → 机构动向\n"
                "D. 风格因子（定性判断）\n"
                "  - 大盘 vs 小盘：对比上证50(000016) 与 中证1000(000852) 相对强弱\n"
                "  - 成长 vs 价值：对比创业板指(399006) 与 上证红利(000015) 相对强弱\n"
                "E. 市场状态标签与仓位基调\n"
                "  - 普涨 / 普跌 / 结构性行情 / 缩量观望 / 系统性风险高位\n"
                "  - 积极 / 中性 / 防御 / 观望避风\n\n"
                "注意事项：\n"
                "- 数据不可用时标注'数据暂不可用（需 AKShare 数据源）'\n"
                "- 北向资金如果返回空，标注'北向资金日终汇总数据暂无'\n"
                "- ⚠️ 分析基于公开数据，不构成投资建议\n\n"
                "输出格式：\n"
                "# 中国市场技术分析报告\n\n"
                "## 一、主要指数量价分析\n"
                "（上证综指/深证成指/创业板指/科创50：趋势、均线、量能）\n\n"
                "## 二、市场宽度（情绪温度计）\n"
                "（涨跌家数比、涨停跌停统计、赚钱效应判断）\n\n"
                "## 三、资金流向\n"
                "（北向资金/主力资金；数据不可用时明确标注）\n\n"
                "## 四、风格因子\n"
                "（大盘 vs 小盘、成长 vs 价值偏向）\n\n"
                "## 五、市场状态标签与仓位基调\n"
                "请使用中文。"
            ),
            MessagesPlaceholder(variable_name="messages"),
        ])

        tool_names = [getattr(t, 'name', getattr(t, '__name__', str(t))) for t in tools]
        prompt = prompt.partial(tool_names=", ".join(tool_names), current_date=current_date)
        chain = prompt | llm.bind_tools(tools)
        result = chain.invoke({"messages": state["messages"]})

        if len(result.tool_calls) == 0:
            report = result.content
            logger.info(f"[中国技术分析] 报告完成，长度: {len(report)}")
            return {
                "messages": [result],
                "cn_tech_report": report,
                "cn_tech_tool_call_count": count + 1,
            }

        logger.info(f"[中国技术分析] 执行 {len(result.tool_calls)} 个工具调用")
        try:
            tool_messages = _exec_tools(tools, result.tool_calls)
            analysis_prompt = (
                "请基于以上数据生成中国市场技术分析报告（大盘环境温度计）。\n\n"
                "# 中国市场技术分析报告\n"
                "## 一、主要指数量价分析\n"
                "（上证综指/深证成指/创业板指/科创50：趋势、均线、量能）\n\n"
                "## 二、市场宽度（情绪温度计）\n"
                "（涨跌家数比、涨停跌停统计、赚钱效应判断）\n\n"
                "## 三、资金流向\n"
                "（北向资金/主力资金；数据不可用时明确标注）\n\n"
                "## 四、风格因子\n"
                "（大盘 vs 小盘 = 上证50 vs 中证1000；成长 vs 价值 = 创业板指 vs 上证红利）\n\n"
                "## 五、市场状态标签与仓位基调\n"
                "（普涨/普跌/结构性/缩量观望/系统性风险高位 → 积极/中性/防御/观望）\n\n"
                "请使用中文。数据不可用时如实标注。"
            )
            messages = state["messages"] + [result] + tool_messages + [HumanMessage(content=analysis_prompt)]
            final_result = llm.invoke(messages)
            report = final_result.content
            logger.info(f"[中国技术分析] 报告完成，长度: {len(report)}")
            return {
                "messages": [result] + tool_messages + [final_result],
                "cn_tech_report": report,
                "cn_tech_tool_call_count": count + 1,
            }
        except Exception as e:
            logger.error(f"[中国技术分析] 失败: {e}")
            return {
                "messages": [result],
                "cn_tech_report": f"分析生成失败: {e}",
                "cn_tech_tool_call_count": count + 1,
            }

    return node


def _exec_tools(tools, tool_calls):
    msgs = []
    for tc in tool_calls:
        tname, targs, tid = tc.get("name"), tc.get("args", {}), tc.get("id")
        for t in tools:
            if getattr(t, 'name', getattr(t, '__name__', '')) == tname:
                try:
                    res = t.invoke(targs)
                except Exception as e:
                    res = f"工具执行失败: {e}"
                msgs.append(ToolMessage(content=str(res), tool_call_id=tid))
                break
        else:
            msgs.append(ToolMessage(content=f"未找到工具: {tname}", tool_call_id=tid))
    return msgs
