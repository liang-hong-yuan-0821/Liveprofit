"""执行日志树读取器（纯函数，无 DB/IO 框架依赖）。

复用 AI.logviewer.logs_reader 的目录枚举（list_layers/list_nodes/list_dp_calls/
list_tushare_calls/list_tools/read_json/read_text/parse_legacy），不在后端复制树遍历
逻辑。日志目录磁盘格式见 AI/utils/llm_callbacks.py 与 dataprovider_log.py 模块 docstring。

两级内容上限（任务执行调用日志方案 §3.2.1）：
- 单文件 EXECUTION_LOG_CONTENT_MAX_BYTES（100KB）：所有落盘文件一致生效；
  JSON 内容不做「截一半的 JSON」。
- 整树聚合 EXECUTION_LOG_TREE_MAX_BYTES（5MB）：按层序（sort_layers）→ 节点序
  遍历，逐节点内按 llm_req → llm_res → tools res → dp res/tushare res 的顺序累计
  已内嵌内容字节数，仅 ExecutionFileDTO 承载的内容文件计入；req.json/meta.json
  等裸 dict 字段不计入、永不树级截断（调用入参，大小天然小）。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from AI.logviewer import logs_reader

EXECUTION_LOG_CONTENT_MAX_BYTES = 100 * 1024
EXECUTION_LOG_TREE_MAX_BYTES = 5 * 1024 * 1024

# 目录名数字前缀（001_xxx / 1000_xxx），seq 解析与接口名推导共用
_SEQ_PREFIX_RE = re.compile(r"^(\d+)_")
_EXT_KINDS = {".md": "md", ".json": "json", ".txt": "txt"}


class _TreeBudget:
    """整树聚合上限：按遍历顺序累计内嵌字节数，超限后一律截断。"""

    def __init__(self, max_total_bytes: int) -> None:
        self._remaining = max_total_bytes
        self._exhausted = False

    def try_embed(self, size: int) -> bool:
        """该内容是否可内嵌；首个跨限文件自身也不内嵌（内嵌即超限）。"""
        if self._exhausted or size > self._remaining:
            self._exhausted = True
            return False
        self._remaining -= size
        return True


def build_execution_logs_tree(
    run_dir: Path,
    *,
    max_file_bytes: int = EXECUTION_LOG_CONTENT_MAX_BYTES,
    max_total_bytes: int = EXECUTION_LOG_TREE_MAX_BYTES,
) -> dict[str, Any]:
    """构建任务日志目录的完整树（layer → 节点 → DP/tushare/tools/LLM）。

    返回可序列化 dict（{"available", "layers"}），router 层转 DTO。
    目录不存在/不可读 → available=false（PENDING 尚未建目录是正常态，不是错误）。
    """
    budget = _TreeBudget(max_total_bytes)
    if not run_dir.is_dir():
        return {"available": False, "layers": []}

    layers = []
    for layer_dir in logs_reader.sort_layers(logs_reader.list_layers(run_dir)):
        nodes = [
            _build_node(run_dir, node_dir, max_file_bytes, budget)
            for node_dir in logs_reader.list_nodes(layer_dir)
        ]
        layers.append({"name": layer_dir.name, "nodes": nodes})
    return {"available": True, "layers": layers}


def _build_node(run_dir: Path, node_dir: Path, max_file_bytes: int, budget: _TreeBudget) -> dict:
    meta = _read_bare_dict(node_dir / "meta.json", max_file_bytes) or {}
    return {
        "dir": _rel(run_dir, node_dir),
        "seq": _parse_seq_prefix(node_dir.name),
        "node": meta.get("node"),
        "model": meta.get("model"),
        "meta": meta or None,
        "llm_req": _read_content_file(run_dir, node_dir / "req.md", max_file_bytes, budget),
        "llm_res": _read_content_file(run_dir, node_dir / "res.md", max_file_bytes, budget),
        "tools": _build_tools(run_dir, node_dir, max_file_bytes, budget),
        "dp_calls": _build_dp_calls(run_dir, node_dir, max_file_bytes, budget),
    }


def _build_dp_calls(run_dir: Path, node_dir: Path, max_file_bytes: int, budget: _TreeBudget) -> list:
    calls = []
    for kind, p in logs_reader.list_dp_calls(node_dir):
        if kind == "new":
            calls.append(_build_dp_call(run_dir, p, max_file_bytes, budget))
        else:
            calls.append(_build_legacy_dp_call(run_dir, p, max_file_bytes, budget))
    return calls


def _build_dp_call(run_dir: Path, dp_dir: Path, max_file_bytes: int, budget: _TreeBudget) -> dict:
    """新格式 DP 调用目录：meta 指向实际结果文件名（缺省按 res.md/res.json 探测）。"""
    meta = _read_bare_dict(dp_dir / "meta.json", max_file_bytes) or {}
    res_file = _find_res_file(dp_dir, meta.get("res"))
    return {
        "dir": _rel(run_dir, dp_dir),
        "name": meta.get("name") or _strip_seq_prefix(dp_dir.name),
        "desc": meta.get("desc"),
        "seq": meta.get("seq"),
        "ts": meta.get("ts"),
        "error": bool(meta.get("error")),
        "legacy": False,
        "req": _read_bare_dict(dp_dir / "req.json", max_file_bytes),
        "res": _read_content_file(run_dir, res_file, max_file_bytes, budget) if res_file else None,
        "tushare": _build_tushare_calls(run_dir, dp_dir, max_file_bytes, budget),
    }


def _find_res_file(dp_dir: Path, meta_res: str | None) -> Path | None:
    """meta.res 为实际结果文件名（dataprovider_log 写入）；缺失时按约定探测。"""
    if meta_res:
        candidate = dp_dir / meta_res
        if candidate.is_file():
            return candidate
    for fname in ("res.md", "res.json"):
        candidate = dp_dir / fname
        if candidate.is_file():
            return candidate
    return None


def _build_tushare_calls(run_dir: Path, dp_dir: Path, max_file_bytes: int, budget: _TreeBudget) -> list:
    calls = []
    for ts_dir in logs_reader.list_tushare_calls(dp_dir):
        meta = _read_bare_dict(ts_dir / "meta.json", max_file_bytes) or {}
        calls.append({
            "dir": _rel(run_dir, ts_dir),
            "name": meta.get("name") or _strip_seq_prefix(ts_dir.name),
            "seq": meta.get("seq"),
            "ts": meta.get("ts"),
            "probe": bool(meta.get("probe")),
            "error": bool(meta.get("error")),
            "req": _read_bare_dict(ts_dir / "req.json", max_file_bytes),
            "res": _read_content_file(run_dir, ts_dir / "res.json", max_file_bytes, budget),
        })
    return calls


def _build_tools(run_dir: Path, node_dir: Path, max_file_bytes: int, budget: _TreeBudget) -> list:
    tools = []
    for tool_dir in logs_reader.list_tools(node_dir):
        req = _read_bare_dict(tool_dir / "req.json", max_file_bytes) or {}
        tools.append({
            "dir": _rel(run_dir, tool_dir),
            "name": req.get("name") or _strip_seq_prefix(tool_dir.name),
            "req": req or None,
            "res": _read_content_file(run_dir, tool_dir / "res.txt", max_file_bytes, budget),
        })
    return tools


def _build_legacy_dp_call(run_dir: Path, path: Path, max_file_bytes: int, budget: _TreeBudget) -> dict:
    """旧格式平铺 {接口名}.json（仅历史 run）：payload {name, desc, req, res} 映射。

    req 为裸 dict（调用入参，不计树级累计）；res 内嵌为 ExecutionFileDTO
    （str → kind=md，dict → kind=json），计入树级累计，path 指向该平铺文件。
    """
    size = path.stat().st_size

    def _res_file(content, kind, parse_error, truncated) -> dict | None:
        return {
            "path": _rel(run_dir, path), "kind": kind, "content": content,
            "parse_error": parse_error, "truncated": truncated, "total_bytes": size,
        }

    if size > max_file_bytes:
        return {
            "dir": None, "name": path.stem, "desc": None, "seq": None, "ts": None,
            "error": False, "legacy": True, "req": None,
            "res": _res_file(None, "json", False, True), "tushare": [],
        }
    payload = logs_reader.parse_legacy(path)
    if payload is None:
        return {
            "dir": None, "name": path.stem, "desc": None, "seq": None, "ts": None,
            "error": False, "legacy": True, "req": None,
            "res": _res_file(None, "json", True, False), "tushare": [],
        }
    res_value = payload.get("res")
    if isinstance(res_value, dict):
        res = _res_file(res_value, "json", False, False)
    elif isinstance(res_value, str):
        res = _res_file(res_value, "md", False, False)
    else:
        res = None
    if res is not None and res["content"] is not None and not budget.try_embed(size):
        res = _res_file(None, res["kind"], False, True)
    return {
        "dir": None,
        "name": payload.get("name") or path.stem,
        "desc": payload.get("desc"),
        "seq": None,
        "ts": None,
        "error": False,
        "legacy": True,
        "req": payload.get("req"),
        "res": res,
        "tushare": [],
    }


def _read_content_file(
    run_dir: Path, path: Path, max_file_bytes: int, budget: _TreeBudget
) -> dict | None:
    """ExecutionFileDTO 承载的内容文件：缺失 → None（运行中未写完是正常态）。

    单文件超限先于解析判断（按 stat().st_size，不读全量）；
    树级超限（truncated）与单文件超限语义统一：content=null，走 content 端点拉全量。
    """
    if path is None or not path.is_file():
        return None
    size = path.stat().st_size
    kind = _EXT_KINDS.get(path.suffix.lower(), "md")
    dto = {
        "path": _rel(run_dir, path), "kind": kind, "content": None,
        "parse_error": False, "truncated": False, "total_bytes": size,
    }
    if size > max_file_bytes:
        dto["truncated"] = True
        return dto
    if not budget.try_embed(size):
        dto["truncated"] = True
        return dto
    if kind == "json":
        data = logs_reader.read_json(path)
        if data is None:
            dto["parse_error"] = True  # 坏 JSON/半写文件（并发读写可接受降级）
        else:
            dto["content"] = data
    else:
        text = logs_reader.read_text(path)
        if text is not None:
            dto["content"] = text
    return dto


def _read_bare_dict(path: Path, max_file_bytes: int) -> dict | None:
    """req.json/meta.json 等裸 dict 字段：缺失/坏 JSON → None；超限 → None（防御性兜底）。"""
    if not path.is_file():
        return None
    if path.stat().st_size > max_file_bytes:
        return None
    return logs_reader.read_json(path)


def _parse_seq_prefix(name: str) -> int | None:
    """目录名数字前缀（3 位起，seq 超 999 后自然 4 位）；解析失败为 None。"""
    m = _SEQ_PREFIX_RE.match(name)
    return int(m.group(1)) if m else None


def _strip_seq_prefix(name: str) -> str:
    m = _SEQ_PREFIX_RE.match(name)
    return name[m.end():] if m else name


def _rel(run_dir: Path, p: Path) -> str:
    return p.relative_to(run_dir).as_posix()
