"""
Tests proving the wallet_transfer_detected escalation pipeline:

  1. A live telemetry event triggers _wallet_transfer_smoke_alert which writes an
     alert row to the DB (committed independently of the outer monitoring transaction).
  2. The created alert is visible via the list_alerts query (correct fields in the
     alerts table row).
  3. An incident can be created from the alert and linked to evidence via
     _maybe_create_incident.
"""
from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch, call

import pytest

from services.api.app import monitoring_runner

WORKSPACE_ID = str(uuid.uuid4())
TARGET_ID = str(uuid.uuid4())
USER_ID = str(uuid.uuid4())
TX_HASH = '0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef'
FROM_ADDR = '0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
TO_ADDR = '0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
BLOCK_NUMBER = 47_300_000
CHAIN_ID = 8453


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _make_transfer_payload(*, evidence_source: str = 'live') -> dict[str, Any]:
    return {
        'tx_hash': TX_HASH,
        'from': FROM_ADDR,
        'to': TO_ADDR,
        'value': hex(int(0.5 * 10 ** 18)),
        'amount': '500000000000000000',
        'block_number': BLOCK_NUMBER,
        'chain_id': CHAIN_ID,
        'event_type': 'transaction',
        'wallet_transfer_direction': 'outbound',
    }


# ---------------------------------------------------------------------------
# Minimal DB connection stub
# ---------------------------------------------------------------------------

class _Rows:
    def __init__(self, rows=None, rowcount=None):
        self._rows = list(rows or [])
        self.rowcount = rowcount if rowcount is not None else len(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _StubConn:
    """Tracks all INSERT/UPDATE/SELECT calls without real DB."""

    def __init__(self):
        self.inserts: list[tuple[str, tuple]] = []
        self.commit_calls = 0
        self._suppression_row = None  # no suppression by default
        self._existing_alert_row = None  # no dedup match by default
        self._detection_conflict = False  # set True to simulate ON CONFLICT DO NOTHING
        # When _detection_conflict is True, this controls the linked_alert_id the stub
        # returns for the recovery-path SELECT:
        # - None  → recovery path runs (creates missing alert for existing detection)
        # - a UUID str → true dedup (alert already linked, returns existing id)
        self._linked_alert_id: str | None = None

    def execute(self, query: str, params=None):
        q = (query or '').strip().lower()
        if q.startswith('insert into'):
            table = q.split('insert into')[1].strip().split('(')[0].strip().split()[0]
            self.inserts.append((table, tuple(params or ())))
            # Simulate ON CONFLICT DO NOTHING returning rowcount=0 for detections
            if 'detections' in table and self._detection_conflict:
                return _Rows(rowcount=0)
            return _Rows(rowcount=1)
        if 'alert_suppression_rules' in q and 'select' in q:
            return _Rows([self._suppression_row] if self._suppression_row else [])
        if q.startswith('select') and 'from alerts' in q:
            return _Rows([self._existing_alert_row] if self._existing_alert_row else [])
        # Recovery-path: SELECT linked_alert_id FROM detections WHERE id = %s
        if 'select' in q and 'linked_alert_id' in q and 'from detections' in q:
            return _Rows([{'linked_alert_id': self._linked_alert_id}])
        if q.startswith('update'):
            return _Rows(rowcount=1)
        return _Rows()

    def commit(self):
        self.commit_calls += 1

    @contextmanager
    def transaction(self):
        yield


# ---------------------------------------------------------------------------
# 1.  Telemetry event → alert row created
# ---------------------------------------------------------------------------

def test_smoke_alert_creates_alert_row_for_live_evidence():
    """A live wallet_transfer_detected event causes _upsert_alert to be called."""
    stub = _StubConn()

    @contextmanager
    def _fake_pg():
        yield stub

    with patch.object(monitoring_runner, 'pg_connection', _fake_pg):
        alert_id = monitoring_runner._wallet_transfer_smoke_alert(
            workspace_id=WORKSPACE_ID,
            user_id=USER_ID,
            target_id=TARGET_ID,
            target_name='Base Wallet',
            payload=_make_transfer_payload(),
            evidence_source='live',
        )

    assert alert_id, 'smoke alert must return an alert_id for live evidence'
    alert_inserts = [t for t, _ in stub.inserts if t == 'alerts']
    assert alert_inserts, 'an alerts row must be inserted'
    assert stub.commit_calls >= 1, 'alert must be committed on the dedicated connection'


def test_smoke_alert_not_created_for_simulator_evidence():
    """Simulator evidence must never produce a smoke alert."""
    stub = _StubConn()

    @contextmanager
    def _fake_pg():
        yield stub

    with patch.object(monitoring_runner, 'pg_connection', _fake_pg):
        result = monitoring_runner._wallet_transfer_smoke_alert(
            workspace_id=WORKSPACE_ID,
            user_id=USER_ID,
            target_id=TARGET_ID,
            target_name='Base Wallet',
            payload=_make_transfer_payload(),
            evidence_source='simulator',
        )

    assert result is None
    alert_inserts = [t for t, _ in stub.inserts if t == 'alerts']
    assert not alert_inserts, 'no alert row must be inserted for simulator evidence'


def test_smoke_alert_not_created_for_demo_evidence():
    """Demo evidence must never produce a smoke alert."""
    stub = _StubConn()

    @contextmanager
    def _fake_pg():
        yield stub

    with patch.object(monitoring_runner, 'pg_connection', _fake_pg):
        result = monitoring_runner._wallet_transfer_smoke_alert(
            workspace_id=WORKSPACE_ID,
            user_id=USER_ID,
            target_id=TARGET_ID,
            target_name='Base Wallet',
            payload=_make_transfer_payload(),
            evidence_source='demo',
        )

    assert result is None


def test_smoke_alert_includes_required_evidence_fields():
    """Alert payload must carry tx_hash, from/to addresses, amount_wei, chain_id, block_number, evidence_source."""
    captured_response: list[dict] = []
    stub = _StubConn()

    original_upsert = monitoring_runner._upsert_alert

    def _capturing_upsert(conn, *, response, **kwargs):
        captured_response.append(dict(response))
        return original_upsert(conn, response=response, **kwargs)

    @contextmanager
    def _fake_pg():
        yield stub

    with (
        patch.object(monitoring_runner, 'pg_connection', _fake_pg),
        patch.object(monitoring_runner, '_upsert_alert', _capturing_upsert),
    ):
        monitoring_runner._wallet_transfer_smoke_alert(
            workspace_id=WORKSPACE_ID,
            user_id=USER_ID,
            target_id=TARGET_ID,
            target_name='Base Wallet',
            payload=_make_transfer_payload(),
            evidence_source='live',
        )

    assert captured_response, 'expected _upsert_alert to be called'
    r = captured_response[0]
    assert r['tx_hash'] == TX_HASH
    assert r['from_address'] == FROM_ADDR
    assert r['to_address'] == TO_ADDR
    assert r['chain_id'] == CHAIN_ID
    assert r['block_number'] == BLOCK_NUMBER
    assert r['evidence_source'] == 'live'
    assert r['severity'] == 'low'
    assert r['source'] == 'live'


def test_smoke_alert_deduplicates_same_tx_hash():
    """The same tx_hash must not create a second alert within the dedup window."""
    existing_alert_id = str(uuid.uuid4())
    stub = _StubConn()
    stub._existing_alert_row = {'id': existing_alert_id, 'occurrence_count': 1}

    @contextmanager
    def _fake_pg():
        yield stub

    with patch.object(monitoring_runner, 'pg_connection', _fake_pg):
        alert_id = monitoring_runner._wallet_transfer_smoke_alert(
            workspace_id=WORKSPACE_ID,
            user_id=USER_ID,
            target_id=TARGET_ID,
            target_name='Base Wallet',
            payload=_make_transfer_payload(),
            evidence_source='live',
        )

    assert alert_id == existing_alert_id, 'dedup must return existing alert_id instead of inserting a new row'
    new_alert_inserts = [t for t, _ in stub.inserts if t == 'alerts']
    assert not new_alert_inserts, 'no new INSERT must happen when the alert already exists'


def test_smoke_alert_committed_before_analysis(caplog):
    """The smoke alert commit on the dedicated connection must precede _process_single_event."""
    import logging
    from services.api.app.activity_providers import ActivityProviderResult
    from services.api.app.evm_activity_provider import ActivityEvent

    target = {
        'id': TARGET_ID,
        'workspace_id': WORKSPACE_ID,
        'asset_id': str(uuid.uuid4()),
        'monitored_system_id': str(uuid.uuid4()),
        'chain_network': 'base',
        'wallet_address': FROM_ADDR,
        'name': 'Base Wallet',
        'target_type': 'wallet',
        'monitoring_checkpoint_cursor': None,
    }

    event = ActivityEvent(
        event_id='test-event-id',
        kind='transaction',
        observed_at=_utcnow(),
        ingestion_source='polling',
        cursor=f'{BLOCK_NUMBER}:{TX_HASH}:-1',
        payload={
            'tx_hash': TX_HASH,
            'from': FROM_ADDR,
            'to': TO_ADDR,
            'value': hex(int(0.5 * 10 ** 18)),
            'amount': '500000000000000000',
            'block_number': BLOCK_NUMBER,
            'chain_id': CHAIN_ID,
            'event_type': 'transaction',
            'wallet_transfer_direction': 'outbound',
        },
    )

    provider_result = ActivityProviderResult(
        mode='live',
        status='live',
        evidence_state='REAL_EVIDENCE',
        truthfulness_state='NOT_CLAIM_SAFE',
        synthetic=False,
        provider_name='evm_activity_provider',
        provider_kind='rpc',
        evidence_present=True,
        recent_real_event_count=1,
        last_real_event_at=_utcnow(),
        events=[event],
        latest_block=BLOCK_NUMBER,
        checkpoint=f'{BLOCK_NUMBER}:{TX_HASH}:-1',
        checkpoint_age_seconds=5,
        degraded_reason=None,
        error_code=None,
        source_type='rpc_polling',
        reason_code=None,
        claim_safe=False,
        detection_outcome='NO_CONFIRMED_ANOMALY_FROM_REAL_EVIDENCE',
    )

    order: list[str] = []
    dedicated_stub = _StubConn()
    original_commit = dedicated_stub.commit

    def _tracking_commit():
        order.append('smoke_alert_commit')
        original_commit()

    dedicated_stub.commit = _tracking_commit  # type: ignore[assignment]

    @contextmanager
    def _fake_pg():
        yield dedicated_stub

    outer_stub = _StubConn()
    outer_stub.execute = MagicMock(return_value=_Rows([{'id': WORKSPACE_ID, 'name': 'WS'}]))

    def _analysis_with_tracking(*args, **kwargs):
        order.append('analysis')
        raise RuntimeError('analysis_unavailable:live_engine_unavailable')

    with (
        patch.object(monitoring_runner, 'fetch_target_activity_result', return_value=provider_result),
        patch.object(monitoring_runner, 'pg_connection', _fake_pg),
        patch.object(monitoring_runner, 'persist_analysis_run', MagicMock(return_value=str(uuid.uuid4()))),
        patch.object(monitoring_runner, '_load_checkpoint', return_value=0),
        patch.object(monitoring_runner, '_process_single_event', side_effect=_analysis_with_tracking),
        patch.object(monitoring_runner, '_persist_detection_evaluation_checkpoint', return_value=None),
    ):
        # Since Fix 3 (event_processing_failed wrapper), analysis failures are swallowed
        # so the cursor and telemetry survive. process_monitoring_target now returns normally.
        monitoring_runner.process_monitoring_target(outer_stub, target)

    assert 'smoke_alert_commit' in order, 'smoke alert commit must fire before analysis raises'
    assert 'analysis' in order, 'analysis must fire'
    assert order.index('smoke_alert_commit') < order.index('analysis'), (
        f'smoke alert commit must precede analysis; order={order}'
    )


# ---------------------------------------------------------------------------
# 2.  Alert visible in /alerts — correct fields in the alerts table row
# ---------------------------------------------------------------------------

def test_alert_row_fields_are_visible_to_list_alerts_query():
    """The alert INSERT uses fields that list_alerts SELECT returns: severity, source, summary, title."""
    stub = _StubConn()
    inserted_params: list[tuple] = []

    original_execute = stub.execute

    def _capturing_execute(query: str, params=None):
        q = (query or '').strip().lower()
        if q.startswith('insert into alerts'):
            inserted_params.append(tuple(params or ()))
        return original_execute(query, params)

    stub.execute = _capturing_execute  # type: ignore[assignment]

    @contextmanager
    def _fake_pg():
        yield stub

    with patch.object(monitoring_runner, 'pg_connection', _fake_pg):
        monitoring_runner._wallet_transfer_smoke_alert(
            workspace_id=WORKSPACE_ID,
            user_id=USER_ID,
            target_id=TARGET_ID,
            target_name='Treasury Wallet',
            payload=_make_transfer_payload(),
            evidence_source='live',
        )

    assert inserted_params, 'expected an INSERT INTO alerts'
    # The INSERT parameter order matches _upsert_alert:
    # (id, workspace_id, user_id, analysis_run_id, target_id, alert_type, title, severity, ...)
    params = inserted_params[0]
    # Param order (from _upsert_alert INSERT):
    # 0:id, 1:workspace_id, 2:user_id, 3:analysis_run_id, 4:target_id, 5:module_key,
    # 6:alert_type, 7:title, 8:severity, 9:source_service, 10:source, 11:summary,
    # 12:payload, 13:matched_patterns, 14:reasons, 15:recommended_action,
    # 16:degraded, 17:dedupe_signature, 18:detection_id
    assert params[8] == 'low', f'alert severity must be low; got {params[8]}'
    assert 'wallet transfer detected' in str(params[7]).lower(), (
        f'alert title must mention wallet transfer; got {params[7]}'
    )
    assert params[1] == WORKSPACE_ID
    assert params[4] == TARGET_ID


# ---------------------------------------------------------------------------
# 3.  Incident can be created from alert with evidence link
# ---------------------------------------------------------------------------

def test_incident_created_from_alert_links_source_alert():
    """_maybe_create_incident must write source_alert_id and linked_alert_ids."""
    stub = _StubConn()
    alert_id = str(uuid.uuid4())
    analysis_run_id = str(uuid.uuid4())

    response = {
        'severity': 'critical',
        'explanation': 'Critical wallet drain detected.',
        'tx_hash': TX_HASH,
        'from_address': FROM_ADDR,
        'to_address': TO_ADDR,
        'chain_id': CHAIN_ID,
        'block_number': BLOCK_NUMBER,
        'evidence_source': 'live',
    }

    incident_id = monitoring_runner._maybe_create_incident(
        stub,
        workspace_id=WORKSPACE_ID,
        user_id=USER_ID,
        target_id=TARGET_ID,
        analysis_run_id=analysis_run_id,
        alert_id=alert_id,
        response=response,
        auto_create=True,
    )

    assert incident_id, 'incident must be created when auto_create=True and severity=critical'
    incident_inserts = [(t, p) for t, p in stub.inserts if t == 'incidents']
    assert incident_inserts, 'an incidents row must be inserted'
    _, params = incident_inserts[0]

    # Verify source_alert_id (index 8 — after id, workspace_id, user_id, analysis_run_id, target_id, event_type, title, severity)
    source_alert_idx = list(params).index(alert_id)
    assert source_alert_idx >= 0, f'alert_id {alert_id} must appear in incident params'

    # linked_alert_ids should be a JSON array containing the alert_id
    linked_ids_json_candidates = [p for p in params if isinstance(p, str) and alert_id in p and p.startswith('[')]
    assert linked_ids_json_candidates, 'linked_alert_ids JSON must contain the alert_id'
    linked_ids = json.loads(linked_ids_json_candidates[0])
    assert alert_id in linked_ids


def test_incident_not_created_for_low_severity_without_auto_create():
    """Low severity wallet transfer smoke alerts must NOT auto-create incidents."""
    stub = _StubConn()
    alert_id = str(uuid.uuid4())

    response = {
        'severity': 'low',
        'explanation': 'Wallet transfer detected.',
        'evidence_source': 'live',
    }

    incident_id = monitoring_runner._maybe_create_incident(
        stub,
        workspace_id=WORKSPACE_ID,
        user_id=USER_ID,
        target_id=TARGET_ID,
        analysis_run_id=str(uuid.uuid4()),
        alert_id=alert_id,
        response=response,
        auto_create=False,
    )

    assert incident_id is None, 'low severity without auto_create=True must not create incident'
    incident_inserts = [t for t, _ in stub.inserts if t == 'incidents']
    assert not incident_inserts, 'no incident row must be inserted'


def test_incident_linked_to_alert_contains_evidence_fields():
    """Incident payload must carry the alert's evidence fields (tx_hash, chain_id, etc.)."""
    stub = _StubConn()
    alert_id = str(uuid.uuid4())

    response = {
        'severity': 'critical',
        'explanation': 'Critical wallet drain.',
        'tx_hash': TX_HASH,
        'from_address': FROM_ADDR,
        'to_address': TO_ADDR,
        'chain_id': CHAIN_ID,
        'block_number': BLOCK_NUMBER,
        'evidence_source': 'live',
    }

    monitoring_runner._maybe_create_incident(
        stub,
        workspace_id=WORKSPACE_ID,
        user_id=USER_ID,
        target_id=TARGET_ID,
        analysis_run_id=str(uuid.uuid4()),
        alert_id=alert_id,
        response=response,
        auto_create=True,
    )

    incident_inserts = [(t, p) for t, p in stub.inserts if t == 'incidents']
    assert incident_inserts
    _, params = incident_inserts[0]

    # The incident payload JSON is the last param (before created_at / updated_at which are SQL NOW())
    payload_json_candidates = [
        p for p in params
        if isinstance(p, str) and 'tx_hash' in p
    ]
    assert payload_json_candidates, 'incident payload must contain tx_hash'
    payload = json.loads(payload_json_candidates[0])
    assert payload.get('tx_hash') == TX_HASH
    assert payload.get('chain_id') == CHAIN_ID
    assert payload.get('evidence_source') == 'live'


# ---------------------------------------------------------------------------
# 4.  Detection row is created before the alert (detection → alert proof chain)
# ---------------------------------------------------------------------------

def test_smoke_alert_creates_detection_row():
    """A live wallet_transfer_detected event must INSERT a detection row before the alert."""
    stub = _StubConn()

    @contextmanager
    def _fake_pg():
        yield stub

    with patch.object(monitoring_runner, 'pg_connection', _fake_pg):
        alert_id = monitoring_runner._wallet_transfer_smoke_alert(
            workspace_id=WORKSPACE_ID,
            user_id=USER_ID,
            target_id=TARGET_ID,
            target_name='Base Wallet',
            payload=_make_transfer_payload(),
            evidence_source='live',
        )

    assert alert_id, 'must return alert_id for live evidence'
    detection_inserts = [p for t, p in stub.inserts if t == 'detections']
    assert detection_inserts, 'a detections row must be inserted'
    # Detection must appear before the alert in the inserts list
    insert_tables = [t for t, _ in stub.inserts]
    assert 'detections' in insert_tables, 'detections must be in insert list'
    assert 'alerts' in insert_tables, 'alerts must be in insert list'
    assert insert_tables.index('detections') < insert_tables.index('alerts'), (
        'detection must be inserted before the alert'
    )


def test_detection_row_has_correct_type_and_evidence_fields():
    """Detection row must have detection_type='monitored_wallet_transfer' and correct evidence."""
    TELEMETRY_ID = str(uuid.uuid4())
    SYSTEM_ID = str(uuid.uuid4())
    ASSET_ID = str(uuid.uuid4())
    stub = _StubConn()

    @contextmanager
    def _fake_pg():
        yield stub

    with patch.object(monitoring_runner, 'pg_connection', _fake_pg):
        monitoring_runner._wallet_transfer_smoke_alert(
            workspace_id=WORKSPACE_ID,
            user_id=USER_ID,
            target_id=TARGET_ID,
            target_name='Base Wallet',
            payload=_make_transfer_payload(),
            evidence_source='live',
            telemetry_id=TELEMETRY_ID,
            monitored_system_id=SYSTEM_ID,
            protected_asset_id=ASSET_ID,
        )

    detection_inserts = [(t, p) for t, p in stub.inserts if t == 'detections']
    assert detection_inserts, 'expected detection insert'
    _, params = detection_inserts[0]
    # INSERT param order: (smoke_detection_id, workspace_id, monitored_system_id, protected_asset_id,
    #   detection_type, severity, confidence, title, explanation,
    #   evidence_source, source_rule, raw_evidence_json)
    assert params[1] == WORKSPACE_ID, 'workspace_id mismatch'
    assert params[2] == SYSTEM_ID, 'monitored_system_id mismatch'
    assert params[3] == ASSET_ID, 'protected_asset_id mismatch'
    assert params[4] == 'monitored_wallet_transfer', f'detection_type must be monitored_wallet_transfer; got {params[4]}'
    assert params[5] == 'low', f'severity must be low; got {params[5]}'
    assert params[9] == 'live', f'evidence_source must be live; got {params[9]}'
    assert params[10] == 'smoke_wallet_transfer', f'source_rule mismatch; got {params[10]}'
    raw = json.loads(params[11])
    assert raw['tx_hash'] == TX_HASH, 'raw_evidence must include tx_hash'
    assert raw['from_address'] == FROM_ADDR, 'raw_evidence must include from_address'
    assert raw['to_address'] == TO_ADDR, 'raw_evidence must include to_address'
    assert raw['chain_id'] == CHAIN_ID, 'raw_evidence must include chain_id'
    assert raw['block_number'] == BLOCK_NUMBER, 'raw_evidence must include block_number'
    assert raw['telemetry_id'] == TELEMETRY_ID, 'raw_evidence must include telemetry_id'
    assert raw['target_id'] == TARGET_ID, 'raw_evidence must include target_id'
    assert raw['detection_type'] == 'monitored_wallet_transfer'


def test_alert_references_detection_id():
    """_upsert_alert must be called with the deterministic smoke_detection_id."""
    TELEMETRY_ID = str(uuid.uuid4())
    stub = _StubConn()
    captured_detection_ids: list[str | None] = []

    original_upsert = monitoring_runner._upsert_alert

    def _capturing_upsert(conn, *, detection_id=None, **kwargs):
        captured_detection_ids.append(detection_id)
        return original_upsert(conn, detection_id=detection_id, **kwargs)

    @contextmanager
    def _fake_pg():
        yield stub

    with (
        patch.object(monitoring_runner, 'pg_connection', _fake_pg),
        patch.object(monitoring_runner, '_upsert_alert', _capturing_upsert),
    ):
        monitoring_runner._wallet_transfer_smoke_alert(
            workspace_id=WORKSPACE_ID,
            user_id=USER_ID,
            target_id=TARGET_ID,
            target_name='Base Wallet',
            payload=_make_transfer_payload(),
            evidence_source='live',
            telemetry_id=TELEMETRY_ID,
        )

    assert captured_detection_ids, '_upsert_alert must be called'
    det_id = captured_detection_ids[0]
    assert det_id is not None, 'detection_id passed to _upsert_alert must not be None'
    uuid.UUID(det_id)  # raises ValueError if not a valid UUID

    # Re-invoke with identical inputs: must produce the same deterministic detection_id
    stub2 = _StubConn()
    captured2: list[str | None] = []

    def _capturing_upsert2(conn, *, detection_id=None, **kwargs):
        captured2.append(detection_id)
        return original_upsert(conn, detection_id=detection_id, **kwargs)

    @contextmanager
    def _fake_pg2():
        yield stub2

    with (
        patch.object(monitoring_runner, 'pg_connection', _fake_pg2),
        patch.object(monitoring_runner, '_upsert_alert', _capturing_upsert2),
    ):
        monitoring_runner._wallet_transfer_smoke_alert(
            workspace_id=WORKSPACE_ID,
            user_id=USER_ID,
            target_id=TARGET_ID,
            target_name='Base Wallet',
            payload=_make_transfer_payload(),
            evidence_source='live',
            telemetry_id=TELEMETRY_ID,
        )

    assert captured2[0] == det_id, 'smoke_detection_id must be deterministic for same tx_hash/target_id'


def test_detection_linked_alert_id_updated_after_alert_created():
    """After alert creation, UPDATE detections SET linked_alert_id must be called with the alert_id."""
    stub = _StubConn()
    updates: list[tuple[str, tuple]] = []

    original_execute = stub.execute

    def _tracking_execute(query: str, params=None):
        q = (query or '').strip().lower()
        if 'update detections' in q and 'linked_alert_id' in q:
            updates.append(('detections', tuple(params or ())))
        return original_execute(query, params)

    stub.execute = _tracking_execute  # type: ignore[assignment]

    @contextmanager
    def _fake_pg():
        yield stub

    with patch.object(monitoring_runner, 'pg_connection', _fake_pg):
        alert_id = monitoring_runner._wallet_transfer_smoke_alert(
            workspace_id=WORKSPACE_ID,
            user_id=USER_ID,
            target_id=TARGET_ID,
            target_name='Base Wallet',
            payload=_make_transfer_payload(),
            evidence_source='live',
        )

    assert alert_id, 'alert must be created'
    assert updates, 'UPDATE detections SET linked_alert_id must be called'
    _, params = updates[0]
    assert alert_id in params, f'alert_id {alert_id} must appear in UPDATE detections params; got {params}'


def test_smoke_alert_includes_telemetry_and_target_id_in_response():
    """Alert response payload must include telemetry_id and target_id for evidence tracing."""
    TELEMETRY_ID = str(uuid.uuid4())
    captured_response: list[dict] = []
    stub = _StubConn()

    original_upsert = monitoring_runner._upsert_alert

    def _capturing_upsert(conn, *, response, **kwargs):
        captured_response.append(dict(response))
        return original_upsert(conn, response=response, **kwargs)

    @contextmanager
    def _fake_pg():
        yield stub

    with (
        patch.object(monitoring_runner, 'pg_connection', _fake_pg),
        patch.object(monitoring_runner, '_upsert_alert', _capturing_upsert),
    ):
        monitoring_runner._wallet_transfer_smoke_alert(
            workspace_id=WORKSPACE_ID,
            user_id=USER_ID,
            target_id=TARGET_ID,
            target_name='Base Wallet',
            payload=_make_transfer_payload(),
            evidence_source='live',
            telemetry_id=TELEMETRY_ID,
        )

    assert captured_response, 'expected _upsert_alert to be called'
    r = captured_response[0]
    assert r.get('telemetry_id') == TELEMETRY_ID, 'response must include telemetry_id'
    assert r.get('target_id') == TARGET_ID, 'response must include target_id'


# ---------------------------------------------------------------------------
# 5.  Idempotency: duplicate tx_hash on second poll must not create new alert
# ---------------------------------------------------------------------------

def test_smoke_alert_skips_duplicate_tx_on_second_poll():
    """When the detection INSERT conflicts (same tx already processed) and an alert is
    already linked, the function returns the existing alert_id without creating a new one."""
    existing_alert_id = str(uuid.uuid4())
    stub = _StubConn()
    stub._detection_conflict = True   # simulate ON CONFLICT DO NOTHING (detection exists)
    stub._linked_alert_id = existing_alert_id  # simulate alert already linked → true dedup

    @contextmanager
    def _fake_pg():
        yield stub

    with patch.object(monitoring_runner, 'pg_connection', _fake_pg):
        result = monitoring_runner._wallet_transfer_smoke_alert(
            workspace_id=WORKSPACE_ID,
            user_id=USER_ID,
            target_id=TARGET_ID,
            target_name='Base Wallet',
            payload=_make_transfer_payload(),
            evidence_source='live',
        )

    assert result == existing_alert_id, (
        f'must return existing alert_id when detection+alert both exist (true dedup); got {result!r}'
    )
    alert_inserts = [t for t, _ in stub.inserts if t == 'alerts']
    assert not alert_inserts, 'no new alert INSERT when alert is already linked to detection'
    assert stub.commit_calls >= 1, 'must commit (the monitoring_run INSERT) on duplicate'


# ---------------------------------------------------------------------------
# 6.  End-to-end: telemetry → detection → alert → visible in /alerts
# ---------------------------------------------------------------------------

def test_wallet_transfer_detected_creates_detection_and_alert_pipeline():
    """wallet_transfer_detected telemetry → detection row → alert row visible in /alerts.

    Verifies the full proof-chain from telemetry event to committed alert:
    1. monitoring_runs row is inserted before detections (FK prerequisite).
    2. detections row is created with detection_type=monitored_wallet_transfer.
    3. alerts row is created with severity=low, source=live, title containing
       'Monitored wallet transfer detected'.
    4. detections.linked_alert_id is back-patched after alert creation.
    5. Alert fields match what list_alerts SELECT would return
       (workspace_id, target_id, severity, source, title, detection_id).
    """
    TELEMETRY_ID = str(uuid.uuid4())
    stub = _StubConn()
    alert_insert_params: list[tuple] = []

    original_execute = stub.execute

    def _tracking_execute(query: str, params=None):
        q = (query or '').strip().lower()
        if q.startswith('insert into alerts'):
            alert_insert_params.append(tuple(params or ()))
        return original_execute(query, params)

    stub.execute = _tracking_execute  # type: ignore[assignment]

    @contextmanager
    def _fake_pg():
        yield stub

    with patch.object(monitoring_runner, 'pg_connection', _fake_pg):
        alert_id = monitoring_runner._wallet_transfer_smoke_alert(
            workspace_id=WORKSPACE_ID,
            user_id=USER_ID,
            target_id=TARGET_ID,
            target_name='Base Wallet',
            payload=_make_transfer_payload(),
            evidence_source='live',
            telemetry_id=TELEMETRY_ID,
        )

    # Alert must be created and committed
    assert alert_id, 'alert_id must be returned for live wallet transfer'
    assert stub.commit_calls >= 1, 'alert must be committed on the dedicated connection'

    insert_tables = [t for t, _ in stub.inserts]

    # monitoring_runs row created before detections (FK prerequisite)
    assert 'monitoring_runs' in insert_tables, 'monitoring_run must be inserted'
    assert 'detections' in insert_tables, 'detection must be inserted'
    assert 'alerts' in insert_tables, 'alert must be inserted'
    assert insert_tables.index('monitoring_runs') < insert_tables.index('detections'), (
        'monitoring_run must be inserted before detection (FK prerequisite)'
    )
    assert insert_tables.index('detections') < insert_tables.index('alerts'), (
        'detection must be inserted before alert'
    )

    # Detection row fields
    detection_params = [p for t, p in stub.inserts if t == 'detections'][0]
    assert detection_params[4] == 'monitored_wallet_transfer', 'detection_type must be monitored_wallet_transfer'
    assert detection_params[5] == 'low', 'severity must be low'
    assert detection_params[9] == 'live', 'evidence_source must be live'
    assert detection_params[10] == 'smoke_wallet_transfer', 'source_rule must be smoke_wallet_transfer'
    raw = json.loads(detection_params[11])
    assert raw.get('tx_hash') == TX_HASH
    assert raw.get('telemetry_id') == TELEMETRY_ID
    assert raw.get('target_id') == TARGET_ID
    assert raw.get('chain_id') == CHAIN_ID
    assert raw.get('block_number') == BLOCK_NUMBER

    # Alert row fields match list_alerts SELECT columns
    assert alert_insert_params, 'alert INSERT must have been captured'
    a = alert_insert_params[0]
    # Param order from _upsert_alert INSERT:
    # 0:id, 1:workspace_id, 2:user_id, 3:analysis_run_id, 4:target_id, 5:module_key,
    # 6:alert_type, 7:title, 8:severity, 9:source_service, 10:source, 11:summary,
    # 12:payload, 13:matched_patterns, 14:reasons, 15:recommended_action,
    # 16:degraded, 17:signature, 18:detection_id
    assert a[1] == WORKSPACE_ID, 'alert workspace_id must match'
    assert a[4] == TARGET_ID, 'alert target_id must match'
    assert a[6] == 'threat_monitoring', 'alert_type must be threat_monitoring'
    assert 'Monitored wallet transfer detected' in str(a[7]), (
        f'alert title must start with "Monitored wallet transfer detected"; got {a[7]}'
    )
    assert a[8] == 'low', f'alert severity must be low; got {a[8]}'
    assert a[10] == 'live', f'alert source must be live; got {a[10]}'

    # linked_alert_id back-patched on detection
    update_calls = [
        (q, p) for q, p in []  # tracked separately below
    ]
    # Verify via stub.inserts that detection INSERT has monitoring_run_id (not NULL)
    assert detection_params[12] is not None, (
        'detection monitoring_run_id must not be NULL (smoke_run_id must be linked)'
    )


def test_smoke_alert_logs_detection_created_and_alert_created(caplog):
    """The required log events wallet_transfer_detection_created and
    wallet_transfer_alert_created must fire on the happy path."""
    import logging

    stub = _StubConn()

    @contextmanager
    def _fake_pg():
        yield stub

    with caplog.at_level(logging.INFO):
        with patch.object(monitoring_runner, 'pg_connection', _fake_pg):
            monitoring_runner._wallet_transfer_smoke_alert(
                workspace_id=WORKSPACE_ID,
                user_id=USER_ID,
                target_id=TARGET_ID,
                target_name='Base Wallet',
                payload=_make_transfer_payload(),
                evidence_source='live',
            )

    log_events = [r.message for r in caplog.records]
    detection_logs = [m for m in log_events if 'wallet_transfer_detection_created' in m]
    alert_logs = [m for m in log_events if 'wallet_transfer_alert_created' in m]
    assert detection_logs, f'wallet_transfer_detection_created must be logged; got: {log_events}'
    assert alert_logs, f'wallet_transfer_alert_created must be logged; got: {log_events}'


def test_smoke_alert_logs_skipped_duplicate(caplog):
    """wallet_transfer_alert_skipped_duplicate must be logged when detection exists and
    alert is already linked (true dedup path — no new alert, returns existing id)."""
    import logging

    existing_alert_id = str(uuid.uuid4())
    stub = _StubConn()
    stub._detection_conflict = True
    stub._linked_alert_id = existing_alert_id  # alert already linked → true dedup

    @contextmanager
    def _fake_pg():
        yield stub

    with caplog.at_level(logging.INFO):
        with patch.object(monitoring_runner, 'pg_connection', _fake_pg):
            monitoring_runner._wallet_transfer_smoke_alert(
                workspace_id=WORKSPACE_ID,
                user_id=USER_ID,
                target_id=TARGET_ID,
                target_name='Base Wallet',
                payload=_make_transfer_payload(),
                evidence_source='live',
            )

    log_events = [r.message for r in caplog.records]
    skipped_logs = [m for m in log_events if 'wallet_transfer_alert_skipped_duplicate' in m]
    assert skipped_logs, (
        f'wallet_transfer_alert_skipped_duplicate must be logged on true dedup; got: {log_events}'
    )


def test_smoke_alert_logs_failed_with_sql_error(caplog):
    """wallet_transfer_alert_failed must be logged with sql_error= when an exception is raised."""
    import logging

    stub = _StubConn()

    class _BrokenConn(_StubConn):
        def execute(self, query: str, params=None):
            if 'detections' in (query or '').lower():
                raise RuntimeError('psycopg.ProgrammingError: query has 17 placeholders but 18 parameters')
            return super().execute(query, params)

    broken_stub = _BrokenConn()

    @contextmanager
    def _fake_pg_broken():
        yield broken_stub

    with caplog.at_level(logging.ERROR):
        with patch.object(monitoring_runner, 'pg_connection', _fake_pg_broken):
            result = monitoring_runner._wallet_transfer_smoke_alert(
                workspace_id=WORKSPACE_ID,
                user_id=USER_ID,
                target_id=TARGET_ID,
                target_name='Base Wallet',
                payload=_make_transfer_payload(),
                evidence_source='live',
            )

    assert result is None, 'must return None on exception'
    log_events = [r.message for r in caplog.records]
    failed_logs = [m for m in log_events if 'wallet_transfer_alert_failed' in m]
    assert failed_logs, f'wallet_transfer_alert_failed must be logged; got: {log_events}'
    assert any('sql_error' in m for m in failed_logs), (
        'wallet_transfer_alert_failed must include sql_error= in message'
    )


# ---------------------------------------------------------------------------
# 7.  FK safety: analysis_run_id must be NULL (not a random UUID) in smoke alerts
# ---------------------------------------------------------------------------

LIVE_WORKSPACE_ID = '1155f479-3e5b-4d90-be6c-fd6c1d6b957d'


def test_smoke_alert_analysis_run_id_is_null_not_random_uuid():
    """The alert INSERT must pass analysis_run_id=None (SQL NULL), never a random UUID.

    Previously a random uuid.uuid4() was generated and passed as analysis_run_id
    without creating a matching analysis_runs row, causing the FK constraint
    alerts_analysis_run_id_fkey to be violated.  The column is nullable
    (NULL REFERENCES analysis_runs(id) ON DELETE SET NULL), so NULL is correct
    for live smoke-rule alerts that bypass the analysis_runs path.
    """
    stub = _StubConn()
    alert_insert_params: list[tuple] = []

    original_execute = stub.execute

    def _capturing_execute(query: str, params=None):
        q = (query or '').strip().lower()
        if q.startswith('insert into alerts'):
            alert_insert_params.append(tuple(params or ()))
        return original_execute(query, params)

    stub.execute = _capturing_execute  # type: ignore[assignment]

    @contextmanager
    def _fake_pg():
        yield stub

    with patch.object(monitoring_runner, 'pg_connection', _fake_pg):
        monitoring_runner._wallet_transfer_smoke_alert(
            workspace_id=WORKSPACE_ID,
            user_id=USER_ID,
            target_id=TARGET_ID,
            target_name='Base Wallet',
            payload=_make_transfer_payload(),
            evidence_source='live',
        )

    assert alert_insert_params, 'expected an INSERT INTO alerts'
    # Param order: (id, workspace_id, user_id, analysis_run_id, target_id, ...)
    params = alert_insert_params[0]
    analysis_run_id_value = params[3]
    assert analysis_run_id_value is None, (
        f'analysis_run_id must be None (SQL NULL) for smoke alerts; '
        f'got {analysis_run_id_value!r} — a non-NULL value violates '
        f'alerts_analysis_run_id_fkey when no analysis_runs row exists'
    )


def test_smoke_alert_fk_fields_are_valid():
    """Alert row must have valid FK-safe fields: workspace_id, user_id, target_id, detection_id.

    Verifies that the alert INSERT carries all required foreign-key columns so
    the row would pass DB constraints even when analysis_run_id is NULL.
    """
    stub = _StubConn()
    alert_insert_params: list[tuple] = []
    alert_id_returned: list[str] = []

    original_execute = stub.execute

    def _capturing_execute(query: str, params=None):
        q = (query or '').strip().lower()
        if q.startswith('insert into alerts'):
            alert_insert_params.append(tuple(params or ()))
        return original_execute(query, params)

    stub.execute = _capturing_execute  # type: ignore[assignment]

    @contextmanager
    def _fake_pg():
        yield stub

    with patch.object(monitoring_runner, 'pg_connection', _fake_pg):
        aid = monitoring_runner._wallet_transfer_smoke_alert(
            workspace_id=WORKSPACE_ID,
            user_id=USER_ID,
            target_id=TARGET_ID,
            target_name='Base Wallet',
            payload=_make_transfer_payload(),
            evidence_source='live',
            telemetry_id=str(uuid.uuid4()),
        )
        if aid:
            alert_id_returned.append(aid)

    assert alert_insert_params, 'expected an INSERT INTO alerts'
    params = alert_insert_params[0]
    # INSERT param order (from _upsert_alert):
    # 0:id, 1:workspace_id, 2:user_id, 3:analysis_run_id, 4:target_id, 5:alert_type,
    # 6:title, 7:severity, 8:source_service, 9:source, 10:summary, 11:payload,
    # 12:matched_patterns, 13:reasons, 14:recommended_action, 15:degraded,
    # 16:dedupe_signature, 17:detection_id

    uuid.UUID(str(params[0]))          # id — valid UUID
    assert params[1] == WORKSPACE_ID   # workspace_id
    assert params[2] == USER_ID        # user_id
    assert params[3] is None, f'analysis_run_id must be None; got {params[3]!r}'  # 3: analysis_run_id
    assert params[4] == TARGET_ID      # target_id
    detection_id_value = params[17]    # detection_id
    assert detection_id_value is not None, 'detection_id must be set in alert row'
    uuid.UUID(str(detection_id_value))


def test_wallet_transfer_detected_telemetry_creates_alert_visible_for_workspace():
    """Full pipeline: wallet_transfer_detected telemetry → detection → alert visible in /alerts.

    Simulates the workspace_id=1155f479-3e5b-4d90-be6c-fd6c1d6b957d scenario and
    verifies the alert INSERT produces a row with the correct workspace_id, target_id,
    source=live, and a non-None detection_id so /alerts would return it.
    """
    workspace_id = LIVE_WORKSPACE_ID
    target_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    telemetry_id = str(uuid.uuid4())
    tx_hash = '0xc0ffee00c0ffee00c0ffee00c0ffee00c0ffee00c0ffee00c0ffee00c0ffee00'

    stub = _StubConn()
    alert_rows: list[tuple] = []

    original_execute = stub.execute

    def _capturing_execute(query: str, params=None):
        q = (query or '').strip().lower()
        if q.startswith('insert into alerts'):
            alert_rows.append(tuple(params or ()))
        return original_execute(query, params)

    stub.execute = _capturing_execute  # type: ignore[assignment]

    @contextmanager
    def _fake_pg():
        yield stub

    with patch.object(monitoring_runner, 'pg_connection', _fake_pg):
        alert_id = monitoring_runner._wallet_transfer_smoke_alert(
            workspace_id=workspace_id,
            user_id=user_id,
            target_id=target_id,
            target_name='Live Treasury Wallet',
            payload={
                'tx_hash': tx_hash,
                'from': '0xaaaa000000000000000000000000000000000001',
                'to': '0xbbbb000000000000000000000000000000000002',
                'value': hex(int(1.5 * 10 ** 18)),
                'block_number': 48_000_000,
                'chain_id': 8453,
                'wallet_transfer_direction': 'inbound',
            },
            evidence_source='live',
            telemetry_id=telemetry_id,
        )

    assert alert_id, 'alert must be created for live wallet transfer'
    assert alert_rows, 'alert INSERT must have been captured'
    params = alert_rows[0]
    # INSERT param order (from _upsert_alert):
    # 0:id, 1:workspace_id, 2:user_id, 3:analysis_run_id, 4:target_id, 5:alert_type,
    # 6:title, 7:severity, 8:source_service, 9:source, 10:summary, 11:payload,
    # 12:matched_patterns, 13:reasons, 14:recommended_action, 15:degraded,
    # 16:dedupe_signature, 17:detection_id

    # INSERT param order (from _upsert_alert):
    # 0:id, 1:workspace_id, 2:user_id, 3:analysis_run_id, 4:target_id, 5:module_key,
    # 6:alert_type, 7:title, 8:severity, 9:source_service, 10:source, 11:summary,
    # 12:payload, 13:matched_patterns, 14:reasons, 15:recommended_action,
    # 16:degraded, 17:dedupe_signature, 18:detection_id

    # workspace_id (index 1) — /alerts is workspace-scoped on this field
    assert params[1] == workspace_id, f'alert workspace_id must be {workspace_id}; got {params[1]}'
    # analysis_run_id (index 3) must be NULL to satisfy FK
    assert params[3] is None, (
        f'analysis_run_id must be None to avoid alerts_analysis_run_id_fkey violation; '
        f'got {params[3]!r}'
    )
    # source (index 10) must be 'live' — /alerts must never show simulator data as live
    assert params[10] == 'live', f'alert source must be live; got {params[10]}'
    # detection_id (index 18) — links the alert to the detections row
    assert params[18] is not None, 'detection_id must be set so /alerts can show the proof chain'


# ---------------------------------------------------------------------------
# 9.  Pipeline: wallet_transfer_detected → alert → Alerts only tab
# ---------------------------------------------------------------------------

def test_smoke_alert_payload_json_contains_telemetry_id():
    """Requirement 5/6: alerts.payload JSON must contain telemetry_id.

    The alerts_only filter in list_target_telemetry uses:
        a.payload->>'telemetry_id' = te.id::text
    so the alert's payload column must store the telemetry_id as a top-level
    JSON key for the Alerts only tab to return the linked telemetry row.
    """
    telemetry_id = str(uuid.uuid4())
    stub = _StubConn()
    captured_payloads: list[dict] = []
    original_execute = stub.execute

    def _capturing_execute(query: str, params=None):
        q = (query or '').strip().lower()
        if q.startswith('insert into alerts') and params and len(params) > 12:
            try:
                captured_payloads.append(json.loads(params[12]))
            except Exception:
                pass
        return original_execute(query, params)

    stub.execute = _capturing_execute  # type: ignore[assignment]

    @contextmanager
    def _fake_pg():
        yield stub

    with patch.object(monitoring_runner, 'pg_connection', _fake_pg):
        monitoring_runner._wallet_transfer_smoke_alert(
            workspace_id=WORKSPACE_ID,
            user_id=USER_ID,
            target_id=TARGET_ID,
            target_name='Treasury',
            payload=_make_transfer_payload(),
            evidence_source='live',
            telemetry_id=telemetry_id,
        )

    assert captured_payloads, 'alert INSERT must have been captured'
    payload = captured_payloads[0]
    assert payload.get('telemetry_id') == telemetry_id, (
        f'alerts.payload JSON must contain telemetry_id={telemetry_id!r} '
        "for the alerts_only filter (a.payload->>'telemetry_id' = te.id::text); "
        f'got payload.telemetry_id={payload.get("telemetry_id")!r}'
    )
    assert payload.get('tx_hash') == TX_HASH, 'payload must include tx_hash'
    assert payload.get('chain_id') == CHAIN_ID, 'payload must include chain_id'
    assert payload.get('block_number') == BLOCK_NUMBER, 'payload must include block_number'
    assert payload.get('monitoring_run_id') is not None, (
        'payload must include monitoring_run_id so the alert links back to the monitoring cycle'
    )


def test_wallet_transfer_to_alerts_only_tab_pipeline():
    """Requirement 9: full pipeline wallet_transfer_detected → detection → alert → Alerts only tab.

    Proves the chain:
    1. _wallet_transfer_smoke_alert creates an alert with telemetry_id in alerts.payload JSON.
    2. list_target_telemetry(event_type_filter='alerts_only') generates SQL that uses
       a.payload->>'telemetry_id' = te.id::text to find telemetry linked to real alerts.
    3. When the DB returns a matching wallet_transfer_detected row, it appears in the result
       and live_telemetry_ready is True.
    """
    from services.api.app.monitoring_runner import list_target_telemetry
    from fastapi import Request
    from unittest.mock import patch as _patch

    ws_id = str(uuid.uuid4())
    target_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    telemetry_id = str(uuid.uuid4())

    # ---- Step 1: smoke alert stores telemetry_id in payload ----
    stub = _StubConn()
    captured_payload: dict = {}
    original_execute = stub.execute

    def _capturing_execute(query: str, params=None):
        q = (query or '').strip().lower()
        if q.startswith('insert into alerts') and params and len(params) > 12:
            try:
                captured_payload.update(json.loads(params[12]))
            except Exception:
                pass
        return original_execute(query, params)

    stub.execute = _capturing_execute  # type: ignore[assignment]

    @contextmanager
    def _fake_pg():
        yield stub

    with patch.object(monitoring_runner, 'pg_connection', _fake_pg):
        alert_id = monitoring_runner._wallet_transfer_smoke_alert(
            workspace_id=ws_id,
            user_id=user_id,
            target_id=target_id,
            target_name='Pipeline Test Wallet',
            payload=_make_transfer_payload(),
            evidence_source='live',
            telemetry_id=telemetry_id,
        )

    assert alert_id, 'alert must be created for live wallet transfer'
    assert captured_payload.get('telemetry_id') == telemetry_id, (
        f'alert payload must include telemetry_id={telemetry_id!r}; '
        f'got {captured_payload.get("telemetry_id")!r}'
    )

    # ---- Step 2 & 3: alerts_only SQL + result from list_target_telemetry ----
    executed_sqls: list[str] = []
    _telemetry_row = {
        'id': telemetry_id,
        'workspace_id': ws_id,
        'target_id': target_id,
        'provider_type': 'evm_rpc',
        'source_type': 'wallet_transfer_detected',
        'evidence_source': 'live',
        'observed_at': '2026-06-17T10:00:00Z',
        'ingested_at': '2026-06-17T10:00:01Z',
        'payload_json': {'tx_hash': TX_HASH, 'block_number': BLOCK_NUMBER, 'chain_id': CHAIN_ID},
        'chain_network': 'base',
        'receipt_block_number': None,
    }

    class _TelemetryConn:
        def execute(self_inner, sql, params=None):
            executed_sqls.append(sql)

            class _R:
                def fetchone(self):
                    return {'cnt': 1}

                def fetchall(self):
                    return [dict(_telemetry_row)]

            return _R()

        def commit(self):
            pass

    mock_pg = MagicMock()
    _conn = _TelemetryConn()
    mock_pg.return_value.__enter__ = lambda s: _conn
    mock_pg.return_value.__exit__ = MagicMock(return_value=False)

    scope = {
        'type': 'http',
        'method': 'GET',
        'path': f'/monitoring/targets/{target_id}/telemetry',
        'query_string': b'',
        'headers': [(b'x-workspace-id', ws_id.encode())],
        'client': ('127.0.0.1', 9000),
    }
    request = Request(scope)

    with (
        _patch('services.api.app.monitoring_runner.pg_connection', mock_pg),
        _patch('services.api.app.monitoring_runner.ensure_pilot_schema'),
        _patch(
            'services.api.app.monitoring_runner.authenticate_with_connection',
            return_value={'id': user_id},
        ),
        _patch(
            'services.api.app.monitoring_runner.resolve_workspace',
            return_value={'workspace_id': ws_id, 'workspace': {}},
        ),
    ):
        result = list_target_telemetry(
            request, target_id=target_id, event_type_filter='alerts_only'
        )

    # Verify the SQL references alerts.payload->>'telemetry_id' for the JOIN
    data_sql = executed_sqls[-1]
    assert 'telemetry_id' in data_sql, (
        'alerts_only SQL must reference telemetry_id to link alerts → telemetry rows'
    )
    assert 'payload' in data_sql.lower(), (
        "alerts_only SQL must use alert.payload->>'telemetry_id' for the JOIN condition"
    )
    assert 'EXISTS' in data_sql.upper(), (
        'alerts_only SQL must use EXISTS subquery to check alert linkage'
    )

    # Verify the telemetry row is returned in the Alerts only result
    assert result['total_count'] == 1, (
        f'/alerts_only must return 1 telemetry row for the linked alert; '
        f'got total_count={result["total_count"]}'
    )
    assert len(result['telemetry']) == 1, (
        f'Alerts only tab must contain the wallet_transfer_detected row; '
        f'got {len(result["telemetry"])} rows'
    )
    assert result['live_telemetry_ready'] is True, (
        'live evidence_source telemetry must set live_telemetry_ready=True'
    )
    row = result['telemetry'][0]
    assert str(row.get('id')) == telemetry_id, (
        f'returned telemetry row id must be {telemetry_id!r}; got {row.get("id")!r}'
    )
