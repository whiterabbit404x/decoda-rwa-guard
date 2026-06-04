"""
Cross-artifact proof consistency tests.

Verifies that the proof artifacts on disk do not contradict each other.
These tests enforce the fail-closed invariants required before broad paid SaaS
or enterprise launch can be claimed.

Invariants enforced:
  1. final-readiness broad_paid_saas_ready=false  →  sell-now-proof must NOT say true
  2. final-readiness safe_to_sell_broadly_today=false  →  launch-proof must NOT say true
  3. live-evidence-proof live_evidence_ready=false  →  sell-now-proof must NOT say true
  4. ci-required-gates frontend_build=not_run  →  no artifact may say production_100_percent_ready=true
  5. ci-required-gates readiness_validation=not_run  →  no artifact may say enterprise_procurement_ready=true
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
ARTIFACTS = REPO_ROOT / "artifacts"


def _load(rel: str) -> dict:
    p = ARTIFACTS / rel
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


# ---------------------------------------------------------------------------
# 1. final-readiness broad_paid_saas_ready=false → sell-now must not say true
# ---------------------------------------------------------------------------
def test_sell_now_broad_paid_saas_agrees_with_final_readiness() -> None:
    """
    If final-readiness says broad_paid_saas_ready=false, sell-now-proof must
    also say false. Overclaiming broad paid SaaS in sell-now while final
    readiness blocks it is a proof consistency violation.
    """
    final_r = _load("final-readiness/latest/summary.json")
    sell_now = _load("sell-now-proof/latest/summary.json")

    if not final_r or not sell_now:
        pytest.skip("One or more artifacts missing; skipping cross-artifact check")

    fr_broad = final_r.get("broad_paid_saas_ready")
    sn_broad = sell_now.get("broad_paid_saas_ready")

    if fr_broad is False:
        assert sn_broad is not True, (
            f"CONSISTENCY VIOLATION: final-readiness says broad_paid_saas_ready=false "
            f"but sell-now-proof says broad_paid_saas_ready={sn_broad!r}. "
            f"Sell-now must honour the stricter final-readiness result. "
            f"final-readiness blockers: {final_r.get('blockers', [])}"
        )


# ---------------------------------------------------------------------------
# 2. final-readiness safe_to_sell_broadly_today=false → launch-proof must not say true
# ---------------------------------------------------------------------------
def test_launch_proof_safe_to_sell_agrees_with_final_readiness() -> None:
    """
    If final-readiness says safe_to_sell_broadly_today=false, launch-proof must
    also say false. A launch-proof that claims it is safe to sell while final
    readiness is blocked is a proof consistency violation.
    """
    final_r = _load("final-readiness/latest/summary.json")
    launch = _load("launch-proof/latest/summary.json")

    if not final_r or not launch:
        pytest.skip("One or more artifacts missing; skipping cross-artifact check")

    fr_safe = final_r.get("safe_to_sell_broadly_today")
    lp_safe = launch.get("safe_to_sell_broadly_today")

    if fr_safe is False:
        assert lp_safe is not True, (
            f"CONSISTENCY VIOLATION: final-readiness says safe_to_sell_broadly_today=false "
            f"but launch-proof says safe_to_sell_broadly_today={lp_safe!r}. "
            f"Launch-proof must not overclaim when final readiness is blocked. "
            f"final-readiness safe_to_sell_reason: {final_r.get('safe_to_sell_reason', '')}"
        )


# ---------------------------------------------------------------------------
# 3. live-evidence-proof live_evidence_ready=false → sell-now must not say true
# ---------------------------------------------------------------------------
def test_sell_now_live_evidence_agrees_with_live_evidence_proof() -> None:
    """
    If live-evidence-proof says live_evidence_ready=false, sell-now-proof must
    also say live_evidence_ready=false. The sell-now proof cannot claim live
    evidence is ready when the canonical live evidence artifact says it is not.
    """
    live_ev = _load("live-evidence-proof/latest/summary.json")
    sell_now = _load("sell-now-proof/latest/summary.json")

    if not live_ev or not sell_now:
        pytest.skip("One or more artifacts missing; skipping cross-artifact check")

    lpe = live_ev.get("live_provider_evidence", {})
    lep_ready = lpe.get("live_evidence_ready")
    sn_ready = sell_now.get("live_evidence_ready")

    if lep_ready is False:
        assert sn_ready is not True, (
            f"CONSISTENCY VIOLATION: live-evidence-proof says live_evidence_ready=false "
            f"but sell-now-proof says live_evidence_ready={sn_ready!r}. "
            f"Sell-now cannot claim live evidence ready when the canonical proof says false. "
            f"Staleness reason: {lpe.get('staleness_reason', lpe.get('freshness_check', {}))}"
        )


# ---------------------------------------------------------------------------
# 4. frontend_build=not_run → no artifact may say production_100_percent_ready=true
# ---------------------------------------------------------------------------
def test_production_100_percent_ready_blocked_when_frontend_build_not_run() -> None:
    """
    If ci-required-gates reports frontend_build=not_run, no artifact may claim
    production_100_percent_ready=true. A frontend build that has never run in CI
    cannot be part of a production-ready claim.
    """
    ci_gates = _load("release-proof/latest/ci-required-gates.json")
    if not ci_gates:
        pytest.skip("ci-required-gates artifact missing; skipping cross-artifact check")

    fb_status = ci_gates.get("required_gates", {}).get("frontend_build", {}).get("status", "not_run")
    if fb_status not in ("pass",):
        # Check all artifacts that have production_100_percent_ready
        artifacts_to_check = {
            "final-readiness/latest/summary.json": _load("final-readiness/latest/summary.json"),
            "sell-now-proof/latest/summary.json": _load("sell-now-proof/latest/summary.json"),
            "launch-proof/latest/summary.json": _load("launch-proof/latest/summary.json"),
        }
        for path, data in artifacts_to_check.items():
            if not data:
                continue
            prod_ready = data.get("production_100_percent_ready")
            assert prod_ready is not True, (
                f"CONSISTENCY VIOLATION: ci-required-gates frontend_build={fb_status!r} "
                f"but {path} says production_100_percent_ready=true. "
                f"A passing frontend build in CI is required before claiming 100% production readiness."
            )


# ---------------------------------------------------------------------------
# 5. readiness_validation=not_run → no artifact may say enterprise_procurement_ready=true
# ---------------------------------------------------------------------------
def test_enterprise_procurement_blocked_when_readiness_validation_not_run() -> None:
    """
    If ci-required-gates reports readiness_validation=not_run, no artifact may
    claim enterprise_procurement_ready=true. Enterprise procurement requires a
    completed and passing readiness validation run.
    """
    ci_gates = _load("release-proof/latest/ci-required-gates.json")
    if not ci_gates:
        pytest.skip("ci-required-gates artifact missing; skipping cross-artifact check")

    rv_status = ci_gates.get("required_gates", {}).get("readiness_validation", {}).get("status", "not_run")
    if rv_status not in ("pass",):
        artifacts_to_check = {
            "final-readiness/latest/summary.json": _load("final-readiness/latest/summary.json"),
            "sell-now-proof/latest/summary.json": _load("sell-now-proof/latest/summary.json"),
            "launch-proof/latest/summary.json": _load("launch-proof/latest/summary.json"),
        }
        for path, data in artifacts_to_check.items():
            if not data:
                continue
            ent_ready = data.get("enterprise_procurement_ready")
            assert ent_ready is not True, (
                f"CONSISTENCY VIOLATION: ci-required-gates readiness_validation={rv_status!r} "
                f"but {path} says enterprise_procurement_ready=true. "
                f"A passing validate_production_readiness.py run is required before claiming "
                f"enterprise procurement readiness."
            )
