"""Structural / wiring tests for the Alert Triage Agent (Screen 6).

Source-level assertions that the migration, endpoints, routes, authorization,
and truthfulness invariants are present — cheap guards that survive refactors.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding='utf-8')


def test_migration_defines_cluster_and_run_tables():
    sql = _read('services/api/migrations/0134_alert_triage_agent.sql')
    for table in ('alert_clusters', 'alert_cluster_members', 'alert_triage_runs', 'alert_cluster_history'):
        assert f'CREATE TABLE IF NOT EXISTS {table}' in sql
    # Workspace-scoped fingerprint uniqueness (idempotent upsert) + dedup guards.
    assert 'uq_alert_clusters_fingerprint' in sql
    assert 'uq_alert_cluster_members_alert' in sql            # one cluster per alert
    assert 'uq_alert_cluster_members_cluster_alert' in sql    # no dup membership
    assert 'uq_alert_triage_runs_active' in sql               # one active run per workspace
    assert 'REFERENCES workspaces(id)' in sql                 # every table workspace-scoped


def test_routes_registered_under_alerts_namespace():
    main = _read('services/api/app/main.py')
    assert "@app.get('/alerts/triage/summary'" in main
    assert "@app.post('/alerts/triage/run'" in main
    assert "@app.get('/alerts/triage/clusters'" in main
    assert "@app.get('/alerts/triage/clusters/{cluster_id}'" in main
    assert 'alert_triage_endpoints' in main


def test_run_command_is_role_gated_and_workspace_scoped():
    service = _read('services/api/app/domains/alert_triage/service.py')
    # Manual triage requires the ops RBAC guard.
    assert 'require_ops_rbac_guard' in service
    # Every read/write resolves the workspace and scopes queries by workspace_id.
    assert 'resolve_workspace' in _read('services/api/app/domains/alert_triage/summary.py')
    assert 'WHERE workspace_id = %s' in service


def test_service_never_opens_incidents_or_executes_actions():
    service = _read('services/api/app/domains/alert_triage/service.py')
    lowered = service.lower()
    # Screen 6 defers incident creation to the existing escalate flow (Screen 7).
    assert 'insert into incidents' not in lowered
    assert 'update incidents' not in lowered
    # It never resolves/mutates alerts or executes response actions.
    assert 'update alerts set' not in lowered
    assert 'response_actions' not in lowered
    assert 'governance_actions' not in lowered


def test_ai_enrichment_is_fail_closed_and_never_fabricates():
    ai = _read('services/api/app/domains/alert_triage/ai_enrichment.py')
    # Only a real (non-simulated) provider earns the ai_assisted label.
    assert "cluster['recommendation_source'] = 'ai_assisted'" in ai
    assert 'simulated' in ai
    # The provider only ever sees already-grounded cluster facts (no raw actors/tx).
    assert 'def _cluster_context' in ai
    # Output is sanitized (untrusted text) and HTML-stripped.
    assert 'def sanitize_narrative' in ai
    assert '_HTML' in ai


def test_severity_and_confidence_policy_is_centralized():
    cfg = _read('services/api/app/domains/alert_triage/config.py')
    # Canonical four-level severity, never derived from a frontend colour.
    assert "CANONICAL_SEVERITIES: tuple[str, ...] = ('low', 'medium', 'high', 'critical')" in cfg
    # AI availability requires a real live provider (mock never counts as AI).
    assert 'LIVE_PROVIDERS' in cfg
