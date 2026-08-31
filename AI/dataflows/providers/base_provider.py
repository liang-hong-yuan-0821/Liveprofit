"""
LiveProfit 基础数据提供器
定义完整的数据接口契约，子类（AKShareProvider / TushareProvider / 未来新增）
按需覆写。未覆写的方法自动返回"不支持"占位。

接口约定（新增 Provider 时参考）：
- 返回类型统一为 str（格式化 Markdown 文本），仅 get_stock_info 返回 dict
- 不可用时返回 "数据不可用: <原因>" 格式，不抛异常
- 方法签名（参数名、默认值、返回类型）必须与基类完全一致
"""

import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class BaseStockDataProvider(ABC):
    """股票数据提供器抽象基类 — 定义完整数据接口"""

    # ---- 子类可覆写的类属性 ----
    GLOBAL_TECH_INDICES: dict = {}
    AI_INDUSTRY_CHAIN: dict = {}
    A_SHARE_CONCEPT_MAP: dict = {}

    def __init__(self, name: str):
        self.name = name
        self.connected = False

    def _not_supported(self, feature: str = "") -> str:
        """统一的"不支持"返回格式"""
        msg = f"数据不可用：{self.name} 不支持"
        return f"{msg} {feature}。" if feature else f"{msg}此功能。"

    # ==================== 股票行情（必须实现） ====================

    @abstractmethod
    def get_stock_data(self, code: str, start_date: str, end_date: str) -> str:
        """获取股票日线行情数据"""
        ...

    @abstractmethod
    def get_stock_info(self, code: str) -> dict:
        """获取股票基本信息 → {"name", "industry", "area", "list_date"}"""
        ...

    # ==================== 基本面 & 新闻 ====================

    def get_fundamentals(self, code: str, curr_date: str = None) -> str:
        """获取财务指标数据"""
        return self._not_supported("基本面数据")

    def get_news(self, code: str, start_date: str, end_date: str) -> str:
        """获取个股相关新闻"""
        return self._not_supported("个股新闻")

    # ==================== 大盘指数 ====================

    def get_index_data(self, index_codes: str, start_date: str, end_date: str) -> str:
        """获取指数行情数据（如上证综指、深证成指）"""
        return self._not_supported("指数行情")

    def get_global_index(self, index_key: str, days: int = 10) -> str:
        """获取全球指数（含海外 + A 股科技指数）"""
        return self._not_supported("全球指数")

    def get_all_tech_indices(self, days: int = 10) -> str:
        """获取所有科技指数数据"""
        results = []
        for key in self.GLOBAL_TECH_INDICES:
            data = self.get_global_index(key, days)
            results.append(data)
            results.append("")
        return "\n".join(results) if results else self._not_supported("科技指数")

    def _fetch_global_index(self, ak_key: str, start: str, end: str):
        """内部方法：拉取单个全球指数的原始 DataFrame（子类覆写）"""
        return None

    # ==================== 市场层 — 宏观 ====================

    def get_global_macro_news(self, curr_date: str) -> str:
        """获取全球宏观快讯（财联社电报等）"""
        return self._not_supported("全球宏观新闻")

    def get_central_bank_calendar(self, curr_date: str) -> str:
        """获取主要央行利率决议日历"""
        return self._not_supported("央行利率日历")

    def get_macro_indicators(self, curr_date: str) -> str:
        """获取关键宏观经济指标（CPI/PMI/社融等）"""
        return self._not_supported("宏观指标")

    def get_commodity_fx_overview(self, days: int = 10) -> str:
        """获取大宗商品+汇率概览"""
        return self._not_supported("大宗商品与汇率")

    def get_us_economic_calendar(self, curr_date: str) -> str:
        """获取美国经济数据发布日历"""
        return self._not_supported("美国经济日历")

    def get_vix_index(self) -> str:
        """获取 VIX 恐慌指数"""
        return self._not_supported("VIX 恐慌指数")

    def get_us_index_data(self, days: int = 20) -> str:
        """获取美股三大指数日线"""
        return self._not_supported("美股指数")

    # ==================== 市场层 — A 股资金 & 情绪 ====================

    def get_ipo_calendar(self, curr_date: str) -> str:
        """获取 IPO 新股发行日历"""
        return self._not_supported("IPO 日历")

    def get_share_unlock_calendar(self, curr_date: str) -> str:
        """获取限售股解禁日历"""
        return self._not_supported("限售股解禁")

    def get_margin_trading_balance(self, curr_date: str) -> str:
        """获取融资融券余额"""
        return self._not_supported("融资融券")

    def get_market_breadth(self, curr_date: str) -> str:
        """获取市场宽度（涨跌家数统计）"""
        return self._not_supported("市场宽度")

    def get_market_fund_flow(self, curr_date: str) -> str:
        """获取全市场资金流向"""
        return self._not_supported("资金流向")

    # ==================== 板块层 ====================

    def get_sector_fund_flow_rank(self, days: int = 5) -> str:
        """获取行业资金流向排名"""
        return self._not_supported("行业资金流向")

    # ==================== 板块层 — 概念/行业数据 ====================

    def get_concept_board(self, concept_name: str, days: int = 10) -> str:
        """获取单个 A 股概念板块行情数据 → Markdown 表格"""
        return self._not_supported("概念板块数据")

    def get_all_concept_boards(self, days: int = 10) -> str:
        """获取 AI 产业链全部概念板块数据（连续 2 个失败则终止剩余请求）"""
        return self._not_supported("概念板块汇总")

    def get_industry_sector_performance(self, days: int = 10) -> str:
        """获取全行业板块涨跌排名（TOP/BOTTOM），输出含近10个交易日逐日涨跌幅矩阵"""
        return self._not_supported("行业板块表现")

    def get_concept_board_heat_rank(self, days: int = 10) -> str:
        """获取热门概念板块热度排名（涨幅+成交额综合排序），输出含近10个交易日逐日涨跌幅矩阵"""
        return self._not_supported("板块热度排名")

    def get_sector_technical_screening(self, days: int = 60) -> str:
        """逐行业技术指标矩阵（均线排列/RSI/MACD/量比）"""
        return self._not_supported("行业技术筛选")

    def get_sector_relative_strength(self, days: int = 20) -> str:
        """各行业相对大盘 alpha 排名"""
        return self._not_supported("行业相对强度")

    def get_concept_rotation_ranking(self, days: int = 5, top_n: int = 10) -> str:
        """获取近 N 个交易日题材板块轮动矩阵（Tushare 打板专题数据）。
        含逐日热度排名、涨停家数、连板家数/高度，
        用于识别持续主线/新晋异动/退潮规律。
        仅 Tushare 数据源支持（AKShare 无对等的打板专题数据接口）。
        """
        return self._not_supported("题材板块轮动矩阵（仅 Tushare 支持 limit_cpt_list）")

    def get_concept_daily_top_gains(self, days: int = 10, top_n: int = 20) -> str:
        """获取近 N 个交易日东财概念板块逐日涨幅 TOP 矩阵（含换手率 + 跨日上榜统计）。

        逐日回退采集 dc_index 按日快照，用于识别主线持续性/新热点扩散。
        仅 Tushare 数据源支持（AKShare 概念接口仅当日快照，无历史逐日横截面）。
        """
        return self._not_supported("逐日概念涨幅TOP20（仅 Tushare 支持 dc_index 按日快照）")

    def get_limit_up_ladder(self, days: int = 20) -> str:
        """获取近 N 个交易日全市场连板梯队与情绪数据。

        每日涨停/跌停/炸板家数 + 连板分档 + 晋级率/炸板率矩阵，
        用于判断市场情绪周期位置（冰点/修复/高潮/退潮）。
        仅 Tushare 数据源支持（limit_list_d 单接口覆盖涨停/炸板/跌停三口径）。
        """
        return self._not_supported("连板梯队（仅 Tushare 支持 limit_list_d）")

    def get_industry_daily_returns_matrix(self, days: int = 10):
        """行业近 N 个交易日逐日涨跌幅结构化矩阵（热力图数据源）。

        返回 dict {"source", "dates", "names", "pct_matrix"}：dates 升序（YYYYMMDD，
        最长 10）、names 与 pct_matrix 行序对应、缺失格为 None。
        结构化接口约定：不支持/失败时返回 None（不返回 _not_supported() 的 str）。
        """
        return None

    def get_concept_daily_returns_matrix(self, days: int = 10, top_n: int = 30):
        """概念板块近 N 个交易日逐日涨跌幅结构化矩阵（热力图数据源，热度 TOP N 同口径）。

        返回结构与 get_industry_daily_returns_matrix 相同（names 为概念名）。
        结构化接口约定：不支持/失败时返回 None。
        """
        return None

    # ==================== 板块层 — 选股层数据（东财概念体系） ====================

    def get_concept_board_names(self) -> str:
        """获取东财概念板块全名单（每行一个概念名，供结构化清单过滤）"""
        return self._not_supported("概念板块名单")

    def get_sector_constituents(self, sector_name: str) -> str:
        """获取东财概念板块当日成分股 → `代码|名称`（每行一条，6 位代码）"""
        return self._not_supported("板块成分股")

    def get_stocks_performance_ranking(self, codes: list, days: int = 10) -> str:
        """批量计算近 N 日涨跌幅 + 最新价/最新成交额。

        返回 `代码|近N日涨幅%|最新价|最新成交额`（每行一条），
        末行为 `板块均值|X.XX`。名称由选股层从成分股行关联。
        连续 3 只失败熔断。
        """
        return self._not_supported("个股涨幅排名")

    # ==================== 每日指标 ====================

    def get_daily_basic(self, code: str, trade_date: str) -> str:
        """获取个股每日基础指标（PE/PB/市值/换手率等）"""
        return self._not_supported("每日指标")

    # ==================== 事件研究系统 — 结构化接口 ====================
    # 与上方 str 接口不同，以下接口返回结构化数据（DataFrame/dict），
    # 供事件研究系统（AI/eventStudy）使用，不经展示层格式化。
    # 不支持时返回 None / {}（调用方检查后降级），不抛异常。

    def get_index_data_df(self, index_code: str, start_date: str, end_date: str):
        """获取指数日线结构化行情 → pandas.DataFrame。

        与展示用 get_index_data 的区别：返回原始 DataFrame（含开高低收、
        成交量、成交额），且分页/limit 由子类内部处理，保证完整区间数据。
        标准列：trade_date / open / high / low / close / vol / amount。
        不支持时返回 None。
        """
        logger.warning("数据不可用：%s 不支持 结构化指数行情。", self.name)
        return None

    def get_trade_cal(self, start_date: str, end_date: str, market: str = "CN"):
        """获取交易日历 → pandas.DataFrame（列：trade_date, is_open）。

        用于事件研究系统 t0 对齐（盘前/盘中/盘后/非交易日 → t0 规则）。
        market 预留多市场扩展（CN/US/KR），V1 仅 CN。
        不支持时返回 None。
        """
        logger.warning("数据不可用：%s 不支持 交易日历（market=%s）。", self.name, market)
        return None

    def get_macro_context(self, date: str, market: str = "CN") -> dict:
        """获取指定日期宏观环境指标 → dict（如 {"rate_10y": 2.34}）。

        供市场环境快照（market_context）计算使用：10 年期国债收益率等。
        market 预留多市场扩展，V1 仅 CN。
        不支持或获取失败时返回空 dict（指标置空，不阻塞）。
        """
        logger.warning("数据不可用：%s 不支持 宏观环境指标（market=%s）。", self.name, market)
        return {}

    # ==================== 全市场日线本地库（store）— 结构化接口 ====================
    # 供 AI/dataflows/store（本地库回填/增量/DAO）消费，返回 DataFrame。
    # 结构化接口约定（规则 8）：默认返回 None（不返回 _not_supported() 的 str，
    # 避免破坏 DataFrame 消费方）；不支持/失败时返回 None。

    def get_full_market_daily_df(self, trade_date: str, market: str = "stock"):
        """单交易日全市场日线（结构化 DataFrame）。
        market: "stock"=股票 daily 接口 / "fund"=场内基金 fund_daily 接口。
        trade_date: YYYYMMDD。不支持或失败返回 None。"""
        return None

    def get_full_market_factor_df(self, trade_date: str, market: str = "stock"):
        """单交易日全市场复权因子（adj_factor / fund_adj）。同上。"""
        return None

    def get_stock_basic_df(self):
        """股票基本信息全量（含退市，含 area 地域）。返回 None 表示不支持/失败。"""
        return None

    def get_fund_basic_df(self):
        """场内基金基本信息全量（fund_basic market='E'）。返回 None 表示不支持/失败。"""
        return None

    def get_concept_list_df(self, source: str = "ths"):
        """概念列表（多来源）。source: "ths"=同花顺 / "dc"=东方财富。返回 None 表示不支持/失败。"""
        return None

    def get_concept_members_df(self, concept_code: str, source: str = "ths",
                               trade_date: str = None):
        """单个概念的全部成分（按概念代码过滤，多来源）。trade_date 仅 dc 来源需要（快照式，
        调用方传最近交易日）；ths 来源忽略该参数。返回 None 表示不支持/失败。"""
        return None
