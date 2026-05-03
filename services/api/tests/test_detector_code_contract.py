from types import SimpleNamespace

from app import pilot


def test_canonical_detector_codes_are_emittable():
    expected = {
        'oracle_nav_divergence',
        'proof_of_reserve_stale',
        'unauthorized_mint_burn',
        'abnormal_redemption_activity',
        'custody_wallet_movement_anomaly',
        'compliance_exposure',
        'monitoring_coverage_gap',
    }
    assert expected.issubset(set(pilot.CANONICAL_DETECTOR_CODES.values()))
    for code in expected:
        assert pilot.canonical_detector_code(code) == code


def test_escalation_preserves_detector_kind_in_timeline(monkeypatch):
    timeline = []

    class _Result:
        def __init__(self, row=None):
            self._row = row

        def fetchone(self):
            return self._row

    class _Conn:
        def execute(self, sql, params=None):
            n = ' '.join(sql.split())
            if 'FROM alerts WHERE id' in n:
                return _Result({'id': 'a1', 'target_id': 't1', 'analysis_run_id': 'r1', 'title': 'A', 'severity': 'high', 'summary': 'S', 'detection_id': 'd1', 'alert_type': 'reserve_mismatch', 'findings': {'detector_kind': 'reserve_mismatch'}})
            if 'FROM evidence' in n:
                return _Result({'id': 'e1', 'tx_hash': '0x1', 'observed_at': '2026-01-01T00:00:00Z', 'raw_payload_json': {'detector_kind': 'reserve_mismatch'}})
            if 'WITH inserted_incident' in n:
                return _Result({'incident_id': 'i1'})
            return _Result(None)

        def commit(self):
            return None

    class _Ctx:
        def __enter__(self):
            return _Conn()

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(pilot, 'pg_connection', lambda: _Ctx())
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_a, **_k: None)
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda *_a, **_k: ({'id': 'u1'}, {'workspace_id': 'w1'}))
    monkeypatch.setattr(pilot, 'write_action_history', lambda *_a, **_k: None)
    monkeypatch.setattr(pilot, '_incident_external_references', lambda **_k: {})
    monkeypatch.setattr(pilot, 'append_incident_timeline_event', lambda *_a, **kwargs: timeline.append(kwargs.get('metadata') or {}))

    pilot.escalate_alert_to_incident('a1', {}, SimpleNamespace(headers={}))
    escalation = next(item for item in timeline if item.get('alert_id') == 'a1')
    assert escalation['detector_kind'] == 'proof_of_reserve_stale'
