"""
集成测试 AI.sectorAgents.analysts.sector_news_analyst — 板块新闻分析师。
使用真实 LLM + 真实 Tushare 数据源。
"""

import pytest
from AI.sectorAgents.analysts.sector_news_analyst import create_sector_news_analyst


@pytest.fixture
def state():
    return {
        "trade_date": "2026-08-08",
        "messages": [],
        "sector_news_tool_call_count": 0,
        # T6：市场层结构化字段为 dict（原 str 原地替换）
        "market_regime": {
            "short_term": {"level": "谨慎"},
            "wave": {"level": "平衡"},
            "long_term": {"level": "配置窗口"},
        },
        "market_event_calendar": {
            "short_term": {"level": "中", "score": 3, "drivers": ["解禁"]},
            "wave": {"level": "低", "score": 2},
            "long_term": {"level": "低", "score": 1},
        },
    }


def test_factory_returns_callable(real_llm, real_toolkit):
    node = create_sector_news_analyst(real_llm, real_toolkit)
    assert callable(node)


def test_market_context_renders_structured_dicts(state):
    """T6：市场层结构化 dict 经紧凑渲染注入（不做 str 长度判定）。"""
    from AI.sectorAgents.analysts.sector_news_analyst import _build_sector_market_context

    context = _build_sector_market_context(state)
    assert "## 大盘环境判定（市场层）" in context
    assert "短线(5日)：谨慎" in context
    assert "## 资金日历（市场层）" in context
    assert "资金压力 3/5" in context


def test_market_context_absent_both_dicts_degrades(state):
    """两个结构化字段都缺失 → 判不可用（不留半截标题）。"""
    from AI.sectorAgents.analysts.sector_news_analyst import _build_sector_market_context

    context = _build_sector_market_context(
        {**state, "market_regime": None, "market_event_calendar": {}})
    assert context == "（市场层数据暂不可用）"


def test_generates_report(real_llm, real_toolkit, state):
    """真实运行：生成板块新闻分析报告，包含候选板块短名单"""
    node = create_sector_news_analyst(real_llm, real_toolkit)
    result = node(state)

    assert "sector_news_report" in result
    assert len(result["sector_news_report"]) > 100

    # 必须产出候选板块短名单
    assert "sector_shortlist" in result
    assert len(result["sector_shortlist"]) > 0

    assert result["sector_news_tool_call_count"] >= 1
