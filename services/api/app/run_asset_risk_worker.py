"""Background worker that runs the autonomous Asset Risk Assessor.

Each cycle enqueues assets whose latest assessment is missing or stale, then
claims and processes queued jobs with a database lease so multiple replicas are
safe. AI is only used for the narrative; the risk score is always deterministic.

Startup states (mirrors the AI-triage worker convention):
  * disabled            ASSET_RISK_ASSESSOR_ENABLED=false — idles with a periodic
                        heartbeat log; does NOT exit, so the service stays up in a
                        clearly disabled state.
  * configuration_error enabled but the database / live-mode config is missing —
                        logs the missing variable names (never secrets) and exits
                        non-zero so the platform restarts it loudly.
  * enabled             enabled and valid — the processing loop runs.

Runs as its own service (railway-asset-risk-worker.json + Procfile entry) so a
Procfile line alone never runs the assessor inside every API replica.
"""

from __future__ import annotations

import argparse
import logging
import os
import time

from services.api.app.domains.asset_risk import config as arc
from services.api.app.domains.asset_risk import worker as asset_risk_worker


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Run the Asset Risk Assessor worker.')
    parser.add_argument('--interval-seconds', type=int, default=None)
    parser.add_argument('--once', action='store_true')
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO').upper(), format='%(asctime)s %(levelname)s %(name)s %(message)s')
    logger = logging.getLogger(__name__)
    args = parse_args()
    config = arc.assessor_config()
    state, detail = asset_risk_worker.resolve_startup_state(config)

    if state == 'configuration_error':
        for item in detail:
            logger.error('event=asset_risk_worker_configuration_error detail=%s', item)
        logger.error('event=asset_risk_worker_exiting reason=configuration_error')
        return 1

    interval = max(30, int(args.interval_seconds if args.interval_seconds is not None else config['interval_seconds']))
    logger.info(
        'event=asset_risk_worker_started state=%s enabled=%s interval_seconds=%s batch_size=%s baseline_days=%s',
        state, config['enabled'], interval, config['batch_size'], config['baseline_days'],
    )

    disabled_heartbeat_every = max(1, 60 // max(2, interval))
    cycle = 0
    while True:
        cycle += 1
        try:
            if state == 'disabled':
                if cycle == 1 or cycle % disabled_heartbeat_every == 0:
                    logger.info('event=asset_risk_worker_disabled detail=ASSET_RISK_ASSESSOR_ENABLED=false; worker idle')
            else:
                summary = asset_risk_worker.run_asset_risk_worker_once(config)
                if summary.get('processed') or summary.get('enqueued') or summary.get('failed'):
                    logger.info(
                        'event=asset_risk_worker_cycle enqueued=%s processed=%s failed=%s',
                        summary.get('enqueued'), summary.get('processed'), summary.get('failed'),
                    )
        except Exception:
            logger.exception('event=asset_risk_worker_cycle_failed')
        if args.once:
            return 0
        time.sleep(interval)


if __name__ == '__main__':
    raise SystemExit(main())
