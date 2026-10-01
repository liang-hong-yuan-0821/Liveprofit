# Alembic / JSONB / pydantic-settings / SQLAlchemy 坑

> 一句话结论：alembic.ini 纯 ASCII；JSONB 写入前归一回写；pydantic-settings 的 alias 写完整 env 名；迁移 raw SQL 用 `text()`；条件 UPDATE 显式 `synchronize_session="fetch"`。

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
