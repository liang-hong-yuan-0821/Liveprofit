from datetime import date
from uuid import uuid4

import pandas as pd
import pytest

from backend.modules.quant_research.application.dataset_builder import DatasetBuilder
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
             "is_suspended": False} for day in days
        ]),
    }


def test_future_rows_do_not_change_historical_input_and_delisted_stock_remains():
    tables = _tables()
    builder = DatasetBuilder()
    before = builder.build(as_of=date(2020, 1, 2), tables=tables)
    tables["daily"].loc[1, "close"] = 9999.
    after = builder.build(as_of=date(2020, 1, 2), tables=tables)
    assert before.quality.status == "READY"
    pd.testing.assert_frame_equal(before.tables["daily"], after.tables["daily"])
    assert before.tables["instrument"]["ts_code"].tolist() == ["000001.SZ"]
    assert pd.isna(before.tables["instrument"].loc[0, "delist_date"])
    assert "list_status" not in before.tables["instrument"]


def test_delisted_stock_stays_in_post_delisting_research_history():
    tables = _tables()
    tables["instrument"].loc[0, "delist_date"] = date(2020, 1, 3)
    dataset = DatasetBuilder().build(as_of=date(2020, 1, 5), tables=tables)
    assert dataset.quality.status == "READY"
    assert dataset.tables["daily"]["trade_date"].tolist() == [date(2020, 1, 2)]


def test_missing_historical_status_is_insufficient_evidence():
    tables = _tables()
    tables["trade_status"] = tables["trade_status"].iloc[0:0]
    dataset = DatasetBuilder().build(as_of=date(2020, 1, 2), tables=tables)
    assert dataset.quality.status == "INSUFFICIENT_EVIDENCE"
    assert "trade_status: historical coverage empty" in dataset.quality.issues


def test_publication_requires_ready_and_checksum_is_verified(tmp_path):
    tables = _tables()
    dataset = DatasetBuilder().build(as_of=date(2020, 1, 2), tables=tables)
    store = DatasetStore(tmp_path / "research")
    snapshot_id = uuid4()
    path = store.publish(snapshot_id, dataset, source="fixture")
    manifest, read_tables = store.read(snapshot_id)
    assert manifest["source"] == "fixture"
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
