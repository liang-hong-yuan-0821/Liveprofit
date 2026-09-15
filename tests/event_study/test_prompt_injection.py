"""市场层事件证据注入测试（T6 重写，替代 2026-08-18 的固定检索版本）。

T6 后的注入契约：
- 国际**影响**节点（`international_news_analyst`）事件类证据**只消费**
  结构化 `international_events`（`format_events_summary` 渲染，历史统计只引用
  预取字段）；`international_event_report` 全文仅展示，不进提示词；
  节点不注册、不调用事件研究工具（`_load_event_study_data` 已删除）；
- 国际**事件提取**节点（`international_event_extraction`）在 LLM 调用**前**
  完成事件研究预取（`prefetch_event_study_evidence`），以 `event_study_prefetch`
  变量注入系统提示词（统计只来自预取结果）。

不依赖真实 LLM / 真实数据源（数据获取与预取均以 stub 替换）。
"""

import ast
import inspect

from langchain_core.messages import AIMessage, HumanMessage

from AI.marketAgents.analysts import (
    international_event_extraction,
    international_news_analyst,
)

# 事件研究旧固定调用路径（T6 起两个节点均不得再引用）
_LEGACY_TOOL_SYMBOL = "search_similar_events"


class StubLLM:
    """记录每次 invoke 的 messages，按调用序返回预设响应。"""

    def __init__(self, responses, order=None):
        self.responses = list(responses)
        self.calls = []
        self.order = order

    def invoke(self, messages):
        if self.order is not None:
            self.order.append("llm")
        self.calls.append(messages)
        index = min(len(self.calls) - 1, len(self.responses) - 1)
        return AIMessage(content=self.responses[index])

    def system_text(self, index=0):
        return self.calls[index][0].content


def _runtime_imports(module) -> set:
    """模块内 Import/ImportFrom 的目标模块名集合（源码级依赖锁用）。"""
    tree = ast.parse(inspect.getsource(module))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _risk_features(status="ok"):
    """特征层 stub（`build_global_risk_features` 返回值形状）。"""
    return {
        "analysis_date": "2026-08-18",
        "as_of_date": "2026-08-15",
        "lookbacks": [1, 5, 20],
        "derived_metrics": {
            "coverage": {
                "ratio": 1.0 if status == "ok" else 0.25,
                "status": status,
                "missing": [] if status == "ok" else ["us_rates.y10"],
            },
        },
        "data_quality": {"degradation_level": "ok" if status == "ok" else "insufficient"},
    }


_ASSESSMENT_JSON = """```json
{
  "as_of_date": "2026-08-15",
  "risk_appetite": "进攻",
  "systemic_risk": "low",
  "confidence": "medium",
  "evidence": [],
  "event_transmissions": []
}
```"""


def _international_events():
    return [
        {
            "event_scope": "market",
            "fact": "美联储降息 25bp",
            "event_time": "2026-08-14",
            "source": "美联储",
            "affected_scope_refs": ["A 股"],
            "history_match_status": "matched",
            "historical_impact": {
                "sample_count": 8, "weighted_car": 0.0132,
                "win_rate": 0.625, "contaminated_sample_count": 0,
            },
        },
        {
            "event_scope": "market",
            "fact": "中东局势升级",
            "event_time": None,
            "source": "新闻",
            "history_match_status": "unmatched",
            "historical_impact": None,
            "data_quality": {"notes": ["无价格或预期差证据，不宣称市场已定价"]},
        },
    ]


_REPORT_MARKER = "全文报告标记：UNIQUE_REPORT_MARKER（仅展示，不得进提示词）"


def _news_state(events=True, report=True):
    state = {
        "trade_date": "2026-08-18",
        "messages": [HumanMessage(content="开始分析")],
        "international_news_tool_call_count": 0,
    }
    if events:
        state["international_events"] = _international_events()
    if report:
        state["international_event_report"] = _REPORT_MARKER
    return state


# ---------------- 国际影响节点：结构化事件 + 风险价格特征注入 ----------------

def test_structured_events_injected_into_intl_news_prompt(monkeypatch):
    monkeypatch.setattr(
        "AI.dataflows.market_features.build_global_risk_features",
        lambda date: _risk_features(),
    )
    llm = StubLLM([_ASSESSMENT_JSON])
    node = international_news_analyst.create_international_news_analyst(llm, toolkit=None)
    result = node(_news_state())

    system = llm.system_text()
    # 结构化事件块（含预取历史统计与未匹配原因）
    assert "## 结构化事件（历史参考，含局限）" in system
    assert "美联储降息 25bp" in system
    assert "历史统计（预取）：样本 8" in system
    assert "加权 CAR 0.0132" in system
    assert "胜率 62.5%" in system
    assert "中东局势升级" in system
    assert "历史统计：无（匹配状态 unmatched）" in system
    assert "不得表述为当期已发生收益" in system
    # 风险价格特征块
    assert "## 全球风险价格特征" in system
    # 全文报告与旧固定调用块都不进提示词
    assert _REPORT_MARKER not in system
    assert "事件研究系统" not in system
    # 结构化输出（第六章字段）
    assessment = result["global_risk_assessment"]
    assert isinstance(assessment, dict)
    assert assessment["risk_appetite"] == "进攻"
    assert assessment["systemic_risk"] == "low"
    assert assessment["data_quality"]["degradation_level"] == "ok"


def test_missing_structured_events_uses_placeholder(monkeypatch):
    monkeypatch.setattr(
        "AI.dataflows.market_features.build_global_risk_features",
        lambda date: _risk_features(),
    )
    llm = StubLLM([_ASSESSMENT_JSON])
    node = international_news_analyst.create_international_news_analyst(llm, toolkit=None)
    result = node(_news_state(events=False))

    system = llm.system_text()
    assert "无结构化事件输入" in system
    assert "不得凭常识补写事件事实或历史统计" in system
    assert _REPORT_MARKER not in system  # 上游只有全文报告也不得当作事件证据
    assert isinstance(result["global_risk_assessment"], dict)


def test_coverage_insufficient_forces_insufficient(monkeypatch):
    """核心风险价格缺失过半：代码级强制 insufficient（不依赖 LLM 遵守提示词）。"""
    monkeypatch.setattr(
        "AI.dataflows.market_features.build_global_risk_features",
        lambda date: _risk_features(status="insufficient"),
    )
    llm = StubLLM([_ASSESSMENT_JSON])
    node = international_news_analyst.create_international_news_analyst(llm, toolkit=None)
    assessment = node(_news_state())["global_risk_assessment"]

    assert assessment["risk_appetite"] == "信息不足"
    assert assessment["systemic_risk"] == "insufficient"
    assert "强制 insufficient" in assessment["parse_note"]
    assert assessment["data_quality"]["degradation_level"] == "insufficient"


def test_intl_news_node_has_no_event_study_dependency():
    """回归锁：影响节点不再引用事件研究工具/预取模块（源码级依赖检查）。"""
    assert not hasattr(international_news_analyst, "_load_event_study_data")
    for name in _runtime_imports(international_news_analyst):
        assert "eventStudy" not in name, name
    tree = ast.parse(inspect.getsource(international_news_analyst))
    referenced = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    referenced |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert _LEGACY_TOOL_SYMBOL not in referenced


# ---------------- 国际事件提取节点：LLM 前预取注入 ----------------

_IDENTIFICATION_REPORT = """# 已识别的重大事件

| 事件 | 类型 | 判断 | 影响方向 | 置信度 |
| --- | --- | --- | --- | --- |
| 美联储降息 25bp | 货币政策 | 宽松 | 利好 | 60% |
"""


def _extraction_state():
    return {
        "trade_date": "2026-08-18",
        "messages": [HumanMessage(content="开始分析")],
        "international_event_tool_call_count": 0,
    }


def _fake_prefetch(order=None):
    def fake(raw_news, central_bank_calendar, macro_indicators, trade_date):
        if order is not None:
            order.append("prefetch")
        assert "# 宏观新闻" in raw_news  # 预取在 LLM 前消费已获取的数据
        return {
            "event_scope": "market",
            "scope_refs": [],
            "asset_tickers": ["000300.SH"],
            "window_type": "post_event_5d",
            "trade_date": trade_date,
            "status": "ok",
            "candidate_count": 1,
            "candidates": [{
                "candidate_id": "market:1",
                "source": "美联储",
                "title": "美联储降息 25bp",
                "event_time": "2026-08-14",
                "time_precision": "day",
                "history_match_status": "matched",
                "historical_impact": {
                    "sample_count": 8, "weighted_car": 0.0132, "win_rate": 0.625,
                },
            }],
        }

    return fake


def _patch_dataflow(monkeypatch):
    monkeypatch.setattr(
        "AI.dataflows.interface.get_global_macro_news",
        lambda date: "# 宏观新闻\n- 美联储官员放鸽")
    monkeypatch.setattr(
        "AI.dataflows.interface.get_central_bank_calendar", lambda date: "# 央行日历")
    monkeypatch.setattr(
        "AI.dataflows.interface.get_macro_indicators", lambda date: "# 宏观指标")
    monkeypatch.setattr(
        "AI.dataflows.interface.get_commodity_fx_overview", lambda days=10: "# 商品汇率")
    # M8：节点已删除非预取历史检索（`get_event_calendar_history` 不再被调用）。
    # 仍打桩以防残留路径触网；「未被调用」由 test_no_non_prefetch_history_retrieval
    # 用 spy 断言。
    monkeypatch.setattr(
        "AI.dataflows.interface.get_event_calendar_history", lambda desc: "# 历史案例")


def test_prefetch_block_injected_before_llm(monkeypatch):
    order = []
    monkeypatch.setattr(
        "AI.marketAgents.analysts.international_event_extraction"
        ".prefetch_event_study_evidence",
        _fake_prefetch(order),
    )
    _patch_dataflow(monkeypatch)

    llm = StubLLM([_IDENTIFICATION_REPORT, "最终事件提取报告"], order=order)
    node = international_event_extraction.create_international_event_extraction(
        llm, toolkit=None)
    result = node(_extraction_state())

    # 预取先于首次 LLM 调用（T4/T6：LLM 前固定候选/资产/窗口）
    assert order[0] == "prefetch" and order[1] == "llm"

    system = llm.system_text(0)
    assert "事件研究历史统计（预取）" in system
    assert "美联储降息 25bp" in system
    assert "加权 CAR" in system
    assert "不得由模型补写数值" in system
    assert "{event_study_prefetch}" not in system

    # 结构化事件只引用预取统计（matched → 带 historical_impact）
    events = result["international_events"]
    assert len(events) == 1
    assert events[0]["history_match_status"] == "matched"
    assert events[0]["historical_impact"]["weighted_car"] == 0.0132


def test_prefetch_failure_degrades_without_blocking(monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("PG 不可用")

    monkeypatch.setattr(
        "AI.marketAgents.analysts.international_event_extraction"
        ".prefetch_event_study_evidence", boom)
    _patch_dataflow(monkeypatch)

    llm = StubLLM([_IDENTIFICATION_REPORT, "最终事件提取报告"])
    node = international_event_extraction.create_international_event_extraction(
        llm, toolkit=None)
    result = node(_extraction_state())

    system = llm.system_text(0)
    # 预取/渲染异常 → 历史统计块整块为空（M17 残留：异常不得中断节点，
    # 也不得以降级文本冒充统计块；降级原因由 degraded_result 承载，不进 Prompt）
    assert "- 预取状态:" not in system
    assert "{event_study_prefetch}" not in system
    # 事件仍产出，但历史统计不得补写
    assert result["international_events"]
    assert all(e["historical_impact"] is None for e in result["international_events"])


def test_second_llm_call_shares_system_prompt(monkeypatch):
    """两次 LLM 调用共用同一系统提示词（评审 M8）。

    第二次调用此前无系统提示词（messages[0] 是 HumanMessage）——
    「历史统计只引用预取块、不得凭常识补写」的约束对最终报告生成失效。
    两次渲染差异只允许来自被 partial 注入的 `output_format`（识别格式 /
    最终报告格式），提示词本体必须同源。
    """
    monkeypatch.setattr(
        "AI.marketAgents.analysts.international_event_extraction"
        ".prefetch_event_study_evidence",
        _fake_prefetch(),
    )
    _patch_dataflow(monkeypatch)
    llm = StubLLM([_IDENTIFICATION_REPORT, "最终事件提取报告"])
    node = international_event_extraction.create_international_event_extraction(
        llm, toolkit=None)
    node(_extraction_state())

    assert len(llm.calls) == 2
    first, second = llm.system_text(0), llm.system_text(1)
    # 第二次调用首条消息是系统提示词（此前为 HumanMessage）
    assert llm.calls[1][0].type == "system"
    # 同源提示词本体（差异仅在被注入的输出格式模板）
    assert second.startswith(first[:500]), "第二次调用系统提示词应与第一次同源"
    assert "事件研究历史统计（预取）" in second
    assert "{event_study_prefetch}" not in second
    # 输出格式分别为「事件识别」与「最终报告」模板（差异只应来自 output_format）
    assert "事件速览" not in first and "事件速览" in second


def test_no_non_prefetch_history_retrieval(monkeypatch):
    """M8 回归锁：非预取历史检索通道已删除，运行期零调用。

    历史统计只有预取一条来源；检索到的历史案例文本不得再进提示词。
    """
    monkeypatch.setattr(
        "AI.marketAgents.analysts.international_event_extraction"
        ".prefetch_event_study_evidence",
        _fake_prefetch(),
    )
    _patch_dataflow(monkeypatch)
    retrieved = []

    def spy(desc):
        retrieved.append(desc)
        return "# 历史案例标记 UNIQUE_LEGACY_HISTORY"

    monkeypatch.setattr("AI.dataflows.interface.get_event_calendar_history", spy)

    llm = StubLLM([_IDENTIFICATION_REPORT, "最终事件提取报告"])
    node = international_event_extraction.create_international_event_extraction(
        llm, toolkit=None)
    result = node(_extraction_state())

    assert retrieved == [], "非预取历史检索不得再被调用"
    for call in llm.calls:
        assert all("UNIQUE_LEGACY_HISTORY" not in str(m.content) for m in call)
    assert result["international_events"]

    # 源码级：只允许文档字符串提及被删除的检索符号，代码不得再引用
    tree = ast.parse(inspect.getsource(international_event_extraction))
    referenced = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    referenced |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "get_event_calendar_history" not in referenced
    assert "_extract_events_desc" not in referenced


def test_extraction_node_has_no_event_study_tool_dependency():
    """回归锁：事件提取节点不注册/不调用事件研究工具（统计只走 LLM 前预取）。"""
    for name in _runtime_imports(international_event_extraction):
        assert "langgraph_tool" not in name, name
    tree = ast.parse(inspect.getsource(international_event_extraction))
    referenced = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    referenced |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert _LEGACY_TOOL_SYMBOL not in referenced
