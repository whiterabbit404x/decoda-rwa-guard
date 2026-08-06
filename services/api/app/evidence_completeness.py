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
    INTEGRITY_SUPERSEDED,
})


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
        present = completeness.get('present_count') or 0
        required = completeness.get('required_count') or 0
        core_missing = any(
            code in {'incident_identity', 'original_alert', 'detection_provenance', 'file_hashes', 'manifest_hash'}
            for code in (completeness.get('missing_codes') or [])
        )
        if core_missing or (required and present < required and (completeness.get('score') or 0) < 60):
            return INTEGRITY_NEEDS_EVIDENCE

    # A completed export with no stored hashes is a legacy/non-package export.
    resolved_has_hashes = has_hashes if has_hashes is not None else _completeness_indicates_hashes(completeness)
    if not resolved_has_hashes:
        return INTEGRITY_LEGACY_EXPORT
    return INTEGRITY_HASH_GENERATED


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
    """True when the package carries a real manifest/integrity hash or file hashes."""
    if str(package.get('integrity_hash') or '').strip():
        return True
    return _package_files_hashed(package) > 0


def _package_completeness_for_integrity(package: dict[str, Any]) -> dict[str, Any] | None:
    completeness = package.get('completeness')
    if isinstance(completeness, dict):
        return completeness
    score = package.get('completeness_score')
    if score is not None:
        return {'score': score}
    return None


def get_evidence_package_display_state(package: dict[str, Any]) -> dict[str, Any]:
    """Single source of truth for a package's Screen 9 display state.

    Returns the canonical ``integrity_status``, the hash-column ``hash_state``,
    the real ``files_hashed`` count, and the download/export-ready booleans.
    """
    status = str(package.get('status') or '').lower()
    verification = package.get('verification') if isinstance(package.get('verification'), dict) else None
    superseded = bool(package.get('superseded'))
    has_hashes = _package_has_hashes(package)
    integrity_status = derive_integrity_status(
        job_status=status,
        verification=verification,
        completeness=_package_completeness_for_integrity(package),
        superseded=superseded,
        has_hashes=has_hashes,
    )
    if has_hashes:
        hash_state = HASH_STATE_PRESENT
    elif status in {'queued', 'pending', 'building', 'running'}:
        hash_state = HASH_STATE_GENERATING
    else:
        hash_state = HASH_STATE_NONE

    storage_available = package.get('storage_available')
    storage_ok = storage_available is not False  # unknown (None) is treated as available
    downloadable = status == 'completed' and storage_ok
    # A completed export can always have its raw artifact downloaded, but only a
    # hashed package is a usable *evidence* artifact (manifest/verify available).
    is_evidence_artifact = integrity_status not in _NON_EXPORTABLE_INTEGRITY
    is_export_ready = downloadable and integrity_status == INTEGRITY_VERIFIED
    return {
        'integrity_status': integrity_status,
        'hash_state': hash_state,
        'files_hashed': _package_files_hashed(package),
        'has_hashes': has_hashes,
        'is_legacy_export': integrity_status == INTEGRITY_LEGACY_EXPORT,
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
        'average_completeness': round(sum(scores) / len(scores)) if scores else None,
    }


def get_package_allowed_actions(
    package: dict[str, Any],
    *,
    can_export: bool,
) -> dict[str, bool]:
    """Which Screen 9 actions are valid for this package and this user.

    ``can_export`` is the caller's ``evidence.export`` permission. Actions that
    are cryptographically meaningless for the package (verify/manifest on a
    legacy export with no manifest) are disabled regardless of permission.
    """
    state = get_evidence_package_display_state(package)
    has_manifest = state['has_hashes'] and not state['is_legacy_export']
    completed = str(package.get('status') or '').lower() == 'completed'
    downloadable = state['is_downloadable']
    return {
        'view': True,
        'download': completed and downloadable and can_export,
        'download_manifest': completed and downloadable and has_manifest and can_export,
        'verify': completed and downloadable and has_manifest and can_export,
        'copy_hash': bool(str(package.get('integrity_hash') or '').strip()),
    }
