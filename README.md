# YoHo - 多智能体股票交易分析系统

基于 [TradingAgents-CN](https://github.com/TauricResearch/TradingAgents) 的简化版，使用 **LangGraph** 编排多个 LLM 智能体协作分析 A 股市场。

## 架构概览

YoHo 使用 LangGraph 构建一个多阶段分析流水线：

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
export YOHO_API_KEY="sk-your-actual-key"
export YOHO_BASE_URL="https://api.openai.com/v1"
export YOHO_QUICK_MODEL="gpt-4o-mini"
export YOHO_DEEP_MODEL="gpt-4o"

# 数据源 (二选一)
export YOHO_DATA_SOURCE="tushare"
export TUSHARE_TOKEN="your-tushare-token"
# 或免费数据源: export YOHO_DATA_SOURCE="akshare"
```

加载配置：

```bash
source a.bash
```

### 3. 运行

```bash
python main.py
```

默认分析 `000001.SZ`（平安银行）。修改 `main.py` 中的股票代码和日期即可分析其他标的。

### Docker 启动

```bash
docker-compose up -d                          # 基础服务 (PostgreSQL + Redis + YoHo)
docker-compose --profile management up -d     # 含管理界面 (Redis Commander + Adminer)
```

## 配置参考

| 环境变量 | 说明 | 默认值 |
|---------|------|--------|
| `YOHO_API_KEY` | OpenAI 兼容 API 密钥 | - |
| `YOHO_BASE_URL` | API 端点地址 | `https://api.openai.com/v1` |
| `YOHO_QUICK_MODEL` | 快速思考模型 | `gpt-4o-mini` |
| `YOHO_DEEP_MODEL` | 深度思考模型 | `gpt-4o` |
| `YOHO_QUICK_TEMPERATURE` | 快速模型温度 | `0.7` |
| `YOHO_DEEP_TEMPERATURE` | 深度模型温度 | `0.3` |
| `YOHO_MAX_TOKENS` | 最大 Token 数 | `8192` |
| `YOHO_DATA_SOURCE` | 数据源 (tushare / akshare) | `tushare` |
| `TUSHARE_TOKEN` | Tushare API Token | - |
| `YOHO_MEMORY_ENABLED` | 启用 ChromaDB 记忆 | `true` |
| `YOHO_MEMORY_PATH` | ChromaDB 存储路径 | `./chroma_db` |
| `YOHO_MAX_DEBATE_ROUNDS` | 最大辩论轮数 | `1` |
| `YOHO_MAX_RISK_ROUNDS` | 最大风险讨论轮数 | `1` |
| `YOHO_LOG_LEVEL` | 日志级别 | `INFO` |
| `REDIS_ENABLED` | 启用 Redis 缓存 | `false` |
| `REDIS_CONNECTION_STRING` | Redis 连接串 | - |
| `TA_CACHE_STRATEGY` | 缓存策略 (integrated / file) | `file` |

## LLM 提供商

YoHo 使用 `langchain-openai` 的 `ChatOpenAI`，兼容任何 OpenAI 兼容 API：

- **OpenAI**: `YOHO_BASE_URL=https://api.openai.com/v1`
- **DeepSeek**: `YOHO_BASE_URL=https://api.deepseek.com/v1`
- **OpenRouter**: `YOHO_BASE_URL=https://openrouter.ai/api/v1`
- **Groq**: `YOHO_BASE_URL=https://api.groq.com/openai/v1`
- **Ollama (本地)** : `YOHO_BASE_URL=http://localhost:11434/v1`
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
YoHo
 ├── Redis (端口 6379, 可选)
 │    ├── 行情缓存 (TTL: 6h)
 │    ├── 新闻缓存 (TTL: 24h)
 │    └── 基本面缓存 (TTL: 24h)
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
YoHo/
├── README.md
├── a.bash                      # 配置文件 (source 此文件)
├── pyproject.toml              # Python 项目配置与依赖
├── main.py                     # 入口
├── Dockerfile                  # Docker 镜像
├── docker-compose.yml          # Docker 服务编排 (PostgreSQL + Redis + YoHo)
└── yoho/
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

| 特性 | TradingAgents-CN | YoHo |
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
