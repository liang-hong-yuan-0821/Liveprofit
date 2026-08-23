"""
调试步进门控（AI/utils/step_gate.py）单测：
enable/checkpoint/disable 状态机、文件协议（waiting → confirmed/skip_all）、
坏 JSON 容错、dp wrapper 集成、LLM 检查点（llm_req/llm_res + unknown 过滤）。

注意：step_gate 与 llm_callbacks._run 均为全局单例，
用例通过 autouse fixture + 显式 reset/disable 隔离状态。
"""

import json
import threading
import time
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from AI.utils import step_gate
from AI.utils.dataprovider_log import _current_node, _write, dataprovider_log
from AI.utils.llm_callbacks import LLMCallbackHandler, _RunState, _run


@pytest.fixture(autouse=True)
def _isolate_gate():
    """每个用例前后重置门控状态，防跨用例泄漏"""
    step_gate.disable()
    yield
    step_gate.disable()


def _confirm_once(run_dir: Path, status: str = "confirmed",
                  delay: float = 0.7) -> threading.Thread:
    """子线程模拟页面：延迟后把检查点文件 status 改写"""
    def _do():
        time.sleep(delay)
        f = run_dir / step_gate.CHECKPOINT_FILE
        payload = json.loads(f.read_text(encoding="utf-8"))
        payload["status"] = status
        f.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    t = threading.Thread(target=_do, daemon=True)
    t.start()
    return t


def _cp(tmp_path: Path) -> dict:
    return json.loads((tmp_path / step_gate.CHECKPOINT_FILE)
                      .read_text(encoding="utf-8"))


# ---- 基础状态机 ----

def test_disabled_fast_path(tmp_path):
    """未启用：checkpoint 立即返回且不写文件"""
    assert not step_gate.is_enabled()
    start = time.monotonic()
    step_gate.checkpoint("dp", layer="market", node="n", name="get_x",
                         dir="d", show_file="res.md")
    assert time.monotonic() - start < 0.1
    assert not (tmp_path / step_gate.CHECKPOINT_FILE).exists()


def test_checkpoint_waiting_payload_and_confirm(tmp_path):
    """waiting 载荷字段完整；页面确认后返回"""
    step_gate.enable(tmp_path)
    assert step_gate.is_enabled()
    t = _confirm_once(tmp_path)
    start = time.monotonic()
    step_gate.checkpoint("dp", layer="sector", node="Sector News Analyst",
                         name="get_x", dir="sector/001_x/001_get_x",
                         show_file="res.md")
    elapsed = time.monotonic() - start
    t.join()
    assert elapsed >= 0.5  # 至少经历一轮轮询
    payload = _cp(tmp_path)
    assert payload["kind"] == "dp"
    assert payload["status"] == "confirmed"
    assert payload["seq"] == 1
    assert payload["layer"] == "sector"
    assert payload["node"] == "Sector News Analyst"
    assert payload["name"] == "get_x"
    assert payload["dir"] == "sector/001_x/001_get_x"
    assert payload["show_file"] == "res.md"
    assert payload["ts"]


def test_skip_all_releases_subsequent(tmp_path):
    """skip_all：放行后续全部检查点（立即返回且不再写文件）"""
    step_gate.enable(tmp_path)
    t = _confirm_once(tmp_path, status="skip_all")
    step_gate.checkpoint("dp", layer="m", node="n", name="x",
                         dir="d", show_file="r.md")
    t.join()

    f = tmp_path / step_gate.CHECKPOINT_FILE
    f.unlink()
    start = time.monotonic()
    step_gate.checkpoint("dp", layer="m", node="n", name="x",
                         dir="d", show_file="r.md")
    assert time.monotonic() - start < 0.1
    assert not f.exists()


def test_bad_json_keeps_waiting(tmp_path):
    """坏 JSON/文件缺失视为仍等待：先破坏文件再确认，最终放行"""
    step_gate.enable(tmp_path)
    f = tmp_path / step_gate.CHECKPOINT_FILE

    def _corrupt_then_confirm():
        time.sleep(0.6)
        f.write_text("not-json", encoding="utf-8")
        time.sleep(1.0)
        f.write_text('{"status": "confirmed"}', encoding="utf-8")

    t = threading.Thread(target=_corrupt_then_confirm, daemon=True)
    t.start()
    start = time.monotonic()
    step_gate.checkpoint("dp", layer="m", node="n", name="x",
                         dir="d", show_file="r.md")
    elapsed = time.monotonic() - start
    t.join()
    assert elapsed >= 1.4  # 坏 JSON 阶段持续等待，未被当作确认


def test_disable_stops_checkpoints(tmp_path):
    """disable 后 checkpoint 立即返回且不写文件"""
    step_gate.enable(tmp_path)
    step_gate.disable()
    start = time.monotonic()
    step_gate.checkpoint("dp", layer="m", node="n", name="x",
                         dir="d", show_file="r.md")
    assert time.monotonic() - start < 0.1
    assert not (tmp_path / step_gate.CHECKPOINT_FILE).exists()


# ---- _write 返回值 ----

def test_write_returns_call_dir_and_res_file(tmp_path):
    """_write 返回 (call_dir, res_file)：str → res.md，dict → res.json"""
    _run.reset(tmp_path)
    token = _current_node.set("International Event Extraction Analyst")
    try:
        written = _write("get_x", "获取x", {}, {"a": 1})
        assert written is not None
        call_dir, res_file = written
        assert res_file == "res.json"
        assert call_dir.name == "001_get_x"

        written = _write("get_y", "获取y", {}, "# y")
        assert written[1] == "res.md"
        assert written[0].name == "002_get_y"
    finally:
        _current_node.reset(token)


def test_write_uninitialized_returns_none(monkeypatch):
    """日志目录未初始化 → _write 返回 None（wrapper 跳过检查点）"""
    monkeypatch.setattr("AI.utils.dataprovider_log._run", _RunState())
    assert _write("get_x", "", {}, "# x") is None


# ---- dp wrapper 检查点集成 ----

def test_dp_wrapper_checkpoint(tmp_path):
    """wrapper 落盘后挂 dp 检查点：目录首段作 layer，等待页面确认"""
    _run.reset(tmp_path)
    token = _current_node.set("Sector News Analyst")
    step_gate.enable(tmp_path)
    try:
        t = _confirm_once(tmp_path)
        start = time.monotonic()

        @dataprovider_log
        def fake_get(x: str) -> str:
            """获取x"""
            return "# x"

        assert fake_get("a") == "# x"
        assert time.monotonic() - start >= 0.5
        t.join()
        payload = _cp(tmp_path)
        assert payload["kind"] == "dp"
        assert payload["status"] == "confirmed"
        assert payload["layer"] == "sector"
        assert payload["node"] == "Sector News Analyst"
        assert payload["name"] == "fake_get"
        assert payload["dir"] == "sector/001_Sector_News_Analyst/001_fake_get"
        assert payload["show_file"] == "res.md"
    finally:
        _current_node.reset(token)
        step_gate.disable()


def test_dp_wrapper_checkpoint_off_writes_no_file(tmp_path):
    """步进关闭：wrapper 正常落盘但不产生检查点文件"""
    _run.reset(tmp_path)
    token = _current_node.set("Sector News Analyst")
    try:
        @dataprovider_log
        def fake_get(x: str) -> str:
            """获取x"""
            return "# x"

        fake_get("a")
        assert not (tmp_path / step_gate.CHECKPOINT_FILE).exists()
        d = tmp_path / "sector" / "001_Sector_News_Analyst" / "001_fake_get"
        assert (d / "res.md").exists()  # 日志照常落盘
    finally:
        _current_node.reset(token)


# ---- LLM 检查点 ----

def test_llm_call_checkpoints(tmp_path):
    """on_llm_start 挂 llm_req、on_llm_end 挂 llm_res，均等待确认后继续"""
    handler = LLMCallbackHandler()
    handler.set_log_dir(tmp_path)
    step_gate.enable(tmp_path)

    def _confirm_two():
        for _ in range(2):
            time.sleep(0.8)
            f = tmp_path / step_gate.CHECKPOINT_FILE
            payload = json.loads(f.read_text(encoding="utf-8"))
            payload["status"] = "confirmed"
            f.write_text(json.dumps(payload, ensure_ascii=False),
                         encoding="utf-8")

    t = threading.Thread(target=_confirm_two, daemon=True)
    t.start()
    start = time.monotonic()
    handler.on_llm_start(
        serialized={"kwargs": {"model": "m"}},
        prompts=["p"],
        run_id="r1",
        metadata={"langgraph_node": "Sector News Analyst"},
    )
    handler.on_llm_end(
        LLMResult(generations=[[ChatGeneration(message=AIMessage(content="# 结果"))]]),
        run_id="r1",
    )
    elapsed = time.monotonic() - start
    t.join()
    assert elapsed >= 1.5  # 两次确认
    payload = _cp(tmp_path)
    assert payload["kind"] == "llm_res"
    assert payload["status"] == "confirmed"
    assert payload["seq"] == 2
    assert payload["layer"] == "sector"
    assert payload["show_file"] == "res.md"
    assert payload["dir"] == "sector/001_Sector_News_Analyst"


def test_llm_unknown_node_no_checkpoint(tmp_path):
    """图外 LLM 调用（节点名 unknown，如决策抽取/反思）不产生检查点"""
    handler = LLMCallbackHandler()
    handler.set_log_dir(tmp_path)
    step_gate.enable(tmp_path)

    start = time.monotonic()
    handler.on_llm_start(
        serialized={"kwargs": {"model": "m"}},
        prompts=["p"],
        run_id="r1",
        metadata={},  # 无 langgraph_node → node=unknown
    )
    handler.on_llm_end(
        LLMResult(generations=[[ChatGeneration(message=AIMessage(content="x"))]]),
        run_id="r1",
    )
    assert time.monotonic() - start < 0.5  # 不阻塞
    assert not (tmp_path / step_gate.CHECKPOINT_FILE).exists()
