"""Audit ETF universe partition before diagnostic monthly representative selection."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Literal, Mapping

import pandas as pd

from backend.modules.quant_research.application.dataset_builder import ResearchDataset
from backend.modules.quant_research.application.fund_candidate_builder import (
    FundCandidateBuild, FundClassificationFacts, build_fund_candidates,
)
from backend.modules.quant_research.application.trial_executor import prepare_trial
from backend.modules.quant_strategy.domain.etf_dual_momentum import select_monthly_representatives
from backend.modules.quant_strategy.domain.etf_defensive_allocation import select_defensive_representatives


@dataclass(frozen=True)
class FundUniversePartition:
    expected_codes: tuple[str, ...]
    candidate_codes: tuple[str, ...]
    excluded_codes: tuple[str, ...]
    gap_codes: tuple[str, ...]
    unaccounted_codes: tuple[str, ...]
    unexpected_codes: tuple[str, ...]
    duplicate_codes: tuple[str, ...]

    @property
    def complete_local(self) -> bool:
        return bool(self.expected_codes) and not (
            self.gap_codes or self.unaccounted_codes or self.unexpected_codes or self.duplicate_codes
        )


@dataclass(frozen=True)
class FundRepresentativeReadiness:
    trial_id: str
    definition_hash: str
    evaluation_as_of: date
    decision_date: date
    funds: FundCandidateBuild
    universe: FundUniversePartition
    disposition: Literal["NOT_SELECTION_DAY", "BLOCKED", "SELECTED", "NO_ELIGIBLE_REPRESENTATIVE"]
    dual_codes: frozenset[str] | None
    defensive_symbols: tuple[tuple[str, str], ...] | None
    local_issues: tuple[str, ...]
    certification_issues: tuple[str, ...]


def _audit_partition(dataset: ResearchDataset, as_of: date, funds: FundCandidateBuild) -> FundUniversePartition:
    frame = dataset.tables["instrument"]
    if not {"ts_code", "instrument_type", "list_date", "delist_date"} <= set(frame):
        raise ValueError("dated fund identities absent")
    active: list[str] = []
    for row in frame.loc[frame["instrument_type"].eq("fund")].itertuples(index=False):
        if (not isinstance(row.ts_code, str) or type(row.list_date) is not date
                or not pd.isna(row.delist_date) and type(row.delist_date) is not date):
            raise ValueError("dated fund listing evidence invalid")
        if row.list_date <= as_of and (pd.isna(row.delist_date) or as_of < row.delist_date):
            active.append(row.ts_code)
    candidate_list = [item.ts_code for item in funds.candidates]
    excluded_list = list(funds.excluded_codes)
    gap_list = list(funds.gaps)
    all_list = candidate_list + excluded_list + gap_list
    counts = Counter(all_list)
    expected = set(active)
    observed = set(all_list)
    return FundUniversePartition(
        expected_codes=tuple(sorted(expected)), candidate_codes=tuple(sorted(set(candidate_list))),
        excluded_codes=tuple(sorted(set(excluded_list))), gap_codes=tuple(sorted(set(gap_list))),
        unaccounted_codes=tuple(sorted(expected - observed)),
        unexpected_codes=tuple(sorted(observed - expected)),
        duplicate_codes=tuple(sorted(code for code, count in counts.items() if count > 1)),
    )


def inspect_monthly_fund_representatives(
    trial_id: str, dataset: ResearchDataset, *, as_of: date, decision_date: date,
    trading_days: tuple[date, ...], classification_facts: Mapping[str, FundClassificationFacts],
    is_month_end_selection: bool,
) -> FundRepresentativeReadiness:
    """Freeze only a diagnostic selection; never create a portfolio target."""
    prepared = prepare_trial(trial_id)
    family = prepared.spec.family
    if family not in ("etf_dual_momentum", "etf_defensive_allocation"):
        raise ValueError("ETF portfolio trial required")
    if (type(as_of) is not date or type(decision_date) is not date or decision_date < as_of
            or type(is_month_end_selection) is not bool):
        raise ValueError("trusted ETF selection dates and month-end flag required")
    funds = build_fund_candidates(
        dataset, as_of=as_of, trading_days=trading_days,
        classification_facts=classification_facts,
    )
    partition = _audit_partition(dataset, as_of, funds)
    local = list(dataset.quality.issues)
    if dataset.quality.status != "READY":
        local.append("dataset: not READY")
    if "fund" not in dataset.quality.instrument_types:
        local.append("dataset: fund universe not selected")
    if not partition.complete_local:
        local.append("fund: candidate/exclusion/gap partition incomplete")
    if funds.gaps:
        local.append(f"fund: {len(funds.gaps)} candidate input gaps")
    certificates = set(funds.certification_issues) | {
        "etf_catalog: independent listed universe not verified",
        "calendar: month-end selection provenance not verified",
        "fund_market_facts: historical source availability not verified",
    }
    coverage = dataset.quality.coverage
    if coverage is None:
        certificates.add("coverage: independent calendar absent")
    elif not coverage.complete:
        certificates.add("coverage: historical symbol/day gaps remain")
    dual: frozenset[str] | None = None
    defensive: tuple[tuple[str, str], ...] | None = None
    if not is_month_end_selection:
        disposition = "NOT_SELECTION_DAY"
    elif local:
        disposition = "BLOCKED"
    elif family == "etf_dual_momentum":
        dual = select_monthly_representatives(
            funds.candidates, decision_date=decision_date,
            lookback=dict(prepared.spec.parameters)["lookback"], data_as_of=as_of,
        )
        disposition = "SELECTED" if dual else "NO_ELIGIBLE_REPRESENTATIVE"
    else:
        defensive = select_defensive_representatives(
            funds.candidates, decision_date=decision_date, data_as_of=as_of,
        )
        disposition = "SELECTED" if defensive else "NO_ELIGIBLE_REPRESENTATIVE"
    return FundRepresentativeReadiness(
        trial_id=prepared.spec.trial_id, definition_hash=prepared.definition_hash,
        evaluation_as_of=as_of, decision_date=decision_date,
        funds=funds, universe=partition, disposition=disposition,
        dual_codes=dual, defensive_symbols=defensive,
        local_issues=tuple(sorted(set(local))),
        certification_issues=tuple(sorted(certificates)),
    )
