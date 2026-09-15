"""db.instrument.ingest — 统一采集（provider 工厂注入，零 AI/backend 依赖）。

daily_job/market_ingest wrapper 调用时传入 provider 工厂；
CLI 入口在函数内延迟 import AI.dataflows.providers.cn（按 LIVEPROFIT_DATA_SOURCE 选源）。
"""
