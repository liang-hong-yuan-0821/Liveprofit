"""Read locally observed market revisions without inventing historical availability."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal


FACT_TABLES = frozenset({"instrument_daily", "adj_factor", "factor_daily", "trade_status_daily"})


@dataclass(frozen=True)
class FactRevisionAsOf:
    status: Literal["UNKNOWN", "PRESENT", "DELETED"]
    payload: dict | None
    observed_at: datetime | None
    revision_id: int | None


def read_fact_as_of(conn, *, fact_table: str, ts_code: str, trade_date: date,
                    cutoff: datetime) -> FactRevisionAsOf:
    """Return the last locally recorded version by cutoff, never an availability certificate.

    The trigger timestamp can precede transaction commit and says nothing about
    when the upstream published the value. Admission and execution cannot use
    PRESENT as evidence that a past decision could have seen the source.
    """
    if (fact_table not in FACT_TABLES or not isinstance(ts_code, str) or not ts_code.strip()
            or type(trade_date) is not date or not isinstance(cutoff, datetime)
            or cutoff.utcoffset() is None):
        raise ValueError("valid fact identity and timezone-aware cutoff required")
    row = conn.execute(
        "SELECT id, operation, observed_at, payload FROM market.fact_revision "
        "WHERE fact_table=%s AND ts_code=%s AND trade_date=%s AND observed_at<=%s "
        "ORDER BY observed_at DESC, id DESC LIMIT 1",
        (fact_table, ts_code, trade_date, cutoff),
    ).fetchone()
    if row is None:
        return FactRevisionAsOf("UNKNOWN", None, None, None)
    revision_id, operation, observed_at, payload = row
    if operation == "DELETE":
        return FactRevisionAsOf("DELETED", None, observed_at, revision_id)
    return FactRevisionAsOf("PRESENT", payload, observed_at, revision_id)
