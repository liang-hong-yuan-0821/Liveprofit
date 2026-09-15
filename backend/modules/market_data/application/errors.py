"""market_data 领域错误（统一错误码总表）。

AssetDisabledError / MarketDataUpstreamUnavailableError / HotConceptsUpstreamUnavailableError
已随证券市场数据库统一方案删除（门控/503 语义删除，决策 13）。
"""

from __future__ import annotations

from backend.shared.errors import DomainError, NotFoundError


class MarketAssetNotFoundError(NotFoundError):
    code = "RESOURCE_NOT_FOUND"


class IntervalNotSupportedError(DomainError):
    code = "INTERVAL_NOT_SUPPORTED"


class RangeTooLargeError(DomainError):
    code = "RANGE_TOO_LARGE"
