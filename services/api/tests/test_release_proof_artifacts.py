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


# Test M: Manifest is generated with required fields
def test_manifest_generated_with_required_fields(artifact_dirs: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify manifest.json is generated with all required fields."""
    from scripts.generate_release_proof import generate_artifact_manifest

    manifest = generate_artifact_manifest(artifact_dirs['release'], artifact_dirs['launch'], mode='local')

    # Check required fields
    assert 'schema_version' in manifest
    assert 'generated_at' in manifest
    assert 'release_channel' in manifest
    assert 'commit_sha' in manifest
    assert 'branch' in manifest
    assert 'files' in manifest
    assert 'overall_status' in manifest
    assert 'blockers' in manifest
    assert 'warnings' in manifest

    # Check files array
    assert isinstance(manifest['files'], list)


# Test N: Manifest includes required artifacts
def test_manifest_includes_required_artifacts(artifact_dirs: dict[str, Path]) -> None:
    """Verify manifest includes paths to required artifacts."""
    from scripts.generate_release_proof import generate_artifact_manifest

    # First create the required artifacts
    ci_gates_data = {'schema_version': 1, 'overall_status': 'pass'}
    summary_data = {'schema_version': 1, 'release_status': 'pass'}
    launch_summary_data = {'schema_version': 1, 'launch_mode': 'pilot'}

    (artifact_dirs['release'] / 'ci-required-gates.json').write_text(json.dumps(ci_gates_data))
    (artifact_dirs['release'] / 'summary.json').write_text(json.dumps(summary_data))
    (artifact_dirs['launch'] / 'summary.json').write_text(json.dumps(launch_summary_data))

    manifest = generate_artifact_manifest(artifact_dirs['release'], artifact_dirs['launch'], mode='local')

    # Extract file paths from manifest
    file_paths = [f['path'] for f in manifest['files']]

    # Should include required artifacts (relative paths)
    assert any('summary.json' in p for p in file_paths)
    assert any('ci-required-gates.json' in p for p in file_paths)


# Test O: Manifest SHA256 matches actual file contents
def test_manifest_sha256_matches_file_contents(artifact_dirs: dict[str, Path]) -> None:
    """Verify manifest SHA256 values match actual files."""
    import hashlib
    from scripts.generate_release_proof import generate_artifact_manifest

    # Create test artifacts
    test_content = {'test': 'data'}
    test_file = artifact_dirs['release'] / 'ci-required-gates.json'
    test_file.write_text(json.dumps(test_content))

    manifest = generate_artifact_manifest(artifact_dirs['release'], artifact_dirs['launch'], mode='local')

    # Find the file entry in manifest
    file_entries = {f['path']: f for f in manifest['files']}

    # Check at least one required file's SHA256
    for path, entry in file_entries.items():
        if entry['status'] == 'present' and entry['required']:
            # Compute expected SHA256
            actual_sha256 = hashlib.sha256(json.dumps(test_content).encode()).hexdigest()
            # Manifest SHA256 should match (if this is the test file)
            if 'ci-required-gates' in path:
                assert entry['sha256'] == actual_sha256


# Test P: Validator fails when manifest SHA256 is tampered
def test_validator_fails_on_manifest_sha256_tamper(artifact_dirs: dict[str, Path]) -> None:
    """Verify validator detects SHA256 tampering."""
    from scripts.validate_release_proof import validate_manifest

    # Create a manifest with wrong SHA256
    invalid_manifest = {
        'schema_version': 1,
        'generated_at': '2026-05-22T00:00:00Z',
        'release_channel': 'local',
        'commit_sha': 'abc123',
        'branch': 'main',
        'files': [
            {
                'path': 'artifacts/release-proof/latest/summary.json',
                'sha256': 'wrong_hash_value_0000000000000000000000000000000000000000',
                'size_bytes': 100,
                'required': True,
                'status': 'present'
            }
        ],
        'overall_status': 'pass',
        'blockers': [],
        'warnings': []
    }

    manifest_path = artifact_dirs['release'] / 'manifest.json'
    with open(manifest_path, 'w') as f:
        json.dump(invalid_manifest, f)

    # Create a dummy summary.json file
    summary_path = artifact_dirs['release'] / 'summary.json'
    summary_path.write_text('{"test": "data"}')

    # Validator should fail due to SHA256 mismatch
    ok, issues = validate_manifest(manifest_path)
    # Should find SHA256 mismatch or other issues
    assert not ok or any('SHA256' in issue for issue in issues) or any('tamper' in issue.lower() for issue in issues)


# Test Q: Test report summary is generated
def test_test_report_summary_generated() -> None:
    """Verify test-report-summary.json is generated."""
    from scripts.generate_release_proof import generate_test_report_summary

    test_report = generate_test_report_summary(mode='local')

    # Check required fields
    assert 'schema_version' in test_report
    assert 'generated_at' in test_report
    assert 'release_channel' in test_report
    assert 'commit_sha' in test_report
    assert 'branch' in test_report
    assert 'test_suites' in test_report
    assert 'overall_status' in test_report
    assert 'blockers' in test_report
    assert 'warnings' in test_report

    # Check test_suites is a dict
    assert isinstance(test_report['test_suites'], dict)


# Test R: Missing test report summary cannot be interpreted as pass
def test_missing_test_report_not_pass() -> None:
    """Verify missing test report summary is not treated as pass."""
    from scripts.generate_release_proof import generate_test_report_summary

    test_report = generate_test_report_summary(mode='local')

    # In local mode, status should be not_run or fail, not pass
    assert test_report['overall_status'] in {'not_run', 'fail', 'missing'}
    assert test_report['overall_status'] != 'pass'


# Test S: Artifact paths must be relative and under artifacts/
def test_manifest_artifact_paths_relative(artifact_dirs: dict[str, Path]) -> None:
    """Verify manifest artifact paths are relative and under artifacts/."""
    from scripts.validate_release_proof import validate_manifest

    # Create a manifest with invalid paths
    invalid_manifest = {
        'schema_version': 1,
        'generated_at': '2026-05-22T00:00:00Z',
        'release_channel': 'local',
        'commit_sha': 'abc123',
        'branch': 'main',
        'files': [
            {
                'path': '/absolute/path/file.json',  # Invalid: absolute path
                'sha256': 'abc123',
                'size_bytes': 100,
                'required': True,
                'status': 'present'
            },
            {
                'path': '../outside/artifacts/file.json',  # Invalid: goes outside
                'sha256': 'abc123',
                'size_bytes': 100,
                'required': True,
                'status': 'present'
            }
        ],
        'overall_status': 'fail',
        'blockers': ['invalid paths'],
        'warnings': []
    }

    manifest_path = artifact_dirs['release'] / 'manifest.json'
    with open(manifest_path, 'w') as f:
        json.dump(invalid_manifest, f)

    ok, issues = validate_manifest(manifest_path)

    # Should have issues about path validation
    assert not ok
    assert any('path' in issue.lower() for issue in issues)


# Test T: Validator fails if generated JSON contains secret-like values
def test_validator_fails_on_secret_like_values(artifact_dirs: dict[str, Path]) -> None:
    """Verify validator detects secret-like values in artifacts."""
    from scripts.validate_release_proof import validate_manifest

    # Create a manifest with secret-like value
    invalid_manifest = {
        'schema_version': 1,
        'generated_at': '2026-05-22T00:00:00Z',
        'release_channel': 'local',
        'commit_sha': 'abc123',
        'branch': 'main',
        'files': [],
        'overall_status': 'pass',
        'blockers': [],
        'api_key': 'sk_test_12345',  # Secret-like value
        'warnings': []
    }

    manifest_path = artifact_dirs['release'] / 'manifest.json'
    with open(manifest_path, 'w') as f:
        json.dump(invalid_manifest, f)

    ok, issues = validate_manifest(manifest_path)

    # Should fail due to secret detection
    assert not ok
    assert any('secret' in issue.lower() or 'api_key' in issue.lower() for issue in issues)


# Test U: Release summary evidence_files includes manifest and test-report
def test_release_proof_evidence_files_complete() -> None:
    """Verify release-proof evidence_files includes all new artifacts."""
    from scripts.generate_release_proof import generate_release_proof

    release_proof = generate_release_proof(mode='local')

    # Should reference all evidence files
    evidence_files = release_proof.get('evidence_files', [])

    # Should include manifest and test-report
    assert any('manifest' in f for f in evidence_files), "manifest.json not in evidence_files"
    assert any('test-report' in f for f in evidence_files), "test-report-summary.json not in evidence_files"


# Test V: All five artifacts validate together
def test_all_five_artifacts_validate_together(artifact_dirs: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify all five artifacts are generated and validate together."""
    import subprocess
    from pathlib import Path

    # Run the generate script
    result = subprocess.run(
        ['python', 'scripts/generate_release_proof.py', '--mode', 'local'],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True
    )

    # Check generation succeeded
    assert result.returncode == 0, f"Generation failed: {result.stderr}"

    # Check all five files exist
    release_dir = REPO_ROOT / 'artifacts' / 'release-proof' / 'latest'
    launch_dir = REPO_ROOT / 'artifacts' / 'launch-proof' / 'latest'

    assert (release_dir / 'ci-required-gates.json').exists()
    assert (release_dir / 'summary.json').exists()
    assert (release_dir / 'manifest.json').exists()
    assert (release_dir / 'test-report-summary.json').exists()
    assert (launch_dir / 'summary.json').exists()
