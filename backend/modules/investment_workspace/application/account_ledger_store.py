"""Stage unverified account replay facts in the caller's portfolio transaction."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select

from backend.modules.investment_workspace.infrastructure.account_ledger_models import (
    AccountLedgerBaselineRow, AccountLedgerMovementRow,
)
from backend.modules.investment_workspace.infrastructure.account_models import AccountObservationRow
from backend.modules.investment_workspace.infrastructure.models import Portfolio
from .account_ledger import LedgerBalance, LedgerBaseline, LedgerMovement, replay_account_balance
from .reconciliation import _key


MAX_AMOUNT = Decimal("10000000000000000")
SOURCE_TYPES = frozenset({"MANUAL_ENTRY", "BROKER_EXPORT", "BROKER_API"})
CORPORATE_ACTION_SOURCE_TYPES = SOURCE_TYPES | frozenset({"ISSUER_NOTICE"})


def _money(value: object, *, signed: bool = True) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ValueError("amount must fit numeric(20,4)")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("amount must fit numeric(20,4)") from None
    if (not amount.is_finite() or abs(amount) >= MAX_AMOUNT
            or (not signed and amount < 0)
            or amount != amount.quantize(Decimal("0.0001"))):
        raise ValueError("amount must fit numeric(20,4)")
    return amount.quantize(Decimal("0.0001"))


def _digest(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _movement_payload(
    *, portfolio_id: uuid.UUID, baseline_id: uuid.UUID, effective_at: datetime,
    kind: str, cash_delta: Decimal, supersedes_id: uuid.UUID | None,
    reason: str | None, source_type: str, source_ref: str,
    holdings_delta: list, fill_price: Decimal | None, fee: Decimal,
) -> dict:
    return {
        "portfolio_id": str(portfolio_id), "baseline_id": str(baseline_id),
        "effective_at": effective_at.astimezone(timezone.utc).isoformat(),
        "kind": kind, "cash_delta": str(cash_delta),
        "holdings_delta": holdings_delta,
        "fill_price": str(fill_price) if fill_price is not None else None,
        "fee": str(fee),
        "supersedes_id": str(supersedes_id) if supersedes_id else None,
        "reason": reason, "source_type": source_type, "source_ref": source_ref,
    }


def _baseline_from_observation(portfolio_id: uuid.UUID, observation: AccountObservationRow) -> LedgerBaseline:
    if not observation.complete_holdings:
        raise ValueError("ledger baseline requires a complete observation")
    if not isinstance(observation.holdings, list):
        raise ValueError("observation holdings are malformed")
    holdings = []
    for item in observation.holdings:
        if not isinstance(item, list) or len(item) != 4:
            raise ValueError("observation holding is malformed")
        market, symbol, quantity, _sellable = item
        holdings.append((market, symbol, _money(quantity, signed=False)))
    return LedgerBaseline(portfolio_id, observation.captured_at, observation.cash, tuple(holdings))


class AccountLedgerStore:
    def __init__(self, session) -> None:
        self.session = session

    def _lock_portfolio(self, portfolio_id: uuid.UUID) -> None:
        if not isinstance(portfolio_id, uuid.UUID):
            raise ValueError("portfolio_id must be a UUID")
        row = self.session.scalar(select(Portfolio).where(Portfolio.id == portfolio_id)
                                  .with_for_update().execution_options(populate_existing=True))
        if row is None:
            raise ValueError("portfolio does not exist")

    def _read_baseline(self, portfolio_id: uuid.UUID) -> tuple[AccountLedgerBaselineRow, AccountObservationRow]:
        baseline = self.session.scalar(select(AccountLedgerBaselineRow).where(
            AccountLedgerBaselineRow.portfolio_id == portfolio_id))
        if baseline is None:
            raise ValueError("ledger baseline is missing")
        observation = self.session.get(AccountObservationRow, baseline.observation_id)
        if (observation is None or observation.portfolio_id != portfolio_id
                or observation.payload_sha256 != baseline.observation_sha256
                or observation.captured_at != baseline.effective_at):
            raise ValueError("ledger baseline observation changed")
        return baseline, observation

    def _read_movements(self, portfolio_id: uuid.UUID, baseline_id: uuid.UUID) -> tuple[LedgerMovement, ...]:
        rows = self.session.scalars(select(AccountLedgerMovementRow).where(
            AccountLedgerMovementRow.portfolio_id == portfolio_id).order_by(
                AccountLedgerMovementRow.recorded_at, AccountLedgerMovementRow.id))
        movements = []
        for row in rows:
            if row.baseline_id != baseline_id or row.payload_sha256 != _digest(_movement_payload(
                portfolio_id=row.portfolio_id, baseline_id=row.baseline_id,
                effective_at=row.effective_at, kind=row.kind, cash_delta=row.cash_delta,
                supersedes_id=row.supersedes_id, reason=row.reason,
                source_type=row.source_type, source_ref=row.source_ref,
                holdings_delta=row.holdings_delta, fill_price=row.fill_price, fee=row.fee,
            )):
                raise ValueError("ledger movement content changed")
            movements.append(LedgerMovement(
                portfolio_id=row.portfolio_id, event_id=row.id,
                recorded_at=row.recorded_at, effective_at=row.effective_at,
                kind=row.kind, cash_delta=row.cash_delta,
                holdings_delta=tuple((market, symbol, Decimal(quantity))
                                     for market, symbol, quantity in row.holdings_delta),
                fill_price=row.fill_price, fee=row.fee, supersedes_id=row.supersedes_id,
                reason=row.reason,
            ))
        return tuple(movements)

    def replay_current(self, portfolio_id: uuid.UUID) -> LedgerBalance:
        """Read a current diagnostic balance; this is not a settlement certificate."""
        self._lock_portfolio(portfolio_id)
        baseline, observation = self._read_baseline(portfolio_id)
        now = self.session.scalar(select(func.clock_timestamp()))
        return replay_account_balance(_baseline_from_observation(portfolio_id, observation),
                                      self._read_movements(portfolio_id, baseline.id),
                                      as_of=now, allow_negative=True)

    def replay_at(self, portfolio_id: uuid.UUID, *, effective_as_of: datetime,
                  recorded_as_of: datetime) -> LedgerBalance:
        """Read a diagnostic past balance using explicit effective and local knowledge cutoffs."""
        if (not isinstance(effective_as_of, datetime) or effective_as_of.utcoffset() is None
                or not isinstance(recorded_as_of, datetime) or recorded_as_of.utcoffset() is None):
            raise ValueError("replay cutoffs must be timezone aware")
        self._lock_portfolio(portfolio_id)
        baseline, observation = self._read_baseline(portfolio_id)
        if (effective_as_of < baseline.effective_at
                or recorded_as_of < baseline.recorded_at
                or recorded_as_of < observation.received_at):
            raise ValueError("replay baseline was not available at requested cutoffs")
        return replay_account_balance(
            _baseline_from_observation(portfolio_id, observation),
            self._read_movements(portfolio_id, baseline.id),
            as_of=effective_as_of, recorded_as_of=recorded_as_of,
            allow_negative=True,
        )

    def establish_baseline(self, portfolio_id: uuid.UUID, observation_id: uuid.UUID) -> tuple[AccountLedgerBaselineRow, bool]:
        """Pin a complete, unverified observation as the sole replay baseline."""
        self._lock_portfolio(portfolio_id)
        if not isinstance(observation_id, uuid.UUID):
            raise ValueError("observation_id must be a UUID")
        observation = self.session.get(AccountObservationRow, observation_id)
        if observation is None or observation.portfolio_id != portfolio_id:
            raise ValueError("observation does not belong to portfolio")
        _baseline_from_observation(portfolio_id, observation)
        existing = self.session.scalar(select(AccountLedgerBaselineRow).where(
            AccountLedgerBaselineRow.portfolio_id == portfolio_id))
        if existing is not None:
            if existing.observation_id != observation_id or existing.observation_sha256 != observation.payload_sha256:
                raise ValueError("portfolio baseline already established from another observation")
            return existing, False
        row = AccountLedgerBaselineRow(
            id=uuid.uuid4(), portfolio_id=portfolio_id, observation_id=observation_id,
            observation_sha256=observation.payload_sha256,
            effective_at=observation.captured_at,
        )
        self.session.add(row)
        self.session.flush()
        return row, True

    def append_cash_flow(
        self, portfolio_id: uuid.UUID, *, effective_at: datetime, amount: object,
        source_type: str, source_ref: str, supersedes_id: uuid.UUID | None = None,
        reason: str | None = None,
    ) -> tuple[AccountLedgerMovementRow, bool]:
        """Append a diagnostic external cash movement; caller commits or rolls back."""
        delta = _money(amount)
        if delta == 0:
            raise ValueError("cash flow amount must be nonzero")
        return self._append_movement(
            portfolio_id, effective_at=effective_at, kind="CASH_FLOW",
            cash_delta=delta, holdings_delta=[], fill_price=None, fee=Decimal("0.0000"),
            source_type=source_type, source_ref=source_ref,
            supersedes_id=supersedes_id, reason=reason,
        )

    def append_trade(
        self, portfolio_id: uuid.UUID, *, effective_at: datetime,
        market: str, symbol: str, quantity_delta: object,
        fill_price: object, fee: object, source_type: str, source_ref: str,
        supersedes_id: uuid.UUID | None = None, reason: str | None = None,
    ) -> tuple[AccountLedgerMovementRow, bool]:
        """Record an actual trade with explicit fee for diagnostic replay only."""
        instrument = _key(market, symbol)
        if instrument is None or len(market) > 8 or len(symbol) > 32:
            raise ValueError("invalid trade instrument")
        quantity = _money(quantity_delta)
        price = _money(fill_price, signed=False)
        actual_fee = _money(fee, signed=False)
        if quantity == 0 or price <= 0:
            raise ValueError("trade requires nonzero quantity and positive price")
        cash_delta = _money(-quantity * price - actual_fee)
        return self._append_movement(
            portfolio_id, effective_at=effective_at, kind="TRADE",
            cash_delta=cash_delta,
            holdings_delta=[[market, symbol, str(quantity)]],
            fill_price=price, fee=actual_fee, source_type=source_type,
            source_ref=source_ref, supersedes_id=supersedes_id, reason=reason,
        )

    def void_trade(
        self, portfolio_id: uuid.UUID, *, trade_movement_id: uuid.UUID,
        reason: str, source_type: str, source_ref: str,
    ) -> tuple[AccountLedgerMovementRow, bool]:
        """Remove one recorded trade from diagnostic replay, retaining its history."""
        self._lock_portfolio(portfolio_id)
        if not isinstance(trade_movement_id, uuid.UUID):
            raise ValueError("trade_movement_id must be a UUID")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("void requires a reason")
        trade = self.session.get(AccountLedgerMovementRow, trade_movement_id)
        if trade is None or trade.portfolio_id != portfolio_id or trade.kind != "TRADE":
            raise ValueError("void predecessor must be a trade in this portfolio")
        return self._append_movement(
            portfolio_id, effective_at=trade.effective_at, kind="VOID",
            cash_delta=Decimal("0.0000"), holdings_delta=[], fill_price=None,
            fee=Decimal("0.0000"), source_type=source_type, source_ref=source_ref,
            supersedes_id=trade_movement_id, reason=reason,
        )

    def append_corporate_action(
        self, portfolio_id: uuid.UUID, *, effective_at: datetime,
        cash_delta: object, holdings_delta: list[tuple[str, str, object]],
        source_type: str, source_ref: str,
        supersedes_id: uuid.UUID | None = None, reason: str | None = None,
    ) -> tuple[AccountLedgerMovementRow, bool]:
        """Stage an explicit, unverified company action for diagnostic replay."""
        cash = _money(cash_delta)
        if not isinstance(holdings_delta, list):
            raise ValueError("corporate action holdings_delta must be a list")
        normalized: list[list[str]] = []
        seen: set[tuple[str, str]] = set()
        for item in holdings_delta:
            if not isinstance(item, tuple) or len(item) != 3:
                raise ValueError("corporate action holding must be a three-item tuple")
            market, symbol, raw_quantity = item
            key = _key(market, symbol)
            quantity = _money(raw_quantity)
            if (key is None or len(market) > 8 or len(symbol) > 32
                    or key in seen or quantity == 0):
                raise ValueError("invalid or duplicate corporate action holding")
            seen.add(key)
            normalized.append([market, symbol, str(quantity)])
        normalized.sort(key=lambda item: (item[0], item[1]))
        if cash == 0 and not normalized:
            raise ValueError("corporate action must change cash or holdings")
        return self._append_movement(
            portfolio_id, effective_at=effective_at, kind="CORPORATE_ACTION",
            cash_delta=cash, holdings_delta=normalized,
            fill_price=None, fee=Decimal("0.0000"),
            source_type=source_type, source_ref=source_ref,
            supersedes_id=supersedes_id, reason=reason,
        )

    def append_adjustment(
        self, portfolio_id: uuid.UUID, *, effective_at: datetime,
        cash_delta: object, holdings_delta: list[tuple[str, str, object]],
        reason: str, source_type: str, source_ref: str,
        supersedes_id: uuid.UUID | None = None,
    ) -> tuple[AccountLedgerMovementRow, bool]:
        """Record a reasoned, unverified balance adjustment for diagnostic replay."""
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("adjustment requires a reason")
        cash = _money(cash_delta)
        if not isinstance(holdings_delta, list):
            raise ValueError("adjustment holdings_delta must be a list")
        normalized: list[list[str]] = []
        seen: set[tuple[str, str]] = set()
        for item in holdings_delta:
            if not isinstance(item, tuple) or len(item) != 3:
                raise ValueError("adjustment holding must be a three-item tuple")
            market, symbol, raw_quantity = item
            key = _key(market, symbol)
            quantity = _money(raw_quantity)
            if (key is None or len(market) > 8 or len(symbol) > 32
                    or key in seen or quantity == 0):
                raise ValueError("invalid or duplicate adjustment holding")
            seen.add(key)
            normalized.append([market, symbol, str(quantity)])
        normalized.sort(key=lambda item: (item[0], item[1]))
        if cash == 0 and not normalized:
            raise ValueError("adjustment must change cash or holdings")
        return self._append_movement(
            portfolio_id, effective_at=effective_at, kind="ADJUSTMENT",
            cash_delta=cash, holdings_delta=normalized,
            fill_price=None, fee=Decimal("0.0000"),
            source_type=source_type, source_ref=source_ref,
            supersedes_id=supersedes_id, reason=reason,
        )

    def _append_movement(
        self, portfolio_id: uuid.UUID, *, effective_at: datetime,
        kind: str, cash_delta: Decimal, holdings_delta: list,
        fill_price: Decimal | None, fee: Decimal,
        source_type: str, source_ref: str,
        supersedes_id: uuid.UUID | None, reason: str | None,
    ) -> tuple[AccountLedgerMovementRow, bool]:
        self._lock_portfolio(portfolio_id)
        allowed_sources = (CORPORATE_ACTION_SOURCE_TYPES if kind == "CORPORATE_ACTION"
                           else SOURCE_TYPES)
        if (source_type not in allowed_sources or not isinstance(source_ref, str)
                or not source_ref.strip() or len(source_ref) > 256):
            raise ValueError("invalid ledger source identity")
        if not isinstance(effective_at, datetime) or effective_at.utcoffset() is None:
            raise ValueError("effective_at must be timezone aware")
        if supersedes_id is not None and not isinstance(supersedes_id, uuid.UUID):
            raise ValueError("supersedes_id must be a UUID")
        if supersedes_id is not None and (not isinstance(reason, str) or not reason.strip()):
            raise ValueError("correction requires a reason")
        if reason is not None and (not isinstance(reason, str) or len(reason) > 512):
            raise ValueError("reason is invalid")
        baseline, observation = self._read_baseline(portfolio_id)
        now = self.session.scalar(select(func.clock_timestamp()))
        if effective_at < baseline.effective_at or effective_at > now:
            raise ValueError("ledger effective time is outside the recorded window")
        payload_sha256 = _digest(_movement_payload(
            portfolio_id=portfolio_id, baseline_id=baseline.id, effective_at=effective_at,
            kind=kind, cash_delta=cash_delta, supersedes_id=supersedes_id,
            reason=reason, source_type=source_type, source_ref=source_ref,
            holdings_delta=holdings_delta, fill_price=fill_price, fee=fee,
        ))
        existing = self.session.scalar(select(AccountLedgerMovementRow).where(
            AccountLedgerMovementRow.portfolio_id == portfolio_id,
            AccountLedgerMovementRow.source_type == source_type,
            AccountLedgerMovementRow.source_ref == source_ref,
        ))
        if existing is not None:
            if existing.payload_sha256 != payload_sha256:
                raise ValueError("ledger source replay changed content")
            return existing, False
        staged_id = uuid.uuid4()
        candidates = self._read_movements(portfolio_id, baseline.id) + (LedgerMovement(
            portfolio_id=portfolio_id, event_id=staged_id,
            recorded_at=now, effective_at=effective_at, kind=kind,
            cash_delta=cash_delta,
            holdings_delta=tuple((market, symbol, Decimal(quantity))
                                 for market, symbol, quantity in holdings_delta),
            fill_price=fill_price, fee=fee, supersedes_id=supersedes_id, reason=reason,
        ),)
        replay_account_balance(_baseline_from_observation(portfolio_id, observation),
                               candidates, as_of=now, allow_negative=True)
        row = AccountLedgerMovementRow(
            id=staged_id, portfolio_id=portfolio_id, baseline_id=baseline.id,
            recorded_at=now, effective_at=effective_at, kind=kind,
            cash_delta=cash_delta, holdings_delta=holdings_delta, fill_price=fill_price, fee=fee,
            supersedes_id=supersedes_id, reason=reason,
            source_type=source_type, source_ref=source_ref, payload_sha256=payload_sha256,
        )
        self.session.add(row)
        self.session.flush()
        return row, True
