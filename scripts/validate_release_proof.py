#!/usr/bin/env python3
"""
Validate release proof artifacts for correctness and fail-closed semantics.

Checks:
- All required artifact files exist
- Schema versions are correct
- Fail-closed semantics: unknown is never treated as pass
- broad_paid_saas_ready cannot be true unless all gates pass
- No secret-like values in artifacts
- Required fields are present
- Manifest SHA256 integrity matches actual files
- Test report summary is not faked as pass
- Artifact paths are relative and under artifacts/
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.security.release_security import validate_security_proof

from scripts.release_proof_context import (
    DEFAULT_MAX_EVIDENCE_AGE_SECONDS,
    SHA_RE,
    git_sha,
    parse_timestamp,
)

SECRET_MARKERS = {
    'secret', 'key', 'password', 'token', 'credential', 'api_key',
    'sk_', 'pk_', 'whsec_', 'rk_', 'bearer ', 'basic ',
}


def _has_secret_like_value(obj: Any, path: str = '') -> list[str]:
    """Scan for secret-like values in JSON."""
    secrets = []

    if isinstance(obj, dict):
        for key, value in obj.items():
            new_path = f'{path}.{key}' if path else key
            if any(marker in key.lower() for marker in SECRET_MARKERS):
                if isinstance(value, str) and value and not value.lower() in {'missing', 'unknown', 'n/a'}:
                    secrets.append(f'{new_path} contains secret marker and value')
            secrets.extend(_has_secret_like_value(value, new_path))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            new_path = f'{path}[{i}]'
            secrets.extend(_has_secret_like_value(item, new_path))

    return secrets


def _load_json(path: Path) -> dict[str, Any] | None:
    """Load JSON file, return None if missing."""
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        print(f'ERROR: Failed to load {path}: {e}')
        return None


def validate_ci_required_gates(path: Path) -> tuple[bool, list[str]]:
    """Validate ci-required-gates.json."""
    issues = []

    gates = _load_json(path)
    if gates is None:
        issues.append(f'{path.relative_to(REPO_ROOT)} not found')
        return False, issues

    # Check schema version
    if gates.get('schema_version') != 1:
        issues.append('ci-required-gates: invalid schema_version')

    # Check required fields
    required_fields = ['generated_at', 'commit_sha', 'branch', 'release_channel', 'overall_status', 'required_gates', 'blockers', 'warnings']
    for field in required_fields:
        if field not in gates:
            issues.append(f'ci-required-gates: missing required field {field}')

    # Check overall_status is only pass when all gates pass
    overall_status = gates.get('overall_status')
    required_gates = gates.get('required_gates', {})

    if overall_status == 'pass':
        for gate_name, gate_data in required_gates.items():
            if isinstance(gate_data, dict):
                gate_status = gate_data.get('status')
                if gate_status not in {'pass', 'not_run'}:  # not_run is allowed for local mode
                    issues.append(f'ci-required-gates: overall_status=pass but {gate_name}={gate_status}')

    # broad_paid_launch_ready is only valid in staging/production mode
    if gates.get('broad_paid_launch_ready') is True:
        channel = gates.get('release_channel', '').lower()
        if channel not in ('staging', 'production'):
            issues.append(
                'ci-required-gates: broad_paid_launch_ready must not be true outside staging/production mode'
            )

    # Check for secrets
    secrets = _has_secret_like_value(gates)
    for secret in secrets:
        issues.append(f'ci-required-gates: {secret}')

    return len(issues) == 0, issues


def validate_release_proof(path: Path) -> tuple[bool, list[str]]:
    """Validate release-proof summary.json."""
    issues = []

    proof = _load_json(path)
    if proof is None:
        issues.append(f'{path.relative_to(REPO_ROOT)} not found')
        return False, issues

    # Check schema version
    if proof.get('schema_version') != 1:
        issues.append('release-proof: invalid schema_version')

    # Check required fields
    required_fields = ['generated_at', 'release_status', 'release_channel', 'commit_sha', 'branch', 'ci_required_gates_ready', 'launch_proof_ready', 'security_proof_ready', 'paid_launch_ready', 'blockers', 'warnings']
    for field in required_fields:
        if field not in proof:
            issues.append(f'release-proof: missing required field {field}')

    # Check fail-closed semantics
    release_status = proof.get('release_status')
    ci_gates_ready = proof.get('ci_required_gates_ready')
    launch_proof_ready = proof.get('launch_proof_ready')
    security_proof_ready = proof.get('security_proof_ready')

    if release_status == 'pass' and (
        not ci_gates_ready or not launch_proof_ready or not security_proof_ready
    ):
        issues.append(
            'release-proof: release_status=pass but gates not ready '
            f'(ci={ci_gates_ready}, launch={launch_proof_ready}, security={security_proof_ready})'
        )

    # paid_launch_ready is only valid in staging/production mode
    if proof.get('paid_launch_ready') is True:
        channel = proof.get('release_channel', '').lower()
        if channel not in ('staging', 'production'):
            issues.append(
                'release-proof: paid_launch_ready must not be true outside staging/production mode'
            )

    # Check for secrets
    secrets = _has_secret_like_value(proof)
    for secret in secrets:
        issues.append(f'release-proof: {secret}')

    return len(issues) == 0, issues


def validate_launch_proof(path: Path) -> tuple[bool, list[str]]:
    """Validate launch-proof summary.json."""
    issues = []

    proof = _load_json(path)
    if proof is None:
        issues.append(f'{path.relative_to(REPO_ROOT)} not found')
        return False, issues

    # Check schema version
    if proof.get('schema_version') != 1:
        issues.append('launch-proof: invalid schema_version')

    # Check required fields
    required_fields = ['generated_at', 'launch_mode', 'pilot_ready', 'paid_launch_ready', 'controlled_pilot_ready', 'broad_paid_saas_ready', 'readiness', 'blockers', 'warnings']
    for field in required_fields:
        if field not in proof:
            issues.append(f'launch-proof: missing required field {field}')

    # Check fail-closed semantics
    broad_paid_saas_ready = proof.get('broad_paid_saas_ready')
    readiness = proof.get('readiness', {})

    if broad_paid_saas_ready is True:
        # All readiness gates must be true
        required_readiness = [
            'billing_ready', 'billing_webhook_ready', 'email_ready',
            'provider_ready', 'live_evidence_ready', 'ci_required_gates_ready'
        ]
        for gate in required_readiness:
            if not readiness.get(gate):
                issues.append(f'launch-proof: broad_paid_saas_ready=true but {gate}={readiness.get(gate)}')

    # Check pilot_ready vs broad_paid_saas_ready distinction
    pilot_ready = proof.get('pilot_ready')
    if pilot_ready and broad_paid_saas_ready:
        if not readiness.get('live_evidence_ready'):
            issues.append('launch-proof: pilot_ready=true but missing live evidence')

    # Check paid_launch_ready is only true in staging/production proof mode
    if proof.get('paid_launch_ready') is True:
        proof_mode = str(proof.get('proof_mode') or '').strip().lower()
        if proof_mode not in ('staging', 'production'):
            issues.append(
                f'launch-proof: paid_launch_ready=true requires proof_mode=staging/production, '
                f'got proof_mode={proof_mode!r}'
            )

    # Check for secrets
    secrets = _has_secret_like_value(proof)
    for secret in secrets:
        issues.append(f'launch-proof: {secret}')

    return len(issues) == 0, issues


def _compute_sha256(path: Path) -> str:
    """Compute SHA256 of file contents."""
    sha256_hash = hashlib.sha256()
    try:
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(4096), b''):
                sha256_hash.update(chunk)
        return sha256_hash.hexdigest()
    except Exception:
        return 'unknown'


def validate_manifest(path: Path) -> tuple[bool, list[str]]:
    """Validate artifact manifest.json."""
    issues = []

    manifest = _load_json(path)
    if manifest is None:
        issues.append(f'{path.relative_to(REPO_ROOT)} not found')
        return False, issues

    # Check schema version
    if manifest.get('schema_version') != 1:
        issues.append('manifest: invalid schema_version')

    # Check required fields
    required_fields = ['generated_at', 'release_channel', 'commit_sha', 'branch', 'files', 'overall_status', 'blockers', 'warnings']
    for field in required_fields:
        if field not in manifest:
            issues.append(f'manifest: missing required field {field}')

    # Validate files array
    files = manifest.get('files', [])
    for file_entry in files:
        if not isinstance(file_entry, dict):
            issues.append('manifest: file entry is not a dict')
            continue

        # Check file entry required fields
        file_path = file_entry.get('path')
        if not file_path:
            issues.append('manifest: file entry missing path')
            continue

        # Check path is relative and under artifacts/
        if file_path.startswith('/') or file_path.startswith('..'):
            issues.append(f'manifest: file path must be relative and under artifacts/: {file_path}')
        if not file_path.startswith('artifacts/'):
            issues.append(f'manifest: file path must be under artifacts/: {file_path}')

        # If file is marked as required and present, verify SHA256
        if file_entry.get('required') and file_entry.get('status') == 'present':
            full_path = REPO_ROOT / file_path
            if full_path.exists():
                actual_sha256 = _compute_sha256(full_path)
                manifest_sha256 = file_entry.get('sha256')
                if manifest_sha256 != 'missing' and actual_sha256 != manifest_sha256:
                    issues.append(f'manifest: SHA256 mismatch for {file_path} (manifest={manifest_sha256}, actual={actual_sha256})')
            else:
                if file_entry.get('required'):
                    issues.append(f'manifest: required file not found: {file_path}')

    # Check overall_status
    overall_status = manifest.get('overall_status')
    if overall_status == 'pass' and manifest.get('blockers'):
        issues.append('manifest: overall_status=pass but blockers present')

    # Check for secrets
    secrets = _has_secret_like_value(manifest)
    for secret in secrets:
        issues.append(f'manifest: {secret}')

    return len(issues) == 0, issues


def validate_test_report_summary(path: Path) -> tuple[bool, list[str]]:
    """Validate test-report-summary.json."""
    issues = []

    report = _load_json(path)
    if report is None:
        issues.append(f'{path.relative_to(REPO_ROOT)} not found')
        return False, issues

    # Check schema version
    if report.get('schema_version') != 1:
        issues.append('test-report-summary: invalid schema_version')

    # Check required fields
    required_fields = ['generated_at', 'release_channel', 'commit_sha', 'branch', 'test_suites', 'overall_status', 'blockers', 'warnings']
    for field in required_fields:
        if field not in report:
            issues.append(f'test-report-summary: missing required field {field}')

    # Check overall_status
    overall_status = report.get('overall_status')
    if overall_status not in {'pass', 'fail', 'not_run', 'missing'}:
        issues.append(f'test-report-summary: invalid overall_status={overall_status}')

    # If status is 'not_run' or 'missing', it cannot be treated as pass
    if overall_status in {'not_run', 'missing'}:
        # This is expected and correct fail-closed behavior
        pass

    # Check test_suites is a dict
    test_suites = report.get('test_suites', {})
    if not isinstance(test_suites, dict):
        issues.append('test-report-summary: test_suites must be a dict')

    # Check for secrets
    secrets = _has_secret_like_value(report)
    for secret in secrets:
        issues.append(f'test-report-summary: {secret}')

    return len(issues) == 0, issues



IDENTITY_FIELDS = ('commit_sha', 'deployment_id', 'ci_run_id', 'environment')
EVIDENCE_IDENTITY_FIELDS = ('evidence_started_at', 'evidence_completed_at')
TIMESTAMP_FIELDS = ('generated_at',) + EVIDENCE_IDENTITY_FIELDS
REQUIRED_CI_GATES = {
    'backend_tests',
    'saas_workflow_validation',
    'readiness_validation',
    'paid_launch_readiness',
    'live_evidence',
    'frontend_build',
    'security_release_gates',
}



def _validate_identity_and_freshness(
    artifact: dict[str, Any],
    label: str,
    *,
    now: datetime,
    max_age_seconds: int,
) -> list[str]:
    issues: list[str] = []
    for field in IDENTITY_FIELDS + TIMESTAMP_FIELDS:
        if not artifact.get(field):
            issues.append(f'{label}: missing required attestation field {field}')
    sha = str(artifact.get('commit_sha') or '').lower()
    if sha and not SHA_RE.fullmatch(sha):
        issues.append(f'{label}: commit_sha must be the exact 40-character Git SHA')
    try:
        generated_at = parse_timestamp(artifact.get('generated_at'))
        started_at = parse_timestamp(artifact.get('evidence_started_at'))
        completed_at = parse_timestamp(artifact.get('evidence_completed_at'))
        if started_at > completed_at:
            issues.append(f'{label}: evidence_started_at is after evidence_completed_at')
        if completed_at > now:
            issues.append(f'{label}: evidence timestamp is in the future')
        age = (now - completed_at).total_seconds()
        if age > max_age_seconds:
            issues.append(f'{label}: stale evidence ({int(age)}s old; max {max_age_seconds}s)')
        if generated_at < started_at or generated_at > now:
            issues.append(f'{label}: generated_at is outside the evidence collection window')
    except ValueError as exc:
        issues.append(f'{label}: invalid evidence timestamp: {exc}')
    return issues


def validate_release_bundle(
    release_proof_dir: Path,
    launch_proof_dir: Path,
    *,
    now: datetime | None = None,
    max_age_seconds: int | None = None,
) -> tuple[bool, list[str], dict[str, Any] | None]:
    """Validate one coherent, fresh release attestation across all proof files."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    max_age_seconds = max_age_seconds or int(
        os.getenv('RELEASE_PROOF_MAX_AGE_SECONDS', str(DEFAULT_MAX_EVIDENCE_AGE_SECONDS))
    )
    paths = {
        'ci-required-gates': release_proof_dir / 'ci-required-gates.json',
        'release-proof': release_proof_dir / 'summary.json',
        'manifest': release_proof_dir / 'manifest.json',
        'test-report-summary': release_proof_dir / 'test-report-summary.json',
        'launch-proof': launch_proof_dir / 'summary.json',
    }
    artifacts: dict[str, dict[str, Any]] = {}
    issues: list[str] = []
    for label, path in paths.items():
        data = _load_json(path)
        if data is None:
            issues.append(f'{label}: required release proof artifact missing')
            continue
        artifacts[label] = data
        issues.extend(
            _validate_identity_and_freshness(
                data, label, now=now, max_age_seconds=max_age_seconds
            )
        )

    if not artifacts:
        return False, issues, None

    reference = artifacts.get('release-proof') or next(iter(artifacts.values()))
    if release_proof_dir.is_relative_to(REPO_ROOT):
        expected_commit = git_sha(REPO_ROOT)
        if expected_commit != 'unknown' and reference.get('commit_sha') != expected_commit:
            issues.append(
                f'release-proof: commit_sha does not match checked-out Git commit '
                f'(expected {expected_commit}, got {reference.get("commit_sha")})'
            )
    identity = {
        field: reference.get(field)
        for field in IDENTITY_FIELDS + EVIDENCE_IDENTITY_FIELDS
    }
    for label, artifact in artifacts.items():
        for field in IDENTITY_FIELDS:
            expected = identity[field]
            if artifact.get(field) != expected:
                issues.append(
                    f'{label}: {field} mismatch (expected {expected!r}, got {artifact.get(field)!r})'
                )

    gates = artifacts.get('ci-required-gates', {})
    required_gates = gates.get('required_gates', {})
    missing_gates = sorted(REQUIRED_CI_GATES - set(required_gates))
    if missing_gates:
        issues.append('ci-required-gates: missing required CI gates: ' + ', '.join(missing_gates))
    manifest = artifacts.get('manifest', {})
    if manifest.get('overall_status') != 'pass' or manifest.get('blockers'):
        issues.append('manifest: overall_status must be pass without blockers')
    artifact_root = release_proof_dir.parents[2] if len(release_proof_dir.parents) >= 3 else REPO_ROOT
    manifested_paths: set[str] = set()
    for entry in manifest.get('files') or []:
        rel_path = entry.get('path')
        if not isinstance(rel_path, str) or not rel_path.startswith('artifacts/'):
            issues.append(f'manifest: invalid artifact path {rel_path!r}')
            continue
        manifested_paths.add(rel_path)
        full_path = artifact_root / rel_path
        if not full_path.exists():
            issues.append(f'manifest: required file not found: {rel_path}')
            continue
        if entry.get('sha256') != _compute_sha256(full_path):
            issues.append(f'manifest: SHA256 mismatch for {rel_path}')
    required_manifest_paths = {
        str((release_proof_dir / 'summary.json').relative_to(artifact_root)),
        str((release_proof_dir / 'ci-required-gates.json').relative_to(artifact_root)),
        str((release_proof_dir / 'test-report-summary.json').relative_to(artifact_root)),
        str((launch_proof_dir / 'summary.json').relative_to(artifact_root)),
    }
    missing_manifest_paths = sorted(required_manifest_paths - manifested_paths)
    if missing_manifest_paths:
        issues.append('manifest: missing required artifacts: ' + ', '.join(missing_manifest_paths))

    test_report = artifacts.get('test-report-summary', {})
    if test_report.get('overall_status') == 'fail':
        issues.append('test-report-summary: failed test report cannot be attested')
    for suite_name, suite in (test_report.get('test_suites') or {}).items():
        if suite.get('status') == 'fail' or suite.get('tests_failed', 0):
            issues.append(f'test-report-summary: suite {suite_name} failed')

    release = artifacts.get('release-proof', {})
    launch = artifacts.get('launch-proof', {})
    enterprise_claim = any((
        release.get('release_status') == 'pass',
        release.get('paid_launch_ready') is True,
        launch.get('paid_launch_ready') is True,
        launch.get('broad_paid_saas_ready') is True,
    ))
    security_path = artifact_root / 'artifacts' / 'security' / 'latest' / 'summary.json'
    security_ok, security_issues = validate_security_proof(
        security_path, artifact_root=security_path.parent
    )
    if enterprise_claim and not security_ok:
        issues.extend(f'security release proof: {issue}' for issue in security_issues)

    if enterprise_claim:
        if test_report.get('overall_status') != 'pass':
            issues.append(
                f'test-report-summary: enterprise readiness requires pass, got '
                f'{test_report.get("overall_status", "missing")}'
            )
        for suite_name, suite in (test_report.get('test_suites') or {}).items():
            if suite.get('status') != 'pass':
                issues.append(f'test-report-summary: enterprise suite {suite_name} status={suite.get("status")}')
        for gate_name in sorted(REQUIRED_CI_GATES):
            status = (required_gates.get(gate_name) or {}).get('status', 'missing')
            if status != 'pass':
                issues.append(f'ci-required-gates: required gate {gate_name} status={status}')
        if gates.get('overall_status') != 'pass':
            issues.append('ci-required-gates: overall_status must be pass for enterprise readiness')
        if release.get('release_status') != 'pass':
            issues.append('release-proof: launch artifact claims readiness while release_status is not pass')
        if not release.get('ci_required_gates_ready') or not release.get('test_report_ready') or not release.get('security_proof_ready'):
            issues.append('release-proof: enterprise-ready result contradicts validated CI/test readiness')
        if not launch.get('paid_launch_ready'):
            issues.append('launch-proof: release claims pass but paid_launch_ready is false')
        if launch.get('broad_paid_saas_ready') and not gates.get('broad_paid_launch_ready'):
            issues.append('launch-proof: broad readiness contradicts ci-required-gates')

    if release.get('release_channel') != identity.get('environment'):
        issues.append('release-proof: release_channel does not match attested environment')
    if gates.get('release_channel') != identity.get('environment'):
        issues.append('ci-required-gates: release_channel does not match attested environment')

    return not issues, sorted(set(issues)), identity

def main() -> int:
    """Validate all five proof artifacts."""
    repo_root = Path(__file__).resolve().parents[1]
    release_proof_dir = repo_root / 'artifacts' / 'release-proof' / 'latest'
    launch_proof_dir = repo_root / 'artifacts' / 'launch-proof' / 'latest'

    ci_gates_path = release_proof_dir / 'ci-required-gates.json'
    release_proof_path = release_proof_dir / 'summary.json'
    manifest_path = release_proof_dir / 'manifest.json'
    test_report_path = release_proof_dir / 'test-report-summary.json'
    launch_proof_path = launch_proof_dir / 'summary.json'

    all_ok = True
    all_issues: list[str] = []

    print('[validate-release-proof] Validating ci-required-gates.json...')
    ok, issues = validate_ci_required_gates(ci_gates_path)
    if ok:
        print('[validate-release-proof] [OK] ci-required-gates.json valid')
    else:
        print('[validate-release-proof] [FAIL] ci-required-gates.json invalid')
        all_ok = False
    all_issues.extend(issues)

    print('[validate-release-proof] Validating manifest.json...')
    ok, issues = validate_manifest(manifest_path)
    if ok:
        print('[validate-release-proof] [OK] manifest.json valid')
    else:
        print('[validate-release-proof] [FAIL] manifest.json invalid')
        all_ok = False
    all_issues.extend(issues)

    print('[validate-release-proof] Validating test-report-summary.json...')
    ok, issues = validate_test_report_summary(test_report_path)
    if ok:
        print('[validate-release-proof] [OK] test-report-summary.json valid')
    else:
        print('[validate-release-proof] [FAIL] test-report-summary.json invalid')
        all_ok = False
    all_issues.extend(issues)

    print('[validate-release-proof] Validating release-proof summary.json...')
    ok, issues = validate_release_proof(release_proof_path)
    if ok:
        print('[validate-release-proof] [OK] release-proof summary.json valid')
    else:
        print('[validate-release-proof] [FAIL] release-proof summary.json invalid')
        all_ok = False
    all_issues.extend(issues)

    print('[validate-release-proof] Validating launch-proof summary.json...')
    ok, issues = validate_launch_proof(launch_proof_path)
    if ok:
        print('[validate-release-proof] [OK] launch-proof summary.json valid')
    else:
        print('[validate-release-proof] [FAIL] launch-proof summary.json invalid')
        all_ok = False
    all_issues.extend(issues)

    print('[validate-release-proof] Validating cross-artifact release attestation...')
    ok, issues, identity = validate_release_bundle(release_proof_dir, launch_proof_dir)
    if ok:
        print(f'[validate-release-proof] [OK] coherent attestation {identity}')
    else:
        print('[validate-release-proof] [FAIL] release attestation is not coherent')
        all_ok = False
    all_issues.extend(issues)

    if all_issues:
        print('\n[validate-release-proof] Issues found:')
        for issue in all_issues:
            print(f'  - {issue}')

    if all_ok:
        print('\n[validate-release-proof] All artifacts valid and fail-closed')
        return 0
    else:
        print(f'\n[validate-release-proof] Validation failed with {len(all_issues)} issue(s)')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
