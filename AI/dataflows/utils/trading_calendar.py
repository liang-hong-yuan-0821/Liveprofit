"""
交易日历校正工具

基于 Tushare trade_cal 接口 + 本地文件缓存，提供交易日判断、日期校正、
数据存在性重试等功能。

三级降级策略：
  1. Tushare trade_cal + 本地 JSON 缓存（主方案）
  2. AKShare tool_trade_date_hist_sina()（备选）
  3. 纯周末跳过 weekday-only（最终兜底）

所有降级路径均为 fail-open：最坏情况原样返回输入日期，不阻塞主流程。

Usage:
    from AI.dataflows.utils import (
        is_trading_day,
        get_last_trading_day,
        get_available_trade_date,
        with_trade_date_retry,
    )

    # 判断是否交易日
    is_trading_day("2026-08-08")  # → False (周六)

    # 获取最近的交易日
    get_last_trading_day("2026-08-08")  # → "2026-08-07"

    # 综合校正（交易日历 + 盘中时间判断）
    get_available_trade_date("2026-08-10")  # 周一 10:00 → "2026-08-07"
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Optional, Callable, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 可选依赖探测
# ---------------------------------------------------------------------------

try:
    import tushare as ts

    TUSHARE_AVAILABLE = True
except ImportError:
    TUSHARE_AVAILABLE = False
    ts = None

try:
    import akshare as ak

    AKSHARE_AVAILABLE = True
except ImportError:
    AKSHARE_AVAILABLE = False
    ak = None

# ---------------------------------------------------------------------------
# 配置常量
# ---------------------------------------------------------------------------

_TZ = ZoneInfo("Asia/Shanghai")
_DATA_READY_HOUR = int(os.getenv("TUSHARE_DATA_READY_HOUR", "20"))
_REFRESH_DAYS = int(os.getenv("TRADE_CALENDAR_REFRESH_DAYS", "365"))
_MAX_RETRY_STEPS = int(os.getenv("TRADE_DATE_RETRY_STEPS", "3"))
_CACHE_DIR = Path(__file__).resolve().parent.parent / "data"
_TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN", "")
_TUSHARE_ENDPOINT = os.getenv("TUSHARE_ENDPOINT", "https://ts.gyzcloud.top/api")

# 模块级单例缓存
_calendar_cache: dict[int, set[str]] = {}  # {year: {"2026-01-02", "2026-01-03", ...}}
_cache_loaded: bool = False


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

def _now() -> datetime:
    """返回 Asia/Shanghai 时区的当前时间。"""
    return datetime.now(_TZ)


def _today_str() -> str:
    """返回 Asia/Shanghai 时区的今天日期字符串 YYYY-MM-DD。"""
    return _now().strftime("%Y-%m-%d")


def _is_valid_date_str(s: str) -> bool:
    """校验字符串是否为合法的 YYYY-MM-DD 格式日期。"""
    if not s or not isinstance(s, str):
        return False
    if len(s) != 10 or s[4] != "-" or s[7] != "-":
        return False
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _cache_path(year: int) -> Path:
    """返回指定年份的缓存文件路径。"""
    return _CACHE_DIR / f"trade_cal_{year}.json"


def _load_cache_file(year: int) -> Optional[set[str]]:
    """从本地 JSON 文件加载指定年份的交易日集合。"""
    path = _cache_path(year)
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        dates = data.get(str(year), [])
        if isinstance(dates, list) and len(dates) > 0:
            return set(dates)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"交易日历缓存文件损坏: {path} ({e})")
    return None


def _save_cache_file(year: int, dates: set[str]) -> None:
    """将指定年份的交易日集合写入本地 JSON 文件。"""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(year)
    payload = {str(year): sorted(dates)}
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        logger.info(f"交易日历缓存已写入: {path} ({len(dates)} 个交易日)")
    except OSError as e:
        logger.warning(f"交易日历缓存写入失败: {path} ({e})")


def _is_cache_fresh(year: int) -> bool:
    """判断缓存是否在有效期内。"""
    path = _cache_path(year)
    if not path.exists():
        return False
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=_TZ)
    age = _now() - mtime
    return age.days < _REFRESH_DAYS


# ---------------------------------------------------------------------------
# 数据源：Tushare trade_cal
# ---------------------------------------------------------------------------

def _fetch_tushare_calendar(year: int) -> Optional[set[str]]:
    """从 Tushare trade_cal 接口拉取全年交易日。

    Returns:
        交易日集合（YYYY-MM-DD 格式），失败返回 None。
    """
    if not TUSHARE_AVAILABLE or ts is None:
        logger.debug("Tushare 不可用，跳过 trade_cal 查询")
        return None

    token = _TUSHARE_TOKEN
    if not token or token == "your-tushare-token":
        logger.debug("Tushare Token 未配置，跳过 trade_cal")
        return None

    try:
        ts.set_token(token)
        api = ts.pro_api()
        api._DataApi__http_url = _TUSHARE_ENDPOINT

        start = f"{year}0101"
        end = f"{year}1231"

        # trade_cal 返回字段: exchange, cal_date, is_open, pretrade_date
        df = api.trade_cal(exchange="SSE", start_date=start, end_date=end)
        if df is None or df.empty:
            logger.debug(f"Tushare trade_cal({year}) 返回空")
            return None

        trading_days: set[str] = set()
        for _, row in df.iterrows():
            if row.get("is_open", 0) == 1:
                cal_date = str(row.get("cal_date", ""))
                if len(cal_date) == 8:
                    trading_days.add(f"{cal_date[:4]}-{cal_date[4:6]}-{cal_date[6:8]}")

        if trading_days:
            logger.info(f"Tushare trade_cal({year}) 获取到 {len(trading_days)} 个交易日")
            return trading_days
        else:
            logger.debug(f"Tushare trade_cal({year}) 未找到交易日")
            return None

    except Exception as e:
        logger.warning(f"Tushare trade_cal({year}) 查询失败: {e}")
        return None


# ---------------------------------------------------------------------------
# 数据源：AKShare tool_trade_date_hist_sina
# ---------------------------------------------------------------------------

def _fetch_akshare_calendar() -> Optional[set[str]]:
    """从 AKShare tool_trade_date_hist_sina 拉取交易日历。

    返回所有可用的历史交易日（跨年），无年份过滤。
    """
    if not AKSHARE_AVAILABLE or ak is None:
        logger.debug("AKShare 不可用，跳过交易日历查询")
        return None

    try:
        df = ak.tool_trade_date_hist_sina()
        if df is None or df.empty:
            logger.debug("AKShare tool_trade_date_hist_sina() 返回空")
            return None

        trading_days: set[str] = set()
        # 列名可能为 trade_date / 日期 等
        for col in df.columns:
            col_lower = str(col).lower()
            if "trade_date" in col_lower or "日期" in col:
                for val in df[col]:
                    s = str(val).strip()
                    if len(s) == 10 and s[4] == "-":
                        trading_days.add(s)
                    elif len(s) == 8 and s.isdigit():
                        trading_days.add(f"{s[:4]}-{s[4:6]}-{s[6:8]}")
                break

        if trading_days:
            logger.info(f"AKShare 获取到 {len(trading_days)} 个交易日")
            return trading_days
        else:
            # 尝试第一列
            first_col = df.columns[0]
            for val in df[first_col]:
                s = str(val).strip()
                if len(s) >= 8:
                    s_clean = s.replace("-", "").replace("/", "")[:8]
                    if s_clean.isdigit():
                        trading_days.add(f"{s_clean[:4]}-{s_clean[4:6]}-{s_clean[6:8]}")
            if trading_days:
                logger.info(f"AKShare 获取到 {len(trading_days)} 个交易日（从列 {first_col}）")
                return trading_days

        logger.debug("AKShare 交易日历解析失败")
        return None

    except Exception as e:
        logger.warning(f"AKShare trade_date 查询失败: {e}")
        return None


# ---------------------------------------------------------------------------
# 兜底：weekday-only
# ---------------------------------------------------------------------------

def _weekday_heuristic(year: int) -> set[str]:
    """纯周末跳过兜底：生成指定年份所有周一至周五的日期集合。

    不处理法定节假日，标注为 "节假日未识别"。
    """
    logger.warning(f"交易日历降级为 weekday-only（{year}），节假日未识别")
    trading_days: set[str] = set()
    d = date(year, 1, 1)
    end = date(year, 12, 31)
    while d <= end:
        if d.weekday() < 5:  # 周一 ~ 周五
            trading_days.add(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)
    return trading_days


# ---------------------------------------------------------------------------
# TradingCalendar — 主类
# ---------------------------------------------------------------------------

class TradingCalendar:
    """交易日历管理器。

    三级数据源降级：
      1. Tushare trade_cal（精确，需 pro token）
      2. AKShare tool_trade_date_hist_sina（免费，覆盖全市场）
      3. Weekday-only 兜底（不识别节假日）

    缓存策略：按年分文件 JSON 缓存，按需拉取 + TTL 刷新（默认 365 天）。
    """

    def __init__(self):
        self._cache: dict[int, set[str]] = {}  # year → {date_str, ...}
        self._source: str = "unknown"

    # ---- 内部 ----

    def _ensure_year(self, year: int) -> set[str]:
        """确保指定年份的交易日集合已加载。"""
        if year in self._cache:
            return self._cache[year]

        trading_days: Optional[set[str]] = None

        # 1) 尝试本地缓存
        if _is_cache_fresh(year):
            trading_days = _load_cache_file(year)
            if trading_days:
                self._source = f"cache({year})"
                self._cache[year] = trading_days
                return trading_days

        # 2) Tushare trade_cal
        trading_days = _fetch_tushare_calendar(year)
        if trading_days:
            self._source = "tushare"
            _save_cache_file(year, trading_days)
            self._cache[year] = trading_days
            return trading_days

        # 3) AKShare
        trading_days = _fetch_akshare_calendar()
        if trading_days:
            self._source = "akshare"
            # AKShare 返回跨年数据，按年份筛选后分别缓存
            this_year: set[str] = {d for d in trading_days if d.startswith(f"{year}-")}
            if this_year:
                _save_cache_file(year, this_year)
                self._cache[year] = this_year
            else:
                self._cache[year] = set()
            # 同时缓存其他年份
            for d in trading_days:
                try:
                    y = int(d[:4])
                    if y != year:
                        self._cache.setdefault(y, set()).add(d)
                except (ValueError, IndexError):
                    pass
            return self._cache.get(year, set())

        # 4) Weekday-only 兜底
        trading_days = _weekday_heuristic(year)
        self._source = "weekday-heuristic"
        # 不缓存兜底结果（每次都重新生成，因为不准确）
        self._cache[year] = trading_days
        return trading_days

    def _ensure_date_coverage(self, date_str: str) -> None:
        """确保 date_str 所在年份 + 前一年份都已加载。"""
        try:
            year = int(date_str[:4])
        except (ValueError, IndexError):
            year = _now().year
        self._ensure_year(year)
        # 前一年（跨年回溯需要）
        if year - 1 not in self._cache:
            self._ensure_year(year - 1)

    # ---- 公开 API ----

    def is_trading_day(self, date_str: str, exchange: str = "SSE") -> bool:
        """判断给定日期是否为交易日。

        Args:
            date_str: 日期，格式 YYYY-MM-DD
            exchange: 交易所（预留，当前仅 SSE）

        Returns:
            True 表示是交易日
        """
        if not _is_valid_date_str(date_str):
            return False
        self._ensure_date_coverage(date_str)
        try:
            year = int(date_str[:4])
        except (ValueError, IndexError):
            return False
        return date_str in self._cache.get(year, set())

    def get_last_trading_day(self, date_str: str, exchange: str = "SSE") -> str:
        """获取 date_str 或之前最近的交易日。

        Args:
            date_str: 参考日期，格式 YYYY-MM-DD
            exchange: 交易所（预留）

        Returns:
            最近的交易日，格式 YYYY-MM-DD
        """
        self._ensure_date_coverage(date_str)
        try:
            year = int(date_str[:4])
            dt = datetime.strptime(date_str, "%Y-%m-%d").date()
        except (ValueError, IndexError):
            return date_str

        trading_days = self._cache.get(year, set())
        # 向前回溯，最多 30 天（覆盖国庆+春节长假期 + 跨年）
        for _ in range(30):
            ds = dt.strftime("%Y-%m-%d")
            # 如果回溯到前一年，加载前一年的日历
            y = dt.year
            if y != year:
                self._ensure_year(y)
                trading_days = self._cache.get(y, set())
                year = y
            if ds in trading_days:
                return ds
            dt -= timedelta(days=1)

        # 30 天都没找到——极端情况，返回原始日期
        logger.warning(f"get_last_trading_day: 30 天回溯未找到交易日（{date_str}），返回原值")
        return date_str

    def get_available_trade_date(self, target_date: str, exchange: str = "SSE") -> str:
        """综合交易日历 + 当前时间，返回实际可用的最新数据日期。

        规则：
          1. target_date 是历史日期（< 今天）→ 仅校验交易日
          2. target_date 是今天：
             a. 是交易日 且 当前时间 >= DATA_READY_HOUR → 返回 target_date
             b. 是交易日 但 当前时间 < DATA_READY_HOUR → 返回上一交易日
             c. 不是交易日 → 返回上一交易日

        Args:
            target_date: 目标日期，格式 YYYY-MM-DD
            exchange: 交易所（预留）

        Returns:
            实际可用的数据日期，格式 YYYY-MM-DD
        """
        # 格式校验：非 YYYY-MM-DD 格式直接返回原值（fail-open）
        if not _is_valid_date_str(target_date):
            logger.warning(f"日期格式无效，无法校正，返回原值: {target_date}")
            return target_date

        today = _today_str()

        # 历史日期：仅做交易日校正
        if target_date < today:
            if self.is_trading_day(target_date, exchange):
                return target_date
            corrected = self.get_last_trading_day(target_date, exchange)
            if corrected != target_date:
                logger.info(f"日期校正（历史非交易日）: {target_date} → {corrected}")
            return corrected

        # 未来日期：回退到最新可用交易日
        if target_date > today:
            logger.warning(f"日期 {target_date} 是未来日期，回退到今天 {today}")
            return self.get_available_trade_date(today, exchange)

        # 今天：需要判断时间
        if self.is_trading_day(target_date, exchange):
            if _now().hour >= _DATA_READY_HOUR:
                return target_date
            else:
                corrected = self.get_last_trading_day(target_date, exchange)
                logger.info(
                    f"日期校正（盘中）: {target_date} 是交易日但未到数据就绪时间 "
                    f"（当前 {_now().hour}:00 < {_DATA_READY_HOUR}:00），"
                    f"使用上一交易日 {corrected}"
                )
                return corrected
        else:
            corrected = self.get_last_trading_day(target_date, exchange)
            if corrected != target_date:
                logger.info(f"日期校正（非交易日）: {target_date} → {corrected}")
            return corrected

    @property
    def data_source(self) -> str:
        """返回当前使用的数据源标识（用于日志/标注）。"""
        return self._source


# ---------------------------------------------------------------------------
# 模块级单例
# ---------------------------------------------------------------------------

_calendar: Optional[TradingCalendar] = None


def _get_calendar() -> TradingCalendar:
    """获取模块级 TradingCalendar 单例。"""
    global _calendar
    if _calendar is None:
        _calendar = TradingCalendar()
    return _calendar


# ---------------------------------------------------------------------------
# 顶层函数（便捷 API）
# ---------------------------------------------------------------------------

def is_trading_day(date_str: str, exchange: str = "SSE") -> bool:
    """判断给定日期是否为交易日。

    Args:
        date_str: 日期，格式 YYYY-MM-DD
        exchange: 交易所代码，默认 SSE（上交所）

    Returns:
        True 表示该日期是交易日
    """
    return _get_calendar().is_trading_day(date_str, exchange)


def get_last_trading_day(date_str: str, exchange: str = "SSE") -> str:
    """获取 date_str 当天或之前最近的交易日。

    Args:
        date_str: 参考日期，格式 YYYY-MM-DD
        exchange: 交易所代码，默认 SSE

    Returns:
        最近的交易日，格式 YYYY-MM-DD。
        如果 date_str 本身就是交易日，返回 date_str。
    """
    return _get_calendar().get_last_trading_day(date_str, exchange)


def get_available_trade_date(target_date: str, exchange: str = "SSE") -> str:
    """综合交易日历 + 当前时间，返回实际可用的最新数据日期。

    这是 A 类接口日期校正的主要入口。规则：
    1. 如果 target_date 是历史日期（< 今天），不做时间判断，仅校验是否为交易日：
       - 是交易日 → 返回 target_date
       - 不是 → 返回上一个交易日
    2. 如果 target_date 是今天：
       a. 是交易日 且 当前时间 >= DATA_READY_HOUR（默认 20:00）→ 返回 target_date
       b. 是交易日 但 当前时间 < DATA_READY_HOUR → 返回上一个交易日
       c. 不是交易日 → 返回上一个交易日

    Args:
        target_date: 目标日期，格式 YYYY-MM-DD
        exchange: 交易所代码，默认 SSE

    Returns:
        实际可用的数据日期，格式 YYYY-MM-DD
    """
    return _get_calendar().get_available_trade_date(target_date, exchange)


def with_trade_date_retry(
    fn: Callable[[str], Optional[str]],
    date_str: str,
    max_steps: int = _MAX_RETRY_STEPS,
) -> Tuple[Optional[str], str]:
    """对精确日期查询做有限回溯，直到查到非空数据或达到步数上限。

    从 date_str 开始向前回溯（每次回到上一个交易日），调用 fn(date) 直到 fn 返回非空。
    最多回溯 max_steps 步。

    典型用法：
        def query(date_str):
            return prov.get_market_breadth(date_str)

        result, actual_date = with_trade_date_retry(query, "2026-08-08")
        # 如果 2026-08-08 是周六，会自动尝试 2026-08-07（周五）

    Args:
        fn: 接受一个日期字符串、返回 Optional[str] 的查询函数。
            返回 None / "" / 以 "暂无"/"N/A" 开头的结果视为空。
        date_str: 起始日期，格式 YYYY-MM-DD
        max_steps: 最大回溯步数，默认 3

    Returns:
        (result, actual_date):
          - result: 查询结果，如果所有回溯步数均无数据则为 None
          - actual_date: 实际命中数据的日期
    """
    cal = _get_calendar()
    current = date_str

    for step in range(max_steps + 1):
        # 第 0 步：原始日期（可能已被校正过）；后续步：上一交易日
        if step == 0:
            # 首次尝试：先做交易日校正
            corrected = cal.get_available_trade_date(current)
            attempt_date = corrected
        else:
            # 回溯到上一个交易日
            attempt_date = cal.get_last_trading_day(
                (datetime.strptime(current, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
            )
            if attempt_date == current:
                # 无法再回溯（极端情况）
                break
            current = attempt_date

        try:
            result = fn(attempt_date)
        except Exception as e:
            logger.warning(f"with_trade_date_retry: fn({attempt_date}) 抛异常: {e}")
            continue

        # 判断是否为空结果
        if result and not _is_empty_result(result):
            if attempt_date != date_str:
                logger.info(f"数据存在性回溯命中: {date_str} → {attempt_date}（第 {step} 步）")
            return result, attempt_date

    logger.warning(f"数据存在性回溯 {max_steps} 步内未找到数据（起始日期 {date_str}）")
    return None, date_str


def _is_empty_result(result: str) -> bool:
    """判断数据源返回的结果是否为"无数据"。

    匹配常见的空结果模式：
      - "暂无...数据。" / "暂无..."
      - "N/A: ..."
      - "获取...失败"
      - "未获取到..."
      - "当前数据源不支持..."
      - 纯空白字符串
    """
    if not result or not result.strip():
        return True
    s = result.strip()
    empty_markers = [
        "暂无",
        "N/A:",
        "获取失败",
        "未获取到",
        "当前数据源不支持",
    ]
    return any(s.startswith(m) for m in empty_markers)
