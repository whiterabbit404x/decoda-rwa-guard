#!/usr/bin/env python3
"""Debug a wallet monitoring target.

Usage:
  python -m services.api.scripts.debug_wallet_target --target-id <uuid>
  python -m services.api.scripts.debug_wallet_target --target-id <uuid> --tx-hash <0x...>

Checks:
  1. Target config: wallet_address, chain_network, target_type, monitoring_enabled
  2. Recent telemetry for the target
  3. If --tx-hash given: calls eth_getTransactionByHash and compares with monitored wallet
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any
from urllib.request import Request, urlopen

from services.api.app.pilot import ensure_pilot_schema, pg_connection


def _rpc_call(rpc_url: str, method: str, params: list[Any]) -> Any:
    payload = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}).encode()
    req = Request(rpc_url, data=payload, headers={'Content-Type': 'application/json'})
    with urlopen(req, timeout=20) as resp:
        body = json.loads(resp.read().decode())
    if body.get('error'):
        raise RuntimeError(f"RPC error: {body['error']}")
    return body.get('result')


def _print_section(title: str) -> None:
    print(f'\n{"=" * 60}')
    print(f'  {title}')
    print('=' * 60)


def debug_target(target_id: str, tx_hash: str | None) -> None:
    _print_section(f'Target config: {target_id}')
    with pg_connection() as conn:
        ensure_pilot_schema(conn)
        row = conn.execute(
            '''
            SELECT id, workspace_id, name, target_type, chain_network, chain_id,
                   wallet_address, contract_identifier, target_metadata,
                   monitoring_enabled, enabled, is_active,
                   monitoring_checkpoint_cursor, watcher_last_observed_block,
                   last_checked_at, last_run_status
            FROM targets
            WHERE id = %s
            LIMIT 1
            ''',
            (target_id,),
        ).fetchone()

    if row is None:
        print(f'ERROR: target {target_id!r} not found in targets table.')
        sys.exit(1)

    t = dict(row)
    wallet_addr = str(t.get('wallet_address') or '').strip()
    contract_id = str(t.get('contract_identifier') or '').strip()
    chain = str(t.get('chain_network') or '').strip()
    target_type = str(t.get('target_type') or '').strip()

    print(f"  target_id          = {t['id']}")
    print(f"  name               = {t.get('name')}")
    print(f"  target_type        = {target_type}")
    print(f"  chain_network      = {chain}")
    print(f"  chain_id           = {t.get('chain_id')}")
    print(f"  wallet_address     = {wallet_addr or 'NULL (MISSING)'}")
    print(f"  contract_id        = {contract_id or 'NULL'}")
    print(f"  monitoring_enabled = {t.get('monitoring_enabled')}")
    print(f"  enabled            = {t.get('enabled')}")
    print(f"  is_active          = {t.get('is_active')}")
    print(f"  last_checked_at    = {t.get('last_checked_at')}")
    print(f"  last_run_status    = {t.get('last_run_status')}")
    print(f"  checkpoint_cursor  = {t.get('monitoring_checkpoint_cursor') or 'none'}")
    print(f"  last_block         = {t.get('watcher_last_observed_block')}")

    monitored_address = (wallet_addr or contract_id).lower()
    if target_type == 'wallet' and not wallet_addr:
        print('\n  DIAGNOSIS: wallet_address is NULL for a wallet type target.')
        print('  EFFECT: fetch_evm_activity returns [] immediately — no block scanning occurs.')
        print('  FIX:    Update this target via PUT /targets/{id} with {"wallet_address": "0x..."}')
        print('          or re-create the target using the UI (wallet_address field is now shown).')

    _print_section('Recent telemetry (last 10 rows)')
    with pg_connection() as conn:
        rows = conn.execute(
            '''
            SELECT id, event_type, evidence_source, observed_at,
                   payload_json->>'block_number' AS block_number,
                   payload_json->>'tx_hash' AS tx_hash,
                   payload_json->>'from' AS tx_from,
                   payload_json->>'to' AS tx_to
            FROM telemetry_events
            WHERE target_id = %s
            ORDER BY observed_at DESC
            LIMIT 10
            ''',
            (target_id,),
        ).fetchall()

    if not rows:
        print('  No telemetry rows found for this target.')
    else:
        for r in rows:
            row_d = dict(r)
            print(
                f"  {row_d.get('observed_at')} | {row_d.get('event_type'):30s} | "
                f"block={row_d.get('block_number') or '-':>12} | "
                f"tx_hash={str(row_d.get('tx_hash') or '-')[:20]} | "
                f"from={str(row_d.get('tx_from') or '-')[:12]} "
                f"to={str(row_d.get('tx_to') or '-')[:12]}"
            )

    if not tx_hash:
        return

    _print_section(f'Transaction debug: {tx_hash}')
    rpc_url = str(os.getenv('STAGING_EVM_RPC_URL') or os.getenv('EVM_RPC_URL') or '').strip()
    if not rpc_url:
        print('  ERROR: EVM_RPC_URL / STAGING_EVM_RPC_URL not set — cannot call eth_getTransactionByHash.')
        return

    try:
        tx = _rpc_call(rpc_url, 'eth_getTransactionByHash', [tx_hash])
    except Exception as exc:
        print(f'  ERROR: eth_getTransactionByHash failed: {exc}')
        return

    if tx is None:
        print(f'  eth_getTransactionByHash({tx_hash!r}) returned null.')
        print('  The transaction may not yet be mined or may be on a different network.')
        return

    tx_from = str(tx.get('from') or '').lower()
    tx_to = str(tx.get('to') or '').lower()
    block_num_hex = tx.get('blockNumber') or '0x0'
    try:
        block_num = int(str(block_num_hex), 16)
    except (ValueError, TypeError):
        block_num = 0

    print(f"  tx_hash      = {tx.get('hash')}")
    print(f"  block_number = {block_num} ({block_num_hex})")
    print(f"  from         = {tx.get('from')}")
    print(f"  to           = {tx.get('to')}")
    value_wei = int(str(tx.get('value') or '0x0'), 16)
    print(f"  value_wei    = {value_wei}")
    print(f"  value_eth    = {value_wei / 1e18:.8f} ETH")

    if not monitored_address:
        print('\n  DIAGNOSIS: no monitored address configured — cannot match.')
        return

    print(f'\n  Monitored address: {monitored_address}')
    outbound = tx_from == monitored_address
    inbound = tx_to == monitored_address
    if outbound:
        print('  MATCH: outbound transfer (tx.from == monitored_wallet)')
        print('  EXPECTED RESULT: wallet_transfer_detected with direction=outbound')
    elif inbound:
        print('  MATCH: inbound transfer (tx.to == monitored_wallet)')
        print('  EXPECTED RESULT: wallet_transfer_detected with direction=inbound')
    else:
        print('  NO MATCH: tx.from and tx.to do not equal monitored wallet.')
        print(f'    tx.from={tx_from!r} vs monitored={monitored_address!r} → {"match" if tx_from == monitored_address else "no match"}')
        print(f'    tx.to  ={tx_to!r} vs monitored={monitored_address!r} → {"match" if tx_to == monitored_address else "no match"}')

    if block_num:
        cursor = t.get('monitoring_checkpoint_cursor') or ''
        last_scanned = 0
        if cursor and ':' in cursor:
            try:
                last_scanned = int(cursor.split(':', 1)[0])
            except ValueError:
                pass
        replay_blocks = max(1, int(os.getenv('MONITOR_REPLAY_BLOCKS', '25')))
        scan_from = max(0, last_scanned - replay_blocks) if last_scanned else 0
        print(f'\n  Block {block_num} vs scan window:')
        print(f'    last_scanned_block = {last_scanned}')
        print(f'    replay_blocks      = {replay_blocks}')
        print(f'    scan_from          = {scan_from}')
        if last_scanned and block_num <= last_scanned:
            print(f'    STATUS: block {block_num} <= last_scanned {last_scanned} — already past cursor, may be deduplicated.')
        elif scan_from and block_num < scan_from:
            print(f'    STATUS: block {block_num} < scan_from {scan_from} — outside replay window, will not be rescanned.')
        else:
            print(f'    STATUS: block {block_num} is within scan window — should be found on next worker cycle.')


def main() -> None:
    parser = argparse.ArgumentParser(description='Debug a wallet monitoring target.')
    parser.add_argument('--target-id', required=True, help='UUID of the target to inspect.')
    parser.add_argument('--tx-hash', default=None, help='Transaction hash to verify against monitored wallet.')
    args = parser.parse_args()
    debug_target(args.target_id, args.tx_hash)


if __name__ == '__main__':
    main()
