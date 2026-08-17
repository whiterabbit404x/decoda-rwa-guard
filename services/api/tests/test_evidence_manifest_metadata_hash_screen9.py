"""Screen 9 — the manifest-METADATA hashing gap that PR #1388 left open.

Production retest of incident ``3c42654c-…`` (package ``64e9aaa3-…``) still failed
AFTER PR #1388 was deployed (merge commit ``3176311d…``): all nine evidence
sections were collected and finalized, then generation failed at
``stage=artifact_hash_generation`` with ``error_class=TypeError
safe_reason=artifact_hash_generation_failed``.

Root cause (distinct from what PR #1388 fixed):

  * PR #1388 gave the per-FILE evidence values a deterministic, fail-closed
    JSON-safe contract (``json_safe_for_hash``, applied by ``_file_sha256``), so a
    psycopg-native ``uuid.UUID`` / ``datetime`` / ``Decimal`` / ``bytes`` inside a
    file value no longer raised ``TypeError``.
  * But the manifest BODY itself was still hashed by a RAW ``json.dumps``:
    ``build_evidence_manifest`` computed
    ``manifest['manifest_sha256'] = sha256(canonical_json(manifest))`` and
    ``canonical_json`` did ``json.dumps`` with NO normalization of its own.
  * The manifest metadata carries ``workspace_id`` as a **raw ``uuid.UUID``** —
    ``resolve_workspace`` returns ``wm.workspace_id`` (a UUID column) straight from
    psycopg with no ``str()`` — so ``json.dumps`` raised
    ``TypeError: Object of type UUID is not JSON serializable`` the instant it
    reached the manifest hash, AFTER every file value had already been finalized.

The fix makes ``canonical_json`` THE single authoritative serializer: it runs
``json_safe_for_hash`` first, so the ENTIRE signed/hashable document — file values,
manifest metadata, the HMAC-seal input and the audit-chain payload — is covered by
ONE contract. ``json_safe_for_hash`` is the identity on already-JSON-native input,
so every previously-stored manifest re-serializes byte-for-byte identically and
still verifies.

These tests reproduce the EXACT production shape (nine JSON-native file sections +
a ``uuid.UUID`` in the manifest metadata) and prove: files hash, metadata is
serializable and deterministic, manifest SHA + HMAC seal complete, the persisted
representation verifies, JSON-native manifests stay byte-for-byte compatible, and
an unsupported metadata value fails CLOSED with a safe structural path/type.
"""
from __future__ import annotations

import datetime
import decimal
import hashlib
import json
import logging
import uuid
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from services.api.app import pilot
from services.api.app import evidence_signing as _es


# The production workspace + incident + package ids from the retest, as their real
# runtime types: workspace_id is a psycopg-native uuid.UUID in the manifest metadata.
WORKSPACE_UUID = uuid.UUID('1155f479-3e5b-4d90-be6c-fd6c1d6b957d')
INCIDENT_ID = '3c42654c-8504-4743-b548-253e0503326e'
FAILED_PACKAGE_ID = '64e9aaa3-8b57-4a19-bea1-d536d55818d9'


def _nine_json_native_sections() -> dict[str, object]:
    """Nine already-JSON-sanitized evidence sections (the shape pilot hands the
    manifest builder after ``rows = _json_safe_value(rows)``)."""
    return {
        'incident.json': {'id': INCIDENT_ID, 'severity': 'high', 'status': 'contained'},
        'timeline.json': [{'event_type': 'evidence_collected', 'at': '2026-08-01T00:03:00+00:00'}],
        'linked_alerts.json': [{'id': 'a1e57000-0000-4000-8000-000000000009', 'severity': 'high'}],
        'detections.json': [{'id': 'det-1', 'confidence': '0.95'}],
        'detection_metrics.json': [{'mttd_seconds': 120, 'evidence_source': 'live'}],
        'response_actions.json': [{'id': 'action-1', 'status': 'executed'}],
        'audit_trail.json': [{'action': 'export.generate'}],
        'summary.json': {'files': 8, 'evidence_origin': 'live'},
        'completeness.json': {'score': '1.0', 'missing': []},
    }


def _build_production_manifest():
    """Build a manifest with the EXACT production metadata types: workspace_id is a
    raw ``uuid.UUID`` (as ``resolve_workspace`` returns it)."""
    return _es.build_evidence_manifest(
        export_id=str(uuid.uuid4()),
        export_type='proof_bundle',
        workspace_id=WORKSPACE_UUID,               # <-- psycopg-native uuid.UUID (production)
        generated_at='2026-08-17T00:00:00+00:00',
        generated_by_user_id=str(uuid.uuid4()),
        source_resource_type='incident',
        source_resource_id=INCIDENT_ID,
        storage_backend='s3',
        file_values=_nine_json_native_sections(),
        previous_audit_anchor_hash=None,
    )


# ── 1–3. Files hash, metadata is serializable + deterministic, manifest SHA done ──

def test_all_nine_file_values_hash_successfully():
    """(#1) Every one of the nine finalized file values produces canonical bytes and
    a real SHA-256 — the collection→finalize→hash stage the production trace reached
    before the TypeError."""
    sections = _nine_json_native_sections()
    assert len(sections) == 9
    for path, value in sorted(sections.items()):
        b, sha = _es._file_sha256(value, logical_path=path)
        assert isinstance(b, bytes) and b
        assert len(sha) == 64 and int(sha, 16) >= 0


def test_manifest_metadata_uuid_is_serializable_and_manifest_sha_completes():
    """(#2, #3) Manifest metadata carrying a raw ``uuid.UUID`` workspace_id serializes
    and the manifest SHA-256 completes — the exact step that raised the production
    TypeError now succeeds."""
    manifest, file_bytes = _build_production_manifest()
    assert len(manifest['files']) == 9
    assert all(f.get('sha256') for f in manifest['files'])
    assert manifest['manifest_sha256'] and len(manifest['manifest_sha256']) == 64
    # The returned manifest IS the canonical JSON-safe document (single contract):
    # the UUID metadata is normalized to its string form so the SAME object hashes,
    # embeds, persists and verifies — never a raw UUID that would fail bundle upload.
    assert manifest['workspace_id'] == str(WORKSPACE_UUID)
    assert str(WORKSPACE_UUID) in _es.canonical_json(manifest).decode('ascii')
    assert len(file_bytes) == 9


def test_manifest_hash_over_uuid_metadata_is_deterministic():
    """(#2) Identical inputs — including the ``uuid.UUID`` metadata — produce an
    identical manifest hash (no per-run nondeterminism from the normalization)."""
    a, _ = _build_production_manifest()
    b, _ = _build_production_manifest()
    # export_id/generated_by differ per call; compare a metadata-stable projection.
    def _stable(m):
        body = {k: v for k, v in m.items() if k not in {'export_id', 'generated_by_user_id', 'manifest_sha256'}}
        return _es.canonical_json(body)
    assert _stable(a) == _stable(b)


def test_recovery_manifest_metadata_datetime_and_decimal_are_serializable():
    """(#2) The enriched recovery manifest carries genuine structural metadata
    (``Decimal`` completeness score, ``datetime`` in the evidence window); these are
    normalized deterministically instead of raising a raw TypeError."""
    manifest, _ = _es.build_recovery_manifest(
        export_id=str(uuid.uuid4()),
        export_type='proof_bundle',
        workspace_id=WORKSPACE_UUID,
        generated_at='2026-08-17T00:00:00+00:00',
        generated_by_user_id=None,
        incident_id=INCIDENT_ID,
        storage_backend='s3',
        file_values=_nine_json_native_sections(),
        completeness_score=decimal.Decimal('0.875'),
        evidence_window={
            'from': datetime.datetime(2026, 8, 1, tzinfo=datetime.timezone.utc),
            'to': datetime.datetime(2026, 8, 17, tzinfo=datetime.timezone.utc),
        },
    )
    assert manifest['manifest_sha256']
    # Returned in canonical JSON-safe form: the Decimal is normalized to its string.
    assert manifest['completeness_score'] == '0.875'
    # Deterministic and re-hashable over the persisted (JSON round-tripped) body.
    persisted_body = json.loads(json.dumps(_es.json_safe_for_hash(
        {k: v for k, v in manifest.items() if k != 'manifest_sha256'})))
    assert hashlib.sha256(_es.canonical_json(persisted_body)).hexdigest() == manifest['manifest_sha256']


# ── 4–5. HMAC seal completes; persisted representation verifies ────────────────

def test_hmac_seal_completes_over_uuid_metadata_manifest():
    """(#4) The HMAC seal is computed over the canonical manifest (which includes the
    UUID metadata) without error."""
    manifest, _ = _build_production_manifest()
    seal = _es.seal_manifest(manifest)
    assert seal['signature_algorithm'] == 'HMAC-SHA256'
    assert seal['signature'] and len(seal['signature']) == 64
    assert seal['signed_manifest_sha256'] == manifest['manifest_sha256']


def test_persisted_manifest_verifies_with_the_same_representation():
    """(#5) The persisted bundle (file values JSON round-tripped, as storage holds
    them) verifies against the manifest + seal: per-file SHA-256s match and the
    canonical manifest hash re-computes — hashed == persisted == verified."""
    manifest, _ = _build_production_manifest()
    seal = _es.seal_manifest(manifest)
    persisted = json.loads(json.dumps(
        {k: _es.json_safe_for_hash(v) for k, v in _nine_json_native_sections().items()}))
    result = _es.verify_bundle(persisted, manifest, seal, signing_secret=_es._DEV_FALLBACK_SECRET)
    assert result['valid'] is True, result['errors']
    # Independent recompute of the manifest hash over the read-back body matches.
    body = {k: v for k, v in manifest.items() if k != 'manifest_sha256'}
    assert hashlib.sha256(_es.canonical_json(body)).hexdigest() == manifest['manifest_sha256']


# ── 6. Backwards compatibility: JSON-native manifests are byte-for-byte identical ──

def test_existing_json_native_manifest_is_byte_for_byte_identical_to_raw_dumps():
    """(#6) For a manifest whose ids are already strings (what a stored manifest read
    back from storage always is), the new normalizing ``canonical_json`` produces
    bytes IDENTICAL to the old raw ``json.dumps`` — existing SHA-256s / HMAC seals
    are unchanged and every previously-stored manifest still verifies."""
    native_manifest = {
        'manifest_version': '1.0',
        'export_id': 'pkg-abc',
        'export_type': 'proof_bundle',
        'workspace_id': str(WORKSPACE_UUID),       # a stored manifest holds the STRING form
        'generated_at': '2026-08-17T00:00:00+00:00',
        'generated_by_user_id': None,
        'source_resource_type': 'incident',
        'source_resource_id': INCIDENT_ID,
        'storage_backend': 's3',
        'files': [{'path': 'a.json', 'sha256': 'x' * 64, 'size_bytes': 5}],
    }
    raw = json.dumps(native_manifest, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('utf-8')
    assert _es.canonical_json(native_manifest) == raw
    # And a JSON-native manifest's recorded hash still recomputes/verifies unchanged.
    native_manifest['manifest_sha256'] = hashlib.sha256(raw).hexdigest()
    recomputed = hashlib.sha256(_es.canonical_json(
        {k: v for k, v in native_manifest.items() if k != 'manifest_sha256'})).hexdigest()
    assert recomputed == native_manifest['manifest_sha256']


def test_canonical_json_serializes_metadata_types_a_raw_json_dumps_rejects():
    """(#2/#6 pin) A manifest dict literally containing a ``uuid.UUID`` is REJECTED by
    a raw ``json.dumps`` (the production failure) but serialized deterministically by
    ``canonical_json`` — proving the fix is in the shared serializer, not a per-field
    ``str()`` patch."""
    manifest_body = {'workspace_id': WORKSPACE_UUID, 'generated_at': '2026-08-17T00:00:00+00:00'}
    with pytest.raises(TypeError):
        json.dumps(manifest_body)  # the exact raw-dumps failure production hit
    b = _es.canonical_json(manifest_body)
    assert json.loads(b)['workspace_id'] == str(WORKSPACE_UUID)


# ── 7. Fail-closed: unsupported manifest metadata → safe structural diagnostic ────

class _WalletKey:
    """Sensitive material — must never be stringified into a hash or a diagnostic."""

    def __init__(self, secret: str) -> None:
        self.secret = secret

    def __str__(self) -> str:  # would leak if the normalizer fell back to str()
        return f'PRIVATE:{self.secret}'


def test_unsupported_manifest_metadata_fails_closed_with_safe_path_and_type():
    """(#7) A manifest-metadata value with no deterministic JSON-safe encoding fails
    CLOSED with an ``EvidenceSerializationError`` naming a safe structural path/type
    (annotated against the manifest) — never a raw TypeError, never the value."""
    with pytest.raises(_es.EvidenceSerializationError) as exc_info:
        _es.build_recovery_manifest(
            export_id='pkg', export_type='proof_bundle', workspace_id='ws',
            generated_at='2026-08-17T00:00:00+00:00', generated_by_user_id=None,
            incident_id=INCIDENT_ID, storage_backend='s3',
            file_values={'summary.json': {'ok': 1}},
            evidence_window={'tags': {_WalletKey('super-secret-material')}},  # a set of an arbitrary obj
        )
    err = exc_info.value
    assert err.logical_path == '<recovery-manifest>'
    assert err.nested_path == '$.evidence_window.tags'
    assert err.nested_type == 'set'
    assert 'super-secret-material' not in str(err)
    assert 'super-secret-material' not in repr(err.__dict__)


# ── Integration: the FULL production path with a uuid.UUID workspace_id ────────
# Reproduces the exact production request end to end. Before the fix this completed
# as 'failed' (error_code=artifact_hash_generation_failed) with no manifest; after
# the fix the package is born verifiable.

USER_ID = 'user-9'


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


def _req() -> SimpleNamespace:
    return SimpleNamespace(headers={'x-workspace-id': str(WORKSPACE_UUID)}, query_params=_EmptyQueryParams())


class _UuidWorkspaceIncidentConn:
    """Minimal incident chain for create_proof_bundle_export/_generate_export_artifact
    that returns a completed proof bundle. The workspace_id flows through as a real
    ``uuid.UUID`` (as production's resolve_workspace returns it), so the manifest
    metadata carries the exact type that broke production."""

    def __init__(self):
        self.executed: list[tuple[str, object]] = []
        self.committed = False
        self.inserted_pkg_id: str | None = None
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
        if 'evidence_source_fingerprint' in n and "(filters->>'response_action_id') IS NULL" in n:
            return _Row(None)
        if "(filters->>'response_action_id') IS NULL" in n and 'ORDER BY created_at DESC LIMIT 1' in n:
            return _Row(None)
        if 'INSERT INTO export_jobs' in n:
            self.inserted_pkg_id = params[0]
            self.status = 'queued'
            return _Row(None)
        if 'SELECT id, export_type, format, filters, requested_by_user_id FROM export_jobs' in n:
            return _Row({
                'id': params[0], 'export_type': 'proof_bundle', 'format': 'json',
                'filters': {'incident_id': INCIDENT_ID, 'include_raw_events': True},
                'requested_by_user_id': USER_ID,
            })
        if 'FROM incidents WHERE workspace_id = %s AND id = %s' in n:
            return _Row({
                'id': uuid.UUID('3c426540-0000-4000-8000-000000000009'), 'workspace_id': WORKSPACE_UUID,
                'title': 'Structural incident', 'severity': 'high', 'status': 'contained',
                'asset_id': uuid.uuid4(), 'summary': 'evidence', 'linked_alert_ids': [uuid.UUID('a1e57000-0000-4000-8000-000000000009')],
                'source_alert_id': uuid.UUID('a1e57000-0000-4000-8000-000000000009'),
                'created_at': datetime.datetime(2026, 8, 1, tzinfo=datetime.timezone.utc),
            })
        if 'FROM alerts a JOIN detection_metrics dm' in n:
            return _Row(rows=[{
                'id': uuid.UUID('a1e57000-0000-4000-8000-000000000009'), 'analysis_run_id': uuid.uuid4(),
                'target_id': uuid.uuid4(), 'title': 'Live anomaly', 'severity': 'high', 'status': 'open',
                'source': 'live', 'detection_id': uuid.uuid4(), 'detection_event_id': None, 'summary': 'live',
                'payload': {'confidence': decimal.Decimal('0.95')},
                'created_at': datetime.datetime(2026, 8, 1, 0, 1, tzinfo=datetime.timezone.utc),
            }])
        if 'FROM alerts a WHERE a.workspace_id' in n:
            return _Row(rows=[])
        if 'FROM detection_metrics WHERE workspace_id = %s AND incident_id = %s' in n:
            return _Row(rows=[{
                'id': uuid.uuid4(), 'alert_id': uuid.UUID('a1e57000-0000-4000-8000-000000000009'),
                'event_observed_at': datetime.datetime(2026, 8, 1, tzinfo=datetime.timezone.utc),
                'detected_at': datetime.datetime(2026, 8, 1, 0, 2, tzinfo=datetime.timezone.utc),
                'mttd_seconds': 120, 'evidence_source': 'live',
                'evidence': {'tx_hash': '0xreal', 'amount': decimal.Decimal('42.5'), 'raw_log': b'\x00\x01', 'chain_id': 1},
            }])
        if 'FROM detection_metrics WHERE workspace_id = %s AND alert_id = ANY' in n:
            return _Row(rows=[])
        if 'FROM response_actions' in n and 'incident_id = %s' in n:
            return _Row(rows=[{
                'id': uuid.uuid4(), 'action_type': 'notify_team', 'status': 'executed', 'mode': 'live',
                'execution_metadata': {'latency': decimal.Decimal('0.42')},
                'executed_at': datetime.datetime(2026, 8, 1, 0, 10, tzinfo=datetime.timezone.utc),
                'rolled_back_at': None, 'created_at': datetime.datetime(2026, 8, 1, 0, 9, tzinfo=datetime.timezone.utc),
            }])
        if 'FROM detections' in n and 'linked_alert_id = ANY' in n:
            return _Row(rows=[{
                'id': uuid.uuid4(), 'detection_type': 'anomaly', 'severity': 'high',
                'confidence': decimal.Decimal('0.95'), 'evidence_source': 'live', 'status': 'open',
                'detected_at': datetime.datetime(2026, 8, 1, 0, 1, 30, tzinfo=datetime.timezone.utc), 'title': 'Anomaly',
            }])
        if 'FROM detections' in n and 'id = %s::uuid' in n:
            return _Row(None)
        if 'FROM audit_logs' in n and 'row_hash' in n:
            return _Row(None)
        if 'FROM audit_logs' in n:
            return _Row(rows=[{
                'id': uuid.uuid4(), 'action': 'export.generate', 'entity_type': 'export_job',
                'entity_id': INCIDENT_ID, 'metadata': {'note': 'collected'},
                'created_at': datetime.datetime(2026, 8, 1, 0, 12, tzinfo=datetime.timezone.utc),
            }])
        if 'FROM incident_timeline' in n:
            return _Row(rows=[{
                'id': uuid.uuid4(), 'event_type': 'evidence_collected', 'message': 'collected',
                'actor_user_id': USER_ID, 'metadata': None,
                'created_at': datetime.datetime(2026, 8, 1, 0, 3, tzinfo=datetime.timezone.utc),
            }])
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
            return _Row({'max_seq': 7})
        if 'UPDATE export_jobs SET package_number' in n:
            self.package_number = params[0]
            return _Row(None)
        if 'SELECT status, error_message, package_number FROM export_jobs WHERE id = %s' in n:
            return _Row({'status': self.status, 'error_message': self.error_message, 'package_number': self.package_number})
        raise AssertionError(f'unexpected query: {n!r}')


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


def _patch(monkeypatch, conn, storage):
    @contextmanager
    def _pg():
        yield conn

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'pg_connection', _pg)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    # The critical fidelity point: production returns workspace_id as a uuid.UUID.
    monkeypatch.setattr(pilot, '_require_workspace_permission', lambda *_a, **_k: (
        {'id': USER_ID, 'mfa_enabled': False}, {'workspace_id': WORKSPACE_UUID, 'role': 'admin'}))
    monkeypatch.setattr(pilot, '_workspace_plan', lambda *_a, **_k: {'exports_enabled': True})
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_a, **_k: {'id': USER_ID})
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda *_a, **_k: {'workspace_id': WORKSPACE_UUID, 'role': 'admin'})
    monkeypatch.setattr(pilot, '_workspace_permission_granted', lambda *_a, **_k: True)
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: storage)


def test_full_proof_bundle_path_with_uuid_workspace_id_is_born_verifiable(monkeypatch, caplog):
    """Reproduces the production request: workspace_id is a real ``uuid.UUID`` in the
    manifest metadata. Before the fix this failed at artifact_hash_generation with a
    raw TypeError and completed as 'failed'; after the fix the package completes and
    is born with a retrievable, verifiable manifest — and NO hash-failure is logged."""
    conn = _UuidWorkspaceIncidentConn()
    storage = _FakeStorage()
    _patch(monkeypatch, conn, storage)

    with caplog.at_level(logging.INFO, logger='services.api.app.pilot'):
        created = pilot.create_proof_bundle_export({'incident_id': INCIDENT_ID, 'include_raw_events': True}, _req())

    assert created['status'] == 'completed', created
    pkg_id = created['package_id']

    # The manifest physically persisted and independently re-verifies.
    object_key = f'{WORKSPACE_UUID}/{pkg_id}.json'
    assert object_key in storage.written
    bundle = json.loads(storage.read_bytes(object_key=object_key))['rows'][0]
    manifest = bundle['manifest.json']
    assert manifest['files'] and all(f.get('sha256') for f in manifest['files'])
    body = {k: v for k, v in manifest.items() if k != 'manifest_sha256'}
    assert hashlib.sha256(_es.canonical_json(body)).hexdigest() == manifest['manifest_sha256']
    # The metadata workspace_id was normalized to the canonical string form.
    assert manifest['workspace_id'] == str(WORKSPACE_UUID)

    messages = [r.getMessage() for r in caplog.records if r.name == 'services.api.app.pilot']
    assert not any('artifact_hash' in m and 'failed' in m for m in messages)
    assert not any(m.startswith('evidence_artifact_hash_failed') for m in messages)
    assert any(m.startswith('evidence_manifest_readback_verified') for m in messages)
