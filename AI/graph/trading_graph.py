"""
YoHo 交易图编排器 (简化版)
整个多智能体交易分析系统的主编排器。

三层 Subgraph 架构：
  Market Layer → Sector Layer → Stock Layer → END

从 TradingAgents-CN 大幅简化：
- LLM 初始化：仅两个 ChatOpenAI 实例（quick + deep）
- 三层子图各自独立编译，顶层仅负责串联
- 移除：create_llm_by_provider、_create_provider_pair
- 移除：所有 LLM 提供商特定初始化代码
- 移除：性能计时和指标报告
"""

import os
import json
import logging
from pathlib import Path
from typing import Dict, Any

from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END, START

from AI.stockAgents import Toolkit
from AI.stockAgents.utils.agent_states import AgentState
from AI.stockAgents.utils.memory import FinancialSituationMemory
from AI.stockAgents.conditional_logic import ConditionalLogic
from AI.stockAgents.stock_layer_graph import StockLayerGraph
from AI.marketAgents.market_layer_graph import MarketLayerGraph
from AI.sectorAgents.sector_layer_graph import SectorLayerGraph
from AI.dataflows.interface import set_config
from AI.default_config import load_config
from AI.utils.call_trace import trace_call, trace_step

from .propagation import Propagator
from .reflection import Reflector
from .signal_processing import SignalProcessor

logger = logging.getLogger(__name__)


class TradingAgentsGraph:
    """多智能体交易分析框架的主编排器"""

    def __init__(
        self,
        selected_analysts=None,
        debug=False,
        config: Dict[str, Any] = None,
    ):
        """
        Args:
            selected_analysts: 选择的分析师列表，默认全部。
                              市场层 = "market"，板块层 = "sector"，
                              个股层 = "stock_tech", "social", "news", "fundamentals"
            debug: 是否开启调试模式
            config: 配置字典，为 None 时从环境变量加载
        """
        if selected_analysts is None:
            selected_analysts = ["market", "sector", "social", "news",
                                 "fundamentals", "stock_tech"]

        self.debug = debug
        self.config = config or load_config()

        set_config(self.config)
        trace_step("TradingAgentsGraph 初始化", debug=debug,
                   analysts=selected_analysts,
                   memory=self.config.get("memory_enabled"))

        # ---- LLM 初始化 (仅 ChatOpenAI) ----
        api_key = self.config.get("api_key", "")
        base_url = self.config.get("base_url", "https://api.openai.com/v1")

        if not api_key:
            raise ValueError(
                "未配置 YOHO_API_KEY。请在 a.bash 中设置，然后执行 source a.bash"
            )

        quick_model = self.config.get("quick_think_llm", "gpt-4o-mini")
        deep_model = self.config.get("deep_think_llm", "gpt-4o")
        quick_temp = self.config.get("quick_temperature", 0.7)
        deep_temp = self.config.get("deep_temperature", 0.3)
        max_tokens = self.config.get("max_tokens", 8192)

        logger.info(f"初始化 LLM: quick={quick_model}, deep={deep_model}, base_url={base_url}")

        self.quick_thinking_llm = ChatOpenAI(
            model=quick_model,
            base_url=base_url,
            api_key=api_key,
            temperature=quick_temp,
            max_tokens=max_tokens,
            timeout=180,
        )

        self.deep_thinking_llm = ChatOpenAI(
            model=deep_model,
            base_url=base_url,
            api_key=api_key,
            temperature=deep_temp,
            max_tokens=max_tokens * 2,
            timeout=300,
        )

        # ---- Toolkit ----
        self.toolkit = Toolkit(config=self.config)

        # ---- 记忆系统 ----
        memory_enabled = self.config.get("memory_enabled", True)
        if memory_enabled:
            self.bull_memory = FinancialSituationMemory("bull_memory", self.config)
            self.bear_memory = FinancialSituationMemory("bear_memory", self.config)
            self.trader_memory = FinancialSituationMemory("trader_memory", self.config)
            self.invest_judge_memory = FinancialSituationMemory("invest_judge", self.config)
            self.risk_manager_memory = FinancialSituationMemory("risk_manager", self.config)
        else:
            self.bull_memory = None
            self.bear_memory = None
            self.trader_memory = None
            self.invest_judge_memory = None
            self.risk_manager_memory = None

        # ---- 路由逻辑 ----
        self.conditional_logic = ConditionalLogic(
            max_debate_rounds=self.config.get("max_debate_rounds", 1),
            max_risk_discuss_rounds=self.config.get("max_risk_discuss_rounds", 1),
        )

        # ---- 执行组件 ----
        self.propagator = Propagator(
            max_recur_limit=self.config.get("max_recur_limit", 100)
        )
        self.reflector = Reflector(self.quick_thinking_llm)
        self.signal_processor = SignalProcessor(self.quick_thinking_llm)

        # 状态追踪
        self.curr_state = None
        self.ticker = None
        self.log_states_dict = {}

        # 编译顶层图
        self.graph = self._build_top_graph(selected_analysts)
        trace_step("顶层图编译完成")

    # ==================== 顶层图构建 ====================

    def _build_top_graph(self, selected_analysts):
        """构建顶层编排图：Market Layer → Sector Layer → Stock Layer → END"""
        workflow = StateGraph(AgentState)

        has_market = "market" in selected_analysts
        has_sector = "sector" in selected_analysts

        # Market Layer 子图
        if has_market:
            market_subgraph = MarketLayerGraph(
                self.quick_thinking_llm, self.toolkit
            ).build()
            workflow.add_node("Market Layer", market_subgraph)
            logger.info("[顶层图] 已添加 Market Layer 子图")

        # Sector Layer 子图
        if has_sector:
            sector_subgraph = SectorLayerGraph(
                self.quick_thinking_llm, self.toolkit
            ).build()
            workflow.add_node("Sector Layer", sector_subgraph)
            logger.info("[顶层图] 已添加 Sector Layer 子图")

        # Stock Layer 子图（个股层）
        stock_subgraph = StockLayerGraph(
            self.quick_thinking_llm,
            self.deep_thinking_llm,
            self.toolkit,
            self.bull_memory,
            self.bear_memory,
            self.trader_memory,
            self.invest_judge_memory,
            self.risk_manager_memory,
            self.conditional_logic,
            self.config,
        ).build(selected_analysts)
        workflow.add_node("Stock Layer", stock_subgraph)
        logger.info("[顶层图] 已添加 Stock Layer 子图")

        # ---- 连线 ----
        first_node = None
        prev_node = None

        if has_market:
            first_node = "Market Layer"
            prev_node = "Market Layer"

        if has_sector:
            if prev_node is None:
                first_node = "Sector Layer"
            else:
                workflow.add_edge(prev_node, "Sector Layer")
            prev_node = "Sector Layer"

        if first_node is None:
            first_node = "Stock Layer"

        if prev_node is not None:
            workflow.add_edge(prev_node, "Stock Layer")

        workflow.add_edge(START, first_node)
        workflow.add_edge("Stock Layer", END)

        layer_names = []
        if has_market:
            layer_names.append("Market")
        if has_sector:
            layer_names.append("Sector")
        layer_names.append("Stock")
        logger.info(f"[顶层图] 编译完成: {' → '.join(layer_names)} → END")

        return workflow.compile()

    # ==================== 运行时 ====================

    def propagate(self, company_name, trade_date, progress_callback=None):
        """运行交易分析图

        Args:
            company_name: 股票代码
            trade_date: 分析日期 (YYYY-MM-DD)
            progress_callback: 可选的进度回调函数

        Returns:
            (final_state, decision_dict)
        """
        self.ticker = company_name
        trace_step("propagate 入口", company=company_name, trade_date=trade_date,
                   callback=bool(progress_callback))
        logger.info(f"开始分析: {company_name} @ {trade_date}")

        # 初始化状态
        init_state = self.propagator.create_initial_state(company_name, trade_date)
        args = self.propagator.get_graph_args(
            use_progress_callback=bool(progress_callback)
        )
        trace_step("初始状态就绪", stream_mode=args.get("stream_mode"),
                   recursion_limit=args.get("config", {}).get("recursion_limit"))

        # 运行图
        final_state = None
        if self.debug:
            trace = []
            for chunk in self.graph.stream(init_state, **args):
                if progress_callback and args.get("stream_mode") == "updates":
                    self._send_progress(chunk, progress_callback)
                    if final_state is None:
                        final_state = init_state.copy()
                    for node_name, node_update in chunk.items():
                        if not node_name.startswith('__'):
                            final_state.update(node_update)
                else:
                    if args.get("stream_mode") == "values":
                        trace.append(chunk)
                    else:
                        if final_state is None:
                            final_state = init_state.copy()
                        for node_name, node_update in chunk.items():
                            if not node_name.startswith('__'):
                                final_state.update(node_update)

            if trace:
                final_state = trace[-1]
        else:
            if progress_callback:
                final_state = None
                for chunk in self.graph.stream(init_state, **args):
                    self._send_progress(chunk, progress_callback)
                    if final_state is None:
                        final_state = init_state.copy()
                    for node_name, node_update in chunk.items():
                        if not node_name.startswith('__'):
                            final_state.update(node_update)
            else:
                final_state = self.graph.invoke(init_state)

        self.curr_state = final_state
        self._log_state(trade_date, final_state)
        trace_step("图执行完成", nodes_visited=len([k for k in final_state.keys()
                     if not k.startswith('__')]))
        trace_step("开始信号处理", stock=company_name)

        # 处理决策信号
        decision = self.process_signal(
            final_state["final_trade_decision"], company_name
        )
        trace_step("决策提取完成", action=decision.get("action"),
                   price=decision.get("target_price"), conf=decision.get("confidence"))

        return final_state, decision

    def _send_progress(self, chunk, callback):
        """发送进度更新（仅三层子图级别）"""
        try:
            if not isinstance(chunk, dict):
                return

            node_name = None
            for key in chunk.keys():
                if not key.startswith('__'):
                    node_name = key
                    break

            if not node_name:
                return

            node_map = {
                "Market Layer": "市场层分析中...",
                "Sector Layer": "板块层分析中...",
                "Stock Layer": "个股层分析中...",
            }

            message = node_map.get(node_name)
            if message:
                callback(message)
        except Exception:
            pass

    def _log_state(self, trade_date, final_state):
        """将最终状态记录到 JSON 文件"""
        try:
            self.log_states_dict[str(trade_date)] = {
                "company": final_state.get("company_of_interest", ""),
                "date": final_state.get("trade_date", ""),
                "international_news_report": final_state.get("international_news_report", ""),
                "us_news_report": final_state.get("us_news_report", ""),
                "us_tech_report": final_state.get("us_tech_report", ""),
                "kr_news_report": final_state.get("kr_news_report", ""),
                "kr_tech_report": final_state.get("kr_tech_report", ""),
                "cn_news_report": final_state.get("cn_news_report", ""),
                "cn_tech_report": final_state.get("cn_tech_report", ""),
                "stock_tech_report": final_state.get("stock_tech_report", ""),
                "sentiment_report": final_state.get("sentiment_report", ""),
                "news_report": final_state.get("news_report", ""),
                "fundamentals_report": final_state.get("fundamentals_report", ""),
                "investment_plan": final_state.get("investment_plan", ""),
                "trader_plan": final_state.get("trader_investment_plan", ""),
                "final_decision": final_state.get("final_trade_decision", ""),
            }

            directory = Path(
                f"results/{self.ticker}/analysis_logs/"
            )
            directory.mkdir(parents=True, exist_ok=True)

            with open(directory / "state_log.json", "w", encoding="utf-8") as f:
                json.dump(self.log_states_dict, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"状态日志保存失败: {e}")

    def reflect_and_remember(self, returns_losses):
        """基于实际收益进行反思并更新记忆"""
        logger.info(f"开始反思: 收益={returns_losses}")
        self.reflector.reflect_bull_researcher(
            self.curr_state, returns_losses, self.bull_memory
        )
        self.reflector.reflect_bear_researcher(
            self.curr_state, returns_losses, self.bear_memory
        )
        self.reflector.reflect_trader(
            self.curr_state, returns_losses, self.trader_memory
        )
        self.reflector.reflect_invest_judge(
            self.curr_state, returns_losses, self.invest_judge_memory
        )
        self.reflector.reflect_risk_manager(
            self.curr_state, returns_losses, self.risk_manager_memory
        )

    def process_signal(self, full_signal, stock_symbol=None):
        """处理信号以提取核心决策"""
        return self.signal_processor.process_signal(full_signal, stock_symbol)
