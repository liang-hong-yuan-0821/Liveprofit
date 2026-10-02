# test-catalog-begin
# {
#   "purpose": "投资工作区 / fill_evidence（成交）：Raw evidence is immutable, bounded and tied to one portfolio.",
#   "keywords": [
#     "投资工作区",
#     "成交",
#     "重放",
#     "fill_evidence",
#     "fill",
#     "replay"
#   ],
#   "covers": [
#     "backend/modules/investment_workspace/application/fill_evidence.py",
#     "backend/modules/investment_workspace/infrastructure/models.py"
#   ],
#   "environment": [
#     "db"
#   ]
# }
# test-catalog-end

"""Raw evidence is immutable, bounded and tied to one portfolio."""

import hashlib
from decimal import Decimal
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from backend.modules.investment_workspace.application.fill_evidence import (
    AccountFillEvidenceService, MAX_EVIDENCE_BYTES,
)
from backend.modules.investment_workspace.infrastructure.models import Portfolio


def test_raw_evidence_capture_replay_and_content_verification(env):
    factory = env["session_factory"]
    portfolio_id, other_portfolio = uuid.uuid4(), uuid.uuid4()
    raw = b"broker statement fixture bytes"
    digest = hashlib.sha256(raw).hexdigest()
    with factory() as session:
        session.add_all([
            Portfolio(id=portfolio_id, name=f"evidence-{portfolio_id}",
                      version=1, total_assets=Decimal(1000), available_cash=Decimal(1000)),
            Portfolio(id=other_portfolio, name=f"evidence-{other_portfolio}",
                      version=1, total_assets=Decimal(1000), available_cash=Decimal(1000)),
        ])
        session.commit()
    with factory() as session:
        store = AccountFillEvidenceService(session)
        with pytest.raises(ValueError, match="bounded nonempty"):
            store.capture(portfolio_id, source_ref="empty", media_type="text/plain", raw_bytes=b"")
        with pytest.raises(ValueError, match="bounded nonempty"):
            store.capture(portfolio_id, source_ref="oversize", media_type="text/plain",
                          raw_bytes=b"x" * (MAX_EVIDENCE_BYTES + 1))
        artifact, created = store.capture(
            portfolio_id, source_ref="statement-1", media_type="text/plain", raw_bytes=raw)
        assert created and artifact.content_sha256 == digest
        replay, created = store.capture(
            portfolio_id, source_ref="statement-1", media_type="text/plain", raw_bytes=raw)
        assert not created and replay.id == artifact.id
        assert store.require_content(portfolio_id, digest).id == artifact.id
        with pytest.raises(ValueError, match="changed content"):
            store.capture(portfolio_id, source_ref="statement-1", media_type="text/plain",
                          raw_bytes=b"changed")
        with pytest.raises(ValueError, match="another source reference"):
            store.capture(portfolio_id, source_ref="statement-2", media_type="text/plain",
                          raw_bytes=raw)
        with pytest.raises(ValueError, match="bytes are missing"):
            store.require_content(other_portfolio, digest)
        session.commit()
        artifact_id = artifact.id
    with factory() as session:
        with pytest.raises(DBAPIError, match="fill evidence history is immutable"):
            session.execute(text("UPDATE account_fill_evidence_artifacts SET raw_bytes=:raw WHERE id=:id"),
                            {"raw": b"changed", "id": artifact_id})
        session.rollback()
