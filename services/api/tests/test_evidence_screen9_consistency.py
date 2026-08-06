"""
Screen 9 (Evidence & Audit) consistency / truthfulness regression tests.

These lock in the fixes for the contradictory package states that the deployed
page showed (no hash shown as "Hash generated", every completed row counted as
export-ready, Files Hashed = 0 while integrity said hashes existed, Verified = 0,
completeness "Unknown", retention hardcoded "Compliant", raw UUIDs, and audit
count presenting the 500-row page cap as the exact lifetime total).

They exercise the canonical selectors directly plus the list/audit endpoints via
a mocked connection (no live DB), matching the existing evidence test harness.
"""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from services.api.app import pilot
from services.api.app import evidence_completeness as ec


# ── Canonical selector unit tests ─────────────────────────────────────────────

def _pkg(**over):
    base = {
        'id': 'uuid-1',
        'status': 'completed',
        'export_type': 'proof_bundle',
        'size_bytes': 1000,
        'incident_id': 'inc-1',
    }
    base.update(over)
    return base


def test_no_hash_is_legacy_export_not_hash_generated():
    """A completed export with no manifest/file hashes must be a legacy export,
    never 'hash_generated' — this is the root contradiction."""
    state = ec.get_evidence_package_display_state(_pkg())
    assert state['integrity_status'] == ec.INTEGRITY_LEGACY_EXPORT
    assert state['hash_state'] == ec.HASH_STATE_NONE
    assert state['is_legacy_export'] is True
    assert state['is_export_ready'] is False
    assert state['files_hashed'] == 0


def test_hashes_without_verification_is_hash_generated():
    state = ec.get_evidence_package_display_state(
        _pkg(integrity_hash='a' * 64, files_hashed=6, completeness_score=90),
    )
    assert state['integrity_status'] == ec.INTEGRITY_HASH_GENERATED
    assert state['hash_state'] == ec.HASH_STATE_PRESENT
    assert state['files_hashed'] == 6
    assert state['ready_for_verification'] is True
    assert state['is_export_ready'] is False  # not yet verified


def test_verified_requires_passing_verification():
    state = ec.get_evidence_package_display_state(
        _pkg(integrity_hash='a' * 64, files_hashed=6,
             verification={'valid': True, 'verified_at': '2026-01-01T00:00:00Z'}),
    )
    assert state['integrity_status'] == ec.INTEGRITY_VERIFIED
    assert state['is_export_ready'] is True


def test_failed_hash_comparison_is_integrity_failed():
    state = ec.get_evidence_package_display_state(
        _pkg(integrity_hash='a' * 64, files_hashed=6, verification={'valid': False}),
    )
    assert state['integrity_status'] == ec.INTEGRITY_INTEGRITY_FAILED
    assert state['is_export_ready'] is False


def test_building_shows_generating_hash_state():
    state = ec.get_evidence_package_display_state(_pkg(status='queued'))
    assert state['integrity_status'] == ec.INTEGRITY_BUILDING
    assert state['hash_state'] == ec.HASH_STATE_GENERATING


def test_files_hashed_counts_manifest_files_when_no_stored_count():
    state = ec.get_evidence_package_display_state(
        _pkg(integrity_hash='a' * 64,
             files={'a.json': None, 'b.json': None},  # not a list → ignored
             ),
    )
    # No list of file dicts and no stored files_hashed → 0 (never invented).
    assert state['files_hashed'] == 0
    state2 = ec.get_evidence_package_display_state(
        _pkg(integrity_hash='a' * 64,
             files=[{'sha256': 'x'}, {'sha256': 'y'}, {'sha256': None}]),
    )
    assert state2['files_hashed'] == 2


def test_export_ready_excludes_non_terminal_and_failed_states():
    assert ec.is_evidence_package_export_ready(_pkg(status='queued')) is False
    assert ec.is_evidence_package_export_ready(_pkg()) is False  # legacy
    assert ec.is_evidence_package_export_ready(
        _pkg(integrity_hash='a' * 64, files_hashed=1, verification={'valid': False})) is False
    assert ec.is_evidence_package_export_ready(
        _pkg(integrity_hash='a' * 64, files_hashed=1, verification={'valid': True})) is True


def test_superseded_wins_over_hashes():
    state = ec.get_evidence_package_display_state(
        _pkg(integrity_hash='a' * 64, files_hashed=6, superseded=True),
    )
    assert state['integrity_status'] == ec.INTEGRITY_SUPERSEDED
    assert state['is_export_ready'] is False


def test_workspace_metrics_do_not_show_unknown_when_scores_exist():
    packages = [
        _pkg(id='p1', integrity_hash='a' * 64, files_hashed=6, completeness_score=90,
             verification={'valid': True}),
        _pkg(id='p2', integrity_hash='b' * 64, files_hashed=6, completeness_score=70),
        _pkg(id='p3'),  # legacy, no score
    ]
    m = ec.get_workspace_evidence_metrics(packages)
    assert m['total_packages'] == 3
    assert m['verified'] == 1
    assert m['files_hashed'] == 12
    assert m['legacy_exports'] == 1
    assert m['export_ready'] == 1            # only the verified one
    assert m['ready_for_verification'] == 1  # the hashed-but-unverified one
    # Average is a real number (80), never "Unknown".
    assert m['average_completeness'] == 80


def test_workspace_metrics_average_is_none_when_no_scores():
    m = ec.get_workspace_evidence_metrics([_pkg(id='p1'), _pkg(id='p2')])
    assert m['average_completeness'] is None  # UI renders "Not calculated"
    assert m['verified'] == 0
    assert m['files_hashed'] == 0


def test_legacy_export_not_counted_as_verified():
    packages = [_pkg(id='p1'), _pkg(id='p2'), _pkg(id='p3')]  # all legacy
    m = ec.get_workspace_evidence_metrics(packages)
    assert m['verified'] == 0
    assert m['export_ready'] == 0
    assert m['legacy_exports'] == 3


def test_allowed_actions_are_permission_and_state_aware():
    verified = _pkg(integrity_hash='a' * 64, files_hashed=6, verification={'valid': True})
    # No export permission → no mutating/download actions.
    denied = ec.get_package_allowed_actions(verified, can_export=False)
    assert denied['download'] is False
    assert denied['verify'] is False
    assert denied['view'] is True
    # With permission on a hashed package → download/verify/manifest allowed.
    allowed = ec.get_package_allowed_actions(verified, can_export=True)
    assert allowed['download'] is True
    assert allowed['verify'] is True
    assert allowed['download_manifest'] is True
    assert allowed['copy_hash'] is True
    # Legacy export (no manifest) → verify/manifest disabled even with permission.
    legacy = ec.get_package_allowed_actions(_pkg(), can_export=True)
    assert legacy['verify'] is False
    assert legacy['download_manifest'] is False
    assert legacy['copy_hash'] is False


def test_integrity_labels_are_canonical():
    assert ec.integrity_status_label('hash_generated') == 'Hash Generated'
    assert ec.integrity_status_label('legacy_export') == 'Legacy Export'
    assert ec.integrity_status_label('verified') == 'Verified'
    assert ec.integrity_status_label(None) == 'Unknown'


def test_derive_integrity_status_has_hashes_signal():
    # Completed + no hashes + no verification → legacy export.
    assert ec.derive_integrity_status(
        job_status='completed', verification=None, completeness=None, has_hashes=False,
    ) == ec.INTEGRITY_LEGACY_EXPORT
    # Completed + hashes present → hash generated.
    assert ec.derive_integrity_status(
        job_status='completed', verification=None, completeness=None, has_hashes=True,
    ) == ec.INTEGRITY_HASH_GENERATED


# ── list_exports integration (mocked connection) ──────────────────────────────

class _Row:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _ExportsConn:
    """Returns export rows for the main SELECT; None for count/permission fetchone."""

    def __init__(self, rows):
        self._rows = rows

    def execute(self, stmt, params=None):
        q = ' '.join(str(stmt).split())
        if 'FROM export_jobs' in q and 'SELECT' in q:
            return _Row(rows=self._rows)
        # retention query / permission query / anything else
        return _Row(rows=[], row=None)

    def commit(self):
        pass


def _patch_exports(monkeypatch, rows, role='analyst'):
    @contextmanager
    def _pg():
        yield _ExportsConn(rows)

    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _pg)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_: {'id': 'u-1'})
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda *_: {'workspace_id': 'ws-1', 'role': role})


def _req(**qp):
    return SimpleNamespace(
        headers={'x-workspace-id': 'ws-1'},
        query_params=SimpleNamespace(get=lambda k, d=None: qp.get(k, d)),
    )


def _export_row(pkg_id, filters, status='completed', package_number=None):
    return {
        'id': pkg_id,
        'workspace_id': 'ws-1',
        'export_type': 'proof_bundle',
        'format': 'json',
        'status': status,
        'output_path': f'ws-1/{pkg_id}.json',
        'storage_backend': 's3',
        'storage_object_key': f'ws-1/{pkg_id}.json',
        'error_message': None,
        'filters': filters,
        'size_bytes': 2000,
        'package_number': package_number,
        'created_at': '2026-06-22T00:00:00Z',
        'updated_at': '2026-06-22T00:01:00Z',
    }


def test_list_exports_three_legacy_rows_are_not_export_ready(monkeypatch):
    """The deployed contradiction: 3 completed rows with no hash must not show as
    hash_generated/export-ready. They are legacy exports with 0 files hashed."""
    rows = [
        _export_row('u1', {'incident_id': 'inc-1'}),
        _export_row('u2', {'incident_id': 'inc-2'}),
        _export_row('u3', {'incident_id': 'inc-3'}),
    ]
    _patch_exports(monkeypatch, rows)
    result = pilot.list_exports(_req())

    for pkg in result['exports']:
        assert pkg['integrity_status'] == 'legacy_export'
        assert pkg['hash_state'] == 'not_generated'
        assert pkg['export_ready'] is False
        assert pkg['files_hashed'] == 0
    m = result['metrics']
    assert m['export_ready'] == 0
    assert m['verified'] == 0
    assert m['files_hashed'] == 0
    assert m['legacy_exports'] == 3
    # Retention is not hardcoded to Compliant.
    assert result['retention_status'] != 'Compliant'
    assert result['retention_status'] == 'Not Configured'


def test_list_exports_hashed_row_reports_hash_generated_and_files(monkeypatch):
    rows = [_export_row('u1', {
        'incident_id': 'inc-1', 'integrity_hash': 'c' * 64, 'files_hashed': 8,
        'completeness_score': 92,
    })]
    _patch_exports(monkeypatch, rows)
    pkg = pilot.list_exports(_req())['exports'][0]
    assert pkg['integrity_status'] == 'hash_generated'
    assert pkg['hash_state'] == 'present'
    assert pkg['files_hashed'] == 8
    assert pkg['export_ready'] is False  # not verified yet
    assert pkg['ready_for_verification'] is True


def test_list_exports_verified_row_counts_as_export_ready(monkeypatch):
    rows = [_export_row('u1', {
        'incident_id': 'inc-1', 'integrity_hash': 'c' * 64, 'files_hashed': 8,
        'verification': {'valid': True, 'verified_at': '2026-06-22T00:05:00Z'},
    })]
    _patch_exports(monkeypatch, rows)
    result = pilot.list_exports(_req())
    pkg = result['exports'][0]
    assert pkg['integrity_status'] == 'verified'
    assert pkg['export_ready'] is True
    assert result['metrics']['export_ready'] == 1
    assert result['metrics']['verified'] == 1


def test_list_exports_returns_readable_ids_and_keeps_uuid(monkeypatch):
    rows = [_export_row('11112222-3333-4444-5555-666677778888', {'incident_id': 'aaaabbbb-cccc'})]
    _patch_exports(monkeypatch, rows)
    pkg = pilot.list_exports(_req())['exports'][0]
    # Human-readable display id (fallback form) — never a raw UUID as the label.
    assert pkg['package_number'].startswith('EV-')
    assert pkg['incident_short_id'] == 'INC-aaaabbbb'
    # Internal UUID stays available for routing/lookup.
    assert pkg['id'] == '11112222-3333-4444-5555-666677778888'


def test_list_exports_uses_persisted_package_number_when_present(monkeypatch):
    rows = [_export_row('u1', {'incident_id': 'inc-1'}, package_number='EV-2026-001')]
    _patch_exports(monkeypatch, rows)
    pkg = pilot.list_exports(_req())['exports'][0]
    assert pkg['package_number'] == 'EV-2026-001'


def test_list_exports_includes_allowed_actions_and_permission(monkeypatch):
    rows = [_export_row('u1', {
        'incident_id': 'inc-1', 'integrity_hash': 'c' * 64, 'files_hashed': 8,
        'verification': {'valid': True},
    })]
    _patch_exports(monkeypatch, rows, role='analyst')
    result = pilot.list_exports(_req())
    assert result['can_export'] is True
    pkg = result['exports'][0]
    assert pkg['allowed_actions']['download'] is True
    assert pkg['allowed_actions']['verify'] is True


def test_list_exports_viewer_cannot_export(monkeypatch):
    rows = [_export_row('u1', {'incident_id': 'inc-1'})]
    _patch_exports(monkeypatch, rows, role='viewer')
    result = pilot.list_exports(_req())
    assert result['can_export'] is False


# ── list_audit_events total / capped semantics ────────────────────────────────

class _AuditConn:
    def __init__(self, audit_rows, total):
        self._audit_rows = audit_rows
        self._total = total

    def execute(self, stmt, params=None):
        q = ' '.join(str(stmt).split())
        if 'COUNT(*)' in q and 'audit_logs' in q:
            return _Row(row={'total': self._total})
        if 'FROM audit_logs' in q:
            return _Row(rows=self._audit_rows)
        return _Row(rows=[], row=None)

    def commit(self):
        pass


def _patch_audit(monkeypatch, audit_rows, total):
    @contextmanager
    def _pg():
        yield _AuditConn(audit_rows, total)

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _pg)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_: {'id': 'u-1'})
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda *_: {'workspace_id': 'ws-1', 'role': 'analyst'})


def _audit_row(i):
    return {
        'id': f'a{i}', 'action': 'evidence_package_generated', 'entity_type': 'export_job',
        'entity_id': 'u1', 'user_id': 'u-1', 'ip_address': '10.0.0.1',
        'metadata': {'result': 'success'}, 'created_at': '2026-06-22T00:00:00Z',
    }


def test_audit_events_reports_capped_when_total_exceeds_page(monkeypatch):
    rows = [_audit_row(i) for i in range(500)]
    _patch_audit(monkeypatch, rows, total=1234)
    result = pilot.list_audit_events(SimpleNamespace(headers={'x-workspace-id': 'ws-1'}))
    assert result['returned'] == 500
    assert result['total'] == 1234
    assert result['limit'] == 500
    assert result['capped'] is True  # 500-row page is not the lifetime total


def test_audit_events_not_capped_when_within_page(monkeypatch):
    rows = [_audit_row(i) for i in range(12)]
    _patch_audit(monkeypatch, rows, total=12)
    result = pilot.list_audit_events(SimpleNamespace(headers={'x-workspace-id': 'ws-1'}))
    assert result['total'] == 12
    assert result['capped'] is False


# ── Retention status helper ───────────────────────────────────────────────────

class _RetConn:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, stmt, params=None):
        return _Row(rows=self._rows)


def test_retention_not_configured_when_no_policy():
    assert pilot._workspace_evidence_retention_status(_RetConn([]), 'ws-1') == 'Not Configured'


def test_retention_compliant_when_exports_and_audit_enabled():
    rows = [
        {'data_class': 'exports', 'retention_days': 365, 'enabled': True},
        {'data_class': 'audit_logs', 'retention_days': 365, 'enabled': True},
    ]
    assert pilot._workspace_evidence_retention_status(_RetConn(rows), 'ws-1') == 'Compliant'


def test_retention_attention_when_partial_or_disabled():
    only_exports = [{'data_class': 'exports', 'retention_days': 365, 'enabled': True}]
    assert pilot._workspace_evidence_retention_status(_RetConn(only_exports), 'ws-1') == 'Attention Required'
    disabled = [
        {'data_class': 'exports', 'retention_days': 365, 'enabled': False},
        {'data_class': 'audit_logs', 'retention_days': 365, 'enabled': True},
    ]
    assert pilot._workspace_evidence_retention_status(_RetConn(disabled), 'ws-1') == 'Attention Required'


# ── Package-number allocation (format + monotonic MAX+1, no reuse) ────────────

class _SeqConn:
    """Serves pg_advisory_xact_lock + MAX(seq) so number allocation can be tested."""

    def __init__(self, current_max):
        self._max = current_max
        self.locked = False

    def execute(self, stmt, params=None):
        q = ' '.join(str(stmt).split())
        if 'pg_advisory_xact_lock' in q:
            self.locked = True
            return _Row(row={'pg_advisory_xact_lock': None})
        if 'MAX(' in q:
            assert self.locked, 'allocation must hold the advisory lock before reading MAX'
            return _Row(row={'max_seq': self._max})
        return _Row(row=None)


def test_package_number_allocation_is_serialized_and_monotonic(monkeypatch):
    monkeypatch.setattr(pilot, 'utc_now_iso', lambda: '2026-06-22T00:00:00Z')
    # First allocation on an empty year → 001.
    assert pilot._next_evidence_package_number(_SeqConn(0), 'ws-1') == 'EV-2026-001'
    # With existing max 3 → 004 (never reuses a number even across deletes).
    assert pilot._next_evidence_package_number(_SeqConn(3), 'ws-1') == 'EV-2026-004'


def test_migration_declares_unique_index_and_advisory_lock():
    """Concurrency safety is enforced structurally: a per-(workspace, package_number)
    unique index plus advisory-lock serialized allocation."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[3]
    migration = (root / 'services/api/migrations/0140_evidence_package_number.sql').read_text()
    assert 'CREATE UNIQUE INDEX' in migration
    assert 'workspace_id, package_number' in migration
    assert 'ADD COLUMN IF NOT EXISTS package_number' in migration
    src = (root / 'services/api/app/pilot.py').read_text()
    assert 'pg_advisory_xact_lock' in src
