"""
YoHo Tushare 数据提供器
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
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

import pandas as pd

from .base_provider import BaseStockDataProvider

logger = logging.getLogger(__name__)

# Tushare API 单次调用超时上限（秒），可通过环境变量 TUSHARE_TIMEOUT 覆盖
_TUSHARE_TIMEOUT = int(os.getenv("TUSHARE_TIMEOUT", "30"))

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
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        future = pool.submit(fn, *args, **kwargs)
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
                df = df.tail(days)
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

    def get_sector_fund_flow_rank(self, days: int = 5) -> str:
        """行业资金流向排名（Tushare moneyflow 聚合）"""
        if not self.connected:
            return "Tushare 未连接。"
        try:
            end = datetime.now().strftime("%Y%m%d")
            start = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")
            df = self._api_call(self.api.moneyflow_hsgt, start_date=start, end_date=end)
            if df is not None and not df.empty:
                df = df.tail(days)
                return f"# 沪深港通资金流向\n{df.to_string(index=False)}"
            return "暂无行业资金流向数据。"
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
                df = df.tail(days)
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
        """获取全行业板块涨跌排名（通过申万行业指数）"""
        if not self.connected:
            return "Tushare 未连接。"
        try:
            # 获取申万一级行业
            idx_df = self._api_call(self.api.index_classify, src='SW2021', level='L1')
            if idx_df is None or idx_df.empty:
                return "未获取到申万行业分类。"

            end = datetime.now().strftime("%Y%m%d")
            start = (datetime.now() - timedelta(days=days * 3)).strftime("%Y%m%d")

            performance_list = []
            for _, row in idx_df.iterrows():
                code = row['index_code']
                name = row['industry_name']
                try:
                    df = self._api_call(self.api.sw_daily, ts_code=code, start_date=start, end_date=end)
                    if df is not None and not df.empty and len(df) >= 2:
                        df = df.tail(days + 1)
                        first_close = float(df['close'].iloc[0])
                        last_close = float(df['close'].iloc[-1])
                        if first_close > 0:
                            pct = (last_close - first_close) / first_close * 100
                            performance_list.append((name, pct, last_close, len(df)))
                except Exception:
                    continue

            if not performance_list:
                return "未获取到行业板块表现数据。"

            performance_list.sort(key=lambda x: x[1], reverse=True)
            lines = [f"# 全行业板块涨跌排名（近 {days} 日）\n"]
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
            return "\n".join(lines)
        except Exception as e:
            logger.warning(f"获取行业板块排名失败: {e}")
            return f"获取行业板块排名失败: {e}"

    def get_concept_board_heat_rank(self, days: int = 10) -> str:
        """获取热门概念板块热度排名（通过东方财富 DC 概念板块）

        优化策略：
        1. dc_index 一次拉取全量板块，自带 pct_change，按涨幅降序取 TOP 30
        2. 仅对 TOP 30 板块调 dc_daily 获取日线，计算量变+热度
        3. API 调用：1 + 30 = 31 次（原方案 1 + 777 = 778 次）
        """
        if not self.connected:
            return "Tushare 未连接。"
        t_start = time.perf_counter()
        try:
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
                return "未获取到概念板块列表。"

            total = len(idx_df)
            logger.info("get_concept_board_heat_rank: dc_index 耗时 %.1fs, 共 %d 个概念板块, 字段: %s",
                        t_dc_index, total, list(idx_df.columns))

            # ===== Step 2: 按 pct_change 降序取 TOP 30 =====
            if "pct_change" in idx_df.columns:
                idx_df = idx_df.sort_values("pct_change", ascending=False)
            top30 = idx_df.head(30)
            logger.info("get_concept_board_heat_rank: TOP30 pct_change 范围: %.2f%% ~ %.2f%%",
                        float(top30["pct_change"].iloc[0]) if len(top30) > 0 else 0,
                        float(top30["pct_change"].iloc[-1]) if len(top30) > 0 else 0)

            end = datetime.now().strftime("%Y%m%d")
            start = (datetime.now() - timedelta(days=days * 3)).strftime("%Y%m%d")

            # ===== Step 3: 仅对 TOP 30 调 dc_daily，计算热度 =====
            heat_list = []
            success_count = 0
            fail_count = 0
            first_daily_logged = False

            for _, row in top30.iterrows():
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
            logger.info("get_concept_board_heat_rank: 完成, 总耗时 %.1fs, dc_index=%.1fs, "
                        "预筛选=%d/%d, dc_daily成功=%d, 失败=%d",
                        t_total, t_dc_index, len(top30), total, success_count, fail_count)

            if not heat_list:
                return "未获取到概念板块热度数据。"

            heat_list.sort(key=lambda x: x[3], reverse=True)
            lines = [f"# 概念板块热度排名（近 {days} 日，数据源：东方财富 DC）\n"]
            lines.append(f"从 {total} 个概念板块中按涨幅预筛选 TOP {len(top30)}，日线覆盖 {success_count} 个\n")
            lines.append("| 排名 | 概念板块 | 涨跌幅(%) | 成交额变化(%) | 综合热度 |")
            lines.append("|------|----------|-----------|---------------|----------|")
            for j, (name, pct, vol_chg, heat) in enumerate(heat_list[:30], 1):
                lines.append(f"| {j} | {name} | {pct:+.2f}% | {vol_chg:+.1f}% | {heat:+.1f} |")

            lines.append("\n## 热度 TOP10")
            for j, (name, pct, vol_chg, heat) in enumerate(heat_list[:10], 1):
                lines.append(f"  {j}. {name}: 涨幅 {pct:+.2f}%, 量变 {vol_chg:+.1f}%, 热度 {heat:+.1f}")
            return "\n".join(lines)
        except Exception as e:
            t_total = time.perf_counter() - t_start
            logger.warning(f"获取概念板块热度失败 (耗时 %.1fs): {e}", t_total)
            return f"获取概念板块热度失败: {e}"

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
                        df = df.rename(columns={'trade_date': 'date'})
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
                    df = df.tail(days)
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

    # ==================== 事件研究系统 — 结构化接口 ====================

    def get_index_data_df(self, index_code: str, start_date: str, end_date: str):
        """获取指数日线结构化行情（DataFrame，完整区间，内部分页）。

        与展示用 get_index_data 不同：不截断（limit 5），按时间窗口分页
        循环拉取，保证事件研究所需的完整估计窗口 + 事件窗口数据。
        返回标准列：trade_date / open / high / low / close / vol / amount。
        获取失败返回 None。
        """
        if not self.connected:
            logger.warning("Tushare 未连接，无法获取结构化指数行情。")
            return None
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
                    frames.append(df)
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

        # 标准化列：trade_date(YYYY-MM-DD str，与 AKShare 输出一致) / open / high / low / close / vol / amount
        std = pd.DataFrame({
            "trade_date": pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d"),
            "open": pd.to_numeric(df.get("open"), errors="coerce"),
            "high": pd.to_numeric(df.get("high"), errors="coerce"),
            "low": pd.to_numeric(df.get("low"), errors="coerce"),
            "close": pd.to_numeric(df.get("close"), errors="coerce"),
            "vol": pd.to_numeric(df.get("vol"), errors="coerce"),
            "amount": pd.to_numeric(df.get("amount"), errors="coerce"),
        })
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
