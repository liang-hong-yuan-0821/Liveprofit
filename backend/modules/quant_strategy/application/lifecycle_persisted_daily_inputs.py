"""Read local daily revisions for offline lifecycle replay preparation.

The caller's calendar declaration and local rows are diagnostic inputs only.
Actual per-day holdings, intent completions and upstream availability are not
inferred from daily planning results.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import select

from backend.modules.quant_strategy.infrastructure.lifecycle_models import (
    PositionDailyFact, PositionDailyFactRevision,
)
from .lifecycle_replay_inventory import inventory_lifecycle_replay


@dataclass(frozen=True)
class LocalDailyFact:
    trade_date: date
    revision_id: UUID
    price_basis: str
    data_as_of: datetime
    fact: dict
    state_version_before: int | None = None
    state_version_after: int | None = None


@dataclass(frozen=True)
class LocalDailyFactInputs:
    status: Literal["LOCAL_CANDIDATE", "UNKNOWN"]
    days: tuple[LocalDailyFact, ...]
    issues: tuple[str, ...]


def load_local_daily_fact_inputs(
    session, *, portfolio_id: UUID, lifecycle_id: UUID,
    calendar_dates: tuple[date, ...], calendar_source_ref: str,
) -> LocalDailyFactInputs:
    """Require an exact declared calendar and a valid current revision per day."""
    inventory = inventory_lifecycle_replay(session, portfolio_id, lifecycle_id)
    daily_issues = tuple(issue for issue in inventory.issues if issue.startswith((
        "DAILY_", "PROCESSED_DAY_FACT_MISSING:",
    )))
    blockers = tuple(issue for issue in daily_issues
                     if issue != "DAILY_FACT_CALENDAR_COVERAGE_UNCERTIFIED")
    if (not isinstance(calendar_dates, tuple) or not calendar_dates
            or any(type(day) is not date for day in calendar_dates)
            or tuple(sorted(set(calendar_dates))) != calendar_dates
            or not isinstance(calendar_source_ref, str)
            or not calendar_source_ref.strip()):
        return LocalDailyFactInputs("UNKNOWN", (), (*daily_issues, "CALENDAR_DECLARATION_INVALID"))
    if blockers or inventory.input_proposal_ids:
        return LocalDailyFactInputs("UNKNOWN", (), daily_issues)

    facts = list(session.scalars(select(PositionDailyFact).where(
        PositionDailyFact.id.in_(inventory.daily_fact_ids),
        PositionDailyFact.lifecycle_id == lifecycle_id,
    ).order_by(PositionDailyFact.trade_date)
        .execution_options(populate_existing=True)))
    if (len(facts) != len(calendar_dates)
            or tuple(fact.trade_date for fact in facts) != calendar_dates):
        return LocalDailyFactInputs("UNKNOWN", (), (*daily_issues, "DAILY_FACT_CALENDAR_MISMATCH"))
    revisions = list(session.scalars(select(PositionDailyFactRevision).where(
        PositionDailyFactRevision.daily_fact_id.in_(inventory.daily_fact_ids),
    ).order_by(PositionDailyFactRevision.daily_fact_id,
               PositionDailyFactRevision.revision_no)))
    latest: dict[UUID, PositionDailyFactRevision] = {}
    for revision in revisions:
        latest[revision.daily_fact_id] = revision
    if len(latest) != len(facts):
        return LocalDailyFactInputs("UNKNOWN", (), (*daily_issues, "DAILY_FACT_REVISION_SET_CHANGED"))
    days = tuple(LocalDailyFact(
        trade_date=fact.trade_date, revision_id=latest[fact.id].id,
        price_basis=fact.price_basis, data_as_of=fact.data_as_of,
        fact=deepcopy(fact.input_payload),
        state_version_before=fact.state_version_before,
        state_version_after=fact.state_version_after,
    ) for fact in facts)
    return LocalDailyFactInputs(
        "LOCAL_CANDIDATE", days,
        (*daily_issues, "CALENDAR_SOURCE_UNCERTIFIED",
         *(f"DAILY_FACT_SOURCE_UNCERTIFIED:{day.revision_id}:{day.trade_date}" for day in days)),
    )
