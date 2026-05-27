#!/usr/bin/env python3
"""
Generate live provider evidence proof for Decoda RWA Guard.

Produces: artifacts/live-evidence-proof/latest/summary.json

Steps:
1. Read provider env (STAGING_EVM_RPC_URL preferred, EVM_RPC_URL fallback)
2. If env vars missing: fail closed, write proof with live_evidence_ready=false
3. If RPC env vars present:
   - Perform eth_chainId and eth_blockNumber JSON-RPC calls
   - Verify observed chain ID matches configured chain ID (if set)
   - Set live_provider_ready=True (RPC poll proves the provider is reachable)
   - Set live_provider_receipt_ready=True (block_number observation proves receipt)
4. Load real live-event evidence (telemetry_event_id, detection_id, alert_id,
   incident_id/response_action_id, evidence_package_id) from:
   - the `live_evidence_chain` parameter, or
   - env var LIVE_EVIDENCE_CHAIN_JSON (a JSON string), or
   - env var LIVE_EVIDENCE_CHAIN_FILE (path to a JSON file).
   The chain MUST carry evidence_source='live' and source_type='rpc_polling'.
5. If no real live-event evidence is found:
   - live_provider_ready stays True (RPC works), but
   - live_telemetry_ready / live_detection_ready / live_alert_ready /
     live_incident_ready / live_evidence_ready all stay False
   - reason: "Live RPC provider checked successfully, but no matching live
     telemetry event was found."
   - chain IDs are all null. No IDs are synthesised from eth_chainId or
     eth_blockNumber alone.
6. If real live-event evidence is found:
   - Build the full chain using the real IDs (never synthesise from RPC alone).
   - live_evidence_ready=True only when all required IDs are present and
     evidence_source='live'/source_type='rpc_polling'.

Fail-closed semantics:
- provider_ready=false when no RPC URL configured
- provider_ready=false when RPC call fails or is unreachable
- provider_ready=false when chain ID mismatch
- live_evidence_ready=false unless real live telemetry event exists
- safe_to_sell_broadly_today is NOT set by this script

Two-tier missing list:
- Provider-level issues (block provider_ready): no RPC URL, RPC error, chain ID mismatch
- Evidence-level issues (block live_evidence_ready only): missing chain ID, worker
  disabled, no matching live telemetry event observed

Usage:
  python scripts/generate_live_evidence_proof.py
  python scripts/generate_live_evidence_proof.py --strict
"""

from __future__ import annotations

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

# Proof-system namespace for content-addressable IDs.
# Using uuid5 (SHA-1 name-based) ensures IDs are derived from actual RPC data,
# not from random uuid4(). This makes evidence verifiable: same on-chain state
# produces the same IDs; different blocks produce different IDs.
_PROOF_NAMESPACE = uuid.UUID('a1b2c3d4-e5f6-4789-abcd-dec0da00aaaa')

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

_PLACEHOLDER_MARKERS = frozenset({
    'example', 'changeme', 'replace-me', 'placeholder', 'test-key', 'your_',
})


def _env_val(name: str) -> str:
    return (os.getenv(name) or '').strip()


def _has_placeholder(val: str) -> bool:
    return any(m in val.lower() for m in _PLACEHOLDER_MARKERS)


def _mask_url(url: str) -> str:
    """Mask API key segment: https://host/v3/SECRET -> https://host/v3/[masked]"""
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
    """
    Perform a single JSON-RPC 2.0 POST call.
    Returns dict with 'result' key on success or 'error' key on failure.
    Never raises — all errors are captured in the 'error' key.
    """
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
            data = json.loads(resp.read())
            return data
    except urllib.error.URLError as exc:
        return {'error': f'URLError: {exc.reason}'}
    except Exception as exc:
        return {'error': f'{type(exc).__name__}: {exc}'}


def _content_id(prefix: str, *parts: str) -> str:
    """
    Generate a content-addressable UUID derived from actual evidence data.

    Uses uuid5 (name-based, SHA-1) so IDs are deterministic: the same chain
    state produces the same IDs and different RPC observations produce different
    IDs. This prevents random uuid4()-only proofs from satisfying live_evidence_ready.
    """
    content = prefix + ':' + ':'.join(str(p) for p in parts)
    return str(uuid.uuid5(_PROOF_NAMESPACE, content))


def _hex_to_dec(hex_val: Any) -> str | None:
    """Convert 0x-prefixed hex or decimal string to decimal string. None on failure."""
    try:
        val = str(hex_val or '').strip()
        if val.startswith('0x') or val.startswith('0X'):
            return str(int(val, 16))
        if val.isdigit():
            return val
        return None
    except Exception:
        return None


def _empty_chain() -> dict[str, Any]:
    return {
        'telemetry_event_id': None,
        'detection_id': None,
        'alert_id': None,
        'incident_id': None,
        'response_action_id': None,
        'evidence_package_id': None,
    }


NO_LIVE_EVENT_REASON = (
    'Live RPC provider checked successfully, '
    'but no matching live telemetry event was found.'
)


def _query_db_latest_live_telemetry(workspace_id: str | None = None) -> dict[str, Any]:
    """
    Query telemetry_events for the most recent live RPC polling row.

    Only used as a fallback when RPC env vars are absent.  Returns a dict with
    'observed_at' (str ISO) and 'row_count' (int) on success; empty dict on
    any error so callers can treat it as a soft dependency.
    """
    try:
        from services.api.app.monitoring_runner import pg_connection
        conn = pg_connection()
        params_list: list[Any] = []
        workspace_clause = ''
        if workspace_id:
            workspace_clause = 'AND workspace_id = %s::uuid'
            params_list.append(workspace_id)
        row = conn.execute(
            f'''
            SELECT MAX(observed_at) AS ts, COUNT(*) AS c
            FROM telemetry_events
            WHERE evidence_source = 'live'
              AND event_type IN (\'rpc_polling\', \'live_provider\')
              AND provider_type IN (\'evm_rpc\', \'live_provider\')
              AND observed_at IS NOT NULL
              {workspace_clause}
            ''',
            params_list or None,
        ).fetchone()
        if row and row.get('ts') is not None:
            ts = row['ts']
            ts_str = ts.isoformat() if hasattr(ts, 'isoformat') else str(ts)
            return {'observed_at': ts_str, 'row_count': int(row.get('c') or 0)}
    except Exception:
        pass
    return {}


def _load_live_evidence_chain_from_env() -> dict[str, Any] | None:
    """
    Load a real live-event evidence chain from env vars.

    Returns the parsed dict when LIVE_EVIDENCE_CHAIN_JSON or
    LIVE_EVIDENCE_CHAIN_FILE points at usable JSON, otherwise None.
    No fields are invented; callers must validate the returned dict.
    """
    raw_json = _env_val('LIVE_EVIDENCE_CHAIN_JSON')
    if raw_json:
        try:
            data = json.loads(raw_json)
            if isinstance(data, dict):
                return data
        except Exception:
            pass

    file_path = _env_val('LIVE_EVIDENCE_CHAIN_FILE')
    if file_path:
        try:
            with open(file_path) as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass

    return None


def _validated_live_evidence_chain(chain: Any) -> dict[str, Any] | None:
    """
    Validate that ``chain`` carries a complete live-event proof.

    Requires:
      - evidence_source == 'live'
      - source_type == 'rpc_polling'
      - telemetry_event_id, detection_id, alert_id, evidence_package_id all truthy
      - incident_id or response_action_id truthy

    Returns the normalized chain on success, None otherwise. No IDs are
    invented; missing fields cause rejection rather than substitution.
    """
    if not isinstance(chain, dict):
        return None
    evidence_source = str(chain.get('evidence_source') or '').strip().lower()
    source_type = str(chain.get('source_type') or '').strip().lower()
    if evidence_source != 'live' or source_type != 'rpc_polling':
        return None
    required = ('telemetry_event_id', 'detection_id', 'alert_id', 'evidence_package_id')
    if not all(str(chain.get(k) or '').strip() for k in required):
        return None
    incident = str(chain.get('incident_id') or '').strip()
    response_action = str(chain.get('response_action_id') or '').strip()
    if not incident and not response_action:
        return None
    return chain


def generate_live_evidence_proof(
    *,
    rpc_url_override: str | None = None,
    live_evidence_chain: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Build live provider evidence proof. Always fail-closed.

    Two-tier missing logic:
    - provider_missing: issues that block provider_ready (no URL, RPC error, chain mismatch)
    - evidence_missing: issues that block live_evidence_ready only (no chain ID, worker
      disabled, no matching live telemetry event observed)

    provider_ready = True only when provider_missing is empty and no contradiction_flags.
    live_evidence_ready = True only when provider_ready AND a real live-event
    evidence chain is supplied (no chain IDs are synthesised from eth_chainId or
    eth_blockNumber alone).

    Args:
        rpc_url_override: inject a URL for unit tests only.
        live_evidence_chain: real telemetry chain captured by the monitoring
            worker (telemetry_event_id, detection_id, alert_id,
            incident_id/response_action_id, evidence_package_id; evidence_source
            must be 'live' and source_type 'rpc_polling'). When None, the
            function also looks at the LIVE_EVIDENCE_CHAIN_JSON /
            LIVE_EVIDENCE_CHAIN_FILE env vars. When no valid chain is found and
            RPC is healthy, the proof reports live_provider_ready=True but
            live_evidence_ready=False with the explicit no-live-event reason.
    """
    now = datetime.now(timezone.utc).isoformat()
    provider_missing: list[str] = []   # blocks provider_ready
    evidence_missing: list[str] = []   # blocks live_evidence_ready only
    contradiction_flags: list[str] = []

    # --- Read env vars; prefer STAGING_* over base ---
    staging_rpc = _env_val('STAGING_EVM_RPC_URL')
    base_rpc = _env_val('EVM_RPC_URL')
    effective_rpc = rpc_url_override or (staging_rpc if staging_rpc else base_rpc)

    staging_chain_id = _env_val('STAGING_EVM_CHAIN_ID')
    base_chain_id = _env_val('EVM_CHAIN_ID') or _env_val('CHAIN_ID')
    effective_chain_id_raw = staging_chain_id if staging_chain_id else base_chain_id

    worker_enabled_raw = _env_val('STAGING_WORKER_ENABLED')
    worker_enabled = worker_enabled_raw.lower() in ('1', 'true', 'yes', 'on')

    rpc_ok = bool(effective_rpc) and not _has_placeholder(effective_rpc)
    chain_id_configured = (
        bool(effective_chain_id_raw) and not _has_placeholder(effective_chain_id_raw)
    )
    provider_url_masked = _mask_url(effective_rpc) if effective_rpc else ''

    # --- Provider-level check: RPC URL required ---
    if not rpc_ok:
        provider_missing.append('EVM_RPC_URL or STAGING_EVM_RPC_URL not configured')
        workspace_id_for_lookup = _env_val('WORKSPACE_ID') or _env_val('STAGING_WORKSPACE_ID') or None
        db_telemetry = _query_db_latest_live_telemetry(workspace_id_for_lookup)
        return _build_fail_result(
            now=now,
            provider_ready=False,
            provider_mode='disabled',
            provider_health_checked=False,
            provider_checked_at=None,
            provider_url_masked=provider_url_masked,
            chain_id_configured=chain_id_configured,
            chain_id_observed=None,
            block_number_observed=None,
            worker_enabled=worker_enabled,
            missing=provider_missing + evidence_missing,
            contradiction_flags=contradiction_flags,
            db_telemetry=db_telemetry,
        )

    # --- Evidence-level checks (don't block provider_ready) ---
    if not chain_id_configured:
        evidence_missing.append('EVM_CHAIN_ID or STAGING_EVM_CHAIN_ID not configured')
    if not worker_enabled:
        evidence_missing.append(
            'STAGING_WORKER_ENABLED not set to true; monitoring worker not confirmed'
        )

    # --- Make RPC health calls ---
    check_time = datetime.now(timezone.utc).isoformat()
    chain_id_resp = _rpc_call(effective_rpc, 'eth_chainId')
    block_resp = _rpc_call(effective_rpc, 'eth_blockNumber')

    chain_id_observed: str | None = None
    block_number_observed: str | None = None

    if 'error' in chain_id_resp:
        provider_missing.append(f"provider unreachable: {chain_id_resp['error']}")
        contradiction_flags.append('provider_unreachable')
        return _build_fail_result(
            now=now,
            provider_ready=False,
            provider_mode='disabled',
            provider_health_checked=True,
            provider_checked_at=check_time,
            provider_url_masked=provider_url_masked,
            chain_id_configured=chain_id_configured,
            chain_id_observed=None,
            block_number_observed=None,
            worker_enabled=worker_enabled,
            missing=provider_missing + evidence_missing,
            contradiction_flags=contradiction_flags,
        )

    if 'result' not in chain_id_resp:
        provider_missing.append('eth_chainId returned unexpected response (no result or error key)')
        contradiction_flags.append('provider_bad_response')
        return _build_fail_result(
            now=now,
            provider_ready=False,
            provider_mode='disabled',
            provider_health_checked=True,
            provider_checked_at=check_time,
            provider_url_masked=provider_url_masked,
            chain_id_configured=chain_id_configured,
            chain_id_observed=None,
            block_number_observed=None,
            worker_enabled=worker_enabled,
            missing=provider_missing + evidence_missing,
            contradiction_flags=contradiction_flags,
        )

    chain_id_observed = _hex_to_dec(chain_id_resp['result'])
    if chain_id_observed is None:
        provider_missing.append('eth_chainId returned unreadable value')
        contradiction_flags.append('provider_bad_response')
        return _build_fail_result(
            now=now,
            provider_ready=False,
            provider_mode='disabled',
            provider_health_checked=True,
            provider_checked_at=check_time,
            provider_url_masked=provider_url_masked,
            chain_id_configured=chain_id_configured,
            chain_id_observed=None,
            block_number_observed=None,
            worker_enabled=worker_enabled,
            missing=provider_missing + evidence_missing,
            contradiction_flags=contradiction_flags,
        )

    block_number_hex_form: str | None = block_resp.get('result') if 'result' in block_resp else None
    if block_number_hex_form:
        block_number_observed = _hex_to_dec(block_number_hex_form)

    # Compute raw_rpc_response_hash from the live RPC responses.
    raw_rpc_response_hash = hashlib.sha256(
        json.dumps({
            'chain_id': chain_id_resp.get('result'),
            'block_number': block_number_hex_form,
        }).encode()
    ).hexdigest()[:32]

    # Optionally fetch block detail for a transaction hash (read-only enrichment).
    # Fails gracefully when mock/network does not provide this response.
    tx_hash: str | None = None
    if block_number_hex_form:
        try:
            block_detail_resp = _rpc_call(
                effective_rpc, 'eth_getBlockByNumber', [block_number_hex_form, False]
            )
            if 'result' in block_detail_resp and isinstance(block_detail_resp['result'], dict):
                txs = block_detail_resp['result'].get('transactions') or []
                if txs and isinstance(txs[0], str) and txs[0].startswith('0x'):
                    tx_hash = txs[0]
        except Exception:
            pass

    # --- Chain ID verification (provider-level: mismatch blocks provider_ready) ---
    if chain_id_configured and effective_chain_id_raw and chain_id_observed:
        configured = effective_chain_id_raw.strip()
        if configured != chain_id_observed:
            provider_missing.append(
                f'chain ID mismatch: configured {configured!r} != observed {chain_id_observed!r}'
            )
            contradiction_flags.append(
                f'chain_id_mismatch: configured={configured!r} observed={chain_id_observed!r}'
            )

    # --- Determine provider_ready (only provider-level issues matter) ---
    provider_ready = not provider_missing and not contradiction_flags

    # --- Provider-level fail: bail out with fail-closed result ---
    if provider_missing or contradiction_flags:
        all_missing = provider_missing + evidence_missing
        return _build_fail_result(
            now=now,
            provider_ready=provider_ready,
            provider_mode='live' if provider_ready else 'disabled',
            provider_health_checked=True,
            provider_checked_at=check_time,
            provider_url_masked=provider_url_masked,
            chain_id_configured=chain_id_configured,
            chain_id_observed=chain_id_observed,
            block_number_observed=block_number_observed,
            worker_enabled=worker_enabled,
            missing=all_missing,
            contradiction_flags=contradiction_flags,
        )

    # --- Look for real live-event evidence; never synthesise from RPC alone ---
    real_chain = _validated_live_evidence_chain(live_evidence_chain)
    if real_chain is None:
        real_chain = _validated_live_evidence_chain(_load_live_evidence_chain_from_env())

    # --- Evidence-level issue: RPC works but no matching live event observed ---
    if real_chain is None:
        evidence_missing.append(NO_LIVE_EVENT_REASON)
        return _build_fail_result(
            now=now,
            provider_ready=provider_ready,
            provider_mode='live',
            provider_health_checked=True,
            provider_checked_at=check_time,
            provider_url_masked=provider_url_masked,
            chain_id_configured=chain_id_configured,
            chain_id_observed=chain_id_observed,
            block_number_observed=block_number_observed,
            worker_enabled=worker_enabled,
            missing=evidence_missing,
            contradiction_flags=contradiction_flags,
        )

    # --- Other evidence-level issues still block live_evidence_ready ---
    if evidence_missing:
        return _build_fail_result(
            now=now,
            provider_ready=provider_ready,
            provider_mode='live',
            provider_health_checked=True,
            provider_checked_at=check_time,
            provider_url_masked=provider_url_masked,
            chain_id_configured=chain_id_configured,
            chain_id_observed=chain_id_observed,
            block_number_observed=block_number_observed,
            worker_enabled=worker_enabled,
            missing=evidence_missing,
            contradiction_flags=contradiction_flags,
        )

    # --- Real live-event evidence: build chain from the supplied real IDs ---
    telemetry_id = str(real_chain['telemetry_event_id'])
    detection_id = str(real_chain['detection_id'])
    alert_id = str(real_chain['alert_id'])
    incident_id = str(real_chain.get('incident_id') or '') or None
    response_action_id = str(real_chain.get('response_action_id') or '') or None
    evidence_package_id = str(real_chain['evidence_package_id'])
    telemetry_ts = str(
        real_chain.get('observed_at')
        or real_chain.get('latest_live_telemetry_at')
        or datetime.now(timezone.utc).isoformat()
    )

    return {
        'schema_version': 1,
        'generated_at': now,
        'live_provider_evidence': {
            'provider_ready': True,
            'provider_mode': 'live',
            'provider_health_checked': True,
            'provider_checked_at': check_time,
            'provider_url_masked': provider_url_masked,
            'chain_id_configured': True,
            'chain_id_observed': chain_id_observed,
            'block_number_observed': block_number_observed,
            'worker_enabled': True,
            'live_provider_ready': True,
            'live_provider_receipt_ready': True,
            'live_telemetry_ready': True,
            'live_detection_ready': True,
            'live_alert_ready': True,
            'live_incident_ready': bool(incident_id or response_action_id),
            'evidence_source': 'live',
            'latest_live_telemetry_at': telemetry_ts,
            'live_evidence_ready': True,
            'chain': {
                'telemetry_event_id': telemetry_id,
                'detection_id': detection_id,
                'alert_id': alert_id,
                'incident_id': incident_id,
                'response_action_id': response_action_id,
                'evidence_package_id': evidence_package_id,
            },
            'telemetry_record': {
                'telemetry_event_id': telemetry_id,
                'observed_at': telemetry_ts,
                'evidence_source': 'live',
                'provider_mode': 'live',
                'source_type': 'rpc_polling',
                'chain_id': chain_id_observed,
                'block_number': block_number_observed,
                'raw_rpc_response_hash': raw_rpc_response_hash,
                'transaction_hash': real_chain.get('transaction_hash') or tx_hash,
                'workspace_id': real_chain.get('workspace_id'),
                'target_id': real_chain.get('target_id'),
                'asset_id': real_chain.get('asset_id'),
            },
            'detection_record': {
                'detection_id': detection_id,
                'detection_name': real_chain.get('detection_name') or 'live_rpc_event_observed',
                'telemetry_event_id': telemetry_id,
                'observed_at': telemetry_ts,
                'evidence_source': 'live',
                'source_type': 'rpc_polling',
                'severity': real_chain.get('severity') or 'informational',
                'confidence': real_chain.get('confidence') or 'high',
            },
            'alert_record': {
                'alert_id': alert_id,
                'detection_id': detection_id,
                'observed_at': telemetry_ts,
                'evidence_source': 'live',
            },
            'incident_record': {
                'incident_id': incident_id,
                'alert_id': alert_id,
                'observed_at': telemetry_ts,
                'evidence_source': 'live',
            },
            'response_action_record': {
                'response_action_id': response_action_id,
                'alert_id': alert_id,
                'observed_at': telemetry_ts,
                'evidence_source': 'live',
            },
            'evidence_package_record': {
                'evidence_package_id': evidence_package_id,
                'telemetry_event_id': telemetry_id,
                'detection_id': detection_id,
                'alert_id': alert_id,
                'incident_id': incident_id,
                'response_action_id': response_action_id,
                'evidence_source': 'live',
                'provider_mode': 'live',
                'source_type': 'rpc_polling',
                'provider_url_masked': provider_url_masked,
                'chain_id': chain_id_observed,
                'block_number': block_number_observed,
                'raw_rpc_response_hash': raw_rpc_response_hash,
                'exported_at': telemetry_ts,
            },
            'missing': [],
            'contradiction_flags': [],
        },
    }


def _build_fail_result(
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
    worker_enabled: bool,
    missing: list[str],
    contradiction_flags: list[str],
    db_telemetry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # live_provider_ready: RPC responded and we have block data.
    receipt_present = block_number_observed is not None
    live_provider_ready = provider_ready and receipt_present
    db = db_telemetry or {}
    db_telemetry_at = db.get('observed_at')
    # live_telemetry_ready from DB: proves the worker DID push live telemetry
    # rows to the DB, even when RPC URL is not configured in this environment.
    # Does NOT imply live_evidence_ready (detection→alert→incident chain absent).
    db_telemetry_ready = bool(db_telemetry_at and int(db.get('row_count') or 0) > 0)
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
            'worker_enabled': worker_enabled,
            'live_provider_ready': live_provider_ready,
            'live_provider_receipt_ready': receipt_present,
            'live_telemetry_ready': db_telemetry_ready,
            'live_detection_ready': False,
            'live_alert_ready': False,
            'live_incident_ready': False,
            'evidence_source': 'live' if db_telemetry_ready else 'unknown',
            'latest_live_telemetry_at': db_telemetry_at,
            'live_evidence_ready': False,
            'chain': _empty_chain(),
            'missing': missing,
            'contradiction_flags': contradiction_flags,
        },
    }


def main(strict: bool = False) -> int:
    print('[generate-live-evidence-proof] Reading provider env vars...')

    result = generate_live_evidence_proof()

    out_dir = REPO_ROOT / 'artifacts' / 'live-evidence-proof' / 'latest'
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / 'summary.json'

    with open(out_path, 'w') as f:
        json.dump(result, f, indent=2)
    print(f'[generate-live-evidence-proof] wrote {out_path.relative_to(REPO_ROOT)}')

    lpe = result.get('live_provider_evidence', {})

    for field in (
        'chain_id_configured', 'chain_id_observed', 'worker_enabled',
        'provider_health_checked', 'provider_ready', 'provider_mode',
        'evidence_source', 'latest_live_telemetry_at', 'live_evidence_ready',
    ):
        print(f'[generate-live-evidence-proof] {field}={lpe.get(field)}')

    if lpe.get('missing'):
        print('[generate-live-evidence-proof] Missing:')
        for item in lpe['missing']:
            print(f'  - {item}')

    if lpe.get('contradiction_flags'):
        print('[generate-live-evidence-proof] Contradiction flags:')
        for flag in lpe['contradiction_flags']:
            print(f'  - {flag}')

    if lpe.get('live_evidence_ready'):
        chain = lpe.get('chain', {})
        print('[generate-live-evidence-proof] Live evidence chain:')
        for key, val in chain.items():
            if val:
                print(f'  {key}: {val}')

    if strict and not lpe.get('live_evidence_ready'):
        print('[generate-live-evidence-proof] FAIL: live_evidence_ready=false in strict mode')
        return 1

    return 0


if __name__ == '__main__':
    strict = '--strict' in sys.argv[1:]
    raise SystemExit(main(strict=strict))
