# Alembic / JSONB / pydantic-settings / SQLAlchemy 坑

> 一句话结论：alembic.ini 纯 ASCII；JSONB 写入前归一回写；pydantic-settings 的 alias 写完整 env 名；迁移 raw SQL 用 `text()`；条件 UPDATE 显式 `synchronize_session="fetch"`。

## 前驱摘要不能替代其原像与管理投影关系（2026-10-01）

- **表象**：0053初版核立即前驱hash后使用managed_state成员；截断前驱orders/stops/expectations原行像并重算hash，后续命令仍可依据不受原件支持的成员封口。
- **根因**：摘要证明内容未改变，不证明两份冗余表达相互一致；原件全集与管理投影必须按冻结schema重新推导。旧+08时间与UTC同刻异文也是投影语义问题，不能重写原件解决。
- **正确姿势**：SQL写端和Python读端同组核完整物理列/精确原文/身份并重建立即前驱managed_state；迁移基线按当时活动状态过滤，后继按固定成员。不递归认证整个旧史；只规范明确业务时间字段的管理比较，保留旧原文/hash。
- **位置/验证**：0053的`lc_bound_verify_previous`、`lifecycle_bound_replay.py`的`_previous_snapshot`；六类入口故障及六类读端重算摘要反例，最终新增55项和独立R2 PASS。完整历史与券商来源仍未认证。

## 同一TIMESTAMPTZ行像受会话时区影响（2026-10-01）

- **表象**：0052捕获时和提交前更换TimeZone，即使最终订单未变，`row_to_json`的TIMESTAMPTZ表示仍变，导致精确原文与最终投影校验误拒绝。
- **根因**：物理值相同不代表默认JSON文本相同；仅规范changes.captured_at不足以固定业务行内时间字段。
- **正确姿势**：入口、行捕获和全链核验函数均固定`TimeZone=UTC`与`DateStyle=ISO,YMD`；保存原SQL文本，不能通过重写原件掩盖漂移。Python摘要仍统一UTC六位微秒。
- **验证**：纽约会话暂存、上海会话提交、奥克兰会话独立读端核验全部通过；0052新文件25项，R1 minor修复后独立R2 PASS。完整历史和生产部署仍未认证。

## 迁移行像递归Decimal字符串化会改JSONB原类型（2026-10-01）

- **表象**：0051初版解析`row_to_json`时使用`parse_float=Decimal`，再递归转字符串；原`config_snapshot={"multiple":1.5}`成为`{"multiple":"1.5"}`，保存的原输入hash仍对应数字载荷。
- **根因**：SQL NUMERIC精度与JSONB内部数字类型是两种约束，统一递归规范不能保留原物理类型；先用float解码也已丢失NUMERIC精度。
- **正确姿势**：迁移物理行像保留精确`row_to_json(...)::text`原文（0051使用`__raw_row_json__`），规范Decimal字符串另作解释视图，不能据解释视图重造原JSONB。导出保存每行精确`row_json`，从字节核数量/SHA/readback；只读仓储重算原文与解释及业务摘要。
- **验证**：`test_lifecycle_operation_schema.py`随机隔离16项，包括嵌套JSONB数字/布尔原文、NUMERIC精度、升级回滚、导出与字段损坏读取；独立R1 major修复后R2 PASS。LIVE捕获和连续历史不在该验证范围。

### 原文与不变字段比较都须类型敏感（2026-10-01）

- **表象**：`_verify_row_image`用Python dict相等核原文/解释，整数`source_signal_id=1`与布尔`true`被视为相等；修原文比较后，订单状态命令的“不变字段”比较又残留同类问题。同步伪造物理原文和重算摘要仍可能漏掉身份类型改变。
- **根因**：`1 == True`及`0 == False`不符合JSON类型语义；完整摘要也不能替代字段全集和命令允许变化范围。
- **正确姿势**：原文解析后的Decimal解释与已存视图按规范JSON字节比较，不变字段也用同一类型敏感比较；历史schema冻结物理列全集，不从当前行补齐截断历史。结论、原因及schema一并纳入结果快照摘要，并与独立结果列核一致。
- **验证**：重放读端R1四major及R2同类残留修复后独立R2 PASS；整数/布尔双向纯测、完整行1→true、截断列/跳revision/无关数量改变及结果列漂移的重算摘要反例均覆盖。读端不认证DB捕获、封口或交易权限。

## alembic.ini 必须纯 ASCII（2026-09-05）

- **表象**：Windows GBK locale 下含中文注释抛 UnicodeDecodeError（表象为 pytest 卡死在配置解析）。
- **正确姿势**：ini 注释用英文，说明写进 backend/migrations/env.py。

## JSONB 写入前必须归一为纯 JSON 基本类型（2026-09-06）

- **表象**：`Object of type HumanMessage is not JSON serializable`。
- **根因**：psycopg 的 JSONB dump 用标准 `json.dumps`（**无 default 兜底**），LangChain 对象（如 HumanMessage）进 JSONB 即炸；"序列化校验"若只 `json.dumps(payload, default=str)` 而**不回写结果**等于没校验（raw 对象仍入列）。
- **正确姿势**：`payload = json.loads(json.dumps(payload, ensure_ascii=False, default=str))`，并在 Service 写入边界再做一次防御。

## pydantic-settings 带 validation_alias 的字段不叠加 env_prefix（2026-09-06）

- **表象**：LLM 400 无效模型名。
- **根因**：env 变量名 = alias 原样，prefix 被忽略——backend CoreSettings 曾写 `validation_alias="QUICK_MODEL"` + `env_prefix="LIVEPROFIT_"`，实际读的是无前缀的 `QUICK_MODEL`，.env 里的 `LIVEPROFIT_QUICK_MODEL` 被静默无视、落默认 gpt-4o-mini。
- **正确姿势**：alias 必须写完整环境变量名（`validation_alias="LIVEPROFIT_QUICK_MODEL"`）；新增 aliased 字段时先实测 `Settings().field` 确认读到的是哪个 env。

## Alembic 迁移内 raw SQL 必须 text() 包装

- SQLAlchemy 2.0 拒绝裸字符串 + params；ORM 模型 Python 端 `default=uuid.uuid4` 不作用于迁移 SQL——INSERT 需显式 `gen_random_uuid()`（PG13+ 内置）。

## SQLAlchemy 条件 UPDATE 含比较运算

- **表象**：WHERE 用 `<`/`>` 比较时，默认 `synchronize_session="auto"`→evaluate 会在 Python 层比较 naive/aware datetime 抛 TypeError。
- **正确姿势**：一律显式 `synchronize_session="fetch"`（既避免异常又保持会话内对象新鲜；False 会让后续 get 读到旧状态）。
