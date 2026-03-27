from __future__ import annotations

import argparse
import time

from services.api.app.monitoring_runner import run_monitoring_cycle


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Run Decoda monitoring worker loop.')
    parser.add_argument('--worker-name', default='monitoring-worker')
    parser.add_argument('--interval-seconds', type=float, default=15.0)
    parser.add_argument('--limit', type=int, default=50)
    parser.add_argument('--once', action='store_true')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    while True:
        summary = run_monitoring_cycle(worker_name=args.worker_name, limit=args.limit)
        print(f"[monitoring-worker] checked={summary['checked']} alerts={summary['alerts_generated']} live_mode={summary['live_mode']}")
        if args.once:
            return 0
        time.sleep(max(1.0, args.interval_seconds))


if __name__ == '__main__':
    raise SystemExit(main())
