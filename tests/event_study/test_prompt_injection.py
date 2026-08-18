"""
事件研究数据 prompt 注入测试（2026-08-18）

国际新闻分析师节点执行时提前请求事件研究系统（search_similar_events），
返回结果直接写进 prompt——与上游事件提取结果同为静态注入。
"""

from langchain_core.messages import AIMessage, HumanMessage

from AI.marketAgents.analysts import international_news_analyst


class StubLLM:
    """记录 prompt 内容并返回固定响应。"""

    def __init__(self):
        self.last_messages = None

    def invoke(self, messages):
        self.last_messages = messages
        return AIMessage(content="分析完成")


def _make_state():
    return {
        "trade_date": "2026-08-18",
        "messages": [HumanMessage(content="开始分析")],
        "international_event_report": "美联储宣布降息 25 个基点，超出市场预期。",
        "international_news_tool_call_count": 0,
    }


def _system_text(messages):
    return messages[0].content


def test_event_study_data_injected_into_prompt(monkeypatch):
    """工具结果应出现在 system prompt 的独立段落中。"""
    calls = []

    class FakeTool:
        def invoke(self, payload):
            calls.append(payload)
            return "## 事件研究系统：相似事件检索结果\n- 预测方向: **利好**"

    monkeypatch.setattr(
        "AI.eventStudy.integration.langgraph_tool.search_similar_events", FakeTool()
    )
    llm = StubLLM()
    node = international_news_analyst.create_international_news_analyst(llm, toolkit=None)
    result = node(_make_state())

    assert result["international_news_report"] == "分析完成"
    system = _system_text(llm.last_messages)
    assert "## 事件研究系统：历史相似事件影响数据" in system
    assert "相似事件检索结果" in system
    # 检索参数：事件文本截取 + 市场基准 + 默认窗口
    assert calls[0]["asset_ticker"] == "000300.SH"
    assert calls[0]["window_type"] == "post_event_5d"
    assert "美联储宣布降息" in calls[0]["event_text"]


def test_event_study_failure_degrades(monkeypatch):
    """工具失败/不可用时写明确提示，节点不阻塞。"""
    class BoomTool:
        def invoke(self, payload):
            raise RuntimeError("PG 不可用")

    monkeypatch.setattr(
        "AI.eventStudy.integration.langgraph_tool.search_similar_events", BoomTool()
    )
    llm = StubLLM()
    node = international_news_analyst.create_international_news_analyst(llm, toolkit=None)
    result = node(_make_state())

    assert result["international_news_report"] == "分析完成"
    system = _system_text(llm.last_messages)
    assert "事件研究系统暂不可用" in system


def test_no_event_report_skips_lookup(monkeypatch):
    """上游无事件文本时跳过检索并写占位提示。"""
    calls = []

    class FakeTool:
        def invoke(self, payload):
            calls.append(payload)
            return "不应被调用"

    monkeypatch.setattr(
        "AI.eventStudy.integration.langgraph_tool.search_similar_events", FakeTool()
    )
    llm = StubLLM()
    node = international_news_analyst.create_international_news_analyst(llm, toolkit=None)
    state = _make_state()
    state["international_event_report"] = "（事件提取数据暂不可用）"
    node(state)

    assert calls == []  # 未调用工具
    assert "跳过事件库检索" in _system_text(llm.last_messages)
