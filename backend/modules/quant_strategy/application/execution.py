"""QuantExecutionService（plan 4.3.1）：运行期唯一业务实现，Worker 直接调用、不经 LangGraph。

流程固定：
  universe = AllMarketUniverseBuilder.list_active_cn_stocks(conn)  # 执行开始时实时枚举
  holdings = snapshot.positions                                     # 提交时冻结的组合持仓
  scan_targets = dedupe(universe + hold_targets)
  for batch in chunks(scan_targets, 200):
      contexts = MarketContextBatchLoader.load_batch(batch, effective_trade_date)
      execute data-complete contexts through BoundedSandboxExecutor(max_workers=8, execution_control)
      persist BUY / holding signal / auditable error rows, then release batch contexts
  所有批次执行完毕后：
      PositionPlanner 从 quant_execution_signals 按 score DESC, ts_code ASC, id ASC
      流式读取当前 attempt 的可行动 signal → 回写建议订单

- AI 风险门控本方案不接线；组合开放风险与熔断由 PositionPlanner 独立执行，
  BUY_REJECTED_RISK_GATE 在枚举/DTO 预留（PositionPlanner 参数）。
- 非持仓正常 HOLD 不落表（只进统计）；错误样本每码至多 100 个按 ts_code 排序。
- 取消/失租：终止全部已登记子进程、丢弃未持久化批次输出并停止后续扫描。
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from AI.strategy_sandbox.runner import run_strategy
from AI.strategy_sandbox.validator import validate_strategy_source
from backend.modules.analysis.application.errors import FatalAnalysisError
from backend.modules.analysis.infrastructure.execution_control import ExecutionControl
from backend.modules.analysis.infrastructure.quant_execution_market_data import (
    AllMarketUniverseBuilder,
    MarketContextBatchLoader,
    benchmark_above_ma120,
)
from backend.modules.investment_workspace.infrastructure.models import (
    Portfolio,
    PortfolioPosition,
)
from backend.modules.quant_strategy.application.data_readiness import (
    DataReadinessError,
    DataReadinessGate,
    DataReadinessSnapshot,
)
from backend.modules.quant_strategy.application.position_lifecycle_manager import (
    PositionLifecycleManager,
)
from backend.modules.quant_strategy.application.position_planner import PositionPlanner
from backend.modules.quant_strategy.application.management_runtime import resolve_management_snapshot, adapt_management_output
from backend.modules.quant_strategy.domain.templates import (
    get_template,
    required_host_scalars,
    validate_frozen_template_context,
    validate_template_context,
)
from backend.modules.quant_strategy.infrastructure.lifecycle_models import (
    PositionLifecycleState,
    SuggestedOrder,
)
from backend.modules.quant_strategy.infrastructure.signals import (
    QuantExecutionSignal,
    QuantExecutionSignalRepository,
)

logger = logging.getLogger(__name__)

BATCH_SIZE = 200
MAX_WORKERS = 8
ERROR_SAMPLE_LIMIT = 100


class StrategySnapshotInvalidError(FatalAnalysisError):
    """已发布快照源码散列/再次 AST 校验不符：不可重试（code 显式透传，防基类默认值遮蔽）。"""

    code = "STRATEGY_SNAPSHOT_INVALID"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.code = self.__class__.code


def _normalize_holding_symbol(symbol: str, suffix_map: dict[str, list[str]]) -> tuple[str | None, str | None]:
    """持仓代码归一（plan 4.3.1）：全市场枚举仅接受六位数字 .SH/.SZ/.BJ；
    无后缀经 market.instrument 唯一六位前缀反查（恰一行才转真实 ts_code）；
    无匹配/多匹配/非法后缀 → 无效。"""
    symbol = (symbol or "").strip().upper()
    if re.match(r"^[0-9]{6}\.(SH|SZ|BJ)$", symbol):
        return symbol, None
    if re.match(r"^[0-9]{6}$", symbol):
        hits = suffix_map.get(symbol, [])
        if len(hits) != 1:
            return None, "INVALID_INSTRUMENT_CODE"
        return hits[0], None
    return None, "INVALID_INSTRUMENT_CODE"


def _raw_price_level(value, ratio: Decimal) -> Decimal | None:
    """Map a price-valued adjusted indicator to today's raw execution basis."""
    return Decimal(str(value)) * ratio if value is not None else None


class QuantExecutionService:
    def __init__(
        self,
        *,
        task_id: uuid.UUID,
        attempt_no: int,
        snapshot: dict,
        market_conn,
        session,
        execution_control: ExecutionControl,
        effective_trade_date: date,
        on_progress=None,
        scan_only: bool = False,
        defer_new_risk: bool = False,
        protection_only: bool = False,
        batch_size: int = BATCH_SIZE,
        max_workers: int = MAX_WORKERS,
        sandbox_timeout: float = 0.3,
    ) -> None:
        self._task_id = task_id
        self._attempt_no = attempt_no
        self._snapshot = snapshot
        self._market_conn = market_conn
        self._session = session
        self._control = execution_control
        self._effective_trade_date = effective_trade_date
        self._on_progress = on_progress or (lambda stage, done, total: None)
        self._scan_only = bool(scan_only)
        # Retry workers receive the same frozen snapshot. A caller cannot turn
        # a batch scan back into an independent BUY planner by omitting a flag.
        mode = snapshot.get("new_risk_mode", "DIRECT")
        if mode not in ("DIRECT", "FAMILY_BATCH"):
            raise StrategySnapshotInvalidError("Unknown frozen new_risk_mode")
        self._defer_new_risk = bool(defer_new_risk) or mode == "FAMILY_BATCH"
        self._protection_only = bool(protection_only)
        self._batch_size = batch_size
        self._max_workers = max_workers
        self._sandbox_timeout = sandbox_timeout
        self._signals = QuantExecutionSignalRepository(session)

    def run(self) -> dict:
        try:
            return self._run()
        except BaseException:
            self._control.terminate_all()
            self._session.rollback()
            raise

    def _run(self) -> dict:
        """执行全市场扫描 + 订单规划，返回 quant_execution 摘要（无源码/无全量信号）。"""
        strategy = self._snapshot["strategy"]
        try:
            management_policy = resolve_management_snapshot(strategy)
        except (ValueError, TypeError) as exc:
            raise StrategySnapshotInvalidError(str(exc)) from exc
        strategy_version_id = (
            uuid.UUID(str(strategy["version_id"])) if strategy.get("version_id") else None
        )
        source_code = strategy["source_code"]
        frozen_template_contract = strategy.get("template_contract")
        # 兼容 0010 之前已存在的旧快照；新快照只使用冻结合同，不读取可变注册表。
        legacy_template = (
            get_template(strategy["template_id"])
            if strategy.get("template_id") and not frozen_template_contract
            else None
        )
        benchmark_required = (
            strategy.get("template_id") == "ma_trend_cross_v1"
            and (strategy.get("template_params") or {}).get("benchmark_filter") == 1
        )
        host_scalars = required_host_scalars(
            strategy.get("template_id"), strategy.get("template_params"),
        )
        # 已发布快照校验：AST + 散列（不符 → 不可重试任务失败）
        issues = validate_strategy_source(source_code)
        if issues:
            raise StrategySnapshotInvalidError(
                f"策略快照 AST 校验失败：{'；'.join(i.code for i in issues[:5])}"
            )
        # 散列校验（与提交时冻结的 source_hash 一致）
        actual_hash = hashlib.sha256(source_code.encode("utf-8")).hexdigest()
        if actual_hash != strategy["source_hash"]:
            raise StrategySnapshotInvalidError("策略快照源码散列不符")

        global_ready = not self._protection_only
        readiness_warning = "量化补采尚未通过覆盖复核" if self._protection_only else None
        try:
            readiness = DataReadinessGate.resolve(self._market_conn, self._effective_trade_date)
            if self._protection_only and not self._snapshot.get("positions"):
                raise DataReadinessError("量化补采尚未通过覆盖复核")
        except DataReadinessError as exc:
            if self._scan_only or not self._snapshot.get("positions"):
                raise
            global_ready = False
            readiness_warning = str(exc)
            requested = self._effective_trade_date
            readiness = DataReadinessSnapshot(
                requested_trade_date=requested, market_as_of_trade_date=requested,
                daily_trade_date=requested, factor_trade_date=requested,
                adj_factor_trade_date=requested, trade_status_trade_date=requested,
                coverage_digest="", universe_digest="",
            )
        effective_date = readiness.market_as_of_trade_date
        universe = AllMarketUniverseBuilder.list_active_cn_stocks(self._market_conn) if global_ready else []
        universe_total = len(universe)

        # 持仓归一：无后缀经 instrument 唯一六位前缀反查（恰一行才转真实 ts_code）
        suffix_map: dict[str, list[str]] = {}
        for row in self._market_conn.execute(
            "SELECT ts_code FROM market.instrument WHERE instrument_type='stock'"
        ).fetchall():
            code = row[0]
            if "." in code:
                suffix_map.setdefault(code.split(".")[0], []).append(code)
        holdings: list[dict] = []
        invalid_holdings: list[tuple[str, str]] = []
        for p in self._snapshot["positions"]:
            ts_code, error = _normalize_holding_symbol(p["symbol"], suffix_map)
            if error:
                invalid_holdings.append((p["symbol"], error))
            else:
                holdings.append({**p, "symbol": ts_code})
        positions_by_code = {p["symbol"]: p for p in holdings}

        target_set = set(universe)
        for h in holdings:
            target_set.add(h["symbol"])
        scan_targets = sorted(target_set)
        stock_symbols = {code for codes in suffix_map.values() for code in codes}
        portfolio_snapshot_for_lifecycle = self._snapshot.get("portfolio")
        active_lifecycles = {}
        lifecycle_versions = {}
        if portfolio_snapshot_for_lifecycle and portfolio_snapshot_for_lifecycle.get("id"):
            portfolio_id = uuid.UUID(str(portfolio_snapshot_for_lifecycle["id"]))
            lifecycle_rows = list(self._session.scalars(
                    select(PositionLifecycleState).where(
                        PositionLifecycleState.portfolio_id == portfolio_id,
                        PositionLifecycleState.closed_at.is_(None),
                    )
                ))
            active_lifecycles = {row.symbol: row.id for row in lifecycle_rows}
            lifecycle_versions = {row.symbol: row.strategy_version_id for row in lifecycle_rows}
        lifecycle_managed_symbols = set(active_lifecycles)
        lifecycle_pending_orders: list[dict] = []
        lifecycle_order_counts = {"BUY": 0, "SELL": 0}

        def _process_lifecycle(ts_code: str, ctx: dict | None, execution_market: dict | None, output: dict | None):
            lifecycle_id = active_lifecycles.get(ts_code)
            if lifecycle_id is None:
                return
            if self._defer_new_risk and lifecycle_versions.get(ts_code) != strategy_version_id:
                # In a registered family batch each owner has its own frozen
                # scan/window. Foreign scans must not advance that owner's day.
                return
            from .ownership import owned_script_output
            output = owned_script_output(output, owner_version_id=lifecycle_versions.get(ts_code),
                                         scanner_version_id=strategy_version_id)
            if ctx is None or execution_market is None:
                fact = {"close": None, "high": None, "data_error": "MARKET_CONTEXT_UNAVAILABLE"}
            else:
                ohlcv, indicators = ctx["ohlcv"], ctx["indicators"]
                qfq_close = Decimal(str(execution_market["qfq_close"]))
                raw_close = Decimal(str(execution_market["raw_close"]))
                ratio = raw_close / qfq_close
                volumes = ohlcv.get("volume", [])
                actual = Decimal(str(ctx["position"].get("shares") or 0))
                action = output.get("action") if output else None
                script_target = None
                if action == "SELL_ALL":
                    script_target = Decimal(0)
                elif action == "SELL_PARTIAL" and output.get("sell_ratio") is not None:
                    script_target = max(Decimal(0), actual * (Decimal(1) - Decimal(str(output["sell_ratio"]))))
                def last(name, offset=-1):
                    values = indicators.get(name, [])
                    return values[offset] if len(values) >= abs(offset) else None
                def raw_level(name, offset=-1):
                    return _raw_price_level(last(name, offset), ratio)
                fact = {
                    "close": raw_close, "high": Decimal(str(ohlcv["high"][-1])) * ratio,
                    "atr": _raw_price_level(execution_market.get("atr_qfq"), ratio),
                    "is_suspended": execution_market.get("is_suspended"),
                    "ma5": raw_level("ma_qfq_5"), "prev_ma5": raw_level("ma_qfq_5", -2),
                    "ma20": raw_level("ma_qfq_20"), "prev_ma20": raw_level("ma_qfq_20", -2),
                    "ma60": raw_level("ma_qfq_60"), "boll_lower": raw_level("boll_lower_qfq"),
                    "boll_mid": raw_level("boll_mid_qfq"), "boll_upper": raw_level("boll_upper_qfq"),
                    "macd": last("macd_qfq"), "prev_macd": last("macd_qfq", -2),
                    "dif": last("macd_dif_qfq"), "prev_dif": last("macd_dif_qfq", -2),
                    "dea": last("macd_dea_qfq"), "prev_dea": last("macd_dea_qfq", -2),
                    "rsi": last("rsi_qfq_6"), "prev_rsi": last("rsi_qfq_6", -2),
                    "volume": volumes[-1] if volumes else None,
                    "volume_base": max(v for v in volumes[-6:-1] if v is not None) if any(v is not None for v in volumes[-6:-1]) else None,
                    "script_target_shares": script_target,
                    "new_risk_allowed": global_ready,
                    "execution_market": execution_market,
                    "execution_policy": self._snapshot.get("execution_policy"),
                    # Position balance does not establish broker sellability.
                    "available_sell_quantity": positions_by_code[ts_code].get("available_quantity"),
                    "source_task_id": str(self._task_id),
                }
            if self._defer_new_risk:
                # Preserve the scanner's instrument classification across the
                # deferral boundary; a batch label cannot certify an ETF/unknown.
                fact["asset_scope"] = "CN_STOCK" if ts_code in stock_symbols else None
            _daily, _intent, order, _decision = PositionLifecycleManager(self._session).process_day(
                lifecycle_id, trade_date=effective_date, fact=fact,
                data_as_of=datetime.now(timezone.utc), price_basis="raw", commit=False,
                defer_buy=self._defer_new_risk,
                buy_context={"valuation_date": effective_date,
                             "asset_scope": "CN_STOCK" if ts_code in stock_symbols else None,
                             "closes": self._load_holding_closes([p["symbol"] for p in holdings], valuation_date=effective_date),
                             "industry_map": industry_map, "industry_bucket_available": industry_available},
            )
            if order is not None:
                lifecycle_order_counts[order.side] += 1
                lifecycle_pending_orders.append({
                    "id": str(order.id), "side": order.side, "symbol": order.symbol,
                    "industry_code": order.industry_code,
                    "remaining_quantity": str(order.quantity - order.filled_quantity),
                    "order_entry_price": str(order.limit_price),
                    "order_stop_price": str(order.stop_price) if order.stop_price is not None else None,
                    "reserved_cash": str(order.reserved_cash), "status": order.status,
                })

        # 行业风险桶数据 + 门控（一次查询）
        industry_map: dict[str, dict] = {}
        for row in self._market_conn.execute(
            "SELECT m.ts_code, m.industry_code, i.name FROM market.industry_member m "
            "JOIN market.industry i ON i.source = m.source AND i.industry_code = m.industry_code "
            "WHERE m.source = 'SW2021'"
        ).fetchall():
            industry_map[row[0]] = {"industry_code": row[1], "name": row[2]}
        from db.instrument.dao import ingest_state as ingest_state_dao

        industry_available, _ = ingest_state_dao.is_industry_bucket_available(self._market_conn)

        loader = MarketContextBatchLoader(lookback=int(strategy.get("required_bars", 250)))
        benchmark_state = (
            benchmark_above_ma120(self._market_conn, effective_date)
            if benchmark_required and global_ready else None
        )
        summary_counts = {
            "data_complete": 0,
            "scanned": 0,
            "failed": 0,
            "buy_matches": 0,
            "holding_signals": 0,
        }
        error_samples: dict[str, list[str]] = {}
        error_counts: dict[str, int] = {}

        def _persist_signal(kind, ts_code, result, execution_market, lifecycle_seed=None) -> None:
            signal = QuantExecutionSignal(
                task_id=self._task_id,
                strategy_version_id=strategy_version_id,
                attempt_no=self._attempt_no,
                signal_kind=kind,
                ts_code=ts_code,
            )
            if kind == "ERROR":
                signal.error_code = result.error_code
                signal.reason = (result.error_message or "")[:240]
            else:
                out = result.output
                signal.action = out["action"]
                signal.score = out["score"]
                signal.reason = out["reason"]
                signal.entry_price = out["entry_price"]
                signal.stop_loss = out["stop_loss"]
                signal.take_profit = out["take_profit"]
                signal.sell_ratio = out["sell_ratio"]
                if execution_market:
                    signal.valuation_price = execution_market.get("raw_close")
                    trade_date = execution_market.get("trade_date") or self._effective_trade_date.isoformat()
                    signal.signal_trade_date = date.fromisoformat(str(trade_date)[:10])
                    signal.signal_price_basis = "qfq"
                    signal.execution_price_basis = "raw"
                    signal.adj_factor_version = execution_market.get("adj_factor_version")
                    signal.execution_market = {
                        **execution_market,
                        "batch_context": {
                            "industry": industry_map.get(ts_code),
                            "industry_bucket_available": industry_available,
                            "new_risk_allowed": global_ready,
                        },
                        **({"lifecycle_seed": lifecycle_seed} if lifecycle_seed else {}),
                    }
            self._signals.add(signal)

        def _persist_data_error(ts_code: str, code: str) -> None:
            self._signals.add(
                QuantExecutionSignal(
                    task_id=self._task_id,
                    strategy_version_id=strategy_version_id,
                    attempt_no=self._attempt_no,
                    signal_kind="ERROR",
                    ts_code=ts_code,
                    error_code=code,
                    reason=f"执行期行情输入不可用: {code}",
                )
            )

        for batch_index in range(0, len(scan_targets), self._batch_size):
            self._control.raise_if_inactive()
            batch = scan_targets[batch_index : batch_index + self._batch_size]
            contexts = loader.load_batch(
                self._market_conn, batch, effective_date, positions_by_code=positions_by_code,
                requested_trade_date=readiness.requested_trade_date,
                benchmark_above_ma120=benchmark_state,
            )
            pending: list = []
            with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
                for item in contexts:
                    self._control.raise_if_inactive()
                    ts = item["ts_code"]
                    if (item["status"] == "OK" and host_scalars
                            and not (item["context"]["position"].get("shares") or 0)):
                        absent = [field for field in host_scalars
                                  if item["context"].get("meta", {}).get(field) is None]
                        if absent:
                            item = {**item, "status": (
                                "BENCHMARK_UNAVAILABLE" if "benchmark_above_ma120" in absent
                                else "INDICATOR_UNAVAILABLE"
                            ), "context": None}
                    if item["status"] == "OK" and frozen_template_contract is not None:
                        guard_error = validate_frozen_template_context(frozen_template_contract, item["context"])
                        if guard_error is not None:
                            item = {**item, "status": guard_error, "context": None}
                    elif item["status"] == "OK" and legacy_template is not None:
                        guard_error = validate_template_context(legacy_template, item["context"])
                        if guard_error is not None:
                            item = {**item, "status": guard_error, "context": None}
                    if item["status"] != "OK":
                        summary_counts["failed"] += 1
                        error_counts[item["status"]] = error_counts.get(item["status"], 0) + 1
                        samples = error_samples.setdefault(item["status"], [])
                        if len(samples) < ERROR_SAMPLE_LIMIT:
                            samples.append(ts)
                            _persist_data_error(ts, item["status"])
                        elif ts in positions_by_code:
                            # 持仓输入错误始终保留，不能被非持仓 sample 上限吞掉。
                            _persist_data_error(ts, item["status"])
                        if ts in active_lifecycles:
                            _process_lifecycle(ts, None, None, None)
                        continue
                    summary_counts["data_complete"] += 1
                    summary_counts["scanned"] += 1
                    ctx = item["context"]
                    has_position = (ctx["position"].get("shares") or 0) > 0

                    execution_market = item["execution_market"]

                    def _execute(_ctx, _ts, _has_position, _execution_market):
                        self._control.raise_if_inactive()
                        proc_holder: dict = {}
                        try:
                            result = run_strategy(
                                source_code,
                                _ctx,
                                has_position=_has_position,
                                timeout=self._sandbox_timeout,
                                allow_null_take_profit=management_policy is not None,
                                on_process=lambda popen: (proc_holder.update(p=popen), self._control.register_process(popen)),
                            )
                        finally:
                            popen = proc_holder.get("p")
                            if popen is not None:
                                self._control.unregister_process(popen)
                        if result.ok and management_policy is not None:
                            result = replace(result, output=adapt_management_output(
                                result.output, management_policy, _ctx, _execution_market,
                            ))
                        return _ts, result, _has_position, _execution_market, _ctx

                    pending.append(pool.submit(_execute, ctx, ts, has_position, execution_market))

                for future in as_completed(pending):
                    self._control.raise_if_inactive()
                    ts, result, has_position, execution_market, lifecycle_ctx = future.result()
                    if not result.ok:
                        summary_counts["failed"] += 1
                        code = result.error_code or "INVALID_OUTPUT"
                        error_counts[code] = error_counts.get(code, 0) + 1
                        # 错误样本按每码 ≤100 条落 ERROR 行（plan 4.3.1 信号保留矩阵）；
                        # 持仓错误全量落，非持仓错误按 sample 上限落
                        samples = error_samples.setdefault(code, [])
                        if len(samples) < ERROR_SAMPLE_LIMIT:
                            samples.append(ts)
                            _persist_signal("ERROR", ts, result, None)
                        elif has_position:
                            _persist_signal("ERROR", ts, result, None)
                        if has_position and ts in active_lifecycles:
                            _process_lifecycle(ts, lifecycle_ctx, execution_market, None)
                        continue
                    action = result.output["action"]
                    if action == "BUY" and not global_ready:
                        _persist_data_error(ts, "NEW_RISK_BLOCKED_DATA_READINESS")
                        if has_position and ts in active_lifecycles:
                            _process_lifecycle(ts, lifecycle_ctx, execution_market, None)
                        continue
                    if action == "HOLD" and not has_position:
                        continue  # 非持仓正常 HOLD 不落表
                    if action == "BUY":
                        summary_counts["buy_matches"] += 1
                        kind = "BUY"
                    else:
                        summary_counts["holding_signals"] += 1
                        kind = "HOLDING"
                    lifecycle_seed = None
                    if (
                        strategy.get("template_id") == "arc_bottom_75a_v1"
                        and result.output["action"] == "BUY"
                    ):
                        closes = lifecycle_ctx["ohlcv"]["close"]
                        if len(closes) >= 41:
                            ratio = Decimal(str(execution_market["raw_close"])) / Decimal(str(execution_market["qfq_close"]))
                            lifecycle_seed = {
                                "arc_neckline_price": str(max(
                                    Decimal(str(closes[-41])), Decimal(str(closes[-11]))
                                ) * ratio),
                                "input_hash": lifecycle_ctx.get("meta", {}).get("data_hash"),
                            }
                    _persist_signal(kind, ts, result, execution_market, lifecycle_seed)
                    if has_position and ts in active_lifecycles:
                        _process_lifecycle(ts, lifecycle_ctx, execution_market, result.output)
            # 每批落盘后提交（取消时已持久化批次保留，未持久化批次丢弃）
            self._control.raise_if_inactive()
            self._session.commit()
            done = min(batch_index + self._batch_size, len(scan_targets))
            self._on_progress("scan", done, len(scan_targets))

        # 持仓无效代码：记录 HOLDING 审计错误
        for symbol, error in invalid_holdings:
            signal = QuantExecutionSignal(
                task_id=self._task_id, attempt_no=self._attempt_no,
                strategy_version_id=strategy_version_id,
                signal_kind="ERROR", ts_code=symbol, error_code=error, reason=f"持仓代码无效: {symbol}",
            )
            self._signals.add(signal)

        if self._scan_only:
            self._control.raise_if_inactive()
            self._session.commit()
            warnings = []
            if error_samples:
                warnings.append(f"扫描错误：{dict(error_counts)}（样本见 signals 表）")
            return {
                "strategy": {
                    "id": str(strategy_version_id) if strategy_version_id else None,
                    "name": strategy.get("name", ""),
                    "version_no": strategy.get("version_no"),
                    "source_hash_prefix": strategy.get("source_hash", "")[:12],
                    "published_at": strategy.get("published_at"),
                },
                "summary": {
                    "universe_total": universe_total,
                    "data_complete": summary_counts["data_complete"],
                    "scanned": summary_counts["scanned"],
                    "buy_matches": summary_counts["buy_matches"],
                    "failed_count": summary_counts["failed"],
                },
                "warnings": warnings,
                "requested_trade_date": readiness.requested_trade_date.isoformat(),
                "market_as_of_trade_date": readiness.market_as_of_trade_date.isoformat(),
            }

        # 全量信号落库后：全局排序规划订单
        portfolio_snapshot = self._snapshot["portfolio"]
        from .planning_account import lock_portfolio, planning_account
        current_portfolio = lock_portfolio(self._session, uuid.UUID(str(portfolio_snapshot["id"])))
        pending_for_plan = [*self._snapshot.get("pending_orders", []), *lifecycle_pending_orders]
        owner_versions = None
        if current_portfolio is not None:
            from .portfolio_drawdown_actions import PortfolioDrawdownActions
            PortfolioDrawdownActions(self._session).pause_for_planning(
                current_portfolio.id, valuation_date=readiness.market_as_of_trade_date)
            current = planning_account(self._session, current_portfolio)
            owner_versions = current["owner_versions"]
            lifecycle_managed_symbols = set(self._session.scalars(select(PositionLifecycleState.symbol).where(
                PositionLifecycleState.portfolio_id == current_portfolio.id,
                PositionLifecycleState.market == "CN", PositionLifecycleState.closed_at.is_(None),
            )))
            portfolio_snapshot, holdings, pending_for_plan = (
                current["portfolio_snapshot"], current["positions"], current["pending_orders"])
        closes = self._load_holding_closes([p["symbol"] for p in holdings], valuation_date=effective_date)
        from .strategy_admission import StrategyAdmissionService
        admission = StrategyAdmissionService(self._session).gate_new_risk(
            strategy_version_id, asset_scope="CN_STOCK",
            risk_profile=portfolio_snapshot.get("risk_profile"),
        )
        from .certified_instrument_rules import live_authorizations
        rule_authorizations = live_authorizations(
            self._session,
            symbols=set(self._session.scalars(select(QuantExecutionSignal.ts_code).where(
                QuantExecutionSignal.task_id == self._task_id,
                QuantExecutionSignal.attempt_no == self._attempt_no,
                QuantExecutionSignal.signal_kind == "BUY",
            ))), decision_date=readiness.market_as_of_trade_date)
        summary = PositionPlanner(self._signals).plan(
            task_id=self._task_id,
            attempt_no=self._attempt_no,
            portfolio_snapshot=portfolio_snapshot,
            positions=holdings,
            closes=closes,
            industry_map=industry_map,
            industry_bucket_available=industry_available,
            risk_gate=None,
            execution_policy_snapshot=self._snapshot.get("execution_policy"),
            pending_orders=pending_for_plan,
            valuation_date=readiness.market_as_of_trade_date,
            lifecycle_managed_symbols=lifecycle_managed_symbols,
            owner_versions=owner_versions, strategy_version_id=strategy_version_id,
            admission_block_code=("AWAITING_FAMILY_BATCH" if self._defer_new_risk
                                  else None if admission["allowed"] else admission["code"]),
            instrument_rules={symbol: authorization.rule
                              for symbol, authorization in rule_authorizations.items()},
        )
        self._control.raise_if_inactive()
        self._session.flush()
        materialization_rejections = self._persist_suggested_orders(
            portfolio_snapshot, positions=holdings, industry_map=industry_map,
            rule_authorizations=rule_authorizations)
        for code, count in materialization_rejections.items():
            summary.suggested_buy_orders -= count
            summary.buy_rejections[code] = summary.buy_rejections.get(code, 0) + count
        self._session.commit()
        summary.suggested_buy_orders += lifecycle_order_counts["BUY"]
        summary.suggested_sell_orders += lifecycle_order_counts["SELL"]

        warnings = list(summary.warnings)
        if readiness_warning is not None:
            warnings.append(f"全市场新仓数据未就绪，已仅处理持仓保护：{readiness_warning}")
        if not industry_available:
            warnings.append("行业风控桶不可用：INDUSTRY_BUCKET_UNAVAILABLE（行业相关 BUY 已拒绝）")
        if error_samples:
            warnings.append(f"扫描错误：{dict(error_counts)}（样本见 signals 表）")
        return {
            "admission": admission,
            "strategy": {
                "name": self._snapshot["strategy"].get("name", ""),
                "version_no": self._snapshot["strategy"]["version_no"],
                "source_hash_prefix": self._snapshot["strategy"]["source_hash"][:12],
                "published_at": self._snapshot["strategy"].get("published_at"),
            },
            "portfolio_snapshot": {
                "id": portfolio_snapshot["id"],
                "name": portfolio_snapshot["name"],
                "version": portfolio_snapshot["version"],
                "total_assets": portfolio_snapshot["total_assets"],
                "available_cash": portfolio_snapshot["available_cash"],
                "risk": portfolio_snapshot["risk"],
                "risk_profile": portfolio_snapshot.get("risk_profile"),
                "snapshot_at": portfolio_snapshot.get("snapshot_at", self._snapshot.get("as_of")),
            },
            "summary": {
                "universe_total": universe_total,
                "data_complete": summary_counts["data_complete"],
                "scanned": summary_counts["scanned"],
                "buy_matches": summary_counts["buy_matches"],
                "suggested_buy_orders": summary.suggested_buy_orders,
                "suggested_sell_orders": summary.suggested_sell_orders,
                "failed_count": summary_counts["failed"],
                "portfolio_open_risk": format(summary.portfolio_open_risk, "f"),
                "daily_new_risk": format(summary.daily_new_risk, "f"),
                "full_drawdown_exit_required": summary.full_drawdown_exit_required,
            },
            "buy_rejections": summary.buy_rejections,
            "warnings": warnings,
            "global_data_ready": global_ready,
            "valued_at": summary.valued_at.isoformat(),
            "requested_trade_date": readiness.requested_trade_date.isoformat(),
            "market_as_of_trade_date": (
                readiness.market_as_of_trade_date.isoformat() if global_ready else None
            ),
        }

    def _persist_suggested_orders(self, portfolio_snapshot: dict, *, positions: list[dict],
                                  industry_map: dict, rule_authorizations: dict | None = None) -> dict[str, int]:
        """Materialize ELIGIBLE signal projections once; retries reuse source-signal uniqueness."""
        portfolio_id = uuid.UUID(str(portfolio_snapshot["id"]))
        # Some isolated execution tests intentionally provide a snapshot without
        # the workspace aggregate. Production submissions always retain it.
        if self._session.get(Portfolio, portfolio_id) is None:
            return {}
        rule_authorizations = rule_authorizations or {}
        rows = list(self._session.scalars(
            select(QuantExecutionSignal).where(
                QuantExecutionSignal.task_id == self._task_id,
                QuantExecutionSignal.attempt_no == self._attempt_no,
                QuantExecutionSignal.order_status == "ELIGIBLE",
                QuantExecutionSignal.shares.isnot(None),
            ).order_by(QuantExecutionSignal.id)
        ))
        from .order_materialization import materialize_signal_orders
        materialize_signal_orders(self._session, portfolio_id=portfolio_id, rows=rows,
            strategy_snapshots={str(row.strategy_version_id): self._snapshot.get("strategy", {}) for row in rows},
            industry_map=industry_map,
            instrument_rules={symbol: authorization.rule
                              for symbol, authorization in rule_authorizations.items()},
            rule_authorizations=rule_authorizations)
        from collections import Counter
        return dict(Counter(row.order_status for row in rows
                            if row.signal_kind == "BUY" and row.order_status != "ELIGIBLE"))

    def _load_holding_closes(self, symbols: list[str], *, valuation_date: date) -> dict:
        """Only same-session closes certify new BUY valuation; missing values block."""
        from decimal import Decimal

        closes: dict[str, Decimal] = {}
        if not symbols:
            return closes
        rows = self._market_conn.execute(
            "SELECT DISTINCT ON (ts_code) ts_code, close FROM market.instrument_daily "
            "WHERE ts_code = ANY(%s) AND trade_date = %s ORDER BY ts_code, trade_date DESC",
            (symbols, valuation_date.isoformat()),
        ).fetchall()
        for r in rows:
            closes[r[0]] = Decimal(str(r[1]))
        return closes
