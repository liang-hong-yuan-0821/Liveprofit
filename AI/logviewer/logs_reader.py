"""
日志目录读取层（纯函数，无 streamlit 依赖，供 app.py 与测试共用）

目录约定见 AI/utils/llm_callbacks.py 与 AI/utils/dataprovider_log.py 的模块 docstring：
  logs/{时间戳}/{layer}/{seq:03d}_{NodeName}/req.md + res.md + meta.json
                              └─ {seq:03d}_{接口名}/req.json + res.md|json + meta.json   ← dataprovider 新格式
                              └─ tools/{seq:03d}_{tool}/req.json + res.txt
                              └─ {接口名}.json                                          ← dataprovider 旧格式（仅历史 run）

所有读取函数对缺失文件/坏 JSON 返回 None，不抛异常（中断的 run 可能不完整）。
"""

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 运行批次目录名：2026-08-19_223929
_RUN_RE = re.compile(r"\d{4}-\d{2}-\d{2}_\d{6}")
# 序号前缀目录：001_International_News_Analyst / 001_get_xxx / 001_search
_SEQ_DIR_RE = re.compile(r"\d{3}_.+")
# 真实执行序（trading_graph：Market → Sector → Stock，Screening 在 Stock 层内）
LAYER_ORDER = ["market", "sector", "stock", "screening"]


def logs_root() -> Path:
    """日志根目录：环境变量 LIVEPROFIT_LOGS_DIR 优先，否则仓库根下 logs/（不依赖启动 CWD）"""
    env = os.environ.get("LIVEPROFIT_LOGS_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "logs"


def list_runs(root: Optional[Path] = None) -> List[Path]:
    """运行批次目录（只认时间戳目录，排除 backups/ 等噪音），最新在前"""
    root = root or logs_root()
    if not root.is_dir():
        return []
    runs = [p for p in root.iterdir() if p.is_dir() and _RUN_RE.fullmatch(p.name)]
    return sorted(runs, reverse=True)


def list_layers(run: Path) -> List[Path]:
    """run 下的直接子目录（market/sector/stock/...），reports 由报告 tab 单独处理"""
    if not run.is_dir():
        return []
    return sorted(
        (p for p in run.iterdir() if p.is_dir() and p.name != "reports"),
        key=lambda p: p.name,
    )


def sort_layers(layers: List[Path]) -> List[Path]:
    """已知 layer 按固定执行序（market→sector→stock→screening），未知 layer 字母序附后"""
    return sorted(
        layers,
        key=lambda p: (LAYER_ORDER.index(p.name)
                       if p.name in LAYER_ORDER else len(LAYER_ORDER), p.name),
    )


def list_nodes(layer_dir: Path) -> List[Path]:
    """layer 下的 LLM 节点目录（{seq:03d}_{NodeName}）"""
    if not layer_dir.is_dir():
        return []
    return sorted(
        (p for p in layer_dir.iterdir() if p.is_dir() and _SEQ_DIR_RE.fullmatch(p.name)),
        key=lambda p: p.name,
    )


def list_tools(node_dir: Path) -> List[Path]:
    """节点目录下 tools/ 内的工具调用目录（{seq:03d}_{tool}）"""
    tools_dir = node_dir / "tools"
    if not tools_dir.is_dir():
        return []
    return sorted(
        (p for p in tools_dir.iterdir() if p.is_dir() and _SEQ_DIR_RE.fullmatch(p.name)),
        key=lambda p: p.name,
    )


def list_dp_calls(node_dir: Path) -> List[Tuple[str, Path]]:
    """节点目录下的 dataprovider 调用：
    ("new", 目录)    = 新格式 {seq:03d}_{接口名}/（目录 vs 文件即新旧判别）
    ("legacy", 文件) = 旧格式 {接口名}.json（平铺文件，排除 meta.json）
    """
    if not node_dir.is_dir():
        return []
    calls: List[Tuple[str, Path]] = []
    for p in node_dir.iterdir():
        if p.is_dir() and _SEQ_DIR_RE.fullmatch(p.name):
            calls.append(("new", p))
        elif p.is_file() and p.suffix == ".json" and p.name != "meta.json":
            calls.append(("legacy", p))
    return sorted(calls, key=lambda kv: kv[1].name)


def list_report_files(run: Path) -> List[Path]:
    """run 级 reports/ 下的报告文件（md/json/html，递归含 charts/ 子目录）"""
    reports_dir = run / "reports"
    if not reports_dir.is_dir():
        return []
    return sorted(
        (p for p in reports_dir.rglob("*") if p.is_file()),
        key=lambda p: p.relative_to(reports_dir).as_posix(),
    )


# 调试步进检查点文件名（run 根下隐藏文件，单点定义见 AI/utils/step_gate.py）
from AI.utils.step_gate import CHECKPOINT_FILE


def find_checkpoint(run: Path) -> Optional[Dict[str, Any]]:
    """读 run 的调试步进检查点文件；缺失/坏 JSON → None"""
    return read_json(run / CHECKPOINT_FILE)


def find_active_checkpoint(root: Optional[Path] = None) -> Optional[Tuple[Path, Dict[str, Any]]]:
    """扫描全部 run（最新在前），返回 (run_dir, payload)；无 → None。

    优先返回 waiting 的检查点（并发多 run 时，较旧 run 仍在等待的检查点
    不能被较新 run 已结束的 confirmed 残留遮蔽）；无 waiting 时退回最新的
    非 waiting 检查点（历史状态展示）。
    """
    fallback: Optional[Tuple[Path, Dict[str, Any]]] = None
    for run in list_runs(root):
        payload = find_checkpoint(run)
        if payload is None:
            continue
        if payload.get("status") == "waiting":
            return run, payload
        if fallback is None:
            fallback = (run, payload)
    return fallback


def read_json(p: Path) -> Optional[Dict[str, Any]]:
    """读取 JSON；缺失/坏 JSON 返回 None"""
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def read_text(p: Path) -> Optional[str]:
    """读取文本；文件缺失返回 None"""
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return None


def parse_legacy(p: Path) -> Optional[Dict[str, Any]]:
    """解析旧格式 {接口名}.json → {name, desc, req, res}"""
    return read_json(p)
