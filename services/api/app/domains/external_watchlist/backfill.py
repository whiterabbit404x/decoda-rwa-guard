"""Historical backfill for external targets.

Runs only in the external watchlist worker, never in an API request: the API
creates a ``pending`` row and returns.

Planning (first time the worker picks the job up)
  1. Approximate the block at ``now - requested_days`` from the network's block
     time, corrected with measured timestamps (``rpc.estimate_block_at_timestamp``).
  2. The window ends just before the target's live tail began
     (``live_start_block - 1``), so backfill and live polling never overlap.
  3. Subtract every range an earlier backfill of this target already scanned.
     A re-run therefore reads only what is missing — resuming a partial scan,
     or extending 7 days of history to 30 — and never re-reads history.

Execution
  Oldest-first chunks through ``rpc.iter_log_chunks`` (adaptive range, bounded
  retries with backoff, provider failover underneath). Each chunk's events,
  findings and the job's progress commit TOGETHER, so a crash or a budget stop
  resumes from exactly the last committed block.

Terminal states
  completed   every planned block scanned
  partial     some blocks scanned, then a range kept failing after retries
              across cycles (or the target was removed)
  failed      nothing scanned (no RPC for the chain, chain mismatch, or the
              first range kept failing)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from services.api.app.domains.external_watchlist import abi
from services.api.app.domains.external_watchlist import config as ewc
from services.api.app.domains.external_watchlist import detection
from services.api.app.domains.external_watchlist import ingest
from services.api.app.domains.external_watchlist import rpc
from services.api.app.domains.external_watchlist import service

logger = logging.getLogger(__name__)

#: Cycles a job may fail on the same range before it stops as partial/failed.
MAX_JOB_ATTEMPTS = 3
#: Cycles a job may wait on an unreachable provider before it stops.
MAX_OUTAGE_ATTEMPTS = 10


@dataclass
class NetworkContext:
    network: str
    client: rpc.ReadOnlyRpcClient | None
    tip: int | None = None
    tip_timestamp: int | None = None
    error: str | None = None
    error_code: str | None = None

    @property
    def ready(self) -> bool:
        return self.client is not None and self.tip is not None and self.error is None


def merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted((int(a), int(b)) for a, b in ranges if a is not None and b is not None and a <= b):
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def subtract_ranges(window: tuple[int, int], covered: list[tuple[int, int]]) -> list[tuple[int, int]]:
    start, end = int(window[0]), int(window[1])
    if start > end:
        return []
    remaining: list[tuple[int, int]] = []
    cursor = start
    for c_start, c_end in merge_ranges(covered):
        if c_end < cursor:
            continue
        if c_start > end:
            break
        if c_start > cursor:
            remaining.append((cursor, min(end, c_start - 1)))
        cursor = max(cursor, c_end + 1)
        if cursor > end:
            break
    if cursor <= end:
        remaining.append((cursor, end))
    return remaining


def covered_spans(job: dict[str, Any]) -> list[tuple[int, int]]:
    """Block ranges this job has fully scanned, derived from its progress."""
    ranges = [tuple(r) for r in (job.get('plan_ranges') or []) if isinstance(r, (list, tuple)) and len(r) == 2]
    index = int(job.get('range_index') or 0)
    spans = [(int(a), int(b)) for a, b in ranges[:index]]
    cursor = job.get('cursor_block')
    if index < len(ranges) and cursor is not None and int(cursor) >= int(ranges[index][0]):
        spans.append((int(ranges[index][0]), min(int(cursor), int(ranges[index][1]))))
    return spans


def filters_for(target: dict[str, Any], watchlist: dict[str, Any]) -> list[dict[str, Any]]:
    profiles = watchlist.get('detection_profiles') if isinstance(watchlist.get('detection_profiles'), list) else list(ewc.DETECTION_PROFILE_KEYS)
    runtime = target.get('runtime_state') if isinstance(target.get('runtime_state'), dict) else {}
    extra = []
    aggregator = (runtime.get('oracle_meta') or {}).get('aggregator') if isinstance(runtime.get('oracle_meta'), dict) else None
    if target.get('target_type') == 'oracle' and abi.normalize_address(aggregator):
        extra.append(abi.normalize_address(aggregator))
    return abi.log_filters_for_target(
        target_type=str(target['target_type']), address=str(target['address']), profiles=profiles,
        extra_emitters=extra,
    )


def history_rule_state(job: dict[str, Any], range_index: int) -> dict[str, Any]:
    """The scan's own replayed state, valid only within the range it was saved in."""
    saved = job.get('rule_state') if isinstance(job.get('rule_state'), dict) else {}
    state = saved.get('state')
    if saved.get('range_index') == range_index and isinstance(state, dict):
        return dict(state)
    return {}


def plan_job(
    connection: Any, *, job: dict[str, Any], target: dict[str, Any], net: NetworkContext,
    config: dict[str, Any], now: datetime,
) -> dict[str, Any]:
    meta = ewc.SUPPORTED_NETWORKS[target['network']]
    window_end = (int(target['live_start_block']) - 1) if target.get('live_start_block') else int(net.tip)
    target_ts = int((now - timedelta(days=int(job['requested_days']))).timestamp())
    cache: dict[int, int] = {}
    estimate = rpc.estimate_block_at_timestamp(
        net.client, target_timestamp=target_ts, tip=int(net.tip), tip_timestamp=int(net.tip_timestamp),
        avg_block_seconds=float(meta['avg_block_seconds']), cache=cache,
    )
    start = min(int(estimate['block']), window_end)
    covered: list[tuple[int, int]] = []
    for other in service.backfills_for_target(connection, str(target['id'])):
        if str(other['id']) != str(job['id']):
            covered.extend(covered_spans(other))
    plan = subtract_ranges((start, window_end), covered) if start <= window_end else []
    total = sum(end - begin + 1 for begin, end in plan)
    window_start_ts = estimate.get('timestamp') or target_ts
    row = service._row(connection.execute(
        """UPDATE external_watchlist_backfills
           SET window_start_at = %s, estimated_start_block = %s, window_end_block = %s,
               plan_ranges = %s::jsonb, total_blocks = %s, planned_at = NOW(), range_index = 0,
               cursor_block = NULL, chunk_size = %s, updated_at = NOW()
           WHERE id = %s RETURNING *""",
        (
            datetime.fromtimestamp(int(window_start_ts), tz=timezone.utc), int(estimate['block']), window_end,
            service._dumps([[a, b] for a, b in plan]), total, int(config['backfill_chunk_blocks']), str(job['id']),
        ),
    ).fetchone())
    connection.commit()
    logger.info(
        'external_watchlist_backfill_planned job_id=%s target_id=%s days=%s start_block=%s end_block=%s ranges=%s blocks=%s',
        job['id'], target['id'], job['requested_days'], start, window_end, len(plan), total,
    )
    return row or job


def _finish(connection: Any, job_id: str, *, status: str, reason: str | None, error: str | None = None) -> None:
    connection.execute(
        """UPDATE external_watchlist_backfills
           SET status = %s, status_reason = %s, last_error = COALESCE(%s, last_error), completed_at = NOW(),
               lease_owner = NULL, lease_expires_at = NULL, updated_at = NOW()
           WHERE id = %s""",
        (status, reason, error, job_id),
    )
    connection.commit()


def _release(connection: Any, job_id: str, *, error: str | None = None, count_attempt: bool = False) -> None:
    connection.execute(
        """UPDATE external_watchlist_backfills
           SET lease_owner = NULL, lease_expires_at = NULL, last_error = COALESCE(%s, last_error),
               attempts = attempts + %s, updated_at = NOW()
           WHERE id = %s""",
        (error, 1 if count_attempt else 0, job_id),
    )
    connection.commit()


def run_job(
    connection: Any,
    *,
    job: dict[str, Any],
    target: dict[str, Any] | None,
    watchlist: dict[str, Any] | None,
    net: NetworkContext,
    config: dict[str, Any],
    now: datetime,
    sleep: Callable[[float], None],
) -> dict[str, Any]:
    """Advance one backfill as far as the cycle's RPC budget allows."""
    job_id = str(job['id'])
    if target is None or watchlist is None:
        _finish(connection, job_id, status='partial' if int(job.get('scanned_blocks') or 0) else 'failed',
                reason='target_removed')
        return {'job_id': job_id, 'state': 'target_removed'}
    if not net.ready:
        terminal = net.error_code in ('RPC_NOT_CONFIGURED', 'CHAIN_MISMATCH')
        if terminal:
            _finish(connection, job_id, status='partial' if int(job.get('scanned_blocks') or 0) else 'failed',
                    reason=str(net.error_code).lower(), error=net.error)
            return {'job_id': job_id, 'state': 'failed', 'reason': net.error_code}
        attempts = int(job.get('attempts') or 0) + 1
        if attempts >= MAX_OUTAGE_ATTEMPTS:
            # A provider that stays unreachable for many cycles ends the scan
            # truthfully instead of leaving it "in progress" indefinitely.
            connection.execute('UPDATE external_watchlist_backfills SET attempts = %s WHERE id = %s', (attempts, job_id))
            _finish(connection, job_id, status='partial' if int(job.get('scanned_blocks') or 0) else 'failed',
                    reason='rpc_unavailable', error=net.error)
            return {'job_id': job_id, 'state': 'failed', 'reason': 'rpc_unavailable'}
        _release(connection, job_id, error=net.error, count_attempt=True)
        return {'job_id': job_id, 'state': 'deferred', 'reason': net.error_code}

    try:
        if not job.get('planned_at'):
            job = plan_job(connection, job=job, target=target, net=net, config=config, now=now)
        plan = [tuple(r) for r in (job.get('plan_ranges') or [])]
        if not plan:
            _finish(connection, job_id, status='completed', reason='window_already_covered')
            return {'job_id': job_id, 'state': 'completed', 'blocks': 0}

        meta = ewc.SUPPORTED_NETWORKS[target['network']]
        rule_ctx = detection.build_context(watchlist=watchlist, target=target)
        index = int(job.get('range_index') or 0)
        history = history_rule_state(job, index)
        chunk_ctx = ingest.ChunkContext(
            watchlist=watchlist, target=target, rule_ctx=rule_ctx,
            rule_state=ingest.load_rule_state(connection, target, runtime_state=history), client=net.client,
            ingest_source=ingest.INGEST_BACKFILL, backfill_id=job_id, history_range_index=index,
            anchors=[(int(net.tip), int(net.tip_timestamp))] + (
                [(int(job['estimated_start_block']), int(job['window_start_at'].timestamp()))]
                if job.get('estimated_start_block') is not None and isinstance(job.get('window_start_at'), datetime) else []
            ),
            avg_block_seconds=float(meta['avg_block_seconds']),
            now=now,
        )
        filters = filters_for(target, watchlist)
        cursor = job.get('cursor_block')
        chunk_size = int(job.get('chunk_size') or config['backfill_chunk_blocks'])
        while index < len(plan):
            range_start, range_end = int(plan[index][0]), int(plan[index][1])
            if chunk_ctx.history_range_index != index:
                # A new disjoint range: blocks between it and the previous one
                # were scanned by another job, so nothing replayed so far is
                # known to be the state just before this range.
                chunk_ctx.history_range_index = index
                chunk_ctx.rule_state.runtime_state = {}
            start = int(cursor) + 1 if cursor is not None and int(cursor) >= range_start else range_start
            if start <= range_end:
                for chunk in rpc.iter_log_chunks(
                    net.client, filters=filters, from_block=start, to_block=range_end, chunk_size=chunk_size,
                    min_chunk_size=int(config['min_chunk_blocks']), max_retries=int(config['chunk_max_retries']),
                    backoff_seconds=float(config['retry_backoff_seconds']),
                    pacing_seconds=float(config['rpc_pacing_seconds']), sleep=sleep,
                ):
                    chunk_ctx.enrichment_limit = int(config['max_enrichments_per_chunk'])
                    stats = ingest.process_logs(connection, chunk_ctx, chunk.logs)
                    chunk_size = chunk.chunk_size
                    connection.execute(
                        """UPDATE external_watchlist_backfills
                           SET range_index = %s, cursor_block = %s,
                               scanned_blocks = LEAST(total_blocks, scanned_blocks + %s),
                               chunks_completed = chunks_completed + 1, retries = retries + %s,
                               logs_found = logs_found + %s, events_inserted = events_inserted + %s,
                               chunk_size = %s, lease_expires_at = NOW() + (%s || ' seconds')::interval,
                               updated_at = NOW()
                           WHERE id = %s""",
                        (
                            index, chunk.to_block, chunk.to_block - chunk.from_block + 1, chunk.retries,
                            stats['logs'], stats['events_inserted'], chunk_size, str(config['lease_seconds']), job_id,
                        ),
                    )
                    connection.commit()
            index += 1
            cursor = None
            connection.execute(
                """UPDATE external_watchlist_backfills SET range_index = %s, cursor_block = NULL, updated_at = NOW()
                   WHERE id = %s""",
                (index, job_id),
            )
            connection.commit()
        _finish(connection, job_id, status='completed', reason=None)
        return {'job_id': job_id, 'state': 'completed'}
    except rpc.RpcBudgetExhausted:
        connection.rollback()
        _release(connection, job_id)
        return {'job_id': job_id, 'state': 'budget_exhausted'}
    except (rpc.ChainMismatch, rpc.RpcNotConfigured, rpc.ExternalExecutionForbidden) as exc:
        connection.rollback()
        fresh = service._row(connection.execute(
            'SELECT scanned_blocks FROM external_watchlist_backfills WHERE id = %s', (job_id,),
        ).fetchone()) or {}
        _finish(connection, job_id, status='partial' if int(fresh.get('scanned_blocks') or 0) else 'failed',
                reason=getattr(exc, 'code', type(exc).__name__).lower(), error=rpc.sanitize_error(exc))
        return {'job_id': job_id, 'state': 'failed', 'reason': getattr(exc, 'code', type(exc).__name__)}
    except Exception as exc:  # noqa: BLE001 - includes ChunkFetchFailed
        connection.rollback()
        error = rpc.sanitize_error(exc)
        fresh = service._row(connection.execute(
            'SELECT attempts, scanned_blocks FROM external_watchlist_backfills WHERE id = %s', (job_id,),
        ).fetchone()) or {}
        attempts = int(fresh.get('attempts') or 0) + 1
        logger.warning('external_watchlist_backfill_chunk_failed job_id=%s attempts=%s error=%s', job_id, attempts, error)
        if attempts >= MAX_JOB_ATTEMPTS:
            connection.execute('UPDATE external_watchlist_backfills SET attempts = %s WHERE id = %s', (attempts, job_id))
            _finish(connection, job_id, status='partial' if int(fresh.get('scanned_blocks') or 0) else 'failed',
                    reason='range_failed_after_retries', error=error)
            return {'job_id': job_id, 'state': 'stopped', 'error': error}
        _release(connection, job_id, error=error, count_attempt=True)
        return {'job_id': job_id, 'state': 'retry_next_cycle', 'error': error}
