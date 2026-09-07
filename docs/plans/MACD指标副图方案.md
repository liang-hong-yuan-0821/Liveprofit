# MACD指标副图方案

> **状态**：待确认（2026-09-06）
> **进度**：0/3 步骤（① 后端 macd 纯函数 3.1 → ② 契约接线 3.2 → ③ 前端副图 3.3）
> **下一步**：评审收敛（2 轮 PASS），待用户确认后进入任务分解
> **关联文档**：[K线指标叠加方案.md](../done/K线指标叠加方案.md)（已归档，MA/BOLL 同模式扩展）、[API契约.md](../API契约.md) §8.2

---

## 一、背景与动机

### 1.1、现状

- K 线图已叠加 MA5/10/20/60 + BOLL(20,2)（[K线指标叠加方案](../done/K线指标叠加方案.md)，2026-09-06 完成归档）：后端补窗口（WARMUP_DAYS=120 自然日）纯 Python 计算，`BarsData.indicators` 并列数组契约，前端 CandlestickChart 主图渲染 + dataZoom 缩放。
- 用户提出 K 线图增加 MACD 副图——MACD 是震荡指标，业界惯例**不放主图**，而是挂在 K 线主图下方的独立副图（红绿柱 + DIF/DEA 两线），与主图共享 x 轴并联动缩放。

### 1.2、目标

1. 指数 K 线卡片下方渲染 MACD 副图（EMA(12,26,9) 标准参数）：MACD 红绿柱 + DIF/DEA 线，与主图共用 dataZoom（缩放/平移联动）。
2. MACD 数值由后端计算，随 `indicators.macd` 下发（延续既定并列数组契约模式）。
3. 向后兼容：旧后端无 `macd` 字段 → 前端不渲染副图（降级纯主图）；旧前端忽略新字段。

---

## 二、架构设计

数据流（延续 K线指标叠加方案，无架构变更）：

```
market_bars_daily → get_bars 补窗口（WARMUP_DAYS=120 不变，MACD 需 ~35 根前导 ≪ 120）
        → compute_indicators（新增 macd 纯函数）
        → BarsData.indicators.macd {fast, slow, signal, dif, dea, hist}（与 bars 等长对齐）
        → 前端 CandlestickChart：主图 grid + MACD 副图 grid（双 xAxis 联动，共用 dataZoom）
```

### 2.1 数据模型设计

**无数据库变更**。契约新增（`IndicatorsDTO` 内）：

```json
"macd": {
  "fast": 12, "slow": 26, "signal": 9,
  "dif":  [null, ..., 12.34],
  "dea":  [null, ..., 10.21],
  "hist": [null, ..., 4.26]
}
```

- 各数组与 `bars` 等长、按 index 对齐；窗口不足处为 `null`（与 ma/boll 同语义）。
- 计算口径（国内软件惯例）：EMA(close, fast) − EMA(close, slow) = DIF；DEA = EMA(DIF, signal)；hist = 2×(DIF − DEA)。EMA 首值用前 N 根 SMA 作种子（seed），DIF 从第 slow 根起有值，DEA/hist 再滞后 signal−1 根。

---

## 三、详细设计

### 3.1 后端 macd 纯函数

#### 3.1.1 模块设计

[indicators.py](../../backend/modules/market_data/application/indicators.py) 新增（纯 Python，零三方依赖）：

```python
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

def ema(values: list[float | None], period: int) -> list[float | None]:
    """指数移动平均：跳过前导 None，从首个非 null 起前 period 根用 SMA 作种子；
    后续 EMA_t = α·v_t + (1−α)·EMA_{t−1}，α = 2/(period+1)；输出与原序列等长（前导 None 保留）。"""

def macd(closes: list[float], fast: int = MACD_FAST, slow: int = MACD_SLOW, signal: int = MACD_SIGNAL) -> dict[str, list[float | None]]:
    """返回 {"dif", "dea", "hist"}：DIF = EMA(fast) − EMA(slow)（第 slow 根起有值）；
    DEA = EMA(DIF, signal)——DIF 是含前导 None 的序列，ema 内部跳过 None 滚动，滞后 signal−1 根；hist = 2×(DIF−DEA)。"""
```

- `compute_indicators` 返回增加 `"macd": {"fast": ..., "slow": ..., "signal": ..., **macd(...)}`。
- **补窗口不变**：MACD 需 slow+signal−1 ≈ 34 根前导，远小于 WARMUP_DAYS=120 覆盖的 ≥59 根，无需调整。
- 空序列/短序列：全部 None，不伪造。

#### 3.1.2 三方依赖能力评估

不依赖外部库/API。EMA 递归纯算术。

#### 3.1.3 风险与验证方式

- 风险：EMA 种子口径与主流软件不一致（SMA seed vs 首值 seed 的差异在长序列上收敛，不影响图形展示）；hist 因子 2 的惯例（国内软件 2×，个别软件 1×）——按国内惯例 2× 固定。
- 验证：单测——已知序列手算 EMA/DIF/DEA/hist 数值、窗口 null 边界（第 slow−1 根 DIF 为 None、第 slow 根有值）、空/短序列；契约测试断言 macd 数组与 bars 等长。

#### 3.1.4 文件变更清单

- **修改**：`backend/modules/market_data/application/indicators.py`（ema/macd + compute_indicators 扩展）
- **修改**：`backend/tests/unit/market_data/test_indicators.py`（macd 数值/边界用例）

### 3.2 契约接线

#### 3.2.1 模块设计

- `backend/api/schemas/market.py`：新增 `MacdDTO(fast: int, slow: int, signal: int, dif/dea/hist: list[float | None])`；`IndicatorsDTO.macd: MacdDTO | None = None`（可选——旧后端响应/未来概念路径无此字段不破坏解析）。
- `service.py` 两处改动点（[service.py:137-149](../../backend/modules/market_data/application/service.py#L137) 的 indicators 是**逐 key 显式重建的 dict**——ma 列表推导 + boll 的 mid/upper/lower 逐一 `[start:]`，不是整体透传）：
  1. 组装 dict 处**显式插入 `"macd"` 键**：`{"fast": ..., "slow": ..., "signal": ..., "dif": computed["macd"]["dif"][start:], "dea": ..., "hist": ...}`——注意 `IndicatorsDTO.macd` 带默认值 None，漏加该键不报错只会静默返回 None（前端静默降级），唯一兜底是契约测试，组装时必须显式写；
  2. dif/dea/hist 三个数组与 ma/boll 同模式做 `[start:]` 切片。
- `routers/market_data.py` 无需改动（indicators dict 整体透传）。
- openapi 再生成 + 契约测试扩展（断言 macd 存在、等长、末根 hist = 2×(dif−dea) 一致性）。

#### 3.2.2 三方依赖能力评估

无新增外部依赖。

#### 3.2.3 风险与验证方式

- 风险：切片处遗漏 macd 数组（对齐破坏）——契约测试"各数组与 bars 等长"断言直接覆盖。
- 验证：契约测试扩展 `backend/tests/contract/api/test_market_data.py`（150 行 seed 用例内追加 macd 断言）；openapi gate。

#### 3.2.4 文件变更清单

- **修改**：`backend/modules/market_data/application/service.py`（切片处补 macd 数组）
- **修改**：`backend/api/schemas/market.py`（MacdDTO + IndicatorsDTO.macd）
- **修改**：`backend/openapi/openapi.v1.json`（再生成）
- **修改**：`backend/tests/contract/api/test_market_data.py`（macd 断言）
- **修改**：`docs/API契约.md` §8.2（indicators.macd 字段说明与示例）

### 3.3 前端 MACD 副图

#### 3.3.1 模块设计

**`CandlestickChart.tsx`**（双 grid 副图）：

- ViewModel 增加 `macd?: { dif: (number|null)[]; dea: (number|null)[]; hist: (number|null)[] }`。
- 布局数值（**先定约束再给值**，按 H=240 验算：副图可视区 ≥40px、两 grid 间距 ≥6px、主图 top 32 给 legend 让位）：
  - 主图 grid：`{ left: 48, right: 16, top: 32, bottom: '36%' }` → 主图高 = 240×(1−36%)−32 = 121.6px，底缘 y = 153.6；
  - 副图 grid：`{ left: 48, right: 16, top: '67%', bottom: 34 }` → 副图高 = 240×(1−67%)−34 = 45.2px，顶缘 y = 160.8（与主图底缘间距 7.2px ≥ 6 ✓），底缘给 slider 让位。
  - 布局说明：仅指数卡 H=240 生效；概念卡（H=160）路径因 `HotConceptDTO.bars` 恒空（router 硬编码 `[]`）不渲染图表，副图不触发；未来概念 bars 落地时需按此约束重新验算小图布局。
- **option 结构级骨架**（双 grid 必须显式绑定轴，ECharts 默认 axisIndex=0 会画错 grid）：

  ```ts
  xAxis: [
    { type: 'category', data: model.xAxisData, gridIndex: 0, axisLabel: { show: false }, ... },
    { type: 'category', data: model.xAxisData, gridIndex: 1, ... },
  ],
  yAxis: [
    { scale: true, gridIndex: 0, ... },   // 主图价格轴
    { scale: true, gridIndex: 1, ... },   // 副图 MACD 轴（独立量纲，与主图不共享）
  ],
  series: [主图 10 系列（默认 xAxisIndex/yAxisIndex=0 不变）,
           { name: 'MACD柱', type: 'bar', data: hist, xAxisIndex: 1, yAxisIndex: 1, ... },
           { name: 'DIF', type: 'line', data: dif, xAxisIndex: 1, yAxisIndex: 1, ... },
           { name: 'DEA', type: 'line', data: dea, xAxisIndex: 1, yAxisIndex: 1, ... }],
  axisPointer: { link: [{ xAxisIndex: 'all' }] },   // 必须放顶层 option，写在 xAxis 对象内会被忽略
  dataZoom: [{ type: 'inside', xAxisIndex: [0, 1], ... }, { type: 'slider', xAxisIndex: [0, 1], ... }],
  ```

- 副图系列：`hist` bar（柱宽 60%，**正红负绿**：itemStyle color 回调 `(value) => (value >= 0 ? '#ef4444' : '#22c55e')`）+ `dif` 线（`#e2e8f0` 白，lineWidth 1）+ `dea` 线（`#fbbf24` 黄，lineWidth 1），`symbol: 'none'`。
- 主图 xAxis 隐藏日期标签（`axisLabel.show: false`，日期只显示在副图底部）；`axisPointer.link`（顶层）十字光标跨图联动；dataZoom 的 `xAxisIndex: [0, 1]` 覆盖两图。
- legend 增加 `DIF`/`DEA`（MACD 柱不进 legend）；`macd` 为 undefined 时不加副图（旧后端降级纯主图，不报错）。
- **legend 换行预案**：legend 增至 9 项后可能超卡片宽度换行压进主图区——实现时实测宽度，必要时 grid top 随 legend 行数抬升或 legend 改 `type: 'scroll'`（观察点，测试不覆盖布局）。
- 现有 MA/BOLL 主图逻辑不动。

**mapper**（`toChartViewModels.ts`）：`macd` 透传 + 两类防御——① 长度防御（dif/dea/hist 任一与 bars 不等长 → 整体丢弃 macd）；② **全 null 防御**（dif/dea/hist 三数组全为 null → 整体丢弃 macd，视为无副图，不白白让出主图高度）。丢弃落点在 mapper（与既有长度防御同职责），组件只需处理 `macd` 为 undefined。

**OpenAPI 客户端再生成**：`pnpm run generate:api`。

#### 3.3.2 三方依赖能力评估

ECharts 6 原生支持多 grid + `axisPointer.link` + bar 柱 `color` 回调，无新依赖。

#### 3.3.3 风险与验证方式

- 风险：双 grid 联动配置错误（缩放只作用于主图或副图）——测试断言 dataZoom `xAxisIndex` 与 `axisPointer.link` 配置；副图轴绑定遗漏（默认 axisIndex=0 画错 grid）——测试断言副图三个 series 的 xAxisIndex/yAxisIndex 为 1；布局挤压——数值已按 H=240 验算（副图 ≥40px、间距 ≥6px，实际 7.2px），概念卡路径不触发副图。
- 验证：Vitest——`CandlestickChart.test.tsx` 扩展（含 macd 时：grid 2 个、yAxis 2 个、series 含 hist bar + dif/dea 线且 xAxisIndex/yAxisIndex=1、dataZoom xAxisIndex 覆盖 [0,1]、顶层 axisPointer.link；无 macd 时 grid 数量回归 1、series 数量回归 10）；`toChartViewModels.test.ts` 扩展（macd 透传、长度不一致丢弃、全 null 丢弃）；`pnpm run typecheck` + `pnpm test`。

#### 3.3.4 文件变更清单

- **修改**：`frontend/src/shared/charts/CandlestickChart.tsx`
- **修改**：`frontend/src/shared/charts/CandlestickChart.test.tsx`
- **修改**：`frontend/src/modules/market/pages/mappers/toChartViewModels.ts`
- **修改**：`frontend/src/modules/market/pages/mappers/toChartViewModels.test.ts`
- **修改**：`frontend/src/api/generated/**`（再生成，不手改）

---

## 四、已确认决策 / 待确认问题

**已确认决策**（沿用 K线指标叠加方案既定模式，无需用户重拍）：

| # | 决策 | 内容 |
|---|------|------|
| D1 | MACD 参数 | EMA(12, 26, 9)，hist = 2×(DIF−DEA)，国内软件惯例 |
| D2 | 计算落点 | 后端补窗口内计算（WARMUP_DAYS=120 不变，覆盖 MACD ~34 根前导需求） |
| D3 | 契约形态 | 并入 `indicators.macd` 并列数组（可选字段，旧响应兼容） |
| D4 | 副图形态 | 红绿柱 + DIF/DEA 线，双 grid 与主图共用 dataZoom 联动；macd 缺失降级纯主图 |

**待确认问题**：无（参数与形态均为业界标准，无分叉决策点）。
