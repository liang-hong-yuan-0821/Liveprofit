"""Read-only local replay work list from already verified revision inputs."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from .lifecycle_persisted_resolved_accounting import load_local_resolved_accounting
from .lifecycle_persisted_revision_impact import load_local_lifecycle_revision_impact
from .lifecycle_replay_inventory import inventory_lifecycle_replay
from .lifecycle_revision_impact_surface import load_local_lifecycle_revision_impact_surface
from .lifecycle_revision_replay_manifest import (
    RevisionReplayManifest, build_revision_replay_manifest,
)


def load_local_revision_replay_manifest(
    session, *, portfolio_id: UUID, lifecycle_id: UUID,
    baseline_as_of: datetime, baseline_quantity: Decimal,
    baseline_total_cost: Decimal, baseline_source_ref: str,
) -> RevisionReplayManifest:
    """Join 3am, 3an and 3ao under one caller-owned transaction and scope.

    The returned old-version bounds are historical identifiers only. No
    projection, 0048 step, day fact, or intent is written by this adapter.
    """
    def unknown(*issues: str) -> RevisionReplayManifest:
        return RevisionReplayManifest(
            "UNKNOWN", "UNKNOWN", None, (), (), (), (), tuple(issues),
        )

    if (not isinstance(portfolio_id, UUID)
            or not isinstance(lifecycle_id, UUID)
            or session.new or session.dirty or session.deleted):
        return unknown("REVISION_MANIFEST_SCOPE_OR_SESSION_INVALID")
    impact = load_local_lifecycle_revision_impact(
        session, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id)
    if impact.status == "UNKNOWN":
        return unknown(*impact.issues)
    surface = load_local_lifecycle_revision_impact_surface(
        session, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id)
    if surface.status == "UNKNOWN":
        return unknown(*impact.issues, *surface.issues)
    accounting = load_local_resolved_accounting(
        session, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id,
        baseline_as_of=baseline_as_of, baseline_quantity=baseline_quantity,
        baseline_total_cost=baseline_total_cost,
        baseline_source_ref=baseline_source_ref)
    if accounting.status == "UNKNOWN":
        return unknown(*impact.issues, *surface.issues, *accounting.issues)
    try:
        inventory = inventory_lifecycle_replay(session, portfolio_id, lifecycle_id)
    except ValueError as exc:
        return unknown(*impact.issues, *surface.issues, *accounting.issues,
                       f"REVISION_MANIFEST_INVENTORY_UNAVAILABLE:{exc}")
    if (inventory.initial_fill_id is None
            or inventory.ambiguous_fill_event_ids):
        return unknown(*impact.issues, *surface.issues, *accounting.issues,
                       *inventory.issues, "REVISION_MANIFEST_INITIAL_ANCHOR_UNKNOWN")
    if session.new or session.dirty or session.deleted:
        return unknown(*impact.issues, *surface.issues, *accounting.issues,
                       "REVISION_MANIFEST_SESSION_CHANGED")
    return build_revision_replay_manifest(
        impact=impact, surface=surface, accounting=accounting,
        initial_fill_id=inventory.initial_fill_id)
