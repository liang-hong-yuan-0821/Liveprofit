"""event_study 领域错误（统一错误码总表）。"""

from __future__ import annotations

from backend.shared.errors import DomainError


class EventStudyBusyError(DomainError):
    code = "EVENT_STUDY_BUSY"
    retryable = True


class EventStudyTimeoutError(DomainError):
    code = "EVENT_STUDY_TIMEOUT"
    retryable = True


class EventStudyInternalError(DomainError):
    code = "EVENT_STUDY_INTERNAL"
    retryable = False
