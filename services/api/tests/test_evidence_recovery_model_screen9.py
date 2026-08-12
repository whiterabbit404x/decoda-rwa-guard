"""Screen 9 — EV-2026-004 manifest-recovery model (decoupled from completeness).

These lock in the fix for the LIVE production contradiction where GET
/api/exports/{EV-2026-004} reported ``is_manifest_missing == false`` and every
recovery action ``false`` while the agent finding said "Collect the missing
required evidence, then regenerate the package." The root cause was that
``is_manifest_missing`` was coupled to ``integrity_status == 'manifest_missing'``;
when the package's required evidence is ALSO incomplete the completeness-driven
integrity_status resolves to ``needs_evidence`` and shadowed the manifest-missing
fact, which in turn hid the recovery action.

The exact current production shape is:

  proof_bundle · completed · export_status=partial · package_status=degraded_diagnostic
  package artifact exists=true / retrievable=true
  manifest exists=false / retrievable=false · manifest_sha256=null
  files=[] · files_hashed=0 · verification=null · can_export=true · legacy=false

For this shape the response MUST report:

  * is_manifest_missing == true              (a fact, independent of integrity_status)
  * evidence completeness = Critical         (a SEPARATE axis)
  * manifest_status == 'missing'
  * verification_status == 'not_ready'
  * verify == false / download_manifest == false
  * EXACTLY ONE recovery outcome — generate_manifest, regenerate_package, or a
    non-empty recovery_blocked_reason. Never a silent dead end.

A degraded diagnostic package built from MISSING source evidence must NOT receive
an integrity manifest (sealing degraded data as verifiable evidence) and must NOT
be regenerated until the evidence is collected (regenerating only reproduces the
degraded package). So its single outcome is the structured evidence_required
blocker, and the agent finding says exactly that.
"""
from __future__ import annotations

import json
from contextlib import contextmanager

from services.api.app import pilot
from services.api.app import evidence_completeness as ec

WS_ID = 'ws-recovery-model-1'
USER_ID = 'user-recovery-model-1'
PKG_ID = 'pkg-recovery-model-1'
OBJECT_KEY = f'evidence/{WS_ID}/EV-2026-004.json'


def _core_missing_completeness() -> dict:
    """EV-2026-004 as it is STORED NOW: the build-time snapshot itself records the
    file_hashes / manifest_hash categories MISSING (files_hashed=0, no manifest),
    so the completeness-driven integrity_status resolves to 'needs_evidence' — the
    state that used to shadow the manifest-missing fact and hide recovery."""
    return ec.compute_evidence_completeness({
        'incident_status': 'open', 'evidence_source_type': 'missing',
        'has_incident': True, 'has_alert': False, 'has_detection': False,
        'has_telemetry': False, 'has_asset': False, 'has_audit_events': False,
        'has_investigation_timeline': False, 'has_chain_metadata': False,
        'has_manifest': False, 'files_hashed': 0,
        'response_action_count': 0, 'executed_action_count': 0,
        'rejected_action_count': 0, 'requires_approval': False,
        'approval_present': False, 'has_execution_result': False,
        'manifest_verified': None,
    })


def _ev2026004_package() -> dict:
    """The exact section-8 production fixture as a plain package dict."""
    return {
        'export_type': 'proof_bundle', 'status': 'completed',
        'incident_id': 'inc-1',
        'export_status': 'partial', 'package_status': 'degraded_diagnostic',
        'evidence_source_type': 'missing',
        'completeness_score': _core_missing_completeness()['score'],
        'completeness': _core_missing_completeness(),
        'files': [], 'files_hashed': 0,
        'verification': None, 'superseded': False,
        'storage_object_key': OBJECT_KEY, 'storage_available': True,
        'package_number': 'EV-2026-004',
    }


def _cat(completeness: dict | None, code: str) -> str:
    for c in (completeness or {}).get('categories', []) or []:
        if isinstance(c, dict) and c.get('code') == code:
            return str(c.get('status'))
    return 'absent'


def _exactly_one_recovery_outcome(*, generate: bool, regenerate: bool, blocked_reason) -> bool:
    outcomes = [bool(generate), bool(regenerate), bool(blocked_reason)]
    return sum(1 for o in outcomes if o) == 1


# ── 1. is_manifest_missing is a FACT, decoupled from integrity_status ─────────

def test_is_manifest_missing_true_even_when_integrity_is_needs_evidence():
    state = ec.get_evidence_package_display_state(_ev2026004_package())
    # Completeness pushes integrity_status to needs_evidence (required evidence is
    # incomplete) — this must NOT hide the manifest-missing fact.
    assert state['integrity_status'] == 'needs_evidence'
    assert state['is_manifest_missing'] is True
    # Manifest presence and evidence completeness are separate axes.
    assert state['manifest_status'] == 'missing'
    assert state['evidence_completeness_status'] == 'critical'


# ── 2. Four canonical status axes for the exact fixture ───────────────────────

def test_canonical_status_axes():
    state = ec.get_evidence_package_display_state(_ev2026004_package())
    assert state['generation_status'] == 'generated_partial'
    assert state['evidence_completeness_status'] == 'critical'
    assert state['manifest_status'] == 'missing'
    assert state['verification_status'] == 'not_ready'
    assert state['files_hashed'] == 0
    assert state['has_hashes'] is False
    assert state['is_evidence_artifact'] is False


# ── 3. Hash evidence is reported MISSING (never silently present) ─────────────

def test_hash_categories_missing():
    completeness = _core_missing_completeness()
    assert _cat(completeness, 'file_hashes') == 'missing'
    assert _cat(completeness, 'manifest_hash') == 'missing'


# ── 4. Verify / Download Manifest are impossible without a manifest ───────────

def test_verify_and_download_manifest_disabled():
    actions = ec.get_package_allowed_actions(_ev2026004_package(), can_export=True)
    assert actions['verify'] is False
    assert actions['download_manifest'] is False
    assert actions['copy_hash'] is False


# ── 5. EXACTLY ONE recovery outcome — the evidence_required blocker ───────────

def test_exactly_one_recovery_outcome_is_evidence_required():
    pkg = _ev2026004_package()
    actions = ec.get_package_allowed_actions(pkg, can_export=True)
    recovery = ec.get_package_recovery_model(pkg, can_export=True)

    # A degraded diagnostic built from MISSING evidence: sealing a manifest would
    # present degraded data as evidence, and regenerating now only reproduces the
    # degraded package — so BOTH actions are withheld with a structured reason.
    assert actions['generate_manifest'] is False
    assert actions['regenerate_package'] is False
    assert recovery['recovery_state'] == 'evidence_required'
    assert recovery['recovery_blocked_reason']
    assert 'evidence' in recovery['recovery_blocked_reason'].lower()

    # Never a silent dead end — and never more than one outcome.
    assert _exactly_one_recovery_outcome(
        generate=actions['generate_manifest'],
        regenerate=actions['regenerate_package'],
        blocked_reason=recovery['recovery_blocked_reason'],
    )


# ── 6. Contrast: a NON-degraded manifest-missing package offers generation ────

def test_non_degraded_manifest_missing_offers_generate_manifest():
    # Same manifest-missing fact, but the source evidence is present (live) and the
    # package is not a degraded diagnostic — so the existing artifact may safely be
    # sealed in place. Recovery is offered, never blocked.
    pkg = {
        'export_type': 'proof_bundle', 'status': 'completed', 'incident_id': 'inc-1',
        'evidence_source_type': 'live', 'export_status': 'partial',
        'completeness_score': 40, 'files_hashed': 8,
        'storage_object_key': OBJECT_KEY, 'storage_available': True,
    }
    recovery = ec.get_package_recovery_model(pkg, can_export=True)
    actions = ec.get_package_allowed_actions(pkg, can_export=True)
    assert ec.get_evidence_package_display_state(pkg)['is_manifest_missing'] is True
    assert recovery['recovery_state'] == 'generate_manifest'
    assert actions['generate_manifest'] is True
    # A healthy artifact offers the in-place manifest (primary) AND regeneration
    # (fallback) — and never a blocker.
    assert actions['regenerate_package'] is True
    assert recovery['recovery_blocked_reason'] is None


# ── 7. Permission denied still exposes a structured reason (no dead end) ──────

def test_permission_denied_has_blocked_reason():
    pkg = _ev2026004_package()
    recovery = ec.get_package_recovery_model(pkg, can_export=False)
    actions = ec.get_package_allowed_actions(pkg, can_export=False)
    assert actions['generate_manifest'] is False
    assert actions['regenerate_package'] is False
    assert recovery['recovery_state'] == 'permission_required'
    assert recovery['recovery_blocked_reason']


# ── End-to-end: the LIVE detail response through pilot.get_export ─────────────

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


def _degraded_bundle_bytes() -> bytes:
    """The EV-2026-004 stored artifact: the package object is retrievable, but it
    contains NO manifest, and its summary carries the degraded_diagnostic status +
    the core-missing completeness snapshot."""
    bundle = {
        'summary.json': {
            'export_id': PKG_ID, 'incident_id': 'inc-1',
            'package_status': 'degraded_diagnostic', 'export_status': 'partial',
            'evidence_source_type': 'missing',
            'completeness': _core_missing_completeness(),
        },
        'alerts.json': [], 'incidents.json': [{'id': 'inc-1', 'status': 'open'}],
        'detections.json': [], 'response_actions.json': [], 'audit_log.json': [],
        'evidence.json': [], 'detection_metrics.json': [],
    }
    return json.dumps({'rows': [bundle]}).encode('utf-8')


_FILTERS_EV = {
    'incident_id': 'inc-1',
    'completeness_score': _core_missing_completeness()['score'],
    'files_hashed': 0,
    'evidence_source_type': 'missing',
    'export_status': 'partial',
}


def _bootstrap(monkeypatch, *, can_export=True):
    conn = _Conn(filters=dict(_FILTERS_EV))

    @contextmanager
    def _fake_pg():
        yield conn

    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_a, **_k: {'id': USER_ID})
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda *_a, **_k: {'workspace_id': WS_ID, 'role': 'analyst'})
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: _FakeStorage(_degraded_bundle_bytes()))
    monkeypatch.setattr(pilot, 'log_audit', lambda *a, **k: None)
    monkeypatch.setattr(pilot, '_workspace_permission_granted', lambda *a, **k: can_export)
    return conn


def test_detail_endpoint_surfaces_manifest_missing_and_recovery(monkeypatch):
    _bootstrap(monkeypatch)
    export = pilot.get_export(PKG_ID, _Req())['export']

    # Manifest state: a fact, even though integrity_status is needs_evidence.
    assert export['is_manifest_missing'] is True
    assert export['integrity_status'] == 'needs_evidence'
    assert export['manifest_status'] == 'missing'
    assert export['generation_status'] == 'generated_partial'
    assert export['verification_status'] == 'not_ready'
    assert export['evidence_completeness_status'] == 'critical'
    assert export['manifest_reference']['exists'] is False
    assert export['manifest_reference']['retrievable'] is False
    assert export['manifest_sha256'] is None
    assert export['files'] == []
    assert export['files_hashed'] == 0

    # The package artifact is a SEPARATE, retrievable reference — Download works.
    assert export['package_artifact_reference']['exists'] is True
    assert export['package_artifact_reference']['retrievable'] is True

    # Actions follow the canonical manifest state.
    actions = export['allowed_actions']
    assert actions['view'] is True
    assert actions['download'] is True
    assert actions['verify'] is False
    assert actions['download_manifest'] is False
    assert actions['copy_hash'] is False

    # EXACTLY ONE recovery outcome — the structured evidence_required blocker.
    assert actions['generate_manifest'] is False
    assert actions['regenerate_package'] is False
    assert export['recovery_state'] == 'evidence_required'
    assert export['recovery_blocked_reason']
    assert _exactly_one_recovery_outcome(
        generate=actions['generate_manifest'],
        regenerate=actions['regenerate_package'],
        blocked_reason=export['recovery_blocked_reason'],
    )

    # Completeness hash evidence is reconciled to the canonical facts: the stale
    # build-time "present" snapshot must NOT survive — both hash categories read
    # 'missing' (section 8), and the missing_codes reflect the real gap.
    assert _cat(export['completeness'], 'file_hashes') == 'missing'
    assert _cat(export['completeness'], 'manifest_hash') == 'missing'
    missing_codes = set((export['completeness'] or {}).get('missing_codes') or [])
    assert {'file_hashes', 'manifest_hash'} <= missing_codes

    # The agent finding matches the single recovery outcome (not a dead end).
    recommended = [f['message'] for f in export['agent_findings'] if f.get('type') == 'recommended_action']
    assert any('evidence' in m.lower() for m in recommended)


def test_detail_endpoint_no_export_permission_is_not_a_dead_end(monkeypatch):
    _bootstrap(monkeypatch, can_export=False)
    export = pilot.get_export(PKG_ID, _Req())['export']
    assert export['is_manifest_missing'] is True
    assert export['allowed_actions']['generate_manifest'] is False
    assert export['allowed_actions']['regenerate_package'] is False
    # Still legible: a structured reason explains why, so the UI is never blank.
    assert export['recovery_state'] == 'permission_required'
    assert export['recovery_blocked_reason']
