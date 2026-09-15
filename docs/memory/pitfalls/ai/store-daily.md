# 全市场日线本地库（store 包）踩坑

> 一句话结论：store 六表（public schema）是旧实现，2026-09-13 已随证券市场数据库统一方案删除——统一实现为 `db/instrument/`（market schema）；本文件的写入/事务/回填约定已原样迁入 db/instrument 并继续有效。

## 位置与表（2026-08-30，已迁移）

- 原 `AI/dataflows/store/` 六表（public schema）已删；新落点 `db/instrument/`（market schema）：instrument_daily / adj_factor / sector / sector_member 等 11 表。写入工具在 `db/instrument/dao/_common.py`、采集在 `db/instrument/ingest/`。

## 分类约定（已废弃）

- 原 `stock_daily` / `adj_factor` 靠 `is_fund_ts_code()` 前缀函数分类——前缀函数已退役：统一显式列 `instrument.instrument_type`（index/stock/fund，源表即类型）；横截面分类过滤走 EXISTS 子查询按 instrument_type 过滤（db/instrument/dao/instrument_daily.py get_cross_section）。

## 写入约定

- 批量写入 COPY → 临时表（表名带 pid+随机后缀）→ `INSERT ... ON CONFLICT`；清洗顺序 close NaN 行显式 drop（close 列 NOT NULL）→ 其余 NaN→None（**必须先转 object dtype**——float64 列上 `where(cond, None)` 会把 None 压回 NaN）；幂等策略由调用方控制（决策 5）：回填 `DO NOTHING`、增量最近 3 交易日 `DO UPDATE`（覆盖 tushare 日终修正）。

## 实测踩坑（2026-08-30 集成验证）

1. **`CREATE TEMP TABLE (LIKE 表)` 默认不复制 DEFAULT 表达式** → 带 `DEFAULT now()` 的 NOT NULL 列（updated_at）在 COPY 时被填 NULL 违例；必须写 `LIKE ... INCLUDING DEFAULTS`。
2. **pandas `Series.apply` 的 dtype 推断不可靠**：单行且结果均匀时推 int64、多行含 None 时把 int 整体压回 float64（单测会骗过）——整数列转换（如 concept.count，tushare 返回 float 300.0）必须用显式 `dtype=object` 的列表推导，否则 COPY 报 `invalid input syntax for type integer`。
3. **批量写入的 SQL 级失败会使事务 abort**，下一段 DB 写入报 "current transaction is aborted"——按来源/批次隔离的采集循环中，每段失败分支必须 rollback 恢复干净状态，且**成功段必须独立 commit**（否则后段失败的回滚把前段成果一并清空，实测 dc 失败清空 ths 899 概念）。
4. **概念成分响应含重复 con_code** 时，同一 INSERT 批次提出两行相同 PK 会报 `ON CONFLICT DO UPDATE cannot affect row a second time`——Provider 归一化与 DAO 双保险 drop_duplicates。

## 事务约定

- 单日提交（库内无"半截日"）；概念采集按来源独立提交、基本信息刷新独立提交（逐日失败 rollback 不得静默丢弃 35 分钟概念采集）；commit 失败（事务可能已 abort）需 rollback 恢复干净状态再继续。

## 回填与增量（入口已迁）

- 回填：`python -m db.instrument.ingest.backfill [--start 2016-01-01] [--end 今天] [--retry-missing] [--skip-concepts]`；断点续跑按库内**已入库交易日集合**跳过（存在即完整，"完整日才入库"不变式）——**禁用 max(trade_date) 截断**：库内只有尾部几日时会把全部历史误判跳过（2026-08-30 实测踩坑）；`--skip-concepts` 在板块数据已新鲜时跳过重采（省 ~35 分钟）；失败清单 `logs/stock_backfill_failures.json`（temp+rename 原子写）。
- 增量：daily_job 步骤 2（`--skip` 名 `market`，原步骤 2/3 合并），最近 3 交易日窗口；板块体系周一自动周刷（`collect_incremental(refresh_sectors=None/True/False)`）；akshare 数据源下结构化方法返回 None → 记 warning 跳过不阻断。
- 实现详见归档方案：docs/requirements/archive/全市场日线本地库方案.md 与 docs/requirements/archive/证券市场数据库统一方案.md
