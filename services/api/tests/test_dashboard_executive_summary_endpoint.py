"""Endpoint wiring for GET /ops/dashboard/executive-summary.

Exercises the real FastAPI handler in main.py with mocked persistence + auth to
prove it authenticates, scopes to the resolved workspace, and returns the full
Screen 2 contract with a deterministic brief when AI is disabled.
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

from services.api.app import main


class _R:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self):
        self.executed = []

    def execute(self, query, params=None):
        n = ' '.join(str(query).split())
        self.executed.append((n, params))
        if 'GROUP BY severity' in n:
            return _R(rows=[{'severity': 'high', 'c': 1}])
        if 'ORDER BY CASE lower(severity)' in n:
            return _R(rows=[{'id': 'a1', 'title': 'Oracle deviation', 'severity': 'high', 'status': 'open', 'alert_type': 'oracle', 'created_at': '2026-07-23T10:00:00+00:00'}])
        if 'SELECT severity FROM incidents' in n:
            return _R(rows=[])
        if 'FROM monitored_systems' in n:
            return _R(row={'c': 2})
        if 'DISTINCT chain_network' in n:
            return _R(row={'c': 2})
        if 'ORDER BY captured_at DESC LIMIT 1' in n:
            return _R(row=None)
        if 'FROM dashboard_snapshots' in n:
            return _R(rows=[])
        if 'FROM dashboard_executive_briefs' in n:
            return _R(row=None)
        return _R(row={'c': 0})

    def commit(self):
        pass


def _bootstrap(monkeypatch, *, workspace_id='ws-1'):
    @contextmanager
    def _pg():
        yield _Conn()

    monkeypatch.setattr(main, 'monitoring_runtime_status', lambda request: {
        'workspace_monitoring_summary': {
            'active_alerts_count': 1, 'active_incidents_count': 0, 'protected_assets_count': 2,
            'configured_systems': 2, 'monitored_systems_count': 2, 'reporting_systems_count': 2,
            'telemetry_freshness': 'fresh', 'last_telemetry_at': '2026-07-23T11:55:00+00:00',
            'last_heartbeat_at': '2026-07-23T11:59:00+00:00', 'evidence_source_summary': 'live_provider',
            'runtime_status': 'live', 'contradiction_flags': [], 'db_failure_classification': None,
        },
        'background_loop_health': {'healthy': True, 'uptime_30d_percent': 99.9},
    })
    monkeypatch.setattr(main, 'pg_connection', _pg)
    monkeypatch.setattr(main, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(main, 'authenticate_with_connection', lambda *a, **k: {'id': 'user-1'})
    monkeypatch.setattr(main, 'resolve_workspace', lambda *a, **k: {'workspace_id': workspace_id})
    # AI disabled -> offline provider -> deterministic brief.
    monkeypatch.setattr(main.ai_triage, 'triage_config', lambda: {'enabled': False, 'provider': '', 'model': ''})


def _req(workspace_id='ws-1'):
    return SimpleNamespace(headers={'x-workspace-id': workspace_id}, client=SimpleNamespace(host='127.0.0.1'), method='GET')


def test_endpoint_returns_full_contract(monkeypatch):
    _bootstrap(monkeypatch)
    resp = main.ops_dashboard_executive_summary(_req())
    assert set(resp).issuperset({'generated_at', 'data_freshness', 'executive_brief', 'metrics', 'risk_trend', 'recent_alerts', 'ai_copilot'})
    assert resp['metrics']['active_alert_count'] == 1
    assert resp['metrics']['total_asset_value_usd'] is None
    assert resp['metrics']['data_source_count'] == 2
    assert resp['executive_brief']['generation_mode'] == 'deterministic_fallback'
    assert 0 <= resp['metrics']['risk_score'] <= 100
    assert resp['data_freshness']['status'] == 'fresh'
    # Deep-linked recent alert.
    assert resp['recent_alerts'][0]['url'] == '/alerts/a1'


def test_endpoint_scopes_queries_to_resolved_workspace(monkeypatch):
    _bootstrap(monkeypatch, workspace_id='ws-target')
    captured = {}

    @contextmanager
    def _pg():
        conn = _Conn()
        captured['conn'] = conn
        yield conn

    monkeypatch.setattr(main, 'pg_connection', _pg)
    main.ops_dashboard_executive_summary(_req('ws-target'))
    conn = captured['conn']
    scoped = [(sql, params) for sql, params in conn.executed if sql.startswith('SELECT') and 'workspace_id = %s' in sql]
    assert scoped, 'expected workspace-scoped reads'
    for sql, params in scoped:
        flat = list(params) if isinstance(params, (list, tuple)) else [params]
        assert 'ws-target' in [str(p) for p in flat], sql
