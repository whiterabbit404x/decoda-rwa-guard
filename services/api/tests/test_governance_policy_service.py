"""Screen 11 — policy persistence, tenancy, RBAC, and the read-only simulator.

Uses the repository's lightweight fake-connection convention so the write paths
are covered without a live Postgres.

Invariants asserted here:
  * every statement carries the workspace id — no unscoped or cross-tenant read,
  * a simulation writes ONE row, stamped simulation=TRUE, and touches nothing
    else: no response action, no incident, no policy state, no settlement state,
  * a simulation is excluded from the daily issuance total,
  * the operator's authority is resolved SERVER-SIDE and a client claim is ignored,
  * a material edit bumps the version AND appends an immutable history row,
  * a rename does not consume a version,
  * an unauthorized user cannot edit, whatever the frontend rendered,
  * a stale editor is rejected with a conflict instead of overwriting,
  * an empty history is reported as empty, never fabricated.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from services.api.app import pilot
from services.api.app.domains.governance_policy import config as gpc
from services.api.app.domains.governance_policy import endpoints, service
from services.api.app.domains.governance_policy.schemas import PolicyDefinition

NOW = datetime(2026, 9, 1, 10, 42, 0, tzinfo=timezone.utc)
WS = 'ws-1'
OTHER_WS = 'ws-2'
POLICY_ID = '11111111-1111-1111-1111-111111111111'
USER_ID = '22222222-2222-2222-2222-222222222222'
OPERATOR_ID = '33333333-3333-3333-3333-333333333333'


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeConn:
    """Matches executed queries on normalized substrings; records every
    statement so a test can assert on reads AND writes."""

    def __init__(self, tables_exist=True, matchers=None):
        self.tables_exist = tables_exist
        self.matchers = list(matchers or [])
        self.statements: list[tuple[str, object]] = []
        self.writes: list[tuple[str, object]] = []
        self.committed = False

    def execute(self, query, params=None):
        q = ' '.join(str(query).split())
        ql = q.lower()
        self.statements.append((q, params))
        if 'to_regclass' in ql:
            return _Result([{'ok': bool(self.tables_exist)}])
        for needle, rows in self.matchers:
            if needle in q:
                if any(kw in ql for kw in ('insert into', 'update ', 'delete ')):
                    self.writes.append((q, params))
                return _Result(rows() if callable(rows) else rows)
        if any(kw in ql for kw in ('insert into', 'update ', 'delete ')):
            self.writes.append((q, params))
            return _Result([])
        return _Result([])

    def commit(self):
        self.committed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def writes_matching(self, needle):
        return [(q, p) for (q, p) in self.writes if needle in q]

    def statements_matching(self, needle):
        return [(q, p) for (q, p) in self.statements if needle in q]


def _policy_row(**overrides):
    row = {
        'id': POLICY_ID,
        'workspace_id': WS,
        'policy_key': 'POL-MINT-007',
        'name': 'RWA Mint Policy',
        'operation': 'MINT',
        'status': 'ACTIVE',
        'version': 7,
        'asset_id': None,
        'required_business_event': 'SUBSCRIPTION',
        'settlement_requirement': 'CLEARED',
        'allowed_window_start_utc': '08:00',
        'allowed_window_end_utc': '18:00',
        'maximum_daily_amount_usd': Decimal('10000000.00'),
        'required_roles': ['TREASURY_OPERATOR', 'COMPLIANCE_APPROVER'],
        'violation_action': 'DENY',
        'origin': 'customer',
        'created_by_user_id': USER_ID,
        'updated_by_user_id': USER_ID,
        'created_at': NOW,
        'updated_at': NOW,
    }
    row.update(overrides)
    return row


def _conn(**overrides):
    matchers = [
        (f'FROM {service.POLICIES_TABLE} WHERE id', [_policy_row()]),
        (f'FROM {service.POLICIES_TABLE} WHERE policy_key', [_policy_row()]),
        (f'FROM {service.POLICIES_TABLE} WHERE workspace_id', [_policy_row()]),
        ('FROM workspace_members WHERE workspace_id', [{'role': 'admin'}]),
        ('SELECT granted FROM workspace_role_permissions', [{'granted': True}]),
        ('COALESCE(SUM(amount_usd), 0) AS total', [{'total': Decimal('0')}]),
        (f'FROM {service.VERSIONS_TABLE}', []),
        # The guarded UPDATE returns the row it matched; an empty result means a
        # concurrent writer already moved the version on.
        (f'UPDATE {service.POLICIES_TABLE}', [{'version': 8}]),
    ]
    matchers = list(overrides.pop('matchers', [])) + matchers
    return FakeConn(matchers=matchers, **overrides)


class _Request:
    def __init__(self, headers=None):
        self.headers = headers or {'x-workspace-id': WS}
        self.client = None


@pytest.fixture
def api(monkeypatch):
    """Patch the pilot seams so the endpoints run against the fake connection."""
    state = {'conn': _conn(), 'role': 'admin', 'can_manage': True}

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda c: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: state['conn'])
    monkeypatch.setattr(pilot, 'utc_now', lambda: NOW)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda c, r: {'id': USER_ID})
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda c, u, w: {
        'workspace_id': WS, 'role': state['role'], 'workspace': {'id': WS, 'name': 'Acme Capital'},
    })
    monkeypatch.setattr(pilot, '_workspace_permission_granted',
                        lambda c, w, role, perm: bool(state['can_manage']))
    monkeypatch.setattr(pilot, 'log_audit', lambda *a, **k: state.setdefault('audits', []).append(k))
    return state


# --------------------------------------------------------------------------
# Tenancy
# --------------------------------------------------------------------------
def test_every_policy_read_is_workspace_scoped():
    conn = _conn()
    service.list_policies(conn, workspace_id=WS)
    service.get_policy(conn, workspace_id=WS, policy_ref=POLICY_ID)
    service.get_policy(conn, workspace_id=WS, policy_ref='POL-MINT-007')
    service.list_versions(conn, workspace_id=WS, policy_id=POLICY_ID)
    reads = [(q, p) for (q, p) in conn.statements if 'governance_polic' in q]
    assert reads, 'the reads must actually run'
    for query, params in reads:
        assert 'workspace_id = %s' in query
        assert WS in (params or ())


def test_a_policy_id_from_another_tenant_resolves_to_nothing():
    # The row exists, but only for OTHER_WS: the WHERE clause carries the
    # caller's workspace, so the lookup returns nothing rather than that tenant's
    # policy.
    conn = FakeConn(matchers=[(f'FROM {service.POLICIES_TABLE} WHERE id', [])])
    assert service.get_policy(conn, workspace_id=OTHER_WS, policy_ref=POLICY_ID) is None


def test_a_missing_policy_table_reads_as_empty_not_as_an_exception():
    conn = FakeConn(tables_exist=False)
    assert service.list_policies(conn, workspace_id=WS) == []
    assert service.get_policy(conn, workspace_id=WS, policy_ref=POLICY_ID) is None
    assert service.list_versions(conn, workspace_id=WS, policy_id=POLICY_ID) == []


# --------------------------------------------------------------------------
# Server-resolved context (§19)
# --------------------------------------------------------------------------
def test_operator_authority_is_read_from_workspace_membership_not_from_the_client():
    conn = _conn()
    policy = service.policy_from_row(_policy_row())
    context = service.build_context(
        conn, workspace_id=WS, policy=policy, now=NOW, simulation=True,
        payload={
            'operation': 'MINT', 'operator_id': OPERATOR_ID,
            # A hostile client asserting its own authority and issuance total.
            'operator_has_treasury_role': True, 'daily_total_usd': '0',
            'policy_version': 999, 'decision': 'ALLOW',
        },
    )
    membership = conn.statements_matching('FROM workspace_members WHERE workspace_id')
    assert membership, 'the operator role must be looked up server-side'
    assert membership[0][1] == (WS, OPERATOR_ID)
    # Nothing the client sent about authority, totals, versions or decisions
    # survived into the evaluation context.
    snapshot = context.as_snapshot()
    assert 'policy_version' not in snapshot and 'decision' not in snapshot
    assert context.daily_total_usd == Decimal('0')  # from the DB sum, not the body


def test_an_operator_outside_the_workspace_has_no_treasury_authority():
    conn = FakeConn(matchers=[('FROM workspace_members WHERE workspace_id', [])])
    assert service.resolve_operator_authority(
        conn, workspace_id=WS, operator_user_id=OPERATOR_ID) is False


def test_an_unnamed_operator_has_no_treasury_authority():
    assert service.resolve_operator_authority(_conn(), workspace_id=WS, operator_user_id=None) is False
    assert service.resolve_operator_authority(_conn(), workspace_id=WS, operator_user_id='user_183') is False


def test_a_failed_membership_lookup_returns_unknown_rather_than_authorized():
    class Boom(FakeConn):
        def execute(self, query, params=None):
            if 'workspace_members' in str(query):
                raise RuntimeError('db down')
            return super().execute(query, params)

    assert service.resolve_operator_authority(
        Boom(), workspace_id=WS, operator_user_id=OPERATOR_ID) is None


# --------------------------------------------------------------------------
# The daily total counts enforcement only
# --------------------------------------------------------------------------
def test_the_daily_total_excludes_simulations_and_denials():
    conn = _conn()
    service.daily_total_usd(conn, workspace_id=WS, policy_id=POLICY_ID, now=NOW)
    query, params = conn.statements_matching('COALESCE(SUM(amount_usd), 0)')[0]
    assert 'simulation = FALSE' in query
    assert "decision = 'ALLOW'" in query
    assert 'workspace_id = %s' in query
    assert params[0] == WS and params[1] == POLICY_ID


def test_an_unreadable_daily_total_is_none_not_zero():
    class Boom(FakeConn):
        def execute(self, query, params=None):
            if 'SUM(amount_usd)' in str(query):
                raise RuntimeError('db down')
            return super().execute(query, params)

    assert service.daily_total_usd(Boom(), workspace_id=WS, policy_id=POLICY_ID, now=NOW) is None


def test_no_evaluations_table_means_the_total_is_unknown_not_zero():
    assert service.daily_total_usd(
        FakeConn(tables_exist=False), workspace_id=WS, policy_id=POLICY_ID, now=NOW) is None


# --------------------------------------------------------------------------
# Simulation is read-only
# --------------------------------------------------------------------------
def test_the_simulator_reproduces_the_reference_deny(api):
    result = endpoints.simulate_endpoint(POLICY_ID, {
        'operation': 'MINT', 'amount_usd': '5000000', 'operator_id': OPERATOR_ID,
        'business_event': 'SUBSCRIPTION', 'settlement_status': 'CLEARED',
        'compliance_approval': False,
    }, _Request())
    assert result['decision'] == 'DENY'
    assert result['reason_codes'] == [gpc.COMPLIANCE_APPROVAL_MISSING]
    assert result['required_approvals'] == [gpc.ROLE_COMPLIANCE_APPROVER]
    assert result['policy_version'] == 7
    assert result['simulation'] is True
    assert result['decision_authority'] == 'Deterministic Policy Engine'
    assert result['ai_authority'] == 'Recommend only'


def test_a_satisfied_simulation_allows(api):
    result = endpoints.simulate_endpoint(POLICY_ID, {
        'operation': 'MINT', 'amount_usd': '5000000', 'operator_id': OPERATOR_ID,
        'business_event': 'SUBSCRIPTION', 'settlement_status': 'CLEARED',
        'compliance_approval': True,
    }, _Request())
    assert result['decision'] == 'ALLOW'
    assert result['reason_codes'] == [gpc.POLICY_SATISFIED]
    assert result['required_approvals'] == []


def test_a_simulation_writes_exactly_one_row_and_it_is_marked_simulation(api):
    endpoints.simulate_endpoint(POLICY_ID, {
        'operation': 'MINT', 'amount_usd': '5000000', 'operator_id': OPERATOR_ID,
        'business_event': 'SUBSCRIPTION', 'settlement_status': 'CLEARED',
    }, _Request())
    writes = api['conn'].writes
    assert len(writes) == 1, [q for q, _ in writes]
    query, params = writes[0]
    assert f'INSERT INTO {service.EVALUATIONS_TABLE}' in query
    assert True in params, 'the row must be stamped simulation = TRUE'
    assert WS in params


def test_a_simulation_never_touches_response_actions_incidents_or_policy_state(api):
    endpoints.simulate_endpoint(POLICY_ID, {
        'operation': 'MINT', 'amount_usd': '1', 'operator_id': OPERATOR_ID,
        'business_event': 'SUBSCRIPTION', 'settlement_status': 'CLEARED',
    }, _Request())
    executed = ' '.join(q.lower() for q, _ in api['conn'].statements)
    for forbidden in ('response_actions', 'response_action_approvals', 'incidents',
                      'asset_authorized_issuances', 'threat_detections', 'alerts'):
        assert forbidden not in executed, f'a simulation must not touch {forbidden}'
    # And it never mutates the policy it evaluated.
    assert not api['conn'].writes_matching(f'UPDATE {service.POLICIES_TABLE}')
    assert not api['conn'].writes_matching(f'INSERT INTO {service.VERSIONS_TABLE}')


def test_the_simulation_endpoint_cannot_reach_an_execution_adapter():
    """Structural proof: the module has no executor in its import graph."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(endpoints))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or '')
    for forbidden in ('response_action_executor', 'integration_gateway', 'evm_activity_provider',
                      'quicknode_streams', 'monitoring_runner'):
        assert not any(forbidden in name for name in imported), forbidden


def test_a_simulation_is_not_recorded_when_recording_is_disabled(api, monkeypatch):
    monkeypatch.setenv('GOVERNANCE_POLICY_RECORD_SIMULATIONS', 'false')
    result = endpoints.simulate_endpoint(POLICY_ID, {
        'operation': 'MINT', 'amount_usd': '1', 'operator_id': OPERATOR_ID,
        'business_event': 'SUBSCRIPTION', 'settlement_status': 'CLEARED',
    }, _Request())
    assert result['recorded'] is False
    assert api['conn'].writes == []
    # The decision is still produced — recording is bookkeeping, not authority.
    assert result['decision'] in ('ALLOW', 'DENY')


def test_simulating_an_unknown_policy_fails_closed_with_a_deny(api, monkeypatch):
    api['conn'] = _conn(matchers=[(f'FROM {service.POLICIES_TABLE} WHERE id', [])])
    monkeypatch.setattr(pilot, 'pg_connection', lambda: api['conn'])
    result = endpoints.simulate_endpoint(POLICY_ID, {
        'operation': 'MINT', 'amount_usd': '1', 'compliance_approval': True,
    }, _Request())
    assert result['decision'] == 'DENY'
    assert result['reason_codes'] == [gpc.POLICY_NOT_FOUND]


def test_a_viewer_may_simulate_but_the_edit_control_is_reported_off(api):
    api['role'] = 'viewer'
    api['can_manage'] = False
    result = endpoints.simulate_endpoint(POLICY_ID, {
        'operation': 'MINT', 'amount_usd': '1', 'operator_id': OPERATOR_ID,
        'business_event': 'SUBSCRIPTION', 'settlement_status': 'CLEARED',
    }, _Request())
    assert result['decision'] in ('ALLOW', 'DENY')
    assert result['can_manage'] is False


# --------------------------------------------------------------------------
# Simulator input validation (§16)
# --------------------------------------------------------------------------
@pytest.mark.parametrize('payload,code', [
    ({}, 'invalid_operation'),
    ({'operation': 'AIRDROP'}, 'invalid_operation'),
    ({'operation': 'MINT', 'amount_usd': 'five million'}, 'invalid_amount'),
    ({'operation': 'MINT', 'amount_usd': '-1'}, 'invalid_amount'),
    ({'operation': 'MINT', 'amount_usd': 'NaN'}, 'invalid_amount'),
    ({'operation': 'MINT', 'business_event': 'WIRE'}, 'invalid_business_event'),
    ({'operation': 'MINT', 'settlement_status': 'SETTLED-ISH'}, 'invalid_settlement_status'),
])
def test_invalid_simulator_input_is_a_400_never_a_guess(api, payload, code):
    with pytest.raises(Exception) as exc:
        endpoints.simulate_endpoint(POLICY_ID, payload, _Request())
    detail = getattr(exc.value, 'detail', {})
    assert getattr(exc.value, 'status_code', None) == 400
    assert detail.get('code') == code
    assert api['conn'].writes == []


def test_a_thousands_separated_amount_is_accepted(api):
    result = endpoints.simulate_endpoint(POLICY_ID, {
        'operation': 'MINT', 'amount_usd': '5,000,000', 'operator_id': OPERATOR_ID,
        'business_event': 'SUBSCRIPTION', 'settlement_status': 'CLEARED',
        'compliance_approval': True,
    }, _Request())
    assert result['decision'] == 'ALLOW'
    assert result['amount_usd'] == '5000000'


# --------------------------------------------------------------------------
# Versioned editing
# --------------------------------------------------------------------------
def _apply(conn, changes, expected_version=7):
    return service.apply_policy_update(
        conn, workspace_id=WS, policy=service.policy_from_row(_policy_row()),
        changes=changes, user_id=USER_ID, expected_version=expected_version, now=NOW,
    )


def test_a_material_change_bumps_the_version_and_appends_immutable_history():
    conn = _conn()
    outcome = _apply(conn, {'maximum_daily_amount_usd': Decimal('5000000')})
    assert outcome['status'] == 'updated'
    assert outcome['version'] == 8
    updates = conn.writes_matching(f'UPDATE {service.POLICIES_TABLE}')
    assert len(updates) == 1
    # Guarded on the version it read, so two concurrent editors cannot both win.
    assert 'AND version = %s' in updates[0][0]
    inserts = conn.writes_matching(f'INSERT INTO {service.VERSIONS_TABLE}')
    assert len(inserts) == 1
    params = inserts[0][1]
    assert 8 in params
    assert any('Maximum issuance changed' in str(p) for p in params)


def test_the_history_row_records_both_the_before_and_the_after():
    conn = _conn()
    _apply(conn, {'maximum_daily_amount_usd': Decimal('5000000')})
    params = conn.writes_matching(f'INSERT INTO {service.VERSIONS_TABLE}')[0][1]
    # (id, workspace_id, policy_id, version, status, snapshot, previous, new, ...)
    snapshot, previous, new = json.loads(params[5]), json.loads(params[6]), json.loads(params[7])
    assert previous['maximum_daily_amount_usd'] == '10000000.00'
    assert new['maximum_daily_amount_usd'] == '5000000'
    # The snapshot is the policy AS OF the new version, so an auditor can replay it.
    assert snapshot['maximum_daily_amount_usd'] == '5000000'
    assert snapshot['version'] == 8


def test_a_rename_updates_in_place_and_does_not_consume_a_version():
    conn = _conn()
    outcome = _apply(conn, {'name': 'Treasury Mint Policy'})
    assert outcome['status'] == 'updated'
    assert outcome['version'] == 7
    assert outcome['material'] == []
    assert conn.writes_matching(f'INSERT INTO {service.VERSIONS_TABLE}') == []


def test_writing_the_same_values_back_is_a_no_op():
    conn = _conn()
    outcome = _apply(conn, {'maximum_daily_amount_usd': Decimal('10000000.00'), 'status': 'ACTIVE'})
    assert outcome['status'] == 'unchanged'
    assert conn.writes == []


def test_an_update_that_matches_no_row_is_a_conflict_and_writes_no_history():
    """The second guard: a writer that commits between our read and our write.

    Both editors pass the expected_version check (both read version 7), so only
    the UPDATE's own WHERE clause stops the second one. When it matches nothing,
    no history row may be appended — that would fabricate an audit entry for an
    edit that never happened, and collide with the winner's (policy_id, version).
    """
    conn = _conn(matchers=[(f'UPDATE {service.POLICIES_TABLE}', [])])
    outcome = _apply(conn, {'maximum_daily_amount_usd': Decimal('5000000')})
    assert outcome['status'] == 'conflict'
    assert conn.writes_matching(f'INSERT INTO {service.VERSIONS_TABLE}') == []


def test_the_update_is_guarded_on_the_version_the_edit_was_built_on():
    conn = _conn()
    _apply(conn, {'maximum_daily_amount_usd': Decimal('5000000')})
    query, params = conn.writes_matching(f'UPDATE {service.POLICIES_TABLE}')[0]
    assert 'AND version = %s' in query
    assert 'RETURNING version' in query
    assert params[-1] == 7, 'the guard must carry the version that was read'


def test_a_stale_editor_is_rejected_rather_than_overwriting_a_newer_policy():
    conn = _conn()
    outcome = _apply(conn, {'status': 'DISABLED'}, expected_version=6)
    assert outcome['status'] == 'conflict'
    assert outcome['current_version'] == 7
    assert conn.writes == []


def test_the_change_summary_is_derived_from_the_diff_not_written_by_a_model():
    summary = service.summarize_change({
        'maximum_daily_amount_usd': (Decimal('5000000'), Decimal('10000000')),
        'status': ('DRAFT', 'ACTIVE'),
    })
    assert 'Status changed: DRAFT → ACTIVE' in summary
    assert 'Maximum issuance changed: 5000000 → 10000000' in summary


# --------------------------------------------------------------------------
# RBAC on the edit path (§11) — enforced by the BACKEND
# --------------------------------------------------------------------------
def test_an_unauthorized_user_cannot_edit_however_the_frontend_rendered(api, monkeypatch):
    denied = {}

    def _require(connection, request, permission, **kwargs):
        denied['permission'] = permission
        raise pilot.HTTPException(status_code=403, detail={'code': 'PERMISSION_DENIED'})

    monkeypatch.setattr(pilot, '_require_workspace_permission', _require)
    with pytest.raises(Exception) as exc:
        endpoints.update_policy_endpoint(POLICY_ID, {'status': 'DISABLED'}, _Request())
    assert getattr(exc.value, 'status_code', None) == 403
    # The canonical permission, not a new one invented for this screen.
    assert denied['permission'] == 'security.manage'
    assert denied['permission'] in pilot.WORKSPACE_PERMISSIONS
    assert api['conn'].writes == []


def test_the_edit_path_requires_the_permission_before_reading_anything(api, monkeypatch):
    order: list[str] = []
    monkeypatch.setattr(pilot, '_require_workspace_permission',
                        lambda c, r, p, **k: (order.append('permission'), ({'id': USER_ID}, {'workspace_id': WS, 'role': 'admin'}))[1])
    original = service.get_policy
    monkeypatch.setattr(service, 'get_policy', lambda *a, **k: (order.append('read'), original(*a, **k))[1])
    endpoints.update_policy_endpoint(POLICY_ID, {'status': 'DISABLED'}, _Request())
    assert order[0] == 'permission'


def test_an_authorized_edit_writes_the_canonical_audit_event(api, monkeypatch):
    monkeypatch.setattr(pilot, '_require_workspace_permission',
                        lambda c, r, p, **k: ({'id': USER_ID}, {'workspace_id': WS, 'role': 'admin'}))
    endpoints.update_policy_endpoint(POLICY_ID, {'status': 'DISABLED'}, _Request())
    audits = api.get('audits') or []
    assert audits, 'a policy change must be audited'
    assert audits[0]['action'] == 'governance_policy.disabled'
    assert audits[0]['entity_type'] == 'governance_policy'
    assert audits[0]['workspace_id'] == WS


def test_activation_and_generic_update_get_distinct_audit_actions(api, monkeypatch):
    monkeypatch.setattr(pilot, '_require_workspace_permission',
                        lambda c, r, p, **k: ({'id': USER_ID}, {'workspace_id': WS, 'role': 'admin'}))
    api['conn'] = _conn(matchers=[
        (f'FROM {service.POLICIES_TABLE} WHERE id', [_policy_row(status='DRAFT')]),
    ])
    monkeypatch.setattr(pilot, 'pg_connection', lambda: api['conn'])
    endpoints.update_policy_endpoint(POLICY_ID, {'status': 'ACTIVE'}, _Request())
    assert (api.get('audits') or [])[-1]['action'] == 'governance_policy.activated'


@pytest.mark.parametrize('payload,code', [
    ({'status': 'RETIRED'}, 'invalid_status'),
    ({'required_roles': ['CFO']}, 'invalid_required_roles'),
    ({'allowed_window_utc': {'start': '8am', 'end': '18:00'}}, 'invalid_allowed_window'),
    ({'maximum_daily_amount_usd': 'lots'}, 'invalid_maximum_daily_amount'),
    ({'violation_action': 'WARN'}, 'invalid_violation_action'),
    ({'name': '   '}, 'invalid_name'),
    ({}, 'no_changes'),
])
def test_invalid_policy_edits_are_rejected(api, monkeypatch, payload, code):
    monkeypatch.setattr(pilot, '_require_workspace_permission',
                        lambda c, r, p, **k: ({'id': USER_ID}, {'workspace_id': WS, 'role': 'admin'}))
    with pytest.raises(Exception) as exc:
        endpoints.update_policy_endpoint(POLICY_ID, payload, _Request())
    assert getattr(exc.value, 'detail', {}).get('code') == code
    assert api['conn'].writes == []


# --------------------------------------------------------------------------
# History reporting
# --------------------------------------------------------------------------
def test_an_empty_history_is_reported_as_empty_and_never_fabricated(api):
    payload = endpoints.policy_history_endpoint(POLICY_ID, _Request())
    assert payload['versions'] == []
    assert payload['current_version'] == 7
    assert payload['policy_key'] == 'POL-MINT-007'


def test_history_returns_real_recorded_versions_newest_first(api, monkeypatch):
    api['conn'] = _conn(matchers=[(f'FROM {service.VERSIONS_TABLE}', [
        {'id': 'v8', 'version': 8, 'status': 'ACTIVE', 'snapshot': {}, 'previous_values': {'maximum_daily_amount_usd': '5000000'},
         'new_values': {'maximum_daily_amount_usd': '10000000'}, 'change_summary': 'Maximum issuance changed: 5000000 → 10000000',
         'changed_by_user_id': USER_ID, 'changed_at': NOW},
    ])])
    monkeypatch.setattr(pilot, 'pg_connection', lambda: api['conn'])
    monkeypatch.setattr(
        'services.api.app.governance._lookup_users',
        lambda c, ids: {USER_ID: {'email': 'admin@acme.test', 'full_name': 'Acme Admin'}},
    )
    payload = endpoints.policy_history_endpoint(POLICY_ID, _Request())
    assert len(payload['versions']) == 1
    row = payload['versions'][0]
    assert row['version'] == 8
    assert row['changed_by'] == 'admin@acme.test'
    assert 'Maximum issuance changed' in row['change_summary']
    query, _ = api['conn'].statements_matching(f'FROM {service.VERSIONS_TABLE}')[0]
    assert 'ORDER BY version DESC' in query


# --------------------------------------------------------------------------
# Storage provisioning
# --------------------------------------------------------------------------
def test_missing_policy_storage_is_a_503_never_a_silent_allow(api, monkeypatch):
    api['conn'] = FakeConn(tables_exist=False)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: api['conn'])
    for call in (
        lambda: endpoints.list_policies_endpoint(_Request()),
        lambda: endpoints.policy_detail_endpoint(POLICY_ID, _Request()),
        lambda: endpoints.simulate_endpoint(POLICY_ID, {'operation': 'MINT'}, _Request()),
    ):
        with pytest.raises(Exception) as exc:
            call()
        assert getattr(exc.value, 'status_code', None) == 503


# --------------------------------------------------------------------------
# The list payload
# --------------------------------------------------------------------------
def test_the_list_payload_serves_the_vocabulary_the_simulator_renders(api):
    payload = endpoints.list_policies_endpoint(_Request())
    assert payload['can_manage'] is True
    assert payload['edit_permission'] == 'security.manage'
    vocabulary = payload['vocabulary']
    assert [o['value'] for o in vocabulary['operations']] == list(gpc.OPERATIONS)
    assert [o['value'] for o in vocabulary['settlement_states']] == list(gpc.SETTLEMENT_STATES)
    assert vocabulary['decision_authority'] == 'Deterministic Policy Engine'
    assert vocabulary['ai_authority'] == 'Recommend only'
    policy = payload['policies'][0]
    assert policy['policy_key'] == 'POL-MINT-007'
    assert policy['maximum_daily_amount_usd'] == '10000000.00'
    assert policy['allowed_window_utc'] == {'start': '08:00', 'end': '18:00'}


def test_a_stored_role_the_engine_cannot_evidence_is_kept_not_silently_dropped():
    policy = service.policy_from_row(_policy_row(required_roles=['TREASURY_OPERATOR', 'BOARD_SIGNATORY']))
    assert policy.required_roles == ('TREASURY_OPERATOR', 'BOARD_SIGNATORY')


def test_required_roles_survive_a_json_encoded_column():
    policy = service.policy_from_row(_policy_row(required_roles='["TREASURY_OPERATOR"]'))
    assert policy.required_roles == ('TREASURY_OPERATOR',)


def test_a_stored_money_value_is_decimal_and_never_a_float():
    policy = service.policy_from_row(_policy_row(maximum_daily_amount_usd='10000000.00'))
    assert isinstance(policy.maximum_daily_amount_usd, Decimal)
    assert policy.as_dict()['maximum_daily_amount_usd'] == '10000000.00'


# --------------------------------------------------------------------------
# The Screen 8 execution-gate contract (§14)
# --------------------------------------------------------------------------
def test_the_evaluation_object_screen_8_consumes_is_complete_and_stable(api):
    """Screen 8 gates execution on THIS object and never on an AI response.

    It must be able to read, without inference: which policy and which version
    decided, what the decision was, why, which sign-offs are still outstanding,
    which canonical permission evidences each of them, and who had authority to
    decide. Pinning the exact key set here means a future change to the decision
    payload cannot silently break the gate.
    """
    result = endpoints.simulate_endpoint(POLICY_ID, {
        'operation': 'MINT', 'amount_usd': '5000000', 'operator_id': OPERATOR_ID,
        'business_event': 'SUBSCRIPTION', 'settlement_status': 'CLEARED',
        'compliance_approval': False,
        # Canonical lifecycle identifiers travel through untouched, so the
        # evaluation appends to the existing event instead of creating a new one.
        'event_id': 'EVT-928181', 'asset_id': None, 'incident_id': None,
    }, _Request())

    contract = {
        'evaluation_id', 'policy_id', 'policy_key', 'policy_version', 'decision',
        'reason_codes', 'required_approvals', 'required_roles', 'approval_permissions',
        'checks', 'operation', 'asset_id', 'incident_id', 'canonical_event_id',
        'amount_usd', 'violation_action', 'evaluated_at', 'engine_version',
        'simulation', 'decision_authority', 'ai_authority',
    }
    assert contract <= set(result)

    assert result['decision'] == 'DENY'
    assert result['reason_codes'] == ['COMPLIANCE_APPROVAL_MISSING']
    assert result['required_approvals'] == ['COMPLIANCE_APPROVER']
    assert result['approval_permissions'] == {
        'TREASURY_OPERATOR': 'response.propose',
        'COMPLIANCE_APPROVER': 'response.approve',
    }
    assert result['violation_action'] == 'DENY'
    assert result['canonical_event_id'] == 'EVT-928181'
    # Screen 8's two authority rows come from the decision itself.
    assert result['decision_authority'] == 'Deterministic Policy Engine'
    assert result['ai_authority'] == 'Recommend only'
    # Every approval Screen 8 would demand maps to a permission the workspace
    # RBAC model actually has, so the gate is enforceable rather than decorative.
    for permission in result['approval_permissions'].values():
        assert permission in pilot.WORKSPACE_PERMISSIONS
    assert set(result['required_approvals']) <= set(result['required_roles'])
    assert result['evaluation_id'], 'the decision must be addressable by id'


def test_the_ai_narrative_travels_beside_the_decision_never_inside_it(api):
    result = endpoints.simulate_endpoint(POLICY_ID, {
        'operation': 'MINT', 'amount_usd': '5000000', 'operator_id': OPERATOR_ID,
        'business_event': 'SUBSCRIPTION', 'settlement_status': 'CLEARED',
        'compliance_approval': False,
    }, _Request())
    # With no provider configured the narrative is the deterministic template.
    assert result['ai_explanation_source'] == 'deterministic'
    assert 'Compliance Approver' in result['ai_explanation']
    assert result['ai_explanation_authority'] == 'AI Analysis: Explanation only'
    # ...and the decision beside it is untouched by any narrative field.
    assert result['decision'] == 'DENY'


# --------------------------------------------------------------------------
# Canonical lifecycle identifiers (§13)
# --------------------------------------------------------------------------
def test_lifecycle_identifiers_travel_through_to_the_stored_evaluation(api):
    asset_id = '44444444-4444-4444-4444-444444444444'
    incident_id = '55555555-5555-5555-5555-555555555555'
    result = endpoints.simulate_endpoint(POLICY_ID, {
        'operation': 'MINT', 'amount_usd': '1', 'operator_id': OPERATOR_ID,
        'business_event': 'SUBSCRIPTION', 'settlement_status': 'CLEARED',
        'asset_id': asset_id, 'incident_id': incident_id, 'event_id': 'EVT-928181',
    }, _Request())
    # The evaluation APPENDS to the existing event rather than creating a new one.
    assert result['asset_id'] == asset_id
    assert result['incident_id'] == incident_id
    assert result['canonical_event_id'] == 'EVT-928181'
    _query, params = api['conn'].writes[0]
    assert asset_id in params and incident_id in params and 'EVT-928181' in params


@pytest.mark.parametrize('field', ['asset_id', 'incident_id'])
def test_a_malformed_lifecycle_identifier_is_a_400_not_a_database_error(api, field):
    with pytest.raises(Exception) as exc:
        endpoints.simulate_endpoint(POLICY_ID, {
            'operation': 'MINT', 'amount_usd': '1', field: 'not-a-uuid',
        }, _Request())
    assert getattr(exc.value, 'status_code', None) == 400
    assert getattr(exc.value, 'detail', {}).get('code') == f'invalid_{field}'
    assert api['conn'].writes == []
