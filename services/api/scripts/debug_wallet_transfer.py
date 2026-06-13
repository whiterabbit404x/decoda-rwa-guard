"""Debug command: explain whether a transaction involves a target's monitored wallet.

Usage:
    python -m services.api.scripts.debug_wallet_transfer \
        --target-id <uuid> --tx-hash 0x<hash>

Steps:
  1. Load the target row (canonical wallet_address + asset context).
  2. Resolve the monitored wallet from canonical + fallback locations.
  3. Fetch the transaction via eth_getTransactionByHash.
  4. Compare tx.from / tx.to against the monitored wallet and explain the result.

Read-only: this never writes telemetry, never mutates the target, and never
prints the RPC URL (which may embed an API key).
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from services.api.app.evm_activity_provider import (
    FailoverJsonRpcClient,
    _resolve_evm_rpc_urls,
    explain_wallet_transfer_match,
    resolve_monitored_wallet,
)


def _load_target(target_id: str) -> dict[str, Any] | None:
    # Imported lazily so the pure helpers above stay importable without fastapi.
    from services.api.app.pilot import pg_connection

    with pg_connection() as connection:
        row = connection.execute(
            '''
            SELECT t.id, t.workspace_id, t.target_type, t.chain_network,
                   t.wallet_address, t.contract_identifier, t.target_metadata,
                   t.asset_id,
                   a.identifier AS asset_identifier,
                   a.identifier AS identifier
            FROM targets t
            LEFT JOIN assets a
              ON a.id = t.asset_id AND a.workspace_id = t.workspace_id
            WHERE t.id = %s::uuid
            ''',
            (target_id,),
        ).fetchone()
    if row is None:
        return None
    target = dict(row)
    # Expose the asset identifier as the asset_context fallback the resolver reads.
    target['asset_context'] = {
        'asset_identifier': target.get('asset_identifier'),
        'identifier': target.get('identifier'),
    }
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Explain a wallet-transfer match for a target + tx hash.')
    parser.add_argument('--target-id', required=True, help='Target UUID')
    parser.add_argument('--tx-hash', required=True, help='Transaction hash (0x...)')
    args = parser.parse_args(argv)

    target = _load_target(args.target_id)
    if target is None:
        print(json.dumps({'error': 'target_not_found', 'target_id': args.target_id}, indent=2))
        return 2

    monitored_wallet = resolve_monitored_wallet(target)
    client = FailoverJsonRpcClient(_resolve_evm_rpc_urls())
    try:
        tx = client.call('eth_getTransactionByHash', [args.tx_hash])
    except Exception as exc:  # noqa: BLE001 - report, do not crash the debug tool
        print(json.dumps({'error': 'rpc_call_failed', 'detail': str(exc)[:200]}, indent=2))
        return 3

    explanation = explain_wallet_transfer_match(monitored_wallet, tx if isinstance(tx, dict) else None)
    result = {
        'target_id': args.target_id,
        'target_type': target.get('target_type'),
        'chain_network': target.get('chain_network'),
        'monitored_wallet': monitored_wallet,
        'monitored_wallet_configured': monitored_wallet is not None,
        'tx_hash': args.tx_hash,
        'tx_found': bool(tx),
        **explanation,
    }
    print(json.dumps(result, indent=2))
    return 0 if explanation.get('matched') else 1


if __name__ == '__main__':
    sys.exit(main())
