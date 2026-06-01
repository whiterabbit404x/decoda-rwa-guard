"""
Tests for the sell-now proof system.

Proves:
  1. Missing sell-now proof is reported in github-proof
  2. Local github-proof cannot fake a real run_id / github_actions_visible_green
  3. Missing RPC (provider_ready=false) fails managed readiness
  4. Missing staging/billing/email fails broad SaaS readiness only (not managed)
  5. Full real proof (all fields present and truthy) passes
"""
from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Path setup — allow importing scripts from the repo root
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_PROOF = REPO_ROOT / "scripts" / "proof"

for _p in [str(REPO_ROOT), str(SCRIPTS_PROOF)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Import modules under test
_sell_now_mod = importlib.import_module("write_sell_now_proof")
_github_mod = importlib.import_module("write_github_zip_proof")

build_sell_now_summary = _sell_now_mod.build_summary
build_github_summary = _github_mod.build_summary


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_live_evidence_proof(
    provider_ready: bool = False,
    live_evidence_ready: bool = False,
    evidence_source: str = "unknown",
) -> dict:
    """Return a live-evidence-proof structure."""
    return {
        "live_provider_evidence": {
            "provider_ready": provider_ready,
            "live_evidence_ready": live_evidence_ready,
            "evidence_source": evidence_source,
        }
    }


def _minimal_staging_proof(
    staging_runtime_reachable: bool = False,
    staging_database_reachable: bool = False,
    staging_worker_enabled: bool = False,
) -> dict:
    return {
        "staging_runtime_reachable": staging_runtime_reachable,
        "staging_database_reachable": staging_database_reachable,
        "staging_worker_enabled": staging_worker_enabled,
        "broad_paid_saas_ready": False,
        "blockers": [],
    }


def _minimal_launch_proof(
    billing_ready: bool = False,
    email_ready: bool = False,
) -> dict:
    return {
        "readiness": {
            "billing_ready": billing_ready,
            "email_ready": email_ready,
        }
    }


def _full_real_sources() -> dict:
    """All sources set to passing values — simulates a fully deployed product."""
    return {
        "github_proof": {
            "github_actions_visible_green": True,
            "repository": "acme/decoda-rwa-guard",
            "run_id": "987654321",
            "branch": "main",
            "commit": "abc123",
        },
        "staging_proof": _minimal_staging_proof(
            staging_runtime_reachable=True,
            staging_database_reachable=True,
            staging_worker_enabled=True,
        ),
        "live_evidence_proof": _minimal_live_evidence_proof(
            provider_ready=True,
            live_evidence_ready=True,
            evidence_source="live",
        ),
        "launch_proof": _minimal_launch_proof(billing_ready=True, email_ready=True),
        "final_readiness": {
            "controlled_pilot_ready": True,
            "broad_paid_saas_ready": True,
            "overall_score": 100,
        },
        "api_live_evidence": None,
    }


# ---------------------------------------------------------------------------
# Test 1: missing sell-now proof is reported in github-proof
# ---------------------------------------------------------------------------

def test_missing_sell_now_proof_reported(tmp_path):
    """write_github_zip_proof reports sell_now_proof=null when file is absent."""
    # Point SELL_NOW_PATH at a non-existent file
    non_existent = tmp_path / "sell-now-proof" / "latest" / "summary.json"

    with patch.object(_github_mod, "SELL_NOW_PATH", non_existent):
        summary = build_github_summary()

    assert summary["sell_now_proof"] is None, "sell_now_proof should be None when file is missing"
    assert "sell_now_proof_note" in summary, "sell_now_proof_note should be present when file missing"
    assert "not found" in summary["sell_now_proof_note"].lower()


# ---------------------------------------------------------------------------
# Test 2: local github-proof cannot fake github_actions_visible_green
# ---------------------------------------------------------------------------

def test_local_github_proof_cannot_fake_run_id():
    """Without GITHUB_RUN_ID + GITHUB_REPOSITORY, github_actions_visible_green must be False."""
    env_overrides = {
        "GITHUB_RUN_ID": "",
        "GITHUB_REPOSITORY": "",
        "GITHUB_REF_NAME": "main",
        "GITHUB_SHA": "abc123",
    }
    with patch.dict("os.environ", env_overrides, clear=False):
        summary = build_github_summary()

    assert summary["github_runtime_context"] is False
    assert summary["github_actions_visible_green"] is False
    assert summary["run_id"] == ""
    assert summary["repository"] == ""


def test_github_proof_sets_green_only_with_both_run_id_and_repo():
    """github_actions_visible_green is True only when both run_id and repository are set."""
    env_with_both = {
        "GITHUB_RUN_ID": "111222333",
        "GITHUB_REPOSITORY": "acme/rwa-guard",
        "GITHUB_REF_NAME": "main",
        "GITHUB_SHA": "deadbeef",
        "GITHUB_SERVER_URL": "https://github.com",
    }
    with patch.dict("os.environ", env_with_both, clear=False):
        summary = build_github_summary()

    assert summary["github_runtime_context"] is True
    assert summary["github_actions_visible_green"] is True
    assert summary["run_id"] == "111222333"
    assert summary["repository"] == "acme/rwa-guard"


def test_github_proof_requires_both_fields_not_just_one():
    """run_id without repository (or vice-versa) is not enough for github_actions_visible_green."""
    env_run_id_only = {
        "GITHUB_RUN_ID": "999",
        "GITHUB_REPOSITORY": "",
        "GITHUB_REF_NAME": "main",
        "GITHUB_SHA": "abc",
    }
    with patch.dict("os.environ", env_run_id_only, clear=False):
        summary = build_github_summary()
    assert summary["github_actions_visible_green"] is False

    env_repo_only = {
        "GITHUB_RUN_ID": "",
        "GITHUB_REPOSITORY": "acme/rwa",
        "GITHUB_REF_NAME": "main",
        "GITHUB_SHA": "abc",
    }
    with patch.dict("os.environ", env_repo_only, clear=False):
        summary = build_github_summary()
    assert summary["github_actions_visible_green"] is False


# ---------------------------------------------------------------------------
# Test 3: missing RPC (provider_ready=false) fails managed readiness
# ---------------------------------------------------------------------------

def test_missing_rpc_fails_managed_readiness():
    """If live-evidence-proof says provider_ready=false, sell_now_managed_ready must be False."""
    sources = {
        "github_proof": None,
        "staging_proof": _minimal_staging_proof(True, True, True),
        "live_evidence_proof": _minimal_live_evidence_proof(
            provider_ready=False,
            live_evidence_ready=False,
            evidence_source="unknown",
        ),
        "launch_proof": _minimal_launch_proof(True, True),
        "final_readiness": {"controlled_pilot_ready": True, "broad_paid_saas_ready": False},
        "api_live_evidence": None,
    }
    summary = build_sell_now_summary(sources)

    assert summary["sell_now_managed_ready"] is False
    assert summary["provider_ready"] is False
    assert any("provider_ready=false" in b for b in summary["blockers"])


def test_disqualifying_evidence_source_fails_managed_readiness():
    """evidence_source in {demo, simulator, fixture, unknown} must block managed readiness."""
    for bad_source in ("demo", "simulator", "fixture", "unknown", ""):
        sources = {
            "github_proof": None,
            "staging_proof": None,
            "live_evidence_proof": _minimal_live_evidence_proof(
                provider_ready=True,
                live_evidence_ready=True,
                evidence_source=bad_source,
            ),
            "launch_proof": None,
            "final_readiness": None,
            "api_live_evidence": None,
        }
        summary = build_sell_now_summary(sources)
        assert summary["sell_now_managed_ready"] is False, (
            f"managed should be False when evidence_source={bad_source!r}"
        )
        assert any("evidence_source" in b for b in summary["blockers"]), (
            f"blocker missing for evidence_source={bad_source!r}"
        )


# ---------------------------------------------------------------------------
# Test 4: missing staging/billing/email fails broad SaaS readiness only
# ---------------------------------------------------------------------------

def test_missing_staging_billing_email_fails_broad_saas_only():
    """Live evidence OK + managed ready, but broad_paid_saas_ready=False when staging/billing/email absent."""
    sources = {
        "github_proof": None,
        "staging_proof": _minimal_staging_proof(False, False, False),  # staging not ready
        "live_evidence_proof": _minimal_live_evidence_proof(
            provider_ready=True,
            live_evidence_ready=True,
            evidence_source="live",
        ),
        "launch_proof": _minimal_launch_proof(billing_ready=False, email_ready=False),
        "final_readiness": {"controlled_pilot_ready": True, "broad_paid_saas_ready": False},
        "api_live_evidence": None,
    }
    summary = build_sell_now_summary(sources)

    assert summary["sell_now_managed_ready"] is True, (
        "managed should be True when provider_ready=True and evidence_source=live (no contradictions)"
    )
    assert summary["broad_paid_saas_ready"] is False
    assert summary["safe_to_sell_broadly_today"] is False
    assert any("billing_ready=false" in b for b in summary["blockers"])
    assert any("email_ready=false" in b for b in summary["blockers"])


def test_missing_billing_only_fails_broad_not_managed():
    """billing_ready=False blocks broad_paid_saas_ready but not sell_now_managed_ready."""
    sources = {
        "github_proof": None,
        "staging_proof": _minimal_staging_proof(True, True, True),
        "live_evidence_proof": _minimal_live_evidence_proof(
            provider_ready=True,
            live_evidence_ready=True,
            evidence_source="live",
        ),
        "launch_proof": _minimal_launch_proof(billing_ready=False, email_ready=True),
        "final_readiness": None,
        "api_live_evidence": None,
    }
    summary = build_sell_now_summary(sources)

    assert summary["sell_now_managed_ready"] is True
    assert summary["broad_paid_saas_ready"] is False
    assert any("billing_ready=false" in b for b in summary["blockers"])
    assert not any("email_ready=false" in b for b in summary["blockers"])


# ---------------------------------------------------------------------------
# Test 5: full real proof passes all readiness flags
# ---------------------------------------------------------------------------

def test_full_real_proof_passes():
    """All sources passing → all three readiness flags True and no blockers."""
    sources = _full_real_sources()
    summary = build_sell_now_summary(sources)

    assert summary["sell_now_managed_ready"] is True
    assert summary["broad_paid_saas_ready"] is True
    assert summary["safe_to_sell_broadly_today"] is True
    assert summary["provider_ready"] is True
    assert summary["live_evidence_ready"] is True
    assert summary["evidence_source"] == "live"
    assert summary["github_actions_visible_green"] is True
    assert summary["staging_runtime_reachable"] is True
    assert summary["staging_database_reachable"] is True
    assert summary["staging_worker_enabled"] is True
    assert summary["billing_ready"] is True
    assert summary["email_ready"] is True
    assert summary["contradiction_flags"] == []
    assert summary["blockers"] == []


# ---------------------------------------------------------------------------
# Test 6: contradiction flags fail managed readiness
# ---------------------------------------------------------------------------

def test_contradiction_github_proof_local_run():
    """github-proof with github_actions_visible_green=True but empty run_id triggers contradiction."""
    sources = {
        "github_proof": {
            "github_actions_visible_green": True,
            "repository": "",   # empty — local run
            "run_id": "",
        },
        "staging_proof": None,
        "live_evidence_proof": _minimal_live_evidence_proof(
            provider_ready=True, live_evidence_ready=True, evidence_source="live"
        ),
        "launch_proof": _minimal_launch_proof(True, True),
        "final_readiness": None,
        "api_live_evidence": None,
    }
    summary = build_sell_now_summary(sources)

    assert summary["github_actions_visible_green"] is False
    assert len(summary["contradiction_flags"]) >= 1
    assert any("run_id" in cf or "repository" in cf for cf in summary["contradiction_flags"])
    # Contradiction causes managed readiness to fail
    assert summary["sell_now_managed_ready"] is False


def test_api_live_contradicts_live_evidence_proof():
    """api/live_evidence says provider_ready=True but live-evidence-proof says False → contradiction flagged."""
    sources = {
        "github_proof": None,
        "staging_proof": None,
        "live_evidence_proof": _minimal_live_evidence_proof(
            provider_ready=False, live_evidence_ready=False, evidence_source="unknown"
        ),
        "launch_proof": None,
        "final_readiness": None,
        "api_live_evidence": {
            "provider_ready": True,
            "live_evidence_ready": True,
            "evidence_source": "live",
        },
    }
    summary = build_sell_now_summary(sources)

    assert len(summary["contradiction_flags"]) >= 1
    assert any("api/live_evidence" in cf for cf in summary["contradiction_flags"])
    # live-evidence-proof is authoritative — managed readiness must fail
    assert summary["sell_now_managed_ready"] is False
    assert summary["provider_ready"] is False


# ---------------------------------------------------------------------------
# Test 7: sell_now_managed_ready becomes True when live-evidence-proof is True
# ---------------------------------------------------------------------------

def test_sell_now_managed_true_when_live_evidence_proof_true():
    """sell_now_managed_ready=True when live-evidence-proof reports provider_ready=True, evidence_source=live."""
    sources = {
        "github_proof": None,
        "staging_proof": None,
        "live_evidence_proof": _minimal_live_evidence_proof(
            provider_ready=True,
            live_evidence_ready=True,
            evidence_source="live",
        ),
        "launch_proof": None,
        "final_readiness": None,
        "api_live_evidence": None,
    }
    summary = build_sell_now_summary(sources)

    assert summary["sell_now_managed_ready"] is True
    assert summary["provider_ready"] is True
    assert summary["live_evidence_ready"] is True
    assert summary["evidence_source"] == "live"
    assert summary["contradiction_flags"] == []


def test_sell_now_managed_true_with_consistent_api_live_evidence():
    """sell_now_managed_ready=True when live-evidence-proof and api_live_evidence agree."""
    sources = {
        "github_proof": None,
        "staging_proof": None,
        "live_evidence_proof": _minimal_live_evidence_proof(
            provider_ready=True,
            live_evidence_ready=True,
            evidence_source="live",
        ),
        "launch_proof": None,
        "final_readiness": None,
        "api_live_evidence": {
            "provider_ready": True,
            "live_evidence_ready": True,
            "evidence_source": "live",
        },
    }
    summary = build_sell_now_summary(sources)

    assert summary["sell_now_managed_ready"] is True
    assert summary["contradiction_flags"] == []


def test_broad_paid_saas_not_true_without_staging_billing_email():
    """broad_paid_saas_ready must not be True unless staging, billing, and email are all True."""
    sources = {
        "github_proof": None,
        "staging_proof": _minimal_staging_proof(True, True, True),
        "live_evidence_proof": _minimal_live_evidence_proof(
            provider_ready=True,
            live_evidence_ready=True,
            evidence_source="live",
        ),
        "launch_proof": _minimal_launch_proof(billing_ready=False, email_ready=False),
        # final_readiness must agree — claiming broad_paid_saas_ready=True here
        # would be a contradiction and also block sell_now_managed_ready.
        "final_readiness": {"broad_paid_saas_ready": False},
        "api_live_evidence": None,
    }
    summary = build_sell_now_summary(sources)

    # sell_now_managed_ready can be True (live evidence is good, no contradictions)
    assert summary["sell_now_managed_ready"] is True
    # broad_paid_saas_ready must be False because billing and email are missing
    assert summary["broad_paid_saas_ready"] is False
    assert summary["safe_to_sell_broadly_today"] is False
    assert summary["contradiction_flags"] == []
