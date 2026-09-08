"""事件研究审核领域错误（登记进 backend/api/exception_handlers.py::_CODE_MAP）。"""

from __future__ import annotations

from backend.shared.errors import DomainError


class ReviewDraftNotFoundError(DomainError):
    code = "REVIEW_DRAFT_NOT_FOUND"
    retryable = False


class ReviewEventNotFoundError(DomainError):
    code = "REVIEW_EVENT_NOT_FOUND"
    retryable = False


class ReviewUpstreamUnavailableError(DomainError):
    code = "REVIEW_UPSTREAM_UNAVAILABLE"
    retryable = True


class ReviewComputeFailedError(DomainError):
    code = "REVIEW_COMPUTE_FAILED"
    retryable = True
