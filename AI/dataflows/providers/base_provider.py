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
        标准列（2026-09-13 扩列）：trade_date / open / high / low / close /
        pre_close / change / pct_chg / vol / amount（tushare 存上游原值、
        AKShare 三列恒 NaN）。
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

    # ==================== 技术因子 — 结构化接口 ====================
    # 技术指标不自算（2026-09-12 决策）：指标值一律取自 Tushare 因子端点
    # （idx_factor_pro 指数 / stk_factor_pro 个股），本地不实现任何指标算法。
    # 结构化接口约定（规则 8）：默认返回 None，不支持/失败时返回 None。

    def get_index_factor_df(self, index_code: str, start_date: str, end_date: str, fields: str | None = None):
        """指数每日技术面因子 → pandas.DataFrame（idx_factor_pro）。

        列：trade_date（YYYY-MM-DD str，升序）+ 子类覆写时决定的因子列。
        分页/limit 由子类内部处理，保证完整区间数据。
        契约：返回 DataFrame 必须携带 attrs["missing_chunks"] = 真实缺段数
        （分页段失败/与数据跨度重叠的空段；基日前合法空段不计 0）——
        消费方据此拒绝部分入库，防止缺段静默导致区间指标全 null。
        不支持时返回 None。
        """
        logger.warning("数据不可用：%s 不支持 指数技术因子（idx_factor_pro）。", self.name)
        return None

    def get_stock_factor_df(self, ts_code: str, start_date: str, end_date: str, fields: str | None = None):
        """个股每日技术面因子 → pandas.DataFrame（stk_factor_pro）。

        列：trade_date（YYYY-MM-DD str，升序）+ 子类覆写时决定的因子列。
        不支持时返回 None。
        """
        logger.warning("数据不可用：%s 不支持 个股技术因子（stk_factor_pro）。", self.name)
        return None

    # ==================== 证券市场数据库（db.instrument ingest）— 结构化接口 ====================
    # 供 db.instrument.ingest（统一采集回填/增量）消费，返回 DataFrame。
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

    def get_sector_daily_df(self, source: str, ts_code: str, start_date: str,
                            end_date: str):
        """单板块指数日线（板块概念Treemap方案 3.1：sector_daily 采集数据源）。

        source 参数化：'dc'=dc_daily 端点（窗口型——仅最近 33 交易日，更早区间 0 行，
        实测 2026-09-13）；'ths'=ths_daily 端点（全历史单请求，能力已实测、暂缓采集）。
        日期参数格式 YYYYMMDD（tushare 端点硬要求；YYYY-MM-DD 的转换由采集函数入口
        完成）。返回 DataFrame（升序）或 None 表示不支持/失败。
        """
        return None

    # ==================== 市场特征层 — 结构化接口（T5） ====================
    # 供 AI/dataflows/market_features.py 消费（方案第四、十二章程结构化接口例外组）。
    # 契约（规则 8）：返回结构化 dict/dict；不支持或失败返回 None（绝不返回
    # _not_supported() 的 str，避免破坏结构化消费方）。
    # 通用约定：
    #   - 所有序列按 trade_date 升序，日期为 YYYY-MM-DD，且已按 curr_date 截断
    #     （晚于 curr_date 的数据不得返回）；
    #   - 每个 dict 含 as_of_date（实际数据截止日）、missing（{名称: 原因}）与 notes；
    #   - 字段缺失/接口不可用时把该分组放入 missing 并降级，不抛异常。

    def get_market_index_features(self, curr_date: str,
                                  lookbacks=(5, 20, 60, 120, 250)):
        """7 指数完整窗口 OHLCV → dict（宽基趋势/风格特征输入）。

        Returns:
            {"as_of_date", "requested_date", "source", "lookbacks",
             "indices": {code: {"trade_dates", "open", "high", "low", "close",
                                "vol", "amount", "rows", "first_date", "last_date"}},
             "missing": {code: 原因}, "notes": [...]}
            序列保留最近 max(lookbacks)+5 个交易日（升序）。
            不支持/失败返回 None。
        """
        logger.warning("数据不可用：%s 不支持 指数特征序列（结构化接口）。", self.name)
        return None

    def get_market_breadth_history(self, curr_date: str, days: int = 20):
        """市场宽度 + 高标情绪序列 → dict（短线判据组：宽度趋势/高标情绪）。

        Returns:
            {"as_of_date", "days",
             "series": [{"trade_date", "up", "down", "flat", "total", "up_ratio",
                         "limit_up", "limit_down", "broken_board_rate", "max_board",
                         "promotion_rate", "premium_rate", "market_amount"}, ...],
             "missing": {名称: 原因}, "notes": [...]}
            缺失字段为 None（缺失不影响其余字段）。不支持/失败返回 None。
        """
        logger.warning("数据不可用：%s 不支持 市场宽度序列（结构化接口）。", self.name)
        return None

    def get_market_fund_flow_history(self, curr_date: str, days: int = 20):
        """主力/北向资金序列 → dict（短线判据组：成交额/资金趋势）。

        Returns:
            {"as_of_date", "days",
             "series": [{"trade_date", "main_net_amount", "northbound_net"}, ...],
             "missing": {名称: 原因}, "notes": [...]}
            main_net_amount 单位万元（全市场净流入合计）；北向停更/无权限时置 None
            并写入 missing。不支持/失败返回 None。
        """
        logger.warning("数据不可用：%s 不支持 资金流序列（结构化接口）。", self.name)
        return None

    def get_margin_trading_history(self, curr_date: str, days: int = 20):
        """两融余额历史序列 → dict（资金 1/5/20 日变化）。

        Returns:
            {"as_of_date", "days",
             "series": [{"trade_date", "rzye", "rqye", "rows"}, ...],
             "missing": {...}, "notes": [...]}
            rzye/rqye 单位为元（交易所合计）。不支持/失败返回 None。
        """
        logger.warning("数据不可用：%s 不支持 两融历史序列（结构化接口）。", self.name)
        return None

    def get_market_valuation(self, curr_date: str, years: int = 5):
        """指数估值历史分位 + 全 A 快照 → dict（长线判据组：估值）。

        Returns:
            {"as_of_date", "years",
             "index_valuation": {code: {"trade_dates", "pe_ttm", "pb",
                                        "first_date", "last_date"}},
             "all_a_snapshot": {"trade_date", "pe_ttm_median", "pb_median", "rows"},
             "missing": {...}, "notes": [...]}
            分位由特征层计算（本接口只回历史序列）。不支持/失败返回 None。
        """
        logger.warning("数据不可用：%s 不支持 市场估值序列（结构化接口）。", self.name)
        return None

    def get_cn_liquidity_indicators(self, curr_date: str, days: int = 20):
        """利率/流动性指标 → dict（长线判据组：流动性）。

        Returns:
            {"as_of_date",
             "shibor": {"trade_dates", "on", "1w", "1m", "3m", "1y"},
             "lpr": {"trade_dates", "1y", "5y"},
             "money_supply": {"months", "m1_yoy", "m2_yoy", "m1_mom", "m2_mom"},
             "missing": {...}, "notes": [...]}
            无接口的能力（10Y 国债、DR007 等）写入 missing 并说明替代口径。
            不支持/失败返回 None。
        """
        logger.warning("数据不可用：%s 不支持 中国流动性指标（结构化接口）。", self.name)
        return None

    def get_cn_event_calendar(self, curr_date: str, windows=(5, 20, 60)):
        """资金日历（IPO/解禁/交割/长假）→ dict（CN News 资金压力）。

        Returns:
            {"as_of_date", "windows",
             "ipo": [{"ts_code", "name", "subscribe_date", "list_date", "price",
                      "market_amount", "market"}],
             "unlocks": [{"ts_code", "name", "float_date", "float_share",
                          "float_ratio"}],
             "expiry": [{"date", "kind"}],
             "holiday_windows": [{"start", "end", "days"}],
             "macro_releases": [...], "missing": {...}, "notes": [...]}
            金额缺失置 None（不可评估，不臆造）；不支持/失败返回 None。
        """
        logger.warning("数据不可用：%s 不支持 资金日历（结构化接口）。", self.name)
        return None

    def get_global_risk_indicators(self, curr_date: str, days: int = 20):
        """全球风险价格序列 → dict（阶段 3 特征组，global_risk_assessment 证据）。

        Returns:
            {"as_of_date", "days",
             "us_treasury": {code: {"trade_dates", "field", <field>: [...],
                                    "source", "unit", "note"}},
             "us_real_yield": {...}, "us_long_rate": {...},
             "global_indices": {...}, "fx": {...}, "commodities": {...},
             "missing": {...}, "notes": [...]}
            无对应代码的能力（VIX/SOX/美元指数等）写入 missing 并在 notes 说明
            替代口径（已实现波动率、USDCNH、国内商品价）。不支持/失败返回 None。
        """
        logger.warning("数据不可用：%s 不支持 全球风险价格序列（结构化接口）。", self.name)
        return None
