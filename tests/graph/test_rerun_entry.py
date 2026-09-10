"""entry checkpoint 回溯解析 + rerun_from_node 入口单测（方案 3.2）。

不依赖真实 LLM：resolve 为纯文件系统函数；rerun_from_node 用 mock
_propagate_inner 验证元数据落盘与状态注入。
"""

import json
from unittest import mock

import pytest

from AI.graph.topology import build_topology
from AI.graph.trading_graph import TradingAgentsGraph
from AI.utils import checkpoint
from AI.utils.checkpoint import resolve_entry_checkpoint, save_init_state

_TOPOLOGY = build_topology(("market", "sector", "screening", "stock"))


@pytest.fixture(autouse=True)
def _reset_run_dir():
    checkpoint.set_checkpoint_run_dir(None)
    yield
    checkpoint.set_checkpoint_run_dir(None)


def _make_cp(run_dir, node_id, ticker=None, bad=False):
    """在 run_dir 下伪造节点 checkpoint 文件。"""
    from AI.utils.llm_callbacks import _sanitize

    layer, _, label = node_id.partition(":")
    rel = run_dir / "checkpoints" / layer
    if layer == "stock":
        rel = rel / (ticker or "000001.SZ")
    path = rel / f"{_sanitize(label)}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if bad:
        path.write_text('{"state": {"messages": [{"type": "human"', encoding="utf-8")
    else:
        path.write_text(json.dumps(
            {"saved_at": "x", "node_id": node_id, "state": {"messages": []}},
            ensure_ascii=False,
        ), encoding="utf-8")
    return path


def _make_init(run_dir, rerun=False):
    run_dir.mkdir(parents=True, exist_ok=True)
    init = run_dir / "checkpoints" / "__init__.json"
    init.parent.mkdir(parents=True, exist_ok=True)
    init.write_text(json.dumps({"saved_at": "x", "node_id": "__init__", "state": {}}),
                    encoding="utf-8")
    if rerun:
        (run_dir / "rerun.json").write_text("{}", encoding="utf-8")


def test_resolve_linear_predecessor(tmp_path):
    """线性链：目标前驱 checkpoint 直接命中。"""
    run_dir = tmp_path / "1"
    _make_cp(run_dir, "market:CN News Analyst")
    path = resolve_entry_checkpoint([run_dir], _TOPOLOGY, "market:CN Tech Analyst")
    assert path is not None and path.name == "CN_News_Analyst.json"


def test_resolve_backtracks_to_older_dir(tmp_path):
    """新目录只有下游 checkpoint → 上游前驱命中更旧目录（不逐目录兜底）。"""
    new_dir, old_dir = tmp_path / "2", tmp_path / "1"
    _make_cp(new_dir, "market:CN Tech Analyst")   # 新目录：目标自己，非前驱
    _make_cp(old_dir, "market:CN News Analyst")   # 旧目录：前驱
    path = resolve_entry_checkpoint([new_dir, old_dir], _TOPOLOGY, "market:CN Tech Analyst")
    assert path is not None
    assert "1" in path.parts and path.name == "CN_News_Analyst.json"


def test_resolve_first_node_prefers_complete_run_init(tmp_path):
    """首节点：最近完整执行目录（无 rerun.json）的 __init__；rerun 目录 __init__ 不被误用。"""
    rerun_dir, full_dir = tmp_path / "2", tmp_path / "1"
    _make_init(rerun_dir, rerun=True)   # rerun 目录：__init__ 为 merged 态，禁用
    _make_init(full_dir)
    path = resolve_entry_checkpoint(
        [rerun_dir, full_dir], _TOPOLOGY,
        "market:International Event Extraction Analyst",
    )
    assert path is not None
    assert "1" in path.parts and path.name == "__init__.json"


def test_resolve_none_when_exhausted(tmp_path):
    """目录链耗尽 → None（重跑不可用）。"""
    path = resolve_entry_checkpoint([tmp_path / "1"], _TOPOLOGY, "market:CN Tech Analyst")
    assert path is None


def test_resolve_loop_member_upshifts_to_loop_entry(tmp_path):
    """环成员目标：entry 解析上移环入口（Bear → Bull 前驱 Fundamentals）。"""
    run_dir = tmp_path / "1"
    _make_cp(run_dir, "stock:Fundamentals Analyst", ticker="000001.SZ")
    path = resolve_entry_checkpoint(
        [run_dir], _TOPOLOGY, "stock:Bear Researcher", ticker="000001.SZ")
    assert path is not None and path.name == "Fundamentals_Analyst.json"


def test_rerun_from_node_writes_markers_and_injects_state(tmp_path, monkeypatch):
    """rerun_from_node：rerun.json/__init__.json 落盘、_rerun_from 注入 effective、
    prompt_overrides 快照生效、走 _propagate_inner。"""
    run_dir = tmp_path / "tasks" / "t" / "2"
    init_state = {
        "platform_log_dir": str(run_dir),
        "attempt_no": 2,
        "task_id": "t",
        "selected_layers": ["market"],
        "trade_date": "2026-09-08",
        "company_of_interest": "000001.SZ",
        "prompt_overrides": {"market:CN News Analyst": "自定义"},
    }
    checkpoint_state = {
        "messages": [],
        "trade_date": "2026-09-08",
        "company_of_interest": "000001.SZ",
        "selected_layers": ["market"],
        "cn_news_report": "旧报告",
    }

    graph = TradingAgentsGraph(
        selectedLayer=["market"],
        config={"api_key": "fake-key-for-test", "memory_enabled": False},
    )
    captured = {}
    with mock.patch.object(
        graph, "_propagate_inner",
        side_effect=lambda state, log_dir, debug, ctx, cb: captured.update(
            {"state": dict(state), "log_dir": log_dir}) or ({"ok": True}, {"action": "持有"}),
    ):
        final, decision = graph.rerun_from_node(
            init_state, checkpoint_state, "market:CN News Analyst")

    # rerun.json 元数据
    marker = json.loads((run_dir / "rerun.json").read_text(encoding="utf-8"))
    assert marker == {"rerun_from": "market:CN News Analyst", "base_attempt": 1}
    # __init__.json 落盘且不含 _rerun_from
    init = json.loads((run_dir / "checkpoints" / "__init__.json").read_text(encoding="utf-8"))
    assert init["node_id"] == "__init__"
    assert "_rerun_from" not in init["state"]
    # _propagate_inner 收到的 state：注入 _rerun_from、覆盖快照已生效
    assert captured["state"]["_rerun_from"] == "market:CN News Analyst"
    assert captured["state"]["platform_log_dir"] == str(run_dir)
    from AI.utils import prompts
    assert prompts.get_override("market:CN News Analyst") == "自定义"
    prompts.set_overrides(None)
