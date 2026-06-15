"""
Regression tests for wallet-transfer telemetry durability.

Production incident: a Base wallet transfer was detected
(wallet_transfers_detected=1) but the subsequent live threat analysis raised
``analysis_unavailable:live_engine_unavailable``. Because the raw telemetry INSERT
and the threat analysis shared one DB transaction, the rollback deleted the detected
telemetry row, so UI search by tx_hash / block_number returned nothing.

Fix under test (services/api/app/monitoring_runner.py):
  - detected wallet transfers are persisted AND committed on a dedicated connection
    BEFORE threat analysis runs, so an analysis failure can no longer roll back the
    raw live evidence;
  - the commit is verified (count by target_id + tx_hash) and logged
    (wallet_transfer_telemetry_committed ... persisted=true/false);
  - list_target_telemetry continues to find the row by tx_hash and block_number.
"""
from __future__ import annotations

import json as _json
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from services.api.app import monitoring_runner
from services.api.app.activity_providers import ActivityProviderResult
from services.api.app.evm_activity_provider import ActivityEvent

# Production evidence from the incident report.
PROD_TARGET_ID = 'e7851a52-8fb1-48cd-84a3-d033f591c5dd'
WALLET_ADDR = '0x5f6f35fd8b10c5576089f99c7c8c351deb851d1f'
COUNTERPARTY = '0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
TX_HASH = '0x42eb6fb953a32dc80fef0f62b4eadfa0fed18c7129d68924cd65bdb37e25a517'
BLOCK_NUM = 47286578
BASE_CHAIN_ID = 8453


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class _Rows:
    def __init__(self, rows=None):
        self._rows = list(rows or [])
        self.rowcount = len(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _DedicatedConn:
    """Stands in for the independent pg_connection() used to commit raw evidence."""

    def __init__(self, verify_count: int = 1):
        self.telemetry_inserts: list[tuple] = []
        self.commit_calls = 0
        self._verify_count = verify_count

    def execute(self, query, params=None):
        q = (query or '').strip().lower()
        if q.startswith('insert into') and 'telemetry_events' in q:
            self.telemetry_inserts.append(tuple(params or ()))
            return _Rows([])
        if q.startswith('select count(*)') and 'telemetry_events' in q:
            # Verification query: report the row as present only after it was inserted.
            return _Rows([{'c': self._verify_count if self.telemetry_inserts else 0}])
        return _Rows([])

    def commit(self):
        self.commit_calls += 1

    @contextmanager
    def transaction(self):
        yield


class _OuterConn:
    """The shared/outer monitoring connection (its transaction is rolled back on failure)."""

    def __init__(self, target):
        self._target = target
        self.telemetry_inserts: list[tuple] = []
        self.inserts: list[tuple[str, tuple]] = []

    def execute(self, query, params=None):
        q = (query or '').strip().lower()
        if q.startswith('insert into'):
            table = q.split('insert into')[1].strip().split('(')[0].strip().split()[0]
            self.inserts.append((table, tuple(params or ())))
            if table == 'telemetry_events':
                self.telemetry_inserts.append(tuple(params or ()))
            return _Rows([])
        if 'from workspaces' in q:
            return _Rows([{'id': self._target['workspace_id'], 'name': 'WS'}])
        if 'asset_registry' in q or 'from assets' in q:
            return _Rows([{'id': self._target['asset_id']}])
        return _Rows([])

    @contextmanager
    def transaction(self):
        yield


def _make_target(*, target_id=PROD_TARGET_ID):
    return {
        'id': target_id,
        'workspace_id': str(uuid.uuid4()),
        'asset_id': str(uuid.uuid4()),
        'monitored_system_id': str(uuid.uuid4()),
        'chain_network': 'base',
        'contract_identifier': None,
        'wallet_address': WALLET_ADDR,
        'name': 'Production Base Wallet',
        'target_type': 'wallet',
        'monitoring_checkpoint_cursor': None,
    }


def _make_wallet_transfer_event() -> ActivityEvent:
    payload = {
        'tx_hash': TX_HASH,
        'from': WALLET_ADDR,
        'to': COUNTERPARTY,
        'amount': '0.5',
        'value': hex(5 * 10 ** 17),
        'block_number': BLOCK_NUM,
        'chain_id': BASE_CHAIN_ID,
        'event_type': 'transaction',
        'source_type': 'rpc_polling',
        'wallet_transfer_direction': 'outbound',
        'observed_at': _utcnow().isoformat(),
    }
    return ActivityEvent(
        event_id='prod-event-id',
        kind='transaction',
        observed_at=_utcnow(),
        ingestion_source='polling',
        cursor=f'{BLOCK_NUM}:{TX_HASH}:-1',
        payload=payload,
    )


def _make_provider_result(events) -> ActivityProviderResult:
    return ActivityProviderResult(
        mode='live',
        status='live',
        evidence_state='REAL_EVIDENCE',
        truthfulness_state='NOT_CLAIM_SAFE',
        synthetic=False,
        provider_name='evm_activity_provider',
        provider_kind='rpc',
        evidence_present=True,
        recent_real_event_count=len(events),
        last_real_event_at=_utcnow(),
        events=list(events),
        latest_block=BLOCK_NUM,
        checkpoint=f'{BLOCK_NUM}:{TX_HASH}:-1',
        checkpoint_age_seconds=5,
        degraded_reason=None,
        error_code=None,
        source_type='rpc_polling',
        reason_code=None,
        claim_safe=False,
        detection_outcome='NO_CONFIRMED_ANOMALY_FROM_REAL_EVIDENCE',
    )


def _run_target(outer_conn, target, dedicated_conn, *, analysis_side_effect=None):
    @contextmanager
    def _fake_pg_connection():
        yield dedicated_conn

    process_patch = (
        patch.object(monitoring_runner, '_process_single_event', side_effect=analysis_side_effect)
        if analysis_side_effect is not None
        else patch.object(
            monitoring_runner,
            '_process_single_event',
            MagicMock(return_value={
                'analysis_run_id': str(uuid.uuid4()),
                'monitoring_state': 'real_event_no_anomaly',
                'alert_id': None,
                'incident_id': None,
                'detection_id': None,
                'protected_asset_coverage_record': None,
            }),
        )
    )

    with (
        patch.object(monitoring_runner, 'fetch_target_activity_result', return_value=_make_provider_result([_make_wallet_transfer_event()])),
        patch.object(monitoring_runner, 'pg_connection', _fake_pg_connection),
        patch.object(monitoring_runner, 'persist_analysis_run', MagicMock(return_value=str(uuid.uuid4()))),
        patch.object(monitoring_runner, '_load_checkpoint', return_value=0),
        process_patch,
    ):
        return monitoring_runner.process_monitoring_target(outer_conn, target)


# ---------------------------------------------------------------------------
# 1. Telemetry survives a threat-analysis failure (the production incident)
# ---------------------------------------------------------------------------

def test_wallet_transfer_persists_when_threat_analysis_raises():
    # Fix 3 (event_processing_failed wrapper): analysis failures are now swallowed at the
    # event level so the cursor and telemetry survive. process_monitoring_target returns
    # normally rather than propagating analysis_unavailable.
    target = _make_target()
    dedicated = _DedicatedConn(verify_count=1)
    outer = _OuterConn(target)

    _run_target(
        outer,
        target,
        dedicated,
        analysis_side_effect=RuntimeError('analysis_unavailable:live_engine_unavailable'),
    )

    # The dedicated connection committed the raw transfer BEFORE analysis raised.
    assert dedicated.commit_calls >= 1, 'raw telemetry must be committed on a dedicated connection'
    tx_rows = [p for p in dedicated.telemetry_inserts if any(TX_HASH in str(x) for x in p)]
    assert tx_rows, 'wallet transfer telemetry must be inserted on the dedicated connection'

    # Wallet transfer must NOT be on the outer shared connection — it must live on the
    # independently committed dedicated connection.
    # Coverage telemetry is legitimately written to the outer connection and its
    # payload_json includes the checkpoint string which embeds TX_HASH; exclude those
    # rows by checking for 'coverage' in any param (idempotency key or payload).
    outer_tx_rows = [
        p for p in outer.telemetry_inserts
        if any(TX_HASH in str(x) for x in p)
        and not any('coverage' in str(x).lower() for x in p)
    ]
    assert not outer_tx_rows, 'wallet transfer evidence must not be duplicated on the outer connection'


def test_wallet_transfer_committed_before_analysis_runs():
    """The dedicated commit must happen before _process_single_event is invoked."""
    target = _make_target()
    dedicated = _DedicatedConn(verify_count=1)
    outer = _OuterConn(target)

    order: list[str] = []

    original_commit = dedicated.commit

    def _tracking_commit():
        order.append('commit')
        original_commit()

    def _analysis(*args, **kwargs):
        order.append('analysis')
        raise RuntimeError('analysis_unavailable:live_engine_unavailable')

    dedicated.commit = _tracking_commit  # type: ignore[assignment]

    # Fix 3: analysis failure is swallowed, process_monitoring_target returns normally.
    _run_target(outer, target, dedicated, analysis_side_effect=_analysis)

    assert order, 'expected commit and analysis to be recorded'
    assert order.index('commit') < order.index('analysis'), (
        f'telemetry must be committed before analysis runs; order={order}'
    )


def test_wallet_transfer_commit_is_verified_and_logged(caplog):
    target = _make_target()
    dedicated = _DedicatedConn(verify_count=1)
    outer = _OuterConn(target)

    import logging
    with caplog.at_level(logging.INFO, logger='services.api.app.monitoring_runner'):
        _run_target(outer, target, dedicated)  # analysis succeeds (default stub)

    committed_logs = [r.getMessage() for r in caplog.records if 'wallet_transfer_telemetry_committed' in r.getMessage()]
    assert committed_logs, 'expected a wallet_transfer_telemetry_committed log line'
    assert any('persisted=true' in m for m in committed_logs), (
        f'commit log must report persisted=true after DB verification; logs={committed_logs}'
    )


def test_wallet_transfer_commit_failure_falls_back_to_shared_connection():
    """If the dedicated commit cannot run, evidence is still kept on the shared connection."""
    target = _make_target()
    outer = _OuterConn(target)

    @contextmanager
    def _broken_pg_connection():
        raise RuntimeError('dedicated connection unavailable')
        yield  # pragma: no cover

    with (
        patch.object(monitoring_runner, 'fetch_target_activity_result', return_value=_make_provider_result([_make_wallet_transfer_event()])),
        patch.object(monitoring_runner, 'pg_connection', _broken_pg_connection),
        patch.object(monitoring_runner, 'persist_analysis_run', MagicMock(return_value=str(uuid.uuid4()))),
        patch.object(monitoring_runner, '_load_checkpoint', return_value=0),
        patch.object(monitoring_runner, '_process_single_event', MagicMock(return_value={
            'analysis_run_id': str(uuid.uuid4()),
            'monitoring_state': 'real_event_no_anomaly',
            'alert_id': None,
            'incident_id': None,
            'detection_id': None,
            'protected_asset_coverage_record': None,
        })),
    ):
        monitoring_runner.process_monitoring_target(outer, target)

    fallback_rows = [p for p in outer.telemetry_inserts if any(TX_HASH in str(x) for x in p)]
    assert fallback_rows, 'on dedicated-commit failure the transfer must fall back to the shared connection'


def test_non_wallet_event_still_uses_shared_connection():
    """Non-wallet events keep using the outer transaction (no dedicated commit needed)."""
    target = _make_target()
    target['target_type'] = 'contract'
    target['wallet_address'] = None
    outer = _OuterConn(target)
    dedicated = _DedicatedConn(verify_count=1)

    contract_event = ActivityEvent(
        event_id='contract-event',
        kind='contract',
        observed_at=_utcnow(),
        ingestion_source='polling',
        cursor=f'{BLOCK_NUM}:{TX_HASH}:0',
        payload={'tx_hash': TX_HASH, 'block_number': BLOCK_NUM, 'event_type': 'contract'},
    )

    @contextmanager
    def _fake_pg_connection():
        yield dedicated

    with (
        patch.object(monitoring_runner, 'fetch_target_activity_result', return_value=_make_provider_result([contract_event])),
        patch.object(monitoring_runner, 'pg_connection', _fake_pg_connection),
        patch.object(monitoring_runner, 'persist_analysis_run', MagicMock(return_value=str(uuid.uuid4()))),
        patch.object(monitoring_runner, '_load_checkpoint', return_value=0),
        patch.object(monitoring_runner, '_process_single_event', MagicMock(return_value={
            'analysis_run_id': str(uuid.uuid4()),
            'monitoring_state': 'real_event_no_anomaly',
            'alert_id': None,
            'incident_id': None,
            'detection_id': None,
            'protected_asset_coverage_record': None,
        })),
    ):
        monitoring_runner.process_monitoring_target(outer, target)

    assert outer.telemetry_inserts, 'non-wallet telemetry must be written on the shared connection'
    assert dedicated.commit_calls == 0, 'non-wallet events must not open a dedicated commit'


# ---------------------------------------------------------------------------
# 2 + 3. UI search returns the persisted transfer by tx_hash and block_number
# ---------------------------------------------------------------------------

def _persisted_telemetry_row(target_id: str, workspace_id: str) -> dict:
    return {
        'id': str(uuid.uuid4()),
        'workspace_id': workspace_id,
        'target_id': target_id,
        'provider_type': 'evm_activity_provider',
        'source_type': 'wallet_transfer_detected',
        'evidence_source': 'live',
        'observed_at': _utcnow(),
        'ingested_at': _utcnow(),
        'payload_json': {
            'tx_hash': TX_HASH,
            'from': WALLET_ADDR,
            'to': COUNTERPARTY,
            'amount': '0.5',
            'block_number': BLOCK_NUM,
            'chain_id': BASE_CHAIN_ID,
            'source_type': 'rpc_polling',
        },
        'chain_network': 'base',
        'receipt_block_number': BLOCK_NUM,
    }


def _search_telemetry(query: str):
    target_id = PROD_TARGET_ID
    workspace_id = str(uuid.uuid4())
    captured: list[tuple] = []
    row = _persisted_telemetry_row(target_id, workspace_id)

    class _MockConn:
        def execute(self, q, params=None):
            captured.append((q, params))
            ql = (q or '').strip().lower()
            if 'telemetry_events' in ql and ql.startswith('select'):
                return _Rows([row])
            return _Rows([])

        @contextmanager
        def transaction(self):
            yield

    fake_user = {'id': str(uuid.uuid4()), 'workspace_id': workspace_id}
    fake_workspace = {'workspace_id': workspace_id, 'workspace': {'id': workspace_id}}
    fake_request = MagicMock()
    fake_request.headers = {'x-workspace-id': workspace_id}

    with (
        patch('services.api.app.monitoring_runner.pg_connection') as mock_pg,
        patch('services.api.app.monitoring_runner.ensure_pilot_schema'),
        patch('services.api.app.monitoring_runner.authenticate_with_connection', return_value=fake_user),
        patch('services.api.app.monitoring_runner.resolve_workspace', return_value=fake_workspace),
    ):
        mock_pg.return_value.__enter__ = lambda s: _MockConn()
        mock_pg.return_value.__exit__ = MagicMock(return_value=False)
        result = monitoring_runner.list_target_telemetry(fake_request, target_id=target_id, limit=50, q=query)
    return result, captured


def test_tx_hash_search_returns_persisted_transfer():
    result, captured = _search_telemetry(TX_HASH)

    telemetry_queries = [(q, p) for q, p in captured if 'telemetry_events' in (q or '').lower()]
    assert telemetry_queries, 'expected a telemetry_events query'
    sql, params = telemetry_queries[0]
    assert "payload_json->>'tx_hash'" in sql.lower(), 'search must filter on tx_hash'
    assert any(TX_HASH.lower() in str(p).lower() for p in (params or []))

    assert result['telemetry'], 'tx_hash search must return the persisted transfer'
    assert result['live_telemetry_ready'] is True
    payload = result['telemetry'][0]['payload_json']
    assert payload['tx_hash'] == TX_HASH


def test_block_number_search_returns_persisted_transfer():
    result, captured = _search_telemetry(str(BLOCK_NUM))

    telemetry_queries = [(q, p) for q, p in captured if 'telemetry_events' in (q or '').lower()]
    assert telemetry_queries, 'expected a telemetry_events query'
    sql, params = telemetry_queries[0]
    assert "payload_json->>'block_number'" in sql.lower(), 'search must filter on block_number'
    assert any(str(BLOCK_NUM) in str(p) for p in (params or []))

    assert result['telemetry'], 'block_number search must return the persisted transfer'
    assert result['telemetry'][0]['block_number'] == BLOCK_NUM


def test_search_exposes_required_ui_fields():
    """UI must be able to show tx_hash / from / to / amount / block_number."""
    result, _ = _search_telemetry(TX_HASH)
    assert result['telemetry'], 'expected at least one row'
    item = result['telemetry'][0]
    payload = item['payload_json']
    assert payload['tx_hash'] == TX_HASH
    assert payload['from'] == WALLET_ADDR
    assert payload['to'] == COUNTERPARTY
    assert payload['amount'] == '0.5'
    assert item['block_number'] == BLOCK_NUM


def test_search_matches_by_from_and_to_address():
    for query in (WALLET_ADDR, COUNTERPARTY):
        result, captured = _search_telemetry(query)
        telemetry_queries = [(q, p) for q, p in captured if 'telemetry_events' in (q or '').lower()]
        sql, params = telemetry_queries[0]
        assert "payload_json->>'from'" in sql.lower()
        assert "payload_json->>'to'" in sql.lower()
        assert any(query.lower() in str(p).lower() for p in (params or []))
        assert result['telemetry'], f'address search ({query}) must return the persisted transfer'
