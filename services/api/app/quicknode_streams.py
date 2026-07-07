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

import gzip
import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime, timezone
from os import getenv
from typing import Any

from fastapi import HTTPException, status

from services.api.app.evm_activity_provider import resolve_monitored_wallet
from services.api.app.pilot import ensure_pilot_schema, pg_connection

logger = logging.getLogger(__name__)
# These quicknode_stream_* lines are mandatory operational evidence: the
# product requires every QuickNode POST to be provable from Railway logs, so
# pin this module to INFO even if a global LOG_LEVEL=WARNING is configured.
# The lines only ever contain sizes, counts, opaque UUIDs, and booleans — never
# payload bodies or secrets — so this cannot leak customer data at INFO.
logger.setLevel(logging.INFO)

# Bumped whenever this webhook's ingestion or diagnostic-logging contract
# changes, so a running deployment can be matched to source from the
# quicknode_streams_webhook_version=... startup log alone (emitted by
# services/api/app/main.py). Lets an operator confirm the deployed API commit
# actually includes this code without shell access to the container.
QUICKNODE_STREAMS_WEBHOOK_VERSION = '2026-07-07-quicknode-stream-signature-failed-401-v3'

BASE_CHAIN_ID = 8453
BASE_CHAIN_NETWORK = 'base'
QUICKNODE_STREAM_SOURCE = 'quicknode_stream'

# QuickNode Streams signs nonce + timestamp + raw payload bytes with
# HMAC-SHA256 (hex digest) keyed by the Stream's security token, delivered
# via these three headers. See:
# https://www.quicknode.com/guides/quicknode-products/streams/validating-incoming-streams-webhook-messages
QUICKNODE_NONCE_HEADER = 'x-qn-nonce'
QUICKNODE_TIMESTAMP_HEADER = 'x-qn-timestamp'
QUICKNODE_SIGNATURE_HEADER = 'x-qn-signature'

# QuickNode's docs don't publish a fixed tolerance; this bounds replay of a
# captured (nonce, timestamp, signature, body) tuple to a configurable window.
DEFAULT_QUICKNODE_TIMESTAMP_TOLERANCE_SECONDS = 300


def _quicknode_timestamp_tolerance_seconds() -> int:
    raw = (getenv('QUICKNODE_STREAMS_TIMESTAMP_TOLERANCE_SECONDS') or '').strip()
    if not raw:
        return DEFAULT_QUICKNODE_TIMESTAMP_TOLERANCE_SECONDS
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_QUICKNODE_TIMESTAMP_TOLERANCE_SECONDS


def _log_signature_failed(reason: str) -> None:
    """Record why a QuickNode Streams request failed verification.

    Emitted (at WARNING, so it survives a global LOG_LEVEL=WARNING) on every
    rejection path before the HTTPException is raised, so a rejected QuickNode
    POST is always provable *and* diagnosable from Railway logs — the mirror of
    the ``quicknode_stream_signature_valid`` success marker. Only a stable
    ``reason`` token is logged; never the secret, signature, nonce, timestamp,
    or body, so this cannot leak credentials or payloads.
    """
    logger.warning('quicknode_stream_signature_failed reason=%s', reason)


def _check_quicknode_timestamp_freshness(timestamp_raw: str) -> None:
    try:
        ts = float(timestamp_raw)
    except ValueError as exc:
        _log_signature_failed('invalid_timestamp')
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid QuickNode Streams timestamp header.') from exc
    if ts > 10 ** 12:  # tolerate milliseconds in addition to seconds
        ts = ts / 1000.0
    now = datetime.now(timezone.utc).timestamp()
    if abs(now - ts) > _quicknode_timestamp_tolerance_seconds():
        _log_signature_failed('timestamp_out_of_tolerance')
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='QuickNode Streams timestamp outside allowed tolerance.')


def verify_quicknode_stream_signature(
    *,
    raw_body: bytes,
    signature_header: str | None,
    nonce_header: str | None = None,
    timestamp_header: str | None = None,
) -> None:
    """Verify a QuickNode Streams HMAC-SHA256 signature.

    QuickNode signs ``nonce + timestamp + raw_body`` (raw wire bytes, i.e.
    still gzip-compressed if Content-Encoding: gzip was used) with
    HMAC-SHA256 keyed by the Stream's security token, hex-encoded, and
    delivered via the X-QN-Nonce / X-QN-Timestamp / X-QN-Signature headers.

    Fails closed: no configured secret, a missing nonce/timestamp/signature
    header, a stale/future timestamp, or an invalid signature always rejects
    the request, never silently accepts an unverified payload.
    """
    secret = (getenv('QUICKNODE_STREAMS_SECRET') or '').strip()
    if not secret:
        _log_signature_failed('secret_not_configured')
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='QuickNode Streams webhook is not configured (QUICKNODE_STREAMS_SECRET missing).',
        )
    nonce = (nonce_header or '').strip()
    timestamp_raw = (timestamp_header or '').strip()
    signature = (signature_header or '').strip()
    if not nonce or not timestamp_raw or not signature:
        _log_signature_failed('missing_signature_headers')
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Missing QuickNode Streams signature headers (X-QN-Nonce/X-QN-Timestamp/X-QN-Signature).',
        )
    _check_quicknode_timestamp_freshness(timestamp_raw)
    if signature.lower().startswith('sha256='):
        signature = signature[len('sha256='):]
    signing_input = nonce.encode('utf-8') + timestamp_raw.encode('utf-8') + raw_body
    expected = hmac.new(secret.encode('utf-8'), signing_input, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature.strip().lower(), expected.lower()):
        # 401 (not 400, and never a silent 200): the request is well-formed but
        # its signature — the webhook's only credential — did not verify, so this
        # is an authentication failure. Logged as signature_failed first so the
        # rejection is provable from Railway logs.
        _log_signature_failed('signature_mismatch')
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid QuickNode Streams signature.')
    logger.info('quicknode_stream_signature_valid')


def _maybe_gunzip_quicknode_body(raw_body: bytes, content_encoding: str | None) -> bytes:
    """Decompress a gzip-encoded QuickNode Streams body.

    Must only be called *after* signature verification: the signature is
    computed over the raw wire bytes (compressed, when Content-Encoding is
    gzip), not the decompressed payload.
    """
    if (content_encoding or '').strip().lower() != 'gzip':
        return raw_body
    try:
        return gzip.decompress(raw_body)
    except OSError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid gzip-encoded QuickNode Streams payload.') from exc


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


def _describe_payload_shape(body: Any) -> dict[str, Any]:
    """Summarize a decoded QuickNode Streams payload's shape for diagnostics.

    Never includes values, only types/keys/lengths, so this is safe to log at
    INFO in production without leaking payload contents or secrets.
    """
    top_level_type = type(body).__name__
    top_level_keys = sorted(body.keys()) if isinstance(body, dict) else None
    data: Any = body
    if isinstance(body, dict) and 'data' in body:
        data = body['data']
    data_type = type(data).__name__
    data_length = len(data) if isinstance(data, (list, dict)) else None
    first_entry: Any = None
    if isinstance(data, list) and data:
        first_entry = data[0]
    elif isinstance(data, dict):
        first_entry = data
    elif isinstance(body, dict) and 'data' not in body:
        first_entry = body
    first_block_keys = None
    first_tx_keys = None
    first_receipt_keys = None
    if isinstance(first_entry, dict):
        block = first_entry.get('block') if isinstance(first_entry.get('block'), dict) else None
        if block is not None:
            first_block_keys = sorted(block.keys())
            txs = block.get('transactions')
        else:
            txs = first_entry.get('transactions')
        if isinstance(txs, list) and txs and isinstance(txs[0], dict):
            first_tx_keys = sorted(txs[0].keys())
        elif block is None and not isinstance(txs, list):
            # Not block/transactions-shaped — the entry itself is likely a flat tx object.
            first_tx_keys = sorted(first_entry.keys())
        receipts = first_entry.get('receipts')
        if isinstance(receipts, list) and receipts and isinstance(receipts[0], dict):
            first_receipt_keys = sorted(receipts[0].keys())
    return {
        'top_level_type': top_level_type,
        'top_level_keys': top_level_keys,
        'data_type': data_type,
        'data_length': data_length,
        'first_block_keys': first_block_keys,
        'first_tx_keys': first_tx_keys,
        'first_receipt_keys': first_receipt_keys,
    }


def _log_payload_shape(body: Any) -> None:
    shape = _describe_payload_shape(body)
    logger.info(
        'quicknode_stream_payload_shape top_level_type=%s top_level_keys=%s data_type=%s '
        'data_length=%s first_block_keys=%s first_tx_keys=%s first_receipt_keys=%s',
        shape['top_level_type'], shape['top_level_keys'], shape['data_type'], shape['data_length'],
        shape['first_block_keys'], shape['first_tx_keys'], shape['first_receipt_keys'],
    )


def _extract_tx_dicts(body: Any) -> list[dict[str, Any]]:
    """Flatten a QuickNode Streams Base payload into a list of raw tx dicts.

    Accepts a single tx object, a list of tx objects, a ``{"data": [...]}``
    envelope, or block-shaped entries.

    Supports both the real QuickNode "Block with Receipts" dataset shape
    (``{"block": {..., "transactions": [...]}, "receipts": [...]}``, batch
    size 1 -> a one-element top-level list) and the older/simpler shape where
    a ``transactions`` list sits directly on the entry. Either way, the
    block's ``number``/``block_number`` is copied onto each transaction that
    does not already carry one, and matching receipt fields (looked up by
    transaction hash) are merged onto each transaction without overwriting
    fields the transaction already provides.
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
        block = item.get('block') if isinstance(item.get('block'), dict) else None
        transactions = block.get('transactions') if block is not None and isinstance(block.get('transactions'), list) else None
        if transactions is None and isinstance(item.get('transactions'), list):
            transactions = item['transactions']
            block = block or item
        if transactions is not None:
            block_source = block or {}
            block_number = block_source.get('number') or block_source.get('block_number') or block_source.get('blockNumber')
            receipts = item.get('receipts') if isinstance(item.get('receipts'), list) else []
            receipts_by_hash = {
                str(r.get('transactionHash') or r.get('transaction_hash') or '').lower(): r
                for r in receipts if isinstance(r, dict)
            }
            for tx in transactions:
                if not isinstance(tx, dict):
                    continue
                merged = dict(tx)
                merged.setdefault('block_number', block_number)
                receipt = receipts_by_hash.get(str(tx.get('hash') or tx.get('transactionHash') or '').lower())
                if receipt is not None:
                    merged.setdefault('status', receipt.get('status'))
                    merged.setdefault('gas_used', receipt.get('gasUsed'))
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


def _load_all_base_wallet_targets(connection: Any) -> list[dict[str, Any]]:
    """Load every active Base wallet target once per payload and log the result.

    Loaded once per webhook call (not once per transaction) since a single
    shared QuickNode Streams webhook covers every workspace's Base wallets —
    the same active-target set is checked against every transaction in the
    payload. Logs target_ids (opaque UUIDs), never secrets.
    """
    rows = connection.execute(_BASE_WALLET_TARGETS_SQL).fetchall()
    targets = [dict(row) for row in rows]
    monitored_wallets_count = sum(1 for target in targets if resolve_monitored_wallet(target) is not None)
    target_ids = [target.get('id') for target in targets]
    logger.info(
        'quicknode_stream_targets_loaded count=%s monitored_wallets_count=%s target_ids=%s',
        len(targets), monitored_wallets_count, target_ids,
    )
    if not targets:
        logger.info('quicknode_stream_no_targets_loaded')
    return targets


def _match_targets_for_tx(
    targets: list[dict[str, Any]], *, from_address: str, to_address: str | None,
) -> list[dict[str, Any]]:
    addresses = {a for a in (from_address, to_address) if a}
    if not addresses:
        return []
    matched: list[dict[str, Any]] = []
    for target in targets:
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


def process_quicknode_base_stream_webhook(
    *,
    raw_body: bytes,
    signature_header: str | None,
    nonce_header: str | None = None,
    timestamp_header: str | None = None,
    content_encoding: str | None = None,
) -> dict[str, Any]:
    """Verify, parse, match, and persist a QuickNode Streams Base webhook payload."""
    # First handler line, logged *before* signature verification so a handler
    # entry is provable from logs even when verification then rejects the
    # request (missing/invalid signature, stale timestamp). Only sizes and
    # header-presence booleans — never the body or any secret.
    logger.info(
        'quicknode_stream_handler_started raw_body_bytes=%s content_encoding=%s '
        'has_signature=%s has_nonce=%s has_timestamp=%s',
        len(raw_body),
        (content_encoding or '').strip().lower() or None,
        bool((signature_header or '').strip()),
        bool((nonce_header or '').strip()),
        bool((timestamp_header or '').strip()),
    )
    verify_quicknode_stream_signature(
        raw_body=raw_body,
        signature_header=signature_header,
        nonce_header=nonce_header,
        timestamp_header=timestamp_header,
    )
    body_bytes = _maybe_gunzip_quicknode_body(raw_body, content_encoding)
    try:
        body = json.loads(body_bytes.decode('utf-8') or '{}')
    except (UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid JSON payload.') from exc
    logger.info(
        'quicknode_stream_payload_parsed decoded_bytes=%s decoded_type=%s',
        len(body_bytes), type(body).__name__,
    )
    _log_payload_shape(body)

    raw_txs = _extract_tx_dicts(body)
    normalized_txs: list[dict[str, Any]] = []
    for raw_tx in raw_txs:
        normalized = normalize_base_stream_tx(raw_tx)
        if normalized is not None:
            normalized_txs.append(normalized)

    sample = normalized_txs[0] if normalized_txs else {}
    logger.info(
        'quicknode_stream_transactions_normalized count=%s sample_tx_hash_present=%s '
        'sample_from_present=%s sample_to_present=%s sample_value_present=%s '
        'sample_block_number_present=%s',
        len(normalized_txs),
        bool(sample.get('tx_hash')),
        bool(sample.get('from_address')),
        bool(sample.get('to_address')),
        sample.get('value') is not None,
        sample.get('block_number') is not None,
    )
    if not normalized_txs:
        reason = 'no_raw_transactions_extracted' if not raw_txs else 'raw_transactions_missing_required_fields'
        logger.info('quicknode_stream_no_transactions_normalized reason=%s', reason)
        return {'received': True, 'results': []}

    results: list[dict[str, Any]] = []
    match_count = 0
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        targets = _load_all_base_wallet_targets(connection)
        for normalized in normalized_txs:
            matched_targets = _match_targets_for_tx(
                targets, from_address=normalized['from_address'], to_address=normalized['to_address'],
            )
            if not matched_targets:
                results.append({'tx_hash': normalized['tx_hash'], 'status': 'no_match'})
                continue
            for target in matched_targets:
                match_count += 1
                target_wallet = resolve_monitored_wallet(target)
                from_match = target_wallet == normalized['from_address']
                to_match = target_wallet is not None and target_wallet == normalized.get('to_address')
                logger.info(
                    'quicknode_stream_wallet_match tx_hash=%s target_id=%s from_match=%s to_match=%s',
                    normalized['tx_hash'], target['id'], from_match, to_match,
                )
                outcome = _persist_quicknode_wallet_transfer(connection, target=target, tx=normalized)
                if outcome['status'] == 'processed':
                    logger.info(
                        'quicknode_stream_event_persisted detected_by=%s tx_hash=%s target_id=%s',
                        QUICKNODE_STREAM_SOURCE, normalized['tx_hash'], target['id'],
                    )
                elif outcome['status'] == 'duplicate_suppressed':
                    logger.info(
                        'quicknode_stream_duplicate_suppressed tx_hash=%s existing_detected_by=%s',
                        normalized['tx_hash'], outcome.get('existing_detected_by'),
                    )
                results.append({'tx_hash': normalized['tx_hash'], 'target_id': target['id'], **outcome})
        if match_count == 0:
            logger.info(
                'quicknode_stream_no_match tx_count=%s target_count=%s',
                len(normalized_txs), len(targets),
            )
    return {'received': True, 'results': results}
