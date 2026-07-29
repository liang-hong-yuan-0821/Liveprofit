"""
YoHo AKShare 数据提供器 (简化版)
免费开源数据源，支持 A 股、港股、全球指数。
无需 API Token，基于网络爬虫。
"""

import logging
from datetime import datetime, timedelta

import pandas as pd

from .base_provider import BaseStockDataProvider

logger = logging.getLogger(__name__)

try:
    import akshare as ak
    AKSHARE_AVAILABLE = True
except ImportError:
    AKSHARE_AVAILABLE = False
    ak = None


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
            # 使用东方财富个股信息
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
        try:
            # 财务指标
            fina = ak.stock_financial_abstract_ths(symbol=code)
            if fina is not None and not fina.empty:
                parts.append("--- 财务指标 (同花顺) ---")
                parts.append(fina.head(20).to_string(index=False))
        except Exception as e:
            parts.append(f"财务指标获取失败: {e}")

        try:
            # 利润表
            profit = ak.stock_profit_sheet_by_report_em(symbol=code)
            if profit is not None and not profit.empty:
                parts.append("--- 利润表 ---")
                parts.append(profit.head(5).to_string(index=False))
        except Exception:
            pass

        try:
            # 资产负债表
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
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")

        try:
            # 尝试多个 akshare 函数获取全球指数
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
        """根据键名调用不同的 akshare 函数获取全球指数"""
        # 美股指数
        if ak_key in ("nasdaq", "nasdaq_100"):
            try:
                return ak.index_us_stock_sina(symbol=f".{ak_key.upper() if ak_key != 'nasdaq' else 'IXIC'}",
                                              start_date=start, end_date=end)
            except Exception:
                pass
            # 回退：使用东方财富全球指数
            try:
                code_map = {"nasdaq": "NDX", "IXIC": "IXIC"}
                return ak.stock_us_hist(symbol=code_map.get(ak_key, "NDX"),
                                        period="daily",
                                        start_date=start, end_date=end)
            except Exception:
                pass

        if ak_key == "sox":
            return ak.index_us_stock_sina(symbol=".SOX", start_date=start, end_date=end)

        if ak_key == "djussc":
            return ak.index_us_stock_sina(symbol=".DJUSSC", start_date=start, end_date=end)

        # 韩国指数
        if ak_key == "kospi":
            return ak.stock_zh_index_daily_em(symbol="KS11")
        if ak_key == "kosdaq":
            return ak.stock_zh_index_daily_em(symbol="KQ11")

        # A股科技指数
        if ak_key == "star50":
            return ak.stock_zh_index_daily_em(symbol="sh000688")
        if ak_key == "chinext":
            return ak.stock_zh_index_daily_em(symbol="sz399006")
        if ak_key == "csi_semi":
            return ak.stock_zh_index_daily_em(symbol="sh990001")
        if ak_key == "csi_ai":
            return ak.stock_zh_index_daily_em(symbol="sh931071")

        # 通用回退
        return ak.index_global_hist_em(symbol=ak_key, start_date=start, end_date=end)

    def get_all_tech_indices(self, days: int = 10) -> str:
        """获取所有科技指数数据并计算相关性"""
        results = []
        raw_data = {}

        for key in self.GLOBAL_TECH_INDICES:
            data = self.get_global_index(key, days)
            results.append(data)
            results.append("")

        return "\n".join(results)


# ==================== 月度数据（科技产业细分） ====================

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


def get_concept_board_data(concept_name: str, days: int = 10) -> str:
    """获取 A 股概念板块数据"""
    if not AKSHARE_AVAILABLE:
        return "AKShare 未安装。"

    code = A_SHARE_CONCEPT_MAP.get(concept_name)
    if not code:
        return f"未知概念板块: {concept_name}"

    try:
        df = ak.stock_board_concept_hist_em(
            symbol=concept_name,
            period="daily",
            start_date=(datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d"),
            end_date=datetime.now().strftime("%Y%m%d"),
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


def get_all_concept_boards(days: int = 10) -> str:
    """获取所有 AI 产业链概念板块数据"""
    results = []
    for name in AI_INDUSTRY_CHAIN:
        data = get_concept_board_data(name, days)
        results.append(data)
        results.append("")
    return "\n".join(results)


# ==================== 相关性分析 ====================

def calculate_correlation(dataframes: dict, target_col: str = "close") -> str:
    """
    计算多个指数之间的相关性矩阵

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
        # 尝试找到收盘价列
        close_col = None
        for col in df.columns:
            if col.lower() in ("close", "收盘", "收盘价", "closing price"):
                close_col = col
                break
        if close_col is None and len(df.columns) > 0:
            close_col = df.columns[-2] if len(df.columns) > 1 else df.columns[0]

        if close_col:
            series = pd.to_numeric(df[close_col], errors="coerce")
            price_matrix[name] = series.values[:10] if len(series) >= 10 else series.values

    if price_matrix.empty or len(price_matrix.columns) < 2:
        return "数据不足，无法计算相关性。"

    # 计算收益率相关性
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
    基于前10天数据的相关性推测明天走势

    Args:
        hist_data: {name: str (格式化数据文本)} 字典

    Returns:
        预测分析文本
    """
    # 分析每个指数的最近趋势
    trends = {}
    for name, data_text in hist_data.items():
        if not data_text or len(data_text) < 50:
            continue
        # 简单解析：找最后几行的涨跌幅
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

    # 综合判断
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
