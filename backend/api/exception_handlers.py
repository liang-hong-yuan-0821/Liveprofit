"""领域错误 → RFC 7807 Problem Details 映射（§2.6 统一错误码总表）。

响应不泄露内部堆栈、Token、租约、artifact 路径或 Provider 原始异常。
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.api.schemas.problem import ProblemDetails, ProblemError
from backend.modules.analysis.application.errors import (
    DomainError,
    IdempotencyKeyInvalidError,
    IdempotencyKeyReusedError,
    InvalidStateConflictError,
    LeaseConflictError,
    ReportNotFoundError,
    TaskCreateInvalidError,
    TaskNotFoundError,
    TaskNotTerminalError,
)
from backend.shared.errors import NotFoundError as SharedNotFoundError

logger = logging.getLogger(__name__)

# code → (HTTP status, retryable)
_CODE_MAP: dict[str, tuple[int, bool]] = {
    "VALIDATION_ERROR": (422, False),
    "RESOURCE_NOT_FOUND": (404, False),
    "TASK_NOT_FOUND": (404, False),
    "REPORT_NOT_FOUND": (404, False),
    "AGENT_NODE_NOT_FOUND": (404, False),
    "AGENT_PROMPT_NOT_EDITABLE": (422, False),
    "RERUN_NOT_AVAILABLE": (409, False),
    "TASK_CREATE_INVALID": (422, False),
    "TASK_NOT_TERMINAL": (409, False),
    "IDEMPOTENCY_KEY_REUSED": (409, False),
    "INVALID_TASK_FILTER": (422, False),
    "REVISION_CONFLICT": (409, True),
    "WATCHLIST_NAME_CONFLICT": (409, False),
    "WATCHLIST_ITEM_DUPLICATE": (409, False),
    "WATCHLIST_NOT_EMPTY": (409, False),
    "WATCHLIST_ITEM_ORDER_CONFLICT": (409, True),
    "PORTFOLIO_NAME_CONFLICT": (409, False),
    "PORTFOLIO_NOT_EMPTY": (409, False),
    "PORTFOLIO_POSITION_CONFLICT": (409, True),
    "INVALID_POSITION": (422, False),
    "INTERVAL_NOT_SUPPORTED": (422, False),
    "RANGE_TOO_LARGE": (422, False),
    "ASSET_DISABLED": (409, False),
    "MARKET_DATA_UPSTREAM_UNAVAILABLE": (503, True),
    "HOT_CONCEPTS_UPSTREAM_UNAVAILABLE": (503, True),
    "EVENT_STUDY_BUSY": (503, True),
    "EVENT_STUDY_TIMEOUT": (504, True),
    "EVENT_STUDY_INTERNAL": (500, False),
    "REVIEW_DRAFT_NOT_FOUND": (404, False),
    "REVIEW_EVENT_NOT_FOUND": (404, False),
    "REVIEW_UPSTREAM_UNAVAILABLE": (503, True),
    "REVIEW_COMPUTE_FAILED": (500, True),
    "TASK_STATE_CONFLICT": (409, False),
    "TASK_LEASE_CONFLICT": (409, False),
    "INTERNAL_ERROR": (500, False),
}

# 特化错误类 → 稳定码（优先于类上的 code）
_SPECIAL_CODES = {
    IdempotencyKeyReusedError: "IDEMPOTENCY_KEY_REUSED",
    IdempotencyKeyInvalidError: "VALIDATION_ERROR",
    TaskCreateInvalidError: "TASK_CREATE_INVALID",
    TaskNotFoundError: "TASK_NOT_FOUND",
    TaskNotTerminalError: "TASK_NOT_TERMINAL",
    ReportNotFoundError: "REPORT_NOT_FOUND",
    InvalidStateConflictError: "TASK_STATE_CONFLICT",
    LeaseConflictError: "TASK_LEASE_CONFLICT",
    SharedNotFoundError: "RESOURCE_NOT_FOUND",
}


def _request_id(request: Request) -> str:
    return request.state.trace_id if hasattr(request.state, "trace_id") else "-"


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ProblemError)
    async def problem_error_handler(request: Request, exc: ProblemError) -> JSONResponse:
        return _render(request, exc.status, exc.code, exc.detail, exc.title, exc.retryable)

    @app.exception_handler(DomainError)
    async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
        code = _SPECIAL_CODES.get(type(exc), getattr(exc, "code", "INTERNAL_ERROR"))
        status, retryable = _CODE_MAP.get(code, (500, False))
        return _render(request, status, code, exc.message, retryable=retryable or exc.retryable)

    @app.exception_handler(SharedNotFoundError)
    async def shared_not_found_handler(request: Request, exc: SharedNotFoundError) -> JSONResponse:
        code = getattr(exc, "code", "RESOURCE_NOT_FOUND")
        status, _ = _CODE_MAP.get(code, (404, False))
        return _render(request, status, code, exc.message, retryable=False)

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = "RESOURCE_NOT_FOUND" if exc.status_code == 404 else "VALIDATION_ERROR"
        if exc.status_code in (401, 403):
            code = "VALIDATION_ERROR"
        status, retryable = _CODE_MAP.get(code, (exc.status_code, False))
        return _render(request, status, code, str(exc.detail), retryable=retryable)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        detail = f"参数校验失败：{first.get('loc', '')} {first.get('msg', '')}".strip()
        return _render(request, 422, "VALIDATION_ERROR", detail, retryable=False)

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("未处理异常：%s %s", request.method, request.url.path)
        return _render(request, 500, "INTERNAL_ERROR", "服务内部错误，请查看服务端日志", retryable=False)


def _render(
    request: Request,
    status: int,
    code: str,
    detail: str,
    title: str | None = None,
    retryable: bool = False,
) -> JSONResponse:
    problem = ProblemDetails(
        status=status,
        title=title or _default_title(status),
        detail=detail[:512],
        code=code,
        request_id=_request_id(request),
        retryable=retryable,
    )
    return JSONResponse(status_code=status, content=problem.model_dump())


def _default_title(status: int) -> str:
    return {
        400: "Bad Request",
        404: "Not Found",
        409: "Conflict",
        422: "Unprocessable Entity",
        500: "Internal Server Error",
        503: "Service Unavailable",
        504: "Gateway Timeout",
    }.get(status, "Error")
