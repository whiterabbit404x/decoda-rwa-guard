"""QuickNode Streams webhook ingestion for Base chain wallet transfers.

Separate push-based detection path, additive to the existing stable RPC
polling worker (services/api/app/monitoring_runner.py) and the optional
realtime WebSocket worker (services/api/app/base_realtime_ingestor.py).
Neither of those is modified or replaced by this module — stable polling
keeps running as the canonical fallback.

Flow: QuickNode Streams posts a signed payload for matched Base activity ->
verify HMAC -> normalize tx fields -> match tx.from/tx.to against every
active Base wallet target's resolved monitored wallet -> persist a
wallet_transfer_detected telemetry row (detected_by=quicknode_stream,
source_type=quicknode_stream), deduped against any existing row for the
same target_id + tx_hash (including rows the stable RPC polling worker
already wrote).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone
from os import getenv
from typing import Any

from fastapi import HTTPException, status

from services.api.app.evm_activity_provider import resolve_monitored_wallet
from services.api.app.pilot import ensure_pilot_schema, pg_connection

BASE_CHAIN_ID = 8453
BASE_CHAIN_NETWORK = 'base'
QUICKNODE_STREAM_SOURCE = 'quicknode_stream'


def verify_quicknode_stream_signature(*, raw_body: bytes, signature_header: str | None) -> None:
    """Verify a QuickNode Streams HMAC-SHA256 signature over the raw request body.

    Fails closed: no configured secret or a missing/invalid signature always
    rejects the request, never silently accepts an unverified payload.
    """
    secret = (getenv('QUICKNODE_STREAMS_SECRET') or '').strip()
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='QuickNode Streams webhook is not configured (QUICKNODE_STREAMS_SECRET missing).',
        )
    if not signature_header or not signature_header.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Missing QuickNode Streams signature header.')
    provided = signature_header.strip()
    if provided.lower().startswith('sha256='):
        provided = provided[len('sha256='):]
    expected = hmac.new(secret.encode('utf-8'), raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(provided.strip().lower(), expected.lower()):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid QuickNode Streams signature.')


def _hex_or_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text, 16) if text.lower().startswith('0x') else int(text)
    except ValueError:
        return None


def _extract_tx_dicts(body: Any) -> list[dict[str, Any]]:
    """Flatten a QuickNode Streams Base payload into a list of raw tx dicts.

    Accepts a single tx object, a list of tx objects, a ``{"data": [...]}``
    envelope, or block-shaped entries carrying a ``transactions`` list (the
    block's ``number``/``block_number`` is copied onto each transaction that
    does not already carry one).
    """
    if isinstance(body, dict):
        if isinstance(body.get('data'), list):
            body = body['data']
        elif isinstance(body.get('data'), dict):
            body = [body['data']]
        else:
            body = [body]
    if not isinstance(body, list):
        return []
    out: list[dict[str, Any]] = []
    for item in body:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get('transactions'), list):
            block = item.get('block') if isinstance(item.get('block'), dict) else item
            block_number = block.get('number') or block.get('block_number') or block.get('blockNumber')
            for tx in item['transactions']:
                if isinstance(tx, dict):
                    merged = dict(tx)
                    merged.setdefault('block_number', block_number)
                    out.append(merged)
        else:
            out.append(item)
    return out


def normalize_base_stream_tx(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize a raw QuickNode Streams tx dict into canonical fields.

    Returns ``None`` when the entry lacks the minimum required facts
    (tx_hash and a from-address) — such entries are skipped rather than
    persisted with guessed data.
    """
    tx_hash = str(raw.get('tx_hash') or raw.get('hash') or raw.get('transactionHash') or '').strip().lower()
    from_address = str(raw.get('from_address') or raw.get('from') or raw.get('fromAddress') or '').strip().lower()
    to_address = str(raw.get('to_address') or raw.get('to') or raw.get('toAddress') or '').strip().lower()
    if not tx_hash or not from_address:
        return None
    value = _hex_or_int(raw.get('value') if raw.get('value') is not None else raw.get('value_wei'))
    block_number = _hex_or_int(raw.get('block_number') or raw.get('blockNumber') or raw.get('blockNum'))
    chain_id = _hex_or_int(raw.get('chain_id') or raw.get('chainId')) or BASE_CHAIN_ID
    return {
        'tx_hash': tx_hash,
        'from_address': from_address,
        'to_address': to_address or None,
        'value': value if value is not None else 0,
        'block_number': block_number,
        'chain_id': chain_id,
    }


# Same target-loading query as base_realtime_ingestor._watched_targets, scoped
# to Base wallet targets. Intentionally unscoped by workspace: a single shared
# QuickNode Streams webhook covers every workspace's Base wallets, so matching
# must check every active target the same way the realtime worker does.
_BASE_WALLET_TARGETS_SQL = """
SELECT id, workspace_id, name, target_type, chain_network, chain_id,
       wallet_address, contract_identifier, asset_id, target_metadata,
       monitoring_enabled, enabled, is_active
FROM targets
WHERE deleted_at IS NULL
  AND target_type = 'wallet'
  AND monitoring_enabled = TRUE
  AND enabled = TRUE
  AND is_active = TRUE
  AND (
    LOWER(COALESCE(chain_network, 'base')) IN ('base', 'base-mainnet')
    OR chain_id = 8453
  )
"""


def _find_matching_base_targets(connection: Any, *, from_address: str, to_address: str | None) -> list[dict[str, Any]]:
    addresses = {a for a in (from_address, to_address) if a}
    if not addresses:
        return []
    rows = connection.execute(_BASE_WALLET_TARGETS_SQL).fetchall()
    matched: list[dict[str, Any]] = []
    for row in rows:
        target = dict(row)
        wallet = resolve_monitored_wallet(target)
        if wallet and wallet in addresses:
            matched.append(target)
    return matched


def _existing_telemetry_for_tx(connection: Any, *, target_id: str, tx_hash: str) -> dict[str, Any] | None:
    """Any existing telemetry row for this target + tx_hash, regardless of who wrote it.

    Checked before insert so a transfer the stable RPC polling worker already
    detected (or a QuickNode Streams retry of the same event) is reported as a
    duplicate instead of creating a second customer-visible row.
    """
    row = connection.execute(
        '''
        SELECT id, event_type, payload_json->>'detected_by' AS detected_by
        FROM telemetry_events
        WHERE target_id = %s AND lower(payload_json->>'tx_hash') = lower(%s)
        LIMIT 1
        ''',
        (target_id, tx_hash),
    ).fetchone()
    return dict(row) if row is not None else None


def _persist_quicknode_wallet_transfer(connection: Any, *, target: dict[str, Any], tx: dict[str, Any]) -> dict[str, Any]:
    existing = _existing_telemetry_for_tx(connection, target_id=target['id'], tx_hash=tx['tx_hash'])
    if existing is not None:
        return {
            'status': 'duplicate_suppressed',
            'existing_detected_by': existing.get('detected_by'),
        }
    target_wallet = resolve_monitored_wallet(target)
    direction = 'outbound' if target_wallet == tx['from_address'] else 'inbound'
    value = tx.get('value') or 0
    observed_at = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        'chain_id': tx.get('chain_id') or BASE_CHAIN_ID,
        'chain_network': BASE_CHAIN_NETWORK,
        'block_number': tx.get('block_number'),
        'tx_hash': tx['tx_hash'],
        'from': tx['from_address'],
        'to': tx.get('to_address'),
        'from_address': tx['from_address'],
        'to_address': tx.get('to_address'),
        'amount': str(value),
        'value_wei': value,
        'value_eth': round(value / 10 ** 18, 18),
        'wallet_transfer_direction': direction,
        'event_type': 'wallet_transfer_detected',
        'source_type': QUICKNODE_STREAM_SOURCE,
        'detected_by': QUICKNODE_STREAM_SOURCE,
        'observed_at': observed_at.isoformat(),
    }
    telemetry_id = str(uuid.uuid4())
    idempotency_key = f"{target['workspace_id']}:{target['id']}:{tx['tx_hash']}"
    payload_json = json.dumps(payload, sort_keys=True, default=str)
    payload_hash = hashlib.sha256(payload_json.encode('utf-8')).hexdigest()
    connection.execute(
        '''
        INSERT INTO telemetry_events (
            id, workspace_id, asset_id, target_id, provider_type, event_type, observed_at, evidence_source, payload_hash, payload_json, idempotency_key
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
        ON CONFLICT (workspace_id, target_id, idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING
        ''',
        (
            telemetry_id,
            target['workspace_id'],
            target.get('asset_id'),
            target['id'],
            QUICKNODE_STREAM_SOURCE,
            'wallet_transfer_detected',
            observed_at,
            'live',
            payload_hash,
            payload_json,
            idempotency_key,
        ),
    )
    connection.commit()
    return {
        'status': 'processed',
        'telemetry_id': telemetry_id,
        'detected_by': QUICKNODE_STREAM_SOURCE,
        'wallet_transfer_direction': direction,
    }


def process_quicknode_base_stream_webhook(*, raw_body: bytes, signature_header: str | None) -> dict[str, Any]:
    """Verify, parse, match, and persist a QuickNode Streams Base webhook payload."""
    verify_quicknode_stream_signature(raw_body=raw_body, signature_header=signature_header)
    try:
        body = json.loads(raw_body.decode('utf-8') or '{}')
    except (UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid JSON payload.') from exc
    raw_txs = _extract_tx_dicts(body)
    results: list[dict[str, Any]] = []
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        for raw_tx in raw_txs:
            normalized = normalize_base_stream_tx(raw_tx)
            if normalized is None:
                continue
            targets = _find_matching_base_targets(
                connection, from_address=normalized['from_address'], to_address=normalized['to_address'],
            )
            if not targets:
                results.append({'tx_hash': normalized['tx_hash'], 'status': 'no_match'})
                continue
            for target in targets:
                outcome = _persist_quicknode_wallet_transfer(connection, target=target, tx=normalized)
                results.append({'tx_hash': normalized['tx_hash'], 'target_id': target['id'], **outcome})
    return {'received': True, 'results': results}
