"""Summarize frozen stock-trial inputs without producing an executable target."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Mapping

from backend.modules.quant_research.application.benchmark_input_builder import (
    BenchmarkInputBuild, build_benchmark_input,
)
from backend.modules.quant_research.application.dataset_builder import ResearchDataset
from backend.modules.quant_research.application.stock_candidate_builder import (
    StockAccountFacts, StockCandidateBuild, build_stock_candidates,
)
from backend.modules.quant_research.application.trial_executor import prepare_trial


@dataclass(frozen=True)
class StockTrialReadiness:
    trial_id: str
    definition_hash: str
    evaluation_as_of: date
    stocks: StockCandidateBuild
    benchmark: BenchmarkInputBuild
    local_issues: tuple[str, ...]
    certification_issues: tuple[str, ...]

    @property
    def local_complete(self) -> bool:
        return not self.local_issues


def inspect_stock_trial_inputs(
    trial_id: str, dataset: ResearchDataset, *, as_of: date,
    trading_days: tuple[date, ...], account_facts: Mapping[str, StockAccountFacts],
) -> StockTrialReadiness:
    """Keep local data gaps distinct from independent certification gaps.

    No PortfolioTrialInput or PortfolioTargetIntent is constructed here.
    """
    prepared = prepare_trial(trial_id)
    family = prepared.spec.family
    if family not in ("stock_medium_momentum", "stock_short_reversion"):
        raise ValueError("stock portfolio trial required")
    params = dict(prepared.spec.parameters)
    required_sessions = params["benchmark_ma"] if family == "stock_medium_momentum" else 120
    stocks = build_stock_candidates(
        dataset, as_of=as_of, trading_days=trading_days, account_facts=account_facts,
    )
    benchmark = build_benchmark_input(
        dataset, as_of=as_of, trading_days=trading_days,
        required_sessions=required_sessions,
    )
    universe = stocks.universe
    local = list(benchmark.issues)
    if stocks.gaps:
        local.append(f"stock: {len(stocks.gaps)} candidate input gaps")
    if not universe.candidate_set_matches_snapshot:
        local.append("stock: candidate set differs from frozen snapshot")
    if not universe.expected_codes:
        local.append("stock: no active identities")
    if dataset.quality.status != "READY":
        local.append("dataset: not READY")
    if "stock" not in dataset.quality.instrument_types:
        local.append("dataset: stock universe not selected")
    local.extend(dataset.quality.issues)
    certificates = set(dataset.quality.certification_issues) | set(benchmark.certification_issues)
    coverage = dataset.quality.coverage
    if coverage is None:
        certificates.add("coverage: independent calendar absent")
    elif not coverage.complete:
        certificates.add("coverage: historical symbol/day gaps remain")
    certificates.add("account: locked ownership and cooldown provenance not verified")
    certificates.add("stock_market_facts: historical source availability not verified")
    return StockTrialReadiness(
        trial_id=prepared.spec.trial_id,
        definition_hash=prepared.definition_hash,
        evaluation_as_of=as_of,
        stocks=stocks, benchmark=benchmark,
        local_issues=tuple(sorted(set(local))),
        certification_issues=tuple(sorted(certificates)),
    )
