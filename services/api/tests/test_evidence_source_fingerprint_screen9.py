"""Screen 9 — canonical evidence SOURCE-SNAPSHOT fingerprint (idempotency identity).

Background (the reported EV-2026-004 idempotency blocker):
  Clicking "Create Evidence Package" for incident 3c42654c-… kept resolving to the
  historical package EV-2026-004 ("An evidence package already exists…") even after
  the canonical collector was repaired so the incident's linked alert, telemetry and
  investigation timeline resolve. The old idempotency key was
  (workspace, incident, export_type, incident-scope) — it could not see that the
  source evidence had materially changed, so no superseding package was created.

  pilot._evidence_source_fingerprint fixes the root cause: it computes a deterministic
  SHA-256 over the IDENTITIES and version markers of the canonical source records the
  proof-bundle collector resolves. These tests lock in the required semantics:

  Case A  identical snapshot          → same fingerprint (reuse, no duplicate)
  Case B  source evidence changed     → different fingerprint (new, superseding pkg)
  Case C  same changed snapshot again → same fingerprint (no runaway EV-N+1)

  Plus: the fingerprint is IDENTITY based (not just a completeness/count score),
  EXCLUDES the volatile audit rows evidence-package creation itself appends, and
  fails closed (404) for a cross-workspace incident.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from services.api.app import pilot


# ── Production identifiers from the reported incident ─────────────────────────
INCIDENT_ID = '3c42654c-8504-4743-b548-253e0503326e'
ALERT_ID = 'a1111111-1111-4111-8111-111111111111'
TELEMETRY_ID = 'e5555555-5555-4555-8555-555555555555'
TELEMETRY_ID_2 = 'e6666666-6666-4666-8666-666666666666'
WS = 'ws-forensic-1'


class _R:
    def __init__(self, value):
        self._value = value

    def fetchone(self):
        return self._value

    def fetchall(self):
        if self._value is None:
            return []
        return self._value if isinstance(self._value, list) else [self._value]


class _FpConn:
    """Read-only fake that serves the canonical source records for one incident.

    Parameterized per-relation so a test can model the incident's snapshot exactly:
    the join-bridged alerts, the canonical fallback alerts, incident-linked and
    alert-linked telemetry, detections, response actions and timeline rows.
    """

    def __init__(
        self, *, incident, join_alerts=None, fallback_alerts=None, incident_metrics=None,
        alert_metrics=None, detections=None, response_actions=None, timeline=None,
    ):
        self.incident = incident
        self.join_alerts = join_alerts or []
        self.fallback_alerts = fallback_alerts or []
        self.incident_metrics = incident_metrics or []
        self.alert_metrics = alert_metrics or []
        self.detections = detections or []
        self.response_actions = response_actions or []
        self.timeline = timeline or []
        self.executed: list[str] = []

    def execute(self, query, params=None):
        s = ' '.join(str(query).split())
        self.executed.append(s)
        if 'FROM incidents WHERE workspace_id = %s AND id = %s' in s:
            return _R(self.incident)
        if 'FROM alerts a JOIN detection_metrics dm' in s:
            return _R(self.join_alerts)
        if 'FROM alerts a WHERE a.workspace_id' in s and 'a.id = ANY' in s:
            return _R(self.fallback_alerts)
        if 'FROM detection_metrics WHERE workspace_id = %s AND incident_id = %s' in s:
            return _R(self.incident_metrics)
        if 'FROM detection_metrics WHERE workspace_id = %s AND alert_id = ANY' in s:
            return _R(self.alert_metrics)
        if 'FROM detections WHERE workspace_id = %s AND linked_alert_id = ANY' in s:
            return _R(self.detections)
        if 'FROM response_actions' in s and 'incident_id = %s' in s:
            return _R(self.response_actions)
        if 'FROM incident_timeline' in s:
            return _R(self.timeline)
        raise AssertionError(f'unexpected query: {s!r}')


# ── Canonical row builders ────────────────────────────────────────────────────
def _incident(*, with_alert: bool):
    return {
        'id': INCIDENT_ID, 'workspace_id': WS, 'status': 'open', 'severity': 'high',
        'asset_id': None,
        'source_alert_id': ALERT_ID if with_alert else None,
        'linked_alert_ids': [ALERT_ID] if with_alert else [],
    }


def _alert(alert_id: str = ALERT_ID):
    return {
        'id': alert_id, 'status': 'open', 'severity': 'high', 'source': 'live_provider',
        'detection_id': None, 'detection_event_id': None,
    }


def _metric(telemetry_id: str = TELEMETRY_ID):
    return {'id': telemetry_id, 'detected_at': '2026-08-07T00:00:00Z'}


def _action():
    return {
        'id': 'action-1', 'status': 'pending', 'action_type': 'notify_team',
        'mode': 'recommended', 'executed_at': None, 'rolled_back_at': None,
    }


def _timeline_row():
    return {'id': 'tl-1', 'event_type': 'evidence_collected', 'created_at': '2026-08-10T00:00:00Z'}


def _case_a() -> _FpConn:
    """Initial snapshot: incident + one response action only (EV-2026-004 era)."""
    return _FpConn(incident=_incident(with_alert=False), response_actions=[_action()])


def _case_b(*, telemetry_id: str = TELEMETRY_ID) -> _FpConn:
    """Repaired snapshot: linked alert + telemetry + investigation timeline resolve."""
    return _FpConn(
        incident=_incident(with_alert=True),
        fallback_alerts=[_alert()],
        alert_metrics=[_metric(telemetry_id)],
        response_actions=[_action()],
        timeline=[_timeline_row()],
    )


def _fp(conn: _FpConn) -> str:
    return pilot._evidence_source_fingerprint(conn, workspace_id=WS, incident_id=INCIDENT_ID)['fingerprint']


# ── Tests ─────────────────────────────────────────────────────────────────────
def test_fingerprint_is_deterministic_sha256():
    """A fingerprint is a stable sha256 digest — same snapshot, same value."""
    fp1 = _fp(_case_a())
    fp2 = _fp(_case_a())
    assert fp1.startswith('sha256:') and len(fp1) == len('sha256:') + 64
    assert fp1 == fp2


def test_case_b_changed_snapshot_differs_from_case_a():
    """Resolving the linked alert + telemetry + timeline changes the fingerprint —
    so idempotency creates a NEW (superseding) package instead of reusing the old."""
    assert _fp(_case_a()) != _fp(_case_b())


def test_case_c_same_changed_snapshot_reproduces_the_same_fingerprint():
    """Clicking Create again with no further evidence change reproduces fingerprint B
    (idempotent) — never a runaway EV-N+1."""
    assert _fp(_case_b()) == _fp(_case_b())


def test_fingerprint_is_source_identity_based_not_a_completeness_score():
    """Two snapshots with the SAME shape/counts but a DIFFERENT telemetry identity
    fingerprint differently — the fingerprint captures authoritative source ids and
    versions, never only a completeness/count score (two snapshots can share one)."""
    assert _fp(_case_b(telemetry_id=TELEMETRY_ID)) != _fp(_case_b(telemetry_id=TELEMETRY_ID_2))


def test_fingerprint_excludes_volatile_audit_events():
    """The fingerprint never consults audit_logs — the very rows evidence-package
    creation itself appends (export.generate) — so a create can never drift the
    fingerprint and spawn a duplicate on the next click."""
    conn = _case_b()
    _fp(conn)
    assert not any('audit_logs' in s for s in conn.executed)


def test_fingerprint_tracks_response_action_state_change():
    """Executing/rolling back a response action (a version marker) changes the
    fingerprint, so newly corroborated evidence supersedes the prior package."""
    base = _fp(_case_a())
    executed = _FpConn(
        incident=_incident(with_alert=False),
        response_actions=[{**_action(), 'status': 'executed', 'executed_at': '2026-08-11T00:00:00Z'}],
    )
    assert base != _fp(executed)


def test_missing_incident_fails_closed_404():
    """A cross-workspace / missing incident raises 404 before any package is minted."""
    with pytest.raises(HTTPException) as exc:
        _fp(_FpConn(incident=None))
    assert exc.value.status_code == 404
