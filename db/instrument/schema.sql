-- ============================================================
-- 证券市场数据库（market schema）——证券市场数据库统一方案 2.2
--
-- 单一事实来源：11 表 DDL + 31 行申万行业字典幂等种子。
-- 全部语句 IF NOT EXISTS / ON CONFLICT DO NOTHING，可重复执行；
-- 全部 SQL 显式 market. 前缀（不依赖 search_path——过渡窗口内
-- public 与 market 的 adj_factor 同名，裸表名写入会静默落到旧表）。
-- 命名规范见 CLAUDE.md「证券市场数据库命名规范」。
-- ============================================================

CREATE SCHEMA IF NOT EXISTS market;

-- 标的主表（股票/基金/指数统一目录，一代码一行；无 market 列——US/KR 原
-- symbol 与 CN 代码格式天然不冲突，ts_code 全局唯一）
CREATE TABLE IF NOT EXISTS market.instrument (
    ts_code         VARCHAR(16) PRIMARY KEY,    -- 统一证券代码：000001.SZ / 000001.SH / 158013.SZ / .INX
    name            VARCHAR(128) NOT NULL,      -- 名称：上证综指 / 平安银行
    instrument_type VARCHAR(16) NOT NULL,       -- index / stock / fund（源表即类型，不靠前缀函数）
    list_status     CHAR(1),                    -- L=上市 / D=退市 / P=暂停（stock_basic 口径；fund 行恒 NULL）
    list_date       DATE,                       -- 上市日期：000001.SZ → 1991-04-03
    delist_date     DATE,                       -- 退市日期（仅退市标的）
    data_source     VARCHAR(32),                -- 数据采集来源：tushare / akshare
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_instrument_type_status
    ON market.instrument (instrument_type, list_status);

-- 统一日线（指数+个股+基金同构一张表，store 决策 1；未复权原值）
CREATE TABLE IF NOT EXISTS market.instrument_daily (
    ts_code     VARCHAR(16) NOT NULL,           -- 证券代码：000001.SZ / 000001.SH
    trade_date  DATE NOT NULL,                  -- 交易日；停牌日无行（整行缺失）
    open        DOUBLE PRECISION,               -- 开盘价（元）
    high        DOUBLE PRECISION,               -- 最高价（元）
    low         DOUBLE PRECISION,               -- 最低价（元）
    close       DOUBLE PRECISION NOT NULL,      -- 收盘价（元）；close 为 NaN 的行写入前显式 drop
    pre_close   DOUBLE PRECISION,               -- 前收盘价（元）；AKShare 兜底指数行 NULL
    change      DOUBLE PRECISION,               -- 涨跌额（元）
    pct_chg     DOUBLE PRECISION,               -- 涨跌幅（%）；按上游原值存，不自算
    vol         DOUBLE PRECISION,               -- 成交量，单位手
    amount      DOUBLE PRECISION,               -- 成交额，单位千元
    source      VARCHAR(32) NOT NULL,           -- tushare / akshare（实际成功源）
    updated_at  TIMESTAMPTZ,                    -- 行最后写入时间（覆盖式写入下 = 最近一次入库）
    PRIMARY KEY (ts_code, trade_date)           -- 含日期列，未来可切 TimescaleDB/分区
);
CREATE INDEX IF NOT EXISTS idx_instrument_daily_trade_date
    ON market.instrument_daily (trade_date, ts_code);

-- 复权因子（股票 adj_factor + 基金 fund_adj 接口共用）
CREATE TABLE IF NOT EXISTS market.adj_factor (
    ts_code     VARCHAR(16) NOT NULL,           -- 证券代码：000001.SZ / 160505.SZ
    trade_date  DATE NOT NULL,                  -- 因子行数 ≥ 当日日线行数属预期（停牌标的仍更新因子）
    adj_factor  DOUBLE PRECISION NOT NULL,      -- 复权因子原值：无除权除息 = 1.0
    PRIMARY KEY (ts_code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_adj_factor_trade_date
    ON market.adj_factor (trade_date, ts_code);

-- 技术因子宽表（指数∪个股列并集；不自算原则——上游 idx_factor_pro/stk_factor_pro 原值）
CREATE TABLE IF NOT EXISTS market.factor_daily (
    ts_code         VARCHAR(16) NOT NULL,       -- 指数/个股代码
    trade_date      DATE NOT NULL,              -- 因子行自带全历史窗口（区间首根即有值）
    close           DOUBLE PRECISION,           -- 收盘价对齐自检列；stk_factor_pro 无此列（反查填充）
    ma_bfq_5        DOUBLE PRECISION,
    ma_bfq_10       DOUBLE PRECISION,
    ma_bfq_20       DOUBLE PRECISION,
    ma_bfq_60       DOUBLE PRECISION,
    ma_bfq_250      DOUBLE PRECISION,           -- 个股因子列，指数行 NULL
    boll_mid_bfq    DOUBLE PRECISION,
    boll_upper_bfq  DOUBLE PRECISION,
    boll_lower_bfq  DOUBLE PRECISION,
    macd_dif_bfq    DOUBLE PRECISION,
    macd_dea_bfq    DOUBLE PRECISION,
    macd_bfq        DOUBLE PRECISION,
    rsi_bfq_6       DOUBLE PRECISION,           -- 个股因子列，指数行 NULL
    rsi_bfq_12      DOUBLE PRECISION,           -- 个股因子列，指数行 NULL
    rsi_bfq_24      DOUBLE PRECISION,           -- 个股因子列，指数行 NULL
    updated_at      TIMESTAMPTZ,                -- 行最后写入时间
    PRIMARY KEY (ts_code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_factor_daily_trade_date
    ON market.factor_daily (trade_date, ts_code);

-- 基金信息表（fund_basic 差异列；场内基金恒 market='E' 过滤拉取）
CREATE TABLE IF NOT EXISTS market.fund_info (
    ts_code         VARCHAR(16) PRIMARY KEY,    -- 基金代码：158013.SZ（易方达国证航天航空行业ETF）
    management      VARCHAR(64),                -- 基金管理人
    custodian       VARCHAR(64),                -- 基金托管人
    trustee         VARCHAR(64),                -- 受托人（实测多数 NULL）
    fund_type       VARCHAR(32),                -- 基金类型（fund_basic 口径）：股票型/混合型/债券型
    invest_type     VARCHAR(32),                -- 投资类型（fund_basic 口径）：指数型/股票型等，部分为空
    type            VARCHAR(32),                -- 基金类型（fund_basic 口径，与 fund_type 语义重叠）
    found_date      DATE,                       -- 基金成立日期
    due_date        DATE,                       -- 基金到期日（封闭式有值，开放式 NULL）
    issue_date      DATE,                       -- 发行日期
    issue_amount    DOUBLE PRECISION,           -- 发行份额，单位亿份
    m_fee           DOUBLE PRECISION,           -- 管理费率，单位 %/年：158013.SZ = 0.15
    c_fee           DOUBLE PRECISION,           -- 托管费率，单位 %/年：158013.SZ = 0.05
    duration_year   DOUBLE PRECISION,           -- 存续期（年），开放式多为 NULL
    p_value         DOUBLE PRECISION,           -- 面值（元）：实测恒 1.0
    min_amount      DOUBLE PRECISION,           -- 起购金额：158013.SZ = 0.1
    exp_return      DOUBLE PRECISION,           -- 预期年化收益（实测多数 NULL）
    benchmark       VARCHAR(128),               -- 业绩基准描述
    status          CHAR(1),                    -- L=上市 / I=发行期 / D=终止上市
    market          CHAR(1),                    -- E=场内 / O=场外；本表恒 'E'（按 market='E' 过滤拉取）
    purc_startdate  DATE,                       -- 申购起始日
    redm_startdate  DATE,                       -- 赎回起始日
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 股票信息表（stock_basic 差异列；不含 industry 列——行业归属走申万体系）
CREATE TABLE IF NOT EXISTS market.stock_info (
    ts_code     VARCHAR(16) PRIMARY KEY,        -- 股票代码：000001.SZ
    exchange    VARCHAR(8),                     -- 交易所：SSE=上海 / SZSE=深圳 / BSE=北京
    market      VARCHAR(16),                    -- 上市板块（stock_basic 口径）：主板/创业板/科创板/北交所
    area        VARCHAR(16),                    -- 地域：深圳
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 板块字典（同花顺板块体系：概念/行业/特色三类板块，type 区分；ths/dc 多来源）
CREATE TABLE IF NOT EXISTS market.sector (
    source       VARCHAR(8) NOT NULL,           -- ths=同花顺 / dc=东方财富（与 type 正交）
    sector_code  VARCHAR(32) NOT NULL,          -- 板块代码：883300.TI（ths）/ BK1753.DC（dc）
    name         VARCHAR(64) NOT NULL,          -- 板块名：激光雷达 / 光刻胶
    type         VARCHAR(8),                    -- N=概念板块 / I=行业板块（同花顺体系）/ S=特色板块；
                                                -- 首期存量恒 'N'，'I'/'S' 增量采集后续阶段；dc 无此口径存 NULL
    count        INTEGER,                       -- 成分数（来源口径快照）；与 sector_member 行数存在滞后偏差属预期
    exchange     VARCHAR(8),                    -- 板块指数市场归属（ths_index 口径）：实测恒 'A'；dc 无此口径存 NULL
    list_date    DATE,                          -- 板块指数发布日期（ths_index 口径，以实测上游响应为准）；dc 无此口径存 NULL
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source, sector_code)
);

-- 板块成分映射（多来源，~12 万行；不存成分股名——ts_code JOIN instrument 取权威名称）
CREATE TABLE IF NOT EXISTS market.sector_member (
    source       VARCHAR(8) NOT NULL,           -- 板块数据来源（与 sector.source 对应）
    sector_code  VARCHAR(32) NOT NULL,          -- 板块代码：883300.TI / BK1753.DC
    ts_code      VARCHAR(16) NOT NULL,          -- 成分股票代码（与 instrument.ts_code 对齐）
    PRIMARY KEY (source, sector_code, ts_code)
);
CREATE INDEX IF NOT EXISTS idx_sector_member_ts
    ON market.sector_member (ts_code);

-- 板块日线（ths/dc 板块指数日行情；热度现场计算的数据底座）
CREATE TABLE IF NOT EXISTS market.sector_daily (
    source         VARCHAR(8) NOT NULL,         -- ths / dc（与 sector.source 对应）
    sector_code    VARCHAR(32) NOT NULL,        -- 板块代码：883300.TI / BK1753.DC
    trade_date     DATE NOT NULL,               -- 交易日；无数据日无行
    open           DOUBLE PRECISION,
    high           DOUBLE PRECISION,
    low            DOUBLE PRECISION,
    close          DOUBLE PRECISION NOT NULL,
    pre_close      DOUBLE PRECISION,
    change         DOUBLE PRECISION,
    pct_chg        DOUBLE PRECISION,            -- 涨跌幅（dc_daily 口径 pct_change → 统一列名）
    vol            DOUBLE PRECISION,            -- 成交量（热度计算输入）
    amount         DOUBLE PRECISION,
    turnover_rate  DOUBLE PRECISION,            -- 换手率（heat_v1 不用，留 heat_v2 候选）
    updated_at     TIMESTAMPTZ,
    PRIMARY KEY (source, sector_code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_sector_daily_trade_date
    ON market.sector_daily (trade_date, source, sector_code);

-- 行业字典（申万 SW2021 等多来源；列名对齐 tushare index_classify：
-- industry_code（去 .SI 后缀 6 位码）/ name（名称列统一裸 name）/ source）
CREATE TABLE IF NOT EXISTS market.industry (
    source        VARCHAR(16) NOT NULL,         -- 行业分类来源：SW2021（申万 2021 版一级）；未来多来源共存
    industry_code VARCHAR(16) NOT NULL,         -- 行业代码（去 .SI 后缀 6 位码）：801080 = 电子
    name          VARCHAR(32) NOT NULL,         -- 行业名称：电子
    count         INTEGER,                      -- 成分股数量；首期恒 NULL（成分采集后 COUNT 写回）
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source, industry_code)
);

-- 行业成分关系（与 sector_member 同构；首期建空表——成分采集后续阶段）
CREATE TABLE IF NOT EXISTS market.industry_member (
    source        VARCHAR(16) NOT NULL,         -- 行业分类来源（与 industry.source 对应）
    industry_code VARCHAR(16) NOT NULL,         -- 行业代码（与 industry.industry_code 对应）
    ts_code       VARCHAR(16) NOT NULL,         -- 成分股票代码（与 instrument.ts_code 对齐）
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source, industry_code, ts_code)
);
CREATE INDEX IF NOT EXISTS idx_industry_member_ts
    ON market.industry_member (ts_code);

-- ============================================================
-- 申万 SW2021 一级行业字典幂等种子（31 行）
-- 来源：tushare index_classify(src='SW2021', level='L1') 的 index_code 去 .SI 后缀。
-- 转录自 AI/eventStudy/db/industry_codes.sql（旧表 code→industry_code，name/source 直迁）。
-- 静态表：不随行情刷新，申万分类调整时同步本文件。
-- ============================================================

INSERT INTO market.industry (source, industry_code, name) VALUES
    ('SW2021', '801010', '农林牧渔'),
    ('SW2021', '801030', '基础化工'),
    ('SW2021', '801040', '钢铁'),
    ('SW2021', '801050', '有色金属'),
    ('SW2021', '801080', '电子'),
    ('SW2021', '801110', '家用电器'),
    ('SW2021', '801120', '食品饮料'),
    ('SW2021', '801130', '纺织服饰'),
    ('SW2021', '801140', '轻工制造'),
    ('SW2021', '801150', '医药生物'),
    ('SW2021', '801160', '公用事业'),
    ('SW2021', '801170', '交通运输'),
    ('SW2021', '801180', '房地产'),
    ('SW2021', '801200', '商贸零售'),
    ('SW2021', '801210', '社会服务'),
    ('SW2021', '801230', '综合'),
    ('SW2021', '801710', '建筑材料'),
    ('SW2021', '801720', '建筑装饰'),
    ('SW2021', '801730', '电力设备'),
    ('SW2021', '801740', '国防军工'),
    ('SW2021', '801750', '计算机'),
    ('SW2021', '801760', '传媒'),
    ('SW2021', '801770', '通信'),
    ('SW2021', '801780', '银行'),
    ('SW2021', '801790', '非银金融'),
    ('SW2021', '801880', '汽车'),
    ('SW2021', '801890', '机械设备'),
    ('SW2021', '801950', '煤炭'),
    ('SW2021', '801960', '石油石化'),
    ('SW2021', '801970', '环保'),
    ('SW2021', '801980', '美容护理')
ON CONFLICT (source, industry_code) DO NOTHING;
