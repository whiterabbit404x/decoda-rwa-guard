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
"""
from __future__ import annotations

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


def main() -> int:
    """Validate all three proof artifacts."""
    repo_root = Path(__file__).resolve().parents[1]
    release_proof_dir = repo_root / 'artifacts' / 'release-proof' / 'latest'
    launch_proof_dir = repo_root / 'artifacts' / 'launch-proof' / 'latest'

    ci_gates_path = release_proof_dir / 'ci-required-gates.json'
    release_proof_path = release_proof_dir / 'summary.json'
    launch_proof_path = launch_proof_dir / 'summary.json'

    all_ok = True
    all_issues: list[str] = []

    print('[validate-release-proof] Validating ci-required-gates.json...')
    ok, issues = validate_ci_required_gates(ci_gates_path)
    if ok:
        print('[validate-release-proof] ✓ ci-required-gates.json valid')
    else:
        print('[validate-release-proof] ✗ ci-required-gates.json invalid')
        all_ok = False
    all_issues.extend(issues)

    print('[validate-release-proof] Validating release-proof summary.json...')
    ok, issues = validate_release_proof(release_proof_path)
    if ok:
        print('[validate-release-proof] ✓ release-proof summary.json valid')
    else:
        print('[validate-release-proof] ✗ release-proof summary.json invalid')
        all_ok = False
    all_issues.extend(issues)

    print('[validate-release-proof] Validating launch-proof summary.json...')
    ok, issues = validate_launch_proof(launch_proof_path)
    if ok:
        print('[validate-release-proof] ✓ launch-proof summary.json valid')
    else:
        print('[validate-release-proof] ✗ launch-proof summary.json invalid')
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
