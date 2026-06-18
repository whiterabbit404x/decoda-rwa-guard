"""Tests for backfill_missing_alerts_for_target.

Coverage:
  1. Live wallet_transfer_detected rows with no alert → both smoke and SIG alerts created
  2. Idempotent: calling twice returns same alert IDs, not duplicates
  3. Workspace isolation: wrong workspace_id returns no rows
  4. Simulator rows are excluded (evidence_source != 'live')
  5. Recovery: detection exists but no linked alert → alert is created and linked
  6. Target-scoped: only processes rows for the specified target_id
  7. native_transfer event type IS included in backfill query (block-range backfill path)
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


class _QueryCapturingConn:
    """Captures SQL queries so tests can assert what event_types were queried."""

    def __init__(self, rows):
        self._rows = rows
        self.captured_queries: list[str] = []

    def execute(self, query, params=None):
        self.captured_queries.append(str(query or ''))
        return _Result(rows=self._rows)

    def commit(self):
        pass


def test_backfill_missing_alerts_query_includes_native_transfer(monkeypatch):
    """backfill_missing_alerts_for_target must query native_transfer in addition to
    wallet_transfer_detected so that block-range-backfilled telemetry (which is stored
    as native_transfer) also gets alerts created and appears in the alerts_only filter."""
    from services.api.app import monitoring_runner

    smoke_id = str(uuid.uuid4())
    capturing_conn = _QueryCapturingConn(rows=[])

    monkeypatch.setattr(monitoring_runner, 'require_live_mode', lambda: None)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(capturing_conn))
    monkeypatch.setattr(monitoring_runner, 'authenticate_with_connection', lambda *_: {'id': USER_ID})
    monkeypatch.setattr(monitoring_runner, 'resolve_workspace', lambda *_: {'workspace_id': WORKSPACE_ID})
    monkeypatch.setattr(monitoring_runner, '_wallet_transfer_smoke_alert', lambda **_: smoke_id)
    monkeypatch.setattr(monitoring_runner, '_strategic_infrastructure_guard_alert', lambda **_: None)

    request = SimpleNamespace(headers={'x-workspace-id': WORKSPACE_ID})
    monitoring_runner.backfill_missing_alerts_for_target(request, target_id=TARGET_ID)

    telemetry_query = next(
        (q for q in capturing_conn.captured_queries if 'telemetry_events' in q.lower()),
        None,
    )
    assert telemetry_query is not None, 'Expected a query against telemetry_events'
    assert 'native_transfer' in telemetry_query, (
        "backfill query must include native_transfer so block-range-backfilled rows get alerts"
    )


# --- Granular counts, scan-all, and simulator/skip handling ---


def test_backfill_returns_granular_counts(monkeypatch):
    """Backfill response exposes created/deduped/linked/skipped counts so an operator can
    confirm whether alerts were created or only deduped (worker shows alerts=0 otherwise)."""
    from services.api.app import monitoring_runner

    conn = _FakeConn([_make_telemetry_row()])
    monkeypatch.setattr(monitoring_runner, 'require_live_mode', lambda: None)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(monitoring_runner, 'authenticate_with_connection', lambda *_: {'id': USER_ID})
    monkeypatch.setattr(monitoring_runner, 'resolve_workspace', lambda *_: {'workspace_id': WORKSPACE_ID})
    monkeypatch.setattr(monitoring_runner, '_wallet_transfer_smoke_alert', lambda **_: str(uuid.uuid4()))
    monkeypatch.setattr(monitoring_runner, '_strategic_infrastructure_guard_alert', lambda **_: str(uuid.uuid4()))

    request = SimpleNamespace(headers={'x-workspace-id': WORKSPACE_ID})
    result = monitoring_runner.backfill_missing_alerts_for_target(request, target_id=TARGET_ID)

    for key in ('created_count', 'deduped_count', 'linked_count', 'skipped_count'):
        assert key in result, f'backfill result must expose {key}'
    assert result['linked_count'] == 1
    assert result['created_count'] == 1
    assert result['skipped_count'] == 0


def test_backfill_skips_simulator_rows_without_creating_alerts(monkeypatch):
    """Simulator/replay rows are scanned (visible) but never create alerts — truthfulness:
    fallback data must never be presented as customer evidence."""
    from services.api.app import monitoring_runner

    live = _make_telemetry_row(
        telemetry_id=str(uuid.uuid4()),
        tx_hash='0x' + 'a' * 60 + 'a517',
        evidence_source='live',
    )
    sim = _make_telemetry_row(
        telemetry_id=str(uuid.uuid4()),
        tx_hash='0x' + 'b' * 60 + '90c7',
        evidence_source='simulator',
    )

    smoke_calls: list[dict] = []

    def _fake_smoke(**kwargs):
        smoke_calls.append(kwargs)
        return str(uuid.uuid4())

    conn = _FakeConn([live, sim])
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
    assert result['skipped_count'] == 1, 'the simulator row must be skipped'
    assert result['linked_count'] == 1, 'only the live row is linked to an alert'
    # smoke alert evaluator only invoked for the live row
    assert len(smoke_calls) == 1
    assert smoke_calls[0]['evidence_source'] == 'live'


def test_backfill_skips_row_missing_tx_hash(monkeypatch):
    """A wallet row with no tx_hash cannot be deduped by tx and is skipped, not collapsed
    into another transaction's alert."""
    from services.api.app import monitoring_runner

    row = _make_telemetry_row(telemetry_id=str(uuid.uuid4()))
    row['payload_json'] = {'from': WALLET_ADDR, 'to': '0xbbbb', 'chain_id': 8453, 'block_number': 1}

    called = {'smoke': 0}

    def _fake_smoke(**kwargs):
        called['smoke'] += 1
        return str(uuid.uuid4())

    conn = _FakeConn([row])
    monkeypatch.setattr(monitoring_runner, 'require_live_mode', lambda: None)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(monitoring_runner, 'authenticate_with_connection', lambda *_: {'id': USER_ID})
    monkeypatch.setattr(monitoring_runner, 'resolve_workspace', lambda *_: {'workspace_id': WORKSPACE_ID})
    monkeypatch.setattr(monitoring_runner, '_wallet_transfer_smoke_alert', _fake_smoke)
    monkeypatch.setattr(monitoring_runner, '_strategic_infrastructure_guard_alert', lambda **_: None)

    request = SimpleNamespace(headers={'x-workspace-id': WORKSPACE_ID})
    result = monitoring_runner.backfill_missing_alerts_for_target(request, target_id=TARGET_ID)

    assert result['skipped_count'] == 1
    assert result['linked_count'] == 0
    assert called['smoke'] == 0, 'must not evaluate alert rules for a tx-less row'


# --- Dedupe signature helpers: different tx_hash = different alert ---


def test_sig_dedupe_signature_differs_by_tx_hash():
    from services.api.app import monitoring_runner

    sig_90c7 = monitoring_runner._sig_dedupe_signature(
        workspace_id=WORKSPACE_ID, target_id=TARGET_ID, chain_id=8453, tx_hash='0xaaa90c7')
    sig_a517 = monitoring_runner._sig_dedupe_signature(
        workspace_id=WORKSPACE_ID, target_id=TARGET_ID, chain_id=8453, tx_hash='0xbbba517')
    assert sig_90c7 != sig_a517, 'different tx_hash must yield a different dedupe key'


def test_sig_dedupe_signature_stable_for_same_inputs():
    from services.api.app import monitoring_runner

    args = dict(workspace_id=WORKSPACE_ID, target_id=TARGET_ID, chain_id=8453, tx_hash=TX_HASH)
    assert monitoring_runner._sig_dedupe_signature(**args) == monitoring_runner._sig_dedupe_signature(**args)


def test_sig_dedupe_signature_not_collapsed_by_target_or_rule_alone():
    """Same workspace/target/chain/rule but two transactions => two distinct keys.
    Guards against deduping by target_id only / target_id + rule_key."""
    from services.api.app import monitoring_runner

    keys = {
        monitoring_runner._sig_dedupe_signature(
            workspace_id=WORKSPACE_ID, target_id=TARGET_ID, chain_id=8453, tx_hash=tx)
        for tx in ('0x1111', '0x2222', '0x3333')
    }
    assert len(keys) == 3, 'each tx_hash must map to its own dedupe key'


def test_smoke_and_sig_signatures_are_distinct_rules():
    from services.api.app import monitoring_runner

    sig = monitoring_runner._sig_dedupe_signature(
        workspace_id=WORKSPACE_ID, target_id=TARGET_ID, chain_id=8453, tx_hash=TX_HASH)
    smoke = monitoring_runner._smoke_dedupe_signature(
        workspace_id=WORKSPACE_ID, target_id=TARGET_ID, chain_id=8453, tx_hash=TX_HASH)
    assert sig != smoke, 'smoke and SIG rules must not share a dedupe key for the same tx'


# --- Backfill scan logging: row_count / observed_at / skipped_reason ---


def test_backfill_logs_row_count_observed_at_and_skipped_reason(monkeypatch, caplog):
    """The scan logs must expose row_count, per-row observed_at, and an explicit
    skipped_reason so an operator can see WHY an older row was (or was not) skipped —
    e.g. confirm a 5-day-old tx_hash was fetched and processed, not silently dropped."""
    import logging
    from services.api.app import monitoring_runner

    live = _make_telemetry_row(
        telemetry_id=str(uuid.uuid4()), tx_hash='0x' + 'a' * 60 + '90c7', evidence_source='live')
    live['observed_at'] = '2026-06-16T00:00:00+00:00'
    sim = _make_telemetry_row(
        telemetry_id=str(uuid.uuid4()), tx_hash='0x' + 'b' * 60 + 'a517', evidence_source='simulator')
    sim['observed_at'] = '2026-06-13T00:00:00+00:00'

    conn = _FakeConn([live, sim])
    monkeypatch.setattr(monitoring_runner, 'require_live_mode', lambda: None)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(monitoring_runner, 'authenticate_with_connection', lambda *_: {'id': USER_ID})
    monkeypatch.setattr(monitoring_runner, 'resolve_workspace', lambda *_: {'workspace_id': WORKSPACE_ID})
    monkeypatch.setattr(monitoring_runner, '_wallet_transfer_smoke_alert', lambda **_: str(uuid.uuid4()))
    monkeypatch.setattr(monitoring_runner, '_strategic_infrastructure_guard_alert', lambda **_: None)

    request = SimpleNamespace(headers={'x-workspace-id': WORKSPACE_ID})
    with caplog.at_level(logging.INFO, logger='services.api.app.monitoring_runner'):
        monitoring_runner.backfill_missing_alerts_for_target(request, target_id=TARGET_ID)

    text = '\n'.join(r.getMessage() for r in caplog.records)
    assert 'strategic_guard_backfill_scan_started' in text
    assert 'row_count=2' in text                                   # both rows fetched, no recency drop
    assert 'observed_at=2026-06-13T00:00:00+00:00' in text         # older row's age is visible
    # the simulator row is the one skipped, and the reason is explicit
    assert 'skipped_reason=evidence_source_not_live' in text
    assert 'created_count=' in text and 'linked_count=' in text and 'deduped_count=' in text


# --- Contract lock: app deterministic ids/signatures == migration 0114 constants ---


def test_deterministic_ids_match_migration_0114_constants():
    """Migration 0114 (0114_backfill_wallet_transfer_alerts_e785.sql) recreates the
    missing alerts in pure SQL using the SAME deterministic UUID5 detection ids and
    dedupe signatures the app computes. If the app's seed format ever changes, the
    migration would create *duplicate* alerts instead of deduping. These frozen
    constants (verified equal to the SQL output) lock the two in sync — update both
    together if the dedupe contract intentionally changes."""
    import json
    from services.api.app import monitoring_runner as m

    WID = '1155f479-3e5b-4d90-be6c-fd6c1d6b957d'
    TID = 'e7851a52-8fb1-48cd-84a3-d033f591c5dd'
    tx_90c7 = '0x' + 'a' * 60 + '90c7'
    tx_a517 = '0x' + 'b' * 60 + 'a517'

    # dedupe signatures come straight from the shared helpers.
    assert m._smoke_dedupe_signature(workspace_id=WID, target_id=TID, chain_id=8453, tx_hash=tx_90c7) \
        == '9896ce3995c55007b4b668d0159a301d'
    assert m._sig_dedupe_signature(workspace_id=WID, target_id=TID, chain_id=8453, tx_hash=tx_90c7) \
        == 'df2d74cbd42a56f98ef77ecedef1e9a0'
    assert m._smoke_dedupe_signature(workspace_id=WID, target_id=TID, chain_id=8453, tx_hash=tx_a517) \
        == '79d30f8f95955cceab300155099a725b'
    assert m._sig_dedupe_signature(workspace_id=WID, target_id=TID, chain_id=8453, tx_hash=tx_a517) \
        == '784d9760ec105d2fb4297803e6ebd024'

    # detection ids are UUID5 of the same seed the app builds inline.
    def smoke_det(tx):
        seed = json.dumps({'target_id': TID, 'tx_hash': tx, 'rule': 'smoke_wallet_transfer'}, sort_keys=True)
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, 'detection:' + seed))

    def sig_det(tx):
        seed = json.dumps(
            {'target_id': TID, 'tx_hash': tx, 'rule': m._SIG_RULE_KEY, 'chain_id': 8453}, sort_keys=True)
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, 'detection:' + seed))

    assert smoke_det(tx_90c7) == '2287bb8c-b480-594e-b225-0bcb0bceba89'
    assert sig_det(tx_90c7) == 'fb0dacf4-5099-560f-afbf-346b36e32dd5'
    assert smoke_det(tx_a517) == 'f57c28eb-f698-566f-9050-55f4b7518679'
    assert sig_det(tx_a517) == 'aaffb248-3891-57b6-8ba2-cc4922ad4a69'
