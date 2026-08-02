"""
YoHo 反思器 (简化版)
在交易结算后对决策进行反思，并更新记忆系统。
从 TradingAgents-CN 复制，仅修改 import 路径。
"""

import logging
from typing import Dict, Any

from AI.utils.call_trace import trace_call, trace_step

logger = logging.getLogger(__name__)


class Reflector:
    """处理决策反思和记忆更新"""

    def __init__(self, quick_thinking_llm):
        self.quick_thinking_llm = quick_thinking_llm
        self.reflection_system_prompt = """
你是一位专业的金融分析专家，负责审查交易决策/分析并提供全面的分步分析。
你的目标是提供对投资决策的详细见解，并指出改进机会，严格遵守以下指南：

1. 推理：
   - 对于每个交易决策，判断它是正确还是错误。正确决策导致收益增加，错误决策导致相反结果。
   - 分析每个成功或错误的因素：市场情报、技术指标、价格走势、新闻、社交媒体情绪、基本面数据。
   - 权衡每个因素在决策过程中的重要性。

2. 改进：
   - 对于任何错误决策，提出修正方案以最大化收益。
   - 提供具体的纠正措施或改进建议。

3. 总结：
   - 总结成功和错误中的经验教训。
   - 突出这些教训如何能适配未来的交易场景。

4. 查询：
   - 将关键见解提炼为一句话，不超过1000个token。
   - 确保简练的句子捕捉到经验教训和推理的精髓。

严格遵守这些指令，确保你的输出详细、准确且可操作。
"""

    def _extract_current_situation(self, current_state: Dict[str, Any]) -> str:
        """从状态中提取当前市场情境"""
        return (
            f"{current_state.get('stock_tech_report', '')}\n\n"
            f"{current_state['sentiment_report']}\n\n"
            f"{current_state['news_report']}\n\n"
            f"{current_state['fundamentals_report']}"
        )

    def _reflect_on_component(self, report: str, situation: str, returns_losses) -> str:
        """为某个组件生成反思"""
        messages = [
            ("system", self.reflection_system_prompt),
            (
                "human",
                f"收益: {returns_losses}\n\n"
                f"分析/决策: {report}\n\n"
                f"客观市场报告参考: {situation}",
            ),
        ]
        return self.quick_thinking_llm.invoke(messages).content

    @trace_call(show_params=["returns_losses"])
    def reflect_bull_researcher(self, current_state, returns_losses, memory):
        if memory is None:
            return
        situation = self._extract_current_situation(current_state)
        bull_debate = current_state["investment_debate_state"]["bull_history"]
        trace_step("反思 Bull Researcher", returns=returns_losses, debate_len=len(bull_debate))
        result = self._reflect_on_component(bull_debate, situation, returns_losses)
        memory.add_situations([(situation, result)])

    @trace_call(show_params=["returns_losses"])
    def reflect_bear_researcher(self, current_state, returns_losses, memory):
        if memory is None:
            return
        situation = self._extract_current_situation(current_state)
        bear_debate = current_state["investment_debate_state"]["bear_history"]
        trace_step("反思 Bear Researcher", returns=returns_losses, debate_len=len(bear_debate))
        result = self._reflect_on_component(bear_debate, situation, returns_losses)
        memory.add_situations([(situation, result)])

    def reflect_trader(self, current_state, returns_losses, memory):
        if memory is None:
            return
        situation = self._extract_current_situation(current_state)
        trader_decision = current_state["trader_investment_plan"]
        result = self._reflect_on_component(trader_decision, situation, returns_losses)
        memory.add_situations([(situation, result)])

    def reflect_invest_judge(self, current_state, returns_losses, memory):
        if memory is None:
            return
        situation = self._extract_current_situation(current_state)
        judge_decision = current_state["investment_debate_state"]["judge_decision"]
        result = self._reflect_on_component(judge_decision, situation, returns_losses)
        memory.add_situations([(situation, result)])

    def reflect_risk_manager(self, current_state, returns_losses, memory):
        if memory is None:
            return
        situation = self._extract_current_situation(current_state)
        risk_decision = current_state["risk_debate_state"]["judge_decision"]
        result = self._reflect_on_component(risk_decision, situation, returns_losses)
        memory.add_situations([(situation, result)])
