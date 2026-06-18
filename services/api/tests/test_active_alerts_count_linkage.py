"""The active-alerts count must recognise detection→alert linkage.

Strategic Infrastructure Guard / smoke wallet-transfer alerts are linked to their
detection via detections.linked_alert_id (the backfill always sets this), and the
alert's own detection_id may be absent. The canonical proof-chain count keys off
alerts.detection_event_id, which these alerts do not populate. So the runtime
"active_alerts" count must also count an open alert when a detection in the same
workspace points back at it (d.linked_alert_id = a.id) and that detection carries
evidence — otherwise 2 real, live, critical alerts render as Active Alerts = 0 and
trip a false 'live_proof_chain_incomplete' / 'proof-chain enrichment incomplete'
banner.

This locks the SQL contract for the legacy open-alerts count and the behaviour
that the count reflects detection→alert linkage.
"""
from __future__ import annotations

from datetime import datetime, timezone

from services.api.app import monitoring_runner
from services.api.tests.test_monitoring_failure_injection_matrix import (
    _FailureInjectionConn,
    _Result,
    _request,
    _setup,
)


class _RecordingConn(_FailureInjectionConn):
    """Records executed SQL and lets the legacy/canonical alert counts be tuned."""

    def __init__(self, *, legacy_alerts: int, canonical_alerts: int, **kwargs):
        super().__init__(**kwargs)
        self.legacy_alerts = legacy_alerts
        self.canonical_alerts = canonical_alerts
        self.executed_sql: list[str] = []

    def execute(self, query, params=None):
        q = ' '.join(str(query).split())
        self.executed_sql.append(q)
        # Canonical proof-chain count: alerts -> detection_events (a.detection_event_id).
        if 'FROM alerts a' in q and 'JOIN detection_events de' in q and 'JOIN telemetry_events te' in q:
            return _Result({'c': self.canonical_alerts})
        # Legacy count: alerts -> detections (detection_id / linked_alert_id) + evidence.
        if 'FROM alerts a' in q and 'JOIN detections d' in q and 'FROM detection_evidence de' in q:
            return _Result({'c': self.legacy_alerts})
        return super().execute(query, params)


def test_legacy_open_alerts_query_uses_bidirectional_detection_link(monkeypatch):
    now = datetime(2026, 6, 13, 8, 0, tzinfo=timezone.utc)
    conn = _RecordingConn(now=now, legacy_alerts=2, canonical_alerts=0)
    _setup(monkeypatch, conn, now)

    monitoring_runner.monitoring_runtime_status(_request())

    legacy_queries = [
        q for q in conn.executed_sql
        if 'FROM alerts a' in q and 'JOIN detections d' in q and 'FROM detection_evidence de' in q
    ]
    assert legacy_queries, 'expected the legacy open-alerts count query to run'
    legacy_sql = legacy_queries[0]
    # The fix: count alerts linked in EITHER direction, de-duplicated per alert.
    assert 'd.linked_alert_id = a.id' in legacy_sql
    assert 'COUNT(DISTINCT a.id)' in legacy_sql


def test_active_alerts_count_reflects_detection_to_alert_linkage(monkeypatch):
    """When alerts are linked only via detections.linked_alert_id (canonical
    detection_event_id absent), active_alerts still reports them."""
    now = datetime(2026, 6, 13, 8, 0, tzinfo=timezone.utc)
    conn = _RecordingConn(now=now, legacy_alerts=2, canonical_alerts=0)
    _setup(monkeypatch, conn, now)

    payload = monitoring_runner.monitoring_runtime_status(_request())

    # monitoring_runtime_status exposes the count top-level as `active_alerts`
    # (= max(canonical_chain_count, legacy_chain_count)); main.py maps it to
    # counts.active_alerts for the UI. With only detection→alert linkage present
    # (legacy=2, canonical=0) the count must still be 2.
    assert int(payload.get('active_alerts') or 0) == 2
    assert int(payload.get('active_alerts_count') or payload.get('active_alerts') or 0) == 2
