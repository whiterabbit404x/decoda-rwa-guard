"""Canonical active-incident query for Screen 2 (Dashboard / Executive Summary).

This is the **single source of truth** for "which incidents are active" in a
workspace. Every Screen 2 surface reads its incident facts from here so they can
never disagree with one another:

  * the Open Incidents metric card,
  * the critical/high active-incident subtitle,
  * the Executive Brief incident aggregates,
  * the Risk Score incident-pressure contribution,
  * the Top Risk Drivers,
  * the persisted dashboard snapshot.

Definition (operator-visible, matching the Incidents list endpoint semantics):

    An incident is *active* when it belongs to the current workspace and its
    lifecycle status is NOT one of the terminal / hidden states
    (resolved, closed, archived, suppressed, cancelled, deleted).

This is a **lifecycle-status** definition, not a proof-chain / evidence-quality
definition. An active incident whose alert -> detection -> telemetry proof chain
is incomplete still counts as active — an operator must still see it. Proof-chain
completeness is surfaced *separately* as an evidence-quality signal
(:func:`services.api.app.dashboard_summary` folds it into ``data_confidence`` and
an evidence-quality warning); it must never be used to hide an incident from the
count. Using the proof-chain-gated count as the "open incidents" number is
exactly the bug this module exists to prevent: it made the Open Incidents card
read 0 while the severity subtitle and Risk Score still reflected 4 real active
incidents.

The exclusion is intentionally fail-closed: any status not in the terminal set
(``open``, ``acknowledged``, ``investigating``, ``contained``, ``reopened`` and
any future/unknown active status) counts as active, so a new workflow state can
never silently drop an incident out of the customer-facing count.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

# Terminal / hidden incident lifecycle statuses. An incident in any of these is
# NOT active. Everything else (open, acknowledged, investigating, contained,
# reopened, and any unknown status) is treated as active — fail-closed.
TERMINAL_INCIDENT_STATUSES: tuple[str, ...] = (
    'resolved',
    'closed',
    'archived',
    'suppressed',
    'cancelled',
    'canceled',
    'deleted',
)

_CRITICAL = {'critical', 'crit', 'sev1', 'p1'}
_HIGH = {'high', 'sev2', 'p2'}
_MEDIUM = {'medium', 'moderate', 'med', 'sev3', 'p3'}
_LOW = {'low', 'info', 'informational', 'sev4', 'p4'}


def normalize_incident_severity(value: Any) -> str:
    """Normalize a raw severity to one of critical | high | medium | low.

    Mirrors the risk scorer's severity normalization so the count of
    critical/high incidents in the subtitle can never disagree with the
    incident-pressure contribution computed from the same records.
    """
    text = str(value or '').strip().lower()
    if text in _CRITICAL:
        return 'critical'
    if text in _HIGH:
        return 'high'
    if text in _LOW:
        return 'low'
    if text in _MEDIUM:
        return 'medium'
    return 'medium'


def active_incident_where_sql(status_column: str = 'status', *, workspace_param: bool = True) -> str:
    """Return the canonical WHERE clause fragment for active incidents.

    Exposed so any other query that needs the *same* active-incident definition
    can reuse the exact filter instead of re-deriving a subtly different one.
    ``status_column`` lets callers alias the incidents table.
    """
    placeholders = ', '.join(['%s'] * len(TERMINAL_INCIDENT_STATUSES))
    clause = f"LOWER(COALESCE({status_column}, 'open')) NOT IN ({placeholders})"
    if workspace_param:
        return f"workspace_id = %s AND {clause}"
    return clause


@dataclass(frozen=True)
class ActiveIncident:
    id: str
    severity: str  # normalized: critical | high | medium | low
    status: str
    created_at: str | None = None


@dataclass(frozen=True)
class ActiveIncidentSummary:
    """Derived, consistent counts for all Screen 2 incident surfaces."""

    incidents: list[ActiveIncident] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.incidents)

    @property
    def severities(self) -> list[str]:
        return [i.severity for i in self.incidents]

    @property
    def critical_high_count(self) -> int:
        return sum(1 for i in self.incidents if i.severity in {'critical', 'high'})

    @property
    def critical_count(self) -> int:
        return sum(1 for i in self.incidents if i.severity == 'critical')


def summarize_active_incidents(rows: Iterable[Mapping[str, Any]]) -> ActiveIncidentSummary:
    """Pure: fold raw incident rows into a consistent summary (DB-free, testable).

    Rows are expected to already be filtered to active incidents by the query
    (:func:`fetch_active_incidents`); as a defensive second layer this also drops
    any row whose status is terminal, so the summary is correct even if a caller
    hands it an unfiltered list.
    """
    incidents: list[ActiveIncident] = []
    for row in rows or []:
        status = str(row.get('status') or 'open').strip().lower()
        if status in TERMINAL_INCIDENT_STATUSES:
            continue
        incidents.append(
            ActiveIncident(
                id=str(row.get('id') or ''),
                severity=normalize_incident_severity(row.get('severity')),
                status=status,
                created_at=_iso(row.get('created_at')),
            )
        )
    return ActiveIncidentSummary(incidents=incidents)


def fetch_active_incidents(connection: Any, workspace_id: str) -> ActiveIncidentSummary:
    """Run the one canonical active-incident query, workspace-scoped.

    Optional-table tolerant: any DB failure degrades to an empty (fail-open for
    reads) summary rather than raising, matching the dashboard's posture, but the
    filter itself is fail-closed on status so no active incident is hidden.
    """
    placeholders = ', '.join(['%s'] * len(TERMINAL_INCIDENT_STATUSES))
    sql = (
        "SELECT id, severity, status, created_at FROM incidents "
        f"WHERE workspace_id = %s AND LOWER(COALESCE(status, 'open')) NOT IN ({placeholders}) "
        "ORDER BY created_at DESC"
    )
    params = (workspace_id, *TERMINAL_INCIDENT_STATUSES)
    try:
        result = connection.execute(sql, params).fetchall()
        rows = [dict(r) for r in result] if result else []
    except Exception:
        rows = []
    return summarize_active_incidents(rows)


def _iso(value: Any) -> str | None:
    from datetime import datetime, timezone

    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    text = str(value)
    return text or None
