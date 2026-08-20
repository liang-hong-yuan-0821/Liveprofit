# Python 工作区约定

## 工作流程规则

### docs/ 目录结构

```
docs/
├── index.md              ← 主干：三层金字塔架构总览（持续演进）
├── 市场层.md             ← 主干：市场层架构定义
├── 板块层.md             ← 主干：板块层架构定义
├── 个股层.md             ← 主干：个股层架构定义
├── template/             ← 文档模板（起草新方案时复制使用）
│   └── 技术方案文档模板.md
├── plans/                ← 进行中的复杂任务方案（每个文件 = 一个任务）
└── done/                 ← 已完成的方案归档（历史参考，不删除）
    ├── 市场层重构方案.md
    ├── 板块层技术方案.md
    ├── 板块轮动预测分析方案.md
    └── 技术指标T-1交易日限制梳理方案.md
```

**两类文档的定位：**

| 类型 | 位置 | 生命周期 | 示例 |
|------|------|----------|------|
| **主干架构文档** | `docs/*.md`（除 template/、plans/、done/） | 持续演进，随项目更新 | 市场层.md、板块层.md |
| **文档模板** | `docs/template/*.md` | 稳定，起草新方案时复制使用 | 技术方案文档模板.md |
| **进行中方案** | `docs/plans/*.md` | 任务驱动，实施完成后移入 done/ | — |
| **已完成方案** | `docs/done/*.md` | 归档保留，作为设计决策的历史参考 | 市场层重构方案.md |

### 工作流程

1. **复杂任务（重构/新架构/多文件变更）**
   - **每个复杂任务 = `docs/plans/` 下的一个方案文件**，该文件即为该任务的唯一追踪载体
   - 先写 `docs/plans/<方案名>.md`，包含：背景动机、设计思路、涉及文件清单、接口/字段变更、向后兼容、验证方法
   - **除非能确保 95% 实现无误，否则必须不断向我澄清问题**
   - **方案写完（澄清完毕）后，自动进入评审循环**（见下方"评审循环规则"）：换新 agent 多轮评审直到 verdict PASS 且无 blocker/major，修完剩余 minor + 过自检清单收尾；**评审通过后才请我确认**，我确认后开始实现
   - **实时状态更新**：实现过程中，每完成一个关键步骤（如：State 字段新增完毕、某个 Agent 写完、子图编译通过），**立即更新方案文件顶部的状态块**，记录当前进度和下一步
   - **代码写完之后，启动 subagent 做 code review**（见下方"Code Review 规则"）
   - **实现完成 + review 通过后，将方案中的架构变更整合进主干文档（`docs/*.md`），方案文件状态更新为"已完成"，移入 `docs/done/` 归档**

2. **中小改动（单文件、加函数、修 bug）**
   - 直接改代码，不需要 plans 文档
   - 如果改动改变了主干文档中描述的结构/接口，同步更新主干文档

3. **Trivial 改动（错别字、格式化、单行修复）**
   - 直接改，无需任何文档

### Plans 文件约定

- 每个 `docs/plans/<方案名>.md` 代表一个独立的复杂任务
- 文件顶部必须有状态块，格式：

  ```
  > **状态**：<当前阶段>（<最后更新时间>）
  > **进度**：<已完成>/<总步骤> 步骤
  > **下一步**：<接下来要做什么>
  ```

- 状态取值：`方案设计` → `评审中`（写完自动进入评审循环）→ `待确认` → `实现中` → `Code Review` → `已完成`
- **实现过程中每完成一个关键步骤，必须更新状态块的进度和下一步**
- 任务完成后状态改为 `已完成`，**文件移入 `docs/done/`** 归档，架构变更同步进主干文档
- **正文章节结构**：复制 [docs/template/技术方案文档模板.md](docs/template/技术方案文档模板.md) 到 `docs/plans/<方案名>.md`，取舍规则详见模板文件末尾速查表。已有历史方案不做回填改造

### Code Review 规则

**每次复杂任务的代码写完之后，必须启动 subagent 做 code review**，review 范围以方案文件的"文件变更清单"为准。

流程：

1. 方案文件状态更新为 `Code Review`
2. 启动 subagent（type: `claude`），prompt 包含：
   - 方案文件路径（含变更清单和设计意图）
   - 逐文件对照方案检查：字段命名一致性、接口签名匹配、边界条件处理、向后兼容、缺失占位
   - **代码逻辑正确性**：函数入参/出参是否与调用方匹配、条件分支是否覆盖所有情况、状态流转是否符合设计、是否存在死代码或不可达路径、异常处理是否到位
3. Review 发现的问题在修复后重新 review（最多 2 轮）
4. Review 通过后状态更新为 `已完成`，执行主干文档合并

> 中小改动不需要 code review。

### 评审循环规则（修复 → 换新 agent 评审 → 直到通过）

适用：任何评审驱动的修复循环（方案文档评审、Code Review 发现问题的修复、其他 agent 评审场景）。**复杂任务的方案文件写完（澄清完毕）后自动触发本循环**，评审通过前不进入实现。

1. 修复完成后，启动 subagent（type: `claude`）做独立评审，prompt 需给出：被评审文件路径、修复背景（此前发现的问题清单）、评审维度、输出格式（verdict: PASS/FAIL + 按 severity 分级的 findings）
2. **每轮评审必须换新 agent**（不复用已完成评审的 agent，保证独立视角）
3. **评审范围以 delta 为主**：默认只核验"本轮修复的落地情况 + 修复点与周边文字的交互"；全文档无差别重读每 3 轮或 verdict 变更时才做一次——每轮全文重读会让文档越长、可挑刺的表面积越大，导致 minor 无限循环
4. **findings 输出约束**：minor 必须区分"影响实施一致性"与"纯润色"（措辞/示例数值/格式样例），纯润色归并为一条；每轮 findings 上限 8 条，按影响排序
5. **停止条件**：verdict PASS 且无 blocker/major → 主会话一次性修完剩余 minor + 过自检清单（数值推导自洽 / 章节交叉引用措辞同步 / 编号连续 / 测试落点与承诺一一对应 / 新文案与既有约定一致）后**收尾，不再开新轮**；仅当新发现"影响实施一致性"的问题时才再开一轮
6. **轮数上限**：通常 ≤3 轮收敛；超过 5 轮仍未干净 → 停下向用户汇报每轮发现类型的分布趋势，询问是否继续
7. 评审 agent 只读，不修改文件

### CLAUDE.md 自我更新规则

- **每次完成一个任务/分析（方案评审收尾、实现完成、Code Review 通过、踩坑解决）后，检查是否有值得沉淀进 CLAUDE.md 的内容**，有则直接更新，无需用户提醒
- 值得写入：新确认的约定或决策、踩过的坑与规避方法、新数据源/新端点的用法（如 Tushare 代理端点）、评审循环暴露的规则缺陷、用户明确要求"记住"的内容
- 不写入：任务本身的状态与进度（属于方案文件状态块）、一次性命令与临时信息、可由代码/git 推导的事实

### Data Provider 接口约定

**接口契约以 `BaseStockDataProvider` 基类为准**（[base_provider.py](AI/dataflows/providers/base_provider.py)）。
所有 Provider（AKShare、Tushare、未来新增）继承该基类。

**设计原则：**
- 基类定义完整接口 + 默认"不支持"返回 → 子类按需覆写
- `interface.py` 通过 `hasattr(prov, 'method_name')` 动态检测可用方法
- 新增数据能力时，**先在基类加方法签名 → 优先在 TushareProvider 覆写**（AKShare 仅在 Tushare 无法覆盖时补充，见强制规则 2）

**强制规则：**

1. **新增方法必须先加到基类** `BaseStockDataProvider`，提供默认 `_not_supported()` 返回
2. **优先 Tushare 实现**：新数据能力默认只在 TushareProvider 覆写；仅当 Tushare 无对应接口/权限、且 AKShare 有对应能力时才在 AKShareProvider 覆写（如 AKShare 独有接口）。两 provider 都覆写时签名完全一致（参数名、默认值、返回类型）
3. **无法提供数据时**，不覆写基类方法即可（自动返回 `"数据不可用：{provider_name} 不支持 <功能>。"`）
4. **仅 `get_stock_data` 和 `get_stock_info` 为抽象方法**（`@abstractmethod`），子类必须实现
5. **返回格式统一为 `str`**（格式化 Markdown），仅 `get_stock_info` 返回 `dict`
6. **类属性** `GLOBAL_TECH_INDICES`、`AI_INDUSTRY_CHAIN`、`A_SHARE_CONCEPT_MAP` 在基类中定义为空 `dict`，子类覆写

### 三方依赖能力评估

- **技术方案文档必须包含三方依赖能力评估**：逐项确认方案所依赖的第三方库（AKShare、LangGraph、LangChain 等）是否有足够能力实现诉求
- 评估要点：
  - **不仅看端点是否存在，更要看能否拿到所需数据**：单个 API 能否直接满足？还是需要多个 API 组合（如 IPO 日历 = 申购 + 上市两个接口；资金流向 = 北向 + 主力两个接口）？组合后的覆盖是否完整？
  - 每个新增数据函数的入参和出参与 AKShare/Tushare 端点的实际签名是否匹配（参数名、日期格式、返回字段）
  - LangGraph 的 subgraph / StateGraph 机制是否支持方案的图谱架构
  - 需占位的工具明确标注"占位 + TODO + 缺失原因"，不假装可实现
- 如果某个依赖能力不确定（端点覆盖不全、字段缺失、需要降级），必须在方案中标记为风险并给出降级策略
- 此评估写入方案文档的独立章节（如"三方依赖能力评估"）

## 项目配置

### pyproject.toml 优先
- **所有 Python 项目使用 `pyproject.toml` 管理依赖**，不使用 `requirements.txt` 作为主要依赖声明
- 构建后端：`setuptools.build_meta`（`setuptools>=61.0`）
- 安装命令：`pip install -e .` 或 `uv pip install -e .`


## 项目结构约定

```
项目根/
├── pyproject.toml          # 项目配置（必需）
├── README.md
├── main.py                 # 入口（被 pyproject.toml 的 scripts 引用）
└── 包名/                   # Python 包（与 pyproject.toml name 对应）
```

## 环境配置
- 使用 `.env` 或 `.bash` 文件管理环境变量
- `python-dotenv` 用于加载 `.env` 文件
- 敏感信息（API Key）不提交到 Git

### Tushare 代理端点（自定义 URL）

- 位置：`TushareProvider._connect()`（`AI/dataflows/providers/tushare_provider.py`）——`ts.set_token(TUSHARE_TOKEN)` + `ts.pro_api()` 之后，覆写私有属性指向自定义端点：

  ```python
  self.api = ts.pro_api()
  self.api._DataApi__http_url = "https://ts.gyzcloud.top/api"  # 自定义 Tushare 端点
  ```

- `_DataApi__http_url` 是 name-mangled 私有属性，写法必须保持双下划线形式；换回官方端点删掉该行即可（默认 `http://api.tushare.pro`）
- Token 通过 `.env` 的 `TUSHARE_TOKEN` 配置（`LIVEPROFIT_DATA_SOURCE=tushare` 时生效）
- **代理端点能力可能与官方有差异** → 新增数据函数做"三方依赖能力评估"时必须对代理端点**实测**（真实 token 探测），不能只看 tushare 官方文档
