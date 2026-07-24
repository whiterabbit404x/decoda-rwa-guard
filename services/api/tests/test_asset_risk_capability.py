"""Asset Risk Assessor — runtime capability, execution policy, and job recovery.

Covers the Screen 3 follow-up that fixed the misleading "pending" state:
  * assessment_capability shape + execution-mode derivation
  * a disabled worker with on-demand disabled returns a structured 503 and does NOT
    create an indefinitely-queued job
  * on-demand bounded assessment runs inline while the background worker is disabled
  * stale queued / expired-lease running jobs are reconciled to a terminal state
  * the worker heartbeat drives worker_healthy (never an env var alone)
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta

from services.api.app import pilot
from services.api.app.domains.asset_risk import config as arc
from services.api.app.domains.asset_risk import registry, summary, worker


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


def _cfg(**overrides):
    base = {
        'enabled': False, 'on_demand_enabled': True, 'interval_seconds': 900,
        'worker_heartbeat_stale_seconds': 1800, 'queued_job_timeout_seconds': 900,
        'assessment_stale_seconds': 3600, 'batch_size': 25, 'job_lease_seconds': 300, 'max_attempts': 3,
    }
    base.update(overrides)
    return base


class _Req:
    headers = {'authorization': 'Bearer t', 'x-workspace-id': 'ws1'}


# --------------------------------------------------------------------------
# execution_mode + capability derivation
# --------------------------------------------------------------------------
def test_execution_mode_is_canonical():
    # Background worker healthy -> background regardless of on-demand.
    assert arc.execution_mode(_cfg(enabled=True), worker_healthy=True) == 'background'
    # Worker down but on-demand enabled -> on_demand (bounded synchronous path).
    assert arc.execution_mode(_cfg(on_demand_enabled=True), worker_healthy=False) == 'on_demand'
    # Worker down AND on-demand disabled -> unavailable (no execution path).
    assert arc.execution_mode(_cfg(on_demand_enabled=False), worker_healthy=False) == 'unavailable'


def test_capability_shape_reports_modes_truthfully():
    worker_health = {
        'enabled': False, 'healthy': False, 'last_heartbeat_at': None, 'queued': 2, 'running': 0,
        'blocked': 0, 'active_jobs': 2, 'oldest_queued_age_seconds': 120, 'last_completed_at': None,
        'last_error': None, 'last_error_at': None, 'last_failure_code': None,
    }
    cap = summary.build_assessment_capability(_cfg(on_demand_enabled=True), worker_health)
    assert cap['background_enabled'] is False
    assert cap['on_demand_enabled'] is True
    assert cap['worker_healthy'] is False
    assert cap['execution_mode'] == 'on_demand'
    assert cap['queue_depth'] == 2 and cap['active_job_count'] == 2
    assert cap['oldest_queued_job_age_seconds'] == 120

    # On-demand disabled + no heartbeat -> unavailable.
    cap2 = summary.build_assessment_capability(_cfg(on_demand_enabled=False), {**worker_health, 'healthy': False})
    assert cap2['execution_mode'] == 'unavailable'


def test_worker_healthy_requires_a_fresh_heartbeat_not_just_env_flag():
    now = pilot.utc_now()
    fresh = now - timedelta(seconds=60)
    stale = now - timedelta(seconds=100000)
    # Enabled + fresh heartbeat -> healthy.
    assert arc.worker_heartbeat_is_fresh(fresh, now, _cfg(enabled=True)) is True
    # Enabled but stale heartbeat -> not fresh.
    assert arc.worker_heartbeat_is_fresh(stale, now, _cfg(enabled=True)) is False
    # Missing heartbeat is never fresh.
    assert arc.worker_heartbeat_is_fresh(None, now, _cfg(enabled=True)) is False


def test_worker_health_marks_unhealthy_without_heartbeat():
    # enabled=True in env, but no persisted heartbeat -> healthy must be False.
    conn = FakeConn(matchers=[('MAX(heartbeat_at)', [{'hb': None}]), ('GROUP BY status', [])])
    health = summary.worker_health(conn, workspace_id='ws1', config=_cfg(enabled=True), latest_assessment_at=None)
    assert health['enabled'] is True
    assert health['healthy'] is False
    assert health['last_heartbeat_at'] is None


# --------------------------------------------------------------------------
# Trigger endpoint execution policy
# --------------------------------------------------------------------------
def _patch_endpoint(monkeypatch, conn, config):
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)

    @contextmanager
    def _pg():
        yield conn

    monkeypatch.setattr(pilot, 'pg_connection', lambda: _pg())
    monkeypatch.setattr(pilot, 'require_ops_rbac_guard', lambda *_a, **_k: ({'id': 'u1'}, {'workspace_id': 'ws1', 'workspace': {'id': 'ws1'}}))
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)
    monkeypatch.setattr(arc, 'assessor_config', lambda: config)


def test_disabled_worker_and_no_on_demand_returns_503_without_queuing(monkeypatch):
    conn = FakeConn(matchers=[
        ('SELECT * FROM assets WHERE id =', [{'id': 'a1', 'workspace_id': 'ws1', 'name': 'Wallet'}]),
        ('MAX(heartbeat_at)', [{'hb': None}]),
    ])
    _patch_endpoint(monkeypatch, conn, _cfg(enabled=False, on_demand_enabled=False))

    raised = None
    try:
        registry.trigger_assessment_endpoint('a1', _Req())
    except Exception as exc:  # noqa: BLE001
        raised = exc

    assert raised is not None, 'expected a 503 to be raised'
    assert getattr(raised, 'status_code', None) == 503
    detail = getattr(raised, 'detail', {})
    assert isinstance(detail, dict) and detail.get('code') == 'assessment_worker_unavailable'
    # Critically: NO queued job was created (no indefinite pending job).
    assert not conn.writes_matching('INSERT INTO asset_risk_jobs')


def test_on_demand_runs_inline_while_background_worker_disabled(monkeypatch):
    conn = FakeConn(matchers=[
        ('SELECT * FROM assets WHERE id =', [{'id': 'a1', 'workspace_id': 'ws1', 'name': 'Wallet'}]),
        ('MAX(heartbeat_at)', [{'hb': None}]),
        ('SELECT id, status FROM asset_risk_jobs', []),  # no active job -> enqueue inserts
        ("UPDATE asset_risk_jobs SET status = 'running'", [{'id': 'job-1'}]),  # inline claim succeeds
    ])
    _patch_endpoint(monkeypatch, conn, _cfg(enabled=False, on_demand_enabled=True))
    monkeypatch.setattr(
        registry.service, 'assess_asset',
        lambda *_a, **_k: {'assessment_id': 'as1', 'status': 'partial', 'risk_score': 40, 'risk_level': 'medium', 'findings_count': 2},
    )

    out = registry.trigger_assessment_endpoint('a1', _Req())
    assert out['status'] == 'partial'
    assert out['execution_mode'] == 'on_demand'
    # A job was created and completed inline (no permanent queued job).
    assert conn.writes_matching('INSERT INTO asset_risk_jobs')
    assert conn.writes_matching("UPDATE asset_risk_jobs SET status = 'completed'")


def test_active_job_is_deduplicated_not_duplicated(monkeypatch):
    conn = FakeConn(matchers=[
        ('SELECT * FROM assets WHERE id =', [{'id': 'a1', 'workspace_id': 'ws1', 'name': 'Wallet'}]),
        ('MAX(heartbeat_at)', [{'hb': None}]),
        ('SELECT id, status FROM asset_risk_jobs', [{'id': 'job-existing', 'status': 'running'}]),
    ])
    _patch_endpoint(monkeypatch, conn, _cfg(enabled=False, on_demand_enabled=True))

    out = registry.trigger_assessment_endpoint('a1', _Req())
    assert out['deduplicated'] is True
    assert out['status'] == 'running'
    assert out['job_id'] == 'job-existing'
    # No second job inserted.
    assert not conn.writes_matching('INSERT INTO asset_risk_jobs')


# --------------------------------------------------------------------------
# Stuck-job reconciliation
# --------------------------------------------------------------------------
def test_reconcile_blocks_stale_jobs_when_no_healthy_worker():
    conn = FakeConn(matchers=[
        ("SET status = 'blocked'", [{'id': 'job-1', 'workspace_id': 'ws1', 'asset_id': 'a1', 'status': 'blocked'}]),
    ])
    out = worker.reconcile_stuck_jobs(conn, config=_cfg(queued_job_timeout_seconds=900), worker_healthy=False)
    assert out['blocked'] == 1
    writes = conn.writes_matching("SET status = 'blocked'")
    assert writes and 'assessment_worker_unavailable' in writes[0][0]


def test_reconcile_is_noop_when_worker_healthy():
    conn = FakeConn()
    out = worker.reconcile_stuck_jobs(conn, config=_cfg(), worker_healthy=True)
    assert out['blocked'] == 0
    assert not conn.writes  # a healthy worker reclaims expired jobs itself
