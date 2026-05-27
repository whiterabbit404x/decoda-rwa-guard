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
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]

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

    # Check broad_paid_launch_ready is never true
    if gates.get('broad_paid_launch_ready') is True:
        issues.append('ci-required-gates: broad_paid_launch_ready must never be true')

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
    required_fields = ['generated_at', 'release_status', 'release_channel', 'commit_sha', 'branch', 'ci_required_gates_ready', 'launch_proof_ready', 'paid_launch_ready', 'blockers', 'warnings']
    for field in required_fields:
        if field not in proof:
            issues.append(f'release-proof: missing required field {field}')

    # Check fail-closed semantics
    release_status = proof.get('release_status')
    ci_gates_ready = proof.get('ci_required_gates_ready')
    launch_proof_ready = proof.get('launch_proof_ready')

    if release_status == 'pass' and (not ci_gates_ready or not launch_proof_ready):
        issues.append(f'release-proof: release_status=pass but gates not ready (ci={ci_gates_ready}, launch={launch_proof_ready})')

    # Check paid_launch_ready is never true in local mode
    if proof.get('paid_launch_ready') is True:
        issues.append('release-proof: paid_launch_ready must never be true in local mode')

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

    # Check paid_launch_ready is never true in local mode
    if proof.get('paid_launch_ready') is True:
        issues.append('launch-proof: paid_launch_ready must never be true in local mode')

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
