"""
LiveProfit Tushare 数据提供器
与 AKShareProvider 接口完全对齐，支持无缝切换。
Tushare 无法提供的数据（如全球宏观新闻）返回明确占位信息。

接口约定（详见 CLAUDE.md）：
- 每个方法签名与 AKShareProvider 一致
- 返回类型统一为 str（格式化文本）
- 不可用时返回 "数据不可用: <原因>"，不抛异常
"""

import os
import re
import time
import logging
import contextvars
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

import pandas as pd

from ..base_provider import BaseStockDataProvider
from . import daily_matrix_utils
from .limit_ladder_utils import calc_break_rate, calc_promotion_rates, format_ladder_matrix
from AI.utils.dataprovider_log import wrap_tushare_api

logger = logging.getLogger(__name__)

# Tushare API 单次调用超时上限（秒），可通过环境变量 TUSHARE_TIMEOUT 覆盖
_TUSHARE_TIMEOUT = int(os.getenv("TUSHARE_TIMEOUT", "30"))

# 技术因子端点字段集合（不自算决策，2026-09-12；技术指标数据源切换方案 §3.1.1）。
# INDEX：大盘页契约消费的 10 因子 + close（入库前与 bars 对齐自检用，mapper 不落库）。
# STOCK：AI 个股报告消费——比 INDEX 多 ma_bfq_250 与 rsi_bfq_6/12/24。
# 常量分开定义避免未来改一处漏一处；新增指标时同步扩展两处并评估消费方。
INDEX_FACTOR_FIELDS = (
    "ts_code,trade_date,close,ma_bfq_5,ma_bfq_10,ma_bfq_20,ma_bfq_60,"
    "boll_mid_bfq,boll_upper_bfq,boll_lower_bfq,"
    "macd_dif_bfq,macd_dea_bfq,macd_bfq"
)
STOCK_FACTOR_FIELDS = (
    "ts_code,trade_date,ma_bfq_5,ma_bfq_10,ma_bfq_20,ma_bfq_60,ma_bfq_250,"
    "boll_mid_bfq,boll_upper_bfq,boll_lower_bfq,"
    "macd_dif_bfq,macd_dea_bfq,macd_bfq,"
    "rsi_bfq_6,rsi_bfq_12,rsi_bfq_24"
)

try:
    import tushare as ts
    TUSHARE_AVAILABLE = True
except ImportError:
    TUSHARE_AVAILABLE = False
    ts = None


def _run_with_timeout(fn, timeout, *args, **kwargs):
    """在线程池中执行 fn，超时则抛 FutureTimeout。

    注意：不能使用 with ThreadPoolExecutor，因为 __exit__ 会调用
    shutdown(wait=True)，导致超时后主线程仍被 hang 住的工作线程阻塞。
    """
    # 显式复制调用方上下文进 worker：实测（2026-08-25，Py3.12.10）本环境
    # ThreadPoolExecutor 不自动传播 contextvars，tushare 端点子日志的
    # _current_dp_call 上下文必须手动带入，否则 worker 内读到 None 不落盘
    ctx = contextvars.copy_context()
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        future = pool.submit(ctx.run, fn, *args, **kwargs)
        return future.result(timeout=timeout)
    finally:
        pool.shutdown(wait=False)


def _parse_up_stat(up_stat: str) -> dict:
    """解析 limit_cpt_list 的 up_stat 字段，提取连板信息。

    返回值：{"days_on_board": int, "consecutive_boards": int}
    解析失败或输入为空时返回 {"days_on_board": 0, "consecutive_boards": 0}。
    """
    if not up_stat or not isinstance(up_stat, str):
        return {"days_on_board": 0, "consecutive_boards": 0}

    s = up_stat.strip()

    # 模式1："9天7板" → days_on_board=9, consecutive_boards=7
    m = re.match(r'(\d+)天(\d+)板', s)
    if m:
        return {"days_on_board": int(m.group(1)), "consecutive_boards": int(m.group(2))}

    # 模式2："首板" → days_on_board=1, consecutive_boards=1
    if '首板' in s:
        return {"days_on_board": 1, "consecutive_boards": 1}

    # 模式3："N连板" → days_on_board=N, consecutive_boards=N
    m = re.match(r'(\d+)连板', s)
    if m:
        n = int(m.group(1))
        return {"days_on_board": n, "consecutive_boards": n}

    # 模式4："N板" 简写 → days_on_board=N, consecutive_boards=N
    m = re.match(r'(\d+)板', s)
    if m:
        n = int(m.group(1))
        return {"days_on_board": n, "consecutive_boards": n}

    # 兜底：提取所有数字，取前两个
    nums = re.findall(r'\d+', s)
    if len(nums) >= 2:
        return {"days_on_board": int(nums[0]), "consecutive_boards": int(nums[1])}
    if len(nums) == 1:
        return {"days_on_board": int(nums[0]), "consecutive_boards": int(nums[0])}

    return {"days_on_board": 0, "consecutive_boards": 0}


def _is_intraday_trading_time(now: datetime = None) -> bool:
    """判定当前本地时刻是否处于 A 股盘中交易时段（周一至周五 9:30-15:00）。

    用于逐日矩阵中当日行的"盘中"标注——盘中运行当日快照未定稿。
    法定节假日（工作日休市）不做精确判定，按交易日近似处理。
    """
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False
    return 930 <= now.hour * 100 + now.minute <= 1500


def _prune_daily_cache(cache: dict, cap: int) -> None:
    """按日缓存超限时删除最旧日期（YYYYMMDD 字符串按字典序即时间序）。"""
    while len(cache) > cap:
        oldest = min(cache)
        del cache[oldest]


def _sort_asc_by_trade_date(df):
    """日线接口（sw_daily / dc_daily / ths_daily）行序按 trade_date 升序归一。

    代理端点实测返回降序（新→旧），直接 tail() 取"最近 N 行"会取到最旧数据
    （2026-08-23 踩坑：行业排名拿到 17 天前数据）。所有日线消费点必须先排序
    再 tail/iloc。
    """
    if df is None or df.empty or "trade_date" not in df.columns:
        return df
    return df.sort_values("trade_date").reset_index(drop=True)


def _fmt_concept_entry(name: str, pct: float, turnover) -> str:
    """概念涨幅 TOP N 条目格式化：名称(涨幅%/换手%)；换手率缺失/非法时仅输出涨幅。"""
    if turnover is not None and pd.notna(turnover):
        try:
            return f"{name}({pct:+.1f}%/{float(turnover):.1f}%)"
        except (TypeError, ValueError):
            pass
    return f"{name}({pct:+.1f}%)"


class TushareProvider(BaseStockDataProvider):
    """Tushare 数据提供器（接口与 AKShareProvider 对齐）"""

    # ==================== 类属性（与 AKShareProvider 一致） ====================

    # 全球科技指数映射：key -> (display_name, tushare_code)
    # Tushare 仅支持 A 股指数，海外指数不可用
    GLOBAL_TECH_INDICES = {
        "SPX":     ("标普500", "spx"),
        "DJI":     ("道琼斯", "dji"),
        "NASDAQ":  ("纳斯达克", "nasdaq"),
        "NDX":     ("纳斯达克100", "nasdaq_100"),
        "SOX":     ("费城半导体", "sox"),
        "DJUSSC":  ("美国半导体", "djussc"),
        "KOSPI":   ("韩国KOSPI", "kospi"),
        "KOSDAQ":  ("韩国KOSDAQ", "kosdaq"),
        "KRX_SEMI":("韩国半导体", "krx_semi"),
        "STAR50":  ("科创50", "star50"),
        "CHINEXT": ("创业板指", "chinext"),
        "CSI_SEMI":("中华半导体", "csi_semi"),
        "CSI_AI":  ("人工智能", "csi_ai"),
    }

    # index_global 端点非 CN 指数映射（2026-09-14 US/KR 上线实测验收）：
    # 键 = 平台 symbol（INDEX_TARGETS/前端目录口径），值 = index_global 的 ts_code
    GLOBAL_INDEX_CODE_MAP = {".INX": "SPX", ".DJI": "DJI", ".IXIC": "IXIC", "KS11": "KS11"}

    AI_INDUSTRY_CHAIN = {
        "存储芯片": "memory_chip",
        "半导体": "semiconductor",
        "光模块": "optical_module",
        "AI服务器": "ai_server",
        "先进封装": "advanced_packaging",
        "算力": "computing_power",
        "AI应用": "ai_application",
        "机器人": "robotics",
        "智能汽车": "smart_vehicle",
    }

    A_SHARE_CONCEPT_MAP = {
        "存储芯片": "BK1037",
        "半导体": "BK1036",
        "光模块": "BK1098",
        "AI服务器": "BK1136",
        "先进封装": "BK1177",
        "算力": "BK1139",
        "AI应用": "BK1163",
        "机器人": "BK0883",
        "智能汽车": "BK0981",
    }

    # ==================== 初始化 ====================

    def __init__(self):
        super().__init__("Tushare")
        self.api = None
        self._connect()

    def _connect(self):
        if not TUSHARE_AVAILABLE:
            logger.error("Tushare 库未安装，请运行: pip install tushare")
            return
        token = os.getenv("TUSHARE_TOKEN", "")
        if token and token != "your-tushare-token":
            try:
                ts.set_token(token)
                self.api = ts.pro_api()
                self.api._DataApi__http_url = "https://ts.gyzcloud.top/api"  # 自定义 Tushare 端点
                wrap_tushare_api(self.api)  # 端点调用子日志（挂当前 DP 调用目录的 tushare/ 下）
                self.api._lp_probe_next = True  # 标记连通性探测调用（meta.probe=true）
                test = self._api_call(self.api.stock_basic, list_status="L", limit=1)
                if test is not None and not test.empty:
                    self.connected = True
                    logger.info("Tushare 连接成功")
                else:
                    logger.warning("Tushare 连接测试失败")
            except Exception as e:
                logger.error(f"Tushare 连接失败: {e}")
        else:
            logger.warning("Tushare Token 未配置，请在 .env 中设置 TUSHARE_TOKEN")

    # ==================== 超时保护 ====================

    def _api_call(self, fn, *args, timeout=None, **kwargs):
        """带超时的 Tushare API 调用。

        超时时记录 warning 日志并返回 None，调用方通过 None 检查自然降级。
        """
        _timeout = timeout if timeout is not None else _TUSHARE_TIMEOUT
        try:
            return _run_with_timeout(fn, _timeout, *args, **kwargs)
        except FutureTimeout:
            logger.warning("Tushare API 调用超时 (%ds): %s", _timeout, getattr(fn, '__name__', str(fn)))
            return None

    def _normalize_code(self, code: str) -> str:
        code = code.strip().upper()
        if "." in code:
            return code
        if code.startswith("6") or code.startswith("9"):
            return f"{code}.SH"
        if code.startswith("0") or code.startswith("3") or code.startswith("2"):
            return f"{code}.SZ"
        return code

    def _normalize_date(self, date_str: str) -> str:
        return date_str.replace("-", "")

    # ==================== 股票行情 ====================

    def get_stock_data(self, code: str, start_date: str, end_date: str) -> str:
        if not self.connected:
            return "Tushare 未连接。请在 .env 中配置有效的 TUSHARE_TOKEN。"
        code = self._normalize_code(code)
        try:
            df = self._api_call(
                self.api.daily,
                ts_code=code,
                start_date=self._normalize_date(start_date),
                end_date=self._normalize_date(end_date),
            )
            if df is not None and not df.empty:
                df = df.sort_values("trade_date", ascending=True)
                columns_order = [
                    "trade_date", "open", "high", "low", "close",
                    "vol", "amount", "pct_chg", "pre_close", "change"
                ]
                available = [c for c in columns_order if c in df.columns]
                return df[available].to_string(index=False)
            return f"未获取到 {code} 在 {start_date} 至 {end_date} 期间的行情数据。"
        except Exception as e:
            logger.error(f"获取行情数据失败 [{code}]: {e}")
            return f"获取行情数据失败: {e}"

    def get_stock_info(self, code: str) -> dict:
        if not self.connected:
            return {"name": f"股票{code}"}
        code = self._normalize_code(code)
        try:
            df = self._api_call(self.api.stock_basic, ts_code=code)
            if df is not None and not df.empty:
                row = df.iloc[0]
                return {
                    "name": row.get("name", f"股票{code}"),
                    "industry": row.get("industry", ""),
                    "area": row.get("area", ""),
                    "list_date": str(row.get("list_date", "")),
                }
        except Exception as e:
            logger.error(f"获取股票信息失败 [{code}]: {e}")
        return {"name": f"股票{code}"}

    # ==================== 基本面 ====================

    def get_fundamentals(self, code: str, curr_date: str = None) -> str:
        if not self.connected:
            return "Tushare 未连接，无法获取基本面数据。"
        code = self._normalize_code(code)
        parts = []
        report_year = int(curr_date[:4]) if curr_date else datetime.now().year
        for year in [report_year - 1, report_year]:
            try:
                for label, fn, fields in [
                    ("财务指标", self.api.fina_indicator,
                     "end_date,roa,roe,grossprofit_margin,netprofit_margin,debt_to_assets,current_ratio,quick_ratio,eps,diluted_eps,bps"),
                    ("利润表", self.api.income,
                     "end_date,revenue,total_revenue,oper_income,oper_cost,operate_profit,total_profit,n_income,undist_profit,diluted_eps,basic_eps"),
                    ("资产负债表", self.api.balancesheet,
                     "end_date,total_assets,total_liab,total_hldr_eqy_exc_min_int,undist_profit,cap_rese,surplus_rese"),
                    ("现金流量表", self.api.cashflow,
                     "end_date,n_cashflow_act,n_cashflow_inv_act,n_cashflow_fin_act,cash_and_equivalents"),
                ]:
                    try:
                        df = self._api_call(fn, ts_code=code, period=f"{year}1231", fields=fields, limit=1)
                        if df is not None and not df.empty:
                            parts.append(f"--- {year}年 {label} ---")
                            parts.append(df.to_string(index=False))
                    except Exception:
                        pass
            except Exception as e:
                logger.warning(f"获取 {year} 年财务数据失败: {e}")
        return "\n\n".join(parts) if parts else f"未获取到 {code} 的财务数据。"

    # ==================== 新闻 ====================

    def get_news(self, code: str, start_date: str, end_date: str) -> str:
        if not self.connected:
            return "Tushare 未连接。"
        code = self._normalize_code(code)
        try:
            df = self._api_call(
                self.api.major_news,
                ts_code=code,
                start_date=self._normalize_date(start_date),
                end_date=self._normalize_date(end_date),
            )
            if df is not None and not df.empty:
                lines = []
                for _, row in df.iterrows():
                    lines.append(f"[{row.get('pub_time', row.get('ann_date', ''))}] "
                                 f"{row.get('title', '')}\n{row.get('content', '')}\n")
                return "\n".join(lines)
        except AttributeError:
            logger.info("Tushare 账号不支持 major_news API")
        except Exception as e:
            logger.warning(f"获取新闻失败: {e}")
        # 回退：公告
        try:
            df = self._api_call(
                self.api.disclosure,
                ts_code=code,
                start_date=self._normalize_date(start_date),
                end_date=self._normalize_date(end_date),
                limit=20,
            )
            if df is not None and not df.empty:
                return "\n".join(f"[{r.get('ann_date', '')}] 公告: {r.get('title', '')}"
                                 for _, r in df.iterrows())
        except Exception:
            pass
        return f"当前 Tushare 账号不支持新闻接口，无法获取 {code} 的相关新闻。"

    # ==================== 大盘指数 ====================

    def get_index_data(self, index_codes: str, start_date: str, end_date: str) -> str:
        if not self.connected:
            return "Tushare 未连接。"
        codes = [c.strip() for c in index_codes.split(",")]
        all_parts = []
        for code in codes:
            try:
                df = self._api_call(
                    self.api.index_daily,
                    ts_code=code,
                    start_date=self._normalize_date(start_date),
                    end_date=self._normalize_date(end_date),
                    limit=5,
                )
                if df is not None and not df.empty:
                    df = df.sort_values("trade_date", ascending=False)
                    cols = ["trade_date", "open", "high", "low", "close", "vol", "pct_chg"]
                    available = [c for c in cols if c in df.columns]
                    all_parts.append(f"--- 指数 {code} ---\n{df[available].head(5).to_string(index=False)}")
            except Exception as e:
                logger.warning(f"获取指数 {code} 数据失败: {e}")
        return "\n\n".join(all_parts) if all_parts else "未获取到指数数据。"

    # ==================== 每日指标 ====================

    def get_daily_basic(self, code: str, trade_date: str) -> str:
        if not self.connected:
            return "Tushare 未连接。"
        code = self._normalize_code(code)
        try:
            df = self._api_call(
                self.api.daily_basic,
                ts_code=code,
                trade_date=self._normalize_date(trade_date),
                fields="ts_code,trade_date,turnover_rate,volume_ratio,pe,pb,total_mv,circ_mv",
            )
            return df.to_string(index=False) if df is not None and not df.empty \
                else f"未获取到 {code} 在 {trade_date} 的每日指标。"
        except Exception as e:
            logger.error(f"获取每日指标失败 [{code}]: {e}")
            return f"获取每日指标失败: {e}"

    # ==================== 全球指数（占位） ====================

    def get_global_index(self, index_key: str, days: int = 10) -> str:
        """获取全球指数（Tushare 仅支持 A 股指数，海外指数不可用）"""
        if index_key in ("STAR50", "CHINEXT", "CSI_SEMI", "CSI_AI"):
            return self._get_a_index(index_key, days)
        name, _ = self.GLOBAL_TECH_INDICES.get(index_key, (index_key, index_key))
        return f"数据不可用：Tushare 不支持海外指数 {name}。"

    def _get_a_index(self, index_key: str, days: int = 10) -> str:
        """获取 A 股指数的简化实现"""
        tscode_map = {
            "STAR50": "000688.SH",
            "CHINEXT": "399006.SZ",
            "CSI_SEMI": "990001.SH",
            "CSI_AI": "931071.SH",
        }
        ts_code = tscode_map.get(index_key)
        if not ts_code or not self.connected:
            return f"数据不可用：无法获取 {index_key} 数据。"
        try:
            end = datetime.now().strftime("%Y%m%d")
            start = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")
            df = self._api_call(self.api.index_daily, ts_code=ts_code, start_date=start, end_date=end)
            if df is not None and not df.empty:
                name, _ = self.GLOBAL_TECH_INDICES.get(index_key, (index_key, ""))
                df = _sort_asc_by_trade_date(df).tail(days)
                return f"## {name} ({index_key})\n最近 {len(df)} 个交易日数据:\n{df.to_string(index=False)}"
            return f"未获取到 {name} 数据。"
        except Exception as e:
            return f"获取 {index_key} 数据失败: {e}"

    def _fetch_global_index(self, ak_key: str, start: str, end: str):  # noqa: ARG002
        """内部方法（Tushare 不支持，返回 None）"""
        return None

    def get_all_tech_indices(self, days: int = 10) -> str:
        results = []
        for key in self.GLOBAL_TECH_INDICES:
            data = self.get_global_index(key, days)
            results.append(data)
            results.append("")
        return "\n".join(results)

    # ==================== 市场层 — 宏观 ====================

    def get_central_bank_calendar(self, curr_date: str) -> str:
        if not self.connected:
            return "数据不可用：Tushare 未连接。"
        lines = ["# 主要央行利率参考（Tushare）\n"]
        try:
            df = self._api_call(self.api.shibor_lpr, date=self._normalize_date(curr_date))
            if df is not None and not df.empty:
                lines.append("## LPR 报价\n" + df.to_string(index=False))
        except Exception:
            lines.append("- LPR 数据: 获取失败")
        return "\n".join(lines) if len(lines) > 1 else self._not_supported("央行利率数据")

    # ==================== 市场层 — A 股资金 ====================

    def get_ipo_calendar(self, curr_date: str) -> str:
        """IPO 日历（Tushare new_share API）"""
        if not self.connected:
            return "Tushare 未连接。"
        try:
            df = self._api_call(
                self.api.new_share,
                start_date=(datetime.now() - timedelta(days=90)).strftime("%Y%m%d"),
                end_date=datetime.now().strftime("%Y%m%d"),
            )
            if df is not None and not df.empty:
                lines = ["# IPO 新股发行日历（Tushare）\n"]
                for _, row in df.iterrows():
                    lines.append(f"- {row.get('name', 'N/A')} | 申购日: {row.get('online_date', 'N/A')} | "
                                 f"上市日: {row.get('list_date', 'N/A')} | "
                                 f"发行价: {row.get('price', 'N/A')} | PE: {row.get('pe', 'N/A')}")
                return "\n".join(lines)
            return "暂无近期 IPO 数据。"
        except Exception as e:
            return f"获取 IPO 日历失败: {e}"

    def get_share_unlock_calendar(self, curr_date: str) -> str:
        """限售股解禁日历（Tushare share_float API）"""
        if not self.connected:
            return "Tushare 未连接。"
        try:
            df = self._api_call(
                self.api.share_float,
                start_date=self._normalize_date(curr_date),
                end_date=(datetime.now() + timedelta(days=30)).strftime("%Y%m%d"),
            )
            if df is not None and not df.empty:
                lines = ["# 限售股解禁日历（Tushare）\n"]
                for _, row in df.head(30).iterrows():
                    lines.append(f"- {row.get('ts_code', 'N/A')} | {row.get('float_date', 'N/A')} | "
                                 f"解禁数量: {row.get('float_share', 'N/A')}万股 | "
                                 f"占总股本: {row.get('float_ratio', 'N/A')}%")
                return "\n".join(lines)
            return "暂无近期限售股解禁数据。"
        except Exception as e:
            return f"获取解禁日历失败: {e}"

    def get_margin_trading_balance(self, curr_date: str) -> str:
        """融资融券余额（Tushare margin API）"""
        if not self.connected:
            return "Tushare 未连接。"
        try:
            df = self._api_call(self.api.margin, trade_date=self._normalize_date(curr_date))
            if df is not None and not df.empty:
                lines = ["# 融资融券交易汇总（Tushare）\n"]
                total_rz = df["rzye"].sum() if "rzye" in df.columns else 0
                total_rq = df["rqye"].sum() if "rqye" in df.columns else 0
                lines.append(f"- 融资余额: {total_rz:.2f} 亿元")
                lines.append(f"- 融券余额: {total_rq:.2f} 亿元")
                lines.append(f"- 数据日期: {curr_date}")
                return "\n".join(lines)
            return "暂无融资融券数据。"
        except Exception as e:
            return f"获取融资融券数据失败: {e}"

    def get_market_breadth(self, curr_date: str) -> str:
        """市场宽度（涨跌家数统计）—— Tushare daily_basic 聚合"""
        if not self.connected:
            return "Tushare 未连接。"
        try:
            df = self._api_call(
                self.api.daily_basic,
                trade_date=self._normalize_date(curr_date),
                fields="ts_code,pct_chg",
            )
            if df is not None and not df.empty:
                up = (df["pct_chg"] > 0).sum()
                down = (df["pct_chg"] < 0).sum()
                flat = (df["pct_chg"] == 0).sum()
                lines = ["# A 股市场宽度\n"]
                lines.append(f"- 上涨: {up} 家")
                lines.append(f"- 下跌: {down} 家")
                lines.append(f"- 平盘: {flat} 家")
                lines.append(f"- 统计日期: {curr_date}")
                return "\n".join(lines)
            return "暂无市场宽度数据。"
        except Exception as e:
            return f"获取市场宽度失败: {e}"

    def get_market_fund_flow(self, curr_date: str) -> str:
        """市场资金流向（Tushare moneyflow API）"""
        if not self.connected:
            return "Tushare 未连接。"
        try:
            df = self._api_call(self.api.moneyflow, trade_date=self._normalize_date(curr_date))
            if df is not None and not df.empty:
                lines = ["# A 股资金流向（Tushare）\n"]
                for col in ["buy_elg_vol", "sell_elg_vol", "net_mf_vol", "net_mf_amount"]:
                    if col in df.columns:
                        total = df[col].sum()
                        lines.append(f"- {col}: {total:.2f}")
                return "\n".join(lines)
            return "暂无资金流向数据。"
        except Exception as e:
            return f"获取资金流向失败: {e}"

    # ==================== 板块层 ====================

    # 东财行业资金流快照按日缓存（仅非当日），仅供 get_sector_fund_flow_rank 使用
    _DC_INDUSTRY_FLOW_DAILY_CACHE = {}

    def get_sector_fund_flow_rank(self, days: int = 5) -> str:
        """行业资金流向排名（东财行业口径，近 N 个交易日净流入聚合）

        逐日 moneyflow_ind_dc(trade_date=d)（单日全量快照含行业/概念/地域），
        仅取行业板块（content_type 含"行业"），按行业聚合近 N 日 net_amount（元）。
        """
        if not self.connected:
            return "Tushare 未连接。"
        try:
            # 交易日列表（trade_cal 实测降序返回，sorted 归一后取最近 days 个）
            end = datetime.now().strftime("%Y%m%d")
            start = (datetime.now() - timedelta(days=days * 3)).strftime("%Y%m%d")
            cal = self._api_call(self.api.trade_cal, exchange='SSE',
                                 start_date=start, end_date=end, is_open='1')
            if cal is None or cal.empty or "cal_date" not in cal.columns:
                return "暂无行业资金流向数据。"
            trade_days = sorted(cal["cal_date"].astype(str).tolist())[-days:]

            today = datetime.now().strftime("%Y%m%d")
            agg = {}   # 行业名 -> {"net": 近N日净流入合计, "last_net": 最新净流入, "last_pct": 最新涨跌幅}
            for d in trade_days:
                df = self._DC_INDUSTRY_FLOW_DAILY_CACHE.get(d)
                if df is None:
                    try:
                        df = self._api_call(self.api.moneyflow_ind_dc, trade_date=d)
                    except Exception as e:
                        logger.warning("moneyflow_ind_dc %s 调用异常: %s", d, e)
                        df = None
                    if df is not None and not df.empty:
                        # 仅缓存非当日日期：当日快照盘中/收盘前未定稿
                        if d != today:
                            self._DC_INDUSTRY_FLOW_DAILY_CACHE[d] = df
                            _prune_daily_cache(self._DC_INDUSTRY_FLOW_DAILY_CACHE, cap=20)
                if df is None or df.empty or "content_type" not in df.columns:
                    continue
                ind = df[df["content_type"].astype(str).str.contains("行业", na=False)]
                for _, row in ind.iterrows():
                    try:
                        net = float(row["net_amount"])
                    except (KeyError, TypeError, ValueError):
                        net = 0.0
                    entry = agg.setdefault(
                        str(row["name"]),
                        {"net": 0.0, "last_net": None, "last_pct": None})
                    entry["net"] += net
                    if d == trade_days[-1]:
                        entry["last_net"] = net
                        try:
                            entry["last_pct"] = float(row["pct_change"])
                        except (KeyError, TypeError, ValueError):
                            entry["last_pct"] = None

            if not agg:
                return "暂无行业资金流向数据。"

            ranked = sorted(agg.items(), key=lambda kv: -kv[1]["net"])
            lines = [f"# 行业资金流向排名（近 {len(trade_days)} 日，东财行业口径）"]
            lines.append(f"（{trade_days[0]} - {trade_days[-1]}）\n")
            lines.append("| 排名 | 行业 | 近%i日净流入(亿) | 最新净流入(亿) | 最新涨跌幅(%%) |" % len(trade_days))
            lines.append("|------|------|----------------|---------------|---------------|")
            for i, (name, v) in enumerate(ranked[:30], 1):
                last_net = f"{v['last_net'] / 1e8:+.2f}" if v["last_net"] is not None else "—"
                last_pct = f"{v['last_pct']:+.2f}" if v["last_pct"] is not None else "—"
                lines.append(f"| {i} | {name} | {v['net'] / 1e8:+.2f} | {last_net} | {last_pct} |")

            lines.append("\n## 净流入 TOP5")
            for i, (name, v) in enumerate(ranked[:5], 1):
                lines.append(f"  {i}. {name}: 近%i日净流入 {v['net'] / 1e8:+.2f} 亿" % len(trade_days))
            lines.append("\n## 净流出 BOTTOM5")
            for i, (name, v) in enumerate(ranked[-5:], 1):
                lines.append(f"  {i}. {name}: 近%i日净流入 {v['net'] / 1e8:+.2f} 亿" % len(trade_days))
            return "\n".join(lines)
        except Exception as e:
            return f"获取行业资金流向失败: {e}"

    # ==================== 板块层 — 概念/行业数据 ====================

    def _get_ths_index_map(self) -> dict:
        """缓存同花顺板块名称→ts_code映射（type='I'为概念板块）"""
        if not self.connected:
            return {}
        try:
            df = self._api_call(self.api.ths_index, exchange='A')
            if df is not None and not df.empty:
                return dict(zip(df['name'], df['ts_code']))
        except Exception as e:
            logger.warning(f"获取同花顺板块列表失败: {e}")
        return {}

    def _get_sw_index_map(self) -> dict:
        """缓存申万行业名称→指数代码映射"""
        if not self.connected:
            return {}
        try:
            df = self._api_call(self.api.index_classify, src='SW2021', level='L1')
            if df is not None and not df.empty:
                return dict(zip(df['industry_name'], df['index_code']))
        except Exception as e:
            logger.warning(f"获取申万行业分类失败: {e}")
        return {}

    # ==================== 板块层 — 选股层数据（东财概念体系） ====================

    # dc_index 概念板块快照按日缓存（名单接口 + 成分股接口 + 选股层多次调用只打一次代理）
    _DC_CONCEPT_INDEX_CACHE = {"date": None, "df": None}

    def _get_dc_concept_index(self):
        """拉取最近一个有数据交易日的东财概念板块快照（dc_index idx_type=概念板块）。

        代理不支持日期区间参数，且盘中/非交易日可能无数据 → 向前最多尝试 5 天。
        当日成功结果按日缓存，跨天自动失效。
        返回 DataFrame（含 name/ts_code/trade_date 等），失败返回 None。
        """
        if not self.connected:
            return None
        today = datetime.now().strftime("%Y%m%d")
        cached = self._DC_CONCEPT_INDEX_CACHE
        if cached["date"] == today and cached["df"] is not None:
            return cached["df"]
        for offset in range(5):
            trade_date = (datetime.now() - timedelta(days=offset)).strftime("%Y%m%d")
            try:
                df = self._api_call(
                    self.api.dc_index, trade_date=trade_date, idx_type="概念板块"
                )
            except Exception as e:
                logger.warning("dc_index 调用异常: %s", e)
                return None
            if df is not None and not df.empty:
                cached["date"] = today
                cached["df"] = df
                return df
            logger.info("_get_dc_concept_index: dc_index trade_date=%s 返回空, 尝试前一天", trade_date)
        return None

    def get_concept_board_names(self) -> str:
        """获取东财概念板块全名单（每行一个概念名，供结构化清单过滤）"""
        if not self.connected:
            return "Tushare 未连接。"
        try:
            df = self._get_dc_concept_index()
            if df is None or df.empty or "name" not in df.columns:
                return "未获取到东财概念板块名单。"
            names = [str(n).strip() for n in df["name"].tolist() if str(n).strip()]
            return "\n".join(names) if names else "东财概念板块名单为空。"
        except Exception as e:
            logger.warning(f"获取东财概念板块名单失败: {e}")
            return f"获取东财概念板块名单失败: {e}"

    def get_sector_constituents(self, sector_name: str) -> str:
        """获取东财概念板块当日成分股（dc_member 快照）→ `代码|名称`

        注意：dc_member 必须传 trade_date（不传返回历史累计成员），
        trade_date 取自 dc_index 快照日期，保证 T-1 口径一致。
        """
        if not self.connected:
            return "Tushare 未连接。"
        try:
            idx_df = self._get_dc_concept_index()
            if idx_df is None or idx_df.empty:
                return "未获取到东财概念板块名单。"
            match = idx_df[idx_df["name"].astype(str) == str(sector_name).strip()]
            if match.empty:
                return f"未找到东财概念板块: {sector_name}"
            ts_code = match.iloc[0]["ts_code"]
            trade_date = match.iloc[0]["trade_date"]
            mem = self._api_call(self.api.dc_member, ts_code=ts_code, trade_date=trade_date)
            if mem is None or mem.empty:
                return f"未获取到 {sector_name} 成分股数据。"
            lines = []
            for _, row in mem.iterrows():
                code = str(row.get("con_code", "")).strip()
                name = str(row.get("name", "")).strip()
                if code and re.fullmatch(r"\d{6}", code):
                    lines.append(f"{code}|{name}")
            return "\n".join(lines) if lines else f"{sector_name} 成分股为空。"
        except Exception as e:
            logger.warning(f"获取板块成分股失败 [{sector_name}]: {e}")
            return f"获取 {sector_name} 成分股失败: {e}"

    def get_stocks_performance_ranking(self, codes: list, days: int = 10) -> str:
        """批量计算近 N 日涨跌幅 + 最新价/最新成交额（末行板块均值）。

        连续 3 只失败熔断，防止代理异常时拖垮整条流水线。
        """
        if not self.connected:
            return "Tushare 未连接。"
        try:
            end = datetime.now().strftime("%Y%m%d")
            start = (datetime.now() - timedelta(days=days * 4)).strftime("%Y%m%d")
            rows = []
            pcts = []
            consecutive_failures = 0
            for raw in codes:
                code = self._normalize_code(str(raw).strip())
                if not code:
                    continue
                try:
                    df = self._api_call(
                        self.api.daily, ts_code=code, start_date=start, end_date=end
                    )
                except Exception:
                    df = None
                if df is None or df.empty or len(df) < 2:
                    consecutive_failures += 1
                    logger.warning("get_stocks_performance_ranking: %s 无数据，连续失败 %d", code, consecutive_failures)
                    if consecutive_failures >= 3:
                        logger.warning("get_stocks_performance_ranking: 连续 3 只失败，熔断")
                        break
                    continue
                try:
                    df = df.sort_values("trade_date").tail(days + 1)
                    first_close = float(df["close"].iloc[0])
                    last_close = float(df["close"].iloc[-1])
                    last_amount = float(df["amount"].iloc[-1]) if "amount" in df.columns else 0.0
                    if first_close <= 0:
                        continue
                    pct = (last_close - first_close) / first_close * 100
                    rows.append((code, pct, last_close, last_amount))
                    pcts.append(pct)
                    consecutive_failures = 0
                except Exception:
                    consecutive_failures += 1
                    continue

            if not rows:
                return "未获取到任何个股涨幅数据。"
            lines = [f"{code}|{pct:+.2f}|{close:.2f}|{amount:.0f}"
                     for code, pct, close, amount in rows]
            avg = sum(pcts) / len(pcts)
            lines.append(f"板块均值|{avg:+.2f}")
            return "\n".join(lines)
        except Exception as e:
            logger.warning(f"获取个股涨幅排名失败: {e}")
            return f"获取个股涨幅排名失败: {e}"

    def get_concept_board(self, concept_name: str, days: int = 10) -> str:
        """获取单个 A 股概念板块行情数据（通过同花顺板块指数）"""
        if not self.connected:
            return "Tushare 未连接。"
        try:
            index_map = self._get_ths_index_map()
            ts_code = index_map.get(concept_name)
            if not ts_code:
                # 模糊匹配
                for name, code in index_map.items():
                    if concept_name in name or name in concept_name:
                        ts_code = code
                        concept_name = name
                        break
            if not ts_code:
                return f"未找到概念板块: {concept_name}"

            end = datetime.now().strftime("%Y%m%d")
            start = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")
            df = self._api_call(self.api.ths_daily, ts_code=ts_code, start_date=start, end_date=end)
            if df is not None and not df.empty:
                df = _sort_asc_by_trade_date(df).tail(days)
                cols = [c for c in ['trade_date', 'open', 'high', 'low', 'close', 'pct_change', 'vol', 'amount'] if c in df.columns]
                return f"## A股概念板块: {concept_name}\n{df[cols].to_string(index=False)}"
            return f"未获取到 {concept_name} 板块数据。"
        except Exception as e:
            logger.warning(f"获取概念板块失败 [{concept_name}]: {e}")
            return f"获取 {concept_name} 板块数据失败: {e}"

    def get_all_concept_boards(self, days: int = 10) -> str:
        """获取 AI 产业链全部概念板块数据"""
        results = []
        consecutive_failures = 0
        total = len(self.AI_INDUSTRY_CHAIN)
        for i, name in enumerate(self.AI_INDUSTRY_CHAIN):
            data = self.get_concept_board(name, days)
            results.append(data)
            results.append("")
            if "失败" in data:
                consecutive_failures += 1
            else:
                consecutive_failures = 0
            if consecutive_failures >= 2:
                skipped = total - i - 1
                if skipped > 0:
                    results.append(f"（连续失败，跳过剩余 {skipped} 个板块）")
                break
        return "\n".join(results)

    def get_industry_sector_performance(self, days: int = 10) -> str:
        """获取全行业板块涨跌排名（通过申万行业指数）

        输出含「近10个交易日逐日涨跌幅」章节（固定 10 列，与 days 参数无关；
        复用同一批 sw_daily 序列，不新增 API 调用）。
        """
        if not self.connected:
            return "Tushare 未连接。"
        try:
            performance_list, daily_closes, date_range = self._fetch_industry_daily_series(days)
            if performance_list is None:
                return "未获取到申万行业分类。"
            if not performance_list:
                return "未获取到行业板块表现数据。"

            performance_list.sort(key=lambda x: x[1], reverse=True)
            lines = [f"# 全行业板块涨跌排名（近 {days} 日）"]
            lines.append(f"（{date_range[0]} - {date_range[1]}）\n"
                         if date_range else "")
            lines.append(f"共覆盖 {len(performance_list)} 个申万一级行业\n")
            lines.append("| 排名 | 行业 | 涨跌幅(%) | 最新收盘价 | 数据天数 |")
            lines.append("|------|------|-----------|------------|----------|")
            for i, (name, pct, price, ndays) in enumerate(performance_list, 1):
                lines.append(f"| {i} | {name} | {pct:+.2f}% | {price:.2f} | {ndays} |")

            lines.append(f"\n## 领涨 TOP5")
            for i, (name, pct, price, _) in enumerate(performance_list[:5], 1):
                lines.append(f"  {i}. {name}: {pct:+.2f}%（收盘 {price:.2f}）")
            lines.append(f"\n## 领跌 BOTTOM5")
            for i, (name, pct, price, _) in enumerate(performance_list[-5:], 1):
                lines.append(f"  {i}. {name}: {pct:+.2f}%（收盘 {price:.2f}）")
            daily_section = self._append_industry_daily_section(performance_list, daily_closes, days)
            if daily_section:
                lines.append(daily_section)
            return "\n".join(lines)
        except Exception as e:
            logger.warning(f"获取行业板块排名失败: {e}")
            return f"获取行业板块排名失败: {e}"

    def _append_industry_daily_section(self, performance_list, daily_closes, days: int) -> str:
        """行业逐日涨跌幅章节（纯格式化）：行序 = 聚合排名序，固定最近 10 个交易日列。

        performance_list = [(name, pct, price, ndays)]（已按 pct 降序）；
        daily_closes = {name: DataFrame[trade_date, close]}（tail(11)，不足按实际）。
        累计列 = 逐日 pct 复利，days=10 时与聚合表 pct 精确一致。
        """
        n = min(daily_matrix_utils.DAILY_COLS, days)
        series_map = {}
        for name, df in daily_closes.items():
            series_map[name] = daily_matrix_utils.daily_pct_from_closes(
                df["trade_date"].astype(str).tolist(),
                pd.to_numeric(df["close"], errors="coerce").tolist(),
            )
        if not series_map:
            return ""
        master = daily_matrix_utils.master_dates_from_series(series_map, n=n)
        if len(master) < 2:
            return "\n> 近10日逐日涨跌幅数据不足（仅 %d 天），已省略。" % len(master)
        covered = {d for s in series_map.values() for d in s}
        gaps = daily_matrix_utils.compute_gap_dates(min(covered), max(covered), covered)
        rows = [(name, series_map.get(name, {}),
                 daily_matrix_utils.cum_pct_from_daily(series_map.get(name, {})))
                for name, *_ in performance_list]
        today = datetime.now().strftime("%Y%m%d")
        intraday_date = master[-1] if (master[-1] == today
                                       and _is_intraday_trading_time()) else None
        caption_parts = ["覆盖 %s ~ %s，共 %d 个交易日" % (
            daily_matrix_utils.fmt_date_short(master[0]),
            daily_matrix_utils.fmt_date_short(master[-1]), len(master))]
        if intraday_date:
            caption_parts.append("最新列 %s（盘中）为盘中未定稿数据"
                                 % daily_matrix_utils.fmt_date_short(master[-1]))
        if len(master) < daily_matrix_utils.DAILY_COLS:
            caption_parts.append("仅覆盖 %d 个交易日" % len(master))
        lag_days = (datetime.now() - pd.to_datetime(master[-1])).days
        if lag_days > 3:
            caption_parts.append("最新交易日 %s（%d 天前），数据可能滞后" % (
                daily_matrix_utils.fmt_date_short(master[-1]), lag_days))
        section = daily_matrix_utils.format_daily_matrix(
            rows, master,
            title="近%d个交易日逐日涨跌幅（行业×日期，列头 MM-DD）" % len(master),
            caption="；".join(caption_parts),
            intraday_date=intraday_date,
            gap_dates=gaps,
            name_col="行业",
        )
        return ("\n\n" + section) if section else ""

    def _fetch_industry_daily_series(self, days: int = 10):
        """行业日线拉取（申万一级）：返回 (performance_list, daily_closes, date_range)。

        performance_list = [(name, pct, price, ndays)]（未排序，provider 获取序）；
        daily_closes = {行业名: DataFrame[trade_date, close]}（tail(11)，逐日章节/矩阵复用）；
        date_range = (start, end) 取自首个成功行业的日线窗口。
        分类列表获取失败 → (None, None, None)；全部行业失败 → ([], {}, None)。
        """
        idx_df = self._api_call(self.api.index_classify, src='SW2021', level='L1')
        if idx_df is None or idx_df.empty:
            return None, None, None

        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=days * 3)).strftime("%Y%m%d")

        performance_list = []
        daily_closes = {}  # 行业名 → tail(11) 日线，逐日章节/矩阵复用（不新增 API 调用）
        date_range = None  # 聚合区间 (start, end)，取自首个成功行业的日线窗口
        for _, row in idx_df.iterrows():
            code = row['index_code']
            name = row['industry_name']
            try:
                df = self._api_call(self.api.sw_daily, ts_code=code, start_date=start, end_date=end)
                if df is not None and not df.empty and len(df) >= 2:
                    df = _sort_asc_by_trade_date(df).tail(days + 1)
                    first_close = float(df['close'].iloc[0])
                    last_close = float(df['close'].iloc[-1])
                    if first_close > 0:
                        pct = (last_close - first_close) / first_close * 100
                        performance_list.append((name, pct, last_close, len(df)))
                        daily_closes[name] = df.tail(11)[["trade_date", "close"]].copy()
                        if date_range is None:
                            date_range = (str(df['trade_date'].min()),
                                          str(df['trade_date'].max()))
            except Exception:
                continue
        return performance_list, daily_closes, date_range

    def get_industry_daily_returns_matrix(self, days: int = 10):
        """行业近 N 个交易日逐日涨跌幅结构化矩阵（热力图数据源）。

        复用 get_industry_sector_performance 的日线拉取（_fetch_industry_daily_series），
        与文本逐日章节同源同口径。
        返回 {"source": "tushare", "dates": [...升序], "names": [...], "pct_matrix": [...]}；
        不支持/数据不足时返回 None（结构化接口约定）。
        """
        if not self.connected:
            return None
        try:
            performance_list, daily_closes, _ = self._fetch_industry_daily_series(days)
            if not performance_list or not daily_closes:
                return None
            series_map = {}
            for name, df in daily_closes.items():
                series_map[name] = daily_matrix_utils.daily_pct_from_closes(
                    df["trade_date"].astype(str).tolist(),
                    pd.to_numeric(df["close"], errors="coerce").tolist(),
                )
            n = min(daily_matrix_utils.DAILY_COLS, days)
            master = daily_matrix_utils.master_dates_from_series(series_map, n=n)
            if len(master) < 2:
                return None
            names = [name for name, *_ in performance_list]
            return {"source": "tushare", "dates": master, "names": names,
                    "pct_matrix": [[series_map.get(nm, {}).get(d) for d in master]
                                   for nm in names]}
        except Exception as e:
            logger.warning(f"获取行业逐日矩阵失败: {e}")
            return None

    def get_concept_board_heat_rank(self, days: int = 10) -> str:
        """获取热门概念板块热度排名（通过东方财富 DC 概念板块）

        优化策略：
        1. dc_index 一次拉取全量板块，自带 pct_change，按涨幅降序取 TOP 30
        2. 仅对 TOP 30 板块调 dc_daily 获取日线，计算量变+热度
        3. API 调用：1 + 30 = 31 次（原方案 1 + 777 = 778 次）
        4. 输出含「近10个交易日逐日涨跌幅」章节（行 = 热度 TOP30，单元格来自
           逐日 dc_index 快照；约 +9 次历史交易日 + 4-5 次周末探测的 dc_index，
           与 get_concept_daily_top_gains 共享 _DC_CONCEPT_DAILY_CACHE）
        """
        if not self.connected:
            return "Tushare 未连接。"
        t_start = time.perf_counter()
        try:
            heat_list, idx_df, trade_date, total, success_count = self._fetch_concept_heat(days, top_n=30)
            if heat_list is None:
                return "未获取到概念板块列表。"
            if not heat_list:
                return "未获取到概念板块热度数据。"

            t_total = time.perf_counter() - t_start
            logger.info("get_concept_board_heat_rank: 完成, 总耗时 %.1fs", t_total)
            lines = [f"# 概念板块热度排名（近 {days} 日，数据源：东方财富 DC）\n"]
            lines.append(f"从 {total} 个概念板块中按涨幅预筛选 TOP {len(heat_list)}，日线覆盖 {success_count} 个\n")
            lines.append("| 排名 | 概念板块 | 涨跌幅(%) | 成交额变化(%) | 综合热度 |")
            lines.append("|------|----------|-----------|---------------|----------|")
            for j, (name, pct, vol_chg, heat) in enumerate(heat_list[:30], 1):
                lines.append(f"| {j} | {name} | {pct:+.2f}% | {vol_chg:+.1f}% | {heat:+.1f} |")

            lines.append("\n## 热度 TOP10")
            for j, (name, pct, vol_chg, heat) in enumerate(heat_list[:10], 1):
                lines.append(f"  {j}. {name}: 涨幅 {pct:+.2f}%, 量变 {vol_chg:+.1f}%, 热度 {heat:+.1f}")

            # ===== Step 4: 逐日涨跌幅章节（近 10 个交易日，行 = 热度 TOP30） =====
            daily_section = self._append_concept_daily_section(heat_list, idx_df, trade_date)
            if daily_section:
                lines.append(daily_section)
            return "\n".join(lines)
        except Exception as e:
            t_total = time.perf_counter() - t_start
            logger.warning(f"获取概念板块热度失败 (耗时 %.1fs): {e}", t_total)
            return f"获取概念板块热度失败: {e}"

    def _fetch_concept_heat(self, days: int, top_n: int = 30):
        """概念热度 TOP N 计算：dc_index 快照预筛 + dc_daily 热度（原热度排名的 Step 1-3）。

        返回 (heat_list, idx_df, trade_date, total, success_count)：
        heat_list = [(name, pct, vol_change, heat)] 已按热度降序；
        idx_df/trade_date = Step 1 探测到的当日快照（供逐日章节/矩阵复用）；
        total = 全量概念数，success_count = dc_daily 日线成功数。
        dc_index 失败 → (None, None, None, 0, 0)。
        """
        t_start = time.perf_counter()
        # ===== Step 1: dc_index 获取全量概念板块（自带 pct_change） =====
        # 从今天往前最多尝试 5 天，找到最近有数据的交易日
        t0 = time.perf_counter()
        idx_df = None
        tried_dates = []
        for offset in range(5):
            trade_date = (datetime.now() - timedelta(days=offset)).strftime("%Y%m%d")
            tried_dates.append(trade_date)
            dc_index_params = {
                "trade_date": trade_date,
                "idx_type": "概念板块",
                "fields": "ts_code,name,pct_change,turnover_rate,up_num,down_num",
            }
            logger.info("get_concept_board_heat_rank: dc_index 请求参数(offset=%d): %s",
                        offset, dc_index_params)
            idx_df = self._api_call(self.api.dc_index, **dc_index_params)
            if idx_df is not None and not idx_df.empty:
                break
            logger.info("get_concept_board_heat_rank: dc_index trade_date=%s 返回空, 尝试前一天",
                        trade_date)

        t_dc_index = time.perf_counter() - t0
        logger.info("get_concept_board_heat_rank: dc_index 尝试日期=%s, 最终=%s, 响应: type=%s, shape=%s",
                    tried_dates,
                    trade_date,
                    type(idx_df).__name__,
                    idx_df.shape if idx_df is not None else "None")
        if idx_df is None or idx_df.empty:
            logger.info("get_concept_board_heat_rank: dc_index 耗时 %.1fs, 尝试 %d 天均无数据",
                        t_dc_index, len(tried_dates))
            return None, None, None, 0, 0

        total = len(idx_df)
        logger.info("get_concept_board_heat_rank: dc_index 耗时 %.1fs, 共 %d 个概念板块, 字段: %s",
                    t_dc_index, total, list(idx_df.columns))

        # ===== Step 2: 按 pct_change 降序取 TOP N =====
        if "pct_change" in idx_df.columns:
            idx_df = idx_df.sort_values("pct_change", ascending=False)
        top_n_df = idx_df.head(top_n)
        logger.info("get_concept_board_heat_rank: TOP%d pct_change 范围: %.2f%% ~ %.2f%%",
                    top_n,
                    float(top_n_df["pct_change"].iloc[0]) if len(top_n_df) > 0 else 0,
                    float(top_n_df["pct_change"].iloc[-1]) if len(top_n_df) > 0 else 0)

        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=days * 3)).strftime("%Y%m%d")

        # ===== Step 3: 仅对 TOP N 调 dc_daily，计算热度 =====
        heat_list = []
        success_count = 0
        fail_count = 0
        first_daily_logged = False

        for _, row in top_n_df.iterrows():
            ts_code = row["ts_code"]
            name = row["name"]
            # dc_index 自带的 pct_change 作为 fallback
            dc_pct = float(row["pct_change"]) if pd.notna(row.get("pct_change")) else 0.0

            try:
                dc_daily_params = {
                    "ts_code": ts_code,
                    "start_date": start,
                    "end_date": end,
                    "idx_type": "概念板块",
                }
                df = self._api_call(self.api.dc_daily, **dc_daily_params)
                df = _sort_asc_by_trade_date(df)   # dc_daily 行序同 sw_daily（降序），先归一

                # 第一个 dc_daily 打印入参和出参样本
                if not first_daily_logged:
                    first_daily_logged = True
                    logger.info("get_concept_board_heat_rank: 首个 dc_daily 请求: %s", dc_daily_params)
                    if df is not None and not df.empty:
                        logger.info("get_concept_board_heat_rank: 首个 dc_daily 响应: shape=%s, 字段=%s, 首行=%s",
                                    df.shape, list(df.columns), df.iloc[0].to_dict())
                    else:
                        logger.info("get_concept_board_heat_rank: 首个 dc_daily 响应: %s",
                                    "空" if df is None else f"empty, shape={df.shape}")
                if df is not None and not df.empty and len(df) >= 2:
                    success_count += 1
                    df = df.tail(days + 1)
                    closes = df["close"].astype(float)
                    first_close = float(closes.iloc[0])
                    last_close = float(closes.iloc[-1])
                    if first_close > 0:
                        pct = (last_close - first_close) / first_close * 100
                    else:
                        pct = dc_pct

                    # 成交量变化
                    vol_change = 0.0
                    vol_col = "vol" if "vol" in df.columns else None
                    if vol_col:
                        vols = df[vol_col].astype(float)
                        recent_vol = float(vols.iloc[-days:].mean()) if len(vols) >= days else float(vols.mean())
                        older_vol = float(vols.iloc[:-days].mean()) if len(vols) > days else recent_vol
                        if older_vol > 0:
                            vol_change = (recent_vol - older_vol) / older_vol * 100

                    heat_score = pct * 0.6 + vol_change * 0.4
                    heat_list.append((name, pct, vol_change, heat_score))
                else:
                    fail_count += 1
                    heat_list.append((name, dc_pct, 0.0, dc_pct * 0.6))
            except Exception:
                fail_count += 1
                heat_list.append((name, dc_pct, 0.0, dc_pct * 0.6))
                continue

        t_total = time.perf_counter() - t_start
        logger.info("get_concept_board_heat_rank: 概念热度采集完成, 耗时 %.1fs, dc_index=%.1fs, "
                    "预筛选=%d/%d, dc_daily成功=%d, 失败=%d",
                    t_total, t_dc_index, len(top_n_df), total, success_count, fail_count)

        if not heat_list:
            # 防御性分支（实际不可达：top_n_df 恒至少 1 行，每行成功/失败两路必 append）——
            # 保留以兜底未来 top_n<=0 / idx_df 异常行等变更，调用方按空列表处理
            return [], None, None, total, 0
        heat_list.sort(key=lambda x: x[3], reverse=True)
        return heat_list, idx_df, trade_date, total, success_count

    def _concept_daily_rows(self, heat_list, today_df, today_date, top_n: int = 30):
        """概念逐日涨跌幅行数据：返回 (rows, master, skipped, n_days)。

        rows = [(name, {YYYYMMDD: pct}, cum_pct)]，行序 = heat_list[:top_n] 序；
        master = 升序日期轴；skipped = 快照探测缺口；快照不足 2 天 → rows=None、
        n_days = 实际快照天数（供调用方生成"数据不足"提示）。
        """
        snapshots, skipped = self._collect_concept_daily_snapshots(
            days=daily_matrix_utils.DAILY_COLS, today_df=today_df, today_date=today_date)
        if len(snapshots) < 2:
            return None, [], skipped, len(snapshots)

        # 逐日 name → pct 查找表
        day_maps = {}   # {YYYYMMDD: {概念名: pct}}
        for d, df in snapshots.items():
            day_map = {}
            for _, row in df.iterrows():
                if pd.isna(row.get("pct_change")):
                    continue
                try:
                    day_map[str(row["name"])] = float(row["pct_change"])
                except (KeyError, TypeError, ValueError):
                    continue
            day_maps[d] = day_map

        master = sorted(snapshots.keys())
        rows = []
        for name, *_ in heat_list[:top_n]:
            series = {d: day_maps[d][name] for d in master if name in day_maps[d]}
            rows.append((name, series, daily_matrix_utils.cum_pct_from_daily(series)))
        return rows, master, skipped, len(snapshots)

    def _append_concept_daily_section(self, heat_list, today_df, today_date) -> str:
        """概念逐日涨跌幅章节（近 10 个交易日）：行 = 热度 TOP30，单元格来自东财逐日快照 pct_change。

        heat_list = [(name, pct, vol_chg, heat)]（已按热度降序）；
        today_df/today_date = Step 1 探测到的当日快照（注入共享采集器，不重复调 API）。
        累计列 = 逐日快照 pct 复利；skipped 传入 gap_dates，由 format_daily_matrix
        按展示窗口过滤（剔除窗口外探测日期，如运行当天无数据）。
        """
        rows, master, skipped, n_days = self._concept_daily_rows(heat_list, today_df, today_date)
        if rows is None:
            return "\n> 近10日逐日涨跌幅数据不足（仅 %d 天），已省略。" % n_days

        today = datetime.now().strftime("%Y%m%d")
        intraday_date = master[-1] if (master[-1] == today
                                       and _is_intraday_trading_time()) else None
        caption_parts = ["覆盖 %s ~ %s，共 %d 个交易日" % (
            daily_matrix_utils.fmt_date_short(master[0]),
            daily_matrix_utils.fmt_date_short(master[-1]), len(master)),
            "累计列为逐日快照涨跌幅复利计算"]
        if intraday_date:
            caption_parts.append("最新列 %s（盘中）为盘中未定稿数据"
                                 % daily_matrix_utils.fmt_date_short(master[-1]))
        if len(master) < daily_matrix_utils.DAILY_COLS:
            caption_parts.append("仅覆盖 %d 个交易日" % len(master))
        lag_days = (datetime.now() - pd.to_datetime(master[-1])).days
        if lag_days > 3:
            caption_parts.append("最新交易日 %s（%d 天前），数据可能滞后" % (
                daily_matrix_utils.fmt_date_short(master[-1]), lag_days))
        section = daily_matrix_utils.format_daily_matrix(
            rows, master,
            title="近%d个交易日逐日涨跌幅（概念×日期，列头 MM-DD）" % len(master),
            caption="；".join(caption_parts),
            intraday_date=intraday_date,
            gap_dates=skipped,
            name_col="概念板块",
        )
        return ("\n\n" + section) if section else ""

    def get_concept_daily_returns_matrix(self, days: int = 10, top_n: int = 30):
        """概念板块近 N 个交易日逐日涨跌幅结构化矩阵（热力图数据源）。

        行集合与概念热度 TOP N 逐日章节同口径（dc_index 快照预筛 + 热度公式排序，
        复用 _fetch_concept_heat + _concept_daily_rows）。
        返回 {"source": "tushare", "dates": [...升序], "names": [...], "pct_matrix": [...]}；
        不支持/数据不足时返回 None（结构化接口约定）。
        """
        if not self.connected:
            return None
        try:
            heat_list, idx_df, trade_date, _, _ = self._fetch_concept_heat(days, top_n)
            if not heat_list:
                return None
            rows, master, _, _ = self._concept_daily_rows(heat_list, idx_df, trade_date, top_n)
            if rows is None or len(master) < 2:
                return None
            names = [name for name, *_ in rows]
            return {"source": "tushare", "dates": master, "names": names,
                    "pct_matrix": [[pcts.get(d) for d in master] for _, pcts, _ in rows]}
        except Exception as e:
            logger.warning(f"获取概念逐日矩阵失败: {e}")
            return None

    def _get_industry_daily_dataframes(self, days: int = 120) -> dict:
        """返回各行业板块的日线 DataFrame 字典 {行业名: DataFrame}

        供 interface 层组合函数（如 get_sector_horizon_screening）做多周期重采样。
        返回空 dict 表示数据不可用。
        """
        if not self.connected:
            return {}
        try:
            idx_df = self._api_call(self.api.index_classify, src='SW2021', level='L1')
            if idx_df is None or idx_df.empty:
                return {}

            end = datetime.now().strftime("%Y%m%d")
            start = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")

            result = {}
            for _, row in idx_df.iterrows():
                code = row['index_code']
                name = row['industry_name']
                try:
                    df = self._api_call(self.api.sw_daily, ts_code=code, start_date=start, end_date=end)
                    if df is not None and not df.empty and len(df) >= 20:
                        df = _sort_asc_by_trade_date(df).rename(columns={'trade_date': 'date'})
                        result[name] = df[['date', 'close']].copy()
                except Exception:
                    continue
            return result
        except Exception:
            return {}

    def get_sector_technical_screening(self, days: int = 60) -> str:
        """逐行业技术指标矩阵（通过申万行业指数计算 RSI/MACD/均线）"""
        if not self.connected:
            return "Tushare 未连接。"
        try:
            idx_df = self._api_call(self.api.index_classify, src='SW2021', level='L1')
            if idx_df is None or idx_df.empty:
                return "未获取到申万行业分类。"

            end = datetime.now().strftime("%Y%m%d")
            start = (datetime.now() - timedelta(days=days * 3)).strftime("%Y%m%d")

            screening_results = []
            for _, row in idx_df.iterrows():
                code = row['index_code']
                name = row['industry_name']
                try:
                    df = self._api_call(self.api.sw_daily, ts_code=code, start_date=start, end_date=end)
                    if df is None or df.empty or len(df) < 20:
                        continue
                    df = _sort_asc_by_trade_date(df).tail(days)
                    closes = df['close'].astype(float)

                    # 均线排列
                    ma5 = closes.tail(5).mean()
                    ma10 = closes.tail(10).mean()
                    ma20 = closes.tail(20).mean()
                    ma60 = closes.tail(60).mean() if len(closes) >= 60 else ma20
                    if ma5 > ma10 > ma20 > ma60:
                        ma_status = '多头排列'
                    elif ma5 < ma10 < ma20 < ma60:
                        ma_status = '空头排列'
                    else:
                        ma_status = '交叉震荡'

                    # RSI(14) 简化
                    delta = closes.diff()
                    gain = delta.clip(lower=0).tail(14).mean()
                    loss = (-delta.clip(upper=0)).tail(14).mean()
                    rsi = 100 - (100 / (1 + gain / loss)) if loss > 0 else 100

                    # MACD 简化信号
                    ema12 = closes.ewm(span=12, adjust=False).mean()
                    ema26 = closes.ewm(span=26, adjust=False).mean()
                    dif = ema12.iloc[-1] - ema26.iloc[-1]
                    dea = pd.Series([ema12.iloc[-1] - ema26.iloc[-1]]).ewm(span=9, adjust=False).mean().iloc[-1]

                    # 量比
                    vol = df['vol'].astype(float) if 'vol' in df.columns else pd.Series([0])
                    vol_ratio = vol.tail(5).mean() / vol.tail(20).mean() if vol.tail(20).mean() > 0 else 0

                    screening_results.append({
                        '行业': name, '均线': ma_status,
                        'RSI': f'{rsi:.0f}', 'MACD信号': '金叉' if dif > dea else '死叉',
                        '量比': f'{vol_ratio:.2f}',
                        '收盘': f'{closes.iloc[-1]:.2f}',
                    })
                except Exception:
                    continue

            if not screening_results:
                return "未获取到行业技术数据。"

            lines = [f"# 全行业技术状态矩阵（近 {days} 日，数据源：Tushare 申万指数）\n"]
            lines.append(f"| 行业 | 均线排列 | RSI | MACD | 量比 | 收盘价 |")
            lines.append(f"|------|----------|-----|------|------|--------|")
            for r in screening_results:
                lines.append(f"| {r['行业']} | {r['均线']} | {r['RSI']} | {r['MACD信号']} | {r['量比']} | {r['收盘']} |")
            return "\n".join(lines)
        except Exception as e:
            logger.warning(f"行业技术筛选失败: {e}")
            return f"行业技术筛选失败: {e}"

    def get_sector_relative_strength(self, days: int = 20) -> str:
        """各行业相对上证综指的 alpha 排名"""
        if not self.connected:
            return "Tushare 未连接。"
        try:
            # 基准：上证综指
            end = datetime.now().strftime("%Y%m%d")
            start = (datetime.now() - timedelta(days=days * 3)).strftime("%Y%m%d")
            base_df = self._api_call(self.api.index_daily, ts_code='000001.SH', start_date=start, end_date=end)
            if base_df is None or base_df.empty:
                return "无法获取基准指数数据。"
            base_df = _sort_asc_by_trade_date(base_df)
            base_close = base_df['close'].astype(float)
            base_ret = (base_close.iloc[-1] / base_close.iloc[0] - 1) * 100

            idx_df = self._api_call(self.api.index_classify, src='SW2021', level='L1')
            if idx_df is None or idx_df.empty:
                return "未获取到申万行业分类。"

            alpha_list = []
            for _, row in idx_df.iterrows():
                code = row['index_code']
                name = row['industry_name']
                try:
                    df = self._api_call(self.api.sw_daily, ts_code=code, start_date=start, end_date=end)
                    if df is not None and not df.empty and len(df) >= 5:
                        df = _sort_asc_by_trade_date(df)
                        df_close = df['close'].astype(float)
                        sector_ret = (df_close.iloc[-1] / df_close.iloc[0] - 1) * 100
                        alpha = sector_ret - base_ret
                        alpha_list.append((name, sector_ret, alpha))
                except Exception:
                    continue

            if not alpha_list:
                return "未获取到行业相对强度数据。"

            alpha_list.sort(key=lambda x: x[2], reverse=True)
            lines = [f"# 行业相对强度排名（近 {days} 日，基准：上证综指 {base_ret:+.2f}%）\n"]
            lines.append("| 排名 | 行业 | 行业涨幅(%) | Alpha(%) | 强弱 |")
            lines.append("|------|------|-------------|----------|------|")
            for i, (name, sr, alpha) in enumerate(alpha_list, 1):
                strength = '强于大盘' if alpha > 3 else ('弱于大盘' if alpha < -3 else '与大盘同步')
                lines.append(f"| {i} | {name} | {sr:+.2f}% | {alpha:+.2f}% | {strength} |")
            return "\n".join(lines)
        except Exception as e:
            logger.warning(f"行业相对强度计算失败: {e}")
            return f"行业相对强度计算失败: {e}"

    def get_concept_rotation_ranking(self, days: int = 5, top_n: int = 10) -> str:
        """获取近 N 个交易日题材板块轮动矩阵（Tushare 打板专题数据）。

        逐日调用 limit_cpt_list，汇总成包含逐日 TOP N 榜单、上榜统计、
        轮动分类标注、强度分级的完整 Markdown 表格。
        """
        if not self.connected:
            return "Tushare 未连接。"

        # ===== Step 1: 逐日回退采集 =====
        collected = []          # [(trade_date_str, DataFrame), ...] 按采集顺序（从早到晚）
        probe_date = datetime.now()
        max_probe = days * 3    # 周末+节假日兜底

        first_error = None
        error_count = 0
        for _ in range(max_probe):
            d = probe_date.strftime("%Y%m%d")
            try:
                df = self._api_call(self.api.limit_cpt_list, trade_date=d)
                if df is not None and not df.empty:
                    # rank 字段为 str 类型，显式转数值
                    df['rank_num'] = pd.to_numeric(df['rank'], errors='coerce')
                    df = df.sort_values(['rank_num', 'up_nums'], ascending=[True, False])
                    collected.append((d, df))
            except Exception as e:
                if first_error is None:
                    first_error = str(e)
                error_count += 1
            probe_date -= timedelta(days=1)
            if len(collected) >= days:
                break

        collected.reverse()  # 调整为从早到晚
        if not collected:
            hint = ""
            if error_count > 0:
                hint = ("（探测 {}/{} 天失败，首次错误: {}；"
                        "limit_cpt_list 为 Tushare 打板专题接口，需 ≥2000 积分权限）").format(
                    error_count, max_probe, first_error[:120] if first_error else "未知")
            else:
                hint = "（可能原因：非交易日/数据未入库/权限不足）"
            return "近 {} 个交易日无打板专题数据。{}".format(days, hint)

        actual_days = len(collected)
        dates = [d for d, _ in collected]

        # ===== Step 2: 逐日 TOP N =====
        top_n_daily = {}
        for d, df in collected:
            top_n_daily[d] = df.head(top_n)

        # ===== Step 3: 上榜统计 & 轮动分类 =====
        seen = {}  # name -> {first_date, last_date, count, up_nums_list, cons_boards_list, rank_nums_list}
        for d, df in collected:
            top_names = set(df.head(top_n)['name'].tolist())
            for _, row in df.iterrows():
                name = row['name']
                rn = row.get('rank_num', 999)
                if pd.isna(rn):
                    rn = 999
                else:
                    rn = int(rn)
                if name not in seen:
                    official_days = int(row['days']) if pd.notna(row.get('days')) else 0
                    seen[name] = {
                        'first_date': d, 'last_date': d, 'count': 0,
                        'up_nums_list': [], 'cons_boards_list': [], 'rank_nums_list': [],
                        'official_days': official_days,
                    }
                entry = seen[name]
                if d > entry['last_date']:
                    entry['last_date'] = d
                if name in top_names:
                    entry['count'] += 1
                up_n = int(row['up_nums']) if pd.notna(row.get('up_nums')) else 0
                cons_n = int(row['cons_nums']) if pd.notna(row.get('cons_nums')) else 0
                parsed = _parse_up_stat(row.get('up_stat', ''))
                entry['up_nums_list'].append(up_n)
                entry['cons_boards_list'].append(parsed['consecutive_boards'])
                entry['rank_nums_list'].append(rn)

        last_date = dates[-1]
        top_names_last = set(top_n_daily[last_date]['name'].tolist()) if last_date in top_n_daily else set()

        stats = []
        for name, e in seen.items():
            in_last = name in top_names_last
            # 最新数据
            latest_up = e['up_nums_list'][-1] if e['up_nums_list'] else 0
            latest_cons = e['cons_boards_list'][-1] if e['cons_boards_list'] else 0
            # 趋势
            up_trend = '走扩' if len(e['up_nums_list']) >= 2 and e['up_nums_list'][-1] > e['up_nums_list'][0] else \
                       ('走弱' if len(e['up_nums_list']) >= 2 and e['up_nums_list'][-1] < e['up_nums_list'][0] else '持平')
            board_trend = '上升' if len(e['cons_boards_list']) >= 2 and e['cons_boards_list'][-1] > e['cons_boards_list'][0] else \
                          ('下降' if len(e['cons_boards_list']) >= 2 and e['cons_boards_list'][-1] < e['cons_boards_list'][0] else '持平')

            stats.append({
                'name': name, 'count': e['count'], 'in_last': in_last,
                'latest_up': latest_up, 'latest_cons': latest_cons,
                'up_trend': up_trend, 'board_trend': board_trend,
                'first_date': e['first_date'], 'last_date': e['last_date'],
                'rank_nums': e['rank_nums_list'],
                'official_days': e.get('official_days', 0),
            })

        # ===== Step 4: 强度分级（基于当日横截面分位数） =====
        all_up = [s['latest_up'] for s in stats if s['latest_up'] > 0]
        all_cons = [s['latest_cons'] for s in stats if s['latest_cons'] > 0]
        if all_up:
            up_p30 = pd.Series(all_up).quantile(0.30)
            up_p70 = pd.Series(all_up).quantile(0.70)
        else:
            up_p30 = up_p70 = 0
        cons_p50 = pd.Series(all_cons).quantile(0.50) if all_cons else 0

        for s in stats:
            up_high = s['latest_up'] >= up_p70
            up_mid = up_p30 <= s['latest_up'] < up_p70
            cons_high = s['latest_cons'] >= cons_p50

            if up_high and cons_high:
                s['strength'] = '强势主线'
            elif up_high and not cons_high:
                s['strength'] = '普涨式轮动'
            elif not up_high and cons_high:
                s['strength'] = '龙头独立行情'
            else:
                s['strength'] = '边缘题材'

            # 轮动分类："首次上榜日"的判定基准——倒数第2个交易日（含）之后首次出现 = 新晋
            new_threshold_date = dates[-2] if len(dates) >= 2 else dates[0]

            if s['count'] >= 3 and s['in_last']:
                s['rotation'] = '持续主线'
            elif s['count'] >= 1 and s['in_last'] and s['first_date'] >= new_threshold_date:
                s['rotation'] = '新晋异动'
            elif s['count'] >= 1 and not s['in_last']:
                s['rotation'] = '退潮'
            else:
                s['rotation'] = '波动'

        # ===== Step 5: TOP N 外蓄势上升 =====
        rising_outside = []
        for s in stats:
            if s['rotation'] == '波动' and len(s['rank_nums']) >= 2:
                ranks = s['rank_nums']
                # 排名持续上升（rank_num 持续下降）
                if ranks[-1] < ranks[0] and ranks[-1] < ranks[-2]:
                    trend_str = ' → '.join([f"#{r}" for r in ranks])
                    rising_outside.append({
                        'name': s['name'], 'trend': trend_str,
                        'up_trend': s['up_trend'], 'latest_up': s['latest_up'],
                    })

        rising_outside.sort(key=lambda x: x['latest_up'], reverse=True)
        rising_outside = rising_outside[:5]

        # ===== Step 6: 格式化输出 =====
        lines = [
            f"# 题材板块近 {actual_days} 日逐日轮动矩阵",
            f"（Tushare 打板专题数据，THS 题材分类，共 {len(stats)} 个板块上榜）\n",
        ]

        # 逐日 TOP N
        lines.append("## 逐日 TOP{}\n".format(top_n))
        for d in dates:
            if d in top_n_daily:
                entries = []
                for _, row in top_n_daily[d].iterrows():
                    name = row['name']
                    up_n = int(row['up_nums']) if pd.notna(row.get('up_nums')) else 0
                    up_stat_str = str(row.get('up_stat', '')) if pd.notna(row.get('up_stat')) else ''
                    entries.append(f"{name}(涨停{up_n}/{up_stat_str})")
                lines.append(f"| {d} | {' | '.join(entries)} |")
        lines.append("")

        # 上榜统计 & 轮动分类
        stats_sorted = sorted(stats, key=lambda x: (-x['count'], -x['latest_up']))
        lines.append("## 板块上榜统计与轮动分类\n")
        lines.append("| 板块 | 上榜次数 | 最新涨停家数 | 最新连板高度 | 涨停趋势 | 连板趋势 | 强度分级 | 轮动标注 | 官方热度持续天数 |")
        lines.append("|------|---------|------------|------------|----------|----------|----------|----------|----------------|")
        for s in stats_sorted:
            cons_str = "{}板".format(s['latest_cons']) if s['latest_cons'] > 0 else "无"
            od = s.get('official_days', 0)
            od_str = str(od) if od > 0 else "—"
            lines.append(
                f"| {s['name']} | {s['count']}/{actual_days} | {s['latest_up']} | {cons_str} "
                f"| {s['up_trend']} | {s['board_trend']} | {s['strength']} | {s['rotation']} | {od_str} |"
            )
        lines.append("")
        lines.append(
            "> - \"上榜次数\"：{} 天观察窗口内进入当日 TOP{} 的天数 / 总交易日数\n".format(actual_days, top_n) +
            "> - \"官方热度持续天数\"：Tushare `days` 字段，板块在官方统计口径下连续上榜天数（可能远超观察窗口），与上榜次数口径不同，供横向参考\n"
            "> - 强度分级基于当日所有上榜板块的涨停家数分位数（P30/P70）和连板高度中位数（P50），随市场冷暖自适应"
        )

        # TOP N 外蓄势上升
        if rising_outside:
            lines.append("\n## TOP {} 外蓄势上升板块\n".format(top_n))
            lines.append("| 板块 | 排名变化 | 涨停家数趋势 | 最新涨停家数 |")
            lines.append("|------|---------|------------|------------|")
            for r in rising_outside:
                lines.append(f"| {r['name']} | {r['trend']} | {r['up_trend']} | {r['latest_up']} |")
            lines.append("")
            lines.append("> 这部分板块虽未进入每日 TOP{}，但排名持续上升——是轮动预测的重要补充信号。".format(top_n))

        return "\n".join(lines)

    # ==================== 板块层 — 概念逐日涨幅 TOP20（东财 EM 体系） ====================

    # 东财概念板块快照按日缓存（仅非当日日期），供 get_concept_daily_top_gains 与
    # get_concept_board_heat_rank 逐日章节共用（经 _collect_concept_daily_snapshots）；
    # 与 _DC_CONCEPT_INDEX_CACHE（单快照、跨天失效）并存，现有调用方零改动。
    _DC_CONCEPT_DAILY_CACHE = {}

    def get_concept_daily_top_gains(self, days: int = 10, top_n: int = 20) -> str:
        """获取近 N 个交易日东财概念板块逐日涨幅 TOP 矩阵（含换手率 + 跨日上榜统计）。

        逐日回退采集 dc_index(trade_date=d, idx_type="概念板块")，每天按 pct_change
        降序取 TOP N（先 dropna 再排序，NaN 严格不入榜）。换手率仅供 LLM 识别
        低成交迷你概念（涨幅虚高），数据层不做过滤。
        """
        if not self.connected:
            return "Tushare 未连接。"
        try:
            return self._collect_concept_daily_top_gains(days, top_n)
        except Exception as e:
            logger.warning("获取逐日概念涨幅失败: %s", e)
            return f"获取逐日概念涨幅失败: {e}"

    def _collect_concept_daily_snapshots(self, days: int, today_df=None, today_date=None):
        """逐日回退采集东财概念板块快照 → (snapshots: {YYYYMMDD: df} 升序, skipped)。

        get_concept_daily_top_gains 与 get_concept_board_heat_rank 共用：
        - 按日缓存 _DC_CONCEPT_DAILY_CACHE 精确命中（仅非当日写入，cap=30）
        - today_df/today_date：调用方已探测到的当日快照直接注入（不重复调 API、不写缓存）
        - skipped：工作日无数据的日期（周末正常休市不入清单）
        """
        today = datetime.now().strftime("%Y%m%d")
        collected = []   # [(date_str, DataFrame)]
        skipped = []     # 无数据被跳过的日期
        if today_df is not None and not today_df.empty and today_date:
            collected.append((today_date, today_df))
        have = {d for d, _ in collected}
        probe_date = datetime.now()
        max_probe = days * 3    # 周末+节假日兜底
        for _ in range(max_probe):
            d = probe_date.strftime("%Y%m%d")
            if d not in have:
                if d in self._DC_CONCEPT_DAILY_CACHE:
                    df = self._DC_CONCEPT_DAILY_CACHE[d]
                else:
                    try:
                        df = self._api_call(
                            self.api.dc_index, trade_date=d, idx_type="概念板块",
                            fields="ts_code,name,pct_change,turnover_rate,up_num,down_num",
                        )
                    except Exception as e:
                        logger.warning("dc_index %s 调用异常: %s", d, e)
                        df = None
                    if df is not None and not df.empty:
                        # 仅缓存非当日日期：当日快照盘中/收盘前未定稿，按日键缓存后跨日不会失效
                        if d != today:
                            self._DC_CONCEPT_DAILY_CACHE[d] = df
                            _prune_daily_cache(self._DC_CONCEPT_DAILY_CACHE, cap=30)
                if df is not None and not df.empty:
                    collected.append((d, df))
                    have.add(d)
                elif probe_date.weekday() < 5:
                    # 仅标注工作日缺数据（周末无数据属正常休市，不入数据缺口清单）
                    skipped.append(d)
            probe_date -= timedelta(days=1)
            if len(collected) >= days:
                break

        collected.sort(key=lambda kv: kv[0])   # 调整为从早到晚
        return {d: df for d, df in collected}, skipped

    def _collect_concept_daily_top_gains(self, days: int, top_n: int) -> str:
        """get_concept_daily_top_gains 实现体（外层已兜住意外异常，遵守基类不抛异常契约）。"""
        today = datetime.now().strftime("%Y%m%d")
        intraday_today = _is_intraday_trading_time()

        # ===== Step 1: 逐日回退采集（共享采集器，语义与旧实现一致） =====
        snapshots, skipped = self._collect_concept_daily_snapshots(days)
        collected = sorted(snapshots.items())   # [(date_str, DataFrame)] 从早到晚
        if not collected:
            return f"近 {days} 个交易日无概念板块快照数据。"

        # ===== Step 2: 逐日 TOP N（dropna → 降序 → 取前 N） + 跨日上榜统计 =====
        daily_top = []   # [(date_str, [(name, pct, turnover), ...])]
        stats = {}       # name -> {"count", "last_date", "last_pct"}
        for d, df in collected:
            work = df.dropna(subset=["pct_change"]).copy()
            work["pct_num"] = pd.to_numeric(work["pct_change"], errors="coerce")
            work = work.dropna(subset=["pct_num"])
            if work.empty:
                # 该日快照有行但涨幅全缺失 → 视同缺数据日
                # （返回了数据行的日期必为交易日，无需周末过滤，与主跳过路径口径一致）
                skipped.append(d)
                continue
            work = work.sort_values("pct_num", ascending=False)
            entries = []
            for _, row in work.head(top_n).iterrows():
                name = str(row["name"])
                pct = float(row["pct_num"])
                entries.append((name, pct, row.get("turnover_rate")))
                stat = stats.setdefault(name, {"count": 0, "last_date": d, "last_pct": pct})
                stat["count"] += 1
                if d >= stat["last_date"]:
                    stat["last_date"] = d
                    stat["last_pct"] = pct
            daily_top.append((d, entries))

        # ===== Step 3: 格式化输出 =====
        actual_days = len(daily_top)
        lines = [f"# 近 {actual_days} 日东财概念板块逐日涨幅 TOP{top_n} 矩阵",
                 "（东财 EM 概念分类，括号内为 涨幅%/换手%）\n"]
        lines.append(f"| 日期 | TOP{top_n} 概念板块（涨幅%/换手%） |")
        lines.append("|------|------------------------------|")
        for d, entries in daily_top:
            date_cell = d + ("（盘中）" if d == today and intraday_today else "")
            cell = "、".join(_fmt_concept_entry(n, p, t) for n, p, t in entries)
            lines.append(f"| {date_cell} | {cell} |")

        # 跨日上榜统计（仅上榜 ≥2 次的板块，控制 prompt 长度）
        frequent = {k: v for k, v in stats.items() if v["count"] >= 2}
        if frequent:
            lines.append("\n## 跨日上榜统计")
            lines.append("| 概念板块 | 上榜次数 | 最近上榜日期 | 最新涨幅 |")
            lines.append("|----------|---------|------------|---------|")
            for name, v in sorted(frequent.items(),
                                  key=lambda kv: (-kv[1]["count"], -kv[1]["last_pct"])):
                lines.append(
                    f"| {name} | {v['count']}/{actual_days} | {v['last_date']} | {v['last_pct']:+.1f}% |"
                )
        if skipped:
            lines.append("\n> 数据缺口：以下日期无数据已跳过：" + "、".join(sorted(skipped)))
        return "\n".join(lines)

    # ==================== 板块层 — 连板梯队与情绪数据（全市场） ====================

    # limit_list_d 单日全表按日缓存（仅非当日日期），相邻两次运行窗口高度重叠，
    # 稳态下每次运行仅需拉取 1~3 个新交易日。
    _LIMIT_LIST_D_DAILY_CACHE = {}

    def get_limit_up_ladder(self, days: int = 20) -> str:
        """获取近 N 个交易日全市场连板梯队与情绪数据。

        逐日回退采集 limit_list_d(trade_date=d)，一次拉全表按 limit 字段三分桶
        （U 涨停 / D 跌停 / Z 炸板），归一化后经共享模块计算晋级率/炸板率并格式化。
        """
        if not self.connected:
            return "Tushare 未连接。"
        try:
            return self._collect_limit_up_ladder(days)
        except Exception as e:
            logger.warning("获取连板梯队失败: %s", e)
            return f"获取连板梯队失败: {e}"

    def _collect_limit_up_ladder(self, days: int) -> str:
        """get_limit_up_ladder 实现体（外层已兜住意外异常，遵守基类不抛异常契约）。"""
        today = datetime.now().strftime("%Y%m%d")
        intraday_today = _is_intraday_trading_time()

        # ===== Step 1: 逐日回退采集 + 归一化 =====
        records = []   # 归一化 day records（从早到晚）
        skipped = []   # 无数据被跳过的日期
        probe_date = datetime.now()
        max_probe = days * 3    # 周末+节假日兜底
        for _ in range(max_probe):
            d = probe_date.strftime("%Y%m%d")
            if d in self._LIMIT_LIST_D_DAILY_CACHE:
                df = self._LIMIT_LIST_D_DAILY_CACHE[d]
            else:
                try:
                    df = self._api_call(self.api.limit_list_d, trade_date=d)
                except Exception as e:
                    logger.warning("get_limit_up_ladder: limit_list_d %s 调用异常: %s", d, e)
                    df = None
                if df is not None and not df.empty:
                    # 仅缓存非当日日期：当日涨停/炸板数据盘中不完整，按日键缓存后跨日不会失效
                    if d != today:
                        self._LIMIT_LIST_D_DAILY_CACHE[d] = df
                        _prune_daily_cache(self._LIMIT_LIST_D_DAILY_CACHE, cap=40)
            record = self._normalize_ladder_day(
                d, df, intraday=(d == today and intraday_today)
            ) if df is not None and not df.empty else None
            if record is None:
                if probe_date.weekday() < 5:
                    # 仅标注工作日缺数据（周末无数据属正常休市，不入数据缺口清单）
                    skipped.append(d)
            else:
                records.append(record)
            probe_date -= timedelta(days=1)
            if len(records) >= days:
                break

        records.reverse()   # 调整为从早到晚
        if not records:
            return f"近 {days} 个交易日无连板梯队数据。"

        # ===== Step 2: 晋级率/炸板率计算 + 格式化（共享纯函数模块） =====
        promo = calc_promotion_rates(records)
        break_rates = calc_break_rate(records)
        return format_ladder_matrix(records, promo, break_rates, skipped_dates=skipped)

    def _normalize_ladder_day(self, date_str: str, df: pd.DataFrame,
                              intraday: bool = False) -> dict:
        """limit_list_d 单日 DataFrame → 归一化梯队记录。

        涨停 U：按 limit_times 归一化连板数（NaN/0 → 首板 1）；
        跌停 D：dt_total；炸板 Z：zb_total；开板回封：U 且 open_times>0。
        字段缺失（如无 limit 列）返回 None，调用方视同无数据日。
        """
        if df is None or df.empty or "limit" not in df.columns:
            return None
        up = df[df["limit"] == "U"]
        down = df[df["limit"] == "D"]
        broken = df[df["limit"] == "Z"]

        stock_boards = {}
        for _, row in up.iterrows():
            ts_code = str(row.get("ts_code", "")).strip()
            if not ts_code:
                continue
            lt = row.get("limit_times")
            try:
                boards = int(float(lt)) if pd.notna(lt) else 1
            except (TypeError, ValueError):
                boards = 1
            stock_boards[ts_code] = max(boards, 1)   # NaN/0 → 首板

        ladder = {}
        for b in stock_boards.values():
            if b >= 2:
                ladder[b] = ladder.get(b, 0) + 1

        open_broken = 0
        if "open_times" in up.columns:
            for v in up["open_times"]:
                try:
                    if pd.notna(v) and float(v) > 0:
                        open_broken += 1
                except (TypeError, ValueError):
                    continue

        return {
            "date": date_str,
            "zt_total": len(stock_boards),
            "dt_total": len(down),
            "ladder": ladder,
            "open_broken": open_broken,
            "zb_total": len(broken),
            "stock_boards": stock_boards,
            "intraday": intraday,
        }

    # ==================== 事件研究系统 — 结构化接口 ====================

    def get_index_data_df(self, index_code: str, start_date: str, end_date: str):
        """获取指数日线结构化行情（DataFrame，完整区间，内部分页）。

        与展示用 get_index_data 不同：不截断（limit 5），按时间窗口分页
        循环拉取，保证事件研究所需的完整估计窗口 + 事件窗口数据。
        返回标准列（2026-09-13 扩列，证券市场数据库统一方案 3.5.1）：
        trade_date / open / high / low / close / pre_close / change / pct_chg /
        vol / amount——pre_close/change/pct_chg 存上游原值不自算；上游帧缺列时
        对应列留 NULL 并告警（缺列降级不阻断）。
        获取失败返回 None。
        """
        if not self.connected:
            logger.warning("Tushare 未连接，无法获取结构化指数行情。")
            return None
        if index_code in self.GLOBAL_INDEX_CODE_MAP:
            return self._get_global_index_df(
                self.GLOBAL_INDEX_CODE_MAP[index_code], start_date, end_date)
        code = self._normalize_code(index_code)
        try:
            start_dt = datetime.strptime(start_date.replace("-", ""), "%Y%m%d")
            end_dt = datetime.strptime(end_date.replace("-", ""), "%Y%m%d")
        except ValueError:
            logger.warning("日期格式错误: %s / %s", start_date, end_date)
            return None

        # 分页：按 120 个自然日一段循环拉取，避免 index_daily 单次 limit 截断
        frames = []
        chunk_start = start_dt
        while chunk_start <= end_dt:
            chunk_end = min(chunk_start + timedelta(days=119), end_dt)
            try:
                df = self._api_call(
                    self.api.index_daily,
                    ts_code=code,
                    start_date=chunk_start.strftime("%Y%m%d"),
                    end_date=chunk_end.strftime("%Y%m%d"),
                )
                if df is not None and not df.empty:
                    frames.append(_sort_asc_by_trade_date(df))
            except Exception as e:
                logger.warning(
                    "指数 %s 分段拉取失败 [%s ~ %s]: %s",
                    code, chunk_start.date(), chunk_end.date(), e,
                )
            chunk_start = chunk_end + timedelta(days=1)

        if not frames:
            return None

        df = pd.concat(frames, ignore_index=True)
        df = df.drop_duplicates(subset=["trade_date"]).sort_values("trade_date")

        # 标准化列：trade_date(YYYY-MM-DD str，与 AKShare 输出一致) / open / high /
        # low / close / pre_close / change / pct_chg / vol / amount（扩列三列存上游原值；
        # 缺列降级——上游帧缺列时对应列留 NULL 并告警，不阻断）
        for extra in ("pre_close", "change", "pct_chg"):
            if extra not in df.columns:
                logger.warning("get_index_data_df: 上游帧缺 %s 列，该列留 NULL", extra)
        std = pd.DataFrame({
            "trade_date": pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d"),
            "open": pd.to_numeric(df.get("open"), errors="coerce"),
            "high": pd.to_numeric(df.get("high"), errors="coerce"),
            "low": pd.to_numeric(df.get("low"), errors="coerce"),
            "close": pd.to_numeric(df.get("close"), errors="coerce"),
            "pre_close": pd.to_numeric(df.get("pre_close"), errors="coerce"),
            "change": pd.to_numeric(df.get("change"), errors="coerce"),
            "pct_chg": pd.to_numeric(df.get("pct_chg"), errors="coerce"),
            "vol": pd.to_numeric(df.get("vol"), errors="coerce"),
            "amount": pd.to_numeric(df.get("amount"), errors="coerce"),
        })
        return std

    def _get_global_index_df(self, ts_global_code: str, start_date: str, end_date: str):
        """非 CN 指数日线（index_global 端点）→ 10 列标准帧（2026-09-14 US/KR 上线）。

        - 上游自带 pre_close/change/pct_chg：直取原值不自算（与 CN index_daily
          扩列三列"存上游原值"同口径）；amount 上游无此列 → 恒 NaN（与 AKShare
          兜底行 amount 恒 NaN 同口径）；swing 丢弃
        - 单次调用不内部再分页：调用方窗口已受限（回填 1825 天/段——实测 8.7 年
          单次 OK、1900 年起超大窗口 ReadTimeout；增量 3 个交易日）
        - 端点返回降序，升序归一后输出（"日线消费必须升序"不变量）
        - 异常/空/未连接返回 None（契约：失败不抛，触发采集链换兜底源）
        """
        if not self.connected:
            logger.warning("Tushare 未连接，无法获取全球指数行情。")
            return None
        try:
            df = self._api_call(
                self.api.index_global,
                ts_code=ts_global_code,
                start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""),
            )
            if df is None or df.empty:
                return None
            df = _sort_asc_by_trade_date(df)
            return pd.DataFrame({
                "trade_date": pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d"),
                "open": pd.to_numeric(df.get("open"), errors="coerce"),
                "high": pd.to_numeric(df.get("high"), errors="coerce"),
                "low": pd.to_numeric(df.get("low"), errors="coerce"),
                "close": pd.to_numeric(df.get("close"), errors="coerce"),
                "pre_close": pd.to_numeric(df.get("pre_close"), errors="coerce"),
                "change": pd.to_numeric(df.get("change"), errors="coerce"),
                "pct_chg": pd.to_numeric(df.get("pct_chg"), errors="coerce"),
                "vol": pd.to_numeric(df.get("vol"), errors="coerce"),
                # index_global 上游无 amount 列，恒 NaN（入库 NULL 属事实）
                "amount": float("nan"),
            })
        except Exception as e:
            # 拉取/构帧/缺列任何失败一律 None（契约：不抛，触发采集链换兜底源）
            logger.warning("全球指数 %s 拉取失败 [%s ~ %s]: %s",
                           ts_global_code, start_date, end_date, e)
            return None

    # ==================== 技术因子 — 结构化接口（不自算，2026-09-12 决策） ====================

    def get_index_factor_df(self, index_code: str, start_date: str, end_date: str, fields: str | None = None):
        """指数每日技术面因子（idx_factor_pro）→ DataFrame，分页保证完整区间。

        官方单次上限 8000 行（实测全历史 8715 行不截断，但设计仍按上限分页保险）：
        按 1825 天（≈5 年）一段循环（≈1220 行/段 ≪ 8000）。返回列：trade_date（升序，
        YYYY-MM-DD str）+ 各因子列（to_numeric）。任一页失败记 warning 继续；全部失败返回 None。
        返回 DataFrame 携带 attrs["missing_chunks"] = 真实缺段数（异常/超时段 + 与数据跨度
        重叠的空段；基日前合法空段不计），消费方据此拒绝部分入库。
        """
        if not self.connected:
            logger.warning("Tushare 未连接，无法获取指数技术因子。")
            return None
        code = self._normalize_code(index_code)
        try:
            start_dt = datetime.strptime(start_date.replace("-", ""), "%Y%m%d")
            end_dt = datetime.strptime(end_date.replace("-", ""), "%Y%m%d")
        except ValueError:
            logger.warning("日期格式错误: %s / %s", start_date, end_date)
            return None

        frames = []
        empty_windows: list[tuple[datetime, datetime]] = []
        failed_chunks = 0
        chunk_start = start_dt
        while chunk_start <= end_dt:
            # 1825 天（≈5 年）/段：≈1220 交易日 ≪ 8000 官方单次上限；边界 +1 天续段不重叠不漏日
            chunk_end = min(chunk_start + timedelta(days=5 * 365), end_dt)
            try:
                df = self._api_call(
                    self.api.idx_factor_pro,
                    ts_code=code,
                    start_date=chunk_start.strftime("%Y%m%d"),
                    end_date=chunk_end.strftime("%Y%m%d"),
                    fields=fields or INDEX_FACTOR_FIELDS,
                )
                if df is not None and not df.empty:
                    frames.append(_sort_asc_by_trade_date(df))
                elif df is not None:
                    # 空段：可能是基日前的合法空（如 000688 科创50 基日 2019-12-31，
                    # 更早段必然无数据），也可能是真洞——与成功帧日期跨度比较后归类
                    empty_windows.append((chunk_start, chunk_end))
                else:
                    failed_chunks += 1
            except Exception as e:
                failed_chunks += 1
                logger.warning(
                    "指数 %s 因子分段拉取失败 [%s ~ %s]: %s",
                    code, chunk_start.date(), chunk_end.date(), e,
                )
            chunk_start = chunk_end + timedelta(days=1)

        if not frames:
            return None

        df = pd.concat(frames, ignore_index=True)
        df = df.drop_duplicates(subset=["trade_date"]).sort_values("trade_date")

        # 空段归类：窗口完全落在数据跨度之外（早于最早交易日/晚于最晚交易日）为合法空，
        # 与跨度重叠的空段才是真洞（区间中间缺数据）
        missing_chunks = failed_chunks
        data_min = pd.to_datetime(df["trade_date"]).min()
        data_max = pd.to_datetime(df["trade_date"]).max()
        for cs, ce in empty_windows:
            if not (ce < data_min or cs > data_max):
                missing_chunks += 1

        std = pd.DataFrame({
            "trade_date": pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d"),
        })
        for col in df.columns:
            if col not in ("ts_code", "trade_date"):
                std[col] = pd.to_numeric(df[col], errors="coerce")
        # 真实缺段数透传给消费方（ingestion 据此拒绝部分入库——回填缺段会静默
        # 导致该区间指标全 null，与"数据缺失是事实"无法区分；基日前合法空段不计）
        std.attrs["missing_chunks"] = missing_chunks
        return std

    def get_stock_factor_df(self, ts_code: str, start_date: str, end_date: str, fields: str | None = None):
        """个股每日技术面因子（stk_factor_pro）→ DataFrame，单次调用。

        消费方传入短区间（AI 个股报告 ≤30 天），行数远小于 8000 上限，不分页。
        返回列：trade_date（升序，YYYY-MM-DD str）+ 各因子列（to_numeric）。失败返回 None。
        """
        if not self.connected:
            logger.warning("Tushare 未连接，无法获取个股技术因子。")
            return None
        code = self._normalize_code(ts_code)
        try:
            df = self._api_call(
                self.api.stk_factor_pro,
                ts_code=code,
                start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""),
                fields=fields or STOCK_FACTOR_FIELDS,
            )
        except Exception as e:
            logger.warning("个股 %s 因子拉取失败: %s", code, e)
            return None
        if df is None or df.empty:
            return None

        df = _sort_asc_by_trade_date(df)
        std = pd.DataFrame({
            "trade_date": pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d"),
        })
        for col in df.columns:
            if col not in ("ts_code", "trade_date"):
                std[col] = pd.to_numeric(df[col], errors="coerce")
        return std

    def get_trade_cal(self, start_date: str, end_date: str, market: str = "CN"):
        """获取交易日历（DataFrame：trade_date, is_open）。V1 仅支持 CN。"""
        if market != "CN":
            logger.warning("Tushare 交易日历仅支持 CN，收到 market=%s", market)
            return None
        if not self.connected:
            return None
        try:
            start_dt = datetime.strptime(start_date.replace("-", ""), "%Y%m%d")
            end_dt = datetime.strptime(end_date.replace("-", ""), "%Y%m%d")
        except ValueError:
            return None

        # 分页：按 2 个自然年一段循环拉取
        frames = []
        chunk_start = start_dt
        while chunk_start <= end_dt:
            chunk_end = min(chunk_start + timedelta(days=729), end_dt)
            try:
                df = self._api_call(
                    self.api.trade_cal,
                    exchange="SSE",
                    start_date=chunk_start.strftime("%Y%m%d"),
                    end_date=chunk_end.strftime("%Y%m%d"),
                    is_open="1",
                )
                if df is not None and not df.empty:
                    frames.append(df)
            except Exception as e:
                logger.warning("交易日历分段拉取失败: %s", e)
            chunk_start = chunk_end + timedelta(days=1)

        if not frames:
            return None
        df = pd.concat(frames, ignore_index=True)
        df = df.drop_duplicates(subset=["cal_date"]).sort_values("cal_date")
        return pd.DataFrame({
            "trade_date": pd.to_datetime(df["cal_date"]),
            "is_open": 1,
        })

    def get_macro_context(self, date: str, market: str = "CN") -> dict:
        """获取指定日期 CN 宏观环境指标（10 年期国债收益率）。失败返回空 dict。"""
        if market != "CN" or not self.connected:
            return {}
        result = {}
        trade_date = date.replace("-", "")
        # 中债国债收益率曲线（10 年期）。接口对积分有要求，任何失败均降级为空。
        try:
            df = self._api_call(
                self.api.yield_curve, trade_date=trade_date, curve_type="0"
            )
            if df is not None and not df.empty:
                row = df[df["term"].astype(str) == "10"]
                if not row.empty:
                    value = pd.to_numeric(row.iloc[0].get("yield"), errors="coerce")
                    if pd.notna(value):
                        result["rate_10y"] = float(value)
        except Exception as e:
            logger.warning("Tushare 10 年期国债收益率获取失败 [%s]: %s", date, e)
        return result

    # ==================== 证券市场数据库（db.instrument ingest）— 结构化接口 ====================

    # 日线标准列序（3.2.1）：实测 fund_daily 列序为
    # ts_code,trade_date,pre_close,open,high,low,close,change,pct_chg,vol,amount，
    # daily 为 ts_code,trade_date,open,high,low,close,pre_close,…——统一按标准列序
    # 重排（缺失列置 NaN），消费方不感知差异。
    _STORE_DAILY_COLS = [
        "ts_code", "trade_date", "open", "high", "low", "close",
        "pre_close", "change", "pct_chg", "vol", "amount",
    ]
    _STORE_BASIC_COLS = [
        "ts_code", "name", "market", "exchange", "industry", "area",
        "list_status", "list_date", "delist_date",
    ]
    _STORE_CONCEPT_COLS = ["ts_code", "name", "count", "exchange",
                           "list_date", "type"]

    @staticmethod
    def _reindex_store_cols(df, cols: list):
        """store 结构化接口列序归一：按 cols 顺序重排，缺失列置 NaN。"""
        if df is None or df.empty:
            return df
        return pd.DataFrame({c: df[c] if c in df.columns else float("nan")
                             for c in cols})

    @staticmethod
    def _normalize_store_member_code(code: str):
        """概念成分代码 → A 股带交易所后缀 ts_code（与 stock_basic 口径对齐）。

        ths/dc 的 con_code 为 6 位无后缀；B 股（900/200）实测不存在于 daily/
        stock_basic 端点（方案 2.1），900 防御性归入 .SH。
        非 A 股形态（美股 .O/.N、港股 .HK、非 6 位等——实测 ths 跨市场概念
        含 NVDA/AAPL/腾讯等境外标的）返回 None，由调用方 drop（V1 范围外）。
        """
        code = str(code).strip()
        if "." in code:
            if code.split(".", 1)[1] in ("SH", "SZ", "BJ"):
                return code
            return None
        if not code.isdigit() or len(code) != 6:
            return None
        if code.startswith("6"):
            return f"{code}.SH"
        if code.startswith(("0", "3")):
            return f"{code}.SZ"
        if code.startswith(("4", "8", "920")):
            return f"{code}.BJ"
        if code.startswith("9"):
            return f"{code}.SH"   # 900 B 股（实测端点不含，防御性归沪）
        return None

    def get_full_market_daily_df(self, trade_date: str, market: str = "stock"):
        """单交易日全市场日线（结构化 DataFrame，标准列序归一）。

        market: "stock"=股票 daily 接口 / "fund"=场内基金 fund_daily 接口。
        只传 trade_date 单日参数——代理端点区间查询有 6000 行静默截断
        （fund_daily 区间甚至返回 0 行），禁止区间查询（方案 3.2.2 硬性约束）。
        不支持/失败返回 None。
        """
        if market not in ("stock", "fund"):
            logger.warning("get_full_market_daily_df: 未知 market=%s", market)
            return None
        if not self.connected:
            logger.warning("Tushare 未连接，无法获取全市场日线。")
            return None
        fn = self.api.daily if market == "stock" else self.api.fund_daily
        try:
            df = self._api_call(fn, trade_date=self._normalize_date(trade_date))
            return self._reindex_store_cols(df, self._STORE_DAILY_COLS) \
                if df is not None else None
        except Exception as e:
            logger.warning("获取全市场日线失败 [%s/%s]: %s", trade_date, market, e)
            return None

    def get_full_market_factor_df(self, trade_date: str, market: str = "stock"):
        """单交易日全市场复权因子（adj_factor / fund_adj），标准列
        [ts_code, trade_date, adj_factor]。只传 trade_date 单日参数。不支持/失败返回 None。"""
        if market not in ("stock", "fund"):
            logger.warning("get_full_market_factor_df: 未知 market=%s", market)
            return None
        if not self.connected:
            return None
        fn = self.api.adj_factor if market == "stock" else self.api.fund_adj
        try:
            df = self._api_call(fn, trade_date=self._normalize_date(trade_date))
            return self._reindex_store_cols(
                df, ["ts_code", "trade_date", "adj_factor"]) if df is not None else None
        except Exception as e:
            logger.warning("获取全市场复权因子失败 [%s/%s]: %s",
                           trade_date, market, e)
            return None

    def get_stock_basic_df(self):
        """股票基本信息全量（stock_basic 不传 list_status，含退市 D/暂停 P，
        含 area 地域原生字段），标准列归一。失败返回 None。"""
        if not self.connected:
            return None
        try:
            df = self._api_call(
                self.api.stock_basic,
                fields="ts_code,name,market,exchange,industry,area,"
                       "list_status,list_date,delist_date",
            )
            return self._reindex_store_cols(df, self._STORE_BASIC_COLS) \
                if df is not None else None
        except Exception as e:
            logger.warning("获取股票基本信息全量失败: %s", e)
            return None

    def get_fund_basic_df(self):
        """场内基金基本信息全量（fund_basic market='E' 全字段透传，
        含费率 m_fee/c_fee/业绩基准 benchmark/托管人 custodian）。失败返回 None。"""
        if not self.connected:
            return None
        try:
            return self._api_call(self.api.fund_basic, market="E")
        except Exception as e:
            logger.warning("获取场内基金基本信息全量失败: %s", e)
            return None

    def get_concept_list_df(self, source: str = "ths"):
        """概念列表（多来源，标准列 [ts_code, name, count, exchange, list_date, type]）。

        ths：ths_index(type='N')（概念指数，899 行实测）；
        dc：dc_index(trade_date=<最近交易日>, idx_type='概念板块')（快照式，
        从今天向前探测最近有数据交易日），dc 缺失列置 None。
        未知 source / 失败返回 None。
        """
        if not self.connected:
            return None
        try:
            if source == "ths":
                df = self._api_call(self.api.ths_index, type="N")
                return self._reindex_store_cols(df, self._STORE_CONCEPT_COLS) \
                    if df is not None else None
            if source == "dc":
                df = self._get_dc_concept_index()
                return self._reindex_store_cols(df, self._STORE_CONCEPT_COLS) \
                    if df is not None else None
            logger.warning("get_concept_list_df: 未知 source=%s", source)
            return None
        except Exception as e:
            logger.warning("获取概念列表失败 [%s]: %s", source, e)
            return None

    def get_concept_members_df(self, concept_code: str, source: str = "ths",
                               trade_date: str = None):
        """单个概念的全部成分，统一归一为 [sector_code, ts_code]（来源的
        con_name 丢弃；决策 12 字段改名落点——DAO/采集层契约列名 sector_code；
        6 位代码补交易所后缀与 stock_basic 对齐）。

        ths：ths_member(ts_code=概念代码)——必须用 ts_code= 参数：实测代理端点
        忽略 code= 参数（返回全量截断 6000 行），ts_code= 过滤精确生效；
        trade_date 参数忽略。
        dc：dc_member(ts_code=板块代码, trade_date=最近交易日) 组合过滤——仅
        ts_code 返回该板块跨 5 个交易日快照（光刻胶 61 成分 × 5 日 ≈ 1883 行）、
        全量拉取截断 8000 行，均实测；trade_date 必传，缺失返回 None。
        """
        if source not in ("ths", "dc"):
            logger.warning("get_concept_members_df: 未知 source=%s", source)
            return None
        if not self.connected:
            return None
        if source == "dc" and not trade_date:
            logger.warning("get_concept_members_df: dc 来源必须传 trade_date（快照式）")
            return None
        try:
            if source == "ths":
                df = self._api_call(self.api.ths_member, ts_code=concept_code)
            else:
                df = self._api_call(
                    self.api.dc_member, ts_code=concept_code,
                    trade_date=self._normalize_date(trade_date),
                )
        except Exception as e:
            logger.warning("概念成分拉取失败 [%s/%s]: %s", source, concept_code, e)
            return None
        if df is None or df.empty:
            return df if df is not None else None
        if "con_code" not in df.columns:
            # 数据形态失败（缺 con_code 列）→ None，与"拉取失败"同义（采集层记 failed）
            logger.warning("概念成分响应缺 con_code 列 [%s/%s]", source, concept_code)
            return None
        codes = df["con_code"].dropna().astype(str)
        out = pd.DataFrame({
            "sector_code": concept_code,
            "ts_code": codes.map(self._normalize_store_member_code),
        })
        # 非 A 股成分（美股/港股等）drop（V1 范围外，实测 ths 概念含境外标的）
        out = out[out["ts_code"].notna()]
        # 实测 dc_member 响应含重复 con_code——同一 INSERT 批次提出两行相同 PK
        # 会报 ON CONFLICT DO UPDATE cannot affect row a second time，必须去重
        return out.drop_duplicates(subset=["ts_code"]).reset_index(drop=True)

    def get_sector_daily_df(self, source: str, ts_code: str, start_date: str,
                            end_date: str):
        """单板块指数日线（板块概念Treemap方案 3.1；base_provider 契约，
        日期参数 YYYYMMDD）。返回升序 DataFrame；失败/不支持返回 None。

        - dc：dc_daily 端点（窗口型数据源——实测仅回最近 33 交易日，更早区间 0 行，
          2026-09-13；不可用于历史回填，采集层按每日增量使用）
        - ths：ths_daily 端点（全历史单请求能力已实测：老板块 4,642 行、首行=上市日；
          TODO 暂缓采集——用户拍板"先存 dc"，启用时加分支即可）
        """
        if not self.connected:
            return None
        if source not in ("dc", "ths"):
            logger.warning("get_sector_daily_df: 未知 source=%s", source)
            return None
        try:
            if source == "dc":
                df = self._api_call(self.api.dc_daily, ts_code=ts_code,
                                    start_date=start_date, end_date=end_date,
                                    idx_type="概念板块")
            else:
                # ths 暂缓（TODO 板块概念Treemap方案 3.1：启用时走
                # self.api.ths_daily 同参数区间查询）
                return None
        except Exception as e:
            logger.warning("板块日线拉取失败 [%s/%s]: %s", source, ts_code, e)
            return None
        if df is None or df.empty:
            return df
        # 日线行序升序归一（dc_daily 降序返回，实测；踩坑见 tushare-endpoints.md）
        return _sort_asc_by_trade_date(df).reset_index(drop=True)

    # ==================== 市场特征层 — 结构化接口（T5） ====================
    # 契约见 AI/dataflows/market_features.py 模块头部；实现约束来自 T1 POC 实测
    # （logs/poc_market_features.log）：
    # - 无 ts_code 的全市场区间查询被代理端点静默截断（6000 行）→ 只做单日查询；
    # - 单标的区间查询可用（index_daily / index_global / fx_daily / fut_daily /
    #   margin / index_dailybasic）；
    # - fx_daily 单日（无 ts_code）= 0 行，必须按 ts_code + 区间；
    # - shibor / shibor_lpr / cn_m 必须 start_date/end_date（date= 返回 0 行）；
    # - 失败/无权限 → 返回 None 或写入 missing，绝不抛异常。

    # 指数特征抓取清单（与 market_features.INDEX_UNIVERSE 一致，单测比对防漂移）
    _MARKET_INDEX_CODES = (
        "000001.SH", "399001.SZ", "399006.SZ", "000688.SH",
        "000016.SH", "000852.SH", "000015.SH",
    )
    # 全球指数清单（POC：VIX/SOX/UDI/USDX/DXY/STI 无数据，不列入）
    _GLOBAL_INDEX_CODES = ("SPX", "IXIC", "DJI", "N225", "HKAH", "HKTECH",
                           "GDAXI", "FTSE")
    # 商品对照（国内价，非 WTI/伦金——POC：USOil/Copper.FXCM 停更于 2023-06-01）
    _COMMODITY_CODES = ("SC.INE", "AU.SHF", "CU.SHF")
    # 估值分位的指数清单（POC：index_dailybasic 支持 ts_code + 区间）
    _VALUATION_INDEX_CODES = ("000300.SH", "000001.SH", "000905.SH",
                              "399006.SZ", "000852.SH")
    # 代理端点无 ts_code 单日查询的静默截断阈值（方案 3.2.2 硬性约束）：
    # 与 `db.instrument.ingest.frames.TRUNCATION_ROWS` 同口径同值（frames 反向
    # 依赖本模块，不能反向 import，故此处重复常量并保持同步）。
    _TRUNCATION_ROWS = 6000

    @classmethod
    def _truncated_rows(cls, df) -> int:
        """单日全市场 DataFrame 行数 ≥ 截断阈值 → 返回行数，否则 0（评审 M9）。

        代理端点在 6000 行处**静默**截断（不报错）：若直接按残缺样本计算宽度/
        资金/估值统计，会得到看似正常实则偏误的数值。命中阈值时调用方必须
        丢弃该日样本并把截断事实写入 `missing`/`notes`（→ 接口 data_quality）。
        """
        if df is None:
            return 0
        try:
            rows = int(len(df))
        except TypeError:
            return 0
        return rows if rows >= cls._TRUNCATION_ROWS else 0

    @staticmethod
    def _series_to_list(series) -> list:
        """DataFrame 列 → list（NaN → None，供特征层判缺失）。"""
        if series is None:
            return []
        out = []
        for value in series:
            try:
                fv = float(value)
            except (TypeError, ValueError):
                out.append(None)
                continue
            out.append(None if pd.isna(fv) else fv)
        return out

    @staticmethod
    def _to_num(value):
        """单值安全转 float（NaN/空/非数值 → None）。"""
        if value is None:
            return None
        try:
            fv = float(value)
        except (TypeError, ValueError):
            return None
        return None if pd.isna(fv) else fv

    @staticmethod
    def _norm_date_value(value):
        """日期值 → YYYY-MM-DD（失败返回 None）。"""
        if value is None:
            return None
        try:
            text = str(value).strip()
            if not text or text.lower() in ("nan", "nat", "none"):
                return None
            return pd.to_datetime(text).strftime("%Y-%m-%d")
        except Exception:
            return None

    def _trade_dates_in_range(self, start_date: str, end_date: str) -> list:
        """区间内交易日（YYYY-MM-DD，升序，含首尾）；日历不可用返回 []。

        交易日历为非行情端点，允许区间查询（与 POC 一致）。
        """
        start = self._norm_date_value(self._normalize_date(start_date))
        end = self._norm_date_value(self._normalize_date(end_date))
        if not start or not end or start > end:
            return []
        cal = self.get_trade_cal(start.replace("-", ""), end.replace("-", ""))
        if cal is None or cal.empty:
            return []
        dates = []
        for value in cal["trade_date"]:
            text = value.strftime("%Y-%m-%d") if hasattr(value, "strftime") \
                else self._norm_date_value(value)
            if text:
                dates.append(text)
        return sorted(set(dates))

    def _recent_trade_dates(self, days: int, end_date: str) -> list:
        """`end_date`（含）向前最近 `days` 个交易日（YYYY-MM-DD，升序）。"""
        try:
            end_dt = datetime.strptime(self._normalize_date(end_date), "%Y%m%d")
        except (ValueError, TypeError):
            return []
        days = max(int(days), 1)
        start_dt = end_dt - timedelta(days=days * 2 + 30)
        dates = self._trade_dates_in_range(
            start_dt.strftime("%Y%m%d"), end_dt.strftime("%Y%m%d"))
        return dates[-days:]

    def _paged_range_fetch(self, fn, ts_code: str, start_date: str, end_date: str,
                           chunk_days: int = 120):
        """单标的区间分页拉取（升序去重）。失败/无数据返回 None。"""
        frames = []
        try:
            cursor = datetime.strptime(start_date, "%Y%m%d")
            end_dt = datetime.strptime(end_date, "%Y%m%d")
        except (ValueError, TypeError):
            return None
        while cursor <= end_dt:
            chunk_end = min(cursor + timedelta(days=chunk_days - 1), end_dt)
            try:
                df = self._api_call(
                    fn, ts_code=ts_code,
                    start_date=cursor.strftime("%Y%m%d"),
                    end_date=chunk_end.strftime("%Y%m%d"),
                )
            except Exception as e:
                logger.warning("区间分页拉取失败 [%s %s~%s]: %s", ts_code,
                               cursor.date(), chunk_end.date(), e)
                df = None
            if df is not None and not df.empty:
                frames.append(df)
            cursor = chunk_end + timedelta(days=1)
        if not frames:
            return None
        df = pd.concat(frames, ignore_index=True)
        date_col = "trade_date" if "trade_date" in df.columns else (
            "date" if "date" in df.columns else None)
        if date_col:
            df = df.drop_duplicates(subset=[date_col]).sort_values(date_col)
        return df

    def _macro_series(self, df, field: str, source: str, unit: str = ""):
        """宏观 DataFrame → 结构化序列 dict（特征层 `_group_series` 消费）。"""
        if df is None or df.empty or field not in df.columns:
            return None
        date_col = "date" if "date" in df.columns else (
            "trade_date" if "trade_date" in df.columns else None)
        if date_col is None:
            return None
        work = df.dropna(subset=[date_col]).sort_values(date_col)
        dates, values = [], []
        for raw_date, raw_value in zip(work[date_col], work[field]):
            date_text = self._norm_date_value(raw_date)
            if not date_text:
                continue
            dates.append(date_text)
            values.append(self._to_num(raw_value))
        if not dates:
            return None
        return {"trade_dates": dates, "field": field, field: values,
                "source": source, "unit": unit}

    # ---- 1) 指数特征 ----

    def get_market_index_features(self, curr_date: str,
                                  lookbacks=(5, 20, 60, 120, 250)):
        if not self.connected:
            logger.warning("Tushare 未连接，无法获取指数特征序列。")
            return None
        try:
            end = self._normalize_date(curr_date)
            end_dt = datetime.strptime(end, "%Y%m%d")
            need = max(int(w) for w in lookbacks) + 5
        except (ValueError, TypeError):
            return None
        start = (end_dt - timedelta(days=need * 2 + 40)).strftime("%Y%m%d")
        end_dash = end_dt.strftime("%Y-%m-%d")

        indices, missing, notes = {}, {}, []
        for code in self._MARKET_INDEX_CODES:
            df = self.get_index_data_df(code, start, end)
            if df is None or df.empty:
                missing[code] = "index_daily 无数据"
                continue
            df = df[df["trade_date"] <= end_dash].tail(need)
            if df.empty:
                missing[code] = "窗口内无数据"
                continue
            trade_dates = [str(d) for d in df["trade_date"]]
            indices[code] = {
                "trade_dates": trade_dates,
                "open": self._series_to_list(df["open"]),
                "high": self._series_to_list(df["high"]),
                "low": self._series_to_list(df["low"]),
                "close": self._series_to_list(df["close"]),
                "vol": self._series_to_list(df["vol"]),
                "amount": self._series_to_list(df["amount"]),
                "rows": len(df),
                "first_date": trade_dates[0] if trade_dates else None,
                "last_date": trade_dates[-1] if trade_dates else None,
            }
        if not indices:
            logger.warning("get_market_index_features: 指数数据全部不可用。")
            return None
        notes.append("index_daily 按 ts_code 区间分页拉取（POC：区间查询对单标的可用，"
                     "无 ts_code 的全市场区间查询会被截断）；amount 单位：千元。")
        if missing:
            notes.append("缺失指数：" + "、".join(f"{k}({v})" for k, v in missing.items()))
        return {
            "as_of_date": max(v["last_date"] for v in indices.values() if v["last_date"]),
            "requested_date": end_dash,
            "source": "tushare:index_daily",
            "lookbacks": [int(w) for w in lookbacks],
            "indices": indices,
            "missing": missing,
            "notes": notes,
        }

    # ---- 2) 市场宽度 + 高标情绪序列 ----

    def get_market_breadth_history(self, curr_date: str, days: int = 20):
        if not self.connected:
            logger.warning("Tushare 未连接，无法获取市场宽度序列。")
            return None
        curr = self._normalize_date(curr_date)
        try:
            curr_dash = datetime.strptime(curr, "%Y%m%d").strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            return None
        dates = self._recent_trade_dates(days, curr)
        if not dates:
            logger.warning("get_market_breadth_history: 交易日历不可用。")
            return None

        today = datetime.now().strftime("%Y%m%d")
        series, ladder_records, missing = [], [], {}
        truncated_days = []
        prev_up_codes: set = set()
        for d in dates:
            compact = d.replace("-", "")
            row = {"trade_date": d, "up": None, "down": None, "flat": None,
                   "total": None, "up_ratio": None, "limit_up": None,
                   "limit_down": None, "broken_board_rate": None, "max_board": None,
                   "promotion_rate": None, "premium_rate": None,
                   "market_amount": None}
            # 全市场单日日线（单日查询；≥6000 行视为代理端点静默截断，评审 M9）
            day_df = self._api_call(self.api.daily, trade_date=compact)
            truncated = self._truncated_rows(day_df)
            if truncated:
                day_df = None  # 不按残缺样本出宽度/溢价统计
                truncated_days.append(f"{d}({truncated} 行)")
                missing.setdefault(
                    "daily",
                    f"{d} 单日全市场日线 {truncated} 行 ≥ {self._TRUNCATION_ROWS}"
                    "：代理端点静默截断，该日宽度/溢价统计降级为缺失")
            if day_df is not None and not day_df.empty:
                pct = pd.to_numeric(day_df.get("pct_chg"), errors="coerce")
                up = int((pct > 0).sum())
                down = int((pct < 0).sum())
                flat = int((pct == 0).sum())
                total = int(pct.notna().sum())
                row.update({
                    "up": up, "down": down, "flat": flat, "total": total,
                    "up_ratio": round(up / total, 4) if total else None,
                })
                amount = pd.to_numeric(day_df.get("amount"), errors="coerce")
                row["market_amount"] = float(amount.sum()) if amount.notna().any() else None
                if prev_up_codes and "ts_code" in day_df.columns:
                    prev_df = day_df[day_df["ts_code"].isin(prev_up_codes)]
                    prev_pct = pd.to_numeric(prev_df.get("pct_chg"),
                                             errors="coerce").dropna()
                    row["premium_rate"] = round(float(prev_pct.mean()), 4) \
                        if len(prev_pct) else None
            else:
                missing.setdefault("daily", f"{d} 单日全市场日线无数据（宽度降级）")

            # 高标情绪（limit_list_d 单日；复用日缓存与归一化函数）
            if compact in self._LIMIT_LIST_D_DAILY_CACHE:
                ldf = self._LIMIT_LIST_D_DAILY_CACHE[compact]
            else:
                ldf = self._api_call(self.api.limit_list_d, trade_date=compact)
                if ldf is not None and not ldf.empty and compact != today:
                    self._LIMIT_LIST_D_DAILY_CACHE[compact] = ldf
                    _prune_daily_cache(self._LIMIT_LIST_D_DAILY_CACHE, cap=40)
            record = self._normalize_ladder_day(compact, ldf) \
                if (ldf is not None and not ldf.empty) else None
            if record:
                ladder_records.append(record)
                zt, zb = record["zt_total"], record["zb_total"]
                row["limit_up"] = zt
                row["limit_down"] = record["dt_total"]
                row["broken_board_rate"] = round(zb / (zt + zb), 4) if (zt + zb) else None
                row["max_board"] = max(record["stock_boards"].values()) \
                    if record["stock_boards"] else 0
                prev_up_codes = set(record["stock_boards"].keys())
            else:
                prev_up_codes = set()
                missing.setdefault("limit_list_d", f"{d} 无涨停梯队数据（高标情绪降级）")
            series.append(row)

        # 晋级率（跨日，复用共享纯函数：需连续交易日按序输入）
        if ladder_records:
            promo_map = {}
            for item in calc_promotion_rates(ladder_records):
                promo_map[item.get("date")] = self._promotion_rate_of(item)
            for row in series:
                compact = row["trade_date"].replace("-", "")
                if compact in promo_map:
                    row["promotion_rate"] = promo_map[compact]

        notes = [
            "宽度来自 daily 单日全市场（pct_chg）；高标情绪来自 limit_list_d 单日；",
            "昨日涨停溢价 = 昨日涨停股今日 pct_chg 均值（替代口径）；",
            "晋级率由连板梯队跨日精确匹配计算（共享纯函数）。",
        ]
        if truncated_days:
            notes.append(
                f"⚠️ 截断降级：{'、'.join(truncated_days)} 单日行数 ≥ "
                f"{self._TRUNCATION_ROWS}（代理端点静默截断），该日样本已丢弃，"
                "不按残缺样本出数（评审 M9）。")
        if missing:
            notes.append("缺失：" + "；".join(f"{k}({v})" for k, v in missing.items()))
        return {
            "as_of_date": series[-1]["trade_date"] if series else None,
            "days": len(series),
            "series": series,
            "missing": missing,
            "notes": notes,
        }

    @staticmethod
    def _promotion_rate_of(promo_row: dict):
        """晋级率行（"1→2": "3/8" 等）→ 合计晋级率（num/den）。"""
        num = den = 0
        for key, value in (promo_row or {}).items():
            if key == "date" or not isinstance(value, str) or "/" not in value:
                continue
            left, _, right = value.partition("/")
            try:
                num += int(left)
                den += int(right)
            except ValueError:
                continue
        return round(num / den, 4) if den else None

    # ---- 3) 主力/北向资金序列 ----

    def get_market_fund_flow_history(self, curr_date: str, days: int = 20):
        if not self.connected:
            logger.warning("Tushare 未连接，无法获取资金流序列。")
            return None
        curr = self._normalize_date(curr_date)
        dates = self._recent_trade_dates(days, curr)
        if not dates:
            return None
        series, missing = [], {}
        truncated_days = []
        for d in dates:
            compact = d.replace("-", "")
            row = {"trade_date": d, "main_net_amount": None, "northbound_net": None}
            df = self._api_call(self.api.moneyflow, trade_date=compact)
            truncated = self._truncated_rows(df)
            if truncated:
                df = None  # 不按残缺样本出主力净额（评审 M9）
                truncated_days.append(f"{d}({truncated} 行)")
                missing.setdefault(
                    "moneyflow",
                    f"{d} 单日全市场资金流 {truncated} 行 ≥ {self._TRUNCATION_ROWS}"
                    "：代理端点静默截断，该日主力净额统计降级为缺失")
            if df is not None and not df.empty:
                amount = pd.to_numeric(df.get("net_mf_amount"), errors="coerce")
                row["main_net_amount"] = float(amount.sum()) if amount.notna().any() else None
            else:
                missing.setdefault("moneyflow", f"{d} 无资金流数据")
            hdf = self._api_call(self.api.moneyflow_hsgt, trade_date=compact)
            if hdf is not None and not hdf.empty and "north_money" in hdf.columns:
                north = pd.to_numeric(hdf["north_money"], errors="coerce").dropna()
                if len(north):
                    # moneyflow_hsgt.north_money 单位百万元 → 统一为万元
                    row["northbound_net"] = float(north.sum()) * 100.0
            else:
                missing.setdefault(
                    "northbound", "moneyflow_hsgt 无数据（北向资金已停更或无权）")
            series.append(row)
        notes = ["main_net_amount 单位万元（全市场 net_mf_amount 合计）；"
                 "northbound_net 已统一为万元。"]
        if truncated_days:
            notes.append(
                f"⚠️ 截断降级：{'、'.join(truncated_days)} 单日行数 ≥ "
                f"{self._TRUNCATION_ROWS}（代理端点静默截断），该日样本已丢弃，"
                "不按残缺样本出数（评审 M9）。")
        if missing:
            notes.append("缺失：" + "；".join(f"{k}({v})" for k, v in missing.items()))
        return {
            "as_of_date": series[-1]["trade_date"] if series else None,
            "days": len(series),
            "series": series,
            "missing": missing,
            "notes": notes,
        }

    # ---- 4) 两融余额历史 ----

    def get_margin_trading_history(self, curr_date: str, days: int = 20):
        if not self.connected:
            logger.warning("Tushare 未连接，无法获取两融历史。")
            return None
        curr = self._normalize_date(curr_date)
        dates = self._recent_trade_dates(days, curr)
        if not dates:
            return None
        series, missing = [], {}
        for d in dates:
            compact = d.replace("-", "")
            df = self._api_call(self.api.margin, trade_date=compact)
            if df is None or df.empty:
                # 新交易日入库前 0 行（POC 结论）：跳过该日，不视为故障
                missing.setdefault("margin", f"{d} 无两融数据（未入库）")
                continue
            rzye = pd.to_numeric(df.get("rzye"), errors="coerce").sum()
            rqye = pd.to_numeric(df.get("rqye"), errors="coerce").sum()
            series.append({
                "trade_date": d,
                "rzye": float(rzye),
                "rqye": float(rqye),
                "rows": int(len(df)),
            })
        if not series:
            logger.warning("get_margin_trading_history: 全部交易日无两融数据。")
            return None
        notes = ["rzye/rqye 单位为元（交易所合计，POC：单日 3 行 = SSE/SZSE/BSE）。"]
        return {
            "as_of_date": series[-1]["trade_date"],
            "days": len(series),
            "series": series,
            "missing": missing,
            "notes": notes,
        }

    # ---- 5) 估值（指数分位序列 + 全 A 快照） ----

    def get_market_valuation(self, curr_date: str, years: int = 5):
        if not self.connected:
            logger.warning("Tushare 未连接，无法获取估值序列。")
            return None
        try:
            end = self._normalize_date(curr_date)
            end_dt = datetime.strptime(end, "%Y%m%d")
        except (ValueError, TypeError):
            return None
        start = (end_dt - timedelta(days=int(years) * 365 + 20)).strftime("%Y%m%d")
        end_dash = end_dt.strftime("%Y-%m-%d")

        index_valuation, missing = {}, {}
        for code in self._VALUATION_INDEX_CODES:
            df = self._api_call(self.api.index_dailybasic, ts_code=code,
                                start_date=start, end_date=end)
            if df is None or df.empty:
                missing[code] = "index_dailybasic 无数据"
                continue
            df = df.drop_duplicates(subset=["trade_date"]).sort_values("trade_date")
            dates = [self._norm_date_value(v) for v in df["trade_date"]]
            kept = [(d, i) for i, d in enumerate(dates) if d and d <= end_dash]
            if not kept:
                missing[code] = "窗口内无数据"
                continue
            idx = [i for _, i in kept]
            pe_all = self._series_to_list(df.get("pe_ttm"))
            pb_all = self._series_to_list(df.get("pb"))
            index_valuation[code] = {
                "trade_dates": [dates[i] for i in idx],
                "pe_ttm": [pe_all[i] if i < len(pe_all) else None for i in idx],
                "pb": [pb_all[i] if i < len(pb_all) else None for i in idx],
                "first_date": dates[idx[0]],
                "last_date": dates[idx[-1]],
            }

        # 全 A 快照：daily_basic 单日（无 ts_code 区间查询会被截断，POC 约束）
        all_a_snapshot = None
        for d in reversed(self._recent_trade_dates(4, end_dash)):
            df = self._api_call(self.api.daily_basic, trade_date=d.replace("-", ""),
                                fields="ts_code,trade_date,pe_ttm,pb")
            truncated = self._truncated_rows(df)
            if truncated:
                # 代理端点静默截断 → 中位数不可用（评审 M9）：该日跳过，截断事实
                # 写入 data_quality，继续尝试更早交易日
                missing["all_a_snapshot_truncated"] = (
                    f"{d} daily_basic 单日全 A {truncated} 行 ≥ {self._TRUNCATION_ROWS}"
                    "：代理端点静默截断，该日快照中位数不可用（不按残缺样本出数）")
                continue
            if df is None or df.empty:
                continue
            pe = pd.to_numeric(df.get("pe_ttm"), errors="coerce").dropna()
            if not len(pe):
                continue
            pb = pd.to_numeric(df.get("pb"), errors="coerce").dropna()
            all_a_snapshot = {
                "trade_date": d,
                "pe_ttm_median": round(float(pe.median()), 4),
                "pb_median": round(float(pb.median()), 4) if len(pb) else None,
                "rows": int(len(df)),
            }
            break
        if all_a_snapshot is None:
            missing["all_a_snapshot"] = "daily_basic 单日全 A 快照无数据"

        if not index_valuation and all_a_snapshot is None:
            logger.warning("get_market_valuation: 指数与全 A 估值均不可用。")
            return None
        as_of_candidates = [v["last_date"] for v in index_valuation.values()] + \
            ([all_a_snapshot["trade_date"]] if all_a_snapshot else [])
        return {
            "as_of_date": max(as_of_candidates),
            "requested_date": end_dash,
            "years": int(years),
            "index_valuation": index_valuation,
            "all_a_snapshot": all_a_snapshot,
            "missing": missing,
            "notes": [
                "index_dailybasic 按 ts_code 区间可用（POC：000300.SH 5 年 = 1211 行）；",
                "全 A 首期仅单日快照中位数（5 年逐日分位需按日单日拉取，成本高）。",
            ],
        }

    # ---- 6) 中国流动性指标 ----

    def get_cn_liquidity_indicators(self, curr_date: str, days: int = 20):
        if not self.connected:
            logger.warning("Tushare 未连接，无法获取流动性指标。")
            return None
        try:
            end = self._normalize_date(curr_date)
            end_dt = datetime.strptime(end, "%Y%m%d")
        except (ValueError, TypeError):
            return None
        end_dash = end_dt.strftime("%Y-%m-%d")
        start = (end_dt - timedelta(days=max(int(days), 20) * 2 + 40)).strftime("%Y%m%d")
        missing, notes = {}, []

        shibor = {}
        sdf = self._api_call(self.api.shibor, start_date=start, end_date=end)
        if sdf is not None and not sdf.empty:
            sdf = sdf.sort_values("date")
            dates = [self._norm_date_value(v) for v in sdf["date"]]
            kept = [i for i, d in enumerate(dates) if d and d <= end_dash]
            cols = {"on": "on", "1w": "1w", "1m": "1m", "3m": "3m", "1y": "1y"}
            shibor = {"trade_dates": [dates[i] for i in kept]}
            for out_key, col in cols.items():
                values = self._series_to_list(sdf.get(col))
                shibor[out_key] = [values[i] if i < len(values) else None for i in kept]
        else:
            missing["shibor"] = "shibor 区间无数据（须传 start_date/end_date）"

        lpr = {}
        ldf = self._api_call(self.api.shibor_lpr,
                             start_date=(end_dt - timedelta(days=5 * 365)).strftime("%Y%m%d"),
                             end_date=end)
        if ldf is not None and not ldf.empty:
            ldf = ldf.sort_values("date")
            dates = [self._norm_date_value(v) for v in ldf["date"]]
            kept = [i for i, d in enumerate(dates) if d and d <= end_dash]
            lpr = {"trade_dates": [dates[i] for i in kept]}
            for out_key, col in (("1y", "1y"), ("5y", "5y")):
                values = self._series_to_list(ldf.get(col))
                lpr[out_key] = [values[i] if i < len(values) else None for i in kept]
        else:
            missing["lpr"] = "shibor_lpr 无数据"

        money_supply = {}
        mdf = self._api_call(self.api.cn_m,
                             start_date=(end_dt - timedelta(days=800)).strftime("%Y%m%d"),
                             end_date=end)
        if mdf is not None and not mdf.empty:
            mdf = mdf.sort_values("month")
            money_supply = {
                "months": [str(v) for v in mdf["month"]],
                "m1_yoy": self._series_to_list(mdf.get("m1_yoy")),
                "m2_yoy": self._series_to_list(mdf.get("m2_yoy")),
                "m1_mom": self._series_to_list(mdf.get("m1_mom")),
                "m2_mom": self._series_to_list(mdf.get("m2_mom")),
            }
        else:
            missing["money_supply"] = "cn_m 无数据"

        shibor_end = shibor.get("trade_dates", [None])[-1] if shibor else None
        money_end = money_supply.get("months", [None])[-1] if money_supply else None
        notes.extend([
            "10Y 国债收益率不可用：yc_cb 无接口权限、yield_curve 接口名无效（POC）；",
            "DR007/MLF/OMO/净投放 无接口：以 Shibor 1W 替代短端利率（非 DR007）；",
            "LPR 为月度报价（POC：最新 20260720，约 7 周滞后）；M1/M2 为月度数据（POC：最新 202607）。",
        ])
        if missing:
            notes.append("缺失：" + "；".join(f"{k}({v})" for k, v in missing.items()))
        return {
            "as_of_date": shibor_end or money_end,
            "requested_date": end_dash,
            "shibor": shibor,
            "lpr": lpr,
            "money_supply": money_supply,
            "missing": missing,
            "notes": notes,
        }

    # ---- 7) 资金日历（IPO/解禁/交割/长假） ----

    @staticmethod
    def _third_friday(year: int, month: int):
        """每月第三个周五（股指期货交割日规则）。"""
        fridays = []
        for day in range(1, 32):
            try:
                d = datetime(year, month, day)
            except ValueError:
                break
            if d.weekday() == 4:
                fridays.append(d)
        return fridays[2].date() if len(fridays) >= 3 else None

    @staticmethod
    def _fourth_wednesday(year: int, month: int):
        """每月第四个周三（ETF 期权到期日规则）。"""
        wednesdays = []
        for day in range(1, 32):
            try:
                d = datetime(year, month, day)
            except ValueError:
                break
            if d.weekday() == 2:
                wednesdays.append(d)
        return wednesdays[3].date() if len(wednesdays) >= 4 else None

    def _rule_based_expiry_calendar(self, curr_date: str, months: int = 3) -> list:
        """规则日历：股指期货（第三个周五）+ ETF 期权（第四个周三）。

        未接交易所日历，法定节假日顺延未处理（notes 标注）。
        """
        base = datetime.strptime(curr_date, "%Y-%m-%d").date()
        out = []
        year, month = base.year, base.month
        for _ in range(months + 1):
            for value, kind in (
                (self._third_friday(year, month),
                 "股指期货交割日（每月第三个周五，规则推导）"),
                (self._fourth_wednesday(year, month),
                 "ETF 期权到期日（每月第四个周三，规则推导）"),
            ):
                if value is not None and value >= base:
                    out.append({"date": value.strftime("%Y-%m-%d"), "kind": kind})
            month += 1
            if month > 12:
                month = 1
                year += 1
        out.sort(key=lambda item: item["date"])
        return out

    def _holiday_windows(self, curr_date: str, end_date: str,
                         min_gap_days: int = 5) -> list:
        """由交易日历断档推导长假窗口（自然日间隔 ≥5 → 非纯周末休市）。"""
        dates = self._trade_dates_in_range(curr_date, end_date)
        windows = []
        for prev_text, next_text in zip(dates, dates[1:]):
            try:
                prev_dt = datetime.strptime(prev_text, "%Y-%m-%d")
                next_dt = datetime.strptime(next_text, "%Y-%m-%d")
            except ValueError:
                continue
            gap = (next_dt - prev_dt).days
            if gap >= min_gap_days:
                windows.append({
                    "start": (prev_dt + timedelta(days=1)).strftime("%Y-%m-%d"),
                    "end": (next_dt - timedelta(days=1)).strftime("%Y-%m-%d"),
                    "days": gap - 1,
                })
        return windows

    def get_cn_event_calendar(self, curr_date: str, windows=(5, 20, 60)):
        if not self.connected:
            logger.warning("Tushare 未连接，无法获取资金日历。")
            return None
        try:
            curr = self._normalize_date(curr_date)
            curr_dash = datetime.strptime(curr, "%Y%m%d").strftime("%Y-%m-%d")
            horizon = max(int(w) for w in windows)
        except (ValueError, TypeError):
            return None
        forward_end = (datetime.strptime(curr_dash, "%Y-%m-%d")
                       + timedelta(days=horizon * 2 + 45)).strftime("%Y%m%d")
        trade_dates = self._trade_dates_in_range(curr, forward_end)
        window_end = trade_dates[horizon] if len(trade_dates) > horizon else (
            trade_dates[-1] if trade_dates else curr_dash)
        fetch_end = (datetime.strptime(window_end, "%Y-%m-%d")
                     + timedelta(days=7)).strftime("%Y%m%d")

        missing, notes = {}, []
        ipo_items = []
        ndf = self._api_call(self.api.new_share, start_date=curr, end_date=fetch_end)
        if ndf is not None and not ndf.empty:
            for _, row in ndf.iterrows():
                subscribe = next((self._norm_date_value(row.get(k)) for k in
                                  ("ipo_date", "online_date", "subscribe_date")
                                  if self._norm_date_value(row.get(k))), None)
                list_date = next((self._norm_date_value(row.get(k)) for k in
                                  ("issue_date", "list_date", "up_date")
                                  if self._norm_date_value(row.get(k))), None)
                if not subscribe and not list_date:
                    continue
                market_amount = self._to_num(row.get("market_amount"))
                if market_amount is None:
                    market_amount = self._to_num(row.get("funds"))
                if market_amount is None:
                    price = self._to_num(row.get("price"))
                    shares = self._to_num(row.get("amount"))
                    if price is not None and shares is not None:
                        # price(元/股) × amount(万股) = 万元 → 亿元
                        market_amount = round(price * shares / 1e4, 4)
                ipo_items.append({
                    "ts_code": str(row.get("ts_code") or "").strip() or None,
                    "name": str(row.get("name") or "").strip() or None,
                    "subscribe_date": subscribe,
                    "list_date": list_date,
                    "price": self._to_num(row.get("price")),
                    "market_amount": market_amount,
                    "market": str(row.get("market") or "").strip() or None,
                })
        else:
            missing["ipo"] = "new_share 区间无数据"

        unlock_items = []
        sdf = self._api_call(self.api.share_float, start_date=curr, end_date=fetch_end)
        if sdf is not None and not sdf.empty:
            for _, row in sdf.iterrows():
                float_date = self._norm_date_value(row.get("float_date"))
                if not float_date:
                    continue
                unlock_items.append({
                    "ts_code": str(row.get("ts_code") or "").strip() or None,
                    "name": str(row.get("name") or "").strip() or None,
                    "float_date": float_date,
                    "float_share": self._to_num(row.get("float_share")),
                    "float_ratio": self._to_num(row.get("float_ratio")),
                    "share_type": str(row.get("share_type") or "").strip() or None,
                })
        else:
            missing["unlock"] = "share_float 区间无数据"

        notes.extend([
            "解禁市值需逐股价格（share_float 无价格列）→ 以数量/占流通比评估并降置信度；",
            "交割日为规则推导（第三个周五/第四个周三），未接交易所日历、未处理节假日顺延；",
            "财报/LPR/MLF/宏观发布日历无可用接口 → 不输出（macro_releases 为空）；",
        ])
        if missing:
            notes.append("缺失：" + "；".join(f"{k}({v})" for k, v in missing.items()))
        return {
            "as_of_date": curr_dash,
            "requested_date": curr_dash,
            "windows": [int(w) for w in windows],
            "window_end": window_end,
            "ipo": ipo_items,
            "unlocks": unlock_items,
            "expiry": self._rule_based_expiry_calendar(curr_dash, months=3),
            "holiday_windows": self._holiday_windows(curr_dash, fetch_end),
            "macro_releases": [],
            "missing": missing,
            "notes": notes,
        }

    # ---- 8) 全球风险价格 ----

    def get_global_risk_indicators(self, curr_date: str, days: int = 20):
        if not self.connected:
            logger.warning("Tushare 未连接，无法获取全球风险价格。")
            return None
        try:
            end = self._normalize_date(curr_date)
            end_dt = datetime.strptime(end, "%Y%m%d")
            need = max(int(days), 20) + 10
        except (ValueError, TypeError):
            return None
        end_dash = end_dt.strftime("%Y-%m-%d")
        start = (end_dt - timedelta(days=need * 2 + 60)).strftime("%Y%m%d")

        missing, notes = {}, []
        us_treasury, us_real_yield, us_long_rate = {}, {}, {}
        tycr = self._api_call(self.api.us_tycr, start_date=start, end_date=end)
        if tycr is not None and not tycr.empty:
            for code, field in (("y10", "y10"), ("y2", "y2")):
                series = self._macro_series(tycr, field, "us_tycr（美国国债收益率曲线）", "%")
                if series:
                    series = self._trim_series(series, need, end_dash)
                    us_treasury[code] = series
        else:
            missing["us_treasury"] = "us_tycr 无数据"
        trycr = self._api_call(self.api.us_trycr, start_date=start, end_date=end)
        if trycr is not None and not trycr.empty:
            series = self._macro_series(trycr, "y10", "us_trycr（美国国债实际收益率）", "%")
            if series:
                us_real_yield["y10"] = self._trim_series(series, need, end_dash)
        trltr = self._api_call(self.api.us_trltr, start_date=start, end_date=end)
        if trltr is not None and not trltr.empty:
            series = self._macro_series(trltr, "ltr_avg", "us_trltr（美国国债长期利率）", "%")
            if series:
                us_long_rate["ltr"] = self._trim_series(series, need, end_dash)

        global_indices = {}
        for code in self._GLOBAL_INDEX_CODES:
            df = self._paged_range_fetch(self.api.index_global, code, start, end)
            if df is None or df.empty:
                missing[f"index_global:{code}"] = "无数据"
                continue
            entry = self._module_frame_entry(df, code, "index_global", end_dash, need)
            if entry:
                global_indices[code] = entry
            else:
                missing[f"index_global:{code}"] = "字段缺失"

        fx = {}
        for code in ("USDCNH.FXCM", "XAUUSD.FXCM"):
            df = self._paged_range_fetch(self.api.fx_daily, code, start, end)
            if df is None or df.empty:
                missing[f"fx_daily:{code}"] = "无数据"
                continue
            entry = self._module_frame_entry(df, code, "fx_daily", end_dash, need,
                                            field="bid_close")
            if entry:
                fx[code] = entry
            else:
                missing[f"fx_daily:{code}"] = "字段缺失"
        if "USDCNH.FXCM" not in fx:
            missing["usdcnh"] = "USDCNH.FXCM 不可用（美元指数代码 DXY/USDX 均无数据）"

        commodities = {}
        for code in self._COMMODITY_CODES:
            df = self._paged_range_fetch(self.api.fut_daily, code, start, end)
            if df is None or df.empty:
                missing[f"fut_daily:{code}"] = "无数据"
                continue
            entry = self._module_frame_entry(df, code, "fut_daily", end_dash, need)
            if entry:
                commodities[code] = entry
            else:
                missing[f"fut_daily:{code}"] = "字段缺失"

        if not (us_treasury or global_indices or fx or commodities):
            logger.warning("get_global_risk_indicators: 全部风险价格不可用。")
            return None
        notes.extend([
            "无 VIX/SOX/美元指数代码（POC 实测 0 行）：波动率以美股指数已实现波动率替代"
            "（特征层计算），美元以 USDCNH 替代；",
            "商品为国内期货价（SC.INE/AU.SHF/CU.SHF），非 WTI/伦金；",
            "美债收益率（us_tycr）为日频，实际收益率/长期利率为辅助分组。",
        ])
        if missing:
            notes.append("缺失：" + "；".join(f"{k}({v})" for k, v in missing.items()))
        return {
            "as_of_date": end_dash,
            "requested_date": end_dash,
            "days": need,
            "us_treasury": us_treasury,
            "us_real_yield": us_real_yield,
            "us_long_rate": us_long_rate,
            "global_indices": global_indices,
            "fx": fx,
            "commodities": commodities,
            "missing": missing,
            "notes": notes,
        }

    @staticmethod
    def _trim_series(series: dict, need: int, end_dash: str) -> dict:
        """序列截断到 `end_dash` 及最近 `need` 个点（升序）。"""
        if not series:
            return series
        field = series.get("field")
        dates = series.get("trade_dates") or []
        values = series.get(field) or [] if field else []
        pairs = [(d, v) for d, v in zip(dates, values) if d and d <= end_dash]
        pairs = pairs[-need:]
        out = dict(series)
        out["trade_dates"] = [d for d, _ in pairs]
        if field:
            out[field] = [v for _, v in pairs]
        return out

    def _module_frame_entry(self, df, code: str, source: str, end_dash: str,
                            need: int, field: str = "close"):
        """指数/汇率/商品的 DataFrame → 结构化序列条目（截断到 end_dash）。"""
        if df is None or df.empty:
            return None
        date_col = "trade_date" if "trade_date" in df.columns else (
            "date" if "date" in df.columns else None)
        if date_col is None or field not in df.columns:
            return None
        work = df.dropna(subset=[date_col]).sort_values(date_col)
        pairs = []
        for raw_date, raw_value in zip(work[date_col], work[field]):
            date_text = self._norm_date_value(raw_date)
            value = self._to_num(raw_value)
            if date_text and date_text <= end_dash:
                pairs.append((date_text, value))
        pairs = pairs[-need:]
        if not pairs:
            return None
        return {
            "trade_dates": [d for d, _ in pairs],
            "field": field,
            field: [v for _, v in pairs],
            "source": f"{source}:{code}",
            "unit": "点" if source in ("index_global", "fut_daily") else "",
            "note": "国内价、非 WTI/伦金" if source == "fut_daily" else None,
        }
