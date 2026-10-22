"""Deliver Guard's signals to Decoda Assets and Decoda Vault on a timer.

    python -m services.api.app.run_decoda_signal_worker [--interval-seconds 30] [--once]

Each pass refreshes the signals of every workspace a Decoda product is routed to (an
alert resolved since the last pass becomes a new signal version) and delivers what is
due. The intake also drains right after each accepted event, and so does the background
job runner; this loop is what reports a change that no new event triggers, and keeps
Vault's coverage report fresh. Safe to run as several replicas: deliveries are leased.
"""

from __future__ import annotations

import argparse
import logging
import signal
import threading

from services.api.app import decoda_products

logger = logging.getLogger(__name__)
_stop = threading.Event()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--interval-seconds', type=float, default=30.0)
    parser.add_argument('--once', action='store_true', help='one pass, then exit')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: _stop.set())
    while True:
        try:
            summary = decoda_products.drain()
            if any(summary.get(k) for k in ('delivered', 'failed', 'dead')):
                logger.info('event=decoda_product_signals_drained %s', summary)
        except Exception as exc:
            logger.warning('event=decoda_product_signal_worker_pass_failed error=%s', type(exc).__name__)
        if args.once or _stop.wait(max(5.0, args.interval_seconds)):
            return


if __name__ == '__main__':
    main()
