"""
YoHo 图设置 (简化版)
构建 LangGraph StateGraph，编排多智能体分析流程。

图形结构：
    START -> Market Analyst -> tools_market -> Msg Clear Market
      -> Fundamentals Analyst -> tools_fundamentals -> Msg Clear Fundamentals
      -> News Analyst -> tools_news -> Msg Clear News
      -> Social Media Analyst -> (clear)
      -> Bull Researcher <-> Bear Researcher (辩论循环)
      -> Research Manager -> Trader
      -> Risky Analyst <-> Safe Analyst <-> Neutral Analyst (风险循环)
      -> Risk Judge -> END

简化：移除所有 LLM 提供商特定条件判断，所有 Agent 统一使用 ChatOpenAI。
"""

import logging
from typing import Dict, Any

from langgraph.graph import END, StateGraph, START
from langgraph.prebuilt import ToolNode

from AI.agents import (
    create_bear_researcher,
    create_bull_researcher,
    create_fundamentals_analyst,
    create_market_analyst,
    create_msg_delete,
    create_news_analyst,
    create_neutral_debator,
    create_research_manager,
    create_risk_manager,
    create_risky_debator,
    create_safe_debator,
    create_social_media_analyst,
    create_tech_market_analyst,
    create_trader,
)
from AI.agents.utils.agent_states import AgentState
from AI.agents.utils.agent_utils import Toolkit
from AI.graph.conditional_logic import ConditionalLogic

logger = logging.getLogger(__name__)


class GraphSetup:
    """负责 Agent 图的设置和配置"""

    def __init__(
        self,
        quick_thinking_llm,
        deep_thinking_llm,
        toolkit: Toolkit,
        tool_nodes: Dict[str, ToolNode],
        bull_memory,
        bear_memory,
        trader_memory,
        invest_judge_memory,
        risk_manager_memory,
        conditional_logic: ConditionalLogic,
        config: Dict[str, Any] = None,
    ):
        self.quick_thinking_llm = quick_thinking_llm
        self.deep_thinking_llm = deep_thinking_llm
        self.toolkit = toolkit
        self.tool_nodes = tool_nodes
        self.bull_memory = bull_memory
        self.bear_memory = bear_memory
        self.trader_memory = trader_memory
        self.invest_judge_memory = invest_judge_memory
        self.risk_manager_memory = risk_manager_memory
        self.conditional_logic = conditional_logic
        self.config = config or {}

    def setup_graph(self, selected_analysts=None):
        """设置并编译 Agent 工作流图

        Args:
            selected_analysts: 选择的分析师类型列表，默认全部
        """
        if selected_analysts is None:
            selected_analysts = ["market", "social", "news", "fundamentals", "tech"]

        if len(selected_analysts) == 0:
            raise ValueError("至少需要选择一个分析师！")

        # ---- 创建分析师节点 ----
        analyst_nodes = {}
        delete_nodes = {}
        tool_nodes = {}

        if "market" in selected_analysts:
            analyst_nodes["market"] = create_market_analyst(
                self.quick_thinking_llm, self.toolkit
            )
            delete_nodes["market"] = create_msg_delete()
            tool_nodes["market"] = self.tool_nodes["market"]

        if "social" in selected_analysts:
            analyst_nodes["social"] = create_social_media_analyst(
                self.quick_thinking_llm, self.toolkit
            )
            delete_nodes["social"] = create_msg_delete()
            tool_nodes["social"] = self.tool_nodes.get("social",
                ToolNode([]))  # 社交媒体分析师无工具

        if "news" in selected_analysts:
            analyst_nodes["news"] = create_news_analyst(
                self.quick_thinking_llm, self.toolkit
            )
            delete_nodes["news"] = create_msg_delete()
            tool_nodes["news"] = self.tool_nodes["news"]

        if "fundamentals" in selected_analysts:
            analyst_nodes["fundamentals"] = create_fundamentals_analyst(
                self.quick_thinking_llm, self.toolkit
            )
            delete_nodes["fundamentals"] = create_msg_delete()
            tool_nodes["fundamentals"] = self.tool_nodes["fundamentals"]

        if "tech" in selected_analysts:
            analyst_nodes["tech"] = create_tech_market_analyst(
                self.quick_thinking_llm, self.toolkit
            )
            delete_nodes["tech"] = create_msg_delete()
            tool_nodes["tech"] = self.tool_nodes.get("tech", ToolNode([]))

        # ---- 创建研究员和经理节点 ----
        bull_researcher_node = create_bull_researcher(
            self.quick_thinking_llm, self.bull_memory
        )
        bear_researcher_node = create_bear_researcher(
            self.quick_thinking_llm, self.bear_memory
        )
        research_manager_node = create_research_manager(
            self.deep_thinking_llm, self.invest_judge_memory
        )
        trader_node = create_trader(self.quick_thinking_llm, self.trader_memory)

        # ---- 创建风险分析节点 ----
        risky_analyst = create_risky_debator(self.quick_thinking_llm)
        neutral_analyst = create_neutral_debator(self.quick_thinking_llm)
        safe_analyst = create_safe_debator(self.quick_thinking_llm)
        risk_manager_node = create_risk_manager(
            self.deep_thinking_llm, self.risk_manager_memory
        )

        # ---- 构建工作流 ----
        workflow = StateGraph(AgentState)

        # 添加分析师节点
        for analyst_type, node in analyst_nodes.items():
            cap = analyst_type.capitalize()
            workflow.add_node(f"{cap} Analyst", node)
            workflow.add_node(f"Msg Clear {cap}", delete_nodes[analyst_type])
            workflow.add_node(f"tools_{analyst_type}", tool_nodes[analyst_type])

        # 添加其他节点
        workflow.add_node("Bull Researcher", bull_researcher_node)
        workflow.add_node("Bear Researcher", bear_researcher_node)
        workflow.add_node("Research Manager", research_manager_node)
        workflow.add_node("Trader", trader_node)
        workflow.add_node("Risky Analyst", risky_analyst)
        workflow.add_node("Neutral Analyst", neutral_analyst)
        workflow.add_node("Safe Analyst", safe_analyst)
        workflow.add_node("Risk Judge", risk_manager_node)

        # ---- 定义边 ----
        first_analyst = selected_analysts[0]
        workflow.add_edge(START, f"{first_analyst.capitalize()} Analyst")

        # 连接分析师序列
        for i, analyst_type in enumerate(selected_analysts):
            current = f"{analyst_type.capitalize()} Analyst"
            current_tools = f"tools_{analyst_type}"
            current_clear = f"Msg Clear {analyst_type.capitalize()}"

            if analyst_type in ("social", "tech"):
                # 社交媒体和科技分析师无工具调用循环，直接清除
                workflow.add_edge(current, current_clear)
            else:
                workflow.add_conditional_edges(
                    current,
                    getattr(self.conditional_logic, f"should_continue_{analyst_type}"),
                    [current_tools, current_clear],
                )
                workflow.add_edge(current_tools, current)

            if i < len(selected_analysts) - 1:
                next_analyst = f"{selected_analysts[i+1].capitalize()} Analyst"
                workflow.add_edge(current_clear, next_analyst)
            else:
                workflow.add_edge(current_clear, "Bull Researcher")

        # 辩论边
        workflow.add_conditional_edges(
            "Bull Researcher",
            self.conditional_logic.should_continue_debate,
            {"Bear Researcher": "Bear Researcher", "Research Manager": "Research Manager"},
        )
        workflow.add_conditional_edges(
            "Bear Researcher",
            self.conditional_logic.should_continue_debate,
            {"Bull Researcher": "Bull Researcher", "Research Manager": "Research Manager"},
        )

        workflow.add_edge("Research Manager", "Trader")
        workflow.add_edge("Trader", "Risky Analyst")

        # 风险分析边
        workflow.add_conditional_edges(
            "Risky Analyst",
            self.conditional_logic.should_continue_risk_analysis,
            {"Safe Analyst": "Safe Analyst", "Risk Judge": "Risk Judge"},
        )
        workflow.add_conditional_edges(
            "Safe Analyst",
            self.conditional_logic.should_continue_risk_analysis,
            {"Neutral Analyst": "Neutral Analyst", "Risk Judge": "Risk Judge"},
        )
        workflow.add_conditional_edges(
            "Neutral Analyst",
            self.conditional_logic.should_continue_risk_analysis,
            {"Risky Analyst": "Risky Analyst", "Risk Judge": "Risk Judge"},
        )

        workflow.add_edge("Risk Judge", END)

        return workflow.compile()
