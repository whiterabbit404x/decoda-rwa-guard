"""Watcher-source target health must be workspace-scoped, and a fallback-leg
degradation must not masquerade as a primary Stream-health failure.

PRODUCTION EVIDENCE
-------------------
Two read-only production queries taken together proved a cross-tenant leak.

Workspace A (the reporting tenant) had NO degraded and NO dead-lettered target::

    enabled=true monitoring_enabled=true is_active=true
    watcher_source_status=active   watcher_degraded_reason=NULL
    monitoring_dead_lettered_at=NULL  monitoring_delivery_attempts=0
    degraded_target_count = 0

…while a DIFFERENT workspace had exactly one::

    workspace_id=<other tenant>  degraded_target_count = 1

Workspace A's Stream was simultaneously healthy and delivering::

    health_status=healthy lag_status=live
    reporting_systems=1 fresh_live_reporting_systems=1
    chosen_evidence_source=live realtime_live_evidence_fresh=True

…and workspace A's runtime status still reported
``degraded_reason='target_source_degraded'``.

THE DEFECT
----------
``get_monitoring_health()`` folded the degraded-target count into the same
``FROM targets`` aggregate that produced the chain-progress MAXes::

    COALESCE(SUM(CASE WHEN watcher_source_status = 'degraded' THEN 1 ELSE 0 END), 0)
        AS degraded_targets
    FROM targets
    WHERE deleted_at IS NULL AND monitoring_enabled AND enabled AND is_active
    -- no workspace_id predicate

One global count, consumed as if it were a per-workspace fact: any workspace's
degraded target raised ``degraded_targets > 0`` for EVERY workspace, so every
tenant inherited ``target_source_degraded``.

A second, narrower asymmetry rode on top. ``provider_degraded_or_unreachable``
already refuses to call the provider unreachable for a fallback-leg fault while
the Stream is demonstrably delivering — but the status REASON had no equivalent
distinction, so the same fallback fact still surfaced as the workspace's primary
status reason.

THE FIX
-------
  * ``target_source_health_snapshot()`` is the single producer of these counts and
    is workspace-scoped. With no resolved workspace it FAILS CLOSED — it runs no
    query at all rather than answering from another tenant's rows.
  * The runtime status reads it after the authoritative workspace context is
    resolved; the operator endpoint opts into the global view explicitly.
  * With a healthy Stream carrying fresh live evidence, a workspace's OWN
    ``target_source_degraded`` is renamed to a fallback-scoped reason. It is not
    hidden: the runtime status stays degraded, ``fallback_rpc`` reports it under
    its own name, and a named downgrade token is emitted.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from services.api.app import monitoring_runner
from services.api.app.monitoring_truth import REALTIME_INGESTION_HEALTHY
from services.api.tests.test_monitoring_status_evidence_integrity_separation import (
    _AlertEvidenceConn,
)
from services.api.tests.test_quicknode_stream_runtime_health_semantics import (  # noqa: F401
    ASSET_ID,
    CHAIN_HEAD,
    DEFAULT_HEALTH,
    NOW,
    STREAM_STALE_SECONDS,
    SYSTEM_ID,
    TARGET_ID,
    _Result,
    _clear_runtime_status_caches,
)

# Two tenants. Neither id is ever written into application code — the runtime must
# derive the scope from the resolved request context, not from a known constant.
WORKSPACE_A = '00000000-0000-0000-0000-0000000000aa'
WORKSPACE_B = '00000000-0000-0000-0000-0000000000bb'


# ---------------------------------------------------------------------------
# A connection that models `targets` as a real MULTI-TENANT table.
#
# The point of the fake: it answers the watcher-source aggregation from EVERY
# workspace's rows whenever the query arrives without a workspace predicate. An
# unscoped query is therefore not merely "not asserted against" here — it makes
# workspace A read workspace B's degraded target and fails the test.
# ---------------------------------------------------------------------------

class _TenantTargetsConn(_AlertEvidenceConn):
    def __init__(
        self,
        *,
        targets_by_workspace: dict[str, list[str]],
        dead_lettered_by_workspace: dict[str, int] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.targets_by_workspace = {k: list(v) for k, v in targets_by_workspace.items()}
        self.dead_lettered_by_workspace = dict(dead_lettered_by_workspace or {})
        # Every watcher-source aggregation this connection served, as
        # (workspace_id_or_None, sql). None means the query carried no scope.
        self.target_source_queries: list[tuple[str | None, str]] = []

    def _watcher_statuses(self, scope: str | None) -> list[str]:
        if scope is None:
            # No workspace predicate: a real database would aggregate every tenant.
            return [s for rows in self.targets_by_workspace.values() for s in rows]
        return list(self.targets_by_workspace.get(scope, []))

    def execute(self, q, p=None):
        text = ' '.join(str(q).split())

        if "watcher_source_status = 'degraded'" in text and 'FROM targets' in text:
            scope = str(p[0]) if p else None
            self.target_source_queries.append((scope, text))
            statuses = self._watcher_statuses(scope)
            return _Result(row={
                'degraded_targets': sum(1 for s in statuses if s == 'degraded'),
                'active_targets': sum(1 for s in statuses if s == 'active'),
            })

        if 'monitoring_dead_lettered_at IS NOT NULL' in text:
            scope = str(p[0]) if p else None
            if scope is None:
                total = sum(self.dead_lettered_by_workspace.values())
            else:
                total = int(self.dead_lettered_by_workspace.get(scope, 0))
            return _Result(row={'c': total})

        return super().execute(q, p)


class _State:
    pass


class _Req:
    def __init__(self, workspace_id: str):
        self.state = _State()
        self.headers = {'x-workspace-id': workspace_id, 'x-workspace-slug': f'ws-{workspace_id[-2:]}'}
        self.query_params = {}


def _runtime_payload(monkeypatch, conn, *, workspace_id: str, health=None, streams_enabled=True):
    """Run monitoring_runtime_status as `workspace_id` and return its payload.

    The payload carries the workspace monitoring payload merged in, so both the
    payload facts (``fallback_rpc``, ``realtime_ingestion``) and the top-level ones
    (``degraded_reason``, ``dead_lettered_targets``) are asserted from one dict.
    """
    monkeypatch.setenv('REALTIME_STREAMS_ENABLED', 'true' if streams_enabled else 'false')
    monkeypatch.setattr(
        monitoring_runner, 'resolve_workspace_context_for_request',
        lambda *a, **k: (
            {'id': 'u'},
            {'workspace_id': workspace_id, 'workspace': {'slug': f'ws-{workspace_id[-2:]}'}},
            True,
        ),
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'ensure_monitoring_runtime_schema_capabilities', lambda *a, **k: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: conn)
    resolved_health = dict(health or DEFAULT_HEALTH)
    monkeypatch.setattr(monitoring_runner, 'get_monitoring_health', lambda *_a, **_k: resolved_health)
    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monitoring_runner.RUNTIME_STATUS_WORKSPACE_CACHE.clear()
    monitoring_runner.RUNTIME_STATUS_SUMMARY_CACHE.clear()
    return monitoring_runner.monitoring_runtime_status(_Req(workspace_id))


def _production_tenants(**overrides):
    """Exactly the production shape: workspace A clean, workspace B degraded.

    Healthy near-tip Stream, fresh live coverage, and the 900s fallback RPC leg
    outside its window — the state in which workspace A wrongly reported
    target_source_degraded.
    """
    params = dict(
        targets_by_workspace={WORKSPACE_A: ['active'], WORKSPACE_B: ['degraded']},
        stream_checkpoint_age_seconds=2,
        stream_lag_blocks=0,
        stream_telemetry_age_seconds=30,
        rpc_poll_age_seconds=3600,
        coverage_age_seconds=30,
        detection_age_seconds=120,
    )
    params.update(overrides)
    return _TenantTargetsConn(**params)


# ---------------------------------------------------------------------------
# 1. THE REGRESSION: workspace B's degraded target has zero effect on workspace A
# ---------------------------------------------------------------------------

def test_other_tenants_degraded_target_does_not_degrade_this_workspace(monkeypatch):
    conn = _production_tenants()
    payload = _runtime_payload(monkeypatch, conn, workspace_id=WORKSPACE_A)

    # The count workspace A is judged on is ITS OWN: 0, not the global 1.
    assert payload['fallback_rpc']['degraded_targets'] == 0
    assert payload['fallback_rpc']['target_source_degraded'] is False
    assert payload['fallback_rpc']['target_health_scope'] == 'workspace'
    assert payload.get('degraded_reason') != 'target_source_degraded'
    assert payload.get('status_reason') != 'target_source_degraded'


def test_target_source_aggregation_is_never_issued_without_a_workspace_predicate(monkeypatch):
    """The mechanism itself: every watcher-source read carries this workspace's id."""
    conn = _production_tenants()
    _runtime_payload(monkeypatch, conn, workspace_id=WORKSPACE_A)

    assert conn.target_source_queries, 'the runtime status never read target health'
    for scope, sql in conn.target_source_queries:
        assert scope == WORKSPACE_A, f'unscoped/foreign watcher-source read: {scope}'
        assert 'workspace_id = %s' in sql


# ---------------------------------------------------------------------------
# 2. Workspace B still sees its OWN degraded target
# ---------------------------------------------------------------------------

def test_owning_workspace_still_sees_its_own_degraded_target(monkeypatch):
    conn = _production_tenants()
    payload = _runtime_payload(monkeypatch, conn, workspace_id=WORKSPACE_B)

    assert payload['fallback_rpc']['degraded_targets'] == 1
    assert payload['fallback_rpc']['target_source_degraded'] is True
    assert payload['fallback_rpc']['target_health_scope'] == 'workspace'


def test_scoping_is_symmetric_between_the_two_tenants(monkeypatch):
    """Isolation is not a one-way filter: each tenant reads only its own rows."""
    a = _runtime_payload(monkeypatch, _production_tenants(), workspace_id=WORKSPACE_A)
    b = _runtime_payload(monkeypatch, _production_tenants(), workspace_id=WORKSPACE_B)
    assert a['fallback_rpc']['degraded_targets'] == 0
    assert b['fallback_rpc']['degraded_targets'] == 1


# ---------------------------------------------------------------------------
# 3. Workspace A's healthy Stream stays live/healthy
# ---------------------------------------------------------------------------

def test_healthy_stream_and_live_coverage_are_preserved(monkeypatch):
    payload = _runtime_payload(monkeypatch, _production_tenants(), workspace_id=WORKSPACE_A)

    realtime = payload['realtime_ingestion']
    assert realtime['healthy'] is True
    assert realtime['status'] == REALTIME_INGESTION_HEALTHY
    assert realtime['lane_state'] == 'live'
    assert realtime['live_coverage_fresh'] is True
    assert payload['evidence_source'] == 'live'
    assert payload['source_of_evidence'] == 'live'
    assert payload['reporting_systems'] == 1
    assert payload['fresh_live_reporting_systems'] == 1


# ---------------------------------------------------------------------------
# 4. Fallback warnings stay separately visible
# ---------------------------------------------------------------------------

def test_fallback_leg_warning_is_still_reported_under_its_own_name(monkeypatch):
    """The 900s fallback leg is outside its window in this shape: it must still say so."""
    payload = _runtime_payload(
        monkeypatch,
        _production_tenants(),
        workspace_id=WORKSPACE_A,
        health={
            **DEFAULT_HEALTH,
            'source_type': 'unavailable',
            'degraded_reason': 'all_rpc_providers_unavailable',
            'last_error': 'rpc timeout',
        },
    )
    assert payload['fallback_rpc']['degraded_or_unreachable'] is True
    # …without claiming the Stream stopped.
    assert payload['realtime_ingestion']['healthy'] is True
    assert payload['provider_degraded_flag'] is False


def test_fallback_rpc_reports_target_source_facts_for_the_owning_workspace(monkeypatch):
    """Demoting the reason must never make the warning invisible."""
    payload = _runtime_payload(monkeypatch, _production_tenants(), workspace_id=WORKSPACE_B)
    assert set(payload['fallback_rpc']) >= {
        'degraded_or_unreachable', 'reachable', 'poll_interval_seconds',
        'target_source_degraded', 'degraded_targets', 'target_health_scope',
    }


# ---------------------------------------------------------------------------
# 5. Healthy Stream + SAME-workspace degradation → fallback semantics, not primary
# ---------------------------------------------------------------------------

def test_same_workspace_degradation_with_healthy_stream_is_named_as_fallback(monkeypatch):
    conn = _production_tenants(
        targets_by_workspace={WORKSPACE_A: ['degraded'], WORKSPACE_B: ['active']},
    )
    payload = _runtime_payload(monkeypatch, conn, workspace_id=WORKSPACE_A)

    # Not presented as a primary Stream-health failure…
    assert payload.get('status_reason') != 'target_source_degraded'
    assert payload.get('degraded_reason') != 'target_source_degraded'
    # …but not hidden either: named as the fallback fact it is.
    assert payload['fallback_rpc']['target_source_degraded'] is True
    assert payload['fallback_rpc']['degraded_targets'] == 1
    assert payload['fallback_rpc']['degraded_or_unreachable'] is True
    assert (
        payload.get('degraded_reason')
        == monitoring_runner.FALLBACK_TARGET_SOURCE_DEGRADED_REASON
    )


def test_same_workspace_degradation_does_not_force_status_fields_green(monkeypatch):
    """The demotion re-attributes the reason; it never paints the workspace healthy."""
    conn = _production_tenants(
        targets_by_workspace={WORKSPACE_A: ['degraded'], WORKSPACE_B: ['active']},
    )
    payload = _runtime_payload(monkeypatch, conn, workspace_id=WORKSPACE_A)
    assert payload['runtime_status'] != 'healthy'
    assert payload['fallback_rpc']['degraded_or_unreachable'] is True


# ---------------------------------------------------------------------------
# 6. Unhealthy Stream + SAME-workspace degradation → unchanged fail-closed behavior
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    'stream_kwargs',
    [
        # Stream stopped: checkpoint far outside the stale window.
        {'stream_checkpoint_age_seconds': STREAM_STALE_SECONDS * 4,
         'stream_telemetry_age_seconds': STREAM_STALE_SECONDS * 4},
        # No realtime evidence at all.
        {'stream_checkpoint_age_seconds': None, 'stream_telemetry_age_seconds': None},
    ],
)
def test_unhealthy_stream_keeps_target_source_degraded_as_the_primary_reason(monkeypatch, stream_kwargs):
    conn = _production_tenants(
        targets_by_workspace={WORKSPACE_A: ['degraded'], WORKSPACE_B: ['active']},
        coverage_age_seconds=None,
        **stream_kwargs,
    )
    payload = _runtime_payload(monkeypatch, conn, workspace_id=WORKSPACE_A)

    assert payload['realtime_ingestion']['healthy'] is False
    assert payload['status_reason'] == 'target_source_degraded'
    assert payload['fallback_rpc']['target_source_degraded'] is True


def test_streams_disabled_keeps_target_source_degraded_as_the_primary_reason(monkeypatch):
    conn = _production_tenants(
        targets_by_workspace={WORKSPACE_A: ['degraded'], WORKSPACE_B: ['active']},
        coverage_age_seconds=None,
    )
    payload = _runtime_payload(
        monkeypatch, conn, workspace_id=WORKSPACE_A, streams_enabled=False,
    )
    assert payload['realtime_ingestion']['healthy'] is False
    assert payload['status_reason'] == 'target_source_degraded'


# ---------------------------------------------------------------------------
# 7. A missing workspace_id must never trigger an all-tenant query
# ---------------------------------------------------------------------------

class _RecordingConn:
    def __init__(self, rows: dict[str, list[str]]):
        self.rows = dict(rows)
        self.queries: list[tuple[str, tuple]] = []

    def execute(self, q, p=None):
        text = ' '.join(str(q).split())
        self.queries.append((text, tuple(p or ())))
        scope = str(p[0]) if p else None
        statuses = (
            [s for v in self.rows.values() for s in v] if scope is None
            else list(self.rows.get(scope, []))
        )
        return _Result(row={
            'degraded_targets': sum(1 for s in statuses if s == 'degraded'),
            'active_targets': sum(1 for s in statuses if s == 'active'),
        })


@pytest.mark.parametrize('missing', [None, '', '   '])
def test_snapshot_fails_closed_and_runs_no_query_without_a_workspace(missing):
    conn = _RecordingConn({WORKSPACE_B: ['degraded']})
    snapshot = monitoring_runner.target_source_health_snapshot(conn, workspace_id=missing)

    assert conn.queries == [], 'an all-tenant query was issued with no workspace scope'
    assert snapshot['counts_available'] is False
    assert snapshot['scope'] == 'unresolved_workspace'
    # Unknown is reported as unknown, never as a reassuring zero…
    assert snapshot['degraded_targets'] is None
    assert snapshot['active_targets'] is None
    # …and it never answers with another tenant's degradation.
    assert snapshot['degraded_reason'] is None


def test_snapshot_scopes_every_query_it_does_run():
    conn = _RecordingConn({WORKSPACE_A: ['active'], WORKSPACE_B: ['degraded']})
    snapshot = monitoring_runner.target_source_health_snapshot(conn, workspace_id=WORKSPACE_A)

    assert len(conn.queries) == 1
    sql, params = conn.queries[0]
    assert 'workspace_id = %s' in sql
    assert params == (WORKSPACE_A,)
    assert snapshot['degraded_targets'] == 0
    assert snapshot['active_targets'] == 1
    assert snapshot['degraded_reason'] is None


def test_snapshot_reports_the_owning_workspaces_degradation():
    conn = _RecordingConn({WORKSPACE_A: ['active'], WORKSPACE_B: ['degraded']})
    snapshot = monitoring_runner.target_source_health_snapshot(conn, workspace_id=WORKSPACE_B)
    assert snapshot['degraded_targets'] == 1
    assert snapshot['degraded_reason'] == monitoring_runner.TARGET_SOURCE_DEGRADED_REASON


def test_operator_global_scope_is_explicit_and_never_the_fallback():
    """The one global aggregation left is opt-in and names itself as operator scope."""
    conn = _RecordingConn({WORKSPACE_A: ['active'], WORKSPACE_B: ['degraded']})
    snapshot = monitoring_runner.target_source_health_snapshot(
        conn, workspace_id=None, operator_global_scope=True,
    )
    assert snapshot['scope'] == 'operator_global'
    assert snapshot['degraded_targets'] == 1
    sql, params = conn.queries[0]
    assert 'workspace_id = %s' not in sql
    assert params == ()


def test_get_monitoring_health_defaults_to_fail_closed_target_health(monkeypatch):
    """A tenant-path caller that passes no workspace gets no target-source verdict."""
    conn = _MonitoringHealthConn()
    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _FakeCtx(conn))
    monkeypatch.setattr(
        monitoring_runner, 'monitoring_ingestion_runtime',
        lambda: {'source': 'polling', 'degraded': False, 'reason': None, 'mode': 'live'},
    )
    monkeypatch.setattr(monitoring_runner, 'get_background_loop_health', lambda: {'loop_running': True})
    monkeypatch.setattr(monitoring_runner, 'monitoring_slo_snapshot', lambda _c: {})

    health = monitoring_runner.get_monitoring_health()

    assert health['target_health_scope'] == 'unresolved_workspace'
    assert health['degraded_targets'] is None
    assert health.get('degraded_reason') is None
    assert not any(
        "watcher_source_status = 'degraded'" in sql for sql in conn.queries
    ), 'get_monitoring_health issued an all-tenant target-health query'


def test_get_monitoring_health_operator_scope_still_reports_global_degradation(monkeypatch):
    """The operator endpoint keeps its deployment-wide view — explicitly."""
    conn = _MonitoringHealthConn(degraded_targets=1)
    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _FakeCtx(conn))
    monkeypatch.setattr(
        monitoring_runner, 'monitoring_ingestion_runtime',
        lambda: {'source': 'polling', 'degraded': False, 'reason': None, 'mode': 'live'},
    )
    monkeypatch.setattr(monitoring_runner, 'get_background_loop_health', lambda: {'loop_running': True})
    monkeypatch.setattr(monitoring_runner, 'monitoring_slo_snapshot', lambda _c: {})

    health = monitoring_runner.get_monitoring_health(operator_global_scope=True)

    assert health['target_health_scope'] == 'operator_global'
    assert health['degraded_targets'] == 1
    assert health['degraded_reason'] == monitoring_runner.TARGET_SOURCE_DEGRADED_REASON


class _FakeCtx:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self._conn

    def __exit__(self, *_):
        return False


class _MonitoringHealthConn:
    """The handful of queries get_monitoring_health runs, with a live-mode worker."""

    def __init__(self, *, degraded_targets: int = 0):
        self.degraded_targets = int(degraded_targets)
        self.queries: list[str] = []

    def execute(self, query, params=None):
        q = ' '.join(str(query).split())
        self.queries.append(q)
        if 'FROM monitoring_worker_state' in q:
            return _Result(row={
                'worker_name': 'monitoring-worker', 'running': True, 'status': 'running',
                'last_started_at': NOW.isoformat(), 'last_heartbeat_at': NOW.isoformat(),
                'last_cycle_at': NOW.isoformat(), 'last_cycle_due_targets': 0,
                'last_cycle_targets_checked': 1, 'last_cycle_alerts_generated': 0,
                'last_error': None, 'updated_at': NOW.isoformat(),
            })
        if 'overdue_count' in q:
            return _Result(row={'overdue_count': 0})
        if 'FROM background_jobs' in q:
            return _Result(row={'queued': 0, 'running': 0, 'failed': 0})
        if 'FROM monitoring_watcher_state' in q:
            return _Result(row=None)
        if "watcher_source_status = 'degraded'" in q:
            return _Result(row={'degraded_targets': self.degraded_targets, 'active_targets': 1})
        if 'watcher_last_observed_block' in q:
            return _Result(row={
                'latest_processed_block': CHAIN_HEAD,
                'max_checkpoint_lag_blocks': 0,
                'latest_checkpoint_at': NOW.isoformat(),
            })
        if 'FROM monitoring_event_receipts' in q:
            return _Result(row={'event_count': 3})
        return _Result(row={})


# ---------------------------------------------------------------------------
# 8. A dead-lettered target in the SAME workspace still degrades
# ---------------------------------------------------------------------------

def test_same_workspace_dead_lettered_target_still_degrades(monkeypatch):
    conn = _production_tenants(
        targets_by_workspace={WORKSPACE_A: ['active'], WORKSPACE_B: ['degraded']},
        dead_lettered_by_workspace={WORKSPACE_A: 1},
    )
    payload = _runtime_payload(monkeypatch, conn, workspace_id=WORKSPACE_A)
    assert payload['dead_lettered_targets'] == 1
    assert payload['runtime_status'] != 'healthy'


def test_another_workspaces_dead_lettered_target_does_not_degrade_this_one(monkeypatch):
    conn = _production_tenants(
        targets_by_workspace={WORKSPACE_A: ['active'], WORKSPACE_B: ['degraded']},
        dead_lettered_by_workspace={WORKSPACE_B: 1},
    )
    payload = _runtime_payload(monkeypatch, conn, workspace_id=WORKSPACE_A)
    assert payload['dead_lettered_targets'] == 0


# ---------------------------------------------------------------------------
# 9. Untouched neighbours: alert evidence, coverage, RPC cadence, polling
# ---------------------------------------------------------------------------

def test_open_alert_evidence_union_anti_join_is_unchanged(monkeypatch):
    """An open alert proven by EITHER chain still counts as proven."""
    conn = _production_tenants(open_alerts=1, canonical_evidence_linked_alerts=1)
    payload = _runtime_payload(monkeypatch, conn, workspace_id=WORKSPACE_A)
    assert payload['raw_open_alerts'] == 1
    assert payload['open_alerts_without_detection_evidence'] == 0
    assert 'alert_without_detection' not in (payload.get('contradiction_flags') or [])


def test_open_alert_without_either_chain_still_warns(monkeypatch):
    conn = _production_tenants(open_alerts=1)
    payload = _runtime_payload(monkeypatch, conn, workspace_id=WORKSPACE_A)
    assert payload['open_alerts_without_detection_evidence'] == 1
    assert 'alert_without_detection' in (payload.get('contradiction_flags') or [])


def test_canonical_polling_interval_is_still_900_seconds(monkeypatch):
    monkeypatch.delenv('MONITORING_POLL_INTERVAL_SECONDS', raising=False)
    monkeypatch.delenv('MONITOR_POLL_INTERVAL_SECONDS', raising=False)
    assert monitoring_runner.canonical_polling_interval_seconds() == 900


def test_runtime_status_still_reports_the_900s_fallback_cadence(monkeypatch):
    payload = _runtime_payload(monkeypatch, _production_tenants(), workspace_id=WORKSPACE_A)
    assert payload['fallback_rpc']['poll_interval_seconds'] == 900


def test_quicknode_stream_stale_window_and_chain_head_sampling_are_unchanged():
    """Nothing in this fix touches how often the chain head is sampled."""
    assert monitoring_runner.QUICKNODE_STREAM_STALE_SECONDS == 300


def test_a_healthy_stream_still_needs_no_extra_rpc_reads(monkeypatch):
    """The workspace-scoped read is a DB query; it must not add provider RPC calls."""
    calls: list[str] = []

    class _CountingRpc:
        def __init__(self, *_a, **_k):
            pass

        def call(self, method, _params):
            calls.append(method)
            return '0x1'

    monkeypatch.setattr(monitoring_runner, 'JsonRpcClient', _CountingRpc)
    _runtime_payload(monkeypatch, _production_tenants(), workspace_id=WORKSPACE_A)
    assert calls == [] or set(calls) <= {'eth_chainId'}
