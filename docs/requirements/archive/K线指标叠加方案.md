# K线指标叠加方案

> **状态**：已完成（2026-09-06）
> **进度**：3/3 步骤（① 指标计算模块 3.1 → ② 服务与契约 3.2 → ③ 前端 3.3）
> **下一步**：归档 docs/done/；Code Review 2 轮通过（F1 带宽几何方向 major + 3 minor 已修）；主干契约已同步 docs/API契约.md §8.2
> **关联文档**：[产品需求分析.md](../../knowledge/产品需求分析.md)（Q-01 所辖指数区块；指标叠加细节以本方案决策表为准）、[API契约.md](../../knowledge/backend/API契约.md) §8.2

---

## 一、背景与动机

### 1.1、现状

- **指数 K 线已有读模型**：`market_ingest` 采集 CN 指数日线入 `market_bars_daily`（Tushare `get_index_data_df`），`GET /market-data/indices/{symbol}/bars` 返回裸 OHLCV，前端 [CandlestickChart.tsx../../../frontend/src/shared/charts/CandlestickChart.tsx) 只渲染 candlestick 系列，无任何技术指标叠加。
- **tushare 不提供指数技术因子**：`TushareProvider` 无 `stk_factor` 方法（仅 `adj_factor` 复权因子）；tushare `stk_factor` 端点只覆盖个股 ts_code、不含指数，且代理端点（gyzcloud）能力未验证。**MA/BOLL 只能自算**（用户已确认数据由后端给出）。
- **范围收缩记录（2026-09-06）**：初版方案含"概念 K 线 + heat_v1 快照解锁"（需新建 `concept_bars_daily` 读模型表，与快照解锁耦合），用户评估成本后决定**概念相关整体后置**，拆独立方案。本次只做指数 K 线指标叠加；概念卡片维持现状（快照空态、"K 线不可用"），`HotConceptDTO` 契约零改动。

### 1.2、目标

1. 所有指数 K 线卡片（`MarketIndicesPanel`）叠加 **MA5/10/20/60 均线 + BOLL(20,2) 布林带**（含带宽填充、legend 开关），指标值由后端计算、随 bars 一起下发。
2. 契约向后兼容：旧前端忽略新字段正常渲染；新前端遇旧后端（无 indicators 字段）降级为纯 K 线，不报错。

---

## 二、架构设计

数据流（无架构变更，无新表、无 DB 迁移、无存量表字段变更）：

```
market_bars_daily（存量读模型）
        │ MarketDataService.get_bars：补窗口读取 [from−120 自然日, to] → 纯 Python 计算 MA/BOLL
        │                              → 按 trading_date >= from 切片
        ▼
BarsData.indicators（与 bars 等长、按 index 对齐；bars 空时 null）
        ▼
前端 mapper → CandlestickChart（candlestick + MA 线 + BOLL 三线一带 + legend）
```

### 2.1 数据模型设计

**无数据库变更**（不新增表/列/迁移）。仅 API 契约新增可选字段：

```json
"indicators": {
  "ma": [
    {"period": 5,  "values": [null, null, null, null, 10.1, 10.2, ...]},
    {"period": 10, "values": [...]},
    {"period": 20, "values": [...]},
    {"period": 60, "values": [...]}
  ],
  "boll": {
    "period": 20, "k": 2.0,
    "mid":   [...],
    "upper": [...],
    "lower": [...]
  }
}
```

- 各数组**与 `bars` 等长、按 index 对齐**；窗口不足（数据真实缺失）处为 `null`。
- bars 为空数组时 `indicators` 为 `null`（前端不渲染空壳图，沿用现有约定）。
- 补窗口计算保证**区间起点处指标即有值**（除非资产历史本身不足）。

---

## 三、详细设计

### 3.1 指标计算模块（后端，纯函数）

#### 3.1.1 模块设计

新建 `backend/modules/market_data/application/indicators.py`，纯 Python 实现（不依赖 pandas/stockstats，零三方依赖）：

```python
def ma(values: list[float], period: int) -> list[float | None]:
    """滚动均值：前 period-1 个位置为 None（窗口不足即 None，不伪造）。"""

def boll(values: list[float], period: int = 20, k: float = 2.0) -> dict[str, list[float | None]]:
    """返回 {"mid", "upper", "lower"}；mid = ma(period)，上下轨 = mid ± k·σ，
    σ 用总体标准差（ddof=0，与主流行情软件口径一致）；窗口不足为 None。"""

def compute_indicators(closes: list[float]) -> dict:
    """组装契约为 {"ma": [{"period": 5, "values": ...}, ...(10/20/60)],
    "boll": {"period": 20, "k": 2.0, "mid": ..., "upper": ..., "lower": ...}}。"""
```

**补窗口语义**（服务层实现，常量 `WARMUP_DAYS = 120` 自然日）：MA60 需要 59 个前导交易日 ≈ 88 自然日，另留长假休市余量（春节前后 90 自然日内可休 7-9 个交易日，最坏前导 <59），取 120 保证前导始终 ≥59。查询时向 repo 取 `[from - 120d, to]` 的 bars 计算指标，再按 `trading_date >= from` 切片返回——**返回区间仍是 [from, to]，指标在 from 处即有值**。资产历史不足 60 根时开头为 null（数据缺失是事实，不伪造）。

#### 3.1.2 三方依赖能力评估

不依赖外部库/API。纯算术。

#### 3.1.3 风险与验证方式

- 风险：切片对齐错位（补窗口序列与返回序列长度/顺序不一致）。
- 验证：**纯函数单测**（test_indicators.py）仅覆盖已知序列的 MA/BOLL 数值、窗口不足 null、空序列、短序列；**补窗口切片断言由 3.2.3 契约测试承载**（同一声明只落一份，不双写）。

#### 3.1.4 文件变更清单

- **新建**：`backend/modules/market_data/application/indicators.py`（纯函数模块）
- **新建**：`backend/tests/unit/market_data/test_indicators.py`（backend/tests/unit **已存在**，新建其下 market_data 子目录）

### 3.2 后端服务与 API 契约

#### 3.2.1 模块设计

**`MarketDataService.get_bars`**：

- bars 读取改为 `[from_date - WARMUP_DAYS, to_date]`（`WARMUP_DAYS = 120` 模块常量），构建 closes 序列 → `compute_indicators` → 按 `trading_date >= from_date` 切片 bars 与 indicators 各数组（等 index 切片）。
- `BarsDTO` 增加 `indicators: dict | None`（bars 为空 → None）。
- `freshness_status`/`as_of`/`source_updated_at` 等字段**语义不变**（基于全表 latest，与补窗口无关）。

**Schema**（`backend/api/schemas/market.py`）：

```python
class MaLineDTO(BaseModel):
    period: int
    values: list[float | None]

class BollBandsDTO(BaseModel):
    period: int
    k: float
    mid: list[float | None]
    upper: list[float | None]
    lower: list[float | None]

class IndicatorsDTO(BaseModel):
    ma: list[MaLineDTO]
    boll: BollBandsDTO

# BarsData 增加：indicators: IndicatorsDTO | None = None
# HotConceptDTO / get_hot_concepts 零改动（概念卡片不在本次范围）
```

**Router**（`backend/api/routers/market_data.py`）：`index_bars` 把 `bars_dto.indicators` 传入 `BarsData`。`hot_concepts` 端点零改动。

**向后兼容**：`indicators` 为新增可选字段（默认 None）——旧前端生成的 client 忽略该字段，新前端遇无该字段的旧后端响应走降级渲染（3.3）。

#### 3.2.2 三方依赖能力评估

无新增外部依赖。

#### 3.2.3 风险与验证方式

- 风险：补窗口切片错位（3.1.3 同源）。
- 验证：契约测试扩展 `backend/tests/contract/api/test_market_data.py`——**密集种 150 行连续自然日**（含周末；`_seed_bars` 不校验交易日，可直接造），请求 from 定位于第 91 根处 → 补窗口（from−120 自然日）内前导 ≥59 根，断言 indicators 存在、各数组与 bars 等长、**首根 MA60 非 null**、旧字段回归；另有单根 bars 用例固化"前导不足时窗口处为 null"降级语义。openapi gate（`test_openapi_gate.py`）自动保证 schema 同步（需重跑 export）。

#### 3.2.4 文件变更清单

- **修改**：`backend/modules/market_data/application/service.py`（get_bars 补窗口计算与切片）
- **修改**：`backend/api/schemas/market.py`（新增 3 个 DTO + BarsData 1 处可选字段）
- **修改**：`backend/api/routers/market_data.py`（index_bars 传参接线）
- **修改**：`backend/openapi/openapi.v1.json`（`python -m backend.scripts.export_openapi` 再生成）
- **修改**：`backend/tests/contract/api/test_market_data.py`（扩展用例）

### 3.3 前端渲染与映射

#### 3.3.1 模块设计

**`CandlestickChart.tsx`**（ViewModel 扩展 + series 组装）：

```ts
export interface CandlestickChartViewModel {
  xAxisData: string[];
  ohlc: [number, number, number, number][];
  volume?: (number | null)[];
  ma?: { period: number; values: (number | null)[] }[];   // 后端 ma 数组原样透传
  boll?: { period: number; k: number; mid: (number|null)[]; upper: (number|null)[]; lower: (number|null)[] };
}
```

series 组装顺序：`candlestick` → 各 `ma` 线（`symbol: 'none'`，lineWidth 1.5）→ BOLL 五系列：
- 3 条可见线：上轨/中轨/下轨（`symbol: 'none'`）；
- 2 条隐藏堆叠系列（ECharts 官方 Confidence Band 模式）：**下轨隐藏垫底**（`stack: 'boll-band'`, lineStyle opacity 0）→ **带宽系列**（data = upper−lower，`stack: 'boll-band'`, `areaStyle` 半透明色, lineStyle opacity 0）——同 stack 系列自底向上累计，此顺序保证填充落在 `[lower, upper]` 之间（上轨垫底会把带宽画到上轨上方并推高 y 轴，实现时严禁颠倒）。
- 颜色约定：MA5 `#fbbf24`、MA10 `#f472b6`、MA20 `#a78bfa`、MA60 `#34d399`；BOLL 三线 `#94a3b8`，带宽填充 rgba(148,163,184,0.15)。实现时按 dataviz skill 校验明暗主题对比度。
- `legend: { data: ['MA5','MA10','MA20','MA60','BOLL上轨','BOLL中轨','BOLL下轨'], top: 0 }`——用户可点按开关；`tooltip: trigger 'axis'` 自动聚合全部系列。
- `ma`/`boll` 为 `undefined` 时不加相应系列（旧后端/降级场景渲染纯 K 线，不报错）。

**`mappers/toChartViewModels.ts`**：

```ts
export function barsToCandlestickViewModel(bars: BarDTO[], indicators?: IndicatorsDTO | null): CandlestickChartViewModel | null
```

- indicators 缺失/null → `ma`/`boll` 不设置；
- 防御：indicators 各数组长度与 bars 不一致时**整体丢弃 indicators**（不渲染错位指标）；
- boll 透传 `{period, k, mid, upper, lower}`。

**`MarketIndicesPanel.tsx`**：`barsToCandlestickViewModel(bars, barsQuery.data.indicators)`。

**`HotConceptsPanel.tsx` 零改动**（概念卡片不在本次范围，继续显示"K 线不可用"）。

**OpenAPI 客户端再生成**：后端 export 后 `pnpm run generate:api`（tags 分组约定不影响 market-data）。

#### 3.3.2 三方依赖能力评估

ECharts 6（已装）：`candlestick` + `line` + 堆叠 `areaStyle` 带宽填充为官方支持模式（Confidence Band 官方示例），legend/tooltip axis 原生支持。无需新依赖。

#### 3.3.3 风险与验证方式

- 风险：MA/BOLL 系列较多（最多 10 个 series）的渲染性能（ECharts Canvas 该量级无压力，风险低）；legend 与图表绘图区共存（legend 高度 ~20px，可接受，实测观察）。
- 验证：Vitest——`CandlestickChart.test.tsx`（新建：**`vi.mock('echarts-for-react')` 捕获 option prop 后断言** series 含 candlestick + 4 MA 线 + BOLL 5 系列与 legend data；堆叠顺序断言（下轨垫底 → 带宽差值）防几何回归；无 ma/boll 时 series 数量回归为 1——jsdom 无 canvas，不 mock 会踩 echarts 初始化坑）；`toChartViewModels.test.ts` 扩展（indicators 映射、缺失降级、长度不一致丢弃）；`MarketIndicesPanel.test.tsx` 扩展 fixture（indicators 字段渲染断言）；`pnpm run typecheck` + `pnpm test`。

#### 3.3.4 文件变更清单

- **修改**：`frontend/src/shared/charts/CandlestickChart.tsx`
- **修改**：`frontend/src/modules/market/pages/mappers/toChartViewModels.ts`
- **修改**：`frontend/src/modules/market/pages/MarketIndicesPanel.tsx`
- **修改**：`frontend/src/api/generated/**`（再生成，不手改）
- **新建**：`frontend/src/shared/charts/CandlestickChart.test.tsx`
- **修改**：`frontend/src/modules/market/pages/mappers/toChartViewModels.test.ts`、`MarketIndicesPanel.test.tsx`

### 3.4 文档同步

- **修改**：`docs/API契约.md` §8.2（bars 响应增加 indicators 字段说明）；§8.3 不动（概念契约本次无变化）。
- 实现完成后按 CLAUDE.md 将本方案归档 `docs/done/` 并同步主干文档（§8.2 即主干契约）。

---

## 四、已确认决策 / 待确认问题

**已确认决策**：

| # | 决策 | 内容 |
|---|------|------|
| D1 | 均线周期 | MA5/10/20/60（2026-09-06 用户拍板） |
| D2 | 计算窗口 | 后端补窗口（warmup 120 自然日，含长假余量），返回仍为请求区间 |
| D3 | 契约形态 | indicators 并列数组（与 bars 等长按 index 对齐），BarDTO 不变 |
| D4 | 概念范围 | **整体后置**（2026-09-06 变更）：概念 K 线 + heat_v1 快照解锁拆独立方案，本次 HotConceptDTO 零改动 |
| D5 | 指标数据来源 | 后端自算（tushare 无指数技术因子，stk_factor 不覆盖） |
| D6 | BOLL 参数 | (20, 2)，总体标准差 ddof=0（产品需求分析未定参数，本方案补齐） |

**待确认问题**：无（阻塞项已全部澄清）。
