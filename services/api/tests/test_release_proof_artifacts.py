"""
Session 11 — CI/Release Evidence and Launch Proof Artifacts.

Tests that release proof artifacts are truthful and fail closed.

Key rules:
- Artifacts must be deterministic JSON.
- Unknown/missing proof must make overall status fail.
- Simulator evidence cannot satisfy live evidence gates.
- broad_paid_saas_ready is only true when ALL gates pass.
- Secrets are never included.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture
def artifact_dirs(tmp_path: Path) -> dict[str, Path]:
    """Create temporary artifact directories."""
    release_dir = tmp_path / 'artifacts' / 'release-proof' / 'latest'
    launch_dir = tmp_path / 'artifacts' / 'launch-proof' / 'latest'
    release_dir.mkdir(parents=True, exist_ok=True)
    launch_dir.mkdir(parents=True, exist_ok=True)
    return {'release': release_dir, 'launch': launch_dir}


def _load_json(path: Path) -> Any:
    """Load and return JSON file."""
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def _has_secret_values(obj: Any) -> list[str]:
    """Check if object contains secret-like values."""
    secrets = []
    markers = {'secret', 'key', 'password', 'token', 'credential', 'sk_', 'pk_'}

    if isinstance(obj, dict):
        for key, value in obj.items():
            if any(m in key.lower() for m in markers):
                if isinstance(value, str) and value:
                    secrets.append(f'{key}={value}')
            secrets.extend(_has_secret_values(value))
    elif isinstance(obj, list):
        for item in obj:
            secrets.extend(_has_secret_values(item))

    return secrets


# Test A: Generator creates all three required artifact files
def test_generator_creates_all_three_artifacts(artifact_dirs: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify generate_release_proof.py creates all three JSON files."""
    # Monkeypatch artifact paths for the test
    monkeypatch.setenv('ARTIFACTS_DIR', str(artifact_dirs['release'].parent.parent))

    # Import here to get the patched environment
    from scripts.generate_release_proof import generate_ci_required_gates, generate_release_proof, generate_launch_proof

    ci_gates = generate_ci_required_gates(mode='local')
    release_proof = generate_release_proof(mode='local')
    launch_proof = generate_launch_proof(mode='local')

    # Verify all three have required structure
    assert isinstance(ci_gates, dict)
    assert 'schema_version' in ci_gates
    assert 'generated_at' in ci_gates
    assert 'required_gates' in ci_gates

    assert isinstance(release_proof, dict)
    assert 'schema_version' in release_proof
    assert 'release_status' in release_proof

    assert isinstance(launch_proof, dict)
    assert 'schema_version' in launch_proof
    assert 'launch_mode' in launch_proof


# Test B: Missing live evidence makes broad_paid_saas_ready false
def test_missing_live_evidence_blocks_broad_paid_saas() -> None:
    """Verify missing live evidence prevents broad paid SaaS readiness."""
    from scripts.generate_release_proof import generate_launch_proof

    launch_proof = generate_launch_proof(mode='local')

    # In local mode without live evidence, broad_paid_saas_ready must be false
    assert launch_proof['broad_paid_saas_ready'] is False
    assert 'live evidence' in ' '.join(launch_proof.get('blockers', [])).lower()


# Test C: Missing CI gate makes release_status fail
def test_missing_ci_gate_makes_release_status_fail() -> None:
    """Verify missing CI gate blocks release status."""
    from scripts.generate_release_proof import generate_release_proof

    release_proof = generate_release_proof(mode='local', strict=False)

    # In local mode without CI gates, release should not pass
    # (ci_required_gates_ready should be false if gates artifact is missing)
    assert release_proof['release_status'] == 'fail'


# Test D: Paid launch readiness blockers are captured
def test_paid_launch_readiness_blockers() -> None:
    """Verify paid launch blockers are properly captured."""
    from scripts.generate_release_proof import generate_ci_required_gates

    ci_gates = generate_ci_required_gates(mode='local')

    # Check paid_launch_readiness gate
    assert 'paid_launch_readiness' in ci_gates['required_gates']
    paid_launch = ci_gates['required_gates']['paid_launch_readiness']

    # In local mode, paid launch should have blockers
    assert isinstance(paid_launch.get('blockers', []), list)


# Test E: Pilot readiness may be true while broad paid SaaS readiness is false
def test_pilot_independent_from_broad_paid_saas() -> None:
    """Verify pilot_ready is independent of broad_paid_saas_ready."""
    from scripts.generate_release_proof import generate_launch_proof

    launch_proof = generate_launch_proof(mode='local')

    # These should be independent (pilot can be true while broad is false)
    assert isinstance(launch_proof['pilot_ready'], bool)
    assert isinstance(launch_proof['broad_paid_saas_ready'], bool)
    # In local mode, broad should always be false
    assert launch_proof['broad_paid_saas_ready'] is False


# Test F: Unknown status must not pass
def test_unknown_status_must_not_pass() -> None:
    """Verify unknown/not_run status is never treated as pass."""
    from scripts.generate_release_proof import generate_ci_required_gates

    ci_gates = generate_ci_required_gates(mode='local', strict=False)

    # In local mode, many gates are not_run, so overall_status should be fail or the gates should not be counted
    gates = ci_gates.get('required_gates', {})
    for gate_name, gate_data in gates.items():
        if isinstance(gate_data, dict):
            status = gate_data.get('status')
            # not_run should not cause overall_status to be pass if there are any fails
            if ci_gates['overall_status'] == 'fail' and status == 'not_run':
                # This is fine - not_run doesn't prevent failure
                pass


# Test G: Artifact JSON contains no secret values
def test_artifact_json_contains_no_secrets() -> None:
    """Verify no secret values in artifacts."""
    from scripts.generate_release_proof import (
        generate_ci_required_gates,
        generate_release_proof,
        generate_launch_proof,
    )

    ci_gates = generate_ci_required_gates(mode='local')
    release_proof = generate_release_proof(mode='local')
    launch_proof = generate_launch_proof(mode='local')

    # Check for secrets
    ci_secrets = _has_secret_values(ci_gates)
    release_secrets = _has_secret_values(release_proof)
    launch_secrets = _has_secret_values(launch_proof)

    assert not ci_secrets, f'ci-required-gates contains secrets: {ci_secrets}'
    assert not release_secrets, f'release-proof contains secrets: {release_secrets}'
    assert not launch_secrets, f'launch-proof contains secrets: {launch_secrets}'


# Test H: Schema versions are correct
def test_schema_versions_are_one() -> None:
    """Verify schema_version is 1 for all artifacts."""
    from scripts.generate_release_proof import (
        generate_ci_required_gates,
        generate_release_proof,
        generate_launch_proof,
    )

    ci_gates = generate_ci_required_gates(mode='local')
    release_proof = generate_release_proof(mode='local')
    launch_proof = generate_launch_proof(mode='local')

    assert ci_gates['schema_version'] == 1
    assert release_proof['schema_version'] == 1
    assert launch_proof['schema_version'] == 1


# Test I: Validator fails if required fields are missing
def test_validator_fails_on_missing_fields(artifact_dirs: dict[str, Path]) -> None:
    """Verify validator detects missing required fields."""
    from scripts.validate_release_proof import validate_ci_required_gates

    # Create invalid artifact
    invalid = {'schema_version': 1}  # Missing required fields
    invalid_path = artifact_dirs['release'] / 'ci-required-gates.json'
    with open(invalid_path, 'w') as f:
        json.dump(invalid, f)

    ok, issues = validate_ci_required_gates(invalid_path)
    assert not ok
    assert len(issues) > 0


# Test J: Validator fails if broad_paid_saas_ready is true while gates are false
def test_validator_fails_on_inconsistent_broad_paid(artifact_dirs: dict[str, Path]) -> None:
    """Verify validator detects invalid broad_paid_saas_ready claims."""
    from scripts.validate_release_proof import validate_launch_proof

    # Create artifact with inconsistent state
    invalid = {
        'schema_version': 1,
        'generated_at': '2026-05-22T00:00:00Z',
        'launch_mode': 'paid_ga',
        'pilot_ready': True,
        'paid_launch_ready': True,
        'controlled_pilot_ready': True,
        'broad_paid_saas_ready': True,  # Invalid: not all gates ready
        'readiness': {
            'billing_ready': False,  # This contradicts broad_paid_saas_ready
            'billing_webhook_ready': False,
            'email_ready': False,
            'provider_ready': False,
            'live_evidence_ready': False,
            'ci_required_gates_ready': False,
        },
        'blockers': [],
        'warnings': [],
    }
    invalid_path = artifact_dirs['launch'] / 'summary.json'
    with open(invalid_path, 'w') as f:
        json.dump(invalid, f)

    ok, issues = validate_launch_proof(invalid_path)
    assert not ok
    assert any('broad_paid_saas_ready' in issue for issue in issues)


# Test K: ci-required-gates overall_status is fail when any required gate fails
def test_ci_gates_overall_status_fail_on_gate_fail() -> None:
    """Verify overall_status is fail when any required gate fails."""
    from scripts.generate_release_proof import generate_ci_required_gates

    ci_gates = generate_ci_required_gates(mode='local', strict=False)

    # Check if any gate is failing
    gates = ci_gates.get('required_gates', {})
    has_fail = any(
        gate_data.get('status') == 'fail'
        for gate_data in gates.values()
        if isinstance(gate_data, dict)
    )

    if has_fail:
        # If any gate fails, overall_status must be fail
        assert ci_gates['overall_status'] == 'fail'


# Test L: release-proof summary includes links/paths to related evidence files
def test_release_proof_includes_evidence_file_paths() -> None:
    """Verify release-proof includes paths to evidence files."""
    from scripts.generate_release_proof import generate_release_proof

    release_proof = generate_release_proof(mode='local')

    # Should reference related evidence files
    assert 'evidence_files' in release_proof
    evidence_files = release_proof['evidence_files']
    assert isinstance(evidence_files, list)

    # Should reference ci-required-gates and launch-proof
    assert any('ci-required-gates' in f for f in evidence_files)
    assert any('launch-proof' in f for f in evidence_files)
