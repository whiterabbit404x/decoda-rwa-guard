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
source_type=quicknode_stream), deduped against any existing wallet-transfer
row for the same target_id + chain_id + tx_hash + event_type — regardless of
detected_by, so a transfer the stable RPC polling worker already wrote (as
either native_transfer or wallet_transfer_detected) suppresses it.
"""
from __future__ import annotations

import gzip
import hashlib
import hmac
import json
import logging
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone
from os import getenv
from typing import Any

import psycopg
from fastapi import HTTPException, status

from services.api.app.evm_activity_provider import resolve_monitored_wallet
# Reuse the exact asset-context loader the stable RPC polling worker uses to
# resolve a wallet target whose monitored address lives on the linked asset
# (monitoring_runner.process_monitoring_target). Importing it here keeps QuickNode's
# wallet resolution byte-for-byte consistent with stable polling instead of
# forking a second resolver that could drift. No import cycle: monitoring_runner
# does not import quicknode_streams (only a comment in worker_status references it).
from services.api.app.monitoring_runner import (
    _load_target_asset_context,
    _strategic_infrastructure_guard_alert,
    _wallet_transfer_smoke_alert,
)
from services.api.app.pilot import ensure_pilot_schema, pg_connection
from services.api.app import telemetry_realtime
from services.api.app.worker_status import TRANSFER_FAMILY_EVENT_TYPES

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
QUICKNODE_STREAMS_WEBHOOK_VERSION = '2026-07-08-quicknode-stream-debug-tx-trace-v5'

BASE_CHAIN_ID = 8453
BASE_CHAIN_NETWORK = 'base'
QUICKNODE_STREAM_SOURCE = 'quicknode_stream'
# detected_by tags for the two RPC-backed recovery paths that reuse the QuickNode
# matcher/dedupe/persist logic but did NOT arrive on the live QuickNode stream:
#   * quicknode_stream_backfill — automatic gap backfill when the stream skips
#     blocks (a jump from block A to B where B > A + 1). Fetched from Base RPC.
#   * quicknode_stream_debug_import — a deliberate one-off import via the ops
#     debug-tx endpoint (dry_run=false). Also fetched from Base RPC.
# Both are deduped against every other transfer-family row for the same
# target + chain + tx_hash (see _existing_telemetry_for_tx), so they can never
# create a second customer-visible row for a transfer the stream, the realtime
# worker, or stable RPC polling already recorded.
QUICKNODE_STREAM_BACKFILL_SOURCE = 'quicknode_stream_backfill'
QUICKNODE_STREAM_DEBUG_IMPORT_SOURCE = 'quicknode_stream_debug_import'

# Single logical QuickNode Stream this API ingests (Base wallet transfers). The
# checkpoint table is keyed by this so a future second stream (another chain)
# gets its own gap tracking without colliding. This checkpoint tracks the
# webhook's *delivery* high-water mark (whatever blocks QuickNode pushes, in the
# order it pushes them) and is what the historical gap-backfill uses.
QUICKNODE_STREAM_KEY_BASE = 'base'

# Two SEPARATE checkpoint identities for the real-time fix. The production
# incident was a single lane that replays blocks sequentially from an old
# ``stream_started_at_block`` (48391739) and is tens of thousands of blocks
# behind the chain tip, so a freshly-confirmed tx is ``stream_not_at_block_yet``
# for a long time and only Stable RPC Polling catches it. The live lane is
# decoupled from that backlog:
#
#   * LIVE ('quicknode:base:live')     — a chain-tip consumer that begins at the
#     current safe head and only ever moves forward at the tip. Its checkpoint is
#     the lag reference (chain_head - live_checkpoint). It is NEVER overwritten by
#     the backfill lane, so historical catch-up cannot drag the live cursor
#     backwards or make the UI claim the provider is behind when it is at the tip.
#   * BACKFILL ('quicknode:base:backfill') — the lower-priority lane that walks the
#     missed historical range separately, deduped against whatever the live lane
#     and Stable RPC Polling already recorded.
QUICKNODE_STREAM_KEY_BASE_LIVE = 'quicknode:base:live'
QUICKNODE_STREAM_KEY_BASE_BACKFILL = 'quicknode:base:backfill'

# Confirmation offset applied to the chain head before the live lane treats a
# block as "safe" to process, so a reorg near the tip cannot persist a transfer
# that later disappears. Small by design — Base has fast, deep finality — so the
# live lane still lands a confirmed tx within a few seconds.
DEFAULT_QUICKNODE_LIVE_CONFIRMATIONS = 2

# Upper bound on how many blocks the live lane processes per tick, so a tick can
# never turn into an unbounded scan if the consumer briefly falls behind; the
# remaining blocks are picked up on the next tick (still at the tip).
DEFAULT_QUICKNODE_LIVE_MAX_BLOCKS_PER_TICK = 25

# Bounded batch for one historical backfill step (lower priority than live).
DEFAULT_QUICKNODE_BACKFILL_MAX_BLOCKS_PER_TICK = 50

# Lag (chain_head - live_checkpoint) at or below which the live lane is reported
# "Live"; above it the lane is "Degraded" and Stable RPC Polling is the fallback.
DEFAULT_QUICKNODE_LIVE_LAG_THRESHOLD_BLOCKS = 10

# No advance of the live checkpoint for longer than this marks the lane "Stale".
DEFAULT_QUICKNODE_LIVE_STALE_SECONDS = 300

# Upper bound on how many missing blocks a single gap-triggered backfill fetches
# inline from RPC, so a one-time huge jump (e.g. the stream (re)starting tens of
# thousands of blocks ahead of a missed tx) cannot turn one webhook into an
# unbounded RPC scan. The checkpoint still advances past the whole gap, so the
# same gap is never re-detected; blocks beyond the cap are reported as
# not-backfilled and remain recoverable via stable RPC polling or the debug-tx
# endpoint. 0 disables the cap.
DEFAULT_QUICKNODE_STREAM_BACKFILL_MAX_BLOCKS = 200

# Cap on per-tx quicknode_stream_no_match_detail lines emitted for a single
# payload, so a misconfigured/unfiltered stream (a whole block of unrelated
# transactions) cannot flood Railway logs. The aggregate quicknode_stream_no_match
# line is always emitted regardless of this cap.
_NO_MATCH_DETAIL_LOG_LIMIT = 25

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


# ---------------------------------------------------------------------------
# Debug-tx tracing (QUICKNODE_STREAM_DEBUG_TX_HASH / QUICKNODE_STREAM_DEBUG_TX_BLOCK)
#
# Operational instrumentation for the "QuickNode Stream missed a fresh tx"
# investigation. When a specific tx hash is configured, every QuickNode batch is
# traced for it so an operator can prove, from Railway logs alone, which of these
# happened for that tx:
#   A) QuickNode never delivered the block/tx  — no batch_range ever covers its block.
#   B) QuickNode delivered the block but the normalizer/filter dropped the tx —
#      a batch_range covers its block yet quicknode_stream_debug_tx_not_seen fires.
#   C) QuickNode delivered the tx but the matcher failed —
#      quicknode_stream_debug_tx_seen with from_matches_target/to_matches_target=false.
#   D) QuickNode delivered + matched but duplicate suppression fired —
#      quicknode_stream_debug_tx_seen with duplicate_found=true, persisted=false.
# The tx from/to and block number logged here are public on-chain facts; the
# monitored wallet is only ever emitted as its last 4 chars, never in full.
# ---------------------------------------------------------------------------


def _debug_tx_hashes() -> set[str]:
    """Configured debug tx hashes (QUICKNODE_STREAM_DEBUG_TX_HASH), lowercased.

    Accepts one hash or a comma-separated list; an empty set when unset, so the
    debug tracing is inert (and cost-free beyond one batch_range line) in every
    normal deployment.
    """
    raw = (getenv('QUICKNODE_STREAM_DEBUG_TX_HASH') or '').strip()
    if not raw:
        return set()
    return {part.strip().lower() for part in raw.split(',') if part.strip()}


def _debug_tx_block_number() -> int | None:
    """Optional known block number of the debug tx (QUICKNODE_STREAM_DEBUG_TX_BLOCK).

    Accepts decimal or 0x-hex. When set, it enables the
    ``quicknode_stream_debug_tx_not_seen`` check: a batch whose block range covers
    this block but does NOT contain the debug tx hash proves QuickNode delivered the
    block *without* the tx (normalizer/filter miss, case B) rather than never
    delivering it (case A). Unset -> the not_seen check is skipped, since a batch that
    does not contain the tx cannot reveal the tx's block number on its own.
    """
    return _hex_or_int((getenv('QUICKNODE_STREAM_DEBUG_TX_BLOCK') or '').strip() or None)


def _log_batch_range_and_debug_tx(normalized_txs: list[dict[str, Any]]) -> None:
    """Log the block range of a QuickNode batch and whether it carries a debug tx.

    Emitted once per payload (task requirement 2: ``quicknode_stream_batch_range``
    with first_block / last_block / tx_count / contains_debug_tx). When a debug tx
    hash is configured, its block is known (QUICKNODE_STREAM_DEBUG_TX_BLOCK), and a
    batch whose range covers that block does not contain the tx, also emits
    ``quicknode_stream_debug_tx_not_seen`` (task requirement 4) — the proof that the
    block was delivered without the tx. Counts and public block numbers only.
    """
    block_numbers = [t['block_number'] for t in normalized_txs if t.get('block_number') is not None]
    first_block = min(block_numbers) if block_numbers else None
    last_block = max(block_numbers) if block_numbers else None
    debug_hashes = _debug_tx_hashes()
    seen_hashes = {t['tx_hash'] for t in normalized_txs}
    contains_debug_tx = bool(debug_hashes & seen_hashes)
    logger.info(
        'quicknode_stream_batch_range first_block=%s last_block=%s tx_count=%s contains_debug_tx=%s',
        first_block if first_block is not None else 'none',
        last_block if last_block is not None else 'none',
        len(normalized_txs),
        str(contains_debug_tx).lower(),
    )
    if not debug_hashes:
        return
    debug_block = _debug_tx_block_number()
    if debug_block is None or first_block is None or last_block is None:
        return
    if not (first_block <= debug_block <= last_block):
        return
    for debug_hash in sorted(debug_hashes):
        if debug_hash not in seen_hashes:
            logger.info(
                'quicknode_stream_debug_tx_not_seen tx_hash=%s tx_block_number=%s '
                'batch_first_block=%s batch_last_block=%s',
                debug_hash, debug_block, first_block, last_block,
            )


def _log_debug_tx_seen(
    *,
    normalized: dict[str, Any],
    target_wallet: str | None,
    from_matches_target: bool,
    to_matches_target: bool,
    duplicate_found: bool,
    persisted: bool,
) -> None:
    """Emit ``quicknode_stream_debug_tx_seen`` for a configured debug tx (task req 3).

    Logs the full per-tx / per-target trace an operator needs to classify the miss:
    the tx's public from/to and block number, whether the tx matched the monitored
    wallet (last4 only, never the full address), whether a duplicate already existed,
    and whether this path persisted a row. Emitted once per matched target, and once
    with wallet fields cleared when the debug tx matched no monitored wallet.
    """
    logger.info(
        'quicknode_stream_debug_tx_seen tx_hash=%s block_number=%s from=%s to=%s '
        'target_wallet_last4=%s from_matches_target=%s to_matches_target=%s '
        'duplicate_found=%s persisted=%s',
        normalized['tx_hash'],
        normalized.get('block_number') if normalized.get('block_number') is not None else 'none',
        normalized['from_address'],
        normalized.get('to_address') or 'none',
        target_wallet[-4:] if target_wallet else 'none',
        str(from_matches_target).lower(),
        str(to_matches_target).lower(),
        str(duplicate_found).lower(),
        str(persisted).lower(),
    )


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


def _iter_stream_entries(body: Any) -> Iterator[dict[str, Any]]:
    """Yield candidate stream-entry dicts from an arbitrarily list-nested body.

    QuickNode Streams delivers either the dataset output array directly, or a
    ``{"data": [...], "metadata": {...}}`` envelope; with some batch/filter
    configurations ``data`` is nested one extra level (a list of lists). Walk
    lists to any depth and unwrap a single ``data`` envelope, yielding each dict
    entry, so a nested batch shape still produces transactions instead of
    silently normalizing to zero. A dict that is *not* an envelope (no
    list/dict ``data`` key) is yielded as-is, so a flat tx object, a block
    object, or a ``{"block": ..., "receipts": ...}`` entry are all preserved —
    a tx's own ``data``/``input`` calldata (a hex string) is never descended
    into.
    """
    if isinstance(body, dict):
        data = body.get('data')
        if isinstance(data, (list, dict)):
            yield from _iter_stream_entries(data)
            return
        yield body
        return
    if isinstance(body, list):
        for item in body:
            if isinstance(item, (list, dict)):
                yield from _iter_stream_entries(item)
        return


def _extract_tx_dicts(body: Any) -> list[dict[str, Any]]:
    """Flatten a QuickNode Streams Base payload into a list of raw tx dicts.

    Accepts a single tx object, a list of tx objects, a ``{"data": [...]}``
    envelope (including one nested an extra list level), or block-shaped
    entries.

    Supports both the real QuickNode "Block with Receipts" dataset shape
    (``{"block": {..., "transactions": [...]}, "receipts": [...]}``, batch
    size 1 -> a one-element top-level list) and the older/simpler shape where
    a ``transactions`` list sits directly on the entry. Either way, the
    block's ``number``/``block_number`` is copied onto each transaction that
    does not already carry one, and matching receipt fields (looked up by
    transaction hash) are merged onto each transaction without overwriting
    fields the transaction already provides.
    """
    out: list[dict[str, Any]] = []
    for item in _iter_stream_entries(body):
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
#
# monitored_system_id is NOT a column on `targets`: the production schema keeps it
# on `monitored_systems` (keyed by target_id). Selecting it directly off `targets`
# was the deploy regression that crashed this handler with
# ``psycopg.errors.UndefinedColumn: column "monitored_system_id" does not exist``.
# It is derived here via a LEFT JOIN so a target that has no monitored_systems row
# still loads (monitored_system_id = NULL) and still matches wallet transfers — the
# join is never required for matching. monitored_systems carries a
# UNIQUE (workspace_id, target_id) constraint, so the join stays one-to-one (no row
# fan-out) and is scoped to the target's own workspace (no cross-tenant leak).
_BASE_WALLET_TARGETS_SQL = """
SELECT t.id, t.workspace_id, t.name, t.target_type, t.chain_network, t.chain_id,
       t.wallet_address, t.contract_identifier, t.asset_id, t.target_metadata,
       t.monitoring_enabled, t.enabled, t.is_active,
       ms.id AS monitored_system_id, t.updated_by_user_id, t.created_by_user_id
FROM targets t
LEFT JOIN monitored_systems ms
       ON ms.target_id = t.id
      AND ms.workspace_id = t.workspace_id
WHERE t.deleted_at IS NULL
  AND t.target_type = 'wallet'
  AND t.monitoring_enabled = TRUE
  AND t.enabled = TRUE
  AND t.is_active = TRUE
  AND (
    LOWER(COALESCE(t.chain_network, 'base')) IN ('base', 'base-mainnet')
    OR t.chain_id = 8453
  )
"""


def _resolve_target_monitored_wallet(
    connection: Any, target: dict[str, Any],
) -> tuple[str | None, str, str | None]:
    """Resolve a target's monitored wallet from the same sources stable RPC polling uses.

    Returns ``(wallet, source, reason)``:

    * ``wallet``  – lowercase 0x address, or None when none is configured anywhere.
    * ``source``  – ``target_config`` (canonical ``wallet_address``, an address typed
      into ``contract_identifier``, or ``target_metadata``), ``asset`` (resolved only
      after loading the linked asset's context, exactly as the stable RPC polling
      worker does), or ``none``.
    * ``reason``  – set only when ``wallet`` is None, explaining why it is missing.

    QuickNode's target query (:data:`_BASE_WALLET_TARGETS_SQL`) loads the raw target
    row but does NOT build ``asset_context``, so a wallet target whose monitored
    address lives on the linked asset — the canonical RWA configuration — resolved
    to None here until the asset context was loaded. That was the production defect:
    ``monitored_wallets_count=0`` / ``target_wallets=['none']`` even though stable
    polling resolved the very same wallet. This reuses ``resolve_monitored_wallet``
    plus stable polling's ``_load_target_asset_context`` so both paths agree, and
    writes the resolved wallet back onto ``target['wallet_address']`` so the matcher,
    persistence, and diagnostics all observe the same wallet (mirroring
    monitoring_runner writing the fallback wallet back onto the target).
    """
    # 1. Direct resolution from the already-loaded target row (no DB round-trip):
    #    canonical wallet_address, an address typed into contract_identifier, or
    #    target_metadata. This is the fast path for correctly configured targets.
    wallet = resolve_monitored_wallet(target)
    if wallet:
        target['wallet_address'] = wallet
        return wallet, 'target_config', None
    # 2. Fall back to the linked asset's identifier, loading the asset context the
    #    same workspace-scoped way stable RPC polling does. Only attempted for a
    #    target that actually links an asset and has no context attached yet.
    asset_id = target.get('asset_id')
    if asset_id and target.get('asset_context') is None:
        asset_context = _load_target_asset_context(
            connection, workspace_id=str(target.get('workspace_id')), target=target,
        )
        if isinstance(asset_context, dict):
            target['asset_context'] = asset_context
            wallet = resolve_monitored_wallet(target)
            if wallet:
                target['wallet_address'] = wallet
                return wallet, 'asset', None
    # 3. No valid wallet in the target config or on the linked asset. Report a
    #    truthful reason so the miss is diagnosable from logs — never faked.
    reason = 'no_asset_linked' if not asset_id else 'no_wallet_in_target_or_asset'
    return None, 'none', reason


def _load_all_base_wallet_targets(connection: Any) -> list[dict[str, Any]]:
    """Load every active Base wallet target once per payload and log the result.

    Loaded once per webhook call (not once per transaction) since a single
    shared QuickNode Streams webhook covers every workspace's Base wallets —
    the same active-target set is checked against every transaction in the
    payload. Each target's monitored wallet is resolved (and written back onto
    the target) the same way stable RPC polling resolves it, so a wallet stored
    on the linked asset is matched here too. Logs target_ids (opaque UUIDs) and
    per-target resolution facts (wallet last4 only), never full wallets or secrets.
    """
    rows = connection.execute(_BASE_WALLET_TARGETS_SQL).fetchall()
    targets = [dict(row) for row in rows]
    monitored_wallets_count = 0
    for target in targets:
        wallet, source, reason = _resolve_target_monitored_wallet(connection, target)
        if wallet is not None:
            monitored_wallets_count += 1
        # Per-target resolution evidence: proves whether each loaded target
        # produced a monitored wallet and from which source, so a repeat of the
        # monitored_wallets_count=0 incident is diagnosable from Railway logs
        # alone. Only the wallet's last 4 chars are logged, never the full address.
        logger.info(
            'quicknode_stream_target_wallet_resolution target_id=%s asset_id=%s '
            'wallet_present=%s wallet_last4=%s wallet_source=%s reason=%s',
            target.get('id'),
            target.get('asset_id') or 'none',
            str(wallet is not None).lower(),
            wallet[-4:] if wallet else 'none',
            source,
            reason or 'none',
        )
    target_ids = [target.get('id') for target in targets]
    logger.info(
        'quicknode_stream_targets_loaded count=%s monitored_wallets_count=%s target_ids=%s',
        len(targets), monitored_wallets_count, target_ids,
    )
    if not targets:
        logger.info('quicknode_stream_no_targets_loaded')
    return targets


def _wallet_fingerprint(wallet: str | None) -> str:
    """Compact, non-reversible tag for a monitored wallet, safe to log.

    Returns ``<last4>/<hash8>`` where hash8 is the first 8 hex chars of the
    SHA-256 of the lowercased address. Enough to correlate a no-match against a
    specific configured target from Railway logs (task requirement: on no-match
    log "target wallet hash/last4 only, not full secrets") without printing the
    full monitored wallet.
    """
    normalized = (wallet or '').strip().lower()
    if not normalized:
        return 'none'
    last4 = normalized[-4:]
    hash8 = hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:8]
    return f'{last4}/{hash8}'


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


# Wallet-transfer telemetry event types that describe the SAME on-chain transfer
# regardless of which detection path recorded it: stable RPC polling writes
# 'native_transfer' for a plain ETH move and 'wallet_transfer_detected' otherwise,
# while QuickNode Streams always writes 'wallet_transfer_detected'. Deduping across
# this family (and never on detected_by) is what collapses a stable-polling row and
# a QuickNode row for one tx into a single customer-visible event. Aliased to the
# shared worker_status.TRANSFER_FAMILY_EVENT_TYPES so the webhook, the stable-polling
# insert path, and the telemetry list route all dedupe over the identical family
# (which also covers the 'wallet_transfer' / 'eth_transfer' / 'base_native_transfer'
# spellings older writers used).
_WALLET_TRANSFER_EVENT_TYPES = TRANSFER_FAMILY_EVENT_TYPES


def _existing_telemetry_for_tx(
    connection: Any, *, target_id: str, tx_hash: str, chain_id: Any,
) -> dict[str, Any] | None:
    """Any existing wallet-transfer telemetry row for this target + chain + tx_hash.

    Dedupe identity: ``target_id + chain_id + tx_hash + event_type``, matched
    REGARDLESS of ``detected_by`` — so a transfer the stable RPC polling worker
    already recorded (or a QuickNode Streams retry of the same event) is reported as
    a duplicate instead of creating a second customer-visible row. ``event_type`` is
    matched across the wallet-transfer family (:data:`_WALLET_TRANSFER_EVENT_TYPES`)
    so a stable-polling ``native_transfer`` still suppresses a QuickNode
    ``wallet_transfer_detected`` for the identical tx. The chain match tolerates a
    legacy row that never stamped ``chain_id`` in its payload, so scoping by chain
    can never re-open a duplicate the previous target_id + tx_hash dedupe caught.
    """
    row = connection.execute(
        '''
        SELECT id, event_type, payload_json->>'detected_by' AS detected_by
        FROM telemetry_events
        WHERE target_id = %s
          AND lower(payload_json->>'tx_hash') = lower(%s)
          AND event_type = ANY(%s)
          AND (
            payload_json->>'chain_id' IS NULL
            OR payload_json->>'chain_id' = %s
          )
        LIMIT 1
        ''',
        (target_id, tx_hash, list(_WALLET_TRANSFER_EVENT_TYPES), str(chain_id)),
    ).fetchone()
    return dict(row) if row is not None else None


def _persist_quicknode_wallet_transfer(
    connection: Any, *, target: dict[str, Any], tx: dict[str, Any],
    source: str = QUICKNODE_STREAM_SOURCE,
) -> dict[str, Any]:
    """Persist one matched wallet-transfer row, deduped across every detection path.

    ``source`` is the detected_by / source_type / provider_type stamped on the row:
    the live webhook uses ``quicknode_stream``; the gap backfill uses
    ``quicknode_stream_backfill``; the ops debug-tx import uses
    ``quicknode_stream_debug_import``. Whatever the source, the row is suppressed
    when any transfer-family row already exists for the same target + chain +
    tx_hash (regardless of its detected_by), so a backfill or debug import can never
    duplicate a transfer the stream or stable RPC polling already recorded.
    """
    existing = _existing_telemetry_for_tx(
        connection,
        target_id=target['id'],
        tx_hash=tx['tx_hash'],
        chain_id=tx.get('chain_id') or BASE_CHAIN_ID,
    )
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
        'source_type': source,
        'detected_by': source,
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
            source,
            'wallet_transfer_detected',
            observed_at,
            'live',
            payload_hash,
            payload_json,
            idempotency_key,
        ),
    )
    connection.commit()
    # Real-time push AFTER the commit (task requirement 10): the row is durable, so
    # the open Target Telemetry page prepends it without a refetch. Fail-safe —
    # a Redis failure only logs and the frontend HTTP refresh remains the fallback
    # (requirement 11); it never rolls back this already-committed telemetry row.
    telemetry_realtime.publish_telemetry_event(
        str(target['workspace_id']),
        telemetry_realtime.build_telemetry_stream_event(
            telemetry_id=telemetry_id,
            workspace_id=str(target['workspace_id']),
            target_id=str(target['id']),
            event_type='wallet_transfer_detected',
            detected_by=source,
            tx_hash=tx['tx_hash'],
            from_address=tx['from_address'],
            to_address=tx.get('to_address'),
            amount=value,
            chain_id=tx.get('chain_id') or BASE_CHAIN_ID,
            block_number=tx.get('block_number'),
            observed_at=observed_at.isoformat(),
            evidence_source='live',
        ),
    )
    return {
        'status': 'processed',
        'telemetry_id': telemetry_id,
        'detected_by': source,
        'wallet_transfer_direction': direction,
        # Returned so the caller can drive the same alert/incident chain stable RPC
        # polling creates for this transfer, without rebuilding the payload.
        'payload': payload,
    }


def _create_wallet_transfer_alert_chain(
    *, target: dict[str, Any], payload: dict[str, Any], telemetry_id: str,
) -> dict[str, str | None]:
    """Create the same alert/incident chain Stable RPC Polling creates for this transfer.

    Reuses the canonical stable-polling rule functions verbatim
    (:func:`monitoring_runner._wallet_transfer_smoke_alert` and
    :func:`monitoring_runner._strategic_infrastructure_guard_alert`) rather than
    forking a second alert path. Those functions:

    * open their own committed connection (independent of this webhook's), exactly
      as they do when invoked from the polling worker and the tx-hash import path;
    * are idempotent on ``workspace_id + target_id + chain_id + tx_hash + rule`` —
      a key that does NOT include the detecting source — so if stable RPC polling
      already created the alert for the same target + tx_hash, this returns the
      existing alert instead of a duplicate, and vice versa (cross-source dedupe);
    * carry ``detected_by`` (``quicknode_stream`` here) into the alert evidence
      package and only fire on ``evidence_source='live'``.

    The smoke rule fires for every live transfer; the Strategic Infrastructure Guard
    rule additionally fires for outbound Base ETH movements and drives incident
    creation through the existing auto-incident / escalation mechanisms, which are
    source-agnostic. Never raises: an alert-creation failure must not turn a verified,
    already-persisted QuickNode webhook into a 5xx, so each rule is guarded and logged.
    """
    # No authenticated user on a webhook: attribute the alert to the target's owner,
    # the same way the tx-hash import path (also user-less) does.
    user_id = str(target.get('updated_by_user_id') or target.get('created_by_user_id') or '')
    target_wallet = resolve_monitored_wallet(target) or ''
    target_name = str(target.get('name') or target.get('id') or '')
    monitored_system_id = str(target['monitored_system_id']) if target.get('monitored_system_id') else None
    protected_asset_id = str(target['asset_id']) if target.get('asset_id') else None
    smoke_alert_id: str | None = None
    sig_alert_id: str | None = None
    try:
        smoke_alert_id = _wallet_transfer_smoke_alert(
            workspace_id=str(target['workspace_id']),
            user_id=user_id,
            target_id=str(target['id']),
            target_name=target_name,
            payload=payload,
            evidence_source='live',
            telemetry_id=telemetry_id,
            monitored_system_id=monitored_system_id,
            protected_asset_id=protected_asset_id,
        )
    except Exception:  # pragma: no cover - defensive; alert failure must not 5xx the webhook
        logger.warning(
            'quicknode_stream_smoke_alert_failed tx_hash=%s target_id=%s',
            payload.get('tx_hash'), target.get('id'), exc_info=True,
        )
    try:
        sig_alert_id = _strategic_infrastructure_guard_alert(
            workspace_id=str(target['workspace_id']),
            user_id=user_id,
            target_id=str(target['id']),
            target_name=target_name,
            target_wallet_address=target_wallet,
            payload=payload,
            evidence_source='live',
            telemetry_id=telemetry_id,
            monitored_system_id=monitored_system_id,
            protected_asset_id=protected_asset_id,
        )
    except Exception:  # pragma: no cover - defensive; alert failure must not 5xx the webhook
        logger.warning(
            'quicknode_stream_sig_alert_failed tx_hash=%s target_id=%s',
            payload.get('tx_hash'), target.get('id'), exc_info=True,
        )
    logger.info(
        'quicknode_stream_alert_chain_created tx_hash=%s target_id=%s '
        'smoke_alert_id=%s sig_alert_id=%s',
        payload.get('tx_hash'), target.get('id'),
        smoke_alert_id or 'none', sig_alert_id or 'none',
    )
    return {'smoke_alert_id': smoke_alert_id, 'sig_alert_id': sig_alert_id}


def _target_load_failed_response(*, tx_count: int) -> dict[str, Any]:
    """Safe fail-closed 200 body for when Base wallet targets could not be loaded.

    Returned only *after* signature verification has already passed, when the
    target-loading query itself fails with a database/schema error (e.g. a column
    the deployed schema does not have). It is a 200, not a 500, so QuickNode Streams
    does not retry a non-auth / non-client bug forever — but it is truthful and
    fail-closed: ``ok=false``, ``fail_closed=true``, and every count is zero, so a
    load failure is never dressed up as a healthy, fully processed webhook. The
    paired ``quicknode_stream_target_load_failed`` log line makes it provable from
    Railway logs. Nothing is persisted and no alert is raised on this path.
    """
    return {
        'received': True,
        'ok': False,
        'fail_closed': True,
        'error': 'target_load_failed',
        'tx_count': tx_count,
        'targets_loaded': 0,
        'matched': 0,
        'persisted': 0,
        'duplicates': 0,
        'skipped': 0,
        'results': [],
    }


def _summary_response(
    *,
    tx_count: int,
    targets_loaded: int,
    matched: int,
    persisted: int,
    duplicates: int,
    skipped: int,
    results: list[dict[str, Any]],
    backfill: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build and log the safe aggregate outcome summary for a processed webhook.

    Emitted (as ``quicknode_stream_summary``) and returned in the 200 body for
    every successfully verified QuickNode POST, so the outcome —
    tx_count/targets_loaded/matched/persisted/duplicates/skipped — is provable
    from Railway logs *and* visible to QuickNode in the response. Counts only:
    never wallet addresses, tx hashes, or secrets. ``ok`` is True because the
    request was verified and processed; it does not assert that any transfer
    matched (that is exactly what ``matched``/``persisted`` report). ``backfill``
    is present only when a stream gap was detected and blocks were re-fetched.
    """
    logger.info(
        'quicknode_stream_summary ok=True tx_count=%s targets_loaded=%s matched=%s '
        'persisted=%s duplicates=%s skipped=%s gap_backfilled=%s',
        tx_count, targets_loaded, matched, persisted, duplicates, skipped,
        (backfill or {}).get('persisted', 0) if backfill else 'none',
    )
    summary: dict[str, Any] = {
        'received': True,
        'ok': True,
        'tx_count': tx_count,
        'targets_loaded': targets_loaded,
        'matched': matched,
        'persisted': persisted,
        'duplicates': duplicates,
        'skipped': skipped,
        'results': results,
    }
    if backfill is not None:
        summary['backfill'] = backfill
    return summary


# ---------------------------------------------------------------------------
# Stream checkpoint tracking + gap detection + backfill-on-gap.
#
# The "QuickNode Stream missed a fresh tx" incident had the stream ALIVE but
# already far past the missed tx's block: only stable RPC polling caught it. A
# live batch_range log proves the stream is advancing, but nothing proved it
# advanced WITHOUT skipping blocks. These functions persist a per-stream
# checkpoint (last_processed_block, latest_stream_block, stream_started_at_block,
# missed_block_gap, webhook_received_at) so a jump from block A to block B where
# B > A + 1 is (a) provable from logs (quicknode_stream_gap_detected) and (b)
# self-healing: the missing blocks A+1..B-1 are fetched from Base RPC and run
# through the SAME matcher/dedupe/persist path, tagged detected_by=
# quicknode_stream_backfill. Stable RPC polling remains the always-on backup;
# this only closes the window between a missed stream block and the next poll.
# ---------------------------------------------------------------------------

_QUICKNODE_STREAM_CHECKPOINTS_DDL = '''
CREATE TABLE IF NOT EXISTS quicknode_stream_checkpoints (
    stream_key TEXT PRIMARY KEY,
    latest_stream_block BIGINT,
    last_processed_block BIGINT,
    missed_block_gap BIGINT NOT NULL DEFAULT 0,
    stream_started_at_block BIGINT,
    webhook_received_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
'''


def _backfill_max_blocks() -> int:
    raw = (getenv('QUICKNODE_STREAM_BACKFILL_MAX_BLOCKS') or '').strip()
    if not raw:
        return DEFAULT_QUICKNODE_STREAM_BACKFILL_MAX_BLOCKS
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_QUICKNODE_STREAM_BACKFILL_MAX_BLOCKS


def _collect_batch_block_numbers(body: Any, normalized_txs: list[dict[str, Any]]) -> list[int]:
    """Every block number a QuickNode batch touched, from the RAW payload + txs.

    Read from the block entries directly (not just normalized txs) so a filtered
    stream that delivers a block whose transactions all dropped in normalization
    still advances the checkpoint by that block. Without this, a run of mat-free
    blocks would look like a gap and be needlessly re-backfilled. Falls back to the
    normalized txs' block numbers for the flat-tx payload shape that has no block
    envelope.
    """
    numbers: set[int] = set()
    for entry in _iter_stream_entries(body):
        block = entry.get('block') if isinstance(entry.get('block'), dict) else None
        block_source = block if block is not None else entry
        block_number = _hex_or_int(
            block_source.get('number')
            or block_source.get('block_number')
            or block_source.get('blockNumber')
        )
        if block_number is not None:
            numbers.add(block_number)
    for tx in normalized_txs:
        if tx.get('block_number') is not None:
            numbers.add(tx['block_number'])
    return sorted(numbers)


def _track_stream_checkpoint_and_detect_gap(
    connection: Any, *, stream_key: str, batch_first_block: int, batch_last_block: int,
    received_at: datetime,
) -> tuple[int, int] | None:
    """Upsert the stream checkpoint and return an inclusive missing-block range, or None.

    Compares this batch's first block (B) against the previously processed high-water
    block (A). When B > A + 1 the stream skipped blocks A+1..B-1: logs
    ``quicknode_stream_gap_detected from_block=A to_block=B missing_count=B-A-1`` and
    returns ``(A+1, B-1)`` for the caller to backfill. Never regresses the checkpoint
    (a re-delivered/old batch whose last block is below the high-water mark advances
    nothing and reports no gap), and records ``stream_started_at_block`` once on the
    first batch ever seen — the boundary that classifies a tx below it as
    ``stream_already_past_block``.
    """
    connection.execute(_QUICKNODE_STREAM_CHECKPOINTS_DDL)
    row = connection.execute(
        'SELECT latest_stream_block, last_processed_block, stream_started_at_block '
        'FROM quicknode_stream_checkpoints WHERE stream_key = %s',
        (stream_key,),
    ).fetchone()
    prev = dict(row) if row else None
    prev_last_processed = prev.get('last_processed_block') if prev else None
    prev_latest = prev.get('latest_stream_block') if prev else None
    prev_started = prev.get('stream_started_at_block') if prev else None

    gap_range: tuple[int, int] | None = None
    missing_count = 0
    if prev_last_processed is not None and batch_first_block > prev_last_processed + 1:
        missing_count = batch_first_block - prev_last_processed - 1
        gap_range = (prev_last_processed + 1, batch_first_block - 1)
        logger.info(
            'quicknode_stream_gap_detected from_block=%s to_block=%s missing_count=%s',
            prev_last_processed, batch_first_block, missing_count,
        )

    new_started = prev_started if prev_started is not None else batch_first_block
    new_last_processed = batch_last_block if prev_last_processed is None else max(prev_last_processed, batch_last_block)
    new_latest = batch_last_block if prev_latest is None else max(prev_latest, batch_last_block)

    connection.execute(
        '''
        INSERT INTO quicknode_stream_checkpoints (
            stream_key, latest_stream_block, last_processed_block, missed_block_gap,
            stream_started_at_block, webhook_received_at, updated_at
        ) VALUES (%s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (stream_key) DO UPDATE SET
            latest_stream_block = EXCLUDED.latest_stream_block,
            last_processed_block = EXCLUDED.last_processed_block,
            missed_block_gap = EXCLUDED.missed_block_gap,
            stream_started_at_block = COALESCE(
                quicknode_stream_checkpoints.stream_started_at_block, EXCLUDED.stream_started_at_block
            ),
            webhook_received_at = EXCLUDED.webhook_received_at,
            updated_at = NOW()
        ''',
        (stream_key, new_latest, new_last_processed, missing_count, new_started, received_at),
    )
    logger.info(
        'quicknode_stream_checkpoint stream_key=%s latest_stream_block=%s last_processed_block=%s '
        'missed_block_gap=%s stream_started_at_block=%s',
        stream_key, new_latest, new_last_processed, missing_count, new_started,
    )
    return gap_range


def _load_stream_checkpoint(connection: Any, *, stream_key: str) -> dict[str, Any] | None:
    """Load a stream's checkpoint row, or None when tracking has not started yet.

    Read-only: used by the debug-tx endpoint to classify why a tx was missed
    (stream_not_at_block_yet vs stream_already_past_block vs gap). Creates the table
    if absent so a debug call on a fresh deployment reports ``no_checkpoint`` rather
    than raising on a missing relation.
    """
    connection.execute(_QUICKNODE_STREAM_CHECKPOINTS_DDL)
    row = connection.execute(
        'SELECT stream_key, latest_stream_block, last_processed_block, missed_block_gap, '
        'stream_started_at_block, webhook_received_at '
        'FROM quicknode_stream_checkpoints WHERE stream_key = %s',
        (stream_key,),
    ).fetchone()
    return dict(row) if row else None


def load_base_stream_checkpoint(connection: Any) -> dict[str, Any] | None:
    """Public read-only accessor for the Base QuickNode Stream checkpoint.

    Returns ``{latest_stream_block, last_processed_block, missed_block_gap,
    stream_started_at_block, webhook_received_at}`` or ``None`` when the stream
    has never reported a block. Used by the Telemetry list route to surface
    stream health (last block + last webhook) as a distinct worker status. This
    is a GLOBAL stream infra fact — the single Base QuickNode Stream serves every
    workspace, so it carries no tenant data; per-target "last stream event" stays
    workspace+target scoped in the caller (telemetry rows detected_by=quicknode_stream).
    """
    return _load_stream_checkpoint(connection, stream_key=QUICKNODE_STREAM_KEY_BASE)


def load_live_lane_checkpoint(connection: Any) -> dict[str, Any] | None:
    """Public read-only accessor for the QuickNode LIVE chain-tip checkpoint."""
    return _load_stream_checkpoint(connection, stream_key=QUICKNODE_STREAM_KEY_BASE_LIVE)


def load_backfill_lane_checkpoint(connection: Any) -> dict[str, Any] | None:
    """Public read-only accessor for the QuickNode historical BACKFILL checkpoint."""
    return _load_stream_checkpoint(connection, stream_key=QUICKNODE_STREAM_KEY_BASE_BACKFILL)


def _coerce_checkpoint_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def build_quicknode_live_lane_status(
    connection: Any, *, now: datetime | None = None,
) -> dict[str, Any]:
    """Load the live + backfill checkpoints and derive the live lane's health.

    Returns ``{state, lag_blocks, chain_head, live_checkpoint_block,
    live_checkpoint_at, backfill_checkpoint_block}`` for the Target Telemetry
    header. ``chain_head`` is the head the live worker last observed (stored in the
    live checkpoint's ``latest_stream_block``) — no RPC call is made on the read
    path. Best-effort and side-effect free: a missing checkpoint yields
    ``state=None`` (no live activity yet), never an exception.
    """
    now = now or datetime.now(timezone.utc)
    live = load_live_lane_checkpoint(connection)
    backfill = load_backfill_lane_checkpoint(connection)
    live_block = _checkpoint_last_block(live)
    backfill_block = _checkpoint_last_block(backfill)
    chain_head = None
    if live is not None and live.get('latest_stream_block') is not None:
        try:
            chain_head = int(live['latest_stream_block'])
        except (TypeError, ValueError):
            chain_head = None
    live_at = _coerce_checkpoint_dt((live or {}).get('webhook_received_at'))
    backfill_at = _coerce_checkpoint_dt((backfill or {}).get('webhook_received_at'))
    backfill_advancing = (
        backfill_block is not None
        and backfill_at is not None
        and (now - backfill_at).total_seconds() <= live_stale_seconds()
    )
    state, lag_blocks = classify_quicknode_lane_state(
        chain_head=chain_head,
        live_checkpoint_block=live_block,
        live_checkpoint_at=live_at,
        now=now,
        lag_threshold=live_lag_threshold_blocks(),
        stale_seconds=live_stale_seconds(),
        backfill_advancing=backfill_advancing,
    )
    return {
        'state': state,
        'lag_blocks': lag_blocks,
        'chain_head': chain_head,
        'live_checkpoint_block': live_block,
        'live_checkpoint_at': live_at.isoformat() if live_at else None,
        'backfill_checkpoint_block': backfill_block,
    }


def _classify_stream_coverage(checkpoint: dict[str, Any] | None, tx_block: int | None) -> str:
    """Classify a tx's block against the stream's observed coverage window.

    Returns one of the task's stream-miss reasons for the block dimension:

    * ``no_checkpoint``            — the stream has never reported a block yet.
    * ``stream_not_at_block_yet``  — tx block is above the highest block the stream
      has delivered, so a live miss just means the stream has not reached it.
    * ``stream_already_past_block``— tx block is below where the stream first started,
      so the stream never covered it (exactly the production incident: the stream
      started far ahead of the missed tx).
    * ``within_stream_range``      — tx block sits inside [started, latest] yet was not
      recorded from the stream, which points at a gap the backfill should have closed.
    """
    if not checkpoint or tx_block is None:
        return 'no_checkpoint'
    latest = checkpoint.get('latest_stream_block')
    started = checkpoint.get('stream_started_at_block')
    if latest is not None and tx_block > latest:
        return 'stream_not_at_block_yet'
    if started is not None and tx_block < started:
        return 'stream_already_past_block'
    return 'within_stream_range'


def _make_base_rpc_client() -> Any | None:
    """Build a failover RPC client for Base, or None when no Base RPC is configured.

    Reuses the exact provider resolution stable RPC polling uses
    (:func:`evm_activity_provider.resolve_chain_rpc`) so the backfill fetches from the
    same endpoint(s) the canonical poller trusts. Imported lazily to avoid a module
    import cycle and so a deployment without Base RPC still imports this module.
    """
    from services.api.app.evm_activity_provider import FailoverJsonRpcClient, resolve_chain_rpc

    chain_rpc = resolve_chain_rpc(BASE_CHAIN_NETWORK)
    if not chain_rpc.get('rpc_url'):
        return None
    return FailoverJsonRpcClient(chain_rpc['rpc_urls'])


def _normalize_block_transactions(block: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize every transaction in an ``eth_getBlockByNumber`` (full-tx) result.

    Copies the block number onto each tx (RPC txs carry ``blockNumber`` already, but
    a filtered/edge shape may not) and runs the SAME
    :func:`normalize_base_stream_tx` the live webhook uses, so a backfilled block is
    matched byte-for-byte the way a streamed block would have been.
    """
    out: list[dict[str, Any]] = []
    block_number = block.get('number') or block.get('block_number') or block.get('blockNumber')
    for tx in (block.get('transactions') or []):
        if not isinstance(tx, dict):
            continue
        merged = dict(tx)
        merged.setdefault('block_number', block_number)
        normalized = normalize_base_stream_tx(merged)
        if normalized is not None:
            out.append(normalized)
    return out


def _backfill_stream_gap(
    connection: Any, targets: list[dict[str, Any]], *, gap_from: int, gap_to: int,
    source: str = QUICKNODE_STREAM_BACKFILL_SOURCE,
) -> dict[str, Any]:
    """Fetch the missing blocks gap_from..gap_to from Base RPC and run the matcher.

    Closes a detected stream gap: each block is fetched with full transactions, every
    tx normalized and matched against the already-loaded active Base wallet targets,
    and a match persisted via the same :func:`_persist_quicknode_wallet_transfer`
    (source ``quicknode_stream_backfill``) + alert chain the live webhook uses — so a
    tx the stream skipped (e.g. block 48365342 in the incident) is caught with no
    duplicate row. Bounded by :func:`_backfill_max_blocks`; a gap larger than the cap
    is backfilled up to the cap and the remainder reported truncated (still covered by
    stable RPC polling). RPC/persist failures are per-block and never raise: a
    backfill error must not turn a verified webhook into a 5xx.
    """
    total_blocks = gap_to - gap_from + 1
    max_blocks = _backfill_max_blocks()
    capped_to = gap_to
    truncated = False
    if max_blocks and total_blocks > max_blocks:
        capped_to = gap_from + max_blocks - 1
        truncated = True
    stats: dict[str, Any] = {
        'gap_from': gap_from,
        'gap_to': gap_to,
        'requested_blocks': total_blocks,
        'blocks_scanned': 0,
        'matched': 0,
        'persisted': 0,
        'duplicates': 0,
        'failed_blocks': 0,
        'truncated': truncated,
    }
    logger.info(
        'quicknode_stream_backfill_started from_block=%s to_block=%s requested_blocks=%s '
        'capped_to=%s truncated=%s',
        gap_from, gap_to, total_blocks, capped_to, str(truncated).lower(),
    )
    client = _make_base_rpc_client()
    if client is None:
        logger.warning(
            'quicknode_stream_backfill_no_rpc from_block=%s to_block=%s reason=base_rpc_not_configured',
            gap_from, gap_to,
        )
        return stats
    for block_number in range(gap_from, capped_to + 1):
        try:
            block = client.call('eth_getBlockByNumber', [hex(block_number), True]) or {}
        except Exception:  # pragma: no cover - defensive; a fetch failure must not 5xx
            stats['failed_blocks'] += 1
            logger.warning(
                'quicknode_stream_backfill_block_fetch_failed block_number=%s', block_number, exc_info=True,
            )
            continue
        stats['blocks_scanned'] += 1
        for normalized in _normalize_block_transactions(block):
            matched_targets = _match_targets_for_tx(
                targets, from_address=normalized['from_address'], to_address=normalized.get('to_address'),
            )
            for target in matched_targets:
                stats['matched'] += 1
                outcome = _persist_quicknode_wallet_transfer(
                    connection, target=target, tx=normalized, source=source,
                )
                persisted_payload = outcome.pop('payload', None)
                if outcome['status'] == 'processed':
                    stats['persisted'] += 1
                    logger.info(
                        'quicknode_stream_backfill_persisted detected_by=%s tx_hash=%s target_id=%s block_number=%s',
                        source, normalized['tx_hash'], target['id'], block_number,
                    )
                    _create_wallet_transfer_alert_chain(
                        target=target,
                        payload=persisted_payload if isinstance(persisted_payload, dict) else {},
                        telemetry_id=str(outcome.get('telemetry_id') or ''),
                    )
                elif outcome['status'] == 'duplicate_suppressed':
                    stats['duplicates'] += 1
    logger.info(
        'quicknode_stream_backfill_complete from_block=%s to_block=%s blocks_scanned=%s matched=%s '
        'persisted=%s duplicates=%s failed_blocks=%s truncated=%s',
        gap_from, capped_to, stats['blocks_scanned'], stats['matched'], stats['persisted'],
        stats['duplicates'], stats['failed_blocks'], str(truncated).lower(),
    )
    return stats


# ---------------------------------------------------------------------------
# Chain-tip LIVE lane + historical BACKFILL lane (the real-time fix).
#
# The webhook above ingests whatever QuickNode pushes, in push order. When the
# provider stream replays from an old block it stays far behind the tip, so a
# freshly-confirmed tx is not delivered for a long time. These two lanes fix that
# by decoupling detection from the push backlog and from each other:
#
#   run_live_tip_ingest  — starts at the CURRENT safe head and walks forward at
#                          the tip only. Reads/writes ONLY the live checkpoint.
#   run_backfill_step    — walks the missed historical range at lower priority.
#                          Reads/writes ONLY the backfill checkpoint.
#
# Because the live lane derives its start from the chain head (not from any
# backfill cursor), a backfill that is 40k blocks behind can never delay the live
# lane: the live lane's very next tick still processes the tip. Both persist
# through the same matcher/dedupe/publish path as the webhook, so a transfer seen
# first by Stable RPC Polling is never duplicated.
# ---------------------------------------------------------------------------


def _quicknode_env_int(name: str, default: int) -> int:
    raw = (getenv(name) or '').strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def live_confirmations() -> int:
    return _quicknode_env_int('QUICKNODE_LIVE_CONFIRMATIONS', DEFAULT_QUICKNODE_LIVE_CONFIRMATIONS)


def live_max_blocks_per_tick() -> int:
    return max(1, _quicknode_env_int(
        'QUICKNODE_LIVE_MAX_BLOCKS_PER_TICK', DEFAULT_QUICKNODE_LIVE_MAX_BLOCKS_PER_TICK,
    ))


def backfill_max_blocks_per_tick() -> int:
    return max(1, _quicknode_env_int(
        'QUICKNODE_BACKFILL_MAX_BLOCKS_PER_TICK', DEFAULT_QUICKNODE_BACKFILL_MAX_BLOCKS_PER_TICK,
    ))


def live_lag_threshold_blocks() -> int:
    return _quicknode_env_int(
        'QUICKNODE_LIVE_LAG_THRESHOLD_BLOCKS', DEFAULT_QUICKNODE_LIVE_LAG_THRESHOLD_BLOCKS,
    )


def live_stale_seconds() -> int:
    return max(30, _quicknode_env_int(
        'QUICKNODE_LIVE_STALE_SECONDS', DEFAULT_QUICKNODE_LIVE_STALE_SECONDS,
    ))


def compute_live_start_block(chain_head: int, confirmations: int) -> int:
    """Safe head = chain_head - confirmations, floored at 0.

    The confirmation offset keeps the live lane a few blocks behind the absolute
    head so a reorg at the very tip cannot persist a transfer that later vanishes.
    """
    return max(0, int(chain_head) - max(0, int(confirmations)))


def get_base_chain_head(rpc_client: Any) -> int | None:
    """Current Base chain head (``eth_blockNumber``) as an int, or None on failure.

    Never raises: a provider hiccup returns None so the caller reports the lane
    ``failed`` for this tick and Stable RPC Polling stays the fallback, rather
    than 5xx-ing a worker loop.
    """
    try:
        return _hex_or_int(rpc_client.call('eth_blockNumber', []))
    except Exception:  # pragma: no cover - defensive; provider failure must not raise
        logger.warning('quicknode_live_chain_head_failed', exc_info=True)
        return None


def _advance_lane_checkpoint(
    connection: Any, *, stream_key: str, block: int, received_at: datetime,
    latest_block: int | None = None,
) -> None:
    """Monotonically advance ONE lane's checkpoint (never regresses).

    Distinct from :func:`_track_stream_checkpoint_and_detect_gap` (the webhook's
    gap detector keyed ``base``): this is the simple high-water upsert the live and
    backfill lanes use on their OWN keys, so neither lane can overwrite the other's
    cursor.

    ``last_processed_block`` is the lane's progress cursor. ``latest_stream_block``
    stores ``latest_block`` when given — the live lane passes the observed CHAIN
    HEAD there so the telemetry list route can compute lag (head - cursor) and the
    live/degraded/stale state WITHOUT an extra RPC call. ``webhook_received_at`` is
    refreshed to ``received_at`` on every advance (including a caught-up tick that
    does not move the cursor), so freshness reflects the last successful tick.
    """
    latest = block if latest_block is None else latest_block
    connection.execute(_QUICKNODE_STREAM_CHECKPOINTS_DDL)
    connection.execute(
        '''
        INSERT INTO quicknode_stream_checkpoints (
            stream_key, latest_stream_block, last_processed_block, missed_block_gap,
            stream_started_at_block, webhook_received_at, updated_at
        ) VALUES (%s, %s, %s, 0, %s, %s, NOW())
        ON CONFLICT (stream_key) DO UPDATE SET
            latest_stream_block = GREATEST(
                COALESCE(quicknode_stream_checkpoints.latest_stream_block, -1), EXCLUDED.latest_stream_block
            ),
            last_processed_block = GREATEST(
                COALESCE(quicknode_stream_checkpoints.last_processed_block, -1), EXCLUDED.last_processed_block
            ),
            stream_started_at_block = COALESCE(
                quicknode_stream_checkpoints.stream_started_at_block, EXCLUDED.stream_started_at_block
            ),
            webhook_received_at = EXCLUDED.webhook_received_at,
            updated_at = NOW()
        ''',
        (stream_key, latest, block, block, received_at),
    )


def _checkpoint_last_block(checkpoint: dict[str, Any] | None) -> int | None:
    if not checkpoint:
        return None
    value = checkpoint.get('last_processed_block')
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def run_live_tip_ingest(
    connection: Any, *, rpc_client: Any, targets: list[dict[str, Any]],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Process new Base blocks at the chain tip, independent of any backlog.

    Reads the LIVE checkpoint, derives the safe head from the CURRENT chain head
    (``head - confirmations``), and processes the small forward window
    ``[live_checkpoint+1 .. safe_head]`` (bounded by
    :func:`live_max_blocks_per_tick`). On the very first tick — when no live
    checkpoint exists — it starts at the safe head itself rather than replaying
    history, so the lane is live within one tick no matter how far behind the
    historical backfill is. Matched transfers persist ``detected_by=
    quicknode_stream`` and publish to the telemetry stream after commit (via
    :func:`_persist_quicknode_wallet_transfer`). Advances ONLY the live checkpoint.
    """
    received_at = now or datetime.now(timezone.utc)
    confirmations = live_confirmations()
    checkpoint = _load_stream_checkpoint(connection, stream_key=QUICKNODE_STREAM_KEY_BASE_LIVE)
    prev_block = _checkpoint_last_block(checkpoint)

    stats: dict[str, Any] = {
        'lane': 'live',
        'chain_head': None,
        'safe_head': None,
        'checkpoint_before': prev_block,
        'checkpoint_after': prev_block,
        'blocks_scanned': 0,
        'matched': 0,
        'persisted': 0,
        'duplicates': 0,
        'failed_blocks': 0,
        'lag_blocks': None,
        'failed': False,
    }

    head = get_base_chain_head(rpc_client)
    if head is None:
        stats['failed'] = True
        logger.warning('quicknode_live_lane stream_lane=live status=chain_head_unavailable')
        return stats
    safe_head = compute_live_start_block(head, confirmations)
    stats['chain_head'] = head
    stats['safe_head'] = safe_head

    if prev_block is None:
        # First run: begin AT the tip, do not replay history. Process just the
        # current safe head so the very first tick establishes the live cursor
        # at the top of the chain.
        start_block = safe_head
    else:
        start_block = prev_block + 1
    end_block = min(safe_head, start_block + live_max_blocks_per_tick() - 1)

    if start_block > safe_head:
        # Caught up: nothing new at the tip yet. Refresh the observed head +
        # last-tick time (without moving the cursor) so freshness/lag stay truthful
        # — a healthy caught-up lane must not drift to "stale".
        _advance_lane_checkpoint(
            connection, stream_key=QUICKNODE_STREAM_KEY_BASE_LIVE,
            block=prev_block, latest_block=head, received_at=received_at,
        )
        connection.commit()
        stats['lag_blocks'] = max(0, head - prev_block) if prev_block is not None else None
        logger.info(
            'quicknode_live_lane stream_lane=live chain_head=%s checkpoint_block=%s lag_blocks=%s '
            'payload_block_range=none tx_count=0 targets_loaded=%s matched_count=0 persisted_count=0 '
            'duplicate_count=0 status=caught_up',
            head, prev_block, stats['lag_blocks'], len(targets),
        )
        return stats

    for block_number in range(start_block, end_block + 1):
        try:
            block = rpc_client.call('eth_getBlockByNumber', [hex(block_number), True]) or {}
        except Exception:  # pragma: no cover - defensive; a fetch failure must not stop the lane
            stats['failed_blocks'] += 1
            logger.warning('quicknode_live_block_fetch_failed block_number=%s', block_number, exc_info=True)
            continue
        stats['blocks_scanned'] += 1
        for normalized in _normalize_block_transactions(block):
            matched_targets = _match_targets_for_tx(
                targets, from_address=normalized['from_address'], to_address=normalized.get('to_address'),
            )
            for target in matched_targets:
                stats['matched'] += 1
                outcome = _persist_quicknode_wallet_transfer(
                    connection, target=target, tx=normalized, source=QUICKNODE_STREAM_SOURCE,
                )
                persisted_payload = outcome.pop('payload', None)
                if outcome['status'] == 'processed':
                    stats['persisted'] += 1
                    logger.info(
                        'quicknode_live_persisted stream_lane=live detected_by=%s tx_hash=%s target_id=%s block_number=%s',
                        QUICKNODE_STREAM_SOURCE, normalized['tx_hash'], target['id'], block_number,
                    )
                    _create_wallet_transfer_alert_chain(
                        target=target,
                        payload=persisted_payload if isinstance(persisted_payload, dict) else {},
                        telemetry_id=str(outcome.get('telemetry_id') or ''),
                    )
                elif outcome['status'] == 'duplicate_suppressed':
                    stats['duplicates'] += 1

    # Advance ONLY the live checkpoint — never the backfill key. Records the
    # observed chain head in latest_stream_block so the UI can compute lag.
    _advance_lane_checkpoint(
        connection, stream_key=QUICKNODE_STREAM_KEY_BASE_LIVE, block=end_block,
        latest_block=head, received_at=received_at,
    )
    connection.commit()
    stats['checkpoint_after'] = end_block
    stats['lag_blocks'] = max(0, head - end_block)
    logger.info(
        'quicknode_live_lane stream_lane=live chain_head=%s checkpoint_block=%s lag_blocks=%s '
        'payload_block_range=%s-%s tx_count=%s targets_loaded=%s matched_count=%s persisted_count=%s '
        'duplicate_count=%s status=processed',
        head, end_block, stats['lag_blocks'], start_block, end_block, stats['blocks_scanned'],
        len(targets), stats['matched'], stats['persisted'], stats['duplicates'],
    )
    return stats


def backfill_start_block() -> int | None:
    """Historical backfill seed block (QUICKNODE_BACKFILL_START_BLOCK), or None.

    The backfill lane is dormant until it has a checkpoint to resume from — with no
    seed there is no historical work and the live lane covers the tip. Set this to
    the missed-block incident's first block (or the stream's old
    ``stream_started_at_block``) so the lower-priority lane walks the gap the live
    lane deliberately skips. Accepts decimal or 0x-hex.
    """
    return _hex_or_int((getenv('QUICKNODE_BACKFILL_START_BLOCK') or '').strip() or None)


def seed_backfill_checkpoint(connection: Any, *, start_block: int) -> bool:
    """Seed the BACKFILL checkpoint at ``start_block`` iff it has none yet.

    Idempotent and never regresses an already-advancing backfill cursor: it only
    inserts when no ``quicknode:base:backfill`` row exists (``ON CONFLICT DO
    NOTHING``). ``last_processed_block = start_block - 1`` so the very next
    :func:`run_backfill_step` begins AT ``start_block``. Returns True when it seeded.
    """
    connection.execute(_QUICKNODE_STREAM_CHECKPOINTS_DDL)
    existing = _load_stream_checkpoint(connection, stream_key=QUICKNODE_STREAM_KEY_BASE_BACKFILL)
    if existing is not None:
        return False
    seed_prev = max(0, int(start_block) - 1)
    connection.execute(
        '''
        INSERT INTO quicknode_stream_checkpoints (
            stream_key, latest_stream_block, last_processed_block, missed_block_gap,
            stream_started_at_block, webhook_received_at, updated_at
        ) VALUES (%s, %s, %s, 0, %s, NOW(), NOW())
        ON CONFLICT (stream_key) DO NOTHING
        ''',
        (QUICKNODE_STREAM_KEY_BASE_BACKFILL, seed_prev, seed_prev, start_block),
    )
    connection.commit()
    logger.info(
        'quicknode_backfill_lane stream_lane=backfill status=seeded start_block=%s checkpoint_block=%s',
        start_block, seed_prev,
    )
    return True


def run_backfill_step(
    connection: Any, *, rpc_client: Any, targets: list[dict[str, Any]],
    live_start_block: int | None = None, now: datetime | None = None,
) -> dict[str, Any]:
    """Process one bounded batch of the historical backfill range (lower priority).

    Walks forward from the BACKFILL checkpoint, bounded by
    :func:`backfill_max_blocks_per_tick`, and never past ``live_start_block`` (the
    block the live lane began at) so the two lanes cover disjoint ranges and never
    fight over the same block. Matched transfers persist ``detected_by=
    quicknode_stream_backfill`` and are deduped against anything the live lane or
    Stable RPC Polling already recorded. Advances ONLY the backfill checkpoint.
    """
    received_at = now or datetime.now(timezone.utc)
    checkpoint = _load_stream_checkpoint(connection, stream_key=QUICKNODE_STREAM_KEY_BASE_BACKFILL)
    prev_block = _checkpoint_last_block(checkpoint)

    stats: dict[str, Any] = {
        'lane': 'backfill',
        'checkpoint_before': prev_block,
        'checkpoint_after': prev_block,
        'blocks_scanned': 0,
        'matched': 0,
        'persisted': 0,
        'duplicates': 0,
        'failed_blocks': 0,
    }

    if prev_block is None:
        # No backfill cursor yet — nothing to resume. A deployment seeds this from
        # the webhook's delivery checkpoint / the missed-block incident range; with
        # no seed there is no historical work to do and the live lane covers the tip.
        logger.info('quicknode_backfill_lane stream_lane=backfill status=no_checkpoint')
        return stats

    start_block = prev_block + 1
    end_block = start_block + backfill_max_blocks_per_tick() - 1
    if live_start_block is not None:
        end_block = min(end_block, int(live_start_block) - 1)
    if start_block > end_block:
        logger.info(
            'quicknode_backfill_lane stream_lane=backfill checkpoint_block=%s status=caught_up_to_live',
            prev_block,
        )
        return stats

    for block_number in range(start_block, end_block + 1):
        try:
            block = rpc_client.call('eth_getBlockByNumber', [hex(block_number), True]) or {}
        except Exception:  # pragma: no cover - defensive
            stats['failed_blocks'] += 1
            logger.warning('quicknode_backfill_block_fetch_failed block_number=%s', block_number, exc_info=True)
            continue
        stats['blocks_scanned'] += 1
        for normalized in _normalize_block_transactions(block):
            matched_targets = _match_targets_for_tx(
                targets, from_address=normalized['from_address'], to_address=normalized.get('to_address'),
            )
            for target in matched_targets:
                stats['matched'] += 1
                outcome = _persist_quicknode_wallet_transfer(
                    connection, target=target, tx=normalized, source=QUICKNODE_STREAM_BACKFILL_SOURCE,
                )
                persisted_payload = outcome.pop('payload', None)
                if outcome['status'] == 'processed':
                    stats['persisted'] += 1
                    _create_wallet_transfer_alert_chain(
                        target=target,
                        payload=persisted_payload if isinstance(persisted_payload, dict) else {},
                        telemetry_id=str(outcome.get('telemetry_id') or ''),
                    )
                elif outcome['status'] == 'duplicate_suppressed':
                    stats['duplicates'] += 1

    _advance_lane_checkpoint(
        connection, stream_key=QUICKNODE_STREAM_KEY_BASE_BACKFILL, block=end_block, received_at=received_at,
    )
    connection.commit()
    stats['checkpoint_after'] = end_block
    logger.info(
        'quicknode_backfill_lane stream_lane=backfill checkpoint_block=%s payload_block_range=%s-%s '
        'tx_count=%s matched_count=%s persisted_count=%s duplicate_count=%s status=processed',
        end_block, start_block, end_block, stats['blocks_scanned'], stats['matched'],
        stats['persisted'], stats['duplicates'],
    )
    return stats


def classify_quicknode_lane_state(
    *,
    chain_head: int | None,
    live_checkpoint_block: int | None,
    live_checkpoint_at: datetime | None,
    now: datetime,
    lag_threshold: int,
    stale_seconds: int,
    backfill_advancing: bool = False,
    failed: bool = False,
) -> tuple[str | None, int | None]:
    """Classify the live QuickNode lane's health from canonical checkpoint facts.

    Returns ``(state, lag_blocks)`` where state is one of:

      * ``'live'``        — lag = chain_head - live_checkpoint is within threshold.
      * ``'catching_up'`` — the live lane has no checkpoint yet but the historical
                            backfill lane is advancing (live path not established).
      * ``'degraded'``    — the live lane exists but lag exceeds the threshold, so
                            Stable RPC Polling is carrying detection.
      * ``'stale'``       — the live checkpoint has not moved for ``stale_seconds``.
      * ``'failed'``      — the last tick could not reach the provider.
      * ``None``          — no live activity and no backfill activity to report.

    Historical backfill progress can move the lane to ``catching_up`` but NEVER to
    ``live`` — only chain-tip proximity does — so a backfill that is catching up can
    never paint a false green "live" (task: "Historical backfill activity must not
    make the UI claim the provider is live").
    """
    lag_blocks: int | None = None
    if chain_head is not None and live_checkpoint_block is not None:
        lag_blocks = max(0, int(chain_head) - int(live_checkpoint_block))

    if failed:
        return 'failed', lag_blocks

    if live_checkpoint_block is None:
        return ('catching_up' if backfill_advancing else None), lag_blocks

    # Live lane established: staleness (no movement) wins over the lag reading.
    if live_checkpoint_at is not None:
        try:
            age_seconds = (now - live_checkpoint_at).total_seconds()
        except (TypeError, ValueError):
            age_seconds = 0.0
        if age_seconds > stale_seconds:
            return 'stale', lag_blocks

    if lag_blocks is not None and lag_blocks <= lag_threshold:
        return 'live', lag_blocks
    return 'degraded', lag_blocks


# ---------------------------------------------------------------------------
# Multi-replica safety for the live lane: a Postgres session-scoped advisory lock
# (the same primitive migrations and the reconcile job use). Only one replica ever
# runs the live tip tick at a time, so two Railway replicas cannot both process —
# and double-persist — the same tip block. Session-scoped (not xact-scoped) so it
# survives the per-block commits the lane performs; the caller MUST release it.
# ---------------------------------------------------------------------------

_QUICKNODE_LIVE_LANE_LOCK_KEYS: tuple[int, int] = (
    int.from_bytes(hashlib.sha256(b'quicknode:base:live').digest()[0:4], 'big', signed=False),
    int.from_bytes(hashlib.sha256(b'quicknode:base:live').digest()[4:8], 'big', signed=False),
)


def try_acquire_live_lane_lock(connection: Any) -> bool:
    """Try to take the global live-lane lock; True when this replica may run the tick."""
    row = connection.execute(
        'SELECT pg_try_advisory_lock(%s, %s) AS acquired',
        _QUICKNODE_LIVE_LANE_LOCK_KEYS,
    ).fetchone()
    if not row:
        return False
    if isinstance(row, dict):
        return bool(row.get('acquired', False))
    return bool(getattr(row, 'acquired', False))


def release_live_lane_lock(connection: Any) -> None:
    """Release the live-lane advisory lock taken by :func:`try_acquire_live_lane_lock`."""
    try:
        connection.execute('SELECT pg_advisory_unlock(%s, %s)', _QUICKNODE_LIVE_LANE_LOCK_KEYS)
    except Exception:  # pragma: no cover - unlock is best-effort; the session ending frees it
        logger.warning('quicknode_live_lane_unlock_failed', exc_info=True)


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
    # Task requirement 2 (and 4): every batch logs its block range + whether it
    # carries the debug tx, and — when the debug tx's block is known but absent from
    # a covering batch — a debug_tx_not_seen line. Emitted before the empty-batch
    # early return so a batch that normalized to zero is still traced.
    _log_batch_range_and_debug_tx(normalized_txs)

    # Every block this batch touched, read from the RAW payload (not just matched
    # txs) so gap tracking still advances across blocks a filtered stream
    # delivered empty of matches. Drives the checkpoint + gap detector below.
    batch_block_numbers = _collect_batch_block_numbers(body, normalized_txs)
    if not normalized_txs and not batch_block_numbers:
        reason = 'no_raw_transactions_extracted' if not raw_txs else 'raw_transactions_missing_required_fields'
        logger.info('quicknode_stream_no_transactions_normalized reason=%s', reason)
        return _summary_response(
            tx_count=0, targets_loaded=0, matched=0, persisted=0, duplicates=0, skipped=0, results=[],
        )

    received_at = datetime.now(timezone.utc)
    results: list[dict[str, Any]] = []
    match_count = 0
    persisted_count = 0
    duplicate_count = 0
    skipped_count = 0
    backfill_stats: dict[str, Any] | None = None
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        # Stream checkpoint + gap detection (task requirements 1 & 2). Auxiliary to
        # the customer-data path: a checkpoint failure must never fail the webhook,
        # so it is guarded and rolls back its own aborted transaction so the target
        # load below still runs on a clean connection.
        gap_range: tuple[int, int] | None = None
        if batch_block_numbers:
            try:
                gap_range = _track_stream_checkpoint_and_detect_gap(
                    connection,
                    stream_key=QUICKNODE_STREAM_KEY_BASE,
                    batch_first_block=batch_block_numbers[0],
                    batch_last_block=batch_block_numbers[-1],
                    received_at=received_at,
                )
            except psycopg.Error as exc:
                try:
                    connection.rollback()
                except Exception:  # pragma: no cover - best-effort on a dead tx
                    pass
                logger.warning(
                    'quicknode_stream_checkpoint_failed error_type=%s', type(exc).__name__, exc_info=True,
                )
        # Targets are needed to MATCH streamed txs and to BACKFILL a detected gap.
        # A matchless, gap-free batch (the common single-empty-block case) skips the
        # load entirely and stays a cheap checkpoint-only update.
        if not normalized_txs and gap_range is None:
            return _summary_response(
                tx_count=0, targets_loaded=0, matched=0, persisted=0, duplicates=0, skipped=0, results=[],
            )
        try:
            targets = _load_all_base_wallet_targets(connection)
        except psycopg.Error as exc:
            # Target loading hit a database/schema error (e.g. a column the deployed
            # schema does not have — the monitored_system_id regression this handler
            # was hardened against). This is neither an auth failure nor a client
            # error, so a 500 would make QuickNode Streams retry the same broken
            # request indefinitely. Fail closed instead: roll back the now-aborted
            # transaction so the connection releases cleanly, log the failure so it is
            # provable from Railway logs, and return a truthful 200 that asserts
            # nothing was processed — never a false "healthy".
            try:
                connection.rollback()
            except Exception:  # pragma: no cover - rollback is best-effort on a dead tx
                pass
            logger.warning(
                'quicknode_stream_target_load_failed error_type=%s tx_count=%s',
                type(exc).__name__, len(normalized_txs), exc_info=True,
            )
            return _target_load_failed_response(tx_count=len(normalized_txs))
        # Backfill-on-gap (task requirement 3): fetch the skipped blocks from Base
        # RPC and run the same matcher, so a tx the stream skipped (e.g. block
        # 48365342 in the incident) is caught here — no duplicate, since the persist
        # dedupes against any existing transfer-family row for the target + tx_hash.
        if gap_range is not None:
            backfill_stats = _backfill_stream_gap(
                connection, targets, gap_from=gap_range[0], gap_to=gap_range[1],
            )
        # Computed once per payload: last4/hash8 tags for every loaded target
        # wallet, so a no-match tx can be diagnosed against the configured
        # targets from logs without ever printing a full monitored wallet.
        target_fingerprints = [_wallet_fingerprint(resolve_monitored_wallet(t)) for t in targets]
        no_match_logged = 0
        # Read once per payload so the hot per-tx loop never re-reads the env.
        debug_hashes = _debug_tx_hashes()
        for normalized in normalized_txs:
            matched_targets = _match_targets_for_tx(
                targets, from_address=normalized['from_address'], to_address=normalized['to_address'],
            )
            if not matched_targets:
                skipped_count += 1
                # Per-tx no-match diagnostics. DEMOTED to DEBUG (task: "Remove or
                # demote the current flood of quicknode_stream_no_match_detail logs
                # in production") and still capped per payload, so a replay batch of
                # hundreds of unrelated txs no longer floods Railway at INFO — the
                # aggregate quicknode_stream_no_match line below carries the counts.
                # from/to are public on-chain facts; wallets are fingerprinted (never
                # printed in full). Suppressed while this module logger sits at its
                # pinned INFO level; drop it to DEBUG to re-enable when diagnosing a miss.
                if no_match_logged < _NO_MATCH_DETAIL_LOG_LIMIT and logger.isEnabledFor(logging.DEBUG):
                    logger.debug(
                        'quicknode_stream_no_match_detail tx_hash=%s from=%s to=%s target_wallets=%s',
                        normalized['tx_hash'], normalized['from_address'],
                        normalized.get('to_address'), target_fingerprints,
                    )
                    no_match_logged += 1
                # A configured debug tx that reached normalization but matched no
                # monitored wallet: log it seen-but-unmatched so case C (matcher
                # failed / wallet not configured) is provable from logs.
                if normalized['tx_hash'] in debug_hashes:
                    _log_debug_tx_seen(
                        normalized=normalized,
                        target_wallet=None,
                        from_matches_target=False,
                        to_matches_target=False,
                        duplicate_found=False,
                        persisted=False,
                    )
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
                # Pull the internal payload out of the outcome before it becomes a
                # JSON-serialized result entry: it feeds the alert chain but should
                # not bloat the webhook response body.
                persisted_payload = outcome.pop('payload', None)
                if outcome['status'] == 'processed':
                    persisted_count += 1
                    logger.info(
                        'quicknode_stream_event_persisted detected_by=%s tx_hash=%s target_id=%s',
                        QUICKNODE_STREAM_SOURCE, normalized['tx_hash'], target['id'],
                    )
                    # Same alert/incident chain Stable RPC Polling raises for a live
                    # wallet transfer — reused verbatim, deduped across sources by tx_hash.
                    alert_ids = _create_wallet_transfer_alert_chain(
                        target=target,
                        payload=persisted_payload if isinstance(persisted_payload, dict) else {},
                        telemetry_id=str(outcome.get('telemetry_id') or ''),
                    )
                    outcome['smoke_alert_id'] = alert_ids.get('smoke_alert_id')
                    outcome['sig_alert_id'] = alert_ids.get('sig_alert_id')
                elif outcome['status'] == 'duplicate_suppressed':
                    duplicate_count += 1
                    logger.info(
                        'quicknode_stream_duplicate_suppressed tx_hash=%s existing_detected_by=%s',
                        normalized['tx_hash'], outcome.get('existing_detected_by'),
                    )
                # A configured debug tx that matched a monitored wallet: log the full
                # per-target trace so case D (matched but duplicate-suppressed) is
                # distinguishable from a real persist, straight from Railway logs.
                if normalized['tx_hash'] in debug_hashes:
                    _log_debug_tx_seen(
                        normalized=normalized,
                        target_wallet=target_wallet,
                        from_matches_target=from_match,
                        to_matches_target=to_match,
                        duplicate_found=outcome['status'] == 'duplicate_suppressed',
                        persisted=outcome['status'] == 'processed',
                    )
                results.append({'tx_hash': normalized['tx_hash'], 'target_id': target['id'], **outcome})
        if match_count == 0:
            logger.info(
                'quicknode_stream_no_match tx_count=%s target_count=%s',
                len(normalized_txs), len(targets),
            )
        return _summary_response(
            tx_count=len(normalized_txs), targets_loaded=len(targets), matched=match_count,
            persisted=persisted_count, duplicates=duplicate_count, skipped=skipped_count, results=results,
            backfill=backfill_stats,
        )


# ---------------------------------------------------------------------------
# Safe ops endpoint: replay the QuickNode matcher/dedupe logic against a tx
# fetched live from Base RPC. Read-only by default (dry_run=true); only writes
# when explicitly asked (dry_run=false), using the exact same persistence + alert
# chain the live webhook uses. Gated by the QuickNode Streams secret because it
# re-runs the webhook's intentionally-unscoped matcher across every workspace's
# Base wallets (task requirement 5).
# ---------------------------------------------------------------------------

QUICKNODE_OPS_TOKEN_HEADER = 'x-quicknode-ops-token'


def verify_quicknode_ops_token(token: str | None) -> None:
    """Authorize a QuickNode ops/debug request against the Stream's shared secret.

    The debug-tx endpoint re-runs the webhook's (intentionally unscoped) matcher
    across every workspace's Base wallets, so it is gated by the same credential as
    the webhook itself — ``QUICKNODE_STREAMS_SECRET`` — rather than workspace RBAC.
    Fails closed: an unconfigured secret (503) or a missing/incorrect token (401)
    always rejects, and the secret is never echoed back in the error detail.
    """
    secret = (getenv('QUICKNODE_STREAMS_SECRET') or '').strip()
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='QuickNode Streams ops endpoint is not configured (QUICKNODE_STREAMS_SECRET missing).',
        )
    provided = (token or '').strip()
    if not provided or not hmac.compare_digest(provided, secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Invalid or missing QuickNode Streams ops token.',
        )


def run_quicknode_debug_tx(*, tx_hash: str, dry_run: bool = True) -> dict[str, Any]:
    """Fetch a tx/receipt from Base RPC and replay the QuickNode matcher/dedupe logic.

    Safe ops diagnostic for the "QuickNode Stream missed a fresh tx" investigation
    (task requirement 5). Fetches the transaction and receipt from the configured
    Base RPC — the same provider stable RPC polling uses — normalizes it exactly as
    the webhook does, then runs the identical unscoped target load + wallet match +
    duplicate check. Nothing is written unless ``dry_run`` is False, in which case it
    persists via the same :func:`_persist_quicknode_wallet_transfer` + alert chain the
    live webhook uses, tagged ``detected_by=quicknode_stream_debug_import`` so a manual
    recovery is distinguishable from a live stream detection (and still idempotent
    against any row the stream or stable poller already wrote).

    Also classifies WHY the live stream missed the tx via ``stream_miss_reason`` (task
    requirement 5): ``matcher_failed``, ``duplicate_suppressed``,
    ``stream_not_at_block_yet``, ``stream_already_past_block``, or ``gap_detected`` —
    derived from the matcher/dedupe verdict and the tx block's position relative to the
    stream checkpoint.

    Returns a truthful report whose ``conclusion`` classifies the outcome:

    * ``tx_not_found_on_rpc``      — the tx does not exist on the Base RPC (wrong chain
      / un-indexed); the endpoint cannot decide anything else.
    * ``tx_missing_required_fields`` — fetched but not normalizable (no hash/from).
    * ``matcher_no_wallet_match``  — normalized fine but matched no active Base wallet
      target (case C: matcher/config discrepancy — if the UI still shows the tx via
      stable polling, the two paths resolve different wallets).
    * ``duplicate_suppressed``     — matched, but a wallet-transfer row already exists
      for every matched target (case D: the QuickNode webhook would suppress it).
    * ``would_match_and_persist`` / ``matched_and_persisted`` — matched with no existing
      row (dry-run vs. write). Implies a live miss is a delivery/normalizer gap
      (case A/B), which only the batch_range / debug_tx_* logs can decide.

    Only public tx from/to and the monitored wallet's last4 are returned — never a
    full monitored wallet or any secret.
    """
    from services.api.app.evm_activity_provider import (
        FailoverJsonRpcClient,
        _hex_to_int,
        resolve_chain_rpc,
    )

    normalized_hash = str(tx_hash or '').strip().lower()
    if not normalized_hash.startswith('0x') or len(normalized_hash) != 66:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='tx_hash must be a 66-char 0x-prefixed hex string.',
        )

    chain_rpc = resolve_chain_rpc(BASE_CHAIN_NETWORK)
    if not chain_rpc.get('rpc_url'):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='EVM RPC not configured for Base (chain 8453).',
        )
    client = FailoverJsonRpcClient(chain_rpc['rpc_urls'])
    try:
        rpc_chain_id = _hex_to_int(client.call('eth_chainId', []))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f'RPC eth_chainId probe failed: {str(exc)[:200]}',
        )
    try:
        tx = client.call('eth_getTransactionByHash', [normalized_hash])
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f'eth_getTransactionByHash failed: {str(exc)[:200]}',
        )

    logger.info(
        'quicknode_stream_debug_tx_endpoint tx_hash=%s dry_run=%s rpc_chain_id=%s tx_found=%s',
        normalized_hash, str(dry_run).lower(), rpc_chain_id, str(bool(tx)).lower(),
    )

    if not tx:
        return {
            'tx_hash': normalized_hash,
            'dry_run': dry_run,
            'rpc_chain_id': rpc_chain_id,
            'tx_found': False,
            'conclusion': 'tx_not_found_on_rpc',
            'message': 'Transaction not found on the Base RPC endpoint (wrong chain, or the RPC does not index it).',
        }

    try:
        receipt = client.call('eth_getTransactionReceipt', [normalized_hash]) or {}
    except Exception:
        receipt = {}

    normalized = normalize_base_stream_tx(dict(tx))
    if normalized is None:
        return {
            'tx_hash': normalized_hash,
            'dry_run': dry_run,
            'rpc_chain_id': rpc_chain_id,
            'tx_found': True,
            'conclusion': 'tx_missing_required_fields',
            'message': 'Transaction fetched but lacks the minimum required fields (hash/from) to normalize.',
        }

    matched_results: list[dict[str, Any]] = []
    persisted_count = 0
    duplicate_count = 0
    checkpoint: dict[str, Any] | None = None
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        # Read-only stream checkpoint, used only to classify WHY the live QuickNode
        # stream missed this tx (task requirement 5). Guarded: a checkpoint read
        # failure must not stop the debug replay — the miss reason falls back to
        # no_checkpoint and the matcher/dedupe verdict still stands.
        try:
            checkpoint = _load_stream_checkpoint(connection, stream_key=QUICKNODE_STREAM_KEY_BASE)
        except psycopg.Error:
            try:
                connection.rollback()
            except Exception:  # pragma: no cover - best-effort on a dead tx
                pass
            checkpoint = None
        targets = _load_all_base_wallet_targets(connection)
        targets_loaded = len(targets)
        matched_targets = _match_targets_for_tx(
            targets, from_address=normalized['from_address'], to_address=normalized.get('to_address'),
        )
        for target in matched_targets:
            target_wallet = resolve_monitored_wallet(target)
            from_match = target_wallet == normalized['from_address']
            to_match = target_wallet is not None and target_wallet == normalized.get('to_address')
            existing = _existing_telemetry_for_tx(
                connection,
                target_id=target['id'],
                tx_hash=normalized['tx_hash'],
                chain_id=normalized.get('chain_id') or BASE_CHAIN_ID,
            )
            duplicate_found = existing is not None
            entry: dict[str, Any] = {
                'target_id': str(target['id']),
                'workspace_id': str(target.get('workspace_id')),
                'target_wallet_last4': target_wallet[-4:] if target_wallet else None,
                'from_matches_target': from_match,
                'to_matches_target': to_match,
                'duplicate_found': duplicate_found,
                'existing_detected_by': existing.get('detected_by') if existing else None,
                'persisted': False,
                'telemetry_id': None,
            }
            if duplicate_found:
                duplicate_count += 1
            elif not dry_run:
                outcome = _persist_quicknode_wallet_transfer(
                    connection, target=target, tx=normalized,
                    source=QUICKNODE_STREAM_DEBUG_IMPORT_SOURCE,
                )
                persisted_payload = outcome.pop('payload', None)
                if outcome['status'] == 'processed':
                    persisted_count += 1
                    entry['persisted'] = True
                    entry['telemetry_id'] = outcome.get('telemetry_id')
                    alert_ids = _create_wallet_transfer_alert_chain(
                        target=target,
                        payload=persisted_payload if isinstance(persisted_payload, dict) else {},
                        telemetry_id=str(outcome.get('telemetry_id') or ''),
                    )
                    entry['smoke_alert_id'] = alert_ids.get('smoke_alert_id')
                    entry['sig_alert_id'] = alert_ids.get('sig_alert_id')
                elif outcome['status'] == 'duplicate_suppressed':
                    # A concurrent writer landed the row between the check above and now.
                    duplicate_count += 1
                    entry['duplicate_found'] = True
                    entry['existing_detected_by'] = outcome.get('existing_detected_by')
            matched_results.append(entry)

    matched_count = len(matched_results)
    if matched_count == 0:
        conclusion = 'matcher_no_wallet_match'
    elif duplicate_count == matched_count:
        conclusion = 'duplicate_suppressed'
    elif dry_run:
        conclusion = 'would_match_and_persist'
    else:
        conclusion = 'matched_and_persisted'

    # Task requirement 5: name WHY the live QuickNode stream missed this tx, using
    # one of the fixed reason tokens. Matcher/dedupe outcomes win first (they are
    # certain); otherwise the block-coverage classification against the checkpoint
    # explains the miss (stream not at the block yet / already past it / a gap that
    # the backfill should close).
    stream_coverage = _classify_stream_coverage(checkpoint, normalized.get('block_number'))
    if matched_count == 0:
        stream_miss_reason = 'matcher_failed'
    elif duplicate_count == matched_count:
        stream_miss_reason = 'duplicate_suppressed'
    elif stream_coverage == 'stream_not_at_block_yet':
        stream_miss_reason = 'stream_not_at_block_yet'
    elif stream_coverage == 'stream_already_past_block':
        stream_miss_reason = 'stream_already_past_block'
    else:
        # within_stream_range / no_checkpoint: the block sits inside the stream's
        # window (or tracking is too new to say) yet arrived only via this path —
        # the signature of a skipped block the gap backfill is meant to catch.
        stream_miss_reason = 'gap_detected'
    logger.info(
        'quicknode_stream_debug_tx_coverage tx_hash=%s tx_block_number=%s stream_coverage=%s '
        'stream_miss_reason=%s latest_stream_block=%s stream_started_at_block=%s',
        normalized_hash,
        normalized.get('block_number') if normalized.get('block_number') is not None else 'none',
        stream_coverage, stream_miss_reason,
        (checkpoint or {}).get('latest_stream_block') if checkpoint else 'none',
        (checkpoint or {}).get('stream_started_at_block') if checkpoint else 'none',
    )

    return {
        'tx_hash': normalized_hash,
        'dry_run': dry_run,
        'rpc_chain_id': rpc_chain_id,
        'tx_found': True,
        'block_number': normalized.get('block_number'),
        'from': normalized['from_address'],
        'to': normalized.get('to_address'),
        'value_wei': normalized.get('value'),
        'receipt_status': receipt.get('status') if isinstance(receipt, dict) else None,
        'targets_loaded': targets_loaded,
        'matched_count': matched_count,
        'persisted_count': persisted_count,
        'duplicate_count': duplicate_count,
        'matched_targets': matched_results,
        'conclusion': conclusion,
        'stream_coverage': stream_coverage,
        'stream_miss_reason': stream_miss_reason,
    }
