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

class _StubConn:
    """Tracks all INSERT/UPDATE/SELECT calls without real DB."""

    def __init__(self):
        self.inserts: list[tuple[str, tuple]] = []
        self.commit_calls = 0
        self._suppression_row = None  # no suppression by default
        self._existing_alert_row = None  # no dedup match by default

    def execute(self, query: str, params=None):
        q = (query or '').strip().lower()
        if q.startswith('insert into'):
            table = q.split('insert into')[1].strip().split('(')[0].strip().split()[0]
            self.inserts.append((table, tuple(params or ())))
        if 'alert_suppression_rules' in q and 'select' in q:
            return _Rows([self._suppression_row] if self._suppression_row else [])
        if q.startswith('select') and 'from alerts' in q:
            return _Rows([self._existing_alert_row] if self._existing_alert_row else [])
        return _Rows([])

    def commit(self):
        self.commit_calls += 1

    @contextmanager
    def transaction(self):
        yield


class _Rows:
    def __init__(self, rows=None):
        self._rows = list(rows or [])

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


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
    ):
        with pytest.raises(RuntimeError, match='analysis_unavailable'):
            monitoring_runner.process_monitoring_target(outer_stub, target)

    assert 'smoke_alert_commit' in order, 'smoke alert commit must fire'
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
    # severity is at index 7
    assert params[7] == 'low', f'alert severity must be low; got {params[7]}'
    # title is at index 6
    assert 'wallet transfer detected' in str(params[6]).lower(), (
        f'alert title must mention wallet transfer; got {params[6]}'
    )
    # workspace_id at index 1
    assert params[1] == WORKSPACE_ID
    # target_id at index 4
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
