"""交易日历适配：AI 内核交易日历（CN/SSE）防腐边界。

- 内核日历仅覆盖 CN；US/KR 资产在实测验收前 availability_status=UNAVAILABLE，不进入新鲜度判定。
- AI 导入延迟执行，保持 CLI 路径纯净。
"""

from __future__ import annotations

from datetime import date, datetime


class CNCalendarAdapter:
    def is_trading_day(self, day: date) -> bool:
        from AI.dataflows.utils.trading_calendar import is_trading_day

        return is_trading_day(day.strftime("%Y%m%d"), exchange="SSE")

    def last_trading_day(self, day: date) -> date:
        from AI.dataflows.utils.trading_calendar import get_last_trading_day

        result = get_last_trading_day(day.strftime("%Y%m%d"), exchange="SSE")
        return datetime.strptime(result, "%Y%m%d").date()


class FakeCalendar:
    """测试注入：可配置交易日与最近交易日。"""

    def __init__(self, *, trading_day: bool = True, last_day: date | None = None) -> None:
        self._trading_day = trading_day
        self._last_day = last_day

    def is_trading_day(self, day: date) -> bool:
        return self._trading_day

    def last_trading_day(self, day: date) -> date:
        return self._last_day or day
