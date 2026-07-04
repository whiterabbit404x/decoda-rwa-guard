#!/usr/bin/env python3
"""Inspect the persisted detection-path facts for a wallet-transfer tx_hash.

Read-only diagnostic for the customer-facing "Detected By" column. For a given
tx_hash (optionally scoped to a workspace/target) it prints, for every
telemetry_events row carrying that tx_hash:

    telemetry_id, workspace_id, target_id, event_type, evidence_source,
    observed_at, ingested_at,
    top_level_detected_by      (payload_json->>'detected_by')
    source_type                (payload_json->>'source_type')
    details.detected_by / details.source_type
    metadata.detected_by
    ingestion_path             (payload_json ingestion_source / ingestion_method)
    created_by_worker          (telemetry_events.provider_type column)
    resolved_detected_by + basis (classify_wallet_transfer_detected_by — the
                                  exact value the API returns for the row)

Nothing is written. Use it to answer "why does this row render Unknown?" — a
row is truly unclassifiable only when every field above is empty AND the
provider_type names no known writer family.

Usage:
    DATABASE_URL=postgres://... python services/api/scripts/inspect_tx_detected_by.py \
        --tx-hash 0xef5324... [--workspace-id <uuid>] [--target-id <uuid>]

Environment:
    DATABASE_URL   PostgreSQL DSN (required)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _ensure_repo_root_on_path() -> None:
    for candidate in Path(__file__).resolve().parents:
        if (candidate / 'services' / 'api' / 'app').is_dir():
            candidate_str = str(candidate)
            if candidate_str not in sys.path:
                sys.path.insert(0, candidate_str)
            return


_ensure_repo_root_on_path()

from services.api.app.worker_status import classify_wallet_transfer_detected_by  # noqa: E402


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


def inspect_tx(conn, tx_hash: str, workspace_id: str | None, target_id: str | None) -> int:
    tx_hash_norm = tx_hash.strip().lower()
    clauses = ["lower(COALESCE(payload_json->>'tx_hash', payload_json->>'hash')) = %s"]
    params: list = [tx_hash_norm]
    if workspace_id:
        clauses.append('workspace_id = %s::uuid')
        params.append(workspace_id)
    if target_id:
        clauses.append('target_id = %s::uuid')
        params.append(target_id)
    rows = _rows(
        conn,
        f'''
        SELECT id, workspace_id, target_id, event_type, provider_type,
               evidence_source, observed_at, ingested_at, payload_json
        FROM telemetry_events
        WHERE {' AND '.join(clauses)}
        ORDER BY observed_at DESC
        LIMIT 50
        ''',
        tuple(params),
    )
    if not rows:
        print(f'No telemetry_events row found for tx_hash={tx_hash_norm}')
        return 1

    for row in rows:
        payload = row.get('payload_json')
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                payload = {}
        payload = payload if isinstance(payload, dict) else {}
        details = payload.get('details') if isinstance(payload.get('details'), dict) else {}
        metadata = payload.get('metadata') if isinstance(payload.get('metadata'), dict) else {}
        resolved, basis = classify_wallet_transfer_detected_by(
            payload=payload,
            provider_type=row.get('provider_type'),
            event_type=row.get('event_type'),
            evidence_source=row.get('evidence_source'),
        )
        facts = {
            'telemetry_id': str(row.get('id')),
            'workspace_id': str(row.get('workspace_id')),
            'target_id': str(row.get('target_id')),
            'event_type': row.get('event_type'),
            'evidence_source': row.get('evidence_source'),
            'observed_at': str(row.get('observed_at')),
            'ingested_at': str(row.get('ingested_at')),
            'top_level_detected_by': payload.get('detected_by'),
            'source_type': payload.get('source_type'),
            'details.detected_by': details.get('detected_by'),
            'details.source_type': details.get('source_type'),
            'metadata.detected_by': metadata.get('detected_by'),
            'ingestion_path': payload.get('ingestion_source') or payload.get('ingestion_method'),
            'created_by_worker': row.get('provider_type'),
            'detected_by_source_marker': payload.get('detected_by_source'),
            'resolved_detected_by': resolved or 'unknown',
            'resolved_basis': basis,
        }
        print('-' * 72)
        for key, value in facts.items():
            print(f'{key:>26}: {value if value not in (None, "") else "-"}')
    print('-' * 72)
    print(f'{len(rows)} row(s) inspected for tx_hash={tx_hash_norm}')
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tx-hash', required=True, help='0x-prefixed transaction hash')
    parser.add_argument('--workspace-id', default=None, help='Optional workspace UUID scope')
    parser.add_argument('--target-id', default=None, help='Optional target UUID scope')
    args = parser.parse_args()

    dsn = os.getenv('DATABASE_URL')
    if not dsn:
        print('DATABASE_URL is required', file=sys.stderr)
        return 2
    conn = _pg_connect(dsn)
    try:
        return inspect_tx(conn, args.tx_hash, args.workspace_id, args.target_id)
    finally:
        conn.close()


if __name__ == '__main__':
    raise SystemExit(main())
