#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

REQUIRED_SUMMARY_PRESENCE_FIELDS = (
    'live_successful_monitoring_demo',
    'simulator_successful_monitoring_demo',
    'telemetry_event_present',
    'detection_generated_from_telemetry',
    'alert_generated_from_detection',
    'incident_opened_from_alert',
    'response_action_recommended_or_executed',
    'evidence_package_exported',
    'billing_email_provider_checks_passing',
    'onboarding_to_first_signal_complete',
    'production_validation_proof_bundle_complete',
    'controlled_pilot_ready',
    'enterprise_procurement_ready',
    'broad_self_serve_ready',
    'broad_self_serve_blocked_reason',
    'telemetry_evidence_source',
)

REQUIRED_SUMMARY_BOOLEAN_FIELDS = (
    'live_successful_monitoring_demo',
    'simulator_successful_monitoring_demo',
    'telemetry_event_present',
    'detection_generated_from_telemetry',
    'alert_generated_from_detection',
    'incident_opened_from_alert',
    'response_action_recommended_or_executed',
    'evidence_package_exported',
    'billing_email_provider_checks_passing',
    'onboarding_to_first_signal_complete',
    'production_validation_proof_bundle_complete',
    'controlled_pilot_ready',
    'enterprise_procurement_ready',
    'broad_self_serve_ready',
)

CHAIN_ARTIFACTS = {
    'telemetry_events': 'telemetry_events.json',
    'detections': 'detections.json',
    'alerts': 'alerts.json',
    'incidents': 'incidents.json',
    'response_actions': 'response_actions.json',
    'runs': 'runs.json',
}

REQUIRED_EVIDENCE_CHAIN_FIELDS = (
    'asset_id',
    'target_id',
    'monitoring_config_id',
    'monitoring_run_id',
    'telemetry_event_id',
    'detection_id',
    'alert_id',
    'incident_id',
    'response_action_id',
    'evidence_package_id',
)

REQUIRED_EVIDENCE_ASSERTION_FIELDS = (
    'simulator_successful_monitoring_demo',
    'telemetry_event_present',
    'detection_generated_from_telemetry',
    'alert_generated_from_detection',
    'incident_opened_from_alert',
    'response_action_recommended_or_executed',
    'evidence_package_exported',
    'onboarding_to_first_signal_complete',
)


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


def _contains_simulator_label(payload: Any) -> bool:
    if isinstance(payload, dict):
        return any(_contains_simulator_label(value) for value in payload.values())
    if isinstance(payload, list):
        return any(_contains_simulator_label(value) for value in payload)
    if isinstance(payload, str):
        value = payload.strip().lower()
        return 'simulator' in value or 'guided' in value
    return False


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

    for field in REQUIRED_SUMMARY_PRESENCE_FIELDS:
        exists = field in summary
        checks.append((f'summary_has_{field}', exists, 'missing required summary field'))

    for field in REQUIRED_SUMMARY_BOOLEAN_FIELDS:
        value = summary.get(field)
        ok = isinstance(value, bool)
        checks.append((f'{field}_boolean', ok, f'expected boolean, got {value!r}'))

    telemetry_source = str(summary.get('telemetry_evidence_source') or '').strip().lower()


    # Mode-specific summary truth constraints
    controlled_pilot_mode = telemetry_source == 'guided_simulator'
    if controlled_pilot_mode:
        checks.append((
            'controlled_pilot_ready_true_for_guided_simulator',
            summary.get('controlled_pilot_ready') is True,
            'controlled pilot mode requires controlled_pilot_ready=true',
        ))
        checks.append((
            'simulator_successful_monitoring_demo_true_for_guided_simulator',
            summary.get('simulator_successful_monitoring_demo') is True,
            'controlled pilot mode requires simulator_successful_monitoring_demo=true',
        ))
        checks.append((
            'telemetry_evidence_source_guided_simulator_for_controlled_pilot',
            telemetry_source == 'guided_simulator',
            'controlled pilot mode requires telemetry_evidence_source=guided_simulator',
        ))

    billing_checks_passing = summary.get('billing_email_provider_checks_passing') is True
    broad_self_serve_ready = summary.get('broad_self_serve_ready') is True
    checks.append((
        'broad_self_serve_false_when_billing_checks_fail',
        billing_checks_passing or not broad_self_serve_ready,
        'broad_self_serve_ready must be false when billing_email_provider_checks_passing is false',
    ))

    enterprise_prereqs_satisfied = (
        telemetry_source == 'live'
        and summary.get('live_successful_monitoring_demo') is True
        and summary.get('billing_email_provider_checks_passing') is True
        and summary.get('onboarding_to_first_signal_complete') is True
        and summary.get('production_validation_proof_bundle_complete') is True
    )
    checks.append((
        'enterprise_procurement_requires_live_staging_provider_prereqs',
        (summary.get('enterprise_procurement_ready') is False) or enterprise_prereqs_satisfied,
        'enterprise_procurement_ready must be false unless live/staging/provider prerequisites are satisfied',
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

        file_size = artifact_path.stat().st_size
        checks.append((f'{filename}_not_empty_file', file_size > 0, f'artifact file is empty: {artifact_path}'))
        if file_size == 0:
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
    evidence_payload: dict[str, Any] | None = None
    if evidence_path.exists():
        payload = _load_json(evidence_path)
        if isinstance(payload, dict):
            evidence_payload = payload
    checks.append((
        'evidence_payload_object',
        isinstance(evidence_payload, dict),
        'evidence.json must be an object payload',
    ))

    workspace_id = evidence_payload.get('workspace_id') if evidence_payload else None
    checks.append((
        'evidence_workspace_id_present',
        workspace_id not in (None, ''),
        'evidence.json must include workspace_id',
    ))
    evidence_source = evidence_payload.get('evidence_source') if evidence_payload else None
    checks.append((
        'evidence_source_present',
        evidence_source not in (None, ''),
        'evidence.json must include evidence_source',
    ))

    chain = evidence_payload.get('chain') if evidence_payload else None
    checks.append((
        'evidence_chain_object_present',
        isinstance(chain, dict),
        'evidence.json must include chain object',
    ))
    chain = chain if isinstance(chain, dict) else {}

    chain_fields_present = True
    for field in REQUIRED_EVIDENCE_CHAIN_FIELDS:
        exists = chain.get(field) not in (None, '')
        checks.append((f'evidence_chain_has_{field}', exists, f'evidence.chain.{field} is required'))
        chain_fields_present = chain_fields_present and exists

    assertions = evidence_payload.get('assertions') if evidence_payload else None
    checks.append((
        'evidence_assertions_object_present',
        isinstance(assertions, dict),
        'evidence.json must include assertions object',
    ))
    assertions = assertions if isinstance(assertions, dict) else {}

    for field in REQUIRED_EVIDENCE_ASSERTION_FIELDS:
        exists = field in assertions
        checks.append((f'evidence_assertion_{field}_present', exists, f'evidence.assertions.{field} is required'))

        if controlled_pilot_mode:
            value = assertions.get(field)
            checks.append((
                f'evidence_assertion_{field}_true_for_controlled_pilot',
                value is True,
                f'evidence.assertions.{field} must be boolean true for controlled pilot validation',
            ))

    evidence_refs = _reference_values(
        chain,
        'telemetry_event_id',
        'detection_id',
        'alert_id',
        'incident_id',
        'response_action_id',
    )
    evidence_has_chain_ids = chain_fields_present and bool(evidence_refs)
    checks.append((
        'evidence_contains_chain_ids',
        evidence_has_chain_ids,
        'evidence.json must include telemetry/detection/alert/incident/response identifiers',
    ))

    required_chain_ids = telemetry_ids | detection_ids | alert_ids | incident_ids | response_action_ids
    checks.append((
        'evidence_package_references_chain',
        bool(required_chain_ids) and required_chain_ids.issubset(evidence_refs),
        'evidence package must reference every id in telemetry→detection→alert→incident→response chain',
    ))
    linked_chain_complete = bool(required_chain_ids) and required_chain_ids.issubset(evidence_refs)

    # Strict anti-mislabeling: simulator-origin data cannot claim live provenance.
    chain_complete = (
        full_chain_loaded
        and bool(telemetry_ids)
        and bool(detection_ids)
        and bool(alert_ids)
        and bool(incident_ids)
        and bool(response_action_ids)
    )

    guided_simulator_pilot_ok = (
        telemetry_source == 'guided_simulator'
        and chain_complete
        and bool(required_chain_ids)
        and required_chain_ids.issubset(evidence_refs)
    )
    checks.append((
        'guided_simulator_controlled_pilot_allowed',
        guided_simulator_pilot_ok or telemetry_source == 'live',
        'controlled pilot pass requires guided_simulator sources plus complete chain',
    ))

    simulator_chain_marked_live = telemetry_source == 'live' and (
        _contains_simulator_label(evidence_payload)
        or any(_contains_simulator_label(records) for records in artifacts.values())
    )
    checks.append((
        'live_source_not_simulator_generated',
        not simulator_chain_marked_live,
        'guided/simulator generated chain cannot be labeled live',
    ))

    blocking_reasons = [str(reason).lower() for reason in summary.get('claim_ineligibility_reasons') or [] if reason]
    readiness_gate_ok = not broad_self_serve_ready or billing_checks_passing
    reasons_block_self_serve = any(
        'billing' in reason or 'email' in reason or 'provider' in reason
        for reason in blocking_reasons
    )
    readiness_reasons_consistent = not broad_self_serve_ready or not reasons_block_self_serve
    checks.append((
        'self_serve_readiness_gate',
        readiness_gate_ok,
        'broad_self_serve_ready cannot be true when billing/email/provider checks are false',
    ))
    checks.append((
        'self_serve_blocking_reasons_consistency',
        readiness_reasons_consistent,
        'broad self-serve readiness cannot be true when claim_ineligibility_reasons include billing/email/provider blockers',
    ))

    production_bundle_complete = summary.get('production_validation_proof_bundle_complete') is True
    checks.append((
        'production_bundle_requires_complete_linked_chain',
        (not production_bundle_complete) or (chain_complete and linked_chain_complete),
        'production_validation_proof_bundle_complete=true requires complete linked telemetry→detection→alert→incident→response chain',
    ))

    checks.append((
        'controlled_pilot_allows_self_serve_blocked',
        not (
            telemetry_source == 'guided_simulator'
            and summary.get('controlled_pilot_ready') is True
            and chain_complete
            and linked_chain_complete
        )
        or (summary.get('broad_self_serve_ready') is not True),
        'guided simulator controlled pilot should keep broad self-serve blocked until readiness gates pass',
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
