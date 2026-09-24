"""Typed, targeted ingestion for the six market resources (no backend imports)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from math import ceil, isfinite

import pandas as pd

from AI.dataflows.providers.base_provider import (
    ProviderNetworkAccessDenied,
    raise_if_network_access_denied,
)
from db.instrument.dao import instrument as instrument_dao
from db.instrument.dao import stock_info
from db.instrument.dao.adj_factor import bulk_upsert_factor
from db.instrument.dao.factor_daily import upsert_observed_bfq_factors
from db.instrument.dao.instrument_daily import bulk_upsert_daily
from db.instrument.ingest.frames import _pull_market_frame, fetch_stock_daily_frame
from db.instrument.ingest.guard import (
    ALL_RESOURCES,
    FATAL_INGEST_ERRORS,
    IngestGuard,
    ingestion_scope,
)
from db.instrument.ingest.incremental import (
    INDEX_TARGETS,
    _basic_to_instrument,
    _bootstrap_instruments,
    _provider_source,
    _providers_from_env,
)
from db.instrument.ingest.sector_daily import collect_sector_daily_incremental
from db.instrument.ingest.sectors import collect_sectors
from db.instrument.ingest.stock_factors import (
    _collect_stock_quant_day_unlocked,
    collect_stock_status_day,
)

logger = logging.getLogger(__name__)
KLINE_FACTOR_COLUMNS = (
    "ma_bfq_5", "ma_bfq_10", "ma_bfq_20", "ma_bfq_60",
    "boll_mid_bfq", "boll_upper_bfq", "boll_lower_bfq",
    "macd_dif_bfq", "macd_dea_bfq", "macd_bfq",
)
QUANT_RESOURCE = "CN_STOCK_QUANT_INPUTS"
CHANGE_NOTIFICATION_RESOURCES = {
    "CN_STOCK_DAILY": ("CN_STOCK_DAILY", QUANT_RESOURCE),
    QUANT_RESOURCE: (QUANT_RESOURCE, "CN_STOCK_DAILY"),
}


def _day(value) -> str:
    if not isinstance(value, (str, date)) or pd.isna(value):
        raise ValueError("refresh dates must be valid calendar dates")
    stamp = pd.Timestamp(value)
    if pd.isna(stamp):
        raise ValueError("refresh dates must be valid calendar dates")
    return stamp.date().isoformat()


@dataclass(frozen=True)
class RefreshSpec:
    resource: str
    target_trade_date: str | date
    codes: tuple[str, ...]
    dates: tuple[str | date, ...] = ()
    missing_units: tuple[tuple[str, str | date], ...] = ()
    component_missing_units: tuple[dict, ...] = ()
    component_thresholds: dict[str, float] = field(default_factory=dict)
    operation_units: tuple[dict, ...] = ()

    def __post_init__(self):
        if self.resource not in ALL_RESOURCES:
            raise ValueError("unsupported market refresh resource")
        codes = tuple(dict.fromkeys(self.codes))
        target = _day(self.target_trade_date)
        dates = tuple(sorted({_day(d) for d in self.dates} or {target}))
        if not codes or not all(isinstance(c, str) and c.strip() for c in codes):
            raise ValueError("refresh requires an explicit nonempty code set")
        if any(d > target for d in dates):
            raise ValueError("refresh date exceeds target trade date")
        units = tuple(dict.fromkeys((c, _day(d)) for c, d in self.missing_units))
        if any(c not in codes or d not in dates for c, d in units):
            raise ValueError("missing units must belong to the frozen code/date target")
        component_units = []
        operations = []
        thresholds = dict(self.component_thresholds)
        if self.resource == QUANT_RESOURCE:
            for unit in self.component_missing_units:
                component, code, day = unit.get("component"), unit.get("code"), _day(unit.get("trade_date"))
                if component not in {"daily", "qfq", "adj_factor", "trade_status"}:
                    raise ValueError("unknown quant input component")
                if code not in codes or day != target:
                    raise ValueError("component gaps must belong to the frozen quant target")
                component_units.append({**unit, "trade_date": day})
            for unit in self.operation_units:
                operation, code, day = unit.get("operation"), unit.get("code"), _day(unit.get("trade_date"))
                if operation not in {"daily", "adj_factor", "qfq_status"} or day != target:
                    raise ValueError("invalid quant refresh operation")
                if operation == "daily":
                    if code not in codes:
                        raise ValueError("daily operation must use a frozen stock code")
                elif code is not None:
                    raise ValueError("component-group operation cannot be code-scoped")
                operations.append({**unit, "trade_date": day})
            allowed_thresholds = {"daily", "qfq", "adj_factor", "trade_status"}
            if set(thresholds) - allowed_thresholds:
                raise ValueError("unknown quant component threshold")
            if thresholds and set(thresholds) != allowed_thresholds:
                raise ValueError("quant target must freeze all component thresholds")
            if any(not isinstance(value, (int, float)) or not 0 < value <= 1
                   for value in thresholds.values()):
                raise ValueError("quant component thresholds must be in (0, 1]")
            operation_keys = [(unit["operation"], unit.get("code"), unit["trade_date"])
                              for unit in operations]
            if len(operation_keys) != len(set(operation_keys)):
                raise ValueError("quant operation units must be unique")
        elif self.component_missing_units or self.operation_units or thresholds:
            raise ValueError("quant operation metadata is only valid for quant input refresh")
        if "INDEX" in self.resource:
            if any(c not in INDEX_TARGETS for c in codes):
                raise ValueError("unknown index target")
            market = self.resource.split("_", 1)[0]
            expected = {c for c in INDEX_TARGETS if (
                (market == "CN" and c.endswith((".SH", ".SZ", ".BJ", ".CSI"))) or
                (market == "US" and c in (".INX", ".DJI", ".IXIC")) or
                (market == "KR" and c == "KS11"))}
            if not set(codes) <= expected:
                raise ValueError("index target does not belong to resource market")
        object.__setattr__(self, "codes", codes)
        object.__setattr__(self, "target_trade_date", target)
        object.__setattr__(self, "dates", dates)
        object.__setattr__(self, "missing_units", units)
        object.__setattr__(self, "component_missing_units", tuple(component_units))
        object.__setattr__(self, "component_thresholds", thresholds)
        object.__setattr__(self, "operation_units", tuple(operations))

    @property
    def units(self):
        if self.resource == QUANT_RESOURCE:
            return self.operation_units
        return self.missing_units or tuple((c, d) for c in self.codes for d in self.dates)


@dataclass(frozen=True)
class RefreshProgress:
    resource: str
    code: str
    trade_date: str
    processed: int
    total: int
    completed: int
    error_code: str | None = None
    operation: str | None = None
    component: str | None = None


ProgressCallback = Callable[[RefreshProgress], None]


def _quant_operation_key(unit):
    return (unit["operation"], unit.get("code") or "", _day(unit["trade_date"]))


def _quant_metrics(conn, spec):
    from db.instrument.dao.quant_inputs import read_stock_quant_facts
    from db.instrument.ingest.stock_factors import MIN_GLOBAL_COVERAGE, REQUIRED_QFQ

    facts = read_stock_quant_facts(conn, list(spec.codes), spec.target_trade_date, REQUIRED_QFQ)
    expected = len(spec.codes)
    thresholds = spec.component_thresholds or {
        "daily": 1.0, "qfq": MIN_GLOBAL_COVERAGE,
        "adj_factor": MIN_GLOBAL_COVERAGE, "trade_status": MIN_GLOBAL_COVERAGE,
    }
    ready = {
        "daily": len(facts["daily_valid"] | facts["suspended"]) >= ceil(expected * thresholds["daily"]),
        "qfq": len(facts["qfq_valid"]) >= ceil(expected * thresholds["qfq"]),
        "adj_factor": len(facts["adj_valid"]) >= ceil(expected * thresholds["adj_factor"]),
        "trade_status": len(facts["status_valid"]) >= ceil(expected * thresholds["trade_status"]),
    }
    return facts, ready


def _missing_units(conn, spec: RefreshSpec, *, forced_attempted=()) -> set:
    """Recheck actual facts under the lock; completed units are never fetched again."""
    if spec.resource == QUANT_RESOURCE:
        facts, ready = _quant_metrics(conn, spec)
        attempted = set(forced_attempted)
        missing = set()
        for unit in spec.operation_units:
            key = _quant_operation_key(unit)
            operation, code, _ = key
            if operation == "daily":
                done = code in facts["daily_valid"] or code in facts["suspended"]
            elif operation == "adj_factor":
                done = ready["adj_factor"]
            else:
                done = ready["qfq"] and ready["trade_status"]
                if unit.get("force_refresh") and key not in attempted:
                    done = False
            if not done:
                missing.add(key)
        return missing
    factors = spec.resource == "CN_INDEX_FACTORS"
    sectors = spec.resource == "CN_SECTOR_DAILY"
    table = "market.factor_daily" if factors else (
        "market.sector_daily" if sectors else "market.instrument_daily")
    key = "sector_code" if sectors else "ts_code"
    required = list(KLINE_FACTOR_COLUMNS) if factors else (
        ["open", "high", "low", "close", "pct_chg"] if spec.resource == "CN_STOCK_DAILY" else
        ["open", "high", "low", "close"] + (["pct_chg"] if sectors else []))
    valid = " AND ".join(f"{c} IS NOT NULL AND {c} <> 'NaN'::float8 "
                         f"AND {c} NOT IN ('Infinity'::float8, '-Infinity'::float8)"
                         for c in required)
    where = " AND source = 'dc'" if sectors else ""
    rows = conn.execute(
        f"SELECT {key}, trade_date FROM {table} WHERE {key} = ANY(%s) "
        f"AND trade_date = ANY(%s::date[]) AND {valid}{where}",
        (list(spec.codes), list(spec.dates)),
    ).fetchall()
    complete = {(str(c), _day(d)) for c, d in rows}
    if spec.resource == "CN_STOCK_DAILY":
        rows = conn.execute(
            "SELECT ts_code, trade_date FROM market.trade_status_daily "
            "WHERE ts_code = ANY(%s) AND trade_date = ANY(%s::date[]) "
            "AND is_suspended = TRUE AND source = 'tushare'",
            (list(spec.codes), list(spec.dates)),
        ).fetchall()
        complete.update((str(c), _day(d)) for c, d in rows)
    return set(spec.units) - complete


def _valid_index_frame(frame, code, dates, required):
    if frame is None or frame.empty:
        raise ValueError("UPSTREAM_NOT_READY")
    if frame.attrs.get("missing_chunks"):
        raise ValueError("UPSTREAM_INCOMPLETE")
    if any(c not in frame.columns for c in ("trade_date", *required)):
        raise ValueError("UPSTREAM_COLUMNS_MISSING")
    out = frame.copy()
    if "ts_code" in out.columns and not out["ts_code"].eq(code).all():
        raise ValueError("UPSTREAM_CODE_MISMATCH")
    days = pd.to_datetime(out["trade_date"].astype(str), errors="coerce")
    if days.isna().any() or days.duplicated().any():
        raise ValueError("UPSTREAM_INVALID_KEYS")
    out["trade_date"] = days.dt.strftime("%Y-%m-%d")
    out = out[out["trade_date"].isin(dates)].copy()
    for col in required:
        out[col] = pd.to_numeric(out[col], errors="coerce").replace([float("inf"), -float("inf")], None)
    out = out.dropna(subset=required)
    if out.empty:
        raise ValueError("UPSTREAM_NOT_READY")
    out["ts_code"] = code
    out["updated_at"] = pd.Timestamp.now(tz="UTC")
    return out


def _collect_index_bars_unlocked(conn, provider, fallback, code, dates):
    start, end = min(dates).replace("-", ""), max(dates).replace("-", "")
    last_error = None
    for source in (provider, fallback):
        if source is None:
            continue
        try:
            source_frame = source.get_index_data_df(code, start, end)
            raise_if_network_access_denied(source)
            frame = _valid_index_frame(source_frame,
                                       code, dates, ["open", "high", "low", "close"])
            frame["source"] = _provider_source(source)
            rows = bulk_upsert_daily(conn, frame, update=True)
            conn.commit()
            return rows
        except FATAL_INGEST_ERRORS:
            raise
        except ProviderNetworkAccessDenied:
            conn.rollback()
            raise
        except Exception as exc:  # noqa: BLE001 - isolate recoverable source/write errors
            conn.rollback()
            last_error = exc
    raise ValueError("UPSTREAM_NOT_READY") from last_error


def _collect_index_factors_unlocked(conn, provider, code, dates):
    frame = provider.get_index_factor_df(code, min(dates).replace("-", ""),
                                         max(dates).replace("-", ""))
    raise_if_network_access_denied(provider)
    frame = _valid_index_frame(frame, code, dates, list(KLINE_FACTOR_COLUMNS))
    rows = upsert_observed_bfq_factors(conn, frame)
    conn.commit()
    return rows


def _collect_quant_adj_factor(conn, provider, spec, day):
    from db.instrument.ingest.stock_factors import MIN_GLOBAL_COVERAGE

    frame = _pull_market_frame(provider, day.replace("-", ""), "stock", "factor", list(spec.codes))
    required = {"ts_code", "trade_date", "adj_factor"}
    if frame is None or frame.empty or not required.issubset(frame.columns):
        raise ValueError("ADJ_FACTOR_FRAME_UNAVAILABLE")
    frame = frame.copy()
    if frame["ts_code"].isna().any() or frame.duplicated(["ts_code", "trade_date"]).any():
        raise ValueError("ADJ_FACTOR_INVALID_KEYS")
    days = pd.to_datetime(frame["trade_date"].astype(str), errors="coerce")
    if days.isna().any() or not days.dt.date.eq(pd.Timestamp(day).date()).all():
        raise ValueError("ADJ_FACTOR_DATE_MISMATCH")
    frame["trade_date"] = days.dt.strftime("%Y-%m-%d")
    frame = frame[frame["ts_code"].astype(str).isin(spec.codes)].copy()
    if frame.empty:
        raise ValueError("ADJ_FACTOR_NOT_READY")
    frame["adj_factor"] = pd.to_numeric(frame["adj_factor"], errors="coerce")
    values = frame["adj_factor"].to_numpy(dtype=float)
    if not all(isfinite(value) and value > 0 for value in values):
        raise ValueError("ADJ_FACTOR_INVALID_VALUE")
    expected = len(spec.codes)
    threshold = spec.component_thresholds.get("adj_factor", MIN_GLOBAL_COVERAGE)
    coverage = frame["ts_code"].nunique() / expected
    if coverage < threshold:
        raise ValueError("ADJ_FACTOR_COVERAGE_INCOMPLETE")
    bulk_upsert_factor(conn, frame, update=True)
    conn.commit()


def _collect_quant_refresh(conn, spec, progress, provider):
    missing = _missing_units(conn, spec)
    total = len(spec.units)
    processed = completed = total - len(missing)
    errors = []
    forced_attempted = set()

    def report(unit, error=None, *, attempted_force=False):
        nonlocal processed, completed
        key = _quant_operation_key(unit)
        attempted = set(forced_attempted)
        if attempted_force and error is None:
            attempted.add(key)
            forced_attempted.add(key)
        remaining = _missing_units(conn, spec, forced_attempted=attempted)
        done = key not in remaining and error is None
        processed += 1
        completed += int(done)
        operation = unit["operation"]
        event = RefreshProgress(
            spec.resource, unit.get("code") or "", unit["trade_date"],
            processed, total, completed,
            None if done else (error or "UPSTREAM_NOT_READY"),
            operation=operation, component={"daily": "daily", "adj_factor": "adj_factor",
                                            "qfq_status": "qfq+trade_status"}[operation],
        )
        if event.error_code:
            errors.append({"operation": operation, "code": unit.get("code"),
                           "trade_date": unit["trade_date"], "error_code": event.error_code})
        if progress is not None:
            try:
                progress(event)
            except Exception:
                logger.warning("quant refresh progress failed after unit commit", exc_info=True)

    operation_by_key = {_quant_operation_key(unit): unit for unit in spec.operation_units}
    for operation in ("qfq_status", "adj_factor"):
        pending = [operation_by_key[key] for key in missing
                   if key in operation_by_key and key[0] == operation]
        for unit in pending:
            error = None
            try:
                day = unit["trade_date"]
                if operation == "qfq_status":
                    outcome = _collect_stock_quant_day_unlocked(
                        conn, provider, day.replace("-", ""), list(spec.codes),
                    )
                    if outcome.get("status") != "SUCCESS":
                        raise ValueError("QFQ_STATUS_NOT_READY")
                else:
                    _collect_quant_adj_factor(conn, provider, spec, day)
            except FATAL_INGEST_ERRORS:
                raise
            except ProviderNetworkAccessDenied:
                conn.rollback()
                raise
            except Exception as exc:
                conn.rollback()
                error = "UPSTREAM_NOT_READY"
                logger.warning("quant refresh operation failed: %s %s: %s", operation,
                               unit["trade_date"], exc)
            report(unit, error, attempted_force=bool(unit.get("force_refresh")))

    # The combined qfq/status fetch may have established trusted suspensions;
    # only now decide which daily rows still need an upstream request.
    remaining = _missing_units(conn, spec, forced_attempted=forced_attempted)
    for key in sorted(missing):
        if key[0] == "daily" and key not in remaining:
            report(operation_by_key[key])
    daily_units = [operation_by_key[key] for key in remaining
                   if key in operation_by_key and key[0] == "daily"]
    for day in sorted({unit["trade_date"] for unit in daily_units}):
        day_units = [unit for unit in daily_units if unit["trade_date"] == day]
        codes = sorted(unit["code"] for unit in day_units)
        error = None
        try:
            frame = fetch_stock_daily_frame(provider, day.replace("-", ""), codes)
            frame["source"] = _provider_source(provider)
            frame["updated_at"] = pd.Timestamp.now(tz="UTC")
            bulk_upsert_daily(conn, frame, update=True)
            conn.commit()
        except FATAL_INGEST_ERRORS:
            raise
        except ProviderNetworkAccessDenied:
            conn.rollback()
            raise
        except Exception as exc:
            conn.rollback()
            error = "UPSTREAM_NOT_READY"
            logger.warning("quant daily refresh failed: %s: %s", day, exc)
        for unit in day_units:
            report(unit, error)

    return {"resource": spec.resource, "processed": processed, "total": total,
            "completed": completed, "failed": total - completed, "errors": errors,
            "status": "SUCCESS" if completed == total else "PARTIAL"}


def collect_refresh(conn, spec: RefreshSpec, progress: ProgressCallback | None = None,
                    guard: IngestGuard | None = None, *, provider=None,
                    fallback_provider=None) -> dict:
    with ingestion_scope(conn, guard) as active:
        if not _missing_units(active.connection, spec):
            total = len(spec.units)
            return {"resource": spec.resource, "processed": total, "total": total,
                    "completed": total, "failed": 0, "errors": [], "status": "SUCCESS"}
        if provider is None:
            if spec.resource == QUANT_RESOURCE:
                from AI.dataflows.providers.cn.tushare import TushareProvider
                provider = TushareProvider()
            else:
                factory, fallback_factory = _providers_from_env()
                provider = factory()
                if fallback_provider is None and spec.resource.endswith("INDEX_BARS"):
                    fallback_provider = fallback_factory()
        previous = active.resources
        active.resources = CHANGE_NOTIFICATION_RESOURCES.get(spec.resource, (spec.resource,))
        try:
            result = _collect_refresh_unlocked(active.connection, spec, progress,
                active.provider(provider), active.provider(fallback_provider))
            active.assert_alive()
            return result
        finally:
            active.resources = previous


def _collect_refresh_unlocked(conn, spec, progress, provider, fallback):
    if spec.resource == QUANT_RESOURCE:
        return _collect_quant_refresh(conn, spec, progress, provider)
    missing = _missing_units(conn, spec)
    total = len(spec.units)
    processed = completed = total - len(missing)
    errors = []
    if missing and "INDEX" in spec.resource:
        _bootstrap_instruments(conn, spec.codes)
        conn.commit()

    def report(units, error=None):
        nonlocal processed, completed
        remaining = _missing_units(conn, spec)
        for code, day in sorted(units):
            processed += 1
            done = (code, day) not in remaining
            completed += int(done)
            event = RefreshProgress(spec.resource, code, day, processed, total,
                                    completed, None if done else (error or "UPSTREAM_NOT_READY"))
            if event.error_code:
                errors.append({"code": code, "trade_date": day, "error_code": event.error_code})
            if progress is not None:
                try:
                    progress(event)
                except Exception:
                    logger.warning("market progress failed after unit commit", exc_info=True)

    if spec.resource == "CN_STOCK_DAILY":
        for day in sorted({d for _, d in missing}):
            units = {(c, d) for c, d in missing if d == day}
            codes = sorted(c for c, _ in units)
            try:
                frame = fetch_stock_daily_frame(provider, day.replace("-", ""), codes)
                frame["source"] = _provider_source(provider)
                frame["updated_at"] = pd.Timestamp.now(tz="UTC")
                bulk_upsert_daily(conn, frame, update=True)
                conn.commit()
            except FATAL_INGEST_ERRORS:
                raise
            except ProviderNetworkAccessDenied:
                conn.rollback()
                raise
            except Exception:
                conn.rollback()
                logger.warning("stock daily unit failed: %s", day, exc_info=True)
            # Status is independent: a failed price source must not block proof
            # of suspension, nor roll back an already committed price batch.
            try:
                collect_stock_status_day(conn, provider, day, codes)
            except FATAL_INGEST_ERRORS:
                raise
            except ProviderNetworkAccessDenied:
                conn.rollback()
                raise
            except Exception:
                conn.rollback()
                logger.warning("stock status unit failed: %s", day, exc_info=True)
            report(units)
    else:
        consecutive_failures = 0
        for code in spec.codes:
            units = {(c, d) for c, d in missing if c == code}
            if not units:
                continue
            error = None
            try:
                dates = sorted(d for _, d in units)
                if spec.resource == "CN_SECTOR_DAILY":
                    if consecutive_failures >= 5:
                        raise ValueError("UPSTREAM_CIRCUIT_OPEN")
                    underlying = getattr(provider, "_provider", provider)
                    fallback = (provider.get_sector_daily_fallback_df
                                if callable(getattr(type(underlying), "get_sector_daily_fallback_df", None))
                                else None)
                    outcome = collect_sector_daily_incremental(
                        conn, provider, codes=[code], end_date=spec.target_trade_date,
                        required_dates=dates, fallback_fetch=fallback)
                    if outcome["failed"]:
                        raise ValueError("UPSTREAM_NOT_READY")
                elif spec.resource == "CN_INDEX_FACTORS":
                    _collect_index_factors_unlocked(conn, provider, code, dates)
                else:
                    _collect_index_bars_unlocked(conn, provider, fallback, code, dates)
                consecutive_failures = 0
            except FATAL_INGEST_ERRORS:
                raise
            except ProviderNetworkAccessDenied:
                conn.rollback()
                raise
            except Exception as exc:  # noqa: BLE001 - unit failures preserve earlier commits
                conn.rollback()
                error = "UPSTREAM_NOT_READY"
                consecutive_failures += 1
                logger.warning("refresh unit failed: %s %s: %s", spec.resource, code, exc)
            report(units, error)
    return {"resource": spec.resource, "processed": processed, "total": total,
            "completed": completed, "failed": total - completed, "errors": errors,
            "status": "SUCCESS" if completed == total else "PARTIAL"}


def initialize_catalog(conn, provider=None, *, guard=None, refresh_sectors=True) -> dict:
    """Explicit cold-start maintenance for stock metadata and optionally the dc catalog.

    ``refresh_sectors=False`` is the narrow repair path when stock lifecycle
    metadata is incomplete but the existing dc board/member dictionary is
    already usable; it avoids refetching every board's member list.
    """
    with ingestion_scope(conn, guard) as active:
        if provider is None:
            factory, _ = _providers_from_env()
            provider = factory()
        provider = active.provider(provider)
        previous = active.resources
        active.resources = ("CN_STOCK_DAILY", "CN_SECTOR_DAILY") if refresh_sectors else ("CN_STOCK_DAILY",)
        try:
            frame = provider.get_stock_basic_df()
            raise_if_network_access_denied(provider)
            required = {"ts_code", "name", "list_status", "list_date"}
            if frame is None or frame.empty or not required.issubset(frame.columns):
                raise ValueError("stock catalog unavailable")
            instrument_dao.upsert_instrument(active.connection, _basic_to_instrument(frame, "stock"))
            stock_info.upsert_stock_info(active.connection, frame)
            active.commit("CN_STOCK_DAILY")
            sectors = collect_sectors(active.connection, provider, sources=("dc",)) if refresh_sectors else {}
            return {"stocks": len(frame), "sectors": sectors}
        finally:
            active.resources = previous
