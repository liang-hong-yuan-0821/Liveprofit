# test-catalog-begin
# {
#   "purpose": "量化研究 / fund_representative_readiness",
#   "keywords": [
#     "量化研究",
#     "现金",
#     "指数",
#     "选择范围",
#     "fund_representative_readiness",
#     "cash",
#     "index",
#     "selection"
#   ],
#   "covers": [
#     "backend/modules/quant_research/application/dataset_builder.py",
#     "backend/modules/quant_research/application/fund_candidate_builder.py",
#     "backend/modules/quant_research/application/fund_representative_readiness.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

from tests.backend.quant_research.support.fund_representative_readiness import (
    _snapshot,
    _inspect,
)
from datetime import timedelta

import pandas as pd
import pytest

from backend.modules.quant_research.application.dataset_builder import DatasetQuality, ResearchDataset
from backend.modules.quant_research.application.fund_candidate_builder import FundClassificationFacts
from backend.modules.quant_research.application.fund_representative_readiness import (
    inspect_monthly_fund_representatives,
)






def test_complete_partition_selects_highest_adv_per_category_and_index_only_diagnostically():
    dataset, days, facts = _snapshot()
    result = _inspect("etf_dual_momentum:60:1:WEEKLY", dataset, days, facts)
    assert result.universe.complete_local
    assert result.universe.expected_codes == tuple(sorted(facts))
    assert result.universe.excluded_codes == ("510999.SH",)
    assert result.disposition == "SELECTED"
    assert result.dual_codes == frozenset({"510310.SH", "511010.SH"})
    assert result.defensive_symbols is None
    assert result.local_issues == ()
    assert "etf_catalog: independent listed universe not verified" in result.certification_issues
    assert "fund_market_facts: historical source availability not verified" in result.certification_issues
    assert not hasattr(result, "intent")


def test_one_missing_classification_blocks_month_selection_instead_of_cash():
    dataset, days, facts = _snapshot()
    facts.pop("510310.SH")
    result = _inspect("etf_dual_momentum:60:1:WEEKLY", dataset, days, facts)
    assert result.disposition == "BLOCKED"
    assert result.dual_codes is None
    assert result.universe.gap_codes == ("510310.SH",)
    assert "fund: 1 candidate input gaps" in result.local_issues


def test_nonselection_day_never_returns_empty_frozen_set():
    dataset, days, facts = _snapshot()
    result = _inspect("etf_dual_momentum:60:1:WEEKLY", dataset, days, facts, month_end=False)
    assert result.disposition == "NOT_SELECTION_DAY"
    assert result.dual_codes is None


def test_defensive_representation_uses_same_partition_and_frozen_category_pairs():
    dataset, days, facts = _snapshot()
    result = _inspect("etf_defensive_allocation:20:0.04:60", dataset, days, facts)
    assert result.disposition == "SELECTED"
    assert result.defensive_symbols == (("BOND_MEDIUM", "511010.SH"),
                                         ("EQUITY_300", "510310.SH"))
    assert result.dual_codes is None


def test_no_eligible_representative_is_distinct_from_missing_evidence():
    dataset, days, facts = _snapshot()
    dataset.tables["daily"]["amount"] = 1000.
    result = _inspect("etf_dual_momentum:60:1:WEEKLY", dataset, days, facts)
    assert result.universe.complete_local
    assert result.disposition == "NO_ELIGIBLE_REPRESENTATIVE"
    assert result.dual_codes == frozenset()
    assert result.certification_issues


def test_invalid_trial_or_decision_date_rejected_before_selection():
    dataset, days, facts = _snapshot()
    with pytest.raises(ValueError, match="ETF portfolio trial required"):
        _inspect("stock_medium_momentum:60:10:120", dataset, days, facts)
    with pytest.raises(ValueError, match="selection dates"):
        inspect_monthly_fund_representatives(
            "etf_dual_momentum:60:1:WEEKLY", dataset,
            as_of=days[-1], decision_date=days[-2], trading_days=days,
            classification_facts=facts, is_month_end_selection=True,
        )
