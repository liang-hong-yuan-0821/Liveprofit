"""
市场上下文计算模块（方案 3.5）

每日计算各市场（V1 仅 CN）环境指标，以 JSON 按市场组织写入
market_context.context（一行 = 某日全部市场，结构 {market: {indicator: value}}）。

指标（决策 B2）：
- return_20d   : 基准指数（CN=沪深300）过去 20 个交易日收益率
- vol_20d      : 近 20 个交易日年化波动率（风险状态）
- amount_20d   : 近 20 个交易日日均成交额（参与度/情绪代理，替代 VIX 方案）
- rate_10y     : 该市场 10 年期国债收益率（经 Provider 宏观接口）

T-1 匹配规则（3.5.1）：事件的环境快照统一取"事件发生前最近一个已收盘
交易日"的 market_context 行中同市场子对象；盘前/盘中 → 前一交易日，
收盘后 → 当日收盘，非交易日 → 最近已收盘日。禁止使用当日快照。
"""

import json
import logging
from datetime import date, time, timedelta

import pandas as pd

from AI.eventStudy.collectors.config import MARKET_BENCHMARK
from AI.eventStudy.collectors.macro_collector import fetch_macro_context
from AI.eventStudy.db import market_data_dao

logger = logging.getLogger(__name__)

TRADING_DAYS_IN_YEAR = 252

POST_MARKET_CUTOFF = time(15, 0)  # 盘中/盘后分界（T-1 规则用）


def _is_trading_day(d: date) -> bool:
    """复用项目本地交易日历（文件缓存，无网络依赖）。"""
    try:
        from AI.dataflows.utils.trading_calendar import is_trading_day
        return bool(is_trading_day(d.isoformat()))
    except Exception:
        return d.weekday() < 5  # 兜底：工作日


def compute_market_context(conn, ctx_date: date, market: str = "CN") -> dict:
    """计算指定日期某市场的环境指标（3.5.1 接口）。

    从 market_data 表取基准指数数据计算收益率/波动率/成交额，
    经 Provider 宏观接口取利率。数据不足的指标置 None。
    """
    indicators = {}
    if market == "CN":
        asset_id = market_data_dao.get_asset_id(conn, MARKET_BENCHMARK)
        if asset_id is None:
            logger.warning(f"市场基准资产未初始化: {MARKET_BENCHMARK}")
            return indicators
        # 多取 1 日计算首日收益率
        start = (ctx_date - timedelta(days=90)).isoformat()
        df = market_data_dao.get_market_data(conn, asset_id, start, ctx_date.isoformat())
        if df.empty:
            return indicators
        # ts 为 tz-aware（数据库会话时区），比较前统一取日期（避免 tz 类型冲突）
        df = df[pd.to_datetime(df["ts"]).dt.date <= ctx_date]
        closes = df["adj_close"].astype(float)
        rets = closes.pct_change().dropna()
        amounts = pd.to_numeric(df["amount"], errors="coerce").dropna()

        if len(closes) >= 21:
            indicators["return_20d"] = round(float(closes.iloc[-1] / closes.iloc[-21] - 1), 6)
        if len(rets) >= 20:
            window = rets.iloc[-20:]
            indicators["vol_20d"] = round(
                float(window.std() * (TRADING_DAYS_IN_YEAR ** 0.5)), 6
            )
        if len(amounts) >= 20:
            indicators["amount_20d"] = round(float(amounts.iloc[-20:].mean()), 2)
        elif amounts.empty:
            logger.warning(f"行情数据无成交额字段（如 AKShare 指数日线），"
                           f"amount_20d 缺失 @ {ctx_date}")

    # 宏观指标（10 年期国债收益率），失败置空不阻塞
    indicators.update(fetch_macro_context(ctx_date.isoformat(), market))
    return indicators


def update_market_context(conn, start_date: str, end_date: str) -> int:
    """批量更新市场环境快照（3.5.1 接口）。

    对区间内每个有行情数据的交易日计算指标并 upsert 到 market_context。
    返回写入的行数。
    """
    rows = conn.execute(
        """
        SELECT DISTINCT trade_date AS d FROM market.instrument_daily
        WHERE trade_date >= %s::date AND trade_date <= %s::date + interval '1 day'
        ORDER BY d
        """,
        (start_date, end_date),
    ).fetchall()
    count = 0
    for (d,) in rows:
        if not isinstance(d, date):
            d = pd.Timestamp(d).date()
        indicators = compute_market_context(conn, d)
        if not indicators:
            continue
        # 保留其他市场已有子对象（V1 仅 CN，结构上仍按市场合并）
        existing = conn.execute(
            "SELECT context FROM market_context WHERE ts = %s::timestamptz", (d.isoformat(),)
        ).fetchone()
        merged = dict(existing[0]) if existing else {}
        merged["CN"] = indicators
        conn.execute(
            """
            INSERT INTO market_context (ts, context)
            VALUES (%s::timestamptz, %s::jsonb)
            ON CONFLICT (ts) DO UPDATE SET context = EXCLUDED.context
            """,
            (d.isoformat(), json.dumps(merged, ensure_ascii=False)),
        )
        count += 1
    conn.commit()
    logger.info(f"[市场环境] 更新 {count} 个交易日快照")
    return count


def resolve_context_date(announced_at, ) -> date:
    """T-1 匹配规则：返回事件应使用的环境快照日期。

    - 盘前/盘中（交易日 T，15:00 前）→ T-1（前一交易日）
    - 收盘后（交易日 T，15:00 后）→ T
    - 非交易日 D → 最近已收盘交易日（< D 或 = D 均无快照，取 < D）
    """
    if hasattr(announced_at, "date"):
        d = announced_at.date()
        t = announced_at.time()
        tz = announced_at.tzinfo
        # 统一转北京时间（CST +08:00）
        if tz is not None:
            import zoneinfo
            try:
                cst = announced_at.astimezone(zoneinfo.ZoneInfo("Asia/Shanghai"))
                d, t = cst.date(), cst.time()
            except Exception:
                pass
    else:
        from datetime import datetime
        dt = pd.Timestamp(announced_at).to_pydatetime()
        d, t = dt.date(), dt.time()

    if _is_trading_day(d):
        if t < POST_MARKET_CUTOFF:
            # 盘前/盘中 → 前一交易日
            cursor = d - timedelta(days=1)
            for _ in range(14):
                if _is_trading_day(cursor):
                    return cursor
                cursor -= timedelta(days=1)
            return d - timedelta(days=1)
        # 盘后 → 当日收盘
        return d
    # 非交易日 → 最近已收盘交易日
    cursor = d - timedelta(days=1)
    for _ in range(14):
        if _is_trading_day(cursor):
            return cursor
        cursor -= timedelta(days=1)
    return d - timedelta(days=1)


def get_event_context(conn, announced_at, market: str = "CN") -> dict:
    """按 T-1 规则取事件的环境快照（3.5.1 匹配规则）。

    Returns:
        {"context_date": "YYYY-MM-DD", "indicators": {...}}
        无可用快照时 indicators 为空 dict。
    """
    ctx_date = resolve_context_date(announced_at)
    row = conn.execute(
        """
        SELECT context FROM market_context
        WHERE ts <= %s::timestamptz
        ORDER BY ts DESC LIMIT 1
        """,
        (ctx_date.isoformat(),),
    ).fetchone()
    if row is None:
        return {"context_date": ctx_date.isoformat(), "indicators": {}}
    ctx = row[0]
    return {
        "context_date": ctx_date.isoformat(),
        "indicators": ctx.get(market, {}),
    }
