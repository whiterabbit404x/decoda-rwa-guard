from __future__ import annotations

from datetime import datetime, timedelta, timezone

from services.api.scripts import repair_monitoring_runtime_state


def test_is_alert_stale_requires_recent_real_evidence() -> None:
    now = datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc)
    max_age = timedelta(minutes=30)
    alert = {
        'id': 'alert-1',
        'status': 'open',
        'linked_detection_id': 'det-1',
        'created_at': '2026-04-23T11:00:00Z',
    }

    stale_without_evidence = repair_monitoring_runtime_state._is_alert_stale(  # noqa: SLF001
        alert,
        now=now,
        max_age=max_age,
        evidence_by_alert_id={},
    )
    assert stale_without_evidence is True

    stale_with_fresh_evidence = repair_monitoring_runtime_state._is_alert_stale(  # noqa: SLF001
        alert,
        now=now,
        max_age=max_age,
        evidence_by_alert_id={'alert-1': [{'observed_at': '2026-04-23T11:50:00Z'}]},
    )
    assert stale_with_fresh_evidence is False


def test_extract_chain_links_evidence_detection_alert_incident() -> None:
    evidence_rows = [{'id': 'ev-1', 'tx_hash': '0xabc', 'observed_at': '2026-04-23T11:58:00Z', 'evidence_origin': 'live'}]
    detections = [{'id': 'det-1', 'tx_hash': '0xabc'}]
    alerts = [{'id': 'alert-1', 'linked_detection_id': 'det-1', 'linked_incident_id': 'inc-1'}]
    incidents = [{'id': 'inc-1'}]

    chain = repair_monitoring_runtime_state._extract_chain(  # noqa: SLF001
        detections=detections,
        alerts=alerts,
        incidents=incidents,
        evidence_rows=evidence_rows,
    )

    assert chain['evidence']['id'] == 'ev-1'
    assert chain['detection']['id'] == 'det-1'
    assert chain['alert']['id'] == 'alert-1'
    assert chain['incident']['id'] == 'inc-1'
