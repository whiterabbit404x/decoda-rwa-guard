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


def _log_startup_provider_status(logger: logging.Logger) -> None:
    """Emit safe startup log lines for provider configuration. Never prints secrets."""
    from services.api.app.evm_activity_provider import _resolve_evm_rpc_url
    rpc_url = _resolve_evm_rpc_url()
    evm_rpc_configured = bool(rpc_url)
    staging_worker_enabled = (os.getenv('STAGING_WORKER_ENABLED') or '').strip().lower() in {'1', 'true', 'yes', 'on'}
    base_worker_enabled = (os.getenv('WORKER_ENABLED') or '').strip().lower() in {'1', 'true', 'yes', 'on'}
    live_mode_env = (os.getenv('LIVE_MODE_ENABLED') or '').strip().lower() in {'1', 'true', 'yes', 'on'}
    worker_enabled = staging_worker_enabled or base_worker_enabled or live_mode_env
    chain_id_raw = (os.getenv('STAGING_EVM_CHAIN_ID') or os.getenv('EVM_CHAIN_ID') or '').strip()
    chain_id_configured = int(chain_id_raw) if chain_id_raw.isdigit() else None
    provider_mode = 'live' if (evm_rpc_configured and worker_enabled) else 'disabled'
    logger.info(
        'worker_startup_provider_status worker_enabled=%s evm_rpc_configured=%s chain_id_configured=%s provider_mode=%s',
        worker_enabled,
        evm_rpc_configured,
        chain_id_configured,
        provider_mode,
    )


def main() -> int:
    logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO').upper(), format='%(asctime)s %(levelname)s %(name)s %(message)s')
    logger = logging.getLogger(__name__)
    _resolve_worker_enabled_env()
    args = parse_args()
    logger.info('monitoring worker starting')
    _log_startup_provider_status(logger)
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
    validate_monitoring_config_or_raise()
    schema_plan = startup_schema_init_plan(process_role='worker')
    logger.info(
        'monitoring worker schema init skipped for role=%s: %s',
        schema_plan.get('process_role', 'worker'),
        schema_plan.get('reason', 'schema init disabled'),
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
            gauge('decoda_monitoring_worker_healthy', 1, worker=args.worker_name)
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
