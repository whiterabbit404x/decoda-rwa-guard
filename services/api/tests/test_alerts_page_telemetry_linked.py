"""The main Alerts page/API must surface telemetry/backfill-created wallet-transfer alerts.

Strategic Infrastructure Guard / smoke wallet-transfer alerts are created from live
on-chain telemetry (and the worker backfill). They carry their evidence in the alert
payload and link to a detection. The /alerts read path must:

  * return BOTH linked alert rows (no exclusion for backfill-origin alerts),
  * normalise a non-terminal status (e.g. 'new' / 'active') to 'open' and a mixed-case
    severity ('CRITICAL') to 'critical', so the count cards read 2 active / 2 critical,
  * surface each alert's tx_hash (…90c7 and …a517 here),
  * never relabel a simulator alert as live (truthfulness).

These lock the read-path contract that backs the Alerts page count cards. The frontend
derives Active = open alerts in the list, Critical = severity 'critical', High Confidence
= live evidence + tx_hash, which this test mirrors over the API output.
"""
from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock, patch

TX_90C7 = '0x' + 'a' * 60 + '90c7'
TX_A517 = '0x' + 'b' * 59 + 'a517'


def _make_request(workspace_id: str) -> Any:
    scope = {
        'type': 'http',
        'method': 'GET',
        'path': '/alerts',
        'query_string': b'',
        'headers': [(b'x-workspace-id', workspace_id.encode())],
        'client': ('127.0.0.1', 9000),
    }
    from fastapi import Request

    return Request(scope)


def _sig_row(workspace_id: str, target_id: str) -> dict[str, Any]:
    """SIG outbound-transfer alert persisted with a non-terminal status='new'/'CRITICAL'."""
    return {
        'id': 'e39485a5-2652-4950-9141-2aa6fe79bea1',
        'alert_type': 'threat_monitoring',
        'title': 'Strategic Infrastructure Guard: Treasury RWA Control Wallet Movement Detected',
        'severity': 'CRITICAL',
        'status': 'new',
        'summary': 'Outbound ETH movement from a Treasury RWA control wallet.',
        'module_key': 'strategic_infrastructure_guard',
        'target_id': target_id,
        'detection_id': str(uuid.uuid4()),
        'incident_id': None,
        'source': 'rpc_polling',
        'source_service': 'threat-engine',
        'created_at': '2026-06-13T00:00:00Z',
        'updated_at': None,
        'payload': {
            'evidence_source': 'live',
            'rule_key': 'strategic_infrastructure_guard_wallet_outbound_transfer',
            'tx_hash': TX_90C7,
            'from_address': '0xabc0000000000000000000000000000000000001',
            'to_address': '0x9990000000000000000000000000000000000002',
            'chain_id': 8453,
            'block_number': 111,
        },
        'linked_evidence_count': 0,
        'evidence_source': 'live',
        'tx_hash': TX_90C7,
        'block_number': '111',
        'from_address': '0xabc0000000000000000000000000000000000001',
        'to_address': '0x9990000000000000000000000000000000000002',
        'amount_wei': '1000',
        'chain_id': '8453',
        'confidence': 'high',
        'detection_type': 'strategic_infrastructure_guard_outbound_transfer',
        'detector_kind': None,
        'evidence_origin': 'live',
        'linked_action_id': None,
        'response_action_mode': None,
        'workspace_id': workspace_id,
    }


def _smoke_row(workspace_id: str, target_id: str) -> dict[str, Any]:
    """Smoke wallet-transfer alert persisted with status='active' (rule via matched_patterns)."""
    return {
        'id': '3fe45390-3723-4b31-bb76-60fc6666e4fd',
        'alert_type': 'threat_monitoring',
        'title': 'Monitored wallet transfer detected',
        'severity': 'critical',
        'status': 'active',
        'summary': 'Wallet transfer detected.',
        'module_key': None,
        'target_id': target_id,
        'detection_id': str(uuid.uuid4()),
        'incident_id': None,
        'source': 'live',
        'source_service': 'threat-engine',
        'created_at': '2026-06-13T00:00:00Z',
        'updated_at': None,
        'payload': {
            'evidence_source': 'live',
            'matched_patterns': [
                {'label': 'wallet_transfer_detected', 'rule_id': 'smoke_wallet_transfer', 'severity': 'critical'}
            ],
            'tx_hash': TX_A517,
            'from_address': '0xabc0000000000000000000000000000000000001',
            'to_address': '0x9990000000000000000000000000000000000003',
            'chain_id': 8453,
            'block_number': 222,
        },
        'linked_evidence_count': 0,
        'evidence_source': 'live',
        'tx_hash': TX_A517,
        'block_number': '222',
        'from_address': '0xabc0000000000000000000000000000000000001',
        'to_address': '0x9990000000000000000000000000000000000003',
        'amount_wei': '2000',
        'chain_id': '8453',
        'confidence': 'high',
        'detection_type': 'monitored_wallet_transfer',
        'detector_kind': None,
        'evidence_origin': 'live',
        'linked_action_id': None,
        'response_action_mode': None,
        'workspace_id': workspace_id,
    }


class _CapturingConn:
    def __init__(self, rows: list[dict[str, Any]]):
        self.rows = rows
        self.last_sql = ''

    def execute(self_inner, sql, params=None):
        self_inner.last_sql = ' '.join(str(sql).split())

        class R:
            def fetchall(self_inner2):
                return [dict(r) for r in self_inner.rows]

        return R()


def _run_list_alerts(conn: _CapturingConn, ws_id: str):
    from services.api.app.pilot import list_alerts

    with (
        patch('services.api.app.pilot.require_live_mode'),
        patch('services.api.app.pilot.pg_connection') as mock_pg,
        patch('services.api.app.pilot.ensure_pilot_schema'),
        patch('services.api.app.pilot.authenticate_with_connection', return_value={'id': str(uuid.uuid4())}),
        patch('services.api.app.pilot.resolve_workspace', return_value={'workspace_id': ws_id}),
    ):
        mock_pg.return_value.__enter__ = lambda s: conn
        mock_pg.return_value.__exit__ = MagicMock(return_value=False)
        return list_alerts(_make_request(ws_id), limit=50, offset=0)


def _count_cards(alerts: list[dict[str, Any]]) -> dict[str, int]:
    """Mirror the Alerts page count-card derivation over the API output."""
    active = len([a for a in alerts if str(a.get('status') or '').lower() in {'open', 'acknowledged', 'investigating'}])
    critical = len([a for a in alerts if str(a.get('severity') or '').lower() == 'critical'])
    high_conf = len([
        a for a in alerts
        if str(a.get('evidence_source') or '').lower() in {'live', 'live_provider'} and a.get('tx_hash')
    ])
    linked_incidents = len([a for a in alerts if a.get('incident_id')])
    return {'active': active, 'critical': critical, 'high_confidence': high_conf, 'linked_incidents': linked_incidents}


def test_both_telemetry_linked_alerts_returned_and_normalized():
    ws_id = '1155f479-3e5b-4d90-be6c-fd6c1d6b957d'
    tg_id = 'e7851a52-8fb1-48cd-84a3-d033f591c5dd'
    conn = _CapturingConn([_sig_row(ws_id, tg_id), _smoke_row(ws_id, tg_id)])

    result = _run_list_alerts(conn, ws_id)
    alerts = result['alerts']

    # Both backfill-origin alerts are returned (no exclusion).
    assert len(alerts) == 2

    # Non-terminal status -> open; mixed-case severity -> critical.
    for alert in alerts:
        assert alert['status'] == 'open'
        assert alert['severity'] == 'critical'
        assert alert['evidence_source'] == 'live'

    # rule_key is surfaced for both rules.
    rule_keys = {a['rule_key'] for a in alerts}
    assert rule_keys == {
        'strategic_infrastructure_guard_wallet_outbound_transfer',
        'smoke_wallet_transfer',
    }


def test_count_cards_read_two_active_two_critical():
    ws_id = '1155f479-3e5b-4d90-be6c-fd6c1d6b957d'
    tg_id = 'e7851a52-8fb1-48cd-84a3-d033f591c5dd'
    conn = _CapturingConn([_sig_row(ws_id, tg_id), _smoke_row(ws_id, tg_id)])

    alerts = _run_list_alerts(conn, ws_id)['alerts']
    cards = _count_cards(alerts)

    assert cards['active'] == 2
    assert cards['critical'] == 2
    assert cards['high_confidence'] == 2  # live evidence + tx_hash on both
    assert cards['linked_incidents'] == 0  # not escalated yet


def test_both_tx_hashes_appear():
    ws_id = '1155f479-3e5b-4d90-be6c-fd6c1d6b957d'
    tg_id = 'e7851a52-8fb1-48cd-84a3-d033f591c5dd'
    conn = _CapturingConn([_sig_row(ws_id, tg_id), _smoke_row(ws_id, tg_id)])

    alerts = _run_list_alerts(conn, ws_id)['alerts']
    hashes = [str(a.get('tx_hash') or '') for a in alerts]

    assert any(h.endswith('90c7') for h in hashes)
    assert any(h.endswith('a517') for h in hashes)


def test_simulator_wallet_alert_not_marked_live():
    """Truthfulness: a simulator wallet-transfer alert keeps evidence_source='simulator'
    and is NOT counted as high confidence, even though it is normalised to open/critical."""
    ws_id = '1155f479-3e5b-4d90-be6c-fd6c1d6b957d'
    tg_id = 'e7851a52-8fb1-48cd-84a3-d033f591c5dd'
    sim = _sig_row(ws_id, tg_id)
    sim['evidence_source'] = 'simulator'
    sim['payload'] = {**sim['payload'], 'evidence_source': 'simulator'}
    conn = _CapturingConn([sim])

    alert = _run_list_alerts(conn, ws_id)['alerts'][0]
    assert alert['evidence_source'] == 'simulator'
    cards = _count_cards([alert])
    assert cards['high_confidence'] == 0
