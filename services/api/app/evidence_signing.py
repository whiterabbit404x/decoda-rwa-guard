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

import hashlib
import hmac
import json
import logging
import os
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


def canonical_json(obj: Any) -> bytes:
    """Deterministic JSON bytes: sorted keys, compact separators, UTF-8, no BOM."""
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('utf-8')


def _file_sha256(value: Any) -> tuple[bytes, str]:
    """Serialize a file value to canonical JSON bytes and return (bytes, sha256_hex)."""
    b = canonical_json(value)
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
        b, sha = _file_sha256(file_values[path])
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

    # Canonical hash of the manifest body (without manifest_sha256 itself)
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
        _, actual_sha256 = _file_sha256(file_values[path])
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
