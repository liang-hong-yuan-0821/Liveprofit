"""真实 AI 内核接线（T6）：Settings → CoreRunConfig → TradingAgentsGraph。

- AI/ 不依赖 backend；平台把 Settings 转为不可变 CoreRunConfig 传入 Adapter，
  禁止 AI 内核在平台模式重复隐式读环境变量。
- 每次执行新建 TradingAgentsGraph（禁止缓存/跨任务复用可变 State）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class CoreRunConfig:
    """平台传给 AI 内核的不可变运行配置（Secret 由内核按需使用，不落库）。"""

    api_key: str
    base_url: str
    quick_think_llm: str
    deep_think_llm: str
    quick_temperature: float
    deep_temperature: float
    max_tokens: int
    data_source: str
    debug: bool = False
    extra: Mapping[str, Any] = ()

    def as_kernel_config(self) -> dict:
        config = {
            "api_key": self.api_key,
            "base_url": self.base_url,
            "quick_think_llm": self.quick_think_llm,
            "deep_think_llm": self.deep_think_llm,
            "quick_temperature": self.quick_temperature,
            "deep_temperature": self.deep_temperature,
            "max_tokens": self.max_tokens,
            "data_source": self.data_source,
            "debug": self.debug,
        }
        config.update(dict(self.extra))
        return config


def build_core_run_config(settings) -> CoreRunConfig:
    core = settings.core
    return CoreRunConfig(
        api_key=core.api_key.get_secret_value(),
        base_url=core.base_url,
        quick_think_llm=core.quick_think_llm,
        deep_think_llm=core.deep_think_llm,
        quick_temperature=core.quick_temperature,
        deep_temperature=core.deep_temperature,
        max_tokens=core.max_tokens,
        data_source=core.data_source,
    )


class RealTradingGraphFactory:
    """每次调用新建 TradingAgentsGraph（AI 内核延迟导入，CLI 路径不受影响）。"""

    def __init__(self, run_config: CoreRunConfig) -> None:
        self._run_config = run_config

    def __call__(self, selected_layers: list[str] | None):
        from AI.graph.trading_graph import TradingAgentsGraph  # 延迟导入：平台进程专属

        config = self._run_config.as_kernel_config()
        graph = TradingAgentsGraph(selectedLayer=selected_layers or None, debug=self._run_config.debug, config=config)
        return _RealGraphAdapter(graph)


class _RealGraphAdapter:
    """包装内核图：propagate/rerun_from_node 返回合并后的最终状态
    （内核只返回信号字典，完整状态在 curr_state）。"""

    def __init__(self, graph) -> None:
        self._graph = graph

    def propagate(self, init_state, progress_callback):
        signal = self._graph.propagate(init_state, progress_callback)
        return self._merged_state(signal)

    def rerun_from_node(self, init_state, checkpoint_state, node_id, progress_callback):
        signal = self._graph.rerun_from_node(
            init_state, checkpoint_state, node_id, progress_callback)
        return self._merged_state(signal)

    def _merged_state(self, signal) -> dict:
        state = dict(getattr(self._graph, "curr_state", None) or {})
        state["signal"] = signal  # 决策信号并入 state，供 artifact_builder 提取
        return state


def build_real_initial_state(
    task,
    execution_logs_root: Path | None = None,
    prompt_overrides: dict[str, str] | None = None,
    rerun_from_node_id: str | None = None,
) -> dict:
    """用内核 Propagator.create_initial_state 构造初始 State（交易日校正在内核完成）。

    task: ClaimedTask（ticker/effective_trade_date/selected_layers）。
    execution_logs_root: 平台日志根目录；非 None 时注入 platform_log_dir
    （{root}/tasks/{task_id}/{attempt_no}），内核写确定性任务目录。
    fake/测试路径传 None → 不注入，内核回退 logs/{时间戳}。
    prompt_overrides: 执行开始快照的提示词覆盖（node_id → text），
    注入 init_state 后由内核 propagate 入口 set_overrides。
    rerun_from_node_id: 单Agent重跑起点（claim 时行 attempt_no 已递增为
    base+1，链起点 = attempt_no-1（=base）至 1，仅含 complete.json 标记目录）；
    非 None 时解析 entry checkpoint 注入 init_state["checkpoint_state"]，
    缺失/坏 JSON → FatalAnalysisError(RERUN_CHECKPOINT_MISSING)。
    """
    from AI.graph.propagation import Propagator  # 延迟导入：平台进程专属

    propagator = Propagator()
    trade_date = task.effective_trade_date.isoformat() if task.effective_trade_date else ""
    init_state = propagator.create_initial_state(trade_date)
    init_state["company_of_interest"] = task.ticker or ""
    # 平台元数据（内核忽略；artifact_builder 用 selected_layers 判 NOT_REQUESTED）
    init_state["selected_layers"] = list(task.selected_layers)
    init_state["task_id"] = str(task.task_id)
    init_state["attempt_no"] = task.attempt_no
    if execution_logs_root is not None:
        init_state["platform_log_dir"] = str(
            execution_logs_root / "tasks" / str(task.task_id) / str(task.attempt_no))
    if prompt_overrides is not None:
        init_state["prompt_overrides"] = dict(prompt_overrides)
    if rerun_from_node_id is not None:
        init_state["checkpoint_state"] = _load_rerun_checkpoint(
            task, execution_logs_root, rerun_from_node_id)
    return init_state


def _load_rerun_checkpoint(task, execution_logs_root, node_id: str) -> dict:
    from backend.modules.analysis.application.errors import FatalAnalysisError
    from backend.modules.analysis.application.graph_topology import attempt_chain_dirs
    from AI.graph.topology import build_topology
    from AI.utils.checkpoint import deserialize_checkpoint, resolve_entry_checkpoint

    assert execution_logs_root is not None
    # claim 时行 attempt_no 已递增为 base+1，链起点 = attempt_no-1（=base）
    run_dirs = attempt_chain_dirs(
        execution_logs_root, task.task_id, task.attempt_no - 1)
    topology = build_topology(tuple(task.selected_layers))
    path = resolve_entry_checkpoint(run_dirs, topology, node_id, ticker=task.ticker)
    if path is None:
        raise FatalAnalysisError(
            f"重跑 entry checkpoint 缺失：task={task.task_id} node={node_id}",
            code="RERUN_CHECKPOINT_MISSING",
        )
    try:
        return deserialize_checkpoint(path)
    except Exception as exc:  # 坏 JSON：明确报错而非静默退化（与 API 校验结论一致）
        raise FatalAnalysisError(
            f"重跑 entry checkpoint 解析失败：{path}",
            code="RERUN_CHECKPOINT_MISSING",
        ) from exc


def make_real_graph_factory(settings) -> Callable[[], Any]:
    """worker wiring 使用：为每个任务新建 factory（fresh graph per execution）。"""
    run_config = build_core_run_config(settings)

    def factory(selected_layers: list[str] | None = None):
        return RealTradingGraphFactory(run_config)(selected_layers)

    return factory
