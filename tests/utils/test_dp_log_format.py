"""
dataprovider 日志新格式测试：
{agent_dir}/{seq:03d}_{接口名}/req.json + res.md|res.json + meta.json

注意：_run 是全局单例，用例通过 fixture 内 reset() 隔离状态。
"""

import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from AI.utils.dataprovider_log import _current_node, _write, dataprovider_log
from AI.utils.llm_callbacks import LLMCallbackHandler, _RunState, _run


@pytest.fixture
def dp_log_env(tmp_path):
    """初始化日志目录 + 节点上下文（隔离全局单例 _run 与 contextvar）"""
    _run.reset(tmp_path)
    token = _current_node.set("International Event Extraction Analyst")
    yield tmp_path
    _current_node.reset(token)


def _agent_dir(tmp_path) -> Path:
    return Path(tmp_path) / "market" / "001_International_Event_Extraction_Analyst"


def test_repeated_calls_no_overwrite(dp_log_env):
    """同一接口调用两次 → 001_/002_ 两个目录，互不覆盖"""
    tmp_path = dp_log_env

    @dataprovider_log
    def fake_get_macro(sec_name: str, days: int = 10) -> str:
        """获取宏观数据"""
        return f"# 宏观 {sec_name} {days}"

    fake_get_macro("CPI")
    fake_get_macro("PPI", days=20)

    agent_dir = _agent_dir(tmp_path)
    d1, d2 = agent_dir / "001_fake_get_macro", agent_dir / "002_fake_get_macro"
    assert d1.is_dir() and d2.is_dir()

    # 不再产生旧格式平铺 json
    assert not (agent_dir / "fake_get_macro.json").exists()

    # req.json 含绑定后的完整入参（含默认值）
    req1 = json.loads((d1 / "req.json").read_text(encoding="utf-8"))
    assert req1 == {"sec_name": "CPI", "days": 10}
    req2 = json.loads((d2 / "req.json").read_text(encoding="utf-8"))
    assert req2 == {"sec_name": "PPI", "days": 20}

    # res.md 内容与返回值一致
    assert (d1 / "res.md").read_text(encoding="utf-8") == "# 宏观 CPI 10"
    assert (d2 / "res.md").read_text(encoding="utf-8") == "# 宏观 PPI 20"
    assert not (d1 / "res.json").exists()

    # meta.json 字段
    meta1 = json.loads((d1 / "meta.json").read_text(encoding="utf-8"))
    assert meta1["name"] == "fake_get_macro"
    assert meta1["desc"] == "获取宏观数据"
    assert meta1["seq"] == 1
    assert meta1["res"] == "res.md"
    assert isinstance(meta1["ts"], str) and meta1["ts"]

    meta2 = json.loads((d2 / "meta.json").read_text(encoding="utf-8"))
    assert meta2["seq"] == 2


def test_dict_result_writes_res_json(dp_log_env):
    """非 str 结果（如 get_stock_info 返回 dict）→ res.json + meta 标记"""
    tmp_path = dp_log_env

    @dataprovider_log
    def fake_get_info(sec: str) -> dict:
        """获取股票信息"""
        return {"name": "平安银行", "code": sec}

    fake_get_info("000001.SZ")

    d = _agent_dir(tmp_path) / "001_fake_get_info"
    assert (d / "res.json").exists()
    assert not (d / "res.md").exists()
    res = json.loads((d / "res.json").read_text(encoding="utf-8"))
    assert res == {"name": "平安银行", "code": "000001.SZ"}
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    assert meta["res"] == "res.json"


def test_chinese_interface_name(dp_log_env):
    """中文接口名：_sanitize 保留 CJK，目录名正常"""
    tmp_path = dp_log_env
    _write("获取央行日历", "获取央行日历", {"curr_date": "2026-08-20"}, "# 日历\n- 无")
    d = _agent_dir(tmp_path) / "001_获取央行日历"
    assert d.is_dir()
    assert (d / "res.md").exists()


def test_reset_restarts_seq(dp_log_env, tmp_path):
    """reset 后 dp_counters 清空，新 run 序号从 001 重新开始"""
    _write("get_x", "获取x", {}, "# x1")
    _write("get_x", "获取x", {}, "# x2")
    assert (_agent_dir(tmp_path) / "002_get_x").is_dir()

    new_log_dir = Path(tmp_path) / "new_run"
    _run.reset(new_log_dir)
    token = _current_node.set("International Event Extraction Analyst")
    try:
        _write("get_x", "获取x", {}, "# x1")
    finally:
        _current_node.reset(token)
    new_agent = new_log_dir / "market" / "001_International_Event_Extraction_Analyst"
    assert (new_agent / "001_get_x").is_dir()
    assert not (new_agent / "002_get_x").exists()


def test_agent_dir_reuses_last_llm_dir(tmp_path):
    """归属分支 1：节点已有 LLM 调用 → dp 日志复用其目录，计数器按该目录编号"""
    handler = LLMCallbackHandler()
    handler.set_log_dir(tmp_path)
    handler.on_llm_start(
        serialized={"kwargs": {"model": "m"}},
        prompts=["p"],
        run_id="r1",
        metadata={"langgraph_node": "Sector News Analyst"},
    )
    token = _current_node.set("Sector News Analyst")
    try:
        _write("get_x", "获取x", {}, "# x1")
        _write("get_x", "获取x", {}, "# x2")
    finally:
        _current_node.reset(token)

    d = tmp_path / "sector" / "001_Sector_News_Analyst"
    assert (d / "001_get_x").is_dir()
    assert (d / "002_get_x").is_dir()
    # 没有写入预测目录（LLM 实际目录 001_ 被正确复用）
    assert not (tmp_path / "sector" / "002_Sector_News_Analyst").exists()


def test_agent_dir_fallback_no_node_context(tmp_path):
    """归属分支 3：无节点上下文 → 回退最近一次 LLM 目录"""
    handler = LLMCallbackHandler()
    handler.set_log_dir(tmp_path)
    handler.on_llm_start(
        serialized={"kwargs": {"model": "m"}},
        prompts=["p"],
        run_id="r1",
        metadata={"langgraph_node": "Sector News Analyst"},
    )
    # 不设 _current_node → 回退 last_llm_dir
    _write("get_x", "获取x", {}, "# x1")
    d = tmp_path / "sector" / "001_Sector_News_Analyst"
    assert (d / "001_get_x").is_dir()


def test_uninitialized_log_dir_skips(monkeypatch):
    """日志目录未初始化 → 跳过写入，不抛异常"""

    @dataprovider_log
    def fake_get(sec: str) -> str:
        """获取数据"""
        return "# 数据"

    monkeypatch.setattr("AI.utils.dataprovider_log._run", _RunState())
    assert fake_get("000001.SZ") == "# 数据"  # 正常返回，日志静默跳过


def test_llm_handler_dir_layout(tmp_path):
    """LLMCallbackHandler 的 req.md/res.md/meta.json 布局不受 dataprovider 改动影响"""
    handler = LLMCallbackHandler()
    handler.set_log_dir(tmp_path)
    handler.on_llm_start(
        serialized={"kwargs": {"model": "test-model"}},
        prompts=["你好"],
        run_id="r1",
        metadata={"langgraph_node": "Sector News Analyst"},
    )
    msg = AIMessage(content="# 结果")
    handler.on_llm_end(LLMResult(generations=[[ChatGeneration(message=msg)]]), run_id="r1")

    d = tmp_path / "sector" / "001_Sector_News_Analyst"
    assert (d / "req.md").read_text(encoding="utf-8") == "你好"
    assert (d / "res.md").read_text(encoding="utf-8") == "# 结果"
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    assert meta["model"] == "test-model"
    assert meta["content_length"] == len("# 结果")
    assert meta["run_id"] == "r1"
