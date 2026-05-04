#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

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

REQUIRED_SUMMARY_FIELDS = REQUIRED_TRUE_FIELDS + (
    'simulator_successful_monitoring_demo',
    'billing_email_provider_checks_passing',
    'broad_self_serve_blocked_reason',
    'enterprise_claim_eligibility',
    'evidence_source',
    'telemetry_evidence_source',
)

CHAIN_ARTIFACTS = {
    'telemetry_events': 'telemetry_events.json',
    'detections': 'detections.json',
    'alerts': 'alerts.json',
    'incidents': 'incidents.json',
    'response_actions': 'response_actions.json',
    'runs': 'runs.json',
}


def _truthy(value: object) -> bool:
    return value is True


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text())


def _id_set(records: Any) -> set[str]:
    ids: set[str] = set()
    if isinstance(records, list):
        for record in records:
            if isinstance(record, dict):
                value = record.get('id')
                if value not in (None, ''):
                    ids.add(str(value))
    return ids


def _reference_values(record: dict[str, Any], *keys: str) -> set[str]:
    values: set[str] = set()
    for key in keys:
        raw = record.get(key)
        if isinstance(raw, list):
            for item in raw:
                if item not in (None, ''):
                    values.add(str(item))
        elif raw not in (None, ''):
            values.add(str(raw))
    return values


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
    parser.add_argument(
        '--allow-empty-runs-with-justification',
        action='store_true',
        help='Allow empty runs.json only for documented exception handling.',
    )
    args = parser.parse_args()

    summary_path = Path(args.summary_path).resolve()
    artifacts_dir = summary_path.parent
    summary = _load_json(summary_path)

    checks: list[tuple[str, bool, str]] = []

    for field in REQUIRED_SUMMARY_FIELDS:
        exists = field in summary
        checks.append((f'summary_has_{field}', exists, 'missing required summary field'))

    for field in REQUIRED_TRUE_FIELDS:
        value = summary.get(field)
        ok = _truthy(value)
        checks.append((field, ok, f'expected true, got {value!r}'))

    evidence_source = str(summary.get('evidence_source') or '').strip().lower()
    telemetry_source = str(summary.get('telemetry_evidence_source') or '').strip().lower()

    source_consistency = evidence_source == telemetry_source
    checks.append((
        'evidence_source_consistency',
        source_consistency,
        f"expected evidence_source to match telemetry_evidence_source, got {evidence_source!r} vs {telemetry_source!r}",
    ))

    full_chain_loaded = True
    artifacts: dict[str, list[dict[str, Any]]] = {}
    for key, filename in CHAIN_ARTIFACTS.items():
        artifact_path = artifacts_dir / filename
        exists = artifact_path.exists()
        checks.append((f'{filename}_exists', exists, f'missing required artifact {artifact_path}'))
        if not exists:
            full_chain_loaded = False
            continue

        payload = _load_json(artifact_path)
        is_list = isinstance(payload, list)
        checks.append((f'{filename}_list', is_list, f'expected list payload, got {type(payload).__name__}'))
        if not is_list:
            full_chain_loaded = False
            continue

        allow_empty = key == 'runs' and args.allow_empty_runs_with_justification
        non_empty = len(payload) > 0 or allow_empty
        checks.append((
            f'{filename}_non_empty',
            non_empty,
            'empty artifact payload is not permitted without explicit runs justification mode',
        ))
        if len(payload) == 0 and not allow_empty:
            full_chain_loaded = False

        parsed: list[dict[str, Any]] = [item for item in payload if isinstance(item, dict)]
        if len(parsed) != len(payload):
            checks.append((f'{filename}_record_shape', False, 'all records must be objects'))
            full_chain_loaded = False
        artifacts[key] = parsed

    telemetry_ids = _id_set(artifacts.get('telemetry_events', []))
    detection_ids = _id_set(artifacts.get('detections', []))
    alert_ids = _id_set(artifacts.get('alerts', []))
    incident_ids = _id_set(artifacts.get('incidents', []))
    response_action_ids = _id_set(artifacts.get('response_actions', []))

    detection_refs = set().union(*[
        _reference_values(d, 'telemetry_event_id', 'telemetry_event_ids', 'telemetry_id', 'event_id')
        for d in artifacts.get('detections', [])
    ]) if artifacts.get('detections') else set()
    checks.append((
        'detection_references_telemetry',
        bool(detection_refs) and detection_refs.issubset(telemetry_ids),
        'detections must reference existing telemetry event ids',
    ))

    alert_refs = set().union(*[
        _reference_values(a, 'detection_id', 'detection_ids')
        for a in artifacts.get('alerts', [])
    ]) if artifacts.get('alerts') else set()
    checks.append((
        'alert_references_detection',
        bool(alert_refs) and alert_refs.issubset(detection_ids),
        'alerts must reference existing detection ids',
    ))

    incident_refs = set().union(*[
        _reference_values(i, 'alert_id', 'alert_ids')
        for i in artifacts.get('incidents', [])
    ]) if artifacts.get('incidents') else set()
    checks.append((
        'incident_references_alert',
        bool(incident_refs) and incident_refs.issubset(alert_ids),
        'incidents must reference existing alert ids',
    ))

    response_refs = set().union(*[
        _reference_values(r, 'incident_id', 'incident_ids')
        for r in artifacts.get('response_actions', [])
    ]) if artifacts.get('response_actions') else set()
    checks.append((
        'response_action_references_incident',
        bool(response_refs) and response_refs.issubset(incident_ids),
        'response actions must reference existing incident ids',
    ))

    evidence_path = artifacts_dir / 'evidence.json'
    evidence_records: list[dict[str, Any]] = []
    if evidence_path.exists():
        payload = _load_json(evidence_path)
        if isinstance(payload, list):
            evidence_records = [item for item in payload if isinstance(item, dict)]
        elif isinstance(payload, dict):
            evidence_records = [payload]

    evidence_refs = set().union(*[
        _reference_values(
            row,
            'telemetry_event_id',
            'telemetry_event_ids',
            'detection_id',
            'detection_ids',
            'alert_id',
            'alert_ids',
            'incident_id',
            'incident_ids',
            'response_action_id',
            'response_action_ids',
        )
        for row in evidence_records
    ]) if evidence_records else set()
    required_chain_ids = telemetry_ids | detection_ids | alert_ids | incident_ids | response_action_ids
    checks.append((
        'evidence_package_references_chain',
        bool(required_chain_ids) and required_chain_ids.issubset(evidence_refs),
        'evidence package must reference every id in telemetry→detection→alert→incident→response chain',
    ))

    # Strict anti-mislabeling: simulator-origin data cannot claim live provenance.
    mislabeled_live = telemetry_source == 'live' and evidence_source == 'live' and not full_chain_loaded
    checks.append((
        'simulator_data_never_mislabeled_live',
        not mislabeled_live,
        'live-labeled evidence is invalid when required live chain artifacts are missing or empty',
    ))

    guided_simulator_pilot_ok = (
        evidence_source == 'guided_simulator'
        and telemetry_source == 'guided_simulator'
        and full_chain_loaded
        and bool(telemetry_ids)
        and bool(detection_ids)
        and bool(alert_ids)
        and bool(incident_ids)
        and bool(response_action_ids)
    )
    checks.append((
        'guided_simulator_controlled_pilot_allowed',
        guided_simulator_pilot_ok or evidence_source == 'live',
        'controlled pilot pass requires guided_simulator sources plus complete chain',
    ))

    blocking_reasons = [str(reason).lower() for reason in summary.get('claim_ineligibility_reasons') or [] if reason]
    readiness_gate_ok = not any(
        'billing' in reason or 'email' in reason or 'provider' in reason
        for reason in blocking_reasons
    )
    checks.append((
        'self_serve_readiness_gate',
        readiness_gate_ok,
        'broad self-serve readiness is rejected when billing/email/provider checks fail',
    ))

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
