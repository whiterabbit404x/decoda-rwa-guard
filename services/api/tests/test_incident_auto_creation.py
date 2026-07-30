"""Tests for automatic incident creation from critical detections (Screen 7).

Fake-connection unit style. The policy predicate is pure; the creator uses a fake
connection that models the ``ON CONFLICT (workspace_id, dedup_key) DO NOTHING
RETURNING id`` idempotency guarantee.
"""
from __future__ import annotations

import pytest

from services.api.app import incident_auto_creation as iac
from services.api.app import pilot


# --------------------------------------------------------------------------
# Pure policy predicate
# --------------------------------------------------------------------------
def _critical_detection(**over):
    det = {
        'id': 'det-1', 'cluster_key': 'ck-1', 'severity': 'critical', 'confidence': 0.92,
        'alert_eligible': True, 'primary_asset_id': 'asset-1', 'title': 'Custody drain',
        'explanation': 'Large unexpected transfer from custody wallet.', 'detection_type': 'custody_transfer_anomaly',
        'linked_incident_id': None, 'evidence_source': 'live',
    }
    det.update(over)
    return det


def test_policy_admits_critical_high_confidence_with_asset():
    ok, reason = iac.evaluate_policy(_critical_detection())
    assert ok is True and reason == 'eligible'


@pytest.mark.parametrize('severity', ['high', 'medium', 'low', 'info'])
def test_policy_rejects_non_critical(severity):
    ok, reason = iac.evaluate_policy(_critical_detection(severity=severity))
    assert ok is False and reason == 'below_min_severity'


def test_policy_rejects_low_confidence():
    ok, reason = iac.evaluate_policy(_critical_detection(confidence=0.4))
    assert ok is False and reason == 'below_min_confidence'


def test_policy_rejects_without_monitored_asset():
    ok, reason = iac.evaluate_policy(_critical_detection(primary_asset_id=None))
    assert ok is False and reason == 'no_monitored_asset'


def test_policy_rejects_when_disabled(monkeypatch):
    monkeypatch.setenv('INCIDENT_AUTO_CREATE_ENABLED', 'false')
    ok, reason = iac.evaluate_policy(_critical_detection())
    assert ok is False and reason == 'policy_disabled'


def test_policy_is_versioned():
    assert iac.auto_create_policy()['policy_version'] == '1.0'


def test_dedup_key_is_stable_per_cluster():
    a = iac.compute_dedup_key('ws-1', _critical_detection())
    b = iac.compute_dedup_key('ws-1', _critical_detection())
    assert a == b == 'auto|ws-1|ck-1'
    # A different workspace or cluster yields a different key.
    assert iac.compute_dedup_key('ws-2', _critical_detection()) != a
    assert iac.compute_dedup_key('ws-1', _critical_detection(cluster_key='ck-2')) != a


# --------------------------------------------------------------------------
# Idempotent creator (fake connection)
# --------------------------------------------------------------------------
class _Result:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class FakeConn:
    """Models the incidents ON CONFLICT DO NOTHING RETURNING behaviour.

    ``dedup_hit`` simulates a pre-existing row with the same dedup_key (the INSERT
    returns no row); otherwise the INSERT returns the new id.
    """

    def __init__(self, *, dedup_hit=False, existing_id='inc-existing', schema_ready=False):
        self.executed: list[tuple[str, object]] = []
        self.dedup_hit = dedup_hit
        self.existing_id = existing_id
        self.schema_ready = schema_ready
        self.committed = False

    def execute(self, sql, params=None):
        s = ' '.join(sql.split())
        self.executed.append((s, params))
        if s.startswith('INSERT INTO incidents'):
            return _Result(None if self.dedup_hit else {'id': params[0]})
        if 'to_regclass' in s:
            return _Result({'present': self.schema_ready})
        if s.startswith('SELECT id FROM incidents WHERE workspace_id'):
            return _Result({'id': self.existing_id})
        if s.startswith('SELECT reference FROM incidents'):
            return _Result({'reference': None})
        return _Result(None)

    def commit(self):
        self.committed = True

    # Introspection helpers for assertions.
    def statements(self):
        return [s for s, _ in self.executed]

    def ran(self, needle):
        return any(needle in s for s in self.statements())


@pytest.fixture
def audit_calls(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(pilot, 'log_audit', lambda *a, **kw: calls.append(kw))
    return calls


def test_critical_detection_creates_exactly_one_incident(audit_calls):
    conn = FakeConn()
    result = iac.maybe_auto_create_incident(
        conn, workspace_id='ws-1', user_id='u-1', detection=_critical_detection(),
        alert_id='alert-1', now='2026-07-30T00:00:00Z',
    )
    assert result is not None
    assert result['created'] is True
    # Exactly one incidents INSERT.
    assert sum(1 for s in conn.statements() if s.startswith('INSERT INTO incidents')) == 1
    # Source alert + detection are linked (so a later manual promotion reuses this incident).
    assert conn.ran('UPDATE alerts SET incident_id')
    assert conn.ran('UPDATE threat_detections SET linked_incident_id')
    # An initial workflow timeline event and a permanent audit event are written.
    assert conn.ran('INSERT INTO incident_timeline')
    assert any(c.get('action') == 'incident.auto_created' for c in audit_calls)


def test_reprocessing_same_detection_does_not_duplicate(audit_calls):
    # Second processing pass hits the dedup_key partial unique index -> no new row.
    conn = FakeConn(dedup_hit=True, existing_id='inc-existing')
    result = iac.maybe_auto_create_incident(
        conn, workspace_id='ws-1', user_id='u-1', detection=_critical_detection(),
        alert_id='alert-1', now='2026-07-30T00:00:00Z',
    )
    assert result == {
        'incident_id': 'inc-existing', 'created': False,
        'dedup_key': 'auto|ws-1|ck-1', 'reason': 'dedup_hit',
    }
    # The pre-existing incident is reused; the detection is (idempotently) linked to it.
    assert conn.ran('UPDATE threat_detections SET linked_incident_id')


def test_existing_linked_incident_short_circuits(audit_calls):
    conn = FakeConn()
    result = iac.maybe_auto_create_incident(
        conn, workspace_id='ws-1', user_id='u-1',
        detection=_critical_detection(linked_incident_id='inc-linked'),
        alert_id='alert-1', now='2026-07-30T00:00:00Z',
    )
    assert result['incident_id'] == 'inc-linked' and result['created'] is False
    # No INSERT is attempted when the detection is already linked.
    assert not conn.ran('INSERT INTO incidents')


def test_non_critical_detection_returns_none(audit_calls):
    conn = FakeConn()
    result = iac.maybe_auto_create_incident(
        conn, workspace_id='ws-1', user_id='u-1', detection=_critical_detection(severity='medium'),
        alert_id='alert-1', now='2026-07-30T00:00:00Z',
    )
    assert result is None
    assert not conn.ran('INSERT INTO incidents')
    assert not audit_calls


def test_created_incident_is_critical_and_open(audit_calls):
    conn = FakeConn()
    iac.maybe_auto_create_incident(
        conn, workspace_id='ws-1', user_id='u-1', detection=_critical_detection(),
        alert_id='alert-1', now='2026-07-30T00:00:00Z',
    )
    insert = next((params for s, params in conn.executed if s.startswith('INSERT INTO incidents')), None)
    assert insert is not None
    # severity param (index 5) is 'critical'; dedup_key is present.
    assert insert[5] == 'critical'
    assert 'auto|ws-1|ck-1' in insert
