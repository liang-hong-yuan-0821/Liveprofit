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

    # ==================== 板块层 — Sector 工具 ====================

    @staticmethod
    @tool
    def get_industry_sector_performance(
        days: Annotated[int, "获取天数，默认10"] = 10,
    ) -> str:
        """
        获取全行业（申万一级+二级）板块涨跌排名。
        遍历所有行业板块指数，按近N日涨跌幅排序，标注领涨TOP5和领跌BOTTOM5。
        用于全市场行业强弱横向对比。
        """
        return dataflow.get_industry_sector_performance(days)

    @staticmethod
    @tool
    def get_sector_fund_flow(
        days: Annotated[int, "获取天数，默认5"] = 5,
    ) -> str:
        """
        获取行业板块主力资金净流入/流出排名。
        用于判断资金在各行业板块间的流向和偏好。
        """
        return dataflow.get_sector_fund_flow(days)

    @staticmethod
    @tool
    def get_concept_board_heat(
        days: Annotated[int, "获取天数，默认10"] = 10,
    ) -> str:
        """
        获取热门概念板块热度排名（涨幅+成交额综合排序）。
        用于判断当前市场热点概念及其持续性。
        """
        return dataflow.get_concept_board_heat(days)

    @staticmethod
    @tool
    def get_sector_technical_screening(
        days: Annotated[int, "计算天数，默认60"] = 60,
    ) -> str:
        """
        逐行业计算技术指标（均线排列、RSI、MACD、量比），输出全行业技术状态矩阵。
        分析对象是行业板块指数的日K线（OHLCV），覆盖约30个申万一级行业。
        返回每个行业的技术状态（强势/偏强/中性/偏弱/弱势）和异动信号。
        """
        return dataflow.get_sector_technical_screening(days)

    @staticmethod
    @tool
    def get_sector_relative_strength(
        days: Annotated[int, "计算天数，默认20"] = 20,
    ) -> str:
        """
        计算各行业相对大盘（上证综指）的 alpha 排名。
        正 alpha = 跑赢大盘，负 alpha = 跑输大盘。
        用于识别真正强势/弱势的行业。
        """
        return dataflow.get_sector_relative_strength(days)

    @staticmethod
    @tool
    def get_industry_policy_news(
        curr_date: Annotated[str, "当前日期 YYYY-mm-dd"],
    ) -> str:
        """
        获取近期产业政策/重大行业新闻（一期占位）。
        当前无免费行业政策聚合接口，Agent 应靠 LLM 训练知识做方向性判断。
        """
        return dataflow.get_industry_policy_news(curr_date)

    # ==================== 市场层 — Global 工具 ====================

    @staticmethod
    @tool
    def get_global_macro_news(
        curr_date: Annotated[str, "当前日期 YYYY-mm-dd"],
    ) -> str:
        """
        获取全球宏观财经新闻（财联社电报）。
        用于扫描地缘政治冲突、战乱、重大事件等黑天鹅信号。
        """
        return dataflow.get_global_macro_news(curr_date)

    @staticmethod
    @tool
    def get_central_bank_calendar(
        curr_date: Annotated[str, "当前日期 YYYY-mm-dd"],
    ) -> str:
        """
        获取主要央行（美联储/中国人民银行/欧央行）利率决议日历与历史决议记录。
        用于判断全球流动性/利率政策方向。
        """
        return dataflow.get_central_bank_calendar(curr_date)

    @staticmethod
    @tool
    def get_macro_indicators(
        curr_date: Annotated[str, "当前日期 YYYY-mm-dd"],
    ) -> str:
        """
        获取关键宏观经济指标（美国CPI/非农/就业、中国PMI/社融等）最新值与发布日历。
        用于判断宏观经济基本面方向。
        """
        return dataflow.get_macro_indicators(curr_date)

    @staticmethod
    @tool
    def get_commodity_fx_overview(
        days: Annotated[int, "获取天数，默认10"] = 10,
    ) -> str:
        """
        获取大宗商品（原油、黄金）和汇率（美元指数）近期概览。
        用于分析大宗商品/汇率联动对A股风格的传导。
        """
        return dataflow.get_commodity_fx_overview(days)

    @staticmethod
    @tool
    def get_event_calendar_history(
        events_desc: Annotated[str, "事件描述，用于检索相似历史案例"],
    ) -> str:
        """
        检索历史案例日历表，返回与当前事件最相似的已验证历史案例。
        用于事件类比和置信度判断。
        """
        return dataflow.get_event_calendar_history(events_desc)

    # ==================== 市场层 — US 工具 ====================

    @staticmethod
    @tool
    def get_us_macro_news(
        curr_date: Annotated[str, "当前日期 YYYY-mm-dd"],
    ) -> str:
        """
        获取美国财经新闻（一期占位）。
        TODO: AKShare 美股聚合新闻接口
        """
        return dataflow.get_us_macro_news(curr_date)

    @staticmethod
    @tool
    def get_us_economic_calendar(
        curr_date: Annotated[str, "当前日期 YYYY-mm-dd"],
    ) -> str:
        """
        获取美国经济数据发布日历（CPI/非农/GDP等，含公布值/预期/前值）。
        基于 AKShare news_economic_baidu 按地区过滤。
        """
        return dataflow.get_us_economic_calendar(curr_date)

    @staticmethod
    @tool
    def get_vix_index() -> str:
        """
        获取 VIX 恐慌指数当前值与近期走势。
        用于判断美股市场恐慌程度。
        """
        return dataflow.get_vix_index()

    @staticmethod
    @tool
    def get_us_index_data(
        days: Annotated[int, "获取天数，默认20"] = 20,
    ) -> str:
        """
        获取美股三大指数（标普500/纳斯达克/道琼斯）近期日线数据。
        用于美股技术面分析。
        """
        return dataflow.get_us_index_data(days)

    @staticmethod
    @tool
    def get_us_sector_rotation(
        days: Annotated[int, "获取天数，默认20"] = 20,
    ) -> str:
        """
        获取美股板块轮动数据（一期占位）。
        TODO: 无免费美股板块轮动数据源
        """
        return dataflow.get_us_sector_rotation(days)

    # ==================== 市场层 — KR 工具 ====================

    @staticmethod
    @tool
    def get_kr_macro_news(
        curr_date: Annotated[str, "当前日期 YYYY-mm-dd"],
    ) -> str:
        """
        获取韩国财经新闻（一期占位）。
        TODO: AKShare 无韩国新闻覆盖
        """
        return dataflow.get_kr_macro_news(curr_date)

    @staticmethod
    @tool
    def get_kr_export_data(
        curr_date: Annotated[str, "当前日期 YYYY-mm-dd"],
    ) -> str:
        """
        获取韩国出口数据（一期占位）。
        TODO: 无免费韩国出口数据接口
        """
        return dataflow.get_kr_export_data(curr_date)

    @staticmethod
    @tool
    def get_kr_foreign_flow(
        days: Annotated[int, "获取天数，默认10"] = 10,
    ) -> str:
        """
        获取韩国市场外资流向数据（一期占位）。
        TODO: 无免费韩国外资流向接口
        """
        return dataflow.get_kr_foreign_flow(days)

    # ==================== 市场层 — CN 工具 ====================

    @staticmethod
    @tool
    def get_ipo_calendar(
        curr_date: Annotated[str, "当前日期 YYYY-mm-dd"],
    ) -> str:
        """
        获取近期新股申购/上市日历，标注大市值新股。
        用于识别打新资金抽血/分流效应。
        """
        return dataflow.get_ipo_calendar(curr_date)

    @staticmethod
    @tool
    def get_share_unlock_calendar(
        curr_date: Annotated[str, "当前日期 YYYY-mm-dd"],
    ) -> str:
        """
        获取限售股解禁日历与解禁市值规模。
        用于评估潜在抛压来源。
        """
        return dataflow.get_share_unlock_calendar(curr_date)

    @staticmethod
    @tool
    def get_futures_expiry_calendar(
        curr_date: Annotated[str, "当前日期 YYYY-mm-dd"],
    ) -> str:
        """
        获取股指期货/期权交割日、行权日日历。
        用于识别'到期日效应'临近时点，警惕尾盘异常波动。
        """
        return dataflow.get_futures_expiry_calendar(curr_date)

    @staticmethod
    @tool
    def get_margin_trading_balance(
        curr_date: Annotated[str, "当前日期 YYYY-mm-dd"],
    ) -> str:
        """
        获取融资融券余额变化（沪市+深市合并）。
        用于判断杠杆资金松紧程度。
        """
        return dataflow.get_margin_trading_balance(curr_date)

    @staticmethod
    @tool
    def get_market_breadth(
        curr_date: Annotated[str, "当前日期 YYYY-mm-dd"],
    ) -> str:
        """
        获取市场宽度数据（涨跌家数、涨停/跌停家数）。
        用于衡量市场情绪温度——赚钱效应和极端情绪信号。
        """
        return dataflow.get_market_breadth(curr_date)

    @staticmethod
    @tool
    def get_market_fund_flow(
        curr_date: Annotated[str, "当前日期 YYYY-mm-dd"],
    ) -> str:
        """
        获取北向资金（沪股通+深股通）和主力资金净流入数据。
        注意：北向资金自 2024-08-16 起仅披露日终汇总，无盘中实时。
        """
        return dataflow.get_market_fund_flow(curr_date)


# ==================== 市场层上下文组装 ====================

def build_market_layer_context(state) -> str:
    """组装市场层 7 份宏观报告，供下游节点增量注入（空字段安全）"""
    parts = []
    for key, label in (
        ("international_news_report", "国际金融市场新闻"),
        ("us_news_report", "美国市场新闻"),
        ("us_tech_report", "美国市场技术"),
        ("kr_news_report", "韩国市场新闻"),
        ("kr_tech_report", "韩国市场技术"),
        ("cn_news_report", "中国市场新闻（资金日历）"),
        ("cn_tech_report", "中国市场技术（大盘情绪）"),
    ):
        val = state.get(key, "")
        if val and len(val) > 20:
            parts.append(f"## {label}\n{val}")
    return "\n\n".join(parts) if parts else ""
