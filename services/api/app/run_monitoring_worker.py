from __future__ import annotations

import argparse
import logging
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

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
    # Default 60s (was 15s) so the worker does not hammer the RPC provider. Override
    # with MONITORING_WORKER_INTERVAL_SECONDS or --interval-seconds when explicitly tuned.
    parser.add_argument('--interval-seconds', type=float, default=float(os.getenv('MONITORING_WORKER_INTERVAL_SECONDS', '60')))
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


def _rpc_recheck_backoff_seconds() -> float:
    """Initial backoff between RPC health rechecks while the worker is unhealthy.

    The per-cycle "am I healthy yet?" probe is a *redundant* eth_chainId +
    eth_blockNumber call on top of normal polling. Rechecking every cycle while
    the provider is rate-limiting only deepens the rate limit, so rechecks back
    off exponentially. Configurable via MONITORING_RPC_RECHECK_BACKOFF_SECONDS.
    """
    try:
        return max(1.0, float(os.getenv('MONITORING_RPC_RECHECK_BACKOFF_SECONDS', '60')))
    except (TypeError, ValueError):
        return 60.0


def _rpc_recheck_max_backoff_seconds() -> float:
    """Upper bound for the exponential RPC recheck backoff."""
    try:
        return max(
            _rpc_recheck_backoff_seconds(),
            float(os.getenv('MONITORING_RPC_RECHECK_MAX_BACKOFF_SECONDS', '600')),
        )
    except (TypeError, ValueError):
        return max(_rpc_recheck_backoff_seconds(), 600.0)


def _rpc_recheck_due(seconds_since_last_recheck: float, backoff_seconds: float) -> bool:
    """True when enough time has elapsed to attempt another RPC health recheck."""
    return seconds_since_last_recheck >= backoff_seconds


def _next_rpc_recheck_backoff(current_backoff: float, max_backoff: float) -> float:
    """Double the recheck backoff, capped at ``max_backoff`` (respects rate limits)."""
    return min(max_backoff, max(1.0, current_backoff) * 2)


def _resolve_worker_enabled_env() -> None:
    """
    Honor STAGING_WORKER_ENABLED, WORKER_ENABLED, and MONITORING_WORKER_ENABLED
    as aliases for LIVE_MODE_ENABLED so Railway workers start correctly without
    requiring a separate LIVE_MODE_ENABLED variable.
    """
    _truthy = {'1', 'true', 'yes', 'on'}
    staging_flag = (os.getenv('STAGING_WORKER_ENABLED') or '').strip().lower()
    base_flag = (os.getenv('WORKER_ENABLED') or '').strip().lower()
    monitoring_flag = (os.getenv('MONITORING_WORKER_ENABLED') or '').strip().lower()
    if staging_flag in _truthy or base_flag in _truthy or monitoring_flag in _truthy:
        os.environ.setdefault('LIVE_MODE_ENABLED', 'true')


_BASE_CHAIN_ID = 8453
_BASE_CHAIN_NAMES = ('base', 'base-mainnet')

# Per-chain RPC validation: (chain_name, expected_chain_id, env_var_names)
_PER_CHAIN_VALIDATIONS = [
    ('base', _BASE_CHAIN_ID, ('EVM_RPC_URL_8453', 'BASE_EVM_RPC_URL')),
]


def _validate_per_chain_rpcs(logger: logging.Logger, probe_cache: dict[str, dict] | None = None) -> None:
    """Probe each configured per-chain RPC and warn when the returned chainId doesn't match.

    Runs at worker startup so misrouted RPC URLs surface immediately in logs
    rather than silently writing wrong-chain block numbers to telemetry.

    ``probe_cache`` lets this reuse a probe the global startup check already made
    for the same URL, so the worker never fires two identical eth_blockNumber
    calls at boot (a "duplicate provider health check").
    """
    from services.api.app.evm_activity_provider import probe_rpc_health
    from urllib.parse import urlparse as _urlparse

    probe_cache = probe_cache or {}

    for chain_name, expected_chain_id, env_vars in _PER_CHAIN_VALIDATIONS:
        rpc_url = ''
        rpc_url_env = ''
        for env_var in env_vars:
            value = (os.getenv(env_var) or '').strip()
            if value:
                rpc_url = value
                rpc_url_env = env_var
                break
        if not rpc_url:
            continue
        try:
            rpc_host = _urlparse(rpc_url).hostname or 'unknown'
        except Exception:
            rpc_host = 'unknown'
        try:
            if rpc_url in probe_cache:
                health = probe_cache[rpc_url]
                logger.info(
                    'startup_per_chain_rpc_probe_reused chain=%s rpc_url_env=%s rpc_host=%s '
                    'reason=already_probed_by_global_startup_check',
                    chain_name, rpc_url_env, rpc_host,
                )
            else:
                health = probe_rpc_health(rpc_url)
        except Exception as exc:
            logger.error(
                'startup_per_chain_rpc_probe_failed chain=%s expected_chain_id=%s '
                'rpc_url_env=%s rpc_host=%s error=%s',
                chain_name, expected_chain_id, rpc_url_env, rpc_host, str(exc)[:200],
            )
            continue
        actual_chain_id = health.get('chain_id_int')
        if not health.get('ok'):
            logger.error(
                'startup_per_chain_rpc_unhealthy chain=%s expected_chain_id=%s '
                'rpc_url_env=%s rpc_host=%s rpc_error=%s',
                chain_name, expected_chain_id, rpc_url_env, rpc_host,
                health.get('error') or 'unknown',
            )
        elif actual_chain_id != expected_chain_id:
            logger.error(
                'startup_per_chain_rpc_chain_id_mismatch chain=%s expected_chain_id=%s '
                'actual_chain_id=%s rpc_url_env=%s rpc_host=%s '
                'action=worker_may_write_wrong_chain_telemetry '
                'fix=set_%s_to_a_%s_mainnet_json_rpc_endpoint',
                chain_name, expected_chain_id, actual_chain_id,
                rpc_url_env, rpc_host,
                rpc_url_env, chain_name,
            )
        else:
            logger.info(
                'startup_per_chain_rpc_ok chain=%s chain_id=%s rpc_url_env=%s rpc_host=%s '
                'eth_blockNumber=%s',
                chain_name, actual_chain_id, rpc_url_env, rpc_host,
                health.get('block_number_int'),
            )


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
    monitoring_worker_enabled = (os.getenv('MONITORING_WORKER_ENABLED') or '').strip().lower() in _truthy
    live_mode_env = (os.getenv('LIVE_MODE_ENABLED') or '').strip().lower() in _truthy
    worker_enabled = staging_worker_enabled or base_worker_enabled or monitoring_worker_enabled or live_mode_env
    live_mode_active = _live_mode_enabled()

    # Compute the enabling reason (which env var triggered it)
    if staging_worker_enabled:
        enabled_reason = 'STAGING_WORKER_ENABLED=true'
    elif base_worker_enabled:
        enabled_reason = 'WORKER_ENABLED=true'
    elif monitoring_worker_enabled:
        enabled_reason = 'MONITORING_WORKER_ENABLED=true'
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
    interval_seconds = float(os.getenv('MONITORING_WORKER_INTERVAL_SECONDS', '60'))
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

    # Base (chain 8453) is the canonical monitored chain. Log the resolution the
    # worker actually uses for Base targets (resolve_chain_rpc — the same resolver
    # the polling loop and /ops/system-health use) so operators can confirm, from
    # logs alone, that Base polling is wired to a real endpoint. Only the host is
    # ever printed — never the URL path, key, query, or credentials.
    try:
        from services.api.app.evm_activity_provider import resolve_chain_rpc as _resolve_chain
        _base_resolved = _resolve_chain('base')
        base_rpc_url = (_base_resolved.get('rpc_url') or '').strip()
        base_rpc_env = _base_resolved.get('rpc_url_env') or 'none'
    except Exception:
        base_rpc_url = ''
        base_rpc_env = 'none'
    base_rpc_configured = bool(base_rpc_url)
    try:
        base_rpc_host = _urlparse(base_rpc_url).hostname or 'unconfigured'
    except Exception:
        base_rpc_host = 'unconfigured'
    logger.info(
        'startup_base_rpc service_role=worker rpc_configured=%s rpc_host=%s rpc_url_env=%s '
        'chain_id=%s worker_enabled=%s polling_interval_seconds=%s',
        base_rpc_configured,
        base_rpc_host,
        base_rpc_env,
        _BASE_CHAIN_ID,
        worker_enabled,
        interval_seconds,
    )
    if base_rpc_configured and worker_enabled:
        logger.info(
            'startup_base_polling_active chain_id=%s rpc_host=%s polling_interval_seconds=%s',
            _BASE_CHAIN_ID,
            base_rpc_host,
            interval_seconds,
        )
    elif not base_rpc_configured:
        logger.warning(
            'worker_startup_base_rpc_missing '
            'Base RPC URL is missing in worker service. Set EVM_RPC_URL or STAGING_EVM_RPC_URL.'
        )

    if not db_url_configured:
        logger.warning(
            'worker_startup_no_database_url '
            'set DATABASE_URL in the Railway worker service environment'
        )
    if not worker_enabled:
        logger.warning(
            'worker_startup_DISABLED reason=no_enabling_env_var '
            'set STAGING_WORKER_ENABLED=true or WORKER_ENABLED=true or MONITORING_WORKER_ENABLED=true or LIVE_MODE_ENABLED=true '
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
    # Reuse the global probe for per-chain validation when the URLs match, so the
    # worker never fires two identical eth_blockNumber calls at boot.
    _startup_probe_cache: dict[str, dict] = {}
    if evm_rpc_configured:
        try:
            health = probe_rpc_health()
        except Exception as exc:
            health = {'ok': False, 'error': str(exc)[:200], 'block_number_hex': None, 'block_number_int': None, 'chain_id_int': None}
        if rpc_url:
            _startup_probe_cache[rpc_url] = health
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
    else:
        logger.info(
            'startup_rpc_health_check status=skipped reason=EVM_RPC_URL_not_configured rpc_host=%s',
            rpc_host,
        )

    # Validate per-chain RPC endpoints at startup — Base mainnet (chain_id=8453) must serve
    # chain 8453, not Ethereum. A misconfigured URL silently produces wrong block numbers.
    # Reuse the global probe when it already hit the same URL (avoids a duplicate call).
    _validate_per_chain_rpcs(logger, probe_cache=_startup_probe_cache)

    return {'rpc_health_ok': rpc_health_ok, 'database_url_configured': db_url_configured}


def _start_health_server(port: int, logger: logging.Logger) -> None:
    # Railway requires a passing healthcheck. The worker has no FastAPI app, so we
    # serve a bare-minimum /health endpoint from a daemon thread. The monitoring
    # loop is unaffected — it runs in the main thread as before.
    # Railway worker service config: set healthcheckPath=/health and expose $PORT.
    # API service config: unchanged — FastAPI already handles /health.
    class _HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == '/health':
                body = b'{"status":"ok","service":"monitoring-worker"}'
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass  # suppress per-request HTTP logs from the health server

    server = HTTPServer(('0.0.0.0', port), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True, name='worker-health-server')
    thread.start()
    logger.info('worker_health_server_started host=0.0.0.0 port=%s path=/health', port)


def main() -> int:
    logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO').upper(), format='%(asctime)s %(levelname)s %(name)s %(message)s')
    logger = logging.getLogger(__name__)
    _resolve_worker_enabled_env()
    args = parse_args()

    # Start the health server early so Railway's healthcheck passes while the
    # monitoring loop initialises (RPC probes, schema checks, etc.).
    _health_port_raw = (os.getenv('PORT') or '').strip()
    _health_port = int(_health_port_raw) if _health_port_raw.isdigit() else 8000
    _start_health_server(_health_port, logger)
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
    # RPC recheck backoff state. The startup probe already ran, so seed the clock now
    # to avoid an immediate redundant recheck on the first cycle.
    _rpc_recheck_backoff = _rpc_recheck_backoff_seconds()
    _rpc_recheck_max_backoff = _rpc_recheck_max_backoff_seconds()
    _last_rpc_recheck_monotonic = time.monotonic()
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
                _seconds_since_recheck = time.monotonic() - _last_rpc_recheck_monotonic
                if _rpc_recheck_due(_seconds_since_recheck, _rpc_recheck_backoff):
                    _last_rpc_recheck_monotonic = time.monotonic()
                    from services.api.app.evm_activity_provider import probe_rpc_health
                    try:
                        recheck = probe_rpc_health()
                    except Exception as recheck_exc:
                        recheck = {'ok': False, 'error': str(recheck_exc)[:200], 'block_number_hex': None, 'block_number_int': None}
                    rpc_healthy = bool(recheck.get('ok'))
                    if rpc_healthy:
                        _rpc_recheck_backoff = _rpc_recheck_backoff_seconds()  # reset on recovery
                        logger.info(
                            'rpc_health_recovered eth_blockNumber_hex=%s block_number_decimal=%s',
                            recheck.get('block_number_hex') or 'missing',
                            recheck.get('block_number_int'),
                        )
                    else:
                        # Back off the redundant probe so we never compound a rate limit.
                        _rpc_recheck_backoff = _next_rpc_recheck_backoff(_rpc_recheck_backoff, _rpc_recheck_max_backoff)
                        logger.warning(
                            'worker_not_marked_healthy reason=eth_blockNumber_not_succeeded rpc_error=%s '
                            'next_recheck_backoff_seconds=%s '
                            'worker stays unhealthy until the RPC health check passes',
                            recheck.get('error') or 'unknown',
                            _rpc_recheck_backoff,
                        )
                else:
                    logger.debug(
                        'rpc_recheck_skipped reason=backoff seconds_since_recheck=%.1f backoff_seconds=%s',
                        _seconds_since_recheck, _rpc_recheck_backoff,
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
