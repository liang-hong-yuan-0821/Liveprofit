"""领域错误基类。

API Router 将领域错误映射为 RFC 7807 Problem Details（T5）；
Worker 将同一错误映射为重试或失败策略（T4）。retryable 只描述业务语义，不自动触发重试。
"""

from __future__ import annotations


class DomainError(Exception):
    """领域错误基类：稳定 code + 可读 message + retryable 语义。"""

    code: str = "DOMAIN_ERROR"
    retryable: bool = False

    def __init__(self, message: str, *, code: str | None = None, retryable: bool | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if retryable is not None:
            self.retryable = retryable


class NotFoundError(DomainError):
    code = "RESOURCE_NOT_FOUND"
