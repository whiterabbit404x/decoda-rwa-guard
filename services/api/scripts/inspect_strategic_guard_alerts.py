#!/usr/bin/env python3
"""Inspect wallet_transfer_detected telemetry and Strategic Infrastructure Guard alerts.

Read-only diagnostic for the alert backfill/dedupe pipeline. For a given workspace +
target it prints, for every wallet_transfer_detected / native_transfer telemetry row:

    telemetry_id, tx_hash, event_type, chain_id, evidence_source, observed_at,
    alert_id (if a matching alert exists), dedupe_key

and, for the alerts table (rule_key = strategic_infrastructure_guard_wallet_outbound_transfer,
plus the direction-agnostic smoke rule) scoped to the workspace + target:

    alert_id, tx_hash, dedupe_signature (dedupe_key), created_at, evidence/payload JSON

Nothing is written. Use it to confirm whether each unique tx_hash has its own alert.

Usage:
    DATABASE_URL=postgres://... python services/api/scripts/inspect_strategic_guard_alerts.py \
        --workspace-id 1155f479-3e5b-4d90-be6c-fd6c1d6b957d \
        --target-id e7851a52-8fb1-48cd-84a3-d033f591c5dd

Environment:
    DATABASE_URL   PostgreSQL DSN (required)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid

# Canonical Strategic Infrastructure Guard rule key (mirrors
# services.api.app.monitoring_runner._SIG_RULE_KEY).
SIG_RULE_KEY = 'strategic_infrastructure_guard_wallet_outbound_transfer'
SMOKE_RULE_KEY = 'smoke_wallet_transfer'

# Defaults match the target/workspace under investigation; override via CLI flags.
DEFAULT_WORKSPACE_ID = '1155f479-3e5b-4d90-be6c-fd6c1d6b957d'
DEFAULT_TARGET_ID = 'e7851a52-8fb1-48cd-84a3-d033f591c5dd'


def _sig_dedupe_signature(*, workspace_id: str, target_id: str, chain_id, tx_hash: str) -> str:
    """Mirror of monitoring_runner._sig_dedupe_signature (kept in sync intentionally).

    Key = workspace_id + target_id + chain_id + tx_hash + rule_key. Different tx_hash
    always yields a different key, so each transaction maps to its own alert.
    """
    seed = json.dumps(
        {
            'workspace_id': str(workspace_id),
            'target_id': str(target_id),
            'chain_id': int(chain_id or 0),
            'tx_hash': str(tx_hash or ''),
            'rule': SIG_RULE_KEY,
        },
        sort_keys=True,
    )
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


def _pg_connect(dsn: str):
    try:
        import psycopg
        conn = psycopg.connect(dsn, row_factory=psycopg.rows.dict_row)
        conn.autocommit = True
        return conn
    except ImportError:
        pass
    try:
        import psycopg2
        import psycopg2.extras
        conn = psycopg2.connect(dsn, cursor_factory=psycopg2.extras.RealDictCursor)
        conn.autocommit = True
        return conn
    except ImportError:
        pass
    raise RuntimeError('Neither psycopg nor psycopg2 is installed')


def _rows(conn, sql: str, params: tuple):
    cur = conn.cursor()
    cur.execute(sql, params)
    return cur.fetchall()


def _short(value, head: int = 10, tail: int = 6) -> str:
    s = str(value or '')
    if len(s) <= head + tail + 1:
        return s
    return f'{s[:head]}…{s[-tail:]}'


def inspect(conn, workspace_id: str, target_id: str) -> int:
    telemetry = _rows(
        conn,
        '''
        SELECT id, target_id, event_type, evidence_source, observed_at, payload_json
        FROM telemetry_events
        WHERE workspace_id = %s::uuid
          AND target_id = %s::uuid
          AND event_type IN ('wallet_transfer_detected', 'native_transfer')
        ORDER BY observed_at DESC
        LIMIT 200
        ''',
        (workspace_id, target_id),
    )

    alerts = _rows(
        conn,
        '''
        SELECT a.id, a.dedupe_signature, a.severity, a.status, a.module_key, a.created_at,
               a.payload, a.detection_id,
               a.payload->>'tx_hash' AS payload_tx_hash,
               a.payload->>'telemetry_id' AS payload_telemetry_id,
               a.payload->>'rule_key' AS payload_rule_key
        FROM alerts a
        WHERE a.workspace_id = %s::uuid
          AND a.target_id = %s::uuid
          AND (
            a.payload->>'rule_key' IN (%s, %s)
            OR a.payload->>'tx_hash' IS NOT NULL
            OR a.payload->>'telemetry_id' IS NOT NULL
          )
        ORDER BY a.created_at DESC
        LIMIT 200
        ''',
        (workspace_id, target_id, SIG_RULE_KEY, SMOKE_RULE_KEY),
    )

    # Index alerts by tx_hash and telemetry_id for per-row linkage reporting.
    by_tx: dict[str, list[dict]] = {}
    by_telemetry: dict[str, list[dict]] = {}
    for a in alerts:
        txh = str(a.get('payload_tx_hash') or '').strip().lower()
        if txh:
            by_tx.setdefault(txh, []).append(a)
        tid = str(a.get('payload_telemetry_id') or '').strip()
        if tid:
            by_telemetry.setdefault(tid, []).append(a)

    print('=' * 100)
    print(f'workspace_id = {workspace_id}')
    print(f'target_id    = {target_id}')
    print('=' * 100)
    print(f'\n[1] wallet_transfer telemetry rows: {len(telemetry)}\n')
    for row in telemetry:
        payload = row.get('payload_json') or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}
        tx_hash = str(payload.get('tx_hash') or payload.get('hash') or '').strip()
        chain_id = payload.get('chain_id')
        telemetry_id = str(row.get('id'))
        dedupe_key = _sig_dedupe_signature(
            workspace_id=workspace_id, target_id=target_id, chain_id=chain_id or 8453, tx_hash=tx_hash,
        ) if tx_hash else 'none'
        linked = by_telemetry.get(telemetry_id) or by_tx.get(tx_hash.lower())
        alert_id = str(linked[0].get('id')) if linked else 'NONE'
        print(f'  telemetry_id   = {telemetry_id}')
        print(f'    tx_hash        = {tx_hash or "(missing)"}  ({_short(tx_hash)})')
        print(f'    event_type     = {row.get("event_type")}')
        print(f'    chain_id       = {chain_id}')
        print(f'    evidence_source= {row.get("evidence_source")}')
        print(f'    observed_at    = {row.get("observed_at")}')
        print(f'    alert_id       = {alert_id}{"" if linked else "  <-- NO ALERT"}')
        print(f'    dedupe_key     = {dedupe_key}')
        print('')

    print(f'[2] alerts (SIG rule={SIG_RULE_KEY!r} / smoke + tx-bearing): {len(alerts)}\n')
    for a in alerts:
        payload = a.get('payload') or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}
        print(f'  alert_id        = {a.get("id")}')
        print(f'    tx_hash         = {a.get("payload_tx_hash")}  ({_short(a.get("payload_tx_hash"))})')
        print(f'    rule_key        = {a.get("payload_rule_key")}')
        print(f'    dedupe_key      = {a.get("dedupe_signature")}')
        print(f'    severity/status = {a.get("severity")}/{a.get("status")}')
        print(f'    module_key      = {a.get("module_key")}')
        print(f'    detection_id    = {a.get("detection_id")}')
        print(f'    created_at      = {a.get("created_at")}')
        evidence = {
            k: payload.get(k)
            for k in ('tx_hash', 'from_address', 'to_address', 'chain_id', 'block_number',
                      'telemetry_id', 'evidence_source', 'value_wei', 'amount_wei')
            if k in payload
        }
        print(f'    evidence        = {json.dumps(evidence, default=str)}')
        print('')

    # Summary: count distinct tx_hashes and how many have an alert.
    tx_hashes = set()
    tx_with_alert = set()
    for row in telemetry:
        payload = row.get('payload_json') or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}
        txh = str(payload.get('tx_hash') or payload.get('hash') or '').strip().lower()
        if not txh:
            continue
        tx_hashes.add(txh)
        if by_tx.get(txh) or by_telemetry.get(str(row.get('id'))):
            tx_with_alert.add(txh)
    print('-' * 100)
    print(f'distinct wallet_transfer tx_hashes : {len(tx_hashes)}')
    print(f'tx_hashes with >=1 linked alert    : {len(tx_with_alert)}')
    missing = tx_hashes - tx_with_alert
    if missing:
        print(f'tx_hashes MISSING an alert         : {len(missing)}')
        for txh in sorted(missing):
            print(f'    - {txh}  ({_short(txh)})')
    else:
        print('every distinct tx_hash has at least one linked alert')
    print('-' * 100)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace-id', default=DEFAULT_WORKSPACE_ID)
    parser.add_argument('--target-id', default=DEFAULT_TARGET_ID)
    args = parser.parse_args()

    dsn = os.getenv('DATABASE_URL', '').strip()
    if not dsn:
        print('[inspect_strategic_guard_alerts] ERROR: DATABASE_URL is required', file=sys.stderr)
        return 2
    for label, value in (('workspace-id', args.workspace_id), ('target-id', args.target_id)):
        try:
            uuid.UUID(str(value))
        except (ValueError, AttributeError):
            print(f'[inspect_strategic_guard_alerts] ERROR: --{label} must be a UUID', file=sys.stderr)
            return 2

    conn = _pg_connect(dsn)
    try:
        return inspect(conn, args.workspace_id, args.target_id)
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == '__main__':
    raise SystemExit(main())
