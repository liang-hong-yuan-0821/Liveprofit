"""
LiveProfit 交易图编排器 (简化版)
整个多智能体交易分析系统的主编排器。

三层 Subgraph 架构（自顶向下）：
  Market Layer → Sector Layer → Stock Layer → END

从 TradingAgents-CN 大幅简化：
- LLM 初始化：仅两个 ChatOpenAI 实例（quick + deep）
- 三层子图各自独立编译，编排图仅负责串联
- 移除：create_llm_by_provider、_create_provider_pair
- 移除：所有 LLM 提供商特定初始化代码
- 移除：性能计时和指标报告
"""

import os
import json
import logging
from datetime import datetime
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
from AI.screening.screening_node import make_screening_node
from AI.dataflows.interface import set_config
from AI.default_config import load_config
from AI.utils.call_trace import trace_call, trace_step

from .propagation import Propagator
from .reflection import Reflector
from .signal_processing import SignalProcessor
from AI.utils import checkpoint
from AI.utils import prompts as agent_prompts
from AI.utils.llm_callbacks import LLMCallbackHandler, ToolCallbackHandler
from AI.utils.dataprovider_log import track_node
from AI.utils import step_gate

logger = logging.getLogger(__name__)


def _log_event_list(events, limit: int = 5) -> list:
    """结构化事件列表 → 状态日志副本（≤ limit 条，dict 直接序列化；评审 m11）。

    `international_events` / `sector_events` / `stock_events` 均为 dict 列表，
    可直接 json.dump；上限与预取上限（`MAX_PREFETCH_CANDIDATES`）同口径，
    防日志膨胀。非 list / 非 dict 项静默丢弃（日志不得因脏数据失败）。
    """
    if not isinstance(events, list):
        return []
    items = [e for e in events if isinstance(e, dict)]
    if len(items) > limit:
        logger.warning("状态日志：结构化事件 %d 条超上限 %d，仅记录前 %d 条",
                       len(items), limit, limit)
    return items[:limit]


def resolve_run_log_dir(init_state: dict, now: datetime | None = None) -> Path:
    """本次运行的日志目录。

    平台任务经 init_state["platform_log_dir"] 注入确定性任务目录
    （logs/tasks/{task_id}/{attempt_no}/）；CLI/既有测试不含该 key 时
    维持现状，写 logs/{时间戳}/。now 参数仅供单测固定时间戳。
    """
    platform_dir = str(init_state.get("platform_log_dir") or "").strip()
    if platform_dir:
        return Path(platform_dir)
    run_ts = (now or datetime.now()).strftime("%Y-%m-%d_%H%M%S")
    return Path(f"logs/{run_ts}")


class TradingAgentsGraph:
    """多智能体交易分析框架的主编排器"""

    def __init__(
        self,
        selectedLayer=None,
        debug=False,
        config: Dict[str, Any] = None,
    ):
        """
        Args:
            selectedLayer: 选择的分析层，默认全部。
                          "market" = 市场层，"sector" = 板块层，"stock" = 个股层
            debug: 是否开启调试模式
            config: 配置字典，为 None 时从环境变量加载
        """
        if selectedLayer is None:
            selectedLayer = ["market", "sector", "stock"]

        self.selectedLayer = selectedLayer
        self.debug = debug
        self.config = config or load_config()

        set_config(self.config)
        trace_step("TradingAgentsGraph 初始化", debug=debug,
                   layers=selectedLayer,
                   memory=self.config.get("memory_enabled"))

        # ---- 日志回调（LLM 初始化之前创建） ----
        self.llm_handler = LLMCallbackHandler()
        self.tool_handler = ToolCallbackHandler()

        # ---- LLM 初始化 (仅 ChatOpenAI) ----
        api_key = self.config.get("api_key", "")
        base_url = self.config.get("base_url", "https://api.openai.com/v1")

        if not api_key:
            raise ValueError(
                "未配置 LIVEPROFIT_API_KEY。请在 a.bash 中设置，然后执行 source a.bash"
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
            callbacks=[self.llm_handler],
        )

        self.deep_thinking_llm = ChatOpenAI(
            model=deep_model,
            base_url=base_url,
            api_key=api_key,
            temperature=deep_temp,
            max_tokens=max_tokens * 2,
            timeout=300,
            callbacks=[self.llm_handler],
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

        # 编译编排图
        self.graph = self._build_graph()
        trace_step("编排图编译完成")

    # ==================== 编排图构建 ====================

    def _build_graph(self):
        """构建编排图（自顶向下）：Market Layer → Sector Layer → Stock Layer → END

        全市场模式（selectedLayer 含 "screening"）：
        Market Layer → Sector Layer → Screening → END，
        个股逐票循环由 propagate() 层驱动（复用 self.stock_subgraph）。
        """
        workflow = StateGraph(AgentState)

        has_market = "market" in self.selectedLayer
        has_sector = "sector" in self.selectedLayer
        has_screening = "screening" in self.selectedLayer
        has_stock = "stock" in self.selectedLayer

        # Market Layer 子图
        if has_market:
            market_subgraph = MarketLayerGraph(
                self.quick_thinking_llm, self.toolkit
            ).build()
            workflow.add_node("Market Layer", market_subgraph)
            logger.info("[编排图] 已添加 Market Layer 子图")

        # Sector Layer 子图（结构化清单提取仅全市场模式启用，单票模式行为与现状一致）
        if has_sector:
            sector_subgraph = SectorLayerGraph(
                self.quick_thinking_llm, self.toolkit,
                enable_structured_list=has_screening,
            ).build()
            workflow.add_node("Sector Layer", sector_subgraph)
            logger.info("[编排图] 已添加 Sector Layer 子图")

        # Stock Layer 子图（个股层）——编译后挂到 self 供 propagate 逐票循环复用；
        # 单票模式仅显式选择 stock 层时挂进顶层图
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
        ).build()
        self.stock_subgraph = stock_subgraph
        if has_stock and not has_screening:
            # 全市场模式下 Stock Layer 不进顶层图（由 propagate 层循环 invoke）
            workflow.add_node("Stock Layer", stock_subgraph)
            logger.info("[编排图] 已添加 Stock Layer 子图")

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

        if has_screening:
            # 全市场模式：Screening 普通函数节点直接接 END
            workflow.add_node(
                "Screening",
                checkpoint.guard_checkpoint("Screening")(
                    track_node("Screening")(make_screening_node(self.config))),
            )
            if prev_node is not None:
                workflow.add_edge(prev_node, "Screening")
            else:
                first_node = "Screening"
            workflow.add_edge("Screening", END)
            logger.info("[编排图] 已添加 Screening 节点（纯代码，无 LLM）")
        elif has_stock:
            # 个股层仅在显式选择时挂进顶层图
            if prev_node is not None:
                workflow.add_edge(prev_node, "Stock Layer")
            else:
                first_node = "Stock Layer"
            workflow.add_edge("Stock Layer", END)
        else:
            # 仅市场/板块层：跳过个股层，直接收口到 END
            if prev_node is not None:
                workflow.add_edge(prev_node, END)
            else:
                raise ValueError("selectedLayer 未包含任何有效层")

        workflow.add_edge(START, first_node)

        layer_names = []
        if has_market:
            layer_names.append("Market")
        if has_sector:
            layer_names.append("Sector")
        if has_screening:
            layer_names.append("Screening")
        elif has_stock:
            layer_names.append("Stock")
        logger.info(f"[编排图] 编译完成: {' → '.join(layer_names)} → END")

        return workflow.compile()

    # ==================== 运行时 ====================

    def propagate(self, init_state, progress_callback=None):
        """运行交易分析图（自顶向下：市场 → 板块 → 个股）

        Args:
            init_state: 初始状态字典，需包含 company_of_interest、trade_date 等字段
                （本方法不就地修改入参——日期校正/prompt_overrides pop 只发生在
                内部浅拷贝上，调用方不得依赖入参被回写）
            progress_callback: 可选的进度回调函数

        Returns:
            (final_state, decision_dict)
        """
        prepared, log_dir, debug_step, ctx = self._prepare_run(
            dict(init_state), progress_callback)
        return self._propagate_inner(prepared, log_dir, debug_step, ctx,
                                     progress_callback)

    def rerun_from_node(self, init_state, checkpoint_state, node_id,
                        progress_callback=None):
        """从节点续跑（单Agent重跑方案 3.2）：checkpoint 态为 entry，
        上游节点快进复用，目标（环成员目标上移环入口）及下游重新执行。

        Args:
            init_state: 新 attempt 初始状态（含 platform_log_dir/attempt_no/
                task_id/selected_layers/prompt_overrides 等平台字段）
            checkpoint_state: 经 deserialize_checkpoint 还原的 entry 态
            node_id: 用户选择的重跑起点节点 id（如 "market:CN News Analyst"）

        Returns:
            (final_state, decision_dict)
        """
        merged = dict(checkpoint_state)
        # 新 attempt 元数据覆盖 checkpoint 态中的旧值
        for key in ("platform_log_dir", "attempt_no", "task_id", "selected_layers",
                    "prompt_overrides"):
            if key in init_state:
                merged[key] = init_state[key]
        # 环成员目标上移环入口：跳过只发生在环入口之前（环整体重演）
        merged["_rerun_from"] = checkpoint._LOOP_ENTRY.get(node_id, node_id)

        prepared, log_dir, debug_step, ctx = self._prepare_run(
            merged, progress_callback)
        checkpoint.write_rerun_marker(
            log_dir, node_id, max(int(prepared.get("attempt_no", 1)) - 1, 0))
        if progress_callback:
            progress_callback(f"从节点 {node_id} 续跑：上游复用上次结果")
        return self._propagate_inner(prepared, log_dir, debug_step, ctx,
                                     progress_callback)

    def _prepare_run(self, init_state, progress_callback=None):
        """propagate/rerun_from_node 共用的运行准备：
        覆盖快照 → 防御性日期校正 → 日志目录/回调 → checkpoint 入口保存。
        返回 (prepared_state, log_dir, debug_step, ctx)。
        """
        company_name = init_state.get("company_of_interest", "")
        trade_date = init_state.get("trade_date", "")
        raw_date = init_state.get("requested_trade_date", trade_date)
        date_correction = init_state.get("date_correction", "")

        # ---- 提示词覆盖快照（平台注入；CLI/内核测试不含该 key → 恒默认） ----
        agent_prompts.set_overrides(init_state.pop("prompt_overrides", None))

        # ---- 防御性日期校正（安全网） ----
        # 如果上游 create_initial_state 未被调用（如 tests 直接构造 init_state），
        # 在此做兜底校正，确保 state 中的 trade_date 始终是有效数据日期。
        if not date_correction and trade_date:
            try:
                from AI.dataflows.utils.trading_calendar import get_available_trade_date
                corrected = get_available_trade_date(trade_date)
                if corrected != trade_date:
                    raw_date = trade_date
                    date_correction = f"{trade_date} → {corrected}"
                    trade_date = corrected
                    init_state["trade_date"] = corrected
                    init_state["requested_trade_date"] = raw_date
                    init_state["date_correction"] = date_correction
                    logger.warning(
                        f"[propagate 防御校正] {raw_date} → {trade_date}"
                    )
            except Exception as e:
                logger.debug(f"propagate 防御校正跳过: {e}")

        self.ticker = company_name or "market_screening"

        # ---- 本次运行的日志目录 ----
        log_dir = resolve_run_log_dir(init_state)
        log_dir.mkdir(parents=True, exist_ok=True)  # 平台多级目录（logs/tasks/{uuid}/{n}）提前建
        self.llm_handler.set_log_dir(log_dir)
        self.tool_handler.set_log_dir(log_dir)
        checkpoint.set_checkpoint_run_dir(log_dir)
        checkpoint.save_init_state(init_state)
        logger.info(f"日志目录: {log_dir.resolve()}")

        # ---- 调试步进模式（LIVEPROFIT_DEBUG_STEP=true）----
        debug_step = self.config.get("debug_step", False)
        if debug_step:
            step_gate.enable(log_dir)
            trace_step("调试步进模式已启用",
                       checkpoint_file=str(step_gate.checkpoint_file()))

        trace_step("propagate 入口", company=company_name, trade_date=trade_date,
                   raw_date=raw_date, correction=date_correction,
                   callback=bool(progress_callback))

        if date_correction:
            logger.info(f"开始分析: {company_name} @ {trade_date}（原始请求 {raw_date}）")
        else:
            logger.info(f"开始分析: {company_name} @ {trade_date}")

        ctx = {
            "company_name": company_name,
            "trade_date": trade_date,
            "raw_date": raw_date,
            "date_correction": date_correction,
        }
        return init_state, log_dir, debug_step, ctx

    def _propagate_inner(self, init_state, log_dir, debug_step, ctx,
                         progress_callback=None):
        """propagate/rerun_from_node 共用的执行主体（图运行 → 报告 → 决策）。"""
        trade_date = ctx["trade_date"]

        args = self.propagator.get_graph_args(
            use_progress_callback=bool(progress_callback)
        )
        # 将日志回调合并进图配置，覆盖 LLM + 工具调用
        existing_callbacks = args.get("config", {}).get("callbacks", [])
        args.setdefault("config", {})["callbacks"] = existing_callbacks + [
            self.llm_handler, self.tool_handler
        ]
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
        self._write_reports(final_state, log_dir)

        # ---- 全市场模式：逐票循环个股层（一期顺序循环，复用同一编译子图） ----
        if "screening" in self.selectedLayer:
            from AI.graph.stock_loop import run_stock_loop
            pool = final_state.get("candidate_stock_pool", [])
            risk_gate = final_state.get("risk_gate", "normal")
            if risk_gate == "block" or not pool:
                if risk_gate == "block":
                    logger.warning("[propagate] risk_gate=block，跳过选股买入流程")
                final_state["stock_results"] = {}
            else:
                stock_results = run_stock_loop(
                    self.stock_subgraph, final_state, pool,
                    self.signal_processor.process_signal,
                )
                final_state["stock_results"] = stock_results

            # 仓位管理层（纯代码收口，含 risk 计划）
            if "position" in self.selectedLayer:
                from AI.position.position_manager import (
                    build_position_plan,
                    build_risk_plan,
                )
                if risk_gate == "block" or not pool:
                    final_state["final_position_plan"] = build_risk_plan(
                        final_state, self.config
                    )
                else:
                    final_state["final_position_plan"] = build_position_plan(
                        final_state, self.config
                    )
                self._write_reports(final_state, log_dir)

            trace_step("全市场模式循环完成",
                       stocks=len(final_state.get("stock_results", {})))

        trace_step("图执行完成", nodes_visited=len([k for k in final_state.keys()
                     if not k.startswith('__')]))
        trace_step("开始信号处理", stock=ctx["company_name"])

        # 处理决策信号：
        # - 单票模式：抽取 final_trade_decision（原路径不变）
        # - 全市场模式：逐票决策已在 stock_results[code]["decision_json"]，
        #   顶层态无 final_trade_decision → 返回语义明确的默认决策
        if "screening" in self.selectedLayer:
            decision = {
                "action": "持有",
                "target_price": None,
                "stop_loss": None,
                "confidence": 0.5,
                "risk_score": 0.5,
                "reasoning": "全市场模式：逐票决策见 stock_results",
            }
        else:
            decision = self.process_signal(
                final_state.get("final_trade_decision", "")
            )
        trace_step("决策提取完成", action=decision.get("action"),
                   price=decision.get("target_price"), conf=decision.get("confidence"))

        # 步进模式收尾：清理门控状态（防跨 run 残留）
        if debug_step:
            step_gate.disable()

        # 完成标记：成功收尾原子写（失败/取消的 attempt 目录无此文件，
        # checkpoint 目录链回溯以此过滤部分执行目录，见 AI/graph/checkpoint.py）
        checkpoint.write_complete_marker(log_dir)

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
                "requested_date": final_state.get("requested_trade_date", ""),
                "date_correction": final_state.get("date_correction", ""),
                "international_news_report": final_state.get("international_news_report", ""),
                "us_news_report": final_state.get("us_news_report", ""),
                "us_tech_report": final_state.get("us_tech_report", ""),
                "kr_news_report": final_state.get("kr_news_report", ""),
                "kr_tech_report": final_state.get("kr_tech_report", ""),
                "cn_news_report": final_state.get("cn_news_report", ""),
                "cn_tech_report": final_state.get("cn_tech_report", ""),
                # 结构化 dict 字段（T6 起 market_regime/market_event_calendar 为
                # dict，json.dump 直接序列化；缺失落 {} 而非 ""，保持类型一致）
                "market_regime": final_state.get("market_regime", {}),
                "market_event_calendar": final_state.get("market_event_calendar", {}),
                "global_risk_assessment": final_state.get("global_risk_assessment", {}),
                "risk_gate": final_state.get("risk_gate", ""),
                # 三级结构化事件（T4/m11）：dict 直存，各 ≤5 条（预取上限同口径）
                "international_events": _log_event_list(
                    final_state.get("international_events")),
                "sector_events": _log_event_list(final_state.get("sector_events")),
                "stock_events": _log_event_list(final_state.get("stock_events")),
                "sector_news_report": final_state.get("sector_news_report", ""),
                "sector_tech_report": final_state.get("sector_tech_report", ""),
                "sector_shortlist": final_state.get("sector_shortlist", ""),
                "sector_tech_confirm": final_state.get("sector_tech_confirm", ""),
                "rotation_prediction_report": final_state.get("rotation_prediction_report", ""),
                "rotation_top_picks": final_state.get("rotation_top_picks", ""),
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

    def _write_reports(self, final_state: dict, log_dir: Path) -> None:
        """将各层分析报告写入独立 Markdown 文件"""
        report_dir = log_dir / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)

        reports = [
            # (序号, 文件名, state key)
            ("01", "market_international_news", "international_news_report"),
            ("02", "market_us_news", "us_news_report"),
            ("03", "market_us_tech", "us_tech_report"),
            ("04", "market_kr_news", "kr_news_report"),
            ("05", "market_kr_tech", "kr_tech_report"),
            ("06", "market_cn_news", "cn_news_report"),
            ("07", "market_cn_tech", "cn_tech_report"),
            ("08", "sector_news", "sector_news_report"),
            ("09", "sector_tech", "sector_tech_report"),
            ("09c", "sector_shortlist", "sector_shortlist"),
            ("09d", "sector_tech_confirm", "sector_tech_confirm"),
            ("09e", "sector_rotation", "rotation_prediction_report"),
            ("09f", "sector_rotation_top_picks", "rotation_top_picks"),
            ("10", "stock_tech", "stock_tech_report"),
            ("11", "sentiment", "sentiment_report"),
            ("12", "stock_news", "news_report"),
            ("13", "fundamentals", "fundamentals_report"),
            ("14", "investment_plan", "investment_plan"),
            ("15", "trader_plan", "trader_investment_plan"),
            ("16", "final_decision", "final_trade_decision"),
        ]

        for seq, name, key in reports:
            content = final_state.get(key, "")
            if content:
                fpath = report_dir / f"{seq}_{name}.md"
                fpath.write_text(str(content), encoding="utf-8")
                logger.info(f"报告已写入: {fpath}")
            else:
                logger.debug(f"报告为空，跳过: {seq}_{name}")

        # 结构化 dict 字段落盘为 JSON（市场环境/资金日历/选股池/逐票结果/交易计划）
        # T6：09a/09b 原为 str 落 .md，dict 原地替换后改走 JSON 落盘（避免 Python repr）
        dict_reports = [
            ("09a", "market_regime", "market_regime"),
            ("09b", "market_event_calendar", "market_event_calendar"),
            ("17", "candidate_stock_pool", "candidate_stock_pool"),
            ("18", "stock_results", "stock_results"),
            ("19", "final_position_plan", "final_position_plan"),
        ]
        for seq, name, key in dict_reports:
            content = final_state.get(key)
            if content:
                fpath = report_dir / f"{seq}_{name}.json"
                fpath.write_text(
                    json.dumps(content, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8",
                )
                logger.info(f"结构化结果已写入: {fpath}")
            else:
                logger.debug(f"结构化结果为空，跳过: {seq}_{name}")

        logger.info(f"全部报告已写入: {report_dir.resolve()}")

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

    def process_signal(self, full_signal):
        """处理信号以提取核心决策"""
        return self.signal_processor.process_signal(full_signal)
