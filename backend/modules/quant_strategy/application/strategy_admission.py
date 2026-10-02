"""Scoped qualification history reader and conservative state writer.

Positive qualification is reserved for the verified research/shadow producer.
This service deliberately exposes no manual grant operation.
"""
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select, func

from backend.modules.quant_strategy.infrastructure.admission_models import StrategyAdmissionEvent
from backend.modules.quant_strategy.infrastructure.models import QuantStrategyVersion

SCOPES = {"CN_STOCK", "CN_ETF"}
PROFILES = {"CONSERVATIVE", "BALANCED", "AGGRESSIVE"}


def _scope(asset_scope, risk_profile):
    if asset_scope not in SCOPES or risk_profile not in PROFILES:
        raise ValueError("explicit supported asset scope and risk profile required")


@dataclass(frozen=True)
class AdmissionDecision:
    allowed: bool
    code: str
    event: StrategyAdmissionEvent | None


class StrategyAdmissionService:
    def __init__(self, session):
        self.session = session

    def gate_new_risk(self, version_id, *, asset_scope, risk_profile) -> dict:
        """Live gate, called under the account lock before order materialization.

        The version lock serializes this check with restriction writers. The
        caller retains both locks until orders commit; no historical timestamp
        supplied by a task may bypass a current suspension.
        """
        if version_id is None or asset_scope not in SCOPES or risk_profile not in PROFILES:
            return {"allowed": False, "code": "ADMISSION_SCOPE_REQUIRED"}
        version = self.session.scalar(select(QuantStrategyVersion).where(
            QuantStrategyVersion.id == version_id).with_for_update(read=True).execution_options(populate_existing=True))
        if version is None:
            return {"allowed": False, "code": "ADMISSION_VERSION_MISSING"}
        decision_at = self.session.scalar(select(func.clock_timestamp()))
        result = self.read(version_id, asset_scope=asset_scope, risk_profile=risk_profile,
                           decision_at=decision_at)
        return {"allowed": result.allowed, "code": result.code,
                "strategy_version_id": str(version_id), "asset_scope": asset_scope,
                "risk_profile": risk_profile, "decision_at": decision_at.isoformat(),
                "event_id": str(result.event.id) if result.event else None,
                "revision": result.event.revision if result.event else None}

    def read(self, version_id: UUID, *, asset_scope: str, risk_profile: str,
             decision_at: datetime) -> AdmissionDecision:
        _scope(asset_scope, risk_profile)
        if not isinstance(decision_at, datetime) or decision_at.utcoffset() is None:
            raise ValueError("decision_at must be timezone-aware")
        event = self.session.scalar(select(StrategyAdmissionEvent).where(
            StrategyAdmissionEvent.strategy_version_id == version_id,
            StrategyAdmissionEvent.asset_scope == asset_scope,
            StrategyAdmissionEvent.risk_profile == risk_profile,
            StrategyAdmissionEvent.recorded_at < decision_at,
        ).order_by(StrategyAdmissionEvent.revision.desc()).limit(1))
        if event is None:
            return AdmissionDecision(False, "ADMISSION_MISSING", None)
        if event.state != "ADVISORY":
            return AdmissionDecision(False, "ADMISSION_" + event.state, event)
        if event.valid_until is None or event.valid_until <= decision_at:
            return AdmissionDecision(False, "ADMISSION_EXPIRED", event)
        if event.evidence_completed_at is None or event.evidence_completed_at >= decision_at:
            return AdmissionDecision(False, "ADMISSION_EVIDENCE_UNAVAILABLE", event)
        return AdmissionDecision(True, "ADVISORY", event)

    def record_restriction(self, version_id: UUID, *, family_id: str, asset_scope: str,
                           risk_profile: str, state: str, reason: str,
                           request_key: str, expected_revision: int) -> StrategyAdmissionEvent:
        """Append EXPERIMENTAL/SUSPENDED/RETIRED; caller owns commit/rollback."""
        _scope(asset_scope, risk_profile)
        if state not in {"EXPERIMENTAL", "SUSPENDED", "RETIRED"}:
            raise ValueError("positive qualification requires the verified evidence producer")
        if (not family_id.strip() or len(family_id) > 64 or not reason.strip()
                or not request_key.strip() or len(request_key) > 128
                or type(expected_revision) is not int or expected_revision < 0):
            raise ValueError("invalid admission identity, reason or revision")
        version = self.session.scalar(select(QuantStrategyVersion).where(
            QuantStrategyVersion.id == version_id).with_for_update().execution_options(populate_existing=True))
        if version is None:
            raise ValueError("strategy version not found")
        existing_family = self.session.scalar(select(StrategyAdmissionEvent.family_id).where(
            StrategyAdmissionEvent.strategy_version_id == version_id).limit(1))
        if existing_family is not None and existing_family != family_id:
            raise ValueError("frozen family cannot be changed across scopes")
        query = select(StrategyAdmissionEvent).where(
            StrategyAdmissionEvent.strategy_version_id == version_id,
            StrategyAdmissionEvent.asset_scope == asset_scope,
            StrategyAdmissionEvent.risk_profile == risk_profile,
        )
        replay = self.session.scalar(query.where(StrategyAdmissionEvent.request_key == request_key))
        if replay is not None:
            if (replay.family_id, replay.state, replay.reason) != (family_id, state, reason):
                raise ValueError("admission request key reused with different content")
            return replay
        latest = self.session.scalar(query.order_by(StrategyAdmissionEvent.revision.desc()).limit(1))
        if (latest.revision if latest else 0) != expected_revision:
            raise ValueError("admission revision conflict")
        if latest is not None:
            if latest.family_id != family_id:
                raise ValueError("frozen family cannot be changed")
            if latest.state == "RETIRED" or state == "EXPERIMENTAL":
                raise ValueError("cannot reopen retired history or reset to experimental")
        event = StrategyAdmissionEvent(
            strategy_version_id=version_id, family_id=family_id, asset_scope=asset_scope,
            risk_profile=risk_profile, revision=expected_revision + 1,
            state=state, reason=reason, request_key=request_key,
        )
        self.session.add(event)
        self.session.flush()
        return event
