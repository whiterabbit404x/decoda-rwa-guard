from __future__ import annotations

from contextlib import contextmanager

from services.api.app import pilot


class _Row:
    def __init__(self, data):
        self._data = data

    def fetchone(self):
        return self._data


class _Connection:
    def __init__(self, counts):
        self.counts = counts

    def execute(self, query, params=None):
        normalized = ' '.join(str(query).split())
        if 'SELECT (SELECT COUNT(*) FROM assets' in normalized:
            return _Row(self.counts)
        raise AssertionError(f'unexpected query: {query}')

    def commit(self):
        return None


def test_get_onboarding_progress_is_data_derived(monkeypatch):
    connection = _Connection(
        {
            'assets_count': 1,
            'targets_count': 1,
            'monitoring_targets_count': 1,
            'evaluated_targets_count': 0,
            'event_receipts_count': 0,
        }
    )

    @contextmanager
    def _fake_pg_connection():
        yield connection

    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg_connection)
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda _connection: None)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda _connection, _request: {'id': 'user-1'})
    monkeypatch.setattr(
        pilot,
        'resolve_workspace',
        lambda _connection, _user_id, _workspace_id: {'workspace_id': 'ws-1', 'workspace': {'id': 'ws-1', 'name': 'Alpha'}},
    )

    payload = pilot.get_onboarding_progress(type('Req', (), {'headers': {}})())

    assert payload['workspace_name'] == 'Alpha'
    assert payload['completed_steps'] == 3
    assert payload['next_step'] == 'evidence_recorded'
    assert payload['steps'][0]['key'] == 'asset_added'
