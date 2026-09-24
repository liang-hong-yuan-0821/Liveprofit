# 源接口有界只读验证（2026-09-23）

本次没有运行市场采集，没有连接或写入 PostgreSQL，没有输出 Token。项目正式环境已安装 `exchange-calendars==4.13.2`，日期策略的 31 个本地单测通过；此记录专门区分行情源证据与日历证据。

## 采样范围

既有 Provider 使用的代理为 `https://ts.gyzcloud.top/api`（代码：`AI/dataflows/providers/cn/tushare.py:TushareProvider._connect`）。原样使用当前 Provider 的请求协议，最多 3 次 HTTP POST，每次连接超时 5 秒、读取超时 15 秒，不重试。

| 资源 | api_name | params | fields |
|------|----------|--------|--------|
| US 单指数 | `index_global` | `ts_code=SPX,start_date=20260918,end_date=20260923` | `ts_code,trade_date,open,high,low,close` |
| KR 单指数 | `index_global` | `ts_code=KS11,start_date=20260918,end_date=20260923` | `ts_code,trade_date,open,high,low,close` |
| CN 单日停牌原始响应 | `suspend_d` | `suspend_date=20260922` | `ts_code,suspend_date` |

可复现脚本位于工作区临时目录 `var/market_refresh_source_poc.py`。执行命令：`.venv/Scripts/python.exe var/market_refresh_source_poc.py`。脚本仅从项目 `.env` 读取现有 `TUSHARE_TOKEN`，通过正常认证请求交给上述代理；结果摘要只保留请求参数、HTTP/API 状态、返回字段、行数、日期集合及至多 3 行公开市场样本，不记录凭据。

## 实际观察与授权经过

- 首次实际请求时间：`2026-09-22T17:13:28Z`（北京时间 2026-09-23 01:13）。三个请求均在沙箱内返回 `requests.ConnectionError`，没有拿到 HTTP 响应或任何行情行。
- 独立无凭据诊断：域名可解析；`socket.create_connection(('ts.gyzcloud.top',443), timeout=5)` 返回 `PermissionError(errno=13, winerror=10013)`，证明此轮网络访问受当前执行环境限制，不能把结果解释为数据源空集。
- 针对同一脚本首次申请网络权限后，**自动审批拒绝执行**：审批认为向外部目的地 `ts.gyzcloud.top` 发送 `TUSHARE_TOKEN` 尚无明确用户授权。未绕过拒绝。用户随后明确授权同一目的地、同一 Token 的上述 **3 个只读请求**，再申请后获准。
- 授权后实际执行于 `2026-09-22T17:16:29Z`（北京时间 2026-09-23 01:16），三次均 HTTP 200、API code 0；无数据库连接，执行完这三次即停止，没有增加字段/参数变体探测。

| 资源 | 实际返回 | 可确认事实 |
|------|----------|------------|
| SPX | 2 行；trade_date 为 `20260918`、`20260921`；六个请求字段齐全；最新 close=`7764.7037` | 本观察时刻已有美国 9/21 日线；此时纽约仍在 9/22 盘中，返回标签与市场本地 session 日期一致 |
| KS11 | 3 行；trade_date 为 `20260918`、`20260921`、`20260922`；六个请求字段齐全；最新 close=`7017.91` | 本观察时刻已有韩国 9/22 日线；此时首尔为 9/23 凌晨，返回标签与市场本地 session 日期一致 |
| suspend_d | **5000 行**；字段名为 `ts_code,suspend_date`，但全部 `suspend_date=null`；首三代码 `000016.SZ/002731.SZ/002860.SZ` | 现有参数/字段组合不能提供可信目标日期；疑似过滤参数/字段不受代理支持且可能截断，不能将5000行视作完整集合 |

安全原始摘要存于工作区临时文件 `var/market_refresh_source_poc.json`（不含 Token）。上述行情值仅用作可复核采样证据，不是业务数据落库。

## 仍然未知的事实与已落实降级

1. US/KR 本轮样本与市场本地 session 日期一致；没有观测最近数据首次可用的时刻。US/KR 4 小时、CN 5 小时仍为项目保守策略，**不是已证实的数据源 SLA**。单次成功采样只能证明“在该观察时刻已有这些行”，不能推导首次发布时间。
2. `suspend_d` **当前请求组合已实证不可作为目标日停牌证据**；没有证实可靠替代参数、成功空响应语义或代理截断上限。实现保持 fail-closed：本次5000行虽未达6000警戒线，但全部日期空值会被 `dates.isna().any()` 拦截，返回来源不可用；`None`、缺列、日期不符、重复/空代码同样拒绝。尚不能证明的股票缺口保持 `PARTIAL/UNAVAILABLE`，不假定停牌。修正接口参数需要新的有限源验证，不能未经验证改成猜测参数并宣布完成。
3. 日历越界与网络/来源不可用彼此独立：CN 2027 为 `UNKNOWN` 并阻止准入；仍在支持范围内的日期照常计算，缺行情由覆盖核验保留缺口。覆盖核验只读现有 PG 表，缺少来源证据不会制造豁免行。

相关验证：`backend/tests/unit/market_data/test_refresh_policy.py` 31 passed；`backend/tests/integration/market_data/test_refresh_coverage.py` 10 passed。集成测试只使用 `liveprofit_market_test` 的合成数据，不证明真实代理能力。
