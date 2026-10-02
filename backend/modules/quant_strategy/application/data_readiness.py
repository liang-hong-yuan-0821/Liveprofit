"""量化任务级共同水位及证券/字段覆盖门禁。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from backend.modules.analysis.application.errors import FatalAnalysisError
from backend.modules.market_data.application.refresh_policy import (
    RefreshTarget,
    Resource,
)
from backend.modules.market_data.infrastructure.refresh_repository import (
    RefreshRepository,
)


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
    coverage_digest: str
    universe_digest: str


class DataReadinessGate:
    """固定共同水位；资源缺失或股票输入覆盖不足时 fail-closed。"""

    @staticmethod
    def resolve(
        conn, requested_trade_date: date, *, coverage_repository: RefreshRepository | None = None,
    ) -> DataReadinessSnapshot:
        row = conn.execute(
            "/* quant_data_readiness */ "
            "SELECT "
            "(SELECT max(d.trade_date) FROM market.instrument_daily d "
            " JOIN market.instrument i ON i.ts_code=d.ts_code "
            " WHERE i.instrument_type='stock' AND i.list_status='L' AND d.trade_date <= %s), "
            "(SELECT max(f.trade_date) FROM market.factor_daily f "
            " WHERE f.trade_date <= %s AND f.ma_qfq_5 IS NOT NULL), "
            "(SELECT max(a.trade_date) FROM market.adj_factor a WHERE a.trade_date <= %s), "
            "(SELECT max(s.trade_date) FROM market.trade_status_effective s WHERE s.trade_date <= %s)",
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
        target = RefreshTarget(
            resource=Resource.CN_STOCK_QUANT_INPUTS,
            market="CN",
            market_date=common,
            calendar_status="OK",
            supported_through=common,
            expected_trade_date=common,
            next_ready_at=None,
            window_dates=(common,),
            session_status="CLOSED",
        )
        coverage = (coverage_repository or RefreshRepository()).snapshot(
            Resource.CN_STOCK_QUANT_INPUTS, target, conn=conn,
        )
        if coverage.get("freshness") != "FRESH":
            missing_count = coverage.get("missing_count", "unknown")
            raise DataReadinessError(
                f"量化数据水位 {common} 覆盖不足：{missing_count} 个字段/证券缺口"
            )
        return DataReadinessSnapshot(
            requested_trade_date=requested_trade_date,
            market_as_of_trade_date=common,
            daily_trade_date=values[0],
            factor_trade_date=values[1],
            adj_factor_trade_date=values[2],
            trade_status_trade_date=values[3],
            coverage_digest=str(coverage["coverage_digest"]),
            universe_digest=str(coverage["universe_digest"]),
        )
