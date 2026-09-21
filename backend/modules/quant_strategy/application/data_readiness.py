"""量化任务级共同数据水位（plan C9）。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from backend.modules.analysis.application.errors import FatalAnalysisError


class DataReadinessError(FatalAnalysisError):
    code = "QUANT_DATA_NOT_READY"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.code = self.__class__.code


def _as_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


@dataclass(frozen=True)
class DataReadinessSnapshot:
    requested_trade_date: date
    market_as_of_trade_date: date
    daily_trade_date: date
    factor_trade_date: date
    adj_factor_trade_date: date
    trade_status_trade_date: date


class DataReadinessGate:
    """固定单次执行共同水位；任何必需资源无水位时整任务 fail-closed。"""

    @staticmethod
    def resolve(conn, requested_trade_date: date) -> DataReadinessSnapshot:
        row = conn.execute(
            "/* quant_data_readiness */ "
            "SELECT "
            "(SELECT max(d.trade_date) FROM market.instrument_daily d "
            " JOIN market.instrument i ON i.ts_code=d.ts_code "
            " WHERE i.instrument_type='stock' AND i.list_status='L' AND d.trade_date <= %s), "
            "(SELECT max(f.trade_date) FROM market.factor_daily f "
            " WHERE f.trade_date <= %s AND f.ma_qfq_5 IS NOT NULL), "
            "(SELECT max(a.trade_date) FROM market.adj_factor a WHERE a.trade_date <= %s), "
            "(SELECT max(s.trade_date) FROM market.trade_status_daily s WHERE s.trade_date <= %s)",
            tuple([requested_trade_date.isoformat()] * 4),
        ).fetchone()
        if row is None:
            raise DataReadinessError("无法读取量化数据水位")
        names = ("日线", "qfq 因子", "复权因子", "交易状态")
        values = tuple(_as_date(v) for v in row[:4])
        missing = [name for name, value in zip(names, values) if value is None]
        if missing:
            raise DataReadinessError(f"量化数据水位未就绪：{','.join(missing)}")
        common = min(values)
        return DataReadinessSnapshot(
            requested_trade_date=requested_trade_date,
            market_as_of_trade_date=common,
            daily_trade_date=values[0],
            factor_trade_date=values[1],
            adj_factor_trade_date=values[2],
            trade_status_trade_date=values[3],
        )
