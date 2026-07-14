"""Tests: AI recommendation reviews surface in the Response Actions read API.

Accepted / rejected AI investigation recommendations are stored in the canonical
``ai_recommendations`` review table (migration 0123). Before this change the
Response Actions list only read the legacy ``response_actions`` table, so those
human-review decisions were invisible on the Response Actions page.

These tests prove ``list_enforcement_actions`` now also returns the AI reviews as
normalized, immutable, never-executed records — workspace-scoped, distinguishable
from legacy policy-engine actions, and correctly filtered by incident. They follow
the repo's fake-connection unit style (no real DB / model / network).
"""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from services.api.app import ai_triage, pilot


class _Result:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _Conn:
    """Fake connection simulating response_actions + ai_recommendations reads.

    Simulates DB-side WHERE filtering (workspace_id, incident_id, action_id) so
    workspace isolation and incident filtering are exercised, not assumed.
    """

    def __init__(self, *, legacy_rows=None, ai_rows=None, schema_ready=True, audit_rows=None):
        self.executed: list[tuple[str, object]] = []
        self._legacy_rows = legacy_rows or []
        self._ai_rows = ai_rows or []
        self._schema_ready = schema_ready
        self._audit_rows = audit_rows or []

    def execute(self, statement, params=None):
        n = ' '.join(str(statement).split())
        self.executed.append((n, params))

        if 'to_regclass' in n:
            return _Result(row={'present': self._schema_ready})

        if 'FROM response_actions' in n and 'SELECT' in n and 'workspace_id = %s' in n:
            # params: (ws, action_id, action_id, incident_id, incident_id, alert_id, alert_id, status, status, limit)
            ws, action_id, incident_id = params[0], params[1], params[3]
            rows = [r for r in self._legacy_rows if str(r.get('workspace_id')) == str(ws)]
            if action_id is not None:
                rows = [r for r in rows if str(r.get('id')) == str(action_id)]
            if incident_id is not None:
                rows = [r for r in rows if str(r.get('incident_id')) == str(incident_id)]
            return _Result(rows=rows)

        if 'FROM ai_recommendations r' in n and 'JOIN ai_triage_results' in n:
            # params: (ws, incident_id, incident_id, action_id, action_id, limit)
            ws, incident_id, action_id = params[0], params[1], params[3]
            rows = [r for r in self._ai_rows if str(r.get('workspace_id')) == str(ws)]
            if incident_id is not None:
                rows = [r for r in rows if str(r.get('incident_id')) == str(incident_id)]
            if action_id is not None:
                rows = [r for r in rows if str(r.get('recommendation_id')) == str(action_id)]
            return _Result(rows=rows)

        if 'FROM audit_logs' in n:
            return _Result(rows=self._audit_rows)

        return _Result()

    def commit(self):
        return None


@contextmanager
def _fake_pg(conn):
    yield conn


def _bootstrap(monkeypatch, conn, workspace_id='ws-1'):
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_a, **_k: {'id': 'user-1'})
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda *_a, **_k: {'workspace_id': workspace_id})
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)


INCIDENT = 'c537b73f-1976-4a44-b589-946194794399'

_LEGACY_NOTIFY_TEAM = {
    'id': 'act-legacy-1',
    'workspace_id': 'ws-1',
    'incident_id': INCIDENT,
    'alert_id': 'alert-1',
    'action_type': 'notify_team',
    'status': 'recommended',
    'mode': 'simulated',
    'source': 'simulator',
    'created_at': '2026-06-20T00:00:00Z',
}


def _ai_row(**over):
    row = {
        'recommendation_id': 'rec-accepted-1',
        'workspace_id': 'ws-1',
        'incident_id': INCIDENT,
        'triage_result_id': 'res-1',
        'triage_job_id': 'job-1',
        'action_type': 'notify_security_team',
        'runbook_id': 'notify_security_team_v1',
        'reason': 'Confirmed suspicious transfer.',
        'risk_level': 'low',
        'requires_human_approval': True,
        'evidence_refs': ['telemetry:tel-1', 'detection:det-1'],
        'review_state': 'accepted',
        'reviewed_by_user_id': 'user-9',
        'reviewed_at': '2026-07-13T10:00:00Z',
        'review_reason': None,
        'created_at': '2026-07-13T09:00:00Z',
        'provider': 'openai',
        'model': 'gpt-5.6-luna',
        'evidence_snapshot_id': 'snap-1',
        'evidence_snapshot_hash': 'sha256:abc',
        'reviewer_email': 'reviewer@example.com',
    }
    row.update(over)
    return row


def _find(actions, record_type):
    return [a for a in actions if a.get('record_type') == record_type]


# --------------------------------------------------------------------------
# 1-2. Accepted / rejected AI recommendation appears in the action list.
# --------------------------------------------------------------------------
def test_accepted_ai_recommendation_appears(monkeypatch):
    conn = _Conn(legacy_rows=[_LEGACY_NOTIFY_TEAM], ai_rows=[_ai_row()])
    _bootstrap(monkeypatch, conn)
    actions = pilot.list_enforcement_actions(SimpleNamespace(headers={}), incident_id=INCIDENT)['actions']
    ai = _find(actions, 'ai_recommendation_review')
    assert len(ai) == 1
    assert ai[0]['decision'] == 'accepted'
    assert ai[0]['review_state'] == 'accepted'
    assert ai[0]['action_type'] == 'notify_security_team'
    assert ai[0]['title'] == 'Notify security team'


def test_rejected_ai_recommendation_appears(monkeypatch):
    conn = _Conn(ai_rows=[_ai_row(recommendation_id='rec-rej-1', review_state='rejected', review_reason='false positive')])
    _bootstrap(monkeypatch, conn)
    actions = pilot.list_enforcement_actions(SimpleNamespace(headers={}), incident_id=INCIDENT)['actions']
    ai = _find(actions, 'ai_recommendation_review')
    assert len(ai) == 1
    assert ai[0]['decision'] == 'rejected'
    assert ai[0]['review_note'] == 'false positive'


# --------------------------------------------------------------------------
# 3. Pending AI recommendation is present (frontend routes it to Recommended).
# --------------------------------------------------------------------------
def test_pending_ai_recommendation_present(monkeypatch):
    conn = _Conn(ai_rows=[_ai_row(recommendation_id='rec-pending-1', review_state='pending_review', reviewed_by_user_id=None, reviewed_at=None, reviewer_email=None)])
    _bootstrap(monkeypatch, conn)
    actions = pilot.list_enforcement_actions(SimpleNamespace(headers={}))['actions']
    ai = _find(actions, 'ai_recommendation_review')
    assert len(ai) == 1
    assert ai[0]['review_state'] == 'pending_review'
    assert ai[0]['decision'] is None
    assert ai[0]['status'] == 'pending_approval'


# --------------------------------------------------------------------------
# 4-5. Accepted and rejected recommendations are NEVER executed.
# --------------------------------------------------------------------------
@pytest.mark.parametrize('state', ['accepted', 'rejected', 'pending_review'])
def test_reviews_are_never_executed(monkeypatch, state):
    conn = _Conn(ai_rows=[_ai_row(review_state=state)])
    _bootstrap(monkeypatch, conn)
    actions = pilot.list_enforcement_actions(SimpleNamespace(headers={}))['actions']
    ai = _find(actions, 'ai_recommendation_review')[0]
    assert ai['executed'] is False
    assert ai['mode'] == 'review'
    assert ai['simulated'] is False


# --------------------------------------------------------------------------
# 6. Building the review record performs no on-chain / external side effect.
#    (Pure read — no INSERT/UPDATE is ever issued for AI records.)
# --------------------------------------------------------------------------
def test_listing_reviews_issues_no_writes(monkeypatch):
    conn = _Conn(ai_rows=[_ai_row()])
    _bootstrap(monkeypatch, conn)
    pilot.list_enforcement_actions(SimpleNamespace(headers={}), incident_id=INCIDENT)
    for sql, _params in conn.executed:
        assert not sql.startswith('INSERT')
        assert not sql.startswith('UPDATE')
        assert not sql.startswith('DELETE')


# --------------------------------------------------------------------------
# 7-8. Legacy simulator action stays visible and is a distinct source type.
# --------------------------------------------------------------------------
def test_legacy_and_ai_are_distinct_sources(monkeypatch):
    conn = _Conn(legacy_rows=[_LEGACY_NOTIFY_TEAM], ai_rows=[_ai_row()])
    _bootstrap(monkeypatch, conn)
    actions = pilot.list_enforcement_actions(SimpleNamespace(headers={}), incident_id=INCIDENT)['actions']
    legacy = _find(actions, 'response_action')
    ai = _find(actions, 'ai_recommendation_review')
    assert len(legacy) == 1 and len(ai) == 1
    assert legacy[0]['source_type'] == 'policy_engine'
    assert ai[0]['source_type'] == 'ai_investigation'
    # The legacy simulator record is untouched (still a simulator/simulated action).
    assert legacy[0]['action_type'] == 'notify_team'


# --------------------------------------------------------------------------
# 9-10. incident_id filter returns both relevant records; excludes other incidents.
# --------------------------------------------------------------------------
def test_incident_filter_returns_both_and_excludes_others(monkeypatch):
    other_incident = '00000000-0000-0000-0000-0000000000ff'
    conn = _Conn(
        legacy_rows=[_LEGACY_NOTIFY_TEAM, {**_LEGACY_NOTIFY_TEAM, 'id': 'act-other', 'incident_id': other_incident}],
        ai_rows=[_ai_row(), _ai_row(recommendation_id='rec-other', incident_id=other_incident)],
    )
    _bootstrap(monkeypatch, conn)
    actions = pilot.list_enforcement_actions(SimpleNamespace(headers={}), incident_id=INCIDENT)['actions']
    ids = {a.get('id') for a in actions}
    assert 'act-legacy-1' in ids  # legacy for this incident
    assert 'rec-accepted-1' in ids  # AI review for this incident
    assert 'act-other' not in ids  # other incident excluded
    assert 'rec-other' not in ids
    assert all(a.get('incident_id') == INCIDENT for a in actions)


# --------------------------------------------------------------------------
# 11. Cross-workspace review records are never returned.
# --------------------------------------------------------------------------
def test_cross_workspace_reviews_never_returned(monkeypatch):
    conn = _Conn(ai_rows=[
        _ai_row(),  # ws-1
        _ai_row(recommendation_id='rec-ws2', workspace_id='ws-2'),  # different tenant
    ])
    _bootstrap(monkeypatch, conn, workspace_id='ws-1')
    actions = pilot.list_enforcement_actions(SimpleNamespace(headers={}), incident_id=INCIDENT)['actions']
    ai = _find(actions, 'ai_recommendation_review')
    assert [a['id'] for a in ai] == ['rec-accepted-1']
    # The workspace_id bound into the query is the caller's, never the incident's alone.
    ai_query = [p for (sql, p) in conn.executed if 'FROM ai_recommendations r' in sql][0]
    assert ai_query[0] == 'ws-1'


# --------------------------------------------------------------------------
# 12-13. Historical accepted records surface with no backfill and no duplication;
#         re-running the read is idempotent (produces identical results).
# --------------------------------------------------------------------------
def test_historical_reads_are_idempotent_no_duplicates(monkeypatch):
    conn = _Conn(legacy_rows=[_LEGACY_NOTIFY_TEAM], ai_rows=[_ai_row()])
    _bootstrap(monkeypatch, conn)
    first = pilot.list_enforcement_actions(SimpleNamespace(headers={}), incident_id=INCIDENT)['actions']
    second = pilot.list_enforcement_actions(SimpleNamespace(headers={}), incident_id=INCIDENT)['actions']
    assert [a['id'] for a in first] == [a['id'] for a in second]
    ai_ids = [a['id'] for a in first if a.get('record_type') == 'ai_recommendation_review']
    assert ai_ids == ['rec-accepted-1']
    assert len(ai_ids) == len(set(ai_ids))  # no duplicate rows


# --------------------------------------------------------------------------
# 14-15. Reviewer / reviewed_at preserved; evidence snapshot linkage serialized.
# --------------------------------------------------------------------------
def test_reviewer_timestamp_and_evidence_snapshot_serialized(monkeypatch):
    conn = _Conn(ai_rows=[_ai_row()])
    _bootstrap(monkeypatch, conn)
    ai = _find(pilot.list_enforcement_actions(SimpleNamespace(headers={}))['actions'], 'ai_recommendation_review')[0]
    assert ai['reviewer_id'] == 'user-9'
    assert ai['reviewer_email'] == 'reviewer@example.com'
    assert ai['reviewed_at'] == '2026-07-13T10:00:00Z'
    assert ai['evidence_snapshot_id'] == 'snap-1'
    assert ai['evidence_snapshot_hash'] == 'sha256:abc'
    assert ai['evidence_refs'] == ['telemetry:tel-1', 'detection:det-1']
    assert ai['evidence_refs_count'] == 2
    assert ai['provider'] == 'openai'
    assert ai['model'] == 'gpt-5.6-luna'
    assert ai['triage_job_id'] == 'job-1'
    assert ai['triage_result_id'] == 'res-1'


# --------------------------------------------------------------------------
# Fail-closed: when the AI schema is absent, no AI records (and no crash).
# --------------------------------------------------------------------------
def test_schema_absent_returns_only_legacy(monkeypatch):
    conn = _Conn(legacy_rows=[_LEGACY_NOTIFY_TEAM], ai_rows=[_ai_row()], schema_ready=False)
    _bootstrap(monkeypatch, conn)
    actions = pilot.list_enforcement_actions(SimpleNamespace(headers={}), incident_id=INCIDENT)['actions']
    assert _find(actions, 'ai_recommendation_review') == []
    assert len(_find(actions, 'response_action')) == 1


# --------------------------------------------------------------------------
# Status filter: an explicit pending filter keeps pending reviews and drops decided
# ones; an executed filter drops all reviews (none are executed).
# --------------------------------------------------------------------------
def test_status_filter_pending_keeps_only_pending_reviews(monkeypatch):
    conn = _Conn(ai_rows=[
        _ai_row(recommendation_id='rec-p', review_state='pending_review'),
        _ai_row(recommendation_id='rec-a', review_state='accepted'),
    ])
    _bootstrap(monkeypatch, conn)
    actions = pilot.list_enforcement_actions(SimpleNamespace(headers={}), status_value='pending')['actions']
    ai_ids = [a['id'] for a in actions if a.get('record_type') == 'ai_recommendation_review']
    assert ai_ids == ['rec-p']


def test_status_filter_executed_drops_all_reviews(monkeypatch):
    conn = _Conn(ai_rows=[_ai_row(review_state='accepted')])
    _bootstrap(monkeypatch, conn)
    actions = pilot.list_enforcement_actions(SimpleNamespace(headers={}), status_value='executed')['actions']
    assert _find(actions, 'ai_recommendation_review') == []


# --------------------------------------------------------------------------
# 16. Audit source is no longer "Unknown source" for recommendation decisions.
# --------------------------------------------------------------------------
def test_resolve_audit_evidence_source_for_recommendation_decisions():
    assert pilot._resolve_audit_evidence_source('incident.recommendation.accepted', {}) == 'ai_investigation'
    assert pilot._resolve_audit_evidence_source('incident.recommendation.rejected', {}) == 'ai_investigation'
    # Metadata linkage alone (older/renamed actions) still resolves truthfully.
    assert pilot._resolve_audit_evidence_source('something.else', {'recommendation_id': 'rec-1'}) == 'ai_investigation'
    # Unrelated audit events keep their existing (None → unchanged) source.
    assert pilot._resolve_audit_evidence_source('workspace.member.invited', {}) is None


# --------------------------------------------------------------------------
# Shared truth: accept/reject returns the normalized review record (not just a
# terminal flag) so the AI panel and Response Actions agree without optimistic state.
# --------------------------------------------------------------------------
class _ReviewConn:
    def __init__(self, rec_row):
        self._rec_row = rec_row
        self.updates: list = []

    def execute(self, statement, params=None):
        n = ' '.join(str(statement).split())
        if 'FROM ai_recommendations WHERE id' in n:
            return _Result(row=self._rec_row)
        if n.startswith('UPDATE ai_recommendations'):
            self.updates.append(params)
            return _Result()
        return _Result()

    def commit(self):
        return None


def _bootstrap_review(monkeypatch, conn):
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)
    monkeypatch.setattr(ai_triage, 'publish_incident_event', lambda *_a, **_k: True)
    monkeypatch.setattr(pilot, '_require_workspace_permission',
                        lambda *_a, **_k: ({'id': 'user-9'}, {'workspace_id': 'ws-1', 'role': 'owner'}))


def test_accept_returns_normalized_review_record(monkeypatch):
    conn = _ReviewConn({'id': 'rec-accepted-1', 'incident_id': INCIDENT, 'action_type': 'notify_security_team',
                        'runbook_id': 'notify_security_team_v1', 'review_state': 'pending_review'})
    _bootstrap_review(monkeypatch, conn)
    out = ai_triage.approve_recommendation(INCIDENT, 'rec-accepted-1', {'reason': 'confirmed'}, SimpleNamespace(headers={}, client=SimpleNamespace(host='127.0.0.1'), method='POST'))
    assert out['decision'] == 'accepted'
    assert out['review_state'] == 'accepted'
    assert out['executed'] is False
    assert out['record_type'] == 'ai_recommendation_review'
    assert out['source_type'] == 'ai_investigation'
    assert out['status'] == 'accepted'
    assert out['reviewed_by_user_id'] == 'user-9'


def test_reject_returns_not_executed_review_record(monkeypatch):
    conn = _ReviewConn({'id': 'rec-rej-1', 'incident_id': INCIDENT, 'action_type': 'increase_monitoring',
                        'runbook_id': None, 'review_state': 'pending_review'})
    _bootstrap_review(monkeypatch, conn)
    out = ai_triage.reject_recommendation(INCIDENT, 'rec-rej-1', {}, SimpleNamespace(headers={}, client=SimpleNamespace(host='127.0.0.1'), method='POST'))
    assert out['decision'] == 'rejected'
    assert out['status'] == 'rejected'
    assert out['executed'] is False


def test_list_audit_events_labels_recommendation_source(monkeypatch):
    audit_rows = [{
        'id': 'aud-1', 'action': 'incident.recommendation.accepted', 'entity_type': 'incident',
        'entity_id': INCIDENT, 'user_id': 'user-9', 'ip_address': '127.0.0.1',
        'metadata': {'recommendation_id': 'rec-accepted-1', 'decision': 'accepted', 'executed': False},
        'created_at': '2026-07-13T10:00:00Z',
    }]
    conn = _Conn(audit_rows=audit_rows)
    _bootstrap(monkeypatch, conn)
    events = pilot.list_audit_events(SimpleNamespace(headers={}))['events']
    assert events[0]['evidence_source_type'] == 'ai_investigation'
    assert events[0]['evidence_source'] == 'ai_investigation'
