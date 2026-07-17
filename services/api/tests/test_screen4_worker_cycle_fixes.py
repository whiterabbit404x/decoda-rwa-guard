"""Screen 4 worker-cycle fixes.

Covers three production bugs surfaced on the Datto USDC monitor:

  §2  selected -> executed invariant: a target selected for a live poll must never vanish
      silently between due-selection and the polling executor. When it is not claimed a
      SPECIFIC blocked reason is logged; when it reaches zero execution with no recorded
      reason a monitoring_due_execution_invariant_failed error is emitted.

  §4  reconciliation transaction abort: each target reconciles inside its own savepoint, so
      one invalid legacy target cannot abort reconciliation for every other target, and the
      connection stays usable afterward. Accurate failed counts are returned.

  §5  optional-query error classification: an aborted transaction (InFailedSqlTransaction)
      must never be mislabeled as an unavailable optional table.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

from psycopg import Error as PsycopgError
from psycopg.errors import (
    InFailedSqlTransaction,
    InsufficientPrivilege,
    QueryCanceled,
    UndefinedColumn,
    UndefinedTable,
)


def _now() -> datetime:
    return datetime(2026, 7, 17, 16, 22, tzinfo=timezone.utc)


class _Result:
    def __init__(self, *, rows=None, row=None):
        self._rows = rows or []
        self._row = row

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._row


class _FakeTransaction:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


@contextmanager
def _fake_pg(conn):
    yield conn


# ---------------------------------------------------------------------------
# §2 — selected -> executed invariant
# ---------------------------------------------------------------------------

class _CycleConnection:
    """Fake worker DB connection serving one candidate target.

    Configurable to reproduce the two ways a selected target fails to execute:
      * claim_returns_empty=True  -> the FOR UPDATE SKIP LOCKED claim returns nothing
                                     (lease held / contended) -> selected-but-not-claimed.
      * poll_parent_missing=True  -> the target is claimed but the poll-parent guard finds
                                     no targets row -> claimed-but-not-executed (invariant).
    """

    def __init__(self, candidate, *, claim_returns_empty=False, poll_parent_missing=False,
                 lease_expires_at=None):
        self._candidate = candidate
        self._claim_returns_empty = claim_returns_empty
        self._poll_parent_missing = poll_parent_missing
        self._lease_expires_at = lease_expires_at
        self._health_row = None

    def transaction(self):
        return _FakeTransaction()

    def execute(self, query, params=None):
        q = ' '.join(str(query).split())
        c = self._candidate

        if 'FROM monitored_systems ms JOIN targets t ON t.id = ms.target_id' in q:
            return _Result(rows=[{
                'monitored_system_id': f'sys-{c["id"]}',
                'workspace_id': c['workspace_id'],
                'target_id': c['id'],
                'asset_id': c.get('asset_id'),
                'monitored_system_enabled': True,
                'monitored_system_runtime_status': 'active',
                'monitored_system_last_heartbeat': None,
                'last_checked_at': c.get('last_checked_at'),
                'monitoring_interval_seconds': c.get('monitoring_interval_seconds', 300),
                'monitoring_enabled': True,
                'enabled': True,
                'is_active': True,
                'monitoring_dead_lettered_at': None,
                'chain_network': c.get('chain_network', 'base-mainnet'),
                'created_at': c.get('created_at', _now()),
            }])

        # The claim query (returns claimed target rows).
        if 'FROM targets' in q and 'FOR UPDATE SKIP LOCKED' in q:
            if self._claim_returns_empty:
                return _Result(rows=[])
            return _Result(rows=[dict(c)])

        # The selected-but-not-claimed diagnostic (my §2 fix).
        if 'monitoring_lease_expires_at' in q and 'monitoring_delivery_attempts' in q and 'id = ANY' in q:
            return _Result(rows=[{
                'id': c['id'],
                'workspace_id': c['workspace_id'],
                'monitoring_dead_lettered_at': None,
                'monitoring_delivery_attempts': 0,
                'monitoring_lease_expires_at': self._lease_expires_at,
            }])

        # Telemetry idempotency-index presence check (worker skips the cycle if missing).
        if 'SELECT EXISTS' in q and 'pg_get_indexdef' in q:
            return _Result(row={'ok': True})

        # Poll-parent guard.
        if q.startswith('SELECT 1 FROM targets WHERE id'):
            return _Result(row=None if self._poll_parent_missing else {'exists': 1})

        if q.startswith('SELECT worker_name'):
            return _Result(row=self._health_row)
        if q.startswith('SELECT COUNT(*) AS overdue_count'):
            return _Result(row={'overdue_count': 0})
        if "COUNT(*) FILTER (WHERE status = 'queued')" in q:
            return _Result(row={'queued': 0, 'running': 0, 'failed': 0})
        if q.startswith('UPDATE monitoring_worker_state'):
            self._health_row = {'worker_name': 'test-worker'}
            return _Result()
        return _Result()

    def commit(self):
        pass


def _due_contract_target():
    return {
        'id': str(uuid.uuid4()),
        'workspace_id': '4fffd3f9-d55f-456f-8a7e-8b9ed2083721',
        'name': 'Datto USDC Monitor',
        'target_type': 'contract',
        'chain_network': 'base-mainnet',
        'chain_id': 8453,
        'wallet_address': None,
        'contract_identifier': '0x833589fcd6edb6e08f4c7c32d4f71b54bda02913',
        'asset_id': str(uuid.uuid4()),
        'enabled': True,
        'monitoring_enabled': True,
        'is_active': True,
        'monitoring_interval_seconds': 300,
        'last_checked_at': None,  # never checked -> immediately due
        'monitoring_dead_lettered_at': None,
        'created_at': _now() - timedelta(hours=1),
    }


def _prep_cycle(monkeypatch, conn):
    from services.api.app import monitoring_runner
    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(conn))
    return monitoring_runner


def test_selected_target_reaches_polling_executor(monkeypatch):
    """A due, claimed target reaches process_monitoring_target and increments checked, with
    no selected-but-not-claimed reason and no invariant failure (§2, acceptance #2)."""
    monkeypatch.setenv('EVM_CHAIN_ID', '8453')
    target = _due_contract_target()
    conn = _CycleConnection(target)
    monitoring_runner = _prep_cycle(monkeypatch, conn)

    processed = []
    monkeypatch.setattr(
        monitoring_runner, 'process_monitoring_target',
        lambda _c, tgt, **_k: (processed.append(tgt['id']) or {'alerts_generated': 0, 'runs': [], 'status': 'completed'}),
    )

    summary = monitoring_runner.run_monitoring_cycle(worker_name='test-worker', limit=10)

    assert target['id'] in processed
    assert summary['checked'] == 1
    assert summary['selected_not_claimed'] == 0


def test_selected_but_not_claimed_records_specific_reason(monkeypatch, caplog):
    """A selected target whose claim returns nothing (lease held) must NOT vanish silently:
    a specific blocked_reason is logged and counted, and the invariant-failure error does
    NOT fire because a reason was recorded (§2, acceptance #3 contrapositive)."""
    import logging
    monkeypatch.setenv('EVM_CHAIN_ID', '8453')
    target = _due_contract_target()
    # Lease held by another worker until the future.
    conn = _CycleConnection(
        target, claim_returns_empty=True, lease_expires_at=_now() + timedelta(seconds=300),
    )
    monitoring_runner = _prep_cycle(monkeypatch, conn)
    monkeypatch.setattr(monitoring_runner, 'utc_now', lambda: _now())
    monkeypatch.setattr(
        monitoring_runner, 'process_monitoring_target',
        lambda *_a, **_k: {'alerts_generated': 0, 'runs': [], 'status': 'completed'},
    )

    with caplog.at_level(logging.WARNING):
        summary = monitoring_runner.run_monitoring_cycle(worker_name='test-worker', limit=10)

    assert summary['checked'] == 0
    assert summary['selected_not_claimed'] == 1
    assert summary['selected_not_claimed_reasons'].get(target['id']) == 'lease_held_by_other_worker'
    assert summary['cycle_state'] == 'selected_not_claimed_cycle'
    assert 'event=monitoring_selected_target_not_claimed' in caplog.text
    assert 'monitoring_due_execution_invariant_failed' not in caplog.text


def test_effective_due_with_zero_checked_and_no_reason_logs_invariant_failure(monkeypatch, caplog):
    """effective_due_count>0 ending with checked==0 and no recorded reason is a silent
    vanish — it MUST emit event=monitoring_due_execution_invariant_failed (§2, acceptance
    #3). Reproduced by claiming the target then failing the poll-parent guard, which skips
    without an error, backoff, or not-claimed reason."""
    import logging
    monkeypatch.setenv('EVM_CHAIN_ID', '8453')
    target = _due_contract_target()
    conn = _CycleConnection(target, poll_parent_missing=True)
    monitoring_runner = _prep_cycle(monkeypatch, conn)

    called = []
    monkeypatch.setattr(
        monitoring_runner, 'process_monitoring_target',
        lambda _c, tgt, **_k: (called.append(tgt['id']) or {'alerts_generated': 0, 'runs': [], 'status': 'completed'}),
    )

    with caplog.at_level(logging.ERROR):
        summary = monitoring_runner.run_monitoring_cycle(worker_name='test-worker', limit=10)

    assert called == []  # poll-parent guard skipped it before process ran
    assert summary['checked'] == 0
    assert summary['effective_due_count'] == 1
    assert summary['selected_not_claimed'] == 0
    assert 'event=monitoring_due_execution_invariant_failed' in caplog.text


# ---------------------------------------------------------------------------
# §4 — reconciliation transaction abort / per-target savepoint
# ---------------------------------------------------------------------------

class _ReconcileConn:
    """Fake reconcile DB connection with faithful savepoint + abort semantics.

    A statement executed while the connection is 'aborted' raises InFailedSqlTransaction
    (as real Postgres does). A savepoint (transaction()) whose block exits with an exception
    clears the aborted flag — modeling ROLLBACK TO SAVEPOINT restoring usability.
    """

    def __init__(self, targets):
        self._targets = targets
        self.aborted = False
        self.executed_after_failure = False

    def transaction(self):
        conn = self

        class _Savepoint:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                if exc_type is not None:
                    # ROLLBACK TO SAVEPOINT — the sub-transaction is discarded and the
                    # connection becomes usable again for the remaining targets.
                    conn.aborted = False
                return False

        return _Savepoint()

    def execute(self, query, params=None):
        if self.aborted:
            raise InFailedSqlTransaction('current transaction is aborted')
        q = ' '.join(str(query).split())

        if q.startswith('SELECT id, target_type, enabled, monitoring_enabled, asset_id FROM targets'):
            return _Result(rows=[dict(t) for t in self._targets])
        if 'RETURNING ms.id' in q:  # unsupported_target_type repair
            return _Result(rows=[])
        if q.startswith('SELECT id, target_id FROM monitored_systems'):
            return _Result(rows=[])
        if 'SELECT t.id FROM targets t JOIN assets a' in q:
            return _Result(rows=[{'id': t['id']} for t in self._targets])
        if q.startswith('SELECT id FROM monitored_systems WHERE workspace_id'):
            return _Result(row={'id': f'sys-{(params or ["", ""])[1]}'})
        return _Result()

    def commit(self):
        pass


def _reconcile_target(tid, **over):
    row = {
        'id': tid,
        'target_type': 'contract',
        'enabled': True,
        'monitoring_enabled': True,
        'asset_id': str(uuid.uuid4()),
        'workspace_id': 'ws-1',
    }
    row.update(over)
    return row


def test_one_failed_reconcile_target_does_not_abort_the_others(monkeypatch):
    """One invalid legacy target that raises mid-reconcile must NOT abort reconciliation for
    the other targets; the failure is isolated, counted, and the remaining targets still
    reconcile (§4, tests #7 and #8)."""
    from services.api.app import pilot

    t1, t2, t3 = _reconcile_target('target-1'), _reconcile_target('target-2'), _reconcile_target('target-3')
    conn = _ReconcileConn([t1, t2, t3])

    def _ensure(_c, *, target_id, workspace_id=None, require_enabled=True):
        if target_id == 'target-2':
            # Simulate a mid-statement DB error that poisons the transaction.
            _c.aborted = True
            raise PsycopgError('bad row for legacy target-2')
        return {'status': 'ok', 'target_id': target_id, 'workspace_id': 'ws-1', 'reason': None}

    monkeypatch.setattr(pilot, 'ensure_monitored_system_for_target', _ensure)
    monkeypatch.setattr(pilot, 'ensure_direct_monitoring_config_for_target',
                        lambda *_a, **_k: {'config_id': 'cfg', 'created': False})

    result = pilot.reconcile_enabled_targets_monitored_systems(conn, workspace_id=None)

    # target-2 recorded as failed; target-1 and target-3 still reconciled.
    assert result['failed_targets'] == 1
    assert [d['target_id'] for d in result['failed_target_details']] == ['target-2']
    assert result['eligible_targets'] == 2  # target-1 + target-3
    # Connection is usable after reconciliation (savepoint rollback cleared the abort):
    # a follow-up query must not raise InFailedSqlTransaction.
    assert conn.aborted is False
    conn.execute('SELECT 1')  # must not raise


def test_per_target_savepoint_rollback_restores_transaction_usability(monkeypatch):
    """After a target failure the savepoint rollback must restore the connection so
    subsequent targets AND downstream queries execute rather than cascading
    InFailedSqlTransaction (§4, test #8 / §5 recovery)."""
    from services.api.app import pilot

    # Fail the FIRST target so, without recovery, every later query would raise.
    t1, t2 = _reconcile_target('target-1'), _reconcile_target('target-2')
    conn = _ReconcileConn([t1, t2])

    def _ensure(_c, *, target_id, workspace_id=None, require_enabled=True):
        if target_id == 'target-1':
            _c.aborted = True
            raise PsycopgError('poisoned transaction')
        return {'status': 'ok', 'target_id': target_id, 'workspace_id': 'ws-1', 'reason': None}

    monkeypatch.setattr(pilot, 'ensure_monitored_system_for_target', _ensure)
    monkeypatch.setattr(pilot, 'ensure_direct_monitoring_config_for_target',
                        lambda *_a, **_k: {'config_id': 'cfg', 'created': False})

    result = pilot.reconcile_enabled_targets_monitored_systems(conn, workspace_id=None)

    assert result['failed_targets'] == 1
    assert result['eligible_targets'] == 1  # target-2 processed after recovery
    assert conn.aborted is False


def test_reconcile_error_category_is_safe_and_never_raw_text():
    """The recorded failure category must be a stable label, never raw error text (no
    identifiers/values/secrets leak) (§4)."""
    from services.api.app.pilot import _reconcile_target_error_category

    assert _reconcile_target_error_category(InFailedSqlTransaction('x')) == 'in_failed_sql_transaction'
    assert _reconcile_target_error_category(UndefinedTable('x')) == 'undefined_table'
    assert _reconcile_target_error_category(InsufficientPrivilege('x')) == 'permission_denied'
    assert _reconcile_target_error_category(ValueError('secret-value-123')) == 'unexpected_error'


# ---------------------------------------------------------------------------
# §5 — optional-query error classification
# ---------------------------------------------------------------------------

def test_in_failed_sql_transaction_not_mislabeled_as_optional_table_unavailable():
    """§5 / test #10: an aborted transaction must classify as transaction_aborted, never as
    optional_table_unavailable."""
    from services.api.app.monitoring_runner import _classify_optional_query_reason

    reason, error_code = _classify_optional_query_reason(InFailedSqlTransaction('aborted'))
    assert reason == 'transaction_aborted'
    assert reason != 'optional_table_unavailable'
    assert error_code == 'runtime_transaction_aborted'


def test_optional_query_reason_classification_differentiates_error_classes():
    """§5: UndefinedTable / UndefinedColumn / permission / timeout / unexpected are all
    differentiated rather than collapsed into optional_table_unavailable."""
    from services.api.app.monitoring_runner import _classify_optional_query_reason

    assert _classify_optional_query_reason(UndefinedTable('x'))[0] == 'optional_table_unavailable'
    assert _classify_optional_query_reason(UndefinedColumn('x'))[0] == 'optional_column_unavailable'
    assert _classify_optional_query_reason(InsufficientPrivilege('x'))[0] == 'permission_denied'
    assert _classify_optional_query_reason(QueryCanceled('x'))[0] == 'query_timeout'
    assert _classify_optional_query_reason(PsycopgError('unknown'))[0] == 'unexpected_database_error'


# ---------------------------------------------------------------------------
# §7 — actual provider used is persisted
# ---------------------------------------------------------------------------

class _ProviderHealthConn:
    """Captures the provider_health_records INSERT so we can assert what was persisted."""

    def __init__(self):
        self.insert_params = None

    def execute(self, query, params=None):
        q = ' '.join(str(query).split())
        if q.startswith('SELECT metadata FROM provider_health_records'):
            return _Result(row=None)  # no prior record -> streak starts fresh
        if 'INSERT INTO provider_health_records' in q:
            self.insert_params = tuple(params or ())
            return _Result()
        return _Result()


def test_actual_provider_used_is_persisted_with_role(monkeypatch):
    """§7 / test #11: the provider actually used for the poll (its redacted host) and its
    route role are persisted in provider_health_records — never a URL or secret."""
    import json
    from services.api.app import pilot

    conn = _ProviderHealthConn()
    record_id = pilot.persist_provider_health_evidence(
        conn,
        workspace_id='4fffd3f9-d55f-456f-8a7e-8b9ed2083721',
        target_id='9c6ecabb-cd52-404f-9859-40567b09dbb4',
        provider_host='base-mainnet.g.alchemy.com',
        status='healthy',
        success=True,
        latency_ms=42,
        chain_id=8453,
        latest_block=33445566,
        role='primary',
        actor_type='worker',
        trigger='scheduled_poll',
    )

    assert record_id
    params = conn.insert_params
    assert params is not None, 'a provider_health_records row must be inserted'
    # provider_type column (index 2) carries the ACTUAL provider host used for the poll.
    assert params[2] == 'base-mainnet.g.alchemy.com'
    # metadata (last param) records the provider host + route role, and never a raw URL/secret.
    metadata = json.loads(params[-1])
    assert metadata['provider_host'] == 'base-mainnet.g.alchemy.com'
    assert metadata['role'] == 'primary'
    assert metadata['actor_type'] == 'worker'
    assert '://' not in params[2] and 'http' not in params[2].lower()
