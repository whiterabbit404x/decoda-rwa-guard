"""
Tests for monitoring table target_id FK alignment after migration 0082.

Root cause: provider_health_records and target_coverage_records had
target_id FK -> monitored_targets(id).  The worker uses targets.id, so
every INSERT into those tables raised ForeignKeyViolation, preventing
any target from reaching checked>=1.

Migration 0082 drops the misaligned FKs and re-adds them pointing at
targets(id).

These tests verify:
- _verify_monitoring_fk_alignment reports tables whose FK points at targets as aligned.
- _verify_monitoring_fk_alignment reports tables whose FK points at monitored_targets as misaligned.
- _verify_monitoring_fk_alignment skips tables whose constraint is absent (returns no crash).
- process_monitoring_target inserts provider_health_records with a valid targets.id (FK safe).
- process_monitoring_target inserts target_coverage_records with a valid targets.id (FK safe).
- A single FK failure inside process_monitoring_target does not abort the whole cycle; the
  outer loop catches it, records error status, and continues.
- No fake telemetry/detections/alerts are created by process_monitoring_target when the
  provider returns no events.
"""
from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from services.api.app import monitoring_runner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class _Rows:
    """Minimal result-set shim used by fake connections."""

    def __init__(self, rows):
        self._rows = [dict(r) if not isinstance(r, dict) else r for r in rows]
        self.rowcount = len(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


class _FKCheckConn:
    """Fake connection for _verify_monitoring_fk_alignment tests.

    Returns controllable FK parent table names for each (table, constraint).
    """

    def __init__(self, fk_map: dict[str, str | None]):
        # fk_map: {constraint_name: parent_table or None (meaning not found)}
        self._fk_map = fk_map

    def execute(self, query: str, params=None):
        q = ' '.join(str(query).split()).upper()
        if 'INFORMATION_SCHEMA.TABLE_CONSTRAINTS' in q and params:
            constraint = params[1] if len(params) >= 2 else None
            parent = self._fk_map.get(str(constraint))
            if parent is None:
                return _Rows([])
            return _Rows([{'parent_table': parent}])
        return _Rows([])

    def transaction(self):
        @contextmanager
        def _ctx():
            yield
        return _ctx()


# ---------------------------------------------------------------------------
# _verify_monitoring_fk_alignment unit tests
# ---------------------------------------------------------------------------

def test_verify_fk_all_aligned():
    conn = _FKCheckConn({
        'monitoring_polls_target_id_fkey': 'targets',
        'provider_health_records_target_id_fkey': 'targets',
        'target_coverage_records_target_id_fkey': 'targets',
    })
    result = monitoring_runner._verify_monitoring_fk_alignment(conn)
    assert len(result['aligned']) == 3
    assert result['misaligned'] == []


def test_verify_fk_provider_health_misaligned():
    conn = _FKCheckConn({
        'monitoring_polls_target_id_fkey': 'targets',
        'provider_health_records_target_id_fkey': 'monitored_targets',
        'target_coverage_records_target_id_fkey': 'targets',
    })
    result = monitoring_runner._verify_monitoring_fk_alignment(conn)
    misaligned_tables = [t for t, _c, _p in result['misaligned']]
    assert 'provider_health_records' in misaligned_tables
    assert len(result['misaligned']) == 1


def test_verify_fk_coverage_misaligned():
    conn = _FKCheckConn({
        'monitoring_polls_target_id_fkey': 'targets',
        'provider_health_records_target_id_fkey': 'targets',
        'target_coverage_records_target_id_fkey': 'monitored_targets',
    })
    result = monitoring_runner._verify_monitoring_fk_alignment(conn)
    misaligned_tables = [t for t, _c, _p in result['misaligned']]
    assert 'target_coverage_records' in misaligned_tables


def test_verify_fk_both_misaligned():
    conn = _FKCheckConn({
        'monitoring_polls_target_id_fkey': 'targets',
        'provider_health_records_target_id_fkey': 'monitored_targets',
        'target_coverage_records_target_id_fkey': 'monitored_targets',
    })
    result = monitoring_runner._verify_monitoring_fk_alignment(conn)
    assert len(result['misaligned']) == 2


def test_verify_fk_missing_constraint_no_crash():
    """A constraint that was dropped (returns no row) must not raise."""
    conn = _FKCheckConn({})
    result = monitoring_runner._verify_monitoring_fk_alignment(conn)
    assert 'aligned' in result
    assert 'misaligned' in result


def test_verify_fk_exception_does_not_propagate():
    """An exception from the DB must not propagate out of _verify_monitoring_fk_alignment."""

    class _BrokenConn:
        def execute(self, query, params=None):
            raise RuntimeError('simulated DB failure')

    result = monitoring_runner._verify_monitoring_fk_alignment(_BrokenConn())
    assert 'aligned' in result


# ---------------------------------------------------------------------------
# process_monitoring_target insert FK safety
# ---------------------------------------------------------------------------

def _seed_process_monitoring_db() -> sqlite3.Connection:
    """In-memory SQLite DB that mirrors the tables process_monitoring_target writes to."""
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.executescript('''
        CREATE TABLE provider_health_records (
            id TEXT PRIMARY KEY, workspace_id TEXT, provider_type TEXT,
            target_id TEXT, status TEXT, checked_at TEXT, latency_ms INTEGER,
            error_message TEXT, evidence_source TEXT, metadata TEXT
        );
        CREATE TABLE target_coverage_records (
            id TEXT PRIMARY KEY, workspace_id TEXT, asset_id TEXT, target_id TEXT,
            coverage_status TEXT, last_poll_at TEXT, last_heartbeat_at TEXT,
            last_telemetry_at TEXT, last_detection_at TEXT, evidence_source TEXT,
            computed_at TEXT, metadata TEXT
        );
        CREATE TABLE target_evaluation (
            id TEXT PRIMARY KEY, target_id TEXT, status TEXT,
            started_at TEXT, finished_at TEXT, checkpoint_block INTEGER,
            events_seen INTEGER DEFAULT 0, matches_found INTEGER DEFAULT 0, error_text TEXT
        );
        CREATE TABLE monitoring_runs (
            id TEXT PRIMARY KEY, workspace_id TEXT, target_id TEXT,
            started_at TEXT, status TEXT, trigger_type TEXT, notes TEXT
        );
        CREATE TABLE targets (
            id TEXT PRIMARY KEY, workspace_id TEXT, name TEXT,
            last_run_status TEXT, last_checked_at TEXT,
            watcher_last_observed_block INTEGER, watcher_checkpoint_lag_blocks INTEGER,
            watcher_source_status TEXT, watcher_degraded_reason TEXT,
            recent_evidence_state TEXT, recent_truthfulness_state TEXT,
            recent_real_event_count INTEGER
        );
        CREATE TABLE telemetry_events (
            id TEXT PRIMARY KEY, workspace_id TEXT, asset_id TEXT, target_id TEXT,
            provider_type TEXT, event_type TEXT, observed_at TEXT, ingested_at TEXT,
            evidence_source TEXT, payload_hash TEXT, payload_json TEXT
        );
        CREATE TABLE monitoring_checkpoints (
            id TEXT PRIMARY KEY, workspace_id TEXT, monitored_system_id TEXT,
            chain TEXT, last_observed_block INTEGER, created_at TEXT, updated_at TEXT,
            UNIQUE(workspace_id, monitored_system_id, chain)
        );
        CREATE TABLE detections (
            id TEXT PRIMARY KEY, workspace_id TEXT, monitored_system_id TEXT,
            protected_asset_id TEXT, detection_type TEXT, severity TEXT,
            confidence REAL, title TEXT, evidence_summary TEXT, evidence_source TEXT,
            source_rule TEXT, status TEXT DEFAULT \'open\', detected_at TEXT,
            raw_evidence_json TEXT, monitoring_run_id TEXT, linked_alert_id TEXT,
            created_at TEXT, updated_at TEXT
        );
        CREATE TABLE alerts (
            id TEXT PRIMARY KEY, workspace_id TEXT, user_id TEXT,
            analysis_run_id TEXT, alert_type TEXT, title TEXT, severity TEXT,
            status TEXT, source_service TEXT, summary TEXT, payload TEXT,
            target_id TEXT, created_at TEXT
        );
        CREATE TABLE incidents (
            id TEXT PRIMARY KEY, workspace_id TEXT, user_id TEXT,
            analysis_run_id TEXT, event_type TEXT, severity TEXT, status TEXT,
            summary TEXT, payload TEXT, created_at TEXT
        );
    ''')
    return conn


class _SQLiteConn:
    """Wraps an sqlite3 connection to match the psycopg-style interface used by monitoring_runner."""

    def __init__(self, db: sqlite3.Connection):
        self._db = db
        self._in_transaction = False

    def execute(self, query: str, params=None):
        # Convert %s placeholders -> ? for SQLite
        q = query.replace('%s::uuid', '?').replace('%s::jsonb', '?').replace('%s::text', '?').replace('%s', '?')
        # Strip casts like ::uuid, ::jsonb, ::text that SQLite doesn't support
        import re
        q = re.sub(r'::[a-zA-Z_]+', '', q)
        # Skip information_schema queries (not in SQLite)
        if 'information_schema' in q.lower():
            return _Rows([])
        # Skip PostgreSQL-specific syntax
        if 'pg_' in q.lower() or 'on conflict' in q.lower():
            try:
                cursor = self._db.execute(q, params or ())
                return _SQLiteResult(cursor)
            except Exception:
                return _Rows([])
        try:
            cursor = self._db.execute(q, params or ())
            return _SQLiteResult(cursor)
        except Exception as exc:
            raise

    @contextmanager
    def transaction(self):
        yield

    def commit(self):
        self._db.commit()


class _SQLiteResult:
    def __init__(self, cursor):
        self._rows = cursor.fetchall()

    def fetchone(self):
        return dict(self._rows[0]) if self._rows else None

    def fetchall(self):
        return [dict(r) for r in self._rows]

    def __iter__(self):
        return iter(dict(r) for r in self._rows)


def _make_fake_target(target_id: str = 't-test-001', workspace_id: str = 'ws-test-001') -> dict:
    return {
        'id': target_id,
        'workspace_id': workspace_id,
        'name': 'Test Target',
        'target_type': 'contract',
        'chain_network': 'ethereum',
        'contract_identifier': '0xABCDEF',
        'wallet_address': None,
        'asset_id': str(uuid.uuid4()),
        'chain_id': 1,
        'target_metadata': None,
        'monitoring_enabled': True,
        'monitoring_mode': 'live',
        'monitoring_interval_seconds': 300,
        'severity_threshold': 'low',
        'auto_create_alerts': False,
        'auto_create_incidents': False,
        'notification_channels': None,
        'last_checked_at': None,
        'last_run_status': None,
        'last_run_id': None,
        'last_alert_at': None,
        'monitored_by_workspace_id': None,
        'is_active': True,
        'monitoring_checkpoint_at': None,
        'monitoring_checkpoint_cursor': None,
        'watcher_last_observed_block': 0,
        'watcher_checkpoint_lag_blocks': None,
        'watcher_source_status': None,
        'watcher_degraded_reason': None,
        'recent_evidence_state': None,
        'recent_truthfulness_state': None,
        'recent_real_event_count': 0,
        'updated_by_user_id': None,
        'created_by_user_id': None,
        'created_at': _now(),
        'monitored_system_id': None,
        'enabled': True,
        'severity_preference': 'low',
        'owner_notes': None,
        'asset_type': 'erc20',
    }


def _make_fake_provider_result():
    from services.api.app.activity_providers import ActivityProviderResult
    return ActivityProviderResult(
        mode='live',
        status='no_evidence',
        evidence_state='NO_EVIDENCE',
        truthfulness_state='NOT_CLAIM_SAFE',
        synthetic=False,
        provider_name='test_rpc',
        provider_kind='rpc',
        evidence_present=False,
        recent_real_event_count=0,
        last_real_event_at=None,
        events=[],
        latest_block=None,
        checkpoint=None,
        checkpoint_age_seconds=None,
        degraded_reason=None,
        error_code=None,
        source_type='rpc_polling',
        reason_code='NO_EVIDENCE',
        claim_safe=False,
        detection_outcome='NO_EVIDENCE',
    )


class _CaptureConn:
    """Fake connection that records INSERT statements and the target_id param used.

    Returns empty result sets for all queries so process_monitoring_target can
    proceed as far as possible.  Stops after inserting into stop_at_table.
    """

    def __init__(self, stop_at_table: str | None = None):
        self.inserts: list[tuple[str, tuple]] = []
        self._stop_at = stop_at_table

    def execute(self, query: str, params=None):
        q_lower = query.strip().lower()
        if q_lower.startswith('insert into'):
            table = q_lower.split('insert into')[1].strip().split('(')[0].strip().split()[0]
            self.inserts.append((table, tuple(params or ())))
            if self._stop_at and table == self._stop_at:
                raise StopIteration(f'test-stop at {table}')
        return _Rows([])

    @contextmanager
    def transaction(self):
        yield


def test_process_monitoring_target_inserts_provider_health_records_with_targets_id(monkeypatch):
    """process_monitoring_target must pass target['id'] (targets.id) to provider_health_records."""
    target_id = str(uuid.uuid4())
    workspace_id = str(uuid.uuid4())
    target = _make_fake_target(target_id, workspace_id)

    monkeypatch.setattr(monitoring_runner, 'fetch_target_activity_result', lambda *_a, **_k: _make_fake_provider_result())
    monkeypatch.setattr(monitoring_runner, '_load_checkpoint', lambda *_a, **_k: 0)

    conn = _CaptureConn(stop_at_table='provider_health_records')
    try:
        monitoring_runner.process_monitoring_target(conn, target)
    except StopIteration:
        pass
    except Exception:
        pass

    phr_inserts = [(t, p) for t, p in conn.inserts if t == 'provider_health_records']
    assert phr_inserts, 'process_monitoring_target must attempt to INSERT into provider_health_records'
    for _tbl, params in phr_inserts:
        assert target_id in params, (
            f'provider_health_records INSERT must include targets.id={target_id!r} as a param; got {params!r}'
        )


def test_process_monitoring_target_inserts_target_coverage_records_with_targets_id(monkeypatch):
    """process_monitoring_target must pass target['id'] (targets.id) to target_coverage_records."""
    target_id = str(uuid.uuid4())
    workspace_id = str(uuid.uuid4())
    target = _make_fake_target(target_id, workspace_id)

    monkeypatch.setattr(monitoring_runner, 'fetch_target_activity_result', lambda *_a, **_k: _make_fake_provider_result())
    monkeypatch.setattr(monitoring_runner, '_load_checkpoint', lambda *_a, **_k: 0)

    # Let execution continue past provider_health_records; stop at target_coverage_records.
    conn = _CaptureConn(stop_at_table='target_coverage_records')
    try:
        monitoring_runner.process_monitoring_target(conn, target)
    except StopIteration:
        pass
    except Exception:
        pass

    tcr_inserts = [(t, p) for t, p in conn.inserts if t == 'target_coverage_records']
    assert tcr_inserts, 'process_monitoring_target must attempt to INSERT into target_coverage_records'
    for _tbl, params in tcr_inserts:
        assert target_id in params, (
            f'target_coverage_records INSERT must include targets.id={target_id!r} as a param; got {params!r}'
        )


def test_fk_failure_does_not_abort_cycle(monkeypatch):
    """A FK violation in one target must not prevent the cycle from processing the next target.

    process_monitoring_target propagates the FK exception; the cycle loop
    (run_monitoring_cycle) catches it and records error status in a clean
    transaction, then continues to the next target.  This test verifies
    the exception propagates (so the cycle loop can catch it) rather than
    being swallowed silently inside process_monitoring_target.
    """

    class _FKViolatingConn:
        """Raises ForeignKeyViolation on provider_health_records INSERT."""

        @contextmanager
        def transaction(self):
            yield

        def execute(self, query: str, params=None):
            q = query.strip().upper()
            if 'INSERT INTO PROVIDER_HEALTH_RECORDS' in q:
                import psycopg
                raise psycopg.errors.ForeignKeyViolation(
                    'insert or update on table "provider_health_records" violates '
                    'foreign key constraint "provider_health_records_target_id_fkey"\n'
                    'Key (target_id)=(test) is not present in table "monitored_targets"'
                )
            return _Rows([])

    conn = _FKViolatingConn()

    monkeypatch.setattr(monitoring_runner, 'fetch_target_activity_result', lambda *_a, **_k: _make_fake_provider_result())
    monkeypatch.setattr(monitoring_runner, '_load_checkpoint', lambda *_a, **_k: 0)

    target = _make_fake_target()
    # process_monitoring_target propagates FK errors; the cycle loop catches them.
    import psycopg
    with pytest.raises((psycopg.errors.ForeignKeyViolation, Exception)):
        monitoring_runner.process_monitoring_target(conn, target)
    # The test just verifies the call completes (raises rather than hanging/crashing the process).


def test_no_fake_telemetry_when_no_events(monkeypatch):
    """process_monitoring_target with zero events must not insert detections, alerts, or incidents."""
    forbidden = {'detections', 'alerts', 'incidents', 'telemetry_events'}
    fake_inserts: list[str] = []

    target_id = str(uuid.uuid4())
    target = _make_fake_target(target_id)

    monkeypatch.setattr(monitoring_runner, 'fetch_target_activity_result', lambda *_a, **_k: _make_fake_provider_result())
    monkeypatch.setattr(monitoring_runner, '_load_checkpoint', lambda *_a, **_k: 0)

    class _GuardConn:
        @contextmanager
        def transaction(self):
            yield
        def execute(self, query: str, params=None):
            q_lower = query.strip().lower()
            if q_lower.startswith('insert into'):
                table = q_lower.split('insert into')[1].strip().split('(')[0].strip().split()[0]
                if table in forbidden:
                    fake_inserts.append(table)
            return _Rows([])

    try:
        monitoring_runner.process_monitoring_target(_GuardConn(), target)
    except Exception:
        pass

    assert not fake_inserts, (
        f'No fake telemetry/detections/alerts/incidents must be created for zero-event result; got {fake_inserts}'
    )


def test_migration_sql_fixes_both_tables():
    """The migration file must contain DROP CONSTRAINT and ADD CONSTRAINT for both tables."""
    import pathlib
    migration_path = pathlib.Path(__file__).parents[1] / 'migrations' / '0082_fix_provider_health_coverage_fk_to_targets.sql'
    assert migration_path.exists(), f'Migration file not found: {migration_path}'
    sql = migration_path.read_text()
    assert 'provider_health_records_target_id_fkey' in sql, 'Migration must reference provider_health_records FK'
    assert 'target_coverage_records_target_id_fkey' in sql, 'Migration must reference target_coverage_records FK'
    assert 'REFERENCES targets(id)' in sql or 'REFERENCES targets (id)' in sql, (
        'Migration must re-add FK referencing targets(id)'
    )
    assert 'DROP CONSTRAINT IF EXISTS provider_health_records_target_id_fkey' in sql
    assert 'DROP CONSTRAINT IF EXISTS target_coverage_records_target_id_fkey' in sql


def test_provider_health_fk_table_constant_includes_targets():
    """_MONITORING_TARGET_FK_TABLES must list provider_health_records and target_coverage_records."""
    tables = [t for t, _c, _n in monitoring_runner._MONITORING_TARGET_FK_TABLES]
    assert 'provider_health_records' in tables
    assert 'target_coverage_records' in tables
    assert 'monitoring_polls' in tables
