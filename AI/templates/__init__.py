"""
输出格式模板库。

存放各 Agent 的"输出格式"Markdown 模板，运行时通过 load_output_format() 读取。
按 market/sector/stock 三层分子文件夹，与 marketAgents/sectorAgents/stockAgents 一一对应。
"""

import functools
from pathlib import Path

_TEMPLATES_DIR = Path(__file__).parent


@functools.lru_cache(maxsize=None)
def load_output_format(layer: str, template_name: str) -> str:
    """读取指定层级、指定 agent 的输出格式模板。

    Args:
        layer: "market" | "sector" | "stock"
        template_name: 文件名（不含 .md 后缀），
            如 "cn_tech_analyst"、"tech_common"

    Returns:
        md 文件内容（已去除首尾空白）

    Raises:
        FileNotFoundError: 对应的 md 文件不存在时直接抛出，
            不做静默兜底，避免"输出格式悄悄变空"的隐蔽问题。
    """
    path = _TEMPLATES_DIR / layer / f"{template_name}.md"
    return path.read_text(encoding="utf-8").strip()
