"""Test: existing wallet_transfer_detected telemetry → Run Detection → alert visible on /alerts."""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch


class _Result:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


@contextmanager
def _fake_pg(connection):
    yield connection


class _TelemetryConn:
    """Fake DB connection returning one wallet_transfer_detected telemetry row."""

    def __init__(self, workspace_id, target_id, telemetry_id, tx_hash):
        self.workspace_id = workspace_id
        self.target_id = target_id
        self.telemetry_id = telemetry_id
        self.tx_hash = tx_hash

    def execute(self, query, params=None):
        normalized = ' '.join(str(query).split())
        if 'FROM telemetry_events te' in normalized:
            return _Result(rows=[{
                'id': self.telemetry_id,
                'target_id': self.target_id,
                'payload_json': {
                    'tx_hash': self.tx_hash,
                    'from': '0xcafe00000000000000000000000000000000feed',
                    'to': '0xdead00000000000000000000000000000000beef',
                    'value': '1000000000000000',
                    'chain_id': 8453,
                    'block_number': 12345678,
                },
                'evidence_source': 'live',
                'target_name': 'Test Base Wallet',
                'monitored_system_id': None,
                'protected_asset_id': None,
            }])
        return _Result()

    def commit(self):
        pass


def test_run_detection_creates_alert_from_existing_wallet_transfer_telemetry(monkeypatch):
    """Given existing wallet_transfer_detected live telemetry, calling
    run_detection_from_existing_telemetry must create a detection and alert
    that are then visible via /alerts (workspace-scoped query)."""
    from services.api.app import monitoring_runner

    workspace_id = str(uuid.uuid4())
    target_id = str(uuid.uuid4())
    telemetry_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    tx_hash = '0xabcdef1234567890'

    conn = _TelemetryConn(workspace_id, target_id, telemetry_id, tx_hash)
    created_alert_ids: list[str] = []

    def _fake_smoke_alert(
        *,
        workspace_id,
        user_id,
        target_id,
        target_name,
        payload,
        evidence_source,
        telemetry_id,
        monitored_system_id,
        protected_asset_id,
    ):
        alert_id = str(uuid.uuid4())
        created_alert_ids.append(alert_id)
        assert evidence_source == 'live', 'must only process live telemetry'
        assert payload.get('tx_hash') == tx_hash
        return alert_id

    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monkeypatch.setattr(monitoring_runner, 'require_live_mode', lambda: None)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(monitoring_runner, 'authenticate_with_connection', lambda *_: {'id': user_id})
    monkeypatch.setattr(monitoring_runner, 'resolve_workspace', lambda *_: {'workspace_id': workspace_id})
    monkeypatch.setattr(monitoring_runner, '_wallet_transfer_smoke_alert', _fake_smoke_alert)

    request = SimpleNamespace(headers={'x-workspace-id': workspace_id})
    result = monitoring_runner.run_detection_from_existing_telemetry(request)

    assert result['status'] == 'completed'
    assert result['telemetry_processed'] == 1, (
        f'Expected 1 telemetry row processed, got {result["telemetry_processed"]}'
    )
    assert result['alerts_created'] == 1, (
        f'Expected 1 alert created, got {result["alerts_created"]}'
    )
    assert len(created_alert_ids) == 1, (
        f'_wallet_transfer_smoke_alert must be called once; called {len(created_alert_ids)} times'
    )
    assert result['alert_ids'] == created_alert_ids


def test_run_detection_idempotent_when_alert_already_exists(monkeypatch):
    """When _wallet_transfer_smoke_alert returns None (duplicate detection),
    run_detection_from_existing_telemetry must report alerts_created=0 and not fail."""
    from services.api.app import monitoring_runner

    workspace_id = str(uuid.uuid4())
    target_id = str(uuid.uuid4())
    telemetry_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())

    conn = _TelemetryConn(workspace_id, target_id, telemetry_id, '0xduplicate')

    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monkeypatch.setattr(monitoring_runner, 'require_live_mode', lambda: None)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(monitoring_runner, 'authenticate_with_connection', lambda *_: {'id': user_id})
    monkeypatch.setattr(monitoring_runner, 'resolve_workspace', lambda *_: {'workspace_id': workspace_id})
    monkeypatch.setattr(monitoring_runner, '_wallet_transfer_smoke_alert', lambda **_: None)

    request = SimpleNamespace(headers={'x-workspace-id': workspace_id})
    result = monitoring_runner.run_detection_from_existing_telemetry(request)

    assert result['status'] == 'completed'
    assert result['telemetry_processed'] == 1
    assert result['alerts_created'] == 0


def test_run_detection_no_telemetry_returns_zero_processed(monkeypatch):
    """When there are no wallet_transfer_detected telemetry rows,
    run_detection_from_existing_telemetry must return telemetry_processed=0."""
    from services.api.app import monitoring_runner

    workspace_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())

    class _EmptyConn:
        def execute(self, query, params=None):
            return _Result(rows=[])

        def commit(self):
            pass

    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monkeypatch.setattr(monitoring_runner, 'require_live_mode', lambda: None)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_EmptyConn()))
    monkeypatch.setattr(monitoring_runner, 'authenticate_with_connection', lambda *_: {'id': user_id})
    monkeypatch.setattr(monitoring_runner, 'resolve_workspace', lambda *_: {'workspace_id': workspace_id})

    request = SimpleNamespace(headers={'x-workspace-id': workspace_id})
    result = monitoring_runner.run_detection_from_existing_telemetry(request)

    assert result['status'] == 'completed'
    assert result['telemetry_processed'] == 0
    assert result['alerts_created'] == 0


def test_smoke_alert_response_includes_confidence_and_detection_type():
    """_wallet_transfer_smoke_alert response dict must include confidence='high'
    and detection_type='monitored_wallet_transfer' so the alert payload carries them."""
    import re
    source = open('services/api/app/monitoring_runner.py', encoding='utf-8').read()

    alert_fn_start = source.find('def _wallet_transfer_smoke_alert(')
    assert alert_fn_start != -1, '_wallet_transfer_smoke_alert must exist'

    # Find the response dict within the function (bounded by next top-level def)
    next_fn = source.find('\ndef ', alert_fn_start + 1)
    fn_body = source[alert_fn_start:next_fn if next_fn != -1 else alert_fn_start + 8000]

    assert "'confidence': 'high'" in fn_body, (
        "_wallet_transfer_smoke_alert response dict must include 'confidence': 'high'"
    )
    assert "'detection_type': 'monitored_wallet_transfer'" in fn_body, (
        "_wallet_transfer_smoke_alert response dict must include 'detection_type': 'monitored_wallet_transfer'"
    )
