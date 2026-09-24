"""Refresh validation and stock-only source behavior; never uses live sources."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

from AI.dataflows.providers.base_provider import ProviderNetworkAccessDenied
from db.instrument.ingest.frames import StoreFetchError, fetch_stock_daily_frame
from db.instrument.ingest.refresh import RefreshSpec, _collect_index_bars_unlocked


def stock_frame(codes, day="20260922"):
    return pd.DataFrame({"ts_code": codes, "trade_date": day, "open": 10., "high": 11.,
                         "low": 9., "close": 10., "pct_chg": 1.})


def test_stock_truncation_uses_single_day_batches_of_100(monkeypatch):
    monkeypatch.setattr("db.instrument.ingest.frames.time.sleep", lambda _: None)
    codes = [f"{i:06}.SZ" for i in range(235)]
    provider = MagicMock()
    provider.get_full_market_daily_df.return_value = stock_frame([str(i) for i in range(6000)])
    provider._api_call.side_effect = lambda fn, **kw: stock_frame(kw["ts_code"].split(","), kw["trade_date"])
    result = fetch_stock_daily_frame(provider, "20260922", codes)
    assert len(result) == 235
    assert [len(c.kwargs["ts_code"].split(",")) for c in provider._api_call.call_args_list] == [100, 100, 35]
    assert all(c.kwargs["trade_date"] == "20260922" for c in provider._api_call.call_args_list)
    assert all("start_date" not in c.kwargs for c in provider._api_call.call_args_list)
    provider.get_full_market_factor_df.assert_not_called()
    provider.api.fund_daily.assert_not_called()


def test_stock_batch_rejects_codes_outside_the_requested_batch(monkeypatch):
    monkeypatch.setattr("db.instrument.ingest.frames.time.sleep", lambda _: None)
    code = "000001.SZ"
    provider = MagicMock()
    provider.get_full_market_daily_df.return_value = stock_frame([code] * 6000)
    provider._api_call.return_value = stock_frame(["999999.SZ"])

    with pytest.raises(StoreFetchError, match="UPSTREAM_NOT_READY"):
        fetch_stock_daily_frame(provider, "20260922", [code])

    assert provider._api_call.call_count == 1


def test_local_network_denial_does_not_try_index_fallback():
    blocked = ProviderNetworkAccessDenied("blocked by local policy")
    provider = SimpleNamespace(
        _network_access_error=blocked,
        get_index_data_df=lambda *args: None,
    )
    fallback = MagicMock()
    conn = MagicMock()

    with pytest.raises(ProviderNetworkAccessDenied):
        _collect_index_bars_unlocked(
            conn, provider, fallback, "000001.SH", ["2026-09-22"],
        )

    fallback.get_index_data_df.assert_not_called()
    conn.rollback.assert_called_once()


@pytest.mark.parametrize("frame", [
    stock_frame(["000001.SZ"], "20260921"),
    stock_frame(["000001.SZ"]).drop(columns="pct_chg"),
    stock_frame(["000001.SZ"]).drop(columns="open"),
    stock_frame(["000001.SZ"]).assign(high=None),
    stock_frame(["000001.SZ", "000001.SZ"]),
    stock_frame(["000001.SZ"]).assign(close=None),
    stock_frame(["000001.SZ"]).assign(close=float("inf")),
])
def test_wrong_day_missing_columns_duplicate_or_null_not_success(frame, monkeypatch):
    monkeypatch.setattr("db.instrument.ingest.frames.time.sleep", lambda _: None)
    provider = SimpleNamespace(get_full_market_daily_df=lambda *a: frame)
    with pytest.raises(StoreFetchError):
        fetch_stock_daily_frame(provider, "20260922", ["000001.SZ"])


def test_spec_normalizes_dates_and_rejects_cross_market_targets():
    spec = RefreshSpec("CN_INDEX_BARS", date(2026, 9, 22), ("000001.SH",),
                       ("20260921", "20260922"), (("000001.SH", "20260921"),))
    assert spec.target_trade_date == "2026-09-22"
    assert spec.units == (("000001.SH", "2026-09-21"),)
    with pytest.raises(ValueError):
        RefreshSpec("US_INDEX_BARS", "2026-09-22", ("000001.SH",))


def test_notifier_uses_platform_url_and_invalidates_exact_resource(monkeypatch):
    from db.instrument.ingest.notifications import market_changed_notifier_from_env
    monkeypatch.setenv("LIVEPROFIT_REDIS_URL", "redis://test-only:6379/12")
    monkeypatch.setenv("REDIS_CONNECTION_STRING", "redis://unused:6379/0")
    monkeypatch.setenv("MARKET_REFRESH_KEY_PREFIX", "test:ingestion:")
    client = MagicMock()
    factory = MagicMock(return_value=client)
    monkeypatch.setattr("redis.Redis.from_url", factory)
    callback = market_changed_notifier_from_env()
    callback("CN_INDEX_BARS")
    assert factory.call_args.args == ("redis://test-only:6379/12",)
    pipe = client.__enter__.return_value.pipeline.return_value.__enter__.return_value
    assert pipe.set.call_args.args[0] == "test:ingestion:changed:CN_INDEX_BARS"
    pipe.delete.assert_called_once_with("test:ingestion:coverage:CN_INDEX_BARS")
    pipe.execute.assert_called_once()
