"""
个股层子图编译器 (Stock Layer Subgraph)
独立编译为 LangGraph CompiledGraph，封装个股层所有节点。
父图通过 add_node("Stock Layer", subgraph) 将其作为一个节点使用。

三层 Subgraph 架构的第三层：
- 个股分析师（stock_tech / social / news / fundamentals）
- 多空辩论（Bull ↔ Bear → Research Manager）
- 交易执行（Trader）
- 风险评估（Risky ↔ Safe ↔ Neutral → Risk Judge）

依赖 ticker，位于市场层和板块层之后。

可扩展：新增个股维度分析师只需在 ANALYSTS 中加 key + 写 Analyst 文件。
"""

import logging
from langgraph.graph import StateGraph, END, START
from langgraph.prebuilt import ToolNode

from AI.stockAgents.utils.agent_states import AgentState
from AI.stockAgents.utils.agent_utils import create_msg_delete
from AI.stockAgents.analysts.market_analyst import create_market_analyst
from AI.stockAgents.analysts.fundamentals_analyst import create_fundamentals_analyst
from AI.stockAgents.analysts.news_analyst import create_news_analyst
from AI.stockAgents.analysts.social_media_analyst import create_social_media_analyst
from AI.stockAgents.researchers.bull_researcher import create_bull_researcher
from AI.stockAgents.researchers.bear_researcher import create_bear_researcher
from AI.stockAgents.managers.research_manager import create_research_manager
from AI.stockAgents.managers.risk_manager import create_risk_manager
from AI.stockAgents.trader.trader import create_trader
from AI.stockAgents.risk_mgmt.aggresive_debator import create_risky_debator
from AI.stockAgents.risk_mgmt.conservative_debator import create_safe_debator
from AI.stockAgents.risk_mgmt.neutral_debator import create_neutral_debator

logger = logging.getLogger(__name__)


class StockLayerGraph:
    """个股层子图编译器"""

    ANALYSTS = ["stock_tech", "social", "news", "fundamentals"]

    LABELS = {
        "stock_tech": "Stock Tech",
        "social": "Social",
        "news": "News",
        "fundamentals": "Fundamentals",
    }

    # news / fundamentals = 工具循环（外层 ToolNode + 条件边）
    # stock_tech / social = 自执行工具（节点内部完成工具调用）
    LOOP_KEYS = {"news", "fundamentals"}

    FACTORY_MAP = {
        "stock_tech": create_market_analyst,
        "social": create_social_media_analyst,
        "news": create_news_analyst,
        "fundamentals": create_fundamentals_analyst,
    }

    def __init__(self, quick_llm, deep_llm, toolkit,
                 bull_memory, bear_memory, trader_memory,
                 invest_judge_memory, risk_manager_memory,
                 conditional_logic, config=None):
        self.quick_llm = quick_llm
        self.deep_llm = deep_llm
        self.toolkit = toolkit
        self.bull_memory = bull_memory
        self.bear_memory = bear_memory
        self.trader_memory = trader_memory
        self.invest_judge_memory = invest_judge_memory
        self.risk_manager_memory = risk_manager_memory
        self.conditional_logic = conditional_logic
        self.config = config or {}

    def build(self, selected_analysts=None):
        """编译并返回个股层子图

        Args:
            selected_analysts: 选择的分析师列表，默认全部个股分析师
        """
        if selected_analysts is None:
            selected_analysts = list(self.ANALYSTS)

        # 仅保留个股级分析师 key
        stock_analysts = [a for a in selected_analysts if a in self.ANALYSTS]

        workflow = StateGraph(AgentState)

        # ---- 个股分析师节点 ----
        for key in stock_analysts:
            label = self.LABELS[key]
            factory = self.FACTORY_MAP[key]

            # Analyst 节点
            workflow.add_node(f"{label} Analyst", factory(self.quick_llm, self.toolkit))
            # Msg Clear 节点
            workflow.add_node(f"Msg Clear {label}", create_msg_delete())

            if key in self.LOOP_KEYS:
                # 工具循环模式
                tools = self._get_tools(key)
                if tools:
                    workflow.add_node(f"tools_{key}", ToolNode(tools))
                    workflow.add_conditional_edges(
                        f"{label} Analyst",
                        self._make_analyst_router(key),
                        {
                            f"tools_{key}": f"tools_{key}",
                            f"Msg Clear {label}": f"Msg Clear {label}",
                        },
                    )
                    workflow.add_edge(f"tools_{key}", f"{label} Analyst")
                else:
                    workflow.add_edge(f"{label} Analyst", f"Msg Clear {label}")
            else:
                # 自执行工具模式：直接边
                workflow.add_edge(f"{label} Analyst", f"Msg Clear {label}")

        # ---- 辩论 & 交易节点 ----
        workflow.add_node("Bull Researcher", create_bull_researcher(
            self.quick_llm, self.bull_memory))
        workflow.add_node("Bear Researcher", create_bear_researcher(
            self.quick_llm, self.bear_memory))
        workflow.add_node("Research Manager", create_research_manager(
            self.deep_llm, self.invest_judge_memory))
        workflow.add_node("Trader", create_trader(
            self.quick_llm, self.trader_memory))

        # ---- 风险分析节点 ----
        workflow.add_node("Risky Analyst", create_risky_debator(self.quick_llm))
        workflow.add_node("Safe Analyst", create_safe_debator(self.quick_llm))
        workflow.add_node("Neutral Analyst", create_neutral_debator(self.quick_llm))
        workflow.add_node("Risk Judge", create_risk_manager(
            self.deep_llm, self.risk_manager_memory))

        # ---- 连线 ----
        # 入口：第一个个股分析师（无分析师时直接进入辩论）
        if stock_analysts:
            first_label = self.LABELS[stock_analysts[0]]
            workflow.add_edge(START, f"{first_label} Analyst")
        else:
            workflow.add_edge(START, "Bull Researcher")

        # 分析师串行连接
        for i, key in enumerate(stock_analysts):
            label = self.LABELS[key]
            current_clear = f"Msg Clear {label}"

            if i < len(stock_analysts) - 1:
                next_label = self.LABELS[stock_analysts[i + 1]]
                workflow.add_edge(current_clear, f"{next_label} Analyst")
            else:
                workflow.add_edge(current_clear, "Bull Researcher")

        # 辩论循环
        workflow.add_conditional_edges(
            "Bull Researcher",
            self.conditional_logic.should_continue_debate,
            {"Bear Researcher": "Bear Researcher",
             "Research Manager": "Research Manager"},
        )
        workflow.add_conditional_edges(
            "Bear Researcher",
            self.conditional_logic.should_continue_debate,
            {"Bull Researcher": "Bull Researcher",
             "Research Manager": "Research Manager"},
        )

        # 交易 → 风险
        workflow.add_edge("Research Manager", "Trader")
        workflow.add_edge("Trader", "Risky Analyst")

        # 风险分析循环
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

        # 出口
        workflow.add_edge("Risk Judge", END)

        logger.info(f"[StockLayerGraph] 编译完成，{len(stock_analysts)} 个分析师 "
                     "+ 辩论 + 交易 + 风险")
        return workflow.compile()

    def _get_tools(self, key):
        """获取指定 key 的工具列表（仅工具循环类调用）"""
        tool_names = {
            "news": ["get_stock_news_unified"],
            "fundamentals": ["get_stock_fundamentals_unified"],
        }.get(key, [])

        tools = []
        for tn in tool_names:
            t = getattr(self.toolkit, tn, None)
            if t is not None:
                tools.append(t)
        return tools

    def _make_analyst_router(self, key):
        """为指定 key 生成条件路由函数，委托给 ConditionalLogic"""
        method_map = {
            "news": "should_continue_news",
            "fundamentals": "should_continue_fundamentals",
        }
        method_name = method_map.get(key)
        if method_name:
            return getattr(self.conditional_logic, method_name)

        # 兜底：直接清除
        label = self.LABELS.get(key, key.capitalize())
        return lambda state: f"Msg Clear {label}"
