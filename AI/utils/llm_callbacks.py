"""
LLM / 工具调用追踪器
通过 LangChain BaseCallbackHandler 拦截 ChatOpenAI 和工具调用。

文件结构（按 layer → 节点名组织）：
  logs/{时间戳}/
    ├── market/
    │   ├── 001_International_News_Analyst/
    │   │   ├── req.md
    │   │   ├── res.md
    │   │   ├── meta.json
    │   │   ├── 001_{dataprovider接口名}/   ← dataprovider 调用（见 dataprovider_log.py）
    │   │   │   ├── req.json
    │   │   │   ├── res.md                 ← 结果为 str（多数接口）
    │   │   │   ├── res.json               ← 结果为非 str（与 res.md 二选一）
    │   │   │   └── meta.json              ← {name, desc, seq, ts, res}
    │   │   └── tools/
    │   │       ├── 001_{tool}/
    │   │       │   ├── req.json
    │   │       │   └── res.txt
    │   │       └── ...
    │   └── 002_US_News_Analyst/
    │       └── ...
    ├── sector/
    │   └── 001_Sector_News_Analyst/
    │       └── ...
    ├── stock/
    │   └── 001_Stock_Tech_Analyst/
    │       └── ...
    └── reports/
        └── ...

控制台仅输出一行摘要。
"""

import json
import logging
import re
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

logger = logging.getLogger(__name__)

_TRUNCATE_CONSOLE = 120


def _ts() -> str:
    return datetime.now().strftime("%H%M%S") + f"{datetime.now().microsecond // 1000:03d}"


def _safe_json(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2, default=str)
    except Exception:
        return str(obj)


def _short(text: str, n: int = _TRUNCATE_CONSOLE) -> str:
    s = str(text).replace("\n", " ")
    return s if len(s) <= n else s[:n] + "…"


def _sanitize(name: str) -> str:
    return re.sub(r"[^\w\-]", "_", name.replace(" ", "_"))


def _extract_model(serialized: Dict[str, Any]) -> str:
    kwargs = serialized.get("kwargs", {}) or {}
    return kwargs.get("model", "") or kwargs.get("model_name", "") or "unknown"


# 节点名 → 所属层 映射
_NODE_LAYER = {
    # ---- Market Layer ----
    "International Event Extraction Analyst": "market",
    "International News Analyst": "market",
    "US News Analyst": "market",
    "US Tech Analyst": "market",
    "KR News Analyst": "market",
    "KR Tech Analyst": "market",
    "CN News Analyst": "market",
    "CN Tech Analyst": "market",
    # ---- Sector Layer ----
    "Sector News Analyst": "sector",
    "Sector Tech Analyst": "sector",
    "Sector Rotation Analyst": "sector",
    # ---- Screening Layer（纯代码节点，无 LLM） ----
    "Screening": "screening",
    # ---- Stock Layer ----
    "Stock Tech Analyst": "stock",
    "Social Analyst": "stock",
    "News Analyst": "stock",
    "Fundamentals Analyst": "stock",
    "Bull Researcher": "stock",
    "Bear Researcher": "stock",
    "Research Manager": "stock",
    "Trader": "stock",
    "Risky Analyst": "stock",
    "Safe Analyst": "stock",
    "Neutral Analyst": "stock",
    "Risk Judge": "stock",
}


def _extract_node(metadata: Optional[Dict[str, Any]]) -> str:
    if not metadata:
        return "unknown"
    node = metadata.get("langgraph_node", "")
    if node:
        return node
    for k in metadata:
        if "node" in k.lower():
            return str(metadata[k])
    return "unknown"


def _node_layer(node: str) -> str:
    """根据节点名确定所属 layer"""
    return _NODE_LAYER.get(node, "unknown")


class _RunState:
    """单次 propagate 的共享状态：序号 + 当前节点目录"""

    def __init__(self):
        self.llm_seq = 0
        self.tool_counters: Dict[str, int] = {}  # dir_name → count
        self.dp_counters: Dict[str, int] = {}    # agent目录相对路径 → dataprovider 调用次数
        self.last_llm_dir = ""

    def next_llm(self) -> int:
        self.llm_seq += 1
        return self.llm_seq

    def next_tool(self, parent_dir: str) -> int:
        self.tool_counters[parent_dir] = self.tool_counters.get(parent_dir, 0) + 1
        return self.tool_counters[parent_dir]

    def next_dp(self, agent_dir: str) -> int:
        """dataprovider 调用序号，按所属 Agent 目录独立计数"""
        self.dp_counters[agent_dir] = self.dp_counters.get(agent_dir, 0) + 1
        return self.dp_counters[agent_dir]

    def reset(self, log_dir: Path):
        self.llm_seq = 0
        self.tool_counters.clear()
        self.dp_counters.clear()
        self.last_llm_dir = ""
        self.log_dir = log_dir


# 全局单例，两个 handler 共享
_run = _RunState()


class LLMCallbackHandler(BaseCallbackHandler):
    """记录 ChatOpenAI 每次调用 → {seq:03d}_{NodeName}/req.md + res.md + meta.json"""

    def __init__(self):
        self._pending: Dict[str, tuple] = {}  # run_id → (dir_name, seq)

    def set_log_dir(self, log_dir: Path) -> None:
        self._pending.clear()
        _run.reset(log_dir)

    def on_llm_start(
        self,
        serialized: Dict[str, Any],
        prompts: List[str],
        *,
        run_id=None,
        parent_run_id=None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        model = _extract_model(serialized)
        node = _extract_node(metadata)
        layer = _node_layer(node)
        seq = _run.next_llm()
        dir_name = f"{layer}/{seq:03d}_{_sanitize(node)}"
        _run.last_llm_dir = dir_name
        rid = str(run_id) if run_id else None
        if rid:
            self._pending[rid] = (dir_name, seq)

        logger.info("[LLM] %s → #%d %s | %s | prompt数=%d",
                    _ts(), seq, node, model, len(prompts))

        if hasattr(_run, 'log_dir') and _run.log_dir:
            call_dir = _run.log_dir / dir_name
            call_dir.mkdir(parents=True, exist_ok=True)
            (call_dir / "req.md").write_text(
                "\n\n".join(str(p) for p in prompts), encoding="utf-8")
            (call_dir / "meta.json").write_text(_safe_json({
                "model": model, "node": node, "run_id": rid, "seq": seq,
            }), encoding="utf-8")

    def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id=None,
        parent_run_id=None,
        **kwargs: Any,
    ) -> None:
        rid = str(run_id) if run_id else None
        entry = self._pending.pop(rid, None) if rid else None
        dir_name, seq = entry if entry else ("unknown", 0)

        for gen in response.generations:
            for g in gen:
                msg = getattr(g, "message", None)
                if msg is None:
                    text = getattr(g, "text", "")
                    logger.info("[LLM] %s ← #%d | text长度=%d %s",
                                _ts(), seq, len(text), _short(text))
                    if hasattr(_run, 'log_dir') and _run.log_dir:
                        d = _run.log_dir / dir_name
                        d.mkdir(parents=True, exist_ok=True)
                        (d / "res.md").write_text(str(text), encoding="utf-8")
                    continue

                content = getattr(msg, "content", "") or ""
                tool_calls = getattr(msg, "tool_calls", None) or []
                additional_kwargs = getattr(msg, "additional_kwargs", {}) or {}
                if not tool_calls and "tool_calls" in additional_kwargs:
                    tool_calls = additional_kwargs["tool_calls"]

                tc_clean = []
                for tc in tool_calls:
                    name = tc.get("name", "?") if isinstance(tc, dict) else getattr(tc, "name", "?")
                    args = tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {})
                    tc_clean.append({"name": name, "args": args})

                logger.info("[LLM] %s ← #%d | content长度=%d tool_calls=%s %s",
                            _ts(), seq, len(str(content)),
                            [t["name"] for t in tc_clean], _short(content))

                if hasattr(_run, 'log_dir') and _run.log_dir:
                    call_dir = _run.log_dir / dir_name
                    call_dir.mkdir(parents=True, exist_ok=True)
                    (call_dir / "res.md").write_text(str(content), encoding="utf-8")
                    try:
                        meta = json.loads((call_dir / "meta.json").read_text("utf-8"))
                    except Exception:
                        meta = {}
                    meta["content_length"] = len(str(content))
                    if tc_clean:
                        meta["tool_calls"] = tc_clean
                    (call_dir / "meta.json").write_text(_safe_json(meta), encoding="utf-8")

    def on_llm_error(self, error, *, run_id=None, parent_run_id=None, **kwargs):
        logger.error("[LLM ERROR] %s: %s", type(error).__name__, str(error)[:200])


class ToolCallbackHandler(BaseCallbackHandler):
    """记录工具调用 → 写入对应 LLM 节点的 tools/ 子目录"""

    def __init__(self):
        self._pending: Dict[str, tuple] = {}  # run_id → (parent_dir, tool_name, tool_seq)

    def set_log_dir(self, log_dir: Path) -> None:
        self._pending.clear()

    def on_tool_start(
        self,
        serialized: Dict[str, Any],
        input_str: str,
        *,
        run_id=None,
        parent_run_id=None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        tool_name = serialized.get("name", "unknown")
        node = _extract_node(metadata)
        # 优先用 metadata 中的节点名 + layer，否则回退到上一次 LLM 调用的目录
        if node != "unknown" and _run.llm_seq > 0:
            layer = _node_layer(node)
            parent_dir = f"{layer}/{_run.llm_seq:03d}_{_sanitize(node)}"
        else:
            parent_dir = _run.last_llm_dir or "unknown"
        tool_seq = _run.next_tool(parent_dir)
        rid = str(run_id) if run_id else None
        if rid:
            self._pending[rid] = (parent_dir, tool_name, tool_seq)

        logger.info("[TOOL] %s → %s::%s %s",
                    _ts(), parent_dir, tool_name, _short(input_str, 200))

        if hasattr(_run, 'log_dir') and _run.log_dir and parent_dir:
            tdir = _run.log_dir / parent_dir / "tools" / f"{tool_seq:03d}_{tool_name}"
            tdir.mkdir(parents=True, exist_ok=True)
            (tdir / "req.json").write_text(
                _safe_json({"name": tool_name, "input": input_str}), encoding="utf-8")

    def on_tool_end(
        self,
        output: str,
        *,
        run_id=None,
        parent_run_id=None,
        **kwargs: Any,
    ) -> None:
        rid = str(run_id) if run_id else None
        entry = self._pending.pop(rid, None) if rid else None
        if entry:
            parent_dir, tool_name, tool_seq = entry
        else:
            parent_dir, tool_name, tool_seq = _run.last_llm_dir or "unknown", "unknown", 0

        logger.info("[TOOL] %s ← %s::%s | output长度=%d %s",
                    _ts(), parent_dir, tool_name, len(output), _short(output))

        if hasattr(_run, 'log_dir') and _run.log_dir and parent_dir:
            tdir = _run.log_dir / parent_dir / "tools" / f"{tool_seq:03d}_{tool_name}"
            tdir.mkdir(parents=True, exist_ok=True)
            (tdir / "res.txt").write_text(str(output), encoding="utf-8")

    def on_tool_error(self, error, *, run_id=None, parent_run_id=None, **kwargs):
        logger.error("[TOOL ERROR] %s: %s", type(error).__name__, str(error)[:200])
