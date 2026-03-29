from __future__ import annotations

import argparse
import logging
import os
import time

from services.api.app.monitoring_runner import run_monitoring_cycle
from services.api.app.pilot import runtime_environment_identity, startup_schema_init_plan


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Run Decoda monitoring worker loop.')
    parser.add_argument('--worker-name', default=os.getenv('MONITORING_WORKER_NAME', 'monitoring-worker'))
    parser.add_argument('--interval-seconds', type=float, default=float(os.getenv('MONITORING_WORKER_INTERVAL_SECONDS', '15')))
    parser.add_argument('--limit', type=int, default=int(os.getenv('MONITORING_WORKER_LIMIT', '50')))
    parser.add_argument('--once', action='store_true')
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO').upper(), format='%(asctime)s %(levelname)s %(name)s %(message)s')
    logger = logging.getLogger(__name__)
    args = parse_args()
    logger.info('monitoring worker starting')
    identity = runtime_environment_identity()
    logger.info(
        'monitoring worker runtime identity app_mode=%s live_mode=%s railway_environment=%s railway_service=%s database_fingerprint=%s',
        identity['app_mode'],
        identity['live_mode_enabled'],
        identity['railway_environment'] or 'unknown',
        identity['railway_service'] or 'unknown',
        identity['database_fingerprint'],
    )
    logger.info(
        'monitoring worker config worker_name=%s interval_seconds=%s limit=%s once=%s',
        args.worker_name,
        args.interval_seconds,
        args.limit,
        args.once,
    )
    schema_plan = startup_schema_init_plan(process_role='worker')
    logger.info(
        'monitoring worker schema init skipped for role=%s: %s',
        schema_plan.get('process_role', 'worker'),
        schema_plan.get('reason', 'schema init disabled'),
    )
    while True:
        try:
            summary = run_monitoring_cycle(worker_name=args.worker_name, limit=args.limit)
            logger.info(
                'monitoring cycle summary due=%s checked=%s alerts=%s live_mode=%s',
                summary.get('due_targets', 0),
                summary.get('checked', 0),
                summary.get('alerts_generated', 0),
                summary.get('live_mode', False),
            )
        except Exception:
            logger.exception('monitoring worker cycle error')
        if args.once:
            logger.info('monitoring worker exiting after one cycle')
            return 0
        time.sleep(max(1.0, args.interval_seconds))


if __name__ == '__main__':
    raise SystemExit(main())
