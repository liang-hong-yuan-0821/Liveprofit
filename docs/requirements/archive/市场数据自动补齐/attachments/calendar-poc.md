# 日历依赖评审 POC（2026-09-23）

环境：项目 Python，pandas 3.0.5、numpy 2.5.1；额外依赖仅安装到工作区 `var/market-refresh-review/deps`，未改变项目 `.venv` 或依赖锁文件。

隔离依赖：exchange-calendars 4.13.2、pyluach 2.3.0、korean-lunar-calendar 0.4.0、toolz 1.1.0。

复现（在项目根目录，隔离依赖已安装）：

```text
.venv/Scripts/python.exe docs/requirements/市场数据自动补齐/attachments/calendar_poc.py
```

脚本不读密钥、不调用行情源、不连接 Redis/PG。实际输出见 [calendar-poc.json](calendar-poc.json)，二次运行与保存结果一致。

| 日历 | 2026 | 2027 | 结论 |
|------|------|------|------|
| XSHG | 242 个 session，与项目 trade_cal_2026.json 缓存开放日完全一致 | 构造时报 ValueError，假期仅记录到 2026 年 | 当前年可用；不能承诺 2027 已支持 |
| XNYS | 251 个 session | 251 个 session | 两年可构造；抽查 DST 切换和提前收盘 |
| XKRX | 246 个 session | 247 个 session | 两年可构造；抽查市场日期和收盘时刻 |

XNYS 抽样：3 月 6 日收盘 21:00 UTC，3 月 9 日 20:00 UTC；11 月 27 日提前至 18:00 UTC。CN 10 月 1 日为非 session。

边界：这不是与各交易所全年公告逐项比对的认证，也不证明行情代理按收盘时间发布数据。它证明当前库版本可运行、所列 session 结果及 XSHG 2027 支持限制。生产应按已验证的支持范围运行，超出范围返回 UNKNOWN，并在到期前提示更新日历依赖。
