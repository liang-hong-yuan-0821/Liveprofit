"""图拓扑运行状态扫描（纯函数，无 DB/IO 框架依赖）。

状态推导规则见 docs/requirements/archive/任务拓扑图方案.md §2.1：

- 节点目录分两类：**LLM 目录** = 含节点级 meta.json 的目录（on_llm_start 最先写，
  AI/utils/llm_callbacks.py）；**预测目录** = 无节点级 meta.json（dataprovider_log
  为纯代码节点按 llm_seq + 1 预测序号创建）——两者 seq 可能并列（screening 预测
  目录与 stock 循环首个 LLM 目录同 seq），running/error 兜底只作用于 LLM 目录。
- 目录名匹配：剥 `^\\d+_` 前缀后 == `_sanitize(label)`。`_sanitize` 直接复用
  `AI.utils.llm_callbacks` 的私有函数（内核日志目录命名的唯一事实来源，勿复制
  第二份——内核改命名规则时此处自动跟随）。
"""

from __future__ import annotations

import re
from pathlib import Path

from AI.graph.topology import Topology
from AI.logviewer import logs_reader
from AI.utils.llm_callbacks import _sanitize

_SEQ_PREFIX_RE = re.compile(r"^(\d+)_")


def attempt_chain_dirs(execution_logs_root: Path, task_id, attempt_no: int) -> list[Path]:
    """attempt 目录链（新→旧，仅存在且含 complete.json 完成标记的目录）。

    单Agent重跑方案 3.4.1：三处消费点（rerun 路由前置校验、worker 侧
    build_real_initial_state、任务拓扑 rerun_available 计算）共享本函数
    单点构造，杜绝口径分叉——任一处置漏过滤即复活环中态无限循环。
    attempt_no 为链上包含的最新目录号（路由侧传 base、worker 侧传 base-1）。
    """
    chain = []
    for n in range(attempt_no, 0, -1):
        d = execution_logs_root / "tasks" / str(task_id) / str(n)
        if (d / "complete.json").is_file():
            chain.append(d)
    return chain


def _dir_seq(name: str) -> int:
    m = _SEQ_PREFIX_RE.match(name)
    return int(m.group(1)) if m else -1


def _strip_seq(name: str) -> str:
    m = _SEQ_PREFIX_RE.match(name)
    return name[m.end():] if m else name


def scan_run_status(
    run_dir: Path,
    topology: Topology,
    *,
    terminal: bool,
    failed: bool,
) -> dict[str, dict]:
    """按日志目录为每个拓扑节点叠加运行状态。

    返回 {node_id: {"dirs": [...], "status": str, "invocation_count": int}}；
    未匹配到目录的节点不在结果中（由调用方按 not_executed 兜底）。
    run_dir 不存在/不可读 → 空结果（available 判定由 router 层做）。
    """
    if not run_dir.is_dir():
        return {}

    # 按 (layer, sanitize(label)) 反查节点（label 在层内唯一）
    node_by_key: dict[tuple[str, str], str] = {
        (n.layer, _sanitize(n.label)): n.id for n in topology.nodes
    }

    # 第一遍：收集每节点的目录 + DP error 判定
    matched: dict[str, dict] = {}
    llm_dirs: list[tuple[int, Path]] = []  # (seq, dir) 仅 LLM 目录，供全局最大 seq
    for layer_dir in logs_reader.list_layers(run_dir):
        for node_dir in logs_reader.list_nodes(layer_dir):
            node_id = node_by_key.get((layer_dir.name, _strip_seq(node_dir.name)))
            if node_id is None:
                continue  # 未匹配任何拓扑节点（旧 run 遗留目录）忽略
            entry = matched.setdefault(node_id, {"dirs": [], "_error": False})
            entry["dirs"].append(node_dir)
            if _node_has_dp_error(node_dir):
                entry["_error"] = True
            if (node_dir / "meta.json").is_file():
                llm_dirs.append((_dir_seq(node_dir.name), node_dir))

    # 全局最大 seq 的 LLM 目录（仅 LLM 目录参与，防预测目录并列 seq 假状态）
    last_llm_dir: Path | None = None
    if llm_dirs:
        last_llm_dir = max(llm_dirs, key=lambda t: t[0])[1]

    result: dict[str, dict] = {}
    for node_id, entry in matched.items():
        dirs = sorted(entry["dirs"], key=lambda d: d.name)
        if entry["_error"]:
            status = "error"
        elif (
            failed
            and last_llm_dir is not None
            and last_llm_dir in entry["dirs"]
            and not (last_llm_dir / "res.md").is_file()
        ):
            # LLM 调用中途失败（on_llm_error 不落盘）的终态兜底
            status = "error"
        elif not terminal and last_llm_dir in entry["dirs"]:
            status = "running"
        else:
            status = "executed"
        result[node_id] = {
            "dirs": [str(d.relative_to(run_dir).as_posix()) for d in dirs],
            "status": status,
            "invocation_count": len(dirs),
        }
    return result


def _node_has_dp_error(node_dir: Path) -> bool:
    """节点目录下任一 DP 调用目录 meta.json 的 error=true（半写文件 → 视为无 error）。"""
    for kind, dp in logs_reader.list_dp_calls(node_dir):
        if kind != "new":
            continue
        meta = logs_reader.read_json(dp / "meta.json")
        if meta and meta.get("error"):
            return True
    return False
