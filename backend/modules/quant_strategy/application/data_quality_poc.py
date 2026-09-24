"""全市场量化数据质量 POC（只读本地 market 库，不调用远端供应商）。"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date

from AI.strategy_sandbox.runner import run_strategy
from backend.modules.analysis.infrastructure.quant_execution_market_data import (
    AllMarketUniverseBuilder,
    MarketContextBatchLoader,
)
from backend.modules.quant_strategy.application.data_readiness import DataReadinessGate
from backend.modules.quant_strategy.domain.templates import TEMPLATES, validate_template_context

FIRST_WAVE = {
    "ma_trend_cross_v1", "trend_pullback_v1", "volume_surge_confirm_v1",
}


class QuantDataQualityPoc:
    def __init__(self, *, batch_size: int = 200, sample_limit: int = 20, runner_sample_size: int = 3):
        self.batch_size = batch_size
        self.sample_limit = sample_limit
        self.runner_sample_size = runner_sample_size

    def run(self, conn, requested_trade_date: date) -> dict:
        readiness = DataReadinessGate.resolve(conn, requested_trade_date)
        as_of = readiness.market_as_of_trade_date
        universe = AllMarketUniverseBuilder.list_active_cn_stocks(conn)
        history = self._scan(conn, universe, as_of, lookback=250, template_ids=())

        by_window: dict[int, list[str]] = defaultdict(list)
        for template_id, definition in TEMPLATES.items():
            by_window[definition.required_bars].append(template_id)
        templates: dict[str, dict] = {}
        for lookback, template_ids in sorted(by_window.items()):
            templates.update(self._scan(
                conn, universe, as_of, lookback=lookback, template_ids=tuple(template_ids),
            )["templates"])

        return {
            "requested_trade_date": readiness.requested_trade_date.isoformat(),
            "market_as_of_trade_date": as_of.isoformat(),
            "resource_watermarks": {
                "daily": readiness.daily_trade_date.isoformat(),
                "factor": readiness.factor_trade_date.isoformat(),
                "adj_factor": readiness.adj_factor_trade_date.isoformat(),
                "trade_status": readiness.trade_status_trade_date.isoformat(),
            },
            "universe_total": len(universe),
            "history_250": history["quality"],
            "templates": templates,
            "operational": {
                "sql_window_capped_per_symbol": True,
                "batch_size": self.batch_size,
                "output_sample_limit_per_status": self.sample_limit,
                "remote_provider_calls": 0,
                "rate_limit_events": 0,
                "retry_events": 0,
                "note": "本 POC 只读本地数据库；供应商限流/重试由采集任务单独审计。",
            },
        }

    def _scan(self, conn, universe, as_of, *, lookback: int, template_ids: tuple[str, ...]) -> dict:
        base_counts: Counter[str] = Counter()
        base_samples: dict[str, list[str]] = defaultdict(list)
        template_counts = {template_id: Counter() for template_id in template_ids}
        template_samples = {template_id: defaultdict(list) for template_id in template_ids}
        runner = {
            template_id: {"attempted": 0, "ok": 0, "errors": Counter()}
            for template_id in template_ids if template_id in FIRST_WAVE
        }
        loader = MarketContextBatchLoader(lookback=lookback)
        for offset in range(0, len(universe), self.batch_size):
            batch = universe[offset : offset + self.batch_size]
            for item in loader.load_batch(conn, batch, as_of):
                status = item["status"]
                base_counts[status] += 1
                self._sample(base_samples[status], item["ts_code"])
                for template_id in template_ids:
                    final_status = status
                    if status == "OK":
                        final_status = validate_template_context(
                            TEMPLATES[template_id], item["context"]
                        ) or "OK"
                    template_counts[template_id][final_status] += 1
                    self._sample(template_samples[template_id][final_status], item["ts_code"])
                    sample = runner.get(template_id)
                    if final_status == "OK" and sample is not None and sample["attempted"] < self.runner_sample_size:
                        sample["attempted"] += 1
                        result = run_strategy(
                            TEMPLATES[template_id].render()[0], item["context"], timeout=2,
                        )
                        if result.ok:
                            sample["ok"] += 1
                        else:
                            sample["errors"][result.error_code or "UNKNOWN"] += 1
        total = len(universe)
        templates = {}
        for template_id in template_ids:
            eligible = template_counts[template_id]["OK"]
            sample = runner.get(template_id)
            templates[template_id] = {
                "required_bars": lookback,
                "eligible": eligible,
                "coverage": eligible / total if total else 0,
                "promotion_eligible": bool(total and eligible == total and (
                    sample is None or sample["attempted"] == sample["ok"]
                )),
                "status_counts": dict(sorted(template_counts[template_id].items())),
                "samples": dict(template_samples[template_id]),
                "sample_truncated_counts": self._truncated_counts(
                    template_counts[template_id], template_samples[template_id]
                ),
                "runner_sample": None if sample is None else {
                    "attempted": sample["attempted"], "ok": sample["ok"],
                    "errors": dict(sample["errors"]),
                },
            }
        eligible = base_counts["OK"]
        return {
            "quality": {
                "required_bars": lookback,
                "eligible": eligible,
                "coverage": eligible / total if total else 0,
                "status_counts": dict(sorted(base_counts.items())),
                "samples": dict(base_samples),
                "sample_truncated_counts": self._truncated_counts(base_counts, base_samples),
            },
            "templates": templates,
        }

    def _sample(self, values: list[str], ts_code: str) -> None:
        if len(values) < self.sample_limit:
            values.append(ts_code)

    @staticmethod
    def _truncated_counts(counts: Counter[str], samples: dict[str, list[str]]) -> dict[str, int]:
        return {
            status: count - len(samples.get(status, ()))
            for status, count in sorted(counts.items())
            if count > len(samples.get(status, ()))
        }
