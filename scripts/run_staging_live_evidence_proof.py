#!/usr/bin/env python3
"""
Run the staging live-provider evidence proof for Decoda RWA Guard (blocker 3).

This is the practical, one-command entry point for proving blocker 3
(live provider evidence) against a real EVM JSON-RPC endpoint.

What it does:
  1. Reads provider env vars (STAGING_* preferred over base vars):
       - STAGING_EVM_RPC_URL    or EVM_RPC_URL
       - STAGING_EVM_CHAIN_ID   or EVM_CHAIN_ID
       - STAGING_WORKER_ENABLED
  2. Prints a preflight checklist with the RPC URL masked.
  3. Fails closed (exit 1, no commands run) when any required env var is missing.
  4. When env vars are present, runs the real proof command chain:
       - python scripts/generate_live_evidence_proof.py --strict   (real RPC calls)
       - make generate-live-evidence-proof
       - make generate-staging-proof
       - make validate-staging-proof
       - python scripts/validate_100_percent_readiness.py --mode staging --strict
  5. Reads artifacts/live-evidence-proof/latest/summary.json and prints a final
     summary of the live-evidence outcome.

Fail-closed semantics:
  - Readiness is never faked. live_evidence_ready is read from the generated
    artifact; it is never hardcoded.
  - Exit 0 only when the artifact reports live_evidence_ready=true.
  - Missing RPC URL, chain id, or worker flag => exit 1 before any command runs.
  - The full RPC URL is never printed; only a masked form is shown.

Usage:
  python scripts/run_staging_live_evidence_proof.py
  make run-staging-live-proof
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]

LIVE_EVIDENCE_PROOF_PATH = (
    REPO_ROOT / 'artifacts' / 'live-evidence-proof' / 'latest' / 'summary.json'
)

_PLACEHOLDER_MARKERS = frozenset({
    'example', 'changeme', 'replace-me', 'placeholder', 'test-key', 'your_',
})

_TRUTHY = frozenset({'1', 'true', 'yes', 'on'})


def _env_val(env: Mapping[str, str], name: str) -> str:
    return (env.get(name) or '').strip()


def _has_placeholder(val: str) -> bool:
    return any(m in val.lower() for m in _PLACEHOLDER_MARKERS)


def mask_rpc_url(url: str) -> str:
    """
    Mask the secret / api-key segment of a JSON-RPC URL for safe display.

    https://host/v3/SECRET -> https://host/v3/[masked]

    Never returns the trailing key segment. Safe to print and log.
    """
    if not url:
        return ''
    parts = url.rstrip('/').rsplit('/', 1)
    if len(parts) == 2 and len(parts[1]) > 6:
        return parts[0] + '/[masked]'
    if len(url) > 20:
        return url[:20] + '...'
    return url


def read_provider_env(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Resolve provider env vars. STAGING_* takes precedence over base vars."""
    if env is None:
        env = os.environ

    staging_rpc = _env_val(env, 'STAGING_EVM_RPC_URL')
    base_rpc = _env_val(env, 'EVM_RPC_URL')
    if staging_rpc:
        rpc_url, rpc_source = staging_rpc, 'STAGING_EVM_RPC_URL'
    elif base_rpc:
        rpc_url, rpc_source = base_rpc, 'EVM_RPC_URL'
    else:
        rpc_url, rpc_source = '', 'STAGING_EVM_RPC_URL / EVM_RPC_URL'

    staging_chain = _env_val(env, 'STAGING_EVM_CHAIN_ID')
    base_chain = _env_val(env, 'EVM_CHAIN_ID')
    if staging_chain:
        chain_id, chain_source = staging_chain, 'STAGING_EVM_CHAIN_ID'
    elif base_chain:
        chain_id, chain_source = base_chain, 'EVM_CHAIN_ID'
    else:
        chain_id, chain_source = '', 'STAGING_EVM_CHAIN_ID / EVM_CHAIN_ID'

    worker_raw = _env_val(env, 'STAGING_WORKER_ENABLED')

    return {
        'rpc_url': rpc_url,
        'rpc_source': rpc_source,
        'rpc_url_masked': mask_rpc_url(rpc_url),
        'chain_id': chain_id,
        'chain_source': chain_source,
        'worker_enabled_raw': worker_raw,
        'worker_enabled': worker_raw.lower() in _TRUTHY,
    }


def build_preflight(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """
    Build the preflight checklist.

    ok is True only when all three requirements pass:
      - a real (non-placeholder) RPC URL
      - a real (non-placeholder) chain id
      - STAGING_WORKER_ENABLED set to a truthy value
    """
    resolved = read_provider_env(env)
    items: list[dict[str, Any]] = []
    missing: list[str] = []

    rpc_present = bool(resolved['rpc_url'])
    rpc_ok = rpc_present and not _has_placeholder(resolved['rpc_url'])
    if rpc_ok:
        rpc_detail = f"{resolved['rpc_source']} = {resolved['rpc_url_masked']}"
    elif rpc_present:
        rpc_detail = f"{resolved['rpc_source']} is a placeholder value"
        missing.append(
            'STAGING_EVM_RPC_URL or EVM_RPC_URL is a placeholder; '
            'set a real JSON-RPC endpoint'
        )
    else:
        rpc_detail = 'STAGING_EVM_RPC_URL / EVM_RPC_URL not set'
        missing.append('STAGING_EVM_RPC_URL or EVM_RPC_URL not configured')
    items.append({'name': 'RPC endpoint', 'ok': rpc_ok, 'detail': rpc_detail})

    chain_present = bool(resolved['chain_id'])
    chain_ok = chain_present and not _has_placeholder(resolved['chain_id'])
    if chain_ok:
        chain_detail = f"{resolved['chain_source']} = {resolved['chain_id']}"
    elif chain_present:
        chain_detail = f"{resolved['chain_source']} is a placeholder value"
        missing.append('STAGING_EVM_CHAIN_ID or EVM_CHAIN_ID is a placeholder')
    else:
        chain_detail = 'STAGING_EVM_CHAIN_ID / EVM_CHAIN_ID not set'
        missing.append('STAGING_EVM_CHAIN_ID or EVM_CHAIN_ID not configured')
    items.append({'name': 'Chain ID', 'ok': chain_ok, 'detail': chain_detail})

    worker_ok = resolved['worker_enabled']
    if worker_ok:
        worker_detail = 'STAGING_WORKER_ENABLED = true'
    else:
        worker_detail = 'STAGING_WORKER_ENABLED not set to true'
        missing.append('STAGING_WORKER_ENABLED not set to true')
    items.append({'name': 'Worker enabled', 'ok': worker_ok, 'detail': worker_detail})

    return {
        'ok': not missing,
        'items': items,
        'missing': missing,
        'resolved': resolved,
    }


def _hr(char: str = '=') -> str:
    return char * 70


def print_preflight(preflight: dict[str, Any]) -> None:
    print(_hr())
    print(' Decoda RWA Guard - Staging Live Evidence Proof (blocker 3)')
    print(_hr())
    print('')
    print('Preflight checklist:')
    for item in preflight['items']:
        marker = '[ OK ]' if item['ok'] else '[FAIL]'
        print(f"  {marker}  {item['name']:<16}{item['detail']}")
    print('')


def print_fail_closed_remediation() -> None:
    print('FAIL-CLOSED: required provider environment variables are missing.')
    print('Blocker 3 (live provider evidence) cannot be proven without a real')
    print('EVM JSON-RPC provider. This is expected and safe - a local proof')
    print('without a real provider must fail closed.')
    print('')
    print('To run the real staging live evidence proof, set:')
    print('  STAGING_EVM_RPC_URL    a real Ethereum-compatible JSON-RPC endpoint')
    print('  STAGING_EVM_CHAIN_ID   the chain id (e.g. 1 for Ethereum mainnet)')
    print('  STAGING_WORKER_ENABLED set to true')
    print('')
    print('Example:')
    print('  export STAGING_EVM_RPC_URL=https://mainnet.infura.io/v3/<project-id>')
    print('  export STAGING_EVM_CHAIN_ID=1')
    print('  export STAGING_WORKER_ENABLED=true')
    print('  make run-staging-live-proof')
    print('')
    print('Do not commit real provider secrets. See .env.staging.example.')


def load_live_evidence_summary(path: Path) -> dict[str, Any] | None:
    """Load the live-evidence-proof artifact. Returns None when absent/unreadable."""
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def build_final_summary(proof: dict[str, Any] | None) -> dict[str, Any]:
    """
    Extract the blocker-3 outcome fields from a live-evidence-proof artifact.

    All flags fail closed: a missing or unreadable artifact yields
    live_evidence_ready=false.
    """
    has_proof = bool(proof) and 'live_provider_evidence' in (proof or {})
    lpe = (proof or {}).get('live_provider_evidence') or {}
    chain = lpe.get('chain') or {}
    return {
        'artifact_present': has_proof,
        'provider_ready': bool(lpe.get('provider_ready')),
        'provider_mode': lpe.get('provider_mode') or 'unknown',
        'provider_health_checked': bool(lpe.get('provider_health_checked')),
        'evidence_source': lpe.get('evidence_source') or 'unknown',
        'latest_live_telemetry_at': lpe.get('latest_live_telemetry_at'),
        'live_evidence_ready': bool(lpe.get('live_evidence_ready')),
        'telemetry_event_id': chain.get('telemetry_event_id'),
        'detection_id': chain.get('detection_id'),
        'alert_id': chain.get('alert_id'),
        'incident_id': chain.get('incident_id'),
        'response_action_id': chain.get('response_action_id'),
        'evidence_package_id': chain.get('evidence_package_id'),
        'missing': list(lpe.get('missing') or []),
        'contradiction_flags': list(lpe.get('contradiction_flags') or []),
    }


def print_final_summary(
    summary: dict[str, Any],
    command_results: list[tuple[str, int]],
    artifact_path: Path,
) -> None:
    print('')
    print(_hr())
    print(' Final summary - staging live evidence proof')
    print(_hr())

    if command_results:
        print('')
        print('Command results:')
        for label, rc in command_results:
            status = 'ok' if rc == 0 else f'exit {rc}'
            print(f'  [{status}]  {label}')

    try:
        rel = artifact_path.relative_to(REPO_ROOT)
    except ValueError:
        rel = artifact_path
    print('')
    print(f'Live evidence artifact: {rel}')
    if not summary['artifact_present']:
        print('  (artifact missing or unreadable - treating as fail closed)')

    print('')
    print('Live provider evidence:')
    for key in (
        'provider_ready', 'provider_mode', 'provider_health_checked',
        'evidence_source', 'latest_live_telemetry_at', 'live_evidence_ready',
    ):
        print(f'  {key}={summary[key]}')

    print('')
    print('Live evidence chain:')
    print(f"  telemetry_event_id={summary['telemetry_event_id']}")
    print(f"  detection_id={summary['detection_id']}")
    print(f"  alert_id={summary['alert_id']}")
    incident = summary['incident_id']
    response = summary['response_action_id']
    if incident:
        print(f'  incident_id={incident}')
    elif response:
        print(f'  response_action_id={response}')
    else:
        print('  incident_id or response_action_id=None')
    print(f"  evidence_package_id={summary['evidence_package_id']}")

    if summary['missing']:
        print('')
        print('Missing (blocks live evidence):')
        for item in summary['missing']:
            print(f'  - {item}')
    if summary['contradiction_flags']:
        print('')
        print('Contradiction flags:')
        for flag in summary['contradiction_flags']:
            print(f'  - {flag}')

    print('')
    if summary['live_evidence_ready']:
        print('BLOCKER 3: PASS - live_evidence_ready=true (real live provider evidence).')
        if any(rc != 0 for _, rc in command_results):
            print('')
            print('Note: blocker 3 (live provider evidence) is proven. One or more')
            print('downstream gates exited non-zero - broad paid-SaaS readiness also')
            print('requires billing / email / staging-URL configuration beyond')
            print('blocker 3. See the command output above for details.')
    else:
        print('BLOCKER 3: FAIL - live_evidence_ready=false (fail closed).')
        print('No real live provider evidence was produced. See missing items above.')
    print(_hr())


def _default_runner(label: str, cmd: list[str]) -> int:
    """Run a single proof command as a subprocess, inheriting the environment."""
    print('')
    print(_hr('-'))
    print(f'[run-staging-live-proof] {label}')
    print(f"[run-staging-live-proof] $ {' '.join(cmd)}")
    print(_hr('-'))
    try:
        proc = subprocess.run(cmd, cwd=str(REPO_ROOT))
    except FileNotFoundError as exc:
        print(f'[run-staging-live-proof] command not found: {exc}')
        return 127
    return proc.returncode


def _proof_commands() -> list[tuple[str, list[str]]]:
    py = sys.executable or 'python'
    scripts_dir = REPO_ROOT / 'scripts'
    return [
        (
            'Live evidence proof (real RPC, strict)',
            [py, str(scripts_dir / 'generate_live_evidence_proof.py'), '--strict'],
        ),
        ('make generate-live-evidence-proof', ['make', 'generate-live-evidence-proof']),
        ('make generate-staging-proof', ['make', 'generate-staging-proof']),
        ('make validate-staging-proof', ['make', 'validate-staging-proof']),
        (
            'Validate 100% readiness (staging, strict)',
            [
                py, str(scripts_dir / 'validate_100_percent_readiness.py'),
                '--mode', 'staging', '--strict',
            ],
        ),
    ]


def run_staging_live_evidence_proof(
    *,
    env: Mapping[str, str] | None = None,
    runner: Callable[[str, list[str]], int] | None = None,
    live_evidence_proof_path: Path | None = None,
) -> int:
    """
    Run the full staging live evidence proof.

    Returns 0 only when the live-evidence-proof artifact reports
    live_evidence_ready=true. Returns 1 in every fail-closed case.
    """
    if env is None:
        env = os.environ
    if runner is None:
        runner = _default_runner
    if live_evidence_proof_path is None:
        live_evidence_proof_path = LIVE_EVIDENCE_PROOF_PATH

    preflight = build_preflight(env)
    print_preflight(preflight)

    if not preflight['ok']:
        print_fail_closed_remediation()
        return 1

    print('Preflight passed. Running staging live evidence proof commands...')

    command_results: list[tuple[str, int]] = []
    for label, cmd in _proof_commands():
        rc = runner(label, cmd)
        command_results.append((label, rc))

    proof = load_live_evidence_summary(live_evidence_proof_path)
    summary = build_final_summary(proof)
    print_final_summary(summary, command_results, live_evidence_proof_path)

    return 0 if summary['live_evidence_ready'] else 1


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if '-h' in argv or '--help' in argv:
        print(__doc__ or '')
        return 0
    return run_staging_live_evidence_proof()


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
