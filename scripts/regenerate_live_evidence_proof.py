#!/usr/bin/env python3
"""Refresh live provider proof without manufacturing workflow records.

The script verifies current RPC connectivity, then accepts only an existing
persisted evidence chain from a configured workspace monitoring target. The
chain must include provider receipt data, matching on-chain activity, a
non-informational detector trigger, and telemetry-to-detection-to-alert
linkage. RPC health and block observations remain informational and cannot set
``live_evidence_ready``. Incident and response-action records are optional and
are preserved only when actual detector policy created them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.generate_live_evidence_proof import (
    NO_LIVE_EVENT_REASON,
    _load_live_evidence_chain_from_env,
    _validated_live_evidence_chain,
)

_PROOF_NAMESPACE = uuid.UUID('a1b2c3d4-e5f6-4789-abcd-dec0da00aaaa')

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

_DEFAULT_OUT_DIR = REPO_ROOT / 'artifacts' / 'live-evidence-proof' / 'latest'
_NO_SECRETS_OUT_DIR = REPO_ROOT / 'artifacts' / 'live-evidence-proof' / 'no-secrets-test' / 'latest'

_PLACEHOLDER_MARKERS = frozenset({
    'example', 'changeme', 'replace-me', 'placeholder', 'test-key', 'your_',
})


def _env_val(name: str) -> str:
    return (os.getenv(name) or '').strip()


def _has_placeholder(val: str) -> bool:
    return any(m in val.lower() for m in _PLACEHOLDER_MARKERS)


def _mask_url(url: str) -> str:
    if not url:
        return ''
    parts = url.rstrip('/').rsplit('/', 1)
    if len(parts) == 2 and len(parts[1]) > 6:
        return parts[0] + '/[masked]'
    return url[:20] + '...' if len(url) > 20 else url


def _rpc_call(
    url: str,
    method: str,
    params: list | None = None,
    timeout: int = 10,
) -> dict[str, Any]:
    payload = json.dumps({
        'jsonrpc': '2.0',
        'method': method,
        'params': params or [],
        'id': 1,
    }).encode()
    try:
        req = urllib.request.Request(
            url,
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.URLError as exc:
        return {'error': f'URLError: {exc.reason}'}
    except Exception as exc:
        return {'error': f'{type(exc).__name__}: {exc}'}


def _content_id(prefix: str, *parts: str) -> str:
    content = prefix + ':' + ':'.join(str(p) for p in parts)
    return str(uuid.uuid5(_PROOF_NAMESPACE, content))


def _hex_to_dec(hex_val: Any) -> str | None:
    try:
        val = str(hex_val or '').strip()
        if val.startswith('0x') or val.startswith('0X'):
            return str(int(val, 16))
        if val.isdigit():
            return val
        return None
    except Exception:
        return None


def _build_fail_closed(
    *,
    now: str,
    provider_ready: bool,
    provider_mode: str,
    provider_health_checked: bool,
    provider_checked_at: str | None,
    provider_url_masked: str,
    chain_id_configured: bool,
    chain_id_observed: str | None,
    block_number_observed: str | None,
    missing: list[str],
    contradiction_flags: list[str],
) -> dict[str, Any]:
    return {
        'schema_version': 1,
        'generated_at': now,
        'live_provider_evidence': {
            'provider_ready': provider_ready,
            'provider_mode': provider_mode,
            'provider_health_checked': provider_health_checked,
            'provider_checked_at': provider_checked_at,
            'provider_url_masked': provider_url_masked,
            'chain_id_configured': chain_id_configured,
            'chain_id_observed': chain_id_observed,
            'block_number_observed': block_number_observed,
            'worker_enabled': False,
            'live_provider_ready': False,
            'live_provider_receipt_ready': block_number_observed is not None,
            'live_telemetry_ready': False,
            'live_detection_ready': False,
            'live_alert_ready': False,
            'live_incident_ready': False,
            'evidence_source': 'unknown',
            'live_evidence_source': 'unknown',
            'latest_live_telemetry_at': None,
            'live_evidence_ready': False,
            'run_id': None,
            'github_run_id': None,
            'chain': {
                'run_id': None,
                'telemetry_event_id': None,
                'detection_id': None,
                'alert_id': None,
                'incident_id': None,
                'response_action_id': None,
                'evidence_package_id': None,
            },
            'missing': missing,
            'contradiction_flags': contradiction_flags,
        },
    }


def regenerate_live_evidence_proof(
    *,
    rpc_url_override: str | None = None,
    github_run_id: str | None = None,
    live_evidence_chain: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Refresh provider health while preserving a persisted detector proof chain.

    The RPC observation is connectivity evidence only. It never creates IDs,
    incidents, response actions, detections, or alerts. Readiness is true only
    when ``live_evidence_chain`` (or the configured chain env input) passes the
    same target-origin, receipt, on-chain-match, detector, and linkage checks as
    generate_live_evidence_proof().
    """
    now = datetime.now(timezone.utc).isoformat()

    staging_rpc = _env_val('STAGING_EVM_RPC_URL')
    base_rpc = _env_val('EVM_RPC_URL')
    effective_rpc = rpc_url_override or (staging_rpc if staging_rpc else base_rpc)

    staging_chain_id = _env_val('STAGING_EVM_CHAIN_ID')
    base_chain_id = _env_val('EVM_CHAIN_ID') or _env_val('CHAIN_ID')
    effective_chain_id_raw = staging_chain_id if staging_chain_id else base_chain_id

    github_run_id = github_run_id or _env_val('GITHUB_RUN_ID') or ''

    rpc_ok = bool(effective_rpc) and not _has_placeholder(effective_rpc)
    chain_id_configured = (
        bool(effective_chain_id_raw) and not _has_placeholder(effective_chain_id_raw)
    )
    provider_url_masked = _mask_url(effective_rpc) if effective_rpc else ''

    if not rpc_ok:
        return _build_fail_closed(
            now=now,
            provider_ready=False,
            provider_mode='disabled',
            provider_health_checked=False,
            provider_checked_at=None,
            provider_url_masked=provider_url_masked,
            chain_id_configured=chain_id_configured,
            chain_id_observed=None,
            block_number_observed=None,
            missing=['EVM_RPC_URL or STAGING_EVM_RPC_URL not configured'],
            contradiction_flags=[],
        )

    check_time = datetime.now(timezone.utc).isoformat()
    chain_id_resp = _rpc_call(effective_rpc, 'eth_chainId')
    block_resp = _rpc_call(effective_rpc, 'eth_blockNumber')

    if 'error' in chain_id_resp:
        return _build_fail_closed(
            now=now,
            provider_ready=False,
            provider_mode='disabled',
            provider_health_checked=True,
            provider_checked_at=check_time,
            provider_url_masked=provider_url_masked,
            chain_id_configured=chain_id_configured,
            chain_id_observed=None,
            block_number_observed=None,
            missing=[f"provider unreachable: {chain_id_resp['error']}"],
            contradiction_flags=['provider_unreachable'],
        )

    if 'result' not in chain_id_resp:
        return _build_fail_closed(
            now=now,
            provider_ready=False,
            provider_mode='disabled',
            provider_health_checked=True,
            provider_checked_at=check_time,
            provider_url_masked=provider_url_masked,
            chain_id_configured=chain_id_configured,
            chain_id_observed=None,
            block_number_observed=None,
            missing=['eth_chainId returned unexpected response (no result or error key)'],
            contradiction_flags=['provider_bad_response'],
        )

    chain_id_observed = _hex_to_dec(chain_id_resp['result'])
    if chain_id_observed is None:
        return _build_fail_closed(
            now=now,
            provider_ready=False,
            provider_mode='disabled',
            provider_health_checked=True,
            provider_checked_at=check_time,
            provider_url_masked=provider_url_masked,
            chain_id_configured=chain_id_configured,
            chain_id_observed=None,
            block_number_observed=None,
            missing=['eth_chainId returned unreadable value'],
            contradiction_flags=['provider_bad_response'],
        )

    block_number_hex: str | None = block_resp.get('result') if 'result' in block_resp else None
    block_number_observed = _hex_to_dec(block_number_hex) if block_number_hex else None

    if chain_id_configured and effective_chain_id_raw and chain_id_observed:
        configured = effective_chain_id_raw.strip()
        if configured != chain_id_observed:
            return _build_fail_closed(
                now=now,
                provider_ready=False,
                provider_mode='disabled',
                provider_health_checked=True,
                provider_checked_at=check_time,
                provider_url_masked=provider_url_masked,
                chain_id_configured=chain_id_configured,
                chain_id_observed=chain_id_observed,
                block_number_observed=block_number_observed,
                missing=[
                    f'chain ID mismatch: configured {configured!r} != observed {chain_id_observed!r}'
                ],
                contradiction_flags=[
                    f'chain_id_mismatch: configured={configured!r} observed={chain_id_observed!r}'
                ],
            )

    raw_rpc_response_hash = hashlib.sha256(
        json.dumps({
            'chain_id': chain_id_resp.get('result'),
            'block_number': block_number_hex,
        }).encode()
    ).hexdigest()[:32]

    actual_chain = live_evidence_chain
    if actual_chain is None:
        actual_chain = _load_live_evidence_chain_from_env()
    real_chain = _validated_live_evidence_chain(actual_chain)
    if real_chain is None:
        return _build_fail_closed(
            now=now,
            provider_ready=True,
            provider_mode='live',
            provider_health_checked=True,
            provider_checked_at=check_time,
            provider_url_masked=provider_url_masked,
            chain_id_configured=chain_id_configured,
            chain_id_observed=chain_id_observed,
            block_number_observed=block_number_observed,
            missing=[NO_LIVE_EVENT_REASON],
            contradiction_flags=[],
        )

    run_id = str(real_chain.get('run_id') or real_chain.get('monitoring_run_id') or '') or None
    telemetry_id = str(real_chain['telemetry_event_id'])
    detection_event_id = str(real_chain['detection_event_id'])
    detection_id = str(real_chain['detection_id'])
    alert_id = str(real_chain['alert_id'])
    incident_id = str(real_chain.get('incident_id') or '') or None
    response_action_id = str(real_chain.get('response_action_id') or '') or None
    evidence_package_id = str(real_chain['evidence_package_id'])
    observed_at = str(real_chain.get('observed_at') or check_time)

    return {
        'schema_version': 1,
        'generated_at': now,
        'live_provider_evidence': {
            'provider_ready': True,
            'provider_mode': 'live',
            'provider_health_checked': True,
            'provider_checked_at': check_time,
            'provider_url_masked': provider_url_masked,
            'chain_id_configured': chain_id_configured,
            'chain_id_observed': chain_id_observed,
            'block_number_observed': block_number_observed,
            'worker_enabled': True,
            'live_provider_ready': True,
            'live_provider_receipt_ready': True,
            'live_telemetry_ready': True,
            'live_detection_ready': True,
            'live_alert_ready': True,
            'live_incident_ready': bool(incident_id),
            'evidence_source': 'live',
            'live_evidence_source': 'live_rpc',
            'latest_live_telemetry_at': observed_at,
            'live_evidence_ready': True,
            'run_id': run_id,
            'github_run_id': github_run_id or None,
            'chain': {
                'run_id': run_id,
                'telemetry_event_id': telemetry_id,
                'detection_event_id': detection_event_id,
                'detection_id': detection_id,
                'alert_id': alert_id,
                'incident_id': incident_id,
                'response_action_id': response_action_id,
                'evidence_package_id': evidence_package_id,
            },
            'telemetry_record': {
                'run_id': run_id,
                'telemetry_event_id': telemetry_id,
                'observed_at': observed_at,
                'generated_at': now,
                'provider_checked_at': check_time,
                'github_run_id': github_run_id or None,
                'evidence_source': 'live',
                'live_evidence_source': 'live_rpc',
                'source': 'live_rpc',
                'source_type': real_chain.get('source_type'),
                'workspace_id': real_chain.get('workspace_id'),
                'target_id': real_chain.get('target_id'),
                'target_identifier': real_chain.get('target_identifier'),
                'target_configured': True,
                'provider_receipt': real_chain.get('provider_receipt'),
                'on_chain_activity': real_chain.get('on_chain_activity'),
                'chain_id': chain_id_observed,
                'block_number': block_number_observed,
                'latest_block_number': block_number_observed,
                'raw_rpc_response_hash': raw_rpc_response_hash,
            },
            'detection_record': {
                'run_id': run_id,
                'detection_event_id': detection_event_id,
                'detection_id': detection_id,
                'detection_name': real_chain.get('detection_name'),
                'telemetry_event_id': telemetry_id,
                'severity': real_chain.get('severity'),
                'detector_result': real_chain.get('detector_result'),
            },
            'alert_record': {
                'run_id': run_id,
                'alert_id': alert_id,
                'detection_event_id': detection_event_id,
                'detection_id': detection_id,
            },
            'incident_record': (
                {'run_id': run_id, 'incident_id': incident_id, 'alert_id': alert_id}
                if incident_id else {}
            ),
            'response_action_record': (
                {'run_id': run_id, 'response_action_id': response_action_id, 'alert_id': alert_id}
                if response_action_id else {}
            ),
            'evidence_package_record': {
                'run_id': run_id,
                'evidence_package_id': evidence_package_id,
                'telemetry_event_id': telemetry_id,
                'detection_event_id': detection_event_id,
                'detection_id': detection_id,
                'alert_id': alert_id,
                'incident_id': incident_id,
                'response_action_id': response_action_id,
                'provider_receipt': real_chain.get('provider_receipt'),
                'on_chain_activity': real_chain.get('on_chain_activity'),
                'persisted_linkage': real_chain.get('persisted_linkage'),
            },
            'missing': [],
            'contradiction_flags': [],
        },
    }


def main(strict: bool = False, out_dir: Path | None = None) -> int:
    print('[regenerate-live-evidence-proof] Reading provider env vars...')

    effective_out_dir = out_dir or _DEFAULT_OUT_DIR
    effective_out_dir.mkdir(parents=True, exist_ok=True)

    result = regenerate_live_evidence_proof()
    lpe = result.get('live_provider_evidence', {})

    summary_path = effective_out_dir / 'summary.json'
    with open(summary_path, 'w') as f:
        json.dump(result, f, indent=2)
    print(f'[regenerate-live-evidence-proof] wrote {summary_path}')

    for field in (
        'provider_ready', 'provider_mode', 'chain_id_configured',
        'chain_id_observed', 'block_number_observed', 'provider_health_checked',
        'evidence_source', 'live_evidence_source', 'latest_live_telemetry_at',
        'live_evidence_ready', 'run_id',
    ):
        print(f'[regenerate-live-evidence-proof] {field}={lpe.get(field)}')

    if lpe.get('live_evidence_ready'):
        chain = lpe.get('chain', {})
        chain_path = effective_out_dir / 'live_evidence_chain.json'
        chain_data = {
            'evidence_source': 'live',
            'live_evidence_source': 'live_rpc',
            'source_type': 'rpc_polling',
            'run_id': lpe.get('run_id'),
            'github_run_id': lpe.get('github_run_id'),
            'generated_at': result.get('generated_at'),
            'provider_checked_at': lpe.get('provider_checked_at'),
            'workspace_id': (lpe.get('telemetry_record') or {}).get('workspace_id'),
            'target_id': (lpe.get('telemetry_record') or {}).get('target_id'),
            'target_identifier': (lpe.get('telemetry_record') or {}).get('target_identifier'),
            'target_configured': True,
            'telemetry_event_id': chain.get('telemetry_event_id'),
            'detection_event_id': chain.get('detection_event_id'),
            'detection_id': chain.get('detection_id'),
            'alert_id': chain.get('alert_id'),
            'incident_id': chain.get('incident_id'),
            'response_action_id': chain.get('response_action_id'),
            'evidence_package_id': chain.get('evidence_package_id'),
            'observed_at': lpe.get('latest_live_telemetry_at'),
            'latest_live_telemetry_at': lpe.get('latest_live_telemetry_at'),
            'chain_id': lpe.get('chain_id_observed'),
            'block_number': lpe.get('block_number_observed'),
            'detection_name': (lpe.get('detection_record') or {}).get('detection_name'),
            'severity': (lpe.get('detection_record') or {}).get('severity'),
            'detector_result': (lpe.get('detection_record') or {}).get('detector_result'),
            'provider_receipt': (lpe.get('telemetry_record') or {}).get('provider_receipt'),
            'on_chain_activity': (lpe.get('telemetry_record') or {}).get('on_chain_activity'),
            'persisted_linkage': (lpe.get('evidence_package_record') or {}).get('persisted_linkage'),
        }
        with open(chain_path, 'w') as f:
            json.dump(chain_data, f, indent=2)
        print(f'[regenerate-live-evidence-proof] wrote {chain_path}')
        print('[regenerate-live-evidence-proof] Live evidence chain:')
        for key, val in chain.items():
            if val:
                print(f'  {key}: {val}')

    if lpe.get('missing'):
        print('[regenerate-live-evidence-proof] Missing:')
        for item in lpe['missing']:
            print(f'  - {item}')

    if lpe.get('contradiction_flags'):
        print('[regenerate-live-evidence-proof] Contradiction flags:')
        for flag in lpe['contradiction_flags']:
            print(f'  - {flag}')

    if strict and not lpe.get('live_evidence_ready'):
        print('[regenerate-live-evidence-proof] FAIL: live_evidence_ready=false in strict mode')
        return 1

    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Regenerate live evidence proof from current RPC response.'
    )
    parser.add_argument('--strict', action='store_true',
                        help='Exit 1 when live_evidence_ready=false')
    parser.add_argument('--no-secrets-test', action='store_true',
                        help='Write to no-secrets-test path, never overwriting provider proof')
    parser.add_argument('--output-dir', default=None,
                        help='Override output directory (default: artifacts/live-evidence-proof/latest)')
    args = parser.parse_args()

    if args.output_dir:
        out = Path(args.output_dir)
    elif args.no_secrets_test:
        out = _NO_SECRETS_OUT_DIR
    else:
        out = None

    raise SystemExit(main(strict=args.strict, out_dir=out))
