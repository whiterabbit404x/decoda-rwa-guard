"""Canonical active-incident query + its consistent reuse across Screen 2.

These are the regression tests for the Open Incidents / subtitle / Risk Score /
Executive Brief inconsistency: one canonical active-incident definition feeds
every surface, so the count, the critical/high subtitle, the incident-pressure
risk contribution, and the brief can never disagree again. Proof-chain
completeness is proven to be a *separate* evidence-quality signal that never
hides an active incident from the count.
"""

from __future__ import annotations

from datetime import datetime, timezone

from services.api.app import dashboard_active_incidents as ai
from services.api.app import dashboard_summary as ds
from services.api.app.dashboard_scoring import compute_risk_score


NOW = datetime(2026, 7, 23, 12, 0, 0, tzinfo=timezone.utc)


class _R:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class IncidentConn:
    """Fake connection whose incidents table returns configurable rows.

    ``incident_rows`` are the rows the DB would return for the canonical active
    query (i.e. already lifecycle-filtered by SQL). ``proof_chain_count`` is the
    proof-chain-gated number the canonical monitoring summary would report.
    """

    def __init__(self, incident_rows=None):
        self.incident_rows = incident_rows if incident_rows is not None else []
        self.executed: list = []

    def execute(self, query, params=None):
        n = ' '.join(str(query).split())
        self.executed.append((n, params))
        if n.startswith('INSERT'):
            return _R(None)
        if 'FROM incidents' in n and 'NOT IN' in n:
            return _R(rows=self.incident_rows)
        if 'GROUP BY severity' in n:
            return _R(rows=[])
        if 'ORDER BY CASE lower(severity)' in n:
            return _R(rows=[])
        if 'DISTINCT chain_network' in n:
            return _R(row={'c': 1})
        if 'FROM monitored_systems' in n:
            return _R(row={'c': 2})
        if 'FROM dashboard_snapshots' in n:
            return _R(row=None, rows=[])
        if 'FROM dashboard_executive_briefs' in n:
            return _R(row=None)
        return _R(row={'c': 0}, rows=[])

    def commit(self):
        pass


def _summary(**over):
    base = {
        'active_alerts_count': 0, 'active_incidents_count': 0, 'protected_assets_count': 3,
        'configured_systems': 2, 'monitored_systems_count': 2, 'reporting_systems_count': 2,
        'telemetry_freshness': 'fresh', 'last_telemetry_at': '2026-07-23T11:55:00+00:00',
        'last_heartbeat_at': '2026-07-23T11:59:00+00:00', 'evidence_source_summary': 'live_provider',
        'runtime_status': 'live', 'contradiction_flags': [], 'db_failure_classification': None,
    }
    base.update(over)
    return base


def _rows(*severities, status='open'):
    return [
        {'id': f'i{i}', 'severity': sev, 'status': status, 'created_at': '2026-07-23T08:00:00+00:00'}
        for i, sev in enumerate(severities)
    ]


# --------------------------------------------------------------------------
# Pure summary folding
# --------------------------------------------------------------------------


def test_summarize_counts_are_consistent():
    summary = ai.summarize_active_incidents(_rows('critical', 'high', 'high', 'medium'))
    assert summary.total == 4
    assert summary.critical_high_count == 3          # 1 critical + 2 high
    assert summary.critical_count == 1
    assert summary.severities == ['critical', 'high', 'high', 'medium']


def test_summarize_excludes_terminal_statuses_defensively():
    rows = _rows('critical') + _rows('high', status='resolved') + _rows('high', status='closed')
    summary = ai.summarize_active_incidents(rows)
    # Only the one active (open) critical survives; resolved/closed dropped.
    assert summary.total == 1
    assert summary.severities == ['critical']


def test_severity_normalization_matches_scorer_buckets():
    summary = ai.summarize_active_incidents(_rows('SEV1', 'P2', 'moderate', 'informational'))
    assert summary.severities == ['critical', 'high', 'medium', 'low']


# --------------------------------------------------------------------------
# Canonical query: workspace scoping + status exclusion in SQL
# --------------------------------------------------------------------------


def test_fetch_active_incidents_is_workspace_scoped_and_excludes_terminal():
    conn = IncidentConn(incident_rows=_rows('critical', 'high'))
    summary = ai.fetch_active_incidents(conn, 'ws-1')
    assert summary.total == 2
    sql, params = conn.executed[-1]
    assert 'workspace_id = %s' in sql
    assert 'NOT IN' in sql
    assert params[0] == 'ws-1'
    # Every terminal status is excluded in the SQL parameters.
    for terminal in ai.TERMINAL_INCIDENT_STATUSES:
        assert terminal in params


def test_fetch_active_incidents_fails_open_on_db_error():
    class Boom:
        def execute(self, *_a, **_k):
            raise RuntimeError('table missing')

    summary = ai.fetch_active_incidents(Boom(), 'ws-1')
    assert summary.total == 0


# --------------------------------------------------------------------------
# Integration through gather_dashboard_aggregates: one source, every surface
# --------------------------------------------------------------------------


def test_four_active_incidents_flow_to_count_subtitle_and_risk():
    conn = IncidentConn(incident_rows=_rows('critical', 'high', 'high', 'medium'))
    agg = ds.gather_dashboard_aggregates(conn, workspace_id='ws-1', now=NOW, canonical_summary=_summary())

    # Open Incidents card
    assert agg['metrics']['open_incident_count'] == 4
    # critical/high subtitle drawn from the SAME records
    assert agg['incidents_critical_high'] == 3
    # Risk incident pressure is derived from the SAME severities and is > 0
    assert agg['incident_severities'] == ['critical', 'high', 'high', 'medium']
    risk = compute_risk_score(ds.risk_inputs_from_aggregates(agg))
    incident_component = next(c for c in risk.components if c.key == 'incident_pressure')
    assert incident_component.points > 0


def test_executive_brief_uses_canonical_active_count_not_proof_chain():
    # Proof chain says 0 (gated), but 4 incidents are actually active.
    conn = IncidentConn(incident_rows=_rows('critical', 'high', 'high', 'medium'))
    resp = ds.build_dashboard_summary(
        conn, workspace_id='ws-1', canonical_summary=_summary(active_incidents_count=0),
        provider=None, now=NOW,
    )
    assert resp['metrics']['open_incident_count'] == 4
    # Deterministic brief must NOT claim there are no open incidents.
    headline = resp['executive_brief']['headline'].lower()
    assert 'no open incident' not in headline
    assert '4 open incident' in headline


def test_zero_active_incidents_zero_incident_risk_contribution():
    conn = IncidentConn(incident_rows=[])
    agg = ds.gather_dashboard_aggregates(conn, workspace_id='ws-1', now=NOW, canonical_summary=_summary())
    assert agg['metrics']['open_incident_count'] == 0
    assert agg['incidents_critical_high'] == 0
    risk = compute_risk_score(ds.risk_inputs_from_aggregates(agg))
    incident_component = next(c for c in risk.components if c.key == 'incident_pressure')
    assert incident_component.points == 0
    # And incident pressure is not a listed risk driver when it is zero.
    assert all(d['key'] != 'incident_pressure' for d in risk.top_risk_drivers)


def test_resolving_the_incident_removes_the_active_contribution():
    # Before: one critical active incident contributes risk.
    before = ds.gather_dashboard_aggregates(
        IncidentConn(incident_rows=_rows('critical')), workspace_id='ws-1', now=NOW, canonical_summary=_summary())
    risk_before = compute_risk_score(ds.risk_inputs_from_aggregates(before))
    pts_before = next(c for c in risk_before.components if c.key == 'incident_pressure').points
    assert before['metrics']['open_incident_count'] == 1
    assert pts_before > 0

    # After: the incident is resolved -> excluded by the canonical query.
    after = ds.gather_dashboard_aggregates(
        IncidentConn(incident_rows=[]), workspace_id='ws-1', now=NOW, canonical_summary=_summary())
    risk_after = compute_risk_score(ds.risk_inputs_from_aggregates(after))
    pts_after = next(c for c in risk_after.components if c.key == 'incident_pressure').points
    assert after['metrics']['open_incident_count'] == 0
    assert pts_after == 0


# --------------------------------------------------------------------------
# Proof-chain completeness is a SEPARATE evidence-quality signal
# --------------------------------------------------------------------------


def test_incomplete_proof_chain_still_counts_but_lowers_confidence():
    # 4 active incidents, but only 1 is backed by a complete proof chain.
    conn = IncidentConn(incident_rows=_rows('critical', 'high', 'high', 'medium'))
    agg = ds.gather_dashboard_aggregates(
        conn, workspace_id='ws-1', now=NOW, canonical_summary=_summary(active_incidents_count=1))
    # Count is the operator-visible 4, never gated down to 1.
    assert agg['metrics']['open_incident_count'] == 4
    assert agg['evidence_incomplete_incident_count'] == 3

    agg['risk'] = compute_risk_score(ds.risk_inputs_from_aggregates(agg)).to_dict()
    fresh = ds.derive_data_freshness(agg.get('last_telemetry_at'), NOW)
    confidence = ds.derive_data_confidence(agg, fresh)
    # Incomplete proof chain lowers confidence even though telemetry is fresh.
    assert confidence['level'] == 'low'


def test_complete_proof_chain_keeps_high_confidence():
    conn = IncidentConn(incident_rows=_rows('critical'))
    agg = ds.gather_dashboard_aggregates(
        conn, workspace_id='ws-1', now=NOW, canonical_summary=_summary(active_incidents_count=1))
    assert agg['evidence_incomplete_incident_count'] == 0
    agg['risk'] = compute_risk_score(ds.risk_inputs_from_aggregates(agg)).to_dict()
    fresh = ds.derive_data_freshness(agg.get('last_telemetry_at'), NOW)
    confidence = ds.derive_data_confidence(agg, fresh)
    # Fresh telemetry, healthy workers/providers, complete evidence, baseline
    # available -> high (or at least never 'low' from incident evidence).
    assert confidence['level'] in {'high', 'medium'}


# --------------------------------------------------------------------------
# Workspace isolation: one workspace's incidents cannot affect another's score
# --------------------------------------------------------------------------


def test_workspace_isolation_incident_query_scoped():
    conn_a = IncidentConn(incident_rows=_rows('critical', 'critical'))
    conn_b = IncidentConn(incident_rows=[])
    ds.build_dashboard_summary(conn_a, workspace_id='ws-a', canonical_summary=_summary(), provider=None, now=NOW)
    ds.build_dashboard_summary(conn_b, workspace_id='ws-b', canonical_summary=_summary(), provider=None, now=NOW)

    # ws-a's incident query carries ws-a and never ws-b.
    incident_reads_a = [(s, p) for s, p in conn_a.executed if 'FROM incidents' in s and 'NOT IN' in s]
    assert incident_reads_a
    for _sql, params in incident_reads_a:
        assert params[0] == 'ws-a'
        assert 'ws-b' not in [str(x) for x in params]

    # ws-b (no active incidents) scores zero incident pressure independently.
    agg_b = ds.gather_dashboard_aggregates(conn_b, workspace_id='ws-b', now=NOW, canonical_summary=_summary())
    risk_b = compute_risk_score(ds.risk_inputs_from_aggregates(agg_b))
    assert next(c for c in risk_b.components if c.key == 'incident_pressure').points == 0
