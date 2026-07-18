"""Operator command: validate the worker's Base RPC endpoints from the runtime, safely.

Run this INSIDE the Railway worker (same process env as the poller) to diagnose a
provider outage without ever printing an API token or full URL:

    python -m services.api.app.run_rpc_endpoint_diagnostics

It actively probes every configured Base RPC endpoint — DNS, TLS (SNI = host), an HTTP
POST of eth_chainId, then eth_blockNumber — and prints a host-only JSON report. Each
endpoint emits one ``event=rpc_endpoint_validation`` log line
(dns_ok / tls_ok / http_ok / json_rpc_ok / chain_id / safe_error_category). A
deterministically broken route (e.g. QuickNode ``TLSV1_ALERT_INTERNAL_ERROR``) is
DISABLED so the poll loop stops re-dialing it every cycle; a transient 429 is left to
the normal per-host backoff. Only the redacted hostname is ever surfaced.

Pass ``--no-disable`` to run a read-only probe that reports without benching any route.
"""
from __future__ import annotations

import json
import sys

from services.api.app.evm_activity_provider import (
    probe_worker_rpc_endpoints,
    rpc_provider_backoff_status,
    rpc_request_volume_snapshot,
    validate_worker_rpc_endpoints,
    worker_rpc_chain_id,
)


def main() -> None:
    disable = '--no-disable' not in sys.argv[1:]
    # Static URL-shape validation first (catches a mis-copied / non-HTTPS variable
    # before any dial), then the live probe.
    shape = validate_worker_rpc_endpoints()
    expected_chain_id = worker_rpc_chain_id() or 8453
    if disable:
        live = probe_worker_rpc_endpoints(expected_chain_id=expected_chain_id)
    else:
        # Read-only: probe every endpoint but never bench a route.
        from services.api.app.evm_activity_provider import probe_rpc_endpoint, _resolve_evm_rpc_urls  # noqa: PLC0415
        endpoints = [probe_rpc_endpoint(u, expected_chain_id=expected_chain_id) for u in _resolve_evm_rpc_urls()]
        live = {
            'endpoint_count': len(endpoints),
            'all_operational': bool(endpoints) and all(e['json_rpc_ok'] for e in endpoints),
            'endpoints': endpoints,
            'disabled_rpc_routes': [],
            'backoff_hosts': [],
        }
    report = {
        'expected_chain_id': expected_chain_id,
        'shape_validation': shape,
        'live_probe': live,
        'provider_backoff': rpc_provider_backoff_status(),
        'request_volume': rpc_request_volume_snapshot(),
    }
    print(json.dumps(report, sort_keys=True, default=str))
    # Non-zero exit when no endpoint is operational, so a CI/ops check can gate on it.
    if not live['all_operational']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
