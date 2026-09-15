# 关键决策记录

按时间**倒序**追加。每条四要素：背景 → 选项 → 拍板结论 → 理由。

## 2026-09-14 图例形态与悬浮读条

- **背景**：图例现状默认图标+灰字（SSR 实测 #54555a 不随系列色）；tooltip axis 气泡遮挡 K 线。影响点：CandlestickChart legend/tooltip option、读条 overlay 与 ChartCore 拆分、主图 grid top（§3.1 表联动 32→36）。
- **选项**：A 双 ECharts legend（数组）+ React overlay 读条；B 自定义 React 图例（legend show:false + dispatchAction 重实现点击/灰色/行分组）。
- **结论**：A。
- **理由**：legend 数组/icon none/inactiveColor 经 SSR 实测可用；点击隐藏变灰走原生机制、不重实现状态同步；B 需自管 selected 同步与点击热区，收益低。行分组（行 1 MA+BOLL、行 2 DIF+DEA）、读条两行（第一行 OHLC）、未悬浮显示最新一根为 2026-09-14 用户拍板。

## 2026-09-13 画线工具范围

- **背景**：用户提出大盘页 K 线图支持画线；影响点：CandlestickChart 渲染载体（markLine/markPoint）、交互模式、持久化层。
- **选项**：四线型（水平线/趋势线/射线/文字标注）× 编辑能力（画+删 / 画+拖拽修改+删）× 持久化（会话 / localStorage / 后端落库）。
- **结论**：四线型 + 画/拖拽修改/删 + localStorage 按标的存 + 仅大盘页全尺寸图（概念卡不传新 props 行为不变）。
- **理由**：2026-09-13 用户拍板；localStorage 无后端改动、刷新不丢；概念卡 160px 高度命中/拖拽精度差不开画线。

