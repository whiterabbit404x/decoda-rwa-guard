#!/usr/bin/env python3
"""Repair Base wallet targets stored with wrong chain_id=1 / chain_network=ethereum-mainnet.

Usage:
    python scripts/repair_base_chain_targets.py --workspace-id <UUID> [--dry-run]

Options:
    --workspace-id WORKSPACE_ID   UUID of the workspace to repair (required)
    --target-chain-id CHAIN_ID    Target chain ID to set (default: 8453 for Base mainnet)
    --target-chain-network NET    chain_network to set (default: base-mainnet)
    --interval-seconds SECS       Set monitoring_interval_seconds on repaired targets (default: 60)
    --dry-run                     Print what would be changed without making changes

Environment:
    DATABASE_URL    PostgreSQL connection string (required)

Exit codes:
    0   Success (or dry-run with no errors)
    1   Error (missing DATABASE_URL, DB failure)
"""
from __future__ import annotations

import argparse
import os
import sys


def _db_connect():
    db_url = (os.getenv('DATABASE_URL') or '').strip()
    if not db_url:
        print('ERROR: DATABASE_URL is not set', file=sys.stderr)
        sys.exit(1)
    try:
        import psycopg2
        return psycopg2.connect(db_url)
    except ImportError:
        pass
    try:
        import psycopg
        return psycopg.connect(db_url)
    except ImportError:
        pass
    print('ERROR: neither psycopg2 nor psycopg is installed', file=sys.stderr)
    sys.exit(1)


def repair(
    *,
    workspace_id: str,
    target_chain_id: int,
    target_chain_network: str,
    interval_seconds: int,
    dry_run: bool,
) -> None:
    conn = _db_connect()
    cur = conn.cursor()

    # Find targets in the workspace that have wrong chain_id or chain_network
    cur.execute(
        """
        SELECT t.id, t.name, t.chain_network, t.chain_id, t.monitoring_interval_seconds
        FROM targets t
        WHERE t.workspace_id = %s::uuid
          AND t.deleted_at IS NULL
          AND COALESCE(t.enabled, FALSE) = TRUE
          AND LOWER(COALESCE(t.chain_network, '')) NOT IN (%s, %s)
        ORDER BY t.created_at
        """,
        (workspace_id, target_chain_network, target_chain_network.split('-')[0]),
    )
    rows = cur.fetchall()

    if not rows:
        print(f'No targets with wrong chain in workspace {workspace_id}')
        conn.close()
        return

    print(f'Found {len(rows)} targets to repair in workspace {workspace_id}:')
    for row in rows:
        tid, name, net, cid, interval = row
        print(f'  {tid}  name={name!r}  chain_network={net!r}  chain_id={cid}  interval={interval}s')

    if dry_run:
        print('\n[dry-run] No changes applied.')
        conn.close()
        return

    target_ids = [str(r[0]) for r in rows]

    # Fix targets
    cur.execute(
        """
        UPDATE targets
        SET chain_network = %s,
            chain_id = %s,
            monitoring_interval_seconds = %s,
            updated_at = NOW()
        WHERE id = ANY(%s::uuid[])
          AND workspace_id = %s::uuid
          AND deleted_at IS NULL
        """,
        (target_chain_network, target_chain_id, interval_seconds, target_ids, workspace_id),
    )
    print(f'Updated {cur.rowcount} targets: chain_network={target_chain_network}, chain_id={target_chain_id}')

    # Fix linked assets
    cur.execute(
        """
        UPDATE assets a
        SET chain_network = %s,
            updated_at = NOW()
        FROM targets t
        WHERE a.id = t.asset_id
          AND t.workspace_id = %s::uuid
          AND t.id = ANY(%s::uuid[])
          AND t.deleted_at IS NULL
          AND a.deleted_at IS NULL
        """,
        (target_chain_network, workspace_id, target_ids),
    )
    print(f'Updated {cur.rowcount} linked assets: chain_network={target_chain_network}')

    # Fix monitored_systems
    cur.execute(
        """
        UPDATE monitored_systems ms
        SET chain = %s
        FROM targets t
        WHERE ms.target_id = t.id
          AND t.workspace_id = %s::uuid
          AND t.id = ANY(%s::uuid[])
          AND t.deleted_at IS NULL
        """,
        (target_chain_network, workspace_id, target_ids),
    )
    print(f'Updated {cur.rowcount} monitored_systems rows: chain={target_chain_network}')

    conn.commit()
    conn.close()
    print('\nRepair complete. Targets will be polled on the next worker cycle.')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--workspace-id', required=True, help='UUID of the workspace to repair')
    parser.add_argument('--target-chain-id', type=int, default=8453, help='chain_id to set (default: 8453)')
    parser.add_argument('--target-chain-network', default='base-mainnet', help='chain_network to set (default: base-mainnet)')
    parser.add_argument('--interval-seconds', type=int, default=60, help='polling interval in seconds (default: 60)')
    parser.add_argument('--dry-run', action='store_true', help='Print changes without applying them')
    args = parser.parse_args()

    repair(
        workspace_id=args.workspace_id,
        target_chain_id=args.target_chain_id,
        target_chain_network=args.target_chain_network,
        interval_seconds=args.interval_seconds,
        dry_run=args.dry_run,
    )


if __name__ == '__main__':
    main()
