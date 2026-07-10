"""Real-time telemetry publication to the workspace-scoped Redis stream.

The Target Telemetry page subscribes to ``/stream/telemetry`` (SSE, backed by the
``decoda:workspace:{workspace_id}:telemetry`` Redis stream) so a newly persisted
wallet-transfer row is prepended in the browser within a few seconds of chain
confirmation — no manual refresh, tx-hash search, or wait for the next stable
poll. This module is the single publish choke point every detection path calls
AFTER it has durably committed a telemetry row:

  * QuickNode live stream / gap backfill / debug import
    (quicknode_streams._persist_quicknode_wallet_transfer)
  * Stable RPC polling + the realtime WebSocket family
    (monitoring_runner._persist_raw_wallet_transfer_telemetry)

Two hard rules (task requirements 10 & 11):

  * Publish ONLY after the DB commit — never before, so the browser can never
    render a row that a rollback then erased.
  * A Redis failure must NEVER propagate: the telemetry row is already durable,
    so :func:`publish_telemetry_event` swallows and logs every error and the
    frontend's periodic HTTP refetch remains the fallback. It returns a bool
    purely so callers/tests can assert whether the push happened.
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Discriminator so a telemetry SSE consumer can distinguish these events from any
# other envelope on the same transport, and never confuse them with alerts.
TELEMETRY_STREAM_EVENT_TYPE = 'telemetry'


def build_telemetry_stream_event(
    *,
    telemetry_id: str,
    workspace_id: str,
    target_id: str,
    event_type: str,
    detected_by: str | None,
    tx_hash: str | None,
    from_address: str | None,
    to_address: str | None,
    amount: Any,
    chain_id: Any,
    block_number: Any,
    observed_at: str | None,
    ingested_at: str | None = None,
    evidence_source: str | None = None,
) -> dict[str, Any]:
    """Build the compact SSE payload the frontend renders without an immediate refetch.

    Carries exactly the fields the telemetry table needs (task's real-time
    publication contract) plus ``type`` + ``target_id`` + ``workspace_id`` so the
    subscriber can filter to the current target and reject cross-workspace or
    non-telemetry envelopes. Values are kept primitive/JSON-safe by the caller.
    """
    return {
        'type': TELEMETRY_STREAM_EVENT_TYPE,
        'telemetry_id': str(telemetry_id),
        'target_id': str(target_id),
        'workspace_id': str(workspace_id),
        'event_type': event_type,
        'detected_by': detected_by,
        'tx_hash': tx_hash,
        'from': from_address,
        'to': to_address,
        'amount': None if amount is None else str(amount),
        'chain_id': None if chain_id is None else str(chain_id),
        'block_number': block_number,
        'observed_at': observed_at,
        'ingested_at': ingested_at,
        'evidence_source': evidence_source,
    }


def publish_telemetry_event(workspace_id: str, event: dict[str, Any]) -> bool:
    """Push one committed telemetry event to the workspace telemetry stream.

    Fail-safe by contract: returns ``True`` only when the event was accepted by
    Redis, ``False`` on any failure (unconfigured backend, connection error,
    serialization error) — and NEVER raises. Callers invoke this after a durable
    commit, so a ``False`` here only means the browser will pick the row up on its
    next HTTP refresh instead of instantly.
    """
    if not workspace_id:
        return False
    # No Redis configured (e.g. local/dev/tests): stay quiet — the row is durable
    # and HTTP polling is the fallback. Debug-level so a real deployment missing
    # REDIS_URL is still discoverable without flooding normal logs. Checked via the
    # env directly so importing this module never forces a `redis` import (the huge
    # persist-path callers stay import-safe without the Redis client installed).
    if not (os.getenv('REDIS_URL', '').strip()):
        logger.debug(
            'telemetry_stream_publish_skipped reason=redis_not_configured workspace_id=%s telemetry_id=%s',
            workspace_id, event.get('telemetry_id'),
        )
        return False
    try:
        from services.api.app.domains import alert_stream

        alert_stream.publish_telemetry(str(workspace_id), event)
        return True
    except Exception as exc:  # pragma: no cover - defensive; publish must never 5xx a commit
        logger.warning(
            'telemetry_stream_publish_failed workspace_id=%s telemetry_id=%s target_id=%s '
            'error_type=%s error=%s',
            workspace_id, event.get('telemetry_id'), event.get('target_id'),
            type(exc).__name__, str(exc)[:200],
        )
        return False
