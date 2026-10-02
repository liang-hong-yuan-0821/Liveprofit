# test-catalog-begin
# {
#   "purpose": "每日研究 / news_analysis",
#   "keywords": [
#     "每日研究",
#     "分析任务",
#     "重复请求",
#     "新闻",
#     "news_analysis",
#     "analysis",
#     "duplicate",
#     "news"
#   ],
#   "covers": [
#     "AI/eventStudy/review/news_analysis.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

from __future__ import annotations

import json
from types import SimpleNamespace

from AI.eventStudy.review.news_analysis import (
    EventAssessmentDraft,
    NewsAnalysisAgents,
    _stable_key,
)


class ScriptedAgent:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        response = self.responses.pop(0)
        return SimpleNamespace(content=json.dumps(response, ensure_ascii=False))


def _responses():
    identity = {"entity": "某公司", "action": "回购", "reference_period": "2026"}
    fact_key = _stable_key("fact", {
        key: " ".join(value.casefold().split()) for key, value in identity.items()
    })
    novelty = {
        "novelty": "new",
        "facts": [{
            "novelty": "new",
            "identity": identity,
            "fact_summary": "某公司公告回购股份",
            "evidence": [{"quote": "某公司公告回购股份"}],
        }],
    }
    labels = {
        "facts": [{
            "fact_key": fact_key,
            "targets": [{
                "target": "market:CN",
                "horizons": [{
                    "trading_days": days,
                    "direction": "bullish",
                    "strength": 0.8,
                    "confidence": 0.9,
                    "evidence_ids": ["source-1-1"],
                    "reason": f"{days}日判断依据公告事实",
                    "invalidations": [],
                } for days in (1, 5, 20)],
            }],
            "stage": "published",
            "expected_value": None,
            "actual_value": None,
            "previous_value": None,
            "importance": 3,
            "event_condition": "不适用",
            "event_type": "其他",
            "event_subtype": None,
            "valid_until": None,
        }],
    }
    return novelty, labels


def _agents(reviewer_responses):
    novelty, labels = _responses()
    novelty_agent = ScriptedAgent([novelty])
    label_agent = ScriptedAgent([labels, labels, labels])
    reviewer_agent = ScriptedAgent(reviewer_responses)
    agents = NewsAnalysisAgents(
        novelty_agent=novelty_agent,
        label_agent=label_agent,
        reviewer_agent=reviewer_agent,
        model_version="mock-v1",
        target_validator=lambda target: ("market", ()) if target == "market:CN" else ("stock", (target,)),
    )
    return agents, novelty_agent, label_agent, reviewer_agent


def test_agent_a_reviews_labeler_output_before_accepting():
    agents, novelty, labeler, reviewer = _agents([{"verdict": "approve"}])

    result = agents.analyze(
        news_id="news-1",
        news={"title": "某公司公告", "raw_content": "某公司公告回购股份"},
        candidates=[],
    )

    assert result.status == "accepted"
    assert isinstance(result.assessments[0], EventAssessmentDraft)
    assert result.llm_calls == 3
    assert (novelty.calls, labeler.calls, reviewer.calls) == (1, 1, 1)


def test_debate_has_hard_two_round_and_seven_call_ceiling():
    challenge = {
        "verdict": "challenge",
        "issues": [{"id": "evidence", "field": "targets", "reason": "补充适用对象证据"}],
    }
    agents, novelty, labeler, reviewer = _agents([challenge, challenge, challenge])

    result = agents.analyze(
        news_id="news-2",
        news={"title": "某公司公告", "raw_content": "某公司公告回购股份"},
        candidates=[],
    )

    assert result.status == "disputed"
    assert result.debate_rounds == 2
    assert result.llm_calls == 7
    assert (novelty.calls, labeler.calls, reviewer.calls) == (1, 3, 3)


def test_duplicate_decision_records_the_matched_event_and_fact():
    novelty, _ = _responses()
    fact = novelty["facts"][0]
    fact.update({
        "novelty": "duplicate", "candidate_event_id": 7,
        "candidate_fact_key": "fact-existing",
    })
    novelty["novelty"] = "duplicate"
    agent = ScriptedAgent([novelty])
    agents = NewsAnalysisAgents(
        novelty_agent=agent, label_agent=ScriptedAgent([]),
        reviewer_agent=ScriptedAgent([]), model_version="mock-v1",
    )

    result = agents.analyze(
        news_id="news-duplicate", news={"title": "某公司公告", "raw_content": "某公司公告回购股份"},
        candidates=[{
            "event_id": 7, "canonical_key": "event-7", "fact_key": "fact-existing",
            "assessment_id": "assessment-7", "labels": {},
        }],
    )

    assert result.status == "skipped"
    assert result.skipped_facts == ({
        "novelty": "duplicate", "event_id": 7, "fact_key": "fact-existing",
        "assessment_id": "assessment-7", "canonical_key": "event-7",
    },)


def test_incomplete_candidate_recall_cannot_accept_a_new_event():
    agents, _, _, _ = _agents([{"verdict": "approve"}])

    result = agents.analyze(
        news_id="news-incomplete", news={"title": "某公司公告", "raw_content": "某公司公告回购股份"},
        candidates=[], candidate_recall_complete=False,
    )

    assert result.status == "disputed"
    assert result.assessments == ()
    assert "候选召回不完整" in result.unresolved_reasons[0]
