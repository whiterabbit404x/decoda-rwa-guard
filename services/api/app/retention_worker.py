from __future__ import annotations

import argparse
import logging
import os
import time

from services.api.app.observability import send_external_oncall_alert
from services.api.app.pilot import record_retention_worker_failure, run_retention_worker_cycle, startup_schema_init_plan


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Run scheduled workspace retention sweeps.')
    parser.add_argument('--worker-name', default=os.getenv('RETENTION_WORKER_NAME', 'retention-worker'))
    parser.add_argument('--interval-seconds', type=int, default=int(os.getenv('RETENTION_WORKER_INTERVAL_SECONDS', '300')))
    parser.add_argument('--batch-size', type=int, default=int(os.getenv('RETENTION_WORKER_BATCH_SIZE', '25')))
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--dry-run', action='store_true', help='Preview what would be deleted without making any changes')
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO').upper(), format='%(asctime)s %(levelname)s %(name)s %(message)s')
    logger = logging.getLogger(__name__)
    args = parse_args()
    plan = startup_schema_init_plan(process_role='retention-worker')
    logger.info('retention worker schema init skipped role=%s reason=%s', plan.get('process_role'), plan.get('reason'))
    if args.dry_run:
        logger.info('retention worker dry_run=true: no data will be deleted or anonymized')
    while True:
        try:
            summary = run_retention_worker_cycle(worker_name=args.worker_name, batch_size=max(1, args.batch_size), dry_run=args.dry_run)
            logger.info('retention worker cycle summary=%s', summary)
        except Exception as exc:
            record_retention_worker_failure(worker_name=args.worker_name, error=exc)
            logger.exception('retention worker cycle failed')
            send_external_oncall_alert('retention_worker_failed', 'Retention worker cycle failed.', worker=args.worker_name, error_type=type(exc).__name__)
        if args.once or args.dry_run:
            return 0
        time.sleep(max(30, args.interval_seconds))


if __name__ == '__main__':
    raise SystemExit(main())
