"""Background worker that processes queued AI incident-triage jobs.

Triage runs asynchronously so it never blocks telemetry ingestion, alerts, or
incident creation. Each cycle claims at most one due queued job via the
distributed-safe conditional UPDATE inside ``process_triage_job``, so multiple
replicas are safe. When AI_TRIAGE_ENABLED is false the worker stays idle.
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


def main() -> int:
    logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO').upper(), format='%(asctime)s %(levelname)s %(name)s %(message)s')
    logger = logging.getLogger(__name__)
    args = parse_args()
    logger.info('event=ai_triage_worker_started enabled=%s interval_seconds=%s', ai_triage.triage_config()['enabled'], args.interval_seconds)
    while True:
        try:
            summary = ai_triage.run_ai_triage_worker_once()
            if summary.get('processed'):
                logger.info('event=ai_triage_worker_cycle processed=%s job=%s', summary.get('processed'), summary.get('job'))
        except Exception:
            logger.exception('event=ai_triage_worker_cycle_failed')
        if args.once:
            return 0
        time.sleep(max(2, args.interval_seconds))


if __name__ == '__main__':
    raise SystemExit(main())
