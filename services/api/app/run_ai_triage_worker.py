"""Background worker that processes queued AI incident-triage jobs.

Triage runs asynchronously so it never blocks telemetry ingestion, alerts, or
incident creation. Each cycle claims at most one due queued job via the
distributed-safe conditional UPDATE inside ``process_triage_job``, so multiple
replicas are safe.

Startup states (deliberately distinct and visible in the logs):
  * disabled            AI_TRIAGE_ENABLED=false — the worker idles and emits a
                        periodic ``ai_triage_worker_disabled`` heartbeat. It does
                        NOT exit, so the Railway service stays up in a clear
                        disabled state.
  * configuration_error enabled but the provider is unknown / missing its key or
                        model — the worker logs ``ai_triage_worker_configuration_error``
                        and exits non-zero so Railway restarts it and the
                        misconfiguration is loud instead of silently idle.
  * enabled             enabled and valid — the processing loop runs.

This worker runs as its OWN Railway service (railway-ai-triage-worker.json). A
Procfile entry alone does NOT create a running Railway service, so triage never
runs inside every API replica; only this single dedicated service claims jobs.
"""
from __future__ import annotations

import argparse
import logging
import os
import time

from services.api.app import ai_triage


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Process queued AI incident-triage jobs.')
    parser.add_argument('--interval-seconds', type=int, default=int(os.getenv('AI_TRIAGE_WORKER_INTERVAL_SECONDS', '5')))
    parser.add_argument('--once', action='store_true')
    return parser.parse_args()


def resolve_startup_state(config: dict | None = None) -> tuple[str, list[str]]:
    """Return (state, detail) where state is disabled | configuration_error | enabled."""
    cfg = config or ai_triage.triage_config()
    if not cfg['enabled']:
        return 'disabled', []
    errors = ai_triage.blocking_configuration_errors(cfg)
    if errors:
        return 'configuration_error', errors
    return 'enabled', []


def main() -> int:
    logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO').upper(), format='%(asctime)s %(levelname)s %(name)s %(message)s')
    logger = logging.getLogger(__name__)
    args = parse_args()
    config = ai_triage.triage_config()
    state, detail = resolve_startup_state(config)

    if state == 'configuration_error':
        for item in detail:
            logger.error('event=ai_triage_worker_configuration_error detail=%s', item)
        logger.error('event=ai_triage_worker_exiting reason=configuration_error provider=%s', config['provider'] or 'unset')
        # Non-zero exit -> Railway restarts the service; the error stays loud.
        return 1

    logger.info(
        'event=ai_triage_worker_started state=%s enabled=%s provider=%s interval_seconds=%s',
        state, config['enabled'], config['provider'] or 'mock', args.interval_seconds,
    )

    # Heartbeat cadence for the disabled state (~ once a minute) so the disabled
    # service is clearly, repeatedly observable without a tight busy loop.
    interval = max(2, args.interval_seconds)
    disabled_heartbeat_every = max(1, 60 // interval)
    cycle = 0
    while True:
        cycle += 1
        try:
            if state == 'disabled':
                if cycle == 1 or cycle % disabled_heartbeat_every == 0:
                    logger.info('event=ai_triage_worker_disabled detail=AI_TRIAGE_ENABLED=false; worker idle, no jobs processed')
            else:
                summary = ai_triage.run_ai_triage_worker_once()
                if summary.get('processed'):
                    logger.info('event=ai_triage_worker_cycle processed=%s job=%s', summary.get('processed'), summary.get('job'))
        except Exception:
            # A crash must be visible and cause a restart, but a single failed
            # cycle should not kill the worker; log with traceback and continue.
            logger.exception('event=ai_triage_worker_cycle_failed')
        if args.once:
            return 0
        time.sleep(interval)


if __name__ == '__main__':
    raise SystemExit(main())
