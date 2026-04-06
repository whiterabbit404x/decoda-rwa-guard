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
REQUIRED_PROTECTED_ASSET_FIELDS = (
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
)


def _lifecycle_execution_details(*, status: str, lifecycle_checks_performed: list[str], worker_monitoring_executed: bool, missing_context_fields: list[str], run_failed: bool) -> tuple[bool, str | None]:
    if lifecycle_checks_performed:
        return True, None
    if status == 'asset_configuration_incomplete':
        return False, 'lifecycle_prerequisites_missing'
    if run_failed:
        return False, 'monitoring_run_request_failed'
    if status == 'monitoring_execution_failed' and not worker_monitoring_executed:
        return False, 'worker_monitoring_not_executed'
    if missing_context_fields:
        return False, 'lifecycle_prerequisites_missing'
    return False, 'no_lifecycle_signal_emitted'


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


def _resolve_asset_for_target(*, api_url: str, token: str, workspace_id: str, target: dict[str, Any]) -> tuple[int, dict[str, Any] | None]:
    target_asset_id = str(target.get('asset_id') or '').strip()
    status, assets_payload = _request_json(f"{api_url.rstrip('/')}/assets", token=token, workspace_id=workspace_id)
    if status >= 400:
        return status, None
    assets = assets_payload.get('assets') if isinstance(assets_payload.get('assets'), list) else []
    if not assets and isinstance(assets_payload, list):
        assets = assets_payload
    if not isinstance(assets, list):
        return 200, None
    for item in assets:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get('id') or '').strip()
        if target_asset_id and item_id == target_asset_id:
            return 200, item
    return 200, None


def _asset_context_from_asset(asset: dict[str, Any] | None, target: dict[str, Any]) -> dict[str, Any]:
    asset = asset or {}
    chain_id = asset.get('chain_id')
    if chain_id is None:
        chain_id = target.get('chain_id')
    if chain_id is None:
        chain_id = int(os.getenv('FEATURE1_PROOF_CHAIN_ID', '1'))
    return {
        'asset_id': asset.get('id') or target.get('asset_id'),
        'asset_identifier': asset.get('asset_identifier') or asset.get('identifier') or target.get('asset_identifier'),
        'symbol': asset.get('asset_symbol') or asset.get('symbol') or target.get('asset_symbol'),
        'chain_id': chain_id,
        'contract_address': asset.get('token_contract_address') or target.get('contract_identifier'),
        'treasury_ops_wallets': asset.get('treasury_ops_wallets') or target.get('treasury_ops_wallets') or [],
        'custody_wallets': asset.get('custody_wallets') or target.get('custody_wallets') or [],
        'expected_flow_patterns': asset.get('expected_flow_patterns') or [],
        'expected_counterparties': asset.get('expected_counterparties') or [],
        'expected_approval_patterns': asset.get('expected_approval_patterns') or {},
        'venue_labels': asset.get('venue_labels') or target.get('venue_labels') or [],
        'expected_liquidity_baseline': asset.get('expected_liquidity_baseline') or {},
        'oracle_sources': asset.get('oracle_sources') or [],
        'expected_oracle_freshness_seconds': asset.get('expected_oracle_freshness_seconds') or target.get('expected_oracle_freshness_seconds'),
        'expected_oracle_update_cadence_seconds': asset.get('expected_oracle_update_cadence_seconds') or target.get('expected_oracle_update_cadence_seconds'),
    }


def _missing_asset_fields(context: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for key in REQUIRED_PROTECTED_ASSET_FIELDS:
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
        f"- worker_monitoring_executed: `{summary.get('worker_monitoring_executed')}`\n"
        f"- lifecycle_checks_executed: `{summary.get('lifecycle_checks_executed')}`\n"
        f"- anomalies_observed: `{summary.get('anomalies_observed')}`\n"
        f"- protected_asset_identity: `{summary.get('protected_asset_identity')}`\n"
        f"- target_identity: `{summary.get('target_identity')}`\n"
        f"- claim_ineligibility_reasons: `{summary.get('claim_ineligibility_reasons')}`\n"
        f"- proof_command: `{summary.get('proof_command')}`\n"
        f"- monitoring_worker_name: `{summary.get('monitoring_worker_name')}`\n"
        f"- monitoring_run_ids: `{summary.get('monitoring_run_ids')}`\n"
        f"- anomalous_tx_hashes: `{summary.get('anomalous_tx_hashes')}`\n"
        f"- anomaly_kind: `{summary.get('anomaly_kind')}`\n"
    )


def _find_anomalous_rows(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in evidence:
        if not isinstance(row, dict):
            continue
        if row.get('record_type') == 'coverage_evaluation':
            continue
        has_link = bool(row.get('event_id')) or (bool(row.get('tx_hash')) and row.get('block_number') is not None)
        if has_link:
            rows.append(row)
    return rows


def _require_honest_proof_or_raise(*, summary: dict[str, Any], alerts: list[dict[str, Any]], runs: list[dict[str, Any]], incidents: list[dict[str, Any]], evidence: list[dict[str, Any]], missing_context_fields: list[str], missing_target_fields: list[str]) -> None:
    problems: list[str] = []
    if missing_context_fields:
        problems.append(f'protected_asset_context missing required fields: {missing_context_fields}')
    if missing_target_fields:
        problems.append(f'target_identity missing required fields: {missing_target_fields}')
    if not summary.get('worker_monitoring_executed'):
        problems.append('worker_monitoring_executed=false')
    if not summary.get('lifecycle_checks_executed'):
        problems.append('lifecycle_checks_executed=false')
    if not summary.get('anomalies_observed'):
        problems.append('anomalies_observed=false')
    if not alerts:
        problems.append('alerts.json empty')
    if not runs:
        problems.append('runs.json empty')
    if not evidence:
        problems.append('evidence.json empty')
    elif all(str(item.get('record_type') or '') == 'coverage_evaluation' for item in evidence if isinstance(item, dict)):
        problems.append('evidence only contains coverage_evaluation rows')
    anomalous_rows = _find_anomalous_rows(evidence)
    if not anomalous_rows:
        problems.append('no tx_hash/block_number or event_id-linked anomaly rows in evidence')
    if any(str((item.get('severity') or '')).lower() in {'high', 'critical'} for item in alerts if isinstance(item, dict)) and not incidents:
        problems.append('incidents.json empty despite high/critical alerts')
    if problems:
        raise RuntimeError('; '.join(problems))


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
        target_identity = _target_identity(None)
        execution_failure_reasons = ['runtime_unavailable', 'worker_monitoring_not_executed']
        summary = {
            'generated_at': _now_iso(),
            'status': 'monitoring_execution_failed',
            'reason': 'runtime_unavailable',
            'http_status': status,
            'enterprise_claim_eligibility': False,
            'market_claim_eligible': False,
            'oracle_claim_eligible': False,
            'claim_ineligibility_reasons': execution_failure_reasons,
            'target_identity': target_identity,
            'protected_asset_context': {},
            'protected_asset_identity': {key: None for key in ('asset_id', 'asset_identifier', 'symbol', 'chain_id', 'contract_address')},
            'missing_target_identity_fields': list(REQUIRED_TARGET_FIELDS),
            'missing_asset_context_fields': list(REQUIRED_PROTECTED_ASSET_FIELDS),
            'worker_monitoring_executed': False,
            'lifecycle_checks_executed': False,
            'lifecycle_checks_not_executed_reason': 'runtime_unavailable',
            'execution_failure_reasons': execution_failure_reasons,
            'market_coverage_status': 'insufficient_real_evidence',
            'oracle_coverage_status': 'insufficient_real_evidence',
        }
        _write_artifacts(
            artifacts_dir=artifacts_dir,
            summary=summary,
            alerts=[],
            incidents=[],
            runs=[],
            evidence=[{
                'record_type': 'coverage_evaluation',
                'status': 'monitoring_execution_failed',
                'reason': 'runtime_unavailable',
                'target_identity': target_identity,
                'protected_asset_context': {},
                'missing_target_identity_fields': list(REQUIRED_TARGET_FIELDS),
                'missing_asset_context_fields': list(REQUIRED_PROTECTED_ASSET_FIELDS),
                'worker_monitoring_executed': False,
                'lifecycle_checks_executed': False,
                'enterprise_claim_eligibility': False,
                'claim_ineligibility_reasons': execution_failure_reasons,
                'execution_failure_reasons': execution_failure_reasons,
                'monitoring_runtime_response': runtime,
            }],
        )
        print(json.dumps({'summary': summary, 'runtime': runtime, 'artifacts_dir': str(artifacts_dir)}, indent=2))
        return 2

    mode = str(runtime.get('configured_mode') or runtime.get('mode') or '').upper()

    target_status, target = _resolve_or_create_target(
        api_url=args.api_url,
        token=args.token,
        workspace_id=args.workspace_id,
        target_id=args.target_id,
    )
    target_identity = _target_identity(target)
    missing_target_fields = _missing_target_fields(target_identity)
    if target is None:
        execution_failure_reasons = ['missing_target_or_asset_profile', 'worker_monitoring_not_executed']
        summary = {
            'generated_at': _now_iso(),
            'status': 'asset_configuration_incomplete',
            'reason': 'no_monitored_target_with_asset_profile_found',
            'enterprise_claim_eligibility': False,
            'market_claim_eligible': False,
            'oracle_claim_eligible': False,
            'claim_ineligibility_reasons': sorted(set([*execution_failure_reasons, *[f'missing_{field}' for field in REQUIRED_TARGET_FIELDS]])),
            'target_identity': target_identity,
            'protected_asset_context': {},
            'protected_asset_identity': {key: None for key in ('asset_id', 'asset_identifier', 'symbol', 'chain_id', 'contract_address')},
            'missing_target_identity_fields': list(REQUIRED_TARGET_FIELDS),
            'missing_asset_context_fields': list(REQUIRED_PROTECTED_ASSET_FIELDS),
            'worker_monitoring_executed': False,
            'lifecycle_checks_executed': False,
            'lifecycle_checks_not_executed_reason': 'lifecycle_prerequisites_missing',
            'execution_failure_reasons': execution_failure_reasons,
            'market_coverage_status': 'insufficient_real_evidence',
            'oracle_coverage_status': 'insufficient_real_evidence',
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
                'protected_asset_context': {},
                'missing_target_identity_fields': list(REQUIRED_TARGET_FIELDS),
                'missing_asset_context_fields': list(REQUIRED_PROTECTED_ASSET_FIELDS),
                'missing_requirements': ['target_id', 'asset_id', *[f'target_identity.{field}' for field in REQUIRED_TARGET_FIELDS], *[f'protected_asset_context.{field}' for field in REQUIRED_PROTECTED_ASSET_FIELDS]],
                'enterprise_claim_eligibility': False,
                'worker_monitoring_executed': False,
                'lifecycle_checks_executed': False,
                'lifecycle_checks_not_executed_reason': 'lifecycle_prerequisites_missing',
                'claim_ineligibility_reasons': summary['claim_ineligibility_reasons'],
                'execution_failure_reasons': execution_failure_reasons,
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
    asset_status, resolved_asset = _resolve_asset_for_target(
        api_url=args.api_url,
        token=args.token,
        workspace_id=args.workspace_id,
        target=target,
    )
    if not protected_asset_context:
        protected_asset_context = _asset_context_from_asset(resolved_asset, target)

    missing_context_fields = _missing_asset_fields(protected_asset_context)

    if mode not in {'LIVE', 'HYBRID'}:
        claim_reasons = sorted(set(['mode_not_live_or_hybrid', *[f'missing_{item}' for item in missing_context_fields], *[f'missing_{item}' for item in missing_target_fields]]))
        execution_failure_reasons = ['mode_not_live_or_hybrid', 'worker_monitoring_not_executed']
        summary = {
            'generated_at': _now_iso(),
            'status': 'live_coverage_denied',
            'reason': 'mode_not_live_or_hybrid',
            'configured_mode': mode,
            'target_identity': target_identity,
            'protected_asset_context': protected_asset_context,
            'protected_asset_identity': {
                'asset_id': protected_asset_context.get('asset_id'),
                'asset_identifier': protected_asset_context.get('asset_identifier'),
                'symbol': protected_asset_context.get('symbol'),
                'chain_id': protected_asset_context.get('chain_id'),
                'contract_address': protected_asset_context.get('contract_address'),
            },
            'missing_target_identity_fields': missing_target_fields,
            'missing_asset_context_fields': missing_context_fields,
            'worker_monitoring_executed': False,
            'lifecycle_checks_executed': False,
            'lifecycle_checks_not_executed_reason': 'mode_not_live_or_hybrid',
            'execution_failure_reasons': execution_failure_reasons,
            'market_coverage_status': 'insufficient_real_evidence',
            'oracle_coverage_status': 'insufficient_real_evidence',
            'enterprise_claim_eligibility': False,
            'market_claim_eligible': False,
            'oracle_claim_eligible': False,
            'claim_ineligibility_reasons': claim_reasons,
        }
        _write_artifacts(
            artifacts_dir=artifacts_dir,
            summary=summary,
            alerts=[],
            incidents=[],
            runs=[],
            evidence=[{
                'record_type': 'coverage_evaluation',
                'status': summary['status'],
                'reason': summary['reason'],
                'configured_mode': mode,
                'target_identity': target_identity,
                'protected_asset_context': protected_asset_context,
                'missing_target_identity_fields': missing_target_fields,
                'missing_asset_context_fields': missing_context_fields,
                'worker_monitoring_executed': False,
                'lifecycle_checks_executed': False,
                'lifecycle_checks_not_executed_reason': 'mode_not_live_or_hybrid',
                'enterprise_claim_eligibility': False,
                'claim_ineligibility_reasons': claim_reasons,
                'execution_failure_reasons': execution_failure_reasons,
            }],
        )
        print(json.dumps({'summary': summary, 'artifacts_dir': str(artifacts_dir)}, indent=2))
        return 2

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
    elif asset_status >= 400 and not protected_asset_context:
        status_value = 'monitoring_execution_failed'
        reason = 'asset_resolution_failed'
        claim_reasons = sorted(set([*claim_reasons, 'asset_resolution_failed']))
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

    run_failed = run_status >= 400
    lifecycle_checks_executed, lifecycle_checks_not_executed_reason = _lifecycle_execution_details(
        status=status_value,
        lifecycle_checks_performed=lifecycle_checks_performed,
        worker_monitoring_executed=worker_monitoring_executed,
        missing_context_fields=missing_context_fields,
        run_failed=run_failed,
    )
    execution_failure_reasons = sorted(
        {
            reason
            for reason in claim_reasons
            if reason in {
                'runtime_unavailable',
                'monitoring_run_request_failed',
                'worker_monitoring_not_executed',
                'asset_resolution_failed',
                'mode_not_live_or_hybrid',
                'lifecycle_checks_not_executed',
            }
        }
    )
    if not lifecycle_checks_executed and status_value in {'live_coverage_confirmed', 'live_coverage_denied'}:
        status_value = 'monitoring_execution_failed'
        reason = 'lifecycle_checks_not_executed'
        claim_reasons = sorted(set([*claim_reasons, 'lifecycle_checks_not_executed']))
        execution_failure_reasons = sorted(set([*execution_failure_reasons, 'lifecycle_checks_not_executed']))

    summary = {
        'generated_at': _now_iso(),
        'status': status_value,
        'reason': reason,
        'workspace_id': args.workspace_id,
        'target_id': target.get('id'),
        'asset_id': protected_asset_context.get('asset_id'),
        'worker_monitoring_executed': worker_monitoring_executed,
        'worker_run_count': len(worker_runs),
        'worker_run_request_http_status': run_status,
        'asset_resolution_http_status': asset_status,
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
        'lifecycle_checks_executed': lifecycle_checks_executed,
        'lifecycle_checks_not_executed_reason': lifecycle_checks_not_executed_reason,
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
        'execution_failure_reasons': execution_failure_reasons,
        'proof_command': os.getenv('FEATURE1_PROOF_COMMAND', '').strip() or 'make proof-feature1-live',
        'monitoring_worker_name': os.getenv('FEATURE1_MONITORING_WORKER_NAME', 'feature1-live-proof-worker'),
        'monitoring_run_ids': [item.get('id') for item in worker_runs],
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
            'lifecycle_checks_not_executed_reason': summary.get('lifecycle_checks_not_executed_reason'),
            'anomalies_observed': anomalies_observed,
            'claim_ineligibility_reasons': claim_reasons,
            'missing_asset_context_fields': missing_context_fields,
            'missing_target_identity_fields': missing_target_fields,
            'execution_failure_reasons': execution_failure_reasons,
            'monitoring_run_response': run_payload,
        }
    ]

    for alert in alerts:
        payload = alert.get('payload') if isinstance(alert.get('payload'), dict) else {}
        detector_results = payload.get('detector_results') if isinstance(payload.get('detector_results'), list) else []
        tx_hash = None
        block_number = None
        event_id = None
        for detector in detector_results:
            if not isinstance(detector, dict):
                continue
            tx_hash = tx_hash or detector.get('tx_hash')
            block_number = block_number if block_number is not None else detector.get('block_number')
            event_id = event_id or detector.get('event_id') or ((detector.get('normalized_event_snapshot') or {}).get('metadata', {}).get('event_id') if isinstance(detector.get('normalized_event_snapshot'), dict) else None)
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
                'event_id': event_id,
                'tx_hash': tx_hash,
                'block_number': block_number,
            }
        )

    anomalous_rows = _find_anomalous_rows(evidence)
    summary['anomalous_tx_hashes'] = sorted({str(row.get('tx_hash')) for row in anomalous_rows if row.get('tx_hash')})
    summary['anomaly_kind'] = (
        'erc20_approval_unexpected_spender'
        if any(str(row.get('detector_family') or '').lower() in {'approval_pattern', 'approval'} for row in anomalous_rows)
        else 'asset_lifecycle_anomaly'
    )
    try:
        _require_honest_proof_or_raise(
            summary=summary,
            alerts=alerts,
            runs=worker_runs or runs,
            incidents=incidents,
            evidence=evidence,
            missing_context_fields=missing_context_fields,
            missing_target_fields=missing_target_fields,
        )
    except RuntimeError as exc:
        summary['status'] = 'monitoring_execution_failed'
        summary['reason'] = 'proof_validation_failed'
        summary['execution_failure_reasons'] = sorted(set([*execution_failure_reasons, 'proof_validation_failed']))
        summary['claim_ineligibility_reasons'] = sorted(set([*(summary.get('claim_ineligibility_reasons') or []), 'proof_validation_failed']))
        _write_artifacts(
            artifacts_dir=artifacts_dir,
            summary=summary,
            alerts=alerts,
            incidents=incidents,
            runs=runs,
            evidence=evidence,
        )
        print(json.dumps({'error': str(exc), **summary, 'artifacts_dir': str(artifacts_dir)}, indent=2))
        return 2

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
