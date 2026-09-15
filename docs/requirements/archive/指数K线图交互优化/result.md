# 最终产出与结论

## 验证结果

| 层面 | 命令/方式 | 结果 |
|------|----------|------|
| 前端单测 | `pnpm --dir=frontend vitest run src/shared/charts/CandlestickChart.test.tsx src/shared/charts/drawings.test.ts src/shared/format/volume.test.ts src/modules/market/pages/MarketIndicesPanel.test.tsx` | ✅ 55/55 通过（33 + 9 + 3 + 10） |
| 前端类型/构建 | `pnpm --dir=frontend typecheck && pnpm --dir=frontend build` | ✅ 通过 |
| 后端测试 | 本方案无后端改动（回归确认） | ⏳ 待用户执行：`pytest backend/tests/contract/api/test_market_data.py backend/tests/integration/market_data/` |
| 人工检查 | 起前后端打开大盘页（清单见下） | ⏳ 待用户执行 |

## Code Review

- **结论**：三轮收敛——R1 FAIL（1 blocker：zr handler 逐渲染累积 + 3 major：y 求交基准错/仅 MACD 轴误套/x top 判据 + 6 minor）→ R2 FAIL（1 blocker：裸 zr.off 误删 ECharts 内部监听，axisPointer/读条/inside zoom 全失效）→ R3 PASS
- **遗留**：无（1 条 polish 已修：外来 handler 存活锚扩到三事件）

## 人工检查清单（待用户执行）

1. 大盘页三 panel：成交量副图显示、日期标签只在最底副图、主图高度可读（H=420）
2. 滚轮缩小 Network 面板 `from` 前移（约每 2× 一跳）；左平移近左界预拉一屏；窗口不跳；缩到头停止扩展；无日期输入框
3. 画线：工具栏出现（概念卡无）；画/拖拽/删除四线型；缩放与渐进加载后画线不漂移；刷新保留；切标的不串
4. 图例：两行纯文字系列色、点击隐藏变灰且重渲染不弹回；悬浮无气泡、图例右侧读条（行 1 OHLC、行 2 量+指标）、未悬浮显示可见窗口末根；**十字光标逐根跟随、读条随鼠标更新**（zrender 共享 Eventful 回归锚）
5. view 模式按住拖拽可平移、滚轮可缩放（ECharts 内部 roam 监听存活回归锚）
6. KLineDialog（热门概念弹窗，无图例布局）读条显示且不压主图顶缘

## 交付物

- `frontend/src/shared/charts/CandlestickChart.tsx`：成交量副图、多 grid H=420、细线半透明、dataZoom 日期锚定、画线渲染与交互、双 legend 图例、悬浮读条、ChartCore memo 拆分
- `frontend/src/shared/charts/drawings.ts`（+ test）：Drawing 类型、load/save 按 kind 校验、hitTest、renderedSegment/buildDrawingsMarks（窗口裁切 + extent 求交）
- `frontend/src/shared/format/volume.ts`（+ test）：量级缩写
- `frontend/src/modules/market/pages/MarketIndicesPanel.tsx`（按需加载状态机 + 画线持久化接线）、`queries.ts`（placeholderData）、`shared/format/dateTime.ts`（daysBetween）及对应测试
- 知识库同步：`docs/knowledge/产品需求分析.md`（v1.6：§3.1.3.2/🟦/契约/错误码总表/AC-25/AC-26）、`docs/knowledge/frontend/前端平台.md`
- 经验沉淀：`docs/memory/pitfalls/frontend/zrender-shared-eventful.md`（+ index 挂载）
