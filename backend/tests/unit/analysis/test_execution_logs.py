"""执行日志树读取器单测（tmp_path 构造目录树，无网络/DB）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.modules.analysis.application.execution_logs import (
    build_execution_logs_tree,
)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _build_full_tree(run: Path) -> Path:
    """完整树：新格式 DP + tushare 子调用 + tools + LLM req/res + 旧格式平铺 json。"""
    node = run / "market" / "001_Intl_Analyst"
    _write(node / "req.md", "提示词")
    _write(node / "res.md", "# 结果")
    _write(node / "meta.json", json.dumps({
        "model": "gpt-4o", "node": "Intl Analyst", "seq": 1, "content_length": 100}))
    dp = node / "001_get_data"
    _write(dp / "req.json", json.dumps({"symbol": "000001.SZ"}))
    _write(dp / "res.md", "# 数据")
    _write(dp / "meta.json", json.dumps({
        "name": "get_data", "desc": "获取数据", "seq": 1,
        "ts": "2026-09-06 10:00:00", "res": "res.md"}))
    ts = dp / "tushare" / "001_daily"
    _write(ts / "req.json", json.dumps({"api_name": "daily", "fields": "", "params": {}}))
    _write(ts / "res.json", json.dumps({"columns": ["trade_date"], "shape": [1, 1], "records": []}))
    _write(ts / "meta.json", json.dumps({
        "name": "daily", "seq": 1, "ts": "t", "res": "res.json", "probe": False, "error": False}))
    _write(node / "get_legacy.json", json.dumps({
        "name": "get_legacy", "desc": "旧格式", "req": {"a": 1}, "res": "# 旧结果"}))
    tool = node / "tools" / "001_search"
    _write(tool / "req.json", json.dumps({"name": "search", "input": "x"}))
    _write(tool / "res.txt", "工具结果")
    return run


def _tree(run: Path, **kwargs) -> dict:
    return build_execution_logs_tree(run, **kwargs)


def test_missing_dir_returns_unavailable(tmp_path):
    tree = _tree(tmp_path / "nope")
    assert tree == {"available": False, "layers": []}


def test_full_tree_structure(tmp_path):
    run = _build_full_tree(tmp_path / "run")
    tree = _tree(run)
    assert tree["available"] is True
    assert len(tree["layers"]) == 1

    layer = tree["layers"][0]
    assert layer["name"] == "market"
    assert len(layer["nodes"]) == 1

    node = layer["nodes"][0]
    assert node["dir"] == "market/001_Intl_Analyst"
    assert node["seq"] == 1
    assert node["node"] == "Intl Analyst"
    assert node["model"] == "gpt-4o"
    assert node["meta"]["content_length"] == 100
    assert node["llm_req"] == {
        "path": "market/001_Intl_Analyst/req.md", "kind": "md", "content": "提示词",
        "parse_error": False, "truncated": False, "total_bytes": len("提示词".encode("utf-8"))}
    assert node["llm_res"]["content"] == "# 结果"

    # 新格式 DP 调用
    assert len(node["dp_calls"]) == 2  # 新格式 + 旧格式
    dp = node["dp_calls"][0]
    assert dp["dir"] == "market/001_Intl_Analyst/001_get_data"
    assert dp["name"] == "get_data"
    assert dp["desc"] == "获取数据"
    assert dp["seq"] == 1
    assert dp["ts"] == "2026-09-06 10:00:00"
    assert dp["error"] is False
    assert dp["legacy"] is False
    assert dp["req"] == {"symbol": "000001.SZ"}
    assert dp["res"]["kind"] == "md"
    assert dp["res"]["content"] == "# 数据"

    # tushare 子调用
    assert len(dp["tushare"]) == 1
    ts = dp["tushare"][0]
    assert ts["dir"] == "market/001_Intl_Analyst/001_get_data/tushare/001_daily"
    assert ts["name"] == "daily"
    assert ts["probe"] is False
    assert ts["error"] is False
    assert ts["req"] == {"api_name": "daily", "fields": "", "params": {}}
    assert ts["res"]["kind"] == "json"
    assert ts["res"]["content"] == {"columns": ["trade_date"], "shape": [1, 1], "records": []}

    # 工具调用
    assert len(node["tools"]) == 1
    tool = node["tools"][0]
    assert tool["name"] == "search"
    assert tool["req"] == {"name": "search", "input": "x"}
    assert tool["res"]["content"] == "工具结果"


def test_legacy_dp_call_mapping(tmp_path):
    run = _build_full_tree(tmp_path / "run")
    node = _tree(run)["layers"][0]["nodes"][0]
    legacy = node["dp_calls"][1]
    assert legacy["dir"] is None
    assert legacy["name"] == "get_legacy"
    assert legacy["desc"] == "旧格式"
    assert legacy["legacy"] is True
    assert legacy["req"] == {"a": 1}
    assert legacy["res"]["kind"] == "md"
    assert legacy["res"]["content"] == "# 旧结果"
    assert legacy["res"]["path"] == "market/001_Intl_Analyst/get_legacy.json"


def test_legacy_dict_res_uses_json_kind(tmp_path):
    run = tmp_path / "run"
    node = run / "market" / "001_N"
    _write(node / "x.json", json.dumps({"name": "x", "desc": "d", "req": {}, "res": {"k": "v"}}))
    legacy = _tree(run)["layers"][0]["nodes"][0]["dp_calls"][0]
    assert legacy["res"]["kind"] == "json"
    assert legacy["res"]["content"] == {"k": "v"}


def test_missing_files_are_none(tmp_path):
    """运行中未写完是正常态：req.md/res 缺失 → 字段 None"""
    run = tmp_path / "run"
    node = run / "market" / "001_N"
    node.mkdir(parents=True)
    dp = node / "001_get"
    dp.mkdir()  # 无 req/res/meta
    n = _tree(run)["layers"][0]["nodes"][0]
    assert n["llm_req"] is None
    assert n["llm_res"] is None
    assert n["meta"] is None
    assert n["dp_calls"][0]["req"] is None
    assert n["dp_calls"][0]["res"] is None
    assert n["dp_calls"][0]["name"] == "get"  # meta 缺失 → 目录名推导


def test_bad_json_parse_error(tmp_path):
    run = tmp_path / "run"
    dp = run / "market" / "001_N" / "001_get"
    _write(dp / "req.json", '{"ok": true}')
    _write(dp / "res.json", "{not json")
    _write(dp / "meta.json", json.dumps({"name": "get", "seq": 1, "ts": "t", "res": "res.json"}))
    res = _tree(run)["layers"][0]["nodes"][0]["dp_calls"][0]["res"]
    assert res["kind"] == "json"
    assert res["content"] is None
    assert res["parse_error"] is True
    assert res["truncated"] is False


def test_single_file_over_limit_truncated(tmp_path):
    """单文件超限先于解析判断：content=None、truncated=True、total_bytes=实际大小"""
    run = tmp_path / "run"
    node = run / "market" / "001_N"
    content = "A" * 50
    _write(node / "res.md", content)
    node_dto = _tree(run, max_file_bytes=10)["layers"][0]["nodes"][0]
    res = node_dto["llm_res"]
    assert res["content"] is None
    assert res["truncated"] is True
    assert res["total_bytes"] == 50
    # req.md 缺失不受影响
    assert node_dto["llm_req"] is None


def test_tree_aggregate_limit_truncates_later_content(tmp_path):
    """整树聚合上限：按遍历顺序累计，超限后后续内容文件一律 truncated；裸 dict 不受影响"""
    run = tmp_path / "run"
    node = run / "market" / "001_N"
    _write(node / "req.md", "AAAA")  # 4 字节
    _write(node / "res.md", "BBBB")  # 4 字节
    dp = node / "001_get"
    _write(dp / "req.json", json.dumps({"symbol": "000001.SZ"}))
    _write(dp / "res.md", "CCCC")
    _write(dp / "meta.json", json.dumps({"name": "get", "seq": 1, "ts": "t", "res": "res.md"}))

    tree = _tree(run, max_total_bytes=6)
    node_dto = tree["layers"][0]["nodes"][0]
    # 第 1 个内容文件内嵌（4 ≤ 6），第 2 个跨限 → truncated + 后续全部 truncated
    assert node_dto["llm_req"]["content"] == "AAAA"
    assert node_dto["llm_req"]["truncated"] is False
    assert node_dto["llm_res"]["content"] is None
    assert node_dto["llm_res"]["truncated"] is True
    assert node_dto["llm_res"]["total_bytes"] == 4
    dp_call = node_dto["dp_calls"][0]
    assert dp_call["res"]["truncated"] is True
    # req.json / meta.json 为裸 dict：不计入树级累计、永不树级截断
    assert dp_call["req"] == {"symbol": "000001.SZ"}
    assert dp_call["name"] == "get"  # meta.json 正常读取（budget 不影响裸 dict）
    assert dp_call["seq"] == 1


def test_tree_aggregate_limit_never_truncates_bare_dicts(tmp_path):
    """budget 耗尽后 req 裸 dict 仍内嵌（调用入参是本功能核心）"""
    run = tmp_path / "run"
    node = run / "market" / "001_N"
    _write(node / "req.md", "AAAA")
    _write(node / "res.md", "BBBB")
    dp = node / "001_get"
    _write(dp / "req.json", json.dumps({"symbol": "000001.SZ"}))
    _write(dp / "res.md", "CCCC")
    _write(dp / "meta.json", json.dumps({"name": "get", "seq": 1, "ts": "t", "res": "res.md"}))
    dp_call = _tree(run, max_total_bytes=4)["layers"][0]["nodes"][0]["dp_calls"][0]
    assert dp_call["res"]["truncated"] is True
    assert dp_call["req"] == {"symbol": "000001.SZ"}


def test_four_digit_seq_prefix(tmp_path):
    """seq 超 999 的 4 位前缀目录：seq 解析完整数字段、接口名推导正确"""
    run = tmp_path / "run"
    node = run / "market" / "1000_Long_Run_Node"
    dp = node / "1001_get_data"
    _write(dp / "req.json", "{}")
    _write(dp / "meta.json", json.dumps({"name": "get_data", "seq": 1001, "ts": "t", "res": "res.md"}))
    node_dto = _tree(run)["layers"][0]["nodes"][0]
    assert node_dto["seq"] == 1000
    assert node_dto["dir"] == "market/1000_Long_Run_Node"
    assert node_dto["dp_calls"][0]["seq"] == 1001
    assert node_dto["dp_calls"][0]["name"] == "get_data"


def test_dp_error_flag(tmp_path):
    run = tmp_path / "run"
    dp = run / "market" / "001_N" / "001_get"
    _write(dp / "meta.json", json.dumps({
        "name": "get", "seq": 1, "ts": "t", "res": "res.json", "error": True}))
    _write(dp / "res.json", json.dumps({"error": "boom"}))
    call = _tree(run)["layers"][0]["nodes"][0]["dp_calls"][0]
    assert call["error"] is True
    assert call["res"]["content"] == {"error": "boom"}
