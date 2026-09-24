"""Market refresh contracts; runtime history is temporary and rebuildable."""
from datetime import date, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Resource(str, Enum):
    """Stable public enum; internal refresh resources must not enter OpenAPI."""

    CN_INDEX_BARS = "CN_INDEX_BARS"
    CN_INDEX_FACTORS = "CN_INDEX_FACTORS"
    US_INDEX_BARS = "US_INDEX_BARS"
    KR_INDEX_BARS = "KR_INDEX_BARS"
    CN_STOCK_DAILY = "CN_STOCK_DAILY"
    CN_SECTOR_DAILY = "CN_SECTOR_DAILY"


class CoverageCounts(BaseModel):
    expected_count: int
    available_count: int
    exempt_count: int
    missing_count: int


class WindowCoverage(CoverageCounts):
    model_config = ConfigDict(populate_by_name=True)
    from_: date | None = Field(alias="from")
    to: date | None


class RefreshEligibility(BaseModel):
    allowed: bool
    reason: str
    next_retry_at: datetime | None


class RefreshJob(BaseModel):
    id: str
    resource: Resource
    target_trade_date: date
    status: Literal["QUEUED", "RUNNING", "RETRY_WAIT", "SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED"]
    attempt: int
    processed: int
    total: int
    created_at: datetime | None = None
    started_at: datetime | None
    heartbeat_at: datetime | None
    finished_at: datetime | None = None
    error_code: str | None
    error_summary: str | None
    result: dict | None = None


class RefreshGroup(CoverageCounts):
    resource: Resource
    market: Literal["CN", "US", "KR"]
    market_date: date
    calendar_status: Literal["OK", "EXPIRING", "UNAVAILABLE"]
    supported_through: date
    expected_trade_date: date | None
    next_ready_at: datetime | None
    latest_observed_date: date | None
    complete_through_date: date | None
    freshness: Literal["FRESH", "STALE", "PARTIAL", "UNAVAILABLE", "UNKNOWN"]
    window_coverage: WindowCoverage
    data_version: str
    auto_eligibility: RefreshEligibility
    manual_eligibility: RefreshEligibility
    job: RefreshJob | None
    warnings: list[str] = Field(default_factory=list)


class RefreshStatusData(BaseModel):
    server_time: datetime
    refresh_available: bool
    worker_online: bool
    concept_display_date: date | None
    groups: list[RefreshGroup]


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resources: list[Resource] = Field(min_length=1, max_length=6)
    mode: Literal["auto", "retry"] = "auto"


class RefreshDecision(BaseModel):
    resource: Resource
    decision: Literal["QUEUED", "IN_PROGRESS", "UP_TO_DATE", "BLOCKED", "COOLDOWN"]
    job_id: str | None
    reason: str
    next_retry_at: datetime | None


class RefreshDecisionsData(BaseModel):
    decisions: list[RefreshDecision]
