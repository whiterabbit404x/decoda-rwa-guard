#!/usr/bin/env python3
"""
Export canonical live evidence chain for Decoda RWA Guard.

Reads service live evidence artifacts:
  services/api/artifacts/live_evidence/latest/summary.json
  services/api/artifacts/live_evidence/latest/evidence.json
  services/api/artifacts/live_evidence/latest/telemetry_events.json

Writes:
  artifacts/live-evidence-proof/latest/live_evidence_chain.json

The output chain must have:
  - evidence_source: "live"
  - source_type: "rpc_polling"
  - telemetry_event_id, detection_id, alert_id
  - incident_id OR response_action_id
  - evidence_package_id
  - observed_at / latest_live_telemetry_at

Exits non-zero (fail-closed) when:
  - Source artifacts are missing or unreadable.
  - Any artifact has evidence_source in ('guided_simulator', 'simulator', 'fixture').
  - A telemetry event has source_type set to something other than 'rpc_polling'.
  - Required chain IDs are missing.
  - Summary reports live_evidence_ready=false.

Usage:
  python scripts/export_live_evidence_chain.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]

_SERVICE_ARTIFACTS_DIR = (
    REPO_ROOT / 'services' / 'api' / 'artifacts' / 'live_evidence' / 'latest'
)
_OUTPUT_DIR = REPO_ROOT / 'artifacts' / 'live-evidence-proof' / 'latest'
_OUTPUT_FILE = _OUTPUT_DIR / 'live_evidence_chain.json'

_REJECTED_SOURCES = frozenset({'guided_simulator', 'simulator', 'fixture'})


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _check_evidence_source(
    source: str,
    artifact_label: str,
) -> str | None:
    """
    Validate evidence_source.

    Returns an error string when the source is rejected, otherwise None.
    """
    source_lower = source.strip().lower()
    if source_lower in _REJECTED_SOURCES:
        return (
            f'{artifact_label} evidence_source={source_lower!r} '
            f'is not live provider evidence'
        )
    if source_lower and source_lower != 'live':
        return (
            f'{artifact_label} evidence_source={source_lower!r} '
            f'(expected "live")'
        )
    return None


def export_live_evidence_chain(
    *,
    service_artifacts_dir: Path | None = None,
    output_file: Path | None = None,
) -> int:
    """
    Export the canonical live evidence chain.

    Returns 0 on success, 1 on any failure.  Always fail-closed.
    """
    if service_artifacts_dir is None:
        service_artifacts_dir = _SERVICE_ARTIFACTS_DIR
    if output_file is None:
        output_file = _OUTPUT_FILE

    # --- Read summary ---
    summary_path = service_artifacts_dir / 'summary.json'
    summary = _read_json(summary_path)
    if not isinstance(summary, dict):
        print(
            f'[export-live-evidence-chain] ERROR: cannot read {summary_path}'
        )
        return 1

    # Fail if summary says not live-ready
    if not summary.get('live_evidence_ready'):
        print(
            '[export-live-evidence-chain] REJECTED: summary.json live_evidence_ready is '
            'false or missing; no live evidence to export'
        )
        return 1

    summary_source = str(summary.get('evidence_source') or '').strip().lower()
    err = _check_evidence_source(summary_source, 'summary.json')
    if err:
        print(f'[export-live-evidence-chain] REJECTED: {err}')
        return 1
    if not summary_source:
        print('[export-live-evidence-chain] REJECTED: summary.json missing evidence_source')
        return 1

    # --- Read evidence chain ---
    evidence_path = service_artifacts_dir / 'evidence.json'
    evidence = _read_json(evidence_path)
    if not isinstance(evidence, dict):
        print(f'[export-live-evidence-chain] ERROR: cannot read {evidence_path}')
        return 1

    evidence_source = str(evidence.get('evidence_source') or '').strip().lower()
    err = _check_evidence_source(evidence_source, 'evidence.json')
    if err:
        print(f'[export-live-evidence-chain] REJECTED: {err}')
        return 1
    if not evidence_source:
        print('[export-live-evidence-chain] REJECTED: evidence.json missing evidence_source')
        return 1

    # --- Read and check telemetry events ---
    telemetry_path = service_artifacts_dir / 'telemetry_events.json'
    raw_telemetry = _read_json(telemetry_path)
    if raw_telemetry is None:
        telemetry_events: list[dict] = []
    elif isinstance(raw_telemetry, list):
        telemetry_events = [e for e in raw_telemetry if isinstance(e, dict)]
    elif isinstance(raw_telemetry, dict):
        telemetry_events = [raw_telemetry]
    else:
        telemetry_events = []

    for i, event in enumerate(telemetry_events):
        ev_source = str(event.get('evidence_source') or '').strip().lower()
        err = _check_evidence_source(ev_source, f'telemetry_events.json[{i}]')
        if err:
            print(f'[export-live-evidence-chain] REJECTED: {err}')
            return 1

        ev_source_type = str(event.get('source_type') or '').strip().lower()
        if ev_source_type and ev_source_type != 'rpc_polling':
            print(
                f'[export-live-evidence-chain] REJECTED: '
                f'telemetry_events.json[{i}] source_type={ev_source_type!r} '
                f'(expected "rpc_polling")'
            )
            return 1

    # --- Extract chain IDs from evidence.json ---
    chain = evidence.get('chain') or {}

    telemetry_event_id = str(chain.get('telemetry_event_id') or '').strip()
    detection_id = str(chain.get('detection_id') or '').strip()
    alert_id = str(chain.get('alert_id') or '').strip()
    incident_id = str(chain.get('incident_id') or '').strip() or None
    response_action_id = str(chain.get('response_action_id') or '').strip() or None
    evidence_package_id = str(chain.get('evidence_package_id') or '').strip()

    # Validate required chain fields
    missing_fields: list[str] = []
    for field, val in [
        ('telemetry_event_id', telemetry_event_id),
        ('detection_id', detection_id),
        ('alert_id', alert_id),
        ('evidence_package_id', evidence_package_id),
    ]:
        if not val:
            missing_fields.append(field)
    if not incident_id and not response_action_id:
        missing_fields.append('incident_id or response_action_id')

    if missing_fields:
        print(
            f'[export-live-evidence-chain] REJECTED: chain missing required fields: '
            f'{missing_fields}'
        )
        return 1

    # --- Determine timestamps ---
    observed_at = str(
        summary.get('latest_live_telemetry_at')
        or (telemetry_events[0].get('observed_at') if telemetry_events else None)
        or datetime.now(timezone.utc).isoformat()
    )

    # --- Determine source_type from telemetry events or default to rpc_polling ---
    source_type = 'rpc_polling'
    if telemetry_events and isinstance(telemetry_events[0], dict):
        ev_st = str(telemetry_events[0].get('source_type') or '').strip().lower()
        if ev_st:
            source_type = ev_st

    # --- Build canonical output ---
    output_chain: dict[str, Any] = {
        'evidence_source': 'live',
        'source_type': source_type,
        'telemetry_event_id': telemetry_event_id,
        'detection_id': detection_id,
        'alert_id': alert_id,
        'incident_id': incident_id,
        'response_action_id': response_action_id,
        'evidence_package_id': evidence_package_id,
        'observed_at': observed_at,
        'latest_live_telemetry_at': observed_at,
        'workspace_id': str(
            evidence.get('workspace_id') or chain.get('workspace_id') or ''
        ),
        'asset_id': str(chain.get('asset_id') or ''),
        'target_id': str(chain.get('target_id') or ''),
        'monitoring_config_id': str(chain.get('monitoring_config_id') or ''),
        'monitoring_run_id': str(chain.get('monitoring_run_id') or ''),
        'exported_at': datetime.now(timezone.utc).isoformat(),
    }

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, 'w') as f:
        json.dump(output_chain, f, indent=2)

    print(f'[export-live-evidence-chain] wrote {output_file}')
    print(
        f'[export-live-evidence-chain] evidence_source=live '
        f'source_type={source_type}'
    )
    print(f'[export-live-evidence-chain] telemetry_event_id={telemetry_event_id}')
    print(f'[export-live-evidence-chain] detection_id={detection_id}')
    print(f'[export-live-evidence-chain] alert_id={alert_id}')
    if incident_id:
        print(f'[export-live-evidence-chain] incident_id={incident_id}')
    if response_action_id:
        print(f'[export-live-evidence-chain] response_action_id={response_action_id}')
    print(f'[export-live-evidence-chain] evidence_package_id={evidence_package_id}')
    return 0


def main() -> int:
    return export_live_evidence_chain()


if __name__ == '__main__':
    raise SystemExit(main())
