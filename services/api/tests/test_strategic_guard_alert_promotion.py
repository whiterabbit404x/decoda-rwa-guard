"""Regression: promote_wallet_transfer_alerts promotes SIG/smoke alerts into Alerts page.

Seeds two in-memory wallet-transfer alert rows (SIG status='new', smoke status='active'),
calls promote_wallet_transfer_alerts, then calls list_alerts and verifies:
  - both alerts are returned with no manual "Open Alert" step
  - count cards: active=2, critical=2, high_confidence=2, linked_incidents=0
  - both tx_hashes (ending 90c7 and a517) surface
  - promotion is idempotent (second call returns 0)
  - simulator alerts (source not live, payload.evidence_source='simulator') are never promoted
"""
from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock, patch

TX_90C7 = '0x' + 'a' * 60 + '90c7'
TX_A517 = '0x' + 'b' * 59 + 'a517'

WS_ID = '1155f479-3e5b-4d90-be6c-fd6c1d6b957d'
TARGET_ID = 'e7851a52-8fb1-48cd-84a3-d033f591c5dd'

_OPEN_EQUIV = {'', 'open', 'active', 'new', 'created', 'linked', 'detection', 'pending', 'none', 'null'}


def _sig_row() -> dict[str, Any]:
    return {
        'id': 'e39485a5-2652-4950-9141-2aa6fe79bea1',
        'workspace_id': WS_ID,
        'alert_type': 'threat_monitoring',
        'title': 'Strategic Infrastructure Guard: Treasury RWA Control Wallet Movement Detected',
        'severity': 'CRITICAL',
        'status': 'new',
        'summary': 'Outbound ETH movement from a Treasury RWA control wallet.',
        'module_key': 'strategic_infrastructure_guard',
        'target_id': TARGET_ID,
        'detection_id': str(uuid.uuid4()),
        'incident_id': None,
        'source': 'rpc_polling',
        'source_service': 'threat-engine',
        'created_at': '2026-06-13T00:00:00Z',
        'updated_at': None,
        'opened_at': None,
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
    }


def _smoke_row() -> dict[str, Any]:
    return {
        'id': '3fe45390-3723-4b31-bb76-60fc6666e4fd',
        'workspace_id': WS_ID,
        'alert_type': 'threat_monitoring',
        'title': 'Monitored wallet transfer detected',
        'severity': 'critical',
        'status': 'active',
        'summary': 'Wallet transfer detected.',
        'module_key': None,
        'target_id': TARGET_ID,
        'detection_id': str(uuid.uuid4()),
        'incident_id': None,
        'source': 'live',
        'source_service': 'threat-engine',
        'created_at': '2026-06-13T01:00:00Z',
        'updated_at': None,
        'opened_at': None,
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
    }


class _PromotionConn:
    """Stateful fake connection: handles the promote UPDATE then returns stored rows for SELECT."""

    def __init__(self, rows: list[dict[str, Any]]):
        self._rows = [dict(r) for r in rows]
        self.rowcount = 0

    def execute(self, sql: Any, params: Any = None):
        flat = ' '.join(str(sql).split())
        if 'UPDATE alerts' in flat and 'opened_at = created_at' in flat:
            return self._apply_promote(list(params or []))
        return _Result([dict(r) for r in self._rows])

    def _apply_promote(self, params: list) -> '_PromotionConn':
        workspace_id = str(params[0]) if params else ''
        target_id = str(params[1]) if (len(params) > 1 and params[1] is not None) else None
        promoted = 0
        for row in self._rows:
            if workspace_id and str(row.get('workspace_id')) != workspace_id:
                continue
            if target_id and str(row.get('target_id')) != target_id:
                continue
            if row.get('opened_at') is not None:
                continue
            payload = row.get('payload') or {}
            rule_key = str(payload.get('rule_key') or '')
            is_wallet = (
                rule_key in {'strategic_infrastructure_guard_wallet_outbound_transfer', 'smoke_wallet_transfer'}
                or str(row.get('module_key') or '') == 'strategic_infrastructure_guard'
                or str(payload.get('detection_type') or '') in {'strategic_infrastructure_guard_outbound_transfer', 'monitored_wallet_transfer'}
            )
            ev_src = str(payload.get('evidence_source') or '')
            is_live = (
                ev_src == 'live'
                or str(row.get('source') or '') in {'live', 'rpc_polling'}
                or str(row.get('source_service') or '') == 'threat-engine'
            )
            has_tx = bool(payload.get('tx_hash'))
            status_ok = str(row.get('status') or '').strip().lower() in _OPEN_EQUIV
            if is_wallet and is_live and has_tx and status_ok:
                row['opened_at'] = row.get('created_at')
                row['status'] = 'open'
                row['severity'] = 'critical'
                if row.get('source') == 'rpc_polling':
                    row['source'] = 'live'
                promoted += 1
        self.rowcount = promoted
        return self

    def fetchall(self):
        return []


class _Result:
    def __init__(self, rows: list[dict[str, Any]]):
        self._rows = rows

    def fetchall(self):
        return self._rows


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


def _run_promote(conn: _PromotionConn, workspace_id: str, target_id: str | None = None) -> int:
    from services.api.app.pilot import promote_wallet_transfer_alerts
    return promote_wallet_transfer_alerts(conn, workspace_id=workspace_id, target_id=target_id)


def _run_list_alerts(conn: _PromotionConn, ws_id: str, **kwargs: Any) -> dict[str, Any]:
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


# ── Tests ────────────────────────────────────────────────────────────────────


def test_promote_returns_two_for_new_and_active_statuses():
    """promote_wallet_transfer_alerts promotes SIG (status='new') and smoke (status='active')."""
    conn = _PromotionConn([_sig_row(), _smoke_row()])
    promoted = _run_promote(conn, WS_ID, target_id=TARGET_ID)
    assert promoted == 2


def test_promote_sets_opened_at_and_status_open():
    """After promotion, opened_at is set, status='open', severity='critical' on each alert."""
    conn = _PromotionConn([_sig_row(), _smoke_row()])
    _run_promote(conn, WS_ID, target_id=TARGET_ID)
    for row in conn._rows:
        assert row.get('opened_at') is not None, f"opened_at should be set on {row['id']}"
        assert row['status'] == 'open'
        assert row['severity'] == 'critical'


def test_promote_is_idempotent():
    """Second call to promote returns 0 — already-promoted rows are skipped."""
    conn = _PromotionConn([_sig_row(), _smoke_row()])
    _run_promote(conn, WS_ID)
    second = _run_promote(conn, WS_ID)
    assert second == 0


def test_promote_then_list_returns_both_alerts_no_manual_open_step():
    """After promotion list_alerts returns both alerts normalised to open/critical, with tx hashes."""
    conn = _PromotionConn([_sig_row(), _smoke_row()])
    _run_promote(conn, WS_ID)
    result = _run_list_alerts(conn, WS_ID, limit=50, offset=0)
    alerts = result['alerts']

    assert len(alerts) == 2
    for alert in alerts:
        assert alert['status'] == 'open'
        assert alert['severity'] == 'critical'
        assert alert['evidence_source'] == 'live'

    hashes = [str(a.get('tx_hash') or '') for a in alerts]
    assert any(h.endswith('90c7') for h in hashes), 'TX_90C7 missing from results'
    assert any(h.endswith('a517') for h in hashes), 'TX_A517 missing from results'

    cards = _count_cards(alerts)
    assert cards == {'active': 2, 'critical': 2, 'high_confidence': 2, 'linked_incidents': 0}


def test_promoted_alerts_pass_open_status_filter():
    """After promotion, both alerts pass the 'open' status quick filter (count cards gate)."""
    conn = _PromotionConn([_sig_row(), _smoke_row()])
    _run_promote(conn, WS_ID)
    alerts = _run_list_alerts(conn, WS_ID, status_value='open', limit=50, offset=0)['alerts']
    assert len(alerts) == 2
    assert _count_cards(alerts)['active'] == 2


def test_promoted_alerts_pass_critical_severity_filter():
    """After promotion, both alerts pass the 'critical' severity quick filter."""
    conn = _PromotionConn([_sig_row(), _smoke_row()])
    _run_promote(conn, WS_ID)
    alerts = _run_list_alerts(conn, WS_ID, severity='critical', limit=50, offset=0)['alerts']
    assert len(alerts) == 2
    assert _count_cards(alerts)['critical'] == 2


def test_promoted_alerts_pass_open_and_critical_filters_together():
    """Both quick filters together still surface both promoted alerts."""
    conn = _PromotionConn([_sig_row(), _smoke_row()])
    _run_promote(conn, WS_ID)
    alerts = _run_list_alerts(conn, WS_ID, severity='critical', status_value='open', limit=50, offset=0)['alerts']
    assert len(alerts) == 2


def test_promote_workspace_scoped():
    """Promotion only affects alerts in the target workspace."""
    other = _sig_row()
    other['workspace_id'] = str(uuid.uuid4())
    conn = _PromotionConn([other, _smoke_row()])
    promoted = _run_promote(conn, WS_ID)
    assert promoted == 1
    assert conn._rows[0].get('opened_at') is None, 'other-workspace alert must not be promoted'
    assert conn._rows[1].get('opened_at') is not None


def test_promote_does_not_touch_simulator_source_alerts():
    """Truthfulness: an alert with non-live source and simulator evidence_source is not promoted."""
    sim = _sig_row()
    sim['source'] = 'simulator'
    sim['source_service'] = None
    sim['payload'] = {**sim['payload'], 'evidence_source': 'simulator'}
    conn = _PromotionConn([sim])
    promoted = _run_promote(conn, WS_ID)
    assert promoted == 0
    assert conn._rows[0].get('opened_at') is None
