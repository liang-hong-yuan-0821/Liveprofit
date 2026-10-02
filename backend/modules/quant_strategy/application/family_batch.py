"""Joint family allocation under current account/qualification locks.

This is an audited projection, not a funds reservation or an order grant.
Caller owns commit/rollback; a replay returns historical evidence only.
"""
from dataclasses import asdict
from datetime import timedelta, timezone
from decimal import Decimal
import hashlib
import json
from uuid import UUID

from sqlalchemy import func, select

from backend.modules.quant_strategy.domain.family_allocation import FamilyAdmission, OwnedExposure, allocate_families
from backend.modules.quant_strategy.domain.portfolio_targets import PortfolioTargetIntent
from backend.modules.quant_strategy.infrastructure.allocation_models import AllocationBatch, AllocationMember
from backend.modules.quant_strategy.infrastructure.admission_models import StrategyAdmissionEvent
from backend.modules.quant_strategy.infrastructure.models import QuantStrategyVersion
from .planning_account import lock_portfolio, planning_account
from .strategy_admission import PROFILES, SCOPES, StrategyAdmissionService
from .target_trial_binding import TargetTrialBindingService

ZERO = Decimal(0)
CN_TIME = timezone(timedelta(hours=8))


def _json(value):
    return json.loads(json.dumps(value, default=str, sort_keys=True))


def _amount(value):
    value = Decimal(str(value))
    if not value.is_finite() or value < 0:
        raise ValueError("finite nonnegative amount required")
    return value


def _intent_payload(intent):
    payload = asdict(intent)
    payload["legs"] = sorted(payload["legs"], key=lambda leg: leg["ts_code"])
    return _json(payload)


class FamilyBatchService:
    def __init__(self, session):
        self.session = session

    def project(self, *, portfolio_id: UUID, request_key: str, asset_scope: str,
                expected_version_ids: tuple[UUID, ...], intents: tuple[PortfolioTargetIntent, ...],
                valuation_date, closes: dict[str, Decimal], protective_targets: dict[str, Decimal] | None = None,
                allow_replay: bool = True):
        """Require the frozen full manifest, including explicit all-cash members.

        Missing members are not silently removed or assigned to another family.
        A duplicate key with different market facts or intent content is an error.
        """
        if not request_key.strip() or len(request_key) > 128 or asset_scope not in SCOPES:
            raise ValueError("invalid batch identity or asset scope")
        versions = set(expected_version_ids)
        if (not versions or len(versions) != len(expected_version_ids)
                or any(not isinstance(v, UUID) for v in versions)
                or len(intents) != len(versions)
                or {i.strategy_version_id for i in intents} != versions
                or len({i.family_id for i in intents}) != len(intents)):
            raise ValueError("complete unique frozen family manifest required")
        if any(i.evaluation_as_of != valuation_date for i in intents):
            raise ValueError("one shared valuation date required")
        prices = {symbol: _amount(value) for symbol, value in closes.items()}
        protection = {symbol: _amount(value) for symbol, value in (protective_targets or {}).items()}
        payloads = {i.strategy_version_id: _intent_payload(i) for i in intents}
        identity = _json(dict(asset_scope=asset_scope, valuation_date=valuation_date,
                             members=[payloads[v] for v in sorted(versions, key=str)],
                             closes=prices, protective_targets=protection))
        fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        account = lock_portfolio(self.session, portfolio_id)
        if account is None:
            raise ValueError("portfolio not found")
        previous = self.session.scalar(select(AllocationBatch).where(
            AllocationBatch.portfolio_id == portfolio_id, AllocationBatch.request_key == request_key))
        if previous is not None:
            if not allow_replay:
                raise ValueError("fresh allocation cannot reuse an existing projection")
            if previous.input_hash != fingerprint:
                raise ValueError("batch key reused with different inputs")
            return previous
        rows = list(self.session.scalars(select(QuantStrategyVersion).where(
            QuantStrategyVersion.id.in_(versions)).order_by(QuantStrategyVersion.id)
            .with_for_update(read=True).execution_options(populate_existing=True)))
        if len(rows) != len(versions):
            raise ValueError("frozen strategy version missing")
        by_id = {row.id: row for row in rows}
        binding_service = TargetTrialBindingService(self.session)
        for intent in intents:
            binding_service.audit_target_intent(intent=intent, asset_scope=asset_scope)
        decision_at = self.session.scalar(select(func.clock_timestamp())).astimezone(CN_TIME)
        if any(i.decision_date != decision_at.date() or i.valid_until < decision_at.date() for i in intents):
            raise ValueError("intent is not valid for the current CN decision date")
        current = planning_account(self.session, account)
        service = StrategyAdmissionService(self.session)
        members, admissions, blocks = [], [], []
        if current["portfolio_snapshot"].get("account_reconciliation_required") is True:
            blocks.append({"code": "BUY_REJECTED_ACCOUNT_RECONCILIATION"})
        for intent in sorted(intents, key=lambda i: str(i.strategy_version_id)):
            event = None
            code = "ADMISSION_SCOPE_REQUIRED"
            if account.risk_profile in PROFILES:
                decision = service.read(intent.strategy_version_id, asset_scope=asset_scope,
                                        risk_profile=account.risk_profile, decision_at=decision_at)
                event, code = decision.event, decision.code
                if event is not None and event.family_id != intent.family_id:
                    code = "ADMISSION_FAMILY_MISMATCH"
                if code == "ADVISORY":
                    admissions.append(FamilyAdmission(intent.family_id, by_id[intent.strategy_version_id].strategy_id,
                        intent.strategy_version_id, event.net_expectancy_lower_bound, event.evidence_completed_at, 0))
            if code != "ADVISORY":
                blocks.append({"family_id": intent.family_id, "code": code})
            members.append(AllocationMember(strategy_version_id=intent.strategy_version_id,
                family_id=intent.family_id, admission_event_id=event.id if event else None,
                admission_code=code, intent=payloads[intent.strategy_version_id]))
        exposure_values = {}
        for position in current["positions"]:
            symbol = position["symbol"]
            if position["market"] != "CN" or prices.get(symbol, ZERO) <= 0:
                blocks.append({"symbol": symbol, "code": "VALUATION_UNAVAILABLE"})
                continue
            exposure_values.setdefault(symbol, [ZERO, ZERO])[0] += _amount(position["quantity"]) * prices[symbol]
        for order in current["pending_orders"]:
            if order["side"] == "BUY":
                if order["market"] != "CN":
                    blocks.append({"symbol": order["symbol"], "code": "UNSUPPORTED_HOLDING_MARKET"})
                exposure_values.setdefault(order["symbol"], [ZERO, ZERO])[1] += (
                    _amount(order["remaining_quantity"]) * _amount(order["order_entry_price"]))
        exposures = []
        for symbol, (held, pending) in sorted(exposure_values.items()):
            owner_id = current["owner_versions"].get(symbol)
            owner = self.session.get(QuantStrategyVersion, UUID(owner_id)) if owner_id else None
            family = self.session.scalar(select(StrategyAdmissionEvent.family_id).where(
                StrategyAdmissionEvent.strategy_version_id == owner.id,
                StrategyAdmissionEvent.recorded_at < decision_at).order_by(StrategyAdmissionEvent.revision).limit(1)) if owner else None
            exposures.append(OwnedExposure(symbol, family if owner and family else None,
                owner.strategy_id if owner and family else None, owner.id if owner and family else None, held, pending))
        capital = _amount(account.total_assets) * _amount(account.max_total_position_pct)
        result = {"blocks": blocks, "closes": prices, "exposures": [asdict(e) for e in exposures],
                  "protective_targets": protection, "capital_budget": capital, "allocation": None}
        if not blocks:
            result["allocation"] = asdict(allocate_families(capital_budget=capital, decision_at=decision_at,
                admissions=tuple(admissions), intents=intents, exposures=tuple(exposures), protective_targets=protection))
        batch = AllocationBatch(portfolio_id=portfolio_id, request_key=request_key, input_hash=fingerprint,
            asset_scope=asset_scope, valuation_date=valuation_date, decision_at=decision_at,
            status="BLOCKED" if blocks else "PROJECTED", account_snapshot=_json(current), result=_json(result))
        self.session.add(batch)
        self.session.flush()
        for member in members:
            member.batch_id = batch.id
        self.session.add_all(members)
        self.session.flush()
        return batch
