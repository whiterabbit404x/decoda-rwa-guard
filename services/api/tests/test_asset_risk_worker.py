"""Asset Risk Assessor worker — enqueue idempotency, claim/lease, cycle flow."""

from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal

from services.api.app import pilot
from services.api.app.domains.asset_risk import worker


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeConn:
    def __init__(self, matchers=None, tables_exist=True):
        self.matchers = matchers or []
        self.tables_exist = tables_exist
        self.writes = []
        self.committed = False

    def execute(self, query, params=None):
        q = ' '.join(str(query).split())
        ql = q.lower()
        if 'to_regclass' in ql:
            return _Result([{'ok': self.tables_exist}])
        for needle, rows in self.matchers:
            if needle in q:
                if any(k in ql for k in ('insert', 'update', 'delete')):
                    self.writes.append((q, params))
                return _Result(rows)
        if any(k in ql for k in ('insert into', 'update ', 'delete ')):
            self.writes.append((q, params))
        return _Result([])

    def commit(self):
        self.committed = True

    def writes_matching(self, needle):
        return [(q, p) for (q, p) in self.writes if needle in q]


def test_enqueue_skips_when_active_job_exists():
    conn = FakeConn(matchers=[
        ('SELECT id, status FROM asset_risk_jobs', [{'id': 'job-existing', 'status': 'running'}]),
    ])
    out = worker.enqueue_assessment(conn, workspace_id='ws-1', asset_id='a1')
    assert out['enqueued'] is False
    assert out['status'] == 'running'
    # No new job inserted.
    assert not conn.writes_matching('INSERT INTO asset_risk_jobs')


def test_enqueue_inserts_when_none_active():
    conn = FakeConn(matchers=[
        ('SELECT id, status FROM asset_risk_jobs', []),  # no active job
    ])
    out = worker.enqueue_assessment(conn, workspace_id='ws-1', asset_id='a1', trigger_source='manual', requested_by_user_id='u1')
    assert out['enqueued'] is True
    assert conn.writes_matching('INSERT INTO asset_risk_jobs')


def test_claim_next_job_uses_skip_locked_and_lease():
    conn = FakeConn(matchers=[
        ('UPDATE asset_risk_jobs SET status =', [{'id': 'job-1', 'workspace_id': 'ws-1', 'asset_id': 'a1', 'trigger_source': 'worker', 'attempts': 1, 'max_attempts': 3}]),
    ])
    claimed = worker._claim_next_job(conn, lease_owner='w-1', lease_seconds=300)
    assert claimed['asset_id'] == 'a1'
    # The claim query must use FOR UPDATE SKIP LOCKED.
    claim_writes = conn.writes_matching('UPDATE asset_risk_jobs SET status =')
    assert claim_writes and 'SKIP LOCKED' in claim_writes[0][0]


def test_run_once_processes_claimed_job(monkeypatch):
    # First connection: enqueue due assets (none). Then claim one job, assess, complete, then no more.
    calls = {'n': 0}

    def fake_pg():
        @contextmanager
        def _cm():
            calls['n'] += 1
            if calls['n'] == 1:
                # enqueue-due-assets cycle: return no due assets
                yield FakeConn(matchers=[('FROM assets a', [])])
            elif calls['n'] == 2:
                # claim a job
                yield FakeConn(matchers=[
                    ('UPDATE asset_risk_jobs SET status =', [{'id': 'job-1', 'workspace_id': 'ws-1', 'asset_id': 'a1', 'trigger_source': 'worker', 'attempts': 1, 'max_attempts': 3}]),
                    ('SELECT * FROM assets WHERE id =', [_asset_row()]),
                    ('EXISTS(SELECT 1 FROM targets', [{'has_target': True, 'has_system': True, 'has_telemetry': True, 'telemetry_fresh': True}]),
                    ('FROM asset_valuation_snapshots', [{'n': 30, 'mean_30d': Decimal('1.0'), 'std_30d': Decimal('0.001'), 'mean_7d': Decimal('1.0')}]),
                    ('FROM alerts', [{'n': 0}]),
                    ('SELECT id, occurrence_count FROM alerts', []),
                    ('SELECT id, finding_type, alert_id FROM asset_risk_findings', []),
                ])
            else:
                # no more jobs
                yield FakeConn(matchers=[('UPDATE asset_risk_jobs SET status =', [])])
        return _cm()

    monkeypatch.setattr(pilot, 'pg_connection', lambda: fake_pg())
    cfg = _cfg()
    out = worker.run_asset_risk_worker_once(cfg)
    assert out['processed'] == 1
    assert out['failed'] == 0


def _asset_row():
    from datetime import datetime, timezone, timedelta
    return {
        'id': 'a1', 'workspace_id': 'ws-1', 'name': 'Asset', 'created_by_user_id': 'u1',
        'rwa_asset_type': 'stablecoin', 'reserve_feed_type': 'attestation', 'reserve_value_usd': Decimal('128'),
        'reserve_verified_at': datetime(2026, 7, 23, 11, 55, tzinfo=timezone.utc), 'reserve_min_coverage_ratio': Decimal('1.0'),
        'reserve_update_interval_seconds': None, 'circulating_supply': Decimal('100000000'), 'token_decimals': 0,
        'reference_price_usd': Decimal('1.00'), 'value_usd': Decimal('100'), 'price_source': 'chainlink',
        'oracle_sources': [], 'chainlink_feeds': [], 'token_contract_address': '0x' + 'a' * 40,
        'token_standard': 'erc20', 'verification_status': 'verified', 'verification_summary': {},
    }


def _cfg():
    return {
        'baseline_days': 30, 'min_baseline_samples': 5, 'reserve_stale_seconds': 86400,
        'price_stale_seconds': 3600, 'deviation_medium_percent': Decimal('5'),
        'deviation_high_percent': Decimal('15'), 'zscore_high': Decimal('3'),
        'oracle_disagreement_percent': Decimal('2'), 'default_min_coverage_ratio': Decimal('1.0'),
        'over_collateralization_ratio': Decimal('2.0'), 'batch_size': 5, 'job_lease_seconds': 300,
        'max_attempts': 3, 'assessment_stale_seconds': 3600, 'interval_seconds': 900,
    }
