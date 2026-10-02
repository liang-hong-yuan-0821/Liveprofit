"""Compare stock target inputs to the frozen research snapshot's own universe.

This is local consistency evidence, not independent source certification.
"""
from collections import Counter
from dataclasses import dataclass
from datetime import date

import pandas as pd

from backend.modules.quant_research.application.dataset_builder import ResearchDataset
from backend.modules.quant_strategy.domain.stock_inputs import StockCandidate


@dataclass(frozen=True)
class StockUniverseAudit:
    as_of: date
    expected_codes: tuple[str, ...]
    candidate_codes: tuple[str, ...]
    missing_codes: tuple[str, ...]
    unexpected_codes: tuple[str, ...]
    duplicate_codes: tuple[str, ...]
    unsupported_codes: tuple[str, ...]
    evidence_issues: tuple[str, ...]

    @property
    def candidate_set_matches_snapshot(self) -> bool:
        return not (self.missing_codes or self.unexpected_codes
                    or self.duplicate_codes or self.unsupported_codes)


def audit_stock_target_universe(
    dataset: ResearchDataset, candidates: tuple[StockCandidate, ...], *, as_of: date,
) -> StockUniverseAudit:
    """Report all known identity/quality gaps without granting production use."""
    if not isinstance(dataset, ResearchDataset) or type(as_of) is not date:
        raise TypeError("frozen research dataset and exact as-of date required")
    if not isinstance(candidates, tuple) or any(
        not isinstance(item, StockCandidate)
        or not isinstance(item.ts_code, str) or not item.ts_code
        for item in candidates
    ):
        raise TypeError("frozen stock candidate tuple required")
    if dataset.quality.as_of != as_of:
        raise ValueError("target universe date differs from research snapshot")
    frame = dataset.tables.get("instrument")
    required = {"ts_code", "instrument_type", "list_date", "delist_date"}
    if not isinstance(frame, pd.DataFrame) or not required <= set(frame):
        raise ValueError("research snapshot lacks dated instrument identities")

    issues = list(dataset.quality.issues) + list(dataset.quality.certification_issues)
    if dataset.quality.status != "READY":
        issues.append("dataset: not READY")
    if "stock" not in dataset.quality.instrument_types:
        issues.append("dataset: stock universe not selected")
    coverage = dataset.quality.coverage
    if coverage is None:
        issues.append("coverage: independent calendar absent")
    elif not coverage.complete:
        issues.append("coverage: historical symbol/day gaps remain")

    stock = frame.loc[frame["instrument_type"].eq("stock")].copy()
    codes = stock["ts_code"]
    if codes.isna().any() or codes.duplicated().any():
        issues.append("instrument: invalid or duplicate stock identity")
    listed = pd.to_datetime(stock["list_date"].astype(str), format="%Y-%m-%d", errors="coerce")
    delisted = pd.to_datetime(stock["delist_date"].astype(str), format="%Y-%m-%d", errors="coerce")
    if (listed.isna().any()
            or (stock["delist_date"].notna() & delisted.isna()).any()
            or (delisted.notna() & (delisted <= listed)).any()):
        issues.append("instrument: unknown listing interval")
    cutoff = pd.Timestamp(as_of)
    active = stock.loc[
        listed.notna() & (listed <= cutoff) & (delisted.isna() | (cutoff < delisted)),
        "ts_code",
    ]
    expected = set(active.dropna().astype(str))
    if not expected:
        issues.append("instrument: no active stock identities")
    observed_list = [item.ts_code for item in candidates]
    counts = Counter(observed_list)
    observed = set(counts)
    duplicates = sorted(code for code, count in counts.items() if count > 1)
    unsupported = sorted(code for code in expected if not code.endswith((".SH", ".SZ")))
    return StockUniverseAudit(
        as_of=as_of,
        expected_codes=tuple(sorted(expected)),
        candidate_codes=tuple(sorted(observed)),
        missing_codes=tuple(sorted(expected - observed)),
        unexpected_codes=tuple(sorted(observed - expected)),
        duplicate_codes=tuple(duplicates),
        unsupported_codes=tuple(unsupported),
        evidence_issues=tuple(sorted(set(issues))),
    )
