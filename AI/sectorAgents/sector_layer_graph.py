"""
板块层子图编译器 (Sector Layer Subgraph)
独立编译为 LangGraph CompiledGraph，封装 3 个板块层 Analyst。
父图通过 add_node("Sector Layer", subgraph) 将其作为一个节点使用。

可扩展：新增板块维度的 Analyst 只需在 ANALYSTS 中加 key + 写 Analyst 文件。
"""
import logging
from langgraph.graph import StateGraph, END, START
from langgraph.prebuilt import ToolNode

from AI.stockAgents.utils.agent_states import AgentState
from AI.stockAgents.utils.agent_utils import create_msg_delete
from AI.sectorAgents.analysts.sector_news_analyst import create_sector_news_analyst
from AI.sectorAgents.analysts.sector_tech_analyst import create_sector_tech_analyst
from AI.sectorAgents.analysts.sector_rotation_analyst import create_sector_rotation_analyst

logger = logging.getLogger(__name__)


class SectorLayerGraph:
    """板块层子图编译器"""

    ANALYSTS = ["sector_news", "sector_tech", "sector_rotation"]

    LABELS = {
        "sector_news": "Sector News",
        "sector_tech": "Sector Tech",
        "sector_rotation": "Sector Rotation",
    }

    # 新闻类 = 工具循环，技术类 / 轮动预测 = 直接边
    LOOP_KEYS = {"sector_news"}

    FACTORY_MAP = {
        "sector_news": create_sector_news_analyst,
        "sector_tech": create_sector_tech_analyst,
        "sector_rotation": create_sector_rotation_analyst,
    }

    def __init__(self, llm, toolkit, max_tool_calls=3):
        self.llm = llm
        self.toolkit = toolkit
        self.max_tool_calls = max_tool_calls

    def build(self):
        """编译并返回板块层子图"""
        workflow = StateGraph(AgentState)

        for key in self.ANALYSTS:
            label = self.LABELS[key]
            factory = self.FACTORY_MAP[key]

            # Analyst 节点
            workflow.add_node(f"{label} Analyst", factory(self.llm, self.toolkit))
            # Msg Clear 节点
            workflow.add_node(f"Msg Clear {label}", create_msg_delete())

            # 新闻类：额外创建 ToolNode + 条件边
            if key in self.LOOP_KEYS:
                tools = self._get_tools(key)
                if tools:
                    workflow.add_node(f"tools_{key}", ToolNode(tools))
                    workflow.add_conditional_edges(
                        f"{label} Analyst",
                        self._make_router(key),
                        {
                            f"tools_{key}": f"tools_{key}",
                            f"Msg Clear {label}": f"Msg Clear {label}",
                        },
                    )
                    workflow.add_edge(f"tools_{key}", f"{label} Analyst")
                else:
                    workflow.add_edge(f"{label} Analyst", f"Msg Clear {label}")
            else:
                # 技术类：直接边
                workflow.add_edge(f"{label} Analyst", f"Msg Clear {label}")

        # 串行连接
        workflow.add_edge(START, f"{self.LABELS[self.ANALYSTS[0]]} Analyst")
        for i in range(len(self.ANALYSTS) - 1):
            src = f"Msg Clear {self.LABELS[self.ANALYSTS[i]]}"
            dst = f"{self.LABELS[self.ANALYSTS[i + 1]]} Analyst"
            workflow.add_edge(src, dst)
        workflow.add_edge(f"Msg Clear {self.LABELS[self.ANALYSTS[-1]]}", END)

        logger.info(f"[SectorLayerGraph] 编译完成，{len(self.ANALYSTS)} 个 Analyst")
        return workflow.compile()

    def _get_tools(self, key):
        """获取指定 key 的工具列表"""
        tool_names = {
            "sector_news": [
                "get_industry_sector_performance",
                "get_sector_fund_flow",
                "get_concept_board_heat",
                "get_industry_policy_news",
            ],
        }.get(key, [])

        tools = []
        for tn in tool_names:
            t = getattr(self.toolkit, tn, None)
            if t is not None:
                tools.append(t)
        return tools

    def _make_router(self, key):
        """为指定 key 生成条件路由函数（新闻类专用）"""
        report_field = f"{key}_report"
        count_field = f"{key}_tool_call_count"
        label = self.LABELS[key]
        max_calls = self.max_tool_calls

        def router(state: AgentState) -> str:
            messages = state.get("messages", [])
            last_msg = messages[-1] if messages else None
            count = state.get(count_field, 0)
            report = state.get(report_field, "")

            if count >= max_calls:
                return f"Msg Clear {label}"
            if report and len(report) > 100:
                return f"Msg Clear {label}"
            if hasattr(last_msg, 'tool_calls') and last_msg.tool_calls:
                return f"tools_{key}"
            return f"Msg Clear {label}"

        return router
