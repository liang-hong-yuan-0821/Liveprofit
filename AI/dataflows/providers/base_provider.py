"""
YoHo 基础数据提供器
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
        """获取全行业板块涨跌排名（TOP/BOTTOM）"""
        return self._not_supported("行业板块表现")

    def get_concept_board_heat_rank(self, days: int = 10) -> str:
        """获取热门概念板块热度排名（涨幅+成交额综合排序）"""
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

    # ==================== 每日指标 ====================

    def get_daily_basic(self, code: str, trade_date: str) -> str:
        """获取个股每日基础指标（PE/PB/市值/换手率等）"""
        return self._not_supported("每日指标")
