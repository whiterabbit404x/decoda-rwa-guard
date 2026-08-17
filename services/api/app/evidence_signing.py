"""
Tamper-evident export bundle signing and verification.

Every evidence export (proof_bundle, incident_report) receives:
  - manifest.json  — SHA-256 hash of each file + canonical manifest hash
  - seal.json      — HMAC-SHA256 over the canonical manifest JSON

Production keys are loaded from the configured managed secret provider. Each seal
records the provider key identifier and immutable version so historical evidence remains
verifiable after rotation. Local/dev may use environment keys or a test fallback.
The raw secret is never logged or included in any export artifact.
"""
from __future__ import annotations

import base64
import datetime
import decimal
import enum
import hashlib
import hmac
import json
import logging
import os
import uuid
from typing import Any

from services.api.app.managed_keys import (
    load_managed_key,
    managed_key_enforcement_mode,
    managed_key_provider,
    using_legacy_environment_keys,
)

_log = logging.getLogger(__name__)

_DEV_FALLBACK_SECRET = b'decoda-dev-signing-secret-NOT-FOR-PRODUCTION'

def _is_production_like() -> bool:
    app_mode = os.getenv('APP_MODE', '').strip().lower()
    app_env = os.getenv('APP_ENV', '').strip().lower()
    return app_mode in {'production', 'staging'} or app_env in {'production', 'staging', 'prod'}


def _get_signing_secret(*, version: str | None = None) -> bytes | None:
    """Return signing material from the configured managed provider or local fallback."""
    try:
        return load_managed_key('EVIDENCE_SIGNING', version=version).material
    except RuntimeError:
        return None


_KNOWN_WEAK_SIGNING_SECRETS = {
    b'changeme',
    b'local',
    b'test',
    b'secret',
    b'password',
    b'proofpass123!',
    b'pdl_whsec_local',
    b'replace-with-long-random-secret',
    _DEV_FALLBACK_SECRET.lower(),
}


def signing_key_status() -> dict[str, Any]:
    """Return non-secret signing-key readiness metadata for startup and health checks."""
    prod = _is_production_like()
    provider = managed_key_provider()
    enforcement = managed_key_enforcement_mode()
    if prod and provider == 'env' and enforcement == 'strict':
        return {
            'configured': False,
            'strong': False,
            'provider': provider,
            'enforcement': enforcement,
            'error': 'MANAGED_KEY_ENFORCEMENT=strict forbids EXPORT_SIGNING_SECRET environment operation.',
        }
    secret = _get_signing_secret()
    if secret is None:
        return {
            'configured': False,
            'strong': False,
            'provider': provider,
            'enforcement': enforcement,
            'error': 'EXPORT_SIGNING_SECRET or a managed evidence signing key is required in production/staging.',
        }
    normalized = secret.strip().lower()
    weak = normalized in _KNOWN_WEAK_SIGNING_SECRETS
    return {
        'configured': True,
        'strong': not weak,
        'provider': provider,
        'enforcement': enforcement,
        'key_id': _signing_key_id(),
        'key_version': _signing_key_version(),
        'error': 'The development dev fallback or a known weak evidence signing key is forbidden in production/staging.' if weak else None,
    }


def signing_available() -> bool:
    """True if a real signing secret is configured."""
    return _get_signing_secret() is not None


def validate_signing_secret_at_startup() -> None:
    """Fail closed on missing/weak keys and on an invalid strict-provider cutover."""
    key_status = signing_key_status()
    prod = _is_production_like()
    if prod and (not key_status['configured'] or not key_status['strong']):
        raise RuntimeError(str(key_status['error']))
    if not key_status['configured']:
        _log.info('evidence_signing_mode=dev_test_key')
    elif using_legacy_environment_keys():
        _log.warning(
            'evidence_signing_mode=legacy_environment_key enforcement=%s; migrate to MANAGED_KEY_PROVIDER before enabling strict enforcement',
            managed_key_enforcement_mode(),
        )
    else:
        _log.info('evidence_signing_mode=%s key_id=%s', managed_key_provider(), key_status['key_id'])


def _require_signing_secret() -> tuple[bytes, bool]:
    prod = _is_production_like()
    if prod and managed_key_provider() == 'env' and managed_key_enforcement_mode() == 'strict':
        raise RuntimeError('MANAGED_KEY_ENFORCEMENT=strict forbids EXPORT_SIGNING_SECRET environment operation.')
    secret = _get_signing_secret()
    if secret is not None:
        if prod and secret == _DEV_FALLBACK_SECRET:
            raise RuntimeError('The development evidence signing dev fallback is forbidden in production/staging.')
        return secret, True
    if prod:
        raise RuntimeError('EXPORT_SIGNING_SECRET or a managed evidence signing key is required in production/staging.')
    return _DEV_FALLBACK_SECRET, False


def _signing_key_id() -> str:
    try:
        return load_managed_key('EVIDENCE_SIGNING').key_id
    except RuntimeError:
        return os.getenv('EXPORT_SIGNING_KEY_ID', 'env-default').strip() or 'env-default'


def _signing_key_version() -> str:
    try:
        return load_managed_key('EVIDENCE_SIGNING').version
    except RuntimeError:
        return 'dev-fallback'


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class EvidenceSerializationError(TypeError):
    """A value in an evidence artifact has no deterministic JSON-safe representation.

    Raised by :func:`json_safe_for_hash` instead of letting a raw ``json.dumps``
    ``TypeError`` escape the artifact-hash stage. Carries a SAFE, structural
    diagnostic — the logical evidence file, the top-level value type, and the
    nested structural path/type where an unsupported object was found — and NEVER
    the value itself, so the failure can be logged and classified precisely without
    leaking evidence contents, secrets, or wallet material.
    """

    def __init__(
        self,
        *,
        nested_path: str,
        nested_type: str,
        logical_path: str | None = None,
        value_type: str | None = None,
    ) -> None:
        self.nested_path = nested_path
        self.nested_type = nested_type
        self.logical_path = logical_path
        self.value_type = value_type
        super().__init__(
            f'unsupported evidence value of type {nested_type!r} at {nested_path}'
        )


def json_safe_for_hash(value: Any, *, path: str = '$') -> Any:
    """Deterministic, recursively JSON-safe representation of an evidence value.

    This is the serialization CONTRACT for evidence hashing. ``canonical_json`` (and
    therefore every per-file SHA-256, the manifest hash and the HMAC seal) is only
    deterministic if the value it serializes is already JSON-native; the evidence
    collectors legitimately produce ``uuid.UUID``, ``datetime``/``date``/``time``,
    ``decimal.Decimal`` and ``bytes`` (psycopg returns these natively), so a raw
    ``json.dumps`` raises ``TypeError`` on them at the artifact-hash stage.

    Rather than a blanket ``default=str`` (which can silently alter evidence
    semantics and make hashes unstable), each legitimate type is encoded EXACTLY as
    the persistence normalizer (``pilot._json_safe_value``) encodes it — so the
    representation that is HASHED is byte-for-byte the same semantic representation
    that is PERSISTED and later VERIFIED. This function is the identity on
    already-JSON-native input (so every previously-stored manifest still verifies).

    Anything without a legitimate, deterministic encoding — a ``set``/``frozenset``
    (nondeterministic ordering) or an arbitrary Python object — fails CLOSED with an
    :class:`EvidenceSerializationError` naming the safe structural path and type,
    never a silent stringification.
    """
    # JSON-native scalars pass through unchanged (bool is intentionally handled by
    # the int/bool isinstance below — json.dumps emits true/false either way).
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return base64.b64encode(bytes(value)).decode('ascii')
    if isinstance(value, enum.Enum):
        inner = value.value
        return inner if isinstance(inner, (str, int, float, bool, type(None))) else str(inner)
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, dict):
        return {
            str(key): json_safe_for_hash(item, path=f'{path}.{key}')
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [json_safe_for_hash(item, path=f'{path}[{index}]') for index, item in enumerate(value)]
    # No deterministic, evidence-safe encoding (set/frozenset/arbitrary object):
    # fail CLOSED with a structural diagnostic and never a silent str().
    raise EvidenceSerializationError(nested_path=path, nested_type=type(value).__name__)


def canonical_json(obj: Any, *, path: str = '$') -> bytes:
    """Deterministic JSON bytes: sorted keys, compact separators, UTF-8, no BOM.

    THE single authoritative evidence-hashing serializer. It first normalizes ``obj``
    through :func:`json_safe_for_hash`, so EVERY hashed / sealed / verified evidence
    document is deterministically JSON-safe regardless of whether the caller
    pre-normalized it — not only each per-file value, but the manifest body ITSELF
    (its metadata: ``workspace_id``, ``generated_at``, the audit anchor, the
    recovery/completeness fields, …), the HMAC-sealed manifest, and the audit-chain
    payload. The evidence collectors and the workspace context legitimately carry
    ``uuid.UUID`` / ``datetime`` / ``Decimal`` / ``bytes`` (psycopg returns these
    natively for uuid / timestamptz / numeric / bytea columns), so a raw
    ``json.dumps`` here raised ``TypeError`` the moment such a value reached the hash
    stage — which is exactly how the manifest-metadata ``workspace_id`` (a raw
    ``uuid.UUID``) failed in production even AFTER per-file values were normalized.

    ``json_safe_for_hash`` is the IDENTITY on already-JSON-native input, so every
    previously-persisted manifest (JSON-native by construction once read back) and
    every audit-chain row re-serializes to byte-for-byte identical bytes and still
    verifies — SHA-256 is not weakened and existing hashes / HMAC seals are
    unchanged. A value with no deterministic JSON-safe encoding fails CLOSED with an
    :class:`EvidenceSerializationError` naming a safe structural path/type, never a
    raw ``json.dumps`` ``TypeError``. ``path`` seeds that structural diagnostic so a
    per-file caller can report the failure against the logical evidence-file root.
    """
    return json.dumps(
        json_safe_for_hash(obj, path=path),
        sort_keys=True,
        separators=(',', ':'),
        ensure_ascii=True,
    ).encode('utf-8')


def _file_sha256(value: Any, *, logical_path: str = '$') -> tuple[bytes, str]:
    """Serialize a file value to canonical JSON bytes and return (bytes, sha256_hex).

    Delegates to :func:`canonical_json` — THE single authoritative serializer —
    seeding the structural diagnostic path at the logical evidence-file root so a
    legitimate ``UUID``/``datetime``/``Decimal``/``bytes`` never raises ``TypeError``
    at hash time and an unsupported type fails closed with a safe path/type
    diagnostic. There is exactly ONE normalization pass (no double-normalization).
    Used by BOTH manifest generation and verification, so the hashed and verified
    representations are identical.
    """
    b = canonical_json(value, path=logical_path)
    return b, _sha256_hex(b)


def build_evidence_manifest(
    *,
    export_id: str,
    export_type: str,
    workspace_id: str,
    generated_at: str,
    generated_by_user_id: str | None,
    source_resource_type: str,
    source_resource_id: str,
    storage_backend: str,
    file_values: dict[str, Any],
    previous_audit_anchor_hash: str | None = None,
    app_version: str | None = None,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    """
    Build the evidence manifest and return (manifest_dict, file_bytes_map).

    file_bytes_map maps each file path to its canonical-JSON bytes so the
    caller can write them consistently with the hashes in the manifest.
    """
    file_bytes_map: dict[str, bytes] = {}
    file_list: list[dict[str, Any]] = []
    for path in sorted(file_values.keys()):
        try:
            b, sha = _file_sha256(file_values[path], logical_path=path)
        except EvidenceSerializationError as exc:
            # Annotate the structural failure with the LOGICAL evidence file and its
            # top-level type so the caller can log path/value_type/nested_path/
            # nested_type precisely (never the value) and classify the failure.
            exc.logical_path = path
            exc.value_type = type(file_values[path]).__name__
            raise
        file_bytes_map[path] = b
        file_list.append({'path': path, 'sha256': sha, 'size_bytes': len(b)})

    manifest: dict[str, Any] = {
        'manifest_version': '1.0',
        'export_id': export_id,
        'export_type': export_type,
        'workspace_id': workspace_id,
        'generated_at': generated_at,
        'generated_by_user_id': generated_by_user_id,
        'source_resource_type': source_resource_type,
        'source_resource_id': source_resource_id,
        'storage_backend': storage_backend,
        'files': file_list,
    }
    if app_version:
        manifest['app_version'] = app_version
    if previous_audit_anchor_hash:
        manifest['previous_audit_anchor_hash'] = previous_audit_anchor_hash

    # Normalize the ENTIRE manifest body (metadata + files) to its deterministic
    # JSON-safe form BEFORE hashing, and RETURN that normalized document. This is the
    # single-contract invariant: the manifest that is HASHED here is byte-for-byte the
    # SAME object that the caller embeds into the bundle, uploads to storage,
    # HMAC-seals and later verifies — generated == persisted == hashed == sealed ==
    # verified. It is what finally covers the manifest METADATA (the psycopg-native
    # ``uuid.UUID`` workspace_id that broke production BOTH at the manifest hash AND,
    # once past it, at the bundle-upload ``json.dumps``), which per-file normalization
    # never touched. ``json_safe_for_hash`` is the identity on already-JSON-native
    # input, so every existing string-id manifest is byte-for-byte unchanged; a
    # genuinely unsupported metadata value fails CLOSED with a safe structural
    # path/type, annotated against the manifest so the caller classifies it precisely.
    try:
        manifest = json_safe_for_hash(manifest)
    except EvidenceSerializationError as exc:
        exc.logical_path = exc.logical_path or '<manifest>'
        exc.value_type = exc.value_type or 'dict'
        raise
    manifest['manifest_sha256'] = _sha256_hex(canonical_json(manifest))
    return manifest, file_bytes_map


def build_recovery_manifest(
    *,
    export_id: str,
    export_type: str,
    workspace_id: str,
    generated_at: str,
    generated_by_user_id: str | None,
    incident_id: str,
    storage_backend: str,
    file_values: dict[str, Any],
    package_number: str | None = None,
    file_source_types: dict[str, str] | None = None,
    completeness_score: Any | None = None,
    missing_evidence_codes: list[str] | None = None,
    unverifiable_evidence_codes: list[str] | None = None,
    evidence_window: dict[str, Any] | None = None,
    app_version: str | None = None,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Build an enriched, deterministic recovery manifest over EXACT stored file bytes.

    Used by the Manifest-Missing recovery flow to reconstruct a tamper-evident
    manifest for a package whose original manifest was never persisted (the
    EV-2026-004 state). File SHA-256 hashes are computed from the exact stored
    ``file_values`` bytes — never regenerated from live DB state — so the
    recovered manifest describes the package exactly as it is stored.

    Superset of :func:`build_evidence_manifest`: it keeps the same verifiable core
    (a ``files`` list with per-file ``sha256``/``size_bytes`` and a canonical
    ``manifest_sha256``) so the existing verify/download paths work unchanged, and
    adds the canonical descriptive fields the recovery manifest must carry:
    ``schema_version``, ``package_id``/``package_number``, ``incident_id``,
    ``created_at``, ``evidence_window``, ``completeness_score``,
    ``missing_evidence_codes``/``unverifiable_evidence_codes`` and, per file, a
    ``media_type`` and ``source_record_type``.

    Given identical inputs (exact same stored bytes and package facts) this returns
    an identical manifest and hash — ``generated_at`` must therefore be a stable
    package fact (e.g. the package's ``created_at``), not a wall-clock read. The
    manifest hash is computed over the canonical serialized body WITHOUT its own
    ``manifest_sha256`` field, so it never recursively hashes its own hash.
    """
    manifest, file_bytes_map = build_evidence_manifest(
        export_id=export_id,
        export_type=export_type,
        workspace_id=workspace_id,
        generated_at=generated_at,
        generated_by_user_id=generated_by_user_id,
        source_resource_type='incident',
        source_resource_id=incident_id,
        storage_backend=storage_backend,
        file_values=file_values,
        app_version=app_version,
    )
    # The core hash is recomputed after enrichment, so drop it first.
    manifest.pop('manifest_sha256', None)

    source_types = file_source_types or {}
    for entry in manifest['files']:
        path = str(entry.get('path') or '')
        # Every included file carries its media type and the source-record type it
        # was collected from — never the file contents themselves.
        entry['media_type'] = 'application/json'
        entry['source_record_type'] = source_types.get(path, 'evidence')

    manifest['schema_version'] = manifest.get('manifest_version', '1.0')
    manifest['package_id'] = export_id
    if package_number:
        manifest['package_number'] = package_number
    manifest['incident_id'] = incident_id
    manifest['created_at'] = generated_at
    # Truthful provenance: this manifest was reconstructed after the fact, not
    # embedded at original package creation. It hashes the exact stored bytes.
    manifest['manifest_origin'] = 'recovery'
    if completeness_score is not None:
        manifest['completeness_score'] = completeness_score
    manifest['missing_evidence_codes'] = list(missing_evidence_codes or [])
    manifest['unverifiable_evidence_codes'] = list(unverifiable_evidence_codes or [])
    if evidence_window:
        manifest['evidence_window'] = evidence_window

    # Normalize the enriched body to its JSON-safe form and RETURN that document, so
    # the enrichment fields (``completeness_score`` Decimal, ``evidence_window``
    # datetimes, …) are embedded/persisted/hashed/verified as ONE canonical
    # representation — never a raw ``Decimal``/``datetime`` that would serialize here
    # but fail at the bundle-upload ``json.dumps``. Identity on JSON-native input; an
    # unsupported value fails CLOSED with a safe structural path/type.
    try:
        manifest = json_safe_for_hash(manifest)
    except EvidenceSerializationError as exc:
        exc.logical_path = exc.logical_path or '<recovery-manifest>'
        exc.value_type = exc.value_type or 'dict'
        raise
    manifest['manifest_sha256'] = _sha256_hex(canonical_json(manifest))
    return manifest, file_bytes_map


def seal_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """
    Compute HMAC-SHA256 over the canonical manifest JSON (which includes manifest_sha256).
    Returns seal dict. Never includes the raw secret.
    """
    secret, is_prod_secret = _require_signing_secret()
    key_id = _signing_key_id()
    canonical = canonical_json(manifest)
    sig = hmac.new(secret, canonical, 'sha256').hexdigest()
    seal: dict[str, Any] = {
        'signature_algorithm': 'HMAC-SHA256',
        'key_id': key_id,
        'key_version': _signing_key_version(),
        'key_provider': managed_key_provider(),
        'signed_manifest_sha256': manifest.get('manifest_sha256', ''),
        'signature': sig,
        'signed_at': manifest.get('generated_at', ''),
    }
    if not is_prod_secret:
        seal['warning'] = (
            'DEV_MODE_TEST_SECRET: This seal was generated with a non-production '
            'test secret and is NOT valid for regulatory, legal, or evidentiary purposes.'
        )
    return seal


def signing_metadata(manifest: dict[str, Any], seal: dict[str, Any]) -> dict[str, Any]:
    """Return the subset of signing metadata safe to include in API responses."""
    return {
        'signed': True,
        'manifest_sha256': manifest.get('manifest_sha256', ''),
        'signature_algorithm': seal.get('signature_algorithm', ''),
        'key_id': seal.get('key_id', ''),
        'key_version': seal.get('key_version', ''),
        'key_provider': seal.get('key_provider', ''),
        'signed_at': seal.get('signed_at', ''),
        'production_secret': 'warning' not in seal,
        'warning': seal.get('warning'),
    }


def verify_bundle(
    file_values: dict[str, Any],
    manifest: dict[str, Any],
    seal: dict[str, Any],
    *,
    signing_secret: bytes | None = None,
) -> dict[str, Any]:
    """
    Verify a bundle.

    Checks:
      1. Every file listed in the manifest exists in file_values
      2. Every file's SHA-256 matches the manifest entry
      3. The canonical manifest hash matches manifest_sha256
      4. The HMAC signature over the canonical manifest matches seal.signature

    Returns {'valid': bool, 'errors': list[str]}.
    """
    errors: list[str] = []

    # 1 & 2: File existence and hash integrity
    for entry in manifest.get('files', []):
        path = entry.get('path', '')
        expected_sha256 = entry.get('sha256', '')
        if path not in file_values:
            errors.append(f'file_missing:{path}')
            continue
        try:
            _, actual_sha256 = _file_sha256(file_values[path], logical_path=path)
        except EvidenceSerializationError:
            # A stored file that cannot be deterministically re-serialized cannot be
            # proven intact — treat it as a verification failure, never a pass.
            errors.append(f'file_unserializable:{path}')
            continue
        if actual_sha256 != expected_sha256:
            errors.append(f'file_tampered:{path}')

    # Check for extra files not listed in manifest (not an error, but noted)
    manifest_paths = {e.get('path', '') for e in manifest.get('files', [])}
    extra = set(file_values.keys()) - manifest_paths
    if extra:
        errors.append(f'unlisted_files:{sorted(extra)}')

    # 3: Canonical manifest hash
    manifest_without_hash = {k: v for k, v in manifest.items() if k != 'manifest_sha256'}
    computed_manifest_sha256 = _sha256_hex(canonical_json(manifest_without_hash))
    if computed_manifest_sha256 != manifest.get('manifest_sha256', ''):
        errors.append('manifest_hash_mismatch')

    # 4: HMAC signature
    secret = signing_secret
    if secret is None:
        secret = _get_signing_secret(version=str(seal.get('key_version') or '') or None)
    if secret is None:
        errors.append('signing_secret_not_available')
    else:
        canonical = canonical_json(manifest)
        expected_sig = hmac.new(secret, canonical, 'sha256').hexdigest()
        actual_sig = seal.get('signature', '')
        if not hmac.compare_digest(expected_sig.encode(), actual_sig.encode() if actual_sig else b''):
            errors.append('hmac_signature_invalid')

    return {'valid': len(errors) == 0, 'errors': errors}


def compute_audit_row_hash(
    *,
    row_id: str,
    workspace_id: str | None,
    user_id: str | None,
    action: str,
    entity_type: str,
    entity_id: str,
    created_at_iso: str,
    metadata_sha256: str,
    previous_row_hash: str | None,
) -> str:
    """Compute the hash-chain hash for a single audit log row."""
    payload = {
        'id': row_id,
        'workspace_id': workspace_id,
        'user_id': user_id,
        'action': action,
        'entity_type': entity_type,
        'entity_id': entity_id,
        'created_at': created_at_iso,
        'metadata_sha256': metadata_sha256,
        'previous_row_hash': previous_row_hash,
    }
    return _sha256_hex(canonical_json(payload))


def verify_audit_chain(rows: list[dict[str, Any]], *, initial_previous_hash: str | None = None) -> dict[str, Any]:
    """
    Verify the hash chain integrity for a list of audit rows.

    Rows must be ordered by created_at ASC (oldest first).
    Returns {'valid': bool, 'errors': list[str], 'chain_length': int}.
    """
    errors: list[str] = []
    previous_hash: str | None = initial_previous_hash

    for i, row in enumerate(rows):
        row_id = str(row.get('id', ''))
        stored_hash = str(row.get('row_hash') or '')
        stored_prev = row.get('previous_row_hash')

        # Verify previous_row_hash linkage
        if stored_prev != previous_hash:
            errors.append(
                f'chain_break_at_row_{i}:id={row_id}'
                f':expected_prev={previous_hash}:stored_prev={stored_prev}'
            )

        # Recompute row_hash
        if stored_hash:
            metadata = row.get('metadata') or {}
            metadata_sha256 = _sha256_hex(canonical_json(metadata))
            created_at_iso = ''
            raw_ts = row.get('created_at')
            if raw_ts:
                created_at_iso = str(raw_ts) if isinstance(raw_ts, str) else raw_ts.isoformat()
            computed = compute_audit_row_hash(
                row_id=row_id,
                workspace_id=str(row.get('workspace_id') or '') or None,
                user_id=str(row.get('user_id') or '') or None,
                action=str(row.get('action', '')),
                entity_type=str(row.get('entity_type', '')),
                entity_id=str(row.get('entity_id', '')),
                created_at_iso=created_at_iso,
                metadata_sha256=metadata_sha256,
                previous_row_hash=str(stored_prev) if stored_prev else None,
            )
            if computed != stored_hash:
                errors.append(f'row_hash_mismatch_at_row_{i}:id={row_id}')
            previous_hash = stored_hash
        else:
            # Row pre-dates hash chaining; advance the chain only if we have no errors yet
            previous_hash = stored_prev if stored_prev else previous_hash

    return {'valid': len(errors) == 0, 'errors': errors, 'chain_length': len(rows)}
