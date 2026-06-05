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

    # ── Check 6: strict final-readiness + no blockers must allow safe_to_sell ──
    if final_r is not None:
        prod_ready = bool(final_r.get('production_100_percent_ready', False))
        fr_blockers = final_r.get('blockers', [])
        fr_strict = bool(final_r.get('strict', False))
        fr_safe = final_r.get('safe_to_sell_broadly_today')
        if prod_ready and not fr_blockers and fr_strict and fr_safe is not True:
            failures.append(
                'CHECK 6 FAIL: final-readiness production_100_percent_ready=true, blockers=[], '
                f'strict=true but safe_to_sell_broadly_today={fr_safe!r}. '
                'Fix: ensure validate_100_percent_readiness.py correctly sets '
                'safe_to_sell_broadly_today=true when strict=true and all gates pass.'
            )
        else:
            print(f'CHECK 6 OK: strict={fr_strict}, production_100_percent_ready={prod_ready}, '
                  f'blockers_count={len(fr_blockers)}, safe_to_sell={fr_safe}')

    # ── Check 7: sell-now must not have stale contradiction_flags ─────────────
    # When final-readiness ran without --strict, safe_to_sell=false is expected.
    # sell-now must not treat this expected state as a "contradiction".
    if sell_now is not None and final_r is not None:
        flags = sell_now.get('contradiction_flags', [])
        final_strict = bool(final_r.get('strict', False))
        final_safe = final_r.get('safe_to_sell_broadly_today')
        stale_strict_flag = any(
            'safe_to_sell_broadly_today=false; sell-now must not contradict' in f
            for f in flags
        )
        if not final_strict and final_safe is False and stale_strict_flag:
            failures.append(
                'CHECK 7 FAIL: sell-now has a contradiction_flag claiming '
                '"safe_to_sell_broadly_today=false; sell-now must not contradict" but '
                'final-readiness ran with strict=false — safe=false is the expected '
                'non-strict state, not a contradiction. '
                'Fix: update write_sell_now_proof.py to not add contradiction_flags for '
                'strict=false final-readiness state.'
            )
        else:
            print(f'CHECK 7 OK: final_strict={final_strict}, final_safe={final_safe}, '
                  f'stale_strict_flag={stale_strict_flag}')

    ci_gates = _load('artifacts/release-proof/latest/ci-required-gates.json')

    # ── Check 8: final-readiness=true must not contradict launch-proof ─────────
    # If final-readiness says production_100_percent_ready=true, the launch-proof
    # must also say paid_launch_ready=true and broad_paid_saas_ready=true.
    if final_r is not None and launch_p is not None:
        fr_prod_ready = bool(final_r.get('production_100_percent_ready', False))
        lp_paid_ready = bool(launch_p.get('paid_launch_ready', False))
        lp_broad = bool(launch_p.get('broad_paid_saas_ready', False))
        if fr_prod_ready and not lp_paid_ready:
            failures.append(
                'CHECK 8 FAIL: final-readiness says production_100_percent_ready=true but '
                'launch-proof says paid_launch_ready=false. '
                'Fix: regenerate launch-proof in staging/production mode via '
                'run_paid_saas_launch_proof.py --mode staging (save-proof-to-repo.yml step F).'
            )
        elif fr_prod_ready and not lp_broad:
            failures.append(
                'CHECK 8 FAIL: final-readiness says production_100_percent_ready=true but '
                'launch-proof says broad_paid_saas_ready=false. '
                'Fix: regenerate launch-proof in staging mode so broad_paid_saas_ready=true '
                'is committed to latest/.'
            )
        else:
            print(f'CHECK 8 OK: production_100_percent_ready={fr_prod_ready}, '
                  f'paid_launch_ready={lp_paid_ready}, broad_paid_saas_ready={lp_broad}')

    # ── Check 9: final-readiness broad_paid=true must not contradict release-proof ─
    if final_r is not None and release_p is not None:
        fr_broad = bool(final_r.get('broad_paid_saas_ready', False))
        rp_paid = bool(release_p.get('paid_launch_ready', False))
        if fr_broad and not rp_paid:
            failures.append(
                'CHECK 9 FAIL: final-readiness says broad_paid_saas_ready=true but '
                'release-proof says paid_launch_ready=false. '
                'Fix: regenerate release-proof with generate_release_proof.py --mode staging '
                'after the launch-proof has been regenerated in staging mode.'
            )
        else:
            print(f'CHECK 9 OK: fr_broad_paid_saas_ready={fr_broad}, '
                  f'release_proof_paid_launch_ready={rp_paid}')

    # ── Check 10: final-readiness broad_paid=true must not contradict ci-gates ──
    if final_r is not None and ci_gates is not None:
        fr_broad = bool(final_r.get('broad_paid_saas_ready', False))
        cg_broad = ci_gates.get('broad_paid_launch_ready')
        if fr_broad and cg_broad is False:
            failures.append(
                'CHECK 10 FAIL: final-readiness says broad_paid_saas_ready=true but '
                'ci-required-gates says broad_paid_launch_ready=false. '
                'Fix: regenerate ci-required-gates with generate_release_proof.py --mode staging '
                'after the launch-proof has broad_paid_saas_ready=true.'
            )
        else:
            print(f'CHECK 10 OK: fr_broad_paid_saas_ready={fr_broad}, '
                  f'ci_gates_broad_paid_launch_ready={cg_broad}')

    # ── Check 11: services/api live_evidence must not be older than live-evidence-proof ──
    live_ev_proof = _load('artifacts/live-evidence-proof/latest/summary.json')
    api_live_ev = _load('services/api/artifacts/live_evidence/latest/summary.json')
    if live_ev_proof is not None and api_live_ev is not None:
        from datetime import datetime, timezone
        def _parse_dt(s: str | None):
            if not s:
                return None
            try:
                return datetime.fromisoformat(str(s).replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                return None
        lpe = live_ev_proof.get('live_provider_evidence', {})
        proof_ts = _parse_dt(lpe.get('latest_live_telemetry_at'))
        api_ts = _parse_dt(api_live_ev.get('latest_live_telemetry_at'))
        if proof_ts and api_ts and api_ts < proof_ts:
            failures.append(
                f'CHECK 11 FAIL: services/api live_evidence telemetry is {api_ts.isoformat()} '
                f'but live-evidence-proof has fresher telemetry at {proof_ts.isoformat()}. '
                'Fix: regenerate services/api/artifacts/live_evidence/latest/summary.json '
                'from the current live-evidence-proof chain.'
            )
        else:
            print(f'CHECK 11 OK: api_live_telemetry={api_ts}, proof_telemetry={proof_ts}')

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
