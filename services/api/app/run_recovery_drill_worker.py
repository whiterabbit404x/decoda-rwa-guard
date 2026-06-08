from __future__ import annotations

import argparse
import logging
import os
import time

from services.api.app.observability import send_external_oncall_alert
from services.api.app.pilot import pg_connection, startup_schema_init_plan
from services.api.app.recovery_drills import run_recovery_drill_cycle


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Run scheduled recovery validation drills.')
    parser.add_argument('--interval-seconds', type=int, default=int(os.getenv('RECOVERY_DRILL_WORKER_INTERVAL_SECONDS', '300')))
    parser.add_argument('--once', action='store_true')
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO').upper(), format='%(asctime)s %(levelname)s %(name)s %(message)s')
    logger = logging.getLogger(__name__)
    args = parse_args()
    plan = startup_schema_init_plan(process_role='recovery-drill-worker')
    logger.info('recovery drill worker schema init skipped role=%s reason=%s', plan.get('process_role'), plan.get('reason'))
    while True:
        try:
            with pg_connection() as connection:
                summary = run_recovery_drill_cycle(connection, alert=send_external_oncall_alert)
            logger.info('recovery drill cycle due=%s passed=%s failed=%s stale_alerts=%s', summary['due'], summary['passed'], summary['failed'], summary['stale_alerts'])
        except Exception:
            logger.exception('recovery drill worker cycle failed')
            send_external_oncall_alert('recovery_drill_worker_failed', 'Recovery drill scheduler cycle failed.')
        if args.once:
            return 0
        time.sleep(max(30, args.interval_seconds))


if __name__ == '__main__':
    raise SystemExit(main())
