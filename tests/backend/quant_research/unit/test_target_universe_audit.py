# test-catalog-begin
# {
#   "purpose": "量化研究 / target_universe_audit：Snapshot candidate matching is not independent market certification.",
#   "keywords": [
#     "量化研究",
#     "重复请求",
#     "target_universe_audit",
#     "duplicate"
#   ],
#   "covers": [
#     "backend/modules/quant_research/application/dataset_builder.py",
#     "backend/modules/quant_research/application/target_universe_audit.py",
#     "backend/modules/quant_strategy/domain/stock_inputs.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

"""Snapshot candidate matching is not independent market certification."""
from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from backend.modules.quant_research.application.dataset_builder import (
    DatasetQuality, ResearchDataset,
)
from backend.modules.quant_research.application.target_universe_audit import (
    audit_stock_target_universe,
)
from backend.modules.quant_strategy.domain.stock_inputs import StockCandidate


DAY = date(2026, 9, 24)


def snapshot(rows, *, issues=(), certifications=("coverage_audit: historical completeness not verified",)):
    frame = pd.DataFrame(rows, columns=(
        "ts_code", "instrument_type", "list_date", "delist_date",
    ))
    return ResearchDataset(
        tables={"instrument": frame},
        quality=DatasetQuality(
            status="READY" if not issues else "INSUFFICIENT_EVIDENCE",
            issues=issues, as_of=DAY, source_rows={"instrument": len(frame)},
            certification_issues=certifications, instrument_types=("stock",),
        ),
    )


def stock(code):
    return StockCandidate(code, (), 300, Decimal(30000000), False, False)


def test_missing_unsupported_and_dated_listing_are_reported_together():
    dataset = snapshot([
        ("000001.SZ", "stock", date(1991, 1, 1), None),
        ("920001.BJ", "stock", date(2024, 1, 1), None),
        ("000002.SZ", "stock", date(1991, 1, 1), DAY),
        ("000003.SZ", "stock", date(2026, 9, 28), None),
        ("510300.SH", "fund", date(2010, 1, 1), None),
    ])
    audit = audit_stock_target_universe(dataset, (stock("000001.SZ"),), as_of=DAY)
    assert audit.expected_codes == ("000001.SZ", "920001.BJ")
    assert audit.missing_codes == ("920001.BJ",)
    assert audit.unsupported_codes == ("920001.BJ",)
    assert not audit.candidate_set_matches_snapshot
    assert "coverage: independent calendar absent" in audit.evidence_issues
    assert "coverage_audit: historical completeness not verified" in audit.evidence_issues


def test_listing_and_delisting_boundaries_and_future_candidate():
    dataset = snapshot([
        ("000001.SZ", "stock", DAY, None),
        ("000002.SZ", "stock", date(1991, 1, 1), date(2026, 9, 25)),
        ("000003.SZ", "stock", date(1991, 1, 1), DAY),
        ("000004.SZ", "stock", date(2026, 9, 25), None),
    ])
    audit = audit_stock_target_universe(dataset, (
        stock("000001.SZ"), stock("000002.SZ"), stock("000004.SZ"),
    ), as_of=DAY)
    assert audit.expected_codes == ("000001.SZ", "000002.SZ")
    assert audit.unexpected_codes == ("000004.SZ",)


def test_local_match_does_not_erase_snapshot_certification_issues():
    dataset = snapshot([("000001.SZ", "stock", date(1991, 1, 1), None)])
    audit = audit_stock_target_universe(dataset, (stock("000001.SZ"),), as_of=DAY)
    assert audit.candidate_set_matches_snapshot
    assert audit.evidence_issues  # Local identity equality does not certify source history.


def test_unexpected_duplicate_and_invalid_listing_are_visible():
    dataset = snapshot([
        ("000001.SZ", "stock", "bad-date", None),
        ("000002.SZ", "stock", date(1991, 1, 1), None),
    ], issues=("daily: incomplete",))
    audit = audit_stock_target_universe(dataset, (
        stock("000002.SZ"), stock("000002.SZ"), stock("999999.SZ"),
    ), as_of=DAY)
    assert audit.duplicate_codes == ("000002.SZ",)
    assert audit.unexpected_codes == ("999999.SZ",)
    assert "instrument: unknown listing interval" in audit.evidence_issues
    assert "dataset: not READY" in audit.evidence_issues


def test_wrong_date_and_invalid_inputs_fail_closed():
    dataset = snapshot([("000001.SZ", "stock", date(1991, 1, 1), None)])
    with pytest.raises(ValueError, match="date differs"):
        audit_stock_target_universe(dataset, (stock("000001.SZ"),),
                                    as_of=date(2026, 9, 23))
    with pytest.raises(TypeError, match="candidate tuple"):
        audit_stock_target_universe(dataset, (stock(None),), as_of=DAY)
    with pytest.raises(ValueError, match="identities"):
        audit_stock_target_universe(ResearchDataset({}, dataset.quality), (), as_of=DAY)


def test_invalid_intervals_duplicates_and_empty_universe_are_evidence_gaps():
    dataset = snapshot([
        ("000001.SZ", "stock", date(2020, 1, 1), date(2019, 1, 1)),
        ("000001.SZ", "stock", date(2020, 1, 1), date(2019, 1, 1)),
    ])
    audit = audit_stock_target_universe(dataset, (), as_of=DAY)
    assert "instrument: invalid or duplicate stock identity" in audit.evidence_issues
    assert "instrument: unknown listing interval" in audit.evidence_issues
    assert "instrument: no active stock identities" in audit.evidence_issues
