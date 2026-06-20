"""Regression: the main /alerts read path returns the two telemetry/backfill-created
wallet-transfer alert IDs from the production incident, with NO filters applied, and emits
the read-path log markers that make a "no alerts-list call reached the backend" vs "backend
returned 0" diagnosis possible from logs alone.

This locks the backend half of the /alerts visibility fix. The frontend half (the Alerts page
calling the same-origin /api/alerts proxy instead of the backend directly) is covered by the
web source spec alerts-panel-proxy-transport.spec.ts.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any
from unittest.mock import MagicMock, patch

# The two existing alert IDs from the production backfill logs (strategic_guard_target_backfill_completed).
SIG_ALERT_ID = 'e39485a5-2652-4950-9141-2aa6fe79bea1'
SMOKE_ALERT_ID = '3fe45390-3723-4b31-bb76-60fc6666e4fd'
TX_90C7 = '0x' + 'a' * 60 + '90c7'
TX_A517 = '0x' + 'b' * 59 + 'a517'


def _make_request(workspace_id: str) -> Any:
    from fastapi import Request

    return Request(
        {
            'type': 'http',
            'method': 'GET',
            'path': '/alerts',
            'query_string': b'',
            'headers': [(b'x-workspace-id', workspace_id.encode())],
            'client': ('127.0.0.1', 9000),
        }
    )


def _sig_row(workspace_id: str, target_id: str) -> dict[str, Any]:
    return {
        'id': SIG_ALERT_ID,
        'alert_type': 'threat_monitoring',
        'title': 'Strategic Infrastructure Guard: Treasury RWA Control Wallet Movement Detected',
        'severity': 'CRITICAL',
        'status': 'new',
        'module_key': 'strategic_infrastructure_guard',
        'target_id': target_id,
        'detection_id': str(uuid.uuid4()),
        'incident_id': None,
        'source': 'rpc_polling',
        'source_service': 'threat-engine',
        'created_at': '2026-06-13T00:00:00Z',
        'payload': {
            'evidence_source': 'live',
            'rule_key': 'strategic_infrastructure_guard_wallet_outbound_transfer',
            'tx_hash': TX_90C7,
            'chain_id': 8453,
        },
        'linked_evidence_count': 0,
        'evidence_source': 'live',
        'tx_hash': TX_90C7,
        'chain_id': '8453',
        'detection_type': 'strategic_infrastructure_guard_outbound_transfer',
        'workspace_id': workspace_id,
    }


def _smoke_row(workspace_id: str, target_id: str) -> dict[str, Any]:
    return {
        'id': SMOKE_ALERT_ID,
        'alert_type': 'threat_monitoring',
        'title': 'Monitored wallet transfer detected',
        'severity': 'critical',
        'status': 'active',
        'module_key': None,
        'target_id': target_id,
        'detection_id': str(uuid.uuid4()),
        'incident_id': None,
        'source': 'live',
        'source_service': 'threat-engine',
        'created_at': '2026-06-13T00:00:00Z',
        'payload': {
            'evidence_source': 'live',
            'matched_patterns': [
                {'label': 'wallet_transfer_detected', 'rule_id': 'smoke_wallet_transfer', 'severity': 'critical'}
            ],
            'tx_hash': TX_A517,
            'chain_id': 8453,
        },
        'linked_evidence_count': 0,
        'evidence_source': 'live',
        'tx_hash': TX_A517,
        'chain_id': '8453',
        'detection_type': 'monitored_wallet_transfer',
        'workspace_id': workspace_id,
    }


class _CapturingConn:
    def __init__(self, rows: list[dict[str, Any]]):
        self.rows = rows

    def execute(self_inner, sql, params=None):
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


def test_both_backfilled_alert_ids_are_returned_without_filters():
    ws_id = '1155f479-3e5b-4d90-be6c-fd6c1d6b957d'
    tg_id = 'e7851a52-8fb1-48cd-84a3-d033f591c5dd'
    conn = _CapturingConn([_sig_row(ws_id, tg_id), _smoke_row(ws_id, tg_id)])

    alerts = _run_list_alerts(conn, ws_id)['alerts']
    returned_ids = [a['id'] for a in alerts]

    # The two existing telemetry-linked alerts are visible (no exclusion, no duplicate).
    assert SIG_ALERT_ID in returned_ids
    assert SMOKE_ALERT_ID in returned_ids
    assert len(returned_ids) == len(set(returned_ids)) == 2


def test_read_path_log_markers_emitted_with_returned_ids(caplog):
    ws_id = '1155f479-3e5b-4d90-be6c-fd6c1d6b957d'
    tg_id = 'e7851a52-8fb1-48cd-84a3-d033f591c5dd'
    conn = _CapturingConn([_sig_row(ws_id, tg_id), _smoke_row(ws_id, tg_id)])

    with caplog.at_level(logging.INFO, logger='services.api.app.pilot'):
        _run_list_alerts(conn, ws_id)

    text = caplog.text
    # Entry marker proves the backend list query actually ran for this workspace.
    assert 'backend_alerts_list_called' in text
    assert f'workspace_id={ws_id}' in text
    # Return marker carries the exact count + ids handed back across the proxy boundary.
    assert 'backend_alerts_list_returned_count' in text
    assert 'returned_count=2' in text
    assert SIG_ALERT_ID in text
    assert SMOKE_ALERT_ID in text
