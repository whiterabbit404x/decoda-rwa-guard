"""Structured, deterministic evidence ZIP archive (Screen 9 — Download Package).

What goes in
------------
    EV-2026-017/
        manifest.json                 the exact sealed manifest
        manifest.sig                  the detached signature document (only when sealed)
        verification.json             the backend verification result for this package
        artifacts/
            on-chain/…                the ORIGINAL immutable evidence artifacts,
            operational/…             byte-for-byte the canonical JSON that was
            policy/…                  hashed into the manifest — never a UI summary
            human-actions/…
            package/…
        reports/
            investigation.md          human-readable investigation report
        verification/
            README.txt                how to re-verify this package offline
            signing-key.json          PUBLIC signer metadata (identifiers only)

What must never go in
---------------------
Private signing material, API secrets, database credentials, storage
credentials and internal tokens. :func:`assert_no_secret_material` scans every
emitted byte for the live signing secret and for a denylist of credential
markers, and the build FAILS CLOSED if anything matches — a leak is a defect,
never a warning.

Byte-for-byte artifact fidelity
-------------------------------
Each artifact is written as ``canonical_json(value)`` — the SAME serializer
whose output was SHA-256'd into the manifest. So the bytes in the ZIP hash to
exactly the digests ``manifest.json`` records, and an auditor can re-verify the
archive offline with nothing but ``sha256sum``.

Determinism
-----------
Entries are emitted in sorted path order with a fixed ZIP timestamp
(1980-01-01) and fixed compression, so the same package always produces the
same archive bytes. Nothing in the archive depends on wall-clock time except
the verification result the caller passes in.

Path safety (ZIP Slip)
----------------------
Every entry name is built from a validated logical manifest path through
:func:`safe_archive_segment`, which rejects absolute paths, drive letters,
``..`` traversal, backslashes, NUL and control characters. A client-supplied
storage path is never used to name an entry.
"""
from __future__ import annotations

import io
import json
import posixpath
import re
import zipfile
from typing import Any

from services.api.app.evidence_signing import canonical_json

#: Fixed archive timestamp so the same package always produces the same bytes.
_FIXED_ZIP_DATE = (1980, 1, 1, 0, 0, 0)

#: Evidence provenance domains, matching Screen 7's canonical set.
DOMAIN_ON_CHAIN = 'on-chain'
DOMAIN_OPERATIONAL = 'operational'
DOMAIN_POLICY = 'policy'
DOMAIN_HUMAN_ACTIONS = 'human-actions'
DOMAIN_PACKAGE = 'package'

#: Deterministic map from a package's logical artifact path to its provenance
#: domain folder. Mirrors ``incident_forensics.ARTIFACT_TYPE_DOMAINS`` at the
#: package-file level: chain observations, business/system records, policy
#: decisions and human actions. An unmapped file lands in ``package/`` rather
#: than being guessed into a domain it may not belong to.
ARCHIVE_DOMAIN_BY_FILE: dict[str, str] = {
    # ON_CHAIN — what the chain itself recorded.
    'evidence.json': DOMAIN_ON_CHAIN,
    'detection_metrics.json': DOMAIN_ON_CHAIN,
    'telemetry_events.json': DOMAIN_ON_CHAIN,
    # OPERATIONAL — what the business/monitoring systems of record said.
    'alerts.json': DOMAIN_OPERATIONAL,
    'detections.json': DOMAIN_OPERATIONAL,
    'incidents.json': DOMAIN_OPERATIONAL,
    'incident.json': DOMAIN_OPERATIONAL,
    'linked_alerts.json': DOMAIN_OPERATIONAL,
    # POLICY — what the deterministic policy engine decided.
    'policy_evaluations.json': DOMAIN_POLICY,
    'policy_snapshot.json': DOMAIN_POLICY,
    'enforcement_actions.json': DOMAIN_POLICY,
    # HUMAN_ACTIONS — what people did.
    'response_actions.json': DOMAIN_HUMAN_ACTIONS,
    'audit_log.json': DOMAIN_HUMAN_ACTIONS,
    'investigation_timeline.json': DOMAIN_HUMAN_ACTIONS,
    'timeline.json': DOMAIN_HUMAN_ACTIONS,
    # Package-level descriptive records (not evidence in a provenance domain).
    'summary.json': DOMAIN_PACKAGE,
    'metadata.json': DOMAIN_PACKAGE,
    'report.json': DOMAIN_PACKAGE,
}

#: Credential markers that must never appear in an exported archive.
_SECRET_MARKERS: tuple[str, ...] = (
    'EXPORT_SIGNING_SECRET',
    'EVIDENCE_SIGNING_SECRET',
    'AUTH_TOKEN_SECRET',
    'SECRET_ENCRYPTION_KEY',
    'AWS_SECRET_ACCESS_KEY',
    'AWS_ACCESS_KEY_ID',
    'DATABASE_URL',
    'POSTGRES_PASSWORD',
    'EXPORT_S3_SECRET',
    'PRIVATE KEY',
    'BEGIN RSA',
    'BEGIN EC PRIVATE',
)

_UNSAFE_SEGMENT = re.compile(r'[\x00-\x1f\x7f\\]')


class ArchiveSafetyError(ValueError):
    """An archive entry name or payload violated a safety rule. Fails closed."""

    def __init__(self, *, reason: str, detail: str | None = None) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(reason if not detail else f'{reason}:{detail}')


def safe_archive_segment(value: str) -> str:
    """Validate one path segment for use inside the archive.

    Rejects absolute paths, drive letters, traversal, backslashes, NUL and other
    control characters. Returns the segment unchanged when it is safe — the name
    is never silently rewritten, because a rewritten evidence filename would no
    longer match the manifest path it must be verifiable against.
    """
    segment = str(value or '')
    if not segment:
        raise ArchiveSafetyError(reason='empty_path_segment')
    if _UNSAFE_SEGMENT.search(segment):
        raise ArchiveSafetyError(reason='unsafe_characters_in_path', detail=segment)
    if segment.startswith('/') or segment.startswith('~'):
        raise ArchiveSafetyError(reason='absolute_path_not_allowed', detail=segment)
    if re.match(r'^[A-Za-z]:', segment):
        raise ArchiveSafetyError(reason='drive_letter_not_allowed', detail=segment)
    parts = segment.split('/')
    if any(part in {'', '.', '..'} for part in parts):
        raise ArchiveSafetyError(reason='path_traversal_not_allowed', detail=segment)
    return segment


def archive_entry_name(root: str, *segments: str) -> str:
    """Build a validated, normalized POSIX entry name rooted at ``root``.

    The result is re-checked after normalization so no combination of validated
    segments can escape the archive root.
    """
    parts = [safe_archive_segment(root), *(safe_archive_segment(part) for part in segments)]
    name = posixpath.normpath(posixpath.join(*parts))
    if name.startswith('/') or name.startswith('..') or '/../' in name:
        raise ArchiveSafetyError(reason='path_traversal_not_allowed', detail=name)
    return name


def artifact_domain(logical_path: str) -> str:
    """The provenance-domain folder for one logical artifact path."""
    return ARCHIVE_DOMAIN_BY_FILE.get(str(logical_path or ''), DOMAIN_PACKAGE)


def assert_no_secret_material(entries: dict[str, bytes], *, extra_denylist: tuple[bytes, ...] = ()) -> None:
    """Fail CLOSED if any archive byte carries credential material.

    ``extra_denylist`` is where the caller passes the LIVE signing secret bytes,
    so an accidental inclusion is caught by value and not only by variable name.
    """
    denied: list[bytes] = [marker.encode('utf-8') for marker in _SECRET_MARKERS]
    denied.extend(value for value in extra_denylist if value)
    for name, payload in entries.items():
        for marker in denied:
            if marker and marker in payload:
                raise ArchiveSafetyError(reason='secret_material_in_archive', detail=name)


def render_investigation_report_markdown(
    *,
    package_id: str,
    package_number: str,
    manifest: dict[str, Any],
    summary: dict[str, Any] | None,
    verification: dict[str, Any] | None,
    signer: dict[str, Any] | None,
    generated_at: str,
) -> str:
    """Human-readable investigation report over the package's own sealed records.

    Every value comes from the manifest, the packaged summary or the backend
    verification result. Nothing is invented, and a fact the package does not
    carry is printed as ``Not recorded`` rather than omitted or guessed. This is
    a READING of the evidence — it never replaces the original artifacts, which
    ship alongside it under ``artifacts/``.
    """
    summary = summary if isinstance(summary, dict) else {}
    verification = verification if isinstance(verification, dict) else {}
    signer = signer if isinstance(signer, dict) else {}
    policy = manifest.get('policy_snapshot') if isinstance(manifest.get('policy_snapshot'), dict) else {}

    def value(raw: Any) -> str:
        text = str(raw).strip() if raw is not None else ''
        return text or 'Not recorded'

    lines: list[str] = [
        f'# Investigation Report — {package_number}',
        '',
        'This report is a human-readable reading of the sealed evidence package.',
        'It does NOT replace the original evidence artifacts, which are included',
        'in this archive under `artifacts/` and are the authoritative record.',
        '',
        '## Package',
        '',
        f'- Package ID: {value(package_id)}',
        f'- Package number: {value(package_number)}',
        f'- Export type: {value(manifest.get("export_type"))}',
        f'- Manifest schema: {value(manifest.get("schema_version") or manifest.get("manifest_version"))}',
        f'- Generated at: {value(manifest.get("generated_at"))}',
        f'- Report generated at: {value(generated_at)}',
        '',
        '## Incident',
        '',
        f'- Incident ID: {value(summary.get("incident_id") or manifest.get("source_resource_id"))}',
        f'- Canonical event ID: {value(summary.get("detection_id") or summary.get("canonical_event_id"))}',
        f'- Alert ID: {value(summary.get("alert_id"))}',
        f'- Asset ID: {value(summary.get("asset_id"))}',
        f'- Target ID: {value(summary.get("target_id"))}',
        f'- Export status: {value(summary.get("export_status"))}',
        f'- Evidence source: {value(summary.get("evidence_source_type"))}',
        '',
        '## Policy',
        '',
        f'- Policy key: {value(policy.get("policy_key"))}',
        f'- Policy version: {value(policy.get("policy_version"))}',
        f'- Decision: {value(policy.get("decision"))}',
        f'- Evaluated at: {value(policy.get("evaluated_at"))}',
        f'- Snapshot source: {value(policy.get("source"))}',
        '',
        '## Response',
        '',
        f'- Response action ID: {value(summary.get("response_action_id"))}',
        f'- Recorded response actions: {value(summary.get("response_action_count"))}',
        '',
        '## Evidence',
        '',
        f'- Artifact count: {value(manifest.get("artifact_count") or len(manifest.get("files") or []))}',
        f'- Hash algorithm: {value(manifest.get("hash_algorithm"))}',
        f'- Merkle root: {value(manifest.get("merkle_root"))}',
        f'- Merkle scheme: {value(manifest.get("merkle_scheme"))}',
        f'- Manifest SHA-256: {value(manifest.get("manifest_sha256"))}',
        '',
        '### Artifacts',
        '',
        '| Domain | Artifact | SHA-256 | Bytes |',
        '| --- | --- | --- | --- |',
    ]
    for entry in sorted(manifest.get('files') or [], key=lambda item: str(item.get('path') or '')):
        if not isinstance(entry, dict):
            continue
        path = str(entry.get('path') or '')
        lines.append(
            f'| {artifact_domain(path)} | {path} | {value(entry.get("sha256"))} | {value(entry.get("size_bytes"))} |'
        )

    lines.extend([
        '',
        '## Signing',
        '',
        f'- Provider: {value(signer.get("provider"))}',
        f'- Algorithm: {value(signer.get("algorithm"))}',
        f'- Key ID: {value(signer.get("key_id"))}',
        f'- Key version: {value(signer.get("key_version"))}',
        f'- Assurance: {value(signer.get("assurance_label") or signer.get("assurance"))}',
        f'- Hardware-backed (HSM/KMS): {"yes" if signer.get("hardware_backed") else "no"}',
    ])
    if signer.get('warning'):
        lines.append(f'- Warning: {signer["warning"]}')

    lines.extend([
        '',
        '## Verification',
        '',
        f'- Status: {value(verification.get("status"))}',
        f'- Verified at: {value(verification.get("verified_at"))}',
        '',
    ])
    for check in verification.get('checks') or []:
        if not isinstance(check, dict):
            continue
        detail = f' — {check["detail"]}' if check.get('detail') else ''
        lines.append(f'- [{str(check.get("status", "")).upper()}] {check.get("label")}{detail}')
    if not verification.get('checks'):
        lines.append('- No verification has been recorded for this package.')

    lines.extend(['', '---', '', 'Generated by Decoda RWA Guard.', ''])
    return '\n'.join(lines)


_README = """Decoda RWA Guard — evidence package verification
================================================

This archive is a sealed, tamper-evident evidence package.

Contents
--------
  manifest.json      The sealed manifest. Lists every artifact with its SHA-256
                     and byte length, the artifact count, the hash algorithm and
                     (schema 2.0+) the Merkle root over the artifact set.
  manifest.sig       Detached signature document over the canonical manifest
                     bytes. Present only when the package was sealed.
  verification.json  The backend verification result recorded for this package.
  artifacts/         The original immutable evidence artifacts, grouped by
                     provenance domain. These bytes are what manifest.json hashes.
  reports/           Human-readable investigation report. NOT a replacement for
                     the artifacts.
  verification/      This file and the PUBLIC signer identifiers.

Re-verifying offline
--------------------
1. Artifact hashes
       Each artifact file in artifacts/ is the exact canonical-JSON byte stream
       that was hashed. For an artifact listed in manifest.json as {"path": P,
       "sha256": H}, find it under artifacts/<domain>/<P> and check:

           sha256sum artifacts/<domain>/<P>      ->  H

2. Manifest hash
       Remove the "manifest_sha256" key from manifest.json, re-serialize the
       remaining object as canonical JSON (UTF-8, keys sorted ascending, separators
       "," and ":", ensure_ascii=true, no trailing newline) and SHA-256 it. The
       result must equal manifest.json's own "manifest_sha256".

3. Merkle root (manifest schema 2.0 and above)
       Scheme: decoda-merkle-v1, SHA-256.
         leaf   = SHA256(0x00 || utf8(path) || 0x1F || ascii(lowercase sha256 hex))
         node   = SHA256(0x01 || left_digest || right_digest)
         order  = leaves sorted ascending by the UTF-8 bytes of "path"
         odd    = a level's unpaired last node is PROMOTED unchanged (never duplicated)
         empty  = no root at all
       The root must equal manifest.json's "merkle_root".

4. Manifest signature
       manifest.sig covers the canonical JSON of the COMPLETE manifest object,
       including its own "manifest_sha256" field. The algorithm and key
       identifiers are recorded in manifest.sig and in verification/signing-key.json.
       Verification requires the corresponding key, which is held by the issuer
       and is deliberately NOT part of this archive.

Signing assurance
-----------------
verification/signing-key.json states the signing provider, algorithm and whether
the signature is hardware-backed (HSM/KMS). Read it rather than assuming: this
build seals with a shared-secret HMAC held by the application, which is
tamper-evident to a key holder but is not a hardware-custodied, non-repudiable
signature.

No credentials
--------------
This archive intentionally contains NO private signing keys, API secrets,
database credentials or storage credentials.
"""


def build_evidence_archive(
    *,
    package_id: str,
    package_number: str,
    manifest: dict[str, Any],
    seal: dict[str, Any] | None,
    file_values: dict[str, Any],
    verification: dict[str, Any] | None,
    signer: dict[str, Any] | None,
    summary: dict[str, Any] | None,
    generated_at: str,
    investigation_report: str | None = None,
    secret_denylist: tuple[bytes, ...] = (),
) -> bytes:
    """Assemble the deterministic evidence ZIP and return its bytes.

    ``file_values`` are the artifact values read back out of the stored package
    — the SAME values verification hashes — so the archive can never contain a
    freshly re-queried version of the evidence.
    """
    root = safe_archive_segment(str(package_number or package_id or 'evidence-package').strip())
    entries: dict[str, bytes] = {}

    # -- manifest + detached signature ------------------------------------
    entries[archive_entry_name(root, 'manifest.json')] = json.dumps(
        manifest, indent=2, sort_keys=True,
    ).encode('utf-8')
    if isinstance(seal, dict) and str(seal.get('signature') or '').strip():
        entries[archive_entry_name(root, 'manifest.sig')] = json.dumps(
            seal, indent=2, sort_keys=True,
        ).encode('utf-8')

    # -- verification result ----------------------------------------------
    entries[archive_entry_name(root, 'verification.json')] = json.dumps(
        verification if isinstance(verification, dict) else {
            'status': None,
            'note': 'No verification has been run for this package.',
        },
        indent=2, sort_keys=True, default=str,
    ).encode('utf-8')

    # -- original artifacts, byte-identical to what the manifest hashes ----
    manifest_paths = [
        str(entry.get('path') or '')
        for entry in (manifest.get('files') or [])
        if isinstance(entry, dict) and str(entry.get('path') or '')
    ]
    for logical_path in sorted(set(manifest_paths)):
        if logical_path not in file_values:
            # A manifest-listed artifact that is not in the package is reported by
            # verification as INCOMPLETE_PACKAGE. It is never silently substituted.
            continue
        entries[archive_entry_name(root, 'artifacts', artifact_domain(logical_path), logical_path)] = (
            canonical_json(file_values[logical_path], path=logical_path)
        )

    # -- human-readable report ---------------------------------------------
    report = investigation_report if investigation_report is not None else render_investigation_report_markdown(
        package_id=package_id, package_number=package_number, manifest=manifest,
        summary=summary, verification=verification, signer=signer, generated_at=generated_at,
    )
    entries[archive_entry_name(root, 'reports', 'investigation.md')] = report.encode('utf-8')

    # -- verification helpers (PUBLIC identifiers only) ---------------------
    entries[archive_entry_name(root, 'verification', 'README.txt')] = _README.encode('utf-8')
    public_signer = {
        key: (signer or {}).get(key)
        for key in ('provider', 'algorithm', 'key_id', 'key_version', 'hardware_backed',
                    'assurance', 'assurance_label', 'key_custody', 'production_grade', 'warning')
    }
    public_signer['contains_key_material'] = False
    public_signer['note'] = (
        'Public signer identifiers only. The verification key is held by the issuer and is '
        'deliberately not included in this archive.'
    )
    entries[archive_entry_name(root, 'verification', 'signing-key.json')] = json.dumps(
        public_signer, indent=2, sort_keys=True, default=str,
    ).encode('utf-8')

    # -- fail closed on any credential material ----------------------------
    assert_no_secret_material(entries, extra_denylist=secret_denylist)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(entries):
            info = zipfile.ZipInfo(filename=name, date_time=_FIXED_ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, entries[name])
    return buffer.getvalue()


def archive_contents_listing(
    *,
    package_number: str,
    manifest: dict[str, Any],
    seal: dict[str, Any] | None,
    file_values: dict[str, Any],
    verification_available: bool,
    pdf_available: bool = False,
) -> list[dict[str, Any]]:
    """Describe what the archive WILL contain, for the Package Contents panel.

    Each entry reports ``available`` truthfully. A file that is not produced —
    ``manifest.sig`` for an unsealed package, ``investigation.pdf`` where PDF
    rendering is not configured — is returned with ``available: false`` and a
    reason, so the UI can show it as unavailable rather than as present.
    """
    manifest_paths = {
        str(entry.get('path') or '')
        for entry in (manifest.get('files') or [])
        if isinstance(entry, dict) and str(entry.get('path') or '')
    }
    present_artifacts = sorted(path for path in manifest_paths if path in file_values)
    sealed = isinstance(seal, dict) and bool(str(seal.get('signature') or '').strip())
    return [
        {
            'key': 'artifacts',
            'label': 'Artifacts',
            'kind': 'directory',
            'path': f'{package_number}/artifacts/',
            'count': len(present_artifacts),
            'declared_count': len(manifest_paths),
            'available': bool(present_artifacts),
            'unavailable_reason': None if present_artifacts else 'No packaged artifacts are retrievable.',
            'items': present_artifacts,
        },
        {
            'key': 'manifest',
            'label': 'Manifest',
            'kind': 'file',
            'path': f'{package_number}/manifest.json',
            'available': bool(manifest.get('files')),
            'unavailable_reason': None if manifest.get('files') else 'This package has no retrievable manifest.',
        },
        {
            'key': 'signature',
            'label': 'Signature',
            'kind': 'file',
            'path': f'{package_number}/manifest.sig',
            'available': sealed,
            'unavailable_reason': None if sealed else 'This package was not sealed with a manifest signature.',
        },
        {
            'key': 'report',
            'label': 'Report',
            'kind': 'file',
            'path': f'{package_number}/reports/investigation.md',
            'available': True,
            'unavailable_reason': None,
            'media_type': 'text/markdown',
        },
        {
            'key': 'report_pdf',
            'label': 'Report (PDF)',
            'kind': 'file',
            'path': f'{package_number}/reports/investigation.pdf',
            'available': bool(pdf_available),
            'unavailable_reason': (
                None if pdf_available
                else 'PDF rendering is not configured in this deployment. The Markdown report is included instead.'
            ),
            'media_type': 'application/pdf',
        },
        {
            'key': 'verification',
            'label': 'Verification',
            'kind': 'file',
            'path': f'{package_number}/verification.json',
            'available': bool(verification_available),
            'unavailable_reason': None if verification_available else 'This package has not been verified yet.',
        },
    ]
