"""Tests for POST /incidents/{incident_id}/response-actions/recommend endpoint.

Covers the context-specific, idempotent Playbook Execution Agent recommendation
generation:
- Generates an incident-SPECIFIC plan (not a single hardcoded notify_team)
- Deduplicates on (incident_id, action_type, mode='recommended') — re-running
  creates no duplicates
- Fresh recommendations record execution_state='recommended' (not 'simulated')
  and evidence_source='incident_context' (never simulator/fake evidence)
- Returns an anchor response_action_id + created_count + action plan
- Returns 404 when incident not found in workspace
- The pure planner is deterministic and context-specific
- Regression: INSERT uses named %(name)s params so placeholder/param count
  cannot mismatch
"""
from __future__ import annotations

import inspect
import re
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from services.api.app import pilot


class _Result:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _TrackingConnection:
    """Fake connection.

    ``existing_by_type`` maps action_type -> existing row (or None). Any type not
    present is treated as "no existing action" so an INSERT happens.
    """

    def __init__(self, incident_row=None, alert_row=None, existing_by_type=None,
                 insert_conflicts=False):
        self.executed: list[tuple[str, object]] = []
        self._incident_row = incident_row
        self._alert_row = alert_row
        self._existing_by_type = existing_by_type or {}
        #: When True every INSERT hits the unique index and returns no row, as it
        #: would for the loser of a concurrent recommend race.
        self.insert_conflicts = insert_conflicts
        self.committed = False

    def execute(self, statement, params=None):
        normalized = ' '.join(str(statement).split())
        self.executed.append((normalized, params))
        if 'FROM incidents WHERE id' in normalized:
            return _Result(self._incident_row)
        if 'FROM alerts WHERE id' in normalized:
            return _Result(self._alert_row)
        if 'FROM response_actions' in normalized and "mode = 'recommended'" in normalized:
            # params = (incident_id, workspace_id, action_type)
            action_type = params[2] if isinstance(params, (tuple, list)) and len(params) >= 3 else None
            return _Result(self._existing_by_type.get(action_type))
        if 'INSERT INTO response_actions' in normalized:
            # The INSERT is `... ON CONFLICT DO NOTHING RETURNING id`, so the
            # driver hands back the new row — or NOTHING when a concurrent caller
            # already inserted it. `insert_conflicts` models that second case,
            # which is the race the dedup index exists to close.
            if self.insert_conflicts:
                return _Result(None)
            return _Result({'id': (params or {}).get('id') if isinstance(params, dict) else None})
        return _Result()

    def commit(self):
        self.committed = True


@contextmanager
def _fake_pg(connection):
    yield connection


def _bootstrap(monkeypatch, connection):
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(connection))
    monkeypatch.setattr(
        pilot,
        '_require_workspace_permission',
        lambda *_a, **_k: ({'id': 'user-1'}, {'workspace_id': 'ws-1'}),
    )
    monkeypatch.setattr(pilot, 'write_action_history', lambda *_a, **_k: None)


def _inserts(conn):
    return [p for stmt, p in conn.executed if 'INSERT INTO response_actions' in stmt]


# ── Pure planner: deterministic + context-specific ───────────────────────────

def test_plan_is_deterministic_for_same_input():
    a = pilot.recommended_action_plan_for_incident(
        severity='high', event_type='unauthorized_mint', summary='unauthorized mint detected')
    b = pilot.recommended_action_plan_for_incident(
        severity='high', event_type='unauthorized_mint', summary='unauthorized mint detected')
    assert a == b


def test_plan_is_context_specific_not_just_notify_team():
    mint = pilot.recommended_action_plan_for_incident(
        severity='critical', event_type='unauthorized_mint', summary='unauthorized mint')
    provider = pilot.recommended_action_plan_for_incident(
        severity='high', event_type='rpc_provider_degraded', summary='rpc provider latency')
    credential = pilot.recommended_action_plan_for_incident(
        severity='high', event_type='credential_compromise', summary='compromised api credential')

    # Each plan differs and reflects its incident context.
    assert 'snapshot_chain_state' in mint and 'escalate_multisig' in mint
    assert 'isolate_provider' in provider and 'increase_monitoring' in provider
    assert 'revoke_approval' in credential and 'rotate_credential' in credential
    # Provider latency must NOT recommend destructive asset containment.
    assert 'pause_asset_transfers' not in provider
    assert 'pause_mint_redeem' not in provider
    assert 'freeze_wallet' not in provider
    # They are genuinely distinct plans, not one repeated action.
    assert mint != provider != credential


def test_plan_always_preserves_evidence_first_and_notifies():
    plan = pilot.recommended_action_plan_for_incident(
        severity='low', event_type='misc', summary='something happened')
    assert plan[0] == 'preserve_evidence'
    assert 'notify_team' in plan


def test_plan_escalates_to_issuer_only_for_high_severity():
    high = pilot.recommended_action_plan_for_incident(severity='critical', event_type='x', summary='x')
    low = pilot.recommended_action_plan_for_incident(severity='low', event_type='x', summary='x')
    assert 'escalate_to_issuer' in high
    assert 'escalate_to_issuer' not in low


# ── Endpoint: creates a context-specific plan (multiple actions) ─────────────

def test_recommend_creates_context_specific_plan(monkeypatch):
    conn = _TrackingConnection(
        incident_row={'id': 'inc-1', 'source_alert_id': None, 'severity': 'critical',
                      'event_type': 'unauthorized_mint', 'summary': 'unauthorized mint detected'},
    )
    _bootstrap(monkeypatch, conn)

    result = pilot.recommend_response_action_for_incident(
        'inc-1', SimpleNamespace(headers={'x-workspace-id': 'ws-1'}))

    assert result['incident_id'] == 'inc-1'
    assert result['created'] is True
    assert result['created_count'] >= 3  # not a single hardcoded action
    assert result['response_action_id']
    inserted_types = [row['action_type'] for row in _inserts(conn)]
    assert inserted_types[0] == 'preserve_evidence'
    assert 'notify_team' in inserted_types
    assert 'snapshot_chain_state' in inserted_types  # mint-specific
    assert conn.committed


# ── Fresh recommendations are truthful: not simulated, incident-context ──────

def test_recommend_records_truthful_state_and_provenance(monkeypatch):
    conn = _TrackingConnection(
        incident_row={'id': 'inc-2', 'source_alert_id': None, 'severity': 'medium',
                      'event_type': 'misc', 'summary': 'noise'},
    )
    _bootstrap(monkeypatch, conn)

    pilot.recommend_response_action_for_incident(
        'inc-2', SimpleNamespace(headers={'x-workspace-id': 'ws-1'}))

    for row in _inserts(conn):
        # A freshly recommended action has NOT been simulated.
        assert row['execution_state'] == 'recommended'
        # Evidence provenance is the incident context, never simulator/fake data.
        assert 'incident_context' in row['execution_metadata']
        assert 'simulator' not in row['execution_metadata']


# ── Idempotency: a second call creates no duplicates ─────────────────────────

def test_recommend_idempotent_no_duplicates(monkeypatch):
    """Second call for the same unchanged incident inserts nothing new."""
    incident = {'id': 'inc-idem', 'source_alert_id': None, 'severity': 'high',
                'event_type': 'rpc_provider_degraded', 'summary': 'rpc latency'}
    stored: dict[str, dict] = {}

    class _IdempotentConnection:
        def __init__(self):
            self.executed: list = []
            self.committed = False

        def execute(self, statement, params=None):
            normalized = ' '.join(str(statement).split())
            self.executed.append((normalized, params))
            if 'FROM incidents WHERE id' in normalized:
                return _Result(incident)
            if 'FROM alerts WHERE id' in normalized:
                return _Result(None)
            if 'FROM response_actions' in normalized and "mode = 'recommended'" in normalized:
                action_type = params[2]
                return _Result(stored.get(action_type))
            if 'INSERT INTO response_actions' in normalized:
                assert isinstance(params, dict)
                stored[params['action_type']] = {'id': params['id']}
                # `RETURNING id` hands the new row back to the caller.
                return _Result({'id': params['id']})
            return _Result()

        def commit(self):
            self.committed = True

    conn = _IdempotentConnection()
    _bootstrap(monkeypatch, conn)
    req = SimpleNamespace(headers={'x-workspace-id': 'ws-1'})

    r1 = pilot.recommend_response_action_for_incident('inc-idem', req)
    inserts_after_first = sum(1 for s, _ in conn.executed if 'INSERT INTO response_actions' in s)
    r2 = pilot.recommend_response_action_for_incident('inc-idem', req)
    inserts_after_second = sum(1 for s, _ in conn.executed if 'INSERT INTO response_actions' in s)

    assert r1['created'] is True
    assert r2['created'] is False
    assert r2['created_count'] == 0
    # No new INSERTs on the second call.
    assert inserts_after_second == inserts_after_first
    # Anchor id is stable across calls.
    assert r1['response_action_id'] == r2['response_action_id']


# ── Already-existing subset is respected (partial idempotency) ───────────────

def test_recommend_only_inserts_missing_actions(monkeypatch):
    conn = _TrackingConnection(
        incident_row={'id': 'inc-3', 'source_alert_id': None, 'severity': 'low',
                      'event_type': 'misc', 'summary': 'x'},
        existing_by_type={'preserve_evidence': {'id': 'existing-pe'}},
    )
    _bootstrap(monkeypatch, conn)

    result = pilot.recommend_response_action_for_incident(
        'inc-3', SimpleNamespace(headers={'x-workspace-id': 'ws-1'}))

    inserted_types = [row['action_type'] for row in _inserts(conn)]
    assert 'preserve_evidence' not in inserted_types  # already existed → reused
    assert 'notify_team' in inserted_types  # missing → created
    assert result['created_count'] == len(inserted_types)


# ── 404 when incident not found ──────────────────────────────────────────────

def test_recommend_returns_404_for_missing_incident(monkeypatch):
    conn = _TrackingConnection(incident_row=None)
    _bootstrap(monkeypatch, conn)

    with pytest.raises(HTTPException) as exc_info:
        pilot.recommend_response_action_for_incident(
            'nonexistent-id', SimpleNamespace(headers={'x-workspace-id': 'ws-1'}))

    assert exc_info.value.status_code == 404
    assert _inserts(conn) == []


# ── Links alert_id from incident's source_alert_id ───────────────────────────

def test_recommend_links_alert_id_from_incident(monkeypatch):
    conn = _TrackingConnection(
        incident_row={'id': 'inc-4', 'source_alert_id': 'alert-99', 'severity': 'high',
                      'event_type': 'suspicious_transfer', 'summary': 'suspicious transfer'},
        alert_row={'alert_type': 'suspicious_transfer', 'title': 'Suspicious transfer'},
    )
    _bootstrap(monkeypatch, conn)

    pilot.recommend_response_action_for_incident(
        'inc-4', SimpleNamespace(headers={'x-workspace-id': 'ws-1'}))

    for row in _inserts(conn):
        assert row['alert_id'] == 'alert-99'


# ── INSERT uses named params — placeholder/param count cannot mismatch ────────

def test_recommend_insert_uses_named_params_and_columns_match(monkeypatch):
    conn = _TrackingConnection(
        incident_row={'id': 'inc-p', 'source_alert_id': None, 'severity': 'low',
                      'event_type': 'misc', 'summary': 'x'},
    )
    _bootstrap(monkeypatch, conn)

    pilot.recommend_response_action_for_incident(
        'inc-p', SimpleNamespace(headers={'x-workspace-id': 'ws-p'}))

    inserts = _inserts(conn)
    assert inserts, 'expected at least one INSERT'
    for params in inserts:
        assert isinstance(params, dict), 'INSERT params must be a dict (named)'

    src = inspect.getsource(pilot.recommend_response_action_for_incident)
    insert_match = re.search(r'INSERT INTO response_actions\s*\((.*?)\)\s*VALUES', src, re.DOTALL)
    assert insert_match, 'Could not find INSERT column list in source'
    columns = [c.strip() for c in insert_match.group(1).split(',')]
    assert set(inserts[0].keys()) == set(columns)
