"""Monitoring status for an external protocol, derived from stored facts.

Pure. Three facts are read separately and never substituted for each other
(CLAUDE.md rule 3):

  heartbeat   the external watchlist worker is alive
  poll        the worker read the chain for a target recently
  telemetry   on-chain events actually arrived (reported, but NOT required
              for "live": a quiet contract is still monitored)

Precedence, fail-closed:

  paused       monitoring switched off, or nothing left to monitor
  error        a network has no RPC configured, or a target keeps failing
  backfilling  a history scan is open AND the worker is alive to run it
  degraded     worker silent, a target never / not recently polled, a failed
               or partial history scan, or recent poll failures
  live         every enabled target was polled recently, no scan is open

"Live" is never the default. A protocol whose worker is not running reports
degraded — not the last status anyone wrote down.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from services.api.app.domains.external_watchlist import config as ewc


def _aware(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _age_seconds(value: Any, now: datetime) -> float | None:
    moment = _aware(value)
    return None if moment is None else (now - moment).total_seconds()


def backfill_progress(job: dict[str, Any] | None) -> dict[str, Any] | None:
    """Progress of one history scan, for the UI's "21 / 30 days analyzed"."""
    if not job:
        return None
    total = int(job.get('total_blocks') or 0)
    scanned = int(job.get('scanned_blocks') or 0)
    status = str(job.get('status') or 'pending')
    days = int(job.get('requested_days') or 0)
    if status == 'completed':
        fraction = 1.0
    elif total > 0:
        fraction = max(0.0, min(1.0, scanned / total))
    else:
        fraction = 0.0
    return {
        'status': status,
        'requested_days': days,
        'percent': round(fraction * 100, 1),
        'days_analyzed': round(fraction * days, 1) if days else 0,
        'scanned_blocks': scanned,
        'total_blocks': total,
        'planned': bool(job.get('planned_at')),
        'events_inserted': int(job.get('events_inserted') or 0),
        'status_reason': job.get('status_reason'),
        'last_error': job.get('last_error'),
        'started_at': job.get('started_at'),
        'completed_at': job.get('completed_at'),
        'created_at': job.get('created_at'),
    }


def derive_status(
    *,
    watchlist: dict[str, Any],
    targets: list[dict[str, Any]],
    latest_backfills: list[dict[str, Any]],
    worker_heartbeat_at: Any,
    now: datetime | None = None,
    config: dict[str, Any] | None = None,
    rpc_configured: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    moment = now or datetime.now(timezone.utc)
    cfg = config or ewc.worker_config()
    is_configured = rpc_configured or ewc.rpc_configured

    if not watchlist.get('monitoring_enabled', True):
        return {'status': 'paused', 'reason': 'monitoring_paused'}
    enabled = [t for t in targets if t.get('monitoring_enabled', True) and not t.get('removed_at')]
    if not enabled:
        return {'status': 'paused', 'reason': 'no_active_targets' if not targets else 'all_targets_paused'}

    for target in enabled:
        if not is_configured(str(target.get('network') or '')):
            return {'status': 'error', 'reason': 'rpc_not_configured', 'network': target.get('network')}
    threshold = int(cfg['error_failure_threshold'])
    failing = [t for t in enabled if int(t.get('consecutive_failures') or 0) >= threshold]
    if failing:
        return {'status': 'error', 'reason': 'repeated_poll_failures', 'targets': len(failing)}

    heartbeat_age = _age_seconds(worker_heartbeat_at, moment)
    worker_alive = heartbeat_age is not None and heartbeat_age <= float(cfg['worker_heartbeat_stale_seconds'])
    enabled_ids = {str(t.get('id')) for t in enabled}
    relevant = [job for job in latest_backfills if str(job.get('target_id')) in enabled_ids]
    open_jobs = [job for job in relevant if job.get('status') in ewc.OPEN_BACKFILL_STATUSES]
    if open_jobs:
        if worker_alive:
            return {'status': 'backfilling', 'reason': 'history_scan_in_progress'}
        return {'status': 'degraded', 'reason': 'worker_not_running'}
    if not worker_alive:
        return {'status': 'degraded', 'reason': 'worker_not_running'}

    stale = float(cfg['poll_stale_seconds'])
    for target in enabled:
        age = _age_seconds(target.get('last_successful_poll_at'), moment)
        if age is None:
            return {'status': 'degraded', 'reason': 'awaiting_first_poll'}
        if age > stale:
            return {'status': 'degraded', 'reason': 'poll_stale'}
    if any(job.get('status') in ('failed', 'partial') for job in relevant):
        return {'status': 'degraded', 'reason': 'history_scan_incomplete'}
    if any(int(t.get('consecutive_failures') or 0) > 0 for t in enabled):
        return {'status': 'degraded', 'reason': 'recent_poll_failures'}
    return {'status': 'live', 'reason': None}


STATUS_REASON_LABELS = {
    'monitoring_paused': 'Monitoring paused',
    'no_active_targets': 'No monitored targets',
    'all_targets_paused': 'All targets paused',
    'rpc_not_configured': 'No RPC endpoint configured for this network',
    'repeated_poll_failures': 'Repeated poll failures',
    'history_scan_in_progress': 'Historical backfill in progress',
    'worker_not_running': 'Monitoring worker is not reporting a heartbeat',
    'awaiting_first_poll': 'Awaiting first successful poll',
    'poll_stale': 'Last successful poll is stale',
    'history_scan_incomplete': 'Historical backfill incomplete',
    'recent_poll_failures': 'Recent poll failures',
}


def rpc_health(targets: list[dict[str, Any]], *, now: datetime | None = None, config: dict[str, Any] | None = None,
               rpc_configured: Callable[[str], bool] | None = None) -> list[dict[str, Any]]:
    """Per-network RPC health from recorded poll outcomes. No live probing: a
    page load never waits on a provider; the diagnostic action does that."""
    moment = now or datetime.now(timezone.utc)
    cfg = config or ewc.worker_config()
    is_configured = rpc_configured or ewc.rpc_configured
    by_network: dict[str, list[dict[str, Any]]] = {}
    for target in targets:
        by_network.setdefault(str(target.get('network') or ''), []).append(target)
    result = []
    for network, rows in sorted(by_network.items()):
        if not is_configured(network):
            state = 'not_configured'
        else:
            ages = [_age_seconds(t.get('last_successful_poll_at'), moment) for t in rows]
            failures = max((int(t.get('consecutive_failures') or 0) for t in rows), default=0)
            if failures >= int(cfg['error_failure_threshold']):
                state = 'failing'
            elif failures > 0:
                state = 'degraded'
            elif any(age is None for age in ages):
                state = 'unknown'
            elif any(age > float(cfg['poll_stale_seconds']) for age in ages if age is not None):
                state = 'stale'
            else:
                state = 'healthy'
        last_error = next((t.get('last_poll_error') for t in rows if t.get('last_poll_error')), None)
        result.append({
            'network': network,
            'network_label': ewc.SUPPORTED_NETWORKS.get(network, {}).get('label', network),
            'state': state,
            'last_error': last_error,
            'last_processed_block': max((int(t['last_processed_block']) for t in rows if t.get('last_processed_block') is not None), default=None),
        })
    return result
