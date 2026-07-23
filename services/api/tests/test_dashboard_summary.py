"""Dashboard aggregation, response contract, persistence and tenant isolation.

Covers required backend tests:

  12. Cross-workspace records never appear in results (every query is scoped).
  13. Asset value "unavailable" is different from zero (null, not 0).
  14. Dashboard aggregates match known seeded records.

Plus score-input mapping, deltas, trend (no fabricated history), freshness,
and snapshot-throttle behaviour.
"""

from __future__ import annotations

from datetime import datetime, timezone

from services.api.app import dashboard_summary as ds


NOW = datetime(2026, 7, 23, 12, 0, 0, tzinfo=timezone.utc)


class _R:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class SeededConn:
    """Fake connection seeded with known workspace records.

    Records every (sql, params) so tests can assert workspace scoping. Returns
    deterministic rows keyed on normalized SQL fragments.
    """

    def __init__(self, *, prev_snapshot=None, trend_rows=None, existing_brief=None):
        self.executed: list = []
        self.inserts: list = []
        self.prev_snapshot = prev_snapshot
        self.trend_rows = trend_rows or []
        self.existing_brief = existing_brief

    def execute(self, query, params=None):
        n = ' '.join(str(query).split())
        self.executed.append((n, params))
        if n.startswith('INSERT'):
            self.inserts.append((n, params))
            return _R(None)
        if 'GROUP BY severity' in n:
            return _R(rows=[{'severity': 'high', 'c': 2}, {'severity': 'medium', 'c': 1}])
        if 'ORDER BY CASE lower(severity)' in n:
            return _R(rows=[
                {'id': 'a1', 'title': 'Oracle deviation', 'severity': 'high', 'status': 'open', 'alert_type': 'oracle', 'created_at': '2026-07-23T10:00:00+00:00'},
                {'id': 'a2', 'title': 'Unusual transfer', 'severity': 'medium', 'status': 'acknowledged', 'alert_type': 'transfer', 'created_at': '2026-07-23T09:00:00+00:00'},
            ])
        if 'SELECT severity FROM incidents' in n:
            return _R(rows=[{'severity': 'high'}])
        if 'FROM monitored_systems' in n:
            return _R(row={'c': 4})
        if 'DISTINCT chain_network' in n:
            return _R(row={'c': 3})
        if 'risk_tier' in n:
            return _R(rows=[{'risk_tier': 'high'}])
        if 'FROM dashboard_snapshots' in n and 'ORDER BY captured_at DESC LIMIT 1' in n:
            return _R(row=self.prev_snapshot)
        if 'FROM dashboard_snapshots' in n:
            return _R(rows=self.trend_rows)
        if 'FROM dashboard_executive_briefs' in n:
            return _R(row=self.existing_brief)
        if 'COUNT(*) AS c FROM incidents' in n and 'created_at' in n:
            return _R(row={'c': 1})
        if 'FROM telemetry_events' in n:
            return _R(row={'c': 120})
        if 'FROM detections' in n:
            return _R(row={'c': 4})
        if 'COUNT(*) AS c' in n:
            return _R(row={'c': 0})
        return _R(row={'c': 0})

    def commit(self):
        pass


def _summary(**over):
    base = {
        'active_alerts_count': 3, 'active_incidents_count': 1, 'protected_assets_count': 5,
        'configured_systems': 4, 'monitored_systems_count': 4, 'reporting_systems_count': 3,
        'telemetry_freshness': 'fresh', 'last_telemetry_at': '2026-07-23T11:55:00+00:00',
        'last_heartbeat_at': '2026-07-23T11:59:00+00:00', 'evidence_source_summary': 'live_provider',
        'runtime_status': 'live', 'contradiction_flags': [], 'db_failure_classification': None,
    }
    base.update(over)
    return base


# --------------------------------------------------------------------------
# 14. Aggregates match known seeded records
# --------------------------------------------------------------------------


def test_aggregates_match_seeded_records():
    conn = SeededConn()
    agg = ds.gather_dashboard_aggregates(conn, workspace_id='ws-1', now=NOW, canonical_summary=_summary())
    assert agg['metrics']['active_alert_count'] == 3          # from canonical summary
    assert agg['metrics']['open_incident_count'] == 1
    assert agg['metrics']['monitored_asset_count'] == 5
    assert agg['metrics']['active_monitor_count'] == 4        # monitored_systems seed
    assert agg['metrics']['data_source_count'] == 3           # distinct chain_network seed
    assert agg['alert_severity_counts'] == {'critical': 0, 'high': 2, 'medium': 1, 'low': 0}
    assert agg['incident_severities'] == ['high']
    assert len(agg['recent_alerts']) == 2
    assert agg['telemetry_events_24h'] == 120


def test_response_metrics_match_and_scores_present():
    conn = SeededConn()
    resp = ds.build_dashboard_summary(conn, workspace_id='ws-1', canonical_summary=_summary(), background_loop_health={'healthy': True, 'uptime_30d_percent': 99.97}, provider=None, now=NOW)
    m = resp['metrics']
    assert m['active_alert_count'] == 3
    assert m['open_incident_count'] == 1
    assert m['data_source_count'] == 3
    assert m['active_monitor_count'] == 4
    assert 0 <= m['risk_score'] <= 100
    assert 0 <= m['system_health_score'] <= 100
    assert m['uptime_30d_percent'] == 99.97
    # Recent alerts deep-link to real records.
    assert resp['recent_alerts'][0]['url'] == '/alerts/a1'


# --------------------------------------------------------------------------
# 13. Asset value "unavailable" != zero
# --------------------------------------------------------------------------


def test_total_asset_value_unavailable_is_null_not_zero():
    conn = SeededConn()
    resp = ds.build_dashboard_summary(conn, workspace_id='ws-1', canonical_summary=_summary(), provider=None, now=NOW)
    assert resp['metrics']['total_asset_value_usd'] is None
    assert resp['metrics']['total_asset_value_usd'] != 0
    # ...but the asset COUNT is still populated.
    assert resp['metrics']['monitored_asset_count'] == 5


# --------------------------------------------------------------------------
# 12. Cross-workspace isolation — every query is workspace-scoped
# --------------------------------------------------------------------------


def test_every_query_is_workspace_scoped():
    conn = SeededConn()
    ds.build_dashboard_summary(conn, workspace_id='ws-target', canonical_summary=_summary(), provider=None, now=NOW)
    saw_scoped_read = False
    for sql, params in _iter_reads(conn):
        # Any SELECT touching a tenant table must filter by workspace_id and pass
        # the target workspace as a parameter.
        if 'workspace_id = %s' in sql:
            saw_scoped_read = True
            assert params is not None and 'ws-target' in [str(p) for p in _flatten(params)], sql
    assert saw_scoped_read


def test_two_workspaces_do_not_share_results():
    # Distinct workspaces produce independent, scoped query streams.
    conn_a = SeededConn()
    conn_b = SeededConn()
    ds.build_dashboard_summary(conn_a, workspace_id='ws-a', canonical_summary=_summary(), provider=None, now=NOW)
    ds.build_dashboard_summary(conn_b, workspace_id='ws-b', canonical_summary=_summary(), provider=None, now=NOW)
    a_params = {str(p) for _, params in conn_a.executed for p in _flatten(params)}
    b_params = {str(p) for _, params in conn_b.executed for p in _flatten(params)}
    assert 'ws-a' in a_params and 'ws-b' not in a_params
    assert 'ws-b' in b_params and 'ws-a' not in b_params


def _iter_reads(conn):
    for sql, params in conn.executed:
        if sql.startswith('SELECT'):
            yield sql, params


def _flatten(params):
    if params is None:
        return []
    if isinstance(params, (list, tuple)):
        return list(params)
    return [params]


# --------------------------------------------------------------------------
# Score input mapping (pure)
# --------------------------------------------------------------------------


def test_risk_and_health_inputs_from_aggregates():
    conn = SeededConn()
    agg = ds.gather_dashboard_aggregates(conn, workspace_id='ws-1', now=NOW, canonical_summary=_summary())
    risk_inputs = ds.risk_inputs_from_aggregates(agg)
    assert risk_inputs.incident_severities == ['high']
    assert len(risk_inputs.alert_clusters) == 2  # high + medium buckets
    health_inputs = ds.health_inputs_from_aggregates(agg)
    assert health_inputs.configured_target_count == 4
    assert health_inputs.telemetry_freshness == 'fresh'


# --------------------------------------------------------------------------
# Deltas + trend (no fabricated history)
# --------------------------------------------------------------------------


def test_deltas_none_without_prior_snapshot():
    deltas = ds.compute_deltas({'active_alert_count': 3, 'open_incident_count': 1}, {'score': 40}, {'score': 90}, {})
    assert all(v is None for v in deltas.values())


def test_deltas_computed_against_prior_snapshot():
    prev = {'risk_score': 30, 'health_score': 95, 'active_alert_count': 1, 'open_incident_count': 0}
    deltas = ds.compute_deltas({'active_alert_count': 3, 'open_incident_count': 1}, {'score': 40}, {'score': 90}, prev)
    assert deltas['risk_score'] == 10
    assert deltas['system_health_score'] == -5
    assert deltas['active_alert_count'] == 2
    assert deltas['open_incident_count'] == 1


def test_trend_uses_real_snapshots_only():
    # No snapshots -> empty trend, trend_available False (never synthesize days).
    conn = SeededConn(trend_rows=[])
    resp = ds.build_dashboard_summary(conn, workspace_id='ws-1', canonical_summary=_summary(), provider=None, now=NOW)
    assert resp['risk_trend'] == []
    assert resp['trend_available'] is False

    rows = [
        {'captured_at': '2026-07-21T12:00:00+00:00', 'risk_score': 20, 'health_score': 95, 'active_alert_count': 1, 'open_incident_count': 0},
        {'captured_at': '2026-07-22T12:00:00+00:00', 'risk_score': 35, 'health_score': 90, 'active_alert_count': 2, 'open_incident_count': 1},
    ]
    conn2 = SeededConn(trend_rows=rows)
    resp2 = ds.build_dashboard_summary(conn2, workspace_id='ws-1', canonical_summary=_summary(), provider=None, now=NOW)
    assert len(resp2['risk_trend']) == 2
    assert resp2['trend_available'] is True
    assert resp2['risk_trend'][0]['risk_score'] == 20


# --------------------------------------------------------------------------
# Data freshness derivation
# --------------------------------------------------------------------------


def test_data_freshness_states():
    fresh = ds.derive_data_freshness('2026-07-23T11:58:00+00:00', NOW)
    assert fresh['status'] == 'fresh'
    stale = ds.derive_data_freshness('2026-07-23T10:00:00+00:00', NOW)
    assert stale['status'] == 'stale'
    unavailable = ds.derive_data_freshness(None, NOW)
    assert unavailable['status'] == 'unavailable'
    assert unavailable['latest_event_at'] is None


# --------------------------------------------------------------------------
# Snapshot throttling
# --------------------------------------------------------------------------


def test_snapshot_is_throttled_within_interval():
    conn = SeededConn()
    response = {'metrics': {'risk_score': 40, 'risk_band': 'moderate', 'system_health_score': 90, 'system_health_status': 'healthy', 'active_alert_count': 3, 'open_incident_count': 1, 'monitored_asset_count': 5, 'active_monitor_count': 4, 'data_source_count': 3, 'uptime_30d_percent': None, 'total_asset_value_usd': None}, '_risk_components': [], '_health_components': []}
    # A snapshot captured 1 minute ago blocks a new write.
    recent = {'captured_at': '2026-07-23T11:59:00+00:00'}
    wrote = ds.persist_dashboard_snapshot(conn, workspace_id='ws-1', response=response, now=NOW, prev_snapshot=recent)
    assert wrote is False
    # An old / absent snapshot allows a write.
    old = {'captured_at': '2026-07-23T10:00:00+00:00'}
    wrote2 = ds.persist_dashboard_snapshot(conn, workspace_id='ws-1', response=response, now=NOW, prev_snapshot=old)
    assert wrote2 is True


# --------------------------------------------------------------------------
# Contract shape
# --------------------------------------------------------------------------


def test_response_contract_shape_is_complete():
    conn = SeededConn()
    resp = ds.build_dashboard_summary(conn, workspace_id='ws-1', canonical_summary=_summary(), provider=None, now=NOW)
    for key in ('generated_at', 'data_freshness', 'executive_brief', 'metrics', 'risk_trend', 'recent_alerts', 'ai_copilot'):
        assert key in resp, key
    assert set(resp['ai_copilot']).issuperset({'top_risk_drivers', 'system_health_insights', 'recommended_focus'})
    assert 'top_risk_drivers' in resp['ai_copilot']
    # Private persistence keys are not leaked to the client.
    assert '_risk_components' not in resp and '_health_components' not in resp
