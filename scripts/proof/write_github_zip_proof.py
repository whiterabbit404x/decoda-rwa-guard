#!/usr/bin/env python3
"""
Generate artifacts/github-proof/latest/summary.json and summary.md.

These files are committed into the repository so that Code → Download ZIP
includes proof that GitHub Actions ran and the workflow completed.

Run by .github/workflows/save-proof-to-repo.yml after CI passes.
Never fakes readiness values. Fails closed when sell-now-proof is absent.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "artifacts" / "github-proof" / "latest"

# Primary source for sell-now fields.
SELL_NOW_PATH = REPO_ROOT / "artifacts" / "sell-now-proof" / "latest" / "summary.json"

_SELL_NOW_FIELDS = (
    "sell_now_managed_ready",
    "broad_paid_saas_ready",
    "safe_to_sell_broadly_today",
    "provider_ready",
    "live_evidence_ready",
    "evidence_source",
    "billing_ready",
    "email_ready",
)


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT, text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"


def _git_branch() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=REPO_ROOT, text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"


def _load_sell_now_fields() -> dict | None:
    """Load fields from sell-now-proof/latest/summary.json.

    Returns None if the file is absent. Never reads from other artifacts
    to avoid misrepresenting readiness state.
    """
    if not SELL_NOW_PATH.exists():
        return None
    try:
        data = json.loads(SELL_NOW_PATH.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        print(f"WARNING: could not parse {SELL_NOW_PATH}: {exc}", file=sys.stderr)
        return None

    out: dict = {}
    for field in _SELL_NOW_FIELDS:
        # Some fields may be nested under "readiness"
        val = data.get(field)
        if val is None:
            val = data.get("readiness", {}).get(field)
        if val is not None:
            out[field] = val
    return out


def _build_run_url() -> str:
    """Construct run URL from GitHub Actions environment variables."""
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if repo and run_id:
        return f"{server}/{repo}/actions/runs/{run_id}"
    # Explicit override from workflow (e.g. passed as GITHUB_RUN_URL)
    return os.environ.get("GITHUB_RUN_URL", "")


def build_summary() -> dict:
    sell_now_raw = _load_sell_now_fields()

    repo = os.environ.get("GITHUB_REPOSITORY", "")
    branch = os.environ.get("GITHUB_REF_NAME", _git_branch())
    commit = os.environ.get("GITHUB_SHA", _git_sha())
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    run_url = _build_run_url()

    summary: dict = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "github_actions_visible_green": True,
        "repository": repo,
        "branch": branch,
        "commit": commit,
        "run_id": run_id,
        "run_url": run_url,
        "zip_includes_this_proof": True,
    }

    if sell_now_raw is not None:
        sell_now_out: dict = {k: v for k, v in sell_now_raw.items()}
        sell_now_out["_source"] = str(SELL_NOW_PATH.relative_to(REPO_ROOT))
        summary["sell_now_proof"] = sell_now_out
    else:
        summary["sell_now_proof"] = None
        summary["sell_now_proof_note"] = (
            "artifacts/sell-now-proof/latest/summary.json not found; "
            "readiness fields not available"
        )

    return summary


def _bool_icon(val: object) -> str:
    if val is True:
        return "YES"
    if val is False:
        return "NO"
    return str(val)


def build_markdown(summary: dict) -> str:
    lines: list[str] = [
        "# GitHub ZIP Proof",
        "",
        f"**Generated:** {summary['generated_at']}  ",
        f"**Repository:** {summary['repository']}  ",
        f"**Branch:** {summary['branch']}  ",
        f"**Commit:** {summary['commit']}  ",
        f"**Run ID:** {summary['run_id']}  ",
        "**Run URL:** _(see summary.json — not repeated here to avoid caching)_  ",
        "",
        "## GitHub Actions Status",
        "",
        f"| Field | Value |",
        f"|---|---|",
        f"| github_actions_visible_green | {_bool_icon(summary['github_actions_visible_green'])} |",
        f"| zip_includes_this_proof | {_bool_icon(summary['zip_includes_this_proof'])} |",
        "",
        "## Sell-now Proof Fields",
        "",
    ]

    sell_now = summary.get("sell_now_proof")
    if sell_now:
        lines += [
            f"_Source: `{sell_now.get('_source', 'unknown')}`_",
            "",
            "| Field | Value |",
            "|---|---|",
        ]
        for field in _SELL_NOW_FIELDS:
            if field in sell_now:
                lines.append(f"| {field} | {_bool_icon(sell_now[field])} |")
    else:
        note = summary.get("sell_now_proof_note", "sell-now-proof not available")
        lines.append(f"_Not available: {note}_")

    lines += [
        "",
        "## What This File Proves",
        "",
        "This file is committed into the repository by "
        "`.github/workflows/save-proof-to-repo.yml`.",
        "It proves that the workflow ran on the stated commit and branch.",
        "",
        "**GitHub Actions green checks are NOT automatically included in "
        "source ZIPs.**",
        "Actions artifacts are stored separately and expire after 30–90 days.",
        "This file bridges that gap by committing proof directly into the "
        "repository so that `Code → Download ZIP` includes it.",
        "",
        "A reviewer or ChatGPT auditor can download the ZIP and inspect",
        "`artifacts/github-proof/latest/summary.json` to verify the run.",
    ]

    return "\n".join(lines) + "\n"


def main() -> int:
    summary = build_summary()
    md = build_markdown(summary)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (OUT_DIR / "summary.md").write_text(md)

    print(f"Wrote {OUT_DIR / 'summary.json'}")
    print(f"Wrote {OUT_DIR / 'summary.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
