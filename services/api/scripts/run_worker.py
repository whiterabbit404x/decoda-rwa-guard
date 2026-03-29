from __future__ import annotations

import argparse
import logging
import os
import time

from services.api.app.pilot import run_background_jobs, startup_schema_init_plan


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Run Decoda API background worker loop.')
    parser.add_argument('--worker-id', default='local-worker')
    parser.add_argument('--limit', type=int, default=20)
    parser.add_argument('--interval-seconds', type=float, default=2.0)
    parser.add_argument('--once', action='store_true')
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO').upper(), format='%(asctime)s %(levelname)s %(name)s %(message)s')
    logger = logging.getLogger(__name__)
    args = parse_args()
    schema_plan = startup_schema_init_plan(process_role='worker')
    logger.info(
        'background worker schema init skipped for role=%s: %s',
        schema_plan.get('process_role', 'worker'),
        schema_plan.get('reason', 'schema init disabled'),
    )
    while True:
        summary = run_background_jobs(worker_id=args.worker_id, limit=args.limit)
        logger.info('[worker] processed=%s failed=%s', summary['processed'], summary['failed'])
        if args.once:
            return 0
        time.sleep(max(0.25, args.interval_seconds))


if __name__ == '__main__':
    raise SystemExit(main())
