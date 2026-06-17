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
                'target_wallet_address': '0xcafe00000000000000000000000000000000feed',
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
    monkeypatch.setattr(monitoring_runner, '_strategic_infrastructure_guard_alert', lambda **_: None)

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
    monkeypatch.setattr(monitoring_runner, '_strategic_infrastructure_guard_alert', lambda **_: None)

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


# ---------------------------------------------------------------------------
# End-to-end: existing wallet_transfer_detected → detection → alert → /alerts
# ---------------------------------------------------------------------------

class _StubConn:
    """Minimal stub connection for end-to-end pipeline tests."""

    def __init__(self):
        self.inserts: list[tuple[str, tuple]] = []
        self.commit_calls = 0
        self._detection_conflict = False

    def execute(self, query: str, params=None):
        q = (query or '').strip().lower()
        if q.startswith('insert into'):
            table = q.split('insert into')[1].strip().split('(')[0].strip().split()[0]
            self.inserts.append((table, tuple(params or ())))
            if 'detections' in table and self._detection_conflict:
                return _Result(rowcount=0)
            return _Result(rowcount=1)
        if 'alert_suppression_rules' in q:
            return _Result(rows=[])
        if 'from alerts' in q:
            return _Result(rows=[])
        if 'update detections' in q or 'update ' in q:
            return _Result(rowcount=1)
        return _Result(rows=[])

    def commit(self):
        self.commit_calls += 1


class _Result:
    def __init__(self, rows=None, rowcount=None):
        self._rows = list(rows or [])
        self.rowcount = rowcount if rowcount is not None else len(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


def test_existing_wallet_transfer_telemetry_creates_detection_and_alert_visible_in_alerts(monkeypatch):
    """Full pipeline: existing wallet_transfer_detected telemetry → detection row →
    alert row → alert would be returned by the /alerts Active Alerts query.

    Given:
      - A workspace with a live wallet_transfer_detected telemetry row.
      - No prior detection or alert for that tx_hash.

    When:
      - run_detection_from_existing_telemetry is called (via POST /run-detection).

    Then:
      - _wallet_transfer_smoke_alert fires once per telemetry row.
      - A detection row is committed (monitoring_runs → detections → alerts in order).
      - An alert row is committed with status='open', source='live', workspace_id correct.
      - The alert INSERT carries a non-None detection_id (proof chain link).
      - alerts_created == 1 in the response (i.e. /alerts would show Active Alerts = 1).
    """
    from services.api.app import monitoring_runner

    workspace_id = str(uuid.uuid4())
    target_id = str(uuid.uuid4())
    telemetry_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    tx_hash = '0xdeadcafe00000000000000000000000000000000000000000000000000001234'

    conn = _TelemetryConn(workspace_id, target_id, telemetry_id, tx_hash)

    # Track all smoke-alert stub inserts
    smoke_stub = _StubConn()
    alert_insert_params: list[tuple] = []

    original_execute = smoke_stub.execute

    def _capturing_execute(query: str, params=None):
        q = (query or '').strip().lower()
        if q.startswith('insert into alerts'):
            alert_insert_params.append(tuple(params or ()))
        return original_execute(query, params)

    smoke_stub.execute = _capturing_execute  # type: ignore[assignment]

    # First pg_connection() call is the outer SELECT (run_detection_from_existing_telemetry);
    # subsequent calls are the smoke-alert dedicated INSERT connection.
    pg_call_count: list[int] = [0]

    @contextmanager
    def _sequenced_pg():
        pg_call_count[0] += 1
        if pg_call_count[0] == 1:
            yield conn        # outer connection: returns telemetry rows
        else:
            yield smoke_stub  # smoke-alert connection: captures INSERTs

    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monkeypatch.setattr(monitoring_runner, 'require_live_mode', lambda: None)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _sequenced_pg())
    monkeypatch.setattr(monitoring_runner, 'authenticate_with_connection', lambda *_: {'id': user_id})
    monkeypatch.setattr(monitoring_runner, 'resolve_workspace', lambda *_: {'workspace_id': workspace_id})

    request = SimpleNamespace(headers={'x-workspace-id': workspace_id})
    result = monitoring_runner.run_detection_from_existing_telemetry(request)

    # run_detection_from_existing_telemetry completed successfully
    assert result['status'] == 'completed'
    assert result['telemetry_processed'] == 1, (
        f'Expected 1 telemetry row processed; got {result["telemetry_processed"]}'
    )
    assert result['alerts_created'] >= 1, (
        f'Expected at least 1 alert created (smoke alert on /alerts); '
        f'got {result["alerts_created"]}'
    )

    # Verify the alert INSERT fields that /alerts queries
    assert alert_insert_params, 'alert INSERT must have been captured in smoke stub'
    params = alert_insert_params[0]
    # Param order from _upsert_alert INSERT (status='open' is hardcoded, not a param):
    # 0:id, 1:workspace_id, 2:user_id, 3:analysis_run_id, 4:target_id, 5:module_key,
    # 6:alert_type, 7:title, 8:severity, 9:source_service, 10:source, 11:summary,
    # 12:payload, 13:matched_patterns, 14:reasons, 15:recommended_action, 16:degraded,
    # 17:dedupe_signature, 18:detection_id
    assert params[1] == workspace_id, (
        f'alert workspace_id must match so /alerts returns it; got {params[1]}'
    )
    assert params[3] is None, (
        f'analysis_run_id must be NULL to avoid alerts_analysis_run_id_fkey violation; '
        f'got {params[3]!r}'
    )
    assert params[4] == target_id, f'alert target_id must match; got {params[4]}'
    assert params[8] == 'critical', f'alert severity must be critical; got {params[8]}'
    assert params[10] == 'live', f'alert source must be live (not simulator); got {params[10]}'
    detection_id_val = params[18]
    assert detection_id_val is not None, (
        'detection_id must be set in alert so /alerts can show the proof chain'
    )
    uuid.UUID(str(detection_id_val))  # must be a valid UUID

    # Verify insert order: monitoring_runs → detections → alerts
    insert_tables = [t for t, _ in smoke_stub.inserts]
    assert 'monitoring_runs' in insert_tables, 'monitoring_runs must be inserted'
    assert 'detections' in insert_tables, 'detection must be inserted'
    assert 'alerts' in insert_tables, 'alert must be inserted'
    assert insert_tables.index('monitoring_runs') < insert_tables.index('detections'), (
        'monitoring_runs must precede detections (FK prerequisite)'
    )
    assert insert_tables.index('detections') < insert_tables.index('alerts'), (
        'detection must precede alert (proof chain)'
    )
    assert smoke_stub.commit_calls >= 1, 'alert must be committed on the dedicated connection'
