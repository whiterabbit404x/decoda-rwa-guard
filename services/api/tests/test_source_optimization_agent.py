"""Tests for the Source Optimization Agent (Screen 4) backend:

* enrichment builder — health scores + five summary cards from measured facts only,
* persisted Auto-Routing settings load + threshold validation + client-safe view,
* evidence-backed agent-decision recording,
* secret redaction (no credentials ever reach the source payload).

These use a lightweight fake connection so they run without a live database.
"""
from __future__ import annotations

import contextlib
from datetime import datetime, timedelta, timezone

import pytest

from services.api.app import monitoring_health_engine as he
from services.api.app import pilot

NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _FakeConn:
    """Routes SELECTs by table name; records INSERTs for assertions."""

    def __init__(self, *, provider_health=None, coverage=None, blocks=None, settings_row=None,
                 activity_row=None, route_changes_row=None, decisions=None):
        self.provider_health = provider_health or []
        self.coverage = coverage or []
        self.blocks = blocks or []
        self.settings_row = settings_row
        self.activity_row = activity_row
        self.route_changes_row = route_changes_row
        self.decisions = decisions or []
        self.inserts: list[tuple[str, tuple]] = []

    def execute(self, query, params=None):
        q = ' '.join(str(query).split())
        upper = q.upper()
        if upper.startswith('INSERT'):
            self.inserts.append((q, params or ()))
            return _Result([])
        if 'FROM MONITOR_CHECKPOINT' in upper:
            return _Result(self.blocks)
        if 'FROM PROVIDER_HEALTH_RECORDS' in upper:
            return _Result(self.provider_health)
        if 'FROM TARGET_COVERAGE_RECORDS' in upper:
            return _Result(self.coverage)
        if 'FROM WORKSPACE_SOURCE_SETTINGS' in upper:
            return _Result([self.settings_row] if self.settings_row else [])
        if 'AUTONOMOUS_ACTIONS_24H' in upper:
            return _Result([self.activity_row] if self.activity_row else [])
        if 'INTERVAL' in upper and 'SOURCE_AGENT_DECISIONS' in upper:
            return _Result([self.route_changes_row] if self.route_changes_row else [])
        if 'FROM SOURCE_AGENT_DECISIONS' in upper:
            return _Result(self.decisions)
        return _Result([])

    def commit(self):
        pass


def _target(**over):
    base = {
        'id': 'target-1', 'name': 'Base Treasury RPC', 'asset_id': 'asset-1', 'asset_name': 'US Treasury',
        'chain_network': 'base', 'chain_id': 8453, 'contract_identifier': '0xabc0000000000000000000000000000000000001',
        'target_type': 'contract', 'monitoring_mode': 'recommended', 'enabled': True, 'monitoring_enabled': True,
        'target_metadata': {'rpc_sources': {'primary_host': 'base-mainnet.infura.io', 'fallback_host': 'base.alchemy.com'}},
    }
    base.update(over)
    return base


def _system(**over):
    base = {
        'id': 'system-1', 'target_id': 'target-1', 'asset_name': 'US Treasury', 'target_name': 'Base Treasury RPC',
        'is_enabled': True, 'runtime_status': 'healthy', 'last_heartbeat': (NOW - timedelta(seconds=30)).isoformat(),
        'last_event_at': (NOW - timedelta(seconds=45)).isoformat(), 'freshness_status': 'fresh', 'coverage_reason': 'covered',
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# Enrichment: summary cards + engine health scoring.
# ---------------------------------------------------------------------------
def test_enrichment_builds_five_summary_cards_from_measured_facts():
    conn = _FakeConn()
    out = pilot._build_monitoring_sources_enrichment(
        conn, workspace_id='ws-1', assets=[{'id': 'asset-1'}], targets=[_target()], systems=[_system()], now=NOW,
    )
    summary = out['summary']
    assert set(summary) == {
        'source_health', 'active_routes', 'telemetry_coverage', 'oracle_heartbeats', 'agent_activity',
    }
    # Card 2 — routing counts come from target metadata (primary + fallback host present).
    assert summary['active_routes']['primary'] == 1
    assert summary['active_routes']['fallback'] == 1
    # Card 3 — a fresh, enabled system counts as covered telemetry.
    assert summary['telemetry_coverage']['fresh'] == 1
    assert summary['telemetry_coverage']['coverage_pct'] == 100.0


def test_enrichment_health_score_present_only_with_live_evidence():
    conn = _FakeConn()
    out = pilot._build_monitoring_sources_enrichment(
        conn, workspace_id='ws-1', assets=[{'id': 'asset-1'}], targets=[_target()], systems=[_system()], now=NOW,
    )
    source = out['sources'][0]
    # Heartbeat + fresh telemetry -> engine produces a healthy score.
    assert source['has_live_evidence'] is True
    assert source['health_status'] == he.HEALTH_HEALTHY
    assert source['health_score'] is not None and source['health_score'] >= 80


def test_enrichment_no_evidence_is_never_healthy():
    # Target with no monitored system => no heartbeat, no telemetry.
    conn = _FakeConn()
    out = pilot._build_monitoring_sources_enrichment(
        conn, workspace_id='ws-1', assets=[{'id': 'asset-1'}], targets=[_target()], systems=[], now=NOW,
    )
    source = out['sources'][0]
    assert source['health_status'] != he.HEALTH_HEALTHY
    assert source['has_live_evidence'] is False
    # No fabricated percentage when nothing measurable was scored.
    assert out['summary']['source_health']['health_pct'] is None


def test_enrichment_never_fabricates_trend():
    conn = _FakeConn()
    out = pilot._build_monitoring_sources_enrichment(
        conn, workspace_id='ws-1', assets=[{'id': 'asset-1'}], targets=[_target()], systems=[_system()], now=NOW,
    )
    assert out['summary']['source_health']['trend_24h'] is None


# ---------------------------------------------------------------------------
# Secret redaction: credentials never reach the payload.
# ---------------------------------------------------------------------------
def test_source_payload_contains_no_credentials():
    # Target metadata carries a full RPC URL with an embedded API key; only the host
    # should ever surface in routing, never the key.
    tgt = _target(target_metadata={'rpc_sources': {
        'primary_host': 'base-mainnet.infura.io',
        'fallback_host': 'base.alchemy.com',
        'primary_url': 'https://base-mainnet.infura.io/v3/SUPERSECRETKEY123',
        'api_key': 'SUPERSECRETKEY123',
    }})
    conn = _FakeConn()
    out = pilot._build_monitoring_sources_enrichment(
        conn, workspace_id='ws-1', assets=[{'id': 'asset-1'}], targets=[tgt], systems=[_system()], now=NOW,
    )
    blob = repr(out['sources'][0])
    assert 'SUPERSECRETKEY123' not in blob
    assert '/v3/' not in blob
    assert out['sources'][0]['primary_provider'] == 'base-mainnet.infura.io'


# ---------------------------------------------------------------------------
# Persisted settings: defaults, load, client-safe view.
# ---------------------------------------------------------------------------
def test_load_source_settings_defaults_when_absent():
    conn = _FakeConn(settings_row=None)
    settings = pilot._load_source_settings(conn, workspace_id='ws-1')
    assert settings['auto_routing_enabled'] is False   # fail-closed default
    assert settings['persisted'] is False
    assert settings['failover_cooldown_seconds'] == 300


def test_load_source_settings_reads_persisted_row():
    conn = _FakeConn(settings_row={
        'auto_routing_enabled': True, 'failover_cooldown_seconds': 600, 'route_recovery_seconds': 1800,
        'thresholds': {'block_lag_healthy_max': 4}, 'updated_at': NOW, 'updated_by_user_id': 'user-1',
    })
    settings = pilot._load_source_settings(conn, workspace_id='ws-1')
    assert settings['auto_routing_enabled'] is True
    assert settings['persisted'] is True
    assert settings['thresholds'] == {'block_lag_healthy_max': 4}


def test_public_source_settings_omits_internal_keys():
    view = pilot._public_source_settings({
        'auto_routing_enabled': True, 'failover_cooldown_seconds': 300, 'route_recovery_seconds': 900,
        'thresholds': {}, 'persisted': True, 'updated_at': NOW.isoformat(), 'updated_by_user_id': 'secret-user',
    })
    assert 'updated_by_user_id' not in view
    assert view['auto_routing_enabled'] is True


# ---------------------------------------------------------------------------
# Threshold-override validation (autonomy boundary: no unsafe values from browser).
# ---------------------------------------------------------------------------
def test_threshold_override_accepts_in_range_values():
    cleaned = pilot._validate_threshold_overrides({'block_lag_healthy_max': 3, 'p95_latency_healthy_max_ms': 500})
    assert cleaned == {'block_lag_healthy_max': 3, 'p95_latency_healthy_max_ms': 500.0}


def test_threshold_override_rejects_unknown_key():
    with pytest.raises(pilot.HTTPException) as exc:
        pilot._validate_threshold_overrides({'delete_prod': 1})
    assert exc.value.status_code == 400


def test_threshold_override_rejects_out_of_range():
    with pytest.raises(pilot.HTTPException) as exc:
        pilot._validate_threshold_overrides({'block_lag_healthy_max': 9999})
    assert exc.value.status_code == 400


def test_threshold_override_rejects_non_numeric():
    with pytest.raises(pilot.HTTPException):
        pilot._validate_threshold_overrides({'block_lag_healthy_max': 'lots'})


# ---------------------------------------------------------------------------
# Evidence-backed decision recording.
# ---------------------------------------------------------------------------
def test_record_agent_decision_persists_evidence_snapshot():
    conn = _FakeConn()
    snapshot = {'health_status': 'critical', 'triggered_rules': ['block_lag.critical']}
    decision_id = pilot._record_source_agent_decision(
        conn, workspace_id='11111111-1111-1111-1111-111111111111', decision_type='escalation_created',
        input_snapshot=snapshot, target_id='22222222-2222-2222-2222-222222222222', approval_required=True,
        health_status='critical', triggered_rule='no_approved_fallback', actor_type='agent',
    )
    assert decision_id
    assert len(conn.inserts) == 1
    query, params = conn.inserts[0]
    assert 'INSERT INTO source_agent_decisions' in query
    # The exact metric snapshot is serialised into the row.
    assert any('block_lag.critical' in str(p) for p in params)


def test_uuid_or_none_rejects_non_uuid():
    assert pilot._uuid_or_none('not-a-uuid') is None
    assert pilot._uuid_or_none('') is None
    valid = '33333333-3333-3333-3333-333333333333'
    assert pilot._uuid_or_none(valid) == valid


# ---------------------------------------------------------------------------
# Agent-activity + route-change counters.
# ---------------------------------------------------------------------------
def test_agent_activity_counts_from_decisions():
    conn = _FakeConn(activity_row={
        'autonomous_actions_24h': 4, 'approvals_required': 2, 'last_optimization_at': NOW,
    })
    activity = pilot._count_agent_activity(conn, workspace_id='ws-1')
    assert activity['autonomous_actions_24h'] == 4
    assert activity['approvals_required'] == 2
    assert activity['last_optimization_at'] is not None


def test_route_changes_counter_defaults_zero():
    conn = _FakeConn(route_changes_row={'count': 0})
    assert pilot._count_route_changes_24h(conn, workspace_id='ws-1') == 0


# ---------------------------------------------------------------------------
# Full health-check execution path (records evidence-backed decisions).
# ---------------------------------------------------------------------------
def _wire_health_check(monkeypatch, conn, *, targets, systems, settings_row=None):
    """Stub the auth/DB seams so run_source_health_check drives the real logic."""
    # Freeze wall-clock to the fixture epoch so telemetry-freshness classification is
    # deterministic regardless of the real calendar date (the fixtures date telemetry
    # relative to NOW; run_source_health_check would otherwise use real utc_now()).
    monkeypatch.setattr(pilot, 'utc_now', lambda: NOW)
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'list_assets', lambda request: {'assets': [{'id': 'asset-1'}]})
    monkeypatch.setattr(pilot, 'list_targets', lambda request: {'targets': targets})
    monkeypatch.setattr(pilot, 'list_monitored_systems', lambda request: {'systems': systems})
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda c: None)
    monkeypatch.setattr(pilot, 'log_audit', lambda *a, **k: None)
    monkeypatch.setattr(
        pilot, 'resolve_workspace_context_for_request',
        lambda c, r: ({'id': '44444444-4444-4444-4444-444444444444'},
                      {'workspace_id': '11111111-1111-1111-1111-111111111111'}, True),
    )

    @contextlib.contextmanager
    def _fake_pg():
        yield conn

    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    conn.settings_row = settings_row


def test_run_source_health_check_records_evidence_backed_decisions(monkeypatch):
    # A monitored system in runtime_status='failed' => endpoint unavailable => critical.
    critical_system = _system(runtime_status='failed', freshness_status='stale')
    conn = _FakeConn()
    _wire_health_check(monkeypatch, conn, targets=[_target()], systems=[critical_system])

    result = pilot.run_source_health_check(object())

    assert result['sources_evaluated'] == 1
    assert result['criticals'] == 1
    # Default settings => Auto-Routing disabled => escalation, not an auto-failover.
    assert result['escalations'] == 1
    assert result['failovers_recommended'] == 0
    # A per-source escalation decision + a completion decision were recorded.
    decision_inserts = [q for q, _ in conn.inserts if 'source_agent_decisions' in q]
    assert len(decision_inserts) == 2
    joined = ' '.join(str(p) for _, p in conn.inserts)
    assert 'escalation_created' in joined
    assert 'health_check_completed' in joined


def test_run_source_health_check_recommends_failover_when_auto_routing_enabled(monkeypatch):
    critical_system = _system(runtime_status='failed', freshness_status='stale')
    conn = _FakeConn()
    _wire_health_check(
        monkeypatch, conn, targets=[_target()], systems=[critical_system],
        settings_row={
            'auto_routing_enabled': True, 'failover_cooldown_seconds': 300, 'route_recovery_seconds': 900,
            'thresholds': {}, 'updated_at': NOW, 'updated_by_user_id': 'user-1',
        },
    )

    result = pilot.run_source_health_check(object())

    # Auto-Routing on + an approved fallback host configured => failover recommended.
    assert result['criticals'] == 1
    assert result['failovers_recommended'] == 1
    assert result['escalations'] == 0
    joined = ' '.join(str(p) for _, p in conn.inserts)
    assert 'failover_recommended' in joined


def test_run_source_health_check_healthy_records_only_completion(monkeypatch):
    conn = _FakeConn()
    _wire_health_check(monkeypatch, conn, targets=[_target()], systems=[_system()])

    result = pilot.run_source_health_check(object())

    assert result['criticals'] == 0
    assert result['warnings'] == 0
    # Only the completion decision is recorded when every source is healthy.
    decision_inserts = [q for q, _ in conn.inserts if 'source_agent_decisions' in q]
    assert len(decision_inserts) == 1
    assert result['completion_decision_id']
