"""
YoHo Tushare 数据提供器 (简化版)
- 仅同步调用
- Token 仅从环境变量 TUSHARE_TOKEN 读取
- 无 MongoDB 回退
- 无异步方法
"""

import os
import logging
from datetime import datetime, timedelta

import pandas as pd

from .base_provider import BaseStockDataProvider

logger = logging.getLogger(__name__)

try:
    import tushare as ts

    TUSHARE_AVAILABLE = True
except ImportError:
    TUSHARE_AVAILABLE = False
    ts = None


class TushareProvider(BaseStockDataProvider):
    """Tushare 数据提供器"""

    def __init__(self):
        super().__init__("Tushare")
        self.api = None
        self._connect()

    def _connect(self):
        """连接到 Tushare API"""
        if not TUSHARE_AVAILABLE:
            logger.error("❌ Tushare 库未安装，请运行: pip install tushare")
            return

        token = os.getenv("TUSHARE_TOKEN", "")
        if token and token != "your-tushare-token":
            try:
                ts.set_token(token)
                self.api = ts.pro_api()
                # 快速连接测试
                test = self.api.stock_basic(list_status="L", limit=1)
                if test is not None and not test.empty:
                    self.connected = True
                    logger.info("✅ Tushare 连接成功")
                else:
                    logger.warning("⚠️ Tushare 连接测试失败")
            except Exception as e:
                logger.error(f"❌ Tushare 连接失败: {e}")
        else:
            logger.warning("⚠️ Tushare Token 未配置，请在 a.bash 中设置 TUSHARE_TOKEN")

    def _normalize_code(self, code: str) -> str:
        """标准化股票代码：确保带有 .SZ 或 .SH 后缀"""
        code = code.strip().upper()
        if "." in code:
            return code
        if code.startswith("6") or code.startswith("9"):
            return f"{code}.SH"
        if code.startswith("0") or code.startswith("3") or code.startswith("2"):
            return f"{code}.SZ"
        return code

    def _normalize_date(self, date_str: str) -> str:
        """标准化日期格式: YYYY-MM-DD -> YYYYMMDD"""
        return date_str.replace("-", "")

    # ==================== 市场行情数据 ====================

    def get_stock_data(self, code: str, start_date: str, end_date: str) -> str:
        """
        获取股票日线行情数据

        Args:
            code: 股票代码 (如 000001.SZ 或 000001)
            start_date: 开始日期 YYYY-MM-DD
            end_date: 结束日期 YYYY-MM-DD

        Returns:
            格式化的行情数据文本
        """
        if not self.connected:
            return "Tushare 未连接。请在 a.bash 中配置有效的 TUSHARE_TOKEN。"

        code = self._normalize_code(code)
        try:
            df = self.api.daily(
                ts_code=code,
                start_date=self._normalize_date(start_date),
                end_date=self._normalize_date(end_date),
            )
            if df is not None and not df.empty:
                # 按日期排序
                df = df.sort_values("trade_date", ascending=True)
                columns_order = [
                    "trade_date", "open", "high", "low", "close",
                    "vol", "amount", "pct_chg", "pre_close", "change"
                ]
                available_cols = [c for c in columns_order if c in df.columns]
                return df[available_cols].to_string(index=False)
            return f"未获取到 {code} 在 {start_date} 至 {end_date} 期间的行情数据。"
        except Exception as e:
            logger.error(f"获取行情数据失败 [{code}]: {e}")
            return f"获取行情数据失败: {e}"

    def get_stock_info(self, code: str) -> dict:
        """
        获取股票基本信息

        Args:
            code: 股票代码

        Returns:
            包含 name, industry, area 等字段的字典
        """
        if not self.connected:
            return {"name": f"股票{code}"}

        code = self._normalize_code(code)
        try:
            df = self.api.stock_basic(ts_code=code)
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

    # ==================== 基本面数据 ====================

    def get_fundamentals(self, code: str, curr_date: str = None) -> str:
        """
        获取股票财务指标数据

        Args:
            code: 股票代码
            curr_date: 当前分析日期，用于确定查询哪个报告期

        Returns:
            格式化的财务数据文本
        """
        if not self.connected:
            return "Tushare 未连接，无法获取基本面数据。"

        code = self._normalize_code(code)
        parts = []

        # 确定查询年份
        if curr_date:
            report_year = int(curr_date[:4])
        else:
            report_year = datetime.now().year

        # 查询最近两个报告期
        for year in [report_year - 1, report_year]:
            try:
                # 财务指标
                indicators = self.api.fina_indicator(
                    ts_code=code,
                    period=f"{year}1231",
                    fields="end_date,roa,roe,grossprofit_margin,netprofit_margin,debt_to_assets,current_ratio,quick_ratio,eps,diluted_eps,bps",
                )
                if indicators is not None and not indicators.empty:
                    parts.append(f"--- {year}年 财务指标 ---")
                    parts.append(indicators.to_string(index=False))

                # 利润表
                income = self.api.income(
                    ts_code=code,
                    period=f"{year}1231",
                    fields="end_date,revenue,total_revenue,oper_income,oper_cost,operate_profit,total_profit,n_income,undist_profit,diluted_eps,basic_eps",
                    limit=1,
                )
                if income is not None and not income.empty:
                    parts.append(f"--- {year}年 利润表 ---")
                    parts.append(income.to_string(index=False))

                # 资产负债表
                balance = self.api.balancesheet(
                    ts_code=code,
                    period=f"{year}1231",
                    fields="end_date,total_assets,total_liab,total_hldr_eqy_exc_min_int,undist_profit,cap_rese,surplus_rese",
                    limit=1,
                )
                if balance is not None and not balance.empty:
                    parts.append(f"--- {year}年 资产负债表 ---")
                    parts.append(balance.to_string(index=False))

                # 现金流量表
                cashflow = self.api.cashflow(
                    ts_code=code,
                    period=f"{year}1231",
                    fields="end_date,n_cashflow_act,n_cashflow_inv_act,n_cashflow_fin_act,cash_and_equivalents",
                    limit=1,
                )
                if cashflow is not None and not cashflow.empty:
                    parts.append(f"--- {year}年 现金流量表 ---")
                    parts.append(cashflow.to_string(index=False))
            except Exception as e:
                logger.warning(f"获取 {year} 年财务数据失败: {e}")
                continue

        if not parts:
            return f"未获取到 {code} 的财务数据。"

        return "\n\n".join(parts)

    # ==================== 新闻数据 ====================

    def get_news(self, code: str, start_date: str, end_date: str) -> str:
        """
        获取股票相关新闻

        注意：Tushare 新闻 API 需要高级权限。如果不可用，返回提示信息。

        Args:
            code: 股票代码
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            新闻数据文本
        """
        if not self.connected:
            return "Tushare 未连接，无法获取新闻数据。"

        code = self._normalize_code(code)
        try:
            # 尝试使用 Tushare 新闻接口
            df = self.api.major_news(
                ts_code=code,
                start_date=self._normalize_date(start_date),
                end_date=self._normalize_date(end_date),
            )
            if df is not None and not df.empty:
                news_lines = []
                for _, row in df.iterrows():
                    title = row.get("title", "")
                    content = row.get("content", "")
                    pub_date = row.get("pub_time", row.get("ann_date", ""))
                    news_lines.append(f"[{pub_date}] {title}\n{content}\n")
                return "\n".join(news_lines)
        except AttributeError:
            logger.info("Tushare 账号不支持 major_news API，尝试其他新闻接口")
        except Exception as e:
            logger.warning(f"获取新闻失败 (major_news): {e}")

        try:
            # 回退到公告接口
            df = self.api.disclosure(
                ts_code=code,
                start_date=self._normalize_date(start_date),
                end_date=self._normalize_date(end_date),
                limit=20,
            )
            if df is not None and not df.empty:
                news_lines = []
                for _, row in df.iterrows():
                    title = row.get("title", "")
                    ann_date = row.get("ann_date", "")
                    news_lines.append(f"[{ann_date}] 公告: {title}")
                return "\n".join(news_lines)
        except AttributeError:
            pass
        except Exception as e:
            logger.warning(f"获取公告失败: {e}")

        return f"当前 Tushare 账号不支持新闻/公告接口，无法获取 {code} 的相关新闻。请升级 Tushare Pro 权限。"

    # ==================== 大盘数据 ====================

    def get_index_data(self, index_codes: str, start_date: str, end_date: str) -> str:
        """
        获取指数行情数据 (如上证综指、深证成指、创业板指)

        Args:
            index_codes: 指数代码，逗号分隔 (如 "000001.SH,399001.SZ")
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            格式化的指数数据文本
        """
        if not self.connected:
            return "Tushare 未连接，无法获取大盘数据。"

        codes = [c.strip() for c in index_codes.split(",")]
        all_parts = []

        for code in codes:
            try:
                df = self.api.index_daily(
                    ts_code=code,
                    start_date=self._normalize_date(start_date),
                    end_date=self._normalize_date(end_date),
                    limit=5,
                )
                if df is not None and not df.empty:
                    df = df.sort_values("trade_date", ascending=False)
                    col_order = ["trade_date", "open", "high", "low", "close", "vol", "pct_chg"]
                    available = [c for c in col_order if c in df.columns]
                    all_parts.append(
                        f"--- 指数 {code} ---\n"
                        f"{df[available].head(5).to_string(index=False)}"
                    )
            except Exception as e:
                logger.warning(f"获取指数 {code} 数据失败: {e}")

        if not all_parts:
            return "未获取到指数数据。"

        return "\n\n".join(all_parts)

    # ==================== 市值数据 ====================

    def get_daily_basic(self, code: str, trade_date: str) -> str:
        """
        获取每日指标 (PE, PB, 换手率, 总市值等)

        Args:
            code: 股票代码
            trade_date: 交易日期 YYYY-MM-DD

        Returns:
            格式化的每日指标文本
        """
        if not self.connected:
            return "Tushare 未连接。"

        code = self._normalize_code(code)
        try:
            df = self.api.daily_basic(
                ts_code=code,
                trade_date=self._normalize_date(trade_date),
                fields="ts_code,trade_date,turnover_rate,volume_ratio,pe,pb,total_mv,circ_mv",
            )
            if df is not None and not df.empty:
                return df.to_string(index=False)
            return f"未获取到 {code} 在 {trade_date} 的每日指标。"
        except Exception as e:
            logger.error(f"获取每日指标失败 [{code}]: {e}")
            return f"获取每日指标失败: {e}"
