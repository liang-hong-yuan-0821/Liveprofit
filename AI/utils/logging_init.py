"""
YoHo 简易日志初始化
使用 Python 标准 logging 模块，无需复杂的管理器。
"""

import logging
import sys


def init_logging(level="INFO"):
    """初始化日志系统"""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
        force=True,
    )


def get_logger(name="yoho"):
    """获取指定名称的 logger"""
    return logging.getLogger(name)
