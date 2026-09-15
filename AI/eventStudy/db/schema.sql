-- ============================================================
-- 事件研究系统数据库 Schema（PostgreSQL 16 + pgvector）
-- 依据 docs/requirements/archive/事件研究方案.md 最终 ER 图
--
-- 约定：
--   - 全部表不设外键（决策 7），关联由应用层保证
--   - PG 只存正式/已处理数据；待审核草稿存 Redis（决策 10）
--   - event_impacts 只含人工确认过的正式记录（B5）
--   - market_data 主键 (asset_id, ts) 含分区列，满足未来
--     TimescaleDB create_hypertable 迁移前提
-- ============================================================

CREATE EXTENSION IF NOT EXISTS vector;

-- ---------- 资产表 ----------
CREATE TABLE IF NOT EXISTS assets (
    asset_id    BIGSERIAL PRIMARY KEY,
    ticker      VARCHAR(32)  NOT NULL,          -- 交易代码，如 000001.SH
    name        VARCHAR(64)  NOT NULL,          -- 资产名称，如上证指数
    asset_class VARCHAR(32)  NOT NULL DEFAULT '指数',  -- 资产类别
    market      VARCHAR(8)   NOT NULL DEFAULT 'CN',    -- 市场（CN/US/KR）
    UNIQUE (ticker)
);

-- ---------- 事件表（仅存人工处理过的事件：approved / ignored） ----------
CREATE TABLE IF NOT EXISTS events (
    event_id        BIGSERIAL PRIMARY KEY,
    title           TEXT NOT NULL,
    content         TEXT,                        -- 事件原文/摘要
    event_type      VARCHAR(64),                 -- 宏观/央行/地缘等（人工审核）
    event_subtype   VARCHAR(64),                 -- CPI、LPR、降准等（人工审核）
    event_condition VARCHAR(32),                 -- 超预期/符合预期/低于预期等（人工审核）
    announced_at    TIMESTAMPTZ NOT NULL,        -- 公布时间
    trading_day     DATE,                        -- 事件对齐后的交易日 t0（影响标注模块写入）
    expected_value  NUMERIC,                     -- 预期值（可选）
    actual_value    NUMERIC,                     -- 实际值
    previous_value  NUMERIC,                     -- 前值
    surprise        NUMERIC,                     -- 预期差（实际-预期，可为空；影响标注模块写入）
    importance      SMALLINT NOT NULL DEFAULT 3, -- 重要性 1-5
    status          VARCHAR(16) NOT NULL,        -- approved / ignored
    embedding       VECTOR(1024),                -- bge-m3 文本向量
    source_url      TEXT,                        -- 来源链接（爬虫）
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_events_status ON events (status);
CREATE INDEX IF NOT EXISTS idx_events_announced_at ON events (announced_at);
CREATE INDEX IF NOT EXISTS idx_events_trading_day ON events (trading_day);
-- 事件量数千条级暂不建 HNSW 索引（3.7.2），数万条后再建

-- ---------- 事件路由字段（三级事件路由，2026-09-11） ----------
-- 依据 docs/requirements/archive/市场层证据驱动分析与三级事件路由改造方案.md 第三章：
--   event_scope          最细作用层级：market / sector / stock，单事件只属一层
--   affected_scope_refs  层内目标引用数组（market 固定 []）：
--                          行业 SW:<6 位申万一级代码>（如 SW:801080）
--                          概念 CONCEPT:<东财 dc 代码>（如 CONCEPT:BK1753.DC）
--                          个股 stock:<6 位代码.SH|SZ|BJ>（如 stock:600519.SH）
-- 物理列首期允许 NULL（兼容历史行）；写入/读取层统一归一化为 market + []。
-- 迁移为确定性步骤：不含 LLM、可重复执行（旧事件不批量 AI 回标，未经审核
-- 确认回标的历史行只在市场层可见）。
ALTER TABLE events ADD COLUMN IF NOT EXISTS event_scope VARCHAR(16);
ALTER TABLE events ADD COLUMN IF NOT EXISTS affected_scope_refs JSONB;

-- 幂等归一：历史行（含首次迁移前的全部旧事件）落 market + []，只在市场层可见
UPDATE events SET event_scope = 'market' WHERE event_scope IS NULL;
UPDATE events SET affected_scope_refs = '[]'::jsonb WHERE affected_scope_refs IS NULL;

-- 路由查询索引：(作用域, 公布时点) B-tree 支撑 status/时点/作用域过滤，
-- GIN 支撑 sector/stock 的 JSONB 数组查询（affected_scope_refs ?| ARRAY[...]，
-- 命中任一目标引用即属该路由；单目标时与 @> 包含查询等价）
CREATE INDEX IF NOT EXISTS idx_events_scope_announced ON events (event_scope, announced_at);
CREATE INDEX IF NOT EXISTS idx_events_scope_refs ON events USING GIN (affected_scope_refs);

-- ---------- 事件影响表（仅存人工确认的正式记录） ----------
CREATE TABLE IF NOT EXISTS event_impacts (
    impact_id                   BIGSERIAL PRIMARY KEY,
    event_id                    BIGINT NOT NULL,
    asset_id                    BIGINT NOT NULL,
    window_type                 VARCHAR(32) NOT NULL,  -- pre_event_5d / event_day / post_event_5d
    window_days                 INTEGER NOT NULL,      -- 窗口长度（交易日数）
    cumulative_abnormal_return  NUMERIC NOT NULL,      -- CAR
    t_stat                      NUMERIC,               -- CAR 的 t 统计量
    direction                   SMALLINT NOT NULL DEFAULT 0,  -- 1 利好 / -1 利空 / 0 中性
    is_contaminated             BOOLEAN NOT NULL DEFAULT FALSE, -- 被其他重大事件污染
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (event_id, asset_id, window_type)
);

CREATE INDEX IF NOT EXISTS idx_event_impacts_event ON event_impacts (event_id);
CREATE INDEX IF NOT EXISTS idx_event_impacts_asset ON event_impacts (asset_id);

-- 幂等保护：同一事件 × 资产 × 窗口仅一条正式记录（confirm_impacts ON CONFLICT 依赖）。
-- 注意：PostgreSQL ADD CONSTRAINT 不支持 IF NOT EXISTS，已存在的表用 DO 块条件迁移；
-- 新建表直接在表定义内带该约束（见上方 UNIQUE 行）。
-- 判断按"列组合"而非约束名：全新库的内联约束为自动命名，按名字查会重复建约束。
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'event_impacts'::regclass AND contype = 'u'
          AND conkey = (
              SELECT array_agg(attnum ORDER BY attnum)
              FROM pg_attribute
              WHERE attrelid = 'event_impacts'::regclass
                AND attname IN ('event_id', 'asset_id', 'window_type')
          )
    ) THEN
        ALTER TABLE event_impacts
            ADD CONSTRAINT uq_event_impacts_event_asset_window
            UNIQUE (event_id, asset_id, window_type);
    END IF;
END $$;

-- ---------- 市场环境快照表（一行 = 某日全部市场） ----------
CREATE TABLE IF NOT EXISTS market_context (
    ts      TIMESTAMPTZ PRIMARY KEY,             -- 日期（日频）
    context JSONB NOT NULL                       -- {"CN": {"return_20d":…, "vol_20d":…, "amount_20d":…, "rate_10y":…}, "US": {…}}
);

-- ---------- 预测表（仅显式保存 / 离线回测时写入） ----------
CREATE TABLE IF NOT EXISTS predictions (
    prediction_id        BIGSERIAL PRIMARY KEY,
    event_id             BIGINT,                 -- 关联事件（显式保存时；回测可为 NULL）
    asset_id             BIGINT NOT NULL,
    window_type          VARCHAR(32) NOT NULL,
    predicted_direction  SMALLINT NOT NULL,
    predicted_return     NUMERIC NOT NULL,
    confidence           NUMERIC NOT NULL,
    similar_event_ids    JSONB,                  -- 相似事件 ID 列表
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_predictions_event ON predictions (event_id);

-- ---------- 初始化资产（V1：4 个目标指数） ----------
INSERT INTO assets (ticker, name, asset_class, market) VALUES
    ('000001.SH', '上证指数', '指数', 'CN'),
    ('000688.SH', '科创50',   '指数', 'CN'),
    ('000698.SH', '科创100',  '指数', 'CN'),
    ('000300.SH', '沪深300',  '指数', 'CN')
ON CONFLICT (ticker) DO NOTHING;
