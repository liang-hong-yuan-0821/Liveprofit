"""
市场层子图编译器 (Market Layer Subgraph)
独立编译为 LangGraph CompiledGraph，封装市场层 8 个 Analyst + 1 个纯代码门控节点。
父图通过 add_node("Market Layer", subgraph) 将其作为一个节点使用。

可扩展：新增国家只需在 COUNTRIES 中加一行 + 写两个 Analyst 文件。

T6 改造（方案第十一/十二章）：
- 移除国际影响的事件研究工具注册（`search_similar_events`）——国际影响节点只消费
  结构化事件与风险价格特征，历史统计由国际事件提取节点在 LLM 前预取；
- 新增纯代码节点 `Risk Gate`（`derive_risk_gate`，市场子图内最后一个节点）：
  不调用 LLM，消费 `global_risk_assessment`/`market_regime`/`market_data_quality`
  按第十二章有序规则表派生 `risk_gate`（原地替换 CN Tech 内正则派生实现）。
"""

import logging
from langgraph.graph import StateGraph, END, START
from langgraph.prebuilt import ToolNode

from AI.dataflows import market_features as mf
from AI.stockAgents.utils.agent_states import AgentState
from AI.stockAgents.utils.agent_utils import create_msg_delete
from AI.utils.dataprovider_log import track_node
from AI.utils.checkpoint import guard_checkpoint
from AI.marketAgents.analysts.international_event_extraction import create_international_event_extraction
from AI.marketAgents.analysts.international_news_analyst import create_international_news_analyst
from AI.marketAgents.analysts.us_news_analyst import create_us_news_analyst
from AI.marketAgents.analysts.us_tech_analyst import create_us_tech_analyst
from AI.marketAgents.analysts.kr_news_analyst import create_kr_news_analyst
from AI.marketAgents.analysts.kr_tech_analyst import create_kr_tech_analyst
from AI.marketAgents.analysts.cn_news_analyst import create_cn_news_analyst
from AI.marketAgents.analysts.cn_tech_analyst import create_cn_tech_analyst

logger = logging.getLogger(__name__)


class MarketLayerGraph:
    """市场层子图编译器"""

    # ★ 新增国家只需在这里加一行 ★
    COUNTRIES = ["cn"]

    LABELS = {
        "intl_event_extraction": "International Event Extraction",
        "intl_news": "International News",
        "us_news": "US News", "us_tech": "US Tech",
        "kr_news": "KR News", "kr_tech": "KR Tech",
        "cn_news": "CN News", "cn_tech": "CN Tech",
    }

    # 纯代码风险门控节点标签（无 LLM；`market:Risk Gate`，市场子图内最后一个节点）
    GATE_LABEL = "Risk Gate"

    # 新闻类 = 工具循环，技术类 = 直接边
    LOOP_KEYS = {"intl_news", "us_news", "kr_news", "cn_news"}

    # intl_event_extraction → international_event, intl_news → international_news
    FIELD_PREFIX = {
        "intl_event_extraction": "international_event",
        "intl_news": "international_news",
    }

    # 工厂函数映射
    FACTORY_MAP = {
        "intl_event_extraction": create_international_event_extraction,
        "intl_news": create_international_news_analyst,
        "us_news": create_us_news_analyst,
        "us_tech": create_us_tech_analyst,
        "kr_news": create_kr_news_analyst,
        "kr_tech": create_kr_tech_analyst,
        "cn_news": create_cn_news_analyst,
        "cn_tech": create_cn_tech_analyst,
    }

    def __init__(self, llm, toolkit, max_tool_calls=3):
        self.llm = llm
        self.toolkit = toolkit
        self.max_tool_calls = max_tool_calls

    def build(self):
        """编译并返回市场层子图"""
        workflow = StateGraph(AgentState)

        all_keys = ["intl_event_extraction", "intl_news"] + [
            f"{c}_{t}" for c in self.COUNTRIES for t in ("news", "tech")
        ]

        # 创建所有节点
        for key in all_keys:
            label = self.LABELS[key]
            factory = self.FACTORY_MAP[key]

            # Analyst 节点
            workflow.add_node(
                f"{label} Analyst",
                guard_checkpoint(f"{label} Analyst")(
                    track_node(f"{label} Analyst")(factory(self.llm, self.toolkit))),
            )
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

        # 风险门控：纯代码节点（无 LLM），CN Tech 之后、市场子图末尾。
        # guard_checkpoint 包装（checkpoint 落盘 + 重跑快进）——`_NODE_LAYER` 需登记。
        workflow.add_node(
            self.GATE_LABEL,
            guard_checkpoint(self.GATE_LABEL)(self._derive_risk_gate),
        )

        # 串行连接
        workflow.add_edge(START, f"{self.LABELS[all_keys[0]]} Analyst")
        for i in range(len(all_keys) - 1):
            src = f"Msg Clear {self.LABELS[all_keys[i]]}"
            dst = f"{self.LABELS[all_keys[i + 1]]} Analyst"
            workflow.add_edge(src, dst)
        workflow.add_edge(f"Msg Clear {self.LABELS[all_keys[-1]]}", self.GATE_LABEL)
        workflow.add_edge(self.GATE_LABEL, END)

        logger.info(
            f"[MarketLayerGraph] 编译完成，{len(all_keys)} 个 Analyst + "
            f"1 个纯代码门控节点（{self.GATE_LABEL}）"
        )
        return workflow.compile()

    @staticmethod
    def _derive_risk_gate(state: AgentState) -> dict:
        """纯代码风险门控：消费结构化状态派生 `risk_gate`（无 LLM、无工具）。

        输入 `global_risk_assessment`（国际影响）/ `market_regime`（CN Tech）/
        `market_data_quality`（图启动快照），按第十二章有序规则表短路判定；
        关键数据不足（含结构解析失败）由 fail-open 改为 `caution`。
        """
        decision = mf.risk_gate_decision(
            state.get("global_risk_assessment"),
            state.get("market_regime"),
            state.get("market_data_quality"),
        )
        logger.info(
            f"[风险门控] gate={decision['gate']} rule={decision['rule']} "
            f"原因={decision['reason']}"
        )
        return {"risk_gate": decision["gate"]}

    def _get_tools(self, key):
        """获取指定 key 的工具列表（仅新闻类调用）"""
        tool_names = {
            "intl_news": [
                "get_global_macro_news", "get_central_bank_calendar",
                "get_macro_indicators", "get_commodity_fx_overview",
                "get_event_calendar_history",
            ],
            "us_news": [
                "get_us_macro_news", "get_us_economic_calendar", "get_vix_index",
            ],
            "kr_news": [
                "get_kr_macro_news", "get_kr_export_data",
            ],
            "cn_news": [
                "get_ipo_calendar", "get_share_unlock_calendar",
                "get_futures_expiry_calendar", "get_margin_trading_balance",
            ],
        }.get(key, [])

        tools = []
        for tn in tool_names:
            t = getattr(self.toolkit, tn, None)
            if t is not None:
                tools.append(t)
        # T6：国际影响不再注册事件研究工具（search_similar_events）——历史统计由
        # 国际事件提取节点在 LLM 前预取，国际影响只消费结构化事件与风险价格特征。
        return tools

    def _make_router(self, key):
        """为指定 key 生成条件路由函数"""
        prefix = self.FIELD_PREFIX.get(key, key)
        report_field = f"{prefix}_report"
        count_field = f"{prefix}_tool_call_count"
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
