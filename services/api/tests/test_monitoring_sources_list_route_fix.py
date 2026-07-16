"""
Screen 4 list-route root-cause regression tests.

Production evidence (workspace Datto) showed the runtime-status path seeing the canonical
monitored system (runtime_rows=1) while the list route saw nothing (list_route_rows=0), so
the page fell through to "Unable to load" + a hardcoded US-Treasury asset CTA.

Root cause: `monitored_systems.is_enabled` (migration 0039) is absent under production schema
drift. The list query named `ms.is_enabled` and re-raised on the missing column, while the
runtime raw loader tolerated it. These tests pin the fix: the list route must return a
configured monitored system regardless of which optional/runtime columns a drifted schema is
missing, before its first heartbeat/telemetry, scoped to the workspace, with USDC identity.
"""
from __future__ import annotations

from contextlib import contextmanager

from fastapi.testclient import TestClient

from services.api.app import main as api_main
from services.api.app import pilot


# --- Datto production identifiers (from the runtime evidence) -----------------------------
WS = '4fffd3f9-d55f-456f-8a7e-8b9ed2083721'
SYS = '1c02c1c0-30e3-4fcc-b648-0e8e65439be6'
TARGET = '9c6ecabb-cd52-404f-9859-40567b09dbb4'
ASSET = '92142dec-002a-451d-8e5a-3cd3f05f065a'


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


def _usdc_ms_json(**overrides):
    base = {
        'id': SYS,
        'workspace_id': WS,
        'asset_id': ASSET,
        'target_id': TARGET,
        'chain': 'base',
        'runtime_status': None,
        'status': None,
        'last_event_at': None,
        'last_coverage_telemetry_at': None,
        'freshness_status': None,
        'confidence_status': None,
        'coverage_reason': None,
        'last_error_text': None,
    }
    base.update(overrides)
    return base


class _DriftConn:
    """Simulates production schema drift: the flat list query names ms.is_enabled which does
    not exist, so it raises; the to_jsonb() fallback returns the canonical row."""

    def __init__(self, *, ms_json=None, drop_is_enabled=True):
        self.queries: list[str] = []
        self.params: list[tuple] = []
        self._ms_json = ms_json if ms_json is not None else _usdc_ms_json()
        self._drop_is_enabled = drop_is_enabled

    def execute(self, query, params=None):
        q = ' '.join(str(query).split())
        self.queries.append(q)
        self.params.append(tuple(params or ()))
        is_fallback = 'to_jsonb(ms)' in q
        if not is_fallback and 'ms.is_enabled' in q and self._drop_is_enabled:
            raise RuntimeError('UndefinedColumn: column ms.is_enabled does not exist')
        if is_fallback:
            return _Result([{
                'id': SYS, 'workspace_id': WS, 'asset_id': ASSET, 'target_id': TARGET,
                'chain': 'base', 'last_heartbeat': None, 'created_at': '2026-07-16T00:00:00+00:00',
                'ms_json': self._ms_json,
                'monitoring_interval_seconds': 30,
                'asset_name': 'USDC', 'target_name': 'Base USDC monitor',
            }])
        return _Result([])


# --- 1 / 2 / 3 / 13: configured system stays visible before first telemetry, on drift ------
def test_list_route_returns_configured_system_before_first_heartbeat_under_drift():
    conn = _DriftConn()
    rows = pilot.list_workspace_monitored_system_rows(conn, WS)
    assert len(rows) == 1, 'is_enabled drift must not drop the canonical monitored system'
    row = rows[0]
    assert row['id'] == SYS
    assert row['target_id'] == TARGET
    assert row['asset_id'] == ASSET
    # No heartbeat / no telemetry yet — still listed (provisioning, not absent).
    assert row['last_heartbeat'] is None
    assert row['last_event_at'] is None
    assert row['runtime_status'] is None
    # Missing is_enabled column defaults to enabled so a configured system is never filtered out.
    assert row['is_enabled'] is True
    assert pilot.monitored_system_row_enabled(row) is True


def test_list_route_scopes_query_to_the_workspace():
    conn = _DriftConn()
    pilot.list_workspace_monitored_system_rows(conn, WS)
    # Every query is workspace-scoped and parameterised (no cross-tenant leakage).
    assert conn.queries, 'expected at least one query'
    for q, p in zip(conn.queries, conn.params):
        assert 'WHERE ms.workspace_id = %s' in q
        assert p == (WS,)


def test_list_route_keeps_row_when_all_runtime_status_columns_drifted():
    # runtime_status/status/coverage columns all absent from the JSON snapshot.
    minimal = {'id': SYS, 'workspace_id': WS, 'asset_id': ASSET, 'target_id': TARGET, 'chain': 'base'}
    conn = _DriftConn(ms_json=minimal)
    rows = pilot.list_workspace_monitored_system_rows(conn, WS)
    assert len(rows) == 1
    row = rows[0]
    assert row['runtime_status'] is None
    assert row['status'] is None
    assert row['is_enabled'] is True  # kept visible, not filtered on missing status


# --- 4: optional joins / telemetry absence do not remove the row --------------------------
def test_list_route_returns_usdc_identity_from_linked_asset():
    conn = _DriftConn(ms_json=_usdc_ms_json(runtime_status='provisioning'))
    rows = pilot.list_workspace_monitored_system_rows(conn, WS)
    row = rows[0]
    assert row['asset_name'] == 'USDC'
    assert row['target_name'] == 'Base USDC monitor'
    assert row['chain'] == 'base'
    assert row['runtime_status'] == 'provisioning'


# --- 5: cross-workspace systems are excluded ----------------------------------------------
def test_list_route_excludes_cross_workspace_rows():
    other_ws = '00000000-0000-0000-0000-000000000000'

    class _ScopedConn:
        def __init__(self):
            self.seen_params = []

        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            self.seen_params.append(tuple(params or ()))
            # A correct scoped query only ever returns rows for the requested workspace.
            assert 'WHERE ms.workspace_id = %s' in q
            if params and params[0] == WS:
                return _Result([{
                    'id': SYS, 'workspace_id': WS, 'asset_id': ASSET, 'target_id': TARGET,
                    'chain': 'base', 'is_enabled': True, 'runtime_status': 'provisioning',
                    'status': 'active', 'last_heartbeat': None, 'last_event_at': None,
                    'last_coverage_telemetry_at': None, 'last_error_text': None,
                    'coverage_reason': None, 'freshness_status': None, 'confidence_status': None,
                    'created_at': '2026-07-16T00:00:00+00:00', 'monitoring_interval_seconds': 30,
                    'asset_name': 'USDC', 'target_name': 'Base USDC monitor',
                }])
            return _Result([])

    conn = _ScopedConn()
    assert len(pilot.list_workspace_monitored_system_rows(conn, WS)) == 1
    assert pilot.list_workspace_monitored_system_rows(conn, other_ws) == []
    assert (WS,) in conn.seen_params
    assert (other_ws,) in conn.seen_params


# --- API failure surfaces a distinct error (never a silent empty) -------------------------
def test_list_route_reraises_non_schema_errors_as_api_failure():
    class _BrokenConn:
        def execute(self, query, params=None):
            raise RuntimeError('connection reset by peer')

    try:
        pilot.list_workspace_monitored_system_rows(_BrokenConn(), WS)
    except RuntimeError as exc:
        assert 'connection reset' in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError('a genuine (non-schema) error must propagate, not return []')


# --- 11: runtime-status count and list-route count agree (route-level, under drift) --------
@contextmanager
def _fake_pg(conn):
    yield conn


def _workspace_context():
    return {'workspace_id': WS, 'workspace': {'id': WS, 'slug': 'datto'}, 'role': 'owner'}


def test_list_route_endpoint_returns_row_and_correlation_id_under_drift(monkeypatch):
    conn = _DriftConn(ms_json=_usdc_ms_json(runtime_status='provisioning'))
    client = TestClient(api_main.app)

    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    monkeypatch.setattr(api_main, 'list_monitored_systems', pilot.list_monitored_systems)
    # Keep the runtime-summary side call from touching the DB; the list payload is what matters.
    monkeypatch.setattr(api_main, 'monitoring_runtime_status', lambda _request=None: {'workspace_monitoring_summary': None})
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(pilot, 'resolve_workspace_context_for_request', lambda *_a, **_k: ({'id': 'u1'}, _workspace_context(), True))

    response = client.get('/monitoring/systems', headers={'authorization': 'Bearer t', 'x-workspace-id': WS})
    assert response.status_code == 200
    payload = response.json()
    # The configured system is present (not an empty list, not an error object).
    assert 'error' not in payload
    assert len(payload['systems']) == 1
    system = payload['systems'][0]
    assert system['id'] == SYS
    assert system['asset_name'] == 'USDC'
    assert system['runtime_status'] == 'provisioning'
    assert payload.get('correlation_id')


def test_list_route_endpoint_surfaces_error_object_when_query_fails(monkeypatch):
    class _HardFailConn:
        def execute(self, query, params=None):
            raise RuntimeError('database temporarily unavailable')

    conn = _HardFailConn()
    client = TestClient(api_main.app)

    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    monkeypatch.setattr(api_main, 'list_monitored_systems', pilot.list_monitored_systems)
    monkeypatch.setattr(api_main, 'monitoring_runtime_status', lambda _request=None: {'workspace_monitoring_summary': None})
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(pilot, 'resolve_workspace_context_for_request', lambda *_a, **_k: ({'id': 'u1'}, _workspace_context(), True))

    response = client.get('/monitoring/systems', headers={'authorization': 'Bearer t', 'x-workspace-id': WS})
    # A genuine failure returns an error object with a stable code + correlation id — this is
    # what the client renders as an API-failure state (never the empty state / asset CTA).
    assert response.status_code == 200
    payload = response.json()
    assert payload['systems'] == []
    assert isinstance(payload.get('error'), dict)
    assert payload['error']['code'] == 'monitored_systems_query_failed'
    assert payload['error'].get('correlation_id')
