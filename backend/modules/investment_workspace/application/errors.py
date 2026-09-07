"""investment_workspace 领域错误（§2.6.3 并发与错误语义）。"""

from __future__ import annotations

from backend.shared.errors import DomainError, NotFoundError


class WatchlistNotFoundError(NotFoundError):
    code = "RESOURCE_NOT_FOUND"


class WatchlistNameConflictError(DomainError):
    code = "WATCHLIST_NAME_CONFLICT"


class WatchlistItemDuplicateError(DomainError):
    code = "WATCHLIST_ITEM_DUPLICATE"


class WatchlistNotEmptyError(DomainError):
    code = "WATCHLIST_NOT_EMPTY"


class WatchlistItemOrderConflictError(DomainError):
    code = "WATCHLIST_ITEM_ORDER_CONFLICT"
    retryable = True


class RevisionConflictError(DomainError):
    code = "REVISION_CONFLICT"
    retryable = True


class PortfolioNotFoundError(NotFoundError):
    code = "RESOURCE_NOT_FOUND"


class PortfolioNameConflictError(DomainError):
    code = "PORTFOLIO_NAME_CONFLICT"


class PortfolioNotEmptyError(DomainError):
    code = "PORTFOLIO_NOT_EMPTY"


class PortfolioPositionConflictError(DomainError):
    code = "PORTFOLIO_POSITION_CONFLICT"
    retryable = True


class InvalidPositionError(DomainError):
    code = "INVALID_POSITION"


class InstrumentInvalidError(DomainError):
    """自选标的非法 market/symbol（持仓场景映射 INVALID_POSITION，由 PortfolioService 处理）。"""

    code = "VALIDATION_ERROR"
