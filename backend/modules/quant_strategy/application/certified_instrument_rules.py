"""New-risk rule lookup at the live order decision; missing evidence stays closed."""

from __future__ import annotations

from datetime import date

from backend.modules.quant_strategy.domain.instrument_rules import InstrumentRuleUnavailable
from backend.modules.quant_strategy.infrastructure.instrument_rule_certificates import (
    InstrumentRuleCertificateRepository, LiveRuleAuthorization,
)
from .execution_constraints import ExecutionConstraintEvaluator, ExecutionPolicy, next_execution_session


def live_authorizations(session, *, symbols: set[str],
                        decision_date: date, expected_asset_type: str = "stock") -> dict[str, LiveRuleAuthorization]:
    if type(decision_date) is not date or not symbols:
        return {}
    calendar = ExecutionConstraintEvaluator(ExecutionPolicy()).calendar
    execution_date = next_execution_session(decision_date, calendar=calendar)
    if execution_date is None:
        return {}
    repository = InstrumentRuleCertificateRepository(session)
    accepted: dict[str, LiveRuleAuthorization] = {}
    for symbol in sorted(symbols):
        try:
            authorization = repository.resolve_live_now(
                symbol=symbol, decision_date=decision_date,
                execution_date=execution_date,
            )
            if authorization.rule.asset_type == expected_asset_type:
                accepted[symbol] = authorization
        except InstrumentRuleUnavailable:
            continue
    return accepted
