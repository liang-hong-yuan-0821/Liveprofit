"""
全市场日线本地库（store 包，方案：docs/plans/全市场日线本地库方案.md）

本地 PostgreSQL 落地全市场 A 股 + 场内基金（ETF/LOF）近 10 年日线 + 复权因子 +
基本信息 + 概念成分映射（多来源），供横截面/选股/回测消费。

模块：
- db.py             连接 + init_store_schema()（六张新表，public schema）
- stock_daily_dao.py  DAO：批量写入 / 查询 / 前复权 / 概念双向查询
- concepts.py       概念体系多来源采集（ths/dc），backfill 与 incremental 共用
- backfill.py       历史回填（一次性，断点续跑，python -m AI.dataflows.store.backfill）
- incremental.py    每日增量（daily_job 步骤 store 调用）
- schema.sql        六张表 DDL（幂等）
"""
