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
    create_trader,
)
from AI.agents.utils.agent_states import AgentState
from AI.agents.utils.agent_utils import Toolkit
from AI.graph.conditional_logic import ConditionalLogic
from AI.marketAgents.market_layer_graph import MarketLayerGraph
from AI.sectorAgents.sector_layer_graph import SectorLayerGraph
from AI.utils.call_trace import trace_call, trace_step

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

    @trace_call(show_params=["selected_analysts"])
    def setup_graph(self, selected_analysts=None):
        """设置并编译 Agent 工作流图

        Args:
            selected_analysts: 选择的分析师类型列表，默认全部
        """
        if selected_analysts is None:
            selected_analysts = ["market", "sector", "stock_tech", "social", "news", "fundamentals"]

        if len(selected_analysts) == 0:
            raise ValueError("至少需要选择一个分析师！")

        # 旧 key 迁移：忽略已废弃的 "tech"
        if "tech" in selected_analysts:
            import warnings
            warnings.warn("'tech' (tech_market_analyst) 已废弃——全球指数已合并到市场层，AI产业链归属板块层。请从 selected_analysts 中移除 'tech'。")
            selected_analysts = [a for a in selected_analysts if a != "tech"]

        # ---- 创建分析师节点 ----
        analyst_nodes = {}
        delete_nodes = {}
        tool_nodes = {}

        # Market Layer Subgraph — 如果 "market" 在列表中，编译子图作为节点
        market_subgraph = None
        if "market" in selected_analysts:
            market_subgraph = MarketLayerGraph(
                self.quick_thinking_llm, self.toolkit
            ).build()

        # Sector Layer Subgraph — 如果 "sector" 在列表中，编译子图作为节点
        sector_subgraph = None
        if "sector" in selected_analysts:
            sector_subgraph = SectorLayerGraph(
                self.quick_thinking_llm, self.toolkit
            ).build()

        if "stock_tech" in selected_analysts:
            analyst_nodes["stock_tech"] = create_market_analyst(
                self.quick_thinking_llm, self.toolkit
            )
            delete_nodes["stock_tech"] = create_msg_delete()
            tool_nodes["stock_tech"] = self.tool_nodes.get("stock_tech",
                self.tool_nodes.get("market", ToolNode([])))

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

        # "tech" key 已废弃（见上方 selected_analysts 处理中的 warn）

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
        trace_step("构建 LangGraph 工作流",
                   analysts=selected_analysts,
                   debate_rounds=self.conditional_logic.max_debate_rounds,
                   risk_rounds=self.conditional_logic.max_risk_discuss_rounds)
        workflow = StateGraph(AgentState)

        # Node label 映射（处理多词 key）
        ANALYST_LABELS = {
            "stock_tech": "Stock Tech", "social": "Social",
            "news": "News", "fundamentals": "Fundamentals",
        }

        # 添加分析师节点
        for analyst_type, node in analyst_nodes.items():
            label = ANALYST_LABELS.get(analyst_type, analyst_type.capitalize())
            workflow.add_node(f"{label} Analyst", node)
            workflow.add_node(f"Msg Clear {label}", delete_nodes[analyst_type])
            workflow.add_node(f"tools_{analyst_type}", tool_nodes[analyst_type])

        # 添加 Market Layer 子图节点
        if market_subgraph is not None:
            workflow.add_node("Market Layer", market_subgraph)

        if sector_subgraph is not None:
            workflow.add_node("Sector Layer", sector_subgraph)

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
        # 仅保留个股级分析师用于连线（排除 market 和 sector 子图层 key）
        stock_analysts = [a for a in selected_analysts if a not in ("market", "sector")]

        # 确定第一个图层节点（market / sector）
        first_layer_node = None
        last_layer_node = None

        if market_subgraph is not None:
            workflow.add_edge(START, "Market Layer")
            first_layer_node = "Market Layer"
            last_layer_node = "Market Layer"

        if sector_subgraph is not None:
            if last_layer_node is not None:
                workflow.add_edge(last_layer_node, "Sector Layer")
            else:
                workflow.add_edge(START, "Sector Layer")
                first_layer_node = "Sector Layer"
            last_layer_node = "Sector Layer"

        if last_layer_node is not None:
            if stock_analysts:
                first_label = ANALYST_LABELS.get(stock_analysts[0], stock_analysts[0].capitalize())
                workflow.add_edge(last_layer_node, f"{first_label} Analyst")
            else:
                workflow.add_edge(last_layer_node, "Bull Researcher")
        else:
            if stock_analysts:
                first = stock_analysts[0]
                first_label = ANALYST_LABELS.get(first, first.capitalize())
                workflow.add_edge(START, f"{first_label} Analyst")

        # 连接股票级分析师序列
        for i, analyst_type in enumerate(stock_analysts):
            label = ANALYST_LABELS.get(analyst_type, analyst_type.capitalize())
            current = f"{label} Analyst"
            current_tools = f"tools_{analyst_type}"
            current_clear = f"Msg Clear {label}"

            if analyst_type in ("social", "stock_tech"):
                # 无工具调用循环，直接清除
                workflow.add_edge(current, current_clear)
            else:
                workflow.add_conditional_edges(
                    current,
                    getattr(self.conditional_logic, f"should_continue_{analyst_type}"),
                    [current_tools, current_clear],
                )
                workflow.add_edge(current_tools, current)

            if i < len(stock_analysts) - 1:
                next_label = ANALYST_LABELS.get(stock_analysts[i+1], stock_analysts[i+1].capitalize())
                workflow.add_edge(current_clear, f"{next_label} Analyst")
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
