#!/usr/bin/env python3
"""
Validate the live-evidence-proof artifact.

Checks:
  1. Proof file exists and is parseable JSON.
  2. Freshness: generated_at must be within the last 20 minutes.
  3. If EVM_RPC_URL or STAGING_EVM_RPC_URL is configured:
       - provider_ready must be true
       - live_evidence_source must be "live_rpc"
       - live_evidence_ready must be true
       - run_id must exist
       - All chain elements (telemetry, detection, alert, incident,
         response_action, evidence_package) must share the same run_id
  4. Fail if EVM_RPC_URL is configured but live_evidence_source="unknown"
  5. Fail if provider_ready=true but no matching telemetry_event_id

Exit codes:
  0 — proof is valid
  1 — proof is invalid (error messages printed to stderr)

Usage:
  python scripts/validate_live_evidence_proof.py
  python scripts/validate_live_evidence_proof.py --proof-path path/to/summary.json
  python scripts/validate_live_evidence_proof.py --no-rpc-required
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_DEFAULT_PROOF_PATH = REPO_ROOT / 'artifacts' / 'live-evidence-proof' / 'latest' / 'summary.json'
_FRESHNESS_MINUTES = 20
_PLACEHOLDER_MARKERS = frozenset({
    'example', 'changeme', 'replace-me', 'placeholder', 'test-key', 'your_',
})


def _env_val(name: str) -> str:
    return (os.getenv(name) or '').strip()


def _has_placeholder(val: str) -> bool:
    return any(m in val.lower() for m in _PLACEHOLDER_MARKERS)


def _rpc_configured() -> bool:
    for var in ('STAGING_EVM_RPC_URL', 'EVM_RPC_URL'):
        val = _env_val(var)
        if val and not _has_placeholder(val):
            return True
    return False


def validate_live_evidence_proof(
    proof_path: Path | None = None,
    *,
    require_rpc: bool | None = None,
    freshness_minutes: int = _FRESHNESS_MINUTES,
) -> tuple[bool, list[str]]:
    """
    Validate the live evidence proof artifact.

    Args:
        proof_path: path to summary.json (default: artifacts/live-evidence-proof/latest/summary.json)
        require_rpc: when True, always require live_rpc evidence; when False, skip;
                     when None (default), derive from EVM_RPC_URL / STAGING_EVM_RPC_URL env vars.
        freshness_minutes: maximum age of proof in minutes (default: 20)

    Returns:
        (ok, errors) where ok=True means proof is valid, errors is a list of failure messages.
    """
    path = proof_path or _DEFAULT_PROOF_PATH
    errors: list[str] = []

    if not path.exists():
        errors.append(f'proof file not found: {path}')
        return False, errors

    try:
        proof = json.loads(path.read_text())
    except Exception as exc:
        errors.append(f'proof file is not valid JSON: {exc}')
        return False, errors

    generated_at_str = str(proof.get('generated_at') or '')
    if not generated_at_str:
        errors.append('proof missing generated_at field')
        return False, errors

    try:
        generated_at = datetime.fromisoformat(generated_at_str)
        if generated_at.tzinfo is None:
            generated_at = generated_at.replace(tzinfo=timezone.utc)
        age_minutes = (datetime.now(timezone.utc) - generated_at).total_seconds() / 60
    except Exception as exc:
        errors.append(f'cannot parse generated_at {generated_at_str!r}: {exc}')
        return False, errors

    print(f'- live-evidence-proof is fresh ({age_minutes:.1f} min old, {generated_at_str})')

    if age_minutes > freshness_minutes:
        errors.append(
            f'live-evidence-proof is stale — generated {age_minutes:.1f} min ago '
            f'({generated_at_str}); expected < {freshness_minutes} min'
        )

    lpe = proof.get('live_provider_evidence', {})
    pr = bool(lpe.get('provider_ready'))
    le = bool(lpe.get('live_evidence_ready'))
    evidence_source = str(lpe.get('evidence_source') or '').strip().lower()
    live_evidence_source = str(lpe.get('live_evidence_source') or '').strip().lower()
    run_id = str(lpe.get('run_id') or '').strip()
    chain = lpe.get('chain') or {}
    missing = lpe.get('missing') or []

    print(f'- EVM_RPC_URL is configured: {_rpc_configured()}')
    print(f'- provider_ready={pr}')
    print(f'- live_evidence_source={live_evidence_source or evidence_source}')
    if run_id:
        print(f'- run_id={run_id}')

    rpc_is_configured = require_rpc if require_rpc is not None else _rpc_configured()

    if rpc_is_configured:
        if not pr:
            errors.append('EVM_RPC_URL is configured but provider_ready=false')
            for m in missing:
                errors.append(f'  missing: {m}')

        if live_evidence_source not in ('live_rpc', 'live'):
            errors.append(
                f'EVM_RPC_URL is configured but live_evidence_source={live_evidence_source!r}; '
                f'expected "live_rpc"'
            )
            for m in missing:
                errors.append(f'  missing: {m}')

        if not le:
            errors.append('EVM_RPC_URL is configured but live_evidence_ready=false')
            for m in missing:
                if m not in errors:
                    errors.append(f'  missing: {m}')

        if pr and not chain.get('telemetry_event_id'):
            errors.append(
                'provider_ready=true but no matching telemetry_event_id in chain; '
                'a current-run telemetry event is required'
            )

        if run_id:
            _validate_run_id_consistency(lpe, run_id, errors)
        elif le:
            errors.append('live_evidence_ready=true but run_id is missing from proof')

    if errors:
        return False, errors

    print('- OK: live evidence proof is valid')
    if live_evidence_source == 'live_rpc':
        print(f'  provider_ready={pr}  live_evidence_ready={le}  live_evidence_source=live_rpc')
    return True, []


def _validate_run_id_consistency(
    lpe: dict,
    run_id: str,
    errors: list[str],
) -> None:
    """
    Verify that all chain elements share the same run_id as the proof root.
    Fails if detection/alert/incident/action/evidence_package have a different run_id.
    """
    record_fields = [
        ('telemetry_record', 'telemetry_event_id'),
        ('detection_record', 'detection_id'),
        ('alert_record', 'alert_id'),
        ('incident_record', 'incident_id'),
        ('response_action_record', 'response_action_id'),
        ('evidence_package_record', 'evidence_package_id'),
    ]
    for record_name, id_field in record_fields:
        record = lpe.get(record_name) or {}
        if not record:
            continue
        record_run_id = str(record.get('run_id') or '').strip()
        if record_run_id and record_run_id != run_id:
            errors.append(
                f'{record_name}.run_id={record_run_id!r} does not match '
                f'proof run_id={run_id!r}; all chain elements must share the same run_id'
            )


def main(
    proof_path_str: str | None = None,
    *,
    require_rpc: bool | None = None,
    no_rpc_required: bool = False,
) -> int:
    if proof_path_str:
        path = Path(proof_path_str)
    else:
        path = _DEFAULT_PROOF_PATH

    effective_require_rpc: bool | None
    if no_rpc_required:
        effective_require_rpc = False
    elif require_rpc is not None:
        effective_require_rpc = require_rpc
    else:
        effective_require_rpc = None

    ok, errors = validate_live_evidence_proof(
        proof_path=path,
        require_rpc=effective_require_rpc,
    )

    if not ok:
        print('::error::live-evidence-proof validation failed', file=sys.stderr)
        for err in errors:
            print(f'  {err}', file=sys.stderr)
        return 1

    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Validate live evidence proof artifact.')
    parser.add_argument('--proof-path', default=None, help='Path to summary.json')
    parser.add_argument('--no-rpc-required', action='store_true',
                        help='Skip RPC-specific checks (for environments without EVM secrets)')
    args = parser.parse_args()
    raise SystemExit(main(
        proof_path_str=args.proof_path,
        no_rpc_required=args.no_rpc_required,
    ))
