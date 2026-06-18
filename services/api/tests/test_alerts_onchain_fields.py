"""Tests that the /alerts list surfaces on-chain wallet-transfer evidence.

Strategic Infrastructure Guard / smoke wallet-transfer alerts record their live
on-chain evidence (tx_hash, from/to, amount, chain_id, block_number,
evidence_source='live') in the alert *payload* and link to a detection via
detection_id / detections.linked_alert_id — they do NOT write an evidence-table
row. The Alerts page must still show those fields, so:

  * list_alerts must SELECT a.payload and COALESCE tx_hash / block_number /
    evidence_source from the evidence table → alert payload → a.source, and
  * the serialized alert must expose tx_hash / from_address / to_address /
    amount_wei / chain_id / evidence_source / confidence top-level.

Truthfulness: the payload's own evidence_source is used (never relabelled), so a
simulator alert keeps evidence_source='simulator'.
"""
from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock, patch


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


def _sig_alert_row(workspace_id: str) -> dict[str, Any]:
    """A row shaped like the NEW list_alerts SELECT for a live SIG alert."""
    return {
        'id': str(uuid.uuid4()),
        'alert_type': 'threat_monitoring',
        'title': 'Strategic Infrastructure Guard: Treasury RWA Control Wallet Movement Detected',
        'severity': 'critical',
        'status': 'open',
        'summary': 'Outbound ETH movement from a wallet classified as Treasury RWA operational infrastructure.',
        'module_key': 'strategic_infrastructure_guard',
        'target_id': str(uuid.uuid4()),
        'detection_id': str(uuid.uuid4()),
        'incident_id': None,
        'assigned_to': None,
        'evidence_summary': None,
        'source': 'rpc_polling',
        'source_service': 'threat-engine',
        'recommended_action': 'review_wallet_transfer',
        'degraded': False,
        'occurrence_count': 1,
        'last_seen_at': None,
        'findings': None,
        'owner_user_id': None,
        'triage_status': None,
        'resolution_note': None,
        'suppressed_until': None,
        'acknowledged_at': None,
        'resolved_at': None,
        'created_at': '2026-06-13T00:00:00Z',
        'updated_at': None,
        'payload': {
            'severity': 'critical',
            'confidence': 'high',
            'evidence_source': 'live',
            'tx_hash': '0x90c7' + 'a' * 60,
            'from_address': '0x5f6f35fd8b10c5576089f99c7c8c351deb851d1f',
            'to_address': '0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
            'value_wei': '1000000000000000000',
            'chain_id': 8453,
            'block_number': '12345678',
        },
        'linked_evidence_count': 0,
        'last_evidence_at': None,
        # The new SQL COALESCEs these from the payload when no evidence row exists.
        'evidence_source': 'live',
        'tx_hash': '0x90c7' + 'a' * 60,
        'block_number': '12345678',
        'from_address': '0x5f6f35fd8b10c5576089f99c7c8c351deb851d1f',
        'to_address': '0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
        'amount_wei': '1000000000000000000',
        'chain_id': '8453',
        'confidence': 'high',
        'detection_type': 'strategic_infrastructure_guard_outbound_transfer',
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


def test_sig_alert_onchain_fields_surfaced_top_level():
    ws_id = str(uuid.uuid4())
    conn = _CapturingConn([_sig_alert_row(ws_id)])
    result = _run_list_alerts(conn, ws_id)

    assert len(result['alerts']) == 1
    alert = result['alerts'][0]
    assert alert['severity'] == 'critical'
    assert alert['status'] == 'open'
    assert alert['evidence_source'] == 'live'
    assert alert['tx_hash'].startswith('0x90c7')
    assert alert['from_address'] == '0x5f6f35fd8b10c5576089f99c7c8c351deb851d1f'
    assert alert['to_address'] == '0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
    assert alert['amount_wei'] == '1000000000000000000'
    assert str(alert['chain_id']) == '8453'
    assert alert['block_number'] == '12345678'
    assert alert['confidence'] == 'high'


def test_list_alerts_sql_coalesces_onchain_fields_from_payload():
    """Lock the SQL contract: payload is selected and tx_hash / evidence_source are
    coalesced from the alert payload (so alerts without an evidence-table row still
    show their live on-chain evidence)."""
    ws_id = str(uuid.uuid4())
    conn = _CapturingConn([])
    _run_list_alerts(conn, ws_id)
    sql = conn.last_sql

    assert 'a.payload' in sql
    assert "COALESCE(ev_latest.tx_hash, a.payload->>'tx_hash'" in sql
    assert "COALESCE(ev_latest.evidence_source, a.payload->>'evidence_source', a.source)" in sql
    assert "a.payload->>'from_address'" in sql
    assert "a.payload->>'to_address'" in sql


def test_simulator_alert_evidence_source_not_relabelled_live():
    """Truthfulness: a simulator alert must keep evidence_source='simulator'."""
    ws_id = str(uuid.uuid4())
    row = _sig_alert_row(ws_id)
    # Simulate what the SQL COALESCE returns for a simulator alert: the payload
    # evidence_source is 'simulator', so the coalesced column is 'simulator'.
    row['evidence_source'] = 'simulator'
    row['payload'] = {**(row['payload'] or {}), 'evidence_source': 'simulator'}
    conn = _CapturingConn([row])
    result = _run_list_alerts(conn, ws_id)
    assert result['alerts'][0]['evidence_source'] == 'simulator'
