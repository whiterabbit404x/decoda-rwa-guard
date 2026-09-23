"""External watchlist worker cycle.

One cycle, bounded in every dimension:

  1. heartbeat                    the worker is alive (its own fact)
  2. historical backfills         claimed with a lease (FOR UPDATE SKIP LOCKED)
  3. live polling                 targets due for a poll, claimed with a lease
  4. status snapshots             re-derived from facts for touched protocols
  5. heartbeat + cycle summary

All RPC traffic in a cycle shares one ``CallBudget`` and goes through the
read-only gateway. Each network's endpoint is verified to serve the expected
chain before any target on it is read (fail closed on a mismatch). A dedicated
EXTERNAL_WATCHLIST_RPC_URL_<chain_id> keeps this traffic off the provider quota
customer monitoring uses.

Runs as its own process (``run_external_watchlist_worker``); never inside an
API request, so a 30-day backfill cannot slow a page down.
"""

from __future__ import annotations

import logging
import os
import socket
import time
from datetime import datetime, timezone
from typing import Any, Callable

from services.api.app import pilot
from services.api.app.domains.external_watchlist import backfill
from services.api.app.domains.external_watchlist import config as ewc
from services.api.app.domains.external_watchlist import detection
from services.api.app.domains.external_watchlist import ingest
from services.api.app.domains.external_watchlist import rpc
from services.api.app.domains.external_watchlist import service
from services.api.app.domains.external_watchlist import status as status_model

logger = logging.getLogger(__name__)


def worker_id() -> str:
    instance = (os.getenv('RAILWAY_REPLICA_ID') or os.getenv('HOSTNAME') or socket.gethostname() or 'local').strip()
    return f'external-watchlist-{instance[:64]}:{os.getpid()}'


def resolve_startup_state(config: dict[str, Any] | None = None) -> tuple[str, list[str]]:
    cfg = config or ewc.worker_config()
    if not cfg['enabled']:
        return 'disabled', []
    errors = []
    if not pilot.database_url():
        errors.append('DATABASE_URL is required for the External Watchlist worker.')
    return ('configuration_error', errors) if errors else ('enabled', [])


def network_context(
    network: str, *, budget: rpc.CallBudget, client_factory: Callable[..., rpc.ReadOnlyRpcClient] | None = None,
) -> backfill.NetworkContext:
    """Build, verify and anchor one network's read-only client for this cycle."""
    factory = client_factory or rpc.build_client
    meta = ewc.SUPPORTED_NETWORKS.get(network)
    if meta is None:
        return backfill.NetworkContext(network, None, error='unsupported network', error_code='UNSUPPORTED_NETWORK')
    try:
        client = factory(network, budget=budget)
        rpc.verify_chain(client, int(meta['chain_id']))
        tip = rpc.block_number(client)
        tip_ts = rpc.block_timestamp(client, tip)
        if tip_ts is None:
            raise RuntimeError('chain tip has no timestamp')
        return backfill.NetworkContext(network, client, tip=tip, tip_timestamp=tip_ts)
    except rpc.RpcBudgetExhausted:
        raise
    except (rpc.RpcNotConfigured, rpc.ChainMismatch) as exc:
        return backfill.NetworkContext(network, None, error=rpc.sanitize_error(exc), error_code=exc.code)
    except Exception as exc:  # noqa: BLE001 - provider outage: reported, retried next cycle
        return backfill.NetworkContext(network, None, error=rpc.sanitize_error(exc), error_code='RPC_UNAVAILABLE')


def claim_backfills(connection: Any, *, owner: str, config: dict[str, Any]) -> list[dict[str, Any]]:
    rows = service._rows(connection.execute(
        """
        WITH claimable AS (
            SELECT b.id FROM external_watchlist_backfills b
            JOIN external_watchlist_targets t ON t.id = b.target_id
            JOIN external_watchlists w ON w.id = b.watchlist_id AND w.deleted_at IS NULL
            WHERE b.status IN ('pending', 'running')
              AND (b.lease_expires_at IS NULL OR b.lease_expires_at < NOW())
              AND (t.removed_at IS NOT NULL OR (t.monitoring_enabled AND w.monitoring_enabled))
            ORDER BY b.created_at ASC
            LIMIT %s
            FOR UPDATE OF b SKIP LOCKED
        )
        UPDATE external_watchlist_backfills b
        SET lease_owner = %s, lease_expires_at = NOW() + (%s || ' seconds')::interval,
            status = 'running', started_at = COALESCE(b.started_at, NOW()), updated_at = NOW()
        FROM claimable WHERE b.id = claimable.id
        RETURNING b.*
        """,
        (int(config['max_backfills_per_cycle']), owner, str(config['lease_seconds'])),
    ).fetchall())
    connection.commit()
    return rows


def claim_poll_targets(connection: Any, *, owner: str, config: dict[str, Any]) -> list[dict[str, Any]]:
    rows = service._rows(connection.execute(
        """
        WITH claimable AS (
            SELECT t.id FROM external_watchlist_targets t
            JOIN external_watchlists w ON w.id = t.watchlist_id AND w.deleted_at IS NULL AND w.monitoring_enabled
            WHERE t.removed_at IS NULL AND t.monitoring_enabled
              AND (t.lease_expires_at IS NULL OR t.lease_expires_at < NOW())
              AND (t.last_polled_at IS NULL OR t.last_polled_at < NOW() - (%s || ' seconds')::interval)
            ORDER BY t.last_polled_at ASC NULLS FIRST, t.created_at ASC
            LIMIT %s
            FOR UPDATE OF t SKIP LOCKED
        )
        UPDATE external_watchlist_targets t
        SET lease_owner = %s, lease_expires_at = NOW() + (%s || ' seconds')::interval
        FROM claimable WHERE t.id = claimable.id
        RETURNING t.*
        """,
        (
            str(max(10, int(config['interval_seconds']) - 5)), int(config['max_targets_per_cycle']),
            owner, str(config['lease_seconds']),
        ),
    ).fetchall())
    connection.commit()
    return rows


def _record_poll(connection: Any, target_id: str, *, error: str | None) -> None:
    if error is None:
        connection.execute(
            """UPDATE external_watchlist_targets
               SET last_polled_at = NOW(), last_successful_poll_at = NOW(), consecutive_failures = 0,
                   last_poll_error = NULL, lease_owner = NULL, lease_expires_at = NULL, updated_at = NOW()
               WHERE id = %s""",
            (target_id,),
        )
    else:
        connection.execute(
            """UPDATE external_watchlist_targets
               SET last_polled_at = NOW(), consecutive_failures = consecutive_failures + 1,
                   last_poll_error = %s, lease_owner = NULL, lease_expires_at = NULL, updated_at = NOW()
               WHERE id = %s""",
            (error[:500], target_id),
        )
    connection.commit()


def _release_target(connection: Any, target_id: str) -> None:
    connection.execute(
        'UPDATE external_watchlist_targets SET lease_owner = NULL, lease_expires_at = NULL WHERE id = %s',
        (target_id,),
    )
    connection.commit()


def poll_target(
    connection: Any, *, target: dict[str, Any], watchlist: dict[str, Any], net: backfill.NetworkContext,
    config: dict[str, Any], now: datetime, sleep: Callable[[float], None],
) -> dict[str, Any]:
    """Read new blocks for one target up to the confirmed tip."""
    target_id = str(target['id'])
    if not net.ready:
        _record_poll(connection, target_id, error=f'{net.error_code}: {net.error}')
        return {'target_id': target_id, 'state': 'failed', 'reason': net.error_code}
    try:
        meta = ewc.SUPPORTED_NETWORKS[target['network']]
        safe_tip = int(net.tip) - ewc.confirmations_for(target['network'], config)
        last = target.get('last_processed_block')
        if last is None:
            last = (int(target['live_start_block']) - 1) if target.get('live_start_block') else safe_tip
        start, end = int(last) + 1, min(safe_tip, int(last) + int(config['max_blocks_per_poll']))
        rule_ctx = detection.build_context(watchlist=watchlist, target=target)
        chunk_ctx = ingest.ChunkContext(
            watchlist=watchlist, target=target, rule_ctx=rule_ctx,
            rule_state=ingest.load_rule_state(connection, target), client=net.client,
            ingest_source=ingest.INGEST_LIVE, anchors=[(int(net.tip), int(net.tip_timestamp))],
            avg_block_seconds=float(meta['avg_block_seconds']), now=now,
        )
        events = findings = 0
        if start <= end:
            for chunk in rpc.iter_log_chunks(
                net.client, filters=backfill.filters_for(target, watchlist), from_block=start, to_block=end,
                chunk_size=int(config['backfill_chunk_blocks']), min_chunk_size=int(config['min_chunk_blocks']),
                max_retries=int(config['chunk_max_retries']), backoff_seconds=float(config['retry_backoff_seconds']),
                pacing_seconds=float(config['rpc_pacing_seconds']), sleep=sleep,
            ):
                chunk_ctx.enrichment_limit = int(config['max_enrichments_per_chunk'])
                stats = ingest.process_logs(connection, chunk_ctx, chunk.logs)
                events += stats['events_inserted']
                findings += stats['findings_created']
                connection.execute(
                    'UPDATE external_watchlist_targets SET last_processed_block = %s, updated_at = NOW() WHERE id = %s',
                    (chunk.to_block, target_id),
                )
                connection.commit()
        heartbeat_drafts = detection.evaluate_heartbeat(
            rule_ctx, state=chunk_ctx.rule_state, now=now, network=target['network'], chain_id=int(target['chain_id']),
        )
        if heartbeat_drafts:
            findings += ingest.store_findings(connection, chunk_ctx, heartbeat_drafts, primary_event_id=None)
            connection.commit()
        _record_poll(connection, target_id, error=None)
        return {'target_id': target_id, 'state': 'ok', 'events_inserted': events, 'findings_created': findings,
                'from_block': start, 'to_block': end}
    except rpc.RpcBudgetExhausted:
        connection.rollback()
        _release_target(connection, target_id)
        return {'target_id': target_id, 'state': 'budget_exhausted'}
    except Exception as exc:  # noqa: BLE001 - one target never stops the cycle
        connection.rollback()
        error = rpc.sanitize_error(exc)
        logger.warning('external_watchlist_poll_failed target_id=%s error=%s', target_id, error)
        _record_poll(connection, target_id, error=error)
        return {'target_id': target_id, 'state': 'failed', 'error': error}


def refresh_statuses(connection: Any, watchlist_ids: set[str], *, now: datetime, config: dict[str, Any]) -> None:
    """Write the derived status snapshot for protocols this cycle touched."""
    if not watchlist_ids:
        return
    ids = sorted(watchlist_ids)
    targets = service.list_targets(connection, ids)
    jobs = service.latest_backfills(connection, ids)
    heartbeat = service.latest_worker_heartbeat(connection)
    for watchlist_id in ids:
        watchlist = service.get_watchlist(connection, watchlist_id)
        if watchlist is None:
            continue
        derived = status_model.derive_status(
            watchlist=watchlist,
            targets=[t for t in targets if str(t['watchlist_id']) == watchlist_id],
            latest_backfills=[j for j in jobs if str(j['watchlist_id']) == watchlist_id],
            worker_heartbeat_at=heartbeat, now=now, config=config,
        )
        if derived['status'] != watchlist.get('status'):
            connection.execute(
                'UPDATE external_watchlists SET status = %s, updated_at = NOW() WHERE id = %s',
                (derived['status'], watchlist_id),
            )
    connection.commit()


def run_worker_once(
    config: dict[str, Any] | None = None,
    *,
    now: datetime | None = None,
    client_factory: Callable[..., rpc.ReadOnlyRpcClient] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    connection_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    cfg = config or ewc.worker_config()
    if not cfg['enabled']:
        return {'state': 'disabled'}
    moment = now or datetime.now(timezone.utc)
    owner = worker_id()
    budget = rpc.CallBudget(int(cfg['max_rpc_calls_per_cycle']))
    networks: dict[str, backfill.NetworkContext] = {}
    summary: dict[str, Any] = {
        'backfills_claimed': 0, 'backfill_results': {}, 'targets_polled': 0, 'poll_failures': 0,
        'events_inserted': 0, 'findings_created': 0, 'rpc_calls': 0, 'budget_exhausted': False,
    }
    touched: set[str] = set()
    open_connection = connection_factory or pilot.pg_connection

    def network(name: str) -> backfill.NetworkContext:
        if name not in networks:
            networks[name] = network_context(name, budget=budget, client_factory=client_factory)
        return networks[name]

    with open_connection() as connection:
        if not service.schema_ready(connection):
            logger.warning('external_watchlist_worker_schema_missing migration=0157_external_watchlist.sql')
            return {'state': 'schema_missing'}
        service.record_worker_heartbeat(connection, worker_name=owner)
        connection.commit()
        try:
            for job in claim_backfills(connection, owner=owner, config=cfg):
                summary['backfills_claimed'] += 1
                touched.add(str(job['watchlist_id']))
                target = service._row(connection.execute(
                    'SELECT * FROM external_watchlist_targets WHERE id = %s AND removed_at IS NULL',
                    (str(job['target_id']),),
                ).fetchone())
                watchlist = service.get_watchlist(connection, str(job['watchlist_id']))
                try:
                    net = network(str(job['network']))
                except rpc.RpcBudgetExhausted:
                    backfill._release(connection, str(job['id']))
                    summary['budget_exhausted'] = True
                    break
                result = backfill.run_job(
                    connection, job=job, target=target, watchlist=watchlist,
                    net=net, config=cfg, now=moment, sleep=sleep,
                )
                state = str(result.get('state'))
                summary['backfill_results'][state] = summary['backfill_results'].get(state, 0) + 1
                if state == 'budget_exhausted':
                    summary['budget_exhausted'] = True
                    break
            if not summary['budget_exhausted']:
                watchlists: dict[str, dict[str, Any] | None] = {}
                for target in claim_poll_targets(connection, owner=owner, config=cfg):
                    watchlist_id = str(target['watchlist_id'])
                    touched.add(watchlist_id)
                    if watchlist_id not in watchlists:
                        watchlists[watchlist_id] = service.get_watchlist(connection, watchlist_id)
                    watchlist = watchlists[watchlist_id]
                    if watchlist is None:
                        _release_target(connection, str(target['id']))
                        continue
                    if summary['budget_exhausted']:
                        _release_target(connection, str(target['id']))
                        continue
                    try:
                        net = network(str(target['network']))
                    except rpc.RpcBudgetExhausted:
                        _release_target(connection, str(target['id']))
                        summary['budget_exhausted'] = True
                        continue
                    result = poll_target(
                        connection, target=target, watchlist=watchlist, net=net,
                        config=cfg, now=moment, sleep=sleep,
                    )
                    summary['targets_polled'] += 1
                    if result['state'] == 'failed':
                        summary['poll_failures'] += 1
                    if result['state'] == 'budget_exhausted':
                        summary['budget_exhausted'] = True
                    summary['events_inserted'] += int(result.get('events_inserted') or 0)
                    summary['findings_created'] += int(result.get('findings_created') or 0)
        except rpc.RpcBudgetExhausted:
            connection.rollback()
            summary['budget_exhausted'] = True
        summary['rpc_calls'] = budget.used
        summary['networks'] = {
            name: {'ready': ctx.ready, 'error_code': ctx.error_code, 'tip': ctx.tip} for name, ctx in networks.items()
        }
        service.record_worker_heartbeat(
            connection, worker_name=owner, summary=summary,
            failed=bool(summary['poll_failures']) and not summary['targets_polled'] > summary['poll_failures'],
            completed=True,
        )
        connection.commit()
        refresh_statuses(connection, touched, now=moment, config=cfg)
    return summary
