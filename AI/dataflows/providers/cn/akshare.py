"""
LiveProfit AKShare 数据提供器 (简化版)
免费开源数据源，支持 A 股、港股、全球指数。
无需 API Token，基于网络爬虫。
"""

import logging
import re
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

import pandas as pd

from ..base_provider import BaseStockDataProvider
from . import daily_matrix_utils

logger = logging.getLogger(__name__)

# 东方财富 API 偶发连接中断，单次调用超时上限（秒）
_AKSHARE_TIMEOUT = 15

try:
    import akshare as ak
    AKSHARE_AVAILABLE = True
except ImportError:
    AKSHARE_AVAILABLE = False
    ak = None


def _call_with_timeout(fn, timeout=_AKSHARE_TIMEOUT, *args, **kwargs):
    """在线程池中执行 fn，超时则抛 TimeoutError。

    注意：不能使用 with ThreadPoolExecutor，因为 __exit__ 会调用
    shutdown(wait=True)，导致超时后主线程仍被 hang 住的工作线程阻塞。
    """
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        future = pool.submit(fn, *args, **kwargs)
        return future.result(timeout=timeout)
    finally:
        pool.shutdown(wait=False)


def _try_call(fn, *args, timeout=_AKSHARE_TIMEOUT, **kwargs):
    """尝试调用 AKShare 函数，超时或异常返回 None"""
    try:
        return _call_with_timeout(fn, timeout, *args, **kwargs)
    except Exception:
        return None


def _is_intraday_trading_time(now=None) -> bool:
    """A 股盘中交易时段判定（周一至周五 9:30-15:00），用于逐日表盘中标注
    （与 cn/tushare.py 的 _is_intraday_trading_time 同口径）。"""
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False
    return 930 <= now.hour * 100 + now.minute <= 1500


def _extract_date_from_row(row, df=None) -> str:
    """从 DataFrame 行中尝试提取日期字段。

    尝试常见日期列名：日期、date、trade_date、时间、report_date 等。
    也尝试从行索引中提取。
    """
    date_columns = ["日期", "date", "trade_date", "时间", "report_date", "通知日期", "变动日期"]

    # 先从列名匹配
    if hasattr(row, 'get'):
        for col in date_columns:
            val = row.get(col)
            if val is not None:
                s = str(val).strip()
                if len(s) >= 8:
                    return _normalize_date_str(s)

    # 尝试行名（index）
    row_name = str(row.name) if hasattr(row, 'name') and row.name is not None else ""
    if len(row_name) >= 8:
        normalized = _normalize_date_str(row_name)
        if normalized:
            return normalized

    # 从 DataFrame 列中模糊搜索
    if df is not None:
        for col in df.columns:
            col_lower = str(col).lower()
            if any(kw in col_lower for kw in ["date", "日期", "time", "时间"]):
                val = row.get(col) if hasattr(row, 'get') else row[col]
                if val is not None:
                    s = str(val).strip()
                    if len(s) >= 8:
                        return _normalize_date_str(s)

    return ""


def _normalize_date_str(s: str) -> str:
    """将日期字符串标准化为 YYYY-MM-DD 格式。"""
    s = s.strip()
    # 已经是 YYYY-MM-DD
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        return s
    # YYYYMMDD
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    # YYYY/MM/DD
    if len(s) == 10 and s[4] == "/" and s[7] == "/":
        return s.replace("/", "-")
    # 带时间戳的
    if len(s) >= 10:
        clean = s[:10].replace("/", "-")
        if clean[4] == "-" and clean[7] == "-":
            return clean
    return ""


def _format_date_note(data_date: str, requested_date: str = "") -> str:
    """生成统一的日期标注行。

    Args:
        data_date: 实际数据日期 (YYYY-MM-DD)
        requested_date: 请求日期 (YYYY-MM-DD)，可选

    Returns:
        标注文本，如 `> ⚠️ 数据日期: 2026-08-07（请求日期为 2026-08-08 时请留意）`
    """
    if not data_date:
        return ""
    if requested_date and data_date != requested_date:
        return (
            f"> ⚠️ 数据日期: {data_date}"
            f"（请求日期为 {requested_date}，数据可能滞后一个交易日）"
        )
    return f"> 数据日期: {data_date}"


class AKShareProvider(BaseStockDataProvider):
    """AKShare 数据提供器"""

    def __init__(self):
        super().__init__("AKShare")
        self.connected = False
        if AKSHARE_AVAILABLE:
            self._configure_requests()
            self.connected = True
            logger.info("AKShare 初始化成功")
        else:
            logger.error("AKShare 未安装，请运行: pip install akshare")

    def _configure_requests(self):
        """配置请求头，绕过反爬虫"""
        try:
            import requests
            _original_get = requests.get

            # Monkey-patch requests.get，自动注入 User-Agent
            def patched_get(url, **kwargs):
                if 'headers' not in kwargs or kwargs['headers'] is None:
                    kwargs['headers'] = {}
                kwargs['headers'].setdefault(
                    'User-Agent',
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                )
                return _original_get(url, **kwargs)

            requests.get = patched_get
        except Exception:
            pass

    def _normalize_code(self, code: str) -> str:
        """标准化 A 股代码为纯6位数字"""
        return code.strip().replace(".SZ", "").replace(".SH", "").upper()

    # ==================== 股票行情 ====================

    def get_stock_data(self, code: str, start_date: str, end_date: str) -> str:
        """获取 A 股日线行情"""
        if not self.connected:
            return "AKShare 未安装。"

        code = self._normalize_code(code)
        try:
            # 使用东方财富历史数据接口（前复权）
            df = ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""),
                adjust="qfq",
            )
            if df is not None and not df.empty:
                cols = ["日期", "开盘", "最高", "最低", "收盘", "成交量", "成交额", "涨跌幅"]
                available = [c for c in cols if c in df.columns]
                return df[available].to_string(index=False)
            return f"未获取到 {code} 的行情数据。"
        except Exception as e:
            logger.error(f"AKShare 行情获取失败 [{code}]: {e}")
            return f"获取行情数据失败: {e}"

    def get_stock_info(self, code: str) -> dict:
        """获取股票基本信息"""
        if not self.connected:
            return {"name": f"股票{code}"}
        try:
            code = self._normalize_code(code)
            # 使用东方财富个股信息接口
            df = ak.stock_individual_info_em(symbol=code)
            if df is not None and not df.empty:
                info = {}
                for _, row in df.iterrows():
                    info[row.iloc[0]] = row.iloc[1] if len(row) > 1 else ""
                return {
                    "name": info.get("股票简称", f"股票{code}"),
                    "industry": info.get("行业", ""),
                    "area": "",
                    "list_date": info.get("上市时间", ""),
                }
        except Exception:
            pass
        return {"name": f"股票{code}"}

    # ==================== 基本面 ====================

    def get_fundamentals(self, code: str, curr_date: str = None) -> str:
        """获取财务数据"""
        if not self.connected:
            return "AKShare 未安装。"
        code = self._normalize_code(code)
        parts = []

        # 财务指标（同花顺接口）
        try:
            fina = ak.stock_financial_abstract_ths(symbol=code)
            if fina is not None and not fina.empty:
                parts.append("--- 财务指标 (同花顺) ---")
                parts.append(fina.head(20).to_string(index=False))
        except Exception as e:
            parts.append(f"财务指标获取失败: {e}")

        # 利润表（东方财富接口）
        try:
            profit = ak.stock_profit_sheet_by_report_em(symbol=code)
            if profit is not None and not profit.empty:
                parts.append("--- 利润表 ---")
                parts.append(profit.head(5).to_string(index=False))
        except Exception:
            pass

        # 资产负债表（东方财富接口）
        try:
            balance = ak.stock_balance_sheet_by_report_em(symbol=code)
            if balance is not None and not balance.empty:
                parts.append("--- 资产负债表 ---")
                parts.append(balance.head(5).to_string(index=False))
        except Exception:
            pass

        return "\n\n".join(parts) if parts else f"未获取到 {code} 的财务数据。"

    # ==================== 新闻 ====================

    def get_news(self, code: str, start_date: str, end_date: str) -> str:
        """获取股票新闻"""
        if not self.connected:
            return "AKShare 未安装。"
        code = self._normalize_code(code)
        try:
            # 使用东方财富新闻接口
            df = ak.stock_news_em(symbol=code)
            if df is not None and not df.empty:
                news_lines = []
                for _, row in df.head(20).iterrows():
                    title = row.get("标题", row.get("title", ""))
                    pub_time = row.get("发布时间", row.get("pub_time", ""))
                    news_lines.append(f"[{pub_time}] {title}")
                return "\n".join(news_lines)
        except Exception as e:
            logger.warning(f"AKShare 新闻获取失败 [{code}]: {e}")
        return f"未获取到 {code} 的新闻数据。"

    # ==================== 指数数据 ====================

    def get_index_data(self, index_codes: str, start_date: str, end_date: str) -> str:
        """获取 A 股指数行情"""
        if not self.connected:
            return "AKShare 未安装。"
        codes = [c.strip() for c in index_codes.split(",")]
        parts = []
        for code in codes:
            try:
                # 根据代码前缀选择交易所
                df = ak.stock_zh_index_daily(symbol=f"sh{code}" if code.startswith("000")
                      else f"sz{code}")
                if df is not None and not df.empty:
                    df = df.tail(5)
                    parts.append(
                        f"--- 指数 {code} ---\n"
                        f"{df.to_string(index=False)}"
                    )
            except Exception as e:
                logger.warning(f"AKShare 指数获取失败 [{code}]: {e}")
        return "\n\n".join(parts) if parts else "未获取到指数数据。"

    # ==================== 全球科技指数 (新增) ====================

    # 关键全球科技指数代码映射 (AKShare 格式)
    GLOBAL_TECH_INDICES = {
        # 美股宽基指数
        "SPX": ("标普500", "spx"),
        "DJI": ("道琼斯工业指数", "dji"),
        # 美股科技指数
        "NASDAQ": ("纳斯达克综合指数", "nasdaq"),
        "NDX": ("纳斯达克100", "nasdaq_100"),
        "SOX": ("费城半导体指数", "sox"),
        "DJUSSC": ("道琼斯美国半导体指数", "djussc"),
        # 韩国科技指数
        "KOSPI": ("韩国综合指数", "kospi"),
        "KOSDAQ": ("韩国科斯达克指数", "kosdaq"),
        "KRX_SEMI": ("韩国半导体指数", "krx_semi"),
        # A股科技指数
        "STAR50": ("科创50", "star50"),
        "CHINEXT": ("创业板指", "chinext"),
        "CSI_SEMI": ("中华半导体芯片指数", "csi_semi"),
        "CSI_AI": ("中证人工智能指数", "csi_ai"),
    }

    def get_global_index(self, index_key: str, days: int = 10) -> str:
        """
        获取全球指数最近 N 天数据

        Args:
            index_key: 指数键名（见 GLOBAL_TECH_INDICES）
            days: 获取天数

        Returns:
            格式化的指数数据文本
        """
        if not self.connected:
            return "AKShare 未安装。"

        if index_key not in self.GLOBAL_TECH_INDICES:
            return f"未知指数: {index_key}，可选: {list(self.GLOBAL_TECH_INDICES.keys())}"

        name, ak_key = self.GLOBAL_TECH_INDICES[index_key]
        # 多拉一些数据以应对非交易日，最终只返回最近 days 条
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")

        try:
            df = self._fetch_global_index(ak_key, start_date, end_date)
            if df is not None and not df.empty:
                df = df.tail(days)
                return (
                    f"## {name} ({index_key})\n"
                    f"最近 {len(df)} 个交易日数据:\n"
                    f"{df.to_string(index=False)}"
                )
            return f"未获取到 {name} 数据。"
        except Exception as e:
            logger.warning(f"获取全球指数失败 [{index_key}]: {e}")
            return f"获取 {name} 数据失败: {e}"

    def _fetch_global_index(self, ak_key: str, start: str, end: str):
        """根据键名调用不同的 akshare 函数获取全球指数（多接口回退）

        注意：index_us_stock_sina / index_global_hist_em 不接受 start_date/end_date，
        调用后由 get_global_index 通过 tail() 截取所需天数。
        """
        # 美股指数 — 优先使用新浪财经接口（仅接受 symbol）
        if ak_key in ("nasdaq", "nasdaq_100"):
            try:
                return ak.index_us_stock_sina(
                    symbol=f".{ak_key.upper() if ak_key != 'nasdaq' else 'IXIC'}"
                )
            except Exception:
                pass
            # 回退：使用东方财富美股历史接口
            try:
                code_map = {"nasdaq": "NDX", "IXIC": "IXIC"}
                return ak.stock_us_hist(symbol=code_map.get(ak_key, "NDX"),
                                        period="daily",
                                        start_date=start, end_date=end)
            except Exception:
                pass

        if ak_key == "spx":
            return ak.index_us_stock_sina(symbol=".INX")

        if ak_key == "dji":
            return ak.index_us_stock_sina(symbol=".DJI")

        if ak_key == "sox":
            return ak.index_us_stock_sina(symbol=".SOX")

        if ak_key == "djussc":
            return ak.index_us_stock_sina(symbol=".DJUSSC")

        # 韩国指数
        if ak_key == "kospi":
            return ak.stock_zh_index_daily_em(symbol="KS11")
        if ak_key == "kosdaq":
            return ak.stock_zh_index_daily_em(symbol="KQ11")

        # A股科技指数（东方财富接口，偶发连接中断，外层有 try/except 兜底）
        if ak_key == "star50":
            return ak.stock_zh_index_daily_em(symbol="sh000688")
        if ak_key == "chinext":
            return ak.stock_zh_index_daily_em(symbol="sz399006")
        if ak_key == "csi_semi":
            return ak.stock_zh_index_daily_em(symbol="sh990001")
        if ak_key == "csi_ai":
            return ak.stock_zh_index_daily_em(symbol="sh931071")

        # 通用回退：东方财富全球指数历史（仅接受 symbol）
        return ak.index_global_hist_em(symbol=ak_key)

    def get_all_tech_indices(self, days: int = 10) -> str:
        """获取所有科技指数数据"""
        results = []
        for key in self.GLOBAL_TECH_INDICES:
            data = self.get_global_index(key, days)
            results.append(data)
            results.append("")
        return "\n".join(results)


    # ==================== AI 产业链概念板块 ====================
    
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
    
    # A股概念板块代码映射
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

    # ==================== 市场层新增方法 ====================

    def get_global_macro_news(self, curr_date: str) -> str:
        """获取财联社电报（全球宏观快讯）"""
        if not AKSHARE_AVAILABLE:
            return "AKShare 未安装，无法获取宏观新闻。"
        try:
            df = ak.stock_info_global_cls()
            if df is None or df.empty:
                return "暂无财联社电报数据。"
            recent = df.head(50)
            lines = ["# 全球宏观财经快讯（财联社电报）\n"]
            for _, row in recent.iterrows():
                # 兼容不同版本的列名
                title = (row.get("标题") or row.get("title")
                      or row.get("content") or str(row.iloc[0]) if len(row) > 0 else "")
                ctime = (row.get("发布时间") or row.get("发布日期")
                      or row.get("ctime") or row.get("datetime") or "")
                lines.append(f"- [{ctime}] {title}")
            return "\n".join(lines)
        except Exception as e:
            logger.warning(f"获取财联社电报失败: {e}")
            return f"获取宏观新闻失败: {e}"

    def get_central_bank_calendar(self, curr_date: str) -> str:
        """获取主要央行利率决议日历"""
        import json
        import os
        cal_path = os.path.join(os.path.dirname(__file__), "..", "data", "central_bank_calendar.json")
        lines = ["# 主要央行利率决议日历\n"]
        if os.path.exists(cal_path):
            try:
                with open(cal_path, "r", encoding="utf-8") as f:
                    calendar = json.load(f)
                for entry in calendar:
                    lines.append(f"- {entry.get('date', 'N/A')} | {entry.get('bank', 'N/A')} | "
                                 f"{entry.get('event', 'N/A')} | 决议: {entry.get('decision', 'N/A')}")
            except Exception as e:
                lines.append(f"日历加载失败: {e}")
        else:
            lines.append("央行日历文件不存在。")
            lines.append("参考: 美联储 2026 年剩余 FOMC 会议: 9/16-17, 11/4-5, 12/16-17")
            lines.append("参考: 中国人民银行 LPR 报价日为每月 20 日（遇节假日顺延）")
        return "\n".join(lines)

    def get_macro_indicators(self, curr_date: str) -> str:
        """获取关键宏观经济指标"""
        if not AKSHARE_AVAILABLE:
            return "AKShare 未安装。"
        lines = ["# 关键宏观经济指标\n"]
        # 中国 PMI（月份倒序，最新在最前）
        try:
            pmi = ak.macro_china_pmi()
            if pmi is not None and not pmi.empty:
                latest = pmi.iloc[0]
                lines.append(f"## 中国制造业 PMI")
                lines.append(f"- 最新值: {latest.get('制造业-指数', 'N/A')} ({latest.get('月份', 'N/A')})")
        except Exception as e:
            lines.append(f"- 中国 PMI: 获取失败 ({e})")
        # 中国社融
        try:
            sf = ak.macro_china_shrzgm()
            if sf is not None and not sf.empty:
                latest = sf.iloc[-1]
                lines.append(f"## 中国社融规模")
                lines.append(f"- 最新值: {latest.to_dict()}")
        except Exception:
            lines.append("- 中国社融: 获取失败")
        # 美国 CPI（akshare 1.18+ 拆分为 yoy/monthly，无 macro_usa_cpi）
        # 末行常为待发布（现值 NaN），取最近一条有现值的数据
        try:
            cpi = ak.macro_usa_cpi_yoy()
            if cpi is not None and not cpi.empty:
                valid = cpi.dropna(subset=["现值"]) if "现值" in cpi.columns else cpi
                latest = valid.iloc[-1] if not valid.empty else cpi.iloc[-1]
                lines.append(f"## 美国 CPI")
                lines.append(f"- 最新值: {latest.to_dict()}")
        except Exception:
            lines.append("- 美国 CPI: 获取失败")
        # 美国非农（末行常为待发布（今值 NaN），取最近一条有今值的数据）
        try:
            nf = ak.macro_usa_non_farm()
            if nf is not None and not nf.empty:
                valid = nf.dropna(subset=["今值"]) if "今值" in nf.columns else nf
                latest = valid.iloc[-1] if not valid.empty else nf.iloc[-1]
                lines.append(f"## 美国非农就业")
                lines.append(f"- 最新值: {latest.to_dict()}")
        except Exception:
            lines.append("- 美国非农: 获取失败")
        return "\n".join(lines)

    def get_commodity_fx_overview(self, days: int = 10) -> str:
        """获取大宗商品+汇率概览"""
        if not AKSHARE_AVAILABLE:
            return "AKShare 未安装。"
        lines = ["# 大宗商品与汇率概览\n"]
        # 原油（多符号回退：sina 外盘期货失败时回退东财全球期货）
        for sym, label in [("CL00Y", "WTI 原油"), ("B00Y", "布伦特原油")]:
            try:
                crude = None
                try:
                    crude = ak.futures_foreign_hist(symbol=sym)
                except Exception:
                    crude = ak.futures_global_hist_em(symbol=sym)
                if crude is not None and not crude.empty:
                    latest = crude.iloc[-1]
                    lines.append(f"## {label}期货")
                    close_val = (latest.get('收盘价', None) or latest.get('close', None)
                              or latest.get('收盘', None)
                              or (latest.iloc[-1] if len(latest) > 0 else "N/A"))
                    lines.append(f"- 最新价: {close_val}")
            except Exception as e:
                lines.append(f"- {label}: 获取失败 ({e})")
        # 黄金
        try:
            gold = ak.spot_hist_sge(symbol="Au99.99")
            if gold is not None and not gold.empty:
                latest = gold.iloc[-1]
                lines.append(f"## 黄金 (Au99.99)")
                lines.append(f"- 最新价: {latest.get('close', latest.iloc[-1])}")
        except Exception:
            lines.append("- 黄金: 获取失败")
        # 美元汇率（akshare 1.18+ 移除 index_investing_global，改用中行牌价 USD/CNY）
        try:
            from datetime import datetime, timedelta
            end = datetime.now().strftime("%Y%m%d")
            start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
            dxy = ak.currency_boc_sina(symbol="美元", start_date=start, end_date=end)
            if dxy is not None and not dxy.empty:
                latest = dxy.iloc[-1]
                lines.append(f"## 美元汇率（USD/CNY 中行牌价）")
                lines.append(f"- 最新价: {latest.get('中行汇买价', latest.iloc[-1])}")
        except Exception:
            lines.append("- 美元汇率: 获取失败")
        return "\n".join(lines)

    def get_us_economic_calendar(self, curr_date: str) -> str:
        """获取美国经济数据发布日历

        优先使用 news_economic_baidu，403 时回退到静态参考信息。
        """
        if not AKSHARE_AVAILABLE:
            return "AKShare 未安装。"

        # 静态参考：美国关键经济数据发布日期规律
        fallback = (
            "# 美国经济数据发布日历（静态参考）\n"
            "- 非农就业: 每月第一个周五\n"
            "- CPI: 每月中旬（10-15日）\n"
            "- PPI: 每月中旬（CPI 前后1-2天）\n"
            "- GDP (初值): 每季度末月 25-30 日\n"
            "- FOMC 利率决议: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm\n"
            "- 零售销售: 每月中旬\n"
            "- 密歇根消费者信心: 每月第二个周五\n"
        )

        try:
            df = ak.news_economic_baidu(date=curr_date.replace("-", ""))
            if df is None or df.empty:
                return "暂无美国经济日历数据。\n\n" + fallback
            # 按地区过滤美国
            if "地区" in df.columns:
                us_data = df[df["地区"].str.contains("美国", na=False)]
            else:
                us_data = df.head(20)
            if us_data.empty:
                return "暂无美国经济数据发布。\n\n" + fallback
            lines = ["# 美国经济数据发布日历\n"]
            for _, row in us_data.iterrows():
                lines.append(f"- {row.get('日期', 'N/A')} | {row.get('事件', row.get('指标', 'N/A'))} | "
                             f"公布: {row.get('公布值', 'N/A')} | 预期: {row.get('预期值', 'N/A')} | "
                             f"前值: {row.get('前值', 'N/A')}")
            return "\n".join(lines)
        except Exception as e:
            logger.warning(f"获取美国经济日历失败，使用静态参考: {e}")
            return fallback

    def get_vix_index(self) -> str:
        """获取 VIX 恐慌指数（多接口回退）"""
        if not AKSHARE_AVAILABLE:
            return "AKShare 未安装。"
        from datetime import datetime, timedelta
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")

        df = None
        # 方案 1: index_investing_global（investing.com VIX 数据）
        try:
            df = ak.index_investing_global(
                country="美国", index_name="VIX恐慌指数",
                start_date=start, end_date=end,
            )
        except Exception:
            pass

        # 方案 2: 尝试旧函数名
        if df is None or df.empty:
            try:
                df = ak.index_vix(start_date=start, end_date=end)
            except Exception:
                pass

        if df is None or df.empty:
            return "暂无 VIX 数据（所有数据源均失败）。\n参考: VIX 通常在 10-30 区间，>20 表示担忧上升，>30 表示恐慌。"
        try:
            latest = df.iloc[-1]
            lines = ["# VIX 恐慌指数\n"]
            close_val = (latest.get('收盘', None) or latest.get('close', None)
                      or latest.iloc[-1] if len(latest) > 0 else None)
            lines.append(f"- 最新值: {close_val}")
            lines.append(f"- 日期: {latest.get('日期', latest.name)}")
            try:
                val = float(close_val) if close_val is not None else 0
                if val < 15:
                    lines.append("- 水位: 低（市场平静）")
                elif val < 20:
                    lines.append("- 水位: 正常")
                elif val < 30:
                    lines.append("- 水位: 偏高（市场担忧）")
                else:
                    lines.append("- 水位: 极高（市场恐慌）")
            except (ValueError, TypeError):
                pass
            return "\n".join(lines)
        except Exception as e:
            logger.warning(f"解析 VIX 数据失败: {e}")
            return f"VIX 数据解析失败: {e}"

    def get_us_index_data(self, days: int = 20) -> str:
        """获取美股三大指数日线"""
        if not AKSHARE_AVAILABLE:
            return "AKShare 未安装。"
        from datetime import datetime, timedelta
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=days + 5)).strftime("%Y%m%d")
        lines = ["# 美股三大指数\n"]
        for symbol, name in [(".INX", "标普500"), (".IXIC", "纳斯达克"), (".DJI", "道琼斯")]:
            try:
                df = ak.index_us_stock_sina(symbol=symbol)
                if df is not None and not df.empty:
                    # index_us_stock_sina 不接受日期参数，直接用 tail 取最近 N 条
                    df = df.tail(days)
                if df is not None and not df.empty:
                    latest = df.iloc[-1]
                    lines.append(f"## {name}")
                    lines.append(f"- 日期: {latest.get('date', latest.name)}")
                    lines.append(f"- 收盘: {latest.get('close', 'N/A')}")
                    lines.append(f"- 涨跌幅: {latest.get('涨跌幅', latest.get('pct_chg', 'N/A'))}%")
            except Exception as e:
                lines.append(f"## {name}: 获取失败 ({e})")
        return "\n".join(lines)

    def get_ipo_calendar(self, curr_date: str) -> str:
        """获取近期 IPO 日历"""
        if not AKSHARE_AVAILABLE:
            return "AKShare 未安装。"
        lines = ["# 近期 IPO 日历\n"]
        # 申购日历
        try:
            ipo = ak.stock_ipo_em()
            if ipo is not None and not ipo.empty:
                lines.append("## 申购中/待申购")
                for _, row in ipo.head(20).iterrows():
                    lines.append(f"- {row.get('申购代码', 'N/A')} | {row.get('名称', 'N/A')} | "
                                 f"发行价: {row.get('发行价格', 'N/A')} | "
                                 f"申购日期: {row.get('申购日期', 'N/A')}")
        except Exception as e:
            lines.append(f"- 申购日历: 获取失败 ({e})")
        # 上市日历
        try:
            new_stock = ak.stock_new_a_spot_em()
            if new_stock is not None and not new_stock.empty:
                lines.append("\n## 近期上市新股")
                for _, row in new_stock.head(20).iterrows():
                    pe = row.get('市盈率', 'N/A')
                    try:
                        pe = float(pe)
                        big = " ★大盘" if pe > 50 else ""
                    except (ValueError, TypeError):
                        big = ""
                    lines.append(f"- {row.get('名称', 'N/A')} | "
                                 f"上市日期: {row.get('上市日期', 'N/A')} | "
                                 f"市盈率: {pe}{big}")
        except Exception as e:
            lines.append(f"- 上市日历: 获取失败 ({e})")
        lines.append("\n> 大市值新股集中申购/上市可能阶段性冻结/分流二级市场资金（打新抽血效应）。")
        return "\n".join(lines)

    def get_share_unlock_calendar(self, curr_date: str) -> str:
        """获取限售股解禁日历"""
        if not AKSHARE_AVAILABLE:
            return "AKShare 未安装。"
        try:
            df = ak.stock_restricted_release_queue_em()
            if df is None or df.empty:
                return "暂无限售股解禁数据。"
            lines = ["# 限售股解禁日历\n"]
            for _, row in df.head(20).iterrows():
                lines.append(f"- {row.get('名称', 'N/A')} | "
                             f"解禁日期: {row.get('解禁日期', 'N/A')} | "
                             f"解禁数量: {row.get('解禁数量', 'N/A')} | "
                             f"解禁市值: {row.get('解禁市值', 'N/A')}")
            lines.append("\n> 限售股集中解禁是潜在的抛压来源，属于'已知的资金流出预期'。")
            return "\n".join(lines)
        except Exception as e:
            logger.warning(f"获取解禁日历失败: {e}")
            return f"获取解禁日历失败: {e}"

    def get_margin_trading_balance(self, curr_date: str) -> str:
        """获取两融余额（沪市+深市）

        注意：本接口取最新一行快照，不按 curr_date 过滤。
        """
        if not AKSHARE_AVAILABLE:
            return "AKShare 未安装。"
        lines = ["# 融资融券余额\n"]
        # 沪市
        try:
            sse = ak.stock_margin_sse()
            if sse is not None and not sse.empty:
                latest = sse.iloc[-1]
                data_date = _extract_date_from_row(latest, sse)
                date_note = _format_date_note(data_date, curr_date)
                lines.append(f"## 沪市")
                if date_note:
                    lines.append(date_note)
                lines.append(f"- 融资余额: {latest.get('融资余额', 'N/A')}")
                lines.append(f"- 融券余额: {latest.get('融券余额', 'N/A')}")
        except Exception as e:
            lines.append(f"- 沪市两融: 获取失败 ({e})")
        # 深市
        try:
            szse = ak.stock_margin_detail_szse()
            if szse is not None and not szse.empty:
                latest = szse.iloc[-1]
                lines.append(f"## 深市")
                lines.append(f"- 融资余额: {latest.get('融资余额', 'N/A')}")
                lines.append(f"- 融券余额: {latest.get('融券余额', 'N/A')}")
        except Exception as e:
            lines.append(f"- 深市两融: 获取失败 ({e})")
        lines.append("\n> 两融余额变化反映杠杆资金松紧——持续上升 = 杠杆加码看多，持续下降 = 去杠杆避险。")
        return "\n".join(lines)

    def get_market_breadth(self, curr_date: str) -> str:
        """获取市场宽度（涨跌家数、涨跌停统计）

        注意：本接口取最新一行快照，不按 curr_date 过滤。
        如果请求日期非交易日，返回的实际是上一交易日数据。
        """
        if not AKSHARE_AVAILABLE:
            return "AKShare 未安装。"
        try:
            df = ak.stock_market_activity_legu()
            if df is None or df.empty:
                return "暂无市场宽度数据。"
            latest = df.iloc[-1]
            # 尝试提取实际数据日期
            data_date = _extract_date_from_row(latest, df)
            date_note = _format_date_note(data_date, curr_date)
            lines = ["# 市场宽度（情绪温度计）\n"]
            if date_note:
                lines.append(date_note + "\n")
            lines.append(f"- 上涨家数: {latest.get('上涨家数', 'N/A')}")
            lines.append(f"- 下跌家数: {latest.get('下跌家数', 'N/A')}")
            lines.append(f"- 涨停家数: {latest.get('涨停家数', 'N/A')}")
            lines.append(f"- 跌停家数: {latest.get('跌停家数', 'N/A')}")
            up = latest.get('上涨家数', 0)
            down = latest.get('下跌家数', 0)
            try:
                ratio = float(up) / max(float(down), 1)
                lines.append(f"- 涨跌比: {ratio:.2f}")
                if ratio > 3:
                    lines.append("  → 极端普涨，注意过热")
                elif ratio < 0.3:
                    lines.append("  → 极端普跌，注意恐慌")
            except (ValueError, TypeError, ZeroDivisionError):
                pass
            return "\n".join(lines)
        except Exception as e:
            logger.warning(f"获取市场宽度失败: {e}")
            return f"获取市场宽度失败: {e}"

    def get_market_fund_flow(self, curr_date: str) -> str:
        """获取北向资金 + 主力资金流向

        注意：本接口取最新一行快照，不按 curr_date 过滤。
        """
        if not AKSHARE_AVAILABLE:
            return "AKShare 未安装。"
        lines = ["# 资金流向\n"]
        # 北向资金
        try:
            north = ak.stock_hsgt_fund_flow_summary_em()
            if north is not None and not north.empty:
                latest = north.iloc[-1]
                data_date = _extract_date_from_row(latest, north)
                date_note = _format_date_note(data_date, curr_date)
                lines.append("## 北向资金（沪股通+深股通）")
                if date_note:
                    lines.append(date_note)
                lines.append(f"- 日期: {data_date or latest.get('日期', latest.name)}")
                lines.append(f"- 当日净流入: {latest.get('当日净流入', 'N/A')}")
                lines.append(f"- 当月累计净流入: {latest.get('当月累计净流入', 'N/A')}")
                lines.append("\n> ⚠️ 注意：自 2024-08-16 起，北向资金不再披露盘中实时数据，仅披露日终汇总。")
            else:
                lines.append("## 北向资金: 暂无数据")
                lines.append("> 北向资金日终汇总数据暂无（2024-08-16 后披露规则调整）。")
        except Exception as e:
            lines.append(f"## 北向资金: 获取失败 ({e})")
        # 主力资金
        try:
            main = ak.stock_fund_flow_big_deal()
            if main is not None and not main.empty:
                latest = main.iloc[-1]
                lines.append("\n## 主力资金")
                lines.append(f"- 主力净流入: {latest.get('主力净流入', latest.get('主力净流入额', 'N/A'))}")
        except Exception:
            lines.append("\n## 主力资金: 获取失败")
        return "\n".join(lines)

    def get_sector_fund_flow_rank(self, days: int = 5) -> str:
        """获取行业板块资金流向排名（东方财富）"""
        if not AKSHARE_AVAILABLE:
            return "AKShare 未安装。"
        try:
            df = ak.stock_sector_fund_flow_rank(
                indicator="今日",
                sector_type="行业资金流向",
            )
            if df is None or df.empty:
                return "暂无行业资金流向数据。"

            lines = ["# 行业资金流向排名\n"]
            lines.append(str(df.head(30).to_string(index=False)))
            return "\n".join(lines)
        except Exception as e:
            logger.warning(f"获取行业资金流向失败: {e}")
            return f"获取行业资金流向失败: {e}"

    def get_concept_board(self, concept_name: str, days: int = 10) -> str:
        """获取 A 股概念板块数据"""
        if not AKSHARE_AVAILABLE:
            return "AKShare 未安装。"

        code = self.A_SHARE_CONCEPT_MAP.get(concept_name)
        if not code:
            return f"未知概念板块: {concept_name}"

        try:
            df = _call_with_timeout(
                lambda: ak.stock_board_concept_hist_em(
                    symbol=concept_name,
                    period="daily",
                    start_date=(datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d"),
                    end_date=datetime.now().strftime("%Y%m%d"),
                ),
                timeout=_AKSHARE_TIMEOUT,
            )
            if df is not None and not df.empty:
                df = df.tail(days)
                return (
                    f"## A股概念板块: {concept_name}\n"
                    f"{df.to_string(index=False)}"
                )
            return f"未获取到 {concept_name} 板块数据。"
        except Exception as e:
            logger.warning(f"获取概念板块失败 [{concept_name}]: {e}")
            return f"获取 {concept_name} 板块数据失败: {e}"


    def get_all_concept_boards(self, days: int = 10) -> str:
        """获取所有 AI 产业链概念板块数据（连续 2 个失败则终止剩余请求）"""
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


    # ==================== 板块层 — 选股层数据（东财概念体系） ====================

    def get_concept_board_names(self) -> str:
        """获取东财概念板块全名单（每行一个概念名，供结构化清单过滤）"""
        if not AKSHARE_AVAILABLE:
            return "AKShare 未安装，无法获取概念板块名单。"
        try:
            df = _try_call(ak.stock_board_concept_name_em)
            if df is None or df.empty:
                return "未获取到东财概念板块名单。"
            # 列名防御：优先"板块名称"列，回退第一列
            name_col = next((c for c in df.columns if "名称" in str(c)), df.columns[0])
            names = [str(n).strip() for n in df[name_col].tolist() if str(n).strip()]
            return "\n".join(names) if names else "东财概念板块名单为空。"
        except Exception as e:
            logger.warning(f"获取东财概念板块名单失败: {e}")
            return f"获取东财概念板块名单失败: {e}"

    def get_sector_constituents(self, sector_name: str) -> str:
        """获取东财概念板块成分股（stock_board_concept_cons_em）→ `代码|名称`"""
        if not AKSHARE_AVAILABLE:
            return "AKShare 未安装，无法获取板块成分股。"
        try:
            df = _try_call(ak.stock_board_concept_cons_em, symbol=sector_name)
            if df is None or df.empty:
                return f"未获取到 {sector_name} 成分股数据。"
            # 列名防御：优先"代码"/"名称"列，缺失时整行扫描 6 位代码
            code_col = next((c for c in df.columns if str(c) in ("代码", "股票代码")), None)
            name_col = next((c for c in df.columns if str(c) in ("名称", "股票简称")), None)
            lines = []
            for _, row in df.iterrows():
                if code_col is not None:
                    code = str(row.get(code_col, "")).strip()
                    name = str(row.get(name_col, "")).strip() if name_col is not None else ""
                else:
                    vals = [str(v).strip() for v in row.tolist()]
                    code = next((v for v in vals if re.fullmatch(r"\d{6}", v)), "")
                    name = ""
                if code and re.fullmatch(r"\d{6}", code):
                    lines.append(f"{code}|{name}")
            return "\n".join(lines) if lines else f"{sector_name} 成分股为空。"
        except Exception as e:
            logger.warning(f"获取板块成分股失败 [{sector_name}]: {e}")
            return f"获取 {sector_name} 成分股失败: {e}"

    def get_stocks_performance_ranking(self, codes: list, days: int = 10) -> str:
        """批量计算近 N 日涨跌幅 + 最新价/最新成交额（末行板块均值）。

        连续 3 只失败熔断，防止数据源异常时拖垮整条流水线。
        """
        if not AKSHARE_AVAILABLE:
            return "AKShare 未安装，无法获取个股涨幅数据。"
        try:
            end_date = datetime.now().strftime("%Y%m%d")
            start_date = (datetime.now() - timedelta(days=days * 4)).strftime("%Y%m%d")
            rows = []
            pcts = []
            consecutive_failures = 0
            for raw in codes:
                code = self._normalize_code(str(raw).strip())
                if not code:
                    continue
                # 不复权：last_close 必须是真实市场价（供金额→股数换算），与 Tushare daily 口径一致
                df = _try_call(
                    ak.stock_zh_a_hist,
                    symbol=code,
                    period="daily",
                    start_date=start_date,
                    end_date=end_date,
                    adjust="",
                )
                if df is None or df.empty or len(df) < 2:
                    consecutive_failures += 1
                    logger.warning(
                        "get_stocks_performance_ranking: %s 无数据，连续失败 %d",
                        code, consecutive_failures,
                    )
                    if consecutive_failures >= 3:
                        logger.warning("get_stocks_performance_ranking: 连续 3 只失败，熔断")
                        break
                    continue
                try:
                    close_col = self._detect_close_column(df)
                    if close_col is None:
                        raise ValueError("无收盘价列")
                    amount_col = next((c for c in df.columns if "成交额" in str(c)), None)
                    df = df.tail(days + 1)
                    first_close = float(df[close_col].iloc[0])
                    last_close = float(df[close_col].iloc[-1])
                    last_amount = float(df[amount_col].iloc[-1]) if amount_col else 0.0
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

    # ==================== 板块层 — 行业板块 ====================


    def get_industry_sector_performance(self, days: int = 10) -> str:
        """
        获取全行业（申万一级+二级）涨跌排名。
        遍历所有行业板块指数，计算近 N 日涨跌幅并排序。
        输出含「近10个交易日逐日涨跌幅」章节（固定 10 列，与 days 参数无关；
        复用同一批 hist_em 序列，不新增 API 调用）。
        """
        if not AKSHARE_AVAILABLE:
            return "AKShare 未安装，无法获取行业板块数据。"

        try:
            performance_list, daily_closes, date_range, err = self._fetch_industry_daily_series(days)
            if err:
                return err
            if not performance_list:
                return "未获取到任何行业板块数据。"

            # 按涨跌幅排序
            performance_list.sort(key=lambda x: x[1], reverse=True)

            lines = [f"# 全行业板块涨跌排名（近 {days} 日）"]
            lines.append(f"（{date_range[0]} - {date_range[1]}）\n"
                         if date_range else "")
            lines.append(f"共覆盖 {len(performance_list)} 个行业板块\n")
            lines.append("| 排名 | 行业 | 涨跌幅(%) | 最新收盘价 | 数据天数 |")
            lines.append("|------|------|-----------|------------|----------|")
            for i, (name, pct, price, ndays) in enumerate(performance_list, 1):
                lines.append(f"| {i} | {name} | {pct:+.2f}% | {price:.2f} | {ndays} |")

            # TOP5 / BOTTOM5 标注
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

    def _fetch_industry_daily_series(self, days: int = 10):
        """行业日线拉取（东财行业板块）：返回 (performance_list, daily_closes, date_range, err)。

        performance_list = [(name, pct, price, ndays)]（未排序，provider 获取序）；
        daily_closes = {行业名: DataFrame[date_col, close_col]}（tail(11)，逐日章节/矩阵复用）；
        date_range = (start, end) 取自首个成功行业的日线窗口。
        err 非空 = 列表接口失败/列表为空（err 即原返回文案）；全部行业失败 → ([], {}, None, None)。
        """
        # 获取所有行业板块名称列表
        industry_df = _try_call(ak.stock_board_industry_name_em)
        if industry_df is None or industry_df.empty:
            return None, None, None, "未获取到行业板块列表（API 不可用）。"

        # 提取板块名称（申万一级行业通常在前列）
        industry_names = industry_df.iloc[:, 0].tolist() if len(industry_df.columns) > 0 else []

        if not industry_names:
            return None, None, None, "行业板块列表为空。"

        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=days * 3)).strftime("%Y%m%d")

        performance_list = []
        daily_closes = {}  # 行业名 → tail(11) 日线，逐日章节/矩阵复用（不新增 API 调用）
        date_range = None  # 聚合区间 (start, end)，取自首个成功行业的日线窗口
        consecutive_failures = 0
        for name in industry_names:
            try:
                df = _call_with_timeout(
                    lambda n=name: ak.stock_board_industry_hist_em(
                        symbol=n,
                        start_date=start_date,
                        end_date=end_date,
                        period="daily",
                        adjust="",
                    ),
                    timeout=_AKSHARE_TIMEOUT,
                )
                if df is not None and not df.empty and len(df) >= 2:
                    df = df.tail(days + 1)
                    close_col = self._detect_close_column(df)
                    if close_col:
                        first_close = float(df[close_col].iloc[0])
                        last_close = float(df[close_col].iloc[-1])
                        if first_close > 0:
                            pct_change = (last_close - first_close) / first_close * 100
                            performance_list.append((name, pct_change, last_close, len(df)))
                            date_col = self._detect_date_column(df)
                            if date_col:
                                daily_closes[name] = df.tail(11)[[date_col, close_col]].copy()
                                if date_range is None:
                                    dvals = pd.to_datetime(df[date_col])
                                    date_range = (dvals.min().strftime("%Y%m%d"),
                                                  dvals.max().strftime("%Y%m%d"))
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
            except Exception:
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    break
                continue
        return performance_list, daily_closes, date_range, None

    def get_industry_daily_returns_matrix(self, days: int = 10):
        """行业近 N 个交易日逐日涨跌幅结构化矩阵（热力图数据源）。

        复用 get_industry_sector_performance 的日线拉取（_fetch_industry_daily_series），
        与文本逐日章节同源同口径。
        返回 {"source": "akshare", "dates": [...升序], "names": [...], "pct_matrix": [...]}；
        不支持/数据不足时返回 None（结构化接口约定）。
        """
        if not AKSHARE_AVAILABLE:
            return None
        try:
            performance_list, daily_closes, _, err = self._fetch_industry_daily_series(days)
            if err or not performance_list or not daily_closes:
                return None
            series_map = {}
            for name, df in daily_closes.items():
                # 列名是动态探测的（date/close 均为 _detect 结果），按列位置取
                series_map[name] = daily_matrix_utils.daily_pct_from_closes(
                    df.iloc[:, 0].astype(str).tolist(),
                    pd.to_numeric(df.iloc[:, 1], errors="coerce").tolist(),
                )
            n = min(daily_matrix_utils.DAILY_COLS, days)
            master = daily_matrix_utils.master_dates_from_series(series_map, n=n)
            if len(master) < 2:
                return None
            names = [name for name, *_ in performance_list]
            return {"source": "akshare", "dates": master, "names": names,
                    "pct_matrix": [[series_map.get(nm, {}).get(d) for d in master]
                                   for nm in names]}
        except Exception as e:
            logger.warning(f"获取行业逐日矩阵失败: {e}")
            return None

    def _append_industry_daily_section(self, performance_list, daily_closes, days: int) -> str:
        """行业逐日涨跌幅章节（纯格式化）：行序 = 聚合排名序，固定最近 10 个交易日列。

        performance_list = [(name, pct, price, ndays)]（已按 pct 降序）；
        daily_closes = {name: DataFrame[日期列, 收盘列]}（tail(11)，不足按实际）。
        累计列 = 逐日 pct 复利，days=10 时与聚合表 pct 一致。
        """
        n = min(daily_matrix_utils.DAILY_COLS, days)
        series_map = {}
        for name, df in daily_closes.items():
            series_map[name] = daily_matrix_utils.daily_pct_from_closes(
                df.iloc[:, 0].tolist(),
                pd.to_numeric(df.iloc[:, 1], errors="coerce").tolist())
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

    @staticmethod
    def _detect_close_column(df) -> str:
        """自动探测 DataFrame 中的收盘价列名"""
        for col in df.columns:
            col_lower = str(col).lower()
            if col_lower in ("close", "收盘", "收盘价", "closing price"):
                return col
        # 回退：取可能的数值列
        for col in df.columns:
            col_lower = str(col).lower()
            if "收盘" in col_lower or "close" in col_lower:
                return col
        return None

    @staticmethod
    def _detect_date_column(df) -> str:
        """自动探测 DataFrame 中的日期列名（逐日章节用）"""
        for col in df.columns:
            col_lower = str(col).lower()
            if col_lower in ("date", "trade_date", "日期", "交易日期"):
                return col
        for col in df.columns:
            col_lower = str(col).lower()
            if "日期" in col_lower or "date" in col_lower:
                return col
        return None


    def get_concept_board_heat_rank(self, days: int = 10) -> str:
        """
        获取热门概念板块热度排名（涨幅+成交额综合排序）。
        遍历所有概念板块，按综合热度排序。
        输出含「近10个交易日逐日涨跌幅」章节（行 = 热度 TOP30，复用已取日线，
        不新增 API 调用）。
        """
        if not AKSHARE_AVAILABLE:
            return "AKShare 未安装，无法获取概念板块数据。"

        try:
            heat_list, daily_pct, err = self._fetch_concept_heat(days, top_n=30)
            if err:
                return err
            if not heat_list:
                return "未获取到任何概念板块数据。"

            lines = [f"# 概念板块热度排名（近 {days} 日）\n"]
            lines.append(f"共覆盖 {len(heat_list)} 个概念板块\n")
            lines.append("| 排名 | 概念板块 | 涨跌幅(%) | 成交额变化(%) | 综合热度 |")
            lines.append("|------|----------|-----------|---------------|----------|")
            for i, (name, pct, vol_chg, heat) in enumerate(heat_list[:30], 1):  # 只展示TOP30
                lines.append(f"| {i} | {name} | {pct:+.2f}% | {vol_chg:+.1f}% | {heat:+.1f} |")

            lines.append(f"\n## 热度 TOP10")
            for i, (name, pct, vol_chg, heat) in enumerate(heat_list[:10], 1):
                lines.append(f"  {i}. {name}: 涨幅 {pct:+.2f}%, 量变 {vol_chg:+.1f}%, 热度 {heat:+.1f}")

            daily_section = self._append_concept_daily_section(heat_list, daily_pct, days)
            if daily_section:
                lines.append(daily_section)
            return "\n".join(lines)
        except Exception as e:
            logger.warning(f"获取概念板块热度失败: {e}")
            return f"获取概念板块热度失败: {e}"

    def _fetch_concept_heat(self, days: int, top_n: int = 30):
        """概念热度计算（遍历概念板块 hist_em）：返回 (heat_list, daily_pct, err)。

        heat_list = [(name, pct, vol_change, heat)] 已按热度降序；
        daily_pct = {概念名: {YYYYMMDD: pct}}（逐日章节/矩阵复用，不新增 API 调用）。
        err 非空 = 列表接口失败/列表为空（err 即原返回文案）。
        """
        # 获取概念板块名称列表
        concept_df = ak.stock_board_concept_name_em()
        if concept_df is None or concept_df.empty:
            return None, None, "未获取到概念板块列表。"

        concept_names = concept_df.iloc[:, 0].tolist() if len(concept_df.columns) > 0 else []
        if not concept_names:
            return None, None, "概念板块列表为空。"

        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=days * 3)).strftime("%Y%m%d")

        heat_list = []
        daily_pct = {}  # 概念名 → {YYYYMMDD: pct}，逐日章节/矩阵复用（不新增 API 调用）
        for name in concept_names:
            try:
                df = ak.stock_board_concept_hist_em(
                    symbol=name,
                    period="daily",
                    start_date=start_date,
                    end_date=end_date,
                )
                if df is not None and not df.empty and len(df) >= 2:
                    df = df.tail(days + 1)
                    close_col = self._detect_close_column(df)
                    volume_col = self._detect_volume_column(df)
                    if close_col:
                        first_close = float(df[close_col].iloc[0])
                        last_close = float(df[close_col].iloc[-1])
                        if first_close > 0:
                            pct_change = (last_close - first_close) / first_close * 100
                            # 计算成交额变化
                            vol_change = 0
                            if volume_col:
                                recent_vol = float(df[volume_col].iloc[-days:].mean()) if len(df) >= days else float(df[volume_col].mean())
                                older_vol = float(df[volume_col].iloc[:-days].mean()) if len(df) > days else recent_vol
                                if older_vol > 0:
                                    vol_change = (recent_vol - older_vol) / older_vol * 100
                            # 综合热度 = 涨跌幅权重0.6 + 成交额变化权重0.4
                            heat_score = pct_change * 0.6 + vol_change * 0.4
                            heat_list.append((name, pct_change, vol_change, heat_score))
                            date_col = self._detect_date_column(df)
                            if date_col:
                                daily_pct[name] = daily_matrix_utils.daily_pct_from_closes(
                                    df.tail(11)[date_col].tolist(),
                                    pd.to_numeric(df.tail(11)[close_col],
                                                 errors="coerce").tolist())
            except Exception:
                continue

        if not heat_list:
            # 防御性分支：concept_names 非空时每行成功 append / 失败 continue，正常路径
            # 仅当全部概念行均失败时可达——保留兜底，调用方按空列表处理
            return [], {}, None
        # 按综合热度排序
        heat_list.sort(key=lambda x: x[3], reverse=True)
        return heat_list, daily_pct, None

    def _concept_daily_rows(self, heat_list, daily_pct, days: int, top_n: int = 30):
        """概念逐日涨跌幅行数据（已取日线口径）：返回 (rows, master, gaps, insufficient)。

        rows = [(name, {YYYYMMDD: pct}, cum_pct)]，行序 = heat_list[:top_n] 序；
        daily_pct 全空 → (None, [], [], None)；master 不足 2 天 → rows=None、
        insufficient = master 长度（供调用方生成"数据不足"提示）。
        """
        n = min(daily_matrix_utils.DAILY_COLS, days)
        if not daily_pct:
            return None, [], [], None
        # 历史概念可能上市晚，日期轴用覆盖 ≥50% 概念的最近 n 天口径
        master = daily_matrix_utils.master_dates_from_series(daily_pct, n=n, min_coverage=0.5)
        if len(master) < 2:
            return None, [], [], len(master)
        covered = {d for s in daily_pct.values() for d in s}
        gaps = daily_matrix_utils.compute_gap_dates(min(covered), max(covered), covered)
        rows = [(name, daily_pct.get(name, {}),
                 daily_matrix_utils.cum_pct_from_daily(daily_pct.get(name, {})))
                for name, *_ in heat_list[:top_n]]
        return rows, master, gaps, None

    def _append_concept_daily_section(self, heat_list, daily_pct, days: int) -> str:
        """概念逐日涨跌幅章节（近 10 个交易日）：行 = 热度 TOP30，单元格来自已取日线 close 序列。

        heat_list = [(name, pct, vol_chg, heat)]（已按热度降序）；
        daily_pct = {概念名: {YYYYMMDD: pct}}。
        累计列 = 逐日 pct 复利，days=10 时与聚合表 pct 一致。
        """
        rows, master, gaps, insufficient = self._concept_daily_rows(heat_list, daily_pct, days)
        if rows is None:
            if insufficient is None:
                return ""
            return "\n> 近10日逐日涨跌幅数据不足（仅 %d 天），已省略。" % insufficient
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
            title="近%d个交易日逐日涨跌幅（概念×日期，列头 MM-DD）" % len(master),
            caption="；".join(caption_parts),
            intraday_date=intraday_date,
            gap_dates=gaps,
            name_col="概念板块",
        )
        return ("\n\n" + section) if section else ""

    def get_concept_daily_returns_matrix(self, days: int = 10, top_n: int = 30):
        """概念板块近 N 个交易日逐日涨跌幅结构化矩阵（热力图数据源）。

        行集合与概念热度 TOP N 章节同口径（批量 hist_em + 热度公式排序，
        复用 _fetch_concept_heat + _concept_daily_rows）。
        返回 {"source": "akshare", "dates": [...升序], "names": [...], "pct_matrix": [...]}；
        不支持/数据不足时返回 None（结构化接口约定）。
        """
        if not AKSHARE_AVAILABLE:
            return None
        try:
            heat_list, daily_pct, err = self._fetch_concept_heat(days, top_n)
            if err or not heat_list:
                return None
            rows, master, _, _ = self._concept_daily_rows(heat_list, daily_pct, days, top_n)
            if rows is None or len(master) < 2:
                return None
            names = [name for name, *_ in rows]
            return {"source": "akshare", "dates": master, "names": names,
                    "pct_matrix": [[pcts.get(d) for d in master] for _, pcts, _ in rows]}
        except Exception as e:
            logger.warning(f"获取概念逐日矩阵失败: {e}")
            return None


    @staticmethod
    def _detect_volume_column(df) -> str:
        """自动探测 DataFrame 中的成交量/成交额列名"""
        for col in df.columns:
            col_lower = str(col).lower()
            if col_lower in ("volume", "vol", "成交量", "成交额", "amount"):
                return col
        for col in df.columns:
            col_lower = str(col).lower()
            if "成交" in col_lower or "volume" in col_lower or "amount" in col_lower:
                return col
        return None


    def _get_industry_daily_dataframes(self, days: int = 120) -> dict:
        """返回各行业板块的日线 DataFrame 字典 {行业名: DataFrame}

        供 interface 层组合函数（如 get_sector_horizon_screening）做多周期重采样。
        返回空 dict 表示数据不可用。
        """
        if not AKSHARE_AVAILABLE:
            return {}
        try:
            industry_df = _try_call(ak.stock_board_industry_name_em)
            if industry_df is None or industry_df.empty:
                return {}
            industry_names = industry_df.iloc[:, 0].tolist() if len(industry_df.columns) > 0 else []
            if not industry_names:
                return {}

            end_date = datetime.now().strftime("%Y%m%d")
            start_date = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")

            result = {}
            consecutive_failures = 0
            for name in industry_names:
                try:
                    df = _call_with_timeout(
                        lambda n=name: ak.stock_board_industry_hist_em(
                            symbol=n, start_date=start_date, end_date=end_date,
                            period="daily", adjust="",
                        ),
                        timeout=_AKSHARE_TIMEOUT,
                    )
                    if df is not None and not df.empty and len(df) >= 20:
                        # 标准化列名：日期→date, 收盘→close
                        col_map = {}
                        for col in df.columns:
                            cl = str(col).strip()
                            if cl in ('日期', 'date', 'trade_date'):
                                col_map[col] = 'date'
                            elif cl in ('收盘', 'close'):
                                col_map[col] = 'close'
                        if col_map:
                            df = df.rename(columns=col_map)
                            result[name] = df[['date', 'close']].copy()
                        consecutive_failures = 0
                    else:
                        consecutive_failures += 1
                        if consecutive_failures >= 3:
                            break
                except Exception:
                    consecutive_failures += 1
                    if consecutive_failures >= 3:
                        break
            return result
        except Exception:
            return {}

    def get_sector_technical_screening(self, days: int = 60) -> str:
        """
        逐行业计算技术指标（均线排列、RSI、MACD、量比），
        输出全行业技术状态矩阵。
        纯计算函数，不依赖额外 API。
        """
        if not AKSHARE_AVAILABLE:
            return "AKShare 未安装，无法获取行业板块数据。"

        try:
            industry_df = _try_call(ak.stock_board_industry_name_em)
            if industry_df is None or industry_df.empty:
                return "未获取到行业板块列表（API 不可用）。"

            industry_names = industry_df.iloc[:, 0].tolist() if len(industry_df.columns) > 0 else []
            if not industry_names:
                return "行业板块列表为空。"

            end_date = datetime.now().strftime("%Y%m%d")
            start_date = (datetime.now() - timedelta(days=days * 3)).strftime("%Y%m%d")

            screening_results = []
            consecutive_failures = 0
            for name in industry_names:
                try:
                    df = _call_with_timeout(
                        lambda n=name: ak.stock_board_industry_hist_em(
                            symbol=n,
                            start_date=start_date,
                            end_date=end_date,
                            period="daily",
                            adjust="",
                        ),
                        timeout=_AKSHARE_TIMEOUT,
                    )
                    if df is None or df.empty or len(df) < 20:
                        consecutive_failures += 1
                        if consecutive_failures >= 3:
                            break
                        continue
                    consecutive_failures = 0

                    df = df.tail(days)
                    close_col = self._detect_close_column(df)
                    if not close_col:
                        continue

                    closes = df[close_col].astype(float)
                    last_close = float(closes.iloc[-1])

                    # 均线计算
                    ma5 = float(closes.tail(5).mean()) if len(closes) >= 5 else last_close
                    ma10 = float(closes.tail(10).mean()) if len(closes) >= 10 else last_close
                    ma20 = float(closes.tail(20).mean()) if len(closes) >= 20 else last_close
                    ma60 = float(closes.tail(60).mean()) if len(closes) >= 60 else last_close

                    # 均线排列判断
                    if last_close > ma5 > ma10 > ma20 > ma60:
                        ma_status = "多头排列"
                    elif last_close < ma5 < ma10 < ma20 < ma60:
                        ma_status = "空头排列"
                    elif last_close > ma5 and last_close > ma20:
                        ma_status = "短期偏多"
                    elif last_close < ma5 and last_close < ma20:
                        ma_status = "短期偏空"
                    else:
                        ma_status = "震荡"

                    # RSI(14) 简化计算
                    rsi = _calc_rsi(closes, 14)

                    # MACD 简化计算
                    macd_signal = _calc_macd_signal(closes)

                    # 量比（5日均量 vs 20日均量）
                    vol_col = self._detect_volume_column(df)
                    volume_ratio = 1.0
                    if vol_col:
                        vols = df[vol_col].astype(float)
                        vol_5 = float(vols.tail(5).mean())
                        vol_20 = float(vols.tail(20).mean()) if len(vols) >= 20 else vol_5
                        volume_ratio = vol_5 / vol_20 if vol_20 > 0 else 1.0

                    # 综合技术状态
                    if ma_status == "多头排列" and rsi > 50 and macd_signal == "金叉" and volume_ratio > 1.1:
                        tech_status = "强势"
                    elif ma_status == "空头排列" and rsi < 50 and macd_signal == "死叉":
                        tech_status = "弱势"
                    elif ma_status in ("多头排列", "短期偏多") and rsi > 50:
                        tech_status = "偏强"
                    elif ma_status in ("空头排列", "短期偏空") and rsi < 50:
                        tech_status = "偏弱"
                    else:
                        tech_status = "中性"

                    # 异动检测
                    alert = ""
                    if volume_ratio > 1.5 and last_close > ma20:
                        alert = "放量突破"
                    elif volume_ratio > 1.5 and last_close < ma20:
                        alert = "放量下跌"
                    elif rsi > 80:
                        alert = "超买"
                    elif rsi < 20:
                        alert = "超卖"

                    screening_results.append({
                        "name": name,
                        "close": last_close,
                        "ma_status": ma_status,
                        "rsi": rsi,
                        "macd": macd_signal,
                        "vol_ratio": volume_ratio,
                        "tech_status": tech_status,
                        "alert": alert,
                    })
                except Exception:
                    continue

            if not screening_results:
                return "未获取到任何行业技术数据。"

            # 按技术状态分组排序
            status_order = {"强势": 0, "偏强": 1, "中性": 2, "偏弱": 3, "弱势": 4}
            screening_results.sort(key=lambda x: status_order.get(x["tech_status"], 2))

            lines = [f"# 全行业技术状态矩阵（近 {days} 日）\n"]
            lines.append(f"共分析 {len(screening_results)} 个行业板块\n")
            lines.append("| 行业 | 收盘价 | 均线状态 | RSI | MACD | 量比 | 综合状态 | 异动 |")
            lines.append("|------|--------|----------|-----|------|------|----------|------|")
            for r in screening_results:
                alert_mark = f"⚠️ {r['alert']}" if r["alert"] else ""
                lines.append(
                    f"| {r['name']} | {r['close']:.2f} | {r['ma_status']} | "
                    f"{r['rsi']:.0f} | {r['macd']} | {r['vol_ratio']:.2f} | "
                    f"{r['tech_status']} | {alert_mark} |"
                )

            # 汇总统计
            strong = sum(1 for r in screening_results if r["tech_status"] == "强势")
            weak = sum(1 for r in screening_results if r["tech_status"] == "弱势")
            biased_strong = sum(1 for r in screening_results if r["tech_status"] == "偏强")
            biased_weak = sum(1 for r in screening_results if r["tech_status"] == "偏弱")
            neutral = sum(1 for r in screening_results if r["tech_status"] == "中性")
            alerts = [r for r in screening_results if r["alert"]]

            lines.append(f"\n## 统计摘要")
            lines.append(f"- 强势: {strong} | 偏强: {biased_strong} | 中性: {neutral} | 偏弱: {biased_weak} | 弱势: {weak}")
            if alerts:
                lines.append(f"\n## ⚠️ 异动信号（共 {len(alerts)} 个）")
                for r in alerts:
                    lines.append(f"  - {r['name']}: {r['alert']}（状态: {r['tech_status']}）")

            return "\n".join(lines)
        except Exception as e:
            logger.warning(f"行业技术筛选失败: {e}")
            return f"行业技术筛选失败: {e}"


    @staticmethod
    def _calc_rsi(closes, period=14):
        """简化 RSI 计算"""
        try:
            if len(closes) < period + 1:
                return 50.0
            deltas = closes.diff()
            gains = deltas.clip(lower=0)
            losses = (-deltas).clip(lower=0)
            avg_gain = float(gains.tail(period).mean())
            avg_loss = float(losses.tail(period).mean())
            if avg_loss == 0:
                return 100.0
            rs = avg_gain / avg_loss
            return float(100 - 100 / (1 + rs))
        except Exception:
            return 50.0


    @staticmethod
    def _calc_macd_signal(closes, fast=12, slow=26, signal=9):
        """简化 MACD 信号判断"""
        try:
            if len(closes) < slow + signal:
                return "数据不足"
            ema_fast = closes.ewm(span=fast, adjust=False).mean()
            ema_slow = closes.ewm(span=slow, adjust=False).mean()
            macd_line = ema_fast - ema_slow
            signal_line = macd_line.ewm(span=signal, adjust=False).mean()
            last_macd = float(macd_line.iloc[-1])
            last_signal = float(signal_line.iloc[-1])
            prev_macd = float(macd_line.iloc[-2])
            prev_signal = float(signal_line.iloc[-2])
            if last_macd > last_signal and prev_macd <= prev_signal:
                return "金叉"
            elif last_macd < last_signal and prev_macd >= prev_signal:
                return "死叉"
            elif last_macd > last_signal:
                return "多头"
            else:
                return "空头"
        except Exception:
            return "计算失败"


    def get_sector_relative_strength(self, days: int = 20) -> str:
        """
        计算各行业相对大盘（上证综指）的 alpha 排名。
        复用行业数据 + 上证综指作为基准。
        """
        if not AKSHARE_AVAILABLE:
            return "AKShare 未安装，无法获取行业板块数据。"

        try:
            # 获取上证综指基准数据
            end_date = datetime.now().strftime("%Y%m%d")
            start_date = (datetime.now() - timedelta(days=days * 3)).strftime("%Y%m%d")

            benchmark_pct = 0.0
            try:
                sh_df = _try_call(
                    ak.index_sh_a_hist, symbol="000001", period="daily",
                    start_date=start_date, end_date=end_date,
                )
                if sh_df is not None and not sh_df.empty and len(sh_df) >= 2:
                    sh_df = sh_df.tail(days + 1)
                    close_col = self._detect_close_column(sh_df)
                    if close_col:
                        first = float(sh_df[close_col].iloc[0])
                        last = float(sh_df[close_col].iloc[-1])
                        if first > 0:
                            benchmark_pct = (last - first) / first * 100
            except Exception:
                pass

            # 获取全行业数据
            industry_df = _try_call(ak.stock_board_industry_name_em)
            if industry_df is None or industry_df.empty:
                return "未获取到行业板块列表（API 不可用）。"

            industry_names = industry_df.iloc[:, 0].tolist() if len(industry_df.columns) > 0 else []

            alpha_list = []
            consecutive_failures = 0
            for name in industry_names:
                try:
                    df = _call_with_timeout(
                        lambda n=name: ak.stock_board_industry_hist_em(
                            symbol=n,
                            start_date=start_date,
                            end_date=end_date,
                            period="daily",
                            adjust="",
                        ),
                        timeout=_AKSHARE_TIMEOUT,
                    )
                    if df is not None and not df.empty and len(df) >= 2:
                        df = df.tail(days + 1)
                        close_col = self._detect_close_column(df)
                        if close_col:
                            first_close = float(df[close_col].iloc[0])
                            last_close = float(df[close_col].iloc[-1])
                            if first_close > 0:
                                sector_pct = (last_close - first_close) / first_close * 100
                                alpha = sector_pct - benchmark_pct
                                alpha_list.append((name, sector_pct, alpha))
                        consecutive_failures = 0
                    else:
                        consecutive_failures += 1
                except Exception:
                    consecutive_failures += 1
                    if consecutive_failures >= 3:
                        break
                    continue

            if not alpha_list:
                return "未获取到行业 alpha 数据。"

            alpha_list.sort(key=lambda x: x[2], reverse=True)

            lines = [f"# 行业相对强度排名（近 {days} 日）\n"]
            lines.append(f"基准: 上证综指 {benchmark_pct:+.2f}%\n")
            lines.append("| 排名 | 行业 | 行业涨幅(%) | Alpha(%) |")
            lines.append("|------|------|-------------|----------|")
            for i, (name, pct, alpha) in enumerate(alpha_list, 1):
                lines.append(f"| {i} | {name} | {pct:+.2f}% | {alpha:+.2f}% |")

            # 显著正/负 alpha
            positive = [(n, a) for n, _, a in alpha_list if a > 3]
            negative = [(n, a) for n, _, a in alpha_list if a < -3]
            if positive:
                lines.append(f"\n## 显著正 Alpha（> +3%）")
                for n, a in positive:
                    lines.append(f"  - {n}: Alpha {a:+.2f}%")
            if negative:
                lines.append(f"\n## 显著负 Alpha（< -3%）")
                for n, a in negative:
                    lines.append(f"  - {n}: Alpha {a:+.2f}%")

            return "\n".join(lines)
        except Exception as e:
            logger.warning(f"行业相对强度计算失败: {e}")
            return f"行业相对强度计算失败: {e}"


# ==================== 相关性分析 ====================
def calculate_correlation(dataframes: dict, target_col: str = "close") -> str:
    """
    计算多个指数之间的收益率相关性矩阵
    Args:
        dataframes: {name: pd.DataFrame} 字典
        target_col: 用于计算相关性的列名
    Returns:
        格式化的相关性分析文本
    """
    if not dataframes or len(dataframes) < 2:
        return "数据不足，无法计算相关性。"
    # 构建价格矩阵
    price_matrix = pd.DataFrame()
    for name, df in dataframes.items():
        if df is None or df.empty:
            continue
        # 自动探测收盘价列：优先匹配 "close" / "收盘" / "收盘价"
        close_col = None
        for col in df.columns:
            if col.lower() in ("close", "收盘", "收盘价", "closing price"):
                close_col = col
                break
        # 未匹配到则尝试倒数第二列
        if close_col is None and len(df.columns) > 0:
            close_col = df.columns[-2] if len(df.columns) > 1 else df.columns[0]
        if close_col:
            series = pd.to_numeric(df[close_col], errors="coerce")
            price_matrix[name] = series.values[:10] if len(series) >= 10 else series.values
    if price_matrix.empty or len(price_matrix.columns) < 2:
        return "数据不足，无法计算相关性。"
    # 计算收益率，再求相关性矩阵
    returns = price_matrix.pct_change().dropna()
    if len(returns) < 3:
        return "数据点不足，无法计算相关性。"
    corr_matrix = returns.corr()
    lines = ["## 指数收益率相关性矩阵（基于最近10日）", ""]
    lines.append(corr_matrix.round(3).to_string())
    lines.append("")
    lines.append("相关性解读：")
    lines.append("- > 0.7: 高度正相关")
    lines.append("- 0.3 ~ 0.7: 中度相关")
    lines.append("- < 0.3: 弱相关")
    lines.append("- 负值: 负相关")
    return "\n".join(lines)
def predict_tomorrow_trend(hist_data: dict) -> str:
    """
    基于近期数据的涨跌幅推测明日走势
    Args:
        hist_data: {name: str (格式化数据文本)} 字典
    Returns:
        预测分析文本
    """
    # 解析每个指数格式化文本中的涨跌幅数值
    trends = {}
    for name, data_text in hist_data.items():
        if not data_text or len(data_text) < 50:
            continue
        # 简单解析：扫描最后10行的浮点数，筛选合理范围的涨跌幅
        lines = data_text.strip().split("\n")
        pct_changes = []
        for line in lines[-10:]:
            parts = line.strip().split()
            for p in parts:
                try:
                    val = float(p)
                    if -20 < val < 20 and abs(val) < 15:
                        pct_changes.append(val)
                except ValueError:
                    continue
        if pct_changes:
            avg_pct = sum(pct_changes) / len(pct_changes)
            recent = pct_changes[-3:] if len(pct_changes) >= 3 else pct_changes
            trends[name] = {
                "avg_10d": round(avg_pct, 3),
                "recent_3d": [round(x, 3) for x in recent],
                "momentum": "↑" if sum(recent) > 0 else "↓",
            }
    if not trends:
        return "数据不足，无法预测。"
    lines = ["## 明日走势预测分析", ""]
    lines.append("基于前10日数据的技术相关性分析：")
    lines.append("")
    for name, t in trends.items():
        mom = "看涨" if t["momentum"] == "↑" else "看跌"
        lines.append(
            f"- **{name}**: 10日均涨跌幅={t['avg_10d']}%, "
            f"近3日={t['recent_3d']}, 趋势={mom}"
        )
    # 综合判断：统计看涨/看跌的指数数量
    bullish = sum(1 for t in trends.values() if t["momentum"] == "↑")
    bearish = len(trends) - bullish
    lines.append("")
    if bullish > bearish:
        lines.append("**综合判断**: 多数科技指数呈上涨趋势，明日大概率延续强势。")
        lines.append("建议关注: 半导体、AI算力等上游板块。")
    elif bearish > bullish:
        lines.append("**综合判断**: 多数科技指数呈下跌趋势，明日可能承压。")
        lines.append("建议关注: 防御性板块，控制仓位。")
    else:
        lines.append("**综合判断**: 涨跌互现，市场方向不明朗。")
        lines.append("建议关注: 等待明确信号，短线观望。")
    lines.append("")
    lines.append("⚠️ 此预测基于历史数据的统计相关性，不构成投资建议。")
    return "\n".join(lines)

    # ==================== 事件研究系统 — 结构化接口 ====================

    def get_index_data_df(self, index_code: str, start_date: str, end_date: str):
        """获取指数日线结构化行情（DataFrame，完整区间）。

        返回标准列：trade_date / open / high / low / close / vol / amount
        （AKShare 指数日线无成交额字段，amount 置 NaN）。
        获取失败返回 None。
        """
        if not AKSHARE_AVAILABLE:
            return None
        symbol = self._index_symbol(index_code)
        try:
            df = _call_with_timeout(
                lambda: ak.stock_zh_index_daily(symbol=symbol),
                timeout=_AKSHARE_TIMEOUT * 2,
            )
            if df is None or df.empty:
                return None
            # 过滤日期区间
            df = df[(df["date"] >= start_date.replace("-", ""))
                    & (df["date"] <= end_date.replace("-", ""))]
            if df.empty:
                return None
            df = df.sort_values("date")
            std = pd.DataFrame({
                "trade_date": df["date"].astype(str),
                "open": pd.to_numeric(df.get("open"), errors="coerce"),
                "high": pd.to_numeric(df.get("high"), errors="coerce"),
                "low": pd.to_numeric(df.get("low"), errors="coerce"),
                "close": pd.to_numeric(df.get("close"), errors="coerce"),
                "vol": pd.to_numeric(df.get("volume"), errors="coerce"),
                "amount": float("nan"),
            })
            return std
        except Exception as e:
            logger.warning("AKShare 结构化指数行情获取失败 [%s]: %s", index_code, e)
            return None

    @staticmethod
    def _index_symbol(index_code: str) -> str:
        """将 000001.SH 格式代码转为 AKShare symbol（sh000001）。"""
        code = index_code.strip()
        if "." in code:
            num, exch = code.split(".")
            return f"{exch.lower()}{num}"
        if code.startswith("000") or code.startswith("600") or code.startswith("68"):
            return f"sh{code}"
        return f"sz{code}"

    def get_trade_cal(self, start_date: str, end_date: str, market: str = "CN"):
        """获取交易日历（DataFrame：trade_date, is_open）。V1 仅支持 CN。"""
        if market != "CN" or not AKSHARE_AVAILABLE:
            return None
        try:
            df = _call_with_timeout(
                lambda: ak.tool_trade_date_hist_sina(),
                timeout=_AKSHARE_TIMEOUT * 2,
            )
            if df is None or df.empty or "trade_date" not in df.columns:
                return None
            dates = pd.to_datetime(df["trade_date"])
            start_ts = pd.Timestamp(start_date.replace("-", ""))
            end_ts = pd.Timestamp(end_date.replace("-", ""))
            mask = (dates >= start_ts) & (dates <= end_ts)
            return pd.DataFrame({
                "trade_date": dates[mask].reset_index(drop=True),
                "is_open": 1,
            })
        except Exception as e:
            logger.warning("AKShare 交易日历获取失败: %s", e)
            return None

    def get_macro_context(self, date: str, market: str = "CN") -> dict:
        """获取指定日期 CN 宏观环境指标（10 年期国债收益率）。失败返回空 dict。"""
        if market != "CN" or not AKSHARE_AVAILABLE:
            return {}
        result = {}
        try:
            end_dt = datetime.strptime(date.replace("-", ""), "%Y%m%d")
            start_dt = end_dt - timedelta(days=10)
            df = _call_with_timeout(
                lambda: ak.bond_china_yield(
                    start_date=start_dt.strftime("%Y%m%d"),
                    end_date=end_dt.strftime("%Y%m%d"),
                ),
                timeout=_AKSHARE_TIMEOUT * 2,
            )
            if df is None or df.empty or "10年" not in df.columns:
                return result
            value = pd.to_numeric(df.iloc[-1]["10年"], errors="coerce")
            if pd.notna(value):
                result["rate_10y"] = float(value)
        except Exception as e:
            logger.warning("AKShare 10 年期国债收益率获取失败 [%s]: %s", date, e)
        return result
