# Agent 输出格式外置为 md 文件技术方案

> **状态**：✅ 已完成（2026-08-12）
> **进度**：8/8 步骤 + Code Review（1 Warning 已修复）
> **下一步**：移入 docs/done/ 归档
>
> **Review 修复记录**：`sector_rotation_analyst.py` 常量截断拼接处多一个空行 + 结尾丢换行，已修复并逐字符复验与原文一致。
>
> **关联文档**：[../index.md](../../index.md)

---

## 一、背景与动机

### 1.1、现状

- 已核实：`AI/marketAgents`（8个）、`AI/sectorAgents`（3个）、`AI/stockAgents/analysts`（4个），共 **15 个 analyst 文件**使用 `ChatPromptTemplate`，prompt 结尾有独立的"输出格式"段落
- 已核实：`international_event_extraction.py` 内部有 **2 次 LLM 调用**（line 78、line 103），各自有独立输出格式
- 格式文本以 Python 字符串硬编码在 `.py` 中，调整格式要改代码
- **关键约束（已核实）**：6 个文件的输出格式被代码级正则依赖，**不能改动格式结构**：
  - `cn_tech_analyst.py:150` `_extract_market_regime` → `market_regime` 字段
  - `cn_news_analyst.py:131` `_extract_calendar_overview` → `market_event_calendar` 字段
  - `sector_news_analyst.py:157` → `sector_shortlist` 字段
  - `sector_rotation_analyst.py:221` → `rotation_top_picks` 字段
  - `sector_tech_analyst.py:181` `_extract_sector_tech_confirm` → `sector_tech_confirm` 字段
  - `international_event_extraction.py:120` `_extract_events_desc` → 提取 `【事件描述摘要】` 块喂给历史案例检索
- 注意：每个提取函数实际有 **2 个正则**——先匹配 ``` 代码块，再匹配 〇 段落。所以 ``` 块和 〇 段都受保护，实施时逐字符保留

### 1.2、目标

输出格式文本从 `.py` 抽离到独立 `.md` 文件，运行时通过 `load_output_format()` 读取。同时**合并结构趋同的格式**：us_tech + kr_tech、us_news + kr_news 各自合成一个"通用国家"共享模板（这两对无下游正则依赖，已核实）。未来新增国家（JP/EU 等）的 analyst 直接复用这些通用模板。

---

## 二、架构设计

- 架构图：不改动现有图谱/Agent 架构，仅在 prompt 构建环节增加一步 `load_output_format()` 调用，替换原硬编码的格式文本
- 设计原则：独立迁移的文件不改一字一句；合并的文件采用通用章节名，国家专属标题保留在 `.py` 中（一行字符串拼接）

### 2.1 数据模型设计

> 涉及共享状态或跨模块接口变更时编写

不适用——本方案不新增 State 字段，不改变跨模块接口签名。

---

## 三、详细设计

### 3.1 templates/ 目录与 md 文件

#### 3.1.1 模块设计

新建 `AI/templates/` 目录（定位：输出格式模板库，不是 prompt 库），共 **14 个 md 文件**（2 合并 + 12 独立）：

```
AI/templates/
├── __init__.py                          # load_output_format() 加载函数
├── market/
│   ├── news_common.md                   # ★ 合并：非中国国家新闻类通用模板（现服务 us/kr，未来 JP/EU 复用）
│   ├── tech_common.md                   # ★ 合并：非中国国家技术类通用模板（现服务 us/kr，未来 JP/EU 复用）
│   ├── cn_news_analyst.md               # 独立（逐字符保留）
│   ├── cn_tech_analyst.md               # 独立（逐字符保留）
│   ├── international_event_extraction.md        # 独立（逐字符保留：含【事件描述摘要】块）
│   ├── international_event_extraction_final.md
│   └── international_news_analyst.md
├── sector/
│   ├── sector_news_analyst.md           # 独立（逐字符保留）
│   ├── sector_rotation_analyst.md       # 独立（逐字符保留）
│   └── sector_tech_analyst.md           # 独立（逐字符保留）
└── stock/
    ├── fundamentals_analyst.md
    ├── market_analyst.md
    ├── news_analyst.md
    └── social_media_analyst.md
```

**合并模板内容**（国家专属标题保留在 `.py`，md 只含章节骨架）：

`tech_common.md`（3 节——保留数据驱动维度，不止指数）：

```markdown
## 一、主要指数
（逐指数：趋势、均线、量能、RSI/MACD）
## 二、市场结构与风格
（板块轮动 / 资金流向 / 跨市场相关性）
## 三、综合研判
请使用中文。
```

`news_common.md`（4 节——覆盖权重股动态与 VIX 情绪两类内容）：

```markdown
## 一、政策与宏观事件
## 二、经济数据与产业
## 三、权重股与市场情绪
## 四、影响判断
请使用中文。
```

**独立迁移的文件**：内容 = 原 `.py` 中"输出格式"之后的整段文本，原样迁移。

#### 3.1.2 三方依赖能力评估

- `pathlib.Path.read_text()` — Python 标准库
- 本模块不依赖外部库/API

#### 3.1.3 风险与验证方式

- 风险：迁移/合并过程中文本复制产生偏差；正则依赖文件的 ``` 块和 〇 段被误改
- 验证：独立文件逐文件 diff——md 内容 与 原 `.py` 硬编码文本完全一致；合并文件按 3.3 节的 diff 方式验证

#### 3.1.4 文件变更清单

| 文件 | 改动 |
|------|------|
| `AI/templates/__init__.py` | 新建 |
| `AI/templates/market/*.md` | 新建 7 个（2 合并 + 5 独立） |
| `AI/templates/sector/*.md` | 新建 3 个 |
| `AI/templates/stock/*.md` | 新建 4 个 |

### 3.2 加载模块 (`__init__.py`)

#### 3.2.1 模块设计

```python
import functools
from pathlib import Path

_TEMPLATES_DIR = Path(__file__).parent

@functools.lru_cache(maxsize=None)
def load_output_format(layer: str, template_name: str) -> str:
    """读取指定层级、指定 agent 的输出格式模板。"""
    path = _TEMPLATES_DIR / layer / f"{template_name}.md"
    return path.read_text(encoding="utf-8").strip()
```

- `lru_cache` 避免每次调用 agent 都读磁盘；热更新场景暂不实现
- `encoding="utf-8"` 必须显式指定（Windows 默认 cp936 会读乱中文）
- 文件缺失直接抛 `FileNotFoundError`，避免"输出格式悄悄变空"

#### 3.2.2 三方依赖能力评估

- `pathlib` + `functools` — Python 标准库，无外部依赖

#### 3.2.3 风险与验证方式

- 风险：低。标准库功能，无 I/O 复杂逻辑
- 验证：单元测试（`tests/templates/test_templates.py`）——临时 md 文件 → `load_output_format()` 读取 → 断言内容一致；删除文件后调用断言抛出 `FileNotFoundError`

#### 3.2.4 文件变更清单

| 文件 | 改动 |
|------|------|
| `AI/templates/__init__.py` | 新建 |
| `pyproject.toml` | 新增 `[tool.setuptools.package-data] "AI.templates" = ["*.md"]`——否则非 editable 安装（wheel）会丢失全部 md 文件，运行时 `FileNotFoundError` |

### 3.3 Agent 文件改造（15 个文件）

#### 3.3.1 模块设计

**11 个独立迁移的 agent**（原样替换）：

```python
from AI.templates import load_output_format

# node 函数内：
output_format = load_output_format("market", "cn_tech_analyst")
prompt = ChatPromptTemplate.from_messages([
    ("system",
        "你是一位...\n\n"
        + date_line + "\n"
        ...
        + output_format  # 替换原来硬编码的"输出格式：\n# ..."整段
    ),
    MessagesPlaceholder(variable_name="messages"),
])
```

**4 个合并迁移的 agent**（us_tech / kr_tech / us_news / kr_news）：国家专属标题保留在 `.py`，章节骨架从通用模板加载：

```python
# us_tech_analyst.py
output_format = load_output_format("market", "tech_common")
prompt = ChatPromptTemplate.from_messages([
    ("system",
        ...
        "输出格式：\n"
        "# 美国市场技术分析报告\n\n"
        + output_format  # "## 一、主要指数\n..."
    ),
    ...
])
```

**`international_event_extraction.py` 特殊处理**：2 次 LLM 调用分别加载：

```python
fmt1 = load_output_format("market", "international_event_extraction")
fmt2 = load_output_format("market", "international_event_extraction_final")
```

#### 3.3.2 三方依赖能力评估

- `AI.templates` — 本方案新建的内部模块（stdlib-only，无循环导入风险：`AI/__init__.py` 只有 docstring）
- `langchain_core.prompts.ChatPromptTemplate` — 已有依赖，不受影响
- 不新增外部依赖

#### 3.3.3 风险与验证方式

- 风险 1（独立迁移）：字符串拼接时引入多余空白。验证：改造前后 diff 完整 system prompt 字符串，必须完全一致。
- 风险 2（正则依赖文件的 ``` 块和 〇 段）：必须逐字符保留。验证：diff + 跑现有集成测试。
- 风险 3（合并迁移）：us/kr tech/news 的 LLM 输出结构变化。验证：改造后各跑一次 us_tech / kr_tech / us_news / kr_news，人工检查报告质量。
- 验证总闸门：`pytest tests/agents/` + `pytest tests/templates/`

#### 3.3.4 文件变更清单

**修改文件（15 个）：**

| 文件 | 改动方式 |
|------|---------|
| `AI/marketAgents/analysts/us_tech_analyst.py` | 合并：加载 `market/tech_common`，标题保留在 .py |
| `AI/marketAgents/analysts/kr_tech_analyst.py` | 合并：加载 `market/tech_common`，标题保留在 .py |
| `AI/marketAgents/analysts/us_news_analyst.py` | 合并：加载 `market/news_common`，标题保留在 .py |
| `AI/marketAgents/analysts/kr_news_analyst.py` | 合并：加载 `market/news_common`，标题保留在 .py |
| `AI/marketAgents/analysts/cn_news_analyst.py` | 原样迁移（逐字符保留） |
| `AI/marketAgents/analysts/cn_tech_analyst.py` | 原样迁移（逐字符保留） |
| `AI/marketAgents/analysts/international_news_analyst.py` | 原样迁移 |
| `AI/marketAgents/analysts/international_event_extraction.py` | 原样迁移（2 段格式各 1 个 md；摘要块逐字符保留） |
| `AI/sectorAgents/analysts/sector_news_analyst.py` | 原样迁移（逐字符保留） |
| `AI/sectorAgents/analysts/sector_tech_analyst.py` | 原样迁移（逐字符保留） |
| `AI/sectorAgents/analysts/sector_rotation_analyst.py` | 原样迁移（逐字符保留） |
| `AI/stockAgents/analysts/fundamentals_analyst.py` | 原样迁移 |
| `AI/stockAgents/analysts/market_analyst.py` | 原样迁移 |
| `AI/stockAgents/analysts/news_analyst.py` | 原样迁移 |
| `AI/stockAgents/analysts/social_media_analyst.py` | 原样迁移 |

**不改动的文件（8 个）**：`stockAgents/researchers/*`（2个）、`stockAgents/managers/*`（2个）、`stockAgents/trader/*`（1个）、`stockAgents/risk_mgmt/*`（3个）——f-string 模式，无独立输出格式段落。

**实施完成后按 CLAUDE.md 工作流程**：启动 subagent code review → 方案移入 `docs/done/` 归档 → 架构变更同步进主干文档（`docs/`）。

---

## 四、已确认决策 / 待确认问题

### 已确认决策

| # | 决策 |
|---|------|
| 1 | 新建 `AI/templates/` 目录（输出格式模板库），按 `market/sector/stock` 三层分子文件夹 |
| 2 | 加载函数 `load_output_format(layer, template_name)` 带 `lru_cache`，文件缺失抛 `FileNotFoundError` |
| 3 | 只抽取"输出格式"段落；模式 B 的 8 个辩论/决策文件不做改造 |
| 4 | 独立迁移的文件原样迁移，不改一字一句 |
| 5 | **非中国国家通用模板**：`tech_common.md`（3 节：主要指数/市场结构与风格/综合研判）+ `news_common.md`（4 节通用结构），现服务 us/kr，未来 JP/EU 等新国家直接复用；国家专属标题保留在 `.py` |
| 6 | **6 个正则依赖文件保持独立且逐字符保留**：cn_tech / cn_news / sector_news / sector_rotation / sector_tech / international_event_extraction（摘要块） |
| 7 | 共 14 个 md 文件（2 合并 + 12 独立，`international_event_extraction` 拆 2 个） |
| 8 | `pyproject.toml` 加 package-data 一行，保证 wheel 安装不丢 md 文件 |

### 待确认问题

无阻塞项。us/kr 合并模板的章节名已按 review 修正版确定；实施后跑一次人工检查输出质量即可。
