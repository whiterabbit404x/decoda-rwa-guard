#!/usr/bin/env python3
"""
Validate launch proof artifact completeness.

Enforces that artifacts/launch-proof/latest/summary.json (or the most recent
timestamped directory) contains all required proof fields and that referenced
source artifacts actually exist with live (non-simulator) evidence.

Fail-closed semantics:
- Missing artifact is treated as a failure, not as "not run yet".
- Simulator/demo/fixture evidence cannot satisfy the live proof gate.
- Referenced source artifacts (live-evidence-proof) must exist and be real.
- A launch proof that claims live_evidence_ready=true without a backing
  live-evidence-proof artifact is rejected.

Required launch proof fields:
  - generated_at (timestamp)
  - proof_mode (must not be 'local' or 'ci' for live readiness)
  - launch_mode
  - schema_version

If live_provider_evidence_ready is true, the live evidence source artifact
(artifacts/live-evidence-proof/latest/summary.json) must also exist and satisfy:
  - evidence_source == 'live' or 'live_provider'
  - provider_ready == True
  - latest_live_telemetry_at is present
  - live_evidence_ready == True
  - block_number_observed is present (proves real RPC response)
  - No contradiction_flags

Usage:
  python scripts/validate_launch_proof_completeness.py
  python scripts/validate_launch_proof_completeness.py --strict    # non-zero exit on failure
  python scripts/validate_launch_proof_completeness.py --proof-dir artifacts/launch-proof/20260604T062544Z
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]

# Artifact paths
_LAUNCH_PROOF_LATEST = REPO_ROOT / 'artifacts' / 'launch-proof' / 'latest'
_LAUNCH_PROOF_ROOT = REPO_ROOT / 'artifacts' / 'launch-proof'
_LIVE_EVIDENCE_PROOF_LATEST = REPO_ROOT / 'artifacts' / 'live-evidence-proof' / 'latest'

# Evidence source values that are NOT acceptable as live proof
_DISALLOWED_EVIDENCE_SOURCES = frozenset({
    'simulator', 'guided_simulator', 'demo', 'fixture', 'static',
    'historical', 'synthetic', 'not_applicable', 'unknown',
})

# Proof modes that indicate local/CI test runs (not staging/production)
_LOCAL_PROOF_MODES = frozenset({'local', 'ci'})


def _rel(path: Path) -> str:
    """Return path relative to REPO_ROOT if possible, otherwise the full path."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        print(f'[completeness] ERROR: cannot parse {_rel(path)}: {e}')
        return None


def _content_digest(path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    sha = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            sha.update(chunk)
    return sha.hexdigest()


def _find_proof_dir(override: str | None) -> Path | None:
    """Return the launch proof directory to validate."""
    if override:
        candidate = Path(override)
        if not candidate.is_absolute():
            candidate = REPO_ROOT / candidate
        return candidate if candidate.is_dir() else None

    # Prefer artifacts/launch-proof/latest/
    if _LAUNCH_PROOF_LATEST.is_dir() and (_LAUNCH_PROOF_LATEST / 'summary.json').exists():
        return _LAUNCH_PROOF_LATEST

    # Fall back to most recent timestamped directory
    if _LAUNCH_PROOF_ROOT.is_dir():
        timestamped = sorted(
            [d for d in _LAUNCH_PROOF_ROOT.iterdir()
             if d.is_dir() and d.name != 'fail-closed-local' and not d.name.startswith('.')],
            key=lambda d: d.name,
            reverse=True,
        )
        for candidate in timestamped:
            if (candidate / 'summary.json').exists():
                return candidate

    return None


def validate_launch_proof_completeness(
    proof_dir_override: str | None = None,
    strict: bool = False,
) -> tuple[bool, list[str], list[str]]:
    """
    Validate launch proof artifact completeness.

    Returns (passed, blockers, warnings).
    Never treats a missing artifact as passing.
    """
    blockers: list[str] = []
    warnings: list[str] = []

    proof_dir = _find_proof_dir(proof_dir_override)

    if proof_dir is None:
        blockers.append(
            'No launch proof artifact found. '
            'Run "make generate-staging-proof" with real staging credentials and EVM_RPC_URL set, '
            'then run "make validate-launch".'
        )
        return False, blockers, warnings

    summary_path = proof_dir / 'summary.json'
    if not summary_path.exists():
        blockers.append(
            f'launch proof summary.json not found in {_rel(proof_dir)}; '
            'proof was not generated or artifact was deleted.'
        )
        return False, blockers, warnings

    summary = _load_json(summary_path)
    if summary is None:
        blockers.append(f'launch proof summary.json at {_rel(summary_path)} is not valid JSON.')
        return False, blockers, warnings

    print(f'[completeness] Validating: {_rel(summary_path)}')

    # --- Required base fields ---
    if not summary.get('generated_at'):
        blockers.append('launch proof missing required field: generated_at')
    else:
        try:
            datetime.fromisoformat(str(summary['generated_at']))
        except Exception:
            blockers.append(f"launch proof generated_at is not a valid ISO timestamp: {summary['generated_at']!r}")

    if not summary.get('schema_version'):
        blockers.append('launch proof missing required field: schema_version')

    launch_mode = str(summary.get('launch_mode') or '').strip()
    if not launch_mode:
        blockers.append('launch proof missing required field: launch_mode')

    proof_mode = str(summary.get('proof_mode') or '').strip()
    if not proof_mode:
        blockers.append('launch proof missing required field: proof_mode')
    elif proof_mode in _LOCAL_PROOF_MODES:
        # Local/CI proofs legitimately do not claim live readiness — warn but don't block
        warnings.append(
            f"proof_mode='{proof_mode}' indicates a local or CI proof run. "
            'For live readiness claims, proof_mode must be staging or production.'
        )

    # --- Compute and record artifact digest ---
    try:
        digest = _content_digest(summary_path)
        print(f'[completeness] artifact_digest(sha256): {digest}')
    except Exception as exc:
        warnings.append(f'Could not compute artifact digest: {exc}')

    # --- Live provider evidence check ---
    readiness_cats = summary.get('readiness_categories') or {}
    live_evidence_claimed = bool(
        readiness_cats.get('live_provider_evidence_ready')
        or summary.get('live_provider_evidence_ready')
        or (summary.get('readiness') or {}).get('live_evidence_ready')
    )

    if live_evidence_claimed:
        _validate_live_evidence_claim(summary, proof_dir, blockers, warnings)
    else:
        warnings.append(
            'live_provider_evidence_ready is false or not set. '
            'Product cannot honestly claim LIVE monitoring until a real staging run is validated.'
        )

    # --- Contradiction guard: broad_paid_saas_ready without live evidence ---
    broad_saas_ready = bool(
        summary.get('broad_paid_saas_ready')
        or summary.get('safe_to_sell_broadly_today')
        or readiness_cats.get('broad_paid_saas_ready')
    )
    if broad_saas_ready and not live_evidence_claimed:
        blockers.append(
            'broad_paid_saas_ready is true but live_provider_evidence_ready is false; '
            'this is a contradiction — broad paid SaaS requires live telemetry proof.'
        )

    # --- Simulator contamination check ---
    live_evidence_section = summary.get('live_provider_evidence') or {}
    evidence_source = str(live_evidence_section.get('evidence_source') or '').strip().lower()
    if evidence_source in _DISALLOWED_EVIDENCE_SOURCES:
        blockers.append(
            f"launch proof live_provider_evidence.evidence_source='{evidence_source}' "
            'is simulator/demo/fixture data; this cannot satisfy the live proof gate.'
        )

    passed = not blockers
    return passed, blockers, warnings


def _validate_live_evidence_claim(
    summary: dict[str, Any],
    proof_dir: Path,
    blockers: list[str],
    warnings: list[str],
) -> None:
    """Validate that a live_evidence_ready=true claim is backed by a real artifact."""

    source_val = str(
        (summary.get('live_provider_evidence') or {}).get('source')
        or 'artifacts/live-evidence-proof/latest/summary.json'
    )
    # Support both absolute paths and repo-relative paths
    candidate = Path(source_val)
    if candidate.is_absolute():
        live_evidence_source_path = candidate
    else:
        live_evidence_source_path = REPO_ROOT / source_val
    source_relative = source_val

    if not live_evidence_source_path.exists():
        # Also check the canonical default path
        canonical = _LIVE_EVIDENCE_PROOF_LATEST / 'summary.json'
        if not canonical.exists():
            blockers.append(
                'launch proof claims live_provider_evidence_ready=true but the '
                f'live evidence source artifact is missing: {source_relative}. '
                'Run "make generate-live-evidence-proof" with a real EVM_RPC_URL '
                'and LIVE_EVIDENCE_CHAIN_JSON set to real monitoring data.'
            )
            return
        live_evidence_source_path = canonical

    live_ev = _load_json(live_evidence_source_path)
    if live_ev is None:
        blockers.append(
            f'live evidence source artifact at '
            f'{_rel(live_evidence_source_path)} is not valid JSON.'
        )
        return

    lpe = live_ev.get('live_provider_evidence') or {}

    # Must have evidence_source = live
    evidence_source = str(lpe.get('evidence_source') or '').strip().lower()
    if evidence_source in _DISALLOWED_EVIDENCE_SOURCES:
        blockers.append(
            f"live evidence source artifact has evidence_source='{evidence_source}'; "
            'simulator/demo/fixture/guided_simulator evidence does not count as live proof. '
            'Run with a real EVM_RPC_URL and real monitoring data.'
        )
    elif evidence_source not in ('live', 'live_provider', 'live_rpc'):
        blockers.append(
            f"live evidence source artifact has evidence_source='{evidence_source}'; "
            "expected 'live', 'live_provider', or 'live_rpc'. "
            'Proof may be from a non-live run.'
        )

    # Must have provider_ready
    if not lpe.get('provider_ready'):
        blockers.append(
            'live evidence source artifact has provider_ready=false; '
            'EVM RPC provider was not reachable when this proof was generated.'
        )

    # Must have block_number_observed (proves real RPC response)
    if not lpe.get('block_number_observed'):
        blockers.append(
            'live evidence source artifact is missing block_number_observed; '
            'a real EVM RPC call must return an observed block number.'
        )

    # Must have latest_live_telemetry_at
    if not lpe.get('latest_live_telemetry_at'):
        blockers.append(
            'live evidence source artifact is missing latest_live_telemetry_at; '
            'heartbeat and poll alone do not prove telemetry data arrived from the monitored asset.'
        )

    # Must have live_evidence_ready
    if not lpe.get('live_evidence_ready'):
        blockers.append(
            'live evidence source artifact has live_evidence_ready=false; '
            'live proof gate cannot be satisfied without a complete evidence chain '
            '(telemetry → detection → alert → export).'
        )

    # Must have no contradiction flags
    contradiction_flags = list(lpe.get('contradiction_flags') or [])
    if contradiction_flags:
        blockers.append(
            f'live evidence source artifact contains contradiction flags: {contradiction_flags[:3]}. '
            'These invalidate the live proof claim.'
        )

    # Chain completeness: all key IDs must be present
    chain = lpe.get('chain') or {}
    required_chain_ids = ('telemetry_event_id', 'detection_id', 'alert_id', 'evidence_package_id')
    missing_ids = [k for k in required_chain_ids if not chain.get(k)]
    if missing_ids:
        blockers.append(
            f'live evidence chain is missing IDs: {missing_ids}. '
            'Each link in the evidence chain must have a persisted ID from the monitoring database.'
        )

    # Artifact freshness (optional warning, not a hard block)
    generated_at = str(live_ev.get('generated_at') or '').strip()
    if generated_at:
        try:
            ts = datetime.fromisoformat(generated_at)
            now = datetime.now(timezone.utc)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age_days = (now - ts).days
            if age_days > 90:
                warnings.append(
                    f'live evidence proof is {age_days} days old (generated {generated_at}). '
                    'For production launch, regenerate with current staging run.'
                )
        except Exception:
            pass

    print(f'[completeness] live evidence source: {_rel(live_evidence_source_path)}')
    print(f'[completeness]   evidence_source={evidence_source!r}')
    print(f'[completeness]   provider_ready={lpe.get("provider_ready")}')
    print(f'[completeness]   block_number_observed={lpe.get("block_number_observed")}')
    print(f'[completeness]   latest_live_telemetry_at={lpe.get("latest_live_telemetry_at")}')
    print(f'[completeness]   live_evidence_ready={lpe.get("live_evidence_ready")}')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--strict', action='store_true', help='Exit non-zero on any blocker (default: always non-zero on failure)')
    parser.add_argument('--proof-dir', dest='proof_dir', default=None, help='Path to a specific launch proof directory to validate')
    args = parser.parse_args(argv)

    passed, blockers, warnings = validate_launch_proof_completeness(
        proof_dir_override=args.proof_dir,
        strict=args.strict,
    )

    if warnings:
        print('\n[completeness] WARNINGS:')
        for w in warnings:
            print(f'  - {w}')

    if blockers:
        print('\n[completeness] BLOCKERS (launch proof is incomplete or invalid):')
        for b in blockers:
            print(f'  - {b}')
        print('\n[completeness] RESULT: FAIL')
        print('\nHow to generate real launch proof:')
        print('  1. Set EVM_RPC_URL or STAGING_EVM_RPC_URL to a real EVM RPC endpoint')
        print('  2. Set LIVE_EVIDENCE_CHAIN_JSON to real monitoring evidence (from a running staging environment)')
        print('  3. Run: make generate-live-evidence-proof')
        print('  4. Run: make generate-staging-proof')
        print('  5. Run: make validate-launch-completeness')
        print('  Note: LIVE_PROVIDER_PROOF_PRESENT=true does NOT substitute for real evidence')
        return 1

    print('\n[completeness] RESULT: PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
