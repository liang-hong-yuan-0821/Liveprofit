"""market_data 领域错误（统一错误码总表）。"""

from __future__ import annotations

from backend.shared.errors import DomainError, NotFoundError


class MarketAssetNotFoundError(NotFoundError):
    code = "RESOURCE_NOT_FOUND"


class IntervalNotSupportedError(DomainError):
    code = "INTERVAL_NOT_SUPPORTED"


class RangeTooLargeError(DomainError):
    code = "RANGE_TOO_LARGE"


class AssetDisabledError(DomainError):
    code = "ASSET_DISABLED"


class MarketDataUpstreamUnavailableError(DomainError):
    code = "MARKET_DATA_UPSTREAM_UNAVAILABLE"
    retryable = True


class HotConceptsUpstreamUnavailableError(DomainError):
    code = "HOT_CONCEPTS_UPSTREAM_UNAVAILABLE"
    retryable = True
