# 量化策略优化与全生命周期验证

> **状态**：实现中（2026-10-02，组合未绑定BUY批量DB捕获/封口实现检查完成；3ar-2尚未整体验收）
> **进度**：0/8项整体验收；完整历史3ar为1/6，3ar-2进行中；已有软件验收证据见tasks。
> **下一步**：在已有全组合scope基础上实现生命周期多步骤DB协调与封口，完成3ar-2闭环后接建单输入冻结；[本轮证据](result.md#checkpoint-3ar2-portfolio-unbound-db-20261002)。[部署、连续历史及来源门禁](tasks.md#acceptance-gates)保持独立。
> **关联文档**：[plan.md](plan.md)｜[tasks.md](tasks.md)｜[原方案](../archive/量化策略与实操层/plan.md)｜[原任务清单](../archive/量化策略与实操层/tasks.md)｜[经验索引](../../experience/index.md)

**测试维护入口**：[测试目录及运行约定](../../../tests/README.md)｜[测试索引](../../knowledge/test/测试索引.md)。目录整理已完成；本任务后续计划按新目录维护，存量功能优先扩展已有脚本。[本次静态检查证据](result.md#test-layout-followup-20261002)不新增业务验收结论。

**最新上游更新核验**：2026-09-25至27日为交易所中秋休市，A股最后交易日9月24日。9月22–24日基金日线原整日缺采已从上游补入2147/2133/2133条；最近29个交易日基金整日/配对复权缺口0、股票日线/复权缺口0，9月15日仍有1条股票复合状态未认证。2020/2021 Bao历史ST批次现分别全量导入951,986/1,062,370条；与Tushare状态的2/1条分歧已单独隔离，分歧证券日不用于独立ST结论，同日其他股票保留可用。[2020原始核验](attachments/baostock_k_st_2020_probe.json)｜[2021原始核验](attachments/baostock_k_st_2021_probe.json)。

**2016年最新核验**：[Bao历史ST全量批次](attachments/baostock_k_st_2016_probe.json)覆盖704,145/704,145个有效证券日、与旧状态262,739条ST零冲突；[定向二轮补采](attachments/status_backfill_2016_baostock_no_trade_targeted.json)只写839个缺口证券状态，全年状态缺896→5。[最终股票年审计](attachments/market_history_audit_2016_final_stock.json)未解释缺日线0、复权缺0；5条有日线且上游空时段S均经[公司原件](attachments/suspension_evidence_2016_intraday_five.json)确认盘中停牌并入库，公告晚于停牌日，复合状态保留历史可得时点边界。因子含暖机缺11,581已按上市前59根日线认证。

**2024年最终核验**：[因子批次](attachments/factor_day_backfill_2024_full.json)242日写1,288,025行、失败0；[独立停牌源](attachments/suspension_source_2024_full.json)242日核实3,092条全天停牌，恰好覆盖全年无日线证券日；[状态批次](attachments/status_backfill_2024_full.json)242日全成功、写1,296,743行。[最终年审计](attachments/market_history_audit_2024_final.json)未解释缺日线0、复权缺0、状态缺0；[暖机核验](attachments/factor_warmup_2024_verification.json)证实因子余5,966条均不越前59根有效日线。

**2023年最新核验**：[因子批次](attachments/factor_day_backfill_2023_full.json)242日写1,249,463行、失败0；[独立停牌源](attachments/suspension_source_2023_full.json)242日核实2,482条全天停牌；[状态批次](attachments/status_backfill_2023_full.json)242日全成功、写1,260,984行。[年度审计](attachments/market_history_audit_2023_after_status.json)预期1,260,984证券日，未解释缺日线0、复权缺0、状态缺0；[暖机核验](attachments/factor_warmup_2023_verification.json)证明因子余16,337条均不越前59根有效日线。28只北交所证券的前置回溯日线已从有效暖机根数排除。

**2019年最新核验**：[独立ST补采](attachments/status_backfill_2019_baostock_targeted.json)与[年审](attachments/market_history_audit_2019_after_baostock.json)已完成，890,251个有效证券日：未解释日线缺0、复权缺0、状态缺0；10,360个因子组合空值已按真实交易根数核验为暖机。

**2020年最新核验**：[按证券隔离补采](attachments/status_backfill_2020_symbol_quarantine.json)又补21条状态；[年审](attachments/market_history_audit_2020_symbol_quarantine.json)确认951,986个有效证券日未解释日线缺0、复权缺0、状态余2条ST源分歧。22,222个因子组合空值已核验为暖机；双方ST值保留，统一有效视图隔离。

**2021年最新核验**：首次独立ST批次补9,881条，[最终重试](attachments/status_backfill_2021_final_retry.json)补11条，原死锁失败日和盘中时段误拒均已恢复；仅余002529.SZ/2021-06-22的ST源分歧。年度日线未解释缺0、复权缺0，28,733个因子组合空值此前已核验为暖机，[最终年审](attachments/market_history_audit_2021_final_retry.json)确认状态未认证1条。

**2022年最新核验**：[定向状态批次](attachments/status_backfill_2022_scoped_baostock.json)153个待补日期全部成功，补230条；[年审](attachments/market_history_audit_2022_after_scoped_baostock.json)确认1,174,346个有效证券日未解释日线缺0、复权缺0、状态缺0。21,958个因子组合空值此前已核验为暖机。



**2017/2018年最新核验**：[2016–2017复审](attachments/market_history_audit_2016_2017_upstream_checkpoint.json)确认2017状态余3条；[2018年审](attachments/market_history_audit_2018_after_baostock.json)确认状态余1条（000979.SZ/2018-08-28）。两年未解释日线缺0、复权缺0，因子余26,826/7,945项此前已核为暖机。剩余项是状态证据未认证，不能称为行情缺失。

**本轮统一检查点**：[2016–2026逐年覆盖与剩余范围](attachments/上游数据更新检查点-20260926.md)。股票年度未解释日线缺0、配对复权缺0，未认证复合状态合计24条。2026新增股票因子952,580行，6,845项空值已核暖机边界；基金逐证券历史与ETF规则仍未整体认证。

## 任务总览

- **目标**：优化现有七套量化策略，新增四个策略家族，建立历史验证、模拟观察与每日真实持仓建议闭环；只有通过成本、稳定性、回撤及可执行性验证的策略版本才进入人工建议。
- **负责人**：用户负责业务决策与真实交易；Codex 负责文档、后续实现与验证。
- **骨架文件**：[plan.md](plan.md)（方案正文）｜[tasks.md](tasks.md)（拆任务清单）｜[log.md](log.md)（时间线）｜[decisions.md](decisions.md)（决策）｜[issues.md](issues.md)（问题）｜[result.md](result.md)（产出结论）｜[retrospective.md](retrospective.md)（复盘）
- **当前实施范围**：按 T1–T8 任务清单推进；T1 先交付研究快照基础层。尚未运行收益研究、真实交易或长周期模拟观察。
- **全量数据审计**：[可读摘要](attachments/历史市场数据系统审计-20260925.md)｜[当前逐年机器报告](attachments/market_history_audit_20260925_current.json)｜[回填前基线](attachments/market_history_audit_20260925.json)。报告列的是证据缺口，不能据此推定历史行情均可回填。
- **2025年状态补采**：[1月](attachments/status_backfill_2025_01.json)和[2–3月](attachments/status_backfill_2025_q1_rest.json)共57个交易日已复核为待补0；[4–6月](attachments/status_backfill_2025_q2.json)60日中45日完整、15日局部未知。[14条独立停牌证据](attachments/suspension_evidence_2025_q2.json)入库后，[上半年审计](attachments/market_history_audit_2025_h1_after_status.json)余未解释缺日线1证券日、状态字段缺16证券日。7–9月继续补采；因子与ETF规则仍未认证。
- **2025年三季度状态**：[逐日补采报告](attachments/status_backfill_2025_q3.json)显示66个交易日33日完整、33日局部未知，仅涉及三只北交所股票；[首18条](attachments/suspension_evidence_2025_q3_verified.json)与[余20条](attachments/suspension_evidence_2025_q3_remaining.json)公司/交易所公告证据已逐项入库。
- **2025年四季度状态**：[逐日补采报告](attachments/status_backfill_2025_q4.json)显示60日58日完整、2日局部未知；[两条公告](attachments/suspension_evidence_2025_q4.json)已入库。[全年复审](attachments/market_history_audit_2025_after_factor1450.json)余未解释缺日线1、状态字段缺56、技术因子缺868,980证券日（含暖机，比[450只阶段](attachments/market_history_audit_2025_after_factor450.json)少272,314）。新增1000只有界批次997只成功、3只经交易日历/上游/本地日期三方核验为新股ATR20暖机、失败0；此前未来上市股误选已修正。
- **2025年最新复审**：[按日因子回填](attachments/factor_day_backfill_2025_alias_restarted.json)在北交所双码源中优先使用主库新码，后半年度108日写入387,423行、失败日0；[年度审计](attachments/market_history_audit_2025_after_conflict_evidence.json)余未解释缺日线0、复权缺口0、因子组合缺6,474、状态缺3。[因子暖机逐日核验](attachments/factor_warmup_2025_verification.json)确认6,474条均在上市后前59个交易日，本地日线与独立XSHG日历逐日一致，因子整行缺不越第20日、MA60缺值不越第59日。北交所新旧码映射后[状态重采](attachments/status_backfill_2025_bse_alias.json)将缺行56降至3；[两笔公司公告](attachments/suspension_evidence_2025_final_conflicts.json)已确认603897.SH、688766.SH的无日线为全天停牌，尚缺的复合状态字段仍留未知。603003.SH 2025-06-10有日线且复牌公告已核，但上游空时段S/R冲突尚未裁决；不能把它判为全天停牌或行情缺失。
- **2016年推进**：[全年因子复审](attachments/market_history_audit_2016_after_full_factor.json)显示因子组合缺587,682→11,581，复权缺0；[上市窗口分类](attachments/factor_warmup_2016_classification.json)后按真实日线根数与停牌证据[再核](attachments/factor_warmup_2016_verification.json)：2016年内无未解释的无日线交易日；32只2015年上市股票的前置窗口经[日线及复权定向回填](attachments/warmup_context_2015_backfill.json)和[199条独立全天停牌核验](attachments/warmup_suspensions_2015_verification.json)复核，11,581条因子余项现已认证为新股暖机。8月中旬后[状态补采](attachments/status_backfill_2016_aug_dec.json)写入262,741条但89日局部未知、4日不可用。[独立逐日停牌源](attachments/suspension_source_2016_full.json)补61,708条，[6只股票暂停上市公告](attachments/suspension_evidence_2016_listing_pauses.json)核对后补891条；[2016年最新审计](attachments/market_history_audit_2016_after_listing_pauses.json)未解释缺日线0、复权缺0、因子含暖机缺11,581、状态缺441,406。公告发布时间晚于历史交易日的事实仅用于追溯完整性，研究as-of读取仍按发布时间门禁；年初ST空源保持未知。
- **2017年推进**：[按日因子回填](attachments/factor_day_backfill_2017_full.json)244日写730,616行、失败日0；[全年独立停牌源](attachments/suspension_source_2017_full.json)244日核验54,383条全天停牌，[10只股票暂停上市公告](attachments/suspension_evidence_2017_listing_pauses.json)补足余1,442条；[最新年度审计](attachments/market_history_audit_2017_after_listing_pauses.json)未解释缺日线0、复权缺0、完整状态缺7,769。[因子暖机认证](attachments/factor_warmup_2017_verification.json)核实26,826条涉及539只2016/2017年上市股票，首根日线均等于上市日、截至缺口日的无日线交易日均有可信停牌证据；缺口不越前59根日线，整行/ATR缺不越前20根。[状态补采](attachments/status_backfill_2017_full.json)244日中242日局部、2日源不可用（1月17日、6月23日），写791,295行。状态缺口继续保留未知，不由停牌公告推断ST。
- **ETF规则证据**：[交易所规则与逐证券待核清单](attachments/ETF历史交易规则官方证据-20260926.md)已整理品种级T+0/T+1、深交所20%名单及每日限价参数来源；这些规则尚未逐只逐日映射到历史ETF目录，不能据此认证ETF回测。
- **2026停牌范围复核**：[公告证据](attachments/2026七项停牌核验证据.md)｜[最新机器报告](attachments/market_history_audit_20260925_official_evidence.json)。整日停牌的8个无日线缺口已有独立证据，盘中停牌的1个有日线情形已单列；9笔状态字段仍待补。
- **确认与评审来源**：用户已确认业务方案并授权生成文档。会话草稿 R1 为 FAIL、均分 7.0；修复后 R2 为 PASS、均分 10.0。详细转录见 [issues.md](issues.md)。此记录不等于已对落盘文件重新全量评审；模板整理不重复启动 R1。
- **结果边界**：软件验收、历史研究结论、长周期模拟观察分别留证。允许全部候选不通过；样本不足持续标记“证据不足”，不以软件完成冒充策略有效。

## 原任务承接

仅引用承接 [量化策略与实操层任务清单](../archive/量化策略与实操层/tasks.md)，本轮不回写原目录，也不把旧任务未完成项视为已经完成。

| 原任务 | 本任务承接 | 交付边界 |
|---|---|---|
| N7 报告/API/前端展示 | T7 | 展示真实账户、生命周期、费用、风险、数据水位与策略资格 |
| N8 完整链路性能与 E2E | T8 | 复用已有基线并复验最终全链路，保留平台专属未验项 |
| N9 forward shadow 与晋级 | T5、T8 | 固定新方案研究与观察门槛，长期观察独立记录，不即时勾选 |

## 进度口径

T1 进行中；T2 仅启动不依赖真实历史收益的候选登记，仍须等待 T1 输入就绪后完成端到端验收。其余任务待开始。执行过程中每完成关键步骤，立即同步本状态块、任务验收项及 [log.md](log.md)。T5 的研究软件验收与真实时间的模拟观察分开记录；T8 仅在相应交付证据真实齐备时收尾，观察尚未结束时不把整个任务标为“已完成”或归档。
