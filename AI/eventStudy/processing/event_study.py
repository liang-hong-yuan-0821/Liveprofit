"""
事件影响标注模块（方案 3.6，核心）

事件研究法（市场模型 OLS）：
- 估计窗口（事件前 gap_days+estimation_days ~ gap_days 天）的资产收益率对
  市场指数收益率 OLS 回归，得 α、β
- 事件窗口异常收益 AR = 资产实际收益 - (α + β × 市场收益)
- CAR = 事件窗口 AR 之和
- 方向判定：CAR > 0.5% 且 t > 1.96 利好；CAR < -0.5% 且 t < -1.96 利空；否则中性

t0 规则（B3）：盘前（09:30 前）公布 → t0 = 当日；盘中/盘后/非交易日 → 下一交易日。
所有窗口（pre_event_5d / event_day / post_event_5d）均相对 t0 在 market_data 中切分。

结果由 impact_writer 写入 Redis 草稿（event_impacts:draft:{event_id}），
经人工确认后由 review 模块落表。
"""

import logging
import math
from datetime import date, datetime, timedelta

import pandas as pd
import statsmodels.api as sm

from AI.eventStudy.collectors.config import (
    CAR_DIRECTION_THRESHOLD, CONTAMINATION_MIN_IMPORTANCE,
    ESTIMATION_DAYS, GAP_DAYS, POST_EVENT_DAYS, PRE_EVENT_DAYS,
    PRE_MARKET_CUTOFF, T_STAT_THRESHOLD, TARGET_ASSETS, WINDOW_TYPES,
    get_market_asset, get_provider,
)
from AI.eventStudy.db import market_data_dao
from AI.eventStudy.processing import impact_writer

logger = logging.getLogger(__name__)


# ==================== 交易日历与 t0 对齐 ====================

def load_trade_days(start_date: str, end_date: str):
    """加载交易日集合。优先 Provider get_trade_cal，不可用回退本地交易日历。

    返回 set[date]；全部途径失败时返回 None（调用方用工作日兜底）。
    """
    prov = get_provider()
    if hasattr(prov, "get_trade_cal"):
        try:
            df = prov.get_trade_cal(start_date, end_date)
            if df is not None and not df.empty:
                return {pd.Timestamp(t).date() for t in df["trade_date"]}
        except Exception as e:
            logger.warning(f"Provider 交易日历获取失败，回退本地日历: {e}")
    # 回退：本地交易日历（文件缓存）
    try:
        from AI.dataflows.utils.trading_calendar import is_trading_day
        days = set()
        cursor = pd.Timestamp(start_date).date()
        end = pd.Timestamp(end_date).date()
        while cursor <= end:
            if is_trading_day(cursor.isoformat()):
                days.add(cursor)
            cursor += timedelta(days=1)
        return days
    except Exception as e:
        logger.warning(f"本地交易日历不可用: {e}")
        return None


def _to_datetime(announced_at) -> datetime:
    if isinstance(announced_at, datetime):
        return announced_at
    return pd.Timestamp(announced_at).to_pydatetime()


def _next_trading_day(d: date, trade_days: set, is_trading_day_fn=None) -> date:
    def is_td(day: date) -> bool:
        if trade_days:
            return day in trade_days
        if is_trading_day_fn:
            return bool(is_trading_day_fn(day.isoformat()))
        return day.weekday() < 5  # 工作日兜底

    cursor = d + timedelta(days=1)
    for _ in range(30):
        if is_td(cursor):
            return cursor
        cursor += timedelta(days=1)
    return cursor  # 兜底：30 天后仍无交易日（异常日历）


def resolve_t0(announced_at, trade_days: set = None, is_trading_day_fn=None) -> date:
    """t0 规则（3.6.1 / B3）纯函数，可单测。

    盘前（09:30 前）公布 → t0 = 当日（若为交易日，否则下一交易日）；
    盘中/盘后/非交易日公布 → t0 = 下一个交易日。
    trade_days 为 None/空集时以工作日近似（日历不可用兜底）。
    """
    trade_days = trade_days or None
    dt = _to_datetime(announced_at)
    d = dt.date()
    t = dt.time()
    if t < PRE_MARKET_CUTOFF:
        # 盘前：当日为交易日则 t0 = 当日
        is_td = (d in trade_days) if trade_days is not None else (
            is_trading_day_fn(d.isoformat()) if is_trading_day_fn else d.weekday() < 5
        )
        if is_td:
            return d
    return _next_trading_day(d, trade_days, is_trading_day_fn)


def align_trading_day(conn, event_row: dict) -> date:
    """计算事件的 t0 并写入 events.trading_day；同时回填 surprise（实际-预期）。

    Args:
        event_row: events 表行 dict（含 event_id / announced_at / actual_value /
                   expected_value）
    Returns:
        t0 日期
    """
    announced = _to_datetime(event_row["announced_at"])
    t0 = resolve_t0(announced, load_trade_days(
        (announced.date() - timedelta(days=10)).isoformat(),
        (announced.date() + timedelta(days=45)).isoformat(),
    ))
    surprise = None
    actual = event_row.get("actual_value")
    expected = event_row.get("expected_value")
    if actual is not None and expected is not None:
        try:
            surprise = float(actual) - float(expected)
        except (TypeError, ValueError):
            surprise = None
    conn.execute(
        "UPDATE events SET trading_day = %s, surprise = %s, updated_at = now() "
        "WHERE event_id = %s",
        (t0, surprise, event_row["event_id"]),
    )
    conn.commit()
    logger.info(f"事件 {event_row['event_id']} t0 对齐: {t0}（公布于 {announced}）")
    return t0


# ==================== 事件研究核心 ====================

def _load_returns(conn, asset_id: int, t0: date, lookback_days: int,
                  lookahead_days: int) -> pd.Series:
    """加载资产日收益率序列（以日期为索引）。

    以自然日冗余覆盖交易日偏移：lookback/lookahead 为交易日数，按 3 倍
    自然日取数，保证窗口充足。
    """
    start = (t0 - timedelta(days=lookback_days * 3)).isoformat()
    end = (t0 + timedelta(days=lookahead_days * 3)).isoformat()
    df = market_data_dao.get_market_data(conn, asset_id, start, end)
    if df.empty:
        return pd.Series(dtype=float)
    df = df.copy()
    df["d"] = df["ts"].dt.date
    df = df.sort_values("ts")
    df["ret"] = df["adj_close"].astype(float).pct_change()
    return df.set_index("d")["ret"]


def _window_ar(asset_ret: pd.Series, market_ret: pd.Series, alpha: float,
               beta: float, dates: list) -> list:
    """计算给定交易日列表上每个交易日的 AR（实际 - 模型预期）。"""
    ars = []
    for d in dates:
        if d not in asset_ret.index or pd.isna(asset_ret[d]):
            continue
        mk = market_ret.get(d, math.nan)
        if pd.isna(mk):
            continue
        ars.append(asset_ret[d] - (alpha + beta * mk))
    return ars


def _t0_index(trading_days: list, t0: date):
    """定位 t0 在交易日序列中的位置；t0 无数据时取第一个 > t0 的交易日。"""
    if t0 in trading_days:
        return trading_days.index(t0)
    return next((i for i, d in enumerate(trading_days) if d > t0), None)


def _window_dates(trading_days: list, t0: date, window_type: str,
                  pre_event_days: int, post_event_days: int) -> list:
    """按 window_type 返回窗口交易日列表（相对 t0 的位置切分）。"""
    idx0 = _t0_index(trading_days, t0)
    if idx0 is None:
        return []
    if window_type == "pre_event_5d":
        return trading_days[max(0, idx0 - pre_event_days):idx0]
    if window_type == "event_day":
        return [trading_days[idx0]]
    if window_type == "post_event_5d":
        return trading_days[idx0 + 1:idx0 + 1 + post_event_days]
    raise ValueError(f"未知窗口类型: {window_type}")


def _estimation_dates(trading_days: list, t0: date,
                      estimation_days: int, gap_days: int) -> list:
    """估计窗口交易日列表：[t0 - gap - estimation, t0 - gap)。"""
    idx0 = _t0_index(trading_days, t0)
    if idx0 is None:
        return []
    start = max(0, idx0 - gap_days - estimation_days)
    end = max(0, idx0 - gap_days)
    return trading_days[start:end]


def _judge_direction(car: float, t_stat) -> int:
    """方向判定（3.6.1）：|CAR| > 0.5% 且 |t| > 1.96，否则中性。"""
    if t_stat is None:
        return 0
    if car > CAR_DIRECTION_THRESHOLD and t_stat > T_STAT_THRESHOLD:
        return 1
    if car < -CAR_DIRECTION_THRESHOLD and t_stat < -T_STAT_THRESHOLD:
        return -1
    return 0


def _check_contamination(conn, event_id: int, window_dates: list) -> bool:
    """污染检查（3.6.1）：事件窗口内是否有其他重要性 ≥4 的已通过事件。"""
    if not window_dates:
        return False
    start = window_dates[0].isoformat()
    end = (window_dates[-1] + timedelta(days=1)).isoformat()
    row = conn.execute(
        """
        SELECT COUNT(*) FROM events
        WHERE status = 'approved' AND importance >= %s AND event_id != %s
          AND announced_at >= %s::timestamptz
          AND announced_at < %s::timestamptz
        """,
        (CONTAMINATION_MIN_IMPORTANCE, event_id, start, end),
    ).fetchone()
    return bool(row and row[0] > 0)


def run_event_study(
    conn,
    event_id: int,
    asset_id: int,
    market_asset_id: int,
    window_type: str,
    estimation_days: int = ESTIMATION_DAYS,
    gap_days: int = GAP_DAYS,
    pre_event_days: int = PRE_EVENT_DAYS,
    post_event_days: int = POST_EVENT_DAYS,
) -> dict:
    """对单个资产 × 单个窗口执行事件研究（3.6.1 接口）。

    Returns:
        {window_type, window_days, cumulative_abnormal_return, t_stat,
         direction, is_contaminated, error}
        数据不足/异常时返回含 error 字段的 dict（不抛异常，供上游降级）。
    """
    result = {"window_type": window_type, "error": None}
    try:
        row = conn.execute(
            "SELECT trading_day FROM events WHERE event_id = %s", (event_id,)
        ).fetchone()
        if row is None or row[0] is None:
            result["error"] = "事件不存在或未做 t0 对齐"
            return result
        t0 = row[0] if isinstance(row[0], date) else pd.Timestamp(row[0]).date()

        lookback = estimation_days + gap_days + pre_event_days + 30
        lookahead = post_event_days + 30
        asset_ret = _load_returns(conn, asset_id, t0, lookback, lookahead)
        market_ret = _load_returns(conn, market_asset_id, t0, lookback, lookahead)
        if asset_ret.empty:
            result["error"] = "资产行情数据不足"
            return result

        trading_days = sorted(asset_ret.index.tolist())
        est_dates = _estimation_dates(trading_days, t0, estimation_days, gap_days)
        if not est_dates:
            result["error"] = "t0 之前交易日不足"
            return result

        # 估计窗口 OLS：asset_ret = α + β × market_ret
        joined = pd.concat([asset_ret, market_ret], axis=1, join="inner").dropna()
        joined.columns = ["asset", "market"]
        est = joined.loc[joined.index.isin(est_dates)]
        if len(est) < 30:
            result["error"] = f"估计窗口有效观测不足 30（实际 {len(est)}）"
            return result
        x = sm.add_constant(est["market"])
        model = sm.OLS(est["asset"], x).fit()
        alpha, beta = float(model.params.iloc[0]), float(model.params.iloc[1])
        # 显著性标准误用估计窗口残差 std（标准事件研究法做法）：
        # 事件窗口样本少（1-5 个），窗口 AR 自身 std 会虚高 t 值
        resid_std = float(model.resid.std())

        ev_dates = _window_dates(trading_days, t0, window_type,
                                 pre_event_days, post_event_days)
        if not ev_dates:
            result["error"] = f"事件窗口无交易日数据（{window_type}）"
            return result
        ars = _window_ar(asset_ret, market_ret, alpha, beta, ev_dates)
        n = len(ars)
        if n == 0:
            result["error"] = "事件窗口无有效 AR 观测"
            return result
        car = float(sum(ars))
        # t = CAR / (resid_std × √n)；单日窗口（event_day）即单点检验
        t_stat = None
        if resid_std and resid_std > 0:
            t_stat = float(car / (resid_std * math.sqrt(n)))

        result.update({
            "window_days": n,
            "cumulative_abnormal_return": round(car, 6),
            "t_stat": round(t_stat, 4) if t_stat is not None else None,
            "direction": _judge_direction(car, t_stat),
            "is_contaminated": _check_contamination(conn, event_id, ev_dates),
        })
    except Exception as e:
        logger.exception(f"事件研究失败 event={event_id} asset={asset_id} {window_type}")
        result["error"] = str(e)
    return result


def compute_all_windows(conn, event_id: int) -> dict:
    """对全部 4 个目标指数 × 3 个窗口执行事件研究（B5：自动遍历全部指数）。

    - 事件未对齐 t0 时先自动对齐
    - 结果写入 Redis 草稿 event_impacts:draft:{event_id}（impact_writer）
    Returns:
        草稿 dict（同 impact_writer 结构）
    """
    event_row = conn.execute(
        "SELECT event_id, announced_at, actual_value, expected_value, trading_day "
        "FROM events WHERE event_id = %s",
        (event_id,),
    ).fetchone()
    if event_row is None:
        raise ValueError(f"事件 {event_id} 不存在")
    event_dict = {
        "event_id": event_row[0],
        "announced_at": event_row[1],
        "actual_value": event_row[2],
        "expected_value": event_row[3],
    }
    t0 = event_row[4]
    if t0 is None:
        t0 = align_trading_day(conn, event_dict)
    else:
        t0 = t0 if isinstance(t0, date) else pd.Timestamp(t0).date()

    # 仅对 V1 目标指数计算（未来 assets 扩展个股/海外时不盲算）
    assets = [a for a in market_data_dao.list_assets(conn)
              if a["ticker"] in TARGET_ASSETS]
    draft = {
        "event_id": event_id,
        "t0": t0.isoformat(),
        "computed_at": datetime.now().isoformat(),
        "assets": {},
    }
    for asset in assets:
        market_ticker = get_market_asset(asset["ticker"])
        market_asset_id = market_data_dao.get_asset_id(conn, market_ticker)
        if market_asset_id is None:
            logger.warning(f"市场基准资产未初始化: {market_ticker}")
            continue
        per_window = {}
        for wt in WINDOW_TYPES:
            r = run_event_study(conn, event_id, asset["asset_id"], market_asset_id, wt)
            per_window[wt] = {
                "window_days": r.get("window_days"),
                "cumulative_abnormal_return": r.get("cumulative_abnormal_return"),
                "t_stat": r.get("t_stat"),
                "direction": r.get("direction"),
                "is_contaminated": r.get("is_contaminated"),
                "error": r.get("error"),
            }
        draft["assets"][asset["ticker"]] = per_window
    impact_writer.write_impact_draft(event_id, draft)
    return draft
