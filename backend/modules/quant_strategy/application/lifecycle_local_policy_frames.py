"""Assemble provisional daily policy frames from three independent replays."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from .lifecycle_accounting_replay import DailyQuantityReplay
from .lifecycle_intent_replay import IntentCompletion
from .lifecycle_local_intent_bridge import LocalIntentCompletionDiagnosis
from .lifecycle_persisted_daily_inputs import LocalDailyFact, LocalDailyFactInputs
from .lifecycle_policy_replay import DailyPolicyFrame
from .lifecycle_version_chain import DailyVersionStep, LocalVersionChain


_CN_TZ = ZoneInfo("Asia/Shanghai")
_COMPLETION_REASONS = {"TEMPLATE_CONFIRM_ADD", "ADD_AT_R", "PROFIT_TARGET_TRIM"}


@dataclass(frozen=True)
class LocalPolicyFrames:
    status: Literal["PROVISIONAL", "UNKNOWN"]
    frames: tuple[DailyPolicyFrame, ...]
    issues: tuple[str, ...]
    version_advances: tuple[bool, ...] = ()
    ending_state_version: int | None = None
    starting_state_version: int | None = None
    first_data_as_of: datetime | None = None


def build_local_policy_frames(
    *, daily: LocalDailyFactInputs, quantities: DailyQuantityReplay,
    intents: LocalIntentCompletionDiagnosis,
    version_chain: LocalVersionChain | None = None,
) -> LocalPolicyFrames:
    """Require exact date sets and at most one completed intent per session."""
    if (not isinstance(daily, LocalDailyFactInputs)
            or not isinstance(quantities, DailyQuantityReplay)
            or not isinstance(intents, LocalIntentCompletionDiagnosis)):
        return LocalPolicyFrames("UNKNOWN", (), ("POLICY_FRAME_INPUT_INVALID",))
    issues = (*daily.issues, *quantities.issues, *intents.issues)
    if (daily.status != "LOCAL_CANDIDATE" or quantities.status != "PROVISIONAL"
            or intents.status != "PROVISIONAL"):
        return LocalPolicyFrames("UNKNOWN", (), (*issues, "POLICY_FRAME_INPUT_UNKNOWN"))
    if (not daily.days or not isinstance(daily.days, tuple)
            or not isinstance(quantities.quantities, tuple)
            or not isinstance(intents.completions, tuple)
            or any(not isinstance(day, LocalDailyFact)
                   or type(day.trade_date) is not date for day in daily.days)
            or any(not isinstance(item, tuple) or len(item) != 2
                   or type(item[0]) is not date for item in quantities.quantities)
            or any(not isinstance(item, IntentCompletion)
                   or type(item.completed_on) is not date
                   for item in intents.completions)
            or tuple(day.trade_date for day in daily.days) != tuple(
                item[0] for item in quantities.quantities)):
        return LocalPolicyFrames("UNKNOWN", (), (*issues, "POLICY_FRAME_DATES_MISMATCH"))
    dates = tuple(day.trade_date for day in daily.days)
    if dates != tuple(sorted(set(dates))):
        return LocalPolicyFrames("UNKNOWN", (), (*issues, "POLICY_FRAME_DATES_INVALID"))
    reasons_by_day: dict[date, tuple[str, str]] = {}
    for completion in intents.completions:
        at = completion.effective_at
        if (not isinstance(completion.intent_id, UUID)
                or not isinstance(completion.fill_event_id, UUID)
                or not isinstance(at, datetime) or at.tzinfo is None
                or at.utcoffset() is None
                or at.astimezone(_CN_TZ).date() != completion.completed_on
                or not isinstance(completion.reason_code, str)
                or completion.reason_code not in _COMPLETION_REASONS):
            return LocalPolicyFrames("UNKNOWN", (),
                                     (*issues, "INTENT_COMPLETION_IDENTITY_INVALID"))
        day = completion.completed_on
        if day in reasons_by_day:
            return LocalPolicyFrames("UNKNOWN", (),
                                     (*issues, "INTENT_MULTIPLE_COMPLETIONS_SAME_DAY"))
        reasons_by_day[day] = (completion.reason_code,
                               f"local-fill:{completion.fill_event_id}")
    if set(reasons_by_day) - {day.trade_date for day in daily.days}:
        return LocalPolicyFrames("UNKNOWN", (),
                                 (*issues, "INTENT_COMPLETION_OUTSIDE_CALENDAR"))
    frames: list[DailyPolicyFrame] = []
    version_advances: list[bool] = []
    previous_version_after: int | None = None
    if version_chain is not None and (
            not isinstance(version_chain, LocalVersionChain)
            or version_chain.status != "PROVISIONAL"
            or version_chain.verified_days != tuple(
                DailyVersionStep(day.trade_date, day.state_version_before,
                                 day.state_version_after) for day in daily.days)):
        return LocalPolicyFrames("UNKNOWN", (), (*issues, "POLICY_VERSION_CHAIN_UNKNOWN"))
    for day, (quantity_date, quantity) in zip(daily.days, quantities.quantities):
        if (type(quantity_date) is not date or not isinstance(quantity, Decimal)
                or not quantity.is_finite() or quantity <= 0):
            return LocalPolicyFrames("UNKNOWN", (),
                                     (*issues, f"POLICY_FRAME_QUANTITY_INVALID:{quantity_date}"))
        if (type(day.state_version_before) is not int
                or type(day.state_version_after) is not int
                or day.state_version_before < 1
                or day.state_version_after - day.state_version_before not in (0, 1)):
            return LocalPolicyFrames("UNKNOWN", (),
                                     (*issues, f"POLICY_FRAME_STATE_VERSION_INVALID:{quantity_date}"))
        if (version_chain is None and previous_version_after is not None
                and day.state_version_before != previous_version_after):
            return LocalPolicyFrames(
                "UNKNOWN", (),
                (*issues, f"POLICY_FRAME_VERSION_GAP_UNATTRIBUTED:{quantity_date}"))
        version_advances.append(day.state_version_after > day.state_version_before)
        previous_version_after = day.state_version_after
        completion = reasons_by_day.get(day.trade_date)
        frames.append(DailyPolicyFrame(
            trade_date=day.trade_date, revision_id=day.revision_id,
            fact=deepcopy(day.fact), price_basis=day.price_basis,
            actual_shares=quantity,
            completed_intent_reasons=(completion[0],) if completion else (),
            completion_source_ref=completion[1] if completion else None,
        ))
    if version_chain is not None and (
            tuple(version_advances) != version_chain.daily_advances
            or previous_version_after != version_chain.ending_version):
        return LocalPolicyFrames("UNKNOWN", (), (*issues, "POLICY_VERSION_CHAIN_MISMATCH"))
    return LocalPolicyFrames("PROVISIONAL", tuple(frames),
                             (*issues, *(version_chain.issues if version_chain else ()),
                              "POLICY_FRAME_SOURCE_UNCERTIFIED"),
                             tuple(version_advances), previous_version_after,
                             daily.days[0].state_version_before,
                             daily.days[0].data_as_of)
