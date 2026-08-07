"""Regression tests for the incident "Create Evidence Package" flow.

POST /exports/proof-bundle -> pilot.create_proof_bundle_export. These lock in the
end-to-end fix for the reported bug where the success toast appeared ("Evidence
package queued.") but no row showed up in the table:

1. Creation PERSISTS a real export-job row (status 'queued') and COMMITS it before
   the API returns success — never a browser-only placeholder.
2. A queued/building package is included by GET /exports and is NOT filtered out.
3. The response is canonical: package_id (+ export_job_id / id aliases), incident_id,
   package_number, status, and a `created` flag distinguishing new vs idempotent.
4. Idempotency: a second create for the same incident + evidence scope reuses the
   existing completed+retrievable package (created=False) and inserts no duplicate.
5. A stale/missing prior artifact regenerates into the SAME row id (no duplicate).
6. Fail-closed: unconfigured generation storage raises 503 and commits nothing
   (no fake "queued" row, no success).
7. An upload failure PERSISTS a visible 'failed' row and reports status='failed'
   (truthful — never a success toast).
8. Cross-workspace: packaging an incident outside the caller's workspace is denied
   (404) and commits nothing.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from services.api.app import pilot


# ── Storage doubles ───────────────────────────────────────────────────────────

class _FakeStorage:
    backend_name = 's3'
    bucket = 'decoda-rwa-guard-evidence'
    endpoint = 'https://example.r2.cloudflarestorage.com'

    def __init__(self):
        self.written: dict[str, bytes] = {}

    def write_bytes(self, *, object_key: str, content: bytes) -> str:
        self.written[object_key] = content
        return object_key

    def read_bytes(self, *, object_key: str) -> bytes:
        return self.written.get(object_key, b'{}')


class _FakeStorageObjectMissing(_FakeStorage):
    """Upload works, but a prior object key is gone (ephemeral/redeploy loss)."""

    def read_bytes(self, *, object_key: str) -> bytes:
        raise FileNotFoundError(f'missing object {object_key}')


class _FakeStorageUploadFails(_FakeStorage):
    """Simulates an R2/S3 PutObject failure."""

    def write_bytes(self, *, object_key: str, content: bytes) -> str:
        raise OSError('Connection timeout: cannot reach R2 endpoint')


# ── Fake DB rows / connection ────────────────────────────────────────────────

class _Row:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows if self._rows else ([] if self._row is None else [self._row])


class _EmptyQueryParams:
    def get(self, key, default=None):
        return default


def _fake_request(workspace_id: str = 'ws-1') -> SimpleNamespace:
    return SimpleNamespace(headers={'x-workspace-id': workspace_id}, query_params=_EmptyQueryParams())


def _monkeypatch_common(monkeypatch, connection, *, workspace_id='ws-1', user_id='user-1', storage=None) -> None:
    @contextmanager
    def _fake_pg():
        yield connection

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, '_require_workspace_permission', lambda *_: (
        {'id': user_id, 'mfa_enabled': False},
        {'workspace_id': workspace_id, 'role': 'admin'},
    ))
    monkeypatch.setattr(pilot, '_workspace_plan', lambda *_: {'exports_enabled': True})
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: storage if storage is not None else _FakeStorage())


class _IncidentChainConnection:
    """Full incident chain for _generate_export_artifact('proof_bundle').

    Stateful about export_jobs status / package_number so the create flow's final
    read reflects the same row the generation step just wrote.
    """

    def __init__(
        self,
        *,
        existing_package_id: str | None = None,
        existing_status: str = 'completed',
        existing_storage_key: str | None = 'ws-1/pkg-existing.json',
        incident_found: bool = True,
    ):
        self.executed: list[tuple[str, object]] = []
        self.committed = False
        self._existing_package_id = existing_package_id
        self._existing_status = existing_status
        self._existing_storage_key = existing_storage_key
        self._incident_found = incident_found
        self.inserted_pkg_id: str | None = None
        self.filters_repaired = False
        # export_jobs row state (mutated by the generation UPDATEs)
        self.status: str | None = None
        self.error_message: str | None = None
        self.package_number: str | None = None

    def execute(self, stmt, params=None):
        normalized = ' '.join(str(stmt).split())
        self.executed.append((normalized, params))
        return self._handle(normalized, params)

    def commit(self):
        self.committed = True

    def _handle(self, stmt, params):
        # Idempotency lookup (incident-scoped: response_action_id IS NULL)
        if "(filters->>'response_action_id') IS NULL" in stmt:
            if self._existing_package_id:
                return _Row({
                    'id': self._existing_package_id,
                    'status': self._existing_status,
                    'storage_object_key': self._existing_storage_key,
                    'package_number': 'EV-2026-001' if self._existing_status == 'completed' else None,
                    'filters': {'incident_id': 'inc-1', 'include_raw_events': True},
                })
            return _Row(None)
        # Regenerate-into-same-row filters repair
        if 'UPDATE export_jobs SET filters' in stmt:
            self.filters_repaired = True
            self.status = 'queued'
            return _Row(None)
        # Fresh INSERT
        if 'INSERT INTO export_jobs' in stmt:
            self.inserted_pkg_id = params[0] if params else None
            self.status = 'queued'
            return _Row(None)
        # _generate_export_artifact job fetch
        if 'FROM export_jobs WHERE id = %s AND workspace_id = %s' in stmt:
            pkg_id = (params[0] if params else None) or self.inserted_pkg_id or self._existing_package_id or 'pkg-1'
            return _Row({
                'id': pkg_id,
                'export_type': 'proof_bundle',
                'format': 'json',
                'filters': {'incident_id': 'inc-1', 'include_raw_events': True},
                'requested_by_user_id': 'user-1',
            })
        # Incident (workspace-scoped) — None models a cross-workspace attempt
        if 'FROM incidents WHERE workspace_id = %s AND id = %s' in stmt:
            if not self._incident_found:
                return _Row(None)
            return _Row({'id': 'inc-1', 'workspace_id': 'ws-1', 'title': 'Incident 1', 'severity': 'high', 'status': 'open', 'asset_id': 'asset-1', 'linked_alert_ids': ['alert-1']})
        if 'FROM alerts a JOIN detection_metrics dm' in stmt:
            return _Row(rows=[{'id': 'alert-1', 'severity': 'high', 'source': 'simulator', 'target_id': 'target-1'}])
        if 'FROM detection_metrics WHERE workspace_id = %s AND incident_id = %s' in stmt:
            return _Row(rows=[{'id': 'metric-1', 'event_observed_at': '2026-01-01T00:00:00Z', 'detected_at': '2026-01-01T00:02:00Z', 'mttd_seconds': 120, 'evidence': {}}])
        if 'FROM response_actions' in stmt and 'incident_id = %s' in stmt:
            return _Row(rows=[{'id': 'action-1', 'action_type': 'notify_team', 'status': 'pending', 'mode': 'recommended', 'execution_metadata': None, 'created_at': '2026-01-01T00:10:00Z', 'executed_at': None, 'rolled_back_at': None}])
        if 'FROM detections' in stmt and 'linked_alert_id = ANY' in stmt:
            return _Row(rows=[{'id': 'det-1', 'detection_type': 'anomaly', 'severity': 'high', 'confidence': 0.9, 'evidence_source': 'simulator', 'status': 'open', 'detected_at': '2026-01-01T00:01:00Z', 'title': 'Anomaly'}])
        if 'FROM audit_logs' in stmt and 'row_hash' in stmt:
            return _Row(None)  # chain tip
        if 'FROM audit_logs' in stmt:
            return _Row(rows=[])
        # Generation terminal UPDATEs (mutate tracked state)
        if "UPDATE export_jobs SET status = 'completed'" in stmt:
            self.status = 'completed'
            return _Row(None)
        if "UPDATE export_jobs SET status = 'failed'" in stmt:
            self.status = 'failed'
            self.error_message = params[0] if params else 'error'
            return _Row(None)
        # package_number allocation (best-effort inside _generate)
        if 'SELECT status, package_number FROM export_jobs WHERE id = %s' in stmt:
            return _Row({'status': self.status, 'package_number': self.package_number})
        if 'pg_advisory_xact_lock' in stmt:
            return _Row(None)
        if 'MAX(NULLIF(regexp_replace(package_number' in stmt:
            return _Row({'max_seq': 0})
        if 'UPDATE export_jobs SET package_number' in stmt:
            self.package_number = params[0] if params else None
            return _Row(None)
        # create_proof_bundle_export final read
        if 'SELECT status, error_message, package_number FROM export_jobs WHERE id = %s' in stmt:
            return _Row({'status': self.status, 'error_message': self.error_message, 'package_number': self.package_number})
        raise AssertionError(f'unexpected query: {stmt!r}')


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_create_persists_row_before_returning_success(monkeypatch):
    """A real export-job row is INSERTed (status 'queued') and COMMITTED before the
    API returns — the fix for a success toast with no persisted package."""
    conn = _IncidentChainConnection()
    _monkeypatch_common(monkeypatch, conn)

    result = pilot.create_proof_bundle_export({'incident_id': 'inc-1', 'include_raw_events': True}, _fake_request())

    inserts = [(s, p) for s, p in conn.executed if 'INSERT INTO export_jobs' in s]
    assert inserts, 'A real export_jobs row must be inserted'
    _, params = inserts[0]
    # Inserted with a truthful initial 'queued' state (a literal in the VALUES tuple).
    assert "'queued'" in inserts[0][0]
    # Correct workspace + incident are stored on the persisted row.
    assert params[1] == 'ws-1', 'The requesting workspace must be stored on the row'
    filters = json.loads(params[3])
    assert filters['incident_id'] == 'inc-1'
    assert conn.committed, 'The row must be committed before returning success'
    assert result['created'] is True
    assert result['status'] == 'completed'


def test_create_returns_canonical_identity(monkeypatch):
    """Response carries package_id (+ aliases), incident_id, package_number, status."""
    conn = _IncidentChainConnection()
    _monkeypatch_common(monkeypatch, conn)

    result = pilot.create_proof_bundle_export({'incident_id': 'inc-1'}, _fake_request())

    pkg_id = conn.inserted_pkg_id
    assert result['package_id'] == pkg_id
    assert result['export_job_id'] == pkg_id  # backward-compatible alias
    assert result['id'] == pkg_id
    assert result['incident_id'] == 'inc-1'
    assert result['package_number'] == 'EV-2026-001'
    assert result['download_link'] == f'/exports/{pkg_id}/download'


def test_create_serializes_with_advisory_lock_before_dedup(monkeypatch):
    """Concurrency-safe idempotency: a per-(workspace, incident) transaction
    advisory lock is acquired BEFORE the reuse SELECT and the INSERT, so two
    concurrent equivalent requests cannot both insert a duplicate. The lock key is
    derived from stable authoritative inputs (workspace + incident), never from a
    browser timestamp / random / display number."""
    conn = _IncidentChainConnection()
    _monkeypatch_common(monkeypatch, conn)

    pilot.create_proof_bundle_export({'incident_id': 'inc-1'}, _fake_request())

    stmts = [s for s, _ in conn.executed]
    lock_idx = next((i for i, s in enumerate(stmts) if 'pg_advisory_xact_lock' in s), None)
    dedup_idx = next((i for i, s in enumerate(stmts) if "(filters->>'response_action_id') IS NULL" in s), None)
    insert_idx = next((i for i, s in enumerate(stmts) if 'INSERT INTO export_jobs' in s), None)
    assert lock_idx is not None, 'A transaction advisory lock must guard the create'
    assert dedup_idx is not None and lock_idx < dedup_idx, 'Lock must precede the reuse check'
    assert insert_idx is not None and lock_idx < insert_idx, 'Lock must precede the insert'
    lock_params = next(p for s, p in conn.executed if 'pg_advisory_xact_lock' in s)
    assert 'ws-1' in lock_params[0] and 'inc-1' in lock_params[0], 'Lock key must be workspace+incident scoped'


def test_create_requires_incident_id(monkeypatch):
    conn = _IncidentChainConnection()
    _monkeypatch_common(monkeypatch, conn)
    with pytest.raises(HTTPException) as exc_info:
        pilot.create_proof_bundle_export({'include_raw_events': True}, _fake_request())
    assert exc_info.value.status_code == 400


def test_idempotent_reuse_returns_existing_without_duplicate(monkeypatch):
    """Second create for the same incident reuses the completed+retrievable package
    (created=False) and inserts NO duplicate row."""
    conn = _IncidentChainConnection(existing_package_id='pkg-existing', existing_status='completed', existing_storage_key='ws-1/pkg-existing.json')
    storage = _FakeStorage()
    storage.written['ws-1/pkg-existing.json'] = b'{"rows": []}'
    _monkeypatch_common(monkeypatch, conn, storage=storage)

    result = pilot.create_proof_bundle_export({'incident_id': 'inc-1'}, _fake_request())

    assert result['package_id'] == 'pkg-existing'
    assert result['created'] is False
    assert result['status'] == 'completed'
    inserts = [s for s, _ in conn.executed if 'INSERT INTO export_jobs' in s]
    assert not inserts, 'Idempotent reuse must not insert a duplicate package'
    assert not conn.filters_repaired, 'Healthy reuse must not regenerate'
    assert conn.committed, 'The append-only request event must still be committed'


def test_idempotent_regenerates_stale_object_into_same_row(monkeypatch):
    """Existing completed row whose artifact is gone regenerates into the SAME id."""
    conn = _IncidentChainConnection(existing_package_id='pkg-gone', existing_status='completed', existing_storage_key='ws-1/pkg-gone.json')
    _monkeypatch_common(monkeypatch, conn, storage=_FakeStorageObjectMissing())

    result = pilot.create_proof_bundle_export({'incident_id': 'inc-1'}, _fake_request())

    assert result['package_id'] == 'pkg-gone', 'Reuses the same row id, never a duplicate'
    assert result['created'] is True
    assert conn.filters_repaired, 'A stale row must be repaired + regenerated in place'
    inserts = [s for s, _ in conn.executed if 'INSERT INTO export_jobs' in s]
    assert not inserts, 'Regeneration must not insert a duplicate row'


def test_storage_unconfigured_fails_closed_no_commit(monkeypatch):
    """Unconfigured generation storage raises 503 and commits NOTHING — no fake
    'queued' row and no success."""
    conn = _IncidentChainConnection()
    _monkeypatch_common(monkeypatch, conn)

    def _raise_storage():
        raise RuntimeError('Local export storage backend is disabled in staging/production. Set EXPORT_STORAGE_BACKEND=s3 and EXPORT_S3_BUCKET.')

    monkeypatch.setattr(pilot, 'load_export_storage', _raise_storage)

    with pytest.raises(HTTPException) as exc_info:
        pilot.create_proof_bundle_export({'incident_id': 'inc-1'}, _fake_request())

    assert exc_info.value.status_code == 503
    assert not conn.committed, 'Fail-closed: nothing may be committed when storage is unavailable'


def test_upload_failure_persists_visible_failed_row_not_success(monkeypatch):
    """An upload failure persists a visible 'failed' row and reports status='failed'
    — never a success toast, but the row IS retained for the table."""
    conn = _IncidentChainConnection()
    _monkeypatch_common(monkeypatch, conn, storage=_FakeStorageUploadFails())

    result = pilot.create_proof_bundle_export({'incident_id': 'inc-1'}, _fake_request())

    assert result['status'] == 'failed'
    assert result['download_link'] is None
    assert conn.committed, 'The failed row must be persisted (visible in the list)'
    assert conn.status == 'failed'


def test_cross_workspace_incident_denied(monkeypatch):
    """Packaging an incident that is not in the caller's workspace is denied (404)
    and commits nothing."""
    conn = _IncidentChainConnection(incident_found=False)
    _monkeypatch_common(monkeypatch, conn)

    with pytest.raises(HTTPException) as exc_info:
        pilot.create_proof_bundle_export({'incident_id': 'inc-other-ws'}, _fake_request('ws-attacker'))

    assert exc_info.value.status_code == 404
    assert not conn.committed


def test_unauthorized_creation_denied_no_commit(monkeypatch):
    """A caller lacking evidence.export is denied by the workspace-permission gate
    (403) and nothing is committed — creation is not a public capability."""
    from fastapi import status as _status

    conn = _IncidentChainConnection()
    _monkeypatch_common(monkeypatch, conn)

    def _deny(*_a, **_k):
        raise HTTPException(status_code=_status.HTTP_403_FORBIDDEN, detail='You do not have permission to export evidence.')

    monkeypatch.setattr(pilot, '_require_workspace_permission', _deny)

    with pytest.raises(HTTPException) as exc_info:
        pilot.create_proof_bundle_export({'incident_id': 'inc-1'}, _fake_request())

    assert exc_info.value.status_code == 403
    assert not conn.committed, 'An unauthorized create must persist nothing'
    inserts = [s for s, _ in conn.executed if 'INSERT INTO export_jobs' in s]
    assert not inserts, 'An unauthorized create must not insert a row'


def test_request_audit_event_recorded_with_safe_ids(monkeypatch):
    """An append-only evidence_package_requested audit event is written with safe
    correlation ids (workspace, incident, request id) — and never secrets."""
    conn = _IncidentChainConnection()
    _monkeypatch_common(monkeypatch, conn)

    captured: list[dict] = []

    def _capture(_conn, *, action, metadata=None, **_k):
        captured.append({'action': action, 'metadata': metadata or {}})

    monkeypatch.setattr(pilot, 'log_audit', _capture)

    result = pilot.create_proof_bundle_export({'incident_id': 'inc-1'}, _fake_request())

    requested = [c for c in captured if c['action'] == 'evidence_package_requested']
    assert requested, 'A request audit event must be recorded'
    meta = requested[0]['metadata']
    assert meta.get('incident_id') == 'inc-1'
    assert meta.get('request_id'), 'A correlation/request id must be present'
    assert meta.get('result') == 'requested'
    # The generation/creation event carries the persisted package id + result.
    created_events = [c for c in captured if c['metadata'].get('event_type') == 'evidence_package_created']
    assert created_events, 'A creation audit event must be recorded on completion'
    assert created_events[0]['metadata'].get('status') == 'completed'
    # No secret/credential material is ever placed in audit metadata.
    blob = repr(captured).lower()
    for forbidden in ('authorization', 'bearer ', 'api_key', 'password', 'secret', 'signing_secret'):
        assert forbidden not in blob, f'audit metadata must not contain {forbidden!r}'
    assert result['created'] is True


# ── GET /exports must include queued/building packages ────────────────────────

def _list_conn_with_status(status_value: str):
    class _ListConnection:
        def execute(self, stmt, params=None):
            normalized = ' '.join(str(stmt).split())
            if 'FROM export_jobs' in normalized and 'ORDER BY created_at DESC' in normalized:
                return _Row(rows=[{
                    'id': 'pkg-inflight',
                    'export_type': 'proof_bundle',
                    'format': 'json',
                    'status': status_value,
                    'output_path': 'ws-1/pkg-inflight.json',
                    'storage_backend': 'pending',
                    'storage_object_key': 'ws-1/pkg-inflight.json',
                    'error_message': None,
                    'filters': {'incident_id': 'inc-1', 'include_raw_events': True},
                    'size_bytes': None,
                    'package_number': None,
                    'created_at': '2026-01-01T00:00:00Z',
                    'updated_at': '2026-01-01T00:00:00Z',
                }])
            if 'workspace_retention_policies' in normalized:
                return _Row(rows=[])
            if 'workspace_permissions' in normalized or 'role_permissions' in normalized:
                return _Row(rows=[])
            return _Row(rows=[])
    return _ListConnection()


@pytest.mark.parametrize('status_value', ['queued', 'building'])
def test_list_includes_in_progress_package_not_filtered(monkeypatch, status_value):
    """A queued/building proof-bundle appears in GET /exports and is labelled as a
    building evidence package — never filtered out as "not completed"."""
    @contextmanager
    def _fake_pg():
        yield _list_conn_with_status(status_value)

    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_: {'id': 'user-1'})
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda *_: {'workspace_id': 'ws-1', 'role': 'admin'})

    result = pilot.list_exports(_fake_request())

    ids = [p['id'] for p in result['exports']]
    assert 'pkg-inflight' in ids, 'In-progress package must not be filtered out of GET /exports'
    pkg = next(p for p in result['exports'] if p['id'] == 'pkg-inflight')
    assert pkg['integrity_status'] == 'building'
    assert pkg['hash_state'] in {'generating', 'not_generated'}
    # The workspace count includes the in-progress package (3 -> 4 semantics).
    assert result['metrics']['total_packages'] == 1
