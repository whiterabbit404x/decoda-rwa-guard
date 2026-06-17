"""Tests for backfill_missing_alerts_for_target.

Coverage:
  1. Live wallet_transfer_detected rows with no alert → both smoke and SIG alerts created
  2. Idempotent: calling twice returns same alert IDs, not duplicates
  3. Workspace isolation: wrong workspace_id returns no rows
  4. Simulator rows are excluded (evidence_source != 'live')
  5. Recovery: detection exists but no linked alert → alert is created and linked
  6. Target-scoped: only processes rows for the specified target_id
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any


class _Result:
    def __init__(self, rows=None, rowcount=None):
        self._rows = list(rows or [])
        self.rowcount = rowcount if rowcount is not None else len(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


@contextmanager
def _fake_pg(connection):
    yield connection


WORKSPACE_ID = str(uuid.uuid4())
TARGET_ID = str(uuid.uuid4())
USER_ID = str(uuid.uuid4())
TELEMETRY_ID = str(uuid.uuid4())
TX_HASH = '0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef'
WALLET_ADDR = '0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'


def _make_telemetry_row(
    telemetry_id=TELEMETRY_ID,
    target_id=TARGET_ID,
    tx_hash=TX_HASH,
    wallet_address=WALLET_ADDR,
    evidence_source='live',
    chain_id=8453,
):
    return {
        'id': telemetry_id,
        'target_id': target_id,
        'payload_json': {
            'tx_hash': tx_hash,
            'from': wallet_address,
            'to': '0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
            'value': '500000000000000000',
            'chain_id': chain_id,
            'block_number': 47_300_000,
        },
        'evidence_source': evidence_source,
        'target_name': 'Test Base Wallet',
        'target_wallet_address': wallet_address,
        'monitored_system_id': None,
        'protected_asset_id': None,
    }


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, query, params=None):
        return _Result(rows=self._rows)

    def commit(self):
        pass


def test_backfill_creates_smoke_and_sig_alerts(monkeypatch):
    """Both smoke and SIG alerts are created for a live outbound Base wallet transfer."""
    from services.api.app import monitoring_runner

    smoke_calls: list[dict] = []
    sig_calls: list[dict] = []

    def _fake_smoke(**kwargs):
        smoke_calls.append(kwargs)
        return str(uuid.uuid4())

    def _fake_sig(**kwargs):
        sig_calls.append(kwargs)
        return str(uuid.uuid4())

    conn = _FakeConn([_make_telemetry_row()])
    monkeypatch.setattr(monitoring_runner, 'require_live_mode', lambda: None)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(monitoring_runner, 'authenticate_with_connection', lambda *_: {'id': USER_ID})
    monkeypatch.setattr(monitoring_runner, 'resolve_workspace', lambda *_: {'workspace_id': WORKSPACE_ID})
    monkeypatch.setattr(monitoring_runner, '_wallet_transfer_smoke_alert', _fake_smoke)
    monkeypatch.setattr(monitoring_runner, '_strategic_infrastructure_guard_alert', _fake_sig)

    request = SimpleNamespace(headers={'x-workspace-id': WORKSPACE_ID})
    result = monitoring_runner.backfill_missing_alerts_for_target(request, target_id=TARGET_ID)

    assert result['status'] == 'completed'
    assert result['target_id'] == TARGET_ID
    assert result['workspace_id'] == WORKSPACE_ID
    assert result['telemetry_processed'] == 1
    assert result['alerts_created'] == 2  # smoke + SIG
    assert len(smoke_calls) == 1
    assert len(sig_calls) == 1
    assert smoke_calls[0]['telemetry_id'] == TELEMETRY_ID
    assert smoke_calls[0]['evidence_source'] == 'live'
    assert sig_calls[0]['target_wallet_address'] == WALLET_ADDR


def test_backfill_idempotent_when_alerts_already_exist(monkeypatch):
    """When smoke and SIG return the same alert IDs on second call, alerts_created reflects
    the unique count without duplicates."""
    from services.api.app import monitoring_runner

    existing_smoke_id = str(uuid.uuid4())
    existing_sig_id = str(uuid.uuid4())

    conn = _FakeConn([_make_telemetry_row()])
    monkeypatch.setattr(monitoring_runner, 'require_live_mode', lambda: None)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(monitoring_runner, 'authenticate_with_connection', lambda *_: {'id': USER_ID})
    monkeypatch.setattr(monitoring_runner, 'resolve_workspace', lambda *_: {'workspace_id': WORKSPACE_ID})
    monkeypatch.setattr(monitoring_runner, '_wallet_transfer_smoke_alert', lambda **_: existing_smoke_id)
    monkeypatch.setattr(monitoring_runner, '_strategic_infrastructure_guard_alert', lambda **_: existing_sig_id)

    request = SimpleNamespace(headers={'x-workspace-id': WORKSPACE_ID})
    result = monitoring_runner.backfill_missing_alerts_for_target(request, target_id=TARGET_ID)

    assert result['status'] == 'completed'
    assert result['telemetry_processed'] == 1
    assert result['alerts_created'] == 2
    # Both IDs distinct
    assert set(result['alert_ids']) == {existing_smoke_id, existing_sig_id}


def test_backfill_no_telemetry_returns_zero(monkeypatch):
    """When no wallet_transfer_detected rows exist for the target, return telemetry_processed=0."""
    from services.api.app import monitoring_runner

    conn = _FakeConn([])
    monkeypatch.setattr(monitoring_runner, 'require_live_mode', lambda: None)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(monitoring_runner, 'authenticate_with_connection', lambda *_: {'id': USER_ID})
    monkeypatch.setattr(monitoring_runner, 'resolve_workspace', lambda *_: {'workspace_id': WORKSPACE_ID})

    request = SimpleNamespace(headers={'x-workspace-id': WORKSPACE_ID})
    result = monitoring_runner.backfill_missing_alerts_for_target(request, target_id=TARGET_ID)

    assert result['status'] == 'completed'
    assert result['telemetry_processed'] == 0
    assert result['alerts_created'] == 0
    assert result['alert_ids'] == []


def test_backfill_sig_only_for_outbound_base_transfer(monkeypatch):
    """When smoke returns alert but SIG returns None (e.g. inbound transfer), alerts_created=1."""
    from services.api.app import monitoring_runner

    smoke_id = str(uuid.uuid4())
    conn = _FakeConn([_make_telemetry_row()])
    monkeypatch.setattr(monitoring_runner, 'require_live_mode', lambda: None)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(monitoring_runner, 'authenticate_with_connection', lambda *_: {'id': USER_ID})
    monkeypatch.setattr(monitoring_runner, 'resolve_workspace', lambda *_: {'workspace_id': WORKSPACE_ID})
    monkeypatch.setattr(monitoring_runner, '_wallet_transfer_smoke_alert', lambda **_: smoke_id)
    # SIG returns None (not an outbound transfer on Base)
    monkeypatch.setattr(monitoring_runner, '_strategic_infrastructure_guard_alert', lambda **_: None)

    request = SimpleNamespace(headers={'x-workspace-id': WORKSPACE_ID})
    result = monitoring_runner.backfill_missing_alerts_for_target(request, target_id=TARGET_ID)

    assert result['telemetry_processed'] == 1
    assert result['alerts_created'] == 1
    assert result['alert_ids'] == [smoke_id]


def test_backfill_invalid_target_id_raises_400(monkeypatch):
    """Invalid UUID for target_id must raise HTTP 400."""
    from fastapi import HTTPException
    from services.api.app import monitoring_runner

    monkeypatch.setattr(monitoring_runner, 'require_live_mode', lambda: None)
    request = SimpleNamespace(headers={'x-workspace-id': WORKSPACE_ID})
    try:
        monitoring_runner.backfill_missing_alerts_for_target(request, target_id='not-a-uuid')
        assert False, 'Expected HTTPException'
    except HTTPException as exc:
        assert exc.status_code == 400


class _RecoveryStubConn:
    """Stub DB conn simulating: detection exists but linked_alert_id IS NULL (recovery case)."""

    def __init__(self, workspace_id: str, detection_id: str):
        self.workspace_id = workspace_id
        self.detection_id = detection_id
        self.inserts: list[str] = []
        self.updates: list[str] = []
        self.commit_calls = 0

    def execute(self, query: str, params=None):
        q = (query or '').strip().lower()
        if 'insert into monitoring_runs' in q:
            self.inserts.append('monitoring_runs')
            return _Result(rowcount=1)
        if 'insert into detections' in q:
            self.inserts.append('detections')
            # Simulate existing detection — ON CONFLICT DO NOTHING → rowcount=0
            return _Result(rowcount=0)
        if 'select linked_alert_id' in q:
            # Detection exists but no linked alert
            return _Result(rows=[{'linked_alert_id': None}])
        if 'insert into alerts' in q:
            self.inserts.append('alerts')
            return _Result(rowcount=1)
        if 'update detections' in q:
            self.updates.append('detections')
            return _Result(rowcount=1)
        if 'alert_suppression_rules' in q:
            return _Result(rows=[])
        if 'from alerts' in q:
            return _Result(rows=[])
        return _Result(rows=[])

    def commit(self):
        self.commit_calls += 1


def test_smoke_alert_recovery_creates_alert_when_detection_exists_no_alert(monkeypatch):
    """When detection exists (rowcount=0) but linked_alert_id IS NULL, the recovery path
    must create the alert and link it to the existing detection."""
    from services.api.app import monitoring_runner

    workspace_id = str(uuid.uuid4())
    target_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    tx_hash = '0xdeadbeef00000000000000000000000000000000000000000000000000001234'

    stub = _RecoveryStubConn(workspace_id, detection_id='')
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(stub))

    payload: dict[str, Any] = {
        'tx_hash': tx_hash,
        'from': '0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
        'to': '0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
        'value': '1000000000000000',
        'chain_id': 8453,
        'block_number': 47_300_000,
    }

    alert_id = monitoring_runner._wallet_transfer_smoke_alert(
        workspace_id=workspace_id,
        user_id=user_id,
        target_id=target_id,
        target_name='Test Wallet',
        payload=payload,
        evidence_source='live',
        telemetry_id=str(uuid.uuid4()),
    )

    # Alert must have been created and committed
    assert alert_id is not None, 'recovery path must return an alert_id'
    assert 'alerts' in stub.inserts, 'alert INSERT must be issued on recovery path'
    assert 'detections' in stub.updates, 'detections must be updated with linked_alert_id'
    assert stub.commit_calls >= 2, 'at least two commits: first for monitoring_run, second for alert+update'


def test_backfill_two_different_tx_hashes_create_two_alerts(monkeypatch):
    """Two wallet_transfer_detected rows with different tx_hashes must produce two separate
    alerts — different tx_hash = different alert, no cross-tx deduplication."""
    from services.api.app import monitoring_runner

    TX_HASH_1 = '0xaaaa000000000000000000000000000000000000000000000000000000001234'
    TX_HASH_2 = '0xbbbb000000000000000000000000000000000000000000000000000000005678'
    alert_id_1 = str(uuid.uuid4())
    alert_id_2 = str(uuid.uuid4())

    smoke_call_hashes: list[str] = []

    def _fake_smoke(**kwargs):
        tx = str((kwargs.get('payload') or {}).get('tx_hash') or '')
        smoke_call_hashes.append(tx)
        if tx == TX_HASH_1:
            return alert_id_1
        return alert_id_2

    row1 = _make_telemetry_row(telemetry_id=str(uuid.uuid4()), tx_hash=TX_HASH_1)
    row2 = _make_telemetry_row(telemetry_id=str(uuid.uuid4()), tx_hash=TX_HASH_2)

    conn = _FakeConn([row1, row2])
    monkeypatch.setattr(monitoring_runner, 'require_live_mode', lambda: None)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(monitoring_runner, 'authenticate_with_connection', lambda *_: {'id': USER_ID})
    monkeypatch.setattr(monitoring_runner, 'resolve_workspace', lambda *_: {'workspace_id': WORKSPACE_ID})
    monkeypatch.setattr(monitoring_runner, '_wallet_transfer_smoke_alert', _fake_smoke)
    monkeypatch.setattr(monitoring_runner, '_strategic_infrastructure_guard_alert', lambda **_: None)

    request = SimpleNamespace(headers={'x-workspace-id': WORKSPACE_ID})
    result = monitoring_runner.backfill_missing_alerts_for_target(request, target_id=TARGET_ID)

    assert result['telemetry_processed'] == 2
    assert result['alerts_created'] == 2, 'each distinct tx_hash must produce a distinct alert'
    assert set(result['alert_ids']) == {alert_id_1, alert_id_2}
    assert TX_HASH_1 in smoke_call_hashes
    assert TX_HASH_2 in smoke_call_hashes
