#!/usr/bin/env python3
"""
Write live_evidence_validated marker when live-evidence-proof confirms live evidence.

Reads:  artifacts/live-evidence-proof/latest/summary.json
Writes: artifacts/staging-proof/latest/live_evidence_validated
        (only when provider_ready=true, live_evidence_ready=true, evidence_source="live")

Always exits 0 — marker is simply not written when conditions are not met.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_LIVE_EVIDENCE_PROOF = (
    REPO_ROOT / 'artifacts' / 'live-evidence-proof' / 'latest' / 'summary.json'
)
_PROOF_DIR = REPO_ROOT / 'artifacts' / 'staging-proof' / 'latest'
_MARKER = _PROOF_DIR / 'live_evidence_validated'


def main() -> int:
    if not _LIVE_EVIDENCE_PROOF.exists():
        print('[validate-live-evidence-marker] live-evidence-proof not found — marker not written')
        return 0

    try:
        with open(_LIVE_EVIDENCE_PROOF) as f:
            proof = json.load(f)
    except Exception as exc:
        print(
            f'[validate-live-evidence-marker] could not read live-evidence-proof: {exc}',
            file=sys.stderr,
        )
        return 0

    lpe = proof.get('live_provider_evidence', {})
    provider_ready = bool(lpe.get('provider_ready'))
    live_evidence_ready = bool(lpe.get('live_evidence_ready'))
    evidence_source = str(lpe.get('evidence_source') or '').strip().lower()

    if not (provider_ready and live_evidence_ready and evidence_source == 'live'):
        print(
            '[validate-live-evidence-marker] conditions not met — marker not written\n'
            f'  provider_ready={provider_ready}\n'
            f'  live_evidence_ready={live_evidence_ready}\n'
            f'  evidence_source={evidence_source!r}'
        )
        return 0

    _PROOF_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    _MARKER.write_text(json.dumps({
        'validated_at': now,
        'provider_ready': provider_ready,
        'live_evidence_ready': live_evidence_ready,
        'evidence_source': evidence_source,
    }, indent=2))

    try:
        label = _MARKER.relative_to(REPO_ROOT)
    except ValueError:
        label = _MARKER
    print(f'[validate-live-evidence-marker] wrote {label}')
    print(f'  provider_ready={provider_ready}')
    print(f'  live_evidence_ready={live_evidence_ready}')
    print(f'  evidence_source={evidence_source!r}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
