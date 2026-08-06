"""Canonical Action History event DTO (Screen 8, read path).

The reported problem: after a successful approval the Action History row still showed a
truncated generic action name, a raw actor UUID, an em-dash decision, no lifecycle
transition, and no approval progress — because React had to reconstruct everything from a
raw event payload. These tests prove the BACKEND now returns ONE fully-formed,
operator-ready DTO per ``action_history`` row: resolved actor identity, human event
label, readable action title, previous -> new lifecycle, decision, quorum progress,
canonical routes, and sanitized metadata — newest-first, workspace-scoped, and with the
history filters kept independent of the Recommended-Actions active-action predicates.
Repo fake-connection unit style (no real DB / model / network).
"""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

from services.api.app import pilot


INCIDENT = 'c537b73f-1976-4a44-b589-946194794399'
ACTOR_UUID = '11111111-1111-4111-8111-111111111111'
ACTION_ID = 'b2222222-2222-4222-8222-222222222222'


def _approval_row(**over):
    row = {
        'id': 'evt-approve-1',
        'actor_type': 'user',
        'actor_id': ACTOR_UUID,
        'object_type': 'response_action',
        'object_id': ACTION_ID,
        'action_type': 'response_action.approved',
        'timestamp': '2026-08-05T10:15:00+00:00',
        'details_json': {
            'event_type': 'response_action_approved',
            'display_label': 'Response Action Approved',
            'type_label': 'Action Approval',
            'action_id': ACTION_ID,
            'action_title': 'Notify security team',
            'incident_id': INCIDENT,
            'actor': 'decoda.guard@gmail.com',
            'decision': 'approved',
            'previous_lifecycle': 'Awaiting Approval',
            'new_lifecycle': 'Approved',
            'approved_count': 1,
            'required_quorum': 1,
            'approval_progress_label': '1 of 1',
            'policy': 'owner_admin_single_approver',
            'result_summary': 'Success',
        },
    }
    row.update(over)
    return row


def _dto(row, *, actors=None, action_types=None):
    return pilot.build_action_history_event_dto(
        row,
        actor_identities=actors or {ACTOR_UUID: {'email': 'decoda.guard@gmail.com', 'full_name': 'Decoda Guard'}},
        action_types=action_types or {ACTION_ID: 'notify_team'},
    )


# 9.1 — the approval event carries the resolved actor DISPLAY identity, and the raw UUID
#       is kept only as a technical field (never the primary label).
def test_dto_includes_actor_display_identity():
    dto = _dto(_approval_row())
    assert dto['actor_email'] == 'decoda.guard@gmail.com'
    assert dto['actor_display_name'] == 'Decoda Guard'
    assert dto['actor_id'] == ACTOR_UUID
    assert dto['actor_display_name'] != dto['actor_id']


# 9.2 — the approval event carries previous + new lifecycle states (key + label).
def test_dto_includes_previous_and_new_states():
    dto = _dto(_approval_row())
    assert dto['previous_state_label'] == 'Awaiting Approval'
    assert dto['new_state_label'] == 'Approved'
    assert dto['previous_state'] == 'awaiting_approval'
    assert dto['new_state'] == 'approved'


# 9.3 — the approval event carries decision Approved (value + label).
def test_dto_includes_decision_approved():
    dto = _dto(_approval_row())
    assert dto['decision'] == 'approved'
    assert dto['decision_label'] == 'Approved'


# 9.3b — a PRE-ENRICHMENT sparse approval row (only counts persisted) still projects a
#        truthful decision + previous->new transition + progress. An Action Approval event
#        NEVER reads "Not applicable" for its decision — it is a domain invariant, recovered
#        canonically at projection time, not fabricated.
def test_dto_derives_canonical_approval_fields_for_sparse_row():
    dto = _dto(_approval_row(details_json={
        'subject_domain': 'ai_recommendation', 'action_version': 1,
        'approved_count': 1, 'required_quorum': 1,
    }))
    assert dto['decision'] == 'approved'
    assert dto['decision_label'] == 'Approved'
    assert dto['previous_state_label'] == 'Awaiting Approval'
    assert dto['new_state_label'] == 'Approved'
    assert dto['approval_progress_label'] == '1 of 1'
    # action_id falls back to the object id (a response_action row) so the row stays navigable.
    assert dto['action_id'] == ACTION_ID


# 9.3c — the approval policy is surfaced as BOTH a stored id and an operator-facing label.
def test_dto_exposes_policy_id_and_label():
    dto = _dto(_approval_row())
    assert dto['approval_policy_id'] == 'owner_admin_single_approver'
    assert dto['approval_policy_label'] == 'Owner/Admin single approver'
    # A non-approval / unknown-policy row never invents a policy.
    bare = _dto(_approval_row(details_json={'event_type': 'response_action_approved'}))
    assert bare['approval_policy_id'] is None
    assert bare['approval_policy_label'] is None


# 9.4 — the approval event carries progress 1 of 1.
def test_dto_includes_progress_one_of_one():
    dto = _dto(_approval_row())
    assert dto['approval_count'] == 1
    assert dto['required_approval_count'] == 1
    assert dto['approval_progress_label'] == '1 of 1'


# 9.5 — the approval event carries a READABLE, canonical action title (never a truncated
#       generic label), resolved from the action_type.
def test_dto_includes_readable_action_title():
    dto = _dto(_approval_row())
    assert dto['action_title'] == 'Notify Security Team'
    assert dto['action_key'] == 'notify_team'
    assert 'Response Action A' not in str(dto['action_title'])


# 9.5b — the incident short id + canonical routes are present.
def test_dto_includes_short_id_and_routes():
    dto = _dto(_approval_row())
    assert dto['incident_short_id'] == 'INC-c537b73f'
    assert dto['action_route'] == f'/response-actions?action_id={ACTION_ID}'
    assert dto['incident_route'] == f'/incidents/{INCIDENT}'
    assert dto['evidence_route'] == f'/evidence?incident_id={INCIDENT}'
    assert dto['audit_route'] == '/history?event_id=evt-approve-1'


# 9.6 — internal event keys map to canonical operator display labels (one backend map),
#       and a raw snake_case / dotted key is never returned.
def test_internal_event_keys_map_to_labels():
    cases = {
        'response_action_created': 'Response Action Created',
        'response_action_approved': 'Response Action Approved',
        'response_action_rejected': 'Response Action Rejected',
        'response_action_approval_blocked': 'Approval Attempt Blocked',
        'response_action_simulation_started': 'Simulation Started',
        'response_action_simulation_passed': 'Simulation Passed',
        'response_action_execution_started': 'Execution Started',
        'response_action_executed': 'Response Action Executed',
        'recommendation_reviewed': 'AI Recommendation Reviewed',
    }
    for key, label in cases.items():
        assert pilot.action_history_event_label(key) == label
    # Dotted persisted action_type -> canonical event_type -> label.
    assert (
        pilot.action_history_event_label(
            pilot._canonical_action_history_event_type('incident.response_action_created', {})
        )
        == 'Response Action Added to Incident'
    )
    # An unknown key is humanised, never returned raw.
    humanised = pilot.action_history_event_label('some_new_future_event')
    assert '_' not in humanised


# 9.6b — the DTO label + type never contain a raw enum separator.
def test_dto_labels_are_never_raw_enums():
    dto = _dto(_approval_row())
    assert dto['event_label'] == 'Response Action Approved'
    assert '_' not in dto['event_label'] and '.' not in dto['event_label']
    assert dto['type_label'] == 'Action Approval'
    assert '_' not in dto['type_label']


# 9.7 — a recommendation review is a SEPARATE event type/label, never the approval.
def test_recommendation_review_is_a_separate_event():
    assert pilot.action_history_event_label('recommendation_reviewed') == 'AI Recommendation Reviewed'
    assert pilot.action_history_event_label('response_action_approved') == 'Response Action Approved'
    assert (
        pilot.action_history_event_label('recommendation_reviewed')
        != pilot.action_history_event_label('response_action_approved')
    )


# 9.10 — the DTO uses the PERSISTED event timestamp, never the current page-load time.
def test_dto_preserves_persisted_timestamp():
    dto = _dto(_approval_row(timestamp='2026-08-05T10:15:00+00:00'))
    assert dto['occurred_at'] == '2026-08-05T10:15:00+00:00'
    assert dto['timestamp'] == '2026-08-05T10:15:00+00:00'


# 9.12 — sensitive MFA / authentication details are excluded from history metadata.
def test_sensitive_mfa_metadata_is_excluded():
    row = _approval_row()
    row['details_json'] = {
        **row['details_json'],
        'mfa_totp_secret': 'JBSWY3DPEHPK3PXP',
        'session_token': 'tok_abc',
        'nested': {'password': 'hunter2', 'authenticator_code': '123456', 'safe': 'keep-me'},
    }
    dto = _dto(row)
    meta = dto['event_metadata']
    assert 'mfa_totp_secret' not in meta
    assert 'session_token' not in meta
    assert 'password' not in meta['nested']
    assert 'authenticator_code' not in meta['nested']
    # Non-sensitive keys survive.
    assert meta['nested']['safe'] == 'keep-me'
    assert meta['decision'] == 'approved'
    # The backward-compat details_json is ALSO sanitized (no secret), while keeping the
    # correlation fields existing consumers (e.g. Threat Operations panel) match on.
    assert 'mfa_totp_secret' not in dto['details_json']
    assert 'session_token' not in dto['details_json']
    assert dto['details_json']['incident_id'] == INCIDENT


# 9.12b — an unresolved actor never leaks the UUID as the display name.
def test_unresolved_actor_never_shows_uuid_as_name():
    dto = pilot.build_action_history_event_dto(
        _approval_row(details_json={'event_type': 'response_action_approved'}),
        actor_identities={},  # actor not resolvable
        action_types={},
    )
    assert dto['actor_display_name'] != dto['actor_id']
    assert dto['actor_display_name'] in {'Operator', 'System'}


# ── list_action_history: ordering, isolation, filters (fake connection) ───────────

class _Result:
    def __init__(self, rows=None):
        self._rows = rows or []

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _HistoryConn:
    def __init__(self, *, history_rows, users=None, action_types=None):
        self.executed: list[tuple[str, object]] = []
        self._history_rows = history_rows
        self._users = users or []
        self._action_types = action_types or []

    def execute(self, statement, params=None):
        n = ' '.join(str(statement).split())
        self.executed.append((n, params))
        if 'FROM action_history' in n:
            return _Result(rows=[dict(r) for r in self._history_rows])
        if 'FROM users WHERE id = ANY' in n:
            return _Result(rows=[dict(u) for u in self._users])
        if 'FROM response_actions WHERE workspace_id' in n:
            return _Result(rows=[dict(a) for a in self._action_types if a.get('_table') == 'response_actions'])
        if 'FROM ai_recommendations WHERE workspace_id' in n:
            return _Result(rows=[dict(a) for a in self._action_types if a.get('_table') == 'ai_recommendations'])
        return _Result()

    def commit(self):
        return None


def _patch_list(monkeypatch, conn):
    @contextmanager
    def _fake_pg():
        yield conn

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_a, **_k: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_a, **_k: {'id': 'user-1'})
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda *_a, **_k: {'workspace_id': 'ws-1'})


def _req():
    return SimpleNamespace(headers={'x-workspace-id': 'ws-1'}, client=SimpleNamespace(host='127.0.0.1'), method='GET')


def _review_row():
    return {
        'id': 'evt-review-1', 'actor_type': 'user', 'actor_id': ACTOR_UUID,
        'object_type': 'response_action', 'object_id': ACTION_ID,
        'action_type': 'recommendation_reviewed',
        'timestamp': '2026-07-14T09:00:00+00:00',
        'details_json': {'event_type': 'recommendation_reviewed', 'decision': 'accepted'},
    }


# 9.8 — ordering is newest-first by the PERSISTED timestamp with a deterministic
#       secondary key, and the just-completed approval appears first.
def test_history_orders_newest_first_deterministically(monkeypatch):
    conn = _HistoryConn(
        history_rows=[_approval_row(), _review_row()],  # backend returns DESC-ordered
        users=[{'id': ACTOR_UUID, 'email': 'decoda.guard@gmail.com', 'full_name': 'Decoda Guard'}],
        action_types=[{'_table': 'response_actions', 'id': ACTION_ID, 'action_type': 'notify_team'}],
    )
    _patch_list(monkeypatch, conn)
    result = pilot.list_action_history(_req())
    events = result['events']
    # The approval (Aug 5) is first, the review (Jul 14) second.
    assert events[0]['event_type'] == 'response_action_approved'
    assert events[1]['event_type'] == 'recommendation_reviewed'
    # Deterministic ordering is enforced in SQL.
    history_sql = next(sql for sql, _ in conn.executed if 'FROM action_history' in sql)
    assert 'ORDER BY timestamp DESC, id DESC' in history_sql


# 9.9 — the history query NEVER inherits the Recommended-Actions active-action predicate,
#       and post-enrichment filters match the canonical DTO fields.
def test_history_filters_do_not_inherit_active_action_predicates(monkeypatch):
    conn = _HistoryConn(
        history_rows=[_approval_row(), _review_row()],
        users=[{'id': ACTOR_UUID, 'email': 'decoda.guard@gmail.com', 'full_name': 'Decoda Guard'}],
        action_types=[{'_table': 'response_actions', 'id': ACTION_ID, 'action_type': 'notify_team'}],
    )
    _patch_list(monkeypatch, conn)
    pilot.list_action_history(_req())
    history_sql = next(sql for sql, _ in conn.executed if 'FROM action_history' in sql)
    for forbidden in ('requires_approval', 'approval_status', 'lifecycle'):
        assert forbidden not in history_sql
    # A dedicated event-type filter keeps only matching events — completed approvals and
    # reviews are both reachable, never hidden by an active-action predicate.
    approved = pilot.list_action_history(_req(), event_type='response_action_approved')['events']
    assert [e['event_type'] for e in approved] == ['response_action_approved']
    reviews = pilot.list_action_history(_req(), event_type='recommendation_reviewed')['events']
    assert [e['event_type'] for e in reviews] == ['recommendation_reviewed']


# 9.11 — workspace isolation: the query is scoped to the caller's workspace, and the
#        actor/action_type resolution stays workspace-scoped.
def test_history_enforces_workspace_isolation(monkeypatch):
    conn = _HistoryConn(
        history_rows=[_approval_row()],
        users=[{'id': ACTOR_UUID, 'email': 'decoda.guard@gmail.com', 'full_name': 'Decoda Guard'}],
        action_types=[{'_table': 'response_actions', 'id': ACTION_ID, 'action_type': 'notify_team'}],
    )
    _patch_list(monkeypatch, conn)
    pilot.list_action_history(_req())
    history_sql, history_params = next((sql, p) for sql, p in conn.executed if 'FROM action_history' in sql)
    assert 'workspace_id = %s' in history_sql
    assert history_params[0] == 'ws-1'
    # action_type resolution is workspace-scoped.
    at_sql = next(sql for sql, _ in conn.executed if 'FROM response_actions WHERE workspace_id' in sql)
    assert 'workspace_id = %s' in at_sql


# 9.9b — the history filters are pushed INTO the SQL WHERE clause (before LIMIT), so a
#        matching older audit row can never be hidden behind newer non-matching rows.
def test_history_filters_are_applied_in_sql_before_limit(monkeypatch):
    conn = _HistoryConn(history_rows=[_approval_row()])
    _patch_list(monkeypatch, conn)
    pilot.list_action_history(
        _req(), event_type='response_action_approved', result='Success', incident_id=INCIDENT,
    )
    history_sql = next(sql for sql, _ in conn.executed if 'FROM action_history' in sql)
    # The filter predicates live in the WHERE clause, ahead of the LIMIT.
    where = history_sql[: history_sql.index('LIMIT')]
    assert "details_json->>'incident_id'" in where
    assert "details_json->>'event_type'" in where
    assert "details_json->>'result'" in where or "details_json->>'result_summary'" in where
    assert 'ORDER BY timestamp DESC, id DESC' in history_sql
    # The event_type filter also matches by the mapped dotted action_type candidates.
    params_for_history = next(p for sql, p in conn.executed if 'FROM action_history' in sql)
    assert 'response_action.approved' in list(params_for_history[17])


# 9.11b — an incident filter narrows to the matching incident only.
def test_history_incident_filter(monkeypatch):
    other = _approval_row(id='evt-2', details_json={
        'event_type': 'response_action_approved', 'incident_id': 'aaaaaaaa-0000-4000-8000-000000000000',
    })
    conn = _HistoryConn(
        history_rows=[_approval_row(), other],
        users=[{'id': ACTOR_UUID, 'email': 'decoda.guard@gmail.com', 'full_name': 'Decoda Guard'}],
    )
    _patch_list(monkeypatch, conn)
    events = pilot.list_action_history(_req(), incident_id=INCIDENT)['events']
    assert [e['incident_id'] for e in events] == [INCIDENT]
