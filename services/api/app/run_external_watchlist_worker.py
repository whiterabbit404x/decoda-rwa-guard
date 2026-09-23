"""Background worker for the External Watchlist (founder-only public monitoring).

Runs historical backfills and live polling for externally watched protocols in
its own process, so a 30-day backfill never runs inside an API request.

Startup states (same convention as the threat-detection / asset-risk workers):
  * disabled            EXTERNAL_WATCHLIST_ENABLED is not true — idles with a
                        periodic log line; does NOT exit.
  * configuration_error enabled but DATABASE_URL is missing — logs the missing
                        variable names (never secrets) and exits non-zero.
  * enabled             the cycle loop runs.

Every RPC call it makes is a read (see domains/external_watchlist/rpc.py).
Set EXTERNAL_WATCHLIST_RPC_URL_<chain_id> to give it provider quota separate
from customer monitoring.
"""

from __future__ import annotations

import argparse
import logging
import os
import time

from services.api.app.domains.external_watchlist import config as ewc
from services.api.app.domains.external_watchlist import worker as external_worker


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Run the External Watchlist worker.')
    parser.add_argument('--interval-seconds', type=int, default=None)
    parser.add_argument('--once', action='store_true')
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO').upper(), format='%(asctime)s %(levelname)s %(name)s %(message)s')
    logger = logging.getLogger(__name__)
    args = parse_args()
    config = ewc.worker_config()
    state, detail = external_worker.resolve_startup_state(config)
    if state == 'configuration_error':
        for item in detail:
            logger.error('event=external_watchlist_worker_configuration_error detail=%s', item)
        return 1
    interval = max(30, int(args.interval_seconds if args.interval_seconds is not None else config['interval_seconds']))
    logger.info(
        'event=external_watchlist_worker_started state=%s interval_seconds=%s max_rpc_calls_per_cycle=%s',
        state, interval, config['max_rpc_calls_per_cycle'],
    )
    cycle = 0
    while True:
        cycle += 1
        try:
            if state == 'disabled':
                if cycle == 1 or cycle % 30 == 0:
                    logger.info('event=external_watchlist_worker_disabled detail=EXTERNAL_WATCHLIST_ENABLED is not true; idle')
            else:
                summary = external_worker.run_worker_once(config)
                logger.info(
                    'event=external_watchlist_worker_cycle backfills=%s polled=%s failures=%s events=%s findings=%s rpc_calls=%s budget_exhausted=%s',
                    summary.get('backfills_claimed'), summary.get('targets_polled'), summary.get('poll_failures'),
                    summary.get('events_inserted'), summary.get('findings_created'), summary.get('rpc_calls'),
                    summary.get('budget_exhausted'),
                )
        except Exception:
            logger.exception('event=external_watchlist_worker_cycle_failed')
        if args.once:
            return 0
        time.sleep(interval)


if __name__ == '__main__':
    raise SystemExit(main())
