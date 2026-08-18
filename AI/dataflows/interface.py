"""
YoHo 数据接口层
支持可配置数据源切换：tushare 或 akshare。
通过环境变量 YOHO_DATA_SOURCE 控制（默认 tushare）。
集成缓存层：Redis > File 二级缓存。
"""

import os
import logging

from AI.utils.dataprovider_log import dataprovider_log

logger = logging.getLogger(__name__)

# 全局数据源选择
_DATA_SOURCE = None
_provider = None
_cache = None
# AKShare 独立实例（国际宏观类数据固定走 AKShare，不随 YOHO_DATA_SOURCE 切换）
_akshare_provider = None


def _correct_trade_date(curr_date: str, caller: str = "") -> str:
    """防御性日期校正：将非交易日/盘中交易日校正为实际可用数据日期。

    在 interface 层做二次校验，防止绕过 State 层的直接调用。
    如果传入的 curr_date 已为有效日期，不做修改。

    Args:
        curr_date: 原始日期，格式 YYYY-MM-DD
        caller: 调用方标识（用于日志）

    Returns:
        校正后的日期，格式 YYYY-MM-DD
    """
    if not curr_date:
        return curr_date
    try:
        from .utils.trading_calendar import get_available_trade_date
        corrected = get_available_trade_date(curr_date)
        if corrected != curr_date:
            logger.warning(
                f"[interface{':' + caller if caller else ''}] "
                f"日期自动校正: {curr_date} → {corrected}"
            )
        return corrected
    except Exception as e:
        logger.debug(f"日期校正跳过（交易日历不可用）: {e}")
        return curr_date


def _get_cache():
    """懒加载缓存管理器，首次调用时初始化"""
    global _cache
    if _cache is None:
        try:
            from .cache import get_cache
            _cache = get_cache()
        except Exception as e:
            logger.warning(f"缓存初始化失败: {e}")
            _cache = None
    return _cache


def _get_data_source() -> str:
    """获取当前数据源名称"""
    global _DATA_SOURCE
    if _DATA_SOURCE is None:
        _DATA_SOURCE = os.getenv("YOHO_DATA_SOURCE", "tushare").lower()
    return _DATA_SOURCE


def _get_provider():
    """懒加载初始化当前选择的数据提供器"""
    global _provider, _DATA_SOURCE
    ds = _get_data_source()

    # 数据源变更时重置 provider
    if _provider is None or _DATA_SOURCE != getattr(_provider, '_source_name', None):
        if ds == "akshare":
            from .providers.akshare_provider import AKShareProvider
            _provider = AKShareProvider()
            _provider._source_name = "akshare"
        else:
            from .providers.tushare_provider import TushareProvider
            _provider = TushareProvider()
            _provider._source_name = "tushare"
        logger.info(f"数据源: {ds}")

    return _provider


def _get_akshare_provider():
    """懒加载 AKShare 数据提供器（独立于 YOHO_DATA_SOURCE）。

    国际宏观类数据（全球宏观新闻/央行日历/宏观指标/大宗商品与汇率）
    Tushare 不提供，接口层固定路由到 AKShare。
    """
    global _akshare_provider
    if _akshare_provider is None:
        try:
            from .providers.akshare_provider import AKShareProvider
            _akshare_provider = AKShareProvider()
        except Exception as e:
            logger.warning(f"AKShare provider 初始化失败: {e}")
    return _akshare_provider


def set_config(config: dict):
    """设置配置（预留扩展点）"""
    pass


def switch_data_source(source: str):
    """切换数据源 (tushare / akshare)"""
    global _DATA_SOURCE, _provider
    _DATA_SOURCE = source.lower()
    _provider = None  # 强制下次重新初始化
    logger.info(f"数据源已切换为: {_DATA_SOURCE}")


# ==================== 股票行情 ====================

@dataprovider_log
def get_china_stock_data(ticker: str, start_date: str, end_date: str) -> str:
    """获取A股日线行情（带缓存）"""
    # 先查缓存
    cache = _get_cache()
    if cache:
        cached = cache.load_stock_data(ticker, data_source=_get_data_source(),
                                        start_date=start_date, end_date=end_date)
        if cached:
            return cached

    # 缓存未命中，从数据源获取
    data = _get_provider().get_stock_data(ticker, start_date, end_date)

    # 写入缓存（排除数据源返回的错误信息）
    if cache and data and not data.startswith("Tushare") and not data.startswith("AKShare"):
        cache.save_stock_data(ticker, data, start_date=start_date,
                              end_date=end_date, data_source=_get_data_source())
    return data


@dataprovider_log
def get_china_stock_info(ticker: str) -> str:
    """获取股票基本信息（公司名称、行业、地区、上市日期）"""
    info = _get_provider().get_stock_info(ticker)
    return (
        f"股票代码: {ticker}\n"
        f"股票名称: {info.get('name', '')}\n"
        f"所属行业: {info.get('industry', '')}\n"
        f"所属地区: {info.get('area', '')}\n"
        f"上市日期: {info.get('list_date', '')}"
    )


# ==================== 基本面 ====================

@dataprovider_log
def get_china_fundamentals(ticker: str, curr_date: str = None) -> str:
    """获取基本面（带缓存）"""
    # 先查缓存
    cache = _get_cache()
    if cache:
        cached = cache.load_fundamentals_data(ticker, data_source=_get_data_source())
        if cached:
            return cached

    # 缓存未命中，从数据源获取
    data = _get_provider().get_fundamentals(ticker, curr_date)

    # 写入缓存
    if cache and data and not data.startswith("Tushare") and not data.startswith("AKShare"):
        cache.save_fundamentals_data(ticker, data, data_source=_get_data_source())
    return data


# ==================== 新闻 ====================

@dataprovider_log
def get_china_news(ticker: str, start_date: str, end_date: str) -> str:
    """获取新闻（带缓存）"""
    # 先查缓存
    cache = _get_cache()
    if cache:
        cached = cache.load_news_data(ticker, data_source=_get_data_source())
        if cached:
            return cached

    # 缓存未命中，从数据源获取
    data = _get_provider().get_news(ticker, start_date, end_date)

    # 写入缓存
    if cache and data and not data.startswith("Tushare") and not data.startswith("AKShare"):
        cache.save_news_data(ticker, data, data_source=_get_data_source())
    return data


# ==================== 大盘 ====================

@dataprovider_log
def get_china_market_overview(curr_date: str, days: int = 7) -> str:
    """获取七大指数近期走势（上证综指/深证成指/创业板指/科创50/上证50/中证1000/上证红利）

    Args:
        curr_date: 分析日期，格式 YYYY-MM-DD
        days: 回看天数，默认 7。CN Tech 长周期分析用 days=120

    注意：非交易日或盘中交易日会被自动校正为上一个可用交易日。
    """
    curr_date = _correct_trade_date(curr_date, caller="get_china_market_overview")
    from datetime import datetime, timedelta
    end_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    start_dt = end_dt - timedelta(days=days)
    start_date = start_dt.strftime("%Y-%m-%d")
    return _get_provider().get_index_data(
        "000001.SH,399001.SZ,399006.SZ,000688.SH,000016.SH,000852.SH,000015.SH",
        start_date, curr_date
    )


@dataprovider_log
def get_china_daily_basic(ticker: str, trade_date: str) -> str:
    """获取每日指标 (PE, PB, 换手率, 总市值等)"""
    prov = _get_provider()
    if hasattr(prov, 'get_daily_basic'):
        return prov.get_daily_basic(ticker, trade_date)
    return "当前数据源不支持每日指标数据。"


# ==================== 全球科技指数 (AKShare) ====================

@dataprovider_log
def get_global_tech_index(index_key: str, days: int = 10) -> str:
    """获取全球科技指数数据"""
    prov = _get_provider()
    if hasattr(prov, 'get_global_index'):
        return prov.get_global_index(index_key, days)
    return "当前数据源不支持全球指数，请切换到 akshare。"


@dataprovider_log
def get_all_tech_indices(days: int = 10) -> str:
    """获取所有全球科技指数"""
    prov = _get_provider()
    if hasattr(prov, 'get_all_tech_indices'):
        return prov.get_all_tech_indices(days)
    return "当前数据源不支持全球指数，请切换到 akshare。"


@dataprovider_log
def get_concept_board(concept_name: str, days: int = 10) -> str:
    """获取 A 股概念板块数据"""
    prov = _get_provider()
    if hasattr(prov, 'get_concept_board'):
        return prov.get_concept_board(concept_name, days)
    return "当前数据源不支持概念板块数据，请切换到 akshare。"


@dataprovider_log
def get_all_concept_boards(days: int = 10) -> str:
    """获取所有 AI 产业链概念板块"""
    prov = _get_provider()
    if hasattr(prov, 'get_all_concept_boards'):
        return prov.get_all_concept_boards(days)
    return "当前数据源不支持概念板块汇总，请切换到 akshare。"


@dataprovider_log
def analyze_tech_correlation(days: int = 10) -> str:
    """分析科技指数相关性并预测"""
    from .providers.akshare_provider import (
        calculate_correlation, predict_tomorrow_trend
    )

    # 检查当前数据源是否支持全球指数
    prov = _get_provider()
    if not hasattr(prov, 'get_global_index'):
        return "当前数据源不支持全球指数分析，请切换到 akshare。"

    # 第一步：拉取各指数的原始 DataFrame，用于计算相关性矩阵
    hist_data = {}
    dataframes = {}
    for key in prov.GLOBAL_TECH_INDICES:
        name, ak_key = prov.GLOBAL_TECH_INDICES[key]
        try:
            end = (__import__('datetime').datetime.now()).strftime("%Y%m%d")
            start = (__import__('datetime').datetime.now() -
                     __import__('datetime').timedelta(days=days*2)).strftime("%Y%m%d")
            df = prov._fetch_global_index(ak_key, start, end)
            if df is not None and not df.empty:
                dataframes[name] = df.tail(days)
        except Exception:
            continue

    # 第二步：拉取 A 股概念板块数据（连续 2 个失败则终止）
    if hasattr(prov, 'get_concept_board'):
        concept_failures = 0
        for concept_name in prov.A_SHARE_CONCEPT_MAP:
            try:
                board_data = prov.get_concept_board(concept_name, days)
                hist_data[concept_name] = board_data
                if "失败" in str(board_data):
                    concept_failures += 1
                else:
                    concept_failures = 0
            except Exception:
                concept_failures += 1
            if concept_failures >= 2:
                break

    # 第三步：拉取格式化文本数据，用于趋势预测
    for key in prov.GLOBAL_TECH_INDICES:
        try:
            data = prov.get_global_index(key, days)
            hist_data[prov.GLOBAL_TECH_INDICES[key][0]] = data
        except Exception:
            continue

    # 第四步：计算相关性 + 趋势预测
    corr_text = calculate_correlation(dataframes)
    pred_text = predict_tomorrow_trend(hist_data)

    return f"{corr_text}\n\n{pred_text}"


# ==================== 板块层 — Sector 接口 ====================


@dataprovider_log
def get_industry_sector_performance(days: int = 10) -> str:
    """获取全行业板块涨跌排名"""
    prov = _get_provider()
    if hasattr(prov, 'get_industry_sector_performance'):
        return prov.get_industry_sector_performance(days)
    return "当前数据源不支持行业板块表现，请切换到 akshare。"


@dataprovider_log
def get_sector_fund_flow(days: int = 5) -> str:
    """获取行业板块主力资金流向排名"""
    prov = _get_provider()
    if hasattr(prov, 'get_sector_fund_flow_rank'):
        return prov.get_sector_fund_flow_rank(days)
    return "当前数据源不支持行业资金流向，请切换到 akshare。"


@dataprovider_log
def get_concept_board_heat(days: int = 10) -> str:
    """获取热门概念板块热度排名（涨幅+成交额综合排序）"""
    prov = _get_provider()
    if hasattr(prov, 'get_concept_board_heat_rank'):
        return prov.get_concept_board_heat_rank(days)
    return "当前数据源不支持板块热度排名，请切换到 akshare。"


# ==================== 板块层 — 选股层数据（东财概念体系） ====================


@dataprovider_log
def get_concept_board_names() -> str:
    """获取东财概念板块全名单（每行一个概念名，供结构化清单过滤）"""
    prov = _get_provider()
    if hasattr(prov, 'get_concept_board_names'):
        return prov.get_concept_board_names()
    return "当前数据源不支持概念板块名单。"


@dataprovider_log
def get_sector_constituents(sector_name: str) -> str:
    """获取东财概念板块当日成分股 → `代码|名称`（每行一条，6 位代码）"""
    prov = _get_provider()
    if hasattr(prov, 'get_sector_constituents'):
        return prov.get_sector_constituents(sector_name)
    return "当前数据源不支持板块成分股。"


@dataprovider_log
def get_stocks_performance_ranking(codes: list, days: int = 10) -> str:
    """批量计算近 N 日涨跌幅 + 最新价/最新成交额（末行板块均值）"""
    prov = _get_provider()
    if hasattr(prov, 'get_stocks_performance_ranking'):
        return prov.get_stocks_performance_ranking(codes, days)
    return "当前数据源不支持个股涨幅排名。"


@dataprovider_log
def get_sector_technical_screening(days: int = 60) -> str:
    """逐行业计算技术指标，输出全行业技术状态矩阵"""
    prov = _get_provider()
    if hasattr(prov, 'get_sector_technical_screening'):
        return prov.get_sector_technical_screening(days)
    return "当前数据源不支持行业技术筛选，请切换到 akshare。"


@dataprovider_log
def get_sector_relative_strength(days: int = 20) -> str:
    """各行业相对大盘的 alpha 排名"""
    prov = _get_provider()
    if hasattr(prov, 'get_sector_relative_strength'):
        return prov.get_sector_relative_strength(days)
    return "当前数据源不支持行业相对强度，请切换到 akshare。"


@dataprovider_log
def get_concept_rotation_ranking(days: int = 5, top_n: int = 10) -> str:
    """获取近N个交易日题材板块轮动矩阵（Tushare 打板专题数据）。

    含逐日热度排名、涨停家数、连板家数/高度，
    用于识别持续主线/新晋异动/退潮规律。
    仅 Tushare 数据源支持（AKShare 无对等的打板专题数据接口）。
    """
    prov = _get_provider()
    if hasattr(prov, 'get_concept_rotation_ranking'):
        return prov.get_concept_rotation_ranking(days, top_n)
    return "当前数据源不支持板块轮动矩阵（仅 Tushare 支持 limit_cpt_list 接口）。"


@dataprovider_log
def get_industry_policy_news(curr_date: str) -> str:
    """近期产业政策/重大行业新闻（一期占位）"""
    # TODO: AKShare 无行业政策聚合新闻接口
    return "数据不可用：暂无行业政策新闻聚合数据源。"


@dataprovider_log
def get_sector_horizon_screening(days: int = 120) -> str:
    """多周期行业技术筛选：基于日线数据重采样，输出 日/周/月 三级趋势矩阵。

    纯计算组合函数，不依赖新 provider 端点。对全行业日线做 pandas resample，
    计算各行业在日线/周线/月线三个周期上的趋势状态，输出多级别共振判定。

    Args:
        days: 回看天数，默认 120（约 6 个月日线，覆盖周线/月线重采样）

    Returns:
        Markdown 表格：行业 × 日/周/月三级趋势状态矩阵
    """
    import pandas as pd
    from datetime import datetime, timedelta

    prov = _get_provider()
    if not hasattr(prov, 'get_industry_sector_performance'):
        return "数据不可用：当前数据源不支持行业板块数据，无法做多周期筛选。"

    # 拉取行业日线原始数据（复用 provider 的行业数据能力）
    try:
        raw = prov.get_industry_sector_performance(days)
    except Exception as e:
        return f"数据不可用：行业数据获取失败 ({e})"

    if not raw or raw.startswith("数据不可用") or raw.startswith("当前数据源"):
        return raw

    # 尝试从 provider 获取原始的每行业日线 DataFrame 做 resample
    # 如果 provider 有 _get_industry_daily_dataframes 方法则直接使用，
    # 否则基于 get_industry_sector_performance 返回的文本做降级处理
    if hasattr(prov, '_get_industry_daily_dataframes'):
        try:
            dfs = prov._get_industry_daily_dataframes(days)
        except Exception:
            dfs = None
    else:
        dfs = None

    if dfs and len(dfs) > 0:
        lines = ["# 全行业多周期技术矩阵（日/周/月）\n"]
        lines.append(f"| 行业 | 日线趋势 | 周线趋势 | 月线趋势 | 周RSI | 20日涨幅 | 距60日高 | 共振判定 |")
        lines.append("|------|---------|---------|---------|-------|---------|---------|---------|")

        end_date = datetime.now()
        for industry_name, df in dfs.items():
            if df is None or df.empty:
                continue
            try:
                df = df.copy()
                df['date'] = pd.to_datetime(df['date'] if 'date' in df.columns else df.index)
                df = df.set_index('date').sort_index()
                close = df['close'].astype(float)

                # 日线趋势（MA5/MA20 关系）
                ma5_d = close.rolling(5).mean().iloc[-1]
                ma20_d = close.rolling(20).mean().iloc[-1]
                daily_trend = "↑多头" if ma5_d > ma20_d else ("↓空头" if ma5_d < ma20_d else "→整理")

                # 周线重采样
                weekly = close.resample('W').last()
                if len(weekly) >= 10:
                    ma5_w = weekly.rolling(5).mean().iloc[-1]
                    ma10_w = weekly.rolling(10).mean().iloc[-1]
                    weekly_trend = "↑多头" if ma5_w > ma10_w else ("↓空头" if ma5_w < ma10_w else "→整理")
                    # 周 RSI(14)
                    delta_w = weekly.diff()
                    gain_w = delta_w.clip(lower=0).rolling(14).mean().iloc[-1]
                    loss_w = (-delta_w.clip(upper=0)).rolling(14).mean().iloc[-1]
                    rsi_w = round(100 - 100 / (1 + gain_w / loss_w), 1) if loss_w != 0 else 50.0
                else:
                    weekly_trend = "数据不足"
                    rsi_w = "N/A"

                # 月线重采样
                monthly = close.resample('ME').last()
                if len(monthly) >= 5:
                    ma5_m = monthly.rolling(5).mean().iloc[-1]
                    ma20_m = monthly.rolling(20).mean().iloc[-1]
                    monthly_trend = "↑多头" if ma5_m > ma20_m else ("↓空头" if ma5_m < ma20_m else "→整理")
                else:
                    monthly_trend = "数据不足"

                # 20日涨幅
                pct_20d = round((close.iloc[-1] / close.iloc[-20] - 1) * 100, 2) if len(close) >= 20 else 0

                # 距60日高点回撤
                if len(close) >= 60:
                    high_60 = close.iloc[-60:].max()
                    drawdown = round((close.iloc[-1] / high_60 - 1) * 100, 2)
                else:
                    drawdown = 0

                # 共振判定
                trends = [daily_trend, weekly_trend, monthly_trend]
                up_count = sum(1 for t in trends if '↑' in str(t))
                if up_count == 3:
                    resonance = "★★★ 三线共振"
                elif up_count == 2:
                    resonance = "★★ 偏强"
                elif up_count == 1:
                    resonance = "★ 偏弱"
                else:
                    resonance = "空头排列"

                lines.append(
                    f"| {industry_name} | {daily_trend} | {weekly_trend} | {monthly_trend} "
                    f"| {rsi_w} | {pct_20d}% | {drawdown}% | {resonance} |"
                )
            except Exception:
                continue

        if len(lines) > 2:
            return "\n".join(lines)

    # 降级：基于 get_industry_sector_performance 的文本输出做简化版
    # 无法重采样时，直接返回行业涨跌排名 + 说明
    return (
        f"{raw}\n\n"
        f"> ⚠️ 多周期技术矩阵（日/周/月）暂不可用："
        f"当前数据源不支持行业板块日线 DataFrame 导出，"
        f"仅能提供日线级别的涨跌排名。周线/月线趋势请参考上述日线排名 + 持续性判断。"
    )


# ==================== 市场层 — Global 接口 ====================
# Tushare 不提供国际宏观类数据，以下函数固定走 AKShare，
# 与全局 YOHO_DATA_SOURCE 配置无关（其余接口仍按全局数据源路由）。

@dataprovider_log
def get_global_macro_news(curr_date: str) -> str:
    """获取全球宏观财经新闻（财联社电报）"""
    prov = _get_akshare_provider()
    if prov is not None and hasattr(prov, 'get_global_macro_news'):
        return prov.get_global_macro_news(curr_date)
    return "数据不可用：AKShare 数据源不可用，无法获取全球宏观新闻。"


@dataprovider_log
def get_central_bank_calendar(curr_date: str) -> str:
    """获取主要央行利率决议日历"""
    prov = _get_akshare_provider()
    if prov is not None and hasattr(prov, 'get_central_bank_calendar'):
        return prov.get_central_bank_calendar(curr_date)
    return "数据不可用：AKShare 数据源不可用，无法获取央行日历。"


@dataprovider_log
def get_macro_indicators(curr_date: str) -> str:
    """获取关键宏观经济指标最新值"""
    prov = _get_akshare_provider()
    if prov is not None and hasattr(prov, 'get_macro_indicators'):
        return prov.get_macro_indicators(curr_date)
    return "数据不可用：AKShare 数据源不可用，无法获取宏观指标。"


@dataprovider_log
def get_commodity_fx_overview(days: int = 10) -> str:
    """获取大宗商品+汇率概览"""
    prov = _get_akshare_provider()
    if prov is not None and hasattr(prov, 'get_commodity_fx_overview'):
        return prov.get_commodity_fx_overview(days)
    return "数据不可用：AKShare 数据源不可用，无法获取大宗商品数据。"


@dataprovider_log
def get_event_calendar_history(events_desc: str) -> str:
    """检索历史案例日历表"""
    import json
    import os
    hist_path = os.path.join(os.path.dirname(__file__), "data", "historical_cases.json")
    if not os.path.exists(hist_path):
        return "历史案例库文件不存在。"

    try:
        with open(hist_path, "r", encoding="utf-8") as f:
            cases = json.load(f)
    except Exception as e:
        return f"历史案例库加载失败: {e}"

    if not cases:
        return "历史案例库为空。"

    # 简单关键词匹配打分
    keywords = set(events_desc.lower().split())
    scored = []
    for c in cases:
        text = (c.get("event_desc", "") + " " + c.get("event_type", "") + " " +
                " ".join(c.get("affected_sectors", []))).lower()
        score = sum(1 for kw in keywords if kw in text)
        if score > 0:
            scored.append((score, c))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:3]

    if not top:
        return "未找到与当前事件匹配的历史案例。"

    lines = ["# 历史案例类比结果\n"]
    for i, (score, case) in enumerate(top, 1):
        lines.append(f"## 案例 {i}（匹配分: {score}）")
        lines.append(f"- 日期: {case.get('date', 'N/A')}")
        lines.append(f"- 事件类型: {case.get('event_type', 'N/A')}")
        lines.append(f"- 事件描述: {case.get('event_desc', 'N/A')}")
        lines.append(f"- 传导链条: {case.get('transmission_chain', 'N/A')}")
        lines.append(f"- 受影响行业: {', '.join(case.get('affected_sectors', []))}")
        lines.append(f"- 市场反应: {case.get('market_impact', 'N/A')}")
        lines.append(f"- 系统性风险: {case.get('systemic_risk', 'N/A')}")
        lines.append("")
    return "\n".join(lines)


# ==================== 市场层 — US 接口 ====================

@dataprovider_log
def get_us_macro_news(curr_date: str) -> str:
    """获取美国财经新闻（一期占位）"""
    # TODO: AKShare 美股聚合新闻接口
    return "数据不可用：暂无美股聚合新闻数据源。"


@dataprovider_log
def get_us_economic_calendar(curr_date: str) -> str:
    """获取美国经济数据发布日历"""
    prov = _get_provider()
    if hasattr(prov, 'get_us_economic_calendar'):
        return prov.get_us_economic_calendar(curr_date)
    return "数据不可用：当前数据源不支持美国经济日历，请切换到 akshare。"


@dataprovider_log
def get_vix_index() -> str:
    """获取 VIX 恐慌指数"""
    prov = _get_provider()
    if hasattr(prov, 'get_vix_index'):
        return prov.get_vix_index()
    return "数据不可用：当前数据源不支持 VIX 指数，请切换到 akshare。"


@dataprovider_log
def get_us_index_data(days: int = 20) -> str:
    """获取美股三大指数近期日线"""
    prov = _get_provider()
    if hasattr(prov, 'get_us_index_data'):
        return prov.get_us_index_data(days)
    return "数据不可用：当前数据源不支持美股指数数据，请切换到 akshare。"


@dataprovider_log
def get_us_sector_rotation(days: int = 20) -> str:
    """获取美股板块轮动数据（一期占位）"""
    # TODO: 无免费美股板块轮动数据源
    return "数据不可用：暂无美股板块轮动数据源。"


# ==================== 市场层 — KR 接口 ====================

@dataprovider_log
def get_kr_macro_news(curr_date: str) -> str:
    """获取韩国财经新闻（一期占位）"""
    # TODO: AKShare 无韩国新闻覆盖
    return "数据不可用：暂无韩国财经新闻数据源。"


@dataprovider_log
def get_kr_export_data(curr_date: str) -> str:
    """获取韩国出口数据（一期占位）"""
    # TODO: 无免费韩国出口数据接口
    return "数据不可用：暂无韩国出口数据源。"


@dataprovider_log
def get_kr_foreign_flow(days: int = 10) -> str:
    """获取韩国市场外资流向（一期占位）"""
    # TODO: 无免费韩国外资流向接口
    return "数据不可用：暂无韩国外资流向数据源。"


# ==================== 市场层 — CN 接口 ====================

@dataprovider_log
def get_ipo_calendar(curr_date: str) -> str:
    """获取近期新股申购/上市日历"""
    prov = _get_provider()
    if hasattr(prov, 'get_ipo_calendar'):
        return prov.get_ipo_calendar(curr_date)
    return "当前数据源不支持 IPO 日历，请切换到 akshare。"


@dataprovider_log
def get_share_unlock_calendar(curr_date: str) -> str:
    """获取限售股解禁日历"""
    prov = _get_provider()
    if hasattr(prov, 'get_share_unlock_calendar'):
        return prov.get_share_unlock_calendar(curr_date)
    return "当前数据源不支持解禁日历，请切换到 akshare。"


@dataprovider_log
def get_futures_expiry_calendar(curr_date: str) -> str:
    """获取期指/期权交割日日历"""
    from datetime import datetime, timedelta
    try:
        curr = datetime.strptime(curr_date, "%Y-%m-%d")
    except ValueError:
        curr = datetime.now()

    # 期指交割日 = 每月第三个周五
    lines = ["# 期货/期权交割日\n"]
    lines.append(f"分析日期: {curr.strftime('%Y-%m-%d')}\n")

    for month_offset in range(-1, 3):
        m = curr.month + month_offset
        y = curr.year + (m - 1) // 12
        m = ((m - 1) % 12) + 1
        # 找该月第三个周五
        first_day = datetime(y, m, 1)
        first_fri = first_day + timedelta(days=(4 - first_day.weekday() + 7) % 7)
        third_fri = first_fri + timedelta(days=14)
        label = ""
        if third_fri.date() == curr.date():
            label = " ← 今天"
        elif abs((third_fri.date() - curr.date()).days) <= 3:
            label = " ← 临近"
        lines.append(f"- {third_fri.strftime('%Y-%m-%d')} (当月第三个周五){label}")

    lines.append("\n> 股指期货（IF/IH/IC/IM）交割日为每月第三个周五。")
    lines.append("> 交割日前后的交易日容易出现'到期日效应'——")
    lines.append("> 多空双方为影响结算价而增加的短线博弈盘，波动率通常放大。")
    return "\n".join(lines)


@dataprovider_log
def get_margin_trading_balance(curr_date: str) -> str:
    """获取两融余额变化"""
    prov = _get_provider()
    if hasattr(prov, 'get_margin_trading_balance'):
        return prov.get_margin_trading_balance(curr_date)
    return "当前数据源不支持两融数据，请切换到 akshare。"


@dataprovider_log
def get_market_breadth(curr_date: str) -> str:
    """获取市场宽度（涨跌家数、涨跌停统计）

    注意：非交易日或盘中交易日会被自动校正为上一个可用交易日。
    """
    curr_date = _correct_trade_date(curr_date, caller="get_market_breadth")
    prov = _get_provider()
    if hasattr(prov, 'get_market_breadth'):
        return prov.get_market_breadth(curr_date)
    return "当前数据源不支持市场宽度数据，请切换到 akshare。"


@dataprovider_log
def get_market_fund_flow(curr_date: str) -> str:
    """获取北向资金 + 主力资金流向

    注意：非交易日或盘中交易日会被自动校正为上一个可用交易日。
    """
    curr_date = _correct_trade_date(curr_date, caller="get_market_fund_flow")
    prov = _get_provider()
    if hasattr(prov, 'get_market_fund_flow'):
        return prov.get_market_fund_flow(curr_date)
    return "当前数据源不支持资金流向数据，请切换到 akshare。"
