"""
集成测试 AI.marketAgents.analysts.cn_news_analyst — 中国新闻分析师。
使用真实 LLM + 真实 Tushare 数据源，验证提示词 + tool 的实际效果。
"""

import pytest
from AI.dataflows import market_features as mf
from AI.marketAgents.analysts.cn_news_analyst import create_cn_news_analyst


@pytest.fixture
def state():
    return {
        "trade_date": "2026-08-08",
        "messages": [],
        "cn_news_tool_call_count": 0,
    }


def test_factory_returns_callable(real_llm, real_toolkit):
    """工厂函数返回可调用的 node"""
    node = create_cn_news_analyst(real_llm, real_toolkit)
    assert callable(node)


def test_generates_report(real_llm, real_toolkit, state):
    """真实运行：生成中国市场新闻分析报告，包含三级别事件日历"""
    node = create_cn_news_analyst(real_llm, real_toolkit)
    result = node(state)

    # 报告生成成功
    assert "cn_news_report" in result
    assert len(result["cn_news_report"]) > 100, f"报告太短: {len(result['cn_news_report'])} 字符"

    # 事件日历已提取（T6：结构化 dict，原文本字段原地替换）
    calendar = result["market_event_calendar"]
    assert isinstance(calendar, dict) and calendar
    assert calendar.get("short_term", {}).get("level") in mf.CALENDAR_PRESSURE_ENUM

    # tool_call_count 已递增
    assert result["cn_news_tool_call_count"] >= 1

    # 报告内容应涉及 A 股市场
    report = result["cn_news_report"]
    keywords = ["IPO", "限售", "两融", "资金", "交割"]
    found = [k for k in keywords if k in report]
    assert len(found) >= 2, f"报告应包含至少 2 个关键主题，实际找到: {found}"
