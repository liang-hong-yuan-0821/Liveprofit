"""quant_strategy 领域错误（plan 4.4.1 稳定错误码，登记于 exception_handlers._CODE_MAP）。"""

from __future__ import annotations

from AI.strategy_sandbox.validator import StrategyValidationIssue
from backend.shared.errors import DomainError


class QuantStrategyError(DomainError):
    """领域错误基类。"""

    code = "QUANT_STRATEGY_ERROR"
    http_status = 500


class StrategyNotFoundError(QuantStrategyError):
    code = "STRATEGY_NOT_FOUND"
    http_status = 404


class StrategyNameConflictError(QuantStrategyError):
    code = "STRATEGY_NAME_CONFLICT"
    http_status = 409


class StrategyVersionNotPublishedError(QuantStrategyError):
    code = "STRATEGY_VERSION_NOT_PUBLISHED"
    http_status = 409


class StrategyVersionInvalidStateError(QuantStrategyError):
    code = "STRATEGY_VERSION_INVALID_STATE"
    http_status = 409


class StrategyRevisionConflictError(QuantStrategyError):
    code = "STRATEGY_REVISION_CONFLICT"
    http_status = 409


class StrategyValidationFailedError(QuantStrategyError):
    code = "STRATEGY_VALIDATION_FAILED"
    http_status = 422

    def __init__(self, issues: list[StrategyValidationIssue]) -> None:
        self.issues = issues
        super().__init__(f"策略源码校验失败：{len(issues)} 处问题")
