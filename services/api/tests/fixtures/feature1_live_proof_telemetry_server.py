#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        now = datetime.now(timezone.utc).isoformat()
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        asset_identifier = (query.get('asset_identifier') or ['USTB-LIVE-PROOF'])[0]
        if parsed.path == '/market/observations':
            self._send_json(
                200,
                {
                    'observations': [
                        {
                            'provider_name': 'proof-market',
                            'source_name': 'proof-market',
                            'source_type': 'market_api',
                            'asset_identifier': asset_identifier,
                            'status': 'ok',
                            'provider_status': 'ok',
                            'observed_at': now,
                            'rolling_volume': 2_000_000.0,
                            'rolling_transfer_count': 12,
                            'unique_counterparties': 5,
                            'concentration_ratio': 0.55,
                            'abnormal_outflow_ratio': 0.81,
                            'burst_score': 2.5,
                            'venue_distribution': {'known': 0.4, 'unknown': 0.6},
                            'route_distribution': {'treasury_ops->unknown_external': 0.9},
                            'freshness_seconds': 2,
                        }
                    ]
                },
            )
            return
        if parsed.path == '/oracle/observations':
            self._send_json(
                200,
                {
                    'status': 'ok',
                    'observations': [
                        {
                            'source_name': 'lab-oracle-a',
                            'provider_name': 'lab-oracle-a',
                            'source_type': 'oracle_api',
                            'asset_identifier': asset_identifier,
                            'observed_value': 1.01,
                            'observed_at': now,
                            'freshness_seconds': 3,
                            'status': 'ok',
                            'provider_status': 'ok',
                            'update_interval_seconds': 60,
                            'block_number': 1,
                        }
                    ],
                },
            )
            return
        self._send_json(404, {'error': 'not_found'})

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return


def main() -> int:
    parser = argparse.ArgumentParser(description='Local telemetry server for Feature1 live proof.')
    parser.add_argument('--port', type=int, default=8011)
    args = parser.parse_args()
    server = HTTPServer(('127.0.0.1', args.port), Handler)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
