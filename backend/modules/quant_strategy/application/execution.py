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

- 风险门控本方案不接线：risk_gate 缺省（null = 不门控），报告 warnings 标注，
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
from datetime import date

from AI.strategy_sandbox.runner import run_strategy
from AI.strategy_sandbox.validator import validate_strategy_source
from backend.modules.analysis.application.errors import FatalAnalysisError
from backend.modules.analysis.infrastructure.execution_control import ExecutionControl
from backend.modules.analysis.infrastructure.quant_execution_market_data import (
    AllMarketUniverseBuilder,
    MarketContextBatchLoader,
)
from backend.modules.quant_strategy.application.position_planner import PositionPlanner
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
        self._batch_size = batch_size
        self._max_workers = max_workers
        self._sandbox_timeout = sandbox_timeout
        self._signals = QuantExecutionSignalRepository(session)

    def run(self) -> dict:
        """执行全市场扫描 + 订单规划，返回 quant_execution 摘要（无源码/无全量信号）。"""
        strategy = self._snapshot["strategy"]
        source_code = strategy["source_code"]
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

        effective_date = self._effective_trade_date
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

        loader = MarketContextBatchLoader()
        summary_counts = {
            "data_complete": 0,
            "scanned": 0,
            "failed": 0,
            "buy_matches": 0,
            "holding_signals": 0,
        }
        error_samples: dict[str, list[str]] = {}
        error_counts: dict[str, int] = {}

        def _persist_signal(kind, ts_code, result, position) -> None:
            signal = QuantExecutionSignal(
                task_id=self._task_id,
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
                if kind == "BUY" and out["entry_price"] is not None:
                    # valuation_price = effective-date close（planner 的 order_cost_price 依据）
                    signal.valuation_price = position.get("close_last")
            self._signals.add(signal)

        total_batches = (len(scan_targets) + self._batch_size - 1) // self._batch_size
        for batch_index in range(0, len(scan_targets), self._batch_size):
            self._control.raise_if_inactive()
            batch = scan_targets[batch_index : batch_index + self._batch_size]
            contexts = loader.load_batch(
                self._market_conn, batch, effective_date, positions_by_code=positions_by_code
            )
            pending: list = []
            with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
                for item in contexts:
                    self._control.raise_if_inactive()
                    ts = item["ts_code"]
                    if item["status"] != "OK":
                        if item["status"] in ("DATA_UNAVAILABLE", "INDICATOR_UNAVAILABLE"):
                            summary_counts["failed"] += 1
                            error_counts[item["status"]] = error_counts.get(item["status"], 0) + 1
                            if len(error_samples.setdefault(item["status"], [])) < ERROR_SAMPLE_LIMIT:
                                error_samples[item["status"]].append(ts)
                        continue
                    summary_counts["data_complete"] += 1
                    summary_counts["scanned"] += 1
                    ctx = item["context"]
                    has_position = (ctx["position"].get("shares") or 0) > 0

                    def _execute(_ctx, _ts, _has_position):
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
                        return _ts, result, _has_position, _ctx["ohlcv"]["close"][-1]

                    pending.append(pool.submit(_execute, ctx, ts, has_position))

                for future in as_completed(pending):
                    self._control.raise_if_inactive()
                    ts, result, has_position, close_last = future.result()
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
                    _persist_signal(kind, ts, result, {"close_last": close_last})
            # 每批落盘后提交（取消时已持久化批次保留，未持久化批次丢弃）
            self._session.commit()
            done = min(batch_index + self._batch_size, len(scan_targets))
            self._on_progress("scan", done, len(scan_targets))

        # 持仓无效代码：记录 HOLDING 审计错误
        for symbol, error in invalid_holdings:
            signal = QuantExecutionSignal(
                task_id=self._task_id, attempt_no=self._attempt_no,
                signal_kind="ERROR", ts_code=symbol, error_code=error, reason=f"持仓代码无效: {symbol}",
            )
            self._signals.add(signal)

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
        )
        self._session.commit()

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
            },
            "buy_rejections": summary.buy_rejections,
            "warnings": warnings,
            "valued_at": summary.valued_at.isoformat(),
        }

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
