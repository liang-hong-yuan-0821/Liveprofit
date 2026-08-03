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
                test = self.api.stock_basic(list_status="L", limit=1)
                if test is not None and not test.empty:
                    self.connected = True
                    logger.info("Tushare 连接成功")
                else:
                    logger.warning("Tushare 连接测试失败")
            except Exception as e:
                logger.error(f"Tushare 连接失败: {e}")
        else:
            logger.warning("Tushare Token 未配置，请在 .env 中设置 TUSHARE_TOKEN")

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
            df = self.api.daily(
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
                        df = fn(ts_code=code, period=f"{year}1231", fields=fields, limit=1)
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
            df = self.api.major_news(
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
            df = self.api.disclosure(ts_code=code,
                                     start_date=self._normalize_date(start_date),
                                     end_date=self._normalize_date(end_date), limit=20)
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
                df = self.api.index_daily(
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
            df = self.api.daily_basic(
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
            df = self.api.index_daily(ts_code=ts_code, start_date=start, end_date=end)
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
            df = self.api.shibor_lpr(date=self._normalize_date(curr_date))
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
            df = self.api.new_share(start_date=(datetime.now() - timedelta(days=90)).strftime("%Y%m%d"),
                                    end_date=datetime.now().strftime("%Y%m%d"))
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
            df = self.api.share_float(
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
            df = self.api.margin(trade_date=self._normalize_date(curr_date))
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
            df = self.api.daily_basic(trade_date=self._normalize_date(curr_date),
                                      fields="ts_code,pct_chg")
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
            df = self.api.moneyflow(trade_date=self._normalize_date(curr_date))
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
            df = self.api.moneyflow_hsgt(start_date=start, end_date=end)
            if df is not None and not df.empty:
                df = df.tail(days)
                return f"# 沪深港通资金流向\n{df.to_string(index=False)}"
            return "暂无行业资金流向数据。"
        except Exception as e:
            return f"获取行业资金流向失败: {e}"