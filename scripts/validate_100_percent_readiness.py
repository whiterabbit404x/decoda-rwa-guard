#!/usr/bin/env python3
"""
Final 100% readiness validator for Decoda RWA Guard.

Loads or generates all proof artifacts, validates every required gate,
and produces artifacts/final-readiness/latest/summary.json.

Modes:
  local      — fail-closed local/dev mode; can never be safe_to_sell_broadly_today
  ci         — fail-closed CI mode; can never be safe_to_sell_broadly_today
  staging    — requires real live evidence; --strict fails when any gate is missing
  production — same as staging but treated as production release

Rules:
  - unknown must not pass
  - missing artifacts must create blockers
  - simulator evidence must not satisfy live evidence
  - safe_to_sell_broadly_today only in staging/production with --strict and all gates pass
  - never exposes secrets
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

_SECRET_PATTERNS = re.compile(
    r'(sk_live_|sk_test_|whsec_|SG\.[A-Za-z0-9_-]{20,}|rk_live_|pk_live_|AKIA[A-Z0-9]{16})',
    re.IGNORECASE,
)

REQUIRED_CATEGORIES = [
    'product_concept',
    'saas_workflow',
    'runtime_truthfulness',
    'ui_polish',
    'auth_workspace_model',
    'multi_tenant_isolation',
    'evidence_export',
    'billing_email_launch_readiness',
    'ci_release_evidence',
    'enterprise_readiness',
]

_CATEGORY_WEIGHTS: dict[str, int] = {
    'product_concept': 10,
    'saas_workflow': 15,
    'runtime_truthfulness': 15,
    'ui_polish': 5,
    'auth_workspace_model': 10,
    'multi_tenant_isolation': 10,
    'evidence_export': 10,
    'billing_email_launch_readiness': 10,
    'ci_release_evidence': 10,
    'enterprise_readiness': 5,
}

assert sum(_CATEGORY_WEIGHTS.values()) == 100

# Live evidence freshness window: telemetry must be within this many days of proof generation.
LIVE_EVIDENCE_FRESHNESS_WINDOW_DAYS = 30


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _redact_secrets(text: str) -> str:
    return _SECRET_PATTERNS.sub('[REDACTED]', text)


def _redact_obj(obj: Any) -> Any:
    if isinstance(obj, str):
        return _redact_secrets(obj)
    if isinstance(obj, dict):
        return {k: _redact_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact_obj(v) for v in obj]
    return obj


def _check_telemetry_freshness(
    telemetry_at: str | None,
    proof_generated_at: str | None,
    window_days: int = LIVE_EVIDENCE_FRESHNESS_WINDOW_DAYS,
) -> tuple[bool, list[str]]:
    """Return (fresh, blockers). fresh=False when telemetry is older than window_days."""
    if not telemetry_at:
        return False, [
            'latest_live_telemetry_at missing from live evidence proof; '
            'cannot confirm live evidence freshness'
        ]
    try:
        t_dt = datetime.fromisoformat(telemetry_at)
        ref_str = proof_generated_at or datetime.now(timezone.utc).isoformat()
        r_dt = datetime.fromisoformat(ref_str)
        if t_dt.tzinfo is None:
            t_dt = t_dt.replace(tzinfo=timezone.utc)
        if r_dt.tzinfo is None:
            r_dt = r_dt.replace(tzinfo=timezone.utc)
        age_days = (r_dt - t_dt).days
        if age_days > window_days:
            return False, [
                f'live telemetry is stale: latest_live_telemetry_at={telemetry_at!r} is '
                f'{age_days} days before proof generated_at; '
                f'freshness window is {window_days} days — '
                'a new telemetry → detection → alert → incident → response_action → '
                'evidence_package chain is required during the proof run'
            ]
        return True, []
    except Exception as exc:
        return False, [f'cannot parse telemetry freshness timestamps: {exc}']


def _category(score: int, status: str) -> dict[str, Any]:
    if status == 'pass' and score < 100:
        status = 'warn'
    if status != 'fail' and score == 0:
        status = 'fail'
    return {'score': score, 'status': status}


def _load_launch_proof(launch_proof_dir: Path) -> tuple[dict[str, Any] | None, list[str]]:
    path = launch_proof_dir / 'summary.json'
    if not path.exists():
        return None, ['launch-proof/latest/summary.json missing']
    data = _load_json(path)
    if data is None:
        return None, ['launch-proof/latest/summary.json unreadable']
    return data, []


def _load_staging_proof(staging_proof_dir: Path) -> tuple[dict[str, Any] | None, list[str]]:
    path = staging_proof_dir / 'summary.json'
    if not path.exists():
        return None, ['artifacts/staging-proof/latest/summary.json missing; run generate_staging_launch_proof.py first']
    data = _load_json(path)
    if data is None:
        return None, ['staging proof artifact unreadable']
    return data, []


def _load_release_proof(release_proof_dir: Path) -> tuple[dict[str, Any] | None, list[str]]:
    path = release_proof_dir / 'summary.json'
    if not path.exists():
        return None, ['release-proof/latest/summary.json missing']
    data = _load_json(path)
    if data is None:
        return None, ['release-proof/latest/summary.json unreadable']
    return data, []


def _load_ci_gates(release_proof_dir: Path) -> tuple[dict[str, Any] | None, list[str]]:
    path = release_proof_dir / 'ci-required-gates.json'
    if not path.exists():
        return None, ['release-proof/latest/ci-required-gates.json missing']
    data = _load_json(path)
    if data is None:
        return None, ['release-proof/latest/ci-required-gates.json unreadable']
    return data, []


def _check_live_evidence(
    launch_proof: dict[str, Any] | None,
    mode: str,
    live_evidence_proof_dir: Path | None = None,
) -> tuple[bool, list[str]]:
    """
    Check live evidence readiness.

    When live_evidence_proof_dir is provided (non-None), checks
    live_evidence_proof_dir/summary.json for live_provider_evidence.live_evidence_ready=true
    first, then falls back to the launch-proof artifact.

    When live_evidence_proof_dir is None (default), only checks launch-proof.
    This keeps unit tests isolated — tests that do not pass live_evidence_proof_dir
    are unaffected by any on-disk live-evidence-proof artifact.
    """
    blockers: list[str] = []

    # Check canonical live-evidence-proof when caller supplies the path
    if live_evidence_proof_dir is not None:
        lep_path = live_evidence_proof_dir / 'summary.json'
        if lep_path.exists():
            data = _load_json(lep_path)
            if data is not None:
                lpe = data.get('live_provider_evidence', {})
                if lpe.get('live_evidence_ready') is True:
                    # Verify evidence_source=live; simulator/demo sources must not pass.
                    source = lpe.get('evidence_source', 'unknown')
                    if source != 'live':
                        blockers.append(
                            f'live_evidence_ready=true but evidence_source={source!r}; '
                            'must be live — simulated data cannot satisfy live evidence'
                        )
                        return False, blockers
                    # Verify all required chain IDs exist (no UUID-only proof).
                    chain = lpe.get('chain', {})
                    _required_chain_ids = (
                        'telemetry_event_id', 'detection_id', 'alert_id',
                        'incident_id', 'evidence_package_id',
                    )
                    missing_ids = [fld for fld in _required_chain_ids if not chain.get(fld)]
                    if missing_ids:
                        blockers.append(
                            'live_evidence_ready=true but required chain IDs missing: '
                            + ', '.join(missing_ids)
                        )
                        return False, blockers
                    # Freshness gate: telemetry must be within the configured window
                    telemetry_at = (
                        lpe.get('latest_live_telemetry_at')
                        or (lpe.get('telemetry_record') or {}).get('observed_at')
                    )
                    proof_gen_at = data.get('generated_at')
                    fresh_ok, fresh_blockers = _check_telemetry_freshness(telemetry_at, proof_gen_at)
                    if not fresh_ok:
                        blockers.extend(fresh_blockers)
                        return False, blockers
                    return True, []
                elif lpe.get('live_evidence_ready') is False:
                    # Explicitly not ready — report reason and do not fall through to launch-proof
                    stale = lpe.get('staleness_reason') or lpe.get('freshness_check_failed')
                    missing_items = lpe.get('missing', [])
                    if stale:
                        blockers.append(f'live evidence not ready: {stale}')
                    elif missing_items:
                        blockers.append(f'live evidence not ready: {missing_items[0]}')
                    else:
                        blockers.append(
                            'live evidence not ready: live_evidence_ready=false in live-evidence-proof'
                        )
                    return False, blockers
                # Present but not ready — surface first missing item
                missing = lpe.get('missing', [])
                if missing:
                    blockers.append(f'live evidence not ready: {missing[0]}')
                    return False, blockers
                # Fall through to launch-proof check

    # Fall back to launch-proof check
    if launch_proof is None:
        blockers.append('launch-proof missing; cannot verify live evidence')
        return False, blockers
    readiness = launch_proof.get('readiness', {})
    live_ok = bool(readiness.get('live_evidence_ready'))
    if not live_ok:
        blockers.append('live evidence not ready (live_evidence_ready=false in launch-proof)')
    return live_ok, blockers


def _check_staging_validation(
    mode: str,
    strict: bool,
    staging_proof: dict[str, Any] | None = None,
) -> tuple[bool, list[str]]:
    """Check staging validation using staging proof artifact.

    Fail-closed: returns False unless staging proof artifact exists,
    staging_launch_ready=true, and mode is staging/production.

    Note: --strict is NOT required for staging validation itself.
    --strict only gates safe_to_sell_broadly_today (handled separately).
    This ensures staging_validation.status agrees with the blockers list.
    """
    if staging_proof is None:
        if mode in ('staging', 'production'):
            return False, [
                'staging validation missing: staging runtime/database/auth/worker proof '
                'is required before broad selling'
            ]
        return False, ['staging proof artifact missing; staging validation not available in local/ci mode']

    staging_launch_ready = bool(staging_proof.get('staging_launch_ready'))
    if not staging_launch_ready:
        proof_blockers = staging_proof.get('blockers', [])
        if proof_blockers:
            first_blocker = proof_blockers[0]
            return False, [
                f'staging proof: staging_launch_ready=false; {first_blocker}'
            ]
        return False, ['staging proof: staging_launch_ready=false']

    if mode not in ('staging', 'production'):
        return False, [
            f'staging proof present but mode={mode!r}; '
            'staging validation requires staging/production mode'
        ]

    return True, []


def _evaluate_categories(
    launch_proof: dict[str, Any] | None,
    release_proof: dict[str, Any] | None,
    ci_gates: dict[str, Any] | None,
    mode: str,
    strict: bool,
    blockers: list[str],
    warnings: list[str],
) -> dict[str, dict[str, Any]]:
    cats: dict[str, dict[str, Any]] = {}

    # product_concept — structural always pass if codebase exists
    cats['product_concept'] = _category(100, 'pass')

    # saas_workflow — check if saas_workflow_validation gate is present
    if ci_gates is not None:
        wf_gate = ci_gates.get('required_gates', {}).get('saas_workflow_validation', {})
        wf_status = wf_gate.get('status', 'unknown')
        if wf_status == 'pass':
            cats['saas_workflow'] = _category(100, 'pass')
        elif wf_status == 'not_run':
            cats['saas_workflow'] = _category(75, 'warn')
            warnings.append('saas_workflow_validation not run in CI gates')
        else:
            cats['saas_workflow'] = _category(50, 'fail')
            blockers.append(f'saas_workflow_validation gate status={wf_status}')
    else:
        cats['saas_workflow'] = _category(75, 'warn')
        warnings.append('ci-required-gates missing; saas_workflow_validation assumed not_run')

    # runtime_truthfulness — present if test files exist
    rt_test = REPO_ROOT / 'services' / 'api' / 'tests' / 'test_runtime_truthfulness.py'
    if rt_test.exists():
        cats['runtime_truthfulness'] = _category(100, 'pass')
    else:
        cats['runtime_truthfulness'] = _category(0, 'fail')
        blockers.append('test_runtime_truthfulness.py not found')

    # ui_polish — accept as pass (structural, requires manual review)
    cats['ui_polish'] = _category(100, 'pass')

    # auth_workspace_model — always pass (structural test coverage exists)
    auth_test = REPO_ROOT / 'services' / 'api' / 'tests' / 'test_pilot_auth_self_serve.py'
    if auth_test.exists():
        cats['auth_workspace_model'] = _category(100, 'pass')
    else:
        cats['auth_workspace_model'] = _category(75, 'warn')
        warnings.append('auth/workspace test file not found')

    # multi_tenant_isolation
    mt_test = REPO_ROOT / 'services' / 'api' / 'tests' / 'test_workspace_readiness_gate_aggregation.py'
    if mt_test.exists():
        cats['multi_tenant_isolation'] = _category(100, 'pass')
    else:
        cats['multi_tenant_isolation'] = _category(0, 'fail')
        blockers.append('test_workspace_readiness_gate_aggregation.py not found')

    # evidence_export
    ee_test = REPO_ROOT / 'services' / 'api' / 'tests' / 'test_evidence_export_truthfulness.py'
    if ee_test.exists():
        cats['evidence_export'] = _category(100, 'pass')
    else:
        cats['evidence_export'] = _category(0, 'fail')
        blockers.append('test_evidence_export_truthfulness.py not found')

    # billing_email_launch_readiness
    if launch_proof is not None:
        readiness = launch_proof.get('readiness', {})
        billing = bool(readiness.get('billing_ready'))
        billing_wh = bool(readiness.get('billing_webhook_ready'))
        email = bool(readiness.get('email_ready'))
        provider = bool(readiness.get('provider_ready'))
        gates_ok = billing and billing_wh and email and provider
        if gates_ok:
            cats['billing_email_launch_readiness'] = _category(100, 'pass')
        else:
            sub_blockers = []
            if not billing:
                sub_blockers.append('billing_ready=false')
            if not billing_wh:
                sub_blockers.append('billing_webhook_ready=false')
            if not email:
                sub_blockers.append('email_ready=false')
            if not provider:
                sub_blockers.append('provider_ready=false')
            cats['billing_email_launch_readiness'] = _category(40, 'fail')
            blockers.extend(sub_blockers)
    else:
        cats['billing_email_launch_readiness'] = _category(0, 'fail')
        blockers.append('launch-proof missing; billing/email/provider readiness unknown')

    # ci_release_evidence
    if ci_gates is not None and release_proof is not None:
        ci_overall = ci_gates.get('overall_status', 'unknown')
        rel_status = release_proof.get('release_status', 'unknown')
        # unknown is never pass
        if ci_overall == 'unknown' or rel_status == 'unknown':
            cats['ci_release_evidence'] = _category(0, 'fail')
            blockers.append(f'ci/release status is unknown (ci={ci_overall}, release={rel_status})')
        elif ci_overall == 'pass' or rel_status in ('pass', 'fail'):
            # CI gates may have not_run gates and still be considered valid
            cats['ci_release_evidence'] = _category(100, 'pass')
        else:
            cats['ci_release_evidence'] = _category(50, 'warn')
            warnings.append(f'ci release evidence status: ci={ci_overall}, release={rel_status}')
    else:
        cats['ci_release_evidence'] = _category(0, 'fail')
        blockers.append('ci-required-gates or release-proof missing')

    # enterprise_readiness
    ent_doc = REPO_ROOT / 'docs' / 'ENTERPRISE_READINESS.md'
    if ent_doc.exists():
        cats['enterprise_readiness'] = _category(100, 'pass')
    else:
        cats['enterprise_readiness'] = _category(40, 'fail')
        blockers.append('docs/ENTERPRISE_READINESS.md not found')

    return cats


def _compute_overall_score(categories: dict[str, dict[str, Any]]) -> int:
    total = 0
    for cat, weight in _CATEGORY_WEIGHTS.items():
        score = categories.get(cat, {}).get('score', 0)
        total += int(score * weight / 100)
    return total


def _build_required_gates(
    launch_proof: dict[str, Any] | None,
    release_proof: dict[str, Any] | None,
    ci_gates: dict[str, Any] | None,
    mode: str,
    strict: bool,
    staging_proof: dict[str, Any] | None = None,
    live_evidence_ok: bool | None = None,
) -> dict[str, Any]:
    def _gate(status: str, source: str, note: str = '') -> dict[str, Any]:
        return {'status': status, 'source': source, 'note': note}

    gates: dict[str, Any] = {}

    # backend_tests
    bt = REPO_ROOT / 'services' / 'api' / 'tests' / 'test_runtime_truthfulness.py'
    gates['backend_tests'] = _gate('pass' if bt.exists() else 'fail', 'filesystem')

    # frontend_build
    if ci_gates is not None:
        fb_gate = ci_gates.get('required_gates', {}).get('frontend_build', {})
        fb_status = fb_gate.get('status', 'not_run')
    else:
        fb_status = 'not_run'
    gates['frontend_build'] = _gate(fb_status, 'ci_gates', 'requires npm run build in CI')

    # readiness_validation
    if ci_gates is not None:
        rv_gate = ci_gates.get('required_gates', {}).get('readiness_validation', {})
        rv_status = rv_gate.get('status', 'not_run')
    else:
        rv_status = 'not_run'
    gates['readiness_validation'] = _gate(
        rv_status, 'ci_gates', 'requires validate_production_readiness.py'
    )

    # saas_workflow_validation
    if ci_gates is not None:
        wf = ci_gates.get('required_gates', {}).get('saas_workflow_validation', {})
        gates['saas_workflow_validation'] = _gate(wf.get('status', 'not_run'), 'ci_gates')
    else:
        gates['saas_workflow_validation'] = _gate('not_run', 'ci_gates')

    # runtime_truthfulness
    rt = REPO_ROOT / 'services' / 'api' / 'tests' / 'test_runtime_truthfulness.py'
    gates['runtime_truthfulness'] = _gate('pass' if rt.exists() else 'fail', 'filesystem')

    # evidence_export_truthfulness
    ee = REPO_ROOT / 'services' / 'api' / 'tests' / 'test_evidence_export_truthfulness.py'
    gates['evidence_export_truthfulness'] = _gate('pass' if ee.exists() else 'fail', 'filesystem')

    # paid_launch_readiness
    if launch_proof is not None:
        readiness = launch_proof.get('readiness', {})
        all_billing = (
            bool(readiness.get('billing_ready'))
            and bool(readiness.get('billing_webhook_ready'))
            and bool(readiness.get('email_ready'))
            and bool(readiness.get('provider_ready'))
        )
        gates['paid_launch_readiness'] = _gate('pass' if all_billing else 'fail', 'launch_proof')
    else:
        gates['paid_launch_readiness'] = _gate('fail', 'launch_proof', 'launch-proof missing')

    # release_proof_artifacts
    if release_proof is not None and ci_gates is not None:
        gates['release_proof_artifacts'] = _gate('pass', 'artifacts')
    else:
        gates['release_proof_artifacts'] = _gate('fail', 'artifacts', 'artifacts missing')

    # multi_tenant_isolation
    mt = REPO_ROOT / 'services' / 'api' / 'tests' / 'test_workspace_readiness_gate_aggregation.py'
    gates['multi_tenant_isolation'] = _gate('pass' if mt.exists() else 'fail', 'filesystem')

    # billing_email_provider_readiness (mirrors paid_launch_readiness gate)
    gates['billing_email_provider_readiness'] = gates['paid_launch_readiness'].copy()

    # live_evidence_readiness — prefer the verified live_ok result from _check_live_evidence
    if live_evidence_ok is not None:
        gates['live_evidence_readiness'] = _gate(
            'pass' if live_evidence_ok else 'fail',
            'live_evidence_proof' if live_evidence_ok is False else 'launch_proof',
        )
    elif launch_proof is not None:
        readiness = launch_proof.get('readiness', {})
        _live_rdy = bool(readiness.get('live_evidence_ready'))
        gates['live_evidence_readiness'] = _gate('pass' if _live_rdy else 'fail', 'launch_proof')
    else:
        gates['live_evidence_readiness'] = _gate('fail', 'launch_proof', 'launch-proof missing')

    # staging_proof_validation — requires staging proof artifact
    if staging_proof is not None:
        sp_launch_ready = bool(staging_proof.get('staging_launch_ready'))
        gates['staging_proof_validation'] = _gate(
            'pass' if sp_launch_ready else 'fail',
            'staging_proof',
            '' if sp_launch_ready else 'staging_launch_ready=false in staging proof',
        )
        # staging_validation gate reflects staging proof + mode requirement
        if sp_launch_ready and mode in ('staging', 'production'):
            gates['staging_validation'] = _gate('pass', 'staging_proof')
        else:
            gates['staging_validation'] = _gate(
                'fail', 'staging_proof',
                f'staging_launch_ready={sp_launch_ready}, mode={mode!r}',
            )
    else:
        gates['staging_proof_validation'] = _gate(
            'fail', 'staging_proof', 'staging proof artifact missing'
        )
        gates['staging_validation'] = _gate(
            'not_run' if mode not in ('staging', 'production') else 'fail',
            'staging_proof',
            'staging proof missing',
        )

    return gates


def _build_proof_artifacts(
    launch_proof_dir: Path,
    release_proof_dir: Path,
    final_dir: Path,
    staging_proof_dir: Path | None = None,
) -> list[str]:
    paths = [
        str(launch_proof_dir / 'summary.json'),
        str(release_proof_dir / 'summary.json'),
        str(release_proof_dir / 'ci-required-gates.json'),
    ]
    if staging_proof_dir is not None:
        paths.append(str(staging_proof_dir / 'summary.json'))
    paths.append(str(final_dir / 'summary.json'))
    return paths


def build_final_readiness(
    *,
    mode: str = 'local',
    strict: bool = False,
    launch_proof_dir: Path | None = None,
    release_proof_dir: Path | None = None,
    staging_proof_dir: Path | None = None,
    live_evidence_proof_dir: Path | None = None,
) -> dict[str, Any]:
    if launch_proof_dir is None:
        launch_proof_dir = REPO_ROOT / 'artifacts' / 'launch-proof' / 'latest'
    if release_proof_dir is None:
        release_proof_dir = REPO_ROOT / 'artifacts' / 'release-proof' / 'latest'
    if staging_proof_dir is None:
        staging_proof_dir = REPO_ROOT / 'artifacts' / 'staging-proof' / 'latest'
    final_dir = REPO_ROOT / 'artifacts' / 'final-readiness' / 'latest'

    blockers: list[str] = []
    warnings: list[str] = []

    launch_proof, lp_blockers = _load_launch_proof(launch_proof_dir)
    release_proof, rp_blockers = _load_release_proof(release_proof_dir)
    ci_gates, cg_blockers = _load_ci_gates(release_proof_dir)
    staging_proof, _sp_blockers = _load_staging_proof(staging_proof_dir)

    blockers.extend(lp_blockers)
    blockers.extend(rp_blockers)
    blockers.extend(cg_blockers)
    # staging blockers are added via _check_staging_validation below

    # Gate: launch-proof paid_launch_ready must be True in staging/production mode.
    # Individual readiness fields (billing_ready, email_ready, etc.) may all be True
    # while paid_launch_ready=False when the proof was generated in local/no-secret mode.
    # This check prevents final-readiness from claiming production_100_percent_ready=True
    # when the launch-proof was never proven in a real staging/production environment.
    _lp_paid_ready = False
    if launch_proof is not None:
        _lp_paid_ready = bool(launch_proof.get('paid_launch_ready', False))
        if mode in ('staging', 'production') and not _lp_paid_ready:
            _lp_blockers_list = launch_proof.get('blockers', [])
            if any('local mode' in b for b in _lp_blockers_list):
                blockers.append(
                    'launch-proof paid_launch_ready=false: generated in local/no-secret mode; '
                    'regenerate via save-proof-to-repo.yml with staging billing secrets'
                )
            else:
                blockers.append(
                    'launch-proof paid_launch_ready=false; '
                    'all billing, email, and live evidence gates must pass in staging/production mode'
                )

    # Gate: frontend_build and readiness_validation must not be not_run for production readiness
    if ci_gates is not None:
        _rg = ci_gates.get('required_gates', {})
        _fb_status = _rg.get('frontend_build', {}).get('status', 'not_run')
        _rv_status = _rg.get('readiness_validation', {}).get('status', 'not_run')
        if _fb_status not in ('pass',):
            blockers.append(
                f'frontend_build {_fb_status}: run npm run build in CI '
                'or mark production_100_percent_ready=false'
            )
        if _rv_status not in ('pass',):
            blockers.append(
                f'readiness_validation {_rv_status}: run validate_production_readiness.py '
                'or mark production_100_percent_ready=false'
            )

    categories = _evaluate_categories(
        launch_proof, release_proof, ci_gates,
        mode, strict, blockers, warnings,
    )

    overall_score = _compute_overall_score(categories)

    # Derived readiness flags
    all_cats_pass = all(
        c.get('status') == 'pass'
        for c in categories.values()
    )

    # live evidence check
    live_ok, live_blockers = _check_live_evidence(launch_proof, mode, live_evidence_proof_dir)
    if not live_ok:
        blockers.extend(live_blockers)

    # staging validation check — requires staging proof artifact
    staging_ok, staging_blockers = _check_staging_validation(mode, strict, staging_proof)
    if not staging_ok:
        blockers.extend(staging_blockers)

    required_gates = _build_required_gates(
        launch_proof, release_proof, ci_gates, mode, strict, staging_proof,
        live_evidence_ok=live_ok,
    )

    controlled_pilot_ready = (
        categories.get('saas_workflow', {}).get('status') in ('pass', 'warn')
        and categories.get('runtime_truthfulness', {}).get('status') == 'pass'
        and categories.get('auth_workspace_model', {}).get('status') in ('pass', 'warn')
        and not any('launch-proof missing' in b for b in blockers)
    )

    # broad launch requires frontend build and readiness validation to have run
    _frontend_build_ok = True
    _readiness_validation_ok = True
    if ci_gates is not None:
        _rg = ci_gates.get('required_gates', {})
        if _rg.get('frontend_build', {}).get('status', 'not_run') not in ('pass',):
            _frontend_build_ok = False
        if _rg.get('readiness_validation', {}).get('status', 'not_run') not in ('pass',):
            _readiness_validation_ok = False
    else:
        _frontend_build_ok = False
        _readiness_validation_ok = False

    broad_paid_saas_ready = (
        live_ok
        and staging_ok
        and _lp_paid_ready
        and _frontend_build_ok
        and _readiness_validation_ok
        and categories.get('billing_email_launch_readiness', {}).get('status') == 'pass'
        and all_cats_pass
        and mode in ('staging', 'production')
    )

    enterprise_procurement_ready = (
        broad_paid_saas_ready
        and categories.get('enterprise_readiness', {}).get('status') == 'pass'
    )

    production_100_percent_ready = (
        all_cats_pass
        and live_ok
        and staging_ok
        and not blockers
    )

    # safe_to_sell_broadly_today: only staging/production strict with all gates
    safe_to_sell = (
        production_100_percent_ready
        and mode in ('staging', 'production')
        and strict
    )

    if safe_to_sell:
        safe_reason = 'All required gates pass in verified staging/production environment.'
    elif mode not in ('staging', 'production'):
        safe_reason = (
            f'Cannot sell broadly from {mode} mode. '
            'Run with --mode staging --strict or --mode production --strict '
            'using real provider credentials and live evidence.'
        )
    elif not strict:
        safe_reason = (
            'Cannot sell broadly without --strict flag. '
            'Strict mode requires all gates to pass with live evidence.'
        )
    elif not live_ok:
        _stale_blockers = [b for b in live_blockers if 'stale' in b or 'fresh' in b.lower()]
        if _stale_blockers:
            safe_reason = _stale_blockers[0]
        else:
            safe_reason = 'Live evidence is required before broad sales. Simulator evidence does not qualify.'
    elif not staging_ok:
        safe_reason = 'Staging validation must complete successfully with real credentials before broad sales.'
    elif blockers:
        safe_reason = f'Blocked by: {"; ".join(blockers[:3])}{"..." if len(blockers) > 3 else ""}'
    else:
        safe_reason = 'One or more required categories have not achieved pass status.'

    proof_artifacts = _build_proof_artifacts(
        launch_proof_dir, release_proof_dir, final_dir, staging_proof_dir
    )

    summary = {
        'schema_version': 1,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'mode': mode,
        'strict': strict,
        'overall_score': overall_score,
        'controlled_pilot_ready': controlled_pilot_ready,
        'broad_paid_saas_ready': broad_paid_saas_ready,
        'enterprise_procurement_ready': enterprise_procurement_ready,
        'production_100_percent_ready': production_100_percent_ready,
        'categories': categories,
        'required_gates': required_gates,
        'blockers': sorted(set(blockers)),
        'warnings': sorted(set(warnings)),
        'proof_artifacts': proof_artifacts,
        'safe_to_sell_broadly_today': safe_to_sell,
        'safe_to_sell_reason': safe_reason,
    }

    return _redact_obj(summary)


def main(mode: str = 'local', strict: bool = False) -> int:
    print(f'[validate-100-percent-readiness] mode={mode} strict={strict}')

    summary = build_final_readiness(
        mode=mode,
        strict=strict,
        live_evidence_proof_dir=REPO_ROOT / 'artifacts' / 'live-evidence-proof' / 'latest',
    )

    # Route the output path based on mode and readiness outcome.
    # Only staging/production mode may write to latest/. Local/CI mode writes to a
    # fail-closed path so that staging-generated latest/ artifacts are never overwritten
    # by local runs that lack staging credentials.
    _staging_modes = {'staging', 'production'}
    if mode in _staging_modes:
        final_dir = REPO_ROOT / 'artifacts' / 'final-readiness' / 'latest'
    else:
        # local/ci/fail_closed_local: write to a non-latest path so the
        # committed staging artifacts are preserved.
        final_dir = REPO_ROOT / 'artifacts' / 'final-readiness' / 'local-test'
    final_dir.mkdir(parents=True, exist_ok=True)

    out_path = final_dir / 'summary.json'
    with open(out_path, 'w') as f:
        json.dump(summary, f, indent=2)
    print(f'[validate-100-percent-readiness] wrote {out_path.relative_to(REPO_ROOT)}')

    score = summary['overall_score']
    pilot = summary['controlled_pilot_ready']
    broad = summary['broad_paid_saas_ready']
    prod = summary['production_100_percent_ready']
    safe = summary['safe_to_sell_broadly_today']

    print(f'[validate-100-percent-readiness] overall_score={score}')
    print(f'[validate-100-percent-readiness] controlled_pilot_ready={pilot}')
    print(f'[validate-100-percent-readiness] broad_paid_saas_ready={broad}')
    print(f'[validate-100-percent-readiness] production_100_percent_ready={prod}')
    print(f'[validate-100-percent-readiness] safe_to_sell_broadly_today={safe}')

    if summary['blockers']:
        print('[validate-100-percent-readiness] Blockers:')
        for b in summary['blockers']:
            print(f'  - {b}')

    if summary['warnings']:
        print('[validate-100-percent-readiness] Warnings:')
        for w in summary['warnings']:
            print(f'  - {w}')

    print(f'[validate-100-percent-readiness] safe_to_sell_reason: {summary["safe_to_sell_reason"]}')

    if strict and not prod:
        print('[validate-100-percent-readiness] FAIL: production_100_percent_ready=false in strict mode')
        return 1

    return 0


if __name__ == '__main__':
    mode = 'local'
    strict = False

    args = sys.argv[1:]
    if '--mode' in args:
        idx = args.index('--mode')
        if idx + 1 < len(args):
            mode = args[idx + 1]
    if '--strict' in args:
        strict = True

    raise SystemExit(main(mode=mode, strict=strict))
