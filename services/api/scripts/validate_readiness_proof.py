#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

REQUIRED_TRUE_FIELDS = (
    'live_successful_monitoring_demo',
    'telemetry_event_present',
    'detection_generated_from_telemetry',
    'alert_generated_from_detection',
    'incident_opened_from_alert',
    'response_action_recommended_or_executed',
    'evidence_package_exported',
    'onboarding_to_first_signal_complete',
    'production_validation_proof_bundle_complete',
)


def _truthy(value: object) -> bool:
    return value is True


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Validate readiness proof summary fields and guard against simulator/live confusion.'
    )
    parser.add_argument(
        '--summary-path',
        default='services/api/artifacts/live_evidence/latest/summary.json',
        help='Path to summary.json artifact produced by guided proof workflow.',
    )
    parser.add_argument(
        '--environment',
        default='staging',
        choices=('test', 'staging', 'production'),
        help='Validation environment. test/staging enforce stronger non-live safety guards.',
    )
    args = parser.parse_args()

    summary_path = Path(args.summary_path).resolve()
    summary = json.loads(summary_path.read_text())

    checks: list[tuple[str, bool, str]] = []

    for field in REQUIRED_TRUE_FIELDS:
        value = summary.get(field)
        ok = _truthy(value)
        checks.append((field, ok, f'expected true, got {value!r}'))

    telemetry_source = str(summary.get('telemetry_evidence_source') or '').strip().lower()
    source_ok = telemetry_source == 'live'
    checks.append(('telemetry_evidence_source', source_ok, f"expected 'live', got {telemetry_source!r}"))

    # Safety gate: non-production validation must never treat simulator/replay/none as live evidence.
    if args.environment in {'test', 'staging'}:
        non_simulator = telemetry_source not in {'', 'none', 'simulator', 'replay'}
        checks.append(
            ('non_live_environment_source_guard', non_simulator, f'environment={args.environment} cannot certify {telemetry_source!r} as live')
        )

    failures = [row for row in checks if not row[1]]
    print('Readiness proof check report:')
    for name, ok, detail in checks:
        state = 'PASS' if ok else 'FAIL'
        suffix = '' if ok else f' ({detail})'
        print(f'- {name}: {state}{suffix}')

    if failures:
        print(f'FAILED: {len(failures)} readiness checks failed for {summary_path}')
        return 2

    print(f'OK: {len(checks)} readiness checks passed for {summary_path}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
