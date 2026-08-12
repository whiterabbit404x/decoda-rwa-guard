"""Screen 9 — evidence detail-response self-consistency (EV-2026-004).

These lock in the canonical-selector fix for the LIVE production contradiction on
EV-2026-004. The authoritative GET /api/exports/{id} response reported a package
with NO retrievable manifest (manifest_reference.exists == false, files == [],
files_hashed == 0, integrity_hash == null) while ALSO claiming, via the stored
build-time completeness snapshot, that the file_hashes and manifest_hash evidence
categories were "present" and the "File hashes generated" checklist item was true.

A package's completeness is computed once, at generation time, with
``has_manifest=True`` / ``files_hashed=N`` by construction — so the persisted
snapshot always claims hashes are present. When the manifest later turns out to be
unretrievable, surfacing that snapshot verbatim makes the detail response
self-contradictory. The fix reconciles the snapshot's HASH evidence against the
canonical manifest reference (resolved fresh against stored bytes) so the detail
response can never claim hashes it cannot produce.

The tests exercise the real detail handler plus the canonical selectors with the
project's fake-DB + fake-storage harness (no live DB, no network, no LLM).

  * Regression: the exact EV-2026-004 production shape yields a response with no
    internal contradictions (section 9 of the task).
  * Invariants: the canonical rules that make such a contradiction impossible
    (section 10 of the task).
"""
from __future__ import annotations

import json
from contextlib import contextmanager

import pytest

from services.api.app import pilot
from services.api.app import evidence_completeness as ec
from services.api.app.evidence_signing import (
    build_evidence_manifest,
    seal_manifest,
    canonical_json,
)

WS_ID = 'ws-detail-1'
USER_ID = 'user-detail-1'
PKG_ID = 'pkg-detail-1'
OBJECT_KEY = f'evidence/{WS_ID}/f7caa149.json'

# The 8 files a proof bundle stores (see _generate_export_artifact).
_PARTIAL_FILES = {
    'summary.json': {'export_id': PKG_ID, 'incident_id': 'inc-1'},
    'alerts.json': [{'id': 'alert-1', 'severity': 'high'}],
    'incidents.json': [{'id': 'inc-1', 'status': 'open'}],
    'detections.json': [{'id': 'det-1'}],
    'response_actions.json': [],
    'audit_log.json': [{'id': 'aud-1', 'action': 'incident.opened'}],
    'evidence.json': [{'id': 'ev-1'}],
    'detection_metrics.json': [{'id': 'dm-1'}],
}


def _build_time_completeness() -> dict:
    """The completeness snapshot exactly as _generate_export_artifact persists it:
    computed with has_manifest=True + files_hashed=8, so the file_hashes and
    manifest_hash categories come out 'present' and hashes_generated is true."""
    return ec.compute_evidence_completeness({
        'incident_status': 'open',
        'evidence_source_type': 'live',
        'has_incident': True,
        'has_alert': True,
        'has_detection': True,
        'has_telemetry': True,
        'has_asset': True,
        'has_audit_events': True,
        'has_investigation_timeline': True,
        'has_chain_metadata': False,
        'has_manifest': True,       # claimed at build time
        'files_hashed': 8,          # claimed at build time
        'response_action_count': 0,
        'executed_action_count': 0,
        'rejected_action_count': 0,
        'requires_approval': False,
        'approval_present': False,
        'has_execution_result': False,
        'manifest_verified': None,
    })


def _stored_bundle(*, include_manifest: bool, completeness: dict | None) -> bytes:
    """Serialize a stored package artifact as _generate_export_artifact does:
    ``{'rows': [ {<files>[, 'manifest.json', 'seal.json']} ]}``. The summary carries
    the build-time completeness snapshot. ``include_manifest=False`` reproduces the
    EV-2026-004 artifact (no manifest embedded)."""
    files = {k: (dict(v) if isinstance(v, dict) else v) for k, v in _PARTIAL_FILES.items()}
    if completeness is not None:
        files['summary.json'] = {**files['summary.json'], 'completeness': completeness}
    bundle = dict(files)
    if include_manifest:
        manifest, _ = build_evidence_manifest(
            export_id=PKG_ID, export_type='proof_bundle', workspace_id=WS_ID,
            generated_at='2026-01-01T00:00:00Z', generated_by_user_id=USER_ID,
            source_resource_type='incident', source_resource_id='inc-1',
            storage_backend='local', file_values=files,
        )
        bundle['manifest.json'] = manifest
        bundle['seal.json'] = seal_manifest(manifest)
    return json.dumps({'rows': [bundle]}).encode('utf-8')


class _Result:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row

    def fetchall(self):
        if self._row is None:
            return []
        return self._row if isinstance(self._row, list) else [self._row]


class _FakeStorage:
    backend_name = 'local'

    def __init__(self, content: bytes | None):
        self._content = content

    def read_bytes(self, *, object_key: str) -> bytes:
        if self._content is None:
            raise FileNotFoundError(object_key)
        return self._content

    def get_object_size(self, *, object_key: str):
        return len(self._content) if self._content is not None else None


class _Conn:
    def __init__(self, *, filters):
        self._filters = filters

    def execute(self, statement, params=None):
        n = ' '.join(str(statement).split())
        if 'SELECT * FROM export_jobs WHERE id = %s AND workspace_id = %s' in n:
            return _Result({
                'id': PKG_ID, 'workspace_id': WS_ID, 'export_type': 'proof_bundle',
                'format': 'json', 'status': 'completed', 'output_path': 'x',
                'storage_backend': 'local', 'storage_object_key': OBJECT_KEY,
                'error_message': None, 'filters': dict(self._filters), 'size_bytes': 4096,
                'package_number': 'EV-2026-004',
                'created_at': '2026-01-01T00:00:00Z', 'updated_at': '2026-01-01T00:00:00Z',
            })
        if "export_type = 'proof_bundle'" in n and 'created_at >' in n:
            return _Result(None)  # not superseded
        return _Result(None)

    def commit(self):
        pass


class _Req:
    headers = {'x-workspace-id': WS_ID}
    query_params: dict = {}
    client = None


def _bootstrap(monkeypatch, filters, content, *, can_export=True):
    conn = _Conn(filters=filters)

    @contextmanager
    def _fake_pg():
        yield conn

    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_a, **_k: {'id': USER_ID})
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda *_a, **_k: {'workspace_id': WS_ID, 'role': 'analyst'})
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: _FakeStorage(content))
    monkeypatch.setattr(pilot, 'log_audit', lambda *a, **k: None)
    monkeypatch.setattr(pilot, '_workspace_permission_granted', lambda *a, **k: can_export)
    return conn


# The EV-2026-004 persisted filters: completeness_score + files_hashed=0 recorded,
# but NO integrity_hash / manifest_sha256 (the manifest was never embedded).
_FILTERS_EV = {
    'incident_id': 'inc-1',
    'completeness_score': 90,
    'files_hashed': 0,
}


def _cat(completeness: dict | None, code: str) -> str:
    for c in (completeness or {}).get('categories', []) or []:
        if isinstance(c, dict) and c.get('code') == code:
            return str(c.get('status'))
    return 'absent'


def _checklist(completeness: dict | None, code: str):
    for c in (completeness or {}).get('checklist', []) or []:
        if isinstance(c, dict) and c.get('code') == code:
            return bool(c.get('present'))
    return None


# ── 9. Regression: the exact EV-2026-004 production shape is self-consistent ──

def test_ev2026004_detail_response_has_no_contradictions(monkeypatch):
    _bootstrap(monkeypatch, dict(_FILTERS_EV),
               _stored_bundle(include_manifest=False, completeness=_build_time_completeness()))
    export = pilot.get_export(PKG_ID, _Req())['export']

    # Canonical manifest state — no retrievable manifest.
    assert export['is_manifest_missing'] is True
    assert export['integrity_status'] == 'manifest_missing'
    assert export['hash_state'] == 'not_generated'
    assert export['files_hashed'] == 0
    assert export['files'] == []
    assert export['manifest_reference']['exists'] is False
    assert export['manifest_reference']['retrievable'] is False
    assert export['manifest_sha256'] is None

    # Allowed actions follow the canonical manifest state, not the stale snapshot.
    actions = export['allowed_actions']
    assert actions['view'] is True
    assert actions['download'] is True
    assert actions['verify'] is False
    assert actions['download_manifest'] is False
    assert actions['copy_hash'] is False
    # A recoverable manifest_missing package always exposes a recovery path.
    assert actions['generate_manifest'] is True or actions['regenerate_package'] is True

    # Completeness hash evidence is reconciled to the canonical facts — the stale
    # "present" snapshot must NOT survive.
    completeness = export['completeness']
    assert _cat(completeness, 'file_hashes') != 'present'
    assert _cat(completeness, 'manifest_hash') != 'present'
    assert _checklist(completeness, 'hashes_generated') is False
    assert _checklist(completeness, 'provenance') is False
    # The score drops once the false hash evidence is removed (not preserved for UI):
    # strictly below the stale build-time snapshot that claimed both hash categories.
    assert completeness['score'] < _build_time_completeness()['score']

    # The package artifact is a SEPARATE, retrievable reference — Download works even
    # though the manifest does not — so the two are never conflated on one key.
    artifact_ref = export['package_artifact_reference']
    assert artifact_ref['exists'] is True
    assert artifact_ref['retrievable'] is True
    assert artifact_ref['storage_key'] == OBJECT_KEY

    # Full contradiction sweep — every hash claim agrees with the manifest state.
    _assert_no_hash_contradictions(export)


def test_ev2026004_missing_codes_include_hash_categories(monkeypatch):
    # After reconciliation the agent findings / missing_codes reflect the real gap:
    # the hash categories are reported missing (they can then be recovered), never
    # silently reported present.
    _bootstrap(monkeypatch, dict(_FILTERS_EV),
               _stored_bundle(include_manifest=False, completeness=_build_time_completeness()))
    export = pilot.get_export(PKG_ID, _Req())['export']
    missing = set((export['completeness'] or {}).get('missing_codes') or [])
    assert 'file_hashes' in missing
    assert 'manifest_hash' in missing
    finding_codes = {f.get('code') for f in export['agent_findings'] if f.get('type') == 'missing_evidence'}
    assert {'file_hashes', 'manifest_hash'} <= finding_codes


def test_healthy_manifest_detail_preserves_completeness(monkeypatch):
    # Guard against over-correction: a package WITH a retrievable manifest keeps its
    # hash evidence present and its score unchanged (reconciliation is a no-op).
    completeness = _build_time_completeness()
    manifest, _ = build_evidence_manifest(
        export_id=PKG_ID, export_type='proof_bundle', workspace_id=WS_ID,
        generated_at='2026-01-01T00:00:00Z', generated_by_user_id=USER_ID,
        source_resource_type='incident', source_resource_id='inc-1',
        storage_backend='local', file_values=_PARTIAL_FILES,
    )
    filters = {
        'incident_id': 'inc-1', 'completeness_score': completeness['score'],
        'files_hashed': len(manifest['files']),
        'integrity_hash': manifest['manifest_sha256'],
        'manifest_sha256': manifest['manifest_sha256'],
    }
    _bootstrap(monkeypatch, filters,
               _stored_bundle(include_manifest=True, completeness=completeness))
    export = pilot.get_export(PKG_ID, _Req())['export']

    assert export['integrity_status'] == 'hash_generated'
    assert export['manifest_reference']['retrievable'] is True
    assert _cat(export['completeness'], 'file_hashes') == 'present'
    assert _cat(export['completeness'], 'manifest_hash') == 'present'
    assert _checklist(export['completeness'], 'hashes_generated') is True
    assert export['completeness']['score'] == completeness['score']
    _assert_no_hash_contradictions(export)


# ── 10. Invariants: the rules that make the contradiction impossible ──────────

def _ev_package() -> dict:
    # EV-2026-004 metadata: no recorded manifest SHA, no retrievable manifest.
    return {'export_type': 'proof_bundle', 'status': 'completed',
            'incident_id': 'inc-1', 'completeness_score': 90, 'files_hashed': 0}


def test_invariant_manifest_not_exists_forbids_manifest_hash_present():
    # manifest_reference.exists == false => manifest_hash cannot be present.
    ref = ec.resolve_manifest_reference(_ev_package())
    assert ref.exists is False
    reconciled = ec.reconcile_completeness_hash_evidence(
        _build_time_completeness(), files_hashed=0,
        manifest_retrievable=ref.retrievable, manifest_sha256=ref.sha256,
    )
    assert _cat(reconciled, 'manifest_hash') != 'present'


def test_invariant_manifest_not_retrievable_forbids_verify():
    # manifest_reference.retrievable == false => verify cannot be allowed.
    pkg = _ev_package()
    assert ec.resolve_manifest_reference(pkg).retrievable is False
    assert ec.get_package_allowed_actions(pkg, can_export=True)['verify'] is False


def test_invariant_no_manifest_sha_forbids_copy_hash():
    # manifest_sha256 == null => copy_hash cannot be allowed.
    pkg = _ev_package()
    assert ec._recorded_manifest_sha256(pkg) is None
    assert ec.get_package_allowed_actions(pkg, can_export=True)['copy_hash'] is False


def test_invariant_zero_files_hashed_forbids_file_hashes_present():
    # files_hashed == 0 => file_hashes cannot be present.
    reconciled = ec.reconcile_completeness_hash_evidence(
        _build_time_completeness(), files_hashed=0,
        manifest_retrievable=False, manifest_sha256=None,
    )
    assert _cat(reconciled, 'file_hashes') != 'present'
    assert _checklist(reconciled, 'hashes_generated') is False


def test_invariant_empty_files_forbids_file_hashes_present(monkeypatch):
    # files == [] (no manifest to enumerate) => file_hashes cannot be present.
    _bootstrap(monkeypatch, dict(_FILTERS_EV),
               _stored_bundle(include_manifest=False, completeness=_build_time_completeness()))
    export = pilot.get_export(PKG_ID, _Req())['export']
    assert export['files'] == []
    assert _cat(export['completeness'], 'file_hashes') != 'present'


def test_invariant_manifest_missing_exposes_a_recovery_path():
    # is_manifest_missing == true + can_export == true => a recovery path is exposed
    # (generate_manifest OR regenerate_package) — recovery is never fully blocked.
    pkg = _ev_package()
    state = ec.get_evidence_package_display_state(pkg)
    assert state['is_manifest_missing'] is True
    actions = ec.get_package_allowed_actions(pkg, can_export=True)
    assert actions['generate_manifest'] is True or actions['regenerate_package'] is True


def test_invariant_recovery_requires_permission():
    # Without evidence.export the recovery path is not offered (fail-closed).
    actions = ec.get_package_allowed_actions(_ev_package(), can_export=False)
    assert actions['generate_manifest'] is False
    assert actions['regenerate_package'] is False


def test_invariant_hash_generated_requires_real_retrievable_manifest():
    # Hash Generated => manifest exists AND retrievable AND manifest_sha256 exists.
    manifest, _ = build_evidence_manifest(
        export_id=PKG_ID, export_type='proof_bundle', workspace_id=WS_ID,
        generated_at='2026-01-01T00:00:00Z', generated_by_user_id=USER_ID,
        source_resource_type='incident', source_resource_id='inc-1',
        storage_backend='local', file_values=_PARTIAL_FILES,
    )
    healthy = {
        'export_type': 'proof_bundle', 'status': 'completed',
        'storage_object_key': OBJECT_KEY,
        'integrity_hash': manifest['manifest_sha256'],
        'manifest_sha256': manifest['manifest_sha256'],
        'manifest_file_count': len(manifest['files']),
    }
    state = ec.get_evidence_package_display_state(healthy)
    assert state['integrity_status'] == 'hash_generated'
    ref = ec.resolve_manifest_reference(healthy)
    assert ref.exists is True and ref.retrievable is True
    assert ec._recorded_manifest_sha256(healthy) == manifest['manifest_sha256']

    # Conversely, EV-2026-004 (no retrievable manifest) is NEVER hash_generated.
    assert ec.get_evidence_package_display_state(_ev_package())['integrity_status'] != 'hash_generated'


def _assert_no_hash_contradictions(export: dict) -> None:
    """Fail if any hash claim in the detail response contradicts the manifest state."""
    completeness = export.get('completeness') or {}
    ref = export['manifest_reference']
    files_hashed = export['files_hashed']
    problems: list[str] = []

    if files_hashed == 0 and _cat(completeness, 'file_hashes') == 'present':
        problems.append('files_hashed==0 but completeness file_hashes==present')
    if not ref['retrievable'] and _cat(completeness, 'manifest_hash') == 'present':
        problems.append('manifest not retrievable but completeness manifest_hash==present')
    if files_hashed == 0 and _checklist(completeness, 'hashes_generated'):
        problems.append('files_hashed==0 but checklist hashes_generated==true')
    if not ref['retrievable'] and export['allowed_actions']['verify']:
        problems.append('manifest not retrievable but verify allowed')
    if export['manifest_sha256'] is None and export['allowed_actions']['copy_hash']:
        problems.append('manifest_sha256 null but copy_hash allowed')
    if export['is_manifest_missing'] and export['can_export'] and not (
        export['allowed_actions']['generate_manifest'] or export['allowed_actions']['regenerate_package']
    ):
        problems.append('manifest_missing + can_export but no recovery path exposed')

    assert not problems, 'detail response contradictions: ' + '; '.join(problems)
