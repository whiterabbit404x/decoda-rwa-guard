from __future__ import annotations

from datetime import datetime, timezone

from services.api.app import pilot


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._row


class _FakeConnection:
    def execute(self, query, params=None):
        normalized = ' '.join(str(query).split())
        if 'SELECT id, target_id, payload, reasons, matched_patterns FROM alerts' in normalized:
            return _FakeResult(
                {
                    'id': 'alert-1',
                    'target_id': 'target-1',
                    'payload': {'tx_hash': '0xabc', 'block_number': 123},
                    'reasons': ['large_transfer'],
                    'matched_patterns': ['suspicious_pattern'],
                }
            )
        if 'WITH latest AS (' in normalized and 'FROM evidence' in normalized:
            observed_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
            return _FakeResult(
                [
                    {
                        'id': 'ev-2',
                        'alert_id': 'alert-1',
                        'observed_at': observed_at,
                        'target_id': 'target-1',
                        'monitored_system_id': 'ms-1',
                        'asset_id': 'asset-1',
                        'event_type': 'transfer',
                        'severity': 'high',
                        'risk_score': 97.5,
                        'summary': 'Large suspicious transfer',
                        'source_provider': 'simulator_runtime',
                    },
                    {
                        'id': 'ev-1',
                        'alert_id': 'alert-1',
                        'observed_at': observed_at,
                        'target_id': 'target-1',
                        'monitored_system_id': 'ms-1',
                        'asset_id': 'asset-1',
                        'event_type': 'transfer',
                        'severity': 'medium',
                        'risk_score': 88.0,
                        'summary': 'Prior sample at same timestamp',
                        'source_provider': 'live_ingestor',
                    },
                ]
            )
        if 'SELECT id, name FROM targets WHERE id = %s' in normalized:
            return _FakeResult({'id': 'target-1', 'name': 'Treasury Wallet'})
        raise AssertionError(f'unexpected query: {query}')


class _FakePgContext:
    def __enter__(self):
        return _FakeConnection()

    def __exit__(self, exc_type, exc, tb):
        return False


def test_list_alert_evidence_returns_latest_linkage_fields(monkeypatch):
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _FakePgContext())
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda connection: None)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda connection, request: {'id': 'user-1'})
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda connection, user_id, workspace_id: {'workspace_id': 'ws-1'})

    response = pilot.list_alert_evidence('alert-1', request=type('Req', (), {'headers': {'x-workspace-id': 'ws-1'}})())

    assert response['alert_id'] == 'alert-1'
    assert response['evidence']['tx_hash'] == '0xabc'
    assert response['evidence']['observed_at'].isoformat() == '2026-01-02T03:04:05+00:00'
    assert response['evidence']['target_id'] == 'target-1'
    assert response['evidence']['monitored_system_id'] == 'ms-1'
    assert response['evidence']['asset_id'] == 'asset-1'
    assert response['evidence']['event_type'] == 'transfer'
    assert response['evidence']['severity'] == 'high'
    assert response['evidence']['risk_score'] == 97.5
    assert response['evidence']['summary'] == 'Large suspicious transfer'
    assert response['evidence']['source_provider'] == 'simulator_runtime'
    assert response['evidence']['source_label'] == 'simulator'
    assert len(response['linked_evidence']) == 2
    assert response['linked_evidence'][1]['source_label'] == 'live'
