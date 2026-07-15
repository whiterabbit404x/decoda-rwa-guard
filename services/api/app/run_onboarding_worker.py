"""Background worker that processes queued Onboarding Agent discovery jobs.

Discovery + RPC benchmarking run asynchronously as durable background jobs so
they survive an API restart and are safe across multiple replicas: each cycle
claims at most one due queued run via the distributed-safe conditional UPDATE
inside ``onboarding_agent.claim_and_run_once``.

The API also runs the pipeline inline (best-effort) right after enqueue for
single-process / preview deployments; this dedicated worker is the authoritative
path for production and simply re-claims anything left queued. Because the claim
is a conditional UPDATE, inline + dedicated execution can never double-process a
run.

Startup states (mirrors the AI triage worker):
  * configuration_error  database/live-mode is unavailable (no DATABASE_URL, or
                         LIVE_MODE off). Logged loudly; the worker exits non-zero
                         so the platform restarts it instead of looping on 503s.
  * enabled              the processing loop runs.
"""
from __future__ import annotations

import argparse
import logging
import os
import time

from services.api.app import onboarding_agent, pilot


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Process queued onboarding discovery jobs.')
    parser.add_argument('--interval-seconds', type=int,
                        default=int(os.getenv('ONBOARDING_WORKER_INTERVAL_SECONDS', '3')))
    parser.add_argument('--once', action='store_true')
    return parser.parse_args()


def database_configuration_errors() -> list[str]:
    errors: list[str] = []
    if not (os.getenv('DATABASE_URL') or '').strip():
        errors.append('DATABASE_URL is not set')
    try:
        summary = pilot.runtime_mode_config_summary()
        if not summary.get('live_mode_enabled'):
            errors.append('live pilot mode is not enabled')
    except Exception as exc:  # pragma: no cover - defensive
        errors.append(f'runtime mode unavailable: {type(exc).__name__}')
    return errors


def main() -> int:
    logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO').upper(),
                        format='%(asctime)s %(levelname)s %(name)s %(message)s')
    logger = logging.getLogger(__name__)
    args = parse_args()

    config_errors = database_configuration_errors()
    if config_errors:
        for item in config_errors:
            logger.error('event=onboarding_worker_configuration_error detail=%s', item)
        logger.error('event=onboarding_worker_exiting reason=configuration_error')
        return 1

    logger.info('event=onboarding_worker_started interval_seconds=%s worker_id=%s',
                args.interval_seconds, onboarding_agent._worker_id())
    interval = max(1, args.interval_seconds)
    while True:
        try:
            summary = onboarding_agent.claim_and_run_once()
            if summary.get('processed'):
                logger.info('event=onboarding_worker_cycle processed=%s run_id=%s session_id=%s error=%s',
                            summary.get('processed'), summary.get('run_id'), summary.get('session_id'),
                            summary.get('error'))
        except Exception:
            logger.exception('event=onboarding_worker_cycle_failed')
        if args.once:
            return 0
        time.sleep(interval)


if __name__ == '__main__':
    raise SystemExit(main())
