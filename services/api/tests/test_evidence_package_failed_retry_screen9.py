"""Screen 9 — a retry after a TERMINAL FAILED evidence package must mint a NEW one.

Production retest symptom (Issue B): a fresh "Create Evidence Package" click for
incident ``3c42654c-…`` generated a new ``request_id`` but the backend reused the
historical FAILED package id ``64e9aaa3-…`` (``job_id/package_id`` unchanged),
requeued it in place, failed again, and the package list stayed at 7 — so no
``EV-2026-008`` was ever created.

Root cause: ``create_proof_bundle_export`` treated ANY existing package for the same
source fingerprint that was not "completed + retrievable" as reusable
(``reuse_existing = existing is not None and not supersede``) and regenerated INTO
THE SAME ROW — including a TERMINAL FAILED package, whose row it flipped back to
``queued`` and re-ran, mutating the historical failed evidence object forever.

Fix: a terminal ``failed`` package for the same snapshot is NOT reused. A
user-triggered retry mints a NEW package attempt (new internal id + the next
``EV-YYYY-NNN`` number) that records ``retry_of`` the failed package; the failed row
is left untouched (immutable/auditable). Two behaviours are deliberately preserved:
a stale/missing COMPLETED artifact is still a storage repair (regenerate into the
same id), and an active ``queued`` row for the same snapshot is still reused
idempotently so a duplicate click / refresh never forks a package.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from services.api.app import pilot


WS = 'ws-1'
USER = 'user-1'
INCIDENT = 'inc-1'
FAILED_ID = '64e9aaa3-8b57-4a19-bea1-d536d55818d9'  # the historical terminal-failed package


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


def _req(workspace_id: str = WS) -> SimpleNamespace:
    return SimpleNamespace(headers={'x-workspace-id': workspace_id}, query_params=_EmptyQueryParams())


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
        if object_key not in self.written:
            raise FileNotFoundError(object_key)
        return self.written[object_key]


class _RetryChainConnection:
    """Incident chain for create_proof_bundle_export/_generate_export_artifact whose
    existing package (same source fingerprint) has a configurable terminal status.
    Tracks which package ids are touched by reuse/terminal UPDATEs so a test can prove
    the historical failed row is never mutated."""

    def __init__(self, *, existing_status: str, existing_storage_key: str = 'ws-1/pkg-existing.json'):
        self.executed: list[tuple[str, object]] = []
        self.committed = False
        self._existing_status = existing_status
        self._existing_storage_key = existing_storage_key
        self.inserted_pkg_id: str | None = None
        self.inserted_filters: dict | None = None
        self.reuse_update_ids: list[str] = []      # ids hit by the reuse "SET status='queued'" UPDATE
        self.status: str | None = None
        self.error_message: str | None = None
        self.package_number: str | None = None

    def execute(self, stmt, params=None):
        n = ' '.join(str(stmt).split())
        self.executed.append((n, params))
        return self._handle(n, params)

    def commit(self):
        self.committed = True

    def _handle(self, n, params):
        if 'pg_advisory_xact_lock' in n:
            return _Row(None)
        # Fingerprint reuse lookup — returns the existing package (same snapshot).
        if 'evidence_source_fingerprint' in n and "(filters->>'response_action_id') IS NULL" in n:
            return _Row({
                'id': FAILED_ID,
                'status': self._existing_status,
                'storage_object_key': self._existing_storage_key,
                'package_number': 'EV-2026-007' if self._existing_status == 'completed' else None,
                'filters': {'incident_id': INCIDENT, 'include_raw_events': True},
            })
        # Supersession-target lookup (latest incident package, any fingerprint).
        if "(filters->>'response_action_id') IS NULL" in n and 'ORDER BY created_at DESC LIMIT 1' in n:
            return _Row({'id': FAILED_ID})
        # Reuse-in-place regeneration (stale/missing completed OR active queued).
        if 'UPDATE export_jobs SET filters = %s::jsonb' in n and "status = 'queued'" in n:
            self.reuse_update_ids.append(params[1])
            self.status = 'queued'
            return _Row(None)
        if 'INSERT INTO export_jobs' in n:
            self.inserted_pkg_id = params[0]
            self.inserted_filters = json.loads(params[3]) if params[3] else {}
            self.status = 'queued'
            return _Row(None)
        if 'SELECT id, export_type, format, filters, requested_by_user_id FROM export_jobs' in n:
            return _Row({
                'id': params[0], 'export_type': 'proof_bundle', 'format': 'json',
                'filters': {'incident_id': INCIDENT, 'include_raw_events': True},
                'requested_by_user_id': USER,
            })
        # ── Incident evidence chain ──
        if 'FROM incidents WHERE workspace_id = %s AND id = %s' in n:
            return _Row({'id': INCIDENT, 'workspace_id': WS, 'title': 'Incident 1', 'severity': 'high',
                         'status': 'open', 'asset_id': 'asset-1', 'linked_alert_ids': ['alert-1']})
        if 'FROM alerts a JOIN detection_metrics dm' in n:
            return _Row(rows=[{'id': 'alert-1', 'severity': 'high', 'source': 'live', 'target_id': 'target-1'}])
        if 'FROM detection_metrics WHERE workspace_id = %s AND incident_id = %s' in n:
            return _Row(rows=[{'id': 'metric-1', 'event_observed_at': '2026-01-01T00:00:00Z',
                               'detected_at': '2026-01-01T00:02:00Z', 'mttd_seconds': 120, 'evidence': {}}])
        if 'FROM response_actions' in n and 'incident_id = %s' in n:
            return _Row(rows=[{'id': 'action-1', 'action_type': 'notify_team', 'status': 'executed', 'mode': 'live',
                               'execution_metadata': None, 'created_at': '2026-01-01T00:10:00Z',
                               'executed_at': '2026-01-01T00:10:00Z', 'rolled_back_at': None}])
        if 'FROM detections' in n and 'linked_alert_id = ANY' in n:
            return _Row(rows=[{'id': 'det-1', 'detection_type': 'anomaly', 'severity': 'high', 'confidence': 0.9,
                               'evidence_source': 'live', 'status': 'open', 'detected_at': '2026-01-01T00:01:00Z', 'title': 'Anomaly'}])
        if 'FROM detections' in n and 'id = %s::uuid' in n:
            return _Row(None)
        if 'FROM audit_logs' in n and 'row_hash' in n:
            return _Row(None)
        if 'FROM audit_logs' in n:
            return _Row(rows=[])
        if 'FROM incident_timeline' in n:
            return _Row(rows=[])
        # ── Terminal / numbering UPDATEs + reads ──
        if "UPDATE export_jobs SET status = 'completed'" in n:
            self.status = 'completed'
            return _Row(None)
        if "UPDATE export_jobs SET status = 'failed'" in n:
            self.status = 'failed'
            self.error_message = params[0]
            return _Row(None)
        if 'SELECT status, package_number FROM export_jobs WHERE id = %s' in n:
            return _Row({'status': self.status, 'package_number': self.package_number})
        if 'MAX(NULLIF(regexp_replace(package_number' in n:
            return _Row({'max_seq': 7})   # 7 existing EV numbers → next is EV-...-008
        if 'UPDATE export_jobs SET package_number' in n:
            self.package_number = params[0]
            return _Row(None)
        if 'SELECT status, error_message, package_number FROM export_jobs WHERE id = %s' in n:
            return _Row({'status': self.status, 'error_message': self.error_message, 'package_number': self.package_number})
        raise AssertionError(f'unexpected query: {n!r}')


def _patch(monkeypatch, conn, storage):
    @contextmanager
    def _pg():
        yield conn

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'pg_connection', _pg)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, '_require_workspace_permission', lambda *_a, **_k: (
        {'id': USER, 'mfa_enabled': False}, {'workspace_id': WS, 'role': 'admin'}))
    monkeypatch.setattr(pilot, '_workspace_plan', lambda *_a, **_k: {'exports_enabled': True})
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_a, **_k: {'id': USER})
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda *_a, **_k: {'workspace_id': WS, 'role': 'admin'})
    monkeypatch.setattr(pilot, '_workspace_permission_granted', lambda *_a, **_k: True)
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: storage)


def _update_stmts(conn):
    return [(s, p) for s, p in conn.executed if 'UPDATE export_jobs' in s]


# ── #8 / #10: a terminal FAILED package is not reused/mutated as the retry job ────

def test_terminal_failed_package_is_not_requeued_in_place(monkeypatch):
    """(#8) A retry after a terminal FAILED package does NOT flip the failed row back
    to 'queued' and re-run it — the historical failed evidence object is never the
    mutable retry job. A brand-new row is inserted instead."""
    conn = _RetryChainConnection(existing_status='failed', existing_storage_key='ws-1/64e9aaa3.json')
    _patch(monkeypatch, conn, _FakeStorage())

    result = pilot.create_proof_bundle_export({'incident_id': INCIDENT}, _req())

    # No reuse "SET status='queued'" UPDATE ran at all (no in-place requeue).
    assert conn.reuse_update_ids == [], 'A terminal failed package must not be requeued in place'
    # A new row WAS inserted (the new attempt).
    inserts = [s for s, _ in conn.executed if 'INSERT INTO export_jobs' in s]
    assert inserts, 'A retry after terminal failure must insert a NEW package row'
    assert result['package_id'] != FAILED_ID


def test_historical_failed_package_row_is_never_mutated(monkeypatch):
    """(#10) The failed package id 64e9aaa3-… appears in NO UPDATE statement — it is
    never converted, requeued, renumbered, or overwritten."""
    conn = _RetryChainConnection(existing_status='failed', existing_storage_key='ws-1/64e9aaa3.json')
    _patch(monkeypatch, conn, _FakeStorage())

    pilot.create_proof_bundle_export({'incident_id': INCIDENT}, _req())

    for stmt, params in _update_stmts(conn):
        assert FAILED_ID not in (params or ()), f'failed row must not be mutated by: {stmt!r}'


# ── #9: retrying a FAILED package creates a new package id + next package number ──

def test_retry_after_failure_creates_new_package_id_and_next_number(monkeypatch):
    """(#9) The retry mints a NEW internal id and is assigned the next EV number
    (EV-2026-008, given 7 existing) once it completes, and links retry_of the failed
    package for auditable lineage."""
    conn = _RetryChainConnection(existing_status='failed', existing_storage_key='ws-1/64e9aaa3.json')
    _patch(monkeypatch, conn, _FakeStorage())

    result = pilot.create_proof_bundle_export({'incident_id': INCIDENT}, _req())

    assert result['created'] is True
    assert result['package_id'] == conn.inserted_pkg_id
    assert result['package_id'] != FAILED_ID, 'A retry must not reuse the failed id'
    assert result['status'] == 'completed'
    assert result['package_number'] == 'EV-2026-008', 'The retry gets the next package number'
    # Truthful lineage recorded on the NEW row (never on the failed one).
    assert conn.inserted_filters.get('retry_of') == FAILED_ID
    assert conn.inserted_filters.get('supersedes_package_id') == FAILED_ID


# ── #11: duplicate submission during an ACTIVE queued generation stays idempotent ──

def test_active_queued_package_is_reused_in_place_not_duplicated(monkeypatch):
    """(#11) An existing ACTIVE (queued) package for the same snapshot is reused in
    place (same id, no duplicate row) — a duplicate click during generation never
    forks a second package."""
    conn = _RetryChainConnection(existing_status='queued', existing_storage_key='ws-1/64e9aaa3.json')
    _patch(monkeypatch, conn, _FakeStorage())

    result = pilot.create_proof_bundle_export({'incident_id': INCIDENT}, _req())

    assert result['package_id'] == FAILED_ID, 'An active queued row is reused in place'
    assert conn.reuse_update_ids == [FAILED_ID], 'The active row is regenerated in place'
    inserts = [s for s, _ in conn.executed if 'INSERT INTO export_jobs' in s]
    assert not inserts, 'Reusing an active queued row must not insert a duplicate'


# ── #12: a re-submit for a completed+retrievable package makes no duplicate ────────

def test_completed_retrievable_package_reused_without_duplicate(monkeypatch):
    """(#12) Re-submitting for an already completed + retrievable package (what a
    refresh / repeated click resolves to) returns the existing package (created=False)
    and inserts NO duplicate — polling/refresh can never create a second package."""
    storage = _FakeStorage()
    storage.written['ws-1/64e9aaa3.json'] = b'{"rows": []}'
    conn = _RetryChainConnection(existing_status='completed', existing_storage_key='ws-1/64e9aaa3.json')
    _patch(monkeypatch, conn, storage)

    result = pilot.create_proof_bundle_export({'incident_id': INCIDENT}, _req())

    assert result['package_id'] == FAILED_ID
    assert result['created'] is False
    assert result['status'] == 'completed'
    inserts = [s for s, _ in conn.executed if 'INSERT INTO export_jobs' in s]
    assert not inserts, 'Idempotent reuse must not insert a duplicate'
    assert conn.reuse_update_ids == [], 'A healthy completed package must not be regenerated'
