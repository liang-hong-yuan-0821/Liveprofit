"""
板块逐日涨跌幅矩阵共享纯函数模块（仿 limit_ladder_utils.py 先例）

供 AKShareProvider / TushareProvider 的板块层接口复用（get_industry_sector_performance /
get_concept_board_heat_rank 的「近 10 个交易日逐日涨跌幅」章节）。

日期规范：模块内部一律使用 "YYYYMMDD" 规范串（字典序即时间序）；
展示时经 fmt_date_short 转 "MM-DD"。

缺口口径（compute_gap_dates 为唯一权威定义）：窗口 = 覆盖日期并集的日历跨度
[min, max]，gap = 窗口内全部周一~周五 − 覆盖日期并集（周末排除；全市场缺失的
工作日含节假日入清单），与 Tushare 采集器 skipped 语义一致。
"""

from datetime import datetime, timedelta

import pandas as pd

# 展示列数与数据窗口：固定最近 10 个交易日（days 参数更大时也不超 10 列）
DAILY_COLS = 10


def fmt_date_short(d) -> str:
    """任意日期输入（"20260819" / "2026-08-19" / datetime / Timestamp）→ "MM-DD"。"""
    try:
        return pd.to_datetime(d).strftime("%m-%d")
    except Exception:
        return str(d)


def _to_canonical(d) -> str:
    """任意日期输入 → "YYYYMMDD" 规范串。"""
    return pd.to_datetime(d).strftime("%Y%m%d")


def daily_pct_from_closes(dates: list, closes: list) -> dict:
    """相邻收盘价计算逐日涨跌幅，返回 {YYYYMMDD: pct(%)}（11 行收盘 → 10 个 pct）。

    前一日收盘缺失/非正、当日收盘为 NaN 时跳过该日（不放入结果）。
    """
    result: dict = {}
    for i in range(1, min(len(dates), len(closes))):
        prev, cur = closes[i - 1], closes[i]
        try:
            prev_f, cur_f = float(prev), float(cur)
        except (TypeError, ValueError):
            continue
        if prev_f > 0 and pd.notna(cur_f):
            result[_to_canonical(dates[i])] = (cur_f - prev_f) / prev_f * 100
    return result


def master_dates_from_series(series_map: dict, n: int = DAILY_COLS,
                             min_coverage: float = 0.5) -> list:
    """逐日表的日期轴（升序，YYYYMMDD）。

    优先取最长序列的最近 n 天；当覆盖全部候选日期的序列占比 < min_coverage 时，
    改用「覆盖占比 ≥ min_coverage 的最近 n 天」口径；仍取不满则退回最长序列口径。
    """
    if not series_map:
        return []
    total = len(series_map)
    longest = max(series_map.values(), key=len)
    candidate = sorted(longest.keys())[-n:] if len(longest) >= n else sorted(longest.keys())
    if len(candidate) >= 2:
        full_cover = sum(
            1 for s in series_map.values() if all(d in s for d in candidate))
        if full_cover / total >= min_coverage:
            return candidate

    # 兜底口径：从最新日期往回挑覆盖占比达标的日期
    all_dates = sorted({d for s in series_map.values() for d in s}, reverse=True)
    picked = []
    for d in all_dates:
        if len(picked) >= n:
            break
        cover = sum(1 for s in series_map.values() if d in s)
        if cover / total >= min_coverage:
            picked.append(d)
    return sorted(picked) if picked else candidate


def compute_gap_dates(window_start: str, window_end: str, covered_dates) -> list:
    """缺口清单的唯一权威定义：窗口 [window_start, window_end]（YYYYMMDD）内
    全部周一~周五 − 覆盖日期并集（周末排除；全市场缺失的工作日含节假日入清单）。

    行业/AKShare 路径的窗口端点取覆盖日期并集的 min/max；
    概念路径的 skipped 与本地结果语义一致，互验见测试。
    """
    covered = set(covered_dates)
    gaps = []
    try:
        d = datetime.strptime(window_start, "%Y%m%d")
        end = datetime.strptime(window_end, "%Y%m%d")
    except ValueError:
        return []
    while d <= end:
        if d.weekday() < 5 and d.strftime("%Y%m%d") not in covered:
            gaps.append(d.strftime("%Y%m%d"))
        d += timedelta(days=1)
    return gaps


def cum_pct_from_daily(pcts: dict) -> float | None:
    """逐日 pct 复利 → 区间累计涨跌幅(%)；空序列返回 None。"""
    if not pcts:
        return None
    acc = 1.0
    for p in pcts.values():
        acc *= 1 + p / 100
    return (acc - 1) * 100


def format_daily_matrix(rows, master_dates, *, title: str, caption: str,
                        intraday_date: str | None = None,
                        cum_label: str = "近10日累计",
                        gap_dates=None, name_col: str = "板块") -> str | None:
    """逐日涨跌幅矩阵 Markdown 段格式化。

    rows = [(name, {YYYYMMDD: pct}, cum_pct|None), ...]（行序即展示序）
    master_dates 少于 2 个 → None（调用方决定省略 + 附注）。
    gap_dates 统一按展示窗口 [master_dates 首末] 过滤后再生成脚注
    （行业路径结果本就 ⊆ 窗口，过滤幂等；概念路径 skipped 中的窗口外
    探测日期如运行当天无数据被剔除）。
    """
    if len(master_dates) < 2:
        return None
    win_start, win_end = master_dates[0], master_dates[-1]
    gaps = sorted(d for d in (gap_dates or []) if win_start <= d <= win_end)

    lines = [f"## {title}", f"（{caption}）", ""]
    header = f"| {name_col} | " + " | ".join(
        fmt_date_short(d) + ("（盘中）" if d == intraday_date else "")
        for d in master_dates) + f" | {cum_label} |"
    lines.append(header)
    lines.append("|" + "------|" * (len(master_dates) + 2))
    for name, pcts, cum in rows:
        cells = [f"{pcts[d]:+.1f}%" if d in pcts else "—" for d in master_dates]
        cum_cell = f"{cum:+.2f}%" if cum is not None else "—"
        lines.append(f"| {name} | " + " | ".join(cells) + f" | {cum_cell} |")

    if gaps:
        lines.append("")
        lines.append("> 数据缺口：以下日期无数据已跳过：" + "、".join(fmt_date_short(d) for d in gaps))
    return "\n".join(lines)
