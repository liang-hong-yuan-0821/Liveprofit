"""analysis 领域错误（§2.6 统一错误码总表 + 领域冲突语义）。

Router 映射为 RFC 7807 Problem Details（T5）；Worker 映射为业务重试/失败策略（T4）。
"""

from __future__ import annotations

from backend.modules.analysis.application.contracts import ClassifiedError
from backend.shared.errors import DomainError, NotFoundError


class TaskNotFoundError(NotFoundError):
    code = "TASK_NOT_FOUND"


class ReportNotFoundError(NotFoundError):
    code = "REPORT_NOT_FOUND"


class TaskCreateInvalidError(DomainError):
    code = "TASK_CREATE_INVALID"


class TaskNotTerminalError(DomainError):
    """仅终态任务可删除：进行中/排队中的任务请先取消（API 映射 409）。"""

    code = "TASK_NOT_TERMINAL"


class IdempotencyKeyReusedError(DomainError):
    code = "IDEMPOTENCY_KEY_REUSED"


class IdempotencyKeyInvalidError(DomainError):
    code = "VALIDATION_ERROR"


class InvalidStateConflictError(DomainError):
    """前置状态不满足：API 映射 409；Worker 记录重复消息并安全退出（§3.1.3）。"""

    code = "TASK_STATE_CONFLICT"


class LeaseConflictError(DomainError):
    """attempt 或 lease token 不匹配：过期 attempt 永久失去写权限（fencing）。"""

    code = "TASK_LEASE_CONFLICT"


# ---- 执行层异常分类（Worker 使用；图/Adapter 抛出的异常经 classify_error 归一化） ----


class RetryableAnalysisError(Exception):
    """可重试执行异常：数据源网络故障、LLM 限流、Redis 暂时不可用等临时错误。"""

    def __init__(self, message: str, *, code: str = "PROVIDER_UNAVAILABLE") -> None:
        super().__init__(message)
        self.code = code


class FatalAnalysisError(Exception):
    """不可重试执行异常：输入、配置、不可恢复 Provider/LLM 错误。"""

    def __init__(self, message: str, *, code: str = "ANALYSIS_INTERNAL") -> None:
        super().__init__(message)
        self.code = code


class CooperativeCancelledError(Exception):
    """协作式取消：图阶段边界检测到 CANCEL_REQUESTED，停止后续推进（不进入 fail_or_retry）。"""


class FencingLostError(Exception):
    """心跳续租失败：执行器失去 fencing 权限，不得保存报告或推进终态（直接放弃写入）。"""


def classify_error(exc: Exception) -> ClassifiedError:
    """执行异常 → ClassifiedError（业务重试由 fail_or_retry 唯一决策，Dramatiq 不做业务重试）。"""
    if isinstance(exc, RetryableAnalysisError):
        return ClassifiedError(code=exc.code, message=str(exc), retryable=True)
    if isinstance(exc, FatalAnalysisError):
        return ClassifiedError(code=exc.code, message=str(exc), retryable=False)
    return ClassifiedError(code="INTERNAL_ERROR", message=str(exc)[:512], retryable=False)
