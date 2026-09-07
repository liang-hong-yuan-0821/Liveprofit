"""market_data 集成测试（目录种子、新鲜度/开闭市、ingestion 规范化）。"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pandas as pd
import pytest

from backend.modules.market_data.application.errors import (
    HotConceptsUpstreamUnavailableError,
    IntervalNotSupportedError,
    MarketDataUpstreamUnavailableError,
    RangeTooLargeError,
)
from backend.modules.market_data.application.service import MarketDataService
from backend.modules.market_data.infrastructure.calendar_adapter import FakeCalendar
from backend.modules.market_data.infrastructure.ingestion import IndexBarsIngestion
from backend.modules.market_data.infrastructure.repositories import SqlAlchemyMarketUow
from backend.modules.market_data.infrastructure.models import MarketAsset


def _uow(env):
    return SqlAlchemyMarketUow(env["session_factory"])


def test_catalog_seeded_with_confirmed_assets(env):
    with _uow(env) as uow:
        assets = MarketDataService(uow).list_assets()
    assert len(assets) == 12
    by_market: dict[str, list] = {}
    for asset in assets:
        by_market.setdefault(asset.market, []).append(asset)
    assert [a.symbol for a in by_market["CN"]] == [
        "000001.SH", "399001.SZ", "399006.SZ", "000688.SH", "000016.SH", "000852.SH", "000015.SH",
    ]
    assert [a.symbol for a in by_market["US"]] == [".INX", ".DJI", ".IXIC"]
    assert [a.symbol for a in by_market["KR"]] == ["KOSPI", "KOSDAQ"]
    for asset in by_market["CN"]:
        assert asset.availability_status == "AVAILABLE"
        assert asset.supported_intervals == ["1d"]
    for asset in by_market["US"] + by_market["KR"]:
        assert asset.availability_status == "UNAVAILABLE"  # 实测验收前不伪造 bars


def test_get_bars_freshness_and_closed_session(env):
    clock = type("Clock", (), {"now": lambda self: datetime(2026, 9, 5, 9, 0, tzinfo=timezone.utc)})()
    calendar = FakeCalendar(trading_day=False, last_day=date(2026, 9, 4))
    with _uow(env) as uow:
        service = MarketDataService(uow, clock=clock, calendar=calendar)
        # 直接落 bars 读模型（ingestion 单独测试）
        asset = uow.session.query(MarketAsset).filter_by(symbol="000001.SH").one()
        from backend.modules.market_data.infrastructure.repositories import MarketBarRepository

        MarketBarRepository(uow.session).upsert(
            asset.id,
            [
                {
                    "trading_date": date(2026, 9, 4),
                    "open": 3300.0, "high": 3350.0, "low": 3290.0, "close": 3340.0,
                    "volume": 1e9, "source_updated_at": datetime(2026, 9, 5, tzinfo=timezone.utc),
                }
            ],
            source="tushare",
        )
        uow.commit()
        dto = service.get_bars(
            market="CN", symbol="000001.SH", interval="1d",
            from_date=date(2026, 9, 1), to_date=date(2026, 9, 5),
        )
    assert dto.freshness_status == "FRESH"  # 覆盖最近已收盘交易日（9-4）
    assert dto.market_session_status == "CLOSED"  # 非交易日 → 休市
    assert dto.market_closed_reason is not None
    assert len(dto.bars) == 1
    assert dto.bars[0]["close"] == 3340.0


def test_bars_validation_errors(env):
    with _uow(env) as uow:
        service = MarketDataService(uow, calendar=FakeCalendar())
        with pytest.raises(IntervalNotSupportedError):
            service.get_bars(
                market="CN", symbol="000001.SH", interval="5d",
                from_date=date(2026, 8, 1), to_date=date(2026, 9, 1),
            )
        with pytest.raises(RangeTooLargeError):
            service.get_bars(
                market="CN", symbol="000001.SH", interval="1d",
                from_date=date(2025, 1, 1), to_date=date(2026, 9, 1),
            )
        with pytest.raises(MarketDataUpstreamUnavailableError):
            service.get_bars(
                market="US", symbol=".INX", interval="1d",
                from_date=date(2026, 8, 1), to_date=date(2026, 9, 1),
            )


class FakeTushareProvider:
    def get_index_data_df(self, index_code, start_date, end_date):
        return pd.DataFrame(
            [
                {"trade_date": "20260904", "open": 3300.0, "high": 3350.0, "low": 3290.0,
                 "close": 3340.0, "vol": 1_000_000.0, "amount": None},
                {"trade_date": "20260905", "open": 3340.0, "high": 3360.0, "low": 3330.0,
                 "close": 3355.0, "vol": 1_100_000.0, "amount": None},
            ]
        )


def test_ingestion_normalizes_and_is_idempotent(env):
    with _uow(env) as uow:
        ingestion = IndexBarsIngestion(uow, provider_factory=lambda market: FakeTushareProvider())
        count = ingestion.ingest_asset("CN", "000001.SH", "20260901", "20260905")
        assert count == 2
        # 幂等重跑：ON CONFLICT DO UPDATE，行数不变
        count2 = ingestion.ingest_asset("CN", "000001.SH", "20260901", "20260905")
        assert count2 == 2
        from backend.modules.market_data.infrastructure.repositories import MarketBarRepository
        from backend.modules.market_data.infrastructure.models import MarketAsset

        asset = uow.session.query(MarketAsset).filter_by(symbol="000001.SH").one()
        bars = MarketBarRepository(uow.session).list_range(asset.id, date(2026, 9, 1), date(2026, 9, 6))
        assert [(b.trading_date, float(b.close)) for b in bars] == [
            (date(2026, 9, 4), 3340.0), (date(2026, 9, 5), 3355.0),
        ]


def test_us_kr_ingestion_requires_live_acceptance(env):
    with _uow(env) as uow:
        ingestion = IndexBarsIngestion(uow, provider_factory=lambda market: FakeTushareProvider())
        with pytest.raises(NotImplementedError, match="live 验收"):
            ingestion.ingest_asset("US", ".INX", "20260901", "20260905")


def test_concept_snapshot_ingestion_placeholder(env):
    from backend.modules.market_data.infrastructure.ingestion import ConceptSnapshotIngestion

    with _uow(env) as uow:
        with pytest.raises(NotImplementedError, match="heat_v1"):
            ConceptSnapshotIngestion(uow).ingest_cn_snapshot(date(2026, 9, 4))
        # 未落快照时：NO_HOT_CONCEPTS 是正常业务态
        as_of, items, status, freshness = MarketDataService(uow).get_hot_concepts(market="CN", as_of=None, limit=30)
        assert status == "NO_HOT_CONCEPTS"
        assert items == []
        assert freshness in ("FRESH", "STALE")
        # 首期仅 CN：其他市场为上游不可用语义
        with pytest.raises(HotConceptsUpstreamUnavailableError):
            MarketDataService(uow).get_hot_concepts(market="US", as_of=None, limit=30)
