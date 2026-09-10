"""AI/utils/checkpoint.py 单测（单Agent重跑与提示词编辑方案 3.2 存档部分）。

不依赖真实 LLM：序列化/合并/落盘位置/完成标记均为纯函数或 fake 节点。
"""

import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from AI.utils import checkpoint
from AI.utils.checkpoint import (
    _LOOP_ENTRY,
    _merge_updates,
    deserialize_checkpoint,
    guard_checkpoint,
    resolve_entry_checkpoint,
    serialize_state,
    set_checkpoint_run_dir,
    write_complete_marker,
)


@pytest.fixture(autouse=True)
def _reset_run_dir(tmp_path):
    checkpoint.set_checkpoint_run_dir(tmp_path / "run")
    yield
    checkpoint.set_checkpoint_run_dir(None)


def _base_state(**overrides):
    state = {
        "messages": [
            HumanMessage(content="开始交易分析"),
            AIMessage(content="报告一"),
        ],
        "trade_date": "2026-09-08",
        "company_of_interest": "000001.SZ",
        "cn_news_report": "## 〇 事件日历速览\n短线风险：中",
    }
    state.update(overrides)
    return state


def test_serialize_deserialize_roundtrip_with_tool_calls():
    """messages（含 AIMessage tool_calls）往返还原；其余字段纯 JSON。"""
    ai = AIMessage(
        content="调用工具",
        tool_calls=[{"name": "get_ipo_calendar", "args": {"date": "2026-09-08"}, "id": "call_1"}],
    )
    state = _base_state(messages=[HumanMessage(content="hi"), ai])
    payload = serialize_state(state, "market:CN News Analyst")
    # 纯 JSON 可 dump/load（无 LangChain 对象泄漏）
    json.dumps(payload, ensure_ascii=False)
    restored = deserialize_checkpoint(_write_tmp(payload))
    assert len(restored["messages"]) == 2
    assert restored["messages"][0].type == "human"
    assert restored["messages"][1].tool_calls[0]["name"] == "get_ipo_calendar"
    assert restored["cn_news_report"] == state["cn_news_report"]


def test_serialize_drops_internal_keys():
    """_ 前缀键与 prompt_overrides 不进快照。"""
    state = _base_state(
        _rerun_from="market:CN Tech Analyst",
        _current_node_id="market:CN News Analyst",
        prompt_overrides={"market:CN News Analyst": "覆盖"},
    )
    payload = serialize_state(state, "market:CN News Analyst")
    for key in ("_rerun_from", "_current_node_id", "prompt_overrides"):
        assert key not in payload["state"]


def test_merge_updates_concatenates_messages():
    """对齐 add_messages reducer：messages 拼接、其余键覆盖。"""
    state = _base_state()
    result = {"messages": [AIMessage(content="新报告")], "cn_news_report": "新版"}
    merged = _merge_updates(state, result)
    assert len(merged["messages"]) == 3
    assert merged["cn_news_report"] == "新版"


def test_guard_checkpoint_writes_layer_and_ticker_paths(tmp_path):
    """market 节点落 checkpoints/market/{sanitized}.json；stock 节点落
    checkpoints/stock/{ticker}/{sanitized}.json。"""
    calls = []

    def _fake_node(state):
        calls.append(1)
        return {"messages": [AIMessage(content="输出")]}

    run_dir = tmp_path / "run"
    checkpoint.set_checkpoint_run_dir(run_dir)

    guard_checkpoint("CN News Analyst")(_fake_node)(_base_state())
    market_file = run_dir / "checkpoints" / "market" / "CN_News_Analyst.json"
    assert market_file.exists()

    guard_checkpoint("Bull Researcher")(_fake_node)(_base_state())
    stock_file = run_dir / "checkpoints" / "stock" / "000001.SZ" / "Bull_Researcher.json"
    assert stock_file.exists()


def test_screening_mode_skips_stock_checkpoint(tmp_path):
    """screening 任务（v1 禁重跑个股层）stock 层节点不落盘；market 层仍落盘。"""
    run_dir = tmp_path / "run"
    checkpoint.set_checkpoint_run_dir(run_dir)

    def _fake_node(state):
        return {"messages": [AIMessage(content="输出")]}

    state = _base_state(selected_layers=["market", "sector", "screening"])
    guard_checkpoint("Bull Researcher")(_fake_node)(dict(state))
    assert not (run_dir / "checkpoints" / "stock").exists()

    guard_checkpoint("CN News Analyst")(_fake_node)(dict(state))
    assert (run_dir / "checkpoints" / "market" / "CN_News_Analyst.json").exists()


def test_write_complete_marker(tmp_path):
    """完成标记原子写（temp+rename 后目标文件存在、无 .tmp 残留）。"""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_complete_marker(run_dir)
    marker = run_dir / "complete.json"
    assert marker.exists()
    assert json.loads(marker.read_text(encoding="utf-8"))["completed_at"]
    assert not list(run_dir.glob("*.tmp"))


def test_deserialize_bad_json_raises():
    """坏 JSON 抛 JSONDecodeError（由调用方转 FatalAnalysisError，明确报错）。"""
    bad = Path(str(Path.cwd() / "logs" / "nope"))  # 占位，仅构造截断内容
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "bad.json"
        p.write_text('{"saved_at": "x", "state": {"messages": [{"type": "human"', encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            deserialize_checkpoint(p)


def _write_tmp(payload: dict) -> Path:
    import tempfile

    tmp = Path(tempfile.mkdtemp()) / "cp.json"
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return tmp
