"""TradingGraphAdapter：AI 内核图的执行适配（§3.1.4 / §3.1.5）。

- 每次执行新建 TradingAgentsGraph，禁止缓存或跨任务复用可变 State/callback handler。
- 组装初始 State、调用 propagate()，保留交易日校正；不更新 PG 状态。
- 真实 AI 内核接线（AI/graph/trading_graph.py + RunContext/Artifact 转换）在 T6 完成；
  T4 先交付可注入 GraphPort 契约与集成测试用 fake graph。
"""

from __future__ import annotations

from typing import Any, Callable, Protocol

from backend.modules.analysis.application.contracts import ClaimedTask

ProgressCallback = Callable[[str], None]


class GraphPort(Protocol):
    """AI 图最小契约：propagate / rerun_from_node（单Agent重跑）。"""

    def propagate(self, init_state: dict, progress_callback: ProgressCallback) -> Any: ...

    def rerun_from_node(
        self,
        init_state: dict,
        checkpoint_state: dict,
        node_id: str,
        progress_callback: ProgressCallback,
    ) -> Any: ...


class TradingGraphAdapter:
    def __init__(
        self,
        graph_factory: Callable[[list[str] | None], GraphPort],
        *,
        initial_state_factory: Callable[[ClaimedTask], dict] | None = None,
    ) -> None:
        self._graph_factory = graph_factory
        self._initial_state_factory = initial_state_factory or _default_initial_state

    def execute(
        self,
        task: ClaimedTask,
        on_progress: ProgressCallback,
        rerun_from: str | None = None,
    ) -> Any:
        """执行 AI 图；返回 final_state（Artifact 提取由 artifact_builder 完成）。

        判定依据 = rerun_from 参数（消息级触发源，不读 init_state/task 行）：
        非 None → 走 rerun_from_node（checkpoint_state 由 initial_state_factory 注入）。
        """
        graph = self._graph_factory(list(task.selected_layers))  # 每次新建，禁止跨任务复用
        init_state = self._initial_state_factory(task)
        if rerun_from is not None:
            checkpoint_state = init_state.pop("checkpoint_state")
            return graph.rerun_from_node(init_state, checkpoint_state, rerun_from, on_progress)
        return graph.propagate(init_state, on_progress)


def _default_initial_state(task: ClaimedTask) -> dict:
    """fake graph 场景的默认初始状态（真实内核路径由 build_real_initial_state 提供）。"""
    return {
        "task_id": str(task.task_id),
        "attempt_no": task.attempt_no,
        "ticker": task.ticker,
        "effective_trade_date": task.effective_trade_date,
        "selected_layers": list(task.selected_layers),
    }
