# 复盘

## 做对了什么

- 从用户任务顺序调整页面，再统一主题和动效；保留行情口径、绘图存储、审核自动流程和并发量化能力。
- 草稿保护用真实父组件＋Query 更新测试，发现仅冻结子组件版本不足以应对父组件卸载。
- 拓扑用真实 ECharts SSR 验证几何，浏览器补充宽度和无页面横溢验证。
- 审核页在导航前拦截业务 API，意外请求直接失败，避免验收触发真实采集/预填。

## 踩坑与处理

- 主题进入完整 option 依赖会重放 dataZoom；改成只合并颜色。经验补充到 echarts-datazoom-anchors.md。
- 缓存刷新失败不能提前返回替换整个编辑树；错误提示与旧数据并存。经验补充到 react-query-dialog.md。
- Playwright 的 **/api/** 会误拦截 Vite 的 /src/api/ 模块，改成 URL pathname.startsWith('/api/')。
- 原生 select 的 getByLabel 文本可能包含 option 文本，使用语义 role 与可访问名称定位。
- React Query 的 refetch Promise 结束不等于 observer 已渲染，回归用 findBy 等待错误状态到达后再检查草稿。

## 下次改进

- 视觉分组、交互边界与大组件拆分分阶段进行，减少单轮改动面积。
- 在已有未提交开发上开展交叠任务前，优先让已有工作形成基线提交；本次保留快照确保未误覆盖，但无法独立提交完整增量。
