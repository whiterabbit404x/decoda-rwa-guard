#!/usr/bin/env python3
"""Admin repair: reset a monitoring target's block cursor to latest - N or run batched catchup.

Usage:
    # Reset cursor to latest_block - 300 (smoke-test window)
    python repair_target_cursor.py --target-id <uuid> --mode reset

    # Reset cursor to a specific block
    python repair_target_cursor.py --target-id <uuid> --mode reset --block 47376000

    # Run catchup in batches until cursor reaches chain head
    python repair_target_cursor.py --target-id <uuid> --mode catchup --batches 20

Environment:
    DATABASE_URL   PostgreSQL DSN (required)
    EVM_RPC_URL    RPC endpoint for eth_blockNumber (required for --mode reset)
    EVM_CHAIN_ID   Chain ID (optional, for logging)
"""
from __future__ import annotations

import argparse
import os
import sys
import uuid
from datetime import datetime, timezone
from urllib import request as _urllib_request
import json


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _rpc_call(rpc_url: str, method: str, params: list) -> object:
    body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}).encode()
    req = _urllib_request.Request(rpc_url, data=body, headers={'Content-Type': 'application/json'})
    with _urllib_request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    err = data.get('error')
    if err:
        raise RuntimeError(f'RPC error {method}: {err}')
    return data.get('result')


def _get_latest_block(rpc_url: str) -> int:
    raw = _rpc_call(rpc_url, 'eth_blockNumber', [])
    return int(str(raw), 16)


def _pg_connect(dsn: str):
    try:
        import psycopg
        conn = psycopg.connect(dsn, row_factory=psycopg.rows.dict_row)
        conn.autocommit = False
        return conn
    except ImportError:
        pass
    try:
        import psycopg2
        import psycopg2.extras
        conn = psycopg2.connect(dsn, cursor_factory=psycopg2.extras.RealDictCursor)
        conn.autocommit = False
        return conn
    except ImportError:
        pass
    raise RuntimeError('Neither psycopg nor psycopg2 is installed')


def _cursor_block(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        return int(cursor.split(':')[0])
    except (ValueError, AttributeError):
        return 0


def cmd_reset(conn, target_id: str, block: int) -> None:
    cur = conn.cursor()
    new_cursor = f'{block}:checkpoint:-1'
    print(f'[repair_target_cursor] Resetting target {target_id} cursor to block {block} ({new_cursor})')
    cur.execute(
        '''
        UPDATE targets
        SET monitoring_checkpoint_cursor = %s,
            watcher_last_observed_block   = %s,
            updated_at                    = NOW()
        WHERE id = %s::uuid
        RETURNING id, workspace_id, monitoring_checkpoint_cursor
        ''',
        (new_cursor, block, target_id),
    )
    row = cur.fetchone()
    if not row:
        print(f'[repair_target_cursor] ERROR: target {target_id} not found', file=sys.stderr)
        conn.rollback()
        sys.exit(1)
    # Also reset the monitor_checkpoint table for this target's workspace+chain
    cur.execute(
        '''
        SELECT workspace_id, chain_network
        FROM targets
        WHERE id = %s::uuid
        ''',
        (target_id,),
    )
    t = cur.fetchone()
    if t:
        workspace_id = str(t['workspace_id'])
        chain = str(t.get('chain_network') or 'base').strip().lower()
        cur.execute(
            '''
            UPDATE monitor_checkpoint
            SET last_processed_block = %s, updated_at = NOW()
            WHERE workspace_id = %s::uuid AND chain = %s
            ''',
            (block, workspace_id, chain),
        )
        print(f'[repair_target_cursor] monitor_checkpoint updated: workspace={workspace_id} chain={chain} block={block}')
    conn.commit()
    print(f'[repair_target_cursor] Done. New cursor: {new_cursor}')


def cmd_catchup(conn, target_id: str, rpc_url: str, max_batches: int, batch_size: int) -> None:
    cur = conn.cursor()
    cur.execute(
        'SELECT id, workspace_id, chain_network, monitoring_checkpoint_cursor FROM targets WHERE id = %s::uuid',
        (target_id,),
    )
    row = cur.fetchone()
    if not row:
        print(f'[repair_target_cursor] ERROR: target {target_id} not found', file=sys.stderr)
        sys.exit(1)
    current_block = _cursor_block(row.get('monitoring_checkpoint_cursor'))
    latest = _get_latest_block(rpc_url)
    print(f'[repair_target_cursor] catchup target={target_id} cursor_block={current_block} latest={latest} behind={latest - current_block}')
    for i in range(max_batches):
        if current_block >= latest - 3:
            print(f'[repair_target_cursor] Caught up at block {current_block}')
            break
        next_block = min(current_block + batch_size, latest - 3)
        new_cursor = f'{next_block}:checkpoint:-1'
        cur.execute(
            'UPDATE targets SET monitoring_checkpoint_cursor = %s, updated_at = NOW() WHERE id = %s::uuid',
            (new_cursor, target_id),
        )
        conn.commit()
        print(f'[repair_target_cursor] batch {i+1}/{max_batches}: advanced cursor {current_block} -> {next_block}')
        current_block = next_block
        # Re-fetch latest in case chain is still advancing
        try:
            latest = _get_latest_block(rpc_url)
        except Exception:
            pass
    print(f'[repair_target_cursor] Final cursor block: {current_block} (latest: {latest})')


def main() -> None:
    parser = argparse.ArgumentParser(description='Repair monitoring target cursor')
    parser.add_argument('--target-id', required=True, help='Target UUID to repair')
    parser.add_argument('--mode', required=True, choices=['reset', 'catchup'], help='Repair mode')
    parser.add_argument('--block', type=int, default=None, help='Block to reset to (reset mode). Default: latest - 300')
    parser.add_argument('--batches', type=int, default=10, help='Number of catchup batches (catchup mode)')
    parser.add_argument('--batch-size', type=int, default=1000, help='Blocks per catchup batch')
    args = parser.parse_args()

    dsn = os.getenv('DATABASE_URL', '')
    if not dsn:
        print('ERROR: DATABASE_URL not set', file=sys.stderr)
        sys.exit(1)

    rpc_url = (
        os.getenv('EVM_RPC_URL_8453')
        or os.getenv('BASE_EVM_RPC_URL')
        or os.getenv('STAGING_EVM_RPC_URL')
        or os.getenv('EVM_RPC_URL')
        or ''
    ).strip()

    conn = _pg_connect(dsn)
    try:
        if args.mode == 'reset':
            if args.block is not None:
                target_block = args.block
            elif rpc_url:
                latest = _get_latest_block(rpc_url)
                target_block = max(0, latest - 300)
                print(f'[repair_target_cursor] latest_block={latest} reset_to={target_block}')
            else:
                print('ERROR: --block required when EVM_RPC_URL is not set', file=sys.stderr)
                sys.exit(1)
            cmd_reset(conn, args.target_id, target_block)
        elif args.mode == 'catchup':
            if not rpc_url:
                print('ERROR: EVM_RPC_URL required for catchup mode', file=sys.stderr)
                sys.exit(1)
            cmd_catchup(conn, args.target_id, rpc_url, args.batches, args.batch_size)
    finally:
        conn.close()


if __name__ == '__main__':
    main()
