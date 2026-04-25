from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from services.api.app import pilot


class _Result:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class _ProofChainConnection:
    def __init__(self) -> None:
        self.asset_id: str | None = None
        self.target_id: str | None = None
        self.persisted_ids: dict[str, set[str]] = {
            'monitoring_runs': set(),
            'evidence': set(),
            'detections': set(),
            'detection_evidence': set(),
            'alerts': set(),
            'incidents': set(),
            'response_actions': set(),
        }
        self.commit_calls = 0

    def execute(self, query, params=None):
        normalized = ' '.join(str(query).split())
        if 'FROM assets' in normalized and 'normalized_identifier = %s' in normalized:
            return _Result({'id': self.asset_id} if self.asset_id else None)
        if 'INSERT INTO assets' in normalized:
            self.asset_id = str(params[0])
            return _Result(None)
        if 'FROM targets' in normalized and 'name = %s' in normalized:
            return _Result({'id': self.target_id} if self.target_id else None)
        if 'INSERT INTO targets' in normalized:
            self.target_id = str(params[0])
            return _Result(None)
        if 'SELECT id, observed_at FROM evidence' in normalized and "source_provider = 'live'" in normalized:
            return _Result(None)
        for table_name in tuple(self.persisted_ids.keys()):
            if f'INSERT INTO {table_name} ' in normalized:
                self.persisted_ids[table_name].add(str(params[0]))
                return _Result(None)
        return _Result(None)

    def commit(self):
        self.commit_calls += 1


class _PgContext:
    def __init__(self, connection):
        self._connection = connection

    def __enter__(self):
        return self._connection

    def __exit__(self, exc_type, exc, tb):
        return False


def _request_context(workspace_id: str):
    return SimpleNamespace(headers={'x-workspace-id': workspace_id})


def test_ensure_monitoring_proof_chain_first_call_persists_all_linked_records(monkeypatch):
    connection = _ProofChainConnection()
    workspace_id = '11111111-1111-1111-1111-111111111111'

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'utc_now', lambda: datetime(2026, 4, 25, 9, 30, tzinfo=timezone.utc))
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _PgContext(connection))
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda _connection: None)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda _connection, _request: {'id': 'user-1'})
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda _connection, _user_id, _workspace_id: {'workspace_id': workspace_id})
    monkeypatch.setattr(
        pilot,
        'ensure_monitored_system_for_target',
        lambda _connection, target_id, workspace_id, require_enabled=False: {'status': 'ok', 'monitored_system_id': 'sys-proof-chain'},
    )

    payload = pilot.ensure_monitoring_proof_chain(workspace_id, _request_context(workspace_id))

    assert payload['status'] == 'degraded'
    assert payload['reason'] == 'simulator_fallback_prevents_live_production_label'
    assert payload['evidence_source'] == 'simulator'
    assert all(len(ids) == 1 for ids in connection.persisted_ids.values())
    assert connection.commit_calls == 1


def test_ensure_monitoring_proof_chain_second_call_reuses_chain_ids_via_idempotent_correlation_key(monkeypatch):
    connection = _ProofChainConnection()
    workspace_id = '11111111-1111-1111-1111-111111111111'

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'utc_now', lambda: datetime(2026, 4, 25, 9, 45, tzinfo=timezone.utc))
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _PgContext(connection))
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda _connection: None)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda _connection, _request: {'id': 'user-1'})
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda _connection, _user_id, _workspace_id: {'workspace_id': workspace_id})
    monkeypatch.setattr(
        pilot,
        'ensure_monitored_system_for_target',
        lambda _connection, target_id, workspace_id, require_enabled=False: {'status': 'ok', 'monitored_system_id': 'sys-proof-chain'},
    )

    first = pilot.ensure_monitoring_proof_chain(workspace_id, _request_context(workspace_id))
    second = pilot.ensure_monitoring_proof_chain(workspace_id, _request_context(workspace_id))

    assert first['correlation_id'] == second['correlation_id']
    assert first['monitoring_run_id'] == second['monitoring_run_id']
    assert first['detection_id'] == second['detection_id']
    assert first['alert_id'] == second['alert_id']
    assert first['incident_id'] == second['incident_id']
    assert first['response_action_id'] == second['response_action_id']
    assert all(len(ids) == 1 for ids in connection.persisted_ids.values())
    assert connection.commit_calls == 2
