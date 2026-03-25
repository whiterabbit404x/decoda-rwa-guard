from __future__ import annotations

import argparse
import time

from services.api.app.pilot import run_background_jobs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Run Decoda API background worker loop.')
    parser.add_argument('--worker-id', default='local-worker')
    parser.add_argument('--limit', type=int, default=20)
    parser.add_argument('--interval-seconds', type=float, default=2.0)
    parser.add_argument('--once', action='store_true')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    while True:
        summary = run_background_jobs(worker_id=args.worker_id, limit=args.limit)
        print(f"[worker] processed={summary['processed']} failed={summary['failed']}")
        if args.once:
            return 0
        time.sleep(max(0.25, args.interval_seconds))


if __name__ == '__main__':
    raise SystemExit(main())
