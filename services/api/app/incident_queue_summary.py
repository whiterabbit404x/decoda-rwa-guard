"""Screen 7 — canonical counters for the incident queue KPI row.

This module is the single source of truth for the four numbers above the
Incidents table (Open Incidents / Critical Incidents / In Investigation /
Awaiting Response). It exists because those tiles previously counted the page of
rows the browser happened to be holding — at most one ``limit`` page, and already
narrowed by whatever severity/status filter the operator had typed. A KPI derived
from a filtered page is not a KPI; it silently reports a subset as the whole.

Two rules keep these numbers honest:

**One definition of "open".** "Open" is the *lifecycle* definition Screen 2's
Dashboard already uses — :data:`dashboard_active_incidents.TERMINAL_INCIDENT_STATUSES`
— so the Open Incidents card and the Screen 7 Open Incidents tile can never show
two different numbers for the same workspace. An incident is open unless its
recorded status is terminal (resolved / closed / archived / suppressed /
cancelled / deleted). Anything else — including a status this build has never
seen — counts as open, so a new workflow state can never silently drop an
incident out of the customer-facing count.

**Whole workspace, always.** The counters are computed by the database over every
incident in the workspace, independent of paging and of the list filters. A tile
labelled "Open Incidents" means the workspace's open incidents.

Semantics, stated once (the API returns these verbatim in ``definitions`` so the
UI never has to restate them from memory):

    open_incidents      lifecycle status is not terminal
    critical_incidents  open AND severity normalizes to critical
    in_investigation    open AND lifecycle status is 'investigating'
    awaiting_response   open AND the incident sits at the approval-required
                        response stage ('awaiting_response', or its legacy
                        persisted spelling 'contained')

No new status model is introduced: every value above is one the incident
workflow already persists.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from services.api.app import pilot
from services.api.app.dashboard_active_incidents import (
    TERMINAL_INCIDENT_STATUSES,
    normalize_incident_severity,
)

#: Lifecycle status meaning "an investigator is actively working the case".
IN_INVESTIGATION_STATUSES: frozenset[str] = frozenset({'investigating'})

#: Lifecycle statuses meaning "the case is waiting on a response decision".
#: ``awaiting_response`` is the canonical value; ``contained`` is the legacy
#: persisted spelling the status pill already renders as "Awaiting Response".
#: Both are existing workflow states — neither is invented for this tile.
AWAITING_RESPONSE_STATUSES: frozenset[str] = frozenset({'awaiting_response', 'contained'})

#: Machine-readable semantics, returned with the counts so the operator-facing
#: labels and the numbers behind them can never drift apart.
COUNTER_DEFINITIONS: dict[str, str] = {
    'open_incidents': 'Lifecycle status is not terminal (not resolved/closed/archived/suppressed/cancelled/deleted).',
    'critical_incidents': 'Open, and severity normalizes to critical.',
    'in_investigation': "Open, and lifecycle status is 'investigating'.",
    'awaiting_response': 'Open, and awaiting a response decision (awaiting_response / contained).',
}


def lifecycle_status(row: Mapping[str, Any]) -> str:
    """The incident's operator-visible lifecycle status, lowercased.

    ``workflow_status`` is the canonical column and ``status`` is its mirror;
    every writer sets them together. Falling back through both — and defaulting
    to ``open`` when neither is recorded — matches what the Incidents list, its
    status filter, and the status pill already display, so the tile can never
    disagree with the row beneath it.
    """
    raw = row.get('lifecycle_status')
    if raw is None:
        raw = row.get('workflow_status') or row.get('status')
    text = str(raw or '').strip().lower()
    return text or 'open'


def is_open(status: str) -> bool:
    """True when the lifecycle status is not terminal. Fail-closed on unknowns."""
    return status not in TERMINAL_INCIDENT_STATUSES


@dataclass(frozen=True)
class IncidentQueueSummary:
    """The four queue counters, plus the workspace total they were drawn from."""

    open_incidents: int = 0
    critical_incidents: int = 0
    in_investigation: int = 0
    awaiting_response: int = 0
    total: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            'open_incidents': self.open_incidents,
            'critical_incidents': self.critical_incidents,
            'in_investigation': self.in_investigation,
            'awaiting_response': self.awaiting_response,
            'total': self.total,
        }


def summarize_incident_queue(rows: Iterable[Mapping[str, Any]]) -> IncidentQueueSummary:
    """Fold incident rows into the four canonical counters. Pure, DB-free.

    Accepts either raw incident rows (one per incident) or pre-grouped buckets
    carrying a ``count``; a row without a ``count`` counts as one incident. That
    lets the database group by (status, severity) — a bounded result — while the
    definitions above stay in one testable place.
    """
    open_incidents = critical = investigating = awaiting = total = 0
    for row in rows:
        try:
            count = int(row.get('count') if row.get('count') is not None else 1)
        except (TypeError, ValueError):
            count = 1
        if count <= 0:
            continue
        total += count
        status = lifecycle_status(row)
        if not is_open(status):
            continue
        open_incidents += count
        if normalize_incident_severity(row.get('severity')) == 'critical':
            critical += count
        if status in IN_INVESTIGATION_STATUSES:
            investigating += count
        if status in AWAITING_RESPONSE_STATUSES:
            awaiting += count
    return IncidentQueueSummary(
        open_incidents=open_incidents,
        critical_incidents=critical,
        in_investigation=investigating,
        awaiting_response=awaiting,
        total=total,
    )


def fetch_incident_queue_summary(connection: Any, workspace_id: str) -> IncidentQueueSummary:
    """Run the one workspace-scoped counting query and fold it.

    Grouping in SQL keeps the result bounded by the number of
    (status, severity) combinations rather than by the number of incidents, so a
    workspace with thousands of cases still costs one small round trip. The
    counting rules themselves stay in :func:`summarize_incident_queue`.

    Read failures are NOT swallowed: a tile with no data must say so rather than
    render a zero the operator would read as "nothing is open".
    """
    rows = connection.execute(
        '''
        SELECT LOWER(COALESCE(workflow_status, status, 'open')) AS lifecycle_status,
               LOWER(COALESCE(severity, '')) AS severity,
               COUNT(*)::int AS count
        FROM incidents
        WHERE workspace_id = %s
        GROUP BY 1, 2
        ''',
        (workspace_id,),
    ).fetchall()
    return summarize_incident_queue([dict(row) for row in (rows or [])])


def get_incident_queue_summary(request: Any) -> dict[str, Any]:
    """``GET /incidents/summary`` — workspace-scoped queue counters.

    Read-only. Returns the counts, the scope they were computed over, and the
    definition of each counter, so the UI renders canonical backend facts instead
    of re-deriving a second (and inevitably different) definition of "open".
    """
    pilot.require_live_mode()
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        user = pilot.authenticate_with_connection(connection, request)
        workspace_context = pilot.resolve_workspace(
            connection, user['id'], request.headers.get('x-workspace-id'),
        )
        workspace_id = workspace_context['workspace_id']
        summary = fetch_incident_queue_summary(connection, workspace_id)
        return {
            'workspace_id': workspace_id,
            # The counters cover every incident in the workspace — never the
            # page or the filter the list happens to be showing.
            'scope': 'workspace',
            'counts': summary.as_dict(),
            'definitions': dict(COUNTER_DEFINITIONS),
        }
