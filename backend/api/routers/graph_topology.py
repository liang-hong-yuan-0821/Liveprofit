"""图拓扑 REST 路由（任务拓扑图方案 §3.2.1：只做协议编解码与错误映射）。

- 静态拓扑来自 AI.graph.topology.build_topology（图定义唯一事实来源，dummy 编译
  不消耗 LLM，lru_cache 缓存）；
- 运行状态由 scan_run_status 扫描任务日志目录叠加（状态规则见其模块 docstring）；
- run_dir 与 execution-logs 端点同源（resolve_execution_logs_root，跨进程一致）；
- 任务存在性经 bundle 校验（404 TASK_NOT_FOUND）；目录不存在 → available=false
  （PENDING 尚未建目录是正常态，不是错误）。
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Request

from backend.api.dependencies import ensure_trace_context
from backend.api.schemas.envelope import Envelope, EnvelopeMeta
from backend.api.schemas.graph_topology import GraphTopologyDTO, TopologyEdgeDTO, TopologyNodeDTO
from backend.bootstrap.settings import resolve_execution_logs_root
from backend.modules.analysis.application.graph_topology import scan_run_status
from backend.modules.analysis.domain.enums import TaskStatus
from backend.modules.analysis.domain.state_machine import is_terminal
from backend.shared.clock import SystemClock

router = APIRouter(prefix="/api/v1", tags=["analysis-tasks"])


def _meta(request: Request) -> EnvelopeMeta:
    return EnvelopeMeta(request_id=request.state.trace_id)


def _task_run_dir(request: Request, task_id: uuid.UUID, attempt_no: int) -> Path:
    """任务日志目录（与 worker 写入侧同一解析函数，跨进程一致）。"""
    return resolve_execution_logs_root(request.app.state.settings.core) / "tasks" / str(task_id) / str(attempt_no)


def _compute_rerun_availability(request: Request, task, topology) -> dict[str, bool]:
    """任务拓扑节点的 rerun_available（单Agent重跑方案 3.4.1）。

    非终态任务 → 全部 false；终态任务 → 逐节点沿 attempt 目录链
    （仅 complete.json 标记目录）resolve_entry_checkpoint，命中 → true；
    screening 任务 stock 层节点恒 false（v1 限制）。每节点仅文件存在性探测。
    """
    from backend.modules.analysis.application.graph_topology import attempt_chain_dirs
    from AI.utils.checkpoint import resolve_entry_checkpoint

    if not is_terminal(task.status):
        return {}
    root = resolve_execution_logs_root(request.app.state.settings.core)
    run_dirs = attempt_chain_dirs(root, task_id=task.id, attempt_no=task.attempt_no)
    if not run_dirs:
        return {}
    screening = "screening" in (task.selected_layers or [])
    result = {}
    for n in topology.nodes:
        if screening and n.layer == "stock":
            result[n.id] = False
            continue
        result[n.id] = resolve_entry_checkpoint(
            run_dirs, topology, n.id, ticker=task.ticker) is not None
    return result


@router.get(
    "/analysis-tasks/{task_id}/graph-topology",
    response_model=Envelope[GraphTopologyDTO],
)
async def get_graph_topology(
    task_id: uuid.UUID,
    request: Request,
    trace_id: str = Depends(ensure_trace_context),
):
    services = request.app.state.analysis_services

    def _do():
        with services.open() as bundle:
            task = bundle.tasks.get_task(task_id)  # 任务不存在 → 404 TASK_NOT_FOUND
            from AI.graph.topology import build_topology  # 延迟导入：平台进程专属

            topology = build_topology(tuple(task.selected_layers))
            run_dir = _task_run_dir(request, task_id, task.attempt_no)
            statuses = scan_run_status(
                run_dir,
                topology,
                terminal=is_terminal(task.status),
                failed=task.status == TaskStatus.FAILED,
            )
            return task, topology, statuses, run_dir

    task, topology, statuses, run_dir = await services.run(_do)

    rerun_available = _compute_rerun_availability(request, task, topology)

    nodes = []
    for n in topology.nodes:
        entry = statuses.get(n.id, {"dirs": [], "status": "not_executed", "invocation_count": 0})
        nodes.append(TopologyNodeDTO(
            id=n.id,
            label=n.label,
            layer=n.layer,
            row=n.row,
            order=n.order,
            status=entry["status"],
            invocation_count=entry["invocation_count"],
            dirs=entry["dirs"],
            rerun_available=rerun_available.get(n.id, False),
        ))
    edges = [
        TopologyEdgeDTO(source=e.source, target=e.target, kind=e.kind, parallel=e.parallel)
        for e in topology.edges
    ]
    data = GraphTopologyDTO(
        task_id=str(task_id),
        attempt_no=task.attempt_no,
        available=run_dir.is_dir(),
        generated_at=SystemClock().now(),
        nodes=nodes,
        edges=edges,
    )
    return Envelope(data=data, meta=_meta(request)).model_dump()
