"""Read-only refresh coverage over existing market tables.

No Redis/source/broker calls and no table creation. A caller may supply the actual
ingest connection for verification under its advisory lock. Frozen job units are
rechecked independently of later calendar/catalog changes.
"""

from __future__ import annotations

import hashlib
import json
from bisect import bisect_left, bisect_right
from contextlib import nullcontext
from datetime import date
from math import ceil

from backend.modules.market_data.application.refresh_policy import (
    KLINE_FACTOR_COLUMNS,
    RefreshTarget,
    Resource,
    classify_freshness,
)


def _day(value):
    return date.fromisoformat(value) if isinstance(value, str) else value


def _iso(value):
    return value.isoformat() if value is not None else None


def _valid_sql(columns: list[str], alias: str = "d") -> str:
    # PostgreSQL NaN compares equal to NaN; plain IS NOT NULL is insufficient.
    return " AND ".join(
        f"{alias}.{column} IS NOT NULL AND {alias}.{column} NOT IN "
        "('NaN'::float8, 'Infinity'::float8, '-Infinity'::float8)"
        for column in columns
    )


def _shape(resource: Resource):
    if resource == Resource.CN_SECTOR_DAILY:
        return "market.sector_daily", "sector_code", ["open", "high", "low", "close", "pct_chg"], " AND d.source = 'dc'"
    if resource == Resource.CN_INDEX_FACTORS:
        return "market.factor_daily", "ts_code", KLINE_FACTOR_COLUMNS, ""
    columns = ["open", "high", "low", "close"]
    if resource == Resource.CN_STOCK_DAILY:
        columns += ["pct_chg"]
    return "market.instrument_daily", "ts_code", columns, ""


def _lifecycle(metadata, day: date) -> str:
    """Unknown metadata never becomes an implicit exemption."""
    if metadata is None:
        return "LIFECYCLE_UNKNOWN"
    list_status, list_date, delist_date = metadata[:3]
    # stock_basic's G (approved but not traded) and UN (not listed) are
    # explicit evidence that no quote should exist before listing. UN is stored
    # as U because market.instrument.list_status is CHAR(1). Once list_date is
    # reached, include the code even if the status snapshot has not rolled to L.
    if list_status in {"G", "U"} and list_date is None:
        return "EXCLUDED"
    if (list_date is not None and day < list_date) or (delist_date is not None and day >= delist_date):
        return "EXCLUDED"
    if list_date is None or list_status not in {"L", "P", "D", "G", "U"} or (list_status == "D" and delist_date is None):
        return "LIFECYCLE_UNKNOWN"
    return "ACTIVE"


def _is_cn_b_share_code(ts_code: str) -> bool:
    """B shares (20xxxx.SZ / 900xxx.SH) are outside this A-share daily feed."""
    code, separator, market = ts_code.partition(".")
    return (
        separator == "."
        and len(code) == 6
        and code.isdigit()
        and ((market == "SZ" and code.startswith("20")) or (market == "SH" and code.startswith("900")))
    )


class RefreshRepository:
    def __init__(self, connection_factory=None) -> None:
        if connection_factory is None:
            from db.instrument.db import get_connection
            connection_factory = get_connection
        self.connection_factory = connection_factory

    @staticmethod
    def _catalog(conn, resource: Resource):
        if resource == Resource.CN_STOCK_QUANT_INPUTS:
            from db.instrument.dao.instrument import list_active_cn_stocks
            codes = list_active_cn_stocks(conn)
            return {code: None for code in codes}, codes
        if resource == Resource.CN_SECTOR_DAILY:
            rows = conn.execute("SELECT sector_code, updated_at FROM market.sector WHERE source = 'dc' ORDER BY sector_code").fetchall()
            return {row[0]: None for row in rows}, rows
        if resource == Resource.CN_STOCK_DAILY:
            rows = conn.execute(
                "SELECT ts_code, list_status, list_date, delist_date, updated_at "
                "FROM market.instrument WHERE instrument_type = 'stock' ORDER BY ts_code"
            ).fetchall()
            # DC membership is a classification feed, not a listing registry.
            # It can publish IPO constituents before stock_basic has a listing
            # record (and before a daily quote can exist). Only the stock master
            # supplies the daily universe and lifecycle facts.
            rows = [row for row in rows if not _is_cn_b_share_code(row[0])]
            return {row[0]: row[1:] for row in rows}, rows
        # The fixed index catalog survives a completely empty instrument table.
        from db.instrument.ingest.incremental import INDEX_TARGETS, _is_cn_index_code
        if resource in {Resource.CN_INDEX_BARS, Resource.CN_INDEX_FACTORS}:
            codes = [code for code in INDEX_TARGETS if _is_cn_index_code(code)]
        elif resource == Resource.US_INDEX_BARS:
            codes = [code for code in (".INX", ".DJI", ".IXIC") if code in INDEX_TARGETS]
        else:
            codes = [code for code in ("KS11",) if code in INDEX_TARGETS]
        return {code: None for code in sorted(codes)}, sorted(codes)

    def snapshot(self, resource: Resource | str, target: RefreshTarget, *, conn=None) -> dict:
        resource = Resource(resource)
        if resource != target.resource:
            raise ValueError("resource must match target.resource")
        with nullcontext(conn) if conn is not None else self.connection_factory() as connection:
            if resource == Resource.CN_STOCK_QUANT_INPUTS:
                return self._quant_snapshot(connection, target)
            catalog, catalog_stamp = self._catalog(connection, resource)
            result = self._coverage(connection, resource, catalog, target.window_dates, target.history_dates)
        dates = target.window_dates
        counts = result["days"].get(target.expected_trade_date, self._empty_counts())
        window = {name: sum(row[name] for row in result["days"].values()) for name in counts}
        catalog_available = bool(catalog)
        if target.expected_trade_date is None:
            block_reason = "CALENDAR_UNAVAILABLE"
        elif not catalog_available:
            block_reason = "CATALOG_UNAVAILABLE"
        elif resource == Resource.CN_STOCK_DAILY and any(
            unit["reason"] == "LIFECYCLE_UNKNOWN" for unit in result["missing_units"]
        ):
            # Daily quotes cannot repair missing instrument lifecycle facts.
            block_reason = "CATALOG_INCOMPLETE"
        else:
            block_reason = None
        spec = {
            "resource": resource.value,
            "target_trade_date": _iso(target.expected_trade_date),
            "codes": list(catalog), "dates": [_iso(day) for day in dates],
            "units": [{"code": unit["code"], "trade_date": unit["trade_date"]} for unit in result["missing_units"]],
        }
        digest_data = [catalog_stamp, result["summary"], result["status_stamp"], result["days"], result["missing_units"]]
        # JSON object keys must be text, unlike the internal date-keyed day map.
        digest_data[3] = [(_iso(day), counts) for day, counts in result["days"].items()]
        digest = hashlib.sha256(json.dumps(digest_data, default=str, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]
        complete = [day for day, counts in result["days"].items() if counts["expected_count"] and not counts["missing_count"]]
        return {
            **target.as_dict(), **counts,
            "latest_observed_date": _iso(result["latest"]),
            "complete_through_date": _iso(max(complete)) if complete else None,
            "freshness": classify_freshness(
                expected_trade_date=target.expected_trade_date, latest_observed_date=result["latest_valid"],
                expected_count=counts["expected_count"], available_count=counts["available_count"],
                exempt_count=counts["exempt_count"], window_missing_count=window["missing_count"],
                catalog_available=catalog_available,
            ),
            "window_coverage": {"from": _iso(dates[0]) if dates else None,
                                "to": _iso(dates[-1]) if dates else None, **window},
            "coverage_digest": digest, "data_version": f"coverage-{digest}",
            "block_reason": block_reason,
            "history_gap": result["history_gap"],
            "warnings": ["HISTORY_GAP"] if result["history_gap"] else [],
            "target_spec": spec, "missing_units": result["missing_units"],
        }

    def verify_spec(self, spec: dict, *, conn=None) -> dict:
        """Recheck only originally missing units; never mutate/replan an in-flight job."""
        resource = Resource(spec["resource"])
        if resource == Resource.CN_STOCK_QUANT_INPUTS:
            with nullcontext(conn) if conn is not None else self.connection_factory() as connection:
                return self._verify_quant_spec(connection, spec)
        units = {(unit["code"], _day(unit["trade_date"])) for unit in spec["units"]}
        codes = {code for code, _ in units}
        dates = tuple(sorted({day for _, day in units}))
        with nullcontext(conn) if conn is not None else self.connection_factory() as connection:
            current_catalog, _ = self._catalog(connection, resource)
            # Removed catalog entries remain required by the frozen job.
            catalog = {code: current_catalog.get(code) for code in sorted(codes)}
            result = self._coverage(connection, resource, catalog, dates, (), units=units)
        counts = {name: sum(row[name] for row in result["days"].values()) for name in self._empty_counts()}
        missing = result["missing_units"]
        completed = len(units) - len(missing)
        return {
            "resource": resource.value, "expected_trade_date": spec["target_trade_date"],
            **counts, "missing_units": missing, "total": len(units), "completed": completed,
            "freshness": "FRESH" if not missing else ("PARTIAL" if completed else "UNAVAILABLE"),
            "latest_observed_date": _iso(result["latest"]),
        }

    @staticmethod
    def _coverage_metric(expected: int, available: int, exempt: int, threshold: float) -> dict:
        missing = max(expected - available - exempt, 0)
        required = ceil(expected * threshold) if expected else 0
        return {
            "expected_count": expected,
            "available_count": available,
            "exempt_count": exempt,
            "missing_count": missing,
            "threshold": threshold,
            "required_count": required,
            "ready": expected > 0 and available + exempt >= required,
        }

    @staticmethod
    def _sha_codes(codes: list[str]) -> str:
        return hashlib.sha256("\n".join(codes).encode("utf-8")).hexdigest()

    def _quant_snapshot(self, conn, target: RefreshTarget) -> dict:
        from db.instrument.ingest.stock_factors import MIN_GLOBAL_COVERAGE, REQUIRED_QFQ

        catalog, catalog_stamp = self._catalog(conn, Resource.CN_STOCK_QUANT_INPUTS)
        codes = sorted(catalog)
        trade_day = target.expected_trade_date
        block_reason = None
        if trade_day is None:
            block_reason = "CALENDAR_UNAVAILABLE"
        elif not codes:
            block_reason = "CATALOG_UNAVAILABLE"
        component_coverage = {}
        component_missing = {"daily": [], "qfq": [], "adj_factor": [], "trade_status": []}
        operation_units = []
        latest = None
        if trade_day is not None and codes:
            facts = self._quant_facts(conn, codes, trade_day, REQUIRED_QFQ)
            expected = len(codes)
            thresholds = {
                "daily": 1.0,
                "qfq": MIN_GLOBAL_COVERAGE,
                "adj_factor": MIN_GLOBAL_COVERAGE,
                "trade_status": MIN_GLOBAL_COVERAGE,
            }
            available = {
                "daily": facts["daily_valid"],
                "qfq": facts["qfq_valid"],
                "adj_factor": facts["adj_valid"],
                "trade_status": facts["status_valid"],
            }
            exemptions = facts["suspended"] - facts["daily_valid"]
            for component in ("daily", "qfq", "adj_factor", "trade_status"):
                component_coverage[component] = self._coverage_metric(
                    expected, len(available[component]),
                    len(exemptions) if component == "daily" else 0,
                    thresholds[component],
                )
                missing_codes = set(codes) - available[component]
                if component == "daily":
                    missing_codes -= exemptions
                for code in sorted(missing_codes):
                    component_missing[component].append({
                        "component": component,
                        "code": code,
                        "trade_date": trade_day.isoformat(),
                        "reason": self._quant_missing_reason(component, code, facts),
                    })
            operation_units.extend(
                {"operation": "daily", "code": item["code"], "trade_date": item["trade_date"]}
                for item in component_missing["daily"]
            )
            if not component_coverage["adj_factor"]["ready"]:
                operation_units.append({
                    "operation": "adj_factor", "trade_date": trade_day.isoformat(),
                })
            if (not component_coverage["qfq"]["ready"]
                    or not component_coverage["trade_status"]["ready"]
                    or bool(component_missing["daily"])):
                qfq_status_unit = {
                    "operation": "qfq_status", "trade_date": trade_day.isoformat(),
                }
                if component_missing["daily"]:
                    qfq_status_unit["force_refresh"] = True
                operation_units.append(qfq_status_unit)
            latest = trade_day if all(metric["ready"] for metric in component_coverage.values()) else None
        fresh = bool(component_coverage) and all(metric["ready"] for metric in component_coverage.values())
        universe_digest = self._sha_codes(codes)
        component_slots = sum(metric["expected_count"] for metric in component_coverage.values())
        component_available = sum(metric["available_count"] for metric in component_coverage.values())
        component_exempt = sum(metric["exempt_count"] for metric in component_coverage.values())
        spec = {
            "resource": Resource.CN_STOCK_QUANT_INPUTS.value,
            "target_trade_date": _iso(trade_day),
            "codes": codes,
            "dates": [_iso(trade_day)] if trade_day else [],
            "universe_digest": universe_digest,
            "component_thresholds": {name: row["threshold"] for name, row in component_coverage.items()},
            "component_coverage": component_coverage,
            "component_missing_units": [unit for items in component_missing.values() for unit in items],
            "units": operation_units,
        }
        coverage_payload = json.dumps(
            {"catalog": catalog_stamp, "metrics": component_coverage,
             "missing": component_missing, "trade_date": _iso(trade_day)},
            default=str, sort_keys=True, separators=(",", ":"),
        )
        digest = hashlib.sha256(coverage_payload.encode("utf-8")).hexdigest()[:24]
        missing_count = sum(metric["missing_count"] for metric in component_coverage.values())
        return {
            **target.as_dict(),
            "coverage_unit": "COMPONENT_CODE_PAIR",
            "expected_count": component_slots,
            "available_count": component_available,
            "exempt_count": component_exempt,
            "missing_count": missing_count,
            "component_coverage": component_coverage,
            "latest_observed_date": _iso(latest),
            "complete_through_date": _iso(latest),
            "freshness": "UNKNOWN" if trade_day is None else (
                "UNAVAILABLE" if not codes else ("FRESH" if fresh else "PARTIAL")
            ),
            "window_coverage": {"from": _iso(trade_day), "to": _iso(trade_day),
                                "expected_count": component_slots, "available_count": component_available,
                                "exempt_count": component_exempt, "missing_count": missing_count},
            "coverage_digest": digest,
            "data_version": f"coverage-{digest}",
            "block_reason": block_reason,
            "history_gap": False,
            "warnings": [],
            "target_spec": spec,
            "missing_units": operation_units,
            "universe_digest": universe_digest,
        }

    @staticmethod
    def _quant_facts(conn, codes, trade_day, qfq_columns):
        from db.instrument.dao.quant_inputs import read_stock_quant_facts
        return read_stock_quant_facts(conn, codes, trade_day, list(qfq_columns))

    @staticmethod
    def _quant_missing_reason(component, code, facts):
        seen_key = {"qfq": "qfq_seen", "adj_factor": "adj_seen",
                    "trade_status": "status_seen"}.get(component)
        if component == "daily":
            return "INVALID_VALUES" if code in facts["daily_seen"] else "MISSING_ROW"
        return "INVALID_VALUES" if seen_key and code in facts[seen_key] else "MISSING_ROW"

    def _verify_quant_spec(self, conn, spec):
        from db.instrument.dao.instrument import list_active_cn_stocks
        from db.instrument.ingest.stock_factors import REQUIRED_QFQ

        codes = list(spec["codes"])
        if not codes:
            return {"resource": spec["resource"], "expected_trade_date": spec["target_trade_date"],
                    "missing_units": list(spec.get("units", [])), "total": len(spec.get("units", [])),
                    "completed": 0, "freshness": "UNAVAILABLE", "universe_digest": spec.get("universe_digest")}
        trade_day = _day(spec["target_trade_date"])
        facts = self._quant_facts(conn, codes, trade_day, REQUIRED_QFQ)
        expected = len(codes)
        threshold = spec.get("component_thresholds") or {
            "daily": 1.0, "qfq": 0.95, "adj_factor": 0.95, "trade_status": 0.95,
        }
        exemptions = facts["suspended"] - facts["daily_valid"]
        metrics = {
            "daily": self._coverage_metric(expected, len(facts["daily_valid"]), len(exemptions), threshold["daily"]),
            "qfq": self._coverage_metric(expected, len(facts["qfq_valid"]), 0, threshold["qfq"]),
            "adj_factor": self._coverage_metric(expected, len(facts["adj_valid"]), 0, threshold["adj_factor"]),
            "trade_status": self._coverage_metric(expected, len(facts["status_valid"]), 0, threshold["trade_status"]),
        }
        fresh = all(metric["ready"] for metric in metrics.values())
        outstanding = []
        for unit in spec.get("units", []):
            operation, code = unit["operation"], unit.get("code")
            if operation == "daily":
                done = code in facts["daily_valid"] or code in exemptions
            elif operation == "adj_factor":
                done = metrics["adj_factor"]["ready"]
            elif operation == "qfq_status":
                done = metrics["qfq"]["ready"] and metrics["trade_status"]["ready"]
            else:
                done = False
            if not done:
                outstanding.append(unit)
        completed = len(spec.get("units", [])) - len(outstanding)
        current_universe = list_active_cn_stocks(conn)
        return {
            "resource": spec["resource"],
            "expected_trade_date": spec["target_trade_date"],
            "component_coverage": metrics,
            "missing_units": outstanding,
            "total": len(spec.get("units", [])),
            "completed": completed,
            "freshness": "FRESH" if fresh else ("PARTIAL" if completed else "UNAVAILABLE"),
            "universe_digest": self._sha_codes(codes),
            "current_universe_digest": self._sha_codes(current_universe),
        }

    @staticmethod
    def _empty_counts():
        return {"expected_count": 0, "available_count": 0, "exempt_count": 0, "missing_count": 0}

    def _coverage(self, conn, resource, catalog, dates, history_dates, *, units=None):
        days = {day: self._empty_counts() for day in dates}
        result = {"days": days, "missing_units": [], "latest": None, "latest_valid": None,
                  "summary": [], "status_stamp": [], "history_gap": False}
        if not catalog:
            return result
        codes = list(catalog)
        table, code_column, columns, source_filter = _shape(resource)
        valid = _valid_sql(columns)
        result["summary"] = conn.execute(
            f"SELECT d.{code_column}, min(d.trade_date), max(d.trade_date), "
            f"max(d.trade_date) FILTER (WHERE {valid}), count(*), max(d.updated_at) "
            f"FROM {table} d WHERE d.{code_column} = ANY(%s){source_filter} "
            f"GROUP BY d.{code_column} ORDER BY d.{code_column}", (codes,),
        ).fetchall()
        if result["summary"]:
            result["latest"] = max(row[2] for row in result["summary"])
            valid_dates = [row[3] for row in result["summary"] if row[3] is not None]
            result["latest_valid"] = max(valid_dates) if valid_dates else None
        observed = {}
        suspended = set()
        if dates:
            rows = conn.execute(
                f"SELECT d.{code_column}, d.trade_date, ({valid}), d.updated_at "
                f"FROM {table} d WHERE d.{code_column} = ANY(%s) AND d.trade_date = ANY(%s){source_filter}",
                (codes, list(dates)),
            ).fetchall()
            observed = {(code, day): bool(is_valid) for code, day, is_valid, _ in rows}
            if resource == Resource.CN_STOCK_DAILY:
                result["status_stamp"] = conn.execute(
                    "SELECT ts_code, trade_date, is_suspended, source, updated_at FROM market.trade_status_daily "
                    "WHERE ts_code = ANY(%s) AND trade_date = ANY(%s) ORDER BY ts_code, trade_date",
                    (codes, list(dates)),
                ).fetchall()
                suspended = {(code, day) for code, day, flag, source, _ in result["status_stamp"]
                             if flag and source == "tushare"}
        for day in dates:
            for code, metadata in catalog.items():
                if units is not None and (code, day) not in units:
                    continue
                # An in-flight Redis job may contain B-share units frozen
                # before the current A-share universe filter was introduced.
                # They are outside this provider's contract and must not keep
                # that legacy job in retry forever or be sent back upstream.
                if resource == Resource.CN_STOCK_DAILY and _is_cn_b_share_code(code):
                    continue
                # Frozen jobs created under the old sector-member union may
                # contain prelisting IPO codes absent from the stock master.
                # Reverification must retire those units as out of scope.
                if resource == Resource.CN_STOCK_DAILY and metadata is None:
                    continue
                life = _lifecycle(metadata, day) if resource == Resource.CN_STOCK_DAILY else "ACTIVE"
                if life == "EXCLUDED":
                    continue
                counts = days[day]
                counts["expected_count"] += 1
                if life == "ACTIVE" and observed.get((code, day)):
                    counts["available_count"] += 1
                elif life == "ACTIVE" and (code, day) in suspended:
                    counts["exempt_count"] += 1
                else:
                    counts["missing_count"] += 1
                    reason = life if life != "ACTIVE" else ("INVALID_VALUES" if (code, day) in observed else "MISSING_ROW")
                    result["missing_units"].append({"code": code, "trade_date": _iso(day), "reason": reason})
        if history_dates:
            result["history_gap"] = self._history_gap(conn, resource, catalog, history_dates, result["summary"])
        return result

    @staticmethod
    def _history_gap(conn, resource, catalog, history_dates, summary):
        """Detect known older holes from each code's first stored row, bounded by calendar support.

        There is deliberately no promise to verify the complete history before
        the calendar's supported range, or before collection ever began.
        """
        if not summary:
            return False
        table, code_column, columns, source_filter = _shape(resource)
        valid = _valid_sql(columns)
        history_query = (
            f"SELECT d.{code_column} AS code, d.trade_date FROM {table} d "
            f"WHERE d.{code_column} = ANY(%s) AND d.trade_date = ANY(%s) AND {valid}{source_filter}"
        )
        params = [list(catalog), list(history_dates)]
        lifecycle_filter = ""
        if resource == Resource.CN_STOCK_DAILY:
            history_query += (
                " UNION SELECT ts_code AS code, trade_date FROM market.trade_status_daily "
                "WHERE ts_code = ANY(%s) AND trade_date = ANY(%s) AND is_suspended AND source = 'tushare'"
            )
            params += [list(catalog), list(history_dates)]
            lifecycle_filter = (
                " JOIN market.instrument i ON i.ts_code = h.code "
                "WHERE i.list_date <= h.trade_date AND (i.delist_date IS NULL OR h.trade_date < i.delist_date)"
            )
        # Suspension facts preceding the first observed quote must not inflate
        # the count and hide a hole inside the history we actually verify.
        params += [[row[0] for row in summary], [row[1] for row in summary]]
        first_seen = " JOIN unnest(%s::text[],%s::date[]) AS first_seen(code,day) ON first_seen.code=h.code AND h.trade_date>=first_seen.day"
        rows = conn.execute(f"SELECT h.code, count(*) FROM ({history_query}) h{first_seen}{lifecycle_filter} GROUP BY h.code", params).fetchall()
        actual = dict(rows)
        for code, first, *_ in summary:
            lower, upper = first, history_dates[-1]
            if resource == Resource.CN_STOCK_DAILY:
                metadata = catalog[code]
                if metadata is None or metadata[1] is None:
                    continue
                lower = max(lower, metadata[1])
                if metadata[2] is not None:
                    # The delisting date itself is outside the live interval.
                    expected = max(0, bisect_left(history_dates, metadata[2]) - bisect_left(history_dates, lower))
                else:
                    expected = max(0, bisect_right(history_dates, upper) - bisect_left(history_dates, lower))
            else:
                expected = max(0, bisect_right(history_dates, upper) - bisect_left(history_dates, lower))
            if actual.get(code, 0) < expected:
                return True
        return False

    def concept_display_date(self, *, conn=None) -> date | None:
        with nullcontext(conn) if conn is not None else self.connection_factory() as connection:
            return connection.execute(
                f"SELECT max(d.trade_date) FROM market.sector_daily d "
                f"WHERE d.source = 'dc' AND {_valid_sql(['open', 'high', 'low', 'close', 'pct_chg'])} "
                "AND EXISTS (SELECT 1 FROM market.sector s WHERE s.source = 'dc' AND s.sector_code = d.sector_code)"
            ).fetchone()[0]
