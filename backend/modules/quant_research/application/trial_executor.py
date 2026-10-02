"""Executable preregistered candidates, without trading or research admission.

The caller owns frozen as-of inputs, exchange calendar flags and ETF monthly
membership. Results retain the entire trial identity, including exit parameters.
"""
from dataclasses import asdict, dataclass, replace
from datetime import date
from decimal import Decimal
import hashlib
import json
from typing import Literal
from uuid import UUID

from AI.strategy_sandbox.protocol import StrategyRunResult
from AI.strategy_sandbox.runner import run_strategy
from AI.strategy_sandbox.validator import validate_strategy_source
from backend.modules.quant_research.domain.trial_registry import TrialSpec, preregistered_trials
from backend.modules.quant_strategy.application.management_runtime import adapt_management_output
from backend.modules.quant_strategy.domain.family_management import TREND_3ATR, MACD_MEAN_REVERSION
from backend.modules.quant_strategy.domain.management_policies import ManagementPolicy
from backend.modules.quant_strategy.domain.templates import (
    TEMPLATES, freeze_template_contract, required_host_scalars, validate_frozen_template_context,
)
from backend.modules.quant_strategy.domain.portfolio_targets import PortfolioTargetIntent
from backend.modules.quant_strategy.domain.stock_inputs import StockCandidate
from backend.modules.quant_strategy.domain.stock_medium_momentum import build_medium_momentum_target
from backend.modules.quant_strategy.domain.stock_short_reversion import build_short_reversion_target
from backend.modules.quant_strategy.domain.etf_dual_momentum import FundCandidate, build_dual_momentum_target
from backend.modules.quant_strategy.domain.etf_defensive_allocation import build_defensive_target
from backend.modules.quant_strategy.domain.portfolio_management import (
    PortfolioHoldingPolicy, PortfolioHoldingState, PortfolioHoldingDecision,
    initialize_holding, evaluate_holding,
)


@dataclass(frozen=True)
class PreparedTrial:
    spec: TrialSpec
    source_code: str | None
    template_contract_json: str | None
    management_config_json: str
    definition_hash: str


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def prepare_trial(trial_id: str) -> PreparedTrial:
    spec = next((item for item in preregistered_trials() if item.trial_id == trial_id), None)
    if spec is None:
        raise ValueError("trial must be preregistered")
    source = contract = None
    params = dict(spec.parameters)
    if spec.family in TEMPLATES:
        template = TEMPLATES[spec.family]
        source, params = template.render(params)
        if validate_strategy_source(source):
            raise ValueError("registered template failed sandbox source validation")
        contract = _json(freeze_template_contract(template))
        if spec.management_policy == "CORRECTED_LEGACY":
            management = {"template_id": spec.family, "reward_multiple": params["reward_multiple"],
                          "initial_exposure_pct": "0.50", "confirmation_window_trading_days": 3}
        else:
            policy = MACD_MEAN_REVERSION if spec.management_policy == "MACD_MEAN_REVERSION" else TREND_3ATR
            management = {"management_policy": policy.to_config()}
    else:
        # Preserve H, G, L and other parameters for the future lifecycle consumer;
        # a shared policy ID alone cannot distinguish candidate exit semantics.
        management = {"policy_id": spec.management_policy, "parameters": params}
    management_json = _json(management)
    digest = hashlib.sha256(_json({"spec": asdict(spec), "source": source,
                                  "contract": contract, "management": management_json}).encode()).hexdigest()
    return PreparedTrial(spec, source, contract, management_json, digest)


def _validate(prepared: PreparedTrial) -> None:
    if not isinstance(prepared, PreparedTrial) or prepared != prepare_trial(prepared.spec.trial_id):
        raise ValueError("prepared trial does not match the registered definition")


def holding_policy_for_trial(prepared: PreparedTrial) -> PortfolioHoldingPolicy:
    _validate(prepared)
    family, params = prepared.spec.family, dict(prepared.spec.parameters)
    if family == "stock_medium_momentum":
        return PortfolioHoldingPolicy(family, trend_ma=params["benchmark_ma"])
    if family == "stock_short_reversion":
        return PortfolioHoldingPolicy(family, exit_days=params["exit_days"], trend_ma=120)
    if family == "etf_dual_momentum":
        return PortfolioHoldingPolicy(family, momentum_lookback=params["lookback"])
    if family == "etf_defensive_allocation":
        return PortfolioHoldingPolicy(family, trend_ma=params["trend_ma"])
    raise ValueError("legacy templates use their existing frozen lifecycle evaluator")


def initialize_trial_holding(prepared: PreparedTrial, *, price_basis: str, fill_date: date,
                             fill_price: Decimal, target_weight: Decimal,
                             sigma20: Decimal | None = None, atr: Decimal | None = None) -> PortfolioHoldingState:
    """Consume actual fill and host-frozen pre-entry risk statistics."""
    return initialize_holding(holding_policy_for_trial(prepared), trial_id=prepared.spec.trial_id,
                              definition_hash=prepared.definition_hash, price_basis=price_basis,
                              fill_date=fill_date, fill_price=fill_price, target_weight=target_weight,
                              sigma20=sigma20, atr=atr)


def evaluate_trial_holding(prepared: PreparedTrial, state: PortfolioHoldingState, **facts) -> PortfolioHoldingDecision:
    policy = holding_policy_for_trial(prepared)
    if state.trial_id != prepared.spec.trial_id or state.definition_hash != prepared.definition_hash:
        raise ValueError("holding belongs to a different frozen candidate")
    return evaluate_holding(policy, state, **facts)


@dataclass(frozen=True)
class SingleTrialResult:
    trial_id: str
    definition_hash: str
    signal: StrategyRunResult


def execute_single_trial(prepared: PreparedTrial, context: dict, market: dict, *,
                         has_position: bool = False, timeout: float = 0.3) -> SingleTrialResult:
    """Evaluate new-entry candidates only; holdings require frozen lifecycle replay."""
    _validate(prepared)
    if prepared.source_code is None:
        raise ValueError("portfolio trial requires the trusted portfolio entry point")
    shares = context.get("position", {}).get("shares")
    if type(has_position) is not bool or has_position or isinstance(shares, bool) or shares != 0:
        raise ValueError("entry candidate execution requires an explicit zero position; use lifecycle replay for holdings")
    params = dict(prepared.spec.parameters)
    error = validate_frozen_template_context(json.loads(prepared.template_contract_json), context)
    if error is None:
        for name in required_host_scalars(prepared.spec.family, params):
            if context.get("meta", {}).get(name) is None:
                error = f"missing host scalar: {name}"
                break
    config = json.loads(prepared.management_config_json)
    management = ManagementPolicy.from_config(config["management_policy"]) if "management_policy" in config else None
    if error:
        result = StrategyRunResult(ok=False, error_code="INDICATOR_UNAVAILABLE", error_message=str(error))
    else:
        result = run_strategy(prepared.source_code, context, has_position=has_position,
                              timeout=timeout, allow_null_take_profit=management is not None)
        if result.ok and management is not None:
            result = replace(result, output=adapt_management_output(result.output, management, context, market))
    return SingleTrialResult(prepared.spec.trial_id, prepared.definition_hash, result)


@dataclass(frozen=True)
class PortfolioTrialInput:
    decision_date: date
    valid_until: date
    evaluation_as_of: date
    strategy_version_id: UUID
    is_week_end: bool = False
    is_month_end: bool = False
    stocks: tuple[StockCandidate, ...] = ()
    funds: tuple[FundCandidate, ...] = ()
    benchmark_bars: tuple[tuple[date, Decimal], ...] = ()
    monthly_representatives: frozenset[str] | None = None
    defensive_symbols: tuple[tuple[str, str], ...] | None = None
    previous_weights: tuple[tuple[str, Decimal], ...] | None = None

    def __post_init__(self):
        if (not all(type(day) is date for day in (self.decision_date, self.valid_until, self.evaluation_as_of))
                or not self.evaluation_as_of <= self.decision_date <= self.valid_until
                or not isinstance(self.strategy_version_id, UUID)):
            raise ValueError("invalid trial dates or frozen strategy version")
        if type(self.is_week_end) is not bool or type(self.is_month_end) is not bool:
            raise TypeError("calendar flags must be trusted booleans")
        if (not isinstance(self.stocks, tuple) or not all(isinstance(item, StockCandidate) for item in self.stocks)
                or not isinstance(self.funds, tuple) or not all(isinstance(item, FundCandidate) for item in self.funds)
                or not isinstance(self.benchmark_bars, tuple)):
            raise TypeError("candidate and benchmark inputs must be frozen tuples")
        for candidates in (self.stocks, self.funds):
            if len({item.ts_code for item in candidates}) != len(candidates):
                raise ValueError("duplicate candidate code")
        if self.monthly_representatives is not None and (
            not isinstance(self.monthly_representatives, frozenset)
            or not all(isinstance(code, str) for code in self.monthly_representatives)
        ):
            raise TypeError("monthly membership must be a frozen code set")
        for pairs in (self.defensive_symbols, self.previous_weights):
            if pairs is not None and (not isinstance(pairs, tuple)
                                       or any(not isinstance(pair, tuple) or len(pair) != 2 for pair in pairs)):
                raise TypeError("frozen assignments must be tuples of pairs")
        if self.previous_weights is not None and len(dict(self.previous_weights)) != len(self.previous_weights):
            raise ValueError("duplicate prior target weight")


@dataclass(frozen=True)
class PortfolioTrialResult:
    trial_id: str
    definition_hash: str
    intent: PortfolioTargetIntent | None
    disposition: Literal["TARGET", "NOT_SCHEDULED", "NO_TARGET_UNCLASSIFIED"]

    def __post_init__(self):
        if (self.disposition == "TARGET") != (self.intent is not None):
            raise ValueError("portfolio trial disposition and intent disagree")
        if self.disposition not in ("TARGET", "NOT_SCHEDULED", "NO_TARGET_UNCLASSIFIED"):
            raise ValueError("invalid portfolio trial disposition")
        if self.intent is not None and (
            self.intent.trial_id != self.trial_id
            or self.intent.definition_hash != self.definition_hash
        ):
            raise ValueError("portfolio target trial identity mismatch")


def execute_portfolio_trial(prepared: PreparedTrial, inputs: PortfolioTrialInput) -> PortfolioTrialResult:
    _validate(prepared)
    if prepared.source_code is not None:
        raise ValueError("single-symbol trial requires the sandbox entry point")
    if (any(day > inputs.evaluation_as_of for day, _ in inputs.benchmark_bars)
            or any(day > inputs.evaluation_as_of
                   for candidate in (*inputs.stocks, *inputs.funds)
                   for day, _ in candidate.adjusted_bars)
            or any(candidate.trade_date > inputs.evaluation_as_of for candidate in inputs.funds)):
        raise ValueError("portfolio trial input contains future market facts")
    common = dict(decision_date=inputs.decision_date, valid_until=inputs.valid_until,
                  evaluation_as_of=inputs.evaluation_as_of, strategy_version_id=inputs.strategy_version_id)
    params = dict(prepared.spec.parameters)
    family = prepared.spec.family
    if family == "stock_medium_momentum":
        scheduled = inputs.is_month_end
        intent = build_medium_momentum_target(inputs.stocks, inputs.benchmark_bars, **common, **params,
                                              is_month_end_rebalance=inputs.is_month_end)
    elif family == "stock_short_reversion":
        scheduled = True
        params["z_threshold"] = Decimal(params["z_threshold"])
        intent = build_short_reversion_target(inputs.stocks, inputs.benchmark_bars, **common, **params)
    elif family == "etf_dual_momentum":
        scheduled = inputs.is_week_end if params.pop("rebalance") == "WEEKLY" else inputs.is_month_end
        intent = None if not scheduled or inputs.monthly_representatives is None else build_dual_momentum_target(
            inputs.funds, monthly_representative_codes=inputs.monthly_representatives, **common, **params)
    elif family == "etf_defensive_allocation":
        scheduled = True
        params["vol_target"] = Decimal(params["vol_target"])
        intent = None if inputs.defensive_symbols is None else build_defensive_target(
            inputs.funds, frozen_symbols=inputs.defensive_symbols, **common, **params,
            is_weekly_rebalance=inputs.is_week_end,
            previous_weights=dict(inputs.previous_weights) if inputs.previous_weights is not None else None)
    else:
        raise ValueError("unsupported candidate family")
    if intent is not None:
        intent = replace(intent, trial_id=prepared.spec.trial_id,
                         definition_hash=prepared.definition_hash)
    disposition = ("TARGET" if intent is not None else
                   "NOT_SCHEDULED" if not scheduled else "NO_TARGET_UNCLASSIFIED")
    return PortfolioTrialResult(prepared.spec.trial_id, prepared.definition_hash, intent, disposition)
