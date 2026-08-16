"""Screen 9 — a normally-created evidence package must be BORN with a manifest.

These lock in the fix for the EV-2026-005 → EV-2026-006 production failure: the
Manifest-Missing defect was traced past the recovery tools (Generate Manifest /
Regenerate Package) into the NORMAL package-generation pipeline itself. A freshly
generated proof bundle (including a superseding one) completed as "Generated" yet
had no retrievable manifest and could never be verified, because
``_generate_export_artifact`` swallowed any manifest-build failure and marked the
job ``completed`` anyway, and never confirmed the manifest actually persisted.

The tests exercise the real generation path (``create_proof_bundle_export`` /
``regenerate_evidence_package``) plus the ``get_export`` detail projection with the
project's fake-DB + fake-storage harness (no live DB, no network, no LLM):

* A normal package is born with a retrievable manifest, real per-file SHA-256s,
  and Verify Integrity / Download Manifest enabled — no Generate-Manifest recovery.
* Manifest generation is NOT gated on evidence completeness (a partial package is
  still hashed).
* If the manifest cannot be built/sealed, the package fails CLOSED (truthful
  ``failed`` status + safe diagnostic code) instead of silently completing as
  Manifest-Missing and forcing the Generate-Manifest → Regenerate loop.
* A read-back gate rejects a package whose persisted bytes do not actually carry
  the sealed manifest.
* Regeneration mints a NEW superseding package that is itself born with a manifest,
  while the original manifest-missing package is preserved untouched.
"""
from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from services.api.app import pilot
from services.api.app import evidence_signing


def _event_tokens(caplog) -> list[str]:
    """The leading structured-event token of every pilot log line, in order.

    Each generation log line is ``<event> key=value key=value ...``; the event
    name is the first whitespace-delimited token. Returned in emission order so a
    test can assert both presence and ordering of the package-generation lifecycle.
    """
    tokens: list[str] = []
    for record in caplog.records:
        if record.name != 'services.api.app.pilot':
            continue
        message = record.getMessage()
        if message:
            tokens.append(message.split(' ', 1)[0])
    return tokens


WS = 'ws-5'
USER = 'user-5'
INCIDENT = 'inc-5'


# ── Storage double (write + read, shared between generation and detail) ───────

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

    def get_object_size(self, *, object_key: str):
        return len(self.written.get(object_key, b'')) or None

    def stored_bundle(self, object_key: str) -> dict:
        data = json.loads(self.written[object_key])
        return data['rows'][0]


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


# ── Stateful export_jobs + incident-chain connection ──────────────────────────

class _BornConn:
    """A mini export_jobs DB that also serves the full proof-bundle incident chain.

    export_jobs rows are stored in ``self.rows`` and mutated by the generation
    UPDATEs (queued -> completed/failed, with the manifest-reference filters patch
    merged), so ``get_export`` sees exactly the row the generator just wrote.
    """

    def __init__(self, *, seed_rows: dict | None = None):
        self.executed: list[tuple[str, object]] = []
        self.committed = False
        self.rows: dict[str, dict] = dict(seed_rows or {})
        self.inserted_pkg_id: str | None = None

    def execute(self, stmt, params=None):
        n = ' '.join(str(stmt).split())
        self.executed.append((n, params))
        return self._handle(n, params)

    def commit(self):
        self.committed = True

    # -- helpers ------------------------------------------------------------
    def _full_row(self, pkg_id: str) -> dict | None:
        row = self.rows.get(pkg_id)
        return dict(row) if row is not None else None

    def _handle(self, n, params):
        # Transaction advisory lock (idempotency serialization).
        if 'pg_advisory_xact_lock' in n:
            return _Row(None)
        # Idempotency reuse lookup (fingerprint-scoped) — no reusable package.
        if 'evidence_source_fingerprint' in n and "(filters->>'response_action_id') IS NULL" in n:
            return _Row(None)
        # Supersession-target lookup (latest incident package).
        if "(filters->>'response_action_id') IS NULL" in n and 'ORDER BY created_at DESC LIMIT 1' in n:
            return _Row(None)
        # "Already superseded?" / detail "newer package exists?" probe.
        if 'created_at > %s' in n and 'LIMIT 1' in n:
            return _Row(None)

        # get_export / regenerate full-row read.
        if 'SELECT * FROM export_jobs WHERE id = %s AND workspace_id = %s' in n:
            return _Row(self._full_row(params[0]))
        # _generate_export_artifact narrow job fetch.
        if 'SELECT id, export_type, format, filters, requested_by_user_id FROM export_jobs' in n:
            row = self.rows.get(params[0], {})
            return _Row({
                'id': params[0],
                'export_type': row.get('export_type', 'proof_bundle'),
                'format': row.get('format', 'json'),
                'filters': dict(row.get('filters') or {'incident_id': INCIDENT, 'include_raw_events': True}),
                'requested_by_user_id': row.get('requested_by_user_id', USER),
            })

        # INSERT a fresh export_jobs row.
        if 'INSERT INTO export_jobs' in n:
            pkg_id = params[0]
            self.inserted_pkg_id = pkg_id
            self.rows[pkg_id] = {
                'id': pkg_id, 'workspace_id': params[1], 'requested_by_user_id': params[2],
                'export_type': 'proof_bundle', 'format': 'json',
                'filters': json.loads(params[3]) if params[3] else {},
                'status': 'queued', 'output_path': params[4],
                'storage_backend': params[5], 'storage_object_key': params[6],
                'error_message': None, 'size_bytes': None, 'package_number': None,
                'created_at': '2026-08-14T00:00:00Z', 'updated_at': '2026-08-14T00:00:00Z',
            }
            return _Row(None)

        # Terminal generation UPDATEs.
        if "UPDATE export_jobs SET status = 'completed'" in n:
            pkg_id = params[6]
            row = self.rows.setdefault(pkg_id, {'filters': {}})
            row['status'] = 'completed'
            row['error_message'] = None
            row['storage_backend'] = params[0]
            row['storage_object_key'] = params[1]
            row['size_bytes'] = params[4]
            merged = dict(row.get('filters') or {})
            merged.update(json.loads(params[5]) if params[5] else {})
            row['filters'] = merged
            return _Row(None)
        if "UPDATE export_jobs SET status = 'failed'" in n:
            pkg_id = params[1]
            row = self.rows.setdefault(pkg_id, {'filters': {}})
            row['status'] = 'failed'
            row['error_message'] = params[0]
            return _Row(None)
        # Regenerate-into-same-row filters repair (unused here, handled defensively).
        if 'UPDATE export_jobs SET filters = %s::jsonb' in n:
            return _Row(None)

        # package_number allocation (best-effort inside _generate).
        if 'SELECT status, package_number FROM export_jobs WHERE id = %s' in n:
            row = self.rows.get(params[0], {})
            return _Row({'status': row.get('status'), 'package_number': row.get('package_number')})
        if 'MAX(NULLIF(regexp_replace(package_number' in n:
            return _Row({'max_seq': 0})
        if 'UPDATE export_jobs SET package_number' in n:
            row = self.rows.get(params[1])
            if row is not None:
                row['package_number'] = params[0]
            return _Row(None)
        # create_proof_bundle_export final read.
        if 'SELECT status, error_message, package_number FROM export_jobs WHERE id = %s' in n:
            row = self.rows.get(params[0], {})
            return _Row({'status': row.get('status'), 'error_message': row.get('error_message'), 'package_number': row.get('package_number')})

        # ── Incident evidence chain (fingerprint + collector share these) ──
        if 'FROM incidents WHERE workspace_id = %s AND id = %s' in n:
            return _Row({
                'id': INCIDENT, 'workspace_id': WS, 'title': 'Repaired incident',
                'severity': 'high', 'status': 'contained', 'asset_id': 'asset-5',
                'summary': 'Repaired EV-2026-005-style snapshot',
                'linked_alert_ids': ['alert-5'], 'source_alert_id': 'alert-5',
                'created_at': '2026-08-01T00:00:00Z',
            })
        if 'FROM alerts a JOIN detection_metrics dm' in n:
            return _Row(rows=[{
                'id': 'alert-5', 'analysis_run_id': 'run-5', 'target_id': 'target-5',
                'title': 'Live anomaly', 'severity': 'high', 'status': 'open',
                'source': 'live', 'detection_id': 'det-5', 'detection_event_id': None,
                'summary': 'live', 'payload': {'source': 'live'}, 'created_at': '2026-08-01T00:01:00Z',
            }])
        if 'FROM alerts a WHERE a.workspace_id' in n:
            return _Row(rows=[])
        if 'FROM detection_metrics WHERE workspace_id = %s AND incident_id = %s' in n:
            return _Row(rows=[{
                'id': 'metric-5', 'alert_id': 'alert-5',
                'event_observed_at': '2026-08-01T00:00:00Z', 'detected_at': '2026-08-01T00:02:00Z',
                'mttd_seconds': 120, 'evidence_source': 'live',
                'evidence': {'tx_hash': '0xreal', 'block_number': 123, 'chain_id': 1},
            }])
        if 'FROM detection_metrics WHERE workspace_id = %s AND alert_id = ANY' in n:
            return _Row(rows=[])
        if 'FROM response_actions' in n and 'incident_id = %s' in n:
            return _Row(rows=[{
                'id': 'action-5', 'action_type': 'notify_team', 'status': 'executed',
                'mode': 'live', 'execution_metadata': None, 'executed_at': '2026-08-01T00:10:00Z',
                'rolled_back_at': None, 'created_at': '2026-08-01T00:09:00Z',
            }])
        if 'FROM detections' in n and 'linked_alert_id = ANY' in n:
            return _Row(rows=[{
                'id': 'det-5', 'detection_type': 'anomaly', 'severity': 'high', 'confidence': 0.95,
                'evidence_source': 'live', 'status': 'open', 'detected_at': '2026-08-01T00:01:30Z',
                'title': 'Live anomaly',
            }])
        if 'FROM detections' in n and 'id = %s::uuid' in n:
            return _Row(None)
        if 'FROM audit_logs' in n and 'row_hash' in n:
            return _Row(None)  # audit chain tip
        if 'FROM audit_logs' in n:
            return _Row(rows=[{
                'id': 'audit-5', 'action': 'export.generate', 'entity_type': 'export_job',
                'entity_id': INCIDENT, 'metadata': None, 'created_at': '2026-08-01T00:12:00Z',
            }])
        if 'FROM incident_timeline' in n:
            return _Row(rows=[{
                'id': 'tl-5', 'event_type': 'evidence_collected', 'message': 'collected',
                'actor_user_id': USER, 'metadata': None, 'created_at': '2026-08-01T00:03:00Z',
            }])

        raise AssertionError(f'unexpected query: {n!r}')


def _patch(monkeypatch, conn, storage) -> None:
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
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda *_a, **_k: {'workspace_id': WS, 'role': 'analyst'})
    monkeypatch.setattr(pilot, '_workspace_permission_granted', lambda *_a, **_k: True)
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: storage)


def _manifest_missing_bundle_bytes() -> bytes:
    """A stored artifact with evidence files but NO manifest.json (EV-2026-005)."""
    return json.dumps({'rows': [{
        'summary.json': {'export_id': 'ev-2026-005', 'incident_id': INCIDENT, 'export_status': 'complete'},
        'alerts.json': [{'id': 'alert-5', 'severity': 'high'}],
        'incidents.json': [{'id': INCIDENT, 'status': 'contained'}],
        'detections.json': [{'id': 'det-5'}],
        'response_actions.json': [{'id': 'action-5', 'status': 'executed'}],
        'audit_log.json': [{'id': 'audit-5'}],
        'evidence.json': [{'tx_hash': '0xreal'}],
        'detection_metrics.json': [{'id': 'metric-5'}],
    }]}).encode('utf-8')


# ── 10. A normal package is born with a retrievable manifest ──────────────────

def test_normal_creation_is_born_with_retrievable_manifest(monkeypatch):
    conn = _BornConn()
    storage = _FakeStorage()
    _patch(monkeypatch, conn, storage)

    created = pilot.create_proof_bundle_export({'incident_id': INCIDENT, 'include_raw_events': True}, _req())
    assert created['status'] == 'completed'
    assert created['created'] is True
    pkg_id = created['package_id']

    # The package artifact exists in storage and physically carries the manifest.
    object_key = f'{WS}/{pkg_id}.json'
    assert object_key in storage.written, 'package artifact must be persisted'
    stored = storage.stored_bundle(object_key)
    assert 'manifest.json' in stored and 'seal.json' in stored
    manifest = stored['manifest.json']
    assert manifest['files'] and all(f.get('sha256') for f in manifest['files'])

    # The detail projection resolves the manifest against the stored bytes.
    detail = pilot.get_export(pkg_id, _req())['export']
    assert detail['manifest_reference']['exists'] is True
    assert detail['manifest_reference']['retrievable'] is True
    assert detail['manifest_sha256'] == manifest['manifest_sha256']
    assert detail['manifest_sha256']
    assert detail['integrity_status'] == 'hash_generated'
    assert detail['hash_state'] == 'present'
    assert detail['is_manifest_missing'] is False
    assert detail['generation_status'] == 'generated'
    assert detail['verification_status'] == 'ready'

    # File hashes: non-empty list, every file carries a SHA-256, count > 0.
    assert detail['files'] and detail['files_hashed'] > 0
    assert all(f.get('sha256') for f in detail['files'])

    # Verify Integrity + Download Manifest are enabled with no recovery required.
    assert detail['allowed_actions']['verify'] is True
    assert detail['allowed_actions']['download_manifest'] is True
    assert detail['recovery_required'] is False


def test_partial_completeness_still_receives_a_manifest(monkeypatch):
    """Source-evidence completeness and cryptographic integrity are independent:
    a package below 100% still has finalized bytes and MUST be hashed."""
    class _PartialConn(_BornConn):
        def _handle(self, n, params):
            # No response actions and no audit events → partial/critical completeness.
            if 'FROM response_actions' in n and 'incident_id = %s' in n:
                return _Row(rows=[])
            if 'FROM audit_logs' in n and 'row_hash' not in n:
                return _Row(rows=[])
            return super()._handle(n, params)

    conn = _PartialConn()
    storage = _FakeStorage()
    _patch(monkeypatch, conn, storage)

    created = pilot.create_proof_bundle_export({'incident_id': INCIDENT}, _req())
    assert created['status'] == 'completed'
    detail = pilot.get_export(created['package_id'], _req())['export']

    # Even a non-complete package is born verifiable.
    assert detail['manifest_reference']['retrievable'] is True
    assert detail['files_hashed'] > 0
    assert detail['allowed_actions']['verify'] is True
    # ...and it is NOT silently reported as a fully-complete evidence bundle.
    assert detail['is_manifest_missing'] is False


# ── 9 & 12. Manifest-build failure fails CLOSED (no silent manifest-missing) ───

def test_manifest_build_failure_fails_closed(monkeypatch):
    conn = _BornConn()
    storage = _FakeStorage()
    _patch(monkeypatch, conn, storage)

    def _boom(_manifest):
        raise ValueError('signing backend exploded')

    monkeypatch.setattr(evidence_signing, 'seal_manifest', _boom)

    created = pilot.create_proof_bundle_export({'incident_id': INCIDENT}, _req())

    # Truthful failed status + the PRECISE failing stage (the seal/sign stage here),
    # never a single generic "manifest generation failed" — the row now names the
    # stage so a production failure is diagnosable from error_message alone.
    assert created['status'] == 'failed'
    assert created['error_message'] == 'manifest_signing_failed'
    assert created['download_link'] is None
    assert conn.rows[created['package_id']]['status'] == 'failed'
    # The manifest-less bundle is NOT persisted as a finished package.
    assert f"{WS}/{created['package_id']}.json" not in storage.written


def test_readback_gate_rejects_mismatched_manifest(monkeypatch):
    """If the recorded/sealed manifest hash does not match the manifest actually
    embedded in the persisted bytes (manifest 'uploaded but not persisted' /
    discarded), the read-back gate fails the job CLOSED instead of completing it as
    verifiable — and reports it as the precise ``manifest_hash_mismatch`` stage."""
    conn = _BornConn()
    storage = _FakeStorage()
    _patch(monkeypatch, conn, storage)

    real_signing_metadata = evidence_signing.signing_metadata

    def _wrong(manifest, seal):
        meta = dict(real_signing_metadata(manifest, seal))
        meta['manifest_sha256'] = '0' * 64  # does not match the embedded manifest
        return meta

    monkeypatch.setattr(evidence_signing, 'signing_metadata', _wrong)

    created = pilot.create_proof_bundle_export({'incident_id': INCIDENT}, _req())
    assert created['status'] == 'failed'
    assert created['error_message'] == 'manifest_hash_mismatch'


# ── 11. Regeneration mints a NEW superseding package born with a manifest ──────

def test_regeneration_supersedes_and_is_born_with_manifest(monkeypatch):
    old_id = 'ev-2026-005'
    old_row = {
        'id': old_id, 'workspace_id': WS, 'requested_by_user_id': USER,
        'export_type': 'proof_bundle', 'format': 'json',
        'filters': {
            'incident_id': INCIDENT, 'include_raw_events': True,
            'completeness_score': 82, 'files_hashed': 8, 'export_status': 'complete',
        },
        'status': 'completed', 'output_path': f'{WS}/{old_id}.json',
        'storage_backend': 's3', 'storage_object_key': f'{WS}/{old_id}.json',
        'error_message': None, 'size_bytes': 1024, 'package_number': 'EV-2026-005',
        'created_at': '2026-08-05T00:00:00Z', 'updated_at': '2026-08-05T00:00:00Z',
    }
    conn = _BornConn(seed_rows={old_id: old_row})
    storage = _FakeStorage()
    storage.written[f'{WS}/{old_id}.json'] = _manifest_missing_bundle_bytes()
    _patch(monkeypatch, conn, storage)

    # Sanity: the seeded package really is Manifest Missing before regeneration.
    before = pilot.get_export(old_id, _req())['export']
    assert before['is_manifest_missing'] is True
    assert before['allowed_actions']['verify'] is False

    result = pilot.regenerate_evidence_package(old_id, _req())

    # A NEW superseding package was created (never the same row).
    assert result['status'] == 'completed'
    assert result['created'] is True
    new_id = result['package_id']
    assert new_id != old_id
    assert result['superseded_package_id'] == old_id
    assert result['supersedes_package_id'] == old_id

    # The new package is born with a retrievable manifest + verification readiness.
    new_detail = pilot.get_export(new_id, _req())['export']
    assert new_detail['manifest_reference']['retrievable'] is True
    assert new_detail['manifest_sha256']
    assert new_detail['integrity_status'] == 'hash_generated'
    assert new_detail['files_hashed'] > 0
    assert new_detail['allowed_actions']['verify'] is True
    assert new_detail['allowed_actions']['download_manifest'] is True
    assert new_detail['is_manifest_missing'] is False
    assert new_detail['supersedes_package_id'] == old_id

    # The original manifest-missing package is preserved untouched (never rewritten).
    assert conn.rows[old_id]['status'] == 'completed'
    assert 'integrity_hash' not in conn.rows[old_id]['filters']
    assert storage.written[f'{WS}/{old_id}.json'] == _manifest_missing_bundle_bytes()


def test_regeneration_refuses_a_package_with_a_usable_manifest(monkeypatch):
    """Verified/usable packages stay immutable — regeneration is only for recovery."""
    conn = _BornConn()
    storage = _FakeStorage()
    _patch(monkeypatch, conn, storage)

    # Create a healthy package (born with a manifest), then try to regenerate it.
    created = pilot.create_proof_bundle_export({'incident_id': INCIDENT}, _req())
    with pytest.raises(HTTPException) as exc:
        pilot.regenerate_evidence_package(created['package_id'], _req())
    assert exc.value.status_code == 409
    assert exc.value.detail['error'] == 'PACKAGE_NOT_ELIGIBLE'


# ── b56fa931 (EV-2026-006) shape: superseding live-evidence bundle ────────────
# b56fa931 is the NEW superseding proof bundle minted by "Regenerate Package" for
# a manifest-missing predecessor, over live incident evidence. These lock in that
# running the ACTUAL production generator for that exact shape produces a package
# that is born verifiable, and emits the full structured lifecycle so the next
# production regeneration is diagnosable from logs alone (no more trace-only gap).

# The canonical package-generation lifecycle events _generate_export_artifact must
# emit, in order, for a proof bundle that is born with a manifest.
_EXPECTED_LIFECYCLE_EVENTS = [
    'evidence_package_generation_started',
    'evidence_manifest_generation_started',
    'evidence_files_finalized',
    'evidence_file_hashes_generated',
    'evidence_manifest_generated',
    'evidence_package_artifact_persisted',
    'evidence_manifest_persisted',
    'evidence_manifest_readback_verified',
    'evidence_package_generation_completed',
]


def _seeded_manifest_missing_conn_and_storage():
    old_id = 'ev-2026-005'
    old_row = {
        'id': old_id, 'workspace_id': WS, 'requested_by_user_id': USER,
        'export_type': 'proof_bundle', 'format': 'json',
        'filters': {
            'incident_id': INCIDENT, 'include_raw_events': True,
            'completeness_score': 82, 'files_hashed': 8, 'export_status': 'complete',
        },
        'status': 'completed', 'output_path': f'{WS}/{old_id}.json',
        'storage_backend': 's3', 'storage_object_key': f'{WS}/{old_id}.json',
        'error_message': None, 'size_bytes': 1024, 'package_number': 'EV-2026-005',
        'created_at': '2026-08-05T00:00:00Z', 'updated_at': '2026-08-05T00:00:00Z',
    }
    conn = _BornConn(seed_rows={old_id: old_row})
    storage = _FakeStorage()
    storage.written[f'{WS}/{old_id}.json'] = _manifest_missing_bundle_bytes()
    return old_id, conn, storage


def test_regenerated_b56fa931_shape_is_born_verifiable(monkeypatch):
    old_id, conn, storage = _seeded_manifest_missing_conn_and_storage()
    _patch(monkeypatch, conn, storage)

    # The predecessor is genuinely Manifest Missing (82% complete — a partial
    # package is still hashed; completeness never gates the manifest).
    before = pilot.get_export(old_id, _req())['export']
    assert before['is_manifest_missing'] is True

    result = pilot.regenerate_evidence_package(old_id, _req())
    assert result['status'] == 'completed'
    new_id = result['package_id']
    assert new_id != old_id

    # The package artifact physically exists in storage.
    new_key = f'{WS}/{new_id}.json'
    assert new_key in storage.written
    manifest = storage.stored_bundle(new_key)['manifest.json']

    detail = pilot.get_export(new_id, _req())['export']

    # files.length >= 1, files_hashed == files.length, every file has a sha256.
    assert len(detail['files']) >= 1
    assert detail['files_hashed'] == len(detail['files'])
    assert detail['files_hashed'] == len(manifest['files'])
    assert all(f.get('sha256') for f in detail['files'])
    # Section-5 single-file contract: each file carries logical_path/media_type/
    # size_bytes/sha256 (a one-file bundle would satisfy exactly this shape).
    for f in detail['files']:
        assert {'logical_path', 'media_type', 'size_bytes', 'sha256'} <= set(f)

    # manifest_reference.exists / retrievable and a real manifest_sha256.
    assert detail['manifest_reference']['exists'] is True
    assert detail['manifest_reference']['retrievable'] is True
    assert detail['manifest_sha256']
    assert detail['manifest_sha256'] == manifest['manifest_sha256']

    # Verify Integrity + Download Manifest enabled; no recovery required.
    assert detail['allowed_actions']['verify'] is True
    assert detail['allowed_actions']['download_manifest'] is True
    assert detail['is_manifest_missing'] is False
    assert detail['recovery_required'] is False

    # generation_status='generated' must NOT coexist with a missing manifest.
    assert detail['generation_status'] == 'generated'
    assert detail['verification_status'] == 'ready'


def test_regeneration_emits_full_structured_lifecycle(monkeypatch, caplog):
    """The trace-only production gap is closed: a real regeneration emits every
    package-generation lifecycle event, in order, so A/B/C/D is separable from logs."""
    old_id, conn, storage = _seeded_manifest_missing_conn_and_storage()
    _patch(monkeypatch, conn, storage)

    with caplog.at_level(logging.INFO, logger='services.api.app.pilot'):
        result = pilot.regenerate_evidence_package(old_id, _req())
    assert result['status'] == 'completed'

    tokens = _event_tokens(caplog)
    for event in _EXPECTED_LIFECYCLE_EVENTS:
        assert event in tokens, f'missing lifecycle event: {event}'

    # Ordering: start brackets everything; the manifest is generated, then the
    # artifact persisted, then the manifest read back, then completion.
    assert tokens.index('evidence_package_generation_started') < tokens.index('evidence_manifest_generated')
    assert tokens.index('evidence_manifest_generated') < tokens.index('evidence_manifest_readback_verified')
    assert tokens.index('evidence_manifest_readback_verified') < tokens.index('evidence_package_generation_completed')
    # A born-with-manifest package never emits a stage-classified failure event.
    assert 'evidence_package_generation_failed' not in tokens

    # The artifact-level completion line (carries manifest_present + files_hashed);
    # distinct from the endpoint-level request_id/job_id correlation line.
    completed = [
        m for m in caplog.messages
        if m.startswith('evidence_package_generation_completed') and 'manifest_present=' in m
    ]
    assert completed and 'status=completed' in completed[-1] and 'manifest_present=yes' in completed[-1]


def test_normal_creation_emits_started_and_completed_brackets(monkeypatch, caplog):
    """Every creation path (not just regenerate) brackets generation with the
    canonical start/complete lifecycle events carrying the package id + type."""
    conn = _BornConn()
    storage = _FakeStorage()
    _patch(monkeypatch, conn, storage)

    with caplog.at_level(logging.INFO, logger='services.api.app.pilot'):
        created = pilot.create_proof_bundle_export({'incident_id': INCIDENT}, _req())
    assert created['status'] == 'completed'
    pkg_id = created['package_id']

    started = [m for m in caplog.messages if m.startswith('evidence_package_generation_started')]
    completed = [m for m in caplog.messages if m.startswith('evidence_package_generation_completed')]
    # The artifact-level bracket carries package_id + export_type (distinct from the
    # endpoint-level request_id/job_id correlation line).
    assert any(f'package_id={pkg_id}' in m and 'export_type=proof_bundle' in m for m in started)
    assert any(f'package_id={pkg_id}' in m and 'status=completed' in m for m in completed)


def test_manifest_with_no_hashable_files_fails_closed(monkeypatch):
    """A manifest that would hash zero files must fail CLOSED — never a 'Generated'
    package with files_hashed=0 (the Manifest-Missing shape). Guards the single-JSON
    path: an empty files list is rejected before persistence."""
    conn = _BornConn()
    storage = _FakeStorage()
    _patch(monkeypatch, conn, storage)

    real_build = evidence_signing.build_evidence_manifest

    def _empty_files(**kwargs):
        manifest, file_bytes = real_build(**kwargs)
        manifest['files'] = []  # simulate a degenerate 'single JSON object, no files' manifest
        return manifest, file_bytes

    monkeypatch.setattr(evidence_signing, 'build_evidence_manifest', _empty_files)

    created = pilot.create_proof_bundle_export({'incident_id': INCIDENT}, _req())
    assert created['status'] == 'failed'
    # Zero hashable files is specifically an artifact-enumeration failure, not a
    # generic manifest failure.
    assert created['error_message'] == 'artifact_enumeration_failed'
    # No manifest-less bundle is persisted as a finished package.
    assert f"{WS}/{created['package_id']}.json" not in storage.written


# ── 13. Stored-bytes integrity: the manifest hashes the EXACT persisted bytes ──

def test_manifest_hashes_the_exact_persisted_bytes(monkeypatch):
    """Read the persisted package artifact back out of storage and independently
    recompute every SHA-256. Each manifest file entry must equal the SHA-256 of the
    exact stored logical-file bytes, and the manifest hash must recompute from the
    stored manifest body — proving hashes are taken over what is actually persisted,
    not a reconstructed/pretty-printed variant."""
    import hashlib as _hashlib
    from services.api.app.evidence_signing import canonical_json as _canon

    conn = _BornConn()
    storage = _FakeStorage()
    _patch(monkeypatch, conn, storage)

    created = pilot.create_proof_bundle_export({'incident_id': INCIDENT}, _req())
    assert created['status'] == 'completed'
    object_key = f"{WS}/{created['package_id']}.json"

    # Read the ACTUAL persisted object back out of storage (not an in-memory copy).
    stored_bundle = json.loads(storage.read_bytes(object_key=object_key))['rows'][0]
    manifest = stored_bundle['manifest.json']

    # A single-file proof bundle would satisfy exactly this contract too: len>=1.
    assert len(manifest['files']) >= 1
    for entry in manifest['files']:
        path = entry['path']
        assert path in stored_bundle, f'manifest lists {path} but it is not in the stored bytes'
        independent = _hashlib.sha256(_canon(stored_bundle[path])).hexdigest()
        assert independent == entry['sha256'], f'sha256 mismatch for {path}'
        assert entry['size_bytes'] == len(_canon(stored_bundle[path]))

    # The manifest hash recomputes over the canonical manifest body (its own hash
    # excluded) — i.e. sha256(persisted manifest bytes) == manifest_sha256.
    body = {k: v for k, v in manifest.items() if k != 'manifest_sha256'}
    assert _hashlib.sha256(_canon(body)).hexdigest() == manifest['manifest_sha256']

    # The detail projection's recorded manifest_sha256 matches the stored manifest.
    detail = pilot.get_export(created['package_id'], _req())['export']
    assert detail['manifest_sha256'] == manifest['manifest_sha256']
    assert detail['files'][0]['sha256'] == manifest['files'][0]['sha256']


# ── 14. Real storage read-back failure fails CLOSED ───────────────────────────

def test_storage_readback_returning_different_bytes_fails_closed(monkeypatch):
    """The read-back gate must READ THE OBJECT BACK OUT OF STORAGE, not re-parse the
    in-memory buffer. If storage returns bytes that differ from what was written
    (corruption / eventual-consistency / a discarded write), generation fails CLOSED
    with the precise ``manifest_storage_readback_failed`` code — never a 'Generated'
    package whose manifest cannot actually be re-read."""

    class _CorruptReadStorage(_FakeStorage):
        def read_bytes(self, *, object_key: str) -> bytes:
            # Write succeeds, but storage hands back different bytes on read-back.
            return super().read_bytes(object_key=object_key) + b' '

    conn = _BornConn()
    storage = _CorruptReadStorage()
    _patch(monkeypatch, conn, storage)

    created = pilot.create_proof_bundle_export({'incident_id': INCIDENT}, _req())
    assert created['status'] == 'failed'
    assert created['error_message'] == 'manifest_storage_readback_failed'
    assert created['download_link'] is None

    # Fail-closed detail: not generated, and Verify / Download Manifest disabled.
    detail = pilot.get_export(created['package_id'], _req())['export']
    assert detail['generation_status'] == 'failed'
    assert detail['allowed_actions']['verify'] is False
    assert detail['allowed_actions']['download_manifest'] is False


# ── 15. Storage WRITE failure fails CLOSED with the write stage code ──────────

def test_storage_write_failure_fails_closed(monkeypatch):
    """A manifest storage upload failure fails the job with a code that identifies
    the storage-WRITE stage, and never persists a successful generation status or a
    finished package object."""

    class _WriteFailsStorage(_FakeStorage):
        def write_bytes(self, *, object_key: str, content: bytes) -> str:
            raise OSError('simulated S3 upload failure')

    conn = _BornConn()
    storage = _WriteFailsStorage()
    _patch(monkeypatch, conn, storage)

    created = pilot.create_proof_bundle_export({'incident_id': INCIDENT}, _req())
    assert created['status'] == 'failed'
    assert created['error_message'] == 'manifest_storage_write_failed'
    # Nothing was persisted as a finished package.
    assert f"{WS}/{created['package_id']}.json" not in storage.written
    assert conn.rows[created['package_id']]['status'] == 'failed'
