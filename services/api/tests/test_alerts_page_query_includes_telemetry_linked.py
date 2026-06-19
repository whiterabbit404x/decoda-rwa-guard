"""Regression: the main /alerts query must INCLUDE telemetry-linked wallet-transfer alerts.

Unlike ``test_alerts_page_telemetry_linked`` (whose fake connection returns the seeded rows
for every query, so it only exercises the Python normalisation), this test drives a fake
connection that faithfully emulates the list_alerts SQL — workspace scoping, the
case-insensitive severity filter, and the normalisation-aware 'open' status filter
(open-equivalent statuses for telemetry-linked wallet alerts), plus pagination. It therefore
locks the read-path contract that backs the Alerts page:

  * Two telemetry-linked wallet-transfer alerts (one ``smoke_wallet_transfer`` /
    ``native_transfer`` row persisted as status='active', one
    ``strategic_infrastructure_guard_wallet_outbound_transfer`` / ``wallet_transfer_detected``
    row persisted as status='new'/severity='CRITICAL') are returned with NO manual "Open Alert"
    step, normalised to open / critical, with both tx hashes (…90c7 and …a517) surfaced.
  * They are STILL returned under the 'Open' status quick filter and the 'critical' severity
    quick filter, even though their raw stored status/severity are not literally 'open'/'critical'
    — the bug that hid them from the filtered count cards.
  * A simulator wallet alert is never relabelled live (truthfulness).
"""
from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock, patch

TX_90C7 = '0x' + 'a' * 60 + '90c7'
TX_A517 = '0x' + 'b' * 59 + 'a517'

WS_ID = '1155f479-3e5b-4d90-be6c-fd6c1d6b957d'
TARGET_ID = 'e7851a52-8fb1-48cd-84a3-d033f591c5dd'

OPEN_EQUIVALENT = {'', 'open', 'active', 'new', 'created', 'linked', 'detection', 'pending', 'none', 'null'}
WALLET_RULE_KEYS = {'strategic_infrastructure_guard_wallet_outbound_transfer', 'smoke_wallet_transfer'}
WALLET_DETECTION_TYPES = {'strategic_infrastructure_guard_outbound_transfer', 'monitored_wallet_transfer'}


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


def _sig_row(workspace_id: str = WS_ID, target_id: str = TARGET_ID) -> dict[str, Any]:
    """SIG outbound-transfer alert persisted with a non-terminal status='new'/severity='CRITICAL'."""
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
            'detection_type': 'strategic_infrastructure_guard_outbound_transfer',
            'event_type': 'wallet_transfer_detected',
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


def _smoke_row(workspace_id: str = WS_ID, target_id: str = TARGET_ID) -> dict[str, Any]:
    """Smoke wallet-transfer alert (native_transfer) persisted with status='active'."""
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
        'created_at': '2026-06-13T01:00:00Z',
        'updated_at': None,
        'payload': {
            'evidence_source': 'live',
            'rule_key': 'smoke_wallet_transfer',
            'detection_type': 'monitored_wallet_transfer',
            'event_type': 'native_transfer',
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


def _payload_tx_hash(row: dict[str, Any]) -> str:
    payload = row.get('payload') or {}
    tx = payload.get('tx_hash')
    if not tx and isinstance(payload.get('evidence'), dict):
        tx = payload['evidence'].get('tx_hash')
    return str(tx or '')


def _is_wallet_transferish(row: dict[str, Any]) -> bool:
    """Mirror of the SQL wallet-transfer predicate used by the 'open' status clause."""
    payload = row.get('payload') or {}
    if str(payload.get('rule_key') or '') in WALLET_RULE_KEYS:
        return True
    if str(row.get('module_key') or '') == 'strategic_infrastructure_guard':
        return True
    if str(payload.get('detection_type') or '') in WALLET_DETECTION_TYPES:
        return True
    return bool(_payload_tx_hash(row))


def _status_match(row: dict[str, Any], status_filter: str | None) -> bool:
    if not status_filter:
        return True
    requested = status_filter.strip().lower()
    raw = str(row.get('status') or '').strip().lower()
    if raw == requested:
        return True
    if requested == 'open' and raw in OPEN_EQUIVALENT:
        if _is_wallet_transferish(row):
            return True
        # detection_id IS NOT NULL path added to the list_alerts WHERE clause (task 3).
        if row.get('detection_id') is not None:
            return True
    return False


def _severity_match(row: dict[str, Any], severity_filter: str | None) -> bool:
    if not severity_filter:
        return True
    return str(row.get('severity') or '').strip().lower() == severity_filter.strip().lower()


class _FaithfulConn:
    """Fake connection that emulates the list_alerts SQL semantics over an in-memory row set."""

    def __init__(self, rows: list[dict[str, Any]]):
        self.rows = rows
        self.last_sql = ''

    def execute(self, sql, params=None):
        flat = ' '.join(str(sql).split())
        self.last_sql = flat
        params = list(params or [])

        # Diagnostic pre-scan: workspace + structural filters only (no severity/status).
        if 'payload_rule_key' in flat and 'LIMIT 500' in flat:
            ws = params[0]
            target = params[1]
            module = params[3]
            source = params[5]
            data = [r for r in self.rows if str(r.get('workspace_id')) == str(ws)]
            data = self._apply_structural(data, target, module, source)
            projected = [
                {
                    'id': r.get('id'),
                    'status': r.get('status'),
                    'severity': r.get('severity'),
                    'target_id': r.get('target_id'),
                    'incident_id': r.get('incident_id'),
                    'module_key': r.get('module_key'),
                    'alert_type': r.get('alert_type'),
                    'payload_rule_key': (r.get('payload') or {}).get('rule_key'),
                    'detection_type': (r.get('payload') or {}).get('detection_type'),
                    'tx_hash': _payload_tx_hash(r) or None,
                    'evidence_source': (r.get('payload') or {}).get('evidence_source') or r.get('source'),
                    'opened_at': r.get('opened_at'),
                }
                for r in data
            ]
            return _Result(projected)

        # Main list query. Param order mirrors list_alerts exactly:
        #   ws, sev, sev, module, module, target, target, status, status, status,
        #   source, source, source, limit, offset
        ws = params[0]
        severity = params[1]
        module = params[3]
        target = params[5]
        status_value = params[7]
        source = params[10]
        limit = params[-2]
        offset = params[-1]
        data = [r for r in self.rows if str(r.get('workspace_id')) == str(ws)]
        data = self._apply_structural(data, target, module, source)
        data = [r for r in data if _severity_match(r, severity)]
        data = [r for r in data if _status_match(r, status_value)]
        data = sorted(data, key=lambda r: str(r.get('created_at') or ''), reverse=True)
        page = data[offset:offset + limit]
        return _Result([dict(r) for r in page])

    @staticmethod
    def _apply_structural(data, target, module, source):
        if target is not None:
            data = [r for r in data if str(r.get('target_id')) == str(target)]
        if module is not None:
            data = [r for r in data if r.get('module_key') == module]
        if source is not None:
            data = [r for r in data if source in (r.get('source'), r.get('source_service'))]
        return data


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


def _run_list_alerts(conn, ws_id: str, **kwargs):
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
        return list_alerts(_make_request(ws_id), **kwargs)


def _count_cards(alerts: list[dict[str, Any]]) -> dict[str, int]:
    active = len([a for a in alerts if str(a.get('status') or '').lower() in {'open', 'acknowledged', 'investigating'}])
    critical = len([a for a in alerts if str(a.get('severity') or '').lower() == 'critical'])
    high_conf = len([
        a for a in alerts
        if str(a.get('evidence_source') or '').lower() in {'live', 'live_provider'} and a.get('tx_hash')
    ])
    linked = len([a for a in alerts if a.get('incident_id')])
    return {'active': active, 'critical': critical, 'high_confidence': high_conf, 'linked_incidents': linked}


def test_both_alerts_returned_no_manual_open_step():
    """No-filter load: both telemetry-linked alerts come back, normalised, with both tx hashes."""
    conn = _FaithfulConn([_sig_row(), _smoke_row()])
    result = _run_list_alerts(conn, WS_ID, limit=50, offset=0)
    alerts = result['alerts']

    assert len(alerts) == 2
    for alert in alerts:
        assert alert['status'] == 'open'
        assert alert['severity'] == 'critical'
        assert alert['evidence_source'] == 'live'

    hashes = [str(a.get('tx_hash') or '') for a in alerts]
    assert any(h.endswith('90c7') for h in hashes)
    assert any(h.endswith('a517') for h in hashes)

    cards = _count_cards(alerts)
    assert cards == {'active': 2, 'critical': 2, 'high_confidence': 2, 'linked_incidents': 0}


def test_both_alerts_returned_under_open_status_filter():
    """The 'Open' quick filter must not drop alerts stored as new/active (open-equivalent)."""
    conn = _FaithfulConn([_sig_row(), _smoke_row()])
    alerts = _run_list_alerts(conn, WS_ID, status_value='open', limit=50, offset=0)['alerts']

    assert len(alerts) == 2
    assert _count_cards(alerts)['active'] == 2


def test_both_alerts_returned_under_critical_severity_filter():
    """The 'critical' quick filter must match a row persisted as 'CRITICAL' (case-insensitive)."""
    conn = _FaithfulConn([_sig_row(), _smoke_row()])
    alerts = _run_list_alerts(conn, WS_ID, severity='critical', limit=50, offset=0)['alerts']

    assert len(alerts) == 2
    assert _count_cards(alerts)['critical'] == 2


def test_both_alerts_returned_under_open_and_critical_filters():
    """Both quick filters together still surface both telemetry-linked critical alerts."""
    conn = _FaithfulConn([_sig_row(), _smoke_row()])
    alerts = _run_list_alerts(conn, WS_ID, severity='critical', status_value='open', limit=50, offset=0)['alerts']

    assert len(alerts) == 2


def test_other_workspace_alerts_are_not_returned():
    """Workspace isolation: alerts in another workspace are never returned."""
    other = _sig_row(workspace_id=str(uuid.uuid4()))
    conn = _FaithfulConn([other, _smoke_row()])
    alerts = _run_list_alerts(conn, WS_ID, limit=50, offset=0)['alerts']

    assert len(alerts) == 1
    assert alerts[0]['id'] == '3fe45390-3723-4b31-bb76-60fc6666e4fd'


def test_query_diagnostics_logs_excluded_wallet_alert_with_reason(caplog):
    """Task 7: the staged read-path diagnostic reports the pre-filter population and names any
    telemetry-linked wallet alert excluded from the returned page, with the exclusion reason."""
    import logging

    conn = _FaithfulConn([_sig_row(), _smoke_row()])
    # A severity filter that matches neither critical alert: both must be reported excluded.
    with caplog.at_level(logging.INFO, logger='services.api.app.pilot'):
        result = _run_list_alerts(conn, WS_ID, severity='high', limit=50, offset=0)

    assert result['alerts'] == []
    diag = [r.getMessage() for r in caplog.records if 'alerts_list_query_diagnostics' in r.getMessage()]
    assert diag, 'expected an alerts_list_query_diagnostics log line'
    line = diag[-1]
    assert 'total_alert_rows_before_filter=2' in line
    assert 'rows_after_workspace_filter=2' in line
    assert 'rows_after_rule_filter=0' in line
    assert 'returned_count=0' in line
    assert 'e39485a5-2652-4950-9141-2aa6fe79bea1:severity_filter' in line
    assert '3fe45390-3723-4b31-bb76-60fc6666e4fd:severity_filter' in line


def test_simulator_wallet_alert_not_relabelled_live():
    """Truthfulness: a simulator wallet alert keeps evidence_source='simulator' and is not
    counted as high confidence, even though it is normalised to open/critical."""
    sim = _sig_row()
    sim['evidence_source'] = 'simulator'
    sim['payload'] = {**sim['payload'], 'evidence_source': 'simulator'}
    conn = _FaithfulConn([sim])
    alert = _run_list_alerts(conn, WS_ID, limit=50, offset=0)['alerts'][0]

    assert alert['evidence_source'] == 'simulator'
    assert _count_cards([alert])['high_confidence'] == 0
