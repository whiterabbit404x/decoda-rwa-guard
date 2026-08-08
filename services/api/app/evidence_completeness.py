"""
Deterministic evidence-completeness scoring for Screen 9 (Evidence & Audit).

This module powers the "Crypto-Auditing Clerk" completeness metric. It is
intentionally free of any LLM, randomness, or network I/O: the score, status,
category breakdown and checklist are a pure function of canonical package facts
(which source records were collected, the incident state, whether hashes and a
manifest exist, and — when a verification has run — whether it passed).

Requirements adapt to the incident state so a package is never penalised for
evidence that legitimately cannot exist yet:
  - execution evidence is only required when a response action was executed,
  - rejection evidence is only required when a response was rejected,
  - closure evidence is only required once the incident is contained/closed,
  - on-chain metadata is only required when the incident has an on-chain
    component.

The integrity-status derivation maps the export-job lifecycle plus any recorded
verification result onto the canonical Screen 9 integrity states. It never
claims "verified" unless a deterministic verification actually passed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# ── Status thresholds (documented in the product spec) ────────────────────────
# 95-100 Excellent · 80-94 Good · 60-79 Incomplete · <60 Critical
_STATUS_BANDS: tuple[tuple[int, str, str], ...] = (
    (95, 'excellent', 'Excellent'),
    (80, 'good', 'Good'),
    (60, 'incomplete', 'Incomplete'),
    (0, 'critical', 'Critical'),
)

# ── Canonical integrity states ────────────────────────────────────────────────
INTEGRITY_DRAFT = 'draft'
INTEGRITY_BUILDING = 'building'
INTEGRITY_HASHING = 'hashing'
INTEGRITY_FAILED = 'failed'
INTEGRITY_NEEDS_EVIDENCE = 'needs_evidence'
INTEGRITY_HASH_GENERATED = 'hash_generated'
INTEGRITY_VERIFYING = 'verifying'
INTEGRITY_VERIFIED = 'verified'
INTEGRITY_INTEGRITY_FAILED = 'integrity_failed'
INTEGRITY_SUPERSEDED = 'superseded'
# A completed export that never produced a tamper-evident manifest or file
# hashes (legacy JSON exports created before Screen 9 packaging, or non-package
# export types). It must NEVER be shown as "hash_generated" or "verified".
INTEGRITY_LEGACY_EXPORT = 'legacy_export'
# A completed evidence-type package that WAS built as a signed proof bundle
# (it carries packaging metadata — a completeness score, a files-hashed count, an
# export status) but whose canonical manifest is not retrievable from storage.
# Distinct from legacy_export (which never had a manifest at all): this package
# was supposed to have one and does not, so Verify/Download Manifest are
# cryptographically impossible and must be disabled with a truthful label.
INTEGRITY_MANIFEST_MISSING = 'manifest_missing'

# Hash-column display states (kept separate from integrity so the two columns
# can never contradict each other — a package with no stored hash is
# "not_generated", never "Pending" next to a "Hash generated" integrity pill).
HASH_STATE_NONE = 'not_generated'
HASH_STATE_GENERATING = 'generating'
HASH_STATE_PRESENT = 'present'

# Canonical, human-readable label for each integrity state. Authoritative so the
# table pill, the details view and the filter dropdown all show the same wording.
INTEGRITY_LABELS: dict[str, str] = {
    INTEGRITY_DRAFT: 'Draft',
    INTEGRITY_BUILDING: 'Building',
    INTEGRITY_HASHING: 'Hashing',
    INTEGRITY_HASH_GENERATED: 'Hash Generated',
    INTEGRITY_VERIFYING: 'Verifying',
    INTEGRITY_VERIFIED: 'Verified',
    INTEGRITY_NEEDS_EVIDENCE: 'Needs Evidence',
    INTEGRITY_INTEGRITY_FAILED: 'Integrity Failed',
    INTEGRITY_FAILED: 'Failed',
    INTEGRITY_SUPERSEDED: 'Superseded',
    INTEGRITY_LEGACY_EXPORT: 'Legacy Export',
    INTEGRITY_MANIFEST_MISSING: 'Manifest Missing',
}


def integrity_status_label(status: str | None) -> str:
    """Canonical display label for an integrity state (falls back to a titleized
    form for any unknown value so the UI never shows a raw enum)."""
    key = str(status or '').lower()
    if key in INTEGRITY_LABELS:
        return INTEGRITY_LABELS[key]
    return key.replace('_', ' ').title() if key else 'Unknown'


# Integrity states in which a completed package is not a usable evidence artifact
# and must not be counted toward export-readiness.
_NON_EXPORTABLE_INTEGRITY = frozenset({
    INTEGRITY_DRAFT,
    INTEGRITY_BUILDING,
    INTEGRITY_HASHING,
    INTEGRITY_FAILED,
    INTEGRITY_INTEGRITY_FAILED,
    INTEGRITY_LEGACY_EXPORT,
    INTEGRITY_MANIFEST_MISSING,
    INTEGRITY_SUPERSEDED,
})

# Evidence-package export types build a signed manifest by construction. Only
# these can meaningfully be in the manifest_missing state; any other completed
# export with no hashes is simply a legacy_export.
EVIDENCE_EXPORT_TYPES = frozenset({'proof_bundle', 'incident_report'})


def _status_for_score(score: int) -> tuple[str, str]:
    for threshold, key, label in _STATUS_BANDS:
        if score >= threshold:
            return key, label
    return 'critical', 'Critical'


# Categories in a stable, documented order. Each entry declares whether it is
# required for the current package (via a predicate over the facts) and whether
# it is present. Order is fixed so the breakdown and hashes are deterministic.
def _category_specs(f: dict[str, Any]) -> list[dict[str, Any]]:
    incident_status = str(f.get('incident_status') or '').lower()
    source = str(f.get('evidence_source_type') or '').lower()
    response_action_count = int(f.get('response_action_count') or 0)
    executed_action_count = int(f.get('executed_action_count') or 0)
    rejected_action_count = int(f.get('rejected_action_count') or 0)
    requires_approval = bool(f.get('requires_approval'))
    closed = incident_status in {'closed', 'contained', 'resolved', 'mitigated'}
    on_chain = bool(f.get('has_chain_metadata')) or source in {'live', 'simulator'}
    degraded_source = source in {'unavailable', 'unknown', 'missing', ''}

    return [
        {
            'code': 'incident_identity',
            'label': 'Incident identity and timestamps',
            'required': True,
            'present': bool(f.get('has_incident')),
        },
        {
            'code': 'original_alert',
            'label': 'Original alert',
            'required': True,
            'present': bool(f.get('has_alert')),
        },
        {
            'code': 'detection_provenance',
            'label': 'Detection provenance',
            'required': True,
            'present': bool(f.get('has_detection')),
        },
        {
            'code': 'telemetry_reference',
            'label': 'Raw telemetry references',
            'required': True,
            'present': bool(f.get('has_telemetry')),
            # Present rows whose source is degraded are collected but cannot be
            # trusted as verifiable live telemetry.
            'unverifiable': bool(f.get('has_telemetry')) and degraded_source,
        },
        {
            'code': 'asset_identity',
            'label': 'Asset identity',
            'required': True,
            'present': bool(f.get('has_asset')),
        },
        {
            'code': 'chain_metadata',
            'label': 'Chain and transaction metadata',
            'required': on_chain,
            'present': bool(f.get('has_chain_metadata')),
            'unverifiable': (not bool(f.get('has_chain_metadata'))) and degraded_source,
        },
        {
            'code': 'investigation_timeline',
            'label': 'Investigation timeline',
            'required': True,
            'present': bool(f.get('has_investigation_timeline')),
        },
        {
            'code': 'response_recommendation',
            'label': 'Recommended response',
            'required': response_action_count > 0,
            'present': response_action_count > 0,
        },
        {
            'code': 'approval_decision',
            'label': 'Approval or rejection decision',
            'required': requires_approval or executed_action_count > 0 or rejected_action_count > 0,
            'present': bool(f.get('approval_present')) or rejected_action_count > 0,
        },
        {
            'code': 'execution_result',
            'label': 'Executed response and execution result',
            'required': executed_action_count > 0,
            'present': executed_action_count > 0 and bool(f.get('has_execution_result', True)),
        },
        {
            'code': 'rejection_evidence',
            'label': 'Rejection evidence',
            'required': rejected_action_count > 0,
            'present': rejected_action_count > 0,
        },
        {
            'code': 'closure_state',
            'label': 'Closure or containment state',
            'required': closed,
            'present': closed,
        },
        {
            'code': 'audit_events',
            'label': 'Audit events',
            'required': True,
            'present': bool(f.get('has_audit_events')),
        },
        {
            'code': 'file_hashes',
            'label': 'File hashes (SHA-256)',
            'required': True,
            'present': int(f.get('files_hashed') or 0) > 0,
        },
        {
            'code': 'manifest_hash',
            'label': 'Manifest hash',
            'required': True,
            'present': bool(f.get('has_manifest')),
        },
    ]


def compute_evidence_completeness(facts: dict[str, Any]) -> dict[str, Any]:
    """Return a deterministic completeness result for a package.

    ``facts`` is a plain dict of canonical signals (see ``_category_specs``).
    The result contains score, status, counts, a per-category breakdown, the
    machine codes of missing items, and the sidebar checklist. Identical facts
    always produce an identical result.
    """
    categories: list[dict[str, Any]] = []
    required_count = 0
    present_count = 0
    missing_count = 0
    unverifiable_count = 0
    missing_codes: list[str] = []

    for spec in _category_specs(facts):
        if not spec['required']:
            categories.append({
                'code': spec['code'],
                'label': spec['label'],
                'required': False,
                'status': 'not_applicable',
            })
            continue
        required_count += 1
        if spec['present']:
            status = 'present'
            present_count += 1
        elif spec.get('unverifiable'):
            status = 'unverifiable'
            unverifiable_count += 1
            missing_codes.append(spec['code'])
        else:
            status = 'missing'
            missing_count += 1
            missing_codes.append(spec['code'])
        categories.append({
            'code': spec['code'],
            'label': spec['label'],
            'required': True,
            'status': status,
        })

    score = round(100 * present_count / required_count) if required_count else 100
    status_key, status_label = _status_for_score(score)

    return {
        'score': score,
        'status': status_key,
        'status_label': status_label,
        'required_count': required_count,
        'present_count': present_count,
        'missing_count': missing_count,
        'unverifiable_count': unverifiable_count,
        'categories': categories,
        'missing_codes': missing_codes,
        'checklist': _build_checklist(facts, categories),
    }


def _category_status(categories: list[dict[str, Any]], code: str) -> str:
    for c in categories:
        if c['code'] == code:
            return str(c['status'])
    return 'not_applicable'


def _build_checklist(facts: dict[str, Any], categories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sidebar checklist. Every item is computed from real package facts.

    ``verified`` is only true once a deterministic verification has passed
    (facts['manifest_verified'] is True). Before then, hashes are 'generated'
    but not 'verified' — the label reflects that truthfully.
    """
    manifest_verified = facts.get('manifest_verified')
    files_hashed = int(facts.get('files_hashed') or 0)

    def present(code: str) -> bool:
        return _category_status(categories, code) == 'present'

    return [
        {'code': 'required_fields', 'label': 'Required fields present',
         'present': _resolve_required_fields_present(categories)},
        {'code': 'hashes_generated', 'label': 'File hashes generated',
         'present': files_hashed > 0},
        {'code': 'hashes_verified', 'label': 'Hashes verified',
         'present': bool(manifest_verified)},
        {'code': 'chain_data', 'label': 'Chain data complete',
         'present': present('chain_metadata') or _category_status(categories, 'chain_metadata') == 'not_applicable'},
        {'code': 'logs_included', 'label': 'Logs included',
         'present': bool(facts.get('has_audit_events'))},
        {'code': 'approvals_included', 'label': 'Response approvals included',
         'present': present('approval_decision') or _category_status(categories, 'approval_decision') == 'not_applicable'},
        {'code': 'execution_included', 'label': 'Execution outcome included',
         'present': present('execution_result') or _category_status(categories, 'execution_result') == 'not_applicable'},
        {'code': 'provenance', 'label': 'Provenance complete',
         'present': bool(facts.get('has_manifest')) and files_hashed > 0},
    ]


def _resolve_required_fields_present(categories: list[dict[str, Any]]) -> bool:
    """The core identity/alert/detection/manifest fields must all be present."""
    core = {'incident_identity', 'original_alert', 'detection_provenance', 'manifest_hash', 'file_hashes'}
    for c in categories:
        if c['code'] in core and c['required'] and c['status'] != 'present':
            return False
    return True


def verify_files_and_manifest(file_values: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    """Recompute every file's SHA-256 + byte count and the manifest hash, and
    compare against the stored manifest. Pure and deterministic.

    A file passes only when its recomputed SHA-256 AND byte count match the
    manifest entry. The manifest hash is recomputed over the canonical manifest
    body with its own ``manifest_sha256`` field excluded — never over bytes that
    contain that field. Returns valid/files/manifest_ok details; the caller
    decides how to persist and audit the result.
    """
    import hashlib
    from services.api.app.evidence_signing import canonical_json

    files_verified = 0
    files_failed: list[str] = []
    missing_files: list[str] = []
    for entry in manifest.get('files', []) or []:
        path = str(entry.get('path') or '')
        expected_sha = str(entry.get('sha256') or '')
        expected_size = entry.get('size_bytes')
        if path not in file_values:
            missing_files.append(path)
            continue
        file_bytes = canonical_json(file_values[path])
        actual_sha = hashlib.sha256(file_bytes).hexdigest()
        size_ok = expected_size is None or len(file_bytes) == int(expected_size)
        if actual_sha == expected_sha and size_ok:
            files_verified += 1
        else:
            files_failed.append(path)

    manifest_without_hash = {k: v for k, v in manifest.items() if k != 'manifest_sha256'}
    manifest_ok = hashlib.sha256(canonical_json(manifest_without_hash)).hexdigest() == str(manifest.get('manifest_sha256') or '')
    valid = (not files_failed) and (not missing_files) and manifest_ok
    return {
        'valid': valid,
        'files_total': len(manifest.get('files', []) or []),
        'files_verified': files_verified,
        'files_failed': files_failed,
        'missing_files': missing_files,
        'manifest_ok': manifest_ok,
    }


def _completeness_indicates_hashes(completeness: dict[str, Any] | None) -> bool:
    """Best-effort inference of whether a package actually carries file hashes.

    Used only when the caller does not pass an explicit ``has_hashes`` signal
    (e.g. unit tests that build a full completeness result). A completed export
    with no completeness facts at all is treated as having no hashes — it is a
    legacy/non-package export, not a hashed evidence package.
    """
    if not completeness:
        return False
    categories = completeness.get('categories')
    if isinstance(categories, list):
        for category in categories:
            if category.get('code') == 'file_hashes':
                return category.get('status') == 'present'
    # A minimal completeness dict ({'score': N}) still implies a real generated
    # package — a score is only computed for packages that were built.
    return completeness.get('score') is not None


def derive_integrity_status(
    *,
    job_status: str,
    verification: dict[str, Any] | None,
    completeness: dict[str, Any] | None,
    superseded: bool = False,
    has_hashes: bool | None = None,
) -> str:
    """Map export-job lifecycle + recorded verification onto a Screen 9 integrity state.

    ``has_hashes`` is the authoritative signal for whether the package carries a
    real manifest/file SHA-256 set (from the stored ``integrity_hash`` or
    ``files_hashed`` count). When it is ``None`` the presence of hashes is
    inferred from ``completeness`` — so callers that have the canonical facts
    (list/detail views) should always pass it explicitly.

    Never returns 'verified' unless a recorded verification actually passed, and
    never returns 'hash_generated' for a completed export that has no hashes —
    that is reported as 'legacy_export' so the hash and integrity columns cannot
    contradict each other.
    """
    status = str(job_status or '').lower()
    if superseded:
        return INTEGRITY_SUPERSEDED
    if status in {'queued', 'pending', 'building', 'running'}:
        return INTEGRITY_BUILDING
    if status == 'failed':
        return INTEGRITY_FAILED
    if status != 'completed':
        return INTEGRITY_BUILDING

    # Completed job — resolve integrity from any recorded verification result.
    if verification is not None:
        valid = verification.get('valid')
        if valid is True:
            return INTEGRITY_VERIFIED
        if valid is False:
            return INTEGRITY_INTEGRITY_FAILED

    # No verification recorded yet. If core required evidence is missing the
    # package still needs evidence (checked first so a hashed-but-incomplete
    # package is reported truthfully).
    if completeness is not None:
        core_missing = any(
            code in {'incident_identity', 'original_alert', 'detection_provenance', 'file_hashes', 'manifest_hash'}
            for code in (completeness.get('missing_codes') or [])
        )
        if core_missing:
            return INTEGRITY_NEEDS_EVIDENCE

    # A completed export with no retrievable manifest is not a verifiable evidence
    # artifact. It is reported as legacy_export here; the display-state selector
    # promotes it to manifest_missing when the package WAS built as an evidence
    # bundle (so Verify/Download stay disabled with a truthful, specific label).
    # A low completeness score does NOT downgrade a properly-manifested package —
    # partial completeness is reported separately by the completeness score, and a
    # 40% package with a real manifest can still be hashed and then verified.
    resolved_has_hashes = has_hashes if has_hashes is not None else _completeness_indicates_hashes(completeness)
    if not resolved_has_hashes:
        return INTEGRITY_LEGACY_EXPORT
    return INTEGRITY_HASH_GENERATED


# ── Canonical evidence-manifest reference (Screen 9) ──────────────────────────
# ONE structure describes whether a package has a real, retrievable manifest.
# Package generation, the list API, the detail API, Download Manifest, Verify
# Integrity, allowed_actions, the agent card and the SHA-256 table column all
# resolve manifest existence through this — never by each inferring it from a
# different field (a files-hashed count, a raw integrity_hash, or the presence of
# an artifact object). A manifest is only "retrievable" when its bytes can
# actually be read back; a persisted hash alone is not proof the manifest exists.

_MANIFEST_MEDIA_TYPE = 'application/json'


@dataclass(frozen=True)
class EvidenceManifestReference:
    """Canonical description of a package's evidence manifest.

    ``exists``     — a manifest was built for this package (a manifest SHA-256 is
                     recorded, or a manifest object is present).
    ``retrievable``— the manifest bytes can actually be read back and parsed with
                     a non-empty file list. Verify/Download require this.
    The remaining fields describe the manifest for display and are never invented.
    ``source``     — 'storage' when confirmed against the stored artifact,
                     'metadata' when derived from persisted package facts only,
                     'storage_unavailable' when storage could not be read.
    """
    exists: bool
    retrievable: bool
    storage_key: str | None
    sha256: str | None
    size_bytes: int | None
    media_type: str
    file_count: int | None
    generated_at: str | None
    source: str

    def as_dict(self) -> dict[str, Any]:
        return {
            'exists': self.exists,
            'retrievable': self.retrievable,
            'storage_key': self.storage_key,
            'sha256': self.sha256,
            'size_bytes': self.size_bytes,
            'media_type': self.media_type,
            'file_count': self.file_count,
            'generated_at': self.generated_at,
            'source': self.source,
        }


def _recorded_manifest_sha256(package: dict[str, Any]) -> str | None:
    """The manifest hash a package persisted at build time, if any.

    ``integrity_hash`` IS the manifest SHA-256 (see _generate_export_artifact),
    so it is the canonical recorded-manifest signal. ``manifest_sha256`` is
    accepted as an explicit alias.
    """
    for key in ('manifest_sha256', 'integrity_hash'):
        value = str(package.get(key) or '').strip()
        if value:
            return value
    return None


def resolve_manifest_reference(
    package: dict[str, Any],
    *,
    bundle_parts: dict[str, Any] | None = None,
) -> EvidenceManifestReference:
    """Single source of truth for a package's manifest.

    When ``bundle_parts`` (the output of the stored-artifact reader) is supplied
    the reference is storage-authoritative: ``retrievable`` reflects whether the
    manifest bytes were actually read back with a non-empty file list. Without
    it the reference is metadata-only (used by the list projection), where a
    recorded manifest SHA-256 is treated as an existing manifest.
    """
    storage_key = str(
        package.get('storage_object_key') or package.get('storage_key') or ''
    ).strip() or None
    recorded_sha = _recorded_manifest_sha256(package)

    if bundle_parts is not None:
        storage_error = bundle_parts.get('storage_error')
        manifest = bundle_parts.get('manifest')
        if storage_error:
            # The artifact could not be read — we cannot confirm retrievability.
            return EvidenceManifestReference(
                exists=bool(recorded_sha), retrievable=False, storage_key=storage_key,
                sha256=recorded_sha, size_bytes=None, media_type=_MANIFEST_MEDIA_TYPE,
                file_count=None, generated_at=None, source='storage_unavailable',
            )
        files = manifest.get('files') if isinstance(manifest, dict) else None
        if isinstance(manifest, dict) and isinstance(files, list) and files:
            # Every referenced file must carry a SHA-256 for the manifest to be a
            # usable evidence manifest — a file entry without one is not verifiable.
            all_hashed = all(isinstance(f, dict) and str(f.get('sha256') or '').strip() for f in files)
            try:
                from services.api.app.evidence_signing import canonical_json
                size_bytes: int | None = len(canonical_json(manifest))
            except Exception:
                size_bytes = None
            return EvidenceManifestReference(
                exists=True, retrievable=all_hashed, storage_key=storage_key,
                sha256=str(manifest.get('manifest_sha256') or '') or recorded_sha,
                size_bytes=size_bytes, media_type=_MANIFEST_MEDIA_TYPE,
                file_count=len(files),
                generated_at=str(manifest.get('generated_at') or '') or None,
                source='storage',
            )
        # Artifact read fine but carries no usable manifest.
        return EvidenceManifestReference(
            exists=bool(recorded_sha), retrievable=False, storage_key=storage_key,
            sha256=recorded_sha, size_bytes=None, media_type=_MANIFEST_MEDIA_TYPE,
            file_count=None, generated_at=None, source='storage',
        )

    # Metadata-only (list projection): a recorded manifest hash means a manifest
    # was built. Retrievability is not re-checked per row for performance; the
    # detail/verify/download paths confirm it against storage.
    file_count = package.get('manifest_file_count')
    try:
        file_count = int(file_count) if file_count is not None else None
    except (TypeError, ValueError):
        file_count = None
    size_bytes = package.get('manifest_size_bytes')
    try:
        size_bytes = int(size_bytes) if size_bytes is not None else None
    except (TypeError, ValueError):
        size_bytes = None
    exists = bool(recorded_sha)
    return EvidenceManifestReference(
        exists=exists, retrievable=exists, storage_key=storage_key,
        sha256=recorded_sha, size_bytes=size_bytes, media_type=_MANIFEST_MEDIA_TYPE,
        file_count=file_count,
        generated_at=str(package.get('manifest_generated_at') or '') or None,
        source='metadata',
    )


def _is_evidence_export_type(package: dict[str, Any]) -> bool:
    return str(package.get('export_type') or '').strip().lower() in EVIDENCE_EXPORT_TYPES


def _has_packaging_metadata(package: dict[str, Any]) -> bool:
    """True when a package was built as a real evidence bundle (so a missing
    manifest is a fault, not a plain legacy export).

    Distinguishes EV-2026-004-style packages (a completeness score / files-hashed
    count / export status were recorded) from bare pre-Screen-9 exports that never
    attempted a manifest.
    """
    if _recorded_manifest_sha256(package):
        return True
    if package.get('completeness_score') is not None or isinstance(package.get('completeness'), dict):
        return True
    if package.get('export_status') or package.get('evidence_source_type'):
        return True
    try:
        if int(package.get('files_hashed') or 0) > 0:
            return True
    except (TypeError, ValueError):
        pass
    return False


# ── Canonical package-state selectors (Screen 9) ──────────────────────────────
# These operate on the plain package dict shape produced by the exports list /
# detail endpoints. Every summary card, table row, agent card, and action menu
# consumes these — the truthfulness rules are defined once, here, and never
# re-derived independently in a React component.

def _package_files_hashed(package: dict[str, Any]) -> int:
    """Number of files that carry a stored SHA-256 for this package.

    Prefers the persisted ``files_hashed`` count; falls back to counting
    manifest file entries that actually have a sha256 when a manifest is loaded.
    """
    try:
        stored = int(package.get('files_hashed') or 0)
    except (TypeError, ValueError):
        stored = 0
    if stored > 0:
        return stored
    files = package.get('files')
    if isinstance(files, list):
        return sum(1 for f in files if isinstance(f, dict) and f.get('sha256'))
    return 0


def _package_has_hashes(package: dict[str, Any]) -> bool:
    """True when the package has a real evidence manifest.

    Keyed on the canonical manifest reference — a recorded manifest SHA-256 (the
    ``integrity_hash``) or a manifest object present in storage. A files-hashed
    count on its own is NOT proof of a manifest: EV-2026-004 carried
    ``files_hashed=8`` while its manifest was never retrievable, which is exactly
    the contradiction this must not reproduce.
    """
    return resolve_manifest_reference(package).exists


def _package_completeness_for_integrity(package: dict[str, Any]) -> dict[str, Any] | None:
    completeness = package.get('completeness')
    if isinstance(completeness, dict):
        return completeness
    score = package.get('completeness_score')
    if score is not None:
        return {'score': score}
    return None


def get_evidence_package_display_state(
    package: dict[str, Any],
    *,
    manifest_reference: EvidenceManifestReference | None = None,
) -> dict[str, Any]:
    """Single source of truth for a package's Screen 9 display state.

    Returns the canonical ``integrity_status``, the hash-column ``hash_state``,
    the real ``files_hashed`` count, and the download/export-ready booleans.

    ``manifest_reference`` is the storage-authoritative reference resolved by the
    detail/verify/download paths (they have already read the artifact). When it
    is omitted the metadata-only reference is used — the list projection cannot
    afford a storage read per row, and confirms retrievability only when a row is
    opened. Either way, manifest existence is resolved in exactly ONE place.
    """
    status = str(package.get('status') or '').lower()
    verification = package.get('verification') if isinstance(package.get('verification'), dict) else None
    superseded = bool(package.get('superseded'))
    ref = manifest_reference if manifest_reference is not None else resolve_manifest_reference(package)
    # "Has a usable manifest" means the manifest is retrievable. At the list level
    # (metadata source) retrievable == exists; the detail level confirms bytes.
    has_manifest = ref.retrievable
    integrity_status = derive_integrity_status(
        job_status=status,
        verification=verification,
        completeness=_package_completeness_for_integrity(package),
        superseded=superseded,
        has_hashes=has_manifest,
    )
    # A completed evidence-type package that was built as a proof bundle but has no
    # retrievable manifest is reported specifically as manifest_missing (never as a
    # plain legacy_export, and never as hash_generated). This is the EV-2026-004
    # state: it must not claim a manifest it cannot produce.
    if (
        integrity_status == INTEGRITY_LEGACY_EXPORT
        and status == 'completed'
        and not superseded
        and _is_evidence_export_type(package)
        and _has_packaging_metadata(package)
    ):
        integrity_status = INTEGRITY_MANIFEST_MISSING

    if has_manifest:
        hash_state = HASH_STATE_PRESENT
    elif status in {'queued', 'pending', 'building', 'running'}:
        hash_state = HASH_STATE_GENERATING
    else:
        hash_state = HASH_STATE_NONE

    storage_available = package.get('storage_available')
    storage_ok = storage_available is not False  # unknown (None) is treated as available
    downloadable = status == 'completed' and storage_ok
    # A completed export can always have its raw artifact downloaded, but only a
    # package with a retrievable manifest is a usable *evidence* artifact
    # (manifest/verify available). Files-hashed is shown only when the manifest is
    # real, so "Files Hashed: 8" can never sit beside a missing manifest.
    is_evidence_artifact = integrity_status not in _NON_EXPORTABLE_INTEGRITY
    is_export_ready = downloadable and integrity_status == INTEGRITY_VERIFIED
    files_hashed = (ref.file_count if ref.file_count is not None else _package_files_hashed(package)) if has_manifest else 0
    return {
        'integrity_status': integrity_status,
        'hash_state': hash_state,
        'files_hashed': files_hashed,
        'has_hashes': has_manifest,
        'manifest_retrievable': ref.retrievable,
        'manifest_reference': ref.as_dict(),
        'is_legacy_export': integrity_status == INTEGRITY_LEGACY_EXPORT,
        'is_manifest_missing': integrity_status == INTEGRITY_MANIFEST_MISSING,
        'is_downloadable': downloadable,
        'is_evidence_artifact': is_evidence_artifact,
        'is_export_ready': is_export_ready,
        'ready_for_verification': downloadable and integrity_status == INTEGRITY_HASH_GENERATED,
    }


def is_evidence_package_export_ready(package: dict[str, Any]) -> bool:
    """A package is fully export-ready only when it has been deterministically
    verified and its artifact is retrievable. Draft/building/failed/integrity-
    failed/legacy/superseded packages are never export-ready. Metadata existing
    is not enough."""
    return bool(get_evidence_package_display_state(package)['is_export_ready'])


def get_evidence_package_integrity_summary(package: dict[str, Any]) -> dict[str, Any]:
    """Compact, truthful integrity summary for a single package."""
    state = get_evidence_package_display_state(package)
    verification = package.get('verification') if isinstance(package.get('verification'), dict) else None
    return {
        'integrity_status': state['integrity_status'],
        'hash_state': state['hash_state'],
        'files_hashed': state['files_hashed'],
        'integrity_hash': package.get('integrity_hash') or None,
        'verified': state['integrity_status'] == INTEGRITY_VERIFIED,
        'verified_at': (verification or {}).get('verified_at') or package.get('verified_at'),
        'is_legacy_export': state['is_legacy_export'],
        'is_export_ready': state['is_export_ready'],
    }


def get_workspace_evidence_metrics(packages: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate canonical evidence metrics for the workspace sidebar.

    Every field is derived from the same per-package display state used by the
    table rows, so the agent card and the table can never disagree. Completeness
    only averages packages that actually have a calculable score; when none do
    the average is ``None`` (the UI shows "Not calculated", never "Unknown").
    """
    total = len(packages)
    verified = 0
    integrity_failures = 0
    files_hashed = 0
    export_ready = 0
    ready_for_verification = 0
    legacy_exports = 0
    manifest_missing = 0
    total_size = 0
    incidents: set[str] = set()
    scores: list[int] = []
    for package in packages:
        state = get_evidence_package_display_state(package)
        if state['integrity_status'] == INTEGRITY_VERIFIED:
            verified += 1
        if state['integrity_status'] == INTEGRITY_INTEGRITY_FAILED:
            integrity_failures += 1
        if state['is_legacy_export']:
            legacy_exports += 1
        if state.get('is_manifest_missing'):
            manifest_missing += 1
        if state['is_export_ready']:
            export_ready += 1
        if state['ready_for_verification']:
            ready_for_verification += 1
        files_hashed += state['files_hashed']
        try:
            total_size += int(package.get('size_bytes') or 0)
        except (TypeError, ValueError):
            pass
        incident_id = package.get('incident_id')
        if incident_id:
            incidents.add(str(incident_id))
        score = package.get('completeness_score')
        if isinstance(score, (int, float)):
            scores.append(int(score))
    return {
        'total_packages': total,
        'total_size_bytes': total_size,
        'verified': verified,
        'integrity_failures': integrity_failures,
        'incidents_covered': len(incidents),
        'files_hashed': files_hashed,
        'export_ready': export_ready,
        'ready_for_verification': ready_for_verification,
        'legacy_exports': legacy_exports,
        'manifest_missing': manifest_missing,
        'average_completeness': round(sum(scores) / len(scores)) if scores else None,
    }


def get_package_allowed_actions(
    package: dict[str, Any],
    *,
    can_export: bool,
    manifest_reference: EvidenceManifestReference | None = None,
) -> dict[str, bool]:
    """Which Screen 9 actions are valid for this package and this user.

    ``can_export`` is the caller's ``evidence.export`` permission. Verify Integrity
    and Download Manifest are returned only when the canonical manifest resolver
    reports the manifest is retrievable — never inferred independently. A legacy
    export or a manifest_missing package (no retrievable manifest) has both
    disabled regardless of permission, so the frontend never guesses.

    ``generate_manifest`` is the recovery action: it is offered ONLY for a
    completed, storage-available evidence package that is currently
    manifest_missing (built as a proof bundle but with no retrievable manifest —
    the EV-2026-004 state) and only to a user with export permission. It disappears
    the moment a retrievable manifest exists, so Verify Integrity is never enabled
    before a real manifest and Generate Manifest is never offered once one exists.
    """
    state = get_evidence_package_display_state(package, manifest_reference=manifest_reference)
    has_manifest = bool(state['manifest_retrievable'])
    completed = str(package.get('status') or '').lower() == 'completed'
    downloadable = state['is_downloadable']
    return {
        'view': True,
        'download': completed and downloadable and can_export,
        'download_manifest': completed and downloadable and has_manifest and can_export,
        'verify': completed and downloadable and has_manifest and can_export,
        'copy_hash': bool(_recorded_manifest_sha256(package)),
        # Recovery: only a manifest_missing evidence package (no retrievable
        # manifest) whose bytes are readable from storage can have a manifest
        # generated in place.
        'generate_manifest': (
            completed
            and downloadable
            and not has_manifest
            and bool(state.get('is_manifest_missing'))
            and can_export
        ),
    }
