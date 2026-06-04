#!/usr/bin/env python3
"""
Assert cross-artifact proof consistency.

Fails with exit code 1 if any of the following contradictions are detected:

1. final-readiness staging_validation.status="pass" but blockers mention "staging validation missing"
2. release-proof release_status="pass" but staging-proof required_dependencies.release_proof="fail"
3. launch-proof paid_launch_ready=true but release-proof release_status="fail"
4. sell-now broad_paid_saas_ready=false but contradiction_flags claim "sell-now broad_paid_saas_ready=true"
5. launch_mode="pilot" in a workflow artifact where BILLING_PROVIDER=paddle (paid SaaS proof was run)

Designed to run as the last proof-generation step before commit/push.
Exit 0 = all proofs consistent.
Exit 1 = contradictions found (printed to stdout).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Allow tests to override artifact root via env var so tests can provide
# isolated artifact fixtures without touching the real repo artifacts.
_ARTIFACT_ROOT_OVERRIDE = os.getenv('ASSERT_PROOF_ARTIFACT_ROOT', '')
_ARTIFACT_ROOT = Path(_ARTIFACT_ROOT_OVERRIDE) if _ARTIFACT_ROOT_OVERRIDE else REPO_ROOT


def _load(rel_path: str) -> dict | None:
    p = _ARTIFACT_ROOT / rel_path
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        return None


def main() -> int:
    failures: list[str] = []
    warnings: list[str] = []

    final_r = _load('artifacts/final-readiness/latest/summary.json')
    staging_p = _load('artifacts/staging-proof/latest/summary.json')
    release_p = _load('artifacts/release-proof/latest/summary.json')
    launch_p = _load('artifacts/launch-proof/latest/summary.json')
    sell_now = _load('artifacts/sell-now-proof/latest/summary.json')

    # ── Check 1: final-readiness staging_validation gate vs blockers ───────────
    if final_r is not None:
        gates = final_r.get('required_gates', {})
        sv_status = gates.get('staging_validation', {}).get('status', '')
        blockers = final_r.get('blockers', [])
        staging_blocker_present = any('staging validation missing' in b for b in blockers)
        if sv_status == 'pass' and staging_blocker_present:
            failures.append(
                'CHECK 1 FAIL: final-readiness required_gates.staging_validation.status="pass" '
                'but blockers include "staging validation missing". '
                'Fix: remove the stale blocker or correct _check_staging_validation().'
            )
        else:
            print(f'CHECK 1 OK: staging_validation.status={sv_status!r}, '
                  f'staging_missing_blocker={staging_blocker_present}')

    # ── Check 2: release-proof pass but staging-proof release_proof dep=fail ──
    if release_p is not None and staging_p is not None:
        rel_status = release_p.get('release_status', 'unknown')
        deps = staging_p.get('required_dependencies', {})
        sp_release = deps.get('release_proof', 'unknown')
        if rel_status == 'pass' and sp_release == 'fail':
            failures.append(
                f'CHECK 2 FAIL: release-proof release_status="pass" but '
                f'staging-proof required_dependencies.release_proof="fail". '
                'Fix: regenerate staging-proof after release-proof is generated.'
            )
        else:
            print(f'CHECK 2 OK: release_status={rel_status!r}, '
                  f'staging_proof.release_proof_dep={sp_release!r}')

    # ── Check 3: launch-proof paid_launch_ready=true but release-proof fails ──
    if launch_p is not None and release_p is not None:
        lp_paid_ready = bool(launch_p.get('paid_launch_ready', False))
        rel_status = release_p.get('release_status', 'unknown')
        if lp_paid_ready and rel_status == 'fail':
            failures.append(
                'CHECK 3 FAIL: launch-proof paid_launch_ready=true but '
                f'release-proof release_status="fail". '
                'Fix: resolve release-proof failures before claiming launch readiness.'
            )
        else:
            print(f'CHECK 3 OK: paid_launch_ready={lp_paid_ready}, '
                  f'release_status={rel_status!r}')

    # ── Check 4: sell-now broad_paid_saas_ready=false but flag claims true ────
    if sell_now is not None:
        sn_broad = bool(sell_now.get('broad_paid_saas_ready', False))
        flags = sell_now.get('contradiction_flags', [])
        stale_flag = any(
            'sell-now broad_paid_saas_ready=true' in f
            and 'final-readiness broad_paid_saas_ready=false' in f
            for f in flags
        )
        if not sn_broad and stale_flag:
            failures.append(
                'CHECK 4 FAIL: sell-now broad_paid_saas_ready=false but contradiction_flags '
                'claim "sell-now broad_paid_saas_ready=true but final-readiness broad_paid_saas_ready=false". '
                'The flag is stale — final-readiness incorrectly said false. '
                'Fix: regenerate final-readiness (with fixed _check_staging_validation) before sell-now.'
            )
        else:
            print(f'CHECK 4 OK: sell_now.broad_paid_saas_ready={sn_broad}, '
                  f'stale_flag_present={stale_flag}')

    # ── Check 5: launch_mode="pilot" when BILLING_PROVIDER=paddle ─────────────
    if launch_p is not None:
        launch_mode = launch_p.get('launch_mode', '')
        billing_provider = (os.getenv('BILLING_PROVIDER') or '').strip().lower()
        # Also check if the artifact itself records the billing provider
        artifact_provider = str(launch_p.get('billing_provider') or '').strip().lower()
        effective_provider = billing_provider or artifact_provider
        if launch_mode == 'pilot' and effective_provider in ('paddle', 'stripe'):
            failures.append(
                f'CHECK 5 FAIL: launch_mode="pilot" but BILLING_PROVIDER={effective_provider!r}. '
                'In a paid SaaS workflow, launch_mode must be "paid_saas". '
                'Fix: ensure run_paid_saas_launch_proof.py is called when BILLING_PROVIDER=paddle/stripe.'
            )
        else:
            print(f'CHECK 5 OK: launch_mode={launch_mode!r}, '
                  f'billing_provider={effective_provider!r}')

    # ── Summary ────────────────────────────────────────────────────────────────
    print()
    if failures:
        print(f'CONSISTENCY ASSERTION FAILED — {len(failures)} violation(s):')
        for i, f in enumerate(failures, 1):
            print(f'  [{i}] {f}')
        return 1

    print(f'All consistency checks passed.')
    if warnings:
        for w in warnings:
            print(f'  WARNING: {w}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
