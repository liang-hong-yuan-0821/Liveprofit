"""
数据提供器（dataprovider）调用日志
在 interface 层记录每次 dataprovider 接口调用，写入所属 Agent 的日志目录：

  logs/{时间戳}/{layer}/{seq:03d}_{NodeName}/{接口名}.json

JSON 结构：
  {
    "name": "接口名",
    "desc": "接口描述（用来干什么）",
    "req":  {"入参名": 值, ...},
    "res":  返回值
  }

归属判定：
- track_node 装饰器为每个图节点设置当前节点上下文（contextvar）；
- 接口调用发生时：
  1) 当前节点与最近一次 LLM 目录一致 → 复用该目录；
  2) 当前节点已知（尚无 LLM 调用）→ 按 layer + 预测序号创建目录；
  3) 无节点上下文（如 ToolNode 内部）→ 回退到最近一次 LLM 目录。

控制台仅输出一行摘要。
"""

import functools
import inspect
import json
import logging
import re
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Callable, Dict, Optional

# 与 llm_callbacks 共享运行状态（log_dir / llm_seq / last_llm_dir）
from AI.utils.llm_callbacks import _run, _NODE_LAYER, _sanitize, _ts

logger = logging.getLogger(__name__)

# 当前正在执行的图节点名
_current_node: ContextVar[str] = ContextVar("yoho_current_node", default="")


def track_node(node_name: str) -> Callable:
    """包装图节点函数：执行期间设置当前节点上下文（供 dataprovider 日志归属）"""

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            token = _current_node.set(node_name)
            try:
                return fn(*args, **kwargs)
            finally:
                _current_node.reset(token)

        return wrapper

    return decorator


def dataprovider_log(fn: Callable) -> Callable:
    """装饰器：记录 dataprovider 接口调用 → {接口名}.json"""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        req = _bind_req(fn, args, kwargs)
        result = fn(*args, **kwargs)
        try:
            _write(fn.__name__, fn.__doc__, req, result)
        except Exception as e:
            logger.debug(f"[DP] 日志写入失败 {fn.__name__}: {e}")
        return result

    return wrapper


def _bind_req(fn: Callable, args: tuple, kwargs: dict) -> Dict[str, Any]:
    """将实际入参绑定为 {"参数名": 值} 字典（含默认值）"""
    try:
        sig = inspect.signature(fn)
        bound = sig.bind(*args, **kwargs)
        bound.apply_defaults()
        return dict(bound.arguments)
    except Exception:
        return {"args": list(args), **kwargs}


def _agent_dir() -> Optional[Path]:
    """定位当前 dataprovider 调用归属的 Agent 日志目录"""
    if not (hasattr(_run, "log_dir") and _run.log_dir):
        return None

    node = _current_node.get()
    if node:
        # 1) 当前节点的最近一次 LLM 目录 → 直接复用
        #    目录名格式 {seq:03d}_{sanitize(node)}，用正则精确匹配节点名
        #    （避免 "News Analyst" 误匹配 "..._Sector_News_Analyst" 这类后缀碰撞）
        if _run.last_llm_dir:
            last_name = _run.last_llm_dir.split("/")[-1]
            m = re.fullmatch(r"(\d{3})_(.+)", last_name)
            if m and m.group(2) == _sanitize(node):
                return _run.log_dir / _run.last_llm_dir
        # 2) 当前节点尚未产生 LLM 调用 → 预测其下一次 LLM 调用的目录
        layer = _NODE_LAYER.get(node, "unknown")
        seq = _run.llm_seq + 1
        return _run.log_dir / f"{layer}/{seq:03d}_{_sanitize(node)}"

    # 3) 无节点上下文（如 ToolNode 内部）→ 回退到最近一次 LLM 目录
    if _run.last_llm_dir:
        return _run.log_dir / _run.last_llm_dir
    return None


def _write(name: str, doc: Optional[str], req: Dict[str, Any], res: Any) -> None:
    """写入 {接口名}.json 到当前 Agent 目录"""
    agent_dir = _agent_dir()
    if agent_dir is None:
        logger.debug(f"[DP] 跳过 {name}：日志目录未初始化")
        return

    # desc = docstring 首行
    desc = ""
    if doc:
        first_line = doc.strip().splitlines()[0]
        desc = first_line.strip()

    agent_dir.mkdir(parents=True, exist_ok=True)
    fpath = agent_dir / f"{name}.json"
    payload = {
        "name": name,
        "desc": desc,
        "req": req,
        "res": res,
    }
    fpath.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    logger.info("[DP] %s → %s::%s", _ts(), agent_dir.relative_to(_run.log_dir), name)
