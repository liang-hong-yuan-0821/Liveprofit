"""
集成测试 AI.sectorAgents.analysts.sector_tech_analyst — 板块技术分析师。
使用真实 LLM + 真实 Tushare 数据源。
"""

import pytest
from AI.sectorAgents.analysts.sector_tech_analyst import create_sector_tech_analyst


@pytest.fixture
def state():
    return {
        "trade_date": "2026-08-08",
        "messages": [],
        "sector_tech_tool_call_count": 0,
        # T6：market_regime 为结构化 dict（原 str 原地替换）
        "market_regime": {
            "short_term": {"level": "适合"},
            "wave": {"level": "进攻"},
            "long_term": {"level": "配置窗口"},
        },
        "market_event_calendar": {
            "short_term": {"level": "低", "score": 1},
            "wave": {"level": "低", "score": 1},
            "long_term": {"level": "低", "score": 1},
        },
        "sector_shortlist": "",
    }


def test_factory_returns_callable(real_llm, real_toolkit):
    node = create_sector_tech_analyst(real_llm, real_toolkit)
    assert callable(node)


def test_market_context_renders_dict_and_isolates_event_reports(state):
    """T6：结构化 dict 渲染注入；事件/信息研究报告技术隔离（不得进技术判断）。"""
    from AI.sectorAgents.analysts.sector_tech_analyst import _build_sector_market_context

    context = _build_sector_market_context({
        **state,
        "international_news_report": "事件原文" * 100,
        "cn_news_report": "日历全文" * 100,
    })
    assert "## 大盘环境判定（市场层）" in context
    assert "短线(5日)：适合" in context
    assert "事件原文" not in context          # 国际影响全文不进技术节点
    assert "日历全文" not in context          # 结构化日历已就位，不退化全文报告
    assert "## 资金日历（市场层）" in context


def test_generates_report(real_llm, real_toolkit, state):
    """真实运行：生成板块技术分析报告"""
    node = create_sector_tech_analyst(real_llm, real_toolkit)
    result = node(state)

    assert "sector_tech_report" in result
    assert len(result["sector_tech_report"]) > 50
    assert "sector_tech_tool_call_count" in result
