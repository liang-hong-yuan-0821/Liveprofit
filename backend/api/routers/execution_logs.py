"""执行调用日志 REST 路由（任务执行调用日志方案 §3.2.1：只做协议编解码与错误映射）。

- 树端点：返回任务日志目录完整树（layer → 节点 → DP/tushare/tools/LLM）；
  目录不存在 → available=false（PENDING 尚未建目录是正常态，不是错误）。
- content 端点：截断文件「查看完整内容」全量拉取（路径逃逸双保险校验）。
- run_dir 由 task_id 服务端构造（用户不可控根）；任务存在性经 bundle 校验（404 TASK_NOT_FOUND）。
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request

from backend.api.dependencies import ensure_trace_context
from backend.api.schemas.envelope import Envelope, EnvelopeMeta
from backend.api.schemas.execution_logs import ExecutionFileDTO, ExecutionLogsDTO
from backend.api.schemas.problem import ProblemError
from backend.bootstrap.settings import resolve_execution_logs_root
from backend.modules.analysis.application.execution_logs import build_execution_logs_tree
from backend.shared.clock import SystemClock

router = APIRouter(prefix="/api/v1", tags=["analysis-tasks"])

_ALLOWED_CONTENT_SUFFIXES = {".json", ".md", ".txt"}
_CONTENT_FILE_MAX_BYTES = 10 * 1024 * 1024  # content 端点单文件上限（树内嵌为 100KB）


def _meta(request: Request, next_cursor: str | None = None) -> EnvelopeMeta:
    return EnvelopeMeta(request_id=request.state.trace_id, next_cursor=next_cursor)


def _task_run_dir(request: Request, task_id: uuid.UUID, attempt_no: int) -> Path:
    """任务日志目录（与 worker 写入侧同一解析函数，跨进程一致）。"""
    return resolve_execution_logs_root(request.app.state.settings.core) / "tasks" / str(task_id) / str(attempt_no)


@router.get(
    "/analysis-tasks/{task_id}/execution-logs",
    response_model=Envelope[ExecutionLogsDTO],
)
async def get_execution_logs(
    task_id: uuid.UUID,
    request: Request,
    trace_id: str = Depends(ensure_trace_context),
):
    services = request.app.state.analysis_services

    def _do():
        with services.open() as bundle:
            task = bundle.tasks.get_task(task_id)  # 任务不存在 → 404 TASK_NOT_FOUND
            run_dir = _task_run_dir(request, task_id, task.attempt_no)
            tree = build_execution_logs_tree(run_dir)
            return task, tree

    task, tree = await services.run(_do)
    data = ExecutionLogsDTO(
        task_id=str(task_id),
        attempt_no=task.attempt_no,
        available=tree["available"],
        generated_at=SystemClock().now(),
        layers=tree["layers"],
    )
    return Envelope(data=data, meta=_meta(request)).model_dump()


@router.get(
    "/analysis-tasks/{task_id}/execution-logs/content",
    response_model=Envelope[ExecutionFileDTO],
)
async def get_execution_log_content(
    task_id: uuid.UUID,
    request: Request,
    file: str = Query(..., description="相对任务日志目录的文件路径（/ 分隔）"),
    trace_id: str = Depends(ensure_trace_context),
):
    services = request.app.state.analysis_services

    def _do():
        with services.open() as bundle:
            task = bundle.tasks.get_task(task_id)  # 任务不存在 → 404 TASK_NOT_FOUND
            run_dir = _task_run_dir(request, task_id, task.attempt_no)

            # 路径校验（双保险：格式白名单 + resolve 前缀，防路径逃逸）
            raw = (file or "").strip()
            suffix = Path(raw).suffix.lower() if raw else ""
            if (
                not raw
                or "\\" in raw
                or raw.startswith(("/", "~"))
                or ":" in raw.split("/")[0]
                or suffix not in _ALLOWED_CONTENT_SUFFIXES
            ):
                raise ProblemError(422, "VALIDATION_ERROR", f"file 参数非法：{file!r}")
            target = (run_dir / raw).resolve()
            if run_dir.resolve() not in target.parents:
                raise ProblemError(422, "VALIDATION_ERROR", f"file 参数非法：{file!r}")
            if not target.is_file():
                raise ProblemError(404, "RESOURCE_NOT_FOUND", f"日志文件不存在：{raw}")
            size = target.stat().st_size
            if size > _CONTENT_FILE_MAX_BYTES:
                raise ProblemError(422, "VALIDATION_ERROR", f"文件超过 10MB 上限：{raw}")

            from AI.logviewer import logs_reader  # 延迟导入：与树读取器同源

            kind = "json" if suffix == ".json" else ("txt" if suffix == ".txt" else "md")
            content = None
            parse_error = False
            if kind == "json":
                content = logs_reader.read_json(target)
                parse_error = content is None
            else:
                content = logs_reader.read_text(target)
            return task, ExecutionFileDTO(
                path=target.relative_to(run_dir).as_posix(),
                kind=kind,
                content=content,
                parse_error=parse_error,
                truncated=False,
                total_bytes=size,
            )

    _, dto = await services.run(_do)
    return Envelope(data=dto, meta=_meta(request)).model_dump()
