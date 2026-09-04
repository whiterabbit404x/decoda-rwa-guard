"""Screen 7 — the incident queue KPI counters.

These tests pin the two properties that make the four tiles above the Incidents
table trustworthy:

  1. **One definition of "open".** The counters use the SAME lifecycle definition
     as Screen 2's Dashboard (``dashboard_active_incidents.TERMINAL_INCIDENT_STATUSES``),
     so the Open Incidents card and the Open Incidents tile can never show two
     different numbers for one workspace. An incident being investigated, awaiting
     a response, or carrying a status this build has never seen is OPEN — only a
     terminal status closes it.
  2. **The whole workspace, not a page.** The count is produced by one grouped,
     workspace-scoped query rather than by folding the page of rows a browser
     happens to be holding, which is capped and already narrowed by the list
     filters.

The truthfulness invariants (see CLAUDE.md):
  * a resolved incident is never counted as open, and a resolved CRITICAL incident
    is never counted as a critical incident — a closed case is not live risk;
  * an unknown/new workflow status counts as open (fail-closed), so no incident
    can silently vanish from the customer-facing count;
  * no incident from another workspace is ever counted.
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest

from services.api.app import incident_queue_summary as q
from services.api.app.dashboard_active_incidents import TERMINAL_INCIDENT_STATUSES

WS_ID = str(uuid.uuid4())
OTHER_WS_ID = str(uuid.uuid4())


# ==========================================================================
# 1. Lifecycle status resolution
# ==========================================================================
def test_workflow_status_is_the_canonical_column():
    assert q.lifecycle_status({'workflow_status': 'Investigating', 'status': 'open'}) == 'investigating'


def test_status_is_used_when_workflow_status_is_absent():
    assert q.lifecycle_status({'status': 'CONTAINED'}) == 'contained'


def test_an_incident_with_no_recorded_status_defaults_to_open():
    # Fail-closed: an unlabelled incident is shown, never dropped.
    assert q.lifecycle_status({}) == 'open'
    assert q.lifecycle_status({'workflow_status': '   '}) == 'open'


@pytest.mark.parametrize('terminal', TERMINAL_INCIDENT_STATUSES)
def test_every_terminal_status_closes_an_incident(terminal):
    assert q.is_open(terminal) is False


@pytest.mark.parametrize('active', ['open', 'investigating', 'contained', 'reopened',
                                    'awaiting_response', 'response_initiated'])
def test_every_active_workflow_status_stays_open(active):
    assert q.is_open(active) is True


def test_an_unknown_future_status_counts_as_open():
    # A workflow state this build has never seen must not silently drop an
    # incident out of the operator-facing count.
    assert q.is_open('quarantined_pending_legal') is True


# ==========================================================================
# 2. The four counters (pure fold)
# ==========================================================================
def test_open_counts_every_non_terminal_incident_not_just_new_ones():
    # The bug this pins: counting only 'open'/'reopened' reported 1 open incident
    # while three more were being actively investigated.
    rows = [
        {'workflow_status': 'open', 'severity': 'high'},
        {'workflow_status': 'investigating', 'severity': 'high'},
        {'workflow_status': 'contained', 'severity': 'high'},
        {'workflow_status': 'reopened', 'severity': 'high'},
    ]
    summary = q.summarize_incident_queue(rows)
    assert summary.open_incidents == 4


def test_resolved_and_closed_incidents_are_not_open():
    rows = [
        {'workflow_status': 'resolved', 'severity': 'critical'},
        {'workflow_status': 'closed', 'severity': 'critical'},
        {'workflow_status': 'open', 'severity': 'low'},
    ]
    summary = q.summarize_incident_queue(rows)
    assert summary.open_incidents == 1
    assert summary.total == 3


def test_a_resolved_critical_incident_is_not_a_critical_incident():
    # A closed case is not live risk. Counting it would overstate the workspace's
    # critical exposure on the customer-facing KPI row.
    rows = [
        {'workflow_status': 'resolved', 'severity': 'critical'},
        {'workflow_status': 'investigating', 'severity': 'critical'},
    ]
    summary = q.summarize_incident_queue(rows)
    assert summary.critical_incidents == 1


def test_in_investigation_counts_only_the_investigating_status():
    rows = [
        {'workflow_status': 'investigating', 'severity': 'medium'},
        {'workflow_status': 'investigating', 'severity': 'medium'},
        {'workflow_status': 'open', 'severity': 'medium'},
        {'workflow_status': 'contained', 'severity': 'medium'},
    ]
    assert q.summarize_incident_queue(rows).in_investigation == 2


def test_awaiting_response_covers_the_canonical_and_legacy_spellings():
    # 'contained' is the legacy persisted value the status pill already renders
    # as "Awaiting Response"; both must land in the same tile.
    rows = [
        {'workflow_status': 'awaiting_response', 'severity': 'high'},
        {'workflow_status': 'contained', 'severity': 'high'},
        {'workflow_status': 'investigating', 'severity': 'high'},
    ]
    assert q.summarize_incident_queue(rows).awaiting_response == 2


def test_a_resolved_incident_never_lands_in_the_investigating_or_awaiting_tiles():
    rows = [
        {'workflow_status': 'resolved', 'severity': 'high'},
        {'status': 'closed', 'severity': 'high'},
    ]
    summary = q.summarize_incident_queue(rows)
    assert (summary.in_investigation, summary.awaiting_response, summary.open_incidents) == (0, 0, 0)


def test_severity_normalization_matches_the_dashboard_scorer():
    # 'sev1'/'p1' are the same risk the dashboard calls critical; an unknown
    # severity is NOT promoted to critical.
    rows = [
        {'workflow_status': 'open', 'severity': 'sev1'},
        {'workflow_status': 'open', 'severity': 'P1'},
        {'workflow_status': 'open', 'severity': 'wat'},
        {'workflow_status': 'open', 'severity': None},
    ]
    assert q.summarize_incident_queue(rows).critical_incidents == 2


def test_grouped_rows_are_folded_by_their_count():
    rows = [
        {'lifecycle_status': 'open', 'severity': 'critical', 'count': 3},
        {'lifecycle_status': 'investigating', 'severity': 'low', 'count': 2},
        {'lifecycle_status': 'resolved', 'severity': 'critical', 'count': 7},
    ]
    summary = q.summarize_incident_queue(rows)
    assert summary.as_dict() == {
        'open_incidents': 5,
        'critical_incidents': 3,
        'in_investigation': 2,
        'awaiting_response': 0,
        'total': 12,
    }


def test_an_empty_workspace_reports_zeros_not_a_missing_key():
    assert q.summarize_incident_queue([]).as_dict() == {
        'open_incidents': 0, 'critical_incidents': 0, 'in_investigation': 0,
        'awaiting_response': 0, 'total': 0,
    }


def test_a_malformed_count_falls_back_to_one_incident():
    assert q.summarize_incident_queue([{'lifecycle_status': 'open', 'count': 'x'}]).open_incidents == 1


def test_the_fold_is_deterministic():
    rows = [
        {'lifecycle_status': 'open', 'severity': 'critical', 'count': 2},
        {'lifecycle_status': 'contained', 'severity': 'high', 'count': 1},
    ]
    assert q.summarize_incident_queue(rows) == q.summarize_incident_queue(rows)


# ==========================================================================
# 3. The query: workspace-scoped, one round trip, not page-limited
# ==========================================================================
class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]]):
        self._rows = rows

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows


class _FakeConnection:
    """Returns only the rows belonging to the workspace the query asked for."""

    def __init__(self, rows_by_workspace: dict[str, list[dict[str, Any]]]):
        self._rows = rows_by_workspace
        self.statements: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, statement: str, params: tuple[Any, ...]) -> _FakeResult:
        self.statements.append((statement, params))
        return _FakeResult(list(self._rows.get(params[0], [])))


def test_only_the_requested_workspace_is_counted():
    connection = _FakeConnection({
        WS_ID: [{'lifecycle_status': 'open', 'severity': 'critical', 'count': 2}],
        OTHER_WS_ID: [{'lifecycle_status': 'open', 'severity': 'critical', 'count': 99}],
    })
    summary = q.fetch_incident_queue_summary(connection, WS_ID)
    assert summary.open_incidents == 2
    assert summary.critical_incidents == 2


def test_the_query_is_workspace_filtered_grouped_and_unpaged():
    connection = _FakeConnection({WS_ID: []})
    q.fetch_incident_queue_summary(connection, WS_ID)
    statement, params = connection.statements[0]
    normalized = ' '.join(statement.split())
    assert 'FROM incidents' in normalized
    assert 'WHERE workspace_id = %s' in normalized
    assert 'GROUP BY' in normalized
    # A KPI must cover the workspace: a LIMIT here would silently report a page.
    assert 'LIMIT' not in normalized.upper()
    assert params == (WS_ID,)


def test_one_round_trip_per_request():
    connection = _FakeConnection({WS_ID: []})
    q.fetch_incident_queue_summary(connection, WS_ID)
    assert len(connection.statements) == 1


def test_a_read_failure_is_not_swallowed_into_zeros():
    # A tile showing 0 is a claim ("nothing is open"). A failed read must raise so
    # the UI can say "unavailable" instead of rendering that claim.
    class _Broken:
        def execute(self, *_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError('connection reset')

    with pytest.raises(RuntimeError):
        q.fetch_incident_queue_summary(_Broken(), WS_ID)


# ==========================================================================
# 4. Route wiring
# ==========================================================================
def test_summary_route_is_declared_before_the_incident_detail_route():
    """'/incidents/summary' must not be swallowed by '/incidents/{incident_id}'.

    FastAPI matches routes in declaration order, and both paths have one segment
    after /incidents, so the literal route only wins while it is declared first.
    """
    from services.api.app.main import app

    paths = [getattr(route, 'path', '') for route in app.routes]
    assert '/incidents/summary' in paths
    assert '/incidents/{incident_id}' in paths
    assert paths.index('/incidents/summary') < paths.index('/incidents/{incident_id}')


def test_counter_definitions_cover_every_returned_counter():
    # Every tile's number ships with the definition behind it, so a label and its
    # semantics can never drift apart.
    counters = set(q.IncidentQueueSummary().as_dict()) - {'total'}
    assert counters == set(q.COUNTER_DEFINITIONS)
