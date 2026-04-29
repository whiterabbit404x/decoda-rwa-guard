from __future__ import annotations

from services.api.app import pilot


class _Result:
    def fetchone(self):
        return None


class _Conn:
    def __init__(self):
        self.calls: list[tuple[str, object]] = []

    def execute(self, statement, params=None):
        self.calls.append((' '.join(str(statement).split()), params))
        return _Result()


def test_detection_alert_incident_governance_pipeline_chain() -> None:
    conn = _Conn()
    telemetry_event = {'id': 'telem-1', 'asset_id': '00000000-0000-0000-0000-000000000001', 'target_id': '00000000-0000-0000-0000-000000000002', 'evidence_source': 'simulator'}

    detection = pilot.create_detection_event_from_telemetry(
        conn,
        workspace_id='ws-1',
        telemetry_event=telemetry_event,
        detection_type='anomaly.counterparty',
        severity='high',
        confidence=0.91,
        evidence={'summary': 'allowlist violation'},
    )
    alert = pilot.create_alert_from_detection_event(conn, workspace_id='ws-1', user_id='user-1', detection_event=detection)
    assert alert is not None

    incident = pilot.create_incident_from_alert(conn, workspace_id='ws-1', user_id='user-1', alert_id=alert['id'])
    pilot.append_pipeline_incident_status(conn, workspace_id='ws-1', incident_id=incident['id'], status_value='investigating', actor_user_id='user-1')
    pilot.append_pipeline_incident_close(conn, workspace_id='ws-1', incident_id=incident['id'], actor_user_id='user-1')
    governance = pilot.create_governance_action_for_incident(
        conn,
        workspace_id='ws-1',
        user_id='user-1',
        incident_id=incident['id'],
        alert_id=alert['id'],
        action_type='block_transaction',
        action_mode='executed',
        recommendation='Submit block request via manual runbook.',
    )

    assert governance['action_mode'] == 'manual_required'
    assert governance['status'] == 'manual_required'
    assert any('INSERT INTO detection_events' in statement for statement, _ in conn.calls)
    assert any('INSERT INTO alerts' in statement for statement, _ in conn.calls)
    assert any('INSERT INTO incidents' in statement for statement, _ in conn.calls)
    assert any('INSERT INTO governance_actions' in statement for statement, _ in conn.calls)
    timeline_inserts = [statement for statement, _ in conn.calls if 'INSERT INTO incident_timeline' in statement]
    assert len(timeline_inserts) >= 4


def test_alert_not_created_for_low_confidence_detection() -> None:
    conn = _Conn()
    detection = {
        'id': 'det-1',
        'target_id': '00000000-0000-0000-0000-000000000002',
        'detection_type': 'anomaly.counterparty',
        'severity': 'medium',
        'confidence': 0.4,
        'evidence_source': 'simulator',
        'evidence_summary': 'noise',
        'evidence': {},
    }
    assert pilot.create_alert_from_detection_event(conn, workspace_id='ws-1', user_id='user-1', detection_event=detection) is None
