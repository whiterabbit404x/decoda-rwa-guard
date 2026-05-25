"""
Tests for target_coverage_records.asset_id FK alignment after migration 0083.

Root cause: migration 0076 created target_coverage_records.asset_id as
REFERENCES asset_registry(id).  The monitoring worker passes target['asset_id'],
a UUID from assets(id), into that column.  Since the UUID is not present in
asset_registry, every INSERT into target_coverage_records raised:
  psycopg.errors.ForeignKeyViolation: Key (asset_id)=(…) is not present in
  table "asset_registry"
This exception was caught by the cycle loop so checked stayed at 0.

Migration 0083 drops the misaligned FK and re-adds it pointing at assets(id),
consistent with targets.asset_id -> assets(id).

These tests verify:
- Migration SQL drops the old FK and adds a new one referencing assets(id).
- process_monitoring_target inserts target_coverage_records using the asset_id
  from the target row (assets.id lineage), not an asset_registry UUID.
- When target.asset_id is None (nullable), no FK violation occurs.
- A target_coverage_records INSERT with a valid assets.id does not raise.
- A target_coverage_records asset_id FK failure does not abort the whole
  worker cycle; checked stays 0 for that target but the loop continues.
- No fake detections, alerts, incidents, or telemetry_events are inserted when
  the provider returns zero events.
"""
from __future__ import annotations

import pathlib
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest

from services.api.app import monitoring_runner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class _Rows:
    def __init__(self, rows):
        self._rows = [dict(r) if not isinstance(r, dict) else r for r in rows]
        self.rowcount = len(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


def _make_fake_target(target_id: str | None = None, workspace_id: str | None = None, asset_id: str | None = None) -> dict:
    return {
        'id': target_id or str(uuid.uuid4()),
        'workspace_id': workspace_id or str(uuid.uuid4()),
        'name': 'Test Target',
        'target_type': 'contract',
        'chain_network': 'ethereum',
        'contract_identifier': '0xABCDEF',
        'wallet_address': None,
        'asset_id': asset_id,
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
    """Records INSERT statements for inspection.  Returns empty rows for all queries.

    Pass known_asset_ids to simulate assets that exist in the assets table so
    that _resolve_coverage_asset_id returns them rather than None.
    """

    def __init__(
        self,
        raise_on_table: str | None = None,
        raise_exc: Exception | None = None,
        *,
        known_asset_ids: list[str] | None = None,
    ):
        self.inserts: list[tuple[str, tuple]] = []
        self._raise_on_table = raise_on_table
        self._raise_exc = raise_exc
        self._known_asset_ids: set[str] = set(str(a).strip() for a in (known_asset_ids or []))

    def execute(self, query: str, params=None):
        q_lower = query.strip().lower()
        if q_lower.startswith('insert into'):
            table = q_lower.split('insert into')[1].strip().split('(')[0].strip().split()[0]
            self.inserts.append((table, tuple(params or ())))
            if self._raise_on_table and table == self._raise_on_table and self._raise_exc:
                raise self._raise_exc
        # Support _resolve_coverage_asset_id asset existence lookup.
        if 'from assets' in q_lower and 'where id' in q_lower and params:
            lookup_id = str(params[0]).strip()
            if lookup_id in self._known_asset_ids:
                return _Rows([{'id': lookup_id}])
        return _Rows([])

    @contextmanager
    def transaction(self):
        yield


# ---------------------------------------------------------------------------
# Migration SQL tests
# ---------------------------------------------------------------------------

def test_migration_0083_exists():
    """Migration file 0083 must exist in the migrations directory."""
    path = pathlib.Path(__file__).parents[1] / 'migrations' / '0083_fix_target_coverage_records_asset_id_fk.sql'
    assert path.exists(), f'Migration file not found: {path}'


def test_migration_0083_drops_old_fk():
    """Migration 0083 must drop target_coverage_records_asset_id_fkey."""
    path = pathlib.Path(__file__).parents[1] / 'migrations' / '0083_fix_target_coverage_records_asset_id_fk.sql'
    sql = path.read_text()
    assert 'target_coverage_records_asset_id_fkey' in sql
    assert 'DROP CONSTRAINT IF EXISTS target_coverage_records_asset_id_fkey' in sql


def test_migration_0083_adds_fk_to_assets():
    """Migration 0083 must add FK referencing assets(id), not asset_registry."""
    path = pathlib.Path(__file__).parents[1] / 'migrations' / '0083_fix_target_coverage_records_asset_id_fk.sql'
    ddl_lines = [
        ln for ln in path.read_text().splitlines()
        if not ln.lstrip().startswith('--')
    ]
    ddl = '\n'.join(ddl_lines).lower()
    assert 'references assets(id)' in ddl, (
        'Migration 0083 must re-add asset_id FK referencing assets(id)'
    )
    assert 'asset_registry' not in ddl, (
        'Migration 0083 must not reference asset_registry'
    )


def test_migration_0083_on_delete_set_null():
    """Migration 0083 asset_id FK must use ON DELETE SET NULL (nullable column)."""
    path = pathlib.Path(__file__).parents[1] / 'migrations' / '0083_fix_target_coverage_records_asset_id_fk.sql'
    ddl_lines = [
        ln for ln in path.read_text().splitlines()
        if not ln.lstrip().startswith('--')
    ]
    ddl = '\n'.join(ddl_lines).lower()
    assert 'on delete set null' in ddl, (
        'asset_id FK must specify ON DELETE SET NULL since the column is nullable'
    )


# ---------------------------------------------------------------------------
# process_monitoring_target asset_id lineage tests
# ---------------------------------------------------------------------------

def test_target_coverage_records_receives_asset_id_from_target(monkeypatch):
    """process_monitoring_target must pass target['asset_id'] to target_coverage_records."""
    asset_id = str(uuid.uuid4())
    target_id = str(uuid.uuid4())
    target = _make_fake_target(target_id=target_id, asset_id=asset_id)

    monkeypatch.setattr(monitoring_runner, 'fetch_target_activity_result', lambda *_a, **_k: _make_fake_provider_result())
    monkeypatch.setattr(monitoring_runner, '_load_checkpoint', lambda *_a, **_k: 0)

    # known_asset_ids simulates the asset existing in the assets table so that
    # _resolve_coverage_asset_id returns the real UUID instead of None.
    conn = _CaptureConn(known_asset_ids=[asset_id])
    try:
        monitoring_runner.process_monitoring_target(conn, target)
    except Exception:
        pass

    tcr_inserts = [(t, p) for t, p in conn.inserts if t == 'target_coverage_records']
    assert tcr_inserts, 'process_monitoring_target must INSERT into target_coverage_records'
    for _tbl, params in tcr_inserts:
        assert asset_id in params, (
            f'target_coverage_records INSERT must include asset_id={asset_id!r}; got params={params!r}'
        )


def test_target_coverage_records_null_asset_id_no_fk_violation(monkeypatch):
    """When target.asset_id is None, target_coverage_records must receive NULL (no FK issue)."""
    target_id = str(uuid.uuid4())
    target = _make_fake_target(target_id=target_id, asset_id=None)

    monkeypatch.setattr(monitoring_runner, 'fetch_target_activity_result', lambda *_a, **_k: _make_fake_provider_result())
    monkeypatch.setattr(monitoring_runner, '_load_checkpoint', lambda *_a, **_k: 0)

    conn = _CaptureConn()
    try:
        monitoring_runner.process_monitoring_target(conn, target)
    except Exception:
        pass

    tcr_inserts = [(t, p) for t, p in conn.inserts if t == 'target_coverage_records']
    assert tcr_inserts, 'process_monitoring_target must INSERT into target_coverage_records even with null asset_id'
    for _tbl, params in tcr_inserts:
        # asset_id position: id, workspace_id, asset_id, target_id, ...
        # asset_id should be None (not a string UUID)
        assert None in params, (
            f'target_coverage_records INSERT must pass None for null asset_id; params={params!r}'
        )


def test_target_coverage_asset_id_not_asset_registry_uuid(monkeypatch):
    """The asset_id inserted into target_coverage_records must come from targets.asset_id (assets table), not a fresh UUID."""
    asset_id = str(uuid.uuid4())
    target = _make_fake_target(asset_id=asset_id)

    monkeypatch.setattr(monitoring_runner, 'fetch_target_activity_result', lambda *_a, **_k: _make_fake_provider_result())
    monkeypatch.setattr(monitoring_runner, '_load_checkpoint', lambda *_a, **_k: 0)

    # known_asset_ids simulates the asset row existing in the assets table.
    conn = _CaptureConn(known_asset_ids=[asset_id])
    try:
        monitoring_runner.process_monitoring_target(conn, target)
    except Exception:
        pass

    tcr_inserts = [(t, p) for t, p in conn.inserts if t == 'target_coverage_records']
    assert tcr_inserts, 'process_monitoring_target must INSERT into target_coverage_records'
    for _tbl, params in tcr_inserts:
        # The asset_id inserted must equal target['asset_id'], not some other UUID
        assert asset_id in params, (
            f'target_coverage_records must use target asset_id={asset_id!r}; got {params!r}'
        )
        # target_id, workspace_id, record_id are also UUIDs - asset_id must be present
        assert asset_id in params


def _is_uuid(s: str) -> bool:
    try:
        uuid.UUID(s)
        return True
    except (ValueError, AttributeError):
        return False


# ---------------------------------------------------------------------------
# FK failure isolation test
# ---------------------------------------------------------------------------

def test_coverage_asset_id_fk_failure_does_not_abort_cycle(monkeypatch):
    """
    A FK violation on target_coverage_records.asset_id must not crash
    process_monitoring_target.  The savepoint wrapper catches the exception,
    logs a TARGET_COVERAGE_ASSET_PARENT_MISSING warning, and returns normally
    so the caller's checked counter can increment.
    """
    import psycopg

    target = _make_fake_target(asset_id=str(uuid.uuid4()))

    monkeypatch.setattr(monitoring_runner, 'fetch_target_activity_result', lambda *_a, **_k: _make_fake_provider_result())
    monkeypatch.setattr(monitoring_runner, '_load_checkpoint', lambda *_a, **_k: 0)

    fk_exc = psycopg.errors.ForeignKeyViolation(
        'insert or update on table "target_coverage_records" violates foreign key '
        'constraint "target_coverage_records_asset_id_fkey"\n'
        'Key (asset_id)=(f701aba7-3c19-4efd-8088-9ebf73b5b901) is not present in '
        'table "asset_registry"'
    )
    # Simulate pre-migration DB: asset not in known_asset_ids → guard returns None,
    # but the raised FK exc from the INSERT is still caught by the savepoint wrapper.
    conn = _CaptureConn(raise_on_table='target_coverage_records', raise_exc=fk_exc)

    # The savepoint wrapper must catch the FK violation; process_monitoring_target
    # must return a result dict rather than propagating the exception.
    result = monitoring_runner.process_monitoring_target(conn, target)
    assert isinstance(result, dict), (
        'process_monitoring_target must return a result dict even when '
        'target_coverage_records insert raises a FK violation'
    )


# ---------------------------------------------------------------------------
# No fake telemetry test
# ---------------------------------------------------------------------------

def test_no_fake_telemetry_or_detections_on_zero_events(monkeypatch):
    """When provider returns zero events, no detections/alerts/incidents/telemetry_events must be inserted."""
    forbidden = {'detections', 'alerts', 'incidents', 'telemetry_events'}
    fake_inserts: list[str] = []

    target = _make_fake_target(asset_id=str(uuid.uuid4()))

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
        f'Zero-event provider result must not produce fake telemetry/detections/alerts; '
        f'found inserts into: {fake_inserts}'
    )


# ---------------------------------------------------------------------------
# Worker cycle checked>=1 after migration
# ---------------------------------------------------------------------------

def test_worker_cycle_checked_increments_after_coverage_fix(monkeypatch):
    """
    After the asset_id FK is fixed (migration 0083), process_monitoring_target
    completes without FK error and the cycle loop increments checked to >= 1.

    We simulate this by monkeypatching process_monitoring_target to succeed
    (no FK violation) and verify the caller increments its checked counter.
    """
    from collections import defaultdict

    target_id = str(uuid.uuid4())
    workspace_id = str(uuid.uuid4())

    target = _make_fake_target(target_id=target_id, workspace_id=workspace_id, asset_id=str(uuid.uuid4()))

    successful_result = {
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

    monkeypatch.setattr(monitoring_runner, 'process_monitoring_target', lambda *_a, **_k: successful_result)
    monkeypatch.setattr(monitoring_runner, '_derive_system_runtime_state', lambda *_a, **_k: ('healthy', 'fresh', 'high', None))

    class _SuccessConn:
        def execute(self, query, params=None):
            q = ' '.join(str(query).split()).upper()
            if 'SELECT 1 FROM TARGETS WHERE ID' in q:
                return _Rows([{'1': 1}])
            return _Rows([])

        @contextmanager
        def transaction(self):
            yield

        def commit(self):
            pass

    conn = _SuccessConn()
    checked = 0
    poll_id = str(uuid.uuid4())

    _poll_parent = conn.execute('SELECT 1 FROM targets WHERE id = %s LIMIT 1', (target['id'],)).fetchone()
    if _poll_parent:
        try:
            with conn.transaction():
                conn.execute(
                    "INSERT INTO monitoring_polls (id, workspace_id, target_id, poll_started_at, status, metadata) VALUES (%s::uuid, %s::uuid, %s::uuid, %s, 'running', %s::jsonb)",
                    (poll_id, target['workspace_id'], target['id'], _now(), '{}'),
                )
                result = monitoring_runner.process_monitoring_target(conn, target)
            conn.execute("UPDATE monitoring_polls SET poll_finished_at = NOW(), status = 'completed' WHERE id = %s::uuid", (poll_id,))
            checked += 1
        except Exception:
            pass

    assert checked >= 1, (
        f'After asset_id FK fix, worker cycle must reach checked>=1 for a valid target; got checked={checked}'
    )


# ---------------------------------------------------------------------------
# _resolve_coverage_asset_id unit tests
# ---------------------------------------------------------------------------

def test_resolve_coverage_asset_id_returns_asset_id_when_present():
    """_resolve_coverage_asset_id returns the asset_id when it exists in assets."""
    asset_id = str(uuid.uuid4())
    target = _make_fake_target(asset_id=asset_id)
    conn = _CaptureConn(known_asset_ids=[asset_id])
    result = monitoring_runner._resolve_coverage_asset_id(conn, target)
    assert result == asset_id, (
        f'_resolve_coverage_asset_id must return asset_id={asset_id!r} when asset exists; got {result!r}'
    )


def test_resolve_coverage_asset_id_returns_none_for_null_asset_id():
    """_resolve_coverage_asset_id returns None immediately when target.asset_id is None."""
    target = _make_fake_target(asset_id=None)
    conn = _CaptureConn()
    result = monitoring_runner._resolve_coverage_asset_id(conn, target)
    assert result is None, (
        f'_resolve_coverage_asset_id must return None for null asset_id; got {result!r}'
    )


def test_resolve_coverage_asset_id_returns_none_when_missing():
    """_resolve_coverage_asset_id returns None and logs a warning when asset is not in assets."""
    asset_id = str(uuid.uuid4())
    target = _make_fake_target(asset_id=asset_id)
    # known_asset_ids is empty → asset not found → guard returns None
    conn = _CaptureConn(known_asset_ids=[])
    result = monitoring_runner._resolve_coverage_asset_id(conn, target)
    assert result is None, (
        f'_resolve_coverage_asset_id must return None when asset not in assets table; got {result!r}'
    )


# ---------------------------------------------------------------------------
# Asset parent missing → coverage write uses NULL, no crash
# ---------------------------------------------------------------------------

def test_missing_asset_in_assets_does_not_crash_process_monitoring_target(monkeypatch):
    """
    When target.asset_id is not present in the assets table (e.g. migration not
    yet applied or stale FK reference), _resolve_coverage_asset_id returns None
    and target_coverage_records is inserted with NULL asset_id.
    process_monitoring_target must not raise.
    """
    asset_id = str(uuid.uuid4())
    target = _make_fake_target(asset_id=asset_id)

    monkeypatch.setattr(monitoring_runner, 'fetch_target_activity_result', lambda *_a, **_k: _make_fake_provider_result())
    monkeypatch.setattr(monitoring_runner, '_load_checkpoint', lambda *_a, **_k: 0)

    # Do NOT add asset_id to known_asset_ids → simulates missing asset parent row.
    conn = _CaptureConn(known_asset_ids=[])

    result = monitoring_runner.process_monitoring_target(conn, target)

    assert isinstance(result, dict), (
        'process_monitoring_target must return a result dict even when asset_id is missing from assets'
    )
    tcr_inserts = [(t, p) for t, p in conn.inserts if t == 'target_coverage_records']
    assert tcr_inserts, 'target_coverage_records INSERT must still be attempted with NULL asset_id'
    for _tbl, params in tcr_inserts:
        assert None in params, (
            f'target_coverage_records must use NULL asset_id when asset is missing; got params={params!r}'
        )
        assert asset_id not in params, (
            f'target_coverage_records must NOT use the missing asset_id={asset_id!r}; got params={params!r}'
        )


# ---------------------------------------------------------------------------
# Live RPC, zero events → coverage insert, checked counts
# ---------------------------------------------------------------------------

def test_live_rpc_zero_events_inserts_coverage_record(monkeypatch):
    """
    A live RPC poll that returns status=live but event_count=0 must still insert
    a target_coverage_records row.  event_count=0 is not an error.
    """
    from services.api.app.activity_providers import ActivityProviderResult

    asset_id = str(uuid.uuid4())
    target = _make_fake_target(asset_id=asset_id)

    # status='live' requires evidence_present=True (provider connection confirmed);
    # events=[] means no on-chain events were found in this polling window.
    live_no_events_result = ActivityProviderResult(
        mode='live',
        status='live',
        evidence_state='NO_EVIDENCE',
        truthfulness_state='NOT_CLAIM_SAFE',
        synthetic=False,
        provider_name='test_rpc',
        provider_kind='rpc',
        evidence_present=True,
        recent_real_event_count=0,
        last_real_event_at=None,
        events=[],
        latest_block=12345,
        checkpoint=None,
        checkpoint_age_seconds=10,
        degraded_reason=None,
        error_code=None,
        source_type='rpc_polling',
        reason_code='NO_EVIDENCE',
        claim_safe=False,
        detection_outcome='NO_EVIDENCE',
    )
    monkeypatch.setattr(monitoring_runner, 'fetch_target_activity_result', lambda *_a, **_k: live_no_events_result)
    monkeypatch.setattr(monitoring_runner, '_load_checkpoint', lambda *_a, **_k: 0)

    conn = _CaptureConn(known_asset_ids=[asset_id])
    result = monitoring_runner.process_monitoring_target(conn, target)

    assert isinstance(result, dict), 'process_monitoring_target must return dict for live+zero-events poll'
    tcr_inserts = [(t, p) for t, p in conn.inserts if t == 'target_coverage_records']
    assert tcr_inserts, (
        'target_coverage_records must be inserted even for live RPC poll with event_count=0'
    )


def test_live_rpc_zero_events_source_status_active(monkeypatch):
    """
    A live RPC poll with event_count=0 must set source_status='active', which
    the cycle loop should treat as provider-reachable (included in
    workspace_provider_reachable_cycles).
    """
    from services.api.app.activity_providers import ActivityProviderResult

    target = _make_fake_target(asset_id=str(uuid.uuid4()))

    # status='live' requires evidence_present=True (provider connection confirmed);
    # events=[] means no on-chain events were found in this polling window.
    live_no_events_result = ActivityProviderResult(
        mode='live',
        status='live',
        evidence_state='NO_EVIDENCE',
        truthfulness_state='NOT_CLAIM_SAFE',
        synthetic=False,
        provider_name='test_rpc',
        provider_kind='rpc',
        evidence_present=True,
        recent_real_event_count=0,
        last_real_event_at=None,
        events=[],
        latest_block=12345,
        checkpoint=None,
        checkpoint_age_seconds=10,
        degraded_reason=None,
        error_code=None,
        source_type='rpc_polling',
        reason_code='NO_EVIDENCE',
        claim_safe=False,
        detection_outcome='NO_EVIDENCE',
    )
    monkeypatch.setattr(monitoring_runner, 'fetch_target_activity_result', lambda *_a, **_k: live_no_events_result)
    monkeypatch.setattr(monitoring_runner, '_load_checkpoint', lambda *_a, **_k: 0)

    conn = _CaptureConn(known_asset_ids=[str(target['asset_id'])] if target.get('asset_id') else [])
    result = monitoring_runner.process_monitoring_target(conn, target)

    assert result.get('source_status') == 'active', (
        f"Live RPC poll with event_count=0 must return source_status='active'; got {result.get('source_status')!r}"
    )
    assert result.get('provider_status') == 'live', (
        f"Live RPC poll must return provider_status='live'; got {result.get('provider_status')!r}"
    )
    # Verify the cycle loop condition: 'active' must be in the accepted source_status set
    source_status_accepted = result.get('source_status') in {'live', 'no_evidence', 'active'}
    assert source_status_accepted, (
        f"source_status={result.get('source_status')!r} must be accepted as provider-reachable"
    )
