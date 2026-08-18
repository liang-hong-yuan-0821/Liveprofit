"""
LiveProfit 环境变量解析工具
参考 TradingAgents-CN 实现，兼容 Python 3.13+。
"""

import os
from typing import Optional, List, Dict, Any


def parse_bool_env(env_var: str, default: bool = False) -> bool:
    """解析布尔环境变量"""
    val = os.getenv(env_var)
    if val is None:
        return default
    val = val.strip().lower()
    truthy = {"true", "1", "yes", "on", "enable", "enabled", "t", "y", "ok", "okay"}
    falsy = {"false", "0", "no", "off", "disable", "disabled", "f", "n", "none", "null", "nil"}
    if val in truthy:
        return True
    if val in falsy:
        return False
    return default


def parse_int_env(env_var: str, default: int = 0) -> int:
    """解析整数环境变量"""
    val = os.getenv(env_var)
    if val is None:
        return default
    try:
        return int(val.strip())
    except (ValueError, TypeError):
        return default


def parse_float_env(env_var: str, default: float = 0.0) -> float:
    """解析浮点环境变量"""
    val = os.getenv(env_var)
    if val is None:
        return default
    try:
        return float(val.strip())
    except (ValueError, TypeError):
        return default


def parse_str_env(env_var: str, default: str = "") -> str:
    """解析字符串环境变量"""
    val = os.getenv(env_var)
    return val.strip() if val else default


def parse_list_env(env_var: str, default: Optional[List[str]] = None, sep: str = ",") -> List[str]:
    """解析列表环境变量"""
    if default is None:
        default = []
    val = os.getenv(env_var)
    if val is None:
        return default
    return [item.strip() for item in val.split(sep) if item.strip()]
