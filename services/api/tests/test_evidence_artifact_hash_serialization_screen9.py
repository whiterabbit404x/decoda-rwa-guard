"""Screen 9 — the evidence artifact-hash serialization contract.

Locks in the fix for the production ``artifact_hash_generation`` failure
(package 64e9aaa3-…, incident 3c42654c-…): a proof bundle whose nine evidence
sections were collected and finalized failed with ``error_class=TypeError`` the
moment per-file SHA-256 generation began.

Root cause: ``evidence_signing.canonical_json`` — the single serializer behind
every per-file hash, the manifest hash and the HMAC seal, on BOTH generation and
verification — did a raw ``json.dumps`` with no JSON-safe normalization of its
own. The evidence collectors legitimately produce ``uuid.UUID``, ``datetime`` /
``date`` / ``time``, ``decimal.Decimal`` and ``bytes`` (psycopg returns these
natively), so the moment such a value reached the hashing stage un-normalized,
``json.dumps`` raised ``TypeError: Object of type X is not JSON serializable``.

The fix gives the hashing path a deterministic, recursively JSON-safe contract
(``json_safe_for_hash``) with EXPLICIT per-type encoding — never a blanket
``default=str`` — that is the identity on already-JSON-native input (so every
previously-stored manifest still verifies), and that fails CLOSED with a safe
structural path/type diagnostic (never the value) on a set or an arbitrary object.

Tests:
  * Contract reproduction: the exact raw structural types that broke production
    now hash deterministically; determinism, native-identity, and hashed==persisted
    are all proven (RED before the fix — raw UUID/datetime/Decimal → TypeError).
  * Full generation path: source collection → normalization → per-artifact SHA-256
    → manifest → seal → storage write → real read-back → verification → completed,
    over a bundle carrying nested structural types, is born verifiable.
  * Negative: an unsupported nested object (and a set) fails CLOSED with the logical
    path/type and NEVER leaks the value.
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
from fastapi import HTTPException

from services.api.app import pilot
from services.api.app import evidence_signing

# Reference the signing symbols through the module object AT CALL TIME (never a
# `from … import X` / module-level alias). Another test in this suite calls
# ``importlib.reload(evidence_signing)`` during its run, which mints a NEW
# ``EvidenceSerializationError`` class and NEW functions; a name captured at import
# time would then no longer match ``pytest.raises``/``except``. Resolving through the
# live module in each test always yields the CURRENT class/function — the same one
# pilot raises and catches.
_es = evidence_signing


# ── 1. Serialization-contract reproduction ────────────────────────────────────

# The exact raw structural types the production evidence collectors emit (psycopg
# returns these natively for uuid / timestamptz / numeric / bytea columns and their
# nested jsonb payloads). Each one raised ``TypeError`` from the old raw json.dumps.
_RAW_EVIDENCE_TYPES = {
    'uuid': uuid.UUID('64e9aaa3-8b57-4a19-bea1-d536d55818d9'),
    'datetime': datetime.datetime(2026, 8, 16, 12, 0, tzinfo=datetime.timezone.utc),
    'date': datetime.date(2026, 8, 16),
    'time': datetime.time(12, 30, 5),
    'decimal': decimal.Decimal('42.5000'),
    'bytes': b'\x00\x01\x02proof',
}


@pytest.mark.parametrize('name', sorted(_RAW_EVIDENCE_TYPES))
def test_raw_collector_types_hash_without_typeerror(name):
    """Every legitimate collector type now produces canonical bytes + a SHA-256,
    instead of a raw ``TypeError`` at the artifact-hash stage."""
    value = _RAW_EVIDENCE_TYPES[name]
    b, sha = _es._file_sha256(value)
    assert isinstance(b, bytes) and b
    assert len(sha) == 64 and int(sha, 16) >= 0  # a real hex SHA-256


def test_build_manifest_over_nested_structural_types():
    """A proof-bundle file carrying deeply NESTED structural types (the shape that
    broke production — a Decimal inside detection_metrics.evidence) hashes cleanly."""
    file_values = {
        'summary.json': {
            'export_id': uuid.UUID('64e9aaa3-8b57-4a19-bea1-d536d55818d9'),
            'generated_at': datetime.datetime(2026, 8, 16, tzinfo=datetime.timezone.utc),
        },
        'detection_metrics.json': [
            {
                'id': uuid.uuid4(),
                'detected_at': datetime.datetime(2026, 8, 1, 0, 2),
                'mttd_seconds': 120,
                'evidence': {'amount': decimal.Decimal('42.5'), 'raw': b'\x01\x02', 'chain_id': 1},
            }
        ],
    }
    manifest, file_bytes = _es.build_evidence_manifest(
        export_id='pkg', export_type='proof_bundle', workspace_id='ws',
        generated_at='2026-08-16T00:00:00Z', generated_by_user_id=None,
        source_resource_type='incident', source_resource_id='inc',
        storage_backend='s3', file_values=file_values,
    )
    assert manifest['files'] and len(manifest['files']) == 2
    assert all(f.get('sha256') for f in manifest['files'])
    assert manifest['manifest_sha256']
    # file_bytes_map carries the exact hashed bytes for each file.
    assert set(file_bytes) == {'summary.json', 'detection_metrics.json'}


def test_hash_is_deterministic_and_key_order_independent():
    payload_a = {'id': _RAW_EVIDENCE_TYPES['uuid'], 'amount': decimal.Decimal('1.0'), 'ts': _RAW_EVIDENCE_TYPES['datetime']}
    payload_b = {'ts': _RAW_EVIDENCE_TYPES['datetime'], 'amount': decimal.Decimal('1.0'), 'id': _RAW_EVIDENCE_TYPES['uuid']}
    assert _es._file_sha256(payload_a)[1] == _es._file_sha256(payload_b)[1]


def test_normalizer_is_identity_on_json_native_data():
    """No hash regression: for already-JSON-native data (what every previously
    stored manifest contains), canonical bytes are byte-for-byte unchanged."""
    native = {
        'files': [{'path': 'a.json', 'sha256': 'x' * 64, 'size_bytes': 5}],
        'n': 1, 'f': 1.5, 'flag': True, 'nothing': None, 'list': [1, 'two', 3.0],
        'nested': {'k': ['v', {'deep': 'value'}]},
    }
    raw = json.dumps(native, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('utf-8')
    assert _es.canonical_json(_es.json_safe_for_hash(native)) == raw


def test_hashed_representation_equals_persisted_representation():
    """The representation HASHED equals the one PERSISTED and later VERIFIED: the
    JSON round-trip of the normalized value re-hashes to the identical SHA-256."""
    value = {'id': uuid.uuid4(), 'amt': decimal.Decimal('7.25'), 'ts': datetime.datetime(2026, 1, 2, 3, 4, 5), 'raw': b'zz'}
    _, sha_generation = _es._file_sha256(value)
    persisted = json.loads(json.dumps(_es.json_safe_for_hash(value)))  # what actually lands in storage
    _, sha_readback = _es._file_sha256(persisted)
    assert sha_generation == sha_readback


def test_verify_bundle_round_trips_structural_types():
    """A manifest built over structural types verifies against the persisted
    (JSON-serialized, read back) file bytes — SHA-256 verification is not weakened."""
    file_values = {
        'evidence.json': [{'id': uuid.uuid4(), 'amount': decimal.Decimal('3.14'), 'observed_at': datetime.datetime(2026, 8, 1, 1, 2, 3)}],
        'summary.json': {'export_id': _RAW_EVIDENCE_TYPES['uuid']},
    }
    manifest, _ = _es.build_evidence_manifest(
        export_id='pkg', export_type='proof_bundle', workspace_id='ws',
        generated_at='2026-08-16T00:00:00Z', generated_by_user_id=None,
        source_resource_type='incident', source_resource_id='inc',
        storage_backend='s3', file_values=file_values,
    )
    seal = evidence_signing.seal_manifest(manifest)
    # Persist exactly as the generator does, then read back the JSON-native values.
    persisted = json.loads(json.dumps({k: _es.json_safe_for_hash(v) for k, v in file_values.items()}))
    result = _es.verify_bundle(persisted, manifest, seal, signing_secret=evidence_signing._DEV_FALLBACK_SECRET)
    assert result['valid'] is True, result['errors']
    # Belt-and-braces: no per-file or manifest integrity error over structural types.
    assert not any(e.startswith(('file_tampered', 'file_missing', 'file_unserializable')) for e in result['errors'])
    assert 'manifest_hash_mismatch' not in result['errors']


# ── 2. Negative: unsupported types fail CLOSED with a SAFE diagnostic ──────────

class _WalletKey:
    """An arbitrary object carrying sensitive material — must never be stringified
    into the hash or leaked into a diagnostic."""

    def __init__(self, secret: str) -> None:
        self.secret = secret

    def __str__(self) -> str:  # would leak if the normalizer fell back to str()
        return f'PRIVATE:{self.secret}'


def test_unsupported_nested_object_fails_closed_without_leaking_value():
    file_values = {
        'summary.json': {'ok': 1},
        'evidence.json': [{'tx_hash': '0xabc', 'signer': {'key': _WalletKey('super-secret-material')}}],
    }
    with pytest.raises(_es.EvidenceSerializationError) as exc_info:
        _es.build_evidence_manifest(
            export_id='pkg', export_type='proof_bundle', workspace_id='ws',
            generated_at='2026-08-16T00:00:00Z', generated_by_user_id=None,
            source_resource_type='incident', source_resource_id='inc',
            storage_backend='s3', file_values=file_values,
        )
    err = exc_info.value
    # Precise, SAFE structural diagnostic — logical file, top-level type, and the
    # nested structural path/type of the offending object.
    assert err.logical_path == 'evidence.json'
    assert err.value_type == 'list'
    assert err.nested_path == 'evidence.json[0].signer.key'
    assert err.nested_type == '_WalletKey'
    # The value is NEVER exposed — not in the message, not in the recorded attributes.
    assert 'super-secret-material' not in str(err)
    assert 'super-secret-material' not in repr(err.__dict__)


def test_set_fails_closed_as_nondeterministic():
    """A set has no deterministic ordering, so it is rejected rather than silently
    serialized (which would make the hash unstable)."""
    with pytest.raises(_es.EvidenceSerializationError) as exc_info:
        _es.json_safe_for_hash({'tags': {'a', 'b', 'c'}}, path='$')
    assert exc_info.value.nested_path == '$.tags'
    assert exc_info.value.nested_type == 'set'


# ── 3. Full generation path (real _generate_export_artifact) ──────────────────

WS = 'ws-9'
USER = 'user-9'
INCIDENT = 'inc-9'


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


class _StructuralConn:
    """Proof-bundle incident chain whose rows carry the RAW structural types the
    production collectors return (UUID, datetime, Decimal, bytes) — nested inside
    the jsonb evidence payloads — so the full generator exercises normalization →
    per-artifact hashing over exactly the shape that failed in production."""

    def __init__(self):
        self.executed: list[tuple[str, object]] = []
        self.committed = False
        self.rows: dict[str, dict] = {}
        self.inserted_pkg_id: str | None = None

    def execute(self, stmt, params=None):
        n = ' '.join(str(stmt).split())
        self.executed.append((n, params))
        return self._handle(n, params)

    def commit(self):
        self.committed = True

    def _full_row(self, pkg_id):
        row = self.rows.get(pkg_id)
        return dict(row) if row is not None else None

    def _handle(self, n, params):
        if 'pg_advisory_xact_lock' in n:
            return _Row(None)
        if 'evidence_source_fingerprint' in n and "(filters->>'response_action_id') IS NULL" in n:
            return _Row(None)
        if "(filters->>'response_action_id') IS NULL" in n and 'ORDER BY created_at DESC LIMIT 1' in n:
            return _Row(None)
        if 'created_at > %s' in n and 'LIMIT 1' in n:
            return _Row(None)
        if 'SELECT * FROM export_jobs WHERE id = %s AND workspace_id = %s' in n:
            return _Row(self._full_row(params[0]))
        if 'SELECT id, export_type, format, filters, requested_by_user_id FROM export_jobs' in n:
            row = self.rows.get(params[0], {})
            return _Row({
                'id': params[0],
                'export_type': row.get('export_type', 'proof_bundle'),
                'format': row.get('format', 'json'),
                'filters': dict(row.get('filters') or {'incident_id': INCIDENT, 'include_raw_events': True}),
                'requested_by_user_id': row.get('requested_by_user_id', USER),
            })
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
                'created_at': '2026-08-16T00:00:00Z', 'updated_at': '2026-08-16T00:00:00Z',
            }
            return _Row(None)
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
        if 'UPDATE export_jobs SET filters = %s::jsonb' in n:
            return _Row(None)
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
        if 'SELECT status, error_message, package_number FROM export_jobs WHERE id = %s' in n:
            row = self.rows.get(params[0], {})
            return _Row({'status': row.get('status'), 'error_message': row.get('error_message'), 'package_number': row.get('package_number')})

        # ── Incident evidence chain — RAW structural types (UUID/datetime/Decimal/bytes) ──
        if 'FROM incidents WHERE workspace_id = %s AND id = %s' in n:
            return _Row({
                'id': uuid.UUID('3c426540-0000-4000-8000-000000000009'), 'workspace_id': WS,
                'title': 'Structural-types incident', 'severity': 'high', 'status': 'contained',
                'asset_id': uuid.uuid4(), 'summary': 'nested structural evidence',
                'linked_alert_ids': [uuid.UUID('a1e57000-0000-4000-8000-000000000009')],
                'source_alert_id': uuid.UUID('a1e57000-0000-4000-8000-000000000009'),
                'created_at': datetime.datetime(2026, 8, 1, tzinfo=datetime.timezone.utc),
            })
        if 'FROM alerts a JOIN detection_metrics dm' in n:
            return _Row(rows=[{
                'id': uuid.UUID('a1e57000-0000-4000-8000-000000000009'), 'analysis_run_id': uuid.uuid4(),
                'target_id': uuid.uuid4(), 'title': 'Live anomaly', 'severity': 'high', 'status': 'open',
                'source': 'live', 'detection_id': uuid.uuid4(), 'detection_event_id': None,
                'summary': 'live', 'payload': {'confidence': decimal.Decimal('0.95'), 'first_seen': datetime.datetime(2026, 8, 1, 0, 1)},
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
                # The production-shape trigger: a Decimal + bytes NESTED in jsonb evidence.
                'evidence': {'tx_hash': '0xreal', 'block_number': 123, 'amount': decimal.Decimal('42.500000'), 'raw_log': b'\x00\x01rawlog', 'chain_id': 1},
            }])
        if 'FROM detection_metrics WHERE workspace_id = %s AND alert_id = ANY' in n:
            return _Row(rows=[])
        if 'FROM response_actions' in n and 'incident_id = %s' in n:
            return _Row(rows=[{
                'id': uuid.uuid4(), 'action_type': 'notify_team', 'status': 'executed', 'mode': 'live',
                'execution_metadata': {'attempts': 1, 'latency': decimal.Decimal('0.42')},
                'executed_at': datetime.datetime(2026, 8, 1, 0, 10, tzinfo=datetime.timezone.utc),
                'rolled_back_at': None, 'created_at': datetime.datetime(2026, 8, 1, 0, 9, tzinfo=datetime.timezone.utc),
            }])
        if 'FROM detections' in n and 'linked_alert_id = ANY' in n:
            return _Row(rows=[{
                'id': uuid.uuid4(), 'detection_type': 'anomaly', 'severity': 'high',
                'confidence': decimal.Decimal('0.95'), 'evidence_source': 'live', 'status': 'open',
                'detected_at': datetime.datetime(2026, 8, 1, 0, 1, 30, tzinfo=datetime.timezone.utc), 'title': 'Live anomaly',
            }])
        if 'FROM detections' in n and 'id = %s::uuid' in n:
            return _Row(None)
        if 'FROM audit_logs' in n and 'row_hash' in n:
            return _Row(None)
        if 'FROM audit_logs' in n:
            return _Row(rows=[{
                'id': uuid.uuid4(), 'action': 'export.generate', 'entity_type': 'export_job',
                'entity_id': INCIDENT, 'metadata': {'note': 'collected'},
                'created_at': datetime.datetime(2026, 8, 1, 0, 12, tzinfo=datetime.timezone.utc),
            }])
        if 'FROM incident_timeline' in n:
            return _Row(rows=[{
                'id': uuid.uuid4(), 'event_type': 'evidence_collected', 'message': 'collected',
                'actor_user_id': USER, 'metadata': None,
                'created_at': datetime.datetime(2026, 8, 1, 0, 3, tzinfo=datetime.timezone.utc),
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


def test_full_proof_bundle_path_with_structural_types_is_born_verifiable(monkeypatch):
    conn = _StructuralConn()
    storage = _FakeStorage()
    _patch(monkeypatch, conn, storage)

    created = pilot.create_proof_bundle_export({'incident_id': INCIDENT, 'include_raw_events': True}, _req())

    # No TypeError; the package completed.
    assert created['status'] == 'completed'
    pkg_id = created['package_id']

    # The artifact physically exists and carries the manifest + seal.
    object_key = f'{WS}/{pkg_id}.json'
    assert object_key in storage.written
    stored_bundle = json.loads(storage.read_bytes(object_key=object_key))['rows'][0]
    assert 'manifest.json' in stored_bundle and 'seal.json' in stored_bundle
    manifest = stored_bundle['manifest.json']

    # Files list non-empty; every evidence file carries a SHA-256; manifest_sha256 present.
    assert manifest['files'] and all(f.get('sha256') for f in manifest['files'])
    assert manifest['manifest_sha256']

    # The stored artifact contains exactly this manifest, and independent recompute of
    # every file's SHA-256 over the stored bytes matches — read-back verification passes.
    for entry in manifest['files']:
        path = entry['path']
        assert path in stored_bundle
        independent = hashlib.sha256(_es.canonical_json(_es.json_safe_for_hash(stored_bundle[path]))).hexdigest()
        assert independent == entry['sha256'], f'sha256 mismatch for {path}'
    body = {k: v for k, v in manifest.items() if k != 'manifest_sha256'}
    assert hashlib.sha256(_es.canonical_json(body)).hexdigest() == manifest['manifest_sha256']

    # Detail projection: born verifiable; Verify Integrity enabled by canonical state.
    detail = pilot.get_export(pkg_id, _req())['export']
    assert detail['manifest_reference']['exists'] is True
    assert detail['manifest_reference']['retrievable'] is True
    assert detail['manifest_sha256'] == manifest['manifest_sha256']
    assert detail['files'] and detail['files_hashed'] > 0
    assert all(f.get('sha256') for f in detail['files'])
    assert detail['integrity_status'] == 'hash_generated'
    assert detail['is_manifest_missing'] is False
    assert detail['verification_status'] == 'ready'
    assert detail['allowed_actions']['verify'] is True
    assert detail['allowed_actions']['download_manifest'] is True


def test_full_proof_bundle_path_emits_no_hash_failure_and_completes(monkeypatch, caplog):
    conn = _StructuralConn()
    storage = _FakeStorage()
    _patch(monkeypatch, conn, storage)

    with caplog.at_level(logging.INFO, logger='services.api.app.pilot'):
        created = pilot.create_proof_bundle_export({'incident_id': INCIDENT}, _req())
    assert created['status'] == 'completed'

    messages = [r.getMessage() for r in caplog.records if r.name == 'services.api.app.pilot']
    # The full lifecycle ran through hashing without any classified hash failure.
    assert any(m.startswith('evidence_files_finalized') for m in messages)
    assert any(m.startswith('evidence_file_hashes_generated') for m in messages)
    assert any(m.startswith('evidence_manifest_readback_verified') for m in messages)
    assert not any(m.startswith('evidence_artifact_hash_failed') for m in messages)
    assert not any('artifact_hash' in m and 'failed' in m for m in messages)


def test_generation_fails_closed_with_precise_diagnostic_on_unsupported_type(monkeypatch, caplog):
    """If an unsupported evidence type ever reaches the hash loop, generation fails
    CLOSED with the precise ``artifact_hash_unsupported_type`` reason and a safe
    ``evidence_artifact_hash_failed`` diagnostic (path/value_type/nested_path/
    nested_type) — never a generic ``artifact_hash_generation_failed`` and never a
    silently-completed package."""
    conn = _StructuralConn()
    storage = _FakeStorage()
    _patch(monkeypatch, conn, storage)

    def _raise_unsupported(**kwargs):
        raise _es.EvidenceSerializationError(
            logical_path='evidence.json', value_type='list',
            nested_path='evidence.json[0].signer.key', nested_type='WalletKey',
        )

    monkeypatch.setattr(evidence_signing, 'build_evidence_manifest', _raise_unsupported)

    with caplog.at_level(logging.ERROR, logger='services.api.app.pilot'):
        created = pilot.create_proof_bundle_export({'incident_id': INCIDENT}, _req())

    assert created['status'] == 'failed'
    assert created['error_message'] == 'artifact_hash_unsupported_type'
    # No manifest-less bundle persisted as a finished package.
    assert f"{WS}/{created['package_id']}.json" not in storage.written

    diag = [m.getMessage() for m in caplog.records if m.getMessage().startswith('evidence_artifact_hash_failed')]
    assert diag, 'expected a precise evidence_artifact_hash_failed diagnostic'
    line = diag[-1]
    assert 'path=evidence.json' in line
    assert 'value_type=list' in line
    assert 'nested_path=evidence.json[0].signer.key' in line
    assert 'nested_type=WalletKey' in line
    assert 'safe_reason=artifact_hash_unsupported_type' in line

    # Fail-closed detail: not generated; Verify / Download Manifest disabled.
    detail = pilot.get_export(created['package_id'], _req())['export']
    assert detail['allowed_actions']['verify'] is False
    assert detail['allowed_actions']['download_manifest'] is False
