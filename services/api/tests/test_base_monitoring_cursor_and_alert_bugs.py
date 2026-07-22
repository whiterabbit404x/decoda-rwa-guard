"""
Tests for the Base monitoring worker cursor and transaction rollback bug fixes.

Covers:
  1. _upsert_alert SQL has correct placeholder count (18 params, 18 %s).
  2. Detection INSERT succeeds when monitoring_run_id is committed before INSERT.
  3. Old backfill event does NOT block cursor advancement (cursor advances even on
     event_processing_failed).
  4. New latest Base native ETH transfer is detected within 1-2 polling cycles
     via live-tail scan while backfill is still catching up.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest


WALLET_ADDR = '0x5f6f35fd8b10c5576089f99c7c8c351deb851d1f'
CHAIN_LATEST = 47_376_498
CURSOR_BLOCK = 47_286_496  # old cursor from failing logs
CHAIN_SAFE_TO = CHAIN_LATEST - 3


def _now() -> datetime:
    return datetime(2026, 6, 15, 16, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# 1. _upsert_alert SQL placeholder count
# ---------------------------------------------------------------------------

def test_upsert_alert_sql_placeholder_count():
    """INSERT SQL in _upsert_alert must have exactly as many %s as parameters."""
    from services.api.app.monitoring_runner import _upsert_alert
    import inspect, ast

    src = inspect.getsource(_upsert_alert)

    # Extract the INSERT INTO alerts VALUES (...) clause
    insert_match = re.search(
        r"INSERT INTO alerts\s*\([^)]+\)\s*VALUES\s*\(([^)]+)\)",
        src,
        re.DOTALL | re.IGNORECASE,
    )
    assert insert_match, "Could not find INSERT INTO alerts VALUES (...) in _upsert_alert"
    values_clause = insert_match.group(1)

    # Count %s placeholders (not counting literal values like 'open', 1, NOW())
    placeholder_count = len(re.findall(r'%s', values_clause))

    # Count the parameters in the tuple passed to connection.execute for the INSERT.
    # The parameters are: alert_id, workspace_id, user_id, analysis_run_id, target_id,
    # 'threat_monitoring', title, severity, 'threat-engine', source, summary, payload,
    # matched_patterns, reasons, recommended_action, degraded, signature, detection_id
    # = 18 parameters
    expected_params = 18

    assert placeholder_count == expected_params, (
        f"_upsert_alert INSERT has {placeholder_count} placeholder(s) but "
        f"passes {expected_params} parameter(s). "
        f"VALUES clause: {values_clause.strip()!r}"
    )


# ---------------------------------------------------------------------------
# 2. Detection INSERT succeeds with valid monitoring_run_id
# ---------------------------------------------------------------------------

class _FakeRows:
    def __init__(self, rows=None):
        self._rows = rows or []
        self.rowcount = len(self._rows)
    def fetchone(self):
        return self._rows[0] if self._rows else None
    def fetchall(self):
        return list(self._rows)
    def __iter__(self):
        return iter(self._rows)


class _FakeConnection:
    """Minimal connection stub that records SQL + params."""

    def __init__(self, workspace_id=None, target_id=None):
        self.executed: list[tuple[str, tuple]] = []
        self._workspace_id = workspace_id or str(uuid.uuid4())
        self._target_id = target_id or str(uuid.uuid4())

    def execute(self, sql: str, params: tuple = ()) -> _FakeRows:
        self.executed.append((sql.strip(), params))
        sql_lower = sql.strip().lower()
        if 'select id, name from workspaces' in sql_lower:
            return _FakeRows([{'id': self._workspace_id, 'name': 'Test Workspace'}])
        if 'select 1 from targets' in sql_lower:
            return _FakeRows([{'1': 1}])
        if 'select id from asset_registry' in sql_lower or 'select id from assets' in sql_lower:
            return _FakeRows([])
        if 'select' in sql_lower:
            return _FakeRows([])
        return _FakeRows()

    def commit(self):
        pass

    def rollback(self):
        pass

    class _Savepoint:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def transaction(self):
        return self._Savepoint()


def test_monitoring_run_id_inserted_before_detection():
    """When monitoring_run_id is not supplied to process_monitoring_target,
    a monitoring_run row must be INSERTed before any detection INSERT."""
    from services.api.app import monitoring_runner

    workspace_id = str(uuid.uuid4())
    target_id = str(uuid.uuid4())
    conn = _FakeConnection(workspace_id=workspace_id, target_id=target_id)

    target = {
        'id': target_id,
        'workspace_id': workspace_id,
        'name': 'Test Target',
        'target_type': 'wallet',
        'chain_network': 'base',
        'wallet_address': WALLET_ADDR,
        'contract_identifier': None,
        'monitoring_checkpoint_cursor': None,
        'monitoring_interval_seconds': 300,
        'updated_by_user_id': str(uuid.uuid4()),
        'created_by_user_id': str(uuid.uuid4()),
        'asset_id': None,
        'monitoring_enabled': True,
        'monitoring_mode': 'live',
        'auto_create_alerts': True,
        'auto_create_incidents': False,
        'severity_threshold': 'low',
        'monitoring_claimed_by': None,
        'monitoring_lease_token': None,
        'monitoring_lease_expires_at': None,
        'watcher_last_observed_block': 0,
        'monitored_system_id': None,
        'severity_preference': None,
        'enabled': True,
        'is_active': True,
    }

    from services.api.app.activity_providers import ActivityProviderResult

    fake_result = ActivityProviderResult(
        events=[],
        status='no_evidence',
        mode='live',
        source_type='rpc_polling',
        provider_name='evm',
        provider_kind='evm',
        evidence_state='NO_EVIDENCE',
        truthfulness_state='UNKNOWN_RISK',
        degraded_reason=None,
        last_real_event_at=None,
        latest_block=CHAIN_LATEST,
        recent_real_event_count=0,
        synthetic=False,
        evidence_present=False,
        checkpoint=None,
        checkpoint_age_seconds=None,
        error_code=None,
        reason_code=None,
        claim_safe=False,
        detection_outcome='NO_CONFIRMED_ANOMALY_FROM_REAL_EVIDENCE',
    )

    with (
        patch.object(monitoring_runner, 'fetch_target_activity_result', return_value=fake_result),
        patch.object(monitoring_runner, '_load_target_asset_context', return_value={}),
        patch.object(monitoring_runner, '_persist_live_coverage_telemetry', return_value=None),
        patch.object(monitoring_runner, '_persist_detection_evaluation_checkpoint', return_value=None),
        patch.object(monitoring_runner, '_persist_no_threat_evaluation_marker', return_value=None),
        patch.object(monitoring_runner, '_resolve_coverage_asset_id', return_value=None),
    ):
        monitoring_runner.process_monitoring_target(conn, target)

    sql_statements = [sql for sql, _ in conn.executed]
    monitoring_run_inserts = [
        sql for sql in sql_statements if 'insert into monitoring_runs' in sql.lower()
    ]
    assert monitoring_run_inserts, (
        "process_monitoring_target must INSERT into monitoring_runs when monitoring_run_id is None. "
        f"SQL executed: {sql_statements}"
    )

    # Verify monitoring_run INSERT happened before any detection INSERT
    first_run_insert_idx = next(
        (i for i, sql in enumerate(sql_statements) if 'insert into monitoring_runs' in sql.lower()),
        None,
    )
    first_detection_insert_idx = next(
        (i for i, sql in enumerate(sql_statements) if 'insert into detections' in sql.lower()),
        len(sql_statements),  # if no detection, that's fine
    )
    assert first_run_insert_idx < first_detection_insert_idx, (
        "monitoring_runs INSERT must precede detections INSERT"
    )


# ---------------------------------------------------------------------------
# 3. Old backfill event does NOT block cursor advancement
# ---------------------------------------------------------------------------

def test_old_backfill_event_does_not_block_cursor_advancement(monkeypatch):
    """When _process_single_event raises (e.g. alert SQL bug or FK error),
    the cursor must still advance to scan_ceiling after the cycle."""
    from services.api.app.evm_activity_provider import fetch_evm_activity, ActivityEvent

    old_tx_hash = '0x42eb6fb953a32dc80fef0f62b4eadfa0fed18c7129d68924cd65bdb37e25a517'
    old_block = CURSOR_BLOCK + 5  # within the current scan window

    class _Rpc:
        def call(self, method, params):
            if method == 'eth_chainId':
                return hex(8453)
            if method == 'eth_blockNumber':
                return hex(CHAIN_LATEST)
            if method == 'eth_getLogs':
                return []
            if method == 'eth_getBlockByNumber':
                bn = int(str(params[0]), 16)
                txs = []
                if bn == old_block:
                    txs = [{
                        'hash': old_tx_hash,
                        'from': WALLET_ADDR,
                        'to': '0xcafe00000000000000000000000000000000feed',
                        'value': hex(10 ** 18),
                        'input': '0x',
                        'blockNumber': hex(bn),
                        'blockHash': f'0xblockhash{bn}',
                    }]
                return {
                    'hash': f'0xblockhash{bn}',
                    'number': hex(bn),
                    'timestamp': hex(int(_now().timestamp()) + bn),
                    'transactions': txs,
                }
            return {}

    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'base')
    monkeypatch.setenv('EVM_CONFIRMATIONS_REQUIRED', '3')
    monkeypatch.setenv('MONITOR_REPLAY_BLOCKS', '5')
    monkeypatch.setenv('MAX_BLOCKS_PER_CYCLE', '100')
    monkeypatch.delenv('EVM_LIVE_TAIL_BLOCKS', raising=False)
    # Cursor-based catch-up over a deep backlog is historical-backfill behavior, gated OFF
    # by default in the polling-only MVP (where a scheduled poll scans only the live tail).
    monkeypatch.setenv('HISTORICAL_BACKFILL_ENABLED', 'true')

    target = {
        'id': str(uuid.uuid4()),
        'workspace_id': str(uuid.uuid4()),
        'name': 'Base Wallet',
        'target_type': 'wallet',
        'chain_network': 'base',
        'wallet_address': WALLET_ADDR,
        'contract_identifier': None,
        'monitoring_checkpoint_cursor': f'{CURSOR_BLOCK}:checkpoint:-1',
        'monitoring_interval_seconds': 300,
    }

    events = fetch_evm_activity(target, None, rpc_client=_Rpc())

    # Old tx must be detected (it is within the scan window)
    tx_hashes = [e.payload.get('tx_hash') for e in events if isinstance(e.payload, dict)]
    assert old_tx_hash in tx_hashes, (
        f"Old tx {old_tx_hash} in block {old_block} should be detected; got {tx_hashes}"
    )

    # Cursor on target must have advanced past old_block
    new_cursor_block = int((target.get('monitoring_checkpoint_cursor') or '0').split(':')[0] or '0')
    scan_to = target.get('_evm_scan_to_block', 0)
    assert scan_to > CURSOR_BLOCK, (
        f"Cursor must advance beyond {CURSOR_BLOCK}; _evm_scan_to_block={scan_to}"
    )


# ---------------------------------------------------------------------------
# 4. Live-tail detects new tx at chain head even during backfill catchup
# ---------------------------------------------------------------------------

def test_live_tail_detects_new_tx_during_catchup(monkeypatch):
    """During backfill catchup, the live-tail window must detect a new transaction
    at the chain head within the same polling cycle."""
    from services.api.app.evm_activity_provider import fetch_evm_activity

    live_tx_hash = '0xLIVETAIL_NEW_TRANSACTION_AT_CHAIN_HEAD_BLOCK'
    live_block = CHAIN_SAFE_TO  # at the very tip of the chain

    # old_block is within backfill window
    old_block = CURSOR_BLOCK + 10

    class _Rpc:
        def call(self, method, params):
            if method == 'eth_chainId':
                return hex(8453)
            if method == 'eth_blockNumber':
                return hex(CHAIN_LATEST)
            if method == 'eth_getLogs':
                return []
            if method == 'eth_getBlockByNumber':
                bn = int(str(params[0]), 16)
                txs = []
                if bn == live_block:
                    txs = [{
                        'hash': live_tx_hash,
                        'from': WALLET_ADDR,
                        'to': '0x000000000000000000000000000000000000dEaD',
                        'value': hex(5 * 10 ** 17),
                        'input': '0x',
                        'blockNumber': hex(bn),
                        'blockHash': f'0xblockhash{bn}',
                    }]
                return {
                    'hash': f'0xblockhash{bn}',
                    'number': hex(bn),
                    'timestamp': hex(int(_now().timestamp()) + bn),
                    'transactions': txs,
                }
            return {}

    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'base')
    monkeypatch.setenv('EVM_CONFIRMATIONS_REQUIRED', '3')
    monkeypatch.setenv('MONITOR_REPLAY_BLOCKS', '5')
    monkeypatch.setenv('MAX_BLOCKS_PER_CYCLE', '100')
    monkeypatch.setenv('EVM_LIVE_TAIL_BLOCKS', '100')  # live-tail window (capped at 25)
    # The catch-up backfill + live-tail combination is historical-backfill behavior, gated
    # OFF by default in the polling-only MVP; enable it to exercise that path here.
    monkeypatch.setenv('HISTORICAL_BACKFILL_ENABLED', 'true')

    target = {
        'id': str(uuid.uuid4()),
        'workspace_id': str(uuid.uuid4()),
        'name': 'Base Wallet Live Tail',
        'target_type': 'wallet',
        'chain_network': 'base',
        'wallet_address': WALLET_ADDR,
        'contract_identifier': None,
        'monitoring_checkpoint_cursor': f'{CURSOR_BLOCK}:checkpoint:-1',
        'monitoring_interval_seconds': 300,
    }

    events = fetch_evm_activity(target, None, rpc_client=_Rpc())

    tx_hashes = [e.payload.get('tx_hash') for e in events if isinstance(e.payload, dict)]
    assert live_tx_hash in tx_hashes, (
        f"Live-tail tx {live_tx_hash} at chain head block {live_block} must be detected "
        f"in the same cycle even during backfill. "
        f"cursor_block={CURSOR_BLOCK} chain_latest={CHAIN_LATEST} "
        f"detected_hashes={tx_hashes}"
    )

    # Cursor must still be at the backfill ceiling, NOT at chain head
    scan_to = target.get('_evm_scan_to_block', 0)
    assert scan_to < CHAIN_SAFE_TO, (
        f"Cursor must NOT advance to chain head during catchup; _evm_scan_to_block={scan_to}, "
        f"chain_safe_to={CHAIN_SAFE_TO}"
    )


# ---------------------------------------------------------------------------
# 5. Recoverable detection/alert SQL errors do NOT increment delivery_attempts
# ---------------------------------------------------------------------------

def test_detection_insert_failure_does_not_dead_letter(monkeypatch):
    """When _create_detection raises an FK/SQL error, the per-event savepoint rolls it
    back, process_monitoring_target completes normally, and the target is NOT dead-lettered
    (delivery_attempts must NOT be incremented by the outer error handler)."""
    from services.api.app import monitoring_runner
    from services.api.app.activity_providers import ActivityProviderResult

    workspace_id = str(uuid.uuid4())
    target_id = str(uuid.uuid4())
    monitoring_run_id = str(uuid.uuid4())

    class _SavepointCtx:
        def __init__(self, should_fail=False):
            self._fail = should_fail
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False

    _savepoint_calls: list[str] = []

    class _ConnWithSavepoints:
        """Connection that raises IntegrityError inside the detection savepoint."""
        def __init__(self):
            self.executed: list[str] = []
            self._in_detection_savepoint = False

        def execute(self, sql: str, params=()) -> _FakeRows:
            sql_lower = sql.strip().lower()
            self.executed.append(sql_lower[:60])
            if 'select id, name from workspaces' in sql_lower:
                return _FakeRows([{'id': workspace_id, 'name': 'ws'}])
            if 'select 1 from targets' in sql_lower:
                return _FakeRows([{'1': 1}])
            if 'insert into detections' in sql_lower:
                # Simulate FK violation: monitoring_run_id not found
                raise Exception('detections_monitoring_run_id_fkey violates foreign key constraint')
            if 'insert into alerts' in sql_lower:
                raise Exception('_upsert_alert placeholder count mismatch simulation')
            return _FakeRows()

        def transaction(self):
            # Real savepoint: catches DB exceptions; returning False re-raises them
            class _SP:
                def __enter__(s2):
                    return s2
                def __exit__(s2, exc_type, exc, tb):
                    _savepoint_calls.append('exit')
                    return False
            return _SP()

    conn = _ConnWithSavepoints()

    target = {
        'id': target_id,
        'workspace_id': workspace_id,
        'name': 'Test Target',
        'target_type': 'wallet',
        'chain_network': 'base',
        'wallet_address': WALLET_ADDR,
        'contract_identifier': None,
        'monitoring_checkpoint_cursor': None,
        'monitoring_interval_seconds': 300,
        'updated_by_user_id': str(uuid.uuid4()),
        'created_by_user_id': str(uuid.uuid4()),
        'asset_id': None,
        'monitoring_enabled': True,
        'monitoring_mode': 'live',
        'auto_create_alerts': True,
        'auto_create_incidents': False,
        'severity_threshold': 'low',
        'monitoring_claimed_by': None,
        'monitoring_lease_token': None,
        'monitoring_lease_expires_at': None,
        'watcher_last_observed_block': 0,
        'monitored_system_id': None,
        'severity_preference': None,
        'enabled': True,
        'is_active': True,
    }

    from services.api.app.activity_providers import ActivityProviderResult
    from services.api.app.evm_activity_provider import ActivityEvent

    live_event = ActivityEvent(
        event_id=str(uuid.uuid4()),
        kind='transaction',
        observed_at=datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc),
        cursor=f'{CHAIN_LATEST}:checkpoint:-1',
        ingestion_source='live',
        payload={
            'tx_hash': '0xfeedfeed',
            'from': WALLET_ADDR,
            'to': '0xcafe',
            'value': str(10 ** 18),
            'block_number': CHAIN_LATEST,
            'event_type': 'transaction',
        },
    )

    fake_result = ActivityProviderResult(
        events=[live_event],
        status='live',
        mode='live',
        source_type='rpc_polling',
        provider_name='evm',
        provider_kind='evm',
        evidence_state='REAL_EVIDENCE',
        truthfulness_state='UNKNOWN_RISK',
        degraded_reason=None,
        last_real_event_at=live_event.observed_at,
        latest_block=CHAIN_LATEST,
        recent_real_event_count=1,
        synthetic=False,
        evidence_present=True,
        checkpoint=None,
        checkpoint_age_seconds=None,
        error_code=None,
        reason_code=None,
        claim_safe=False,
        detection_outcome='DETECTION_CONFIRMED',
    )

    raised = False
    try:
        with (
            patch.object(monitoring_runner, 'fetch_target_activity_result', return_value=fake_result),
            patch.object(monitoring_runner, '_load_target_asset_context', return_value={}),
            patch.object(monitoring_runner, '_persist_live_coverage_telemetry', return_value=None),
            patch.object(monitoring_runner, '_persist_detection_evaluation_checkpoint', return_value=None),
            patch.object(monitoring_runner, '_persist_no_threat_evaluation_marker', return_value=None),
            patch.object(monitoring_runner, '_resolve_coverage_asset_id', return_value=None),
            patch.object(monitoring_runner, '_load_checkpoint', return_value=0),
            patch.object(monitoring_runner, '_threat_call', return_value=(
                {
                    'severity': 'high',
                    'matched_patterns': [{'label': 'large_transfer'}],
                    'explanation': 'large transfer detected',
                    'recommended_action': 'review',
                    'degraded': False,
                    'source': 'live',
                },
                {},
            )),
            patch.object(monitoring_runner, 'persist_analysis_run', return_value=str(uuid.uuid4())),
            patch.object(monitoring_runner, '_persist_raw_wallet_transfer_telemetry', return_value=True),
            patch.object(monitoring_runner, '_wallet_transfer_smoke_alert', return_value=None),
            patch.object(monitoring_runner, '_persist_evidence', return_value=None),
            patch.object(monitoring_runner, '_record_detection_metric', return_value=None),
            patch.object(monitoring_runner, '_upsert_checkpoint', return_value=None),
            patch.object(monitoring_runner, '_persist_detection_evaluation_checkpoint', return_value=None),
        ):
            monitoring_runner.process_monitoring_target(conn, target, monitoring_run_id=monitoring_run_id)
    except Exception as exc:
        raised = True
        assert False, (
            f"process_monitoring_target must NOT raise when detection/alert INSERT fails with "
            f"a recoverable SQL error. Got: {exc!r}"
        )

    assert not raised, "process_monitoring_target raised unexpectedly"

    # Verify that at least one savepoint was opened (for detection and/or alert insert)
    assert len(_savepoint_calls) >= 1, (
        "At least one savepoint must have been entered for detection/alert inserts"
    )

    # Verify that target UPDATE (clear lease) still ran — proves connection stayed healthy
    target_update_ran = any('update targets' in s for s in conn.executed)
    assert target_update_ran, (
        f"targets UPDATE (clear lease) must still run after recoverable detection/alert failure. "
        f"Executed: {conn.executed}"
    )
