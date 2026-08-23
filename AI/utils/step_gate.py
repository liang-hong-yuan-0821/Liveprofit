"""
调试步进门控（debug step mode）
在主体分析流程的关键节点暂停，等待 Streamlit 日志查看器页面确认后继续。

文件协议（CLI 分析进程与查看器两个独立进程共享 logs/ 目录）：
  logs/{时间戳}/.debug_checkpoint.json
    ├── status    waiting（后端写）→ confirmed / skip_all（页面写）
    ├── seq       本次 run 内检查点全局序号（从 1 递增）
    ├── kind      dp = DP 响应 / llm_req = LLM 调用前 / llm_res = 节点 res
    ├── ts        检查点创建时间
    ├── layer     dir 相对 run 目录路径首段
    ├── node      节点名（未消毒原始名）
    ├── name      dp：接口名；llm_req/llm_res：节点名
    ├── dir       日志目录相对 run 的路径（`/` 归一化），页面据此读取内容
    └── show_file 页面渲染的目标文件（dp → res.md|json；llm_req → req.md；llm_res → res.md）

后端写 waiting 后每 0.5s 轮询该文件，status != "waiting" 即继续：
  confirmed → 放行本步；skip_all → 放行后续全部检查点（不再写文件）。
缺失/坏 JSON 视为仍等待。双方均 temp+rename 原子写，防读到半截文件。
无限等待（不设超时）；未启用（LIVEPROFIT_DEBUG_STEP != true）时 checkpoint 为零开销快路径。

启用入口：TradingAgentsGraph.propagate()（见 AI/graph/trading_graph.py）。
"""

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# run 根目录下的检查点文件名（隐藏文件，logs_reader 的 list_runs/list_layers 不受影响）
CHECKPOINT_FILE = ".debug_checkpoint.json"
# 后端轮询间隔（秒）
_POLL_INTERVAL = 0.5

# 模块级状态：LangGraph 节点与 LangChain 回调均为单线程顺序执行，无需锁
_enabled = False
_run_dir: Optional[Path] = None
_skip_all = False
_seq = 0


def enable(run_dir: Path) -> None:
    """启用步进模式（每次 propagate 开始时调用，重置全部状态）"""
    global _enabled, _run_dir, _skip_all, _seq
    _enabled = True
    _run_dir = run_dir
    _skip_all = False
    _seq = 0
    logger.info("[步进] 调试步进模式已启用，检查点文件: %s",
                checkpoint_file())


def disable() -> None:
    """关闭步进模式（propagate 末尾调用，状态卫生，防跨 run 残留）"""
    global _enabled, _run_dir, _skip_all, _seq
    _enabled = False
    _run_dir = None
    _skip_all = False
    _seq = 0


def is_enabled() -> bool:
    return _enabled


def checkpoint_file() -> Optional[Path]:
    """当前 run 的检查点文件路径；未启用返回 None"""
    if _run_dir is None:
        return None
    return _run_dir / CHECKPOINT_FILE


def _write_payload(path: Path, payload: Dict[str, Any]) -> None:
    """原子写：temp + rename，防对端读到半截文件。

    tmp 名带进程号后缀：后端与页面（及页面多标签）进程共用同一检查点文件，
    若共用同一 tmp 名会并发交错，各写各的 tmp 再 rename 保证原子性。
    """
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(path)


def _read_status() -> Optional[Dict[str, Any]]:
    """读检查点文件；缺失/坏 JSON 返回 None（视为仍等待）"""
    path = checkpoint_file()
    if path is None or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def checkpoint(kind: str, *, layer: str, node: str, name: str,
               dir: str, show_file: str) -> None:
    """关键节点暂停：写 waiting 载荷 → 轮询直到页面确认/跳过。

    未启用或已跳过全部时立即返回（零开销快路径，不写文件）。
    调用方应包 try/except 隔离，步进机制永不干扰数据流与日志落盘。
    """
    global _seq, _skip_all
    if not _enabled or _skip_all:
        return
    path = checkpoint_file()
    if path is None:
        return

    _seq += 1
    payload = {
        "status": "waiting",
        "seq": _seq,
        "kind": kind,
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "layer": layer,
        "node": node,
        "name": name,
        "dir": dir,
        "show_file": show_file,
    }
    _write_payload(path, payload)
    logger.info("⏸ [步进] #%d 等待页面确认：%s %s::%s → %s",
                _seq, kind, node, name, path)

    while True:
        time.sleep(_POLL_INTERVAL)
        cur = _read_status()
        if cur is None:
            continue  # 文件缺失/坏 JSON → 视为仍等待
        status = cur.get("status")
        if status == "confirmed":
            logger.info("▶ [步进] #%d 已确认，继续", _seq)
            return
        if status == "skip_all":
            _skip_all = True
            logger.info("⏭ [步进] #%d 已跳过全部，后续检查点自动放行", _seq)
            return
