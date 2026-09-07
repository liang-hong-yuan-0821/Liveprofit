"""
LiveProfit 数据提供器层

- base_provider.py: BaseStockDataProvider 接口契约基类（跨数据源/跨市场）
- cn/: A 股数据提供器子包
    - tushare.py: TushareProvider（Tushare Pro API，需 Token，走代理端点）
    - akshare.py: AKShareProvider（开源免费，无需 Token）
    - daily_matrix_utils.py / limit_ladder_utils.py: 板块逐日矩阵 / 连板梯队纯函数模块
"""
