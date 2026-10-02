# 持仓总量不能证明可卖量

保护性退出应保留意图，但建单必须核同一持仓、同一交易日的可信结算可卖量。

## 表象（2026-09-29）

`PositionPlanner._plan_sell`原用`holding.get("available_quantity", holding["quantity"])`；扫描给生命周期的`available_sell_quantity`也回退到持仓`quantity`。账户当前只有持仓总量，没有券商结算、冻结或T+1可卖事实，因而可能在不可卖时仍生成SELL建议。生命周期直调还会把超出实际持仓的可卖量经`plan_reduction`的`min`静默裁剪后落单；畸形值抛错中断保护流程。

## 根因与正确姿势

持仓数量与可卖数量是不同业务事实。T+1、冻结、在途卖出、券商对账差异都可能使可卖量更小；下一交易日也不能仅凭日期推断已经结算。普通扫描、生命周期和全回撤零目标投影都应在组合锁内读取同一来源的逐持仓可卖量，核有限且`0 <= available <= actual`，再扣所有来源未完成SELL预留。缺证、畸形、负值或超量保留退出意图并明确标记未知，不建单；行情日缺失单独标记。成交仍作为已发生事实入账。

当前`planning_account`尚未提供可信可卖量，故软件只能安全保留退出意图；T4券商/结算事实接入后才可验收真实跨日SELL建单。相关实现为`backend/modules/quant_strategy/application/position_planner.py`、`execution.py`、`position_lifecycle_manager.py`及`portfolio_drawdown_actions.py`。隔离PG普通扫描10项、生命周期56项与独立Code Review R2通过；实际券商可卖来源未验收。
