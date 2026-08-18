"""
LiveProfit 简易日志初始化
使用 Python 标准 logging 模块，无需复杂的管理器。
"""

import logging
import sys
import io


def init_logging(level="INFO"):
    """初始化日志系统"""
    # Windows GBK 编码兼容：强制 stdout 使用 UTF-8，避免 Unicode 字符写入失败
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
        force=True,
    )


def get_logger(name="liveprofit"):
    """获取指定名称的 logger"""
    return logging.getLogger(name)
