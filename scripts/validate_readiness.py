#!/usr/bin/env python3
"""
Lightweight CI readiness validation gate for Decoda RWA Guard.

Validates that proof artifacts from earlier CI steps are present, consistent,
and that the Python backend imports cleanly.

Designed to run BEFORE generate_release_proof.py so results can be recorded
in ci-required-gates.json as the readiness_validation gate.

Exit codes:
  0  All checks passed
  1  One or more checks failed
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def main() -> int:
    errors: list[str] = []

    # 1. Proof artifacts from prior CI steps must exist.
    required_artifacts = [
        "artifacts/staging-proof/latest/summary.json",
        "artifacts/launch-proof/latest/summary.json",
        "artifacts/live-evidence-proof/latest/summary.json",
    ]
    for rel in required_artifacts:
        p = REPO_ROOT / rel
        if not p.exists():
            errors.append(f"Required artifact missing: {rel}")
        elif _load_json(p) is None:
            errors.append(f"Cannot parse JSON: {rel}")

    # 2. Staging proof must have no "not configured" blockers.
    sp_path = REPO_ROOT / "artifacts/staging-proof/latest/summary.json"
    if sp_path.exists():
        sp = _load_json(sp_path)
        if sp is not None:
            not_cfg = [b for b in sp.get("blockers", []) if "not configured" in b]
            if not_cfg:
                errors.append(f"Staging proof: unconfigured blockers: {not_cfg[0]}")

    # 3. Python backend import health — ensures core module is importable.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; sys.path.insert(0, '.'); "
                "from services.api.app.paid_launch_readiness import build_paid_launch_readiness; "
                "r = build_paid_launch_readiness(); "
                "assert isinstance(r, dict), 'unexpected return type'"
            ),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        timeout=30,
    )
    if result.returncode != 0:
        msg = (result.stdout + result.stderr).decode(errors="replace").strip()[:300]
        errors.append(f"Backend import health check failed: {msg}")

    if errors:
        print("[validate-readiness] FAIL")
        for e in errors:
            print(f"  ERROR: {e}")
        return 1

    print("[validate-readiness] PASS: all readiness checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
