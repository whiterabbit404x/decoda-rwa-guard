#!/usr/bin/env python3
"""
Generate artifacts/sell-now-proof/latest/summary.json and summary.md.

Aggregates proof from:
  artifacts/github-proof/latest/summary.json
  artifacts/staging-proof/latest/summary.json
  artifacts/live-evidence-proof/latest/summary.json
  artifacts/launch-proof/latest/summary.json
  artifacts/final-readiness/latest/summary.json
  services/api/artifacts/live_evidence/latest/summary.json  (optional)

Fail-closed rules:
  - If live-evidence-proof says provider_ready=false -> sell_now_managed_ready=false
  - If evidence_source is unknown/demo/simulator/fixture -> sell_now_managed_ready=false
  - If staging/billing/email are missing -> broad_paid_saas_ready=false
  - If proof files contradict each other -> list contradiction_flags and fail readiness
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "artifacts" / "sell-now-proof" / "latest"

SOURCES: dict[str, Path] = {
    "github_proof": REPO_ROOT / "artifacts" / "github-proof" / "latest" / "summary.json",
    "staging_proof": REPO_ROOT / "artifacts" / "staging-proof" / "latest" / "summary.json",
    "live_evidence_proof": REPO_ROOT / "artifacts" / "live-evidence-proof" / "latest" / "summary.json",
    "launch_proof": REPO_ROOT / "artifacts" / "launch-proof" / "latest" / "summary.json",
    "final_readiness": REPO_ROOT / "artifacts" / "final-readiness" / "latest" / "summary.json",
    "release_proof": REPO_ROOT / "artifacts" / "release-proof" / "latest" / "summary.json",
    "api_live_evidence": (
        REPO_ROOT / "services" / "api" / "artifacts" / "live_evidence" / "latest" / "summary.json"
    ),
}

# Evidence sources that disqualify managed readiness
_DISQUALIFYING_EVIDENCE_SOURCES = {"unknown", "demo", "simulator", "fixture", ""}


def _load(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        print(f"WARNING: could not parse {path}: {exc}", file=sys.stderr)
        return None


def _bool_icon(val: object) -> str:
    if val is True:
        return "YES"
    if val is False:
        return "NO"
    return str(val) if val is not None else "n/a"


def build_summary(loaded: dict[str, dict | None]) -> dict:
    github = loaded.get("github_proof") or {}
    staging = loaded.get("staging_proof") or {}
    live_ev = loaded.get("live_evidence_proof") or {}
    launch = loaded.get("launch_proof") or {}
    final_r = loaded.get("final_readiness") or {}
    release_p = loaded.get("release_proof") or {}
    api_live = loaded.get("api_live_evidence")  # None if absent

    contradiction_flags: list[str] = []

    # ── Live evidence fields (primary: live-evidence-proof) ───────────────────
    lpe = live_ev.get("live_provider_evidence", {})
    provider_ready = bool(lpe.get("provider_ready", False))
    live_evidence_ready = bool(lpe.get("live_evidence_ready", False))
    evidence_source: str = lpe.get("evidence_source", "unknown") or "unknown"

    # Cross-check api/live_evidence against primary proof
    if api_live is not None:
        api_provider_ready = bool(api_live.get("provider_ready", False))
        api_live_ev_ready = bool(api_live.get("live_evidence_ready", False))
        api_evidence_source = api_live.get("evidence_source", "unknown") or "unknown"

        if api_provider_ready and not provider_ready:
            contradiction_flags.append(
                "api/live_evidence says provider_ready=true but live-evidence-proof says provider_ready=false"
            )
        if (
            api_live_ev_ready
            and not live_evidence_ready
            and api_evidence_source not in _DISQUALIFYING_EVIDENCE_SOURCES
        ):
            contradiction_flags.append(
                f"api/live_evidence says live_evidence_ready=true (source={api_evidence_source!r}) "
                "but live-evidence-proof says live_evidence_ready=false"
            )

    # ── GitHub Actions fields ─────────────────────────────────────────────────
    github_run_id: str = github.get("run_id") or ""
    github_repository: str = github.get("repository") or ""
    github_actions_visible_green = bool(github.get("github_actions_visible_green", False))

    # Local run: no real GITHUB_RUN_ID — cannot claim CI green
    if not github_run_id or not github_repository:
        if github_actions_visible_green:
            contradiction_flags.append(
                "github-proof claims github_actions_visible_green=true but run_id or repository is empty "
                "(locally generated proof does not prove real CI run)"
            )
        github_actions_visible_green = False

    # ── Staging fields ────────────────────────────────────────────────────────
    staging_runtime_reachable = bool(staging.get("staging_runtime_reachable", False))
    staging_database_reachable = bool(staging.get("staging_database_reachable", False))
    staging_worker_enabled = bool(staging.get("staging_worker_enabled", False))

    # ── Launch/billing/email fields ───────────────────────────────────────────
    launch_readiness = launch.get("readiness", {})
    billing_ready = bool(launch_readiness.get("billing_ready", False))
    email_ready = bool(launch_readiness.get("email_ready", False))

    # ── Release proof fields ──────────────────────────────────────────────────
    release_status: str = release_p.get("release_status") or "unknown"
    rp_ci_gates_ready: bool = bool(release_p.get("ci_required_gates_ready", False))
    rp_test_report_ready: bool = bool(release_p.get("test_report_ready", False))

    # ── Cross-source contradiction checks ────────────────────────────────────
    if final_r.get("broad_paid_saas_ready") is True and not billing_ready:
        contradiction_flags.append(
            "final-readiness says broad_paid_saas_ready=true but launch-proof billing_ready=false"
        )

    staging_lp = staging.get("live_provider_validation", {})
    if staging_lp.get("provider_ready") is True and not provider_ready:
        contradiction_flags.append(
            "staging-proof live_provider_validation says provider_ready=true "
            "but live-evidence-proof says provider_ready=false"
        )

    # Release-proof gates: fail if release-proof exists and reports failure
    if release_p:
        if release_status == "fail":
            contradiction_flags.append(
                "release-proof release_status=fail: required CI gates or test suites are failing; "
                "safe_to_sell_broadly_today cannot be true"
            )
        if not rp_ci_gates_ready:
            contradiction_flags.append(
                "release-proof ci_required_gates_ready=false"
            )
        if not rp_test_report_ready:
            contradiction_flags.append(
                "release-proof test_report_ready=false: required test suites did not all pass"
            )

    # Final-readiness gate: stricter result wins
    if final_r.get("safe_to_sell_broadly_today") is False:
        contradiction_flags.append(
            "final-readiness says safe_to_sell_broadly_today=false; sell-now must not contradict"
        )

    # ── Fail-closed readiness ────────────────────────────────────────────────
    # Contradictions cause managed readiness to fail
    sell_now_managed_ready = (
        provider_ready
        and live_evidence_ready
        and evidence_source not in _DISQUALIFYING_EVIDENCE_SOURCES
        and not contradiction_flags
    )

    broad_paid_saas_ready = (
        staging_runtime_reachable
        and staging_database_reachable
        and staging_worker_enabled
        and billing_ready
        and email_ready
    )

    # Stricter result wins: if final-readiness says broad_paid_saas_ready=false, honour it
    if final_r.get("broad_paid_saas_ready") is False and broad_paid_saas_ready:
        contradiction_flags.append(
            "sell-now broad_paid_saas_ready=true but final-readiness broad_paid_saas_ready=false; "
            "stricter result wins"
        )
        broad_paid_saas_ready = False
        sell_now_managed_ready = False

    safe_to_sell_broadly_today = (
        sell_now_managed_ready
        and broad_paid_saas_ready
        and not (release_p and release_status == "fail")
        and not (release_p and not rp_ci_gates_ready)
        and not (release_p and not rp_test_report_ready)
        and final_r.get("safe_to_sell_broadly_today") is not False
    )

    # ── Blockers ─────────────────────────────────────────────────────────────
    blockers: list[str] = []

    if not provider_ready:
        blockers.append("provider_ready=false: live RPC provider not configured or not proven in CI artifact")
    if not live_evidence_ready:
        blockers.append("live_evidence_ready=false: no live telemetry chain proven in CI artifact")
    if evidence_source in _DISQUALIFYING_EVIDENCE_SOURCES:
        blockers.append(f"evidence_source={evidence_source!r}: not live evidence")
    for cf in contradiction_flags:
        blockers.append(f"contradiction: {cf}")
    if not staging_runtime_reachable:
        blockers.append("staging_runtime_reachable=false")
    if not staging_database_reachable:
        blockers.append("staging_database_reachable=false")
    if not staging_worker_enabled:
        blockers.append("staging_worker_enabled=false")
    if not billing_ready:
        blockers.append("billing_ready=false: billing provider not configured")
    if not email_ready:
        blockers.append("email_ready=false: email provider not configured")

    # ── Warnings ─────────────────────────────────────────────────────────────
    warnings: list[str] = []

    if not github_actions_visible_green:
        warnings.append("github_actions_visible_green=false: CI green status not proven in artifact")

    for key, path in SOURCES.items():
        if loaded.get(key) is None:
            if key == "api_live_evidence":
                warnings.append(
                    "optional source missing: services/api/artifacts/live_evidence/latest/summary.json"
                )
            else:
                warnings.append(f"required source missing: {path.relative_to(REPO_ROOT)}")

    # ── Safe / prohibited claims ─────────────────────────────────────────────
    safe_claims: list[str] = []
    prohibited_claims: list[str] = []

    if final_r.get("controlled_pilot_ready"):
        safe_claims.append(
            "controlled pilot ready: single customer with direct onboarding, no billing required"
        )
    if final_r.get("overall_score") is not None:
        safe_claims.append(f"overall readiness score: {final_r['overall_score']}/100")

    if sell_now_managed_ready:
        safe_claims.append("live EVM telemetry received and proven in CI artifact")
        safe_claims.append("detection → alert → incident chain proven from live provider data")
    else:
        prohibited_claims.append("Do NOT claim live monitoring proven from real RPC data")
        prohibited_claims.append("Do NOT claim this product is ready for managed or pilot customer delivery")

    if not broad_paid_saas_ready:
        prohibited_claims.append("Do NOT claim broad paid SaaS readiness")
        prohibited_claims.append("Do NOT claim billing, email, or staging are production-ready")

    if not safe_to_sell_broadly_today:
        prohibited_claims.append("Do NOT claim safe_to_sell_broadly_today=true")

    # ── Missing required sources ─────────────────────────────────────────────
    missing_required = [
        str(SOURCES[k].relative_to(REPO_ROOT))
        for k in SOURCES
        if k != "api_live_evidence" and loaded.get(k) is None
    ]

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        # Top-level readiness
        "sell_now_managed_ready": sell_now_managed_ready,
        "broad_paid_saas_ready": broad_paid_saas_ready,
        "safe_to_sell_broadly_today": safe_to_sell_broadly_today,
        # Evidence
        "provider_ready": provider_ready,
        "live_evidence_ready": live_evidence_ready,
        "evidence_source": evidence_source,
        # GitHub Actions
        "github_actions_visible_green": github_actions_visible_green,
        # Staging / infrastructure
        "staging_runtime_reachable": staging_runtime_reachable,
        "staging_database_reachable": staging_database_reachable,
        "staging_worker_enabled": staging_worker_enabled,
        # Providers
        "billing_ready": billing_ready,
        "email_ready": email_ready,
        # Release proof status
        "release_status": release_status,
        "release_ci_gates_ready": rp_ci_gates_ready,
        "release_test_report_ready": rp_test_report_ready,
        # Diagnostics
        "blockers": blockers,
        "warnings": warnings,
        "contradiction_flags": contradiction_flags,
        "safe_claims": safe_claims,
        "prohibited_claims": prohibited_claims,
        # Source tracking
        "sources_loaded": {k: (v is not None) for k, v in loaded.items()},
        "missing_required_sources": missing_required,
    }


def build_markdown(summary: dict) -> str:
    lines: list[str] = [
        "# Sell-Now Proof",
        "",
        f"**Generated:** {summary['generated_at']}",
        "",
        "## Readiness Summary",
        "",
        "| Flag | Value |",
        "|---|---|",
        f"| sell_now_managed_ready | {_bool_icon(summary['sell_now_managed_ready'])} |",
        f"| broad_paid_saas_ready | {_bool_icon(summary['broad_paid_saas_ready'])} |",
        f"| safe_to_sell_broadly_today | {_bool_icon(summary['safe_to_sell_broadly_today'])} |",
        "",
        "## Evidence",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| provider_ready | {_bool_icon(summary['provider_ready'])} |",
        f"| live_evidence_ready | {_bool_icon(summary['live_evidence_ready'])} |",
        f"| evidence_source | {summary['evidence_source']} |",
        f"| github_actions_visible_green | {_bool_icon(summary['github_actions_visible_green'])} |",
        "",
        "## Staging / Infrastructure",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| staging_runtime_reachable | {_bool_icon(summary['staging_runtime_reachable'])} |",
        f"| staging_database_reachable | {_bool_icon(summary['staging_database_reachable'])} |",
        f"| staging_worker_enabled | {_bool_icon(summary['staging_worker_enabled'])} |",
        f"| billing_ready | {_bool_icon(summary['billing_ready'])} |",
        f"| email_ready | {_bool_icon(summary['email_ready'])} |",
        "",
    ]

    if summary.get("blockers"):
        lines += ["## Blockers", ""]
        for b in summary["blockers"]:
            lines.append(f"- {b}")
        lines.append("")

    if summary.get("warnings"):
        lines += ["## Warnings", ""]
        for w in summary["warnings"]:
            lines.append(f"- {w}")
        lines.append("")

    if summary.get("contradiction_flags"):
        lines += ["## Contradiction Flags", ""]
        for c in summary["contradiction_flags"]:
            lines.append(f"- {c}")
        lines.append("")

    if summary.get("safe_claims"):
        lines += ["## Safe Claims", ""]
        for c in summary["safe_claims"]:
            lines.append(f"- {c}")
        lines.append("")

    if summary.get("prohibited_claims"):
        lines += ["## Prohibited Claims", ""]
        for c in summary["prohibited_claims"]:
            lines.append(f"- {c}")
        lines.append("")

    sources = summary.get("sources_loaded", {})
    if sources:
        lines += ["## Sources", "", "| Source | Loaded |", "|---|---|"]
        for k, loaded in sources.items():
            lines.append(f"| {k} | {_bool_icon(loaded)} |")
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    loaded = {key: _load(path) for key, path in SOURCES.items()}
    summary = build_summary(loaded)
    md = build_markdown(summary)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (OUT_DIR / "summary.md").write_text(md)

    print(f"Wrote {OUT_DIR / 'summary.json'}")
    print(f"Wrote {OUT_DIR / 'summary.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
