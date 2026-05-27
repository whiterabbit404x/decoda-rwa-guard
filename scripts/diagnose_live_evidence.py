#!/usr/bin/env python3
"""
Safe diagnostic for live evidence configuration and artifacts.

Prints ONLY boolean presence flags and artifact counts.
NEVER prints secret values, RPC URLs, or provider keys.

Output lines:
  STAGING_EVM_RPC_URL_present=true/false
  EVM_RPC_URL_present=true/false
  STAGING_EVM_CHAIN_ID_present=true/false
  STAGING_WORKER_ENABLED=<value or (not set)>
  live_artifact_dir_exists=true/false
  live_rpc_polling_artifacts_count=N
  simulator_artifacts_count=N

live_rpc_polling_artifacts_count counts artifact files (summary, evidence,
telemetry_events) that have evidence_source="live" AND source_type="rpc_polling".

simulator_artifacts_count counts files that have evidence_source in
("guided_simulator", "simulator", "fixture").

Usage:
  python scripts/diagnose_live_evidence.py
  make diagnose-live-evidence
"""
from __future__ import annotations

import json
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_SERVICE_ARTIFACTS_DIR = (
    REPO_ROOT / 'services' / 'api' / 'artifacts' / 'live_evidence' / 'latest'
)

_SIMULATOR_SOURCES = frozenset({'guided_simulator', 'simulator', 'fixture'})


def diagnose(
    *,
    service_artifacts_dir: Path | None = None,
) -> dict:
    """
    Return a diagnostic dict with only boolean/integer values.
    Never returns or exposes secret values.
    """
    if service_artifacts_dir is None:
        service_artifacts_dir = _SERVICE_ARTIFACTS_DIR

    staging_rpc_present = bool((os.getenv('STAGING_EVM_RPC_URL') or '').strip())
    base_rpc_present = bool((os.getenv('EVM_RPC_URL') or '').strip())
    staging_chain_present = bool((os.getenv('STAGING_EVM_CHAIN_ID') or '').strip())
    worker_raw = (os.getenv('STAGING_WORKER_ENABLED') or '').strip()

    dir_exists = service_artifacts_dir.exists()
    live_rpc_polling_count = 0
    simulator_count = 0

    if dir_exists:
        for fname in ('summary.json', 'evidence.json', 'telemetry_events.json'):
            fpath = service_artifacts_dir / fname
            if not fpath.exists():
                continue
            try:
                data = json.loads(fpath.read_text())
            except Exception:
                continue
            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue
                src = str(item.get('evidence_source') or '').strip().lower()
                st = str(item.get('source_type') or '').strip().lower()
                if src == 'live' and st == 'rpc_polling':
                    live_rpc_polling_count += 1
                elif src in _SIMULATOR_SOURCES:
                    simulator_count += 1

    return {
        'STAGING_EVM_RPC_URL_present': staging_rpc_present,
        'EVM_RPC_URL_present': base_rpc_present,
        'STAGING_EVM_CHAIN_ID_present': staging_chain_present,
        'STAGING_WORKER_ENABLED': worker_raw if worker_raw else '(not set)',
        'live_artifact_dir_exists': dir_exists,
        'live_rpc_polling_artifacts_count': live_rpc_polling_count,
        'simulator_artifacts_count': simulator_count,
    }


def main() -> int:
    result = diagnose()
    for key, value in result.items():
        print(f'{key}={value}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
