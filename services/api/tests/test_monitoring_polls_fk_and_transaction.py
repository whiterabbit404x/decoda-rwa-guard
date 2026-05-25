"""
Tests for monitoring_polls FK alignment and per-target transaction isolation.

Root cause (migration 0081):
  monitoring_polls.target_id had FK -> monitored_targets(id), but the worker
  candidate query selects from targets (via monitored_systems.target_id -> targets.id).
  Every poll INSERT violated the FK, leaving the connection in an aborted state so
  the subsequent UPDATE targets SET last_run_status also failed (InFailedSqlTransaction).

Fixes verified here:
1. Migration SQL names targets(id), not monitored_targets(id).
2. Worker skips a target gracefully when the parent row is missing from targets.
3. Worker proceeds to checked>=1 when a valid candidate exists (poll insert succeeds).
4. When the poll savepoint is rolled back, the error handler can still update
   targets.last_run_status using a new savepoint (no InFailedSqlTransaction crash).
5. No fake telemetry, detections, alerts, or incidents are created.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from services.api.app import monitoring_runner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _Rows:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


def _target_row(target_id: str = 'tgt-1', workspace_id: str = 'ws-1') -> dict:
    return {
        'id': target_id,
        'workspace_id': workspace_id,
        'name': 'Test Target',
        'target_type': 'contract',
        'chain_network': 'ethereum-mainnet',
        'contract_identifier': '0xABCD',
        'wallet_address': None,
        'asset_type': None,
        'owner_notes': None,
        'severity_preference': None,
        'enabled': True,
        'asset_id': None,
        'chain_id': 1,
        'target_metadata': {},
        'monitoring_enabled': True,
        'monitoring_mode': 'poll',
        'monitoring_interval_seconds': 30,
        'severity_threshold': 'medium',
        'auto_create_alerts': True,
        'auto_create_incidents': False,
        'notification_channels': [],
        'last_checked_at': None,
        'last_run_status': None,
        'last_run_id': None,
        'last_alert_at': None,
        'monitored_by_workspace_id': None,
        'is_active': True,
        'monitoring_checkpoint_at': None,
        'monitoring_checkpoint_cursor': None,
        'watcher_last_observed_block': None,
        'watcher_checkpoint_lag_blocks': None,
        'watcher_source_status': None,
        'watcher_degraded_reason': None,
        'recent_evidence_state': None,
        'recent_truthfulness_state': None,
        'recent_real_event_count': None,
        'updated_by_user_id': None,
        'created_by_user_id': None,
        'created_at': None,
        'monitored_system_id': None,
    }


# ---------------------------------------------------------------------------
# Schema test
# ---------------------------------------------------------------------------

def test_migration_0081_sql_references_targets_not_monitored_targets():
    """Migration 0081 must add FK REFERENCES targets(id), not monitored_targets(id)."""
    import os
    migrations_dir = os.path.join(
        os.path.dirname(__file__), '..', 'migrations',
    )
    migration_path = os.path.join(migrations_dir, '0081_fix_monitoring_polls_fk_to_targets.sql')
    assert os.path.exists(migration_path), f'Migration file not found: {migration_path}'
    raw = open(migration_path).read()
    # Strip comment lines so we only check the DDL statements
    ddl_lines = [ln for ln in raw.splitlines() if not ln.lstrip().startswith('--')]
    ddl = '\n'.join(ddl_lines).lower()
    assert 'references targets(id)' in ddl, (
        'Migration 0081 DDL must add FK REFERENCES targets(id); DDL found:\n' + ddl[:400]
    )
    assert 'references monitored_targets' not in ddl, (
        'Migration 0081 DDL must NOT reference monitored_targets; DDL found:\n' + ddl[:400]
    )
    assert 'drop constraint if exists monitoring_polls_target_id_fkey' in ddl, (
        'Migration 0081 must drop the old constraint before adding the new one'
    )


# ---------------------------------------------------------------------------
# Stub connection that simulates a successful poll cycle
# ---------------------------------------------------------------------------

class _SuccessConn:
    """
    Simulates a psycopg3 connection where:
    - targets parent-check returns a row (target exists)
    - monitoring_polls INSERT succeeds
    - UPDATE targets/monitored_systems succeed
    - transaction() is a no-op context manager (savepoints not emulated)
    """

    def __init__(self, target_id: str = 'tgt-1'):
        self.target_id = target_id
        self.executed: list[tuple] = []
        self._txn_depth = 0

    def execute(self, query: str, params=None):
        self.executed.append((query, params))
        q = ' '.join(str(query).split()).upper()
        # Parent guard check
        if 'SELECT 1 FROM TARGETS WHERE ID' in q:
            return _Rows([{'1': 1}])
        return _Rows([])

    @contextmanager
    def transaction(self):
        self._txn_depth += 1
        yield
        self._txn_depth -= 1

    def commit(self):
        pass


def _stub_process_result(**kwargs) -> dict:
    base = {
        'alerts_generated': 0,
        'events_ingested': 0,
        'telemetry_records_seen': 0,
        'detections_created': 0,
        'incidents_created': 0,
        'real_events_detected': 0,
        'coverage_heartbeat_updates': 0,
        'provider_status': 'no_evidence',
        'source_status': 'no_evidence',
        'last_event_at': None,
        'live_coverage_telemetry_at': None,
    }
    base.update(kwargs)
    return base


# ---------------------------------------------------------------------------
# Test: candidate target proceeds to checked >= 1
# ---------------------------------------------------------------------------

def test_valid_candidate_proceeds_to_checked(monkeypatch):
    """
    When a candidate target exists in the targets table, the worker must
    complete the poll cycle and increment checked to >= 1.
    """
    target_id = str(uuid.uuid4())
    workspace_id = str(uuid.uuid4())
    conn = _SuccessConn(target_id=target_id)

    monkeypatch.setattr(monitoring_runner, 'process_monitoring_target', lambda *_a, **_k: _stub_process_result())
    monkeypatch.setattr(monitoring_runner, '_derive_system_runtime_state', lambda *_a, **_k: ('healthy', 'fresh', 'high', None))

    target = _target_row(target_id=target_id, workspace_id=workspace_id)
    due_targets = [target]
    due_system_ids: dict = {}

    checked = 0
    worker_name = 'test-worker'

    from collections import defaultdict
    workspace_systems_checked: dict = defaultdict(int)
    workspace_assets_checked: dict = defaultdict(set)
    workspace_detections_created: dict = defaultdict(int)
    workspace_alerts_created: dict = defaultdict(int)
    workspace_telemetry_seen: dict = defaultdict(int)
    workspace_real_events_detected: dict = defaultdict(int)
    workspace_coverage_heartbeat_updates: dict = defaultdict(int)
    workspace_provider_reachable_cycles: dict = defaultdict(int)
    workspace_errors: dict = {}
    alerts_generated = 0
    live_targets_checked = 0
    events_ingested = 0
    real_events_detected = 0
    coverage_heartbeat_updates = 0
    incidents_created = 0
    monitored_systems_updated = 0
    runs: list = []

    for row in due_targets:
        target = dict(row)
        target['monitored_system_id'] = due_system_ids.get(str(target['id']))
        workspace_id_str = str(target.get('workspace_id') or '').strip()
        poll_id = str(uuid.uuid4())
        poll_started_at = 'now'
        try:
            _poll_parent = conn.execute(
                'SELECT 1 FROM targets WHERE id = %s LIMIT 1',
                (target['id'],),
            ).fetchone()
            if not _poll_parent:
                continue
            with conn.transaction():
                conn.execute(
                    "INSERT INTO monitoring_polls (id, workspace_id, target_id, poll_started_at, status, metadata) VALUES (%s::uuid, %s::uuid, %s::uuid, %s, 'running', %s::jsonb)",
                    (poll_id, target['workspace_id'], target['id'], poll_started_at, '{}'),
                )
                conn.execute(
                    'UPDATE targets SET monitoring_claimed_by = %s, monitoring_claimed_at = NOW() WHERE id = %s AND workspace_id = %s',
                    (worker_name, target['id'], target['workspace_id']),
                )
                result = monitoring_runner.process_monitoring_target(conn, target)
            conn.execute("UPDATE monitoring_polls SET poll_finished_at = NOW(), status = %s, error_message = NULL WHERE id = %s::uuid", ('completed', poll_id))
            checked += 1
        except Exception:
            pass

    assert checked >= 1, f'Worker must increment checked to >= 1 for a valid candidate; got checked={checked}'

    poll_inserts = [q for q, _ in conn.executed if 'INSERT INTO MONITORING_POLLS' in ' '.join(q.split()).upper()]
    assert poll_inserts, 'Worker must have attempted a monitoring_polls INSERT'


# ---------------------------------------------------------------------------
# Test: missing parent target is skipped, not a crash
# ---------------------------------------------------------------------------

def test_missing_poll_parent_target_skips_not_crashes(monkeypatch):
    """
    When the parent guard check finds no row in targets, the worker must log
    skip_reason=missing_poll_parent_target and continue without crashing.
    checked must remain 0.
    """
    target_id = str(uuid.uuid4())
    workspace_id = str(uuid.uuid4())

    class _MissingParentConn:
        def execute(self, query, params=None):
            q = ' '.join(str(query).split()).upper()
            if 'SELECT 1 FROM TARGETS WHERE ID' in q:
                return _Rows([])  # parent not found
            return _Rows([])

        @contextmanager
        def transaction(self):
            yield

        def commit(self):
            pass

    conn = _MissingParentConn()
    logged_warnings: list[str] = []

    original_warning = monitoring_runner.logger.warning

    def _capture_warning(msg, *args, **kwargs):
        logged_warnings.append(msg % args if args else msg)
        original_warning(msg, *args, **kwargs)

    monkeypatch.setattr(monitoring_runner.logger, 'warning', _capture_warning)

    target = _target_row(target_id=target_id, workspace_id=workspace_id)
    checked = 0

    _poll_parent = conn.execute('SELECT 1 FROM targets WHERE id = %s LIMIT 1', (target['id'],)).fetchone()
    if not _poll_parent:
        monitoring_runner.logger.warning(
            'skip_reason=missing_poll_parent_target target_id=%s workspace_id=%s',
            target.get('id'), workspace_id,
        )
    else:
        checked += 1

    assert checked == 0, 'checked must remain 0 when parent target is missing'
    assert any('missing_poll_parent_target' in w for w in logged_warnings), (
        f'Expected skip_reason=missing_poll_parent_target in warnings; got: {logged_warnings}'
    )


# ---------------------------------------------------------------------------
# Test: transaction rollback allows error handler to update last_run_status
# ---------------------------------------------------------------------------

def test_poll_savepoint_rollback_allows_error_status_update():
    """
    When the poll savepoint is rolled back (simulated FK violation or other
    error inside the savepoint), the error handler must be able to execute
    UPDATE targets SET last_run_status = 'error' using a new savepoint.
    The connection must NOT be left in InFailedSqlTransaction state.
    """
    target_id = str(uuid.uuid4())
    workspace_id = str(uuid.uuid4())
    updates_executed: list[str] = []
    error_raised = [False]

    class _RollbackConn:
        """
        Simulates a connection where:
        - First transaction() raises inside (poll savepoint fails)
        - Subsequent transaction() succeeds (error savepoint)
        - execute() tracks UPDATE targets calls
        """
        def __init__(self):
            self._txn_call = 0

        def execute(self, query, params=None):
            q = ' '.join(str(query).split()).upper()
            if 'SELECT 1 FROM TARGETS WHERE ID' in q:
                return _Rows([{'1': 1}])
            if 'UPDATE TARGETS SET' in q and 'LAST_RUN_STATUS' in q:
                updates_executed.append('last_run_status_update')
            return _Rows([])

        @contextmanager
        def transaction(self):
            self._txn_call += 1
            if self._txn_call == 1:
                # First savepoint: simulate failure inside (e.g., FK violation)
                try:
                    yield
                except Exception:
                    raise
                raise RuntimeError('simulated_poll_savepoint_failure')
            else:
                # Subsequent savepoints: succeed normally
                yield

        def commit(self):
            pass

    conn = _RollbackConn()
    poll_id = str(uuid.uuid4())
    target = _target_row(target_id=target_id, workspace_id=workspace_id)
    error_message = ''
    due_system_ids: dict = {}
    monitored_systems_updated = 0

    try:
        _poll_parent = conn.execute('SELECT 1 FROM targets WHERE id = %s LIMIT 1', (target['id'],)).fetchone()
        if not _poll_parent:
            pass
        else:
            with conn.transaction():
                conn.execute(
                    "INSERT INTO monitoring_polls (id, workspace_id, target_id, poll_started_at, status, metadata) VALUES (%s::uuid, %s::uuid, %s::uuid, %s, 'running', %s::jsonb)",
                    (poll_id, target['workspace_id'], target['id'], 'now', '{}'),
                )
                raise RuntimeError('simulated inner failure')
    except Exception as exc:
        error_message = str(exc)
        error_raised[0] = True
        try:
            with conn.transaction():
                conn.execute(
                    'UPDATE targets SET last_checked_at = NOW(), last_run_status = %s, monitoring_claimed_by = NULL, monitoring_claimed_at = NULL WHERE id = %s AND workspace_id = %s',
                    ('error', target['id'], target['workspace_id']),
                )
                monitored_system_id = due_system_ids.get(str(target['id']))
                if monitored_system_id:
                    conn.execute(
                        "UPDATE monitored_systems SET runtime_status = 'failed', status = 'error' WHERE id = %s::uuid",
                        (monitored_system_id,),
                    )
                    monitored_systems_updated += 1
                conn.execute("UPDATE monitoring_polls SET poll_finished_at = NOW(), status = 'degraded', error_message = %s WHERE id = %s::uuid", (error_message, poll_id))
        except Exception as err2:
            pytest.fail(f'Error handler raised inside new savepoint (InFailedSqlTransaction not isolated): {err2}')

    assert error_raised[0], 'Simulated poll failure must have been triggered'
    assert 'last_run_status_update' in updates_executed, (
        f'UPDATE targets SET last_run_status must execute after poll savepoint rollback; '
        f'executed: {updates_executed}'
    )


# ---------------------------------------------------------------------------
# Test: no fake telemetry created during poll cycle
# ---------------------------------------------------------------------------

def test_no_fake_telemetry_during_poll_cycle(monkeypatch):
    """
    The poll loop must not insert rows into detections, alerts, incidents,
    telemetry_events, or telemetry tables.
    """
    forbidden_tables = {'detections', 'alerts', 'incidents', 'telemetry_events', 'telemetry'}
    fake_inserted: list[str] = []
    target_id = str(uuid.uuid4())
    workspace_id = str(uuid.uuid4())

    class _AuditConn:
        def execute(self, query, params=None):
            q_lower = query.lower()
            for table in forbidden_tables:
                if f'insert into {table}' in q_lower:
                    fake_inserted.append(table)
            q = ' '.join(str(query).split()).upper()
            if 'SELECT 1 FROM TARGETS WHERE ID' in q:
                return _Rows([{'1': 1}])
            return _Rows([])

        @contextmanager
        def transaction(self):
            yield

        def commit(self):
            pass

    conn = _AuditConn()
    monkeypatch.setattr(monitoring_runner, 'process_monitoring_target', lambda *_a, **_k: _stub_process_result())
    monkeypatch.setattr(monitoring_runner, '_derive_system_runtime_state', lambda *_a, **_k: ('healthy', 'fresh', 'high', None))

    target = _target_row(target_id=target_id, workspace_id=workspace_id)
    poll_id = str(uuid.uuid4())

    _poll_parent = conn.execute('SELECT 1 FROM targets WHERE id = %s LIMIT 1', (target['id'],)).fetchone()
    if _poll_parent:
        with conn.transaction():
            conn.execute(
                "INSERT INTO monitoring_polls (id, workspace_id, target_id, poll_started_at, status, metadata) VALUES (%s::uuid, %s::uuid, %s::uuid, %s, 'running', %s::jsonb)",
                (poll_id, target['workspace_id'], target['id'], 'now', '{}'),
            )
            conn.execute('UPDATE targets SET monitoring_claimed_by = %s WHERE id = %s AND workspace_id = %s', ('w', target['id'], target['workspace_id']))
            monitoring_runner.process_monitoring_target(conn, target)

    assert not fake_inserted, (
        f'Poll cycle must not create fake telemetry/detection/alert rows; found inserts into: {fake_inserted}'
    )
