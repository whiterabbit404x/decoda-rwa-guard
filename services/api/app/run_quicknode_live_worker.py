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

# Emitted once per worker process (not every tick) so the readiness marker proves the
# live lane started without flooding logs on every poll.
_LIVE_LANE_STARTED_EMITTED = False


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
        backfill_start_block,
        emit_quicknode_live_lane_started,
        quicknode_backfill_enabled,
        release_live_lane_lock,
        run_backfill_step,
        run_live_tip_ingest,
        seed_backfill_checkpoint,
        seed_backfill_from_base_checkpoint,
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
            # Emit the mandatory readiness marker once per process, with a real chain
            # head + live checkpoint + lag, so an operator can prove the live lane is
            # running and how close to the tip it is straight from Railway logs.
            global _LIVE_LANE_STARTED_EMITTED
            if not _LIVE_LANE_STARTED_EMITTED:
                emit_quicknode_live_lane_started(
                    connection, rpc_client=rpc_client,
                    deployment_commit_sha=os.getenv('BACKEND_GIT_COMMIT') or os.getenv('RAILWAY_GIT_COMMIT_SHA'),
                )
                _LIVE_LANE_STARTED_EMITTED = True
            # Migrate the legacy `base` delivery checkpoint into the backfill lane so
            # history is walked exactly once, without ever seeding the live lane from
            # that old block. Idempotent + never regresses an advancing cursor.
            seed_backfill_from_base_checkpoint(connection)
            # Explicit override: seed the historical backfill lane from
            # QUICKNODE_BACKFILL_START_BLOCK when set (e.g. a specific missed-block
            # incident range). Idempotent; no-op once the lane has a cursor.
            seed_block = backfill_start_block()
            if seed_block is not None:
                seed_backfill_checkpoint(connection, start_block=seed_block)
            targets = _load_all_base_wallet_targets(connection)
            # The live tip lane always runs — it is the whole point of this worker and
            # must never be delayed by the historical backlog.
            live_stats = run_live_tip_ingest(
                connection, rpc_client=rpc_client, targets=targets, now=now,
            )
            # The lower-priority historical lane is independently gateable
            # (QUICKNODE_BACKFILL_ENABLED=false suspends catch-up without touching the
            # tip or Stable RPC Polling).
            backfill_stats: dict[str, object] | None = None
            if quicknode_backfill_enabled():
                backfill_stats = run_backfill_step(
                    connection, rpc_client=rpc_client, targets=targets,
                    live_start_block=live_stats.get('checkpoint_after'), now=now,
                )
            return {'status': 'processed', 'live': live_stats, 'backfill': backfill_stats}
        finally:
            release_live_lane_lock(connection)


def _live_worker_config_error() -> str | None:
    """Return a human-readable reason the enabled worker cannot run, else None.

    The RPC-poller live worker walks the Base chain tip via JSON-RPC, so a Base RPC
    endpoint is REQUIRED. Resolved exactly the way Stable RPC Polling resolves it
    (EVM_RPC_URL_8453 / BASE_EVM_RPC_URL / EVM_RPC_URL); if none is configured the
    worker cannot make progress and must fail loudly rather than idle silently.
    """
    from services.api.app.quicknode_streams import _make_base_rpc_client

    try:
        if _make_base_rpc_client() is None:
            return 'base_rpc_not_configured (set EVM_RPC_URL_8453 / BASE_EVM_RPC_URL / EVM_RPC_URL)'
    except Exception as exc:  # pragma: no cover - defensive resolution guard
        return f'base_rpc_resolution_failed:{type(exc).__name__}'
    return None


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    commit_sha = os.getenv('BACKEND_GIT_COMMIT') or os.getenv('RAILWAY_GIT_COMMIT_SHA') or 'unavailable'
    if not _bool_env('QUICKNODE_LIVE_ENABLED', False):
        # Emit the disabled marker on EVERY idle tick (not just once at boot) so an
        # operator can prove from a fresh Railway log tail that the live lane is
        # deployed-but-off — the most likely reason QuickNode is not detecting at the
        # chain tip in production — rather than seeing silence and guessing.
        while True:  # keep the container alive without burning RPC budget
            logger.info(
                'event=quicknode_live_lane_disabled deployment_commit_sha=%s deployment_has_worker=true '
                'enabled=false reason=QUICKNODE_LIVE_ENABLED_not_true '
                'action=set_QUICKNODE_LIVE_ENABLED_true_to_run_chain_tip_lane '
                '(stable RPC polling remains the fallback)',
                commit_sha,
            )
            time.sleep(DEFAULT_IDLE_INTERVAL_SECONDS)
    # Enabled: validate required configuration up front. A missing Base RPC is a
    # deploy-time misconfiguration — fail loudly (non-zero exit) so Railway marks the
    # service failed and restarts it visibly, instead of silently idling every tick.
    config_error = _live_worker_config_error()
    if config_error:
        logger.error(
            'event=quicknode_live_lane_configuration_error severity=high deployment_commit_sha=%s '
            'enabled=true reason=%s',
            commit_sha, config_error,
        )
        return 2
    interval = _poll_interval_seconds()
    logger.info(
        'quicknode_live_worker_started deployment_commit_sha=%s poll_interval_seconds=%s '
        'backfill_enabled=%s',
        commit_sha, interval, str(_bool_env('QUICKNODE_BACKFILL_ENABLED', True)).lower(),
    )
    while True:
        try:
            run_one_tick()
        except Exception:  # pragma: no cover - loop must survive a transient failure
            logger.warning('quicknode_live_worker_tick_failed', exc_info=True)
        time.sleep(interval)


if __name__ == '__main__':
    raise SystemExit(main())
