from __future__ import annotations

from datetime import datetime, timedelta, timezone

from services.api.app.recovery_drills import evaluate_drill_result, recovery_drill_readiness


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self, rows):
        self.rows = rows
        self.query = ''

    def execute(self, query, params=None):
        self.query = query
        return _Rows(self.rows)


def _successful_row(run_type: str, now: datetime) -> dict:
    return {
        'run_type': run_type,
        'enabled': True,
        'max_success_age_seconds': 86400,
        'id': f'{run_type}-run',
        'backup_identifier': 'backup-2026-06-08' if run_type == 'backup_restore' else None,
        'target_rto_seconds': 3600,
        'target_rpo_seconds': 300,
        'measured_rto_seconds': 1200,
        'measured_rpo_seconds': 60,
        'integrity_checks': {'row_counts': True, 'constraints': {'passed': True}},
        'audit_chain_valid': True,
        'evidence_chain_valid': True,
        'started_at': now - timedelta(minutes=21),
        'completed_at': now - timedelta(minutes=1),
    }


def test_evaluate_drill_result_requires_objectives_integrity_and_chains() -> None:
    result = evaluate_drill_result(
        'backup_restore',
        {
            'backup_identifier': 'backup-123',
            'measured_rto_seconds': 901,
            'measured_rpo_seconds': 61,
            'integrity_checks': {'row_counts': True},
            'audit_chain_valid': False,
            'evidence_chain_valid': True,
        },
        target_rto_seconds=900,
        target_rpo_seconds=60,
    )

    assert result['passed'] is False
    assert result['failure_codes'] == ['rto_target_missed', 'rpo_target_missed', 'audit_chain_invalid']


def test_recovery_readiness_requires_recent_success_for_every_drill_type() -> None:
    now = datetime(2026, 6, 8, tzinfo=timezone.utc)
    connection = _Connection([
        _successful_row('backup_restore', now),
        _successful_row('regional_failover', now),
        _successful_row('provider_failover', now),
    ])

    result = recovery_drill_readiness(connection, now=now)

    assert result['pass'] is True
    assert result['reason_code'] is None
    assert 'status = \'passed\'' in connection.query
    assert all(drill['pass'] for drill in result['drills'].values())


def test_recovery_readiness_rejects_stale_success_instead_of_schema_presence() -> None:
    now = datetime(2026, 6, 8, tzinfo=timezone.utc)
    rows = [
        _successful_row('backup_restore', now),
        _successful_row('regional_failover', now),
        _successful_row('provider_failover', now),
    ]
    rows[0]['completed_at'] = now - timedelta(days=2)

    result = recovery_drill_readiness(_Connection(rows), now=now)

    assert result['pass'] is False
    assert result['reason_code'] == 'recovery_drills_not_current'
    assert result['drills']['backup_restore']['pass'] is False
    assert result['drills']['backup_restore']['reason_code'] == 'recovery_drill_stale'


class _AlertConnection:
    def __init__(self):
        self.queries = []

    def execute(self, query, params=None):
        self.queries.append((query, params))
        if query.startswith('SELECT id, delivered'):
            class _One:
                def fetchone(self):
                    return None
            return _One()
        return _Rows([])


def test_failed_drill_alert_is_persisted_and_delivered(monkeypatch) -> None:
    from services.api.app import recovery_drills

    connection = _AlertConnection()
    delivered = []

    result = recovery_drills._record_operator_alert(
        connection,
        run_type='provider_failover',
        alert_kind='failed',
        run_id='00000000-0000-0000-0000-000000000001',
        summary='Provider failover failed.',
        details={'failure_codes': ['rto_target_missed']},
        alert=lambda alert_type, summary, **details: delivered.append((alert_type, summary, details)) or True,
    )

    assert result is True
    assert delivered[0][0] == 'recovery_drill_failed'
    assert any('INSERT INTO recovery_drill_operator_alerts' in query for query, _ in connection.queries)
    assert any('operator_alerted_at' in query for query, _ in connection.queries)
