from __future__ import annotations

from pathlib import Path


def test_alert_triage_and_suppression_endpoints_and_fields_exist() -> None:
    pilot = Path('services/api/app/pilot.py').read_text(encoding='utf-8')
    main = Path('services/api/app/main.py').read_text(encoding='utf-8')
    migration = Path('services/api/migrations/0012_monitoring_live_mvp.sql').read_text(encoding='utf-8')

    assert 'triage_status' in pilot
    assert 'suppressed_until' in pilot
    assert 'def create_alert_suppression' in pilot
    assert 'def list_alert_evidence' in pilot
    assert '/alerts/{alert_id}/evidence' in main
    assert '/alerts/suppressions' in main
    assert 'CREATE TABLE IF NOT EXISTS alert_suppression_rules' in migration
