"""Record a sourced input correction request without changing a live lifecycle."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import select

from backend.modules.quant_strategy.infrastructure.lifecycle_models import (
    PositionDailyFact, PositionDailyFactInputProposal,
    PositionDailyFactRevision, PositionLifecycleState,
)
from .planning_account import lock_portfolio


def propose_daily_fact_input(
    session, *, portfolio_id: UUID, lifecycle_id: UUID, trade_date: date,
    request_key: str, input_payload: dict, price_basis: str,
    data_as_of: datetime, reason_code: str, source_ref: str,
    source_sha256: str,
) -> tuple[PositionDailyFactInputProposal, bool]:
    """Append a diagnostic correction proposal; caller owns commit/rollback.

    Source fields are declarations. This method neither authenticates the
    upstream record nor changes the current daily fact or any projection.
    """
    if session.new or session.dirty or session.deleted:
        raise ValueError("input proposal requires a clean session")
    if (not isinstance(portfolio_id, UUID) or not isinstance(lifecycle_id, UUID)
            or not isinstance(trade_date, date) or isinstance(trade_date, datetime)
            or not isinstance(input_payload, dict)
            or not isinstance(data_as_of, datetime) or data_as_of.tzinfo is None
            or data_as_of.utcoffset() is None):
        raise ValueError("input proposal identity, payload or timestamp is invalid")
    if (not isinstance(request_key, str) or not request_key.strip() or len(request_key) > 128
            or not isinstance(price_basis, str) or not price_basis.strip() or len(price_basis) > 16
            or not isinstance(reason_code, str) or not reason_code.strip() or len(reason_code) > 64
            or not isinstance(source_ref, str) or not source_ref.strip()
            or len(source_ref) > 2048
            or not isinstance(source_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", source_sha256) is None):
        raise ValueError("input proposal reason or source declaration is invalid")
    try:
        canonical = json.dumps(input_payload, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("input proposal payload must be finite JSON") from exc
    normalized = json.loads(canonical)
    canonical_bytes = canonical.encode("utf-8")
    if len(canonical_bytes) > 1048576:
        raise ValueError("input proposal payload exceeds 1 MiB")
    digest = hashlib.sha256(canonical_bytes).hexdigest()

    if lock_portfolio(session, portfolio_id) is None:
        raise ValueError("portfolio does not exist")
    lifecycle = session.scalar(select(PositionLifecycleState).where(
        PositionLifecycleState.id == lifecycle_id,
        PositionLifecycleState.portfolio_id == portfolio_id,
    ).with_for_update().execution_options(populate_existing=True))
    if lifecycle is None:
        raise ValueError("lifecycle does not belong to portfolio")
    daily = session.scalar(select(PositionDailyFact).where(
        PositionDailyFact.lifecycle_id == lifecycle_id,
        PositionDailyFact.trade_date == trade_date,
    ).with_for_update().execution_options(populate_existing=True))
    if daily is None:
        raise ValueError("daily fact does not exist")
    existing = session.scalar(select(PositionDailyFactInputProposal).where(
        PositionDailyFactInputProposal.daily_fact_id == daily.id,
        PositionDailyFactInputProposal.request_key == request_key,
    ))
    if existing is not None:
        if (existing.canonical_input_bytes != json.dumps(
                existing.proposed_input_payload, sort_keys=True, separators=(",", ":"),
                ensure_ascii=False, allow_nan=False).encode("utf-8")
                or hashlib.sha256(existing.canonical_input_bytes).hexdigest()
                != existing.proposed_input_hash):
            raise ValueError("stored input proposal payload digest is invalid")
        if (existing.proposed_input_hash != digest
                or existing.proposed_input_payload != normalized
                or existing.proposed_price_basis != price_basis
                or existing.proposed_data_as_of != data_as_of
                or existing.reason_code != reason_code
                or existing.source_ref != source_ref
                or existing.source_sha256 != source_sha256):
            raise ValueError("input proposal request key changed content")
        return existing, False
    current_revision = session.scalar(select(PositionDailyFactRevision).where(
        PositionDailyFactRevision.daily_fact_id == daily.id,
    ).order_by(PositionDailyFactRevision.revision_no.desc()).limit(1))
    if current_revision is None or any(
            getattr(current_revision, field) != getattr(daily, field)
            for field in ("input_payload", "input_hash", "price_basis", "data_as_of",
                          "planning_result", "state_version_after", "final_target_shares")):
        raise ValueError("daily fact current revision is missing or mismatched")
    try:
        current_canonical = json.dumps(daily.input_payload, sort_keys=True,
                                       separators=(",", ":"), ensure_ascii=False,
                                       allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("daily fact current input is invalid") from exc
    if hashlib.sha256(current_canonical.encode("utf-8")).hexdigest() != daily.input_hash:
        raise ValueError("daily fact current input hash is invalid")
    if (normalized == daily.input_payload and price_basis == daily.price_basis
            and data_as_of == daily.data_as_of):
        raise ValueError("input proposal has no change")
    competing = session.scalar(select(PositionDailyFactInputProposal.id).where(
        PositionDailyFactInputProposal.daily_fact_id == daily.id,
        PositionDailyFactInputProposal.base_revision_id == current_revision.id,
    ))
    if competing is not None:
        raise ValueError("current daily fact revision already has an input proposal")
    proposal = PositionDailyFactInputProposal(
        id=uuid.uuid4(), daily_fact_id=daily.id, base_revision_id=current_revision.id,
        request_key=request_key, proposed_price_basis=price_basis,
        proposed_data_as_of=data_as_of, proposed_input_payload=normalized,
        canonical_input_bytes=canonical_bytes,
        proposed_input_hash=digest, reason_code=reason_code,
        source_ref=source_ref, source_sha256=source_sha256,
    )
    session.add(proposal)
    session.flush()
    return proposal, True
