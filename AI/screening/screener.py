"""
选股层 — 核心筛选逻辑（纯函数，无 LLM）

"从数字里筛数字"是确定性运算：涨幅排名、超均值筛选、流动性过滤
全部可回溯、可复现。数据获取与解析分离，本模块只做解析与筛选。
"""

import logging
import re

from AI.utils.stock_utils import StockUtils

logger = logging.getLogger(__name__)

_CODE_RE = re.compile(r"^\d{6}$")

# 数据接口错误返回的关键词（命中即视为不可用）
_DATA_ERROR_KEYWORDS = ("未连接", "未获取", "无法获取", "未安装", "失败", "不支持", "数据不可用")


def _is_error_text(text: str) -> bool:
    return not text or any(kw in text for kw in _DATA_ERROR_KEYWORDS)


def parse_constituents(text: str) -> list:
    """解析成分股行 `代码|名称` → [{"code": "000422", "name": "湖北宜化"}]。

    仅保留 6 位数字代码行；错误文本返回空列表。
    """
    out = []
    if _is_error_text(text):
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        code, _, name = line.partition("|")
        code, name = code.strip(), name.strip()
        if _CODE_RE.fullmatch(code):
            out.append({"code": code, "name": name})
    return out


def parse_ranking(text: str) -> tuple:
    """解析排名文本 `代码|近N日涨幅%|最新价|最新成交额`（末行板块均值）。

    Returns:
        (rows, sector_avg)：rows 为 [{code, pct_change, last_close, last_amount}]，
        sector_avg 为板块内均值（解析失败时为 0.0）。
    """
    rows = []
    avg = 0.0
    if _is_error_text(text):
        return rows, avg
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("板块均值"):
            try:
                avg = float(line.split("|")[1])
            except (IndexError, ValueError):
                pass
            continue
        parts = line.split("|")
        if len(parts) < 4:
            continue
        try:
            code = parts[0].strip()
            pct_change = float(parts[1])
            last_close = float(parts[2])
            last_amount = float(parts[3])
        except ValueError:
            continue
        rows.append({
            "code": code,
            "pct_change": pct_change,
            "last_close": last_close,
            "last_amount": last_amount,
        })
    return rows, avg


def filter_and_score(rows: list, sector_avg: float, name_map: dict,
                     min_turnover: float) -> list:
    """超板块均值筛选 + 流动性过滤，按 vs_avg 降序。

    - vs_avg = 个股近 N 日涨幅 - 板块内成分股均值，须 > 0
    - 剔除名称含 ST / 退 的个股
    - 剔除最新成交额 < min_turnover 的个股
    """
    out = []
    for row in rows:
        code = StockUtils.normalize_code(row["code"])
        name = name_map.get(code, "")
        if "ST" in name or "退" in name:
            continue
        if row["last_amount"] < min_turnover:
            continue
        vs_avg = row["pct_change"] - sector_avg
        if vs_avg <= 0:
            continue
        out.append({
            "code": code,
            "name": name,
            "pct_change": round(row["pct_change"], 2),
            "sector_avg_pct": round(sector_avg, 2),
            "vs_avg": round(vs_avg, 2),
            "last_close": row["last_close"],
        })
    out.sort(key=lambda x: x["vs_avg"], reverse=True)
    return out
