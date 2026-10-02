"""Shared fixtures/builders for tests.backend.market_data.integration.test_market_flow; no test cases."""

from __future__ import annotations
from datetime import date, datetime, timezone
import pytest
import db.instrument.db as market_db
from backend.modules.market_data.application.errors import (
    IntervalNotSupportedError,
    RangeTooLargeError,
)
from backend.modules.market_data.application.service import MarketDataService
from backend.modules.market_data.infrastructure.calendar_adapter import FakeCalendar


@pytest.fixture
def service(env):
    """market_conn 工厂注入（模块常量直赋值，module 级 fixture 内不得用 function monkeypatch）。"""
    market_db.PG_CONNECTION_STRING = env["psycopg_dsn"]
    from db.instrument.db import get_connection

    return lambda **kw: MarketDataService(market_conn=get_connection, **kw)


def _seed(env, sql: str, params: dict) -> None:
    from sqlalchemy import text

    with env["session_factory"]() as session:
        session.execute(text(sql), params)
        session.commit()
