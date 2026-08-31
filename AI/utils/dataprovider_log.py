"""
数据提供器（dataprovider）调用日志
在 interface 层记录每次 dataprovider 接口调用，写入所属 Agent 的日志目录。

新格式（每次调用一个目录，序号按所属 Agent 目录独立计数，重复调用不覆盖）：

  logs/{时间戳}/{layer}/{seq:03d}_{NodeName}/{seq:03d}_{接口名}/
    ├── req.json     ← 绑定后的完整入参
    ├── res.md       ← 结果为 str（多数接口，markdown 契约）
    ├── res.json     ← 结果为非 str（如 get_stock_info 返回 dict，二选一）
    ├── meta.json    ← {name, desc, seq, ts, res: 实际结果文件名}
    └── tushare/     ← tushare 端点子日志（仅 tushare 数据源，见 wrap_tushare_api）
        └── {seq:03d}_{api_name}/
            ├── req.json   ← {"api_name", "fields", "params"}
            ├── res.json   ← DataFrame → {"columns", "shape", "records", "row_count",
            │                 "truncated"}；调用异常 → {"error": ...}
            └── meta.json  ← {name, seq, ts, res, probe, error?}

旧格式 {接口名}.json（{name, desc, req, res}）仅存在于历史 run，由 AI/logviewer 兼容展示。

归属判定：
- track_node 装饰器为每个图节点设置当前节点上下文（contextvar）；
- 接口调用发生时：
  1) 当前节点与最近一次 LLM 目录一致 → 复用该目录；
  2) 当前节点已知（尚无 LLM 调用）→ 按 layer + 预测序号创建目录；
  3) 无节点上下文（如 ToolNode 内部）→ 回退到最近一次 LLM 目录。

控制台仅输出一行摘要。

调试步进模式（LIVEPROFIT_DEBUG_STEP=true）下，每次调用落盘后挂 DP 响应检查点
（见 AI/utils/step_gate.py）。

tushare 端点子日志语义：
- 仅在 tushare 数据源 + 调用发生在 @dataprovider_log 装饰函数内（DP 调用目录已创建）时落盘；
  run 外/非装饰路径的端点调用透传不落盘
- 超时补写：_api_call 超时返回 None 后，线程池 worker 仍会完成本次端点调用并补写
  tushare/ 日志（上下文在 submit 时已复制进 worker，归属正确）——「DP res 显示超时、
  tushare 展开却有成功记录」并存属预期诊断行为
- 连通性探测调用（TushareProvider._connect 的 stock_basic limit=1）在 DP 上下文内会被
  记录，meta 带 probe=true，viewer 加「🔌 连通性探测」角标
- 环境变量 LIVEPROFIT_TUSHARE_LOG=0 关闭 tushare 子日志
- 单次结果 records 超 500 行截断（truncated=true + row_count 元数据），防全市场快照类
  接口撑爆 run 目录
"""

import functools
import inspect
import json
import logging
import os
import re
import threading
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Callable, Dict, Optional

# 与 llm_callbacks 共享运行状态（log_dir / llm_seq / last_llm_dir / dp_counters）
from AI.utils.llm_callbacks import _run, _NODE_LAYER, _safe_json, _sanitize, _ts
from AI.utils import step_gate

logger = logging.getLogger(__name__)

# 当前正在执行的图节点名
_current_node: ContextVar[str] = ContextVar("liveprofit_current_node", default="")

# 当前 dataprovider 调用的落盘上下文（装饰函数执行期间由 dataprovider_log 设置，
# 供 tushare 端点子日志挂靠；run 外/非装饰路径为 None）
_current_dp_call: ContextVar[Optional["_DpCallContext"]] = ContextVar(
    "liveprofit_current_dp_call", default=None)


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


class _DpCallContext:
    """一次 dataprovider 调用的落盘上下文：调用目录 + tushare 子调用序号。

    嵌套装饰调用靠 contextvar token set/reset 恢复外层。序号计数加锁：
    端点 A 超时后其 worker 仍存活，主线程发起的端点 B 会再开一个 worker，
    两个 worker 可并发递增同一计数器（2026-08-25 code review 发现）。
    """

    def __init__(self, call_dir: Path):
        self.dir = call_dir
        self._tushare_seq = 0
        self._tushare_lock = threading.Lock()

    def next_tushare(self) -> int:
        """tushare 端点子调用序号，按所属 DP 调用独立从 001 计数"""
        with self._tushare_lock:
            self._tushare_seq += 1
            return self._tushare_seq


def dataprovider_log(fn: Callable) -> Callable:
    """装饰器：记录 dataprovider 接口调用 → {seq:03d}_{接口名}/ 目录。

    fn 执行前先建目录（写 req.json）并设置 _current_dp_call 上下文，
    tushare 端点子日志在 fn 执行期间经该上下文挂靠到 tushare/ 子目录。
    fn 抛异常时目录保留 req.json + res.json({"error"})（可调试），异常原样上抛。
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        req = _bind_req(fn, args, kwargs)
        call_dir = _prepare_call(fn.__name__, req)
        token = _current_dp_call.set(_DpCallContext(call_dir)) \
            if call_dir is not None else None
        try:
            try:
                result = fn(*args, **kwargs)
            except Exception as e:
                if call_dir is not None:
                    _finalize_call(call_dir, fn.__name__, fn.__doc__, req,
                                   None, error=e)
                raise
            if call_dir is not None:
                written = _finalize_call(call_dir, fn.__name__, fn.__doc__,
                                         req, result)
                # 调试步进：DP 响应检查点（仅成功路径；异常隔离不干扰数据流）
                if written is not None:
                    try:
                        rel = str(call_dir.relative_to(_run.log_dir)).replace("\\", "/")
                        step_gate.checkpoint(
                            "dp",
                            layer=rel.split("/")[0],
                            node=_current_node.get() or "unknown",
                            name=fn.__name__,
                            dir=rel,
                            show_file=written[1],
                        )
                    except Exception as e:
                        logger.debug(f"[DP] 步进检查点失败 {fn.__name__}: {e}")
            return result
        finally:
            if token is not None:
                _current_dp_call.reset(token)

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


def _prepare_call(name: str, req: Dict[str, Any]) -> Optional[Path]:
    """fn 执行前创建 {seq:03d}_{接口名}/ 目录并写 req.json，返回 call_dir
    （供 tushare 子日志挂靠）。

    日志目录未初始化或落盘失败时返回 None（fn 照常执行，不落盘）。
    """
    agent_dir = _agent_dir()
    if agent_dir is None:
        logger.debug(f"[DP] 跳过 {name}：日志目录未初始化")
        return None
    try:
        agent_dir.mkdir(parents=True, exist_ok=True)
        # 序号 key 用归一化的相对路径，同一 Agent 目录内独立计数
        rel = str(agent_dir.relative_to(_run.log_dir)).replace("\\", "/")
        seq = _run.next_dp(rel)
        call_dir = agent_dir / f"{seq:03d}_{_sanitize(name)}"
        call_dir.mkdir(parents=True, exist_ok=True)
        (call_dir / "req.json").write_text(_safe_json(req), encoding="utf-8")
        return call_dir
    except Exception as e:
        logger.debug(f"[DP] 目录创建失败 {name}: {e}")
        return None


def _finalize_call(call_dir: Path, name: str, doc: Optional[str],
                   req: Dict[str, Any], res: Any,
                   error: Optional[Exception] = None) -> Optional[tuple]:
    """写入 res + meta.json 到调用目录，返回 (call_dir, res_file) 供步进检查点。

    error 非 None 时（fn 抛异常）写 res.json {"error"} + meta 标记 error。
    落盘失败返回 None，不抛异常（日志故障不干扰数据流）。
    """
    try:
        # desc = docstring 首行
        desc = ""
        if doc:
            first_line = doc.strip().splitlines()[0]
            desc = first_line.strip()

        if error is not None:
            res_file = "res.json"
            (call_dir / res_file).write_text(
                _safe_json({"error": f"{type(error).__name__}: {error}"}),
                encoding="utf-8")
            meta_extra: Dict[str, Any] = {"error": True}
        elif isinstance(res, str):
            # res：str（markdown 契约）→ res.md；非 str → res.json
            res_file = "res.md"
            (call_dir / res_file).write_text(res, encoding="utf-8")
            meta_extra = {}
        else:
            res_file = "res.json"
            (call_dir / res_file).write_text(_safe_json(res), encoding="utf-8")
            meta_extra = {}

        (call_dir / "meta.json").write_text(_safe_json({
            "name": name,
            "desc": desc,
            "seq": int(call_dir.name[:3]),
            "ts": _ts(),
            "res": res_file,
            **meta_extra,
        }), encoding="utf-8")

        rel = str(call_dir.parent.relative_to(_run.log_dir)).replace("\\", "/")
        logger.info("[DP] %s → %s::%s::%03d", _ts(), rel, name,
                    int(call_dir.name[:3]))
        return call_dir, res_file
    except Exception as e:
        logger.debug(f"[DP] 日志写入失败 {name}: {e}")
        return None


def _write(name: str, doc: Optional[str], req: Dict[str, Any],
           res: Any) -> Optional[tuple]:
    """旧签名入口（= prepare + finalize），供直接调用方与既有测试使用"""
    call_dir = _prepare_call(name, req)
    if call_dir is None:
        return None
    return _finalize_call(call_dir, name, doc, req, res)


# ==================== tushare 端点子日志 ====================

# 环境变量 LIVEPROFIT_TUSHARE_LOG=0 关闭（模块导入时读一次）
_TUSHARE_LOG_ENABLED = os.getenv("LIVEPROFIT_TUSHARE_LOG", "1").lower() \
    not in ("0", "false", "no")
# 单次端点结果 records 截断上限（行），防全市场快照类接口撑爆 run 目录
_TUSHARE_RES_MAX_ROWS = 500


def wrap_tushare_api(api: Any) -> Any:
    """包装 tushare DataApi 实例：拦截所有端点调用并写入当前 DP 调用目录的
    tushare/ 子目录（{seq:03d}_{api_name}/）。

    tushare 库内 query 是唯一网络出口（__getattr__ 返回 partial(self.query, name)，
    属性访问时求值），实例属性覆盖 api.query 即可全量拦截。

    - 无 DP 调用上下文（run 外/非装饰调用）→ 透传不落盘
    - 幂等：已包装过的实例直接返回
    - 落盘失败吞掉（debug 日志），不干扰数据流
    - 端点抛异常（如返回 code!=0）：写 res.json {"error"} 后原样 re-raise
    - 连通性探测：api._lp_probe_next 标志（一次性消费）→ meta.probe=true
    """
    if not _TUSHARE_LOG_ENABLED:
        return api
    # tushare DataApi 的 __getattr__ 对任意未知属性返回 partial(self.query, name)
    # （属性访问时求值），getattr 判定幂等/探测标志会拿到 truthy 的 partial 误判——
    # 必须查实例 __dict__（实例属性优先于 __getattr__，赋值与读取都落在 __dict__）
    if "_lp_tushare_logged" in getattr(api, "__dict__", {}):
        return api
    if not callable(getattr(api, "query", None)):
        return api

    _orig_query = api.query

    def logged_query(api_name, fields="", **kwargs):
        ctx = _current_dp_call.get()
        probe = "_lp_probe_next" in getattr(api, "__dict__", {})
        if probe:
            try:
                del api._lp_probe_next
            except Exception:
                pass
        try:
            result = _orig_query(api_name, fields, **kwargs)
        except Exception as e:
            if ctx is not None:
                _write_tushare_call(ctx, api_name, fields, kwargs,
                                    error=e, probe=probe)
            raise
        if ctx is not None:
            _write_tushare_call(ctx, api_name, fields, kwargs,
                                result=result, probe=probe)
        return result

    try:
        api.query = logged_query
        api._lp_tushare_logged = True
    except Exception:
        pass  # 个别对象不允许设属性：放弃包装（透传不落盘），不干扰连接初始化
    return api


def _write_tushare_call(ctx: "_DpCallContext", api_name: str, fields: str,
                        kwargs: Dict[str, Any], result: Any = None,
                        error: Optional[Exception] = None,
                        probe: bool = False) -> None:
    """把一次 tushare 端点调用写入 {ctx.dir}/tushare/{seq:03d}_{api_name}/。

    error 非 None 时写 {"error"}（由 logged_query re-raise）；result 为 DataFrame
    时归一为 {"columns", "shape", "records", "row_count", "truncated"}。
    落盘失败吞掉（debug 日志），不干扰数据流。
    """
    try:
        seq = ctx.next_tushare()
        tdir = ctx.dir / "tushare" / f"{seq:03d}_{_sanitize(api_name)}"
        tdir.mkdir(parents=True, exist_ok=True)
        (tdir / "req.json").write_text(_safe_json({
            "api_name": api_name,
            "fields": fields,
            "params": kwargs,
        }), encoding="utf-8")
        if error is not None:
            res_payload: Dict[str, Any] = {
                "error": f"{type(error).__name__}: {error}",
                "api_name": api_name,
            }
            meta_payload: Dict[str, Any] = {"error": True}
        else:
            res_payload = _norm_tushare_res(result)
            meta_payload = {}
        (tdir / "res.json").write_text(_safe_json(res_payload), encoding="utf-8")
        (tdir / "meta.json").write_text(_safe_json({
            "name": api_name,
            "seq": seq,
            "ts": _ts(),
            "res": "res.json",
            "probe": probe,
            **meta_payload,
        }), encoding="utf-8")
        logger.info("[TS] %s → %s::%03d %s", _ts(), ctx.dir.name, seq, api_name)
    except Exception as e:
        logger.debug(f"[TS] 子日志写入失败 {api_name}: {e}")


def _norm_tushare_res(result: Any) -> Any:
    """tushare 端点结果归一：DataFrame → {"columns", "shape", "records",
    "row_count", "truncated"}（records 超上限截断）；其他类型原样返回。

    经 df.to_json 归一（NaN→null、numpy 标量→Python 标量），避免 _safe_json
    对 DataFrame 整体退化为 str 产生坏日志；归一失败写 {"error"} 保持目录完整
    （不丢半成品目录）。
    """
    if not hasattr(result, "to_json"):  # 非 DataFrame（dict/list/None 等）
        return result
    try:
        # default_handler=str：±inf 等 JSON 非法值兜底，防整批 records 丢失
        records = json.loads(result.to_json(
            orient="records", force_ascii=False, default_handler=str))
        # Series 等非 DataFrame 对象无 columns/shape：getattr 兜底
        columns = [str(c) for c in getattr(result, "columns", [])]
        shape = getattr(result, "shape", None)
        shape = [int(shape[0]), int(shape[1])] if shape is not None \
            else [len(records), len(columns)]
    except Exception as e:
        return {"error": f"结果归一失败: {type(e).__name__}: {e}",
                "raw_type": type(result).__name__}
    row_count = len(records)
    truncated = row_count > _TUSHARE_RES_MAX_ROWS
    if truncated:
        records = records[:_TUSHARE_RES_MAX_ROWS]
    return {
        "columns": columns,
        "shape": shape,
        "records": records,
        "row_count": row_count,
        "truncated": truncated,
    }
