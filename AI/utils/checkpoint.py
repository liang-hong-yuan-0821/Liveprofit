"""单Agent重跑：checkpoint 存档 + 快进 guard + entry 解析（方案 3.2）。

- `guard_checkpoint(node_name)`：层构建器 add_node 接线处的统一包装器——
  执行前注入 `state["_current_node_id"]`（供 AI.utils.prompts 覆盖解析），
  重跑时对 `(row, order)` 小于目标（经 `_LOOP_ENTRY` 环入口上移）的节点快进
  （返回 {}），节点完成后落 checkpoint（最后写入态）。
- 序列化：`messages` 经 `langchain_core.messages.messages_to_dict`（包顶层导入，
  venv 实测 `messages.utils` 只暴露 messages_from_dict）；其余字段经
  `json.loads(json.dumps(x, default=str))` 归一；丢弃 `_` 前缀键与
  `prompt_overrides`。读取用 `messages_from_dict` 还原。
- `resolve_entry_checkpoint(run_dirs, topology, node_id, ticker)`：run_dirs 为
  attempt 目录链（新→旧，**仅含 complete.json 完成标记目录**——失败/取消的
  部分执行目录可能留有 count<cap 的环中态 checkpoint，被跳过环成员出口路由
  读该态恒返环内回边 → 无限循环，故由调用方预过滤）。逐目录找 effective
  的前驱 checkpoint，未命中继续更旧目录（不逐目录 __init__ 兜底——重跑
  attempt 只覆盖写目标+下游，上游前驱在更旧目录）；目录链耗尽且无前驱
  （全局首节点）→ 最近一次完整执行目录（无 rerun.json）的 __init__.json
  （rerun 目录的 __init__.json = merged entry 态，不可作首节点 entry——
  须保持"目标消息通道与原运行等价"不变式）。
- 依赖约束：本模块顶层只 import langchain_core 与 AI.utils.llm_callbacks
  （轻依赖，被全部层构建器 import）；topology 函数级导入破循环依赖
  （topology.py 顶层 import 层构建器，层构建器顶层 import 本模块）。
"""

import json
import logging
from datetime import datetime
from pathlib import Path

from langchain_core.messages import messages_from_dict, messages_to_dict

from AI.utils.llm_callbacks import _NODE_LAYER, _sanitize

logger = logging.getLogger(__name__)

# 环成员 → 环入口成员（键为完整拓扑节点 id；环必须整体重演：跳过只发生在
# 环入口之前，回边重入的必是真实执行过的成员，出口路由不会返回 map 外目标）
_LOOP_ENTRY = {
    "stock:Bull Researcher": "stock:Bull Researcher",
    "stock:Bear Researcher": "stock:Bull Researcher",
    "stock:Risky Analyst": "stock:Risky Analyst",
    "stock:Safe Analyst": "stock:Risky Analyst",
    "stock:Neutral Analyst": "stock:Risky Analyst",
}

# 本次运行的 checkpoint 根目录（propagate/rerun 入口设置；worker 串行执行下安全）
_current_run_dir: Path | None = None


def set_checkpoint_run_dir(run_dir: Path | None) -> None:
    """设置本次运行的 checkpoint 根目录（propagate/rerun 入口调用）。"""
    global _current_run_dir
    _current_run_dir = Path(run_dir) if run_dir else None


def _checkpoints_root(run_dir: Path) -> Path:
    return run_dir / "checkpoints"


def _node_checkpoint_path(run_dir: Path, node_id: str, ticker: str | None) -> Path:
    """节点 checkpoint 文件路径：{run_dir}/checkpoints/{layer}[/{ticker}]/{Sanitized}.json"""
    layer, _, label = node_id.partition(":")
    rel = Path(layer)
    if layer == "stock":
        rel = rel / (ticker or "unknown")
    return _checkpoints_root(run_dir) / rel / f"{_sanitize(label)}.json"


def serialize_state(state: dict, node_id: str) -> dict:
    """状态 → 纯 JSON dict（messages 经 messages_to_dict，其余 default=str 归一）。

    丢弃 `_` 前缀键（_rerun_from/_current_node_id 等运行时标记）与
    prompt_overrides（平台注入的覆盖快照不污染快照）。
    """
    data = {
        k: v
        for k, v in state.items()
        if not k.startswith("_") and k != "prompt_overrides"
    }
    if "messages" in data:
        data["messages"] = messages_to_dict(data["messages"] or [])
    return {
        "saved_at": datetime.now().isoformat(),
        "node_id": node_id,
        "state": json.loads(json.dumps(data, ensure_ascii=False, default=str)),
    }


def deserialize_checkpoint(path: Path) -> dict:
    """读 checkpoint 文件还原 state（messages 经 messages_from_dict）。

    坏 JSON 抛 JSONDecodeError——由调用方（backend build_real_initial_state）
    转 FatalAnalysisError(RERUN_CHECKPOINT_MISSING)，明确报错而非静默退化。
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    state = dict(raw.get("state") or {})
    if isinstance(state.get("messages"), list):
        state["messages"] = messages_from_dict(state["messages"])
    return state


def _atomic_write_json(path: Path, payload: dict) -> None:
    """temp+rename 原子写（防半写文件毒害 resolve/解析）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def save_init_state(state: dict) -> None:
    """保存本次运行入口 state 到 {run_dir}/checkpoints/__init__.json。"""
    if _current_run_dir is None:
        return
    _atomic_write_json(
        _checkpoints_root(_current_run_dir) / "__init__.json",
        serialize_state(state, "__init__"),
    )


def write_complete_marker(run_dir: Path) -> None:
    """成功收尾原子写完成标记（失败/取消的 attempt 目录无此文件）。"""
    _atomic_write_json(Path(run_dir) / "complete.json", {
        "completed_at": datetime.now().isoformat(),
    })


def write_rerun_marker(run_dir: Path, node_id: str, base_attempt: int | None) -> None:
    """重跑元数据 {run_dir}/rerun.json。"""
    _atomic_write_json(Path(run_dir) / "rerun.json", {
        "rerun_from": node_id,
        "base_attempt": base_attempt,
    })


def _merge_updates(state: dict, result: dict) -> dict:
    """对齐 add_messages reducer 语义合并节点输出：messages 拼接、其余覆盖。"""
    merged = {**state, **result}
    merged["messages"] = list(state.get("messages") or []) + list(
        result.get("messages") or []
    )
    return merged


def _is_upstream(state: dict, node_id: str) -> bool:
    """节点是否在重跑目标之前（快进）。_rerun_from 未设置 → False。"""
    target = state.get("_rerun_from")
    if not target:
        return False
    from AI.graph.topology import build_topology  # 函数级导入破循环依赖

    topology = build_topology(("market", "sector", "screening", "stock"))
    pos = {n.id: (n.row, n.order) for n in topology.nodes}
    # 未知节点置极端值：不在拓扑的节点恒判非上游（Screening 行号差异下
    # 单股/全市场任务的"上游"判定结论与全形态拓扑一致；screening 任务的
    # stock 层重跑由后端前置校验禁止，本处不参与）
    own = pos.get(node_id, (10**9, 10**9))
    return own < pos.get(target, (0, 0))


def _save_checkpoint(state: dict, node_id: str) -> None:
    """节点完成后落 checkpoint（最后写入态；screening 模式 stock 层跳过）。"""
    if _current_run_dir is None:
        return
    layer = node_id.partition(":")[0]
    selected = state.get("selected_layers") or ()
    if layer == "stock" and "screening" in selected:
        return  # v1 禁止 screening 任务重跑个股层节点，省每票 12 文件 × N 票
    ticker = state.get("company_of_interest") if layer == "stock" else None
    _atomic_write_json(
        _node_checkpoint_path(_current_run_dir, node_id, ticker),
        serialize_state(state, node_id),
    )


def guard_checkpoint(node_name: str):
    """层构建器接线处统一包装器：注入 _current_node_id + 快进 + checkpoint。

    用法：workflow.add_node("CN News Analyst",
    guard_checkpoint("CN News Analyst")(track_node("CN News Analyst")(factory(...))))
    包装对 langgraph 透明：builder.nodes 不变、build_topology 输出不变。
    """

    node_id = f"{_NODE_LAYER[node_name]}:{node_name}"

    def deco(fn):
        def wrapped(state: dict, *args, **kwargs):
            state["_current_node_id"] = node_id
            if _is_upstream(state, node_id):
                return {}  # 快进：输出已在 entry 快照中
            result = fn(state, *args, **kwargs) or {}
            _save_checkpoint(_merge_updates(state, result), node_id)
            return result

        return wrapped

    return deco


def resolve_entry_checkpoint(
    run_dirs: list[Path],
    topology,
    node_id: str,
    ticker: str | None = None,
) -> Path | None:
    """沿 attempt 目录链（新→旧）解析目标节点的 entry checkpoint。

    环成员目标经 _LOOP_ENTRY 上移环入口后按 effective 解析前驱；
    目录链耗尽且 effective 无前驱（全局首节点）→ 最近一次完整执行目录
    （无 rerun.json）的 __init__.json；都没有 → None（重跑不可用）。

    run_dirs 须由调用方预过滤为仅含 complete.json 标记目录（见模块 docstring）。
    """
    effective = _LOOP_ENTRY.get(node_id, node_id)

    nodes = sorted(topology.nodes, key=lambda n: (n.row, n.order))
    # 未知节点防御：effective 不在拓扑内直接不可用（调用路径均有前置校验，
    # 防未来新调用点绕过后落入"前驱=拓扑末节点"的错误兜底）
    if not any(n.id == effective for n in nodes):
        return None
    predecessor_id = None
    for n in nodes:
        if n.id == effective:
            break
        predecessor_id = n.id

    for run_dir in run_dirs:
        if predecessor_id is not None:
            path = _node_checkpoint_path(run_dir, predecessor_id, ticker)
            if path.exists():
                return path
            continue  # 未命中 → 继续更旧目录
        # 全局首节点：最近完整执行目录（无 rerun.json）的 __init__.json
        if not (run_dir / "rerun.json").exists():
            init = _checkpoints_root(run_dir) / "__init__.json"
            if init.exists():
                return init
    return None
