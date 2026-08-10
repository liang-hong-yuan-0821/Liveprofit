"""
YoHo 调用栈追踪工具
提供装饰器和工具函数，帮助理解项目运行时的调用栈和参数流转。

使用方式：
    1. 装饰器:  @trace_call 自动打印函数入口/出口 + 耗时 + 返回值摘要
    2. 手动:    trace_state("步骤名", key=val) 在关键节点记录状态

设置环境变量 YOHO_TRACE=0 可关闭追踪输出。
"""

import os
import time
import functools
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

# 通过环境变量控制开关
_TRACE_ENABLED = os.getenv("YOHO_TRACE", "1") != "0"
_INDENT = 0  # 全局缩进，树状显示调用层级
_INDENT_CHAR = "│  "

# ---- 颜色方案：深蓝系（兼容深浅色终端） ----
CALL = "\033[38;5;39m"       # 浅蓝 — 函数入口/出口
STEP = "\033[38;5;33m"       # 中蓝 — 流程步骤
PARAM = "\033[38;5;75m"      # 亮蓝 — 参数名/值
VALUE = "\033[38;5;111m"     # 浅蓝灰 — 返回值
DURATION = "\033[38;5;69m"   # 灰蓝 — 耗时
RESET = "\033[0m"


def _format_val(v, max_len=120):
    """安全格式化值，截断过长内容"""
    s = str(v)
    if len(s) > max_len:
        s = s[:max_len] + f"...(总长{len(s)})"
    return s


def _fmt_params(params: dict) -> str:
    """格式化参数字典"""
    if not params:
        return ""
    parts = []
    for k, v in params.items():
        parts.append(f"{CALL}{k}{RESET}={PARAM}{_format_val(v)}{RESET}")
    return ", ".join(parts)


def trace_call(label: str = None, show_params: list = None, show_result: bool = False):
    """调用栈追踪装饰器

    Args:
        label: 自定义标签（默认用函数名）
        show_params: 要打印的参数名列表（自动从同名参数取值）
        show_result: 是否在退出时打印返回值
    """
    def decorator(func: Callable) -> Callable:
        nonlocal label
        if label is None:
            label = func.__name__

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            if not _TRACE_ENABLED:
                return func(*args, **kwargs)

            global _INDENT
            indent = _INDENT_CHAR * _INDENT

            # ---- 入口 ----
            arg_parts = []
            if show_params:
                import inspect
                sig = inspect.signature(func)
                bound = sig.bind(*args, **kwargs)
                bound.apply_defaults()
                for p in show_params:
                    if p in bound.arguments:
                        arg_parts.append(
                            f"{CALL}{p}{RESET}={PARAM}{_format_val(bound.arguments[p])}{RESET}"
                        )
            arg_str = ", ".join(arg_parts)
            logger.info(
                f"{indent}{CALL}▶ {label}{RESET}"
                + (f"({arg_str})" if arg_str else "")
            )

            _INDENT += 1
            t0 = time.perf_counter()

            try:
                result = func(*args, **kwargs)
                elapsed = time.perf_counter() - t0
                _INDENT -= 1

                # ---- 出口 ----
                suffix = f" {DURATION}{elapsed:.2f}s{RESET}"
                if show_result:
                    suffix += f" → {VALUE}{_format_val(result, 80)}{RESET}"
                logger.info(
                    f"{indent}{CALL}◀ {label}{RESET}{suffix}"
                )
                return result
            except Exception:
                elapsed = time.perf_counter() - t0
                _INDENT -= 1
                logger.info(
                    f"{indent}{CALL}◀ {label}{RESET} {DURATION}{elapsed:.2f}s{RESET} ❌异常"
                )
                raise

        return wrapper
    return decorator


def trace_step(step_name: str, **kwargs):
    """手动记录流程步骤

    用法：
        trace_step("分析师开始", analyst="Market", company="000001.SZ")
    """
    if not _TRACE_ENABLED:
        return
    indent = _INDENT_CHAR * _INDENT
    parts = []
    for k, v in kwargs.items():
        parts.append(f"{CALL}{k}{RESET}={PARAM}{_format_val(v)}{RESET}")
    extra = "  " + ", ".join(parts) if parts else ""
    logger.info(f"{indent}{STEP}● {step_name}{RESET}{extra}")


def trace_state(state: dict, title: str = "状态快照", keys: list = None):
    """打印状态字典的关键字段

    用法：
        trace_state(final_state, "最终状态", keys=["market_report", "final_trade_decision"])
    """
    if not _TRACE_ENABLED:
        return
    indent = _INDENT_CHAR * (_INDENT + 1)
    logger.info(f"{INDENT_CHAR * _INDENT}{STEP}▾ {title}{RESET}")
    if keys is None:
        keys = [
            "company_of_interest", "trade_date",
            "international_news_report", "cn_news_report", "cn_tech_report",
            "sector_news_report", "sector_tech_report", "rotation_prediction_report",
            "stock_tech_report", "sentiment_report", "news_report",
            "fundamentals_report",
            "trader_investment_plan", "final_trade_decision",
        ]
    for key in keys:
        val = state.get(key, "<缺失>")
        if val:
            preview = _format_val(val, 200)
            logger.info(f"{indent}{PARAM}{key}{RESET}: {preview}")
        else:
            logger.info(f"{indent}{PARAM}{key}{RESET}: <空>")
