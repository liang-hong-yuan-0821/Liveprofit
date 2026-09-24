"""分析执行器（§3.1.4「Worker 执行器职责」）。

- 心跳、图执行、进度发布、终态写入均使用独立 Session（每次 open 一个新 bundle）。
- 心跳续租失败（fencing 丢失）或到达 attempt deadline 后，执行器停止提交任何状态/产物。
- 协作式取消：进度回调边界检查 CANCEL_REQUESTED，发现即 mark_cancelled 并停止后续图推进。
- 业务异常经 classify_error → fail_or_retry（Dramatiq 不做业务重试）。
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from contextlib import AbstractContextManager
from dataclasses import replace
from datetime import date
from typing import Any, Callable, Protocol

from backend.modules.analysis.application.contracts import AnalysisArtifact, ClaimedTask
from backend.modules.analysis.application.errors import (
    CooperativeCancelledError,
    FencingLostError,
    LeaseConflictError,
    classify_error,
)
from backend.modules.analysis.domain.enums import TaskEventType, TaskStatus
from backend.modules.analysis.infrastructure.artifact_store import ArtifactFencingError
from backend.modules.analysis.infrastructure.execution_control import (
    ExecutionControl,
    ExecutionInactiveError,
)
from backend.modules.analysis.infrastructure.source_guard import assert_no_source_leak

logger = logging.getLogger(__name__)

PhaseExtractor = Callable[[str], str | None]
ArtifactBuilder = Callable[[Any], AnalysisArtifact]


class ServiceBundle(Protocol):
    """一次独立 Session 的完整服务集合（心跳/进度/终态各自 open）。"""

    tasks: Any  # TaskService
    reports: Any  # ReportService
    events: Any  # TaskEventService
    build_artifact: ArtifactBuilder


class ServiceBundleFactory(Protocol):
    def open(self) -> AbstractContextManager[ServiceBundle]: ...


def default_phase_extractor(message: str) -> str | None:
    """首版只归类市场/板块/个股/决策的稳定阶段消息（不解析日志文本）。"""
    for phase, keywords in (
        ("market", ("市场", "market")),
        ("sector", ("板块", "sector")),
        ("stock", ("个股", "stock")),
        ("decision", ("决策", "decision")),
    ):
        if any(keyword in message for keyword in keywords):
            return phase
    return None


class LeaseHeartbeat(threading.Thread):
    """独立心跳线程：每个 tick 都用独立 Session 调 renew_lease（不与 Actor/进度共享 Session）。"""

    def __init__(
        self,
        *,
        task_id: uuid.UUID,
        attempt_no: int,
        lease_token: str,
        bundle_factory: ServiceBundleFactory,
        heartbeat_interval_seconds: float,
        max_attempt_runtime_seconds: float,
    ) -> None:
        super().__init__(daemon=True, name=f"lease-heartbeat-{task_id}-{attempt_no}")
        self._task_id = task_id
        self._attempt_no = attempt_no
        self._lease_token = lease_token
        self._bundle_factory = bundle_factory
        self._interval = heartbeat_interval_seconds
        self._deadline = time.monotonic() + max_attempt_runtime_seconds
        # 注意：不可覆盖 Thread 内部方法名 _stop（join 时会被调用）
        self._stop_requested = threading.Event()
        self.fencing_lost = threading.Event()

    def run(self) -> None:
        while not self._stop_requested.wait(self._interval):
            if time.monotonic() > self._deadline:
                # 到达 attempt deadline：停止续租，Actor 在阶段边界协作停止
                self.fencing_lost.set()
                return
            ok = False
            try:
                with self._bundle_factory.open() as bundle:
                    ok = bundle.tasks.renew_lease(self._task_id, self._attempt_no, self._lease_token)
            except Exception:  # noqa: BLE001 - 任何续租异常均视为失去 fencing
                logger.exception("心跳续租异常：task=%s attempt=%s", self._task_id, self._attempt_no)
            if not ok:
                self.fencing_lost.set()
                return

    def stop(self) -> None:
        self._stop_requested.set()


def _is_quant_task(claimed: ClaimedTask) -> bool:
    """量化任务判定：request_params 含 execution_snapshot 且 selected_layers 含 position。"""
    return (
        isinstance(claimed.request_params, dict)
        and claimed.request_params.get("execution_snapshot") is not None
        and "position" in (claimed.selected_layers or ())
    )


def _is_daily_research_task(claimed: ClaimedTask) -> bool:
    workflow = (claimed.request_params or {}).get("daily_research")
    return isinstance(workflow, dict) and workflow.get("kind") in {"news", "quant"}


def _as_state_dict(state: Any) -> dict:
    if state is None:
        return {}
    if isinstance(state, dict):
        return dict(state)
    if hasattr(state, "__dict__"):
        return dict(state.__dict__)
    return {}


class AnalysisExecutor:
    def __init__(
        self,
        *,
        graph_adapter: Any,
        bundle_factory: ServiceBundleFactory,
        worker_id: str,
        heartbeat_interval_seconds: float = 30.0,
        max_attempt_runtime_seconds: float = 3600.0,
        phase_extractor: PhaseExtractor = default_phase_extractor,
        artifact_store: Any = None,
        core_version: str | None = None,
        market_dsn: str | None = None,
        max_news_per_run: int = 100,
        quant_preflight: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self._adapter = graph_adapter
        self._bundles = bundle_factory
        self._worker_id = worker_id
        self._heartbeat_interval = heartbeat_interval_seconds
        self._max_runtime = max_attempt_runtime_seconds
        self._phase_extractor = phase_extractor
        self._artifact_store = artifact_store
        self._core_version = core_version
        self._market_dsn = market_dsn
        self._max_news_per_run = max(1, int(max_news_per_run))
        self._quant_preflight = quant_preflight
        self._sequence = 0
        self._cancel_flag = False

    def execute(self, claimed: ClaimedTask, rerun_from: str | None = None) -> None:
        self._claimed = claimed
        with self._bundles.open() as bundle:
            bundle.events.publish(
                claimed.task_id,
                TaskEventType.STARTED,
                attempt_no=claimed.attempt_no,
                worker_id=self._worker_id,
            )
        heartbeat = LeaseHeartbeat(
            task_id=claimed.task_id,
            attempt_no=claimed.attempt_no,
            lease_token=claimed.lease_token,
            bundle_factory=self._bundles,
            heartbeat_interval_seconds=self._heartbeat_interval,
            max_attempt_runtime_seconds=self._max_runtime,
        )
        heartbeat.start()
        try:
            if _is_daily_research_task(claimed):
                self._run_daily_research(claimed, heartbeat)
            elif _is_quant_task(claimed):
                self._run_quant(claimed, heartbeat)
            else:
                self._run_graph(claimed, rerun_from=rerun_from)
        finally:
            heartbeat.stop()
            heartbeat.join(timeout=self._heartbeat_interval * 2 + 5)

    def _run_daily_research(self, claimed: ClaimedTask, heartbeat: LeaseHeartbeat) -> None:
        """Run scheduled/manual research through the existing lease and report lifecycle."""
        control = ExecutionControl(
            claimed.task_id,
            claimed.lease_token,
            is_cancelled=lambda: self._cancel_flag,
            is_fencing_active=lambda: heartbeat.fencing_lost.is_set(),
        )
        try:
            workflow = (claimed.request_params or {}).get("daily_research") or {}
            if workflow.get("kind") == "news":
                from backend.modules.daily_research.application.news_pipeline import (
                    acquire_news_analysis_lock,
                )
                with self._bundles.open() as bundle:
                    news_lock = acquire_news_analysis_lock(bundle.uow.session)
                try:
                    self._run_daily_news_pipeline(claimed, workflow)
                finally:
                    news_lock.release()
            elif workflow.get("kind") == "quant":
                from backend.modules.daily_research.application.quant_pipeline import (
                    run_daily_quant,
                )

                with self._bundles.open() as bundle:
                    run_daily_quant(
                        bundle,
                        claimed=claimed,
                        workflow=workflow,
                        execution_control=control,
                        market_dsn=self._market_dsn,
                        quant_preflight=self._quant_preflight,
                        on_progress=self._on_progress,
                        stock_research=lambda ticker, context: self._run_daily_stock_candidate(
                            claimed, ticker, context,
                        ),
                        persist_artifact=lambda report, artifact: self._persist_artifact(
                            claimed, {"daily_research": report}, artifact
                        ) if self._artifact_store is not None else artifact,
                    )
            else:
                raise ValueError(f"每日研究 kind 非法：{workflow.get('kind')}")
        except CooperativeCancelledError:
            control.terminate_all()
            return
        except ExecutionInactiveError:
            control.terminate_all()
            try:
                with self._bundles.open() as bundle:
                    bundle.tasks.mark_cancelled(
                        claimed.task_id, claimed.attempt_no, claimed.lease_token
                    )
            except LeaseConflictError:
                logger.info("每日研究已取消或失租：task=%s", claimed.task_id)
        except (LeaseConflictError, FencingLostError, ArtifactFencingError):
            control.terminate_all()
            logger.warning("每日研究提交被 fencing 拒绝：task=%s", claimed.task_id)
        except Exception as exc:
            control.terminate_all()
            self._fail_or_retry(claimed, exc)

    def _run_daily_news_pipeline(self, claimed: ClaimedTask, workflow: dict[str, Any]) -> None:
        from backend.modules.daily_research.application.news_pipeline import (
            build_market_research_context,
            commit_news_run,
            market_trade_date_for_cutoff,
            prepare_news_run,
        )

        with self._bundles.open() as bundle:
            prepared = prepare_news_run(
                bundle.uow.session,
                task_id=str(claimed.task_id),
                cutoff_at=(workflow.get("news_cutoff_at") or
                           (workflow.get("scheduled_at") if workflow.get("trigger") == "scheduled" else None)),
                on_progress=self._on_progress,
                max_news=self._max_news_per_run,
            )
        market_state = None
        market_error = None
        market_trade_date = market_trade_date_for_cutoff(prepared.cutoff_at)
        try:
            self._on_progress("市场与板块 Agent 研判", 0, 1)
            workflow_with_context = dict(workflow)
            workflow_with_context["research_context"] = build_market_research_context(prepared)
            graph_task = replace(
                claimed,
                selected_layers=("market", "sector"),
                effective_trade_date=market_trade_date,
                request_params={"daily_research": workflow_with_context},
            )
            market_state = self._adapter.execute(
                graph_task, self._on_progress, daily_research=True,
            )
            self._on_progress("市场与板块 Agent 研判", 1, 1)
        except (CooperativeCancelledError, ExecutionInactiveError,
                LeaseConflictError, FencingLostError, ArtifactFencingError):
            raise
        except Exception as exc:  # preserve validated event work; publish partial market section
            market_error = f"{type(exc).__name__}: {str(exc)[:500]}"
            logger.exception("每日市场/板块 Agent 失败，新闻判断将以 partial 状态提交")

        from db.instrument.db import get_connection

        outcome_connection = None
        market_outcome_error = None
        try:
            outcome_connection = get_connection(self._market_dsn)
        except Exception as exc:
            market_outcome_error = f"{type(exc).__name__}: {str(exc)[:300]}"
            logger.warning("每日事件实际价格验证暂不可用", exc_info=True)
        try:
            with self._bundles.open() as bundle:
                commit_news_run(
                    bundle,
                    prepared=prepared,
                    claimed=claimed,
                    market_agent_state=market_state,
                    market_agent_error=market_error,
                    market_trade_date=market_trade_date,
                    market_conn=outcome_connection,
                    market_outcome_error=market_outcome_error,
                    persist_artifact=lambda report, artifact: self._persist_artifact(
                        claimed, {"daily_research": report}, artifact
                    ) if self._artifact_store is not None else artifact,
                )
        finally:
            if outcome_connection is not None:
                try:
                    outcome_connection.close()
                except Exception:
                    logger.debug("每日事件价格连接关闭失败", exc_info=True)

    def _run_daily_stock_candidate(
        self,
        claimed: ClaimedTask,
        ticker: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Run the existing stock-agent chain on the quant batch's frozen inputs."""
        trade_date = date.fromisoformat(str(context["market_as_of_trade_date"]))
        stock_task = replace(
            claimed,
            ticker=ticker,
            selected_layers=("stock",),
            effective_trade_date=trade_date,
            request_params={"daily_research": {
                "kind": "quant",
                "trigger": "daily_stock_candidate",
                "stock_candidate": True,
                "research_context": context,
            }},
        )
        state = self._adapter.execute(
            stock_task, self._on_progress, daily_research=True,
        )
        debate = state.get("risk_debate_state") or {}
        return {
            "status": "completed",
            "market_as_of_trade_date": trade_date.isoformat(),
            "event_cutoff_at": context.get("cutoff_at"),
            "risk_gate": context.get("risk_gate"),
            "event_count": context.get("event_count", 0),
            "events": context.get("events", []),
            "decision": state.get("signal"),
            "decision_text": state.get("final_trade_decision"),
            "investment_plan": state.get("investment_plan"),
            "risk_judgement": debate.get("judge_decision") if isinstance(debate, dict) else None,
            "reports": {
                "stock_tech": state.get("stock_tech_report"),
                "news": state.get("news_report"),
                "fundamentals": state.get("fundamentals_report"),
                "sentiment": state.get("sentiment_report"),
                "investment_plan": state.get("investment_plan"),
                "trader_plan": state.get("trader_investment_plan"),
            },
        }

    # ---- 量化执行分支（plan 4.3.1：不经 LangGraph，Worker 直接执行） ----

    def _run_quant(self, claimed: ClaimedTask, heartbeat: LeaseHeartbeat) -> None:
        snapshot = claimed.request_params.get("execution_snapshot") or {}
        forbidden_source_code = (snapshot.get("strategy") or {}).get("source_code")
        control: ExecutionControl | None = None
        try:
            from db.instrument.db import get_connection

            with get_connection(self._market_dsn) as market_conn:
                with self._bundles.open() as bundle:
                    control = ExecutionControl(
                        claimed.task_id,
                        claimed.lease_token,
                        is_cancelled=lambda: self._cancel_flag,
                        is_fencing_active=lambda: heartbeat.fencing_lost.is_set(),
                    )
                    from backend.modules.quant_strategy.application.execution import (
                        QuantExecutionService,
                    )

                    service = QuantExecutionService(
                        task_id=claimed.task_id,
                        attempt_no=claimed.attempt_no,
                        snapshot=snapshot,
                        market_conn=market_conn,
                        session=bundle.uow.session,
                        execution_control=control,
                        effective_trade_date=claimed.effective_trade_date or date.today(),
                        on_progress=self._on_progress,
                    )
                    summary = service.run()

                    # 同选 AI 层（market/sector/screening/stock）时 AI 层照旧独立运行
                    # （plan 4.2.1/决策 7：量化不读取其产物，两者互不影响；报告合并两者结果）
                    ai_layers = set(claimed.selected_layers or ()) - {"position"}
                    if ai_layers:
                        ai_state = self._adapter.execute(claimed, self._on_progress, rerun_from=None)
                        final_state = _as_state_dict(ai_state)
                        final_state["selected_layers"] = list(claimed.selected_layers)
                        final_state["quant_execution"] = summary
                    else:
                        final_state = {
                            "selected_layers": list(claimed.selected_layers),
                            "quant_execution": summary,
                        }
                    # 序列化边界防泄漏：完整摘要不得承载源码/敏感键/完整散列
                    assert_no_source_leak(
                        final_state, forbidden_source_code, context="quant-final-state"
                    )
                    artifact = bundle.build_artifact(final_state)
                    if self._artifact_store is not None:
                        artifact = self._persist_artifact(
                            claimed, final_state, artifact, forbidden_source_code=forbidden_source_code
                        )
                    bundle.tasks.complete_task(
                        claimed.task_id, claimed.attempt_no, claimed.lease_token, artifact, bundle.reports
                    )
        except CooperativeCancelledError:
            if control is not None:
                control.terminate_all()
            return  # 回调中已完成 mark_cancelled
        except ExecutionInactiveError:
            logger.info("量化执行因取消/失租停止：task=%s attempt=%s", claimed.task_id, claimed.attempt_no)
            if control is not None:
                control.terminate_all()
            try:
                with self._bundles.open() as bundle:
                    bundle.tasks.mark_cancelled(claimed.task_id, claimed.attempt_no, claimed.lease_token)
            except LeaseConflictError:
                logger.warning("取消收口被 fencing 拒绝：task=%s", claimed.task_id)
        except (LeaseConflictError, FencingLostError, ArtifactFencingError):
            if control is not None:
                control.terminate_all()
            logger.warning("量化完成被 fencing 拒绝：task=%s attempt=%s", claimed.task_id, claimed.attempt_no)
        except Exception as exc:
            if control is not None:
                control.terminate_all()
            self._fail_or_retry(claimed, exc)

    # ---- 内部流程 ----

    def _run_graph(self, claimed: ClaimedTask, rerun_from: str | None = None) -> None:
        try:
            final_state = self._adapter.execute(claimed, self._on_progress, rerun_from=rerun_from)
        except CooperativeCancelledError:
            return  # 回调中已完成 mark_cancelled
        except FencingLostError:
            logger.warning("fencing 丢失，放弃写入：task=%s attempt=%s", claimed.task_id, claimed.attempt_no)
            return
        except LeaseConflictError:
            logger.warning("租约冲突，放弃写入（迟到 attempt）：task=%s attempt=%s", claimed.task_id, claimed.attempt_no)
            return
        except Exception as exc:
            self._fail_or_retry(claimed, exc)
            return

        if self._cancel_flag:
            # 图已返回但取消标志已置位（未在回调中收口）：持有租约收口 CANCELLED
            try:
                with self._bundles.open() as bundle:
                    bundle.tasks.mark_cancelled(claimed.task_id, claimed.attempt_no, claimed.lease_token)
            except LeaseConflictError:
                logger.warning("取消收口被 fencing 拒绝：task=%s attempt=%s", claimed.task_id, claimed.attempt_no)
            return

        try:
            with self._bundles.open() as bundle:
                # 图返回后复核 DB 状态（取消请求可能在最后一次 progress 之后到达，回调已无机会检测）
                task_orm = bundle.uow.tasks.get(claimed.task_id)
                if task_orm is None:
                    raise FencingLostError()
                if task_orm.status == TaskStatus.CANCEL_REQUESTED.value:
                    self._cancel_flag = True
                if self._cancel_flag:
                    bundle.tasks.mark_cancelled(claimed.task_id, claimed.attempt_no, claimed.lease_token)
                    return
                artifact = bundle.build_artifact(final_state)
                if self._artifact_store is not None:
                    artifact = self._persist_artifact(claimed, final_state, artifact)
                bundle.tasks.complete_task(
                    claimed.task_id, claimed.attempt_no, claimed.lease_token, artifact, bundle.reports
                )
        except (LeaseConflictError, FencingLostError):
            logger.warning("完成被 fencing 拒绝（迟到 attempt）：task=%s attempt=%s", claimed.task_id, claimed.attempt_no)
        except ArtifactFencingError:
            # 最终目录已存在且不一致：过期 attempt 不得覆盖，直接放弃写入（reconciliation 兜底）
            logger.warning("产物发布 fencing 冲突，放弃本次 attempt：task=%s attempt=%s", claimed.task_id, claimed.attempt_no)
        except Exception as exc:
            self._fail_or_retry(claimed, exc)

    def _persist_artifact(
        self,
        claimed: ClaimedTask,
        final_state: Any,
        artifact: AnalysisArtifact,
        *,
        forbidden_source_code: str | None = None,
    ) -> AnalysisArtifact:
        """staging 校验 → manifest → 同卷 os.replace 发布；返回带受控引用与 checksum 的 artifact。"""
        uri, checksum = self._artifact_store.persist_artifact(
            task_id=claimed.task_id,
            attempt_no=claimed.attempt_no,
            lease_token=claimed.lease_token,
            core_version=self._core_version,
            final_state=final_state if isinstance(final_state, dict) else {},
            report_json=artifact.report_json,
            forbidden_source_code=forbidden_source_code,
        )
        return AnalysisArtifact(
            report_json=artifact.report_json,
            conclusion_summary=artifact.conclusion_summary,
            risk_flag=artifact.risk_flag,
            risk_hint=artifact.risk_hint,
            decision=artifact.decision,
            artifact_uri=uri,
            checksum=checksum,
            duration_ms=artifact.duration_ms,
        )

    def _fail_or_retry(self, claimed: ClaimedTask, exc: Exception) -> None:
        logger.exception("分析执行失败，进入 fail_or_retry：task=%s attempt=%s", claimed.task_id, claimed.attempt_no)
        classified = classify_error(exc)
        try:
            with self._bundles.open() as bundle:
                bundle.tasks.fail_or_retry(claimed.task_id, claimed.attempt_no, claimed.lease_token, classified)
        except LeaseConflictError:
            logger.warning("失败/重试被 fencing 拒绝：task=%s attempt=%s", claimed.task_id, claimed.attempt_no)

    def _on_progress(self, stage: str, done: int | None = None, total: int | None = None) -> None:
        """阶段边界：先检查取消标志与 fencing，再发布 progress（单一独立 Session）。

        图路径按单参 message 调用；量化扫描按 (stage, done, total) 三参调用——统一归一为消息文本。
        """
        message = stage if done is None else f"{stage} {done}/{total}"
        claimed = self._claimed
        if self._cancel_flag:
            return
        self._sequence += 1
        with self._bundles.open() as bundle:
            task = bundle.uow.tasks.get(claimed.task_id)  # ORM 实体（含 lease_token）
            if task is None:
                raise FencingLostError()
            if task.status == TaskStatus.CANCEL_REQUESTED.value:
                self._cancel_flag = True
                bundle.tasks.mark_cancelled(claimed.task_id, claimed.attempt_no, claimed.lease_token)
                raise CooperativeCancelledError()
            if task.status != TaskStatus.RUNNING.value or task.lease_token != claimed.lease_token:
                raise FencingLostError()
            bundle.events.publish(
                claimed.task_id,
                TaskEventType.PROGRESS,
                attempt_no=claimed.attempt_no,
                sequence=self._sequence,
                phase=self._phase_extractor(message),
                message=message,
            )
