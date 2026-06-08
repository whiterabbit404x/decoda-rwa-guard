#!/usr/bin/env python3
"""Validate a restored PostgreSQL backup in an explicitly isolated database."""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone


def _connect(dsn: str):
    import psycopg
    from psycopg.rows import dict_row
    return psycopg.connect(dsn, row_factory=dict_row)


def validate_restore(dsn: str, *, environment: str, source_region: str | None, recovery_region: str | None, backup_id: str | None) -> dict:
    if os.getenv('RESTORE_VALIDATION_ISOLATED', '').lower() != 'true':
        raise RuntimeError('RESTORE_VALIDATION_ISOLATED=true is required; validation must never target a live database.')
    from services.api.app.evidence_signing import verify_audit_chain, verify_bundle
    from services.api.app.export_storage import load_export_storage

    result = {'audit_chain_valid': True, 'evidence_chain_valid': True, 'workspaces_checked': 0, 'exports_checked': 0, 'errors': []}
    with _connect(dsn) as connection:
        workspaces = connection.execute('SELECT DISTINCT workspace_id FROM audit_logs WHERE workspace_id IS NOT NULL').fetchall()
        for workspace in workspaces:
            rows = connection.execute(
                '''SELECT id, workspace_id, user_id, action, entity_type, entity_id, metadata, created_at,
                          row_hash, previous_row_hash FROM audit_logs
                   WHERE workspace_id = %s ORDER BY created_at, id''',
                (workspace['workspace_id'],),
            ).fetchall()
            initial_anchor = None
            if rows and rows[0].get('previous_row_hash'):
                anchor = connection.execute(
                    '''SELECT chain_anchor_before FROM data_deletion_events
                       WHERE workspace_id = %s AND data_class = 'audit_logs' AND chain_anchor_before = %s
                       ORDER BY created_at DESC LIMIT 1''',
                    (workspace['workspace_id'], rows[0]['previous_row_hash']),
                ).fetchone()
                initial_anchor = str(anchor['chain_anchor_before']) if anchor else None
            verification = verify_audit_chain([dict(row) for row in rows], initial_previous_hash=initial_anchor)
            result['workspaces_checked'] += 1
            if not verification['valid']:
                result['audit_chain_valid'] = False
                result['errors'].append({'workspace_id': str(workspace['workspace_id']), 'audit_errors': verification['errors']})

        storage = load_export_storage()
        exports = connection.execute(
            "SELECT id, storage_object_key FROM export_jobs WHERE status = 'completed' AND export_type IN ('proof_bundle','incident_report') AND deleted_at IS NULL"
        ).fetchall()
        for export in exports:
            key = str(export.get('storage_object_key') or '')
            if not key:
                continue
            try:
                payload = json.loads(storage.read_bytes(object_key=key))
                bundle = payload['rows'][0]
                manifest = bundle.pop('manifest.json')
                seal = bundle.pop('seal.json')
                verification = verify_bundle(bundle, manifest, seal)
                result['exports_checked'] += 1
                if not verification['valid']:
                    result['evidence_chain_valid'] = False
                    result['errors'].append({'export_id': str(export['id']), 'evidence_errors': verification['errors']})
            except Exception as exc:  # noqa: BLE001
                result['evidence_chain_valid'] = False
                result['errors'].append({'export_id': str(export['id']), 'read_error': str(exc)})

        status = 'passed' if result['audit_chain_valid'] and result['evidence_chain_valid'] else 'failed'
        connection.execute(
            '''INSERT INTO recovery_validation_runs
               (id, run_type, environment, source_region, recovery_region, backup_identifier, status,
                audit_chain_valid, evidence_chain_valid, database_checks, completed_at, details)
               VALUES (%s, 'backup_restore', %s, %s, %s, %s, %s, %s, %s, %s::jsonb, NOW(), %s::jsonb)''',
            (str(uuid.uuid4()), environment, source_region, recovery_region, backup_id, status,
             result['audit_chain_valid'], result['evidence_chain_valid'], json.dumps({'select_1': True}), json.dumps(result)),
        )
    result['status'] = status
    result['completed_at'] = datetime.now(timezone.utc).isoformat()
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--database-url', default=os.getenv('RESTORE_DATABASE_URL'))
    parser.add_argument('--environment', default='isolated-restore-validation')
    parser.add_argument('--source-region', default=os.getenv('SOURCE_REGION'))
    parser.add_argument('--recovery-region', default=os.getenv('RECOVERY_REGION'))
    parser.add_argument('--backup-id', default=os.getenv('BACKUP_IDENTIFIER'))
    args = parser.parse_args()
    if not args.database_url:
        parser.error('--database-url or RESTORE_DATABASE_URL is required')
    result = validate_restore(args.database_url, environment=args.environment, source_region=args.source_region, recovery_region=args.recovery_region, backup_id=args.backup_id)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    sys.exit(main())
