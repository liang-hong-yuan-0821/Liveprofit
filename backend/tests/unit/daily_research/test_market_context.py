from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from backend.modules.daily_research.application.news_pipeline import (
    PreparedNewsRun,
    build_market_research_context,
    _published_by_cutoff,
)


def _target(scope: str, target: str) -> dict:
    return {
        "scope": scope,
        "target": target,
        "scope_refs": [],
        "horizons": [{
            "trading_days": 1,
            "direction": "bullish",
            "strength": 0.8,
            "confidence": 0.9,
        }],
    }


def test_daily_context_uses_latest_accepted_fact_and_separates_market_and_sector():
    cutoff = datetime(2026, 9, 23, 13, 0, tzinfo=timezone.utc)
    historical = {
        "assessment_id": "a-1",
        "event_id": 7,
        "fact_key": "f-1",
        "revision": 1,
        "title": "旧标签",
        "content": "旧摘要",
        "event_type": "政策",
        "labels": {"fact": {}, "targets": [_target("market", "market:CN")]},
        "evidence": [],
    }
    accepted_update = SimpleNamespace(
        review_status="accepted",
        event_id=7,
        fact_key="f-1",
        canonical_key="event-7",
        title="最新标签",
        fact_summary="更正后的摘要",
        event_type="政策",
        labels={
            "fact": {"stage": "implemented"},
            "targets": [_target("sector", "SW:801080")],
        },
        evidence=(),
        unresolved_reasons=(),
    )
    disputed = SimpleNamespace(
        review_status="disputed",
        event_id=8,
        fact_key="f-2",
        canonical_key="event-8",
        title="有争议标签",
        fact_summary="不能进入市场研究快照",
        event_type="其他",
        labels={"targets": [_target("market", "market:CN")]},
        evidence=(),
        unresolved_reasons=(),
    )
    prepared = PreparedNewsRun(
        task_id="task-1",
        cutoff_at=cutoff,
        captured={"status": "ok"},
        results=(SimpleNamespace(
            result=SimpleNamespace(assessments=(accepted_update, disputed), debate_rounds=1)
        ),),
        event_candidates=(historical,),
        pending_at_start=0,
        capture_after_cutoff=False,
        model_version="mock",
        prompt_version="mock",
    )

    context = build_market_research_context(prepared)

    assert context["cutoff_at"] == cutoff.isoformat()
    assert context["event_count"] == 1
    assert context["events"][0]["title"] == "最新标签"
    assert context["events"][0]["market_target"] is False
    assert context["events"][0]["sector_targets"][0]["target"] == "SW:801080"


def test_strict_schedule_marks_only_newly_discovered_pre_cutoff_news_as_late():
    cutoff = datetime(2026, 9, 23, 1, 0, tzinfo=timezone.utc)
    assert _published_by_cutoff("2026-09-23T00:59:00+00:00", cutoff)
    assert not _published_by_cutoff("2026-09-23T01:01:00+00:00", cutoff)
    assert _published_by_cutoff(None, cutoff)


def test_daily_market_and_sector_subgraphs_compile_without_live_news_tool_edges():
    from AI.marketAgents.market_layer_graph import MarketLayerGraph
    from AI.sectorAgents.sector_layer_graph import SectorLayerGraph
    from AI.stockAgents.conditional_logic import ConditionalLogic
    from AI.stockAgents.stock_layer_graph import StockLayerGraph

    market = MarketLayerGraph(object(), object()).build_daily_research()
    sector = SectorLayerGraph(object(), object(), enable_structured_list=True).build_daily_research()
    stock = StockLayerGraph(
        object(), object(), object(), None, None, None, None, None, ConditionalLogic(), {},
    ).build_daily_research()

    market_nodes = set(market.get_graph().nodes)
    sector_nodes = set(sector.get_graph().nodes)
    stock_nodes = set(stock.get_graph().nodes)
    assert {"CN News Analyst", "CN Tech Analyst", "Risk Gate"} <= market_nodes
    assert {"Sector News Analyst", "Sector Tech Analyst", "Sector Rotation Analyst"} <= sector_nodes
    assert {"News Analyst", "Bull Researcher", "Risk Judge"} <= stock_nodes
    assert "tools_news" not in stock_nodes
    assert not any(node.startswith("tools_") for node in market_nodes | sector_nodes)


def test_daily_trading_graph_compiles_stock_only_candidate_path():
    from AI.graph.trading_graph import TradingAgentsGraph
    from AI.stockAgents.conditional_logic import ConditionalLogic

    graph_builder = TradingAgentsGraph.__new__(TradingAgentsGraph)
    graph_builder.daily_research = True
    graph_builder.selectedLayer = ["stock"]
    graph_builder.quick_thinking_llm = object()
    graph_builder.deep_thinking_llm = object()
    graph_builder.toolkit = object()
    graph_builder.bull_memory = None
    graph_builder.bear_memory = None
    graph_builder.trader_memory = None
    graph_builder.invest_judge_memory = None
    graph_builder.risk_manager_memory = None
    graph_builder.conditional_logic = ConditionalLogic()
    graph_builder.config = {}

    compiled = graph_builder._build_graph()

    assert "Stock Layer" in set(compiled.get_graph().nodes)
