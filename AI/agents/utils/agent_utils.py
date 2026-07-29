"""
YoHo Toolkit (简化版)
仅保留基于 Tushare 数据源的 5 个核心工具。

从 TradingAgents-CN 的 Toolkit 类裁减：
- 移除：所有 Finnhub、Reddit、Google News、SimFin、YFinance、OpenAI Web Search 工具
- 保留：Tushare 数据 + Stockstats 技术指标
"""

import logging
from typing import Annotated
from datetime import date, timedelta, datetime

from langchain_core.messages import HumanMessage, RemoveMessage
from langchain_core.tools import tool

import AI.dataflows.interface as dataflow
from AI.dataflows.technical.stockstats import StockstatsUtils

logger = logging.getLogger(__name__)


# ==================== 消息清理 ====================

def create_msg_delete():
    """创建消息清理节点函数，防止上下文溢出"""

    def delete_messages(state):
        messages = state["messages"]
        removal_operations = [RemoveMessage(id=m.id) for m in messages]
        placeholder = HumanMessage(content="Continue")
        return {"messages": removal_operations + [placeholder]}

    return delete_messages


# ==================== Toolkit 类 ====================

class Toolkit:
    """简化版工具集，仅包含 Tushare 数据源相关工具"""

    def __init__(self, config=None):
        self._config = config or {}

    # ==================== 统一市场数据工具 ====================

    @staticmethod
    @tool
    def get_stock_market_data_unified(
        ticker: Annotated[str, "股票代码，如 000001.SZ"],
        start_date: Annotated[str, "开始日期 YYYY-mm-dd"],
        end_date: Annotated[str, "结束日期 YYYY-mm-dd"],
    ) -> str:
        """
        获取股票日线行情数据（价格、成交量、涨跌幅等），用于技术分析和趋势判断。
        适用于 A 股市场。
        """
        return dataflow.get_china_stock_data(ticker, start_date, end_date)

    # ==================== 统一基本面数据工具 ====================

    @staticmethod
    @tool
    def get_stock_fundamentals_unified(
        ticker: Annotated[str, "股票代码，如 000001.SZ"],
        curr_date: Annotated[str, "当前分析日期 YYYY-mm-dd"],
    ) -> str:
        """
        获取股票基本面/财务数据（ROE、ROA、毛利率、EPS、营收、利润、资产负债等）。
        用于评估公司财务健康状况和估值水平。
        """
        return dataflow.get_china_fundamentals(ticker, curr_date)

    # ==================== 统一新闻数据工具 ====================

    @staticmethod
    @tool
    def get_stock_news_unified(
        ticker: Annotated[str, "股票代码，如 000001.SZ"],
        curr_date: Annotated[str, "当前分析日期 YYYY-mm-dd"],
    ) -> str:
        """
        获取股票相关新闻和公告。用于了解市场情绪、重大事件和政策影响。
        注意：需要 Tushare Pro 高级权限才能获取新闻数据。
        """
        curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
        start_dt = curr_dt - timedelta(days=30)
        start_date = start_dt.strftime("%Y-%m-%d")
        return dataflow.get_china_news(ticker, start_date, curr_date)

    # ==================== 技术指标工具 ====================

    @staticmethod
    @tool
    def get_stockstats_indicators_report(
        symbol: Annotated[str, "股票代码，如 000001.SZ"],
        curr_date: Annotated[str, "当前日期 YYYY-mm-dd"],
        lookback_days: Annotated[int, "回看天数，默认365"] = 365,
    ) -> str:
        """
        获取股票技术指标报告（均线、RSI、MACD、布林带等）。
        用于判断买卖时机、趋势强度、超买超卖状态。
        """
        return StockstatsUtils.get_indicators_report(symbol, curr_date, lookback_days)

    # ==================== 市场概况工具 ====================

    @staticmethod
    @tool
    def get_china_market_overview(
        curr_date: Annotated[str, "当前日期 YYYY-mm-dd"],
    ) -> str:
        """
        获取 A 股市场概况（上证综指、深证成指、创业板指等主要指数行情）。
        用于了解整体市场环境和趋势。
        """
        return dataflow.get_china_market_overview(curr_date)

    # ==================== 全球科技指数工具 ====================

    @staticmethod
    @tool
    def get_global_tech_indices(
        days: Annotated[int, "获取天数，默认10"] = 10,
    ) -> str:
        """
        获取全球主要科技指数近N日数据。
        包含：美股（纳斯达克综合指数、纳斯达克100、费城半导体指数）、
        韩国（KOSPI、科斯达克）、A股（科创50、创业板指、半导体芯片指数、AI指数）。
        用于分析全球科技板块联动关系和跨境传导。
        """
        return dataflow.get_all_tech_indices(days)

    @staticmethod
    @tool
    def get_ai_industry_chain(
        days: Annotated[int, "获取天数，默认10"] = 10,
    ) -> str:
        """
        获取 AI 产业链各细分板块数据。
        包含：存储芯片、半导体、光模块、AI服务器、先进封装、
        算力、AI应用、机器人、智能汽车等概念板块。
        用于分析AI产业链各环节的轮动和强弱关系。
        """
        return dataflow.get_all_concept_boards(days)

    @staticmethod
    @tool
    def get_tech_correlation_analysis(
        days: Annotated[int, "分析天数，默认10"] = 10,
    ) -> str:
        """
        计算全球科技指数间的相关性矩阵，并基于前N日数据推测明日走势。
        分析美股→韩股→A股的科技传导链条，结合动量和相关性给出预测。
        注意：预测基于统计相关性，不构成投资建议。
        """
        return dataflow.analyze_tech_correlation(days)
