"""
持仓数据读写（项目根 data/portfolio.json 手动维护）

格式（数组）：
[
  {"code": "600519", "name": "贵州茅台", "shares": 200, "cost_price": 1400.0,
   "last_price": 1501.0},   # last_price 可选，缺省按 cost_price 估值
  ...
]

缺失文件 / 解析失败 → 视为空仓（不阻塞前序流程）。
"""

import json
import logging
from pathlib import Path

from AI.utils.stock_utils import StockUtils

logger = logging.getLogger(__name__)

DEFAULT_PORTFOLIO_PATH = Path("data/portfolio.json")


def load_portfolio(path=None) -> list:
    """读取持仓明细 → [{"code", "name", "shares", "cost_price", "last_price"}]"""
    p = Path(path) if path else DEFAULT_PORTFOLIO_PATH
    if not p.exists():
        logger.info(f"[持仓] {p} 不存在，视为空仓")
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"[持仓] 持仓文件解析失败，视为空仓: {e}")
        return []
    if not isinstance(data, list):
        logger.warning("[持仓] 持仓文件格式错误（应为数组），视为空仓")
        return []

    holdings = []
    for item in data:
        if not isinstance(item, dict):
            logger.warning(f"[持仓] 持仓条目非法，跳过: {item}")
            continue
        try:
            code = StockUtils.normalize_code(str(item["code"]))
            shares = float(item["shares"])
            cost_price = float(item["cost_price"])
            last_price = item.get("last_price")
            last_price = float(last_price) if last_price is not None else None
        except (KeyError, TypeError, ValueError):
            logger.warning(f"[持仓] 持仓条目字段缺失/非法，跳过: {item}")
            continue
        holdings.append({
            "code": code,
            "name": str(item.get("name", "")),
            "shares": shares,
            "cost_price": cost_price,
            "last_price": last_price,
        })
    return holdings


def save_portfolio(holdings: list, path=None) -> bool:
    """写回持仓明细（占位：一期以手动维护为主，此函数供后续扩展）"""
    p = Path(path) if path else DEFAULT_PORTFOLIO_PATH
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(holdings, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return True
    except OSError as e:
        logger.warning(f"[持仓] 持仓文件写入失败: {e}")
        return False
