from __future__ import annotations

import json
import os
import shlex
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

RUN_TYPES = ('backup_restore', 'regional_failover', 'provider_failover')
_COMMAND_ENV = {
    'backup_restore': 'RECOVERY_DRILL_BACKUP_RESTORE_COMMAND',
    'regional_failover': 'RECOVERY_DRILL_REGIONAL_FAILOVER_COMMAND',
    'provider_failover': 'RECOVERY_DRILL_PROVIDER_FAILOVER_COMMAND',
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, 'isoformat') else str(value)


def _all_integrity_checks_pass(checks: Any) -> bool:
    if not isinstance(checks, dict) or not checks:
        return False
    return all(value is True or (isinstance(value, dict) and value.get('passed') is True) for value in checks.values())


def evaluate_drill_result(run_type: str, result: dict[str, Any], *, target_rto_seconds: int, target_rpo_seconds: int) -> dict[str, Any]:
    """Validate provider output without accepting a command's status claim on faith."""
    integrity_checks = result.get('integrity_checks') or {}
    measured_rto_raw = result.get('measured_rto_seconds')
    measured_rpo_raw = result.get('measured_rpo_seconds')
    try:
        measured_rto = int(measured_rto_raw) if measured_rto_raw is not None else None
    except (TypeError, ValueError):
        measured_rto = None
    try:
        measured_rpo = int(measured_rpo_raw) if measured_rpo_raw is not None else None
    except (TypeError, ValueError):
        measured_rpo = None
    failures: list[str] = []
    if run_type == 'backup_restore' and not str(result.get('backup_identifier') or '').strip():
        failures.append('backup_identifier_missing')
    if measured_rto is None or measured_rto > target_rto_seconds:
        failures.append('rto_target_missed')
    if measured_rpo is None or measured_rpo > target_rpo_seconds:
        failures.append('rpo_target_missed')
    if not _all_integrity_checks_pass(integrity_checks):
        failures.append('integrity_checks_failed')
    if result.get('audit_chain_valid') is not True:
        failures.append('audit_chain_invalid')
    if result.get('evidence_chain_valid') is not True:
        failures.append('evidence_chain_invalid')
    return {
        'passed': not failures,
        'failure_codes': failures,
        'integrity_checks': integrity_checks,
        'measured_rto_seconds': measured_rto,
        'measured_rpo_seconds': measured_rpo,
    }


def recovery_drill_readiness(connection: Any, *, now: datetime | None = None) -> dict[str, Any]:
    now = now or _utc_now()
    rows = connection.execute(
        '''SELECT s.run_type, s.enabled, s.max_success_age_seconds,
                  r.id, r.backup_identifier, r.target_rto_seconds, r.target_rpo_seconds,
                  r.measured_rto_seconds, r.measured_rpo_seconds, r.integrity_checks,
                  r.audit_chain_valid, r.evidence_chain_valid, r.started_at, r.completed_at
             FROM recovery_drill_schedules s
             LEFT JOIN LATERAL (
                 SELECT * FROM recovery_validation_runs candidate
                  WHERE candidate.run_type = s.run_type AND candidate.status = 'passed'
                  ORDER BY candidate.completed_at DESC NULLS LAST LIMIT 1
             ) r ON TRUE
            WHERE s.enabled = TRUE ORDER BY s.run_type'''
    ).fetchall()
    by_type: dict[str, Any] = {}
    for row in rows:
        completed_at = row.get('completed_at')
        fresh_after = now - timedelta(seconds=int(row.get('max_success_age_seconds') or 0))
        checks_pass = _all_integrity_checks_pass(row.get('integrity_checks'))
        targets_met = (
            row.get('measured_rto_seconds') is not None
            and row.get('measured_rpo_seconds') is not None
            and row.get('target_rto_seconds') is not None
            and row.get('target_rpo_seconds') is not None
            and int(row['measured_rto_seconds']) <= int(row['target_rto_seconds'])
            and int(row['measured_rpo_seconds']) <= int(row['target_rpo_seconds'])
        )
        passed = bool(
            row.get('id')
            and completed_at
            and completed_at >= fresh_after
            and row.get('audit_chain_valid') is True
            and row.get('evidence_chain_valid') is True
            and checks_pass
            and targets_met
            and (row['run_type'] != 'backup_restore' or row.get('backup_identifier'))
        )
        if not row.get('id'):
            reason_code = 'recovery_drill_missing'
        elif not completed_at or completed_at < fresh_after:
            reason_code = 'recovery_drill_stale'
        elif not targets_met:
            reason_code = 'recovery_drill_objectives_missed'
        elif not checks_pass or row.get('audit_chain_valid') is not True or row.get('evidence_chain_valid') is not True:
            reason_code = 'recovery_drill_validation_failed'
        elif row['run_type'] == 'backup_restore' and not row.get('backup_identifier'):
            reason_code = 'recovery_drill_backup_unidentified'
        else:
            reason_code = None
        by_type[row['run_type']] = {
            'pass': passed,
            'reason_code': reason_code,
            'latest_successful_run_id': str(row['id']) if row.get('id') else None,
            'completed_at': _iso(completed_at),
            'fresh_until': _iso(completed_at + timedelta(seconds=int(row['max_success_age_seconds']))) if completed_at else None,
            'target_rto_seconds': row.get('target_rto_seconds'),
            'measured_rto_seconds': row.get('measured_rto_seconds'),
            'target_rpo_seconds': row.get('target_rpo_seconds'),
            'measured_rpo_seconds': row.get('measured_rpo_seconds'),
            'backup_identifier': row.get('backup_identifier'),
            'integrity_checks': row.get('integrity_checks') or {},
            'audit_chain_valid': row.get('audit_chain_valid'),
            'evidence_chain_valid': row.get('evidence_chain_valid'),
        }
    missing_types = [run_type for run_type in RUN_TYPES if run_type not in by_type]
    return {
        'pass': bool(by_type) and not missing_types and all(item['pass'] for item in by_type.values()),
        'reason_code': None if by_type and not missing_types and all(item['pass'] for item in by_type.values()) else 'recovery_drills_not_current',
        'required_run_types': list(RUN_TYPES),
        'missing_schedule_types': missing_types,
        'drills': by_type,
    }


def _record_operator_alert(connection: Any, *, run_type: str, alert_kind: str, run_id: str | None, summary: str, details: dict[str, Any], alert: Callable[..., bool]) -> bool:
    fingerprint = f'{run_type}:{alert_kind}:{run_id or details.get("fresh_until") or "missing"}'
    existing = connection.execute(
        'SELECT id, delivered FROM recovery_drill_operator_alerts WHERE fingerprint = %s', (fingerprint,)
    ).fetchone()
    if existing:
        return bool(existing.get('delivered'))
    delivered = alert(f'recovery_drill_{alert_kind}', summary, run_type=run_type, run_id=run_id, **details)
    connection.execute(
        '''INSERT INTO recovery_drill_operator_alerts
             (id, run_type, alert_kind, fingerprint, recovery_validation_run_id, summary, details, delivered)
           VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s)''',
        (str(uuid.uuid4()), run_type, alert_kind, fingerprint, run_id, summary, json.dumps(details), delivered),
    )
    if run_id:
        connection.execute('UPDATE recovery_validation_runs SET operator_alerted_at = NOW() WHERE id = %s', (run_id,))
    return delivered


def _execute_drill_command(run_type: str, schedule: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    command = os.getenv(_COMMAND_ENV[run_type], '').strip()
    if not command:
        return {}, f'{_COMMAND_ENV[run_type]} is not configured'
    timeout = max(30, int(os.getenv('RECOVERY_DRILL_COMMAND_TIMEOUT_SECONDS', '14400')))
    try:
        completed = subprocess.run(shlex.split(command), capture_output=True, text=True, timeout=timeout, check=False)
    except Exception as exc:
        return {}, f'{type(exc).__name__}: {exc}'
    if completed.returncode != 0:
        return {}, (completed.stderr or completed.stdout or f'command exited {completed.returncode}')[-2000:]
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return {}, f'invalid JSON output: {exc}'
    return payload if isinstance(payload, dict) else {}, None


def run_recovery_drill_cycle(connection: Any, *, alert: Callable[..., bool], now: datetime | None = None) -> dict[str, Any]:
    now = now or _utc_now()
    schedules = connection.execute(
        '''SELECT * FROM recovery_drill_schedules
            WHERE enabled = TRUE AND next_run_at <= %s
            ORDER BY next_run_at FOR UPDATE SKIP LOCKED''', (now,)
    ).fetchall()
    summary: dict[str, Any] = {'due': len(schedules), 'passed': 0, 'failed': 0, 'stale_alerts': 0, 'runs': []}
    for schedule in schedules:
        run_type = schedule['run_type']
        run_id = str(uuid.uuid4())
        connection.execute(
            '''INSERT INTO recovery_validation_runs
                 (id, run_type, environment, source_region, recovery_region, status, started_at,
                  scheduled_for, trigger_type, target_rto_seconds, target_rpo_seconds)
               VALUES (%s,%s,%s,%s,%s,'running',%s,%s,'scheduler',%s,%s)''',
            (run_id, run_type, os.getenv('APP_ENV', 'development'), os.getenv('PRIMARY_REGION') or None,
             os.getenv('RECOVERY_REGION') or None, now, schedule['next_run_at'], schedule['target_rto_seconds'], schedule['target_rpo_seconds']),
        )
        connection.execute(
            "UPDATE recovery_drill_schedules SET last_started_at=%s,last_status='running',updated_at=%s WHERE run_type=%s",
            (now, now, run_type),
        )
        payload, command_error = _execute_drill_command(run_type, schedule)
        validation = evaluate_drill_result(
            run_type, payload, target_rto_seconds=int(schedule['target_rto_seconds']), target_rpo_seconds=int(schedule['target_rpo_seconds'])
        ) if not command_error else {'passed': False, 'failure_codes': ['drill_command_failed'], 'integrity_checks': {}, 'measured_rto_seconds': None, 'measured_rpo_seconds': None}
        passed = bool(validation['passed'])
        failure_codes = validation['failure_codes']
        failure_message = command_error or (', '.join(failure_codes) if failure_codes else None)
        completed_at = _utc_now()
        connection.execute(
            '''UPDATE recovery_validation_runs SET status=%s, backup_identifier=%s, measured_rto_seconds=%s,
                  measured_rpo_seconds=%s, integrity_checks=%s::jsonb, database_checks=%s::jsonb,
                  audit_chain_valid=%s, evidence_chain_valid=%s, completed_at=%s, failure_code=%s,
                  failure_message=%s, details=%s::jsonb WHERE id=%s''',
            ('passed' if passed else 'failed', payload.get('backup_identifier'), validation['measured_rto_seconds'],
             validation['measured_rpo_seconds'], json.dumps(validation['integrity_checks']), json.dumps(payload.get('database_checks') or {}),
             payload.get('audit_chain_valid'), payload.get('evidence_chain_valid'), completed_at,
             failure_codes[0] if failure_codes else None, failure_message, json.dumps(payload.get('details') or {}), run_id),
        )
        connection.execute(
            '''UPDATE recovery_drill_schedules SET last_completed_at=%s,last_status=%s,
                  next_run_at=%s,updated_at=%s WHERE run_type=%s''',
            (completed_at, 'passed' if passed else 'failed', completed_at + timedelta(seconds=int(schedule['cadence_seconds'])), completed_at, run_type),
        )
        if not passed:
            _record_operator_alert(connection, run_type=run_type, alert_kind='failed', run_id=run_id,
                                   summary=f'{run_type} recovery drill failed.', details={'failure_codes': failure_codes, 'failure_message': failure_message}, alert=alert)
        summary['passed' if passed else 'failed'] += 1
        summary['runs'].append({'id': run_id, 'run_type': run_type, 'status': 'passed' if passed else 'failed', 'failure_codes': failure_codes})

    readiness = recovery_drill_readiness(connection, now=now)
    for run_type, drill in readiness['drills'].items():
        if drill['pass']:
            connection.execute(
                "UPDATE recovery_drill_operator_alerts SET resolved_at=%s WHERE run_type=%s AND resolved_at IS NULL",
                (now, run_type),
            )
            continue
        if drill['reason_code'] in {'recovery_drill_missing', 'recovery_drill_stale'}:
            _record_operator_alert(connection, run_type=run_type, alert_kind='stale', run_id=None,
                                   summary=f'{run_type} recovery drill is missing or stale.', details={'reason_code': drill['reason_code'], 'fresh_until': drill['fresh_until']}, alert=alert)
            summary['stale_alerts'] += 1
    connection.commit()
    summary['readiness'] = readiness
    return summary
