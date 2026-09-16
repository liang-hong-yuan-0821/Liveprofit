# 问题与解决

评审 findings 与实施中遇到的问题。按时间**倒序**追加，每条三要素：表象 → 根因 → 解决。

## 2026-09-17 脚本协议是否跟随模板库方案收紧（SELL_PARTIAL 去留 + reason 固定码）

- **表象**：模板库方案（`docs/requirements/量化策略模板库/plan.md`，状态待确认）决策 3 要求脚本只开放 `BUY/SELL_ALL/HOLD`、删除 `SELL_PARTIAL`，`reason` 收紧为固定 ASCII 原因码（`STOP_LOSS`/`NO_SIGNAL`/`MA_TREND_CROSS` 等，前端映射中文）；现行实现支持 `SELL_PARTIAL + sell_ratio`，`reason` 为 ≤240 字符自由文本。用户倾向「让脚本感知持仓」而非把 SELL_PARTIAL 退役。
- **根因**：无状态脚本输出「卖 X%」不可幂等——同日重跑重复建议、部分成交后比例基数漂移（每次以当前持仓为基数会砍半收敛）；生命周期状态机需要 `reason_code` 参与幂等键 `(position_id, trade_date, target_shares, reason_code)`。但「感知持仓」只能解决基数问题，解决不了竞态（沙箱无事务，读-算-写非原子）和审计（脚本可编辑，历史决策不可复现）。
- **解决**：**待用户拍板**，两轨折中已提（推荐）：脚本侧 `context.position` 扩展 `initial_fill_price/initial_stop_price/冻结风险容量`，脚本保留 `SELL_PARTIAL` 作为**意图**输出；平台侧把意图规范成目标仓位绝对值、按唯一键去重、部分成交后差额重算。无论走哪条，`reason` 固定码都建议做（前端码→中文映射）。用户已拍板保留自定义代码输入（见 decisions.md 2026-09-17），模板库方案「V1 不开放自定义源码编辑」按过期处理。

## 2026-09-17 模板库方案内部缺口：持仓成本止损的成交价来源

- **表象**：模板库方案 3.2.1 要求脚本持仓时判断 \(C_t\le P(1-p)\) 输出 `STOP_LOSS`，但 `context.position` 只有 `shares/average_cost/market_value`，P（初始成交价）无处获取；该方案又声明「脚本不读取真实成交、不以 average_cost 冒充成交事实」——两者矛盾。
- **根因**：成本止损判断的归属未定：放脚本（需扩展 context 注入成交事实，违背其自身设计）还是放生命周期层（已持有 `initial_fill_price/initial_stop_price`）。
- **解决**：**待用户拍板**，推荐 A——止损统一由 `PositionLifecycleManager` 裁决（含成本止损、移动止损、获利减仓、兑现时限），脚本持仓时只输出技术形态失效（死亡交叉/结构失效 → `SELL_ALL` + 固定码）。

## 2026-09-17 冲突 B：行情冻结 vs 实时读取（模板库 C2/C4 与决策 10 冲突）

- **表象**：模板库方案 C2/C4 要求「task-scoped 快照冻结、rerun 只读快照不重取数据」；已实现方案按用户拍板（决策 10「不回测、只按条件选股」）为**实时读取、不冻结**（快照表已砍）。模板库方案引用的前置文档 `../选股策略/plan.md` 是冻结版旧稿。
- **根因**：模板库方案基于旧冻结设计起草，未吸收决策 10；但它的生命周期状态机（移动止损、3 日兑现时限、目标仓位）是有状态跨日逻辑，确实需要「逐日价格事实」可复现——这是实时模式唯一的真实弱点。
- **解决**：**待用户拍板落地时机**（现在落 revisions vs 等模板库方案过评审），推荐「实时读取 + 生命周期自持价格事实」中间态：信号扫描维持实时；生命周期推进时把依据的逐日价格事实（high/close/MA 值）作为审计字段落进生命周期状态表（`high_water_mark`、触发日 close 等本就要存），不建全市场快照表。模板库方案 C2/C4 相应改述。

## 2026-09-17 Context 指标扩展（模板库前置）

- **表象**：模板库七模板依赖 `ma_bfq_60`、`boll_mid/upper/lower_bfq`、`macd_dif/dea/bfq`（现行白名单只有 `ma_bfq_5/ma_bfq_20/rsi_bfq_6`）；需要深负索引 `close[-20]`（MA5 预上穿公式）、`close[-41]`（圆弧底锚点）、`volume[-6:-1]` 五日基线。
- **根因**：模板因子集超出已实现的 `INDEXED_PATHS` 合同；且全市场股票因子采集（`stk_factor_pro` → `market.factor_daily`）未经验证（C1 POC 未做），「列存在」不等于数据可用。
- **解决**：待模板库方案确认后实施——扩展 `strategy_contract.py` 字段清单与快照投影 + 四向契约测试（源码实际读取集合 = 模板 required_fields ⊆ 快照投影 ∩ 白名单）；**C1 全市场股票因子采集 POC（250 日窗口 100% 覆盖）为模板发布前置门禁**，未达门槛不得发布模板。

## 2026-09-17 价格双口径（模板库前置）

- **表象**：模板库方案要求信号行保留 raw `entry/stop/take`，订单层新增规范化 `order_entry/order_stop/order_take`（`OrderPriceNormalizer`：Decimal、最小 0.01 元、ROUND_HALF_UP；顺序固定「原始七键校验 → tick 规范化 → 规范化价格复核」）；现行实现只有 raw 信号价 + `order_cost_price` 等订单列。
- **根因**：tick 规范化与风险收益复核属于订单层职责，不应改写策略信号（信号可审计性与订单价格分离）。
- **解决**：待模板库方案确认后实施——signals 表加 3 个 order 价格列、PositionPlanner 加规范化步骤、新增 `INVALID_PRICE_RANGE` 拒绝码（规范化后盈亏比低于 `max(template.reward_multiple, portfolio.min_risk_reward_ratio)` 或严格关系不成立时 BUY 信号保留、订单拒绝）。

## 2026-09-17 `isfinite` 语义微调

- **表象**：模板库方案要求 `isfinite` 对非数值输入返回 `False` 且不抛异常（输入门控依赖）；现行子进程实现 `_isfinite` 对非 float 输入返回 `True`。
- **根因**：语义约定不一致——模板输入门控用 `isfinite` 做数值合法性判定。
- **解决**：小修，随模板库方案一起落：`_isfinite` 改为数值判定（int/float 且非 bool 才参与 isfinite，其余返回 False），不改抛出行为。

## 2026-09-16 人工验收两项（任务收尾）

- **表象**：行业 POC（`python -m db.instrument.ingest.industries --poc`，真实 TUSHARE_TOKEN）与完整栈 E2E（建策略 → 发布 → 建组合 → 提交量化任务 → 报告面板买点/订单展示）尚未人工验收。
- **根因**：均需真实环境（真实 token / 完整 Worker+行情栈），自动测试无法覆盖。
- **解决**：待用户执行；两项通过后任务文件夹移入 archive/ 归档（README 状态当前为「Code Review 通过」）。
