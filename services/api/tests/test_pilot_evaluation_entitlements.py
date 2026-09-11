"""Pilot is a 30-day EVALUATION, not a permanently stripped-down plan.

The product rule these tests hold in place:

    ACTIVE Pilot    evaluates the important production security workflows —
                    monitoring, threat detection, alerts, incidents, AI
                    investigation, incident playbooks, response recommendations,
                    evidence and audit exports — bounded by 30 days, 1 workspace,
                    5 monitored contracts, 10 evidence packages, and
                    recommend-only execution.

    EXPIRED Pilot   keeps every record it produced and loses the ability to START
                    new expensive work, until the organization upgrades.

Both halves matter. Labelling an evaluation feature "upgrade required" while the
window is open defeats the evaluation; leaving it open after the window closes
gives away the thing Scale is sold for. The same entitlement call has to answer
both, which is why ``effective_entitlements`` reads plan AND lifecycle.

Run:
    python -m pytest services/api/tests/test_pilot_evaluation_entitlements.py -q
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from services.api.app import ai_triage
from services.api.app import entitlements as ent
from services.api.app import forensic_investigation
from services.api.app import organizations as org_service
from services.api.app import pilot

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
WS = 'ws-1111-1111-1111-111111111111'
ORG = 'org-1111-1111-1111-111111111111'
USER = 'user-1'
INCIDENT = 'inc-1111-1111-1111-111111111111'

#: The workflows an approved evaluator is invited to exercise. Every one of them
#: must be ON during an active Pilot — that is what "evaluate Decoda" means.
EVALUATION_FEATURES: tuple[str, ...] = (
    ent.FEATURE_THREAT_MONITORING,
    ent.FEATURE_AI_INVESTIGATION,
    ent.FEATURE_INCIDENT_PLAYBOOKS,
    ent.FEATURE_RESPONSE_RECOMMENDATIONS,
    ent.FEATURE_EVIDENCE_EXPORT,
)


def _org(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        'id': ORG,
        'name': 'ABC Tokenization',
        'plan': ent.PLAN_PILOT,
        'status': ent.STATUS_ACTIVE,
        'evaluation_started_at': NOW - timedelta(days=7),
        'evaluation_expires_at': NOW + timedelta(days=23),
        'entitlement_overrides': {},
    }
    base.update(overrides)
    return base


def _active_pilot() -> dict[str, Any]:
    return _org()


def _expired_pilot() -> dict[str, Any]:
    return _org(evaluation_started_at=NOW - timedelta(days=40), evaluation_expires_at=NOW - timedelta(days=10))


# The handler-level tests below run the real code paths, which read the real
# clock, so their fixtures are anchored to wall-clock time rather than to NOW.
def _live_active_pilot() -> dict[str, Any]:
    moment = datetime.now(timezone.utc)
    return _org(
        evaluation_started_at=moment - timedelta(days=7),
        evaluation_expires_at=moment + timedelta(days=23),
    )


def _live_expired_pilot() -> dict[str, Any]:
    moment = datetime.now(timezone.utc)
    return _org(
        evaluation_started_at=moment - timedelta(days=40),
        evaluation_expires_at=moment - timedelta(days=10),
    )


# ═══ 1. ACTIVE PILOT evaluates the production workflows ══════════════════════

def test_active_pilot_can_evaluate_every_scale_security_workflow() -> None:
    entitlements = ent.effective_entitlements(_active_pilot(), now=NOW)
    for feature in EVALUATION_FEATURES:
        assert ent.has_entitlement(entitlements, feature) is True, feature


def test_active_pilot_keeps_its_usage_bounds() -> None:
    """Cost and risk are bounded by LIMITS, not by withholding workflows."""
    entitlements = ent.effective_entitlements(_active_pilot(), now=NOW)
    assert ent.limit_for(entitlements, ent.LIMIT_WORKSPACES) == 1
    assert ent.limit_for(entitlements, ent.LIMIT_MONITORED_CONTRACTS) == 5
    assert ent.limit_for(entitlements, ent.LIMIT_EVIDENCE_PACKAGES) == 10


def test_active_pilot_is_recommend_only() -> None:
    entitlements = ent.effective_entitlements(_active_pilot(), now=NOW)
    assert ent.has_entitlement(entitlements, ent.FEATURE_AUTOMATIC_EXECUTION) is False


def test_no_plan_enables_automatic_execution_merely_by_being_paid() -> None:
    for plan in ent.PLANS:
        entitlements = ent.effective_entitlements(_org(plan=plan, evaluation_expires_at=None), now=NOW)
        assert ent.has_entitlement(entitlements, ent.FEATURE_AUTOMATIC_EXECUTION) is False


@pytest.mark.parametrize('count', [0, 1, 2, 3, 4])
def test_contracts_one_through_five_are_allowed(count: int) -> None:
    ent.enforce_resource_creation(_active_pilot(), ent.LIMIT_MONITORED_CONTRACTS, count, now=NOW)


def test_contract_six_is_refused_with_a_limit_reason() -> None:
    with pytest.raises(HTTPException) as exc_info:
        ent.enforce_resource_creation(_active_pilot(), ent.LIMIT_MONITORED_CONTRACTS, 5, now=NOW)
    assert exc_info.value.detail['code'] == ent.CODE_PLAN_LIMIT_REACHED
    assert exc_info.value.detail['resource'] == 'monitored_contracts'


def test_one_workspace_allowed_and_a_second_refused() -> None:
    ent.enforce_resource_creation(_active_pilot(), ent.LIMIT_WORKSPACES, 0, now=NOW)
    with pytest.raises(HTTPException) as exc_info:
        ent.enforce_resource_creation(_active_pilot(), ent.LIMIT_WORKSPACES, 1, now=NOW)
    assert exc_info.value.detail['resource'] == 'workspaces'


@pytest.mark.parametrize('count', list(range(10)))
def test_evidence_packages_one_through_ten_are_allowed(count: int) -> None:
    ent.enforce_resource_creation(_active_pilot(), ent.LIMIT_EVIDENCE_PACKAGES, count, now=NOW)


def test_evidence_package_eleven_is_refused() -> None:
    with pytest.raises(HTTPException) as exc_info:
        ent.enforce_resource_creation(_active_pilot(), ent.LIMIT_EVIDENCE_PACKAGES, 10, now=NOW)
    assert exc_info.value.detail['resource'] == 'evidence_packages'


# ═══ 2. EXPIRATION withdraws NEW work, never data ════════════════════════════

def test_expired_pilot_loses_every_expensive_capability() -> None:
    entitlements = ent.effective_entitlements(_expired_pilot(), now=NOW)
    for feature in EVALUATION_FEATURES:
        assert ent.has_entitlement(entitlements, feature) is False, feature
    assert ent.has_entitlement(entitlements, ent.FEATURE_AUTOMATIC_EXECUTION) is False


def test_expired_pilot_is_not_re_scoped_only_stopped() -> None:
    """Limits are unchanged: expiry stops new work, it does not shrink the tenant."""
    entitlements = ent.effective_entitlements(_expired_pilot(), now=NOW)
    assert ent.limit_for(entitlements, ent.LIMIT_MONITORED_CONTRACTS) == 5
    assert ent.limit_for(entitlements, ent.LIMIT_EVIDENCE_PACKAGES) == 10


def test_expired_pilot_is_refused_with_the_evaluation_reason_not_a_limit_reason() -> None:
    """The remedy differs: "your evaluation ended" is not "delete one and retry"."""
    with pytest.raises(HTTPException) as exc_info:
        ent.enforce_resource_creation(_expired_pilot(), ent.LIMIT_MONITORED_CONTRACTS, 0, now=NOW)
    assert exc_info.value.detail['code'] == ent.CODE_EVALUATION_EXPIRED
    assert exc_info.value.detail['lifecycle_state'] == ent.LIFECYCLE_EXPIRED_PILOT


def test_expired_pilot_asking_for_a_withdrawn_feature_hears_why() -> None:
    with pytest.raises(HTTPException) as exc_info:
        ent.require_entitlement(_expired_pilot(), ent.FEATURE_AI_INVESTIGATION, now=NOW)
    assert exc_info.value.detail['code'] == ent.CODE_EVALUATION_EXPIRED


def test_a_plan_that_never_had_a_feature_hears_the_entitlement_reason() -> None:
    with pytest.raises(HTTPException) as exc_info:
        ent.require_entitlement(_active_pilot(), ent.FEATURE_CUSTOM_INTEGRATIONS, now=NOW)
    assert exc_info.value.detail['code'] == ent.CODE_PLAN_ENTITLEMENT_REQUIRED


def test_suspension_withdraws_the_same_capabilities_as_expiry() -> None:
    entitlements = ent.effective_entitlements(_org(status=ent.STATUS_SUSPENDED), now=NOW)
    for feature in EVALUATION_FEATURES:
        assert ent.has_entitlement(entitlements, feature) is False, feature


def test_a_grandfathered_pilot_with_no_deadline_is_never_expired() -> None:
    """A missing deadline is not evidence of one. Old Pilots keep working."""
    grandfathered = _org(evaluation_started_at=None, evaluation_expires_at=None)
    assert ent.evaluation_expired(grandfathered, now=NOW) is False
    assert ent.lifecycle_state(grandfathered, now=NOW) == ent.LIFECYCLE_ACTIVE_PILOT
    entitlements = ent.effective_entitlements(grandfathered, now=NOW)
    for feature in EVALUATION_FEATURES:
        assert ent.has_entitlement(entitlements, feature) is True, feature


# ═══ 3. SCALE and ENTERPRISE ═════════════════════════════════════════════════

def test_scale_supports_three_workspaces_and_twenty_five_contracts() -> None:
    scale = _org(plan=ent.PLAN_SCALE, evaluation_started_at=None, evaluation_expires_at=None)
    entitlements = ent.effective_entitlements(scale, now=NOW)
    assert ent.limit_for(entitlements, ent.LIMIT_WORKSPACES) == 3
    assert ent.limit_for(entitlements, ent.LIMIT_MONITORED_CONTRACTS) == 25
    ent.enforce_resource_creation(scale, ent.LIMIT_WORKSPACES, 2, now=NOW)
    ent.enforce_resource_creation(scale, ent.LIMIT_MONITORED_CONTRACTS, 24, now=NOW)


def test_scale_evidence_packages_are_unlimited_not_a_large_number() -> None:
    scale = _org(plan=ent.PLAN_SCALE, evaluation_started_at=None, evaluation_expires_at=None)
    entitlements = ent.effective_entitlements(scale, now=NOW)
    assert ent.limit_for(entitlements, ent.LIMIT_EVIDENCE_PACKAGES) is None
    assert entitlements['evidence_unlimited'] is True
    ent.enforce_resource_creation(scale, ent.LIMIT_EVIDENCE_PACKAGES, 10_000, now=NOW)


def test_scale_never_renders_an_evaluation_countdown() -> None:
    scale = _org(plan=ent.PLAN_SCALE, evaluation_started_at=None, evaluation_expires_at=None)
    assert ent.evaluation_payload(scale, now=NOW) is None
    assert ent.days_remaining(scale, now=NOW) is None
    assert ent.lifecycle_state(scale, now=NOW) == ent.LIFECYCLE_ACTIVE_SCALE


def test_scale_holds_playbooks_and_priority_routing() -> None:
    scale = _org(plan=ent.PLAN_SCALE, evaluation_started_at=None, evaluation_expires_at=None)
    entitlements = ent.effective_entitlements(scale, now=NOW)
    assert ent.has_entitlement(entitlements, ent.FEATURE_INCIDENT_PLAYBOOKS) is True
    assert ent.has_entitlement(entitlements, ent.FEATURE_PRIORITY_ROUTING) is True


def test_enterprise_widens_only_through_audited_overrides() -> None:
    enterprise = _org(
        plan=ent.PLAN_ENTERPRISE, evaluation_started_at=None, evaluation_expires_at=None,
        entitlement_overrides={ent.LIMIT_MONITORED_CONTRACTS: 500, ent.FEATURE_AUTOMATIC_EXECUTION: True},
    )
    entitlements = ent.effective_entitlements(enterprise, now=NOW)
    assert ent.limit_for(entitlements, ent.LIMIT_MONITORED_CONTRACTS) == 500
    assert ent.has_entitlement(entitlements, ent.FEATURE_AUTOMATIC_EXECUTION) is True
    assert ent.has_entitlement(entitlements, ent.FEATURE_CUSTOM_INTEGRATIONS) is True


# ═══ 4. The authoritative matrix ═════════════════════════════════════════════

def test_matrix_states_the_active_versus_expired_pilot_distinction() -> None:
    rows = {row['capability']: row for row in ent.capability_matrix(now=NOW)}
    for capability in ('Incident playbooks', 'AI investigation', 'Threat detection', 'Audit-ready exports'):
        assert rows[capability][ent.MATRIX_ACTIVE_PILOT] == ent.MATRIX_YES, capability
        assert rows[capability][ent.MATRIX_EXPIRED_PILOT] == ent.MATRIX_NO, capability
        assert rows[capability][ent.MATRIX_SCALE] == ent.MATRIX_YES, capability


def test_matrix_never_reports_read_surfaces_as_lost_on_expiry() -> None:
    rows = {row['capability']: row for row in ent.capability_matrix(now=NOW)}
    for capability in ('Core dashboard', 'Alerts', 'Incidents'):
        assert rows[capability][ent.MATRIX_EXPIRED_PILOT] == ent.MATRIX_YES, capability


def test_matrix_flags_every_feature_no_code_path_reads() -> None:
    """An intention must not be presented as a control."""
    unenforced = {row['capability'] for row in ent.capability_matrix(now=NOW) if not row['enforced']}
    assert unenforced == {
        'Multi-network', 'Custom evidence templates', 'Custom integrations', 'Priority alert routing',
    }


def test_the_published_matrix_document_matches_the_engine() -> None:
    from scripts.render_plan_matrix import DOC_PATH, render

    committed = (Path(__file__).resolve().parents[3] / DOC_PATH).read_text(encoding='utf-8')
    assert committed == render(), (
        'docs/PLAN_ENTITLEMENT_MATRIX.md is stale. '
        'Regenerate with: python -m scripts.render_plan_matrix --write'
    )


# ═══ 5. Backend enforcement, in the real handlers ════════════════════════════

class _Result:
    def __init__(self, row: Any = None) -> None:
        self._row = row

    def fetchone(self) -> Any:
        return self._row

    def fetchall(self) -> list[Any]:
        return [] if self._row is None else [self._row]


class _RecordingConn:
    def __init__(self, reads: dict[str, Any] | None = None) -> None:
        self.reads = reads or {}
        self.writes: list[str] = []
        self.committed = False

    def execute(self, query: str, params: Any = None) -> _Result:
        sql = ' '.join(str(query).split())
        lowered = sql.lower()
        if lowered.startswith(('insert', 'update', 'delete')):
            self.writes.append(sql)
            return _Result()
        for needle, row in self.reads.items():
            if needle in lowered:
                return _Result(row)
        return _Result()

    def commit(self) -> None:
        self.committed = True

    def transaction(self):
        @contextmanager
        def _noop():
            yield

        return _noop()


def _request() -> SimpleNamespace:
    return SimpleNamespace(headers={'x-workspace-id': WS}, client=None)


@pytest.fixture
def tenant(monkeypatch: pytest.MonkeyPatch):
    """An authenticated session whose organization the test chooses per case."""
    state = SimpleNamespace(organization=_live_active_pilot())

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_a, **_k: None)
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)
    monkeypatch.setattr(ai_triage, '_audit', lambda *_a, **_k: None)
    user = {
        'id': USER, 'email': 'a@example.com',
        'email_verified_at': '2026-01-01T00:00:00Z', 'mfa_enabled': False,
    }
    workspace_context = {'workspace_id': WS, 'role': 'owner', 'workspace': {'id': WS, 'name': 'W', 'slug': 'w'}}
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_a, **_k: dict(user))
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda *_a, **_k: dict(workspace_context))
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda *_a, **_k: (dict(user), dict(workspace_context)))
    monkeypatch.setattr(
        pilot, '_require_workspace_permission', lambda *_a, **_k: (dict(user), dict(workspace_context)),
    )
    # The real resolve_context runs, so these tests exercise the actual
    # plan-plus-lifecycle resolution rather than a stubbed verdict.
    monkeypatch.setattr(org_service, 'tenancy_schema_ready', lambda *_a, **_k: True)
    monkeypatch.setattr(org_service, 'organization_for_workspace', lambda *_a, **_k: dict(state.organization))
    return state


def _connection(monkeypatch: pytest.MonkeyPatch, connection: _RecordingConn) -> None:
    @contextmanager
    def _pg():
        yield connection

    monkeypatch.setattr(pilot, 'pg_connection', _pg)


def test_enforce_plan_operation_allows_an_active_evaluation(monkeypatch, tenant) -> None:
    connection = _RecordingConn()
    context = pilot.enforce_plan_operation(connection, WS, ent.FEATURE_AI_INVESTIGATION)
    assert context['lifecycle_state'] == ent.LIFECYCLE_ACTIVE_PILOT


def test_enforce_plan_operation_refuses_an_expired_evaluation(monkeypatch, tenant) -> None:
    tenant.organization = _live_expired_pilot()
    with pytest.raises(HTTPException) as exc_info:
        pilot.enforce_plan_operation(_RecordingConn(), WS, ent.FEATURE_AI_INVESTIGATION)
    assert exc_info.value.detail['code'] == ent.CODE_EVALUATION_EXPIRED


def test_enforce_plan_operation_applies_no_rule_before_the_tenancy_migration(
    monkeypatch, tenant,
) -> None:
    """A deployment without migration 0150 keeps its pre-existing behaviour."""
    tenant.organization = _live_expired_pilot()
    monkeypatch.setattr(org_service, 'tenancy_schema_ready', lambda *_a, **_k: False)
    context = pilot.enforce_plan_operation(_RecordingConn(), WS, ent.FEATURE_AI_INVESTIGATION)
    assert context['available'] is False


def test_expired_pilot_cannot_start_a_new_monitoring_workload(monkeypatch, tenant) -> None:
    tenant.organization = _live_expired_pilot()
    connection = _RecordingConn()
    _connection(monkeypatch, connection)
    with pytest.raises(HTTPException) as exc_info:
        pilot.set_target_enabled('target-1', True, _request())
    assert exc_info.value.detail['code'] == ent.CODE_EVALUATION_EXPIRED
    assert connection.writes == []


def test_expired_pilot_may_still_STOP_monitoring(monkeypatch, tenant) -> None:
    """Stopping work is never refused — only starting it is."""
    tenant.organization = _live_expired_pilot()
    connection = _RecordingConn()
    _connection(monkeypatch, connection)
    with pytest.raises(HTTPException) as exc_info:
        pilot.set_target_enabled('target-1', False, _request())
    # 404 for the absent target, NOT 403: the lifecycle gate never ran.
    assert exc_info.value.status_code == 404


@pytest.mark.parametrize(
    'call',
    [
        pytest.param(
            lambda: pilot.create_notification_destination(
                {'destination_type': 'webhook', 'name': 'Ops', 'config': {}}, _request(),
            ),
            id='notification_destination',
        ),
        pytest.param(
            lambda: pilot.create_webhook({'target_url': 'https://example.com/hook'}, _request()),
            id='outbound_webhook',
        ),
        pytest.param(
            lambda: pilot.create_slack_integration(
                {'mode': 'webhook', 'webhook_url': 'https://hooks.slack.com/services/T/B/x'}, _request(),
            ),
            id='slack_integration',
        ),
    ],
)
def test_expired_pilot_cannot_create_a_new_production_integration(monkeypatch, tenant, call) -> None:
    tenant.organization = _live_expired_pilot()
    connection = _RecordingConn()
    _connection(monkeypatch, connection)
    with pytest.raises(HTTPException) as exc_info:
        call()
    assert exc_info.value.detail['code'] == ent.CODE_EVALUATION_EXPIRED
    assert connection.writes == []


def test_expired_pilot_cannot_queue_a_new_ai_investigation(monkeypatch, tenant) -> None:
    tenant.organization = _live_expired_pilot()
    connection = _RecordingConn(reads={'from incidents': {'id': INCIDENT, 'workspace_id': WS}})
    _connection(monkeypatch, connection)
    with pytest.raises(HTTPException) as exc_info:
        ai_triage.request_triage(INCIDENT, _request())
    assert exc_info.value.detail['code'] == ent.CODE_EVALUATION_EXPIRED
    assert connection.writes == []


def test_active_pilot_can_queue_an_ai_investigation(monkeypatch, tenant) -> None:
    """The gate must not be the reason an ACTIVE evaluator cannot investigate."""
    monkeypatch.setattr(ai_triage, 'triage_config', lambda: {'enabled': False})
    connection = _RecordingConn(reads={'from incidents': {'id': INCIDENT, 'workspace_id': WS}})
    _connection(monkeypatch, connection)
    result = ai_triage.request_triage(INCIDENT, _request())
    # Reaches the AI-disabled branch, which is downstream of the plan gate.
    assert result['status'] == ai_triage.JOB_DISABLED


@pytest.mark.parametrize(
    'call',
    [
        pytest.param(lambda: forensic_investigation.rerun_investigation(INCIDENT, _request()), id='rerun'),
        pytest.param(lambda: forensic_investigation.generate_report(INCIDENT, _request()), id='report'),
    ],
)
def test_expired_pilot_cannot_start_a_new_forensic_run(monkeypatch, tenant, call) -> None:
    tenant.organization = _live_expired_pilot()
    connection = _RecordingConn()
    _connection(monkeypatch, connection)
    monkeypatch.setattr(forensic_investigation, 'forensic_schema_ready', lambda *_a, **_k: True)
    with pytest.raises(HTTPException) as exc_info:
        call()
    assert exc_info.value.detail['code'] == ent.CODE_EVALUATION_EXPIRED
    assert connection.writes == []


def test_reading_an_existing_investigation_is_never_gated() -> None:
    """The read path must not learn about plans at all."""
    source = Path(forensic_investigation.__file__).read_text(encoding='utf-8')
    start = source.index('def get_investigation(')
    body = source[start:source.index('def get_workflow(', start)]
    assert 'enforce_plan_operation' not in body


# ═══ 6. Runtime surfaces read the effective entitlements ═════════════════════

def test_account_plan_context_reports_the_evaluation_state(monkeypatch, tenant) -> None:
    tenant.organization = _active_pilot()
    context = org_service.resolve_context(_RecordingConn(), WS, heal=False, now=NOW)
    assert context['lifecycle_state'] == ent.LIFECYCLE_ACTIVE_PILOT
    assert context['entitlements'][ent.FEATURE_INCIDENT_PLAYBOOKS] is True

    tenant.organization = _expired_pilot()
    expired = org_service.resolve_context(_RecordingConn(), WS, heal=False, now=NOW)
    assert expired['lifecycle_state'] == ent.LIFECYCLE_EXPIRED_PILOT
    assert expired['entitlements'][ent.FEATURE_INCIDENT_PLAYBOOKS] is False
    # The cap the UI renders is unchanged — expiry is not a re-scoping.
    assert expired['entitlements'][ent.LIMIT_MONITORED_CONTRACTS] == 5


def test_the_execution_lock_closes_when_the_evaluation_ends(monkeypatch, tenant) -> None:
    monkeypatch.setattr(org_service, 'tenancy_schema_state', lambda *_a, **_k: org_service.SCHEMA_READY)
    active = pilot.plan_execution_lock(_RecordingConn(), WS)
    assert active == {'locked': True, 'reason': 'plan_recommend_only', 'plan': 'pilot'}

    tenant.organization = _live_expired_pilot()
    expired = pilot.plan_execution_lock(_RecordingConn(), WS)
    assert expired['locked'] is True
    assert expired['reason'] == 'evaluation_expired'


# ═══ 7. Workers ══════════════════════════════════════════════════════════════

def test_the_monitoring_worker_skips_expired_and_suspended_tenants() -> None:
    from services.api.app import monitoring_runner

    clause = ' '.join(monitoring_runner._ORGANIZATION_MONITORING_EXCLUSION_SQL.split())
    assert "tenant_org.status <> 'active'" in clause
    assert "tenant_org.plan = 'pilot'" in clause
    assert 'tenant_org.evaluation_expires_at <= NOW()' in clause


def test_the_monitoring_worker_does_not_shut_down_grandfathered_pilots() -> None:
    """``evaluation_expires_at IS NULL`` must never match the exclusion."""
    from services.api.app import monitoring_runner

    clause = ' '.join(monitoring_runner._ORGANIZATION_MONITORING_EXCLUSION_SQL.split())
    assert 'tenant_org.evaluation_expires_at IS NOT NULL' in clause


def test_every_worker_asks_the_same_question_about_an_inactive_tenant() -> None:
    """Two workers with two definitions of "active" would eventually disagree,
    and the disagreement would be a tenant still spending budget."""
    from services.api.app import monitoring_runner

    shared = org_service.inactive_tenant_exclusion_sql('t.workspace_id')
    assert monitoring_runner._ORGANIZATION_MONITORING_EXCLUSION_SQL == shared
    assert 'a.workspace_id' in org_service.inactive_tenant_exclusion_sql('a.workspace_id')


def test_the_asset_risk_worker_stops_auto_queueing_for_an_inactive_tenant() -> None:
    """Staleness alone is not a reason to spend an expired tenant's budget. Without
    this the worker would re-queue assessments the API refuses on demand."""
    from services.api.app.domains.asset_risk import worker as asset_risk_worker

    captured: list[str] = []

    class _Conn:
        def execute(self, query: str, params: Any = None) -> _Result:
            sql = ' '.join(str(query).split())
            captured.append(sql)
            if 'information_schema' in sql:
                return _Result({'org_table': 1, 'link_column': 1})
            return _Result()

        def commit(self) -> None:
            pass

    asset_risk_worker._enqueue_due_assets(
        _Conn(), config={'assessment_stale_seconds': 3600, 'batch_size': 10},
    )
    selection = next(sql for sql in captured if 'FROM assets a' in sql)
    assert "tenant_org.status <> 'active'" in selection
    assert 'tenant_ws.id = a.workspace_id' in selection


def test_the_asset_risk_worker_keeps_running_before_the_tenancy_migration() -> None:
    """A missing organizations table removes the clause, never the work."""
    from services.api.app.domains.asset_risk import worker as asset_risk_worker

    captured: list[str] = []

    class _Conn:
        def execute(self, query: str, params: Any = None) -> _Result:
            sql = ' '.join(str(query).split())
            captured.append(sql)
            if 'information_schema' in sql:
                return _Result({'org_table': 0, 'link_column': 0})
            return _Result()

        def commit(self) -> None:
            pass

    asset_risk_worker._enqueue_due_assets(
        _Conn(), config={'assessment_stale_seconds': 3600, 'batch_size': 10},
    )
    selection = next(sql for sql in captured if 'FROM assets a' in sql)
    assert 'tenant_org' not in selection


# ═══ 8. Upgrading preserves the organization ═════════════════════════════════

class _PlanChangeConn:
    """Applies ``UPDATE organizations`` to one in-memory row."""

    def __init__(self, row: dict[str, Any]) -> None:
        self.row = row
        self.updates = 0

    def execute(self, query: str, params: Any = None) -> _Result:
        sql = ' '.join(str(query).split()).lower()
        if sql.startswith('update organizations'):
            self.updates += 1
            self.row['plan'] = params[0]
            if 'evaluation_started_at = null' in sql:
                self.row['evaluation_started_at'] = None
                self.row['evaluation_expires_at'] = None
                if self.row.get('status') == ent.STATUS_EXPIRED:
                    self.row['status'] = ent.STATUS_ACTIVE
            else:
                self.row['evaluation_started_at'] = params[1]
                self.row['evaluation_expires_at'] = params[2]
            return _Result()
        return _Result(dict(self.row))

    def commit(self) -> None:
        pass


def test_pilot_to_scale_keeps_the_same_organization_and_lifts_the_expiry() -> None:
    row = _expired_pilot()
    row['status'] = ent.STATUS_EXPIRED
    connection = _PlanChangeConn(row)

    upgraded = org_service.set_plan(connection, organization_id=ORG, plan=ent.PLAN_SCALE, now=NOW)

    # Same tenant row: every workspace, asset, incident and evidence package it
    # owns is addressed by this id and is therefore untouched.
    assert str(upgraded['id']) == ORG
    assert upgraded['plan'] == ent.PLAN_SCALE
    assert upgraded['evaluation_expires_at'] is None
    assert upgraded['status'] == ent.STATUS_ACTIVE
    assert ent.lifecycle_state(upgraded, now=NOW) == ent.LIFECYCLE_ACTIVE_SCALE


def test_scale_limits_apply_immediately_after_the_upgrade() -> None:
    row = _expired_pilot()
    connection = _PlanChangeConn(row)
    upgraded = org_service.set_plan(connection, organization_id=ORG, plan=ent.PLAN_SCALE, now=NOW)

    entitlements = ent.effective_entitlements(upgraded, now=NOW)
    assert ent.limit_for(entitlements, ent.LIMIT_MONITORED_CONTRACTS) == 25
    assert ent.limit_for(entitlements, ent.LIMIT_EVIDENCE_PACKAGES) is None
    for feature in EVALUATION_FEATURES:
        assert ent.has_entitlement(entitlements, feature) is True, feature


# ═══ 9. Asset-linked monitoring targets answer to the same gate ══════════════

def test_verifying_an_asset_cannot_mint_a_target_past_the_plan_limit(monkeypatch, tenant) -> None:
    """``POST /assets/{id}/verify`` links a monitoring target. Minting one is the
    same billable act as ``POST /targets`` and must not route around its cap."""
    calls: list[str] = []

    def _enforce(connection: Any, workspace_id: str, limit_key: str):
        calls.append(limit_key)
        raise HTTPException(
            status_code=403,
            detail={'code': ent.CODE_PLAN_LIMIT_REACHED, 'resource': ent.LIMIT_RESOURCES[limit_key]},
        )

    monkeypatch.setattr(pilot, 'enforce_plan_creation_limit', _enforce)
    connection = _RecordingConn()

    with pytest.raises(HTTPException) as exc_info:
        pilot._upsert_asset_monitoring_linkage(
            connection,
            workspace_id=WS,
            asset_row={'id': 'asset-1', 'name': 'ABC', 'asset_type': 'contract', 'identifier': '0x' + 'a' * 40},
        )
    assert exc_info.value.detail['resource'] == 'monitoring_targets'
    assert calls == [ent.LIMIT_MONITORING_TARGETS]
    assert connection.writes == []


def test_relinking_an_existing_target_is_never_refused(monkeypatch, tenant) -> None:
    """Re-linking adds no target, so it must not be gated — an expired tenant has
    to be able to keep its own records consistent."""
    calls: list[str] = []
    monkeypatch.setattr(
        pilot, 'enforce_plan_creation_limit', lambda *a, **k: calls.append(a[2]),
    )
    monkeypatch.setattr(pilot, 'ensure_monitored_system_for_target', lambda *a, **k: {'status': 'ok'})
    connection = _RecordingConn(reads={'from targets': {'id': 'target-1'}})

    pilot._upsert_asset_monitoring_linkage(
        connection,
        workspace_id=WS,
        asset_row={'id': 'asset-1', 'name': 'ABC', 'asset_type': 'contract', 'identifier': '0x' + 'a' * 40},
    )
    assert calls == []
    assert any(write.lower().startswith('update targets') for write in connection.writes)


def test_expired_pilot_cannot_trigger_a_new_asset_verification_scan(monkeypatch, tenant) -> None:
    tenant.organization = _live_expired_pilot()
    connection = _RecordingConn(reads={'from assets': {'id': 'asset-1', 'identifier': '0x' + 'a' * 40,
                                                       'chain_network': 'base-mainnet', 'asset_type': 'contract'}})
    _connection(monkeypatch, connection)
    with pytest.raises(HTTPException) as exc_info:
        pilot.verify_asset('asset-1', _request())
    assert exc_info.value.detail['code'] == ent.CODE_EVALUATION_EXPIRED
    assert connection.writes == []


def test_expired_pilot_cannot_trigger_a_new_asset_risk_assessment(monkeypatch, tenant) -> None:
    from services.api.app.domains.asset_risk import registry, service

    tenant.organization = _live_expired_pilot()
    connection = _RecordingConn(reads={'from assets': {'id': 'asset-1', 'workspace_id': WS}})
    _connection(monkeypatch, connection)
    monkeypatch.setattr(
        pilot, 'require_ops_rbac_guard',
        lambda *_a, **_k: ({'id': USER}, {'workspace_id': WS, 'role': 'owner'}),
    )
    monkeypatch.setattr(service, '_table_exists', lambda *_a, **_k: True)

    with pytest.raises(HTTPException) as exc_info:
        registry.trigger_assessment_endpoint('asset-1', _request())
    assert exc_info.value.detail['code'] == ent.CODE_EVALUATION_EXPIRED
    assert connection.writes == []
