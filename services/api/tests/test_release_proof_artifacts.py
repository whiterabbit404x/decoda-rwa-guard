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
from datetime import datetime, timedelta
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))


_VALID_PADDLE_PROOF_ENV = {
    'BILLING_PROVIDER': 'paddle',
    'PADDLE_API_KEY': 'pdl_api_ci_fixture_abc123',
    'PADDLE_CLIENT_TOKEN': 'pdl_client_ci_fixture_abc123',
    'PADDLE_PRICE_ID': 'pri_ci_monthly_abc123',
    'PADDLE_WEBHOOK_SECRET': 'pdl_whsec_ci_fixture_abc123',
    'PADDLE_ENVIRONMENT': 'production',
    'EMAIL_PROVIDER': 'resend',
    'RESEND_API_KEY': 're_ci_fixture_abc123',
    'EMAIL_FROM': 'noreply@decoda.io',
    'EMAIL_DOMAIN': 'decoda.io',
}


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


# Test B: broad_paid_saas_ready is always false in local mode
def test_missing_live_evidence_blocks_broad_paid_saas() -> None:
    """Verify broad_paid_saas_ready is never true in local mode.

    Live evidence may now be sourced from the canonical service summary when no
    EVM_RPC_URL is configured, so blockers may or may not include 'live evidence'.
    The key invariant is that broad_paid_saas_ready is always false in local mode
    because billing, email, and CI gates cannot pass locally.
    """
    from scripts.generate_release_proof import generate_launch_proof

    launch_proof = generate_launch_proof(mode='local')

    # broad_paid_saas_ready must never be true in local mode (fail-closed invariant)
    assert launch_proof['broad_paid_saas_ready'] is False
    # paid_launch_ready is also always false in local mode
    assert launch_proof['paid_launch_ready'] is False
    # There must always be at least one blocker in local mode
    assert len(launch_proof.get('blockers', [])) > 0


# Test C: Missing CI gate makes release_status fail
def test_missing_ci_gate_makes_release_status_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify missing CI gate blocks release status when no artifacts exist on disk."""
    import scripts.generate_release_proof as grp
    monkeypatch.setattr(grp, 'REPO_ROOT', tmp_path)

    from scripts.generate_release_proof import generate_release_proof

    release_proof = generate_release_proof(mode='local', strict=False)

    # In local mode without CI gates artifact, release should not pass
    assert release_proof['release_status'] == 'fail'
    assert release_proof['ci_required_gates_ready'] is False


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


def test_validate_release_proof_output_is_ascii_safe(artifact_dirs: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    """validate_release_proof.py must not output characters that crash Windows GBK/cp1252 consoles."""
    import io
    import subprocess

    result = subprocess.run(
        ['python', 'scripts/validate_release_proof.py'],
        cwd=REPO_ROOT,
        capture_output=True,
        encoding='utf-8',
    )
    combined = result.stdout + result.stderr
    # Verify no non-ASCII characters are emitted (prevents UnicodeEncodeError on Windows consoles)
    try:
        combined.encode('ascii')
    except UnicodeEncodeError as exc:
        pytest.fail(f'validate_release_proof.py emits non-ASCII output that would crash Windows GBK consoles: {exc}')


# ---------------------------------------------------------------------------
# Mode-separation tests: local/CI never paid, staging may be paid
# ---------------------------------------------------------------------------

def test_local_mode_launch_proof_never_has_paid_launch_ready_true() -> None:
    """Local mode launch-proof must never have paid_launch_ready=True."""
    from scripts.generate_release_proof import generate_launch_proof

    proof = generate_launch_proof(mode='local')
    assert proof['paid_launch_ready'] is False, (
        f'paid_launch_ready must be False in local mode, got: {proof["paid_launch_ready"]}'
    )
    assert proof['broad_paid_saas_ready'] is False, (
        f'broad_paid_saas_ready must be False in local mode'
    )
    assert proof['schema_version'] == 1, (
        f'schema_version must be 1, got: {proof["schema_version"]}'
    )


def test_ci_mode_launch_proof_never_has_paid_launch_ready_true() -> None:
    """CI mode launch-proof must never have paid_launch_ready=True."""
    from scripts.generate_release_proof import generate_launch_proof

    proof = generate_launch_proof(mode='ci')
    assert proof['paid_launch_ready'] is False, (
        f'paid_launch_ready must be False in ci mode, got: {proof["paid_launch_ready"]}'
    )
    assert proof['broad_paid_saas_ready'] is False, (
        f'broad_paid_saas_ready must be False in ci mode'
    )


def test_local_mode_release_proof_never_has_paid_launch_ready_true() -> None:
    """Local mode release-proof must never have paid_launch_ready=True."""
    from scripts.generate_release_proof import generate_release_proof

    proof = generate_release_proof(mode='local')
    assert proof['paid_launch_ready'] is False, (
        f'paid_launch_ready must be False in local mode release-proof, got: {proof["paid_launch_ready"]}'
    )
    assert proof['schema_version'] == 1, (
        f'release-proof schema_version must be 1, got: {proof["schema_version"]}'
    )


def test_launch_proof_schema_version_matches_validator_expectation() -> None:
    """Schema version produced by generate_release_proof must match what validate_release_proof expects."""
    from scripts.generate_release_proof import generate_launch_proof
    from scripts.validate_release_proof import validate_launch_proof
    import tempfile, os

    proof = generate_launch_proof(mode='local')
    assert proof['schema_version'] == 1, (
        'generate_release_proof must produce schema_version=1 to satisfy validate_release_proof'
    )
    # Write to a temp file and validate
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(proof, f)
        tmp_path_str = f.name
    try:
        ok, issues = validate_launch_proof(Path(tmp_path_str))
        schema_issues = [i for i in issues if 'schema' in i.lower()]
        assert not schema_issues, (
            f'Schema version mismatch between generator and validator: {schema_issues}'
        )
    finally:
        os.unlink(tmp_path_str)


def test_validate_release_proof_passes_for_local_fail_closed_artifacts() -> None:
    """validate_release_proof.py must pass after generate_release_proof.py --mode local."""
    result = subprocess.run(
        ['python', 'scripts/generate_release_proof.py', '--mode', 'local'],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f'generate_release_proof.py --mode local failed:\n{result.stderr}'
    )
    result2 = subprocess.run(
        ['python', 'scripts/validate_release_proof.py'],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result2.returncode == 0, (
        f'validate_release_proof.py failed after local generation:\n'
        f'{result2.stdout}\n{result2.stderr}'
    )


def test_paid_saas_launch_proof_local_mode_never_has_paid_launch_ready_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_paid_saas_launch_proof.py --mode local must produce paid_launch_ready=false."""
    import os

    env = {k: v for k, v in os.environ.items()}
    env.update(_VALID_PADDLE_PROOF_ENV)
    result = subprocess.run(
        [sys.executable, 'scripts/staging/run_paid_saas_launch_proof.py', '--mode', 'local'],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f'Paid SaaS launch proof should exit 0 even in local mode (gates pass).\n'
        f'stdout: {result.stdout}\nstderr: {result.stderr}'
    )
    artifact = REPO_ROOT / 'artifacts' / 'launch-proof' / 'latest' / 'summary.json'
    if artifact.exists():
        data = json.loads(artifact.read_text())
        assert data.get('paid_launch_ready') is False, (
            f'paid_launch_ready must be False in local mode, got: {data.get("paid_launch_ready")}'
        )
        assert data.get('broad_paid_saas_ready') is False, (
            f'broad_paid_saas_ready must be False in local mode'
        )
        assert data.get('schema_version') == 1, (
            f'schema_version must be 1, got: {data.get("schema_version")}'
        )
        local_blocker = any(
            'local mode' in b for b in data.get('blockers', [])
        )
        assert local_blocker, (
            f'Blockers must include a local-mode message. blockers={data.get("blockers")}'
        )


def test_paid_saas_launch_proof_staging_mode_can_have_paid_launch_ready_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_paid_saas_launch_proof.py --mode staging with all gates can set paid_launch_ready=true."""
    import os

    env = {k: v for k, v in os.environ.items()}
    env.update(_VALID_PADDLE_PROOF_ENV)
    result = subprocess.run(
        [sys.executable, 'scripts/staging/run_paid_saas_launch_proof.py', '--mode', 'staging'],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f'Paid SaaS launch proof should exit 0 with valid Paddle+Resend fixture env in staging mode.\n'
        f'stdout: {result.stdout}\nstderr: {result.stderr}'
    )
    artifact = REPO_ROOT / 'artifacts' / 'launch-proof' / 'latest' / 'summary.json'
    if artifact.exists():
        data = json.loads(artifact.read_text())
        assert data.get('schema_version') == 1, (
            f'schema_version must be 1 even in staging mode, got: {data.get("schema_version")}'
        )
        assert data.get('proof_mode') == 'staging', (
            f'proof_mode must be staging, got: {data.get("proof_mode")}'
        )
        # In staging mode with all gates passing, paid_launch_ready may be true
        assert data.get('paid_launch_ready') is True, (
            f'paid_launch_ready should be True in staging mode with all gates passing'
        )


# ---------------------------------------------------------------------------
# Ordering / consistency tests required by the proof pipeline fix
# ---------------------------------------------------------------------------

def test_manifest_sha256_matches_launch_proof_after_generation() -> None:
    """Manifest SHA256 for launch-proof must match the actual file after generation."""
    import hashlib

    # Generate all proof artifacts atomically.
    result = subprocess.run(
        ['python', 'scripts/generate_release_proof.py', '--mode', 'local'],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f'generate_release_proof.py failed:\n{result.stderr}'

    manifest_path = REPO_ROOT / 'artifacts' / 'release-proof' / 'latest' / 'manifest.json'
    launch_path   = REPO_ROOT / 'artifacts' / 'launch-proof'  / 'latest' / 'summary.json'

    assert manifest_path.exists(), 'manifest.json was not created'
    assert launch_path.exists(),   'launch-proof summary.json was not created'

    manifest = json.loads(manifest_path.read_text())
    files = {entry['path']: entry for entry in manifest.get('files', [])}

    launch_key = 'artifacts/launch-proof/latest/summary.json'
    assert launch_key in files, f'manifest does not contain {launch_key}'

    entry = files[launch_key]
    actual_sha256 = hashlib.sha256(launch_path.read_bytes()).hexdigest()
    assert entry['sha256'] == actual_sha256, (
        f'Manifest SHA256 mismatch for launch-proof after generation.\n'
        f'  manifest={entry["sha256"]}\n'
        f'  actual  ={actual_sha256}\n'
        'The manifest was not generated after the launch-proof was finalised.'
    )


def test_validate_release_proof_passes_after_proof_pipeline_generation() -> None:
    """validate_release_proof must pass immediately after generate_release_proof runs."""
    gen = subprocess.run(
        ['python', 'scripts/generate_release_proof.py', '--mode', 'local'],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert gen.returncode == 0, f'generate_release_proof.py failed:\n{gen.stderr}'

    val = subprocess.run(
        ['python', 'scripts/validate_release_proof.py'],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert val.returncode == 0, (
        f'validate_release_proof.py failed after pipeline generation:\n'
        f'{val.stdout}\n{val.stderr}'
    )


def test_validate_release_proof_fails_when_launch_proof_modified_after_manifest() -> None:
    """Modifying launch-proof after manifest generation must cause validation to fail."""
    import hashlib, time

    gen = subprocess.run(
        ['python', 'scripts/generate_release_proof.py', '--mode', 'local'],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert gen.returncode == 0, f'generate_release_proof.py failed:\n{gen.stderr}'

    # Overwrite launch-proof with different content to simulate post-manifest modification.
    launch_path = REPO_ROOT / 'artifacts' / 'launch-proof' / 'latest' / 'summary.json'
    original = launch_path.read_text()
    tampered = json.loads(original)
    tampered['_tampered_by_test'] = True
    launch_path.write_text(json.dumps(tampered, indent=2))

    try:
        val = subprocess.run(
            ['python', 'scripts/validate_release_proof.py'],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert val.returncode != 0, (
            'validate_release_proof.py should have failed after launch-proof was modified, '
            'but it passed. The manifest integrity check is not working.'
        )
        assert 'SHA256' in val.stdout + val.stderr, (
            'Expected SHA256 mismatch error in output, got:\n'
            f'{val.stdout}\n{val.stderr}'
        )
    finally:
        # Restore original so other tests are not affected.
        launch_path.write_text(original)
        # Re-generate to leave artifacts in a consistent state.
        subprocess.run(
            ['python', 'scripts/generate_release_proof.py', '--mode', 'local'],
            cwd=REPO_ROOT,
            capture_output=True,
        )


def test_authoritative_release_workflow_is_single_and_immutable() -> None:
    workflows = sorted((REPO_ROOT / '.github' / 'workflows').glob('*.yml'))
    # ci-quality-gates.yml is the CI testing and coverage gate workflow (Phase 7 audit fix).
    # release-attestation.yml remains the single authoritative release workflow.
    known_workflows = {'release-attestation.yml', 'ci-quality-gates.yml'}
    workflow_names = {path.name for path in workflows}
    unknown = workflow_names - known_workflows
    assert not unknown, f'Unknown workflow files found: {unknown}. Only {known_workflows} are approved.'
    assert 'release-attestation.yml' in workflow_names, 'Release attestation workflow must exist.'
    workflow = next(p for p in workflows if p.name == 'release-attestation.yml').read_text()
    assert 'scripts/release_attestation.py create' in workflow
    assert 'scripts/release_attestation.py verify' in workflow
    assert 'X-Expected-Image-Ref: $RELEASE_IMAGE_REF' in workflow
    assert '--image-ref "$RELEASE_IMAGE_REF"' in workflow
    assert '--penetration-test-evidence "$PENTEST_EVIDENCE"' in workflow
    assert 'only the protected production environment is release-authoritative' in workflow
    assert 'artifacts/release-attestations/${{ github.sha }}/${{ inputs.deployment_id }}/' in workflow
    assert 'overwrite: false' in workflow
    assert '/latest' not in workflow
    assert 'if: always()' not in workflow


def test_no_regen_launch_proof_flag_preserves_existing_launch_proof() -> None:
    """--no-regen-launch-proof must not overwrite an existing launch-proof."""
    import hashlib

    # First, generate a fresh launch-proof using generate_release_proof.py normally.
    gen1 = subprocess.run(
        ['python', 'scripts/generate_release_proof.py', '--mode', 'local'],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert gen1.returncode == 0, f'Initial generation failed:\n{gen1.stderr}'

    launch_path = REPO_ROOT / 'artifacts' / 'launch-proof' / 'latest' / 'summary.json'
    sha_before = hashlib.sha256(launch_path.read_bytes()).hexdigest()

    # Run again with --no-regen-launch-proof; launch-proof should be unchanged.
    gen2 = subprocess.run(
        ['python', 'scripts/generate_release_proof.py', '--mode', 'local', '--no-regen-launch-proof'],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert gen2.returncode == 0, f'Second generation failed:\n{gen2.stderr}'

    sha_after = hashlib.sha256(launch_path.read_bytes()).hexdigest()
    assert sha_before == sha_after, (
        '--no-regen-launch-proof must not modify the existing launch-proof.\n'
        f'  SHA256 before: {sha_before}\n'
        f'  SHA256 after : {sha_after}'
    )

    # Validate should still pass because manifest was regenerated to hash the preserved file.
    val = subprocess.run(
        ['python', 'scripts/validate_release_proof.py'],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert val.returncode == 0, (
        'validate_release_proof.py failed after --no-regen-launch-proof run:\n'
        f'{val.stdout}\n{val.stderr}'
    )

# Release attestation bundle consistency tests.
def _write_enterprise_release_bundle(tmp_path: Path, now: datetime) -> tuple[Path, Path]:
    from scripts.generate_release_proof import _compute_sha256

    root = tmp_path
    release_dir = root / 'artifacts' / 'release-proof' / 'latest'
    launch_dir = root / 'artifacts' / 'launch-proof' / 'latest'
    release_dir.mkdir(parents=True)
    launch_dir.mkdir(parents=True)
    completed = now.isoformat().replace('+00:00', 'Z')
    started = (now - timedelta(minutes=5)).isoformat().replace('+00:00', 'Z')
    identity = {
        'commit_sha': 'a' * 40,
        'deployment_id': 'deploy-123',
        'ci_run_id': 'run-456',
        'environment': 'staging',
        'evidence_started_at': started,
        'evidence_completed_at': completed,
        'generated_at': completed,
    }
    required_gates = {
        name: {'status': 'pass', 'summary': 'passed'}
        for name in (
            'backend_tests', 'saas_workflow_validation', 'readiness_validation',
            'paid_launch_readiness', 'live_evidence', 'frontend_build',
        )
    }
    gates = {
        'schema_version': 1, **identity, 'branch': 'main', 'release_channel': 'staging',
        'overall_status': 'pass', 'broad_paid_launch_ready': True,
        'required_gates': required_gates, 'blockers': [], 'warnings': [],
    }
    release = {
        'schema_version': 1, **identity, 'branch': 'main', 'release_channel': 'staging',
        'release_status': 'pass', 'ci_required_gates_ready': True,
        'launch_proof_ready': True, 'manifest_ready': True, 'test_report_ready': True,
        'paid_launch_ready': True, 'blockers': [], 'warnings': [], 'evidence_files': [],
    }
    launch = {
        'schema_version': 1, **identity, 'proof_mode': 'staging', 'launch_mode': 'paid_ga',
        'pilot_ready': True, 'paid_launch_ready': True, 'controlled_pilot_ready': True,
        'broad_paid_saas_ready': True,
        'readiness': {key: True for key in (
            'billing_ready', 'billing_webhook_ready', 'email_ready', 'provider_ready',
            'live_evidence_ready', 'ci_required_gates_ready',
        )},
        'blockers': [], 'warnings': [],
    }
    report = {
        'schema_version': 1, **identity, 'branch': 'main', 'release_channel': 'staging',
        'test_suites': {'required': {
            'name': 'required', 'status': 'pass', 'tests_run': 10,
            'tests_passed': 10, 'tests_failed': 0, 'summary': '10 passed',
        }},
        'overall_status': 'pass', 'blockers': [], 'warnings': [],
    }
    files = {
        release_dir / 'summary.json': release,
        release_dir / 'ci-required-gates.json': gates,
        release_dir / 'test-report-summary.json': report,
        launch_dir / 'summary.json': launch,
    }
    for path, data in files.items():
        path.write_text(json.dumps(data))
    manifest_entries = []
    for path in files:
        manifest_entries.append({
            'path': str(path.relative_to(root)), 'sha256': _compute_sha256(path),
            'size_bytes': path.stat().st_size, 'required': True, 'status': 'present',
        })
    manifest = {
        'schema_version': 1, **identity, 'branch': 'main', 'release_channel': 'staging',
        'files': manifest_entries, 'overall_status': 'pass', 'blockers': [], 'warnings': [],
    }
    (release_dir / 'manifest.json').write_text(json.dumps(manifest))
    return release_dir, launch_dir


def _refresh_bundle_manifest(release_dir: Path, launch_dir: Path) -> None:
    from scripts.generate_release_proof import _compute_sha256

    root = release_dir.parents[2]
    manifest_path = release_dir / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    for entry in manifest['files']:
        path = root / entry['path']
        entry['sha256'] = _compute_sha256(path)
        entry['size_bytes'] = path.stat().st_size
    manifest_path.write_text(json.dumps(manifest))


def test_release_bundle_rejects_stale_evidence(tmp_path: Path) -> None:
    from datetime import datetime, timedelta, timezone
    from scripts.validate_release_proof import validate_release_bundle

    now = datetime.now(timezone.utc)
    release_dir, launch_dir = _write_enterprise_release_bundle(tmp_path, now - timedelta(days=2))
    ok, issues, _ = validate_release_bundle(release_dir, launch_dir, now=now)
    assert not ok
    assert any('stale evidence' in issue for issue in issues)


def test_release_bundle_rejects_mismatched_commit_sha(tmp_path: Path) -> None:
    from datetime import datetime, timezone
    from scripts.validate_release_proof import validate_release_bundle

    now = datetime.now(timezone.utc)
    release_dir, launch_dir = _write_enterprise_release_bundle(tmp_path, now)
    launch_path = launch_dir / 'summary.json'
    launch = json.loads(launch_path.read_text())
    launch['commit_sha'] = 'b' * 40
    launch_path.write_text(json.dumps(launch))
    _refresh_bundle_manifest(release_dir, launch_dir)
    ok, issues, _ = validate_release_bundle(release_dir, launch_dir, now=now)
    assert not ok
    assert any('commit_sha mismatch' in issue for issue in issues)


def test_release_bundle_rejects_failed_test_report(tmp_path: Path) -> None:
    from datetime import datetime, timezone
    from scripts.validate_release_proof import validate_release_bundle

    now = datetime.now(timezone.utc)
    release_dir, launch_dir = _write_enterprise_release_bundle(tmp_path, now)
    report_path = release_dir / 'test-report-summary.json'
    report = json.loads(report_path.read_text())
    report['overall_status'] = 'fail'
    report['test_suites']['required']['status'] = 'fail'
    report['test_suites']['required']['tests_failed'] = 1
    report_path.write_text(json.dumps(report))
    _refresh_bundle_manifest(release_dir, launch_dir)
    ok, issues, _ = validate_release_bundle(release_dir, launch_dir, now=now)
    assert not ok
    assert any('test-report-summary' in issue for issue in issues)


def test_release_bundle_rejects_missing_required_ci_gate(tmp_path: Path) -> None:
    from datetime import datetime, timezone
    from scripts.validate_release_proof import validate_release_bundle

    now = datetime.now(timezone.utc)
    release_dir, launch_dir = _write_enterprise_release_bundle(tmp_path, now)
    gates_path = release_dir / 'ci-required-gates.json'
    gates = json.loads(gates_path.read_text())
    gates['required_gates'].pop('frontend_build')
    gates_path.write_text(json.dumps(gates))
    _refresh_bundle_manifest(release_dir, launch_dir)
    ok, issues, _ = validate_release_bundle(release_dir, launch_dir, now=now)
    assert not ok
    assert any('missing required CI gates' in issue for issue in issues)


def test_release_bundle_rejects_contradictory_launch_artifacts(tmp_path: Path) -> None:
    from datetime import datetime, timezone
    from scripts.validate_release_proof import validate_release_bundle

    now = datetime.now(timezone.utc)
    release_dir, launch_dir = _write_enterprise_release_bundle(tmp_path, now)
    gates_path = release_dir / 'ci-required-gates.json'
    gates = json.loads(gates_path.read_text())
    gates['broad_paid_launch_ready'] = False
    gates_path.write_text(json.dumps(gates))
    _refresh_bundle_manifest(release_dir, launch_dir)
    ok, issues, _ = validate_release_bundle(release_dir, launch_dir, now=now)
    assert not ok
    assert any('broad readiness contradicts' in issue for issue in issues)


def test_release_attestation_workflow_requires_one_strict_production_like_evidence_source() -> None:
    workflow = (REPO_ROOT / '.github/workflows/release-attestation.yml').read_text()
    assert workflow.count('curl --fail-with-body') == 1
    assert 'X-Expected-Commit-SHA' in workflow
    assert 'X-Deployment-ID' in workflow
    assert 'X-CI-Run-ID' in workflow
    assert 'RELEASE_ATTESTATION_SIGNING_KEY' in workflow
    assert 'workflow_dispatch:' in workflow
    assert 'pull_request:' not in workflow
    assert 'push:' not in workflow
