"""
YoHo 数据接口层
统一数据访问入口，支持 tushare / akshare 双数据源切换，
集成 Redis > File 二级缓存。
"""

from .interface import (
    get_china_stock_data,
    get_china_stock_info,
    get_china_fundamentals,
    get_china_news,
    get_china_market_overview,
    get_china_daily_basic,
    get_global_tech_index,
    get_all_tech_indices,
    get_concept_board,
    get_all_concept_boards,
    analyze_tech_correlation,
    # 板块层
    get_industry_sector_performance,
    get_sector_fund_flow,
    get_concept_board_heat,
    get_sector_technical_screening,
    get_sector_relative_strength,
    get_industry_policy_news,
    get_concept_rotation_ranking,
    switch_data_source,
    set_config,
)
