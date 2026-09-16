# 关键决策记录

按时间**倒序**追加。每条四要素：背景 → 选项 → 拍板结论 → 理由。

## 2026-09-16 弹窗 K 线指标数据源：个股接因子、概念自算

- **背景**：用户要求 treemap 点击弹窗的 K 线与指数 K 线同款（含 MA/BOLL/MACD 等指标）。现状：概念（板块指数）K 线契约 `indicators` 恒 null（板块指数无任何上游因子源——tushare 无板块指数因子端点，"技术指标不自算"决策在此路径无因子可取）；个股因子表 factor_daily 只有指数行（stk_factor_pro 采集为后续阶段）→ 个股弹窗也是纯 K 线。
- **选项**：a) 前端自算（弹窗内从 bars 算）；b) 后端自算（service 层算并回传）；c) **个股按需接 stk_factor_pro 真因子 + 概念自算**。
- **结论**：c——个股弹窗点击时按需调 stk_factor_pro 拉真因子并入库 factor_daily（factor_daily 即缓存层，下次同区间走表不调上游；拉取失败降级纯 K 线，不阻断 K 线响应）；概念板块指数因无因子源只能自算（预热窗口 [from−120d, to] 全段计算后切回请求窗口，口径沿用归档 K线指标叠加方案 3.1 / MACD指标副图方案 3.1 已批纯函数设计）。
- **理由**：用户拍板。最贴合"技术指标不自算"决策（个股仍取上游因子原值）；概念自算是该决策的显式例外——无因子源时"不自算"不可满足，只能自算。契约变化：概念 bars 的 `indicators` 由恒 null 改为自算值（冻结断言随需求演进更新）；个股 bars 由恒纯 K 线改为按需因子（拉取失败仍纯 K 线）。

## 2026-09-16 treemap 点击后缩放保持

- **背景**：用户反馈放大板块图后点击个股，treemap 弹回放大前样式。
- **选项**：a) 缓存缩放状态并恢复；b) **option 按 data 引用 memoize**（数据未变不重建）。
- **结论**：b——拆分布局与两张图的 option 用 useMemo 按 data 引用缓存；点击节点只改父组件 state（弹窗开关），data 引用未变 → echarts-for-react 深比较同引用短路跳过 setOption → treemap 内部缩放状态保留。榜单日期变化 → data 变 → 重建（新数据缩放重置属预期）。
- **理由**：根因是 notMerge 全量 setOption 重置 treemap 内部缩放状态；memoize 引用即根除（零状态搬运、无恢复时序问题）。

## 2026-09-14 日线数据命名定稿：bars 系不改名，频率由 interval 区分

- **背景**：日线数据全链叫 `bars`（BarsData/BarDTO/get_bars/`/indices/{symbol}/bars`/useMarketBarsQuery/barsToCandlestickViewModel）。讨论经历两轮：先拟"bar+daily 修饰改名"（DailyBarsData/DailyBarDTO），后用户指出周/月线的 DTO 结构完全相同（timestamp/OHLC/volume 六字段不变）——频率是取值不是类型，DailyBarDTO 对周/月线会名字撒谎。
- **选项**：a) DailyBars* 改名（已推翻）；b) kline 系改名；c) **bars 系保持现状**。
- **结论**：c——DTO/服务/前端全部不动；接口频率区分由 `interval` 查询参数 + 响应 `interval` 字段承载（现状已有）；本方案新端点（concepts/stocks bars）同样带 `interval=1d` 参数；未来周/月线零新 DTO、零新端点。
- **理由**：用户拍板"就 bar 系，接口得区分"；interval 参数即为接口区分手段，改名是纯 churn 且引入误导。

## 2026-09-14 数据先存 dc（撤销全历史回填）

- **背景**：v1 按"全历史回填"设计走 ths_daily（实测唯一全历史可行路径，899 板块 ≈ 250 万行）；用户表示暂不需要长历史。
- **选项**：a) ths 全历史回填；b) **dc_daily 每日增量积累**（33 交易日窗口，历史自启动日起增长，不回填）。
- **结论**：b——先存 dc；热度口径保持原 dc 不变（get_hot_concepts 零改动）；ths 全历史能力已验证、暂缓（未来按 source 参数扩展）。
- **理由**：用户拍板"先不用这么长的数据"；dc 与热度原口径天然一致，改动最小、当天见效。

## 2026-09-14 treemap 交互形态

- **背景**：板块区块现状为热门概念卡片网格（HotConceptsPanel），恒空态（sector_daily 0 行）。
- **选项**：卡片保留 / treemap 替换 / 并存。
- **结论**：treemap 替换卡片网格；两层结构（概念 → 成分股）；矩形大小=热度分、颜色=当日涨跌幅（红涨绿跌）；悬浮 tooltip（名称/热度/涨跌幅）+ **点击弹窗** K 线。
- **理由**：用户拍板（treemap 两层、大小=热度、颜色=涨跌、点击弹窗 4 项）；卡片与 treemap 并存会重复展示同一批数据。

## 2026-09-14 相关范围决策

- **背景**：treemap 上线后 hot 端点无前端消费方；前端 useHotConceptsQuery 成 dead code。
- **选项**：hot 端点删除 / 保留。
- **结论**：hot 端点**保留**（口径不变、契约不动）；前端 hook 与 queryKeys.hotConcepts 删除（dead code 不留，与端点去留正交）。
- **理由**：hot 端点是文档化平台契约（API契约 §8.3、产品需求分析 §3.1.4.3），删除纯收缩无收益。
