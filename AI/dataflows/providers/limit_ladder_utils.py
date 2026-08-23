"""
连板梯队归一化数据 → 晋级率/炸板率/格式化 纯函数模块（无 IO，便于单测）。

day_records 结构（由 TushareProvider.get_limit_up_ladder 归一化后传入，
按日期从早到晚排序）：

    {"date": "20260819",           # YYYYMMDD
     "zt_total": 36,               # 涨停家数（limit=='U'）
     "dt_total": 118,              # 跌停家数（limit=='D'）
     "ladder": {2: 8, 3: 4, 5: 1}, # 连板≥2 分档计数（不含首板）
     "open_broken": 6,             # 开板回封家数（limit=='U' 且 open_times>0）
     "zb_total": 12,               # 炸板未回封家数（limit=='Z'）
     "stock_boards": {"600000.SH": 2, "300001.SZ": 1},  # 当日涨停 {ts_code: 归一化连板数}
     "intraday": bool,             # 可选：True 时行尾标注"盘中"
    }
"""

# 两相邻采集日自然日间隔超过该值 → 视为不连续（长假期/缺数据日），晋级率输出 "--"
MAX_GAP_DAYS = 3

# 晋级率列定义：(昨日档位, 今日档位)；None 表示合并档（今日≥6板 且 昨日≥5板）
PROMOTION_COLUMNS = [(1, 2), (2, 3), (3, 4), (4, 5), (5, None)]


def _board_level_count(stock_boards: dict, level: int) -> int:
    """某档连板家数。level==1 时为'首板'口径：连板数 ≤1 的 ts_code 数。"""
    if level == 1:
        return sum(1 for b in stock_boards.values() if b <= 1)
    return sum(1 for b in stock_boards.values() if b == level)


def _gap_days(date_a: str, date_b: str) -> int:
    """两个 YYYYMMDD 日期之间的自然日间隔（date_b > date_a）。"""
    from datetime import datetime
    a = datetime.strptime(date_a, "%Y%m%d")
    b = datetime.strptime(date_b, "%Y%m%d")
    return (b - a).days


def calc_promotion_rates(day_records: list) -> list:
    """逐日晋级率矩阵：今日 L+1 板且昨日 L 板（ts_code 精确匹配）/ 昨日 L 板家数。

    返回与 day_records 等长的列表，每项：
        {"date": ..., "1→2": "3/8"|"--", "2→3": ..., "3→4": ..., "4→5": ..., "5→6+": ...}
    - 窗口首日（无昨日数据）恒为 "--"
    - 与上一采集日间隔 > MAX_GAP_DAYS 自然日（长假期/缺数据日）→ 该行全部 "--"
    - 分母为 0 → 该格 "--"（脚注说明当日无对应梯队）
    """
    result = []
    for i, rec in enumerate(day_records):
        rates = {}
        prev = day_records[i - 1] if i > 0 else None
        consecutive = (
            prev is not None
            and _gap_days(prev["date"], rec["date"]) <= MAX_GAP_DAYS
        )
        for from_level, to_level in PROMOTION_COLUMNS:
            if not consecutive:
                rates[_promo_label(from_level, to_level)] = "--"
                continue
            today_boards = rec["stock_boards"]
            prev_boards = prev["stock_boards"]
            if to_level is None:
                # 合并档："5→6+"：今日≥6板 且 昨日≥5板 / 昨日≥5板
                den = sum(1 for b in prev_boards.values() if b >= from_level)
                num = sum(
                    1 for code, b in today_boards.items()
                    if b >= 6 and prev_boards.get(code, 0) >= from_level
                )
            else:
                den = _board_level_count(prev_boards, from_level)
                num = sum(
                    1 for code, b in today_boards.items()
                    if b == to_level and prev_boards.get(code, 0) == from_level
                )
            rates[_promo_label(from_level, to_level)] = f"{num}/{den}" if den > 0 else "--"
        result.append({"date": rec["date"], **rates})
    return result


def _promo_label(from_level: int, to_level) -> str:
    """晋级率列标签：1→2 / … / 5→6+"""
    if to_level is None:
        return f"{from_level}→6+"
    return f"{from_level}→{to_level}"


def calc_break_rate(day_records: list) -> list:
    """逐日炸板率与开板回封率。

    炸板率 = zb_total / (zt_total + zb_total)（分母 zt_total 含开板回封、分子仅未回封）
    开板回封率 = open_broken / zt_total（辅助列）
    分母为 0 → 对应格 "--"。

    返回与 day_records 等长的列表，每项 {"date", "zb_rate", "open_back_rate"}。
    """
    result = []
    for rec in day_records:
        zt, zb = rec["zt_total"], rec["zb_total"]
        if zt + zb > 0:
            zb_rate = f"{zb / (zt + zb) * 100:.1f}%"
        else:
            zb_rate = "--"
        open_back_rate = (
            f"{rec['open_broken'] / zt * 100:.1f}%" if zt > 0 else "--"
        )
        result.append({
            "date": rec["date"],
            "zb_rate": zb_rate,
            "open_back_rate": open_back_rate,
        })
    return result


def _ladder_cell(rec: dict, level: int) -> int:
    """梯队表单元格：2~5 板取 ladder 分档，6 板+ 为 ≥6 合并计数。"""
    if level <= 5:
        return rec["ladder"].get(level, 0)
    return sum(n for lv, n in rec["ladder"].items() if lv >= 6)


def format_ladder_matrix(day_records: list, promo: list, break_rates: list,
                         skipped_dates: list = None) -> str:
    """将归一化梯队记录格式化为 Markdown（梯队表 + 晋级率矩阵 + 口径脚注）。

    列宽上限：梯队表与晋级率矩阵 ≥6 板合并为"6板+"单列（情绪高潮期最高板可达 10+ 板）。
    skipped_dates: 采集中被跳过的无数据日期（YYYYMMDD），有值时追加数据缺口脚注。
    """
    n = len(day_records)
    lines = [f"# 近 {n} 日连板梯队与情绪数据"]

    # ---- 每日连板梯队 ----
    lines.append("\n## 每日连板梯队")
    lines.append("| 日期 | 涨停总数 | 跌停数 | 首板 | 2板 | 3板 | 4板 | 5板 | 6板+ "
                 "| 最高板(家数) | 开板回封(家数/率) | 炸板数 | 炸板率 |")
    lines.append("|------|---------|--------|------|-----|-----|-----|-----|------"
                 "|------------|------------------|--------|--------|")
    for i, rec in enumerate(day_records):
        first_boards = _board_level_count(rec["stock_boards"], 1)
        ladder = rec["ladder"]
        if ladder:
            max_level = max(ladder)
            highest = f"{max_level}板({ladder[max_level]})"
        elif first_boards > 0:
            highest = f"1板({first_boards})"
        else:
            highest = "--"
        open_back_rate = break_rates[i]["open_back_rate"]
        date_cell = rec["date"] + ("（盘中）" if rec.get("intraday") else "")
        lines.append(
            f"| {date_cell} | {rec['zt_total']} | {rec['dt_total']} | {first_boards} "
            f"| {_ladder_cell(rec, 2)} | {_ladder_cell(rec, 3)} | {_ladder_cell(rec, 4)} "
            f"| {_ladder_cell(rec, 5)} | {_ladder_cell(rec, 6)} | {highest} "
            f"| {rec['open_broken']}({open_back_rate}) | {rec['zb_total']} "
            f"| {break_rates[i]['zb_rate']} |"
        )

    # ---- 晋级率矩阵 ----
    lines.append("\n## 晋级率矩阵（精确个股匹配）")
    lines.append("| 日期 | 1→2 | 2→3 | 3→4 | 4→5 | 5→6+ |")
    lines.append("|------|-----|-----|-----|-----|-------|")
    for i, rec in enumerate(day_records):
        date_cell = rec["date"] + ("（盘中）" if rec.get("intraday") else "")
        p = promo[i]
        lines.append(
            f"| {date_cell} | {p['1→2']} | {p['2→3']} | {p['3→4']} | {p['4→5']} | {p['5→6+']} |"
        )

    # ---- 口径脚注 ----
    lines.append(
        "\n> 口径脚注：晋级率 = 今日L板且昨日L-1板家数 / 昨日L-1板家数"
        "（\"5→6+\"为合并口径：分子为今日≥6板且昨日≥5板家数，分母为昨日≥5板家数）；"
        "炸板率 = 炸板家数/(涨停+炸板)，分母 zt_total 含开板回封、分子仅未回封"
        "（开板回封列输出\"家数(率)\"）；"
        "梯队与晋级率均为全市场混合口径（含 20cm/ST），情绪周期判定优先看主板 10cm 结构；"
        "晋级率需连续两日数据，首日/缺日对应行输出 `--`；"
        "当日数据盘中未定稿时行尾标注\"盘中\""
    )
    if skipped_dates:
        lines.append(
            "> 数据缺口：以下日期无数据已跳过：" + "、".join(sorted(skipped_dates))
        )
    return "\n".join(lines)
