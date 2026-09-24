from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
import pytest

from backend.modules.daily_research.application.quant_pipeline import (
    _build_candidate_rows,
    _candidate_stock_research_context,
    _event_adjustments,
    _rank_percentiles,
)


def _signal(signal_id, strategy_id, ticker, score):
    return SimpleNamespace(
        id=signal_id,
        strategy_version_id=strategy_id,
        ts_code=ticker,
        score=Decimal(str(score)),
        reason="单测信号",
    )


def test_strategy_scores_use_plan_midrank_percentile_and_average_ties():
    strategy_id = uuid.uuid4()
    rows = [
        _signal(1, strategy_id, "000001.SZ", 10),
        _signal(2, strategy_id, "000002.SZ", 20),
        _signal(3, strategy_id, "000003.SZ", 20),
        _signal(4, strategy_id, "000004.SZ", 40),
    ]

    assert _rank_percentiles(rows) == {
        1: 12.5,
        2: 50.0,
        3: 50.0,
        4: 87.5,
    }
    assert _rank_percentiles([_signal(5, strategy_id, "000005.SZ", 7)]) == {5: 50.0}


def test_event_score_deduplicates_scope_paths_and_excludes_market_fanout():
    as_of = datetime(2026, 9, 23, 13, 0, tzinfo=timezone.utc)
    ticker = "000001.SZ"
    labels = {
        "fact": {
            "stage": "published",
            "first_published_at": (as_of - timedelta(days=7)).isoformat(),
        },
        "targets": [
            {
                "target": "stock:000001.SZ",
                "scope": "stock",
                "scope_refs": ["stock:000001.SZ"],
                "horizons": [{
                    "trading_days": 1,
                    "direction": "bullish",
                    "strength": 0.8,
                    "confidence": 0.8,
                }],
            },
            {
                "target": "SW:801080",
                "scope": "sector",
                "scope_refs": ["SW:801080"],
                "horizons": [{
                    "trading_days": 1,
                    "direction": "bullish",
                    "strength": 0.8,
                    "confidence": 0.8,
                }],
            },
            {
                "target": "market:CN",
                "scope": "market",
                "scope_refs": [],
                "horizons": [{
                    "trading_days": 1,
                    "direction": "bearish",
                    "strength": 1.0,
                    "confidence": 1.0,
                }],
            },
        ],
    }
    event = {
        "event_id": 7,
        "assessment_id": "assessment-7",
        "fact_key": "fact-7",
        "title": "公司公告",
        "event_type": "其他",
        "announced_at": as_of - timedelta(days=7),
        "available_at": as_of,
        "labels": labels,
    }

    result = _event_adjustments(
        [event],
        as_of=as_of,
        universe={ticker, "000002.SZ"},
        industries={"SW:801080": {ticker}},
        sectors={},
    )

    assert set(result) == {ticker}
    assert len(result[ticker]) == 1
    assert result[ticker][0]["relevance"] == 1.0
    assert result[ticker][0]["score"] == pytest.approx(0.32)


def test_recently_updated_old_event_uses_the_assessment_time_for_decay():
    as_of = datetime(2026, 9, 23, 13, 0, tzinfo=timezone.utc)
    published_at = as_of - timedelta(days=180)
    event = {
        "event_id": 19,
        "assessment_id": "assessment-19-r2",
        "fact_key": "fact-19",
        "novelty": "update",
        "title": "更新的政策进展",
        "event_type": "政策",
        "announced_at": published_at,
        "available_at": as_of - timedelta(hours=1),
        "labels": {
            "fact": {"stage": "implemented", "first_published_at": published_at.isoformat()},
            "targets": [{
                "target": "stock:000001.SZ",
                "scope": "stock",
                "scope_refs": ["stock:000001.SZ"],
                "horizons": [{
                    "trading_days": 1,
                    "direction": "bullish",
                    "strength": 0.8,
                    "confidence": 0.8,
                }],
            }],
        },
    }

    result = _event_adjustments(
        [event], as_of=as_of, universe={"000001.SZ"}, industries={}, sectors={},
    )

    assert result["000001.SZ"][0]["age_days"] == pytest.approx(0.04)
    assert result["000001.SZ"][0]["score"] > 0.31


def test_candidate_stock_context_freezes_matching_stock_sector_and_market_events():
    cutoff = datetime(2026, 9, 23, 13, 0, tzinfo=timezone.utc)
    rows = [
        {
            "event_id": 1, "assessment_id": "a1", "fact_key": "f1", "revision": 2,
            "event_title": "个股事件", "event_content": "公司公告事实", "event_type": "业绩",
            "available_at": cutoff.isoformat(), "labels": {"fact": {"stage": "published"}, "targets": [
                {"scope": "stock", "target": "stock:000001.SZ", "scope_refs": ["stock:000001.SZ"]},
            ]},
            "evidence": [{"evidence_id": "e1", "quote": "原文", "source": "财联社"}],
        },
        {
            "event_id": 2, "assessment_id": "a2", "fact_key": "f2", "revision": 1,
            "title": "板块政策", "content": "板块摘要", "event_type": "政策", "available_at": cutoff.isoformat(),
            "labels": {"fact": {"stage": "implemented"}, "targets": [
                {"scope": "sector", "target": "SW:801080", "scope_refs": ["SW:801080"]},
            ]}, "evidence": [],
        },
        {
            "event_id": 3, "assessment_id": "a3", "fact_key": "f3", "revision": 1,
            "title": "其他公司事件", "content": "其他公司", "event_type": "其他", "available_at": cutoff.isoformat(),
            "labels": {"fact": {}, "targets": [
                {"scope": "stock", "target": "stock:000002.SZ", "scope_refs": ["stock:000002.SZ"]},
            ]}, "evidence": [],
        },
    ]

    context = _candidate_stock_research_context(
        rows,
        ticker="000001.SZ",
        cutoff_at=cutoff,
        market_as_of_trade_date=cutoff.date(),
        risk_gate="caution",
        market_outlook={"horizons": {"5": {"direction": "mixed"}}},
        universe={"000001.SZ", "000002.SZ"},
        industries={"SW:801080": {"000001.SZ"}},
        sectors={},
    )

    assert context["event_count"] == 2
    assert {row["event_id"] for row in context["events"]} == {1, 2}
    assert context["risk_gate"] == "caution"
    assert context["market_horizons"]["5"]["direction"] == "mixed"


def test_candidate_row_keeps_positive_and_negative_event_evidence_separate():
    strategy_a, strategy_b = uuid.uuid4(), uuid.uuid4()
    rows = [
        _signal(1, strategy_a, "000001.SZ", 1),
        _signal(2, strategy_b, "000001.SZ", 1),
    ]
    strategies = {
        strategy_a: {"name": "策略 A", "version_no": 1},
        strategy_b: {"name": "策略 B", "version_no": 3},
    }
    candidates, total = _build_candidate_rows(
        rows,
        strategies=strategies,
        event_adjustments={"000001.SZ": [
            {"score": 0.8, "title": "利好"},
            {"score": -0.1, "title": "利空"},
        ]},
    )

    assert total == 1
    assert candidates[0]["quant_score"] == 50.0
    assert candidates[0]["event_positive_score"] == 16.0
    assert candidates[0]["event_negative_score"] == -2.0
    assert candidates[0]["event_score"] == 14.0
    assert candidates[0]["total_score"] == 64.0


def test_neutral_and_mixed_events_remain_visible_without_changing_candidate_weight():
    as_of = datetime(2026, 9, 23, 13, 0, tzinfo=timezone.utc)
    event = {
        "event_id": 22, "assessment_id": "neutral-assessment", "fact_key": "fact-neutral",
        "title": "中性政策", "event_type": "政策", "available_at": as_of,
        "labels": {"fact": {"first_published_at": as_of.isoformat()}, "targets": [{
            "scope": "stock", "target": "stock:000001.SZ", "scope_refs": ["stock:000001.SZ"],
            "horizons": [{
                "trading_days": 1, "direction": "mixed", "strength": 0.9, "confidence": 0.8,
            }],
        }]},
    }

    adjustments = _event_adjustments(
        [event], as_of=as_of, universe={"000001.SZ"}, industries={}, sectors={},
    )
    candidates, _ = _build_candidate_rows(
        [_signal(1, uuid.uuid4(), "000001.SZ", 1)],
        strategies={}, event_adjustments=adjustments,
    )

    assert len(adjustments["000001.SZ"]) == 1
    assert adjustments["000001.SZ"][0]["direction"] == "mixed"
    assert adjustments["000001.SZ"][0]["score"] == 0
    assert candidates[0]["event_evidence_status"] == "available"
    assert candidates[0]["event_score"] == 0
