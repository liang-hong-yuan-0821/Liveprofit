-- 全市场日线本地库（方案：docs/plans/全市场日线本地库方案.md 3.1）
--
-- 六张新表建在 liveprofit 库 public schema，与事件研究（market_data 等）同库不同表，
-- 现有表及消费方零改动。幂等（IF NOT EXISTS），由 init_store_schema() 执行。
--
-- 分类策略（决策 1）：stock_daily / adj_factor 不冗余 asset_type 列，股票与场内基金共用，
-- 分类靠前缀函数 is_fund_ts_code() 判定（股票 60/68/00/30/8/4/920 vs 基金 5(沪)/15/16/18(深)）。
-- 类型决策（决策 2）：行情列全部 DOUBLE PRECISION；分区决策（决策 3）：V1 不分区。

-- 股票基本信息（实测全量 5551 行，含退市股）
CREATE TABLE IF NOT EXISTS stock_basic (
    ts_code      VARCHAR(16) PRIMARY KEY,   -- 证券代码（后缀 .SH=沪/.SZ=深/.BJ=北）：如 000001.SZ（平安银行）、
                                            -- 600000.SH（浦发银行）、920992.BJ（北交所）
    name         VARCHAR(32) NOT NULL,      -- 证券简称：如"平安银行"
    market       VARCHAR(16),               -- 上市板块（stock_basic 口径）：主板/创业板/科创板/北交所；
                                            -- 如 000001.SZ→主板、300xxx.SZ→创业板、688xxx.SH→科创板、920xxx.BJ→北交所
    exchange     VARCHAR(8),                -- 交易所：SSE=上海证券交易所（沪市）/ SZSE=深圳证券交易所（深市）/
                                            -- BSE=北京证券交易所（北交所）；与 market 是不同维度（板块层级 vs 交易所）
    industry     VARCHAR(32),               -- 所属行业（stock_basic 口径）：如 000001.SZ→"银行"
    area         VARCHAR(16),               -- 公司地域（stock_basic 原生字段）：如"深圳"/"北京"/"吉林"/"江苏"
    list_status  CHAR(1),                   -- 上市状态：'L'=上市 / 'D'=退市 / 'P'=暂停上市
    list_date    DATE,                      -- 上市日期：如 000001.SZ → 1991-04-03
    delist_date  DATE,                      -- 退市日期（仅退市股有值）：如 000418.SZ（小天鹅A，吸收合并退市）；
                                            -- 在上市股为 NULL
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()  -- 行最后写入时间
);

-- 场内基金基本信息（~2900 行，ETF/LOF，fund_basic market='E' 全字段）
CREATE TABLE IF NOT EXISTS fund_basic (
    ts_code        VARCHAR(16) PRIMARY KEY, -- 基金代码：如 158013.SZ（易方达国证航天航空行业ETF）、
                                            -- 161725.SZ（招商中证白酒LOF）；本表恒为场内基金（market='E' 过滤）
    name           VARCHAR(64) NOT NULL,    -- 基金全称：如"易方达国证航天航空行业ETF"
    management     VARCHAR(64),             -- 基金管理人：如"易方达基金管理有限公司"
    custodian      VARCHAR(64),             -- 基金托管人：如"国泰海通证券股份有限公司"
    fund_type      VARCHAR(32),             -- 基金类型（fund_basic 口径）：如 '股票型'/'混合型'/'债券型'；
                                            -- 实测 158013.SZ = '股票型'
    invest_type    VARCHAR(32),             -- 投资类型（fund_basic 口径）：官方含指数型/股票型等；实测部分基金为空
                                            -- （NaN→NULL），原样保留
    type           VARCHAR(32),             -- 基金类型（fund_basic 口径，与 fund_type 语义重叠）：实测 '股票型'
    found_date     DATE,                    -- 基金成立日期：如 158013.SZ → 2026-08-26
    due_date       DATE,                    -- 基金到期日（封闭式/定期开放基金有值；普通开放式为 NULL）
    list_date      DATE,                    -- 场内上市日期（交易所挂牌日，fund_basic 口径）
    issue_date     DATE,                    -- 发行日期：如 158013.SZ → 2026-07-27
    delist_date    DATE,                    -- 场内退市日期（仅已摘牌基金有值；在册基金为 NULL）
    issue_amount   DOUBLE PRECISION,        -- 发行份额，单位：亿份
    m_fee          DOUBLE PRECISION,        -- 管理费率，单位 %/年：如 158013.SZ = 0.15
    c_fee          DOUBLE PRECISION,        -- 托管费率，单位 %/年：如 158013.SZ = 0.05
    duration_year  DOUBLE PRECISION,        -- 存续期（年），开放式基金多为 NULL
    p_value        DOUBLE PRECISION,        -- 面值（元）：实测恒 1.0
    min_amount     DOUBLE PRECISION,        -- 起购金额（fund_basic 口径）：如 158013.SZ = 0.1
    exp_return     DOUBLE PRECISION,        -- 预期年化收益（fund_basic 口径）：实测多数为 NULL
    benchmark      VARCHAR(128),            -- 业绩基准（跟踪指数描述）：如 158013.SZ =
                                            -- "国证航天航空行业指数收益率×100%"
    status         CHAR(1),                 -- 基金状态：'L'=上市 / 'I'=发行期 / 'D'=终止上市；如 158013.SZ = 'L'
    trustee        VARCHAR(64),             -- 受托人（fund_basic 口径）：实测多数为 NULL
    purc_startdate DATE,                    -- 申购起始日
    redm_startdate DATE,                    -- 赎回起始日
    market         CHAR(1),                 -- 交易场所：'E'=场内 / 'O'=场外；本表恒 'E'（按 market='E' 过滤拉取）
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()  -- 行最后写入时间
);

-- 日线共用表（未复权原值；~1400 万行）
CREATE TABLE IF NOT EXISTS stock_daily (
    ts_code    VARCHAR(16) NOT NULL,        -- 证券代码（股票与场内基金共用）：如 000001.SZ（平安银行）、
                                            -- 158013.SZ（航天航空ETF）；分类靠前缀函数 is_fund_ts_code 判定（2.1 决策 1）
    trade_date DATE NOT NULL,               -- 交易日（自然日）；停牌日无行（整行缺失，不存 NULL 行），
                                            -- 查询方需自行 ffill/容忍缺口
    open       DOUBLE PRECISION,            -- 开盘价（元，未复权）：如 920992.BJ@2026-08-28 → 11.31
    high       DOUBLE PRECISION,            -- 最高价（元，未复权）：11.49
    low        DOUBLE PRECISION,            -- 最低价（元，未复权）：11.26
    close      DOUBLE PRECISION NOT NULL,   -- 收盘价（元，未复权）：11.41；NOT NULL——写入清洗时
                                            -- close 为 NaN 的行显式 drop（3.3.1）
    pre_close  DOUBLE PRECISION,            -- 前收盘价（元）：11.37（新上市首日为 NULL）
    change     DOUBLE PRECISION,            -- 涨跌额（元）= close - pre_close：0.04
    pct_chg    DOUBLE PRECISION,            -- 涨跌幅（%）= change / pre_close × 100：0.3518
    vol        DOUBLE PRECISION,            -- 成交量，单位：手（1 手 = 100 股/份）：5670.63
    amount     DOUBLE PRECISION,            -- 成交额，单位：千元：6450.05523（约 645 万元）
    PRIMARY KEY (ts_code, trade_date)       -- 含日期列，未来可切 TimescaleDB/分区
);
CREATE INDEX IF NOT EXISTS idx_stock_daily_trade_date ON stock_daily (trade_date, ts_code);
-- 反向索引：某日全市场横截面查询（get_cross_section）

-- 复权因子共用表（~1400 万行）
CREATE TABLE IF NOT EXISTS adj_factor (
    ts_code    VARCHAR(16) NOT NULL,        -- 证券代码（股票 adj_factor 接口 + 基金 fund_adj 接口共用）：
                                            -- 如 000001.SZ / 160505.SZ
    trade_date DATE NOT NULL,               -- 交易日；因子行数 ≥ 当日日线行数属预期（停牌标的仍更新因子，3.4.3）
    adj_factor DOUBLE PRECISION NOT NULL,   -- 复权因子（原值）：无除权除息 = 1.0；如 160505.SZ@2026-08-28 = 11.1326；
                                            -- 前复权公式 qfq_x = x × factor_t / factor_latest（3.3.1 get_qfq_daily）
    PRIMARY KEY (ts_code, trade_date)
);

-- 概念列表（多来源，~2000 行；决策 8/9）
-- 概念与成分拆两张表（决策 8）：概念元数据（成分数/发布日期）不冗余到成分行，
-- 概念改名/下线只 UPDATE 1 行；概念列表与成分变动独立刷新
CREATE TABLE IF NOT EXISTS concept (
    source       VARCHAR(8) NOT NULL,       -- ths=同花顺 / dc=东方财富（决策 9 多来源解耦）
    concept_code VARCHAR(32) NOT NULL,      -- 883300.TI（ths）/ BK1753.DC（dc）
    name         VARCHAR(64) NOT NULL,      -- 概念名（如"激光雷达"/"光刻胶"）
    count        INTEGER,                   -- 成分数（来源口径快照）：如 883300.TI"沪深300样本股" = 300、
                                            -- 864008.TI"激光雷达" = 1；与 concept_member 实际行数存在滞后偏差属预期
    exchange     VARCHAR(8),                -- 概念指数市场归属（ths_index 口径）：实测恒为 'A'（= 全部 A 股市场）；
                                            -- dc 来源无此口径，存 NULL
    list_date    DATE,                      -- 概念指数发布日期（ths_index 口径）：如 883300.TI"沪深300样本股" = 2010-04-13；
                                            -- dc 来源无此口径，存 NULL
    type         VARCHAR(8),                -- 板块指数类型（ths_index 口径）：'N'=概念指数（本表 V1 恒为 N——
                                            -- 拉取时即按 type='N' 过滤）；同花顺另含 'I'=行业指数、'S'=特色指数，
                                            -- 保留此列以备未来扩展类型；dc 来源无此口径，存 NULL
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source, concept_code)
);

-- 概念成分映射（多来源，逐概念拉取，~12 万行）
CREATE TABLE IF NOT EXISTS concept_member (
    source       VARCHAR(8) NOT NULL,       -- 概念来源：ths=同花顺 / dc=东方财富（与 concept.source 对应）
    concept_code VARCHAR(32) NOT NULL,      -- 概念代码：如 883300.TI（ths）/ BK1753.DC（dc 光刻胶）
    ts_code      VARCHAR(16) NOT NULL,      -- 成分股票代码：如 301630.SZ（光刻胶成分"同宇新材"）
    PRIMARY KEY (source, concept_code, ts_code)
    -- 不存成分股名（决策 12）：概念来源名称与 stock_basic.name 存在改名不同步的漂移风险，
    -- 展示/对账一律 ts_code JOIN stock_basic 取权威名称
);
CREATE INDEX IF NOT EXISTS idx_concept_member_ts ON concept_member (ts_code);
-- 反查索引：某股票属于哪些概念（get_stock_concepts）
