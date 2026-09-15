# 时间线日志

按时间**倒序**追加（最新在上）。每条 = 日期 + 动作 + 结果/备注；一句话能说清就不写长段。

| 日期 | 动作 | 结果/备注 |
|------|------|----------|
| 2026-09-14 | 用户确认全案（top 30 按推荐）→ 任务分解 + 开始实施 | tasks.md 6 任务（T1 采集 → T2 tree API → T3 bars API → T4 前端 → T5 文档 → T6 首跑验收）；README 状态实现中 |
| 2026-09-14 | 命名讨论收敛：日/周/月线 DTO 结构相同 → 撤销 daily 修饰改名 | 定稿：bars 系不改名（BarDTO/BarsData/get_bars//bars 端点），接口频率区分 = interval 参数 + 响应 interval 字段；新端点同样带 interval=1d；方案 3.5 改名模块删除、已确认决策第 5 条改写 |
| 2026-09-14 | 用户拍板日线命名：bar 保留 + daily 修饰、全链统一改名（同日被推翻） | 方案曾新增 3.5 存量命名改造模块：DailyBarsData/DailyBarDTO/get_daily_bars//daily-bars 端点——后经"DTO 结构频率无关"讨论撤销 |
| 2026-09-14 | 文档目录重构（另一任务）将本方案迁入 requirements/板块概念Treemap/ | plan.md 整迁（git mv）+ 链接随迁 + 8 文件骨架建立；CLAUDE.md 已重写为任务文件夹制 |
| 2026-09-14 | 用户拍板：数据先存 dc | v1（ths 全历史回填）重写为 v2（dc_daily 每日增量、热度口径原 dc 不变、ths 暂缓）；评审结论 v1 中被废弃的条目随 v2 重评 |
| 2026-09-14 | v2 方案评审收敛 | R1' 全量 8.9 分（F1–F5 修复）→ R2' 核验 PASS，全部维度 ≥8 |
| 2026-09-14 | v1 方案评审收敛（后被 v2 取代） | R1 19 条 findings（6.0 分）→ R2 8.1 → R3 PASS；关键结论（echarts 静态色/LEFT JOIN/截断 top 100）在 v2 中保留 |
| 2026-09-13/14 | 现状摸底与端点能力实测 | 根因确认：market.sector_daily 0 行 → 板块区块恒空态；实测 dc_daily 仅最近 33 交易日（更早 0 行）、ths_daily 单请求全历史（老板块 4,642 行）、push2his.eastmoney.com 直连+代理均重置 |
| 2026-09-14 | 实施 T1-T5 全部完成 | 采集（32 新用例+169 回归）→ tree API（12+2）→ bars API（16+4）→ 前端（35 模块/229 全量/typecheck/build）→ 文档同步（grep 0 残留）；T6 增量首跑后台执行、code review subagent 并行启动 |
| 2026-09-14 | CR 两轮收敛：第一轮 11 条全修 → 第二轮 PASS（2 新 minor + 2 polish 再修） | 修复含 SQL ROW_NUMBER 截断、treePathInfo 虚拟根残影、可证伪关闭卸载用例、interval 422 断言、m.ts_code 二级键；修复后后端 22 + 采集 170 + 前端 36/229 + typecheck/build 全绿 |
| 2026-09-14 | T6 数据首跑完成 + 真数据服务链验证 | 1031 板块/51,381 行/0 失败（~50 行/板块）；hot/tree/概念 K 线/个股 K 线真数据直调全 OK；发现并记录预置测试隔离问题（caplog 混跑失效，非本任务引入） |
| 2026-09-14 | 修复 start_all 采集阻塞前端启动（用户反馈） | 板块日线增量使采集体量膨胀到 15-50 分钟/日，原"先数据后前端"同步等待设计不再成立；改为后台执行（nohup + logs/market-ingest.log + pidfile 防重入），手动 ingest-market 保持前台；每日 08:30 APScheduler 批处理兜底数据新鲜度 |
| 2026-09-14 | 修复 treemap 标签显示（用户浏览器验收反馈） | ① 概念名称不显示：根因 = echarts treemap 非叶子节点 label 仅走 upperLabel 分支且默认 show:false（读 TreemapView 源码 + SSR 实测定位，levels 挂无效必须系列级）→ 系列级 upperLabel {show:true, height:18}；② 股票名旁加涨跌幅：叶子 label formatter 回调（+3.21%/-2.50%/停牌 —），个股节点数据携带 pct_chg；SSR 实测五项标签全部渲染，38 测试 + typecheck + build 全绿 |
| 2026-09-14 | 修复板块成分采集列名 bug + 双源重采完成 | provider concept_code→sector_code（决策 12 落点）后重采：dc 94,243 行写入 0 失败（总行数 93,397→96,284）、ths +63；历史新高成分 2→8、光刻胶 61→62；采集链自统一方案迁移后首次真正写成功 |
| 2026-09-14 | 修复 treemap 个股可见性（用户验收反馈） | 面积保底 PCT_FLOOR=0.5 + visibleMin 20→5；回归 170+38+typecheck 全绿 |
| 2026-09-14 | 板块排序按涨跌分组（用户拍板） | 展示层重排（契约 rank 顺序不变）：涨（红）挨一起在前、热度降序；跌（绿）在后、跌少→跌多；停牌灰最后；个股层同规则（红前绿后、绿内跌少在前）；39 测试 + typecheck 全绿 |
| 2026-09-14 | 排序修订：完全按涨跌幅、热度不参与（用户拍板） | sortByPctDesc 替换分组排序：涨跌混排消失——涨(红)前涨多→涨少、跌(绿)后跌少→跌多、停牌灰最后；fixture 以"热度与涨幅反向"验证热度不参与；39 测试 + typecheck 全绿 |
| 2026-09-14 | 矩形大小改为 |涨跌幅|、tooltip 删热度分行（用户拍板修订） | 概念层大小与个股层同口径（|pct| + 面积保底 0.5、停牌灰最小块）；热度分仅存于 API 契约（后端仍按热度选 top 30），前端展示完全不消费；39 测试 + typecheck + build 全绿 |
| 2026-09-14 | 修复排序未生效根因：series 默认 sort:true 降级为 desc（用户验收反馈） | TreemapSeries 默认 sort:true，layout 把 true 降级 'desc' 按 value（=|pct|）重排——数据数组顺序被覆盖；改显式 sort:false 保留数据顺序（正前负后、跌少在前），visibleMin 依赖 sort 一并移除（截断后 ~3000 节点无需保护）；SSR 文档序验证 涨10|涨5|涨1|跌03|跌2|跌8；40 测试 + typecheck + build 全绿 |
| 2026-09-14 | 涨跌分两张图（用户拍板） | 上下等宽等高排列：上图=上涨概念（红）、下图=下跌概念+停牌灰；单侧无数据时另一张占满全高；splitUpDown 纯函数 + 单测；41 测试 + typecheck + build 全绿 |
| 2026-09-14 | 移除 treemap 默认面包屑（用户验收反馈） | 面包屑是 echarts treemap 内置下钻路径条（默认显示），加 breadcrumb:{show:false} 关闭；41 测试 + typecheck + build 全绿 |
| 2026-09-14 | 移除上涨/下跌标题（用户验收反馈） | 分图仅靠红绿颜色区分，保留不可见的 aria-label 无障碍标注；41 测试 + typecheck + build 全绿 |
