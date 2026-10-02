"""Append unverified account observations without changing account projections."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select

from backend.modules.investment_workspace.infrastructure.account_models import AccountObservationRow
from backend.modules.investment_workspace.infrastructure.models import Portfolio
from .reconciliation import AccountObservation, ObservedHolding, _amount, _key


SOURCE_TYPES = frozenset({"MANUAL_IMPORT", "BROKER_EXPORT", "BROKER_API"})
MAX_AMOUNT = Decimal("10000000000000000")


def _stored_amount(value: object, label: str) -> Decimal:
    amount = _amount(value)
    if amount is None or amount >= MAX_AMOUNT or amount != amount.quantize(Decimal("0.0001")):
        raise ValueError(f"{label} must be a nonnegative numeric(20,4) value")
    return amount.quantize(Decimal("0.0001"))


def _normalized_payload(observation: AccountObservation) -> dict:
    if type(observation.trade_date) is not date:
        raise ValueError("trade_date must be a date")
    if (not isinstance(observation.captured_at, datetime)
            or observation.captured_at.utcoffset() is None
            or observation.captured_at.astimezone(ZoneInfo("Asia/Shanghai")).date()
            < observation.trade_date):
        raise ValueError("captured_at must be aware and no earlier than trade_date in China")
    if type(observation.complete_holdings) is not bool:
        raise ValueError("complete_holdings must be explicit")
    cash = _stored_amount(observation.cash, "cash")
    holdings: list[tuple] = []
    seen: set[tuple[str, str]] = set()
    try:
        observed_rows = iter(observation.holdings)
    except TypeError:
        raise ValueError("holdings must be iterable") from None
    for holding in observed_rows:
        if not isinstance(holding, ObservedHolding):
            raise ValueError("holding must be an ObservedHolding")
        key = _key(holding.market, holding.symbol)
        if key is None or len(key[0]) > 8 or len(key[1]) > 32 or key in seen:
            raise ValueError("holding identity is invalid or duplicated")
        seen.add(key)
        quantity = _stored_amount(holding.quantity, "quantity")
        sellable = (None if holding.sellable_quantity is None else
                    _stored_amount(holding.sellable_quantity, "sellable_quantity"))
        if sellable is not None and sellable > quantity:
            raise ValueError("sellable_quantity exceeds quantity")
        holdings.append((key[0], key[1], quantity, sellable))
    holdings.sort(key=lambda row: (row[0], row[1]))
    payload = {
        "portfolio_id": str(observation.portfolio_id),
        "trade_date": observation.trade_date.isoformat(),
        "captured_at": observation.captured_at.astimezone(timezone.utc).isoformat(),
        "cash": str(cash),
        "complete_holdings": observation.complete_holdings,
        "holdings": [[market, symbol, str(qty), str(sellable) if sellable is not None else None]
                     for market, symbol, qty, sellable in holdings],
    }
    return payload


class AccountObservationService:
    def __init__(self, session) -> None:
        self.session = session

    def record(self, observation: AccountObservation, *, source_type: str) -> tuple[AccountObservationRow, bool]:
        """Stage an immutable observation; the caller owns commit/rollback.

        Source labels and a matching projection never grant broker certification.
        """
        if not isinstance(observation.portfolio_id, uuid.UUID):
            raise ValueError("portfolio_id must be a UUID")
        if source_type not in SOURCE_TYPES:
            raise ValueError("unsupported source_type")
        if (not isinstance(observation.source_ref, str)
                or not observation.source_ref.strip()
                or len(observation.source_ref) > 256):
            raise ValueError("source_ref must be nonempty and at most 256 characters")
        payload = _normalized_payload(observation)
        payload_sha256 = hashlib.sha256(json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")).hexdigest()

        portfolio = self.session.scalar(select(Portfolio).where(
            Portfolio.id == observation.portfolio_id
        ).with_for_update().execution_options(populate_existing=True))
        if portfolio is None:
            raise ValueError("portfolio does not exist")
        existing = self.session.scalar(select(AccountObservationRow).where(
            AccountObservationRow.portfolio_id == observation.portfolio_id,
            AccountObservationRow.source_type == source_type,
            AccountObservationRow.source_ref == observation.source_ref,
        ))
        if existing is not None:
            if existing.payload_sha256 != payload_sha256:
                raise ValueError("source_ref replay changed account observation content")
            return existing, False

        row = AccountObservationRow(
            id=uuid.uuid4(), portfolio_id=observation.portfolio_id,
            trade_date=observation.trade_date, captured_at=observation.captured_at,
            source_type=source_type, source_ref=observation.source_ref,
            payload_sha256=payload_sha256, cash=Decimal(payload["cash"]),
            complete_holdings=observation.complete_holdings, holdings=payload["holdings"],
        )
        self.session.add(row)
        self.session.flush()
        return row, True
