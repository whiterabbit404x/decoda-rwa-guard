"""QuickNode live chain-tip ingestion worker entrypoint.

The real-time detection lane the "new tx doesn't show up until Stable RPC Polling"
fix is built around. It is INDEPENDENT of both the QuickNode Streams webhook
backlog and the always-on Stable RPC Polling worker:

  * Every tick it reads the current Base chain head and processes the small
    forward window at the tip (services/api/app/quicknode_streams.run_live_tip_ingest),
    persisting matched transfers detected_by=quicknode_stream and publishing them
    to the workspace telemetry stream after commit — so an open Target Telemetry
    page prepends the row within a few seconds of confirmation.
  * It then advances ONE lower-priority historical backfill step
    (run_backfill_step) over the missed range, on a SEPARATE checkpoint, so the
    backlog never delays the tip.

Multi-replica safe: a Postgres session advisory lock ensures only one replica runs
the live tick at a time (two Railway replicas cannot double-process the same tip
block). Stable RPC Polling keeps running regardless; if this worker is disabled or
its provider is down, detection simply falls back to polling.

Enable with QUICKNODE_LIVE_ENABLED=true. Requires a Base RPC endpoint configured
the same way Stable RPC Polling resolves it (EVM_RPC_URL_8453 / BASE_EVM_RPC_URL /
EVM_RPC_URL). Default: disabled — the webhook + stable polling keep working.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_SECONDS = 3
DEFAULT_IDLE_INTERVAL_SECONDS = 60


def _bool_env(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name) or '').strip().lower()
    if raw in ('1', 'true', 'yes', 'on'):
        return True
    if raw in ('0', 'false', 'no', 'off'):
        return False
    return default


def _poll_interval_seconds() -> int:
    raw = (os.getenv('QUICKNODE_LIVE_POLL_INTERVAL_SECONDS') or '').strip()
    try:
        return max(1, int(raw)) if raw else DEFAULT_POLL_INTERVAL_SECONDS
    except ValueError:
        return DEFAULT_POLL_INTERVAL_SECONDS


def run_one_tick() -> dict[str, object]:
    """Run a single live tick + one backfill step under the live-lane lock.

    Returns a small stats dict (also useful for a manual/one-shot invocation). A
    provider or DB failure is logged and returned as ``skipped``/``failed`` rather
    than raised, so the worker loop keeps ticking and Stable RPC Polling stays the
    fallback.
    """
    # Imported lazily so the module stays importable (and unit-testable) without a
    # live DB/RPC, mirroring the rest of the worker entrypoints.
    from services.api.app.pilot import ensure_pilot_schema, pg_connection
    from services.api.app.quicknode_streams import (
        _load_all_base_wallet_targets,
        _make_base_rpc_client,
        release_live_lane_lock,
        run_backfill_step,
        run_live_tip_ingest,
        try_acquire_live_lane_lock,
    )

    now = datetime.now(timezone.utc)
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        if not try_acquire_live_lane_lock(connection):
            # Another replica owns the live lane this tick — do nothing (no double
            # processing of the tip block).
            logger.debug('quicknode_live_worker_tick skipped=another_replica_active')
            return {'status': 'skipped_locked'}
        try:
            rpc_client = _make_base_rpc_client()
            if rpc_client is None:
                logger.warning('quicknode_live_worker_tick status=base_rpc_not_configured')
                return {'status': 'no_rpc'}
            targets = _load_all_base_wallet_targets(connection)
            live_stats = run_live_tip_ingest(
                connection, rpc_client=rpc_client, targets=targets, now=now,
            )
            backfill_stats = run_backfill_step(
                connection, rpc_client=rpc_client, targets=targets,
                live_start_block=live_stats.get('checkpoint_after'), now=now,
            )
            return {'status': 'processed', 'live': live_stats, 'backfill': backfill_stats}
        finally:
            release_live_lane_lock(connection)


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    if not _bool_env('QUICKNODE_LIVE_ENABLED', False):
        logger.info(
            'quicknode_live_disabled set QUICKNODE_LIVE_ENABLED=true to enable the '
            'chain-tip lane (stable RPC polling remains the fallback)'
        )
        while True:  # keep the container alive without burning RPC budget
            time.sleep(DEFAULT_IDLE_INTERVAL_SECONDS)
    interval = _poll_interval_seconds()
    logger.info('quicknode_live_worker_started poll_interval_seconds=%s', interval)
    while True:
        try:
            run_one_tick()
        except Exception:  # pragma: no cover - loop must survive a transient failure
            logger.warning('quicknode_live_worker_tick_failed', exc_info=True)
        time.sleep(interval)


if __name__ == '__main__':
    raise SystemExit(main())
