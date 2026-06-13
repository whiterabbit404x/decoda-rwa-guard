from __future__ import annotations

import argparse
import logging
import os
import socket
import time

from services.api.app.activity_providers import validate_monitoring_config_or_raise
from services.api.app.pilot import evaluate_monitoring_system_alerts
from services.api.app.monitoring_runner import run_monitoring_cycle
from services.api.app.observability import increment, gauge, observe, span, send_external_oncall_alert
from services.api.app.pilot import runtime_environment_identity, startup_schema_init_plan


def _resolve_git_commit_sha() -> str | None:
    for env_var in (
        'RAILWAY_GIT_COMMIT_SHA',
        'APP_BUILD_COMMIT',
        'SOURCE_COMMIT',
        'COMMIT_SHA',
    ):
        value = (os.getenv(env_var) or '').strip()
        if value:
            return value
    return None


def _default_worker_name() -> str:
    instance = (os.getenv('RAILWAY_REPLICA_ID') or os.getenv('HOSTNAME') or socket.gethostname() or 'local').strip()
    return f'monitoring-worker-{instance[:80]}'


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Run Decoda monitoring worker loop.')
    parser.add_argument('--worker-name', default=os.getenv('MONITORING_WORKER_NAME') or _default_worker_name())
    parser.add_argument('--interval-seconds', type=float, default=float(os.getenv('MONITORING_WORKER_INTERVAL_SECONDS', '15')))
    parser.add_argument('--limit', type=int, default=int(os.getenv('MONITORING_WORKER_LIMIT', '50')))
    parser.add_argument('--once', action='store_true')
    return parser.parse_args()


def _compute_next_sleep_seconds(
    *,
    worker_interval_seconds: float,
    effective_due_count: int,
    soonest_due_in_seconds: int | None,
    max_sleep_seconds: float = 30.0,
) -> float:
    base_sleep_seconds = max(1.0, float(worker_interval_seconds))
    next_sleep_seconds = base_sleep_seconds
    sleep_override_seconds: float | None = None
    if soonest_due_in_seconds is not None:
        sleep_override_seconds = min(
            base_sleep_seconds,
            max(1.0, float(soonest_due_in_seconds)),
        )
    if effective_due_count == 0 and sleep_override_seconds is not None:
        next_sleep_seconds = sleep_override_seconds
    return min(max_sleep_seconds, next_sleep_seconds)


def _resolve_worker_enabled_env() -> None:
    """
    Honor STAGING_WORKER_ENABLED (preferred) and WORKER_ENABLED (fallback)
    as aliases for LIVE_MODE_ENABLED so Railway staging workers start correctly
    without requiring a separate LIVE_MODE_ENABLED variable.
    """
    _truthy = {'1', 'true', 'yes', 'on'}
    staging_flag = (os.getenv('STAGING_WORKER_ENABLED') or '').strip().lower()
    base_flag = (os.getenv('WORKER_ENABLED') or '').strip().lower()
    if staging_flag in _truthy or base_flag in _truthy:
        os.environ.setdefault('LIVE_MODE_ENABLED', 'true')


def _log_startup_provider_status(logger: logging.Logger) -> dict:
    """Emit safe startup log lines for provider configuration. Never prints secrets.

    Returns a dict with keys:
      rpc_health_ok: True when RPC check passed, False when it failed, None when skipped.
      database_url_configured: True when DATABASE_URL is set.
    """
    from services.api.app.evm_activity_provider import _resolve_evm_rpc_url, probe_rpc_health
    from services.api.app.pilot import live_mode_enabled as _live_mode_enabled
    from urllib.parse import urlparse as _urlparse
    rpc_url = _resolve_evm_rpc_url()
    evm_rpc_configured = bool(rpc_url)
    database_url_configured = bool((os.getenv('DATABASE_URL') or '').strip())
    try:
        rpc_host = _urlparse(rpc_url).hostname or 'unconfigured'
    except Exception:
        rpc_host = 'unconfigured'
    _truthy = {'1', 'true', 'yes', 'on'}
    staging_worker_enabled = (os.getenv('STAGING_WORKER_ENABLED') or '').strip().lower() in _truthy
    base_worker_enabled = (os.getenv('WORKER_ENABLED') or '').strip().lower() in _truthy
    live_mode_env = (os.getenv('LIVE_MODE_ENABLED') or '').strip().lower() in _truthy
    worker_enabled = staging_worker_enabled or base_worker_enabled or live_mode_env
    live_mode_active = _live_mode_enabled()

    # Compute the enabling reason (which env var triggered it)
    if staging_worker_enabled:
        enabled_reason = 'STAGING_WORKER_ENABLED=true'
    elif base_worker_enabled:
        enabled_reason = 'WORKER_ENABLED=true'
    elif live_mode_env:
        enabled_reason = 'LIVE_MODE_ENABLED=true'
    else:
        enabled_reason = 'none_set — worker loop WILL NOT run'

    chain_id_raw = (os.getenv('STAGING_EVM_CHAIN_ID') or os.getenv('EVM_CHAIN_ID') or '').strip()
    chain_id_configured = int(chain_id_raw) if chain_id_raw.isdigit() else None
    chain_id_source = (
        'STAGING_EVM_CHAIN_ID' if (os.getenv('STAGING_EVM_CHAIN_ID') or '').strip().isdigit()
        else ('EVM_CHAIN_ID' if (os.getenv('EVM_CHAIN_ID') or '').strip().isdigit() else 'not_set')
    )
    interval_seconds = float(os.getenv('MONITORING_WORKER_INTERVAL_SECONDS', '15'))
    provider_mode = 'live' if (evm_rpc_configured and worker_enabled) else 'disabled'

    db_url_configured = bool((os.getenv('DATABASE_URL') or '').strip())
    logger.info(
        'startup service_role=worker live_mode_enabled=%s worker_enabled=%s enabled_reason=%s '
        'evm_rpc_configured=%s rpc_host=%s chain_id=%s chain_id_source=%s '
        'database_url_configured=%s polling_interval_seconds=%s provider_mode=%s',
        live_mode_active,
        worker_enabled,
        enabled_reason,
        evm_rpc_configured,
        rpc_host,
        chain_id_configured or 'not_set',
        chain_id_source,
        db_url_configured,
        interval_seconds,
        provider_mode,
    )
    if not db_url_configured:
        logger.warning(
            'worker_startup_no_database_url '
            'set DATABASE_URL in the Railway worker service environment'
        )
    if not worker_enabled:
        logger.warning(
            'worker_startup_DISABLED reason=no_enabling_env_var '
            'set STAGING_WORKER_ENABLED=true or WORKER_ENABLED=true or LIVE_MODE_ENABLED=true '
            'in the Railway worker service environment'
        )
    elif not evm_rpc_configured:
        logger.warning(
            'worker_startup_no_rpc_url reason=EVM_RPC_URL_missing '
            'polling will degrade — set EVM_RPC_URL or STAGING_EVM_RPC_URL '
            'in the Railway worker service environment'
        )
    if chain_id_configured is None:
        logger.warning(
            'worker_startup_no_chain_id reason=chain_id_missing '
            'set EVM_CHAIN_ID=8453 for Base mainnet in the worker service environment'
        )

    # Always perform an RPC health check at startup to surface connectivity issues
    # immediately in logs rather than waiting for the first monitoring cycle.
    rpc_health_ok: bool | None = None
    if evm_rpc_configured:
        try:
            health = probe_rpc_health()
        except Exception as exc:
            health = {'ok': False, 'error': str(exc)[:200], 'block_number_hex': None, 'block_number_int': None, 'chain_id_int': None}
        rpc_health_ok = bool(health.get('ok'))
        if health.get('ok'):
            logger.info(
                'startup_rpc_health_check status=ok rpc_host=%s '
                'eth_blockNumber_hex=%s block_number_decimal=%s chain_id=%s',
                rpc_host,
                health.get('block_number_hex') or 'missing',
                health.get('block_number_int'),
                health.get('chain_id_int'),
            )
            return {'rpc_health_ok': True, 'database_url_configured': db_url_configured}
        else:
            logger.error(
                'startup_rpc_health_check status=FAILED rpc_host=%s '
                'eth_blockNumber_hex=%s block_number_decimal=%s chain_id=%s rpc_error=%s '
                'action=worker_will_not_produce_live_telemetry',
                rpc_host,
                health.get('block_number_hex') or 'missing',
                health.get('block_number_int'),
                health.get('chain_id_int'),
                health.get('error') or 'unknown',
            )
            logger.warning(
                'startup_rpc_connectivity_FAILED '
                'Fix EVM_RPC_URL connectivity in the Railway worker service environment. '
                'No live chain telemetry will be inserted until RPC responds successfully.'
            )
            return {'rpc_health_ok': False, 'database_url_configured': db_url_configured}
    else:
        logger.info(
            'startup_rpc_health_check status=skipped reason=EVM_RPC_URL_not_configured rpc_host=%s',
            rpc_host,
        )
        return {'rpc_health_ok': None, 'database_url_configured': db_url_configured}


def main() -> int:
    logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO').upper(), format='%(asctime)s %(levelname)s %(name)s %(message)s')
    logger = logging.getLogger(__name__)
    _resolve_worker_enabled_env()
    args = parse_args()
    logger.info('monitoring worker starting')
    logger.info('startup_git_commit_sha service_role=worker git_commit_sha=%s', _resolve_git_commit_sha() or 'unavailable')
    _startup_status = _log_startup_provider_status(logger)
    rpc_healthy_at_startup = bool(_startup_status.get('rpc_health_ok'))
    identity = runtime_environment_identity()
    logger.info(
        'monitoring worker runtime identity app_mode=%s live_mode=%s railway_environment=%s railway_service=%s database_backend=%s database_fingerprint=%s',
        identity['app_mode'],
        identity['live_mode_enabled'],
        identity['railway_environment'] or 'unknown',
        identity['railway_service'] or 'unknown',
        identity['database_backend'],
        identity['database_fingerprint'],
    )
    logger.info(
        'monitoring worker config worker_name=%s interval_seconds=%s limit=%s once=%s',
        args.worker_name,
        args.interval_seconds,
        args.limit,
        args.once,
    )
    logger.info(
        'worker_startup worker_name=%s service_role=worker WORKER_ENABLED=true app_mode=%s live_mode=%s interval_seconds=%s limit=%s',
        args.worker_name,
        identity.get('app_mode'),
        identity.get('live_mode_enabled'),
        args.interval_seconds,
        args.limit,
    )
    validate_monitoring_config_or_raise()
    schema_plan = startup_schema_init_plan(process_role='worker')
    logger.info(
        'monitoring worker schema init skipped for role=%s: %s',
        schema_plan.get('process_role', 'worker'),
        schema_plan.get('reason', 'schema init disabled'),
    )
    # Initialize before the loop so `if not rpc_healthy` (re-check below) can never hit
    # an UnboundLocalError, even on the very first iteration or an early-failing cycle.
    rpc_healthy = rpc_healthy_at_startup or False
    if not rpc_healthy_at_startup:
        gauge('decoda_monitoring_worker_healthy', 0, worker=args.worker_name)
        logger.warning(
            'worker_initial_gauge=unhealthy reason=startup_rpc_health_check_failed '
            'worker will retry; gauge will be set to 1 after first successful monitoring cycle'
        )
    while True:
        try:
            cycle_started = time.monotonic()
            with span('monitoring.worker.cycle', worker_name=args.worker_name):
                summary = run_monitoring_cycle(worker_name=args.worker_name, limit=args.limit, trigger_type='scheduler')
            effective_due_count = int(summary.get('effective_due_count', summary.get('due_targets', 0)) or 0)
            soonest_due_in_seconds = summary.get('soonest_due_in_seconds')
            if soonest_due_in_seconds is not None:
                try:
                    soonest_due_in_seconds = int(soonest_due_in_seconds)
                except (TypeError, ValueError):
                    soonest_due_in_seconds = None
            next_sleep_seconds = _compute_next_sleep_seconds(
                worker_interval_seconds=args.interval_seconds,
                effective_due_count=effective_due_count,
                soonest_due_in_seconds=soonest_due_in_seconds,
            )
            observe('decoda_monitoring_cycle_duration_seconds', time.monotonic() - cycle_started, worker=args.worker_name)
            if not rpc_healthy:
                from services.api.app.evm_activity_provider import probe_rpc_health
                try:
                    recheck = probe_rpc_health()
                except Exception as recheck_exc:
                    recheck = {'ok': False, 'error': str(recheck_exc)[:200], 'block_number_hex': None, 'block_number_int': None}
                rpc_healthy = bool(recheck.get('ok'))
                if rpc_healthy:
                    logger.info(
                        'rpc_health_recovered eth_blockNumber_hex=%s block_number_decimal=%s',
                        recheck.get('block_number_hex') or 'missing',
                        recheck.get('block_number_int'),
                    )
                else:
                    logger.warning(
                        'worker_not_marked_healthy reason=eth_blockNumber_not_succeeded rpc_error=%s '
                        'worker stays unhealthy until the RPC health check passes',
                        recheck.get('error') or 'unknown',
                    )
            gauge('decoda_monitoring_worker_healthy', 1 if rpc_healthy else 0, worker=args.worker_name)
            increment('decoda_monitoring_targets_checked_total', int(summary.get('checked', 0) or 0), worker=args.worker_name)
            increment('decoda_detection_events_total', int(summary.get('alerts_generated', 0) or 0), worker=args.worker_name)
            self_monitoring = evaluate_monitoring_system_alerts(stale_after_seconds=max(60, int(args.interval_seconds) * 4))
            if any(self_monitoring.values()):
                increment('decoda_self_monitoring_findings_total', sum(self_monitoring.values()))
            if summary.get('proof_chain_failed') or summary.get('proof_chain_status') == 'failed':
                send_external_oncall_alert('proof_chain_failure', 'Monitoring proof chain failed.', worker=args.worker_name, summary=summary)
            logger.info(
                'monitoring cycle summary due=%s effective_due_count=%s checked=%s alerts=%s live_mode=%s soonest_due_in_seconds=%s next_sleep_seconds=%s',
                summary.get('due_targets', 0),
                effective_due_count,
                summary.get('checked', 0),
                summary.get('alerts_generated', 0),
                summary.get('live_mode', False),
                soonest_due_in_seconds,
                next_sleep_seconds,
            )
        except Exception as exc:
            gauge('decoda_monitoring_worker_healthy', 0, worker=args.worker_name)
            increment('decoda_monitoring_worker_failures_total', worker=args.worker_name)
            send_external_oncall_alert('missing_heartbeat', 'Monitoring worker cycle failed; heartbeat is at risk.', worker=args.worker_name, error_type=type(exc).__name__)
            logger.exception('monitoring worker cycle error')
            next_sleep_seconds = min(30.0, max(1.0, float(args.interval_seconds)))
        if args.once:
            logger.info('monitoring worker exiting after one cycle')
            return 0
        time.sleep(next_sleep_seconds)


if __name__ == '__main__':
    raise SystemExit(main())
