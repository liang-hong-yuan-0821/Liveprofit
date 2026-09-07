"""通用依赖：request_id / trace 上下文、workspace UoW。"""

from __future__ import annotations

import uuid
from contextlib import contextmanager

from fastapi import Request

from backend.bootstrap.observability import get_trace_id, set_trace_id


def ensure_trace_context(request: Request) -> str:
    """从 X-Trace-ID 请求头恢复 trace context，缺失时生成新 ID。"""
    trace_id = request.headers.get("X-Trace-ID") or request.headers.get("X-Request-ID")
    if not trace_id:
        trace_id = uuid.uuid4().hex
    set_trace_id(trace_id)
    return trace_id


@contextmanager
def open_workspace_uow(services):
    """自选/组合路由：在 API 线程池内打开独立 sync Session（事务边界在 Service）。"""
    from backend.modules.investment_workspace.infrastructure.repositories import (
        SqlAlchemyWorkspaceUnitOfWork,
    )

    with SqlAlchemyWorkspaceUnitOfWork(services._container.sync_session_factory) as uow:
        yield uow
