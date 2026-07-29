"""
YoHo 数据接口层
支持可配置数据源切换：tushare 或 akshare。
通过环境变量 YOHO_DATA_SOURCE 控制（默认 tushare）。
集成缓存层：Redis > MongoDB > File 三级缓存。
"""

import os
import logging

logger = logging.getLogger(__name__)

# 全局数据源选择
_DATA_SOURCE = None
_provider = None
_cache = None


def _get_cache():
    """懒加载缓存管理器"""
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


def set_config(config: dict):
    """设置配置"""
    pass


def switch_data_source(source: str):
    """切换数据源 (tushare / akshare)"""
    global _DATA_SOURCE, _provider
    _DATA_SOURCE = source.lower()
    _provider = None
    logger.info(f"数据源已切换为: {_DATA_SOURCE}")


# ==================== 股票行情 ====================

def get_china_stock_data(ticker: str, start_date: str, end_date: str) -> str:
    """获取A股日线行情（带缓存）"""
    cache = _get_cache()
    if cache:
        cached = cache.load_stock_data(ticker, data_source=_get_data_source(),
                                        start_date=start_date, end_date=end_date)
        if cached:
            return cached
    data = _get_provider().get_stock_data(ticker, start_date, end_date)
    if cache and data and not data.startswith("Tushare") and not data.startswith("AKShare"):
        cache.save_stock_data(ticker, data, start_date=start_date,
                              end_date=end_date, data_source=_get_data_source())
    return data


def get_china_stock_info(ticker: str) -> str:
    info = _get_provider().get_stock_info(ticker)
    return (
        f"股票代码: {ticker}\n"
        f"股票名称: {info.get('name', '')}\n"
        f"所属行业: {info.get('industry', '')}\n"
        f"所属地区: {info.get('area', '')}\n"
        f"上市日期: {info.get('list_date', '')}"
    )


# ==================== 基本面 ====================

def get_china_fundamentals(ticker: str, curr_date: str = None) -> str:
    """获取基本面（带缓存）"""
    cache = _get_cache()
    if cache:
        cached = cache.load_fundamentals_data(ticker, data_source=_get_data_source())
        if cached:
            return cached
    data = _get_provider().get_fundamentals(ticker, curr_date)
    if cache and data and not data.startswith("Tushare") and not data.startswith("AKShare"):
        cache.save_fundamentals_data(ticker, data, data_source=_get_data_source())
    return data


# ==================== 新闻 ====================

def get_china_news(ticker: str, start_date: str, end_date: str) -> str:
    """获取新闻（带缓存）"""
    cache = _get_cache()
    if cache:
        cached = cache.load_news_data(ticker, data_source=_get_data_source())
        if cached:
            return cached
    data = _get_provider().get_news(ticker, start_date, end_date)
    if cache and data and not data.startswith("Tushare") and not data.startswith("AKShare"):
        cache.save_news_data(ticker, data, data_source=_get_data_source())
    return data


# ==================== 大盘 ====================

def get_china_market_overview(curr_date: str) -> str:
    from datetime import datetime, timedelta
    end_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    start_dt = end_dt - timedelta(days=7)
    start_date = start_dt.strftime("%Y-%m-%d")
    return _get_provider().get_index_data(
        "000001.SH,399001.SZ,399006.SZ", start_date, curr_date
    )


def get_china_daily_basic(ticker: str, trade_date: str) -> str:
    return _get_provider().get_daily_basic(ticker, trade_date)


# ==================== 全球科技指数 (AKShare) ====================

def get_global_tech_index(index_key: str, days: int = 10) -> str:
    """获取全球科技指数数据"""
    prov = _get_provider()
    if hasattr(prov, 'get_global_index'):
        return prov.get_global_index(index_key, days)
    return "当前数据源不支持全球指数，请切换到 akshare。"


def get_all_tech_indices(days: int = 10) -> str:
    """获取所有全球科技指数"""
    prov = _get_provider()
    if hasattr(prov, 'get_all_tech_indices'):
        return prov.get_all_tech_indices(days)
    return "当前数据源不支持全球指数，请切换到 akshare。"


def get_concept_board(concept_name: str, days: int = 10) -> str:
    """获取 A 股概念板块数据"""
    from .providers.akshare_provider import get_concept_board_data
    return get_concept_board_data(concept_name, days)


def get_all_concept_boards(days: int = 10) -> str:
    """获取所有 AI 产业链概念板块"""
    from .providers.akshare_provider import get_all_concept_boards
    return get_all_concept_boards(days)


def analyze_tech_correlation(days: int = 10) -> str:
    """分析科技指数相关性并预测"""
    from .providers.akshare_provider import (
        calculate_correlation, predict_tomorrow_trend
    )
    # 收集所有科技指数数据
    prov = _get_provider()
    if not hasattr(prov, 'get_global_index'):
        return "当前数据源不支持全球指数分析，请切换到 akshare。"

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

    # 也加入概念板块
    from .providers.akshare_provider import get_concept_board_data
    for concept_name in prov.A_SHARE_CONCEPT_MAP:
        try:
            board_data = get_concept_board_data(concept_name, days)
            hist_data[concept_name] = board_data
        except Exception:
            continue

    for key in prov.GLOBAL_TECH_INDICES:
        try:
            data = prov.get_global_index(key, days)
            hist_data[prov.GLOBAL_TECH_INDICES[key][0]] = data
        except Exception:
            continue

    corr_text = calculate_correlation(dataframes)
    pred_text = predict_tomorrow_trend(hist_data)

    return f"{corr_text}\n\n{pred_text}"
