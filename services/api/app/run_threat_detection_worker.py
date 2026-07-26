"""Background worker that runs the autonomous Threat Detection Engineer.

Each cycle finds workspaces with telemetry newer than their checkpoint and
processes only the recent correlation window (never a full-history rescan). The
deterministic engine is the source of truth; AI is used only for the narrative,
so the worker keeps producing detections even when AI is disabled.

Startup states (mirrors the Asset Risk / AI-triage worker convention):
  * disabled            THREAT_DETECTION_ENABLED=false — idles with a periodic
                        heartbeat log; does NOT exit, so the service stays up in a
                        clearly disabled state.
  * configuration_error enabled but the database / live-mode config is missing —
                        logs the missing variable names (never secrets) and exits
                        non-zero so the platform restarts it loudly.
  * enabled             enabled and valid — the processing loop runs.

Runs as its own service (railway-threat-detection-worker.json + Procfile entry)
so a Procfile line alone never runs the engine inside every API replica.
"""

from __future__ import annotations

import argparse
import logging
import os
import time

from services.api.app.domains.threat_detection import config as tdc
from services.api.app.domains.threat_detection import worker as threat_detection_worker


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Run the Threat Detection Engineer worker.')
    parser.add_argument('--interval-seconds', type=int, default=None)
    parser.add_argument('--once', action='store_true')
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO').upper(), format='%(asctime)s %(levelname)s %(name)s %(message)s')
    logger = logging.getLogger(__name__)
    args = parse_args()
    config = tdc.engine_config()
    state, detail = threat_detection_worker.resolve_startup_state(config)

    if state == 'configuration_error':
        for item in detail:
            logger.error('event=threat_detection_worker_configuration_error detail=%s', item)
        logger.error('event=threat_detection_worker_exiting reason=configuration_error')
        return 1

    interval = max(30, int(args.interval_seconds if args.interval_seconds is not None else config['interval_seconds']))
    logger.info(
        'event=threat_detection_worker_started state=%s enabled=%s interval_seconds=%s batch_size=%s baseline_days=%s',
        state, config['enabled'], interval, config['batch_size'], config['baseline_days'],
    )

    disabled_heartbeat_every = max(1, 60 // max(2, interval))
    cycle = 0
    while True:
        cycle += 1
        try:
            if state == 'disabled':
                if cycle == 1 or cycle % disabled_heartbeat_every == 0:
                    logger.info('event=threat_detection_worker_disabled detail=THREAT_DETECTION_ENABLED=false; worker idle')
            else:
                summary = threat_detection_worker.run_threat_detection_worker_once(config)
                if summary.get('workspaces_processed') or summary.get('detections_created') or summary.get('failed'):
                    logger.info(
                        'event=threat_detection_worker_cycle workspaces=%s created=%s updated=%s anomalies=%s alerts=%s failed=%s',
                        summary.get('workspaces_processed'), summary.get('detections_created'),
                        summary.get('detections_updated'), summary.get('anomalies'),
                        summary.get('alerts_upserted'), summary.get('failed'),
                    )
        except Exception:
            logger.exception('event=threat_detection_worker_cycle_failed')
        if args.once:
            return 0
        time.sleep(interval)


if __name__ == '__main__':
    raise SystemExit(main())
