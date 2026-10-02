# test-catalog-begin
# {
#   "purpose": "量化研究 / dataset_builder",
#   "keywords": [
#     "量化研究",
#     "交易日历",
#     "数据完整性",
#     "数据库",
#     "ETF",
#     "历史审计",
#     "ST状态",
#     "状态",
#     "个股分析",
#     "停牌",
#     "dataset_builder",
#     "calendar",
#     "coverage",
#     "db",
#     "etf",
#     "history",
#     "st",
#     "status",
#     "stock",
#     "suspension"
#   ],
#   "covers": [
#     "backend/modules/quant_research/application/dataset_builder.py",
#     "backend/modules/quant_research/infrastructure/dataset_repository.py",
#     "backend/modules/quant_research/infrastructure/dataset_store.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

import shutil
from datetime import date
from uuid import UUID, uuid4

import pandas as pd
import pytest

from backend.modules.quant_research.application.dataset_builder import DatasetBuilder
from backend.modules.quant_research.infrastructure.dataset_repository import (
    DatasetRepository,
)
from backend.modules.quant_research.infrastructure.dataset_store import (
    DatasetIntegrityError,
    DatasetStore,
)


def _tables():
    days = [date(2020, 1, 2), date(2020, 1, 3)]
    return {
        "instrument": pd.DataFrame([
            {"ts_code": "000001.SZ", "instrument_type": "stock", "list_status": "D",
             "list_date": date(1991, 1, 1), "delist_date": date(2021, 1, 1)},
        ]),
        "daily": pd.DataFrame([
            {"ts_code": "000001.SZ", "trade_date": day, "open": 10., "high": 11.,
             "low": 9., "close": 10., "available_at": f"{day}T16:00:00+08:00"}
            for day in days
        ]),
        "adj_factor": pd.DataFrame([
            {"ts_code": "000001.SZ", "trade_date": day, "adj_factor": 1.} for day in days
        ]),
        "factor": pd.DataFrame([
            {"ts_code": "000001.SZ", "trade_date": day, "ma_qfq_5": 10.} for day in days
        ]),
        "trade_status": pd.DataFrame([
            {"ts_code": "000001.SZ", "trade_date": day, "is_st": False,
             "is_suspended": False, "suspension_scope": "none"} for day in days
        ]),
    }


def test_future_rows_do_not_change_historical_input_and_delisted_stock_remains():
    tables = _tables()
    builder = DatasetBuilder()
    before = builder.build(as_of=date(2020, 1, 2), tables=tables)
    tables["daily"].loc[1, "close"] = 9999.
    after = builder.build(as_of=date(2020, 1, 2), tables=tables)
    tables["daily"] = tables["daily"].iloc[:1].copy()
    after_deletion = builder.build(as_of=date(2020, 1, 2), tables=tables)
    assert before.quality.status == "READY"
    assert not before.quality.certifiable
    assert "coverage_audit: historical completeness not verified" in before.quality.certification_issues
    pd.testing.assert_frame_equal(before.tables["daily"], after.tables["daily"])
    pd.testing.assert_frame_equal(before.tables["daily"], after_deletion.tables["daily"])
    assert before.tables["instrument"]["ts_code"].tolist() == ["000001.SZ"]
    assert pd.isna(before.tables["instrument"].loc[0, "delist_date"])
    assert "list_status" not in before.tables["instrument"]


def test_benchmark_is_frozen_separately_from_stock_universe():
    tables = _tables()
    tables["benchmark_daily"] = pd.DataFrame([
        {"ts_code": "000300.SH", "trade_date": date(2020, 1, 2), "close": 4000., "source": "tushare"},
        {"ts_code": "000300.SH", "trade_date": date(2020, 1, 3), "close": 9000., "source": "tushare"},
    ])
    dataset = DatasetBuilder().build(as_of=date(2020, 1, 2), tables=tables,
                                     instrument_types=("stock",))
    assert dataset.quality.status == "READY"
    assert dataset.tables["instrument"]["ts_code"].tolist() == ["000001.SZ"]
    assert dataset.tables["benchmark_daily"]["close"].tolist() == [4000.]
    assert "benchmark_daily: exchange-calendar and source availability not verified" in dataset.quality.certification_issues
    tables["benchmark_daily"].loc[0, "close"] = -1
    bad = DatasetBuilder().build(as_of=date(2020, 1, 2), tables=tables,
                                 instrument_types=("stock",))
    assert "benchmark_daily: invalid index observations" in bad.quality.issues


def test_bad_benchmark_date_records_quality_issue_instead_of_raising():
    tables = _tables()
    tables["benchmark_daily"] = pd.DataFrame([
        {"ts_code": "000300.SH", "trade_date": "not-a-date", "close": 4000., "source": "tushare"},
    ])
    dataset = DatasetBuilder().build(as_of=date(2020, 1, 2), tables=tables,
                                     instrument_types=("stock",))
    assert dataset.quality.status == "INSUFFICIENT_EVIDENCE"
    assert "benchmark_daily: invalid trade_date" in dataset.quality.issues
    assert dataset.tables["benchmark_daily"].empty


def test_mixed_date_types_keep_valid_benchmark_rows_and_reject_bad_date():
    tables = _tables()
    tables["benchmark_daily"] = pd.DataFrame([
        {"ts_code": "000300.SH", "trade_date": date(2020, 1, 2), "close": 4000., "source": "tushare"},
        {"ts_code": "000300.SH", "trade_date": pd.Timestamp("2020-01-03"), "close": 4001., "source": "tushare"},
        {"ts_code": "000300.SH", "trade_date": "not-a-date", "close": 4002., "source": "tushare"},
        {"ts_code": "000300.SH", "trade_date": pd.Timestamp("2020-01-04"), "close": 4003., "source": "tushare"},
    ])
    dataset = DatasetBuilder().build(as_of=date(2020, 1, 3), tables=tables,
                                     instrument_types=("stock",))
    assert dataset.tables["benchmark_daily"]["close"].tolist() == [4000., 4001.]
    assert "benchmark_daily: invalid trade_date" in dataset.quality.issues


def test_retrospective_suspension_notice_is_frozen_only_after_publication():
    tables = _tables()
    tables["suspension_evidence"] = pd.DataFrame([{
        "ts_code": "000001.SZ", "trade_date": date(2020, 1, 2),
        "scope": "intraday", "published_on": date(2020, 1, 3),
    }])
    early = DatasetBuilder().build(as_of=date(2020, 1, 2), tables=tables)
    assert early.tables["suspension_evidence"].empty
    later = DatasetBuilder().build(as_of=date(2020, 1, 3), tables=tables)
    assert later.tables["suspension_evidence"]["scope"].tolist() == ["intraday"]


def test_delisted_stock_stays_in_post_delisting_research_history():
    tables = _tables()
    tables["instrument"].loc[0, "delist_date"] = date(2020, 1, 3)
    dataset = DatasetBuilder().build(as_of=date(2020, 1, 5), tables=tables)
    assert dataset.quality.status == "READY"
    assert dataset.tables["daily"]["trade_date"].tolist() == [date(2020, 1, 2)]


def test_all_active_instruments_with_null_delist_dates():
    tables = _tables()
    tables["instrument"]["delist_date"] = None
    dataset = DatasetBuilder().build(as_of=date(2020, 1, 3), tables=tables)
    assert dataset.quality.status == "READY"
    assert len(dataset.tables["daily"]) == 2


def test_unknown_listing_start_cannot_disappear_from_coverage():
    tables = _tables()
    tables["instrument"]["list_date"] = None
    dataset = DatasetBuilder().build(as_of=date(2020, 1, 3), tables=tables)
    assert dataset.quality.status == "INSUFFICIENT_EVIDENCE"
    assert "instrument: invalid historical listing interval" in dataset.quality.issues


def test_missing_historical_status_is_insufficient_evidence():
    tables = _tables()
    tables["trade_status"] = tables["trade_status"].iloc[0:0]
    dataset = DatasetBuilder().build(as_of=date(2020, 1, 2), tables=tables)
    assert dataset.quality.status == "INSUFFICIENT_EVIDENCE"
    assert "trade_status: historical coverage empty" in dataset.quality.issues


@pytest.mark.parametrize("case,issue", [
    ("status", "trade_status: invalid historical flags"),
    ("ohlc", "daily: impossible OHLC"),
    ("instrument", "instrument: duplicate identity"),
])
def test_invalid_historical_facts_cannot_be_published(case, issue):
    tables = _tables()
    if case == "status":
        tables["trade_status"]["is_st"] = tables["trade_status"]["is_st"].astype(object)
        tables["trade_status"].loc[0, "is_st"] = None
    elif case == "ohlc":
        tables["daily"].loc[0, "high"] = 8.
    else:
        tables["instrument"] = pd.concat(
            [tables["instrument"], tables["instrument"]], ignore_index=True,
        )
    dataset = DatasetBuilder().build(as_of=date(2020, 1, 3), tables=tables)
    assert dataset.quality.status == "INSUFFICIENT_EVIDENCE"
    assert issue in dataset.quality.issues


def test_etf_catalog_requires_as_of_tracking_evidence():
    tables = _tables()
    for frame in tables.values():
        frame["ts_code"] = "510300.SH"
    tables["instrument"]["instrument_type"] = "fund"
    missing = DatasetBuilder().build(as_of=date(2020, 1, 2), tables=tables)
    assert "etf_catalog: dated classification evidence absent" in missing.quality.issues

    tables["etf_catalog"] = pd.DataFrame([{
        "ts_code": "510300.SH", "index_code": "000300.SH", "etf_type": "股票型",
        "list_date": date(2012, 5, 28), "available_at": "2021-01-01T00:00:00+08:00",
    }])
    future = DatasetBuilder().build(as_of=date(2020, 1, 2), tables=tables)
    assert "etf_catalog: historical ETF classification incomplete" in future.quality.issues
    tables["etf_catalog"].loc[0, "available_at"] = "2019-01-01T00:00:00+08:00"
    ready = DatasetBuilder().build(as_of=date(2020, 1, 2), tables=tables)
    assert ready.quality.status == "READY"


def test_etf_does_not_require_stock_st_status():
    tables = _tables()
    for frame in tables.values():
        frame["ts_code"] = "510300.SH"
    tables["instrument"]["instrument_type"] = "fund"
    tables["trade_status"] = tables["trade_status"].iloc[:0]
    tables["etf_catalog"] = pd.DataFrame([{
        "ts_code": "510300.SH", "index_code": "000300.SH", "etf_type": "股票型",
        "list_date": date(2012, 5, 28), "available_at": "2019-01-01T00:00:00+08:00",
    }])
    tables["benchmark_daily"] = pd.DataFrame([{
        "ts_code": "wrong", "trade_date": "invalid", "close": -1,
        "source": "",
    }])
    dataset = DatasetBuilder().build(as_of=date(2020, 1, 2), tables=tables,
                                     instrument_types=("fund",))
    assert dataset.quality.status == "READY"
    assert not dataset.quality.certifiable
    assert "benchmark_daily" not in dataset.tables


def test_stock_only_snapshot_excludes_unverified_fund_universe(tmp_path):
    tables = _tables()
    tables["instrument"] = pd.concat([tables["instrument"], pd.DataFrame([{
        "ts_code": "510300.SH", "instrument_type": "fund",
        "list_date": date(2012, 5, 28), "delist_date": None,
    }])], ignore_index=True)
    tables["daily"] = pd.concat([tables["daily"], pd.DataFrame([{
        "ts_code": "510300.SH", "trade_date": date(2020, 1, 2),
        "open": 4., "high": 4.1, "low": 3.9, "close": 4.,
        "available_at": "2020-01-02T16:00:00+08:00",
    }])], ignore_index=True)
    stock = DatasetBuilder().build(
        as_of=date(2020, 1, 2), tables=tables, instrument_types=("stock",),
    )
    assert stock.quality.status == "READY"
    assert stock.tables["instrument"]["ts_code"].tolist() == ["000001.SZ"]
    assert stock.tables["daily"]["ts_code"].tolist() == ["000001.SZ"]
    store = DatasetStore(tmp_path / "stocks")
    snapshot_id = uuid4()
    store.publish(snapshot_id, stock, source="fixture")
    manifest, _ = store.read(snapshot_id)
    assert manifest["instrument_types"] == ["stock"]
    combined = DatasetBuilder().build(as_of=date(2020, 1, 2), tables=tables)
    assert combined.quality.status == "INSUFFICIENT_EVIDENCE"
    assert "etf_catalog: dated classification evidence absent" in combined.quality.issues


def test_publication_requires_ready_and_checksum_is_verified(tmp_path):
    tables = _tables()
    dataset = DatasetBuilder().build(as_of=date(2020, 1, 2), tables=tables)
    store = DatasetStore(tmp_path / "research")
    snapshot_id = uuid4()
    path = store.publish(snapshot_id, dataset, source="fixture")
    manifest, read_tables = store.read(snapshot_id)
    assert manifest["source"] == "fixture"
    assert manifest["quality"] == "READY" and not manifest["certifiable"]
    assert "industry_history: point-in-time membership absent" in manifest["certification_issues"]
    pd.testing.assert_frame_equal(read_tables["daily"], dataset.tables["daily"])
    with pytest.raises(DatasetIntegrityError):
        store.publish(snapshot_id, dataset, source="fixture")
    with (path / "daily.parquet").open("ab") as stream:
        stream.write(b"tampered")
    with pytest.raises(DatasetIntegrityError):
        store.read(snapshot_id)


def test_unready_snapshot_cannot_be_published(tmp_path):
    tables = _tables()
    tables["trade_status"] = tables["trade_status"].iloc[0:0]
    dataset = DatasetBuilder().build(as_of=date(2020, 1, 2), tables=tables)
    store = DatasetStore(tmp_path)
    with pytest.raises(DatasetIntegrityError):
        store.publish(uuid4(), dataset, source="fixture")
    assert not list(tmp_path.iterdir())


def test_verified_calendar_gap_blocks_publication_and_reports_count(tmp_path):
    tables = _tables()
    tables["factor"]["ma_qfq_20"] = 10.
    tables["factor"]["ma_qfq_60"] = 10.
    tables["factor"]["atr_qfq"] = 1.
    tables["trade_status"]["market_board"] = "MAIN"
    tables["trade_status"]["source"] = "tushare"
    days = (date(2020, 1, 2), date(2020, 1, 3))
    complete = DatasetBuilder().build(
        as_of=days[-1], tables=tables, verified_trading_days=days,
    )
    assert complete.quality.status == "READY"
    assert complete.quality.coverage.complete
    store = DatasetStore(tmp_path / "complete")
    snapshot_id = uuid4()
    store.publish(snapshot_id, complete, source="fixture")
    manifest, _ = store.read(snapshot_id)
    assert manifest["coverage"]["expected_symbol_days"] == 2
    assert manifest["coverage"]["missing_counts"]["daily"] == 0
    assert manifest["coverage"]["not_applicable_counts"]["factor_warmup"] == 0
    tables["daily"] = tables["daily"].iloc[:1]
    missing = DatasetBuilder().build(
        as_of=days[-1], tables=tables, verified_trading_days=days,
    )
    assert missing.quality.status == "INSUFFICIENT_EVIDENCE"
    assert "coverage_audit: daily missing 1 symbol/day" in missing.quality.issues
    with pytest.raises(DatasetIntegrityError):
        DatasetStore(tmp_path / "missing").publish(uuid4(), missing, source="fixture")


def test_verified_listing_warmup_is_recorded_without_fabricating_factors():
    tables = _tables()
    tables["instrument"].loc[0, "list_date"] = date(2020, 1, 2)
    tables["factor"] = tables["factor"].iloc[:0]
    tables["trade_status"]["market_board"] = "MAIN"
    tables["trade_status"]["source"] = "tushare"
    dataset = DatasetBuilder().build(
        as_of=date(2020, 1, 3), tables=tables,
        verified_trading_days=(date(2020, 1, 2), date(2020, 1, 3)),
    )
    assert dataset.quality.status == "READY"
    assert dataset.quality.coverage.not_applicable_counts["factor_warmup"] == 2
    assert dataset.quality.coverage.missing_counts["factor"] == 0
    assert not dataset.quality.certifiable


def test_verified_stock_snapshot_reports_missing_hs300_session():
    tables = _tables()
    tables["factor"]["ma_qfq_20"] = 10.
    tables["factor"]["ma_qfq_60"] = 10.
    tables["factor"]["atr_qfq"] = 1.
    tables["trade_status"]["market_board"] = "MAIN"
    tables["trade_status"]["source"] = "tushare"
    tables["benchmark_daily"] = pd.DataFrame([{
        "ts_code": "000300.SH", "trade_date": date(2020, 1, 2),
        "close": 4000., "source": "tushare",
    }])
    dataset = DatasetBuilder().build(
        as_of=date(2020, 1, 3), tables=tables,
        verified_trading_days=(date(2020, 1, 2), date(2020, 1, 3)),
    )
    assert dataset.quality.coverage.missing_counts["benchmark_daily"] == 1
    assert "coverage_audit: benchmark_daily missing 1 symbol/day" in dataset.quality.issues
    assert dataset.quality.status == "INSUFFICIENT_EVIDENCE"


def test_unchecked_optional_tables_cannot_make_snapshot_certifiable():
    tables = _tables()
    tables["corporate_actions"] = pd.DataFrame()
    tables["instrument_rules"] = pd.DataFrame()
    tables["industry_history"] = pd.DataFrame()
    dataset = DatasetBuilder().build(as_of=date(2020, 1, 2), tables=tables)
    assert dataset.quality.status == "READY"
    assert not dataset.quality.certifiable
    assert "corporate_actions: historical adjustment evidence not verified" in dataset.quality.certification_issues
    assert "instrument_rules: historical trading rules not verified" in dataset.quality.certification_issues
    assert "industry_history: point-in-time membership not verified" in dataset.quality.certification_issues


class _Session:
    def __init__(self, *, fail_flush=False):
        self.record = None
        self.fail_flush = fail_flush

    def add(self, record):
        self.record = record

    def flush(self):
        if self.fail_flush:
            self.record = None
            raise RuntimeError("database write failed")

    def get(self, _model, _snapshot_id):
        return self.record


def test_db_failure_never_exposes_ready_snapshot(tmp_path):
    dataset = DatasetBuilder().build(as_of=date(2020, 1, 2), tables=_tables())
    repository = DatasetRepository(DatasetStore(tmp_path))
    session = _Session(fail_flush=True)
    with pytest.raises(RuntimeError, match="database write failed"):
        repository.publish(session, dataset, source="fixture")
    assert session.record is None
    snapshot_id = next(path for path in tmp_path.iterdir() if path.is_dir()).name
    with pytest.raises(DatasetIntegrityError, match="not READY"):
        repository.read(session, UUID(snapshot_id))


def test_db_checksum_detects_manifest_rewrite(tmp_path):
    dataset = DatasetBuilder().build(as_of=date(2020, 1, 2), tables=_tables())
    repository = DatasetRepository(DatasetStore(tmp_path))
    session = _Session()
    record = repository.publish(session, dataset, source="fixture")
    assert repository.read(session, record.id)[0]["snapshot_id"] == str(record.id)
    path = tmp_path / str(record.id) / "manifest.json"
    path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(DatasetIntegrityError):
        repository.read(session, record.id)


def test_snapshot_reference_survives_storage_root_relocation(tmp_path):
    dataset = DatasetBuilder().build(as_of=date(2020, 1, 2), tables=_tables())
    old_root = tmp_path / "old"
    repository = DatasetRepository(DatasetStore(old_root))
    session = _Session()
    record = repository.publish(session, dataset, source="fixture")
    new_root = tmp_path / "restored"
    shutil.move(str(old_root), str(new_root))
    restored = DatasetRepository(DatasetStore(new_root))
    assert restored.read(session, record.id)[0]["snapshot_id"] == str(record.id)
