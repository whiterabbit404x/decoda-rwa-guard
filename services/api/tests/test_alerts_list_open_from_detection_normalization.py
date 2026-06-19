"""Regression: old 'open_from_detection' style alerts appear in /alerts list.

Before migration 0116 and the accompanying Python fix, alerts created via the
"Open Alert" button had matched_patterns[0].rule_id='open_from_detection' (not a
canonical wallet-transfer rule key) and no top-level payload.rule_key.

The Python normalisation layer (_alert_rule_key → _is_wallet_transfer_rule_alert)
must now recognise these as wallet-transfer alerts when they also carry a tx_hash,
so their status is normalised to 'open' and severity to 'critical' without requiring
migration 0116 to have run.
"""
from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock, patch

from services.api.app.pilot import (
    _alert_rule_key,
    _is_wallet_transfer_rule_alert,
    _normalize_alert_view,
)

WS_ID = '1155f479-3e5b-4d90-be6c-fd6c1d6b957d'
TARGET_ID = 'e7851a52-8fb1-48cd-84a3-d033f591c5dd'
TX_HASH = '0x' + 'c' * 62 + '90c7'

SMOKE_ALERT_ID = '3fe45390-3723-4b31-bb76-60fc6666e4fd'
SIG_ALERT_ID   = 'e39485a5-2652-4950-9141-2aa6fe79bea1'


# ── helpers for old-style alert rows ────────────────────────────────────────

def _old_smoke_row(*, status: str = 'active', opened_at: Any = None) -> dict[str, Any]:
    """Smoke alert created by pre-migration 'Open Alert' button: no rule_key in payload,
    matched_patterns[0].rule_id='open_from_detection', has tx_hash."""
    return {
        'id': SMOKE_ALERT_ID,
        'alert_type': 'threat_monitoring',
        'title': '',
        'severity': 'medium',
        'status': status,
        'module_key': None,
        'target_id': TARGET_ID,
        'detection_id': str(uuid.uuid4()),
        'incident_id': None,
        'source': 'live',
        'source_service': 'threat-engine',
        'opened_at': opened_at,
        'payload': {
            # No rule_key — the bug: old code didn't set this
            'detection_type': 'monitored_wallet_transfer',
            'evidence_source': 'live',
            'tx_hash': TX_HASH,
            'matched_patterns': [
                {'label': 'monitored_wallet_transfer', 'rule_id': 'open_from_detection', 'severity': 'critical'}
            ],
        },
        'evidence_source': 'live',
        'tx_hash': TX_HASH,
        'chain_id': '8453',
        'confidence': 'high',
        'detection_type': 'monitored_wallet_transfer',
    }


def _old_sig_row(*, status: str = 'new', opened_at: Any = None) -> dict[str, Any]:
    """SIG alert created by pre-migration 'Open Alert' button: no rule_key in payload,
    matched_patterns[0].rule_id='open_from_detection', module_key=None (not set by old code),
    but has tx_hash."""
    return {
        'id': SIG_ALERT_ID,
        'alert_type': 'threat_monitoring',
        'title': '',
        'severity': 'CRITICAL',
        'status': status,
        'module_key': None,  # old open_alert_from_detection didn't pass module_key
        'target_id': TARGET_ID,
        'detection_id': str(uuid.uuid4()),
        'incident_id': None,
        'source': 'rpc_polling',
        'source_service': 'threat-engine',
        'opened_at': opened_at,
        'payload': {
            # No rule_key — the bug
            'detection_type': 'strategic_infrastructure_guard_outbound_transfer',
            'evidence_source': 'live',
            'tx_hash': TX_HASH,
            'matched_patterns': [
                {'label': 'sig', 'rule_id': 'open_from_detection', 'severity': 'critical'}
            ],
        },
        'evidence_source': 'live',
        'tx_hash': TX_HASH,
        'chain_id': '8453',
        'confidence': 'high',
        'detection_type': 'strategic_infrastructure_guard_outbound_transfer',
    }


# ── Unit tests for _alert_rule_key ───────────────────────────────────────────

def test_alert_rule_key_smoke_old_style():
    """Old smoke alert with no payload.rule_key returns 'open_from_detection'."""
    row = _old_smoke_row()
    row['payload'] = {
        'detection_type': 'monitored_wallet_transfer',
        'matched_patterns': [{'rule_id': 'open_from_detection'}],
    }
    assert _alert_rule_key(row) == 'open_from_detection'


def test_alert_rule_key_sig_old_style():
    """Old SIG alert with no payload.rule_key returns 'open_from_detection'."""
    row = _old_sig_row()
    row['payload'] = {
        'detection_type': 'strategic_infrastructure_guard_outbound_transfer',
        'matched_patterns': [{'rule_id': 'open_from_detection'}],
    }
    assert _alert_rule_key(row) == 'open_from_detection'


# ── Unit tests for _is_wallet_transfer_rule_alert ────────────────────────────

def test_is_wallet_transfer_smoke_via_detection_type():
    """Smoke alert is recognised via detection_type even with open_from_detection rule_id."""
    row = _old_smoke_row()
    rule_key = _alert_rule_key(row)
    assert _is_wallet_transfer_rule_alert(row, rule_key) is True


def test_is_wallet_transfer_sig_via_detection_type():
    """SIG alert is recognised via detection_type even with open_from_detection rule_id."""
    row = _old_sig_row()
    rule_key = _alert_rule_key(row)
    assert _is_wallet_transfer_rule_alert(row, rule_key) is True


def test_is_wallet_transfer_open_from_detection_with_tx_hash():
    """open_from_detection rule_id + tx_hash → recognised as wallet-transfer (belt-and-braces)."""
    row = {
        'module_key': None,
        'detection_type': 'some_unknown_detection_type',  # not in _WALLET_TRANSFER_DETECTION_TYPES
        'tx_hash': TX_HASH,
    }
    rule_key = 'open_from_detection'
    assert _is_wallet_transfer_rule_alert(row, rule_key) is True


def test_is_wallet_transfer_open_from_detection_no_tx_hash():
    """open_from_detection without tx_hash is NOT treated as wallet-transfer."""
    row = {
        'module_key': None,
        'detection_type': 'some_unknown_detection_type',
        'tx_hash': None,
    }
    rule_key = 'open_from_detection'
    assert _is_wallet_transfer_rule_alert(row, rule_key) is False


# ── Unit tests for _normalize_alert_view ────────────────────────────────────

def test_normalize_smoke_old_style_status_and_severity():
    """Old smoke alert is normalised to status='open' and severity='critical'."""
    item = _old_smoke_row()
    item['rule_key'] = _alert_rule_key(item)
    _normalize_alert_view(item)
    assert item['status'] == 'open'
    assert item['severity'] == 'critical'


def test_normalize_sig_old_style_status_and_severity():
    """Old SIG alert is normalised to status='open' and severity='critical'."""
    item = _old_sig_row()
    item['rule_key'] = _alert_rule_key(item)
    _normalize_alert_view(item)
    assert item['status'] == 'open'
    assert item['severity'] == 'critical'


def test_normalize_smoke_old_style_title_filled():
    """Old smoke alert with empty title gets canonical SIG title."""
    item = _old_smoke_row()
    item['title'] = ''
    _normalize_alert_view(item)
    assert 'Strategic Infrastructure Guard' in (item.get('title') or '')


# ── Integration: _run_list_alerts with old-style rows ────────────────────────

def _make_request(workspace_id: str = WS_ID) -> Any:
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


class _MockConn:
    """Mock connection that returns fixed rows and handles self-heal SQL gracefully."""

    def __init__(self, rows: list[dict[str, Any]]):
        self._rows = rows

    def execute(self, sql: str, params=None):
        q = ' '.join(str(sql).split()).lower()
        # Catalog check for opened_at column (self-heal) → say it exists to skip ALTER TABLE
        if 'information_schema.columns' in q:
            return _MR(row={'exists': True})
        # promote_wallet_transfer_alerts UPDATE → no-op
        if 'update alerts' in q and 'opened_at' in q:
            return _MR(rowcount=0)
        return _MR(rows=self._rows)

    def commit(self):
        pass


class _MR:
    def __init__(self, row=None, rows=None, rowcount=0):
        self._row = row
        self._rows = rows or []
        self.rowcount = rowcount

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


def _run_list(rows, **kw):
    from services.api.app.pilot import list_alerts
    conn = _MockConn(rows)
    with (
        patch('services.api.app.pilot.require_live_mode'),
        patch('services.api.app.pilot.pg_connection') as mock_pg,
        patch('services.api.app.pilot.ensure_pilot_schema'),
        patch('services.api.app.pilot.authenticate_with_connection', return_value={'id': 'u1'}),
        patch('services.api.app.pilot.resolve_workspace', return_value={'workspace_id': WS_ID}),
    ):
        mock_pg.return_value.__enter__ = lambda s: conn
        mock_pg.return_value.__exit__ = MagicMock(return_value=False)
        return list_alerts(_make_request(), **kw)


def test_list_alerts_returns_both_old_style_alerts():
    """Both old-style alerts appear in /alerts list."""
    rows = [_old_smoke_row(), _old_sig_row()]
    result = _run_list(rows)
    ids = {a['id'] for a in result['alerts']}
    assert SMOKE_ALERT_ID in ids
    assert SIG_ALERT_ID in ids


def test_list_alerts_old_style_normalised_to_open_critical():
    """Both old-style alerts are normalised to status='open' and severity='critical'."""
    rows = [_old_smoke_row(), _old_sig_row()]
    alerts = _run_list(rows)['alerts']
    for a in alerts:
        assert a['status'] == 'open', f"Expected open, got {a['status']} for {a['id']}"
        assert a['severity'] == 'critical', f"Expected critical, got {a['severity']} for {a['id']}"


def test_list_alerts_old_style_high_confidence():
    """Both old-style alerts qualify as high-confidence (evidence_source=live + tx_hash)."""
    rows = [_old_smoke_row(), _old_sig_row()]
    alerts = _run_list(rows)['alerts']
    high_conf = [
        a for a in alerts
        if str(a.get('evidence_source') or '').lower() in {'live', 'live_provider'} and a.get('tx_hash')
    ]
    assert len(high_conf) == 2


def test_list_alerts_old_style_count_cards():
    """Count cards: active=2, critical=2, high_confidence=2."""
    rows = [_old_smoke_row(), _old_sig_row()]
    alerts = _run_list(rows)['alerts']
    active = sum(1 for a in alerts if str(a.get('status') or '').lower() in {'open', 'acknowledged', 'investigating'})
    critical = sum(1 for a in alerts if str(a.get('severity') or '').lower() == 'critical')
    high_conf = sum(
        1 for a in alerts
        if str(a.get('evidence_source') or '').lower() in {'live', 'live_provider'} and a.get('tx_hash')
    )
    assert active == 2
    assert critical == 2
    assert high_conf == 2
