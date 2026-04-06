#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text())


def _require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def validate_bundle(artifacts_dir: Path) -> list[str]:
    failures: list[str] = []
    required_files = ('summary.json', 'alerts.json', 'runs.json', 'incidents.json', 'evidence.json', 'report.md')
    for file_name in required_files:
        _require((artifacts_dir / file_name).exists(), f'missing required artifact: {file_name}', failures)
    if failures:
        return failures

    summary = _load_json(artifacts_dir / 'summary.json')
    alerts = _load_json(artifacts_dir / 'alerts.json')
    runs = _load_json(artifacts_dir / 'runs.json')
    incidents = _load_json(artifacts_dir / 'incidents.json')
    evidence = _load_json(artifacts_dir / 'evidence.json')
    report = (artifacts_dir / 'report.md').read_text().strip()

    _require(summary.get('status') != 'asset_configuration_incomplete', 'summary.status=asset_configuration_incomplete', failures)
    _require(bool(summary.get('worker_monitoring_executed')), 'summary.worker_monitoring_executed=false', failures)
    _require(bool(summary.get('anomalies_observed')), 'summary.anomalies_observed=false', failures)
    _require(isinstance(alerts, list) and len(alerts) > 0, 'alerts.json is empty', failures)
    _require(isinstance(runs, list) and len(runs) > 0, 'runs.json is empty', failures)
    _require(isinstance(incidents, list), 'incidents.json is not a list', failures)
    _require(bool(report), 'report.md is empty', failures)

    high_or_critical = [
        row for row in alerts
        if isinstance(row, dict) and str(row.get('severity') or '').lower() in {'high', 'critical'}
    ]
    if high_or_critical:
        _require(len(incidents) > 0, 'incidents.json is empty for high/critical alerts', failures)

    _require(isinstance(evidence, list) and len(evidence) > 0, 'evidence.json is empty', failures)
    evidence_rows = [row for row in evidence if isinstance(row, dict)]
    only_coverage = evidence_rows and all(str(row.get('record_type') or '') == 'coverage_evaluation' for row in evidence_rows)
    _require(not only_coverage, 'evidence.json contains only coverage_evaluation rows', failures)

    protected_asset_identity = summary.get('protected_asset_identity') if isinstance(summary.get('protected_asset_identity'), dict) else {}
    target_identity = summary.get('target_identity') if isinstance(summary.get('target_identity'), dict) else {}
    required_asset_fields = ('asset_id', 'asset_identifier', 'symbol', 'chain_id', 'contract_address')
    required_target_fields = ('target_id', 'target_name_or_label', 'target_type', 'target_locator')
    for field in required_asset_fields:
        value = protected_asset_identity.get(field)
        _require(value not in (None, '', [], {}), f'protected_asset_identity.{field} missing', failures)
    for field in required_target_fields:
        value = target_identity.get(field)
        _require(value not in (None, '', [], {}), f'target_identity.{field} missing', failures)

    has_tx_or_event_linked_row = False
    for row in evidence_rows:
        if row.get('record_type') == 'coverage_evaluation':
            continue
        if (row.get('tx_hash') and row.get('block_number') is not None) or row.get('event_id'):
            has_tx_or_event_linked_row = True
            break
    _require(has_tx_or_event_linked_row, 'no tx_hash/block_number or event_id linked anomaly evidence row found', failures)
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description='Validate Feature 1 live evidence artifacts for stale/placeholder content.')
    parser.add_argument(
        '--artifacts-dir',
        default='services/api/artifacts/live_evidence/latest',
        help='Directory containing summary.json/alerts.json/runs.json/incidents.json/evidence.json/report.md',
    )
    args = parser.parse_args()
    artifacts_dir = Path(args.artifacts_dir).resolve()
    failures = validate_bundle(artifacts_dir)
    if failures:
        print(json.dumps({'status': 'invalid', 'artifacts_dir': str(artifacts_dir), 'failures': failures}, indent=2))
        return 2
    print(json.dumps({'status': 'ok', 'artifacts_dir': str(artifacts_dir)}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
