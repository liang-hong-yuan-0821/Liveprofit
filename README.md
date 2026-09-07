# LiveProfit - 多智能体股票交易分析系统

基于 [TradingAgents-CN](https://github.com/TauricResearch/TradingAgents) 的简化版，使用 **LangGraph** 编排多个 LLM 智能体协作分析 A 股市场。

## 架构概览

LiveProfit 使用 LangGraph 构建一个多阶段分析流水线：

```
START
  │
  ├─ 市场分析师 ──► 技术指标数据
  ├─ 基本面分析师 ──► 财务数据
  ├─ 新闻分析师 ──► 新闻/公告
  ├─ 情绪分析师 ──► 情绪综合评估
  ├─ 科技分析师 ──► 全球科技指数 & AI产业链 (纳斯达克/韩国/A股)
  │
  ├─ 看涨研究员 ◄──► 看跌研究员 (投资辩论)
  │
  ├─ 研究经理 ──► 投资计划
  ├─ 交易员 ──► 交易决策
  │
  ├─ 激进分析师 ◄──► 保守分析师 ◄──► 中性分析师 (风险辩论)
  │
  └─ 风险经理 ──► 最终决策
         │
      信号处理器 ──► 结构化 JSON
```

## 快速开始

### 1. 安装依赖

```bash
pip install -e .
# 或使用 uv（更快）
uv pip install -e .
```

### 2. 配置

编辑 `a.bash`，填入你的 API 密钥：

```bash
# LLM (OpenAI 兼容 API)
export LIVEPROFIT_API_KEY="sk-your-actual-key"
export LIVEPROFIT_BASE_URL="https://api.openai.com/v1"
export LIVEPROFIT_QUICK_MODEL="gpt-4o-mini"
export LIVEPROFIT_DEEP_MODEL="gpt-4o"

# 数据源 (二选一)
export LIVEPROFIT_DATA_SOURCE="tushare"
export TUSHARE_TOKEN="your-tushare-token"
# 或免费数据源: export LIVEPROFIT_DATA_SOURCE="akshare"
```

加载配置：

```bash
source a.bash
```

### 3. 运行

```bash
./run.sh classic                 # 经典模式：运行 AI 分析（等价于 python main.py）
# 或直接：python main.py
```

默认分析 `000001.SZ`（平安银行）。修改 `main.py` 中的股票代码和日期即可分析其他标的。

> 现在直接运行 `./run.sh`（无参数）会一键启动 **Web 全栈**（后端平台 + 前端工作台），
> 经典 CLI 分析模式请用 `./run.sh classic`。

### Docker 启动

```bash
docker-compose up -d                          # 基础服务 (PostgreSQL + Redis + LiveProfit)
docker-compose --profile management up -d     # 含管理界面 (Redis Commander + Adminer)
```

## 平台模式启动（Web 后端：API / Worker / Dispatcher）

平台后端提供统一 Web API（REST + SSE，契约见 [docs/API契约.md](docs/API契约.md)），
与经典 CLI 分析共用 AI 内核。**仅本机 loopback 使用**（首期不支持 LAN/公网访问）。

### 方式一：一键脚本（推荐）

```bash
./run.sh                 # 一键全栈：后端平台 + 大盘数据采集 + 前端 dev server + 打开浏览器（http://localhost:5173）
                         #   启动完成后前台实时输出 api/worker/dispatcher 日志（Ctrl+C 退出查看，服务保持运行）
./run.sh stop            # 停止一键启动的前端与后端进程（不停止 Docker 基础设施）
./run.sh platform        # 仅启动平台后端（infra + 迁移 + API/Worker/Dispatcher）
./run.sh ingest-market   # 仅采集 CN 指数日线（大盘数据，幂等；一键启动时自动执行）
```

### 方式二：手动分步（本地开发）

```bash
# 1. 基础设施
docker compose up -d

# 2. 依赖（platform 依赖组）
pip install -e ".[platform,test]"

# 3. 数据库迁移（只新增平台表，不动事件研究既有表）
alembic upgrade head

# 4. 三个进程（各自终端窗口）
liveprofit-api          # FastAPI：REST + SSE（127.0.0.1:8000）
liveprofit-worker       # Dramatiq 分析 Worker（单进程单线程运行 AI 图）
liveprofit-dispatcher   # Outbox Dispatcher（发布/租约恢复/业务重试唯一调度者）
```

### 方式三：全栈容器（含前端 Nginx）

```bash
./run.sh stack                              # 构建前端产物后 compose --profile app up（Nginx 127.0.0.1:${FRONTEND_HOST_PORT:-3000}）
# 等价手动方式：
#   pnpm --dir frontend build
#   docker compose --profile app up -d --build
```

### 启动后检查

| 入口 | 地址 | 说明 |
|------|------|------|
| 存活检查 | http://127.0.0.1:8000/health/live | 进程存活 |
| 就绪检查 | http://127.0.0.1:8000/health/ready | PG + Redis 依赖就绪 |
| API 文档 | http://127.0.0.1:8000/docs | FastAPI Swagger（OpenAPI v1 另见 backend/openapi/openapi.v1.json） |
| 指标 | http://127.0.0.1:8000/metrics | Prometheus 文本格式 |

### 配置要求

- `.env` 需包含平台连接配置：`LIVEPROFIT_DATABASE_URL` / `LIVEPROFIT_REDIS_URL`，
  未配置时自动回退到 `PG_*` / `REDIS_*` 变量（注意 `localhost` 会自动归一化为 `127.0.0.1`）。
- run.sh 默认以 `HF_HUB_OFFLINE=1` 启动（本地已缓存 HuggingFace 模型时跳过联网检查，
  避免国内网络下启动超时重试；可在 `.env` 中显式设 `HF_HUB_OFFLINE=0` 覆盖）。
- LLM 透传沿用内核变量：`LIVEPROFIT_API_KEY` / `LIVEPROFIT_BASE_URL` / `LIVEPROFIT_QUICK_MODEL` / `LIVEPROFIT_DEEP_MODEL`。
- 进程日志：`logs/api.log` / `logs/worker.log` / `logs/dispatcher.log`。

## 前端（Web 投研工作台）

`frontend/` 是独立的 React + TypeScript 工程（Node 22+ / pnpm），与平台后端通过版本化 OpenAPI
（`backend/openapi/openapi.v1.json` → `frontend/src/api/generated/`，契约见 [docs/API契约.md](docs/API契约.md)）对接。

| 页面 | 路由 | 说明 |
|------|------|------|
| 大盘 | `/market` | 市场（宏观指数）/ 板块（热门概念）/ 信息（宏观信息）三固定区块 |
| 自选 | `/watchlist` | 自选分组/标的 与 手工组合/持仓 两独立资源区 |
| AI 投研看板 | `/ai` | 待处理 / 进行中 / 最近结论 / 新建分析 |
| AI 任务中心 | `/ai/tasks` | 全量任务摘要 + 服务端状态筛选 + Cursor 分页 |
| 任务详情 | `/ai/tasks/:taskId` | 任务 REST 状态 + SSE 进度恢复 + 最新结构化报告 |
| 事件研究 | `/ai/event-study` | 单次同步影响预测（不创建分析任务） |

技术栈：React 19 · TypeScript · TanStack Query · Zustand · Tailwind CSS（shadcn 风格组件）· ECharts · Vitest/RTL · Playwright。

### 运行方式（已集成到 run.sh）

```bash
# 一键全栈（推荐）：后端平台 + 大盘数据采集 + 前端 dev server + 自动打开浏览器
./run.sh                         # Web 工作台 http://localhost:5173（/api 代理 → 127.0.0.1:8000）
./run.sh stop                    # 停止全部
./run.sh ingest-market           # 单独补采大盘数据（幂等）

# 仅前端开发模式（Vite dev server，需后端已运行：./run.sh platform）
./run.sh frontend-dev            # 浏览器打开 http://localhost:5173

# 前端质量检查（typecheck + 单测 + 构建）
./run.sh frontend-check

# E2E 端到端测试（需后端已运行；自动拉起 dev server）
# 注意：「任务闭环」用例会创建真实分析任务（触发真实 LLM），建议用受控/假 Worker 环境
./run.sh frontend-e2e
# 对 Compose 全栈（Nginx 3000 端口）跑 E2E：
#   FRONTEND_BASE_URL=http://127.0.0.1:3000 ./run.sh frontend-e2e

# 全栈容器（infra + API/Worker/Dispatcher + 前端 Nginx）
./run.sh stack                   # 访问 http://127.0.0.1:3000
```

前端手动命令（不经过 run.sh）：`pnpm --dir frontend dev / test / build / generate:api / e2e`。
OpenAPI 变更后重新生成前端 client：`pnpm --dir frontend generate:api`（生成物提交，禁止手写领域 DTO）。

## 配置参考

| 环境变量 | 说明 | 默认值 |
|---------|------|--------|
| `LIVEPROFIT_API_KEY` | OpenAI 兼容 API 密钥 | - |
| `LIVEPROFIT_BASE_URL` | API 端点地址 | `https://api.openai.com/v1` |
| `LIVEPROFIT_QUICK_MODEL` | 快速思考模型 | `gpt-4o-mini` |
| `LIVEPROFIT_DEEP_MODEL` | 深度思考模型 | `gpt-4o` |
| `LIVEPROFIT_QUICK_TEMPERATURE` | 快速模型温度 | `0.7` |
| `LIVEPROFIT_DEEP_TEMPERATURE` | 深度模型温度 | `0.3` |
| `LIVEPROFIT_MAX_TOKENS` | 最大 Token 数 | `8192` |
| `LIVEPROFIT_DATA_SOURCE` | 数据源 (tushare / akshare) | `tushare` |
| `TUSHARE_TOKEN` | Tushare API Token | - |
| `LIVEPROFIT_MEMORY_ENABLED` | 启用 ChromaDB 记忆 | `true` |
| `LIVEPROFIT_MEMORY_PATH` | ChromaDB 存储路径 | `./chroma_db` |
| `LIVEPROFIT_MAX_DEBATE_ROUNDS` | 最大辩论轮数 | `1` |
| `LIVEPROFIT_MAX_RISK_ROUNDS` | 最大风险讨论轮数 | `1` |
| `LIVEPROFIT_LOG_LEVEL` | 日志级别 | `INFO` |
| `REDIS_ENABLED` | 启用 Redis 缓存 | `false` |
| `REDIS_CONNECTION_STRING` | Redis 连接串 | - |
| `TA_CACHE_STRATEGY` | 缓存策略 (integrated / file) | `file` |

## LLM 提供商

LiveProfit 使用 `langchain-openai` 的 `ChatOpenAI`，兼容任何 OpenAI 兼容 API：

- **OpenAI**: `LIVEPROFIT_BASE_URL=https://api.openai.com/v1`
- **DeepSeek**: `LIVEPROFIT_BASE_URL=https://api.deepseek.com/v1`
- **OpenRouter**: `LIVEPROFIT_BASE_URL=https://openrouter.ai/api/v1`
- **Groq**: `LIVEPROFIT_BASE_URL=https://api.groq.com/openai/v1`
- **Ollama (本地)** : `LIVEPROFIT_BASE_URL=http://localhost:11434/v1`
- **其他** : 任何兼容 OpenAI 格式的 API

## 数据源

| 数据源 | 环境变量值 | 需要 Token | 支持范围 |
|--------|-----------|-----------|---------|
| Tushare | `tushare` | 是 | A 股行情、基本面、新闻、指数 |
| AKShare | `akshare` | 否（免费） | A 股行情、基本面、新闻、**全球指数** |

- Tushare 注册：https://tushare.pro/
- AKShare 无需注册，直接使用

### 全球科技指数（需 AKShare）

科技市场分析师支持获取以下全球指数：
- **美股**：纳斯达克综合指数、纳斯达克100、费城半导体指数 (SOX)
- **韩国**：KOSPI、科斯达克 (KOSDAQ)
- **A股**：科创50、创业板指、半导体芯片指数、AI指数
- **AI产业链细分**：存储芯片、半导体、光模块、AI服务器、先进封装、算力、AI应用、机器人、智能汽车（共 9 个概念板块）

基于前10天数据的相关性分析预测明日走势。

## 存储架构

参考 TradingAgents-CN 的三级存储体系：

```
LiveProfit
 ├── Redis (端口 6379, 可选)
 │    ├── 行情缓存 (TTL: 1h)
 │    ├── 新闻缓存 (TTL: 4h)
 │    └── 基本面缓存 (TTL: 12h)
 │
 ├── ChromaDB (持久化: ./chroma_db/)
 │    ├── bull_memory / bear_memory
 │    ├── trader_memory / invest_judge / risk_manager
 │    └── 嵌入: text-embedding-3-small (OpenAI)
 │
 └── File Cache (dataflows/cache/data_cache/)
      ├── china_stocks/      (TTL: 1h)
      ├── china_news/        (TTL: 4h)
      └── china_fundamentals/(TTL: 12h)
```

## 项目结构

```
LiveProfit/
├── README.md
├── a.bash                      # 配置文件 (source 此文件)
├── pyproject.toml              # Python 项目配置与依赖
├── main.py                     # 入口
├── Dockerfile                  # Docker 镜像
├── docker-compose.yml          # Docker 服务编排 (PostgreSQL + Redis + LiveProfit)
└── liveprofit/
    ├── default_config.py       # 环境变量 → 配置字典
    ├── config/                 # 配置层
    │   ├── providers_config.py    # 数据源配置 (Tushare/AKShare)
    │   └── env_utils.py           # 环境变量解析
    ├── graph/                  # LangGraph 编排
    │   ├── trading_graph.py      # 主编排器
    │   ├── setup.py              # StateGraph 构建
    │   ├── conditional_logic.py  # 条件路由
    │   ├── propagation.py        # 状态初始化
    │   ├── reflection.py         # 反思与记忆
    │   └── signal_processing.py  # 信号提取
    ├── agents/                 # LLM 智能体 (12 个)
    │   ├── analysts/           # 5 分析师 (市场/基本面/新闻/情绪/科技)
    │   ├── researchers/        # 2 研究员 (多头/空头)
    │   ├── managers/           # 2 经理 (研究/风险)
    │   ├── risk_mgmt/          # 3 风险 (激进/保守/中性)
    │   ├── trader/             # 1 交易员
    │   └── utils/              # 工具、状态、ChromaDB 记忆
    ├── dataflows/              # 数据层
    │   ├── interface.py        # 数据接口 (含缓存)
    │   ├── providers/          # 数据提供器 (Tushare + AKShare)
    │   ├── cache/              # 缓存层 (File/DB/Adaptive/Integrated)
    │   └── technical/          # Stockstats 技术指标
    └── utils/                  # 股票工具、日志
```

## 与 TradingAgents-CN 的差异

| 特性 | TradingAgents-CN | LiveProfit |
|------|-----------------|------|
| LLM 提供商 | 13+ (含适配器层) | 1 (OpenAI 兼容) |
| 数据源 | 10+ (中/港/美) | 2 (Tushare / AKShare) |
| 配置文件 | MongoDB + JSON + .env | `a.bash` (环境变量) |
| 缓存层 | MongoDB + Redis + File | Redis + File |
| ChromaDB | 非持久化 + 7 嵌入提供商 | 持久化 + OpenAI 嵌入 |
| Python 文件数 | ~112 | ~57 |
| API/Web UI | FastAPI + Streamlit | 无 |
| Docker | 5 服务 (含前后端) | 2 服务 (仅后端存储) |

## 输出示例

```python
{
    'action': '持有',
    'target_price': 12.5,
    'confidence': 0.75,
    'risk_score': 0.4,
    'reasoning': '基于综合分析，公司基本面稳健但短期技术面偏弱...'
}
```

## License

MIT
