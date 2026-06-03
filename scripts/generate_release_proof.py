#!/usr/bin/env python3
"""
Generate canonical CI/release evidence and launch proof artifacts.

This script creates five JSON files that provide fail-closed proof of:
1. CI required gates status
2. Release proof summary
3. Launch proof summary
4. Deterministic artifact manifest with SHA256 integrity
5. Machine-readable test report summary

Never includes secret values. Fails closed when expected proof is absent.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

try:
    from services.api.app.paid_launch_readiness import build_paid_launch_readiness
    _PAID_LAUNCH_IMPORT_OK = True
except ImportError as _paid_launch_import_err:
    _PAID_LAUNCH_IMPORT_OK = False
    _paid_launch_import_reason = str(_paid_launch_import_err)

    def build_paid_launch_readiness() -> dict:  # type: ignore[misc]
        return {
            'paid_launch_ready': False,
            'billing_ready': False,
            'billing_webhook_ready': False,
            'email_ready': False,
            'provider_ready': False,
            'paid_launch_blockers': [
                f'backend dependencies not installed ({_paid_launch_import_reason}); '
                'run: pip install -r services/api/requirements.txt'
            ],
        }


@dataclass
class GateResult:
    status: str  # pass, fail, not_run
    command: str
    summary: str


def _git_info() -> tuple[str, str]:
    """Get current commit SHA and branch name."""
    try:
        sha = subprocess.check_output(
            ['git', 'rev-parse', 'HEAD'],
            cwd=REPO_ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        sha = 'unknown'

    try:
        branch = subprocess.check_output(
            ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
            cwd=REPO_ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        branch = 'unknown'

    return sha, branch


def _run_pytest(test_file: str) -> tuple[bool, str]:
    """Run pytest and return (passed, summary)."""
    try:
        result = subprocess.run(
            ['python', '-m', 'pytest', test_file, '-q'],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            timeout=60,
        )
        passed = result.returncode == 0
        summary = (result.stdout + result.stderr).strip() or 'Test passed'
        return passed, summary
    except subprocess.TimeoutExpired:
        return False, 'Test timed out'
    except Exception as e:
        return False, f'Error running test: {e}'


def _run_validation(script_path: str, env: dict[str, str] | None = None) -> tuple[bool, str]:
    """Run a validation script and return (passed, summary)."""
    try:
        result = subprocess.run(
            ['python', script_path],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            timeout=30,
            env=env or os.environ.copy(),
        )
        passed = result.returncode == 0
        summary = (result.stdout + result.stderr).strip() or 'Validation passed'
        return passed, summary
    except subprocess.TimeoutExpired:
        return False, 'Validation timed out'
    except Exception as e:
        return False, f'Error running validation: {e}'


def _check_live_evidence() -> tuple[bool, list[str]]:
    """
    Check if live evidence proof is available.

    Priority:
    1. Canonical live-evidence-proof artifact
       (artifacts/live-evidence-proof/latest/summary.json)
    2. Canonical service live evidence summary
       (services/api/artifacts/live_evidence/latest/summary.json)
       — checked even when (1) exists but reports not ready, to avoid stale-artifact
         contradictions where the service proves live evidence but the top-level
         artifact is stale.
    3. Legacy path (services/api/artifacts/...) for backward compatibility.
    """
    blockers: list[str] = []

    service_summary_path = (
        REPO_ROOT / 'services' / 'api' / 'artifacts' / 'live_evidence' / 'latest' / 'summary.json'
    )

    def _service_summary_is_live() -> bool:
        """Return True when the canonical service summary reports live evidence."""
        if not service_summary_path.exists():
            return False
        try:
            with open(service_summary_path) as f:
                svc = json.load(f)
            return (
                str(svc.get('evidence_source') or '').strip().lower() == 'live'
                and svc.get('live_evidence_ready') is True
                and svc.get('provider_ready') is True
            )
        except Exception:
            return False

    # Primary: canonical live-evidence-proof artifact (strict source of truth).
    # When this artifact exists and reports false, that answer is authoritative —
    # no service summary fallback can override it.
    canonical_path = REPO_ROOT / 'artifacts' / 'live-evidence-proof' / 'latest' / 'summary.json'
    if canonical_path.exists():
        try:
            with open(canonical_path) as f:
                proof = json.load(f)
            lpe = proof.get('live_provider_evidence', {})
            if lpe.get('live_evidence_ready') is True:
                return True, []
            missing = lpe.get('missing', [])
            if missing:
                blockers.append(f'live evidence not ready: {missing[0]}')
            else:
                blockers.append(
                    'live evidence not ready (live_evidence_ready=false in live-evidence-proof)'
                )
            return False, blockers
        except Exception as e:
            blockers.append(f'failed to read live-evidence-proof: {e}')
            return False, blockers

    # Secondary: service summary (covers the case where canonical artifact hasn't been
    # generated yet but the backend has already produced real live evidence)
    if _service_summary_is_live():
        return True, []

    # Fallback: legacy path for backward compatibility
    legacy_path = service_summary_path  # same path, already resolved above
    if not legacy_path.exists():
        blockers.append('live evidence summary not found')
        return False, blockers

    try:
        with open(legacy_path) as f:
            evidence = json.load(f)
        evidence_source = evidence.get('evidence_source', '').lower()
        if evidence_source != 'live':
            blockers.append(f'evidence source is {evidence_source}, not live')
            return False, blockers
        return True, []
    except Exception as e:
        blockers.append(f'failed to read live evidence: {e}')
        return False, blockers


def generate_ci_required_gates(*, mode: str, strict: bool = False) -> dict[str, Any]:
    """Generate CI required gates proof."""
    commit_sha, branch = _git_info()

    gates: dict[str, Any] = {
        'backend_tests': {
            'status': 'not_run',
            'command': 'python -m pytest services/api/tests/ -q',
            'summary': 'Not run in local mode'
        },
        'saas_workflow_validation': {
            'status': 'not_run',
            'command': 'python services/api/scripts/validate_staging.py',
            'summary': 'Not run in local mode'
        },
        'readiness_validation': {
            'status': 'not_run',
            'command': 'python services/api/scripts/validate_production_readiness.py',
            'summary': 'Not run in local mode'
        },
        'paid_launch_readiness': {
            'status': 'not_run',
            'summary': 'Checking paid launch gates...',
            'blockers': []
        },
        'live_evidence': {
            'status': 'not_run',
            'summary': 'Not run in local mode',
            'blockers': []
        },
        'frontend_build': {
            'status': 'not_run',
            'command': 'npm run build',
            'summary': 'Not run in local mode'
        },
    }

    # Check paid launch readiness
    paid_launch = build_paid_launch_readiness()
    gates['paid_launch_readiness']['status'] = 'pass' if paid_launch['paid_launch_ready'] else 'fail'
    gates['paid_launch_readiness']['blockers'] = paid_launch.get('paid_launch_blockers', [])
    gates['paid_launch_readiness']['summary'] = (
        'All paid launch gates pass' if paid_launch['paid_launch_ready']
        else f"Paid launch blocked: {'; '.join(paid_launch.get('paid_launch_blockers', []))}"
    )

    # Check live evidence
    live_ok, live_blockers = _check_live_evidence()
    gates['live_evidence']['status'] = 'pass' if live_ok else 'fail'
    gates['live_evidence']['blockers'] = live_blockers
    gates['live_evidence']['summary'] = (
        'Live evidence available' if live_ok
        else f"Live evidence not available: {'; '.join(live_blockers)}"
    )

    # Overall status: only pass if all gates pass
    gate_statuses = [
        gates[key]['status'] for key in gates
        if gates[key]['status'] in {'pass', 'fail'}
    ]
    overall_pass = all(status == 'pass' for status in gate_statuses if status != 'not_run')

    blockers: list[str] = []

    # Collect blockers
    for gate_name, gate_data in gates.items():
        if gate_data.get('status') == 'fail':
            blockers.extend(gate_data.get('blockers', []))

    # Add not_run as blockers in strict mode
    if strict:
        for gate_name, gate_data in gates.items():
            if gate_data.get('status') == 'not_run':
                blockers.append(f'{gate_name} not run in strict mode')

    return {
        'schema_version': 1,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'commit_sha': commit_sha,
        'branch': branch,
        'release_channel': mode,
        'overall_status': 'pass' if overall_pass and not blockers else 'fail',
        'broad_paid_launch_ready': False,  # Never pass broad launch in local mode
        'required_gates': gates,
        'blockers': sorted(set(blockers)),
        'warnings': []
    }


def generate_release_proof(*, mode: str, strict: bool = False) -> dict[str, Any]:
    """Generate release proof summary."""
    commit_sha, branch = _git_info()

    ci_gates_ready = False
    launch_proof_ready = False
    manifest_ready = False
    test_report_ready = False

    # Check if ci-required-gates artifact exists and is passing
    ci_gates_path = REPO_ROOT / 'artifacts' / 'release-proof' / 'latest' / 'ci-required-gates.json'
    if ci_gates_path.exists():
        try:
            with open(ci_gates_path) as f:
                ci_gates = json.load(f)
            ci_gates_ready = ci_gates.get('overall_status') == 'pass'
        except:
            pass

    # Check if launch-proof artifact exists and is passing
    launch_proof_path = REPO_ROOT / 'artifacts' / 'launch-proof' / 'latest' / 'summary.json'
    if launch_proof_path.exists():
        try:
            with open(launch_proof_path) as f:
                launch_proof = json.load(f)
            launch_proof_ready = launch_proof.get('pilot_ready', False)
        except:
            pass

    # Check if manifest artifact exists
    manifest_path = REPO_ROOT / 'artifacts' / 'release-proof' / 'latest' / 'manifest.json'
    if manifest_path.exists():
        try:
            with open(manifest_path) as f:
                manifest = json.load(f)
            manifest_ready = manifest.get('overall_status') == 'pass'
        except:
            pass

    # Check if test-report-summary artifact exists
    test_report_path = REPO_ROOT / 'artifacts' / 'release-proof' / 'latest' / 'test-report-summary.json'
    if test_report_path.exists():
        try:
            with open(test_report_path) as f:
                test_report = json.load(f)
            test_report_ready = test_report.get('overall_status') != 'fail'
        except:
            pass

    blockers: list[str] = []
    if not ci_gates_ready:
        blockers.append('ci-required-gates not ready')
    if not launch_proof_ready:
        blockers.append('launch-proof not ready')
    if not manifest_ready:
        blockers.append('manifest not ready')
    if not test_report_ready:
        blockers.append('test-report-summary not ready')

    release_ready = (
        ci_gates_ready and launch_proof_ready and manifest_ready and test_report_ready
        and not blockers
    )

    return {
        'schema_version': 1,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'release_status': 'pass' if release_ready else 'fail',
        'release_channel': mode,
        'commit_sha': commit_sha,
        'branch': branch,
        'ci_required_gates_ready': ci_gates_ready,
        'launch_proof_ready': launch_proof_ready,
        'manifest_ready': manifest_ready,
        'test_report_ready': test_report_ready,
        'paid_launch_ready': False,  # Never pass broad paid launch in local mode
        'blockers': sorted(set(blockers)),
        'warnings': [],
        'evidence_files': [
            'artifacts/release-proof/latest/ci-required-gates.json',
            'artifacts/release-proof/latest/manifest.json',
            'artifacts/release-proof/latest/test-report-summary.json',
            'artifacts/launch-proof/latest/summary.json'
        ]
    }


def generate_launch_proof(*, mode: str) -> dict[str, Any]:
    """Generate launch proof summary."""
    commit_sha, branch = _git_info()

    # Check paid launch readiness
    paid_launch = build_paid_launch_readiness()

    # Check if we can claim pilot readiness
    # For local mode, be fail-closed: assume pilot requires live evidence
    live_ok, _ = _check_live_evidence()

    pilot_ready = live_ok  # Fail closed: local mode requires live evidence
    controlled_pilot_ready = True  # Can be true for controlled pilots without full paid GA
    broad_paid_saas_ready = False  # Never pass in local mode
    paid_launch_ready = False  # Never pass in local mode

    # provider_ready: prefer paid_launch result; fall back to service summary when
    # live evidence is proven (service summary already confirms provider was reachable).
    provider_ready = paid_launch.get('provider_ready', False)
    if live_ok and not provider_ready:
        _svc_path = (
            REPO_ROOT / 'services' / 'api' / 'artifacts' / 'live_evidence' / 'latest' / 'summary.json'
        )
        try:
            if _svc_path.exists():
                with open(_svc_path) as _f:
                    _svc = json.load(_f)
                if _svc.get('provider_ready') is True and _svc.get('evidence_source', '').lower() == 'live':
                    provider_ready = True
        except Exception:
            pass

    readiness = {
        'billing_ready': paid_launch.get('billing_ready', False),
        'billing_webhook_ready': paid_launch.get('billing_webhook_ready', False),
        'email_ready': paid_launch.get('email_ready', False),
        'provider_ready': provider_ready,
        'live_evidence_ready': live_ok,
        'ci_required_gates_ready': False,  # Check if gates exist and pass
    }

    # Check ci-required-gates
    ci_gates_path = REPO_ROOT / 'artifacts' / 'release-proof' / 'latest' / 'ci-required-gates.json'
    if ci_gates_path.exists():
        try:
            with open(ci_gates_path) as f:
                ci_gates = json.load(f)
            readiness['ci_required_gates_ready'] = ci_gates.get('overall_status') == 'pass'
        except:
            pass

    blockers: list[str] = []

    # Collect blockers for paid launch
    if not paid_launch.get('billing_ready'):
        blockers.append('billing not ready')
    if not paid_launch.get('billing_webhook_ready'):
        blockers.append('billing webhook not ready')
    if not paid_launch.get('email_ready'):
        blockers.append('email not ready')
    # Use readiness['provider_ready'] which incorporates service summary fallback
    if not readiness['provider_ready']:
        blockers.append('provider not ready')
    if not readiness['live_evidence_ready']:
        blockers.append('live evidence not ready')
    if not readiness['ci_required_gates_ready']:
        blockers.append('ci gates not ready')

    # managed_pilot_ready: read from sell-now-proof artifact (file read, fail-closed).
    managed_pilot_ready = False
    _sell_now_path = REPO_ROOT / 'artifacts' / 'sell-now-proof' / 'latest' / 'summary.json'
    if _sell_now_path.exists():
        try:
            with open(_sell_now_path) as _f:
                _sell = json.load(_f)
            managed_pilot_ready = _sell.get('sell_now_managed_ready') is True
        except Exception:
            pass

    # niw_positioning_ready: run the lightweight validator script (fail-closed on any error).
    niw_positioning_ready = False
    try:
        _niw = subprocess.run(
            ['python', 'scripts/validate_niw_positioning.py'],
            cwd=REPO_ROOT,
            capture_output=True,
            timeout=30,
        )
        niw_positioning_ready = _niw.returncode == 0
    except Exception:
        pass

    # readiness_categories: granular truth table required by cross-validation tests.
    readiness_categories = {
        'live_provider_evidence_ready': readiness.get('live_evidence_ready', False),
        'managed_pilot_ready': managed_pilot_ready,
        'niw_positioning_ready': niw_positioning_ready,
        'broad_paid_saas_ready': broad_paid_saas_ready,
        'ci_required_gates_ready': readiness.get('ci_required_gates_ready', False),
    }

    # allowed_claims / prohibited_claims: required by launch-proof cross-validation tests.
    allowed_claims: list[str] = []
    if niw_positioning_ready:
        allowed_claims.append('NIW Strategic Infrastructure Guard positioning ready')
    if managed_pilot_ready:
        allowed_claims.append('controlled pilot / managed sale ready')
    if readiness.get('live_evidence_ready'):
        allowed_claims.append('live provider evidence ready')
    allowed_claims.append('not broad paid SaaS ready')

    prohibited_claims = [
        'broad paid SaaS production ready',
        'billing ready',
        'staging runtime fully ready',
        'staging database fully ready',
        'worker fully ready',
    ]

    return {
        'schema_version': 1,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'launch_mode': 'paid_ga' if broad_paid_saas_ready else 'pilot',
        'pilot_ready': pilot_ready,
        'paid_launch_ready': paid_launch_ready,
        'controlled_pilot_ready': controlled_pilot_ready,
        'broad_paid_saas_ready': broad_paid_saas_ready,
        'readiness_categories': readiness_categories,
        'allowed_claims': allowed_claims,
        'prohibited_claims': prohibited_claims,
        'readiness': readiness,
        'blockers': sorted(set(blockers)),
        'warnings': [],
        'artifact_paths': {
            'ci_required_gates': 'artifacts/release-proof/latest/ci-required-gates.json',
            'release_summary': 'artifacts/release-proof/latest/summary.json'
        }
    }


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


def generate_artifact_manifest(
    release_proof_dir: Path,
    launch_proof_dir: Path,
    *,
    mode: str
) -> dict[str, Any]:
    """Generate manifest of all release proof artifacts with SHA256 integrity."""
    commit_sha, branch = _git_info()

    required_files = [
        release_proof_dir / 'summary.json',
        release_proof_dir / 'ci-required-gates.json',
        launch_proof_dir / 'summary.json',
    ]

    files: list[dict[str, Any]] = []
    blockers: list[str] = []

    for fpath in required_files:
        # Try to compute relative path, but fall back if paths are outside REPO_ROOT
        try:
            rel_path = fpath.relative_to(REPO_ROOT)
            path_str = str(rel_path)
        except ValueError:
            # Path is outside REPO_ROOT, use computed relative path
            path_str = str(fpath.relative_to(fpath.anchor) if fpath.is_absolute() else fpath)

        if not fpath.exists():
            blockers.append(f'required file missing: {path_str}')
            files.append({
                'path': path_str,
                'sha256': 'missing',
                'size_bytes': 0,
                'required': True,
                'status': 'missing'
            })
        else:
            file_size = fpath.stat().st_size
            sha256 = _compute_sha256(fpath)
            files.append({
                'path': path_str,
                'sha256': sha256,
                'size_bytes': file_size,
                'required': True,
                'status': 'present'
            })

    overall_status = 'fail' if blockers else 'pass'

    return {
        'schema_version': 1,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'release_channel': mode,
        'commit_sha': commit_sha,
        'branch': branch,
        'files': files,
        'overall_status': overall_status,
        'blockers': sorted(set(blockers)),
        'warnings': []
    }


def generate_test_report_summary(*, mode: str) -> dict[str, Any]:
    """Generate machine-readable test report summary."""
    commit_sha, branch = _git_info()

    test_suites: dict[str, Any] = {
        'release_proof_artifacts': {
            'name': 'release-proof-artifacts',
            'status': 'not_run',
            'tests_run': 0,
            'tests_passed': 0,
            'tests_failed': 0,
            'summary': 'Test not run in local generation mode'
        }
    }

    blockers: list[str] = []
    if mode == 'local':
        blockers.append('test suite not executed in local mode')

    overall_test_status = 'not_run' if mode == 'local' else 'fail'
    if blockers:
        overall_test_status = 'fail'

    return {
        'schema_version': 1,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'release_channel': mode,
        'commit_sha': commit_sha,
        'branch': branch,
        'test_suites': test_suites,
        'overall_status': overall_test_status,
        'blockers': sorted(set(blockers)),
        'warnings': []
    }


def main(mode: str = 'local', strict: bool = False, regen_launch_proof: bool = True) -> int:
    """Generate all five proof artifacts in correct order.

    When regen_launch_proof=False the launch-proof is preserved as-is so that a
    richer proof written by run_paid_saas_launch_proof.py / run_no_billing_launch_proof.py
    is not overwritten.  The manifest is still generated after all other files are
    finalised, so it hashes the current launch-proof on disk.
    """
    print(f'[generate-release-proof] mode={mode} strict={strict} regen_launch_proof={regen_launch_proof}')

    # Create artifact directories
    release_proof_dir = REPO_ROOT / 'artifacts' / 'release-proof' / 'latest'
    launch_proof_dir = REPO_ROOT / 'artifacts' / 'launch-proof' / 'latest'
    release_proof_dir.mkdir(parents=True, exist_ok=True)
    launch_proof_dir.mkdir(parents=True, exist_ok=True)

    launch_proof_path = launch_proof_dir / 'summary.json'

    # Phase 1: Generate and write independent first-order artifacts
    ci_gates = generate_ci_required_gates(mode=mode, strict=strict)
    ci_gates_path = release_proof_dir / 'ci-required-gates.json'
    with open(ci_gates_path, 'w') as f:
        json.dump(ci_gates, f, indent=2)
    print(f'[generate-release-proof] wrote {ci_gates_path.relative_to(REPO_ROOT)}')

    if regen_launch_proof:
        launch_proof = generate_launch_proof(mode=mode)
        with open(launch_proof_path, 'w') as f:
            json.dump(launch_proof, f, indent=2)
        print(f'[generate-release-proof] wrote {launch_proof_path.relative_to(REPO_ROOT)}')
    elif launch_proof_path.exists():
        print(f'[generate-release-proof] preserved existing {launch_proof_path.relative_to(REPO_ROOT)}')
    else:
        # Fallback: no prior launch-proof exists, generate one so the manifest can hash it
        print(f'[generate-release-proof] WARNING: --no-regen-launch-proof set but no existing launch-proof found; generating fallback')
        launch_proof = generate_launch_proof(mode=mode)
        with open(launch_proof_path, 'w') as f:
            json.dump(launch_proof, f, indent=2)
        print(f'[generate-release-proof] wrote {launch_proof_path.relative_to(REPO_ROOT)} (fallback)')

    # Phase 2: Generate release-proof summary (references ci-gates and launch-proof)
    release_proof = generate_release_proof(mode=mode, strict=strict)
    release_proof_path = release_proof_dir / 'summary.json'
    with open(release_proof_path, 'w') as f:
        json.dump(release_proof, f, indent=2)
    print(f'[generate-release-proof] wrote {release_proof_path.relative_to(REPO_ROOT)}')

    # Phase 3: Generate test-report-summary (independent)
    test_report = generate_test_report_summary(mode=mode)
    test_report_path = release_proof_dir / 'test-report-summary.json'
    with open(test_report_path, 'w') as f:
        json.dump(test_report, f, indent=2)
    print(f'[generate-release-proof] wrote {test_report_path.relative_to(REPO_ROOT)}')

    # Phase 4: Generate manifest (now all other files exist with correct hashes)
    manifest = generate_artifact_manifest(release_proof_dir, launch_proof_dir, mode=mode)
    manifest_path = release_proof_dir / 'manifest.json'
    with open(manifest_path, 'w') as f:
        json.dump(manifest, f, indent=2)
    print(f'[generate-release-proof] wrote {manifest_path.relative_to(REPO_ROOT)}')

    # Determine exit code
    if strict:
        # In strict mode, fail if any required gate is not passing
        if ci_gates['overall_status'] != 'pass':
            print(f'[generate-release-proof] FAIL: ci-required-gates not passing')
            return 1
        if release_proof['release_status'] != 'pass':
            print(f'[generate-release-proof] FAIL: release-proof not passing')
            return 1

    return 0


if __name__ == '__main__':
    mode = 'local'
    strict = False
    regen_launch_proof = True

    if len(sys.argv) > 1:
        if '--mode' in sys.argv:
            idx = sys.argv.index('--mode')
            if idx + 1 < len(sys.argv):
                mode = sys.argv[idx + 1]
        if '--strict' in sys.argv:
            strict = True
        if '--no-regen-launch-proof' in sys.argv:
            regen_launch_proof = False

    raise SystemExit(main(mode=mode, strict=strict, regen_launch_proof=regen_launch_proof))
