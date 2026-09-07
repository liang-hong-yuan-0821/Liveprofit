"""RFC 7807 Problem Details（§2.6 统一错误码总表）。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ProblemDetails(BaseModel):
    type: str = Field(default="about:blank")
    title: str = Field(description="HTTP 语义标题")
    status: int = Field(description="HTTP 状态码")
    detail: str = Field(description="可读摘要")
    code: str = Field(description="稳定错误码（统一错误码总表）")
    request_id: str = Field(description="请求标识")
    retryable: bool = Field(description="是否可安全重试")


class ProblemError(Exception):
    """Router 层直接构造的 Problem Details 错误（由 exception_handlers 统一渲染）。"""

    def __init__(
        self,
        status: int,
        code: str,
        detail: str,
        *,
        title: str | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail
        self.title = title or _DEFAULT_TITLES.get(status, "Error")
        self.retryable = retryable


_DEFAULT_TITLES = {
    400: "Bad Request",
    404: "Not Found",
    409: "Conflict",
    422: "Unprocessable Entity",
    500: "Internal Server Error",
    503: "Service Unavailable",
    504: "Gateway Timeout",
}
