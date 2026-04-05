#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import urllib.error
import urllib.request

ALLOWED_STATUSES = {
    'live_coverage_confirmed',
    'live_coverage_denied',
    'monitoring_execution_failed',
    'asset_configuration_incomplete',
    'dry_run_requested',
}

REQUIRED_TARGET_FIELDS = (
    'target_id',
    'target_name_or_label',
    'target_type',
    'target_locator',
)


def _request_json(url: str, *, method: str = 'GET', token: str = '', workspace_id: str = '', payload: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    headers = {'Accept': 'application/json'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    if workspace_id:
        headers['x-workspace-id'] = workspace_id
    body = None
    if payload is not None:
        headers['Content-Type'] = 'application/json'
        body = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, method=method, headers=headers, data=body)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode('utf-8') or '{}')
            return resp.status, data
    except urllib.error.HTTPError as exc:
        text = exc.read().decode('utf-8', errors='ignore')
        try:
            return exc.code, json.loads(text or '{}')
        except Exception:
            return exc.code, {'error': text}
    except urllib.error.URLError as exc:
        return 503, {'error': str(exc.reason), 'code': 'connection_unavailable'}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pick_target(targets: list[dict[str, Any]], target_id: str) -> dict[str, Any] | None:
    return next((item for item in targets if (not target_id or str(item.get('id')) == target_id) and item.get('asset_id')), None)


def _target_identity(target: dict[str, Any] | None) -> dict[str, Any]:
    target = target or {}
    return {
        'target_id': target.get('id'),
        'target_name_or_label': target.get('name') or target.get('asset_label') or target.get('target_label'),
        'target_type': target.get('target_type'),
        'chain_network': target.get('chain_network'),
        'target_locator': target.get('wallet_address') or target.get('contract_identifier') or target.get('name'),
    }


def _missing_target_fields(target_identity: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for key in REQUIRED_TARGET_FIELDS:
        value = target_identity.get(key)
        if value is None:
            missing.append(key)
        elif isinstance(value, str) and not value.strip():
            missing.append(key)
    return missing


def _default_asset_payload() -> dict[str, Any]:
    asset_identifier = os.getenv('FEATURE1_PROOF_ASSET_IDENTIFIER', 'USTB-REAL')
    return {
        'name': f'Feature1 Protected Asset {asset_identifier}',
        'asset_symbol': os.getenv('FEATURE1_PROOF_ASSET_SYMBOL', 'USTB'),
        'asset_identifier': asset_identifier,
        'chain_network': os.getenv('FEATURE1_PROOF_CHAIN_NETWORK', 'ethereum'),
        'token_contract_address': os.getenv('FEATURE1_PROOF_ASSET_CONTRACT', '0x' + 'a' * 40),
        'treasury_ops_wallets': [os.getenv('FEATURE1_PROOF_TREASURY_WALLET', '0x1111111111111111111111111111111111111111')],
        'custody_wallets': [os.getenv('FEATURE1_PROOF_CUSTODY_WALLET', '0x2222222222222222222222222222222222222222')],
        'expected_counterparties': [os.getenv('FEATURE1_PROOF_COUNTERPARTY', '0x3333333333333333333333333333333333333333')],
        'expected_flow_patterns': [{'source_class': 'treasury_ops', 'destination_class': 'custody'}],
        'expected_approval_patterns': {'allowed_spenders': [os.getenv('FEATURE1_PROOF_SPENDER', '0x4444444444444444444444444444444444444444')]},
        'venue_labels': [os.getenv('FEATURE1_PROOF_VENUE', 'venue-a')],
        'expected_liquidity_baseline': {'minimum_transfer_count': 1},
        'oracle_sources': [os.getenv('FEATURE1_PROOF_ORACLE_SOURCE', 'oracle-a')],
        'expected_oracle_freshness_seconds': int(os.getenv('FEATURE1_PROOF_ORACLE_FRESHNESS_SECONDS', '120')),
        'expected_oracle_update_cadence_seconds': int(os.getenv('FEATURE1_PROOF_ORACLE_CADENCE_SECONDS', '120')),
        'baseline_status': 'ready',
        'baseline_confidence': 0.9,
        'baseline_coverage': 0.9,
    }


def _resolve_or_create_target(*, api_url: str, token: str, workspace_id: str, target_id: str) -> tuple[int, dict[str, Any] | None]:
    status, targets_payload = _request_json(f"{api_url.rstrip('/')}/targets", token=token, workspace_id=workspace_id)
    targets = targets_payload.get('targets') if isinstance(targets_payload.get('targets'), list) else []
    selected = _pick_target(targets, target_id)
    if status == 200 and selected is not None:
        return status, selected

    asset_payload = _default_asset_payload()
    asset_status, asset = _request_json(
        f"{api_url.rstrip('/')}/assets",
        method='POST',
        token=token,
        workspace_id=workspace_id,
        payload=asset_payload,
    )
    if asset_status >= 400 or not isinstance(asset, dict):
        return max(status, asset_status), None

    target_payload = {
        'name': os.getenv('FEATURE1_PROOF_TARGET_NAME', 'feature1-proof-target'),
        'target_type': os.getenv('FEATURE1_PROOF_TARGET_TYPE', 'wallet'),
        'chain_network': asset_payload.get('chain_network', 'ethereum'),
        'wallet_address': os.getenv('FEATURE1_PROOF_TREASURY_WALLET', '0x1111111111111111111111111111111111111111'),
        'monitoring_enabled': True,
        'monitoring_mode': 'stream',
        'chain_id': 1,
        'asset_label': 'Protected treasury monitoring target',
        'asset_id': asset.get('id'),
        'enabled': True,
        'auto_create_incidents': True,
        'severity_threshold': 'high',
    }
    target_status, created_target = _request_json(
        f"{api_url.rstrip('/')}/targets",
        method='POST',
        token=token,
        workspace_id=workspace_id,
        payload=target_payload,
    )
    if target_status >= 400 or not isinstance(created_target, dict):
        return target_status, None
    return target_status, created_target


def _missing_asset_fields(context: dict[str, Any]) -> list[str]:
    required = [
        'asset_id',
        'asset_identifier',
        'symbol',
        'chain_id',
        'contract_address',
        'treasury_ops_wallets',
        'custody_wallets',
        'expected_flow_patterns',
        'expected_counterparties',
        'expected_approval_patterns',
        'venue_labels',
        'expected_liquidity_baseline',
        'oracle_sources',
        'expected_oracle_freshness_seconds',
        'expected_oracle_update_cadence_seconds',
    ]
    missing: list[str] = []
    for key in required:
        value = context.get(key)
        if value is None:
            missing.append(key)
        elif isinstance(value, str) and not value.strip():
            missing.append(key)
        elif isinstance(value, (list, dict)) and len(value) == 0:
            missing.append(key)
    return missing


def _write_artifacts(*, artifacts_dir: Path, summary: dict[str, Any], alerts: list[dict[str, Any]], incidents: list[dict[str, Any]], runs: list[dict[str, Any]], evidence: list[dict[str, Any]]) -> None:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    (artifacts_dir / 'summary.json').write_text(json.dumps(summary, indent=2))
    (artifacts_dir / 'alerts.json').write_text(json.dumps(alerts, indent=2, default=str))
    (artifacts_dir / 'incidents.json').write_text(json.dumps(incidents, indent=2, default=str))
    (artifacts_dir / 'runs.json').write_text(json.dumps(runs, indent=2, default=str))
    (artifacts_dir / 'evidence.json').write_text(json.dumps(evidence, indent=2, default=str))
    (artifacts_dir / 'report.md').write_text(
        '# Feature1 Real Asset Evidence\n\n'
        f"- status: `{summary['status']}`\n"
        f"- enterprise_claim_eligibility: `{summary.get('enterprise_claim_eligibility')}`\n"
        f"- market_coverage_status: `{summary.get('market_coverage_status')}`\n"
        f"- oracle_coverage_status: `{summary.get('oracle_coverage_status')}`\n"
        f"- claim_ineligibility_reasons: `{summary.get('claim_ineligibility_reasons')}`\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description='Run Feature 1 real-asset evidence flow.')
    parser.add_argument('--api-url', default=os.getenv('FEATURE1_API_URL', 'http://127.0.0.1:8000'))
    parser.add_argument('--token', default=os.getenv('FEATURE1_API_TOKEN', ''))
    parser.add_argument('--workspace-id', default=os.getenv('FEATURE1_WORKSPACE_ID', ''))
    parser.add_argument('--target-id', default=os.getenv('FEATURE1_TARGET_ID', ''))
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    artifacts_dir = Path(os.getenv('FEATURE1_EVIDENCE_DIR', 'services/api/artifacts/live_evidence/latest')).resolve()

    if args.dry_run:
        summary = {
            'generated_at': _now_iso(),
            'status': 'dry_run_requested',
            'reason': 'dry_run_requested',
            'enterprise_claim_eligibility': False,
            'market_claim_eligible': False,
            'oracle_claim_eligible': False,
            'claim_ineligibility_reasons': ['dry_run_requested'],
        }
        _write_artifacts(artifacts_dir=artifacts_dir, summary=summary, alerts=[], incidents=[], runs=[], evidence=[{'record_type': 'dry_run_requested'}])
        print(json.dumps({'summary': summary, 'artifacts_dir': str(artifacts_dir)}, indent=2))
        return 0

    status, runtime = _request_json(f"{args.api_url.rstrip('/')}/ops/monitoring/runtime-status", token=args.token, workspace_id=args.workspace_id)
    if status != 200:
        summary = {
            'generated_at': _now_iso(),
            'status': 'monitoring_execution_failed',
            'reason': 'runtime_unavailable',
            'http_status': status,
            'enterprise_claim_eligibility': False,
            'market_claim_eligible': False,
            'oracle_claim_eligible': False,
            'claim_ineligibility_reasons': ['runtime_unavailable'],
        }
        _write_artifacts(artifacts_dir=artifacts_dir, summary=summary, alerts=[], incidents=[], runs=[], evidence=[{'record_type': 'monitoring_execution_failure', 'details': runtime}])
        print(json.dumps({'summary': summary, 'runtime': runtime, 'artifacts_dir': str(artifacts_dir)}, indent=2))
        return 2

    mode = str(runtime.get('configured_mode') or runtime.get('mode') or '').upper()
    if mode not in {'LIVE', 'HYBRID'}:
        summary = {
            'generated_at': _now_iso(),
            'status': 'live_coverage_denied',
            'reason': 'mode_not_live_or_hybrid',
            'configured_mode': mode,
            'enterprise_claim_eligibility': False,
            'market_claim_eligible': False,
            'oracle_claim_eligible': False,
            'claim_ineligibility_reasons': ['mode_not_live_or_hybrid'],
        }
        _write_artifacts(artifacts_dir=artifacts_dir, summary=summary, alerts=[], incidents=[], runs=[], evidence=[{'record_type': 'coverage_denial', 'details': summary}])
        print(json.dumps({'summary': summary, 'artifacts_dir': str(artifacts_dir)}, indent=2))
        return 2

    target_status, target = _resolve_or_create_target(
        api_url=args.api_url,
        token=args.token,
        workspace_id=args.workspace_id,
        target_id=args.target_id,
    )
    target_identity = _target_identity(target)
    missing_target_fields = _missing_target_fields(target_identity)
    if target is None:
        summary = {
            'generated_at': _now_iso(),
            'status': 'asset_configuration_incomplete',
            'reason': 'no_monitored_target_with_asset_profile_found',
            'enterprise_claim_eligibility': False,
            'market_claim_eligible': False,
            'oracle_claim_eligible': False,
            'claim_ineligibility_reasons': ['missing_target_or_asset_profile', *[f'missing_{field}' for field in REQUIRED_TARGET_FIELDS]],
            'target_identity': target_identity,
            'missing_target_identity_fields': list(REQUIRED_TARGET_FIELDS),
            'target_resolution_http_status': target_status,
        }
        _write_artifacts(
            artifacts_dir=artifacts_dir,
            summary=summary,
            alerts=[],
            incidents=[],
            runs=[],
            evidence=[{
                'record_type': 'asset_configuration',
                'status': 'asset_configuration_incomplete',
                'reason': 'no_monitored_target_with_asset_profile_found',
                'target_identity': target_identity,
                'missing_target_identity_fields': list(REQUIRED_TARGET_FIELDS),
                'missing_requirements': ['target_id', 'asset_id', *[f'target_identity.{field}' for field in REQUIRED_TARGET_FIELDS]],
                'enterprise_claim_eligibility': False,
            }],
        )
        print(json.dumps({'summary': summary, 'artifacts_dir': str(artifacts_dir)}, indent=2))
        return 2

    run_status, run_payload = _request_json(
        f"{args.api_url.rstrip('/')}/ops/monitoring/run",
        method='POST',
        token=args.token,
        workspace_id=args.workspace_id,
        payload={'worker_name': 'feature1-proof-worker', 'limit': 100},
    )

    _, alerts_payload = _request_json(f"{args.api_url.rstrip('/')}/alerts?target_id={target['id']}", token=args.token, workspace_id=args.workspace_id)
    _, incidents_payload = _request_json(f"{args.api_url.rstrip('/')}/incidents?target_id={target['id']}", token=args.token, workspace_id=args.workspace_id)
    _, runs_payload = _request_json(f"{args.api_url.rstrip('/')}/pilot/history?kind=analysis_runs", token=args.token, workspace_id=args.workspace_id)

    alerts = alerts_payload.get('alerts') if isinstance(alerts_payload.get('alerts'), list) else []
    incidents = incidents_payload.get('incidents') if isinstance(incidents_payload.get('incidents'), list) else []
    runs = runs_payload.get('analysis_runs') if isinstance(runs_payload.get('analysis_runs'), list) else []

    worker_runs = [
        item for item in runs
        if str(((item.get('response_payload') or {}).get('monitoring_path') or 'worker')).lower() == 'worker'
        and str(item.get('target_id') or '') == str(target.get('id') or '')
        and 'manual_run_once' not in json.dumps(item).lower()
    ]
    worker_monitoring_executed = bool(worker_runs)

    first_payload = (worker_runs[0].get('response_payload') if worker_runs else {}) if isinstance(worker_runs[0] if worker_runs else {}, dict) else {}
    coverage_record = (first_payload.get('protected_asset_coverage_record') if isinstance(first_payload.get('protected_asset_coverage_record'), dict) else {}) if isinstance(first_payload, dict) else {}
    protected_asset_context = (coverage_record.get('protected_asset_context') if isinstance(coverage_record.get('protected_asset_context'), dict) else {})

    if not protected_asset_context:
        protected_asset_context = {
            'asset_id': target.get('asset_id'),
            'asset_identifier': target.get('asset_identifier'),
            'symbol': target.get('asset_symbol'),
            'chain_id': target.get('chain_id'),
            'contract_address': target.get('contract_identifier'),
            'treasury_ops_wallets': target.get('treasury_ops_wallets') or [],
            'custody_wallets': target.get('custody_wallets') or [],
            'expected_flow_patterns': [],
            'expected_counterparties': [],
            'expected_approval_patterns': {},
            'venue_labels': target.get('venue_labels') or [],
            'expected_liquidity_baseline': {},
            'oracle_sources': [],
            'expected_oracle_freshness_seconds': target.get('expected_oracle_freshness_seconds'),
            'expected_oracle_update_cadence_seconds': target.get('expected_oracle_update_cadence_seconds'),
        }

    missing_context_fields = _missing_asset_fields(protected_asset_context)

    market_coverage_status = str(coverage_record.get('market_coverage_status') or first_payload.get('market_coverage_status') or 'insufficient_real_evidence')
    oracle_coverage_status = str(coverage_record.get('oracle_coverage_status') or first_payload.get('oracle_coverage_status') or 'insufficient_real_evidence')
    claim_reasons = sorted(
        {
            str(reason)
            for reason in [
                *(coverage_record.get('claim_ineligibility_reasons') or []),
                *(first_payload.get('claim_ineligibility_reasons') or []),
                *[
                    reason
                    for alert in alerts
                    for reason in (((alert.get('payload') or {}).get('claim_ineligibility_reasons')) or [])
                ],
            ]
            if str(reason).strip()
        }
    )

    market_claim_eligible = bool(coverage_record.get('market_claim_eligible'))
    oracle_claim_eligible = bool(coverage_record.get('oracle_claim_eligible'))
    enterprise_claim_eligibility = bool(coverage_record.get('enterprise_claim_eligibility'))
    market_observation_count = int(coverage_record.get('market_observation_count') or 0)
    oracle_observation_count = int(coverage_record.get('oracle_observation_count') or 0)
    real_provider_observations_present = market_observation_count > 0 and oracle_observation_count > 0

    lifecycle_checks_performed = sorted(
        {
            str(result.get('detector_family'))
            for alert in alerts
            for result in ((alert.get('payload') or {}).get('detector_results') or [])
            if isinstance(result, dict) and result.get('lifecycle_stage')
        }
    )

    anomalies_observed = any(str(((alert.get('payload') or {}).get('detector_status') or '')).lower() == 'anomaly_detected' for alert in alerts)

    if run_status >= 400:
        status_value = 'monitoring_execution_failed'
        reason = 'monitoring_run_request_failed'
        claim_reasons = sorted(set([*claim_reasons, 'monitoring_run_request_failed']))
    elif missing_context_fields or missing_target_fields:
        status_value = 'asset_configuration_incomplete'
        reason = 'asset_or_target_context_missing_required_fields'
        claim_reasons = sorted(set([*claim_reasons, *[f'missing_{item}' for item in missing_context_fields]]))
        claim_reasons = sorted(set([*claim_reasons, *[f'missing_{item}' for item in missing_target_fields]]))
    elif not worker_monitoring_executed:
        status_value = 'monitoring_execution_failed'
        reason = 'worker_monitoring_not_executed'
        claim_reasons = sorted(set([*claim_reasons, 'worker_monitoring_not_executed']))
    elif enterprise_claim_eligibility and market_claim_eligible and oracle_claim_eligible and worker_monitoring_executed and real_provider_observations_present:
        status_value = 'live_coverage_confirmed'
        reason = None
    elif worker_monitoring_executed:
        status_value = 'live_coverage_denied'
        reason = 'coverage_requirements_not_satisfied'
        if not real_provider_observations_present:
            claim_reasons = sorted(set([*claim_reasons, 'missing_real_provider_observations']))
    else:
        status_value = 'monitoring_execution_failed'
        reason = 'worker_monitoring_not_executed'
        claim_reasons = sorted(set([*claim_reasons, 'worker_monitoring_not_executed']))

    summary = {
        'generated_at': _now_iso(),
        'status': status_value,
        'reason': reason,
        'workspace_id': args.workspace_id,
        'target_id': target.get('id'),
        'worker_monitoring_executed': worker_monitoring_executed,
        'worker_run_count': len(worker_runs),
        'worker_run_request_http_status': run_status,
        'protected_asset_context': protected_asset_context,
        'market_coverage_status': market_coverage_status,
        'oracle_coverage_status': oracle_coverage_status,
        'market_provider_count': int(coverage_record.get('market_provider_count') or 0),
        'market_provider_reachable_count': int(coverage_record.get('market_provider_reachable_count') or 0),
        'market_provider_fresh_count': int(coverage_record.get('market_provider_fresh_count') or 0),
        'market_provider_names': coverage_record.get('market_provider_names') or [],
        'market_observation_count': market_observation_count,
        'oracle_provider_count': int(coverage_record.get('oracle_provider_count') or 0),
        'oracle_provider_reachable_count': int(coverage_record.get('oracle_provider_reachable_count') or 0),
        'oracle_provider_fresh_count': int(coverage_record.get('oracle_provider_fresh_count') or 0),
        'oracle_provider_names': coverage_record.get('oracle_provider_names') or [],
        'oracle_observation_count': oracle_observation_count,
        'enterprise_claim_eligibility': enterprise_claim_eligibility,
        'market_claim_eligible': market_claim_eligible,
        'oracle_claim_eligible': oracle_claim_eligible,
        'claim_ineligibility_reasons': claim_reasons,
        'external_market_telemetry_present': market_observation_count > 0,
        'real_oracle_observations_present': oracle_observation_count > 0,
        'lifecycle_checks_executed': bool(lifecycle_checks_performed or worker_monitoring_executed),
        'lifecycle_checks_performed': lifecycle_checks_performed,
        'anomalies_observed': anomalies_observed,
        'result_scope': 'enterprise_claim_eligible' if enterprise_claim_eligibility else 'internal_only',
        'target_identity': target_identity,
        'protected_asset_identity': {
            'asset_id': protected_asset_context.get('asset_id'),
            'asset_identifier': protected_asset_context.get('asset_identifier'),
            'symbol': protected_asset_context.get('symbol'),
            'chain_id': protected_asset_context.get('chain_id'),
            'contract_address': protected_asset_context.get('contract_address'),
        },
        'missing_asset_context_fields': missing_context_fields,
        'missing_target_identity_fields': missing_target_fields,
    }

    if summary['status'] not in ALLOWED_STATUSES:
        summary['status'] = 'monitoring_execution_failed'
        summary['reason'] = 'invalid_status_guard_triggered'

    evidence = [
        {
            'record_type': 'coverage_evaluation',
            'status': summary['status'],
            'reason': summary.get('reason'),
            'protected_asset_context': protected_asset_context,
            'target_identity': summary['target_identity'],
            'worker_monitoring_executed': worker_monitoring_executed,
            'worker_run_request_http_status': run_status,
            'worker_run_ids': [item.get('id') for item in worker_runs],
            'market_coverage_status': market_coverage_status,
            'oracle_coverage_status': oracle_coverage_status,
            'enterprise_claim_eligibility': enterprise_claim_eligibility,
            'market_claim_eligible': market_claim_eligible,
            'oracle_claim_eligible': oracle_claim_eligible,
            'market_provider_names': coverage_record.get('market_provider_names') or [],
            'oracle_provider_names': coverage_record.get('oracle_provider_names') or [],
            'market_observation_count': market_observation_count,
            'oracle_observation_count': oracle_observation_count,
            'lifecycle_checks_performed': lifecycle_checks_performed,
            'lifecycle_checks_executed': summary['lifecycle_checks_executed'],
            'anomalies_observed': anomalies_observed,
            'claim_ineligibility_reasons': claim_reasons,
            'missing_asset_context_fields': missing_context_fields,
            'missing_target_identity_fields': missing_target_fields,
            'monitoring_run_response': run_payload,
        }
    ]

    for alert in alerts:
        payload = alert.get('payload') if isinstance(alert.get('payload'), dict) else {}
        evidence.append(
            {
                'record_type': 'alert_evaluation',
                'alert_id': alert.get('id'),
                'analysis_run_id': alert.get('analysis_run_id'),
                'detector_family': payload.get('detector_family'),
                'detector_status': payload.get('detector_status'),
                'enterprise_claim_eligibility': bool(payload.get('enterprise_claim_eligibility')),
                'market_coverage_status': payload.get('market_coverage_status'),
                'oracle_coverage_status': payload.get('oracle_coverage_status'),
                'claim_ineligibility_reasons': payload.get('claim_ineligibility_reasons') or [],
            }
        )

    _write_artifacts(
        artifacts_dir=artifacts_dir,
        summary=summary,
        alerts=alerts,
        incidents=incidents,
        runs=runs,
        evidence=evidence,
    )
    print(json.dumps({**summary, 'artifacts_dir': str(artifacts_dir)}, indent=2))
    return 0 if summary['status'] in {'live_coverage_confirmed', 'live_coverage_denied'} else 2


if __name__ == '__main__':
    sys.exit(main())
