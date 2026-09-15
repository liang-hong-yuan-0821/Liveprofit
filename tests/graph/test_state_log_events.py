"""
单元测试：状态日志中的三级结构化事件（评审 m11）

`_log_state` 此前只记录各层报告文本，`international_events` / `sector_events` /
`stock_events` 三级结构化事件不落日志（复盘时无法回看事件路由产物）。
修复后：三键均直存 dict 列表，各 ≤5 条（与预取上限同口径，防日志膨胀）。

不初始化 LLM / 不触网：用 `__new__` 造实例，日志目录重定向到 tmp_path。
"""

import json

from AI.graph.trading_graph import TradingAgentsGraph, _log_event_list


def _graph():
    graph = TradingAgentsGraph.__new__(TradingAgentsGraph)  # 绕过 LLM 初始化
    graph.log_states_dict = {}
    graph.ticker = "000001.SH"
    return graph


def test_log_event_list_truncates_and_filters():
    """≤5 条截断；非 list / 非 dict 项安全丢弃（日志不得因脏数据失败）。"""
    events = [{"event_id": f"event:{i}"} for i in range(8)]
    assert _log_event_list(events) == events[:5]
    assert _log_event_list([{"a": 1}, "脏数据", 3]) == [{"a": 1}]
    assert _log_event_list(None) == []
    assert _log_event_list("不是列表") == []


def test_log_state_persists_three_tier_events(monkeypatch, tmp_path):
    """三键写入 state_log.json，结构可 JSON 序列化且保留路由字段。"""
    monkeypatch.chdir(tmp_path)
    graph = _graph()
    final_state = {
        "trade_date": "2026-08-18",
        "international_events": [
            {"event_id": "event:1", "event_scope": "market", "title": "降准"}],
        "sector_events": [
            {"event_id": "event:2", "event_scope": "sector",
             "affected_scope_refs": ["SW:801080"]}],
        "stock_events": [],
    }
    graph._log_state("2026-08-18", final_state)

    written = json.loads(
        (tmp_path / "results" / "000001.SH" / "analysis_logs" / "state_log.json")
        .read_text(encoding="utf-8"))
    entry = written["2026-08-18"]
    assert entry["international_events"] == final_state["international_events"]
    assert entry["sector_events"] == final_state["sector_events"]
    assert entry["stock_events"] == []
    # 缺失字段落默认值（不得 KeyError）
    assert entry["risk_gate"] == ""
    assert entry["market_regime"] == {}
