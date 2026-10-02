"""Persist an account-wide pause and sticky zero-position targets at full drawdown."""

from __future__ import annotations

from datetime import date, datetime, timezone
from dataclasses import dataclass
import base64
import binascii
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import hmac
import json
import os
from uuid import UUID, uuid4

from sqlalchemy import select

from backend.modules.investment_workspace.domain.risk_profiles import PROFILES
from backend.modules.investment_workspace.infrastructure.models import PortfolioPosition
from backend.modules.quant_strategy.infrastructure.lifecycle_models import SuggestedOrder
from backend.modules.quant_strategy.infrastructure.portfolio_risk_models import (
    PortfolioExitTarget, PortfolioRiskEvent,
)
from .planning_account import lock_portfolio


def _positive(value) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() and result > 0 else None


class PortfolioDrawdownFactsConflict(ValueError):
    """A frozen same-day pause conflicts with corrected current account facts."""


@dataclass(frozen=True)
class PortfolioExitProjection:
    pause_event_id: UUID
    position_id: UUID
    symbol: str
    target_quantity: Decimal
    current_quantity: Decimal
    status: str
    suggested_quantity: Decimal = Decimal(0)
    suggested_price: Decimal | None = None
    earliest_execution_trade_date: date | None = None


class PortfolioDrawdownActions:
    def __init__(self, session) -> None:
        self.session = session

    def latest(self, portfolio_id: UUID) -> PortfolioRiskEvent | None:
        return self.session.scalar(select(PortfolioRiskEvent).where(
            PortfolioRiskEvent.portfolio_id == portfolio_id,
        ).order_by(PortfolioRiskEvent.revision.desc()).limit(1))

    @staticmethod
    def _digest_facts(portfolio_id: UUID, valuation_date: date, profile: str,
                      nav: Decimal, peak: Decimal, limit: Decimal) -> str:
        payload = {"portfolio_id": str(portfolio_id), "facts_as_of": valuation_date.isoformat(),
                   "risk_profile": profile, "nav": format(nav.normalize(), "f"),
                   "peak": format(peak.normalize(), "f"),
                   "limit": format(limit.normalize(), "f")}
        return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    @staticmethod
    def _verify_resume_signature(portfolio_id: UUID, pause_event_id: UUID,
                                 valuation_date: date, reviewed_by: str,
                                 reason: str, facts_sha256: str,
                                 review_signature: str) -> None:
        try:
            configured = json.loads(os.environ.get("LIVEPROFIT_RISK_REVIEW_KEYS", ""))
        except (ValueError, TypeError):
            configured = None
        if (not isinstance(configured, dict) or not configured
                or any(not isinstance(name, str) or not name.strip()
                       or not isinstance(value, str) for name, value in configured.items())
                or len(set(configured.values())) != len(configured)):
            raise ValueError("risk reviewer signing keys are not configured uniquely")
        encoded_secret = configured.get(reviewed_by.strip())
        try:
            secret = base64.b64decode(encoded_secret, validate=True)
        except (TypeError, ValueError, binascii.Error):
            secret = None
        if (secret is None or len(secret) < 32
                or base64.b64encode(secret).decode("ascii") != encoded_secret):
            raise ValueError("risk reviewer signing key is not configured")
        if not isinstance(review_signature, str) or len(review_signature) != 64:
            raise ValueError("valid risk review signature required")
        payload = json.dumps({
            "portfolio_id": str(portfolio_id), "pause_event_id": str(pause_event_id),
            "valuation_date": valuation_date.isoformat(),
            "reviewed_by": reviewed_by.strip(), "reason": reason.strip(),
            "facts_sha256": facts_sha256,
        }, sort_keys=True, separators=(",", ":")).encode()
        expected = hmac.new(secret, payload, "sha256").hexdigest()
        if not hmac.compare_digest(expected, review_signature):
            raise ValueError("invalid risk review signature")

    def active_pause(self, portfolio_id: UUID) -> PortfolioRiskEvent | None:
        event = self.latest(portfolio_id)
        return event if event is not None and event.kind == "PAUSE" else None

    def resume_after_review(self, portfolio_id: UUID, *, pause_event_id: UUID,
                            valuation_date: date, reviewed_by: str,
                            reason: str, review_signature: str) -> PortfolioRiskEvent:
        """Explicit audited RESUME; no rebound or caller retry automatically unlocks BUY."""
        if (type(valuation_date) is not date or not isinstance(pause_event_id, UUID)
                or not isinstance(reviewed_by, str) or not reviewed_by.strip()
                or not isinstance(reason, str) or not reason.strip()):
            raise ValueError("complete explicit resume review required")
        portfolio = lock_portfolio(self.session, portfolio_id)
        if portfolio is None:
            raise ValueError("portfolio not found")
        latest = self.latest(portfolio_id)
        if latest is not None and latest.kind == "RESUME":
            self._verify_resume_signature(portfolio_id, pause_event_id, valuation_date,
                                          reviewed_by, reason, latest.facts_sha256,
                                          review_signature)
            prior = self.session.scalar(select(PortfolioRiskEvent).where(
                PortfolioRiskEvent.portfolio_id == portfolio_id,
                PortfolioRiskEvent.revision == latest.revision - 1,
            ))
            if (prior is not None and prior.id == pause_event_id
                    and latest.reviewed_by == reviewed_by.strip()
                    and latest.reason == reason.strip()
                    and latest.review_signature == review_signature
                    and latest.facts_as_of == valuation_date):
                return latest
            raise ValueError("resume request conflicts with current portfolio risk revision")
        if latest is None or latest.kind != "PAUSE" or latest.id != pause_event_id:
            raise ValueError("current pause event required")
        if valuation_date <= latest.facts_as_of or portfolio.risk_facts_as_of != valuation_date:
            raise ValueError("fresh post-pause risk facts required")
        profile = portfolio.risk_profile
        nav = _positive(portfolio.net_asset_value)
        peak = _positive(portfolio.peak_net_asset_value)
        limit = _positive(portfolio.max_drawdown_pct)
        if (profile not in PROFILES or nav is None or peak is None or limit is None
                or nav > peak or limit > PROFILES[profile].drawdown
                or (peak - nav) / peak >= limit / 2):
            raise ValueError("resume risk facts exceed safe drawdown budget")
        held = self.session.scalar(select(PortfolioPosition.id).where(
            PortfolioPosition.portfolio_id == portfolio_id,
            PortfolioPosition.quantity != 0,
        ).limit(1))
        if held is not None:
            raise ValueError("all paused positions must exit before resume")
        active = ("PROPOSED", "EXECUTING", "PARTIALLY_FILLED", "RECONCILIATION_REQUIRED")
        pending_buy = self.session.scalar(select(SuggestedOrder.id).where(
            SuggestedOrder.portfolio_id == portfolio_id, SuggestedOrder.side == "BUY",
            SuggestedOrder.status.in_(active),
            SuggestedOrder.quantity > SuggestedOrder.filled_quantity,
        ).limit(1))
        if pending_buy is not None:
            raise ValueError("active BUY suggestions must be resolved before resume")
        facts_sha256 = self._digest_facts(portfolio_id, valuation_date, profile, nav, peak, limit)
        self._verify_resume_signature(portfolio_id, pause_event_id, valuation_date,
                                      reviewed_by, reason, facts_sha256, review_signature)
        event = PortfolioRiskEvent(
            id=uuid4(), portfolio_id=portfolio_id, revision=latest.revision + 1,
            kind="RESUME", facts_as_of=valuation_date, risk_profile=profile,
            net_asset_value=nav, peak_net_asset_value=peak,
            drawdown_limit=limit,
            facts_sha256=facts_sha256,
            reason=reason.strip(), reviewed_by=reviewed_by.strip(),
            review_signature=review_signature,
        )
        self.session.add(event)
        self.session.flush()
        return event

    def pause_for_planning(self, portfolio_id: UUID, *, valuation_date: date) -> PortfolioRiskEvent | None:
        """Preserve an existing pause and SELL planning during fact correction.

        The strict method still reports the conflict to reconciliation callers.
        With an active pause, every BUY planner sees the frozen pause event.
        """
        try:
            return self.pause_if_full(portfolio_id, valuation_date=valuation_date)
        except PortfolioDrawdownFactsConflict:
            event = self.active_pause(portfolio_id)
            if event is None:
                raise
            self._capture_targets(portfolio_id, event)
            self._quarantine_buy_orders(portfolio_id)
            return event

    def _quarantine_buy_orders(self, portfolio_id: UUID) -> None:
        """Stop unsubmitted BUYs and require reconciliation of broker-facing BUYs."""
        orders = self.session.scalars(select(SuggestedOrder).where(
            SuggestedOrder.portfolio_id == portfolio_id,
            SuggestedOrder.side == "BUY",
            SuggestedOrder.status.in_(("PROPOSED", "EXECUTING", "PARTIALLY_FILLED")),
            SuggestedOrder.quantity > SuggestedOrder.filled_quantity,
        ).with_for_update().execution_options(populate_existing=True))
        for order in orders:
            order.status = ("SUPERSEDED" if order.status == "PROPOSED" and not order.filled_quantity
                            else "RECONCILIATION_REQUIRED")
            order.revision += 1
            order.updated_at = datetime.now(timezone.utc)

    def project_exit_targets(self, portfolio_id: UUID, *, valuation_date: date,
                             market_by_instrument: dict[tuple[str, str], dict],
                             available_by_position: dict[UUID, Decimal],
                             execution_policy_snapshot: dict | None = None) -> tuple[PortfolioExitProjection, ...]:
        """Project SELL candidates and capture any newly held positions.

        Missing availability or same-session market evidence leaves the sticky
        zero target pending; the position balance is never treated as sellable.
        Caller commits any newly captured targets with its planning transaction.
        """
        from .execution_constraints import ExecutionConstraintEvaluator, ExecutionPolicy
        from .position_planner import plan_reduction

        if type(valuation_date) is not date:
            raise ValueError("explicit valuation date required")
        if lock_portfolio(self.session, portfolio_id) is None:
            raise ValueError("portfolio not found")
        event = self.active_pause(portfolio_id)
        if event is None:
            return ()
        self._capture_targets(portfolio_id, event)
        targets = list(self.session.scalars(select(PortfolioExitTarget).where(
            PortfolioExitTarget.pause_event_id == event.id,
        ).order_by(PortfolioExitTarget.symbol, PortfolioExitTarget.position_id)))
        positions = {p.id: p for p in self.session.scalars(select(PortfolioPosition).where(
            PortfolioPosition.portfolio_id == portfolio_id,
        ).with_for_update().execution_options(populate_existing=True))}
        active = ("PROPOSED", "EXECUTING", "PARTIALLY_FILLED", "RECONCILIATION_REQUIRED")
        pending = list(self.session.scalars(select(SuggestedOrder).where(
            SuggestedOrder.portfolio_id == portfolio_id,
            SuggestedOrder.side == "SELL", SuggestedOrder.status.in_(active),
            SuggestedOrder.quantity > SuggestedOrder.filled_quantity,
        ).execution_options(populate_existing=True)))
        reserved_by_instrument: dict[tuple[str, str], Decimal] = {}
        for order in pending:
            key = (order.market, order.symbol)
            reserved_by_instrument[key] = (reserved_by_instrument.get(key, Decimal(0))
                                           + order.quantity - order.filled_quantity)
        execution = ExecutionConstraintEvaluator(ExecutionPolicy.from_snapshot(execution_policy_snapshot))
        result = []
        for target in targets:
            position = positions.get(target.position_id)
            if position is None:
                raise ValueError("captured exit position missing")
            if position.market != target.market or position.symbol != target.symbol:
                raise ValueError("captured exit instrument mismatch")
            quantity = Decimal(position.quantity)
            base = dict(pause_event_id=event.id, position_id=position.id, symbol=target.symbol,
                        target_quantity=Decimal(0), current_quantity=quantity)
            if quantity <= 0:
                result.append(PortfolioExitProjection(**base, status="CLOSED"))
                continue
            if position.market != "CN":
                result.append(PortfolioExitProjection(**base, status="UNSUPPORTED_MARKET"))
                continue
            available = available_by_position.get(position.id)
            if (not isinstance(available, Decimal) or not available.is_finite()
                    or available < 0 or available > quantity):
                result.append(PortfolioExitProjection(**base, status="SELLABLE_UNKNOWN"))
                continue
            market = market_by_instrument.get((target.market, target.symbol))
            if not isinstance(market, dict) or str(market.get("trade_date")) != valuation_date.isoformat():
                result.append(PortfolioExitProjection(**base, status="SELL_MARKET_FACTS_UNAVAILABLE"))
                continue
            reserved = reserved_by_instrument.get((target.market, target.symbol), Decimal(0))
            if reserved >= available and reserved > 0:
                result.append(PortfolioExitProjection(**base, status="SELL_ALREADY_RESERVED"))
                continue
            reduction = plan_reduction(
                target_quantity=Decimal(0), actual_quantity=quantity,
                available_quantity=available, reserved_quantity=reserved,
                market=market, execution=execution,
            )
            if reduction.code is None:
                result.append(PortfolioExitProjection(
                    **base, status="READY", suggested_quantity=reduction.quantity,
                    suggested_price=reduction.price,
                    earliest_execution_trade_date=reduction.earliest_execution_trade_date,
                ))
            else:
                result.append(PortfolioExitProjection(
                    **base, status=reduction.code,
                    earliest_execution_trade_date=reduction.earliest_execution_trade_date,
                ))
        return tuple(result)

    def _capture_targets(self, portfolio_id: UUID, event: PortfolioRiskEvent) -> None:
        positions = list(self.session.scalars(select(PortfolioPosition).where(
            PortfolioPosition.portfolio_id == portfolio_id,
            PortfolioPosition.quantity > 0,
        ).with_for_update().execution_options(populate_existing=True)))
        known = set(self.session.scalars(select(PortfolioExitTarget.position_id).where(
            PortfolioExitTarget.pause_event_id == event.id,
        )))
        for position in positions:
            if position.id in known:
                continue
            self.session.add(PortfolioExitTarget(
                id=uuid4(), pause_event_id=event.id, position_id=position.id,
                market=position.market, symbol=position.symbol,
                quantity_at_capture=position.quantity,
            ))
        self.session.flush()

    def pause_if_full(self, portfolio_id: UUID, *, valuation_date: date) -> PortfolioRiskEvent | None:
        """Caller commits event and targets with its planning transaction."""
        if type(valuation_date) is not date:
            raise ValueError("explicit valuation date required")
        portfolio = lock_portfolio(self.session, portfolio_id)
        if portfolio is None:
            raise ValueError("portfolio not found")
        profile = portfolio.risk_profile
        nav = _positive(portfolio.net_asset_value)
        peak = _positive(portfolio.peak_net_asset_value)
        limit = _positive(portfolio.max_drawdown_pct)
        latest = self.latest(portfolio_id)
        if (profile not in PROFILES or nav is None or peak is None or limit is None
                or peak < nav or limit > PROFILES[profile].drawdown
                or portfolio.risk_facts_as_of != valuation_date):
            if latest is not None and latest.kind == "PAUSE":
                if latest.facts_as_of == valuation_date:
                    raise PortfolioDrawdownFactsConflict("same-day drawdown trigger facts changed; correction review required")
                self._capture_targets(portfolio_id, latest)
                self._quarantine_buy_orders(portfolio_id)
                return latest
            return None
        digest = self._digest_facts(portfolio_id, valuation_date, profile, nav, peak, limit)
        if latest is not None and latest.kind == "PAUSE" and latest.facts_as_of == valuation_date:
            if latest.facts_sha256 != digest:
                raise PortfolioDrawdownFactsConflict("same-day drawdown trigger facts changed; correction review required")
        if (peak - nav) / peak < limit:
            if latest is not None and latest.kind == "PAUSE":
                self._capture_targets(portfolio_id, latest)
                self._quarantine_buy_orders(portfolio_id)
                return latest
            return None
        if latest is not None and latest.kind == "PAUSE":
            event = latest
        else:
            event = PortfolioRiskEvent(
                id=uuid4(), portfolio_id=portfolio_id,
                revision=(latest.revision + 1 if latest else 1), kind="PAUSE",
                facts_as_of=valuation_date, risk_profile=profile,
                net_asset_value=nav, peak_net_asset_value=peak,
                drawdown_limit=limit, facts_sha256=digest,
                reason="FULL_DRAWDOWN",
            )
            self.session.add(event)
            self.session.flush()

        self._capture_targets(portfolio_id, event)
        self._quarantine_buy_orders(portfolio_id)
        return event
