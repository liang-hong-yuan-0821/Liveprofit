# test-catalog-begin
# {
#   "purpose": "每日研究 / assessment_review",
#   "keywords": [
#     "每日研究",
#     "批次",
#     "版本修订",
#     "assessment_review",
#     "batch",
#     "revision"
#   ],
#   "covers": [
#     "AI/eventStudy/review/news_dao.py",
#     "backend/modules/daily_research/application/assessment_review.py",
#     "backend/modules/daily_research/application/news_pipeline.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from AI.eventStudy.review import news_dao
from backend.modules.daily_research.application.assessment_review import (
    validate_assessment_labels,
)
from backend.modules.daily_research.application.news_pipeline import _expected_assessment_revision


def _labels():
    return {
        "fact": {
            "stage": "published",
            "event_type": "政策",
            "event_subtype": "监管",
            "event_condition": "未知",
            "importance": 3,
            "expected_value": None,
            "actual_value": None,
            "previous_value": None,
            "valid_until": None,
        },
        "targets": [{
            "target": "market:CN",
            "scope": "market",
            "scope_refs": [],
            "horizons": [
                {
                    "trading_days": days,
                    "direction": "bullish",
                    "strength": 0.7,
                    "confidence": 0.8,
                    "evidence_ids": ["source-1-1"],
                    "reason": f"第 {days} 个交易日影响理由",
                    "invalidations": [],
                }
                for days in (1, 5, 20)
            ],
        }],
    }


def test_manual_label_validation_keeps_the_1_5_20_horizon_contract():
    labels = validate_assessment_labels(
        _labels(), evidence=[{"evidence_id": "source-1-1"}], conn=object(),
    )

    assert [item["trading_days"] for item in labels["targets"][0]["horizons"]] == [1, 5, 20]
    assert labels["fact"]["importance"] == 3


def test_manual_label_validation_rejects_unverifiable_direction_and_bad_importance():
    labels = _labels()
    labels["targets"][0]["horizons"][1]["evidence_ids"] = ["missing"]
    with pytest.raises(ValueError, match="不存在的原文证据"):
        validate_assessment_labels(labels, evidence=[{"evidence_id": "source-1-1"}], conn=object())

    labels = _labels()
    labels["fact"]["importance"] = 6
    with pytest.raises(ValueError, match="importance"):
        validate_assessment_labels(labels, evidence=[{"evidence_id": "source-1-1"}], conn=object())


def test_manual_label_validation_requires_scoring_fields_used_by_quant_pipeline():
    labels = _labels()
    labels["targets"][0]["horizons"][0]["strength"] = None

    with pytest.raises(ValueError, match="strength 和 confidence"):
        validate_assessment_labels(labels, evidence=[{"evidence_id": "source-1-1"}], conn=object())


def test_same_batch_assessments_advance_the_expected_fact_revision():
    candidates = {(7, "fact-a"): 3}
    working = {}

    assert _expected_assessment_revision(working, candidates, event_id=7, fact_key="fact-a") == 3
    working[(7, "fact-a")] = 4
    assert _expected_assessment_revision(working, candidates, event_id=7, fact_key="fact-a") == 4
    assert _expected_assessment_revision(working, candidates, event_id=7, fact_key="fact-b") == 0


def test_candidate_lookback_uses_latest_assessment_time_not_original_event_time():
    class Result:
        def fetchall(self):
            return []

    class Connection:
        sql = ""

        def execute(self, sql, params):
            self.sql = sql
            return Result()

    conn = Connection()
    news_dao.list_event_candidates(
        conn,
        as_of=datetime(2026, 9, 23, tzinfo=timezone.utc),
        lookback_days=90,
    )

    assert "r.available_at >= %s - (%s * INTERVAL '1 day')" in conn.sql
    assert "e.announced_at >=" not in conn.sql
