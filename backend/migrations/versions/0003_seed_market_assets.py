"""seed market assets catalog

首期资产目录（§5.1 P-1 已确认）：CN 7 / US 3 / KR 2。
- CN：Tushare/AKShare 双源已有日线能力 → AVAILABLE。
- US/KR：AKShare 为 ingestion candidate，仅实测验收通过的资产才能 AVAILABLE
  → 首期 UNAVAILABLE（不伪造 bars）。
display_order：产品固定组序 US → KR → CN 由前端渲染；组内顺序按本迁移。

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-06

"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

ASSETS = [
    # (market, symbol, name, currency, market_timezone, display_order, enabled, intervals, availability, source)
    ("US", ".INX", "标普500", "USD", "America/New_York", 10, True, ["1d"], "UNAVAILABLE", "akshare"),
    ("US", ".DJI", "道琼斯工业指数", "USD", "America/New_York", 20, True, ["1d"], "UNAVAILABLE", "akshare"),
    ("US", ".IXIC", "纳斯达克综合指数", "USD", "America/New_York", 30, True, ["1d"], "UNAVAILABLE", "akshare"),
    ("KR", "KOSPI", "韩国综合指数", "KRW", "Asia/Seoul", 10, True, ["1d"], "UNAVAILABLE", "akshare"),
    ("KR", "KOSDAQ", "韩国科斯达克指数", "KRW", "Asia/Seoul", 20, True, ["1d"], "UNAVAILABLE", "akshare"),
    ("CN", "000001.SH", "上证综指", "CNY", "Asia/Shanghai", 10, True, ["1d"], "AVAILABLE", "tushare"),
    ("CN", "399001.SZ", "深证成指", "CNY", "Asia/Shanghai", 20, True, ["1d"], "AVAILABLE", "tushare"),
    ("CN", "399006.SZ", "创业板指", "CNY", "Asia/Shanghai", 30, True, ["1d"], "AVAILABLE", "tushare"),
    ("CN", "000688.SH", "科创50", "CNY", "Asia/Shanghai", 40, True, ["1d"], "AVAILABLE", "tushare"),
    ("CN", "000016.SH", "上证50", "CNY", "Asia/Shanghai", 50, True, ["1d"], "AVAILABLE", "tushare"),
    ("CN", "000852.SH", "中证1000", "CNY", "Asia/Shanghai", 60, True, ["1d"], "AVAILABLE", "tushare"),
    ("CN", "000015.SH", "上证红利", "CNY", "Asia/Shanghai", 70, True, ["1d"], "AVAILABLE", "tushare"),
]


def upgrade() -> None:
    conn = op.get_bind()
    for market, symbol, name, currency, timezone, order, enabled, intervals, availability, source in ASSETS:
        conn.execute(
            text(
                """
                INSERT INTO market_assets
                    (id, market, symbol, name, currency, market_timezone, display_order, enabled,
                     supported_intervals, availability_status, data_source)
                VALUES
                    (gen_random_uuid(), :market, :symbol, :name, :currency, :timezone, :order, :enabled,
                     CAST(:intervals AS jsonb), :availability, :source)
                ON CONFLICT (market, symbol) DO NOTHING
                """
            ),
            {
                "market": market,
                "symbol": symbol,
                "name": name,
                "currency": currency,
                "timezone": timezone,
                "order": order,
                "enabled": enabled,
                "intervals": str(intervals).replace("'", '"'),
                "availability": availability,
                "source": source,
            },
        )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("DELETE FROM market_assets"))
