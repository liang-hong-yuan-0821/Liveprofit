# MongoDB 全面替换为 PostgreSQL 方案

> **状态**：已完成（2026-08-18）
> **进度**：6/6 步骤
> **下一步**：移入 docs/done/ 归档（主干文档 docs/index.md 已核查无 MongoDB 描述，无需同步）
> **关联文档**：[docs/index.md](../index.md)（存储架构）、[docs/done/事件研究方案.md](../done/事件研究方案.md)

---

## 一、背景与动机

### 1.1、现状

全库检索 + 运行时调用链分析结论：**MongoDB 在本项目中属于"基础设施已部署、业务接入极少且大部分是死代码"的状态**。

**（1）MongoDB 的实际使用面**

| 集合 | 使用状态 | 证据 |
|------|---------|------|
| `cache`（自适应缓存 KV） | **唯一活跃路径**，且完全可选 | [adaptive.py:88](AI/dataflows/cache/adaptive.py#L88) `replace_one(upsert=True)`；仅 `TA_CACHE_STRATEGY=integrated` 时可能触达，位于 Redis > MongoDB > File 降级链中间层 |
| `token_usage`（LLM 使用记录） | 死代码 | [mongodb_storage.py](AI/config/mongodb_storage.py) 类 `MongoDBStorage` 全库无实例化调用 |
| `stock_data` / `news_data` / `fundamentals_data` | 死代码 | [db_cache.py](AI/dataflows/cache/db_cache.py) 类 `DatabaseCacheManager` 全库无实例化调用 |

**（2）基础设施现状**

- [docker-compose.yml:39-65](docker-compose.yml#L39-L65) 运行 `mongo:4.4`（已 EOL 的镜像版本）+ `mongo-express` 管理界面（[docker-compose.yml:130-149](docker-compose.yml#L130-L149)），独立维护一个数据库服务仅为一层可选缓存兜底
- 项目**已有 PostgreSQL 16 + pgvector**（[docker-compose.yml:10-37](docker-compose.yml#L10-L37)，事件研究系统主存储，`AI/eventStudy/db/` 已使用 psycopg3），技术栈就绪
- `.env`（[.env:18-28](.env#L18-L28)，含 :18-20 节注释头）与 `.env.example:34-43` 维护 8 个 `MONGODB_*` 配置项
- [pyproject.toml:30](pyproject.toml#L30) 维护 `pymongo>=4.0.0` 运行时依赖
- [README.md:139-142](README.md#L139-L142) 存储架构图、[README.md:174-176](README.md#L174-L176) 目录树、[README.md:208-209](README.md#L208-L209) 差异表均描述 MongoDB

**（3）使用的 MongoDB 专有特性盘点**

无 TTL 索引（缓存过期是应用层 `expires_at` 字段判断）、无事务、无 GridFS、无 change streams、无 bulk write、无更新操作符；聚合管道仅存在于死代码中（[mongodb_storage.py:121-131](AI/config/mongodb_storage.py#L121-L131)）。**没有任何需要翻译到 PostgreSQL 的 Mongo 专有特性**。

### 1.2、目标

- MongoDB 从项目中**彻底移除**：docker-compose 服务、数据卷、pymongo 依赖、`MONGODB_*` 环境变量、5 个死代码文件全部清理
- 替换后的存储体系：**PostgreSQL 16 + pgvector**（事件研究主存储，不变）+ **Redis**（热缓存，已开 AOF 持久化）+ **ChromaDB**（记忆）+ **File Cache**（兜底，不变）
- 缓存降级链由三级（Redis > MongoDB > File）简化为二级（Redis > File）
- 全库代码与配置中不再出现 mongo/pymongo 字样（`docs/done/` 历史归档，以及 README"与 TradingAgents-CN 差异表"中对 TradingAgents-CN 的客观描述除外）
- PostgreSQL 侧**无需新增任何表**——本方案实质是"用已有的 PG + Redis 架构全面替换并移除 MongoDB"，而非把 Mongo 的 KV 缓存强行迁入 PG

---

## 二、架构设计

### 替换前（现状）

```
TA_CACHE_STRATEGY=integrated 时：
数据请求 → IntegratedCacheManager
              ├── AdaptiveCacheSystem: Redis(优先) → MongoDB(次) → File(兜底)
              └── StockDataCache 文件缓存（始终双写兜底）

存储服务：PostgreSQL 16+pgvector（事件研究）/ MongoDB 4.4（可选缓存+死代码）/ Redis 7 / ChromaDB
```

### 替换后（目标）

```
数据请求 → IntegratedCacheManager
              ├── AdaptiveCacheSystem: Redis(优先) → File(兜底)   ← 删掉 MongoDB 层
              └── StockDataCache 文件缓存（始终双写兜底）

存储服务：PostgreSQL 16+pgvector（事件研究）/ Redis 7 / ChromaDB  ← MongoDB 移除
```

- `IntegratedCacheManager` 双写与回退逻辑不变（它只依赖 `AdaptiveCacheSystem.get_backend()` 返回 `"redis"` / `"file"` 两态判断，见 [integrated.py:20-36](AI/dataflows/cache/integrated.py#L20-L36)）
- [interface.py:52-62](AI/dataflows/interface.py#L52-L62) 的懒加载入口 `get_cache()` 不变
- 文件缓存始终写入的兜底语义不变（Redis 重启/不可用时 AOF 恢复或文件缓存兜底）

### 2.1 数据模型设计

**不涉及**。本方案不新增、不变更任何 PostgreSQL 表（事件研究系统 6 张表维持现状），也不迁移任何 Mongo 数据——`cache` 集合中仅有 TTL 最长 12 小时的短期缓存，直接随容器销毁。

---

## 三、详细设计

### 3.1 缓存层改造：adaptive.py 降级链删 Mongo 层

#### 3.1.1 模块设计

[adaptive.py](AI/dataflows/cache/adaptive.py) 是唯一活跃 Mongo 路径，改造目标为 Redis > File 二级链：

| 位置 | 现状 | 改动 |
|------|------|------|
| 模块 docstring（:1-5） | "Redis > MongoDB > File" | 改为 "Redis > File" |
| :21-25 | `import pymongo` + `PYMONGO_AVAILABLE` | 整块删除 |
| :33 | `self._mongo_db = None` | 删除 |
| :49-59 | MongoDB 连接检测块（ping + `self._backend = "mongodb"`） | 整块删除 |
| :83-89 | `save_data` 的 `elif self._backend == "mongodb"` 分支 | 删除，`else` 分支（文件兜底）不变 |
| :107-111 | `load_data` 的 `elif self._backend == "mongodb"` 分支 | 删除，`else` 分支不变 |
| :124-126 | `get_backend()` | 不变（返回值仅剩 `"redis"` / `"file"`） |
| [interface.py:5](AI/dataflows/interface.py#L5) | 模块 docstring "集成缓存层：Redis > MongoDB > File 三级缓存。" | 改为 "集成缓存层：Redis > File 二级缓存。" |
| [dataflows/__init__.py:4](AI/dataflows/__init__.py#L4) | 模块 docstring "集成 Redis > MongoDB > File 三级缓存。" | 改为 "集成 Redis > File 二级缓存。" |

- 类接口完全不变：`save_data(key, data, data_type) -> bool`、`load_data(key, data_type) -> Optional[any]`、`get_backend() -> str`
- [cache/__init__.py:1-5](AI/dataflows/cache/__init__.py#L1-L5) docstring 同步改为"支持二级缓存后端：Redis > File"
- [integrated.py](AI/dataflows/cache/integrated.py) 无需任何改动（docstring 与逻辑均无 mongo 字样，已全文件核查）；[interface.py](AI/dataflows/interface.py)、[dataflows/__init__.py](AI/dataflows/__init__.py) 仅需上述 docstring 同步，逻辑不变

#### 3.1.2 三方依赖能力评估

本模块改造只做减法：移除 pymongo，不新增任何依赖。Redis 检测（redis-py，[adaptive.py:36-47](AI/dataflows/cache/adaptive.py#L36-L47)）与文件兜底路径均维持现状，能力不受影响。

#### 3.1.3 风险与验证方式

- 风险：删错分支导致 `save_data`/`load_data` 在 `_backend == "file"` 时行为变化 → 验证时重点回归文件兜底路径
- 验证：Redis 启动（`docker-compose up -d redis`）+ `TA_CACHE_STRATEGY=integrated` 冒烟：save→load 命中 Redis；停 Redis 后重新初始化 → 回退文件缓存；日志中无 "mongodb" 字样

#### 3.1.4 文件变更清单

| 类型 | 文件 | 说明 |
|------|------|------|
| 修改 | `AI/dataflows/cache/adaptive.py` | 删 Mongo 检测与读写分支，docstring 更新 |
| 修改 | `AI/dataflows/cache/__init__.py` | docstring 更新 |
| 修改 | `AI/dataflows/interface.py` | 模块 docstring 同步（:5） |
| 修改 | `AI/dataflows/__init__.py` | 模块 docstring 同步（:4） |

### 3.2 配置层死代码清理（AI/config/）

#### 3.2.1 模块设计

删除 4 个文件并清理导出（均已全库检索确认无调用方）：

| 文件 | 内容 | 无调用方证据 |
|------|------|-------------|
| `AI/config/mongodb_storage.py` | `MongoDBStorage`（token_usage 集合适配器） | 全库无 `MongoDBStorage(` 实例化 |
| `AI/config/database_manager.py` | `DatabaseManager` / `get_database_manager` / `is_mongodb_available` / `is_redis_available` / `get_cache_backend` | 全库无任何调用 |
| `AI/config/database_config.py` | `DatabaseConfig`（Mongo/Redis 配置读取） | 全库无 `DatabaseConfig` 引用 |
| `AI/config/usage_models.py` | `UsageRecord` / `ModelConfig` / `PricingConfig` | 仅被 `mongodb_storage.py` 与 [config/__init__.py:5](AI/config/__init__.py#L5) 导出引用，删除前者后无消费者 |

- [config/__init__.py](AI/config/__init__.py) 删除 5、6、7、8 行导出，保留 `providers_config`、`env_utils` 导出
- [default_config.py:25,27,28](AI/default_config.py#L25-L28) 删除 3 个 `mongodb_*` 键（`redis_*` 键保留，Redis 仍在用）；`load_config()` 的消费方均不读取这些键

> 说明：token 使用统计能力（`UsageRecord` 模型）随死代码一并移除。将来如需接入 LLM 用量统计，届时基于 psycopg 新写 PG 版即可（建一张 `token_usage` 表），不影响本次方案。

#### 3.2.2 三方依赖能力评估

本模块只做减法，不依赖外部库。

#### 3.2.3 风险与验证方式

- 风险：存在未检出的动态引用（如字符串 import）导致删除后 ImportError → 验证时全库 grep 类名/模块名归零 + `python -c "import AI.config"` 通过
- 验证：删除后 grep `MongoDBStorage|DatabaseManager|DatabaseCacheManager|DatabaseConfig|UsageRecord|ModelConfig|PricingConfig` 全库（排除 `docs/`、`.venv/`）零命中

#### 3.2.4 文件变更清单

| 类型 | 文件 | 说明 |
|------|------|------|
| 删除 | `AI/config/mongodb_storage.py` | 死代码 |
| 删除 | `AI/config/database_manager.py` | 死代码 |
| 删除 | `AI/config/database_config.py` | 死代码 |
| 删除 | `AI/config/usage_models.py` | 模型类无消费者 |
| 修改 | `AI/config/__init__.py` | 清理 4 个导出 |
| 修改 | `AI/default_config.py` | 删 3 个 `mongodb_*` 配置键 |

### 3.3 缓存死代码删除（db_cache.py）

#### 3.3.1 模块设计

删除 [AI/dataflows/cache/db_cache.py](AI/dataflows/cache/db_cache.py) 整文件（`DatabaseCacheManager`，MongoDB + Redis 双后端缓存，全库无实例化，未接入 [cache/__init__.py](AI/dataflows/cache/__init__.py) 的缓存入口）。同一目录下 `file_cache.py`、`adaptive.py`、`integrated.py` 均不引用该文件。

#### 3.3.2 三方依赖能力评估

本模块只做减法，不依赖外部库。

#### 3.3.3 风险与验证方式

- 风险：极低（零引用），删除后跑一遍现有测试套件确认无隐式导入
- 验证：`grep -rn "db_cache" AI/ tests/` 零命中（排除文件自身）

#### 3.3.4 文件变更清单

| 类型 | 文件 | 说明 |
|------|------|------|
| 删除 | `AI/dataflows/cache/db_cache.py` | 死代码 |

### 3.4 基础设施清理（docker-compose / run.sh / 数据卷）

#### 3.4.1 模块设计

- [docker-compose.yml](docker-compose.yml)：
  - 删除 `mongodb` 服务（:39-65）
  - 删除 `mongo-express` 服务（:130-149；注意 :110-129 是 adminer——PostgreSQL 管理界面，保持不变）
  - 删除 `volumes.mongodb_data`（:151-154，卷名 `liveprofit_mongodb_data`）
  - 顶部注释（:5）"仅提供 MongoDB + Redis + PostgreSQL 服务" → "仅提供 Redis + PostgreSQL 服务"
  - `postgres`、`redis`、`redis-commander`、`adminer`、`networks` 均不动
- [run.sh:71-75](run.sh#L71-L75)：:71 "启动 MongoDB + Redis 服务..." → "启动 PostgreSQL + Redis 服务..."；:74 注释 "等待 MongoDB 和 Redis 就绪" → "等待 PostgreSQL 和 Redis 就绪"；:75 "等待 MongoDB/Redis 健康检查..." → "等待 Redis 健康检查..."
- **数据卷销毁**：`cache` 集合仅存 TTL ≤ 12h 的短期缓存（stock 1h / news 4h / fundamentals 12h，[adaptive.py:69-72](AI/dataflows/cache/adaptive.py#L69-L72)），无业务资产，**无需迁移**。实施时执行 `docker-compose down` 后 `docker volume rm liveprofit_mongodb_data`（实施前先提交 git 存档，如需留底可用 `mongodump` 备份后销毁）

#### 3.4.2 三方依赖能力评估

本模块不依赖外部库/API。

#### 3.4.3 风险与验证方式

- 风险：run.sh 文案改动不影响逻辑（原本就是 `docker-compose up -d` 起全部服务 + sleep 5）；销毁数据卷不可逆 → 已确认缓存数据无迁移价值，实施前 git 存档
- 验证：`docker-compose config` 校验通过；`docker-compose up -d` 后 `postgres`/`redis` healthcheck 通过；`docker ps` 无 mongo 容器

#### 3.4.4 文件变更清单

| 类型 | 文件 | 说明 |
|------|------|------|
| 修改 | `docker-compose.yml` | 删 mongodb / mongo-express 服务与卷，注释更新 |
| 修改 | `run.sh` | 启动日志文案调整 |

### 3.5 环境变量与依赖清理

#### 3.5.1 模块设计

- [.env:18-28](.env#L18-L28)：删除 MongoDB 配置节（含 :18-20 节注释头与 8 个变量）：`MONGODB_ENABLED` / `MONGODB_HOST` / `MONGODB_PORT` / `MONGODB_USERNAME` / `MONGODB_PASSWORD` / `MONGODB_DATABASE` / `MONGODB_AUTH_SOURCE` / `MONGODB_CONNECTION_STRING`
- [.env.example:34-43](.env.example#L34-L43)：同上删除；[.env.example:56](.env.example#L56) `TA_CACHE_STRATEGY` 注释 "integrated: 自适应 Redis > MongoDB > File（需要 MongoDB/Redis 已启用）" → "integrated: 自适应 Redis > File（需要 Redis 已启用）"
- [pyproject.toml:30](pyproject.toml#L30)：删除 `pymongo>=4.0.0` 依赖；`uv.lock` 重新生成（`uv lock`）
- `TRADINGAGENTS_MONGODB_URL` 环境变量仅被死代码引用（[mongodb_storage.py:33](AI/config/mongodb_storage.py#L33)、[database_config.py:22](AI/config/database_config.py#L22)、[db_cache.py:38](AI/dataflows/cache/db_cache.py#L38)），随死代码删除一并消失，无残留引用

#### 3.5.2 三方依赖能力评估

只移除 pymongo，其余依赖不动；psycopg（PG）、redis-py（Redis）均已在使用中，能力不受影响。

#### 3.5.3 风险与验证方式

- 风险：`.env` 是本地真实配置文件，改动需在实施时同步告知用户；其他项目（如 TradingAgents-CN）若复用同一 `.env` 不受影响（各项目独立目录）
- 验证：删除后 `grep -rn -i "mongo\|pymongo" AI/ main.py tests/ docker-compose.yml run.sh .env.example pyproject.toml` 零命中；README.md 除"与 TradingAgents-CN 差异表"中对 TradingAgents-CN 的客观描述（:208 行、:209 左侧列）外零命中（`.env` 单独确认）；`uv sync` 后 `pip show pymongo` 不存在且项目可正常启动

#### 3.5.4 文件变更清单

| 类型 | 文件 | 说明 |
|------|------|------|
| 修改 | `.env` | 删 MongoDB 配置段（本地文件，实施时操作） |
| 修改 | `.env.example` | 删 MongoDB 配置段 + `TA_CACHE_STRATEGY` 注释更新 |
| 修改 | `pyproject.toml` | 删 pymongo 依赖 |
| 修改 | `uv.lock` | `uv lock` 重新生成 |

### 3.6 文档同步（README.md）

#### 3.6.1 模块设计

- [README.md:74-75](README.md#L74-L75)：docker 启动说明 :74 `(MongoDB + Redis + LiveProfit)` → `(PostgreSQL + Redis + LiveProfit)`；:75 `(Redis Commander + Mongo Express)` → `(Redis Commander + Adminer)`
- [README.md:96-97](README.md#L96-L97)：环境变量表删除 `MONGODB_ENABLED` / `MONGODB_CONNECTION_STRING` 两行
- [README.md:139-142](README.md#L139-L142)：存储架构图删除 MongoDB 块（token_usage/stock_data/news_data/fundamentals_data 4 行）
- [README.md:170](README.md#L170)：目录树注释 "Docker 服务编排 (MongoDB + Redis + LiveProfit)" → "(PostgreSQL + Redis + LiveProfit)"
- [README.md:174-178](README.md#L174-L178)：目录树删除 `database_manager.py`（:174）、`database_config.py`（:175）、`mongodb_storage.py`（:176）、`usage_models.py`（:178）四行
- [README.md:208-213](README.md#L208-L213)：差异表 "配置文件" 行维持原状；"缓存层" 行右侧 LiveProfit 列 `MongoDB + Redis + File` → `Redis + File`（左侧 TradingAgents-CN 列维持原状）；"Docker" 行 "4 服务 (仅后端存储)" → "2 服务 (仅后端存储)"（管理 profile 另有 redis-commander、adminer 两个可选界面）
- [docs/index.md](../index.md)：已核查，主干文档无 MongoDB 描述，无需改动；`docs/done/` 归档文档按约定不回溯修改

#### 3.6.2 三方依赖能力评估

本模块不依赖外部库/API。

#### 3.6.3 风险与验证方式

- 风险：README 部分目录树已滞后于实际结构（`liveprofit/` 包已改名 `AI/`），本次只改 MongoDB 相关行，不扩散修复范围
- 验证：人工检查 README 渲染后无 MongoDB 残留描述

#### 3.6.4 文件变更清单

| 类型 | 文件 | 说明 |
|------|------|------|
| 修改 | `README.md` | 存储架构图 / 目录树 / 环境变量表 / 差异表同步 |

---

## 四、已确认决策 / 待确认问题

### 已确认决策

1. **方案范围仅 Liveprofit**（2026-08-18 确认）：TradingAgents-CN 的 MongoDB 替换另行立项，不在本方案范围
2. **一次性全量替换**：不保留过渡期双写，一次实施完成
3. **PG 部署沿用现状**：复用已有 PostgreSQL 16 + pgvector 实例（[docker-compose.yml:10-37](docker-compose.yml#L10-L37)），各项目独立实例
4. **缓存降级链直接删层**：adaptive.py 由 Redis > MongoDB > File 三级简化为 Redis > File 二级，PG 不承担缓存职责（Redis 已开 AOF 持久化 + 文件缓存始终双写兜底）
5. **死代码全部删除**：mongodb_storage.py / db_cache.py / database_manager.py（连带 database_config.py / usage_models.py）直接删除，不保留 PG 改写版
6. **Mongo 缓存数据不迁移**：仅 TTL ≤ 12h 的短期缓存，随数据卷销毁

### 待确认问题

无阻塞项。方案已闭环，确认后即可进入实现。

---

## 实施步骤（预估）

| 步骤 | 内容 | 涉及文件 |
|------|------|---------|
| 1 | git 存档当前状态（注意：工作区已有未提交改动——international_news_analyst.py、docker-compose.yml、docs/市场层.md 及未跟踪的 docs/plans/，需一并提交形成干净基线） | — |
| 2 | 缓存层改造（3.1） | adaptive.py、cache/__init__.py、interface.py、dataflows/__init__.py |
| 3 | 死代码删除（3.2 + 3.3） | AI/config/ 4 文件、db_cache.py、config/__init__.py、default_config.py |
| 4 | 基础设施清理（3.4） | docker-compose.yml、run.sh、销毁 liveprofit_mongodb_data 卷 |
| 5 | 环境变量与依赖（3.5） | .env、.env.example、pyproject.toml、uv.lock |
| 6 | 文档同步 + 全库 grep 归零验证（3.6） | README.md |
