"""分析任务 REST 路由（§3.2.1：只做协议编解码与错误映射）。"""

from __future__ import annotations

import logging
import shutil
import uuid

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import ValidationError

from backend.api.cursors import decode_cursor, encode_cursor
from backend.api.dependencies import ensure_trace_context
from backend.api.schemas.envelope import DeleteResultData, Envelope, EnvelopeMeta
from backend.api.schemas.problem import ProblemError
from backend.api.schemas.tasks import (
    CreateAnalysisTaskRequest,
    TaskCreatedData,
    TaskDTO,
    TaskListData,
    TaskListItemDTO,
)
from backend.bootstrap.settings import resolve_execution_logs_root
from backend.modules.analysis.application.contracts import (
    CreateAnalysisTaskCommand,
    TaskListQuery,
)
from backend.modules.analysis.domain.enums import TaskType

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["analysis-tasks"])

VALID_STATUS_FILTERS = {"all", "active", "succeeded", "failed", "cancelled"}

_EVENTS_URL = "/api/v1/analysis-tasks/{task_id}/events"
_REPORT_URL = "/api/v1/analysis-tasks/{task_id}/report"
_EXECUTION_LOGS_URL = "/api/v1/analysis-tasks/{task_id}/execution-logs"
_GRAPH_TOPOLOGY_URL = "/api/v1/analysis-tasks/{task_id}/graph-topology"


def _meta(request: Request, next_cursor: str | None = None) -> EnvelopeMeta:
    return EnvelopeMeta(request_id=request.state.trace_id, next_cursor=next_cursor)


@router.post("/analysis-tasks", status_code=202, response_model=Envelope[TaskCreatedData])
async def create_task(
    payload: CreateAnalysisTaskRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    trace_id: str = Depends(ensure_trace_context),
):
    if not idempotency_key:
        raise ProblemError(422, "VALIDATION_ERROR", "缺少 Idempotency-Key 请求头")
    command = CreateAnalysisTaskCommand(
        task_type=TaskType(payload.task_type),
        ticker=payload.ticker,
        requested_trade_date=payload.requested_trade_date,
        selected_layers=tuple(payload.selected_layers),
        analysis_options=payload.analysis_options.model_dump() if payload.analysis_options else {},
        trace_id=trace_id,
    )
    services = request.app.state.analysis_services

    def _do():
        with services.open() as bundle:
            return bundle.tasks.create_task(command, idempotency_key=idempotency_key, trace_id=trace_id)

    result = await services.run(_do)
    task_id = result.task_id
    data = TaskCreatedData(
        task_id=task_id,
        status=result.status.value,
        requested_trade_date=result.requested_trade_date,
        effective_trade_date=result.effective_trade_date,
        date_correction=result.date_correction,
        events_url=_EVENTS_URL.format(task_id=task_id),
        report_url=_REPORT_URL.format(task_id=task_id),
        execution_logs_url=_EXECUTION_LOGS_URL.format(task_id=task_id),
        graph_topology_url=_GRAPH_TOPOLOGY_URL.format(task_id=task_id),
    )
    return Envelope(data=data, meta=_meta(request)).model_dump()


@router.delete("/analysis-tasks/{task_id}", response_model=Envelope[DeleteResultData])
async def delete_task(
    task_id: uuid.UUID,
    request: Request,
    trace_id: str = Depends(ensure_trace_context),
):
    """删除任务记录（仅终态任务可删；FK 级联删除报告与 Outbox，DELETE 返回 200 envelope）。"""
    services = request.app.state.analysis_services

    def _do():
        with services.open() as bundle:
            result = bundle.tasks.delete_task(task_id)
            # 执行调用日志目录 best-effort 清理（DB 级联删除成功后一次清掉所有 attempt 目录；
            # 终态任务 worker 已停，正常不会失败；Windows 文件占用兜底不阻塞删除语义）
            _cleanup_execution_logs(request, task_id)
            return result

    result = await services.run(_do)
    return Envelope(
        data=DeleteResultData(deleted=result["deleted"], resource_id=result["resource_id"]),
        meta=_meta(request),
    ).model_dump()


@router.get("/analysis-tasks", response_model=Envelope[TaskListData])
async def list_tasks(
    request: Request,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    status: str = Query(default="all"),
    trace_id: str = Depends(ensure_trace_context),
):
    if status not in VALID_STATUS_FILTERS:
        raise ProblemError(422, "INVALID_TASK_FILTER", f"status 取值非法：{status}")
    try:
        decoded_cursor = decode_cursor(cursor) if cursor else None
    except (ValueError, TypeError, KeyError, ValidationError):
        raise ProblemError(422, "VALIDATION_ERROR", "cursor 非法") from None

    services = request.app.state.analysis_services

    def _do():
        with services.open() as bundle:
            return bundle.tasks.list_tasks(
                TaskListQuery(cursor=decoded_cursor, limit=limit, status=status)
            )

    items, next_cursor_tuple = await services.run(_do)
    next_cursor = None
    if next_cursor_tuple is not None:
        next_cursor = encode_cursor(next_cursor_tuple[0], next_cursor_tuple[1])
    data = TaskListData(items=[_to_list_item(item) for item in items])
    return Envelope(data=data, meta=_meta(request, next_cursor=next_cursor)).model_dump()


@router.get("/analysis-tasks/{task_id}", response_model=Envelope[TaskDTO])
async def get_task(
    task_id: uuid.UUID,
    request: Request,
    trace_id: str = Depends(ensure_trace_context),
):
    services = request.app.state.analysis_services

    def _do():
        with services.open() as bundle:
            return bundle.tasks.get_task(task_id)

    dto = await services.run(_do)
    data = _to_task_dto(dto)
    return Envelope(data=data, meta=_meta(request)).model_dump()


@router.post("/analysis-tasks/{task_id}/cancel", response_model=Envelope[TaskDTO])
async def cancel_task(
    task_id: uuid.UUID,
    request: Request,
    trace_id: str = Depends(ensure_trace_context),
):
    services = request.app.state.analysis_services

    def _do():
        with services.open() as bundle:
            return bundle.tasks.request_cancel(task_id)

    dto = await services.run(_do)
    data = _to_task_dto(dto)
    return Envelope(data=data, meta=_meta(request)).model_dump()


def _to_list_item(item) -> TaskListItemDTO:
    return TaskListItemDTO(
        id=item.id,
        task_type=item.task_type.value,
        ticker=item.ticker,
        effective_trade_date=item.effective_trade_date,
        status=item.status.value,
        attempt_no=item.attempt_no,
        error_code=item.error_code,
        error_summary=item.error_summary,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def _to_task_dto(dto) -> TaskDTO:
    return TaskDTO(
        id=dto.id,
        task_type=dto.task_type.value,
        ticker=dto.ticker,
        requested_trade_date=dto.requested_trade_date,
        effective_trade_date=dto.effective_trade_date,
        date_correction=dto.date_correction,
        selected_layers=list(dto.selected_layers),
        status=dto.status.value,
        attempt_no=dto.attempt_no,
        next_retry_at=dto.next_retry_at,
        error_code=dto.error_code,
        error_summary=dto.error_summary,
        created_at=dto.created_at,
        updated_at=dto.updated_at,
        events_url=_EVENTS_URL.format(task_id=dto.id),
        report_url=_REPORT_URL.format(task_id=dto.id),
        execution_logs_url=_EXECUTION_LOGS_URL.format(task_id=dto.id),
        graph_topology_url=_GRAPH_TOPOLOGY_URL.format(task_id=dto.id),
    )


def _cleanup_execution_logs(request: Request, task_id: uuid.UUID) -> None:
    """删除任务后 best-effort 清理执行日志目录（所有 attempt）；失败仅 log warning。"""
    run_root = resolve_execution_logs_root(request.app.state.settings.core)
    try:
        shutil.rmtree(run_root / "tasks" / str(task_id))
    except FileNotFoundError:
        pass  # 从未产生日志目录
    except OSError:
        logger.warning("执行日志目录清理失败（不阻塞删除语义）: tasks/%s", task_id)
