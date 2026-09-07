"""ReportService：报告读写、看板摘要投影、版本查询（§3.1.3）。

- ReportService 是 analysis_reports 的唯一写入口；save_version 事务由调用方控制。
- 保存版本时从结构化 artifact 明确投影 conclusion_summary/risk_flag/risk_hint/has_report
  与 has_unavailable_sections 并落库，看板接口只读（§5.1 P-5）。
- 首期只读取最新成功版本，不接受版本选择参数，不向 API 泄露内部 State。
"""

from __future__ import annotations

import json

import uuid

from backend.modules.analysis.application.contracts import AnalysisArtifact, ReportDTO
from backend.modules.analysis.application.errors import ReportNotFoundError
from backend.modules.analysis.domain.ports import Clock, SystemClock
from backend.modules.analysis.infrastructure.models import AnalysisReport
from backend.shared.ids import new_uuid

REPORT_SCHEMA_VERSION = "v1"


class ReportService:
    def __init__(self, *, clock: Clock | None = None) -> None:
        self._clock = clock or SystemClock()

    def save_version(
        self, uow, task_id: uuid.UUID, attempt_no: int, artifact: AnalysisArtifact
    ) -> ReportDTO:
        """保存报告版本（不 commit，事务由调用方控制）；版本递增，task_id + report_version 唯一。"""
        now = self._clock.now()
        version = uow.reports.next_version(task_id)
        report_json = artifact.report_json or {}
        # 写入边界防御：归一为纯 JSON 基本类型（LangChain 对象 → str），
        # 与 artifact_builder 的 decision round-trip 同一约定（psycopg JSONB dump 无 default 兜底）
        report_json = json.loads(json.dumps(report_json, ensure_ascii=False, default=str))
        sections = report_json.get("sections") or []
        has_unavailable = any(
            isinstance(s, dict) and s.get("status") == "UNAVAILABLE" for s in sections
        )
        has_report = bool(report_json)
        report = AnalysisReport(
            id=new_uuid(),
            task_id=task_id,
            attempt_no=attempt_no,
            report_version=version,
            schema_version=REPORT_SCHEMA_VERSION,
            report_json=report_json,
            conclusion_summary=artifact.conclusion_summary,
            risk_flag=artifact.risk_flag,
            risk_hint=artifact.risk_hint,
            has_report=has_report,
            has_unavailable_sections=has_unavailable,
            decision=artifact.decision,
            artifact_uri=artifact.artifact_uri,
            checksum=artifact.checksum,
            generated_at=now,
            created_at=now,
            updated_at=now,
        )
        uow.reports.add(report)
        return self._to_dto(report)

    def get_latest_report(self, uow, task_id: uuid.UUID) -> ReportDTO:
        report = uow.reports.get_latest(task_id)
        if report is None:
            raise ReportNotFoundError(f"任务 {task_id} 暂无报告")
        return self._to_dto(report)

    @staticmethod
    def _to_dto(report: AnalysisReport) -> ReportDTO:
        return ReportDTO(
            report_id=report.id,
            task_id=report.task_id,
            attempt_no=report.attempt_no,
            report_version=report.report_version,
            schema_version=report.schema_version,
            generated_at=report.generated_at,
            report_json=report.report_json or {},
            conclusion_summary=report.conclusion_summary,
            risk_flag=bool(report.risk_flag),
            risk_hint=report.risk_hint,
            has_report=bool(report.has_report),
        )
