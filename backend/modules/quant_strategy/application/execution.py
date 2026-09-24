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
)
from backend.modules.quant_strategy.application.position_planner import PositionPlanner
from backend.modules.quant_strategy.application.position_lifecycle_manager import PositionLifecycleManager
from backend.modules.quant_strategy.application.data_readiness import DataReadinessGate
from backend.modules.quant_strategy.domain.templates import (
    get_template,
    validate_frozen_template_context,
    validate_template_context,
)
from backend.modules.quant_strategy.infrastructure.signals import (
    QuantExecutionSignal,
    QuantExecutionSignalRepository,
)
from backend.modules.quant_strategy.infrastructure.lifecycle_models import PositionLifecycleState, SuggestedOrder
from backend.modules.investment_workspace.infrastructure.models import Portfolio, PortfolioPosition

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

        readiness = DataReadinessGate.resolve(self._market_conn, self._effective_trade_date)
        effective_date = readiness.market_as_of_trade_date
        universe = AllMarketUniverseBuilder.list_active_cn_stocks(self._market_conn)
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
        portfolio_snapshot_for_lifecycle = self._snapshot.get("portfolio")
        active_lifecycles = {}
        if portfolio_snapshot_for_lifecycle and portfolio_snapshot_for_lifecycle.get("id"):
            portfolio_id = uuid.UUID(str(portfolio_snapshot_for_lifecycle["id"]))
            active_lifecycles = {
                row.symbol: row.id for row in self._session.scalars(
                    select(PositionLifecycleState).where(
                        PositionLifecycleState.portfolio_id == portfolio_id,
                        PositionLifecycleState.closed_at.is_(None),
                    )
                )
            }
        lifecycle_managed_symbols = set(active_lifecycles)
        lifecycle_pending_orders: list[dict] = []
        lifecycle_order_counts = {"BUY": 0, "SELL": 0}

        def _process_lifecycle(ts_code: str, ctx: dict | None, execution_market: dict | None, output: dict | None):
            lifecycle_id = active_lifecycles.get(ts_code)
            if lifecycle_id is None:
                return
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
                fact = {
                    "close": raw_close, "high": Decimal(str(ohlcv["high"][-1])) * ratio,
                    "ma5": last("ma_qfq_5"), "prev_ma5": last("ma_qfq_5", -2),
                    "ma20": last("ma_qfq_20"), "prev_ma20": last("ma_qfq_20", -2),
                    "ma60": last("ma_qfq_60"), "boll_lower": last("boll_lower_qfq"),
                    "boll_mid": last("boll_mid_qfq"), "boll_upper": last("boll_upper_qfq"),
                    "macd": last("macd_qfq"), "prev_macd": last("macd_qfq", -2),
                    "dif": last("macd_dif_qfq"), "prev_dif": last("macd_dif_qfq", -2),
                    "dea": last("macd_dea_qfq"), "prev_dea": last("macd_dea_qfq", -2),
                    "rsi": last("rsi_qfq_6"), "prev_rsi": last("rsi_qfq_6", -2),
                    "volume": volumes[-1] if volumes else None,
                    "volume_base": max(v for v in volumes[-6:-1] if v is not None) if any(v is not None for v in volumes[-6:-1]) else None,
                    "script_target_shares": script_target,
                    "source_task_id": str(self._task_id),
                }
            _daily, _intent, order, _decision = PositionLifecycleManager(self._session).process_day(
                lifecycle_id, trade_date=effective_date, fact=fact,
                data_as_of=datetime.now(timezone.utc), price_basis="raw", commit=False,
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

        total_batches = (len(scan_targets) + self._batch_size - 1) // self._batch_size
        for batch_index in range(0, len(scan_targets), self._batch_size):
            self._control.raise_if_inactive()
            batch = scan_targets[batch_index : batch_index + self._batch_size]
            contexts = loader.load_batch(
                self._market_conn, batch, effective_date, positions_by_code=positions_by_code,
                requested_trade_date=readiness.requested_trade_date,
            )
            pending: list = []
            with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
                for item in contexts:
                    self._control.raise_if_inactive()
                    ts = item["ts_code"]
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
                                on_process=lambda popen: (proc_holder.update(p=popen), self._control.register_process(popen)),
                            )
                        finally:
                            popen = proc_holder.get("p")
                            if popen is not None:
                                self._control.unregister_process(popen)
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
        closes = self._load_holding_closes([p["symbol"] for p in holdings])
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
            pending_orders=[*self._snapshot.get("pending_orders", []), *lifecycle_pending_orders],
            valuation_date=readiness.market_as_of_trade_date,
            lifecycle_managed_symbols=lifecycle_managed_symbols,
        )
        self._control.raise_if_inactive()
        self._session.flush()
        self._persist_suggested_orders(portfolio_snapshot, positions=holdings, industry_map=industry_map)
        self._session.commit()
        summary.suggested_buy_orders += lifecycle_order_counts["BUY"]
        summary.suggested_sell_orders += lifecycle_order_counts["SELL"]

        warnings = list(summary.warnings)
        if not industry_available:
            warnings.append("行业风控桶不可用：INDUSTRY_BUCKET_UNAVAILABLE（行业相关 BUY 已拒绝）")
        if error_samples:
            warnings.append(f"扫描错误：{dict(error_counts)}（样本见 signals 表）")
        return {
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
                "snapshot_at": self._snapshot.get("as_of"),
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
            },
            "buy_rejections": summary.buy_rejections,
            "warnings": warnings,
            "valued_at": summary.valued_at.isoformat(),
            "requested_trade_date": readiness.requested_trade_date.isoformat(),
            "market_as_of_trade_date": readiness.market_as_of_trade_date.isoformat(),
        }

    def _persist_suggested_orders(self, portfolio_snapshot: dict, *, positions: list[dict], industry_map: dict) -> None:
        """Materialize ELIGIBLE signal projections once; retries reuse source-signal uniqueness."""
        portfolio_id = uuid.UUID(str(portfolio_snapshot["id"]))
        # Some isolated execution tests intentionally provide a snapshot without
        # the workspace aggregate. Production submissions always retain it.
        if self._session.get(Portfolio, portfolio_id) is None:
            return
        rows = list(self._session.scalars(
            select(QuantExecutionSignal).where(
                QuantExecutionSignal.task_id == self._task_id,
                QuantExecutionSignal.attempt_no == self._attempt_no,
                QuantExecutionSignal.order_status == "ELIGIBLE",
                QuantExecutionSignal.shares.isnot(None),
            ).order_by(QuantExecutionSignal.id)
        ))
        existing = set(self._session.scalars(
            select(SuggestedOrder.source_signal_id).where(
                SuggestedOrder.source_signal_id.in_([r.id for r in rows])
            )
        )) if rows else set()
        position_ids = {
            p.symbol: p.id for p in self._session.scalars(
                select(PortfolioPosition).where(PortfolioPosition.portfolio_id == portfolio_id)
            )
        }
        for signal in rows:
            if signal.id in existing:
                continue
            shares = Decimal(str(signal.shares))
            side = "BUY" if signal.signal_kind == "BUY" else "SELL"
            lifecycle_policy = self._snapshot.get("strategy", {}).get("lifecycle_policy")
            if side == "BUY" and lifecycle_policy is not None:
                initial_exposure = Decimal(str(
                    lifecycle_policy.get("config", {}).get("initial_exposure_pct", "0.50")
                ))
                if initial_exposure not in {Decimal("0.50")}:
                    raise StrategySnapshotInvalidError("生命周期首仓比例必须为 0.50")
                shares = (shares * initial_exposure / Decimal(100)).to_integral_value(
                    rounding="ROUND_FLOOR"
                ) * Decimal(100)
            price = signal.order_cost_price or signal.order_entry_price or signal.valuation_price
            if price is None or shares <= 0:
                continue
            price = Decimal(str(price))
            stop = Decimal(str(signal.order_stop_price)) if signal.order_stop_price is not None else None
            fees = Decimal(str(signal.estimated_fees or 0))
            reserved_cash = (shares * price + fees) if side == "BUY" else Decimal(0)
            reserved_risk = (
                max(Decimal(0), price - stop) * shares if side == "BUY" and stop is not None else Decimal(0)
            )
            bucket = industry_map.get(signal.ts_code) or {}
            self._session.add(SuggestedOrder(
                id=uuid.uuid4(), portfolio_id=portfolio_id,
                position_id=uuid.UUID(str(position_ids[signal.ts_code])) if position_ids.get(signal.ts_code) else None,
                source_signal_id=signal.id, market="CN", symbol=signal.ts_code,
                industry_code=bucket.get("industry_code"), side=side, quantity=shares,
                filled_quantity=Decimal(0), limit_price=price, stop_price=stop,
                reserved_cash=reserved_cash, reserved_risk=reserved_risk,
                reason_code=(signal.reason or signal.action or "QUANT_SIGNAL")[:64],
                status="PROPOSED", revision=1,
                earliest_execution_trade_date=signal.earliest_execution_trade_date,
            ))

    def _load_holding_closes(self, symbols: list[str]) -> dict:
        """持仓估值收盘价：以 effective_trade_date 为上界取各票最新 close（无前视）。"""
        from decimal import Decimal

        closes: dict[str, Decimal] = {}
        if not symbols:
            return closes
        rows = self._market_conn.execute(
            "SELECT DISTINCT ON (ts_code) ts_code, close FROM market.instrument_daily "
            "WHERE ts_code = ANY(%s) AND trade_date <= %s ORDER BY ts_code, trade_date DESC",
            (symbols, self._effective_trade_date.isoformat()),
        ).fetchall()
        for r in rows:
            closes[r[0]] = Decimal(str(r[1]))
        return closes
