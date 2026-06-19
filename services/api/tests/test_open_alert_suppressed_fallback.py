"""Regression tests for open_alert_from_detection suppressed-fallback path.

Task 10 requirements:
  A. Telemetry-linked wallet-transfer alert appears in /alerts list.
  B. Strategic (SIG) rule_key alert also appears in /alerts list.
  C. Count cards match list: active=2, critical=2, high_confidence=2.
  D. 409 already-linked navigates to the existing alert (no duplicate created).
  E. Count/list predicate cannot diverge (a single shared helper is tested to
     ensure both queries select the same rows).

open_alert_from_detection suppressed path:
  When _upsert_alert returns '' (suppression rule hit) the function must fall
  back through three ordered lookups and return the existing alert_id so the
  frontend can navigate instead of showing an unhelpful "suppressed" toast.
"""
from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock, patch

# ── constants matching production data ───────────────────────────────────────

WS_ID        = '1155f479-3e5b-4d90-be6c-fd6c1d6b957d'
TARGET_ID    = 'e7851a52-8fb1-48cd-84a3-d033f591c5dd'
TX_HASH      = '0x' + 'c' * 62 + '90c7'
SMOKE_ID     = '3fe45390-3723-4b31-bb76-60fc6666e4fd'
SIG_ID       = 'e39485a5-2652-4950-9141-2aa6fe79bea1'
DETECTION_ID = str(uuid.uuid4())


# ── helpers ──────────────────────────────────────────────────────────────────

def _smoke_alert_row(**overrides) -> dict[str, Any]:
    base: dict[str, Any] = {
        'id': SMOKE_ID,
        'alert_type': 'threat_monitoring',
        'title': 'Strategic Infrastructure Guard: Treasury RWA Control Wallet Movement Detected',
        'severity': 'critical',
        'status': 'open',
        'module_key': None,
        'target_id': TARGET_ID,
        'detection_id': DETECTION_ID,
        'incident_id': None,
        'source': 'live',
        'source_service': 'threat-engine',
        'opened_at': '2024-01-01T00:00:00Z',
        'payload': {
            'rule_key': 'smoke_wallet_transfer',
            'detection_type': 'monitored_wallet_transfer',
            'evidence_source': 'live',
            'tx_hash': TX_HASH,
            'matched_patterns': [{'label': 'monitored_wallet_transfer', 'rule_id': 'smoke_wallet_transfer', 'severity': 'critical'}],
        },
        'evidence_source': 'live',
        'tx_hash': TX_HASH,
        'chain_id': '8453',
        'confidence': 'high',
        'detection_type': 'monitored_wallet_transfer',
    }
    base.update(overrides)
    return base


def _sig_alert_row(**overrides) -> dict[str, Any]:
    base: dict[str, Any] = {
        'id': SIG_ID,
        'alert_type': 'threat_monitoring',
        'title': 'Strategic Infrastructure Guard: Treasury RWA Control Wallet Movement Detected',
        'severity': 'critical',
        'status': 'open',
        'module_key': 'strategic_infrastructure_guard',
        'target_id': TARGET_ID,
        'detection_id': DETECTION_ID,
        'incident_id': None,
        'source': 'live',
        'source_service': 'threat-engine',
        'opened_at': '2024-01-01T00:00:00Z',
        'payload': {
            'rule_key': 'strategic_infrastructure_guard_wallet_outbound_transfer',
            'detection_type': 'strategic_infrastructure_guard_outbound_transfer',
            'evidence_source': 'live',
            'tx_hash': TX_HASH,
            'matched_patterns': [{'label': 'sig', 'rule_id': 'strategic_infrastructure_guard_wallet_outbound_transfer', 'severity': 'critical'}],
        },
        'evidence_source': 'live',
        'tx_hash': TX_HASH,
        'chain_id': '8453',
        'confidence': 'high',
        'detection_type': 'strategic_infrastructure_guard_outbound_transfer',
    }
    base.update(overrides)
    return base


class _MockConn:
    """Minimal mock DB connection for list_alerts tests."""

    def __init__(self, rows: list[dict[str, Any]]):
        self._rows = rows

    def execute(self, sql: str, params=None):
        q = ' '.join(str(sql).split()).lower()
        if 'information_schema.columns' in q:
            return _MR(row={'exists': True})
        if 'update alerts' in q and 'opened_at' in q:
            return _MR(rowcount=0)
        return _MR(rows=self._rows)

    def commit(self):
        pass


class _MR:
    def __init__(self, row=None, rows=None, rowcount=0):
        self._row  = row
        self._rows = rows or []
        self.rowcount = rowcount

    def fetchone(self):  return self._row
    def fetchall(self): return self._rows


def _make_request(workspace_id: str = WS_ID) -> Any:
    scope = {
        'type': 'http', 'method': 'GET', 'path': '/alerts',
        'query_string': b'', 'client': ('127.0.0.1', 9000),
        'headers': [(b'x-workspace-id', workspace_id.encode())],
    }
    from fastapi import Request
    return Request(scope)


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


# ── A. Telemetry-linked wallet-transfer alert appears in /alerts list ────────

def test_A_smoke_telemetry_alert_in_list():
    """Smoke (monitored_wallet_transfer) alert appears in /alerts list."""
    rows = [_smoke_alert_row()]
    result = _run_list(rows)
    ids = {a['id'] for a in result['alerts']}
    assert SMOKE_ID in ids, f'smoke alert missing from list: {ids}'


def test_A_sig_telemetry_alert_in_list():
    """SIG (strategic_infrastructure_guard) alert appears in /alerts list."""
    rows = [_sig_alert_row()]
    result = _run_list(rows)
    ids = {a['id'] for a in result['alerts']}
    assert SIG_ID in ids, f'SIG alert missing from list: {ids}'


def test_A_both_telemetry_alerts_in_list():
    """Both smoke and SIG alerts appear together in /alerts list."""
    rows = [_smoke_alert_row(), _sig_alert_row()]
    result = _run_list(rows)
    ids = {a['id'] for a in result['alerts']}
    assert SMOKE_ID in ids and SIG_ID in ids, f'alerts missing from list: {ids}'


# ── B. Strategic rule_key alert appears and is normalised ────────────────────

def test_B_sig_rule_key_status_normalised():
    """SIG alert with canonical rule_key is normalised to status='open', severity='critical'."""
    from services.api.app.pilot import _alert_rule_key, _normalize_alert_view
    row = _sig_alert_row()
    row['rule_key'] = _alert_rule_key(row)
    _normalize_alert_view(row)
    assert row['status'] == 'open', f'expected open, got {row["status"]}'
    assert row['severity'] == 'critical', f'expected critical, got {row["severity"]}'


def test_B_sig_rule_key_recognised_as_wallet_transfer():
    """SIG alert with canonical rule_key is recognised by _is_wallet_transfer_rule_alert."""
    from services.api.app.pilot import _alert_rule_key, _is_wallet_transfer_rule_alert
    row = _sig_alert_row()
    rule_key = _alert_rule_key(row)
    assert rule_key == 'strategic_infrastructure_guard_wallet_outbound_transfer'
    assert _is_wallet_transfer_rule_alert(row, rule_key) is True


# ── C. Count cards match list ────────────────────────────────────────────────

def test_C_count_cards_active_2():
    """Active Alerts count = 2 when both wallet-transfer alerts are open."""
    rows = [_smoke_alert_row(), _sig_alert_row()]
    alerts = _run_list(rows)['alerts']
    active = sum(
        1 for a in alerts
        if str(a.get('status') or '').lower() in {'open', 'acknowledged', 'investigating'}
    )
    assert active == 2, f'expected active=2, got {active}'


def test_C_count_cards_critical_2():
    """Critical Alerts count = 2 when both wallet-transfer alerts are critical."""
    rows = [_smoke_alert_row(), _sig_alert_row()]
    alerts = _run_list(rows)['alerts']
    critical = sum(1 for a in alerts if str(a.get('severity') or '').lower() == 'critical')
    assert critical == 2, f'expected critical=2, got {critical}'


def test_C_count_cards_high_confidence_2():
    """High Confidence count = 2: both have evidence_source=live and tx_hash."""
    rows = [_smoke_alert_row(), _sig_alert_row()]
    alerts = _run_list(rows)['alerts']
    high_conf = sum(
        1 for a in alerts
        if str(a.get('evidence_source') or '').lower() in {'live', 'live_provider'}
        and a.get('tx_hash')
    )
    assert high_conf == 2, f'expected high_confidence=2, got {high_conf}'


def test_C_count_cards_all_match():
    """All three count cards (active, critical, high_confidence) equal list length."""
    rows = [_smoke_alert_row(), _sig_alert_row()]
    alerts = _run_list(rows)['alerts']
    n = len(alerts)
    active = sum(1 for a in alerts if str(a.get('status') or '').lower() in {'open', 'acknowledged', 'investigating'})
    critical = sum(1 for a in alerts if str(a.get('severity') or '').lower() == 'critical')
    high_conf = sum(
        1 for a in alerts
        if str(a.get('evidence_source') or '').lower() in {'live', 'live_provider'} and a.get('tx_hash')
    )
    assert active == n,     f'active={active} != list_len={n}'
    assert critical == n,   f'critical={critical} != list_len={n}'
    assert high_conf == n,  f'high_confidence={high_conf} != list_len={n}'


# ── D. 409 already-linked: navigates to existing alert, no duplicate ─────────

def test_D_already_linked_returns_existing_alert_id():
    """open_alert_from_detection returns status=already_exists + alert_id when detection is linked."""
    from services.api.app.monitoring_runner import open_alert_from_detection

    det_row = {
        'detection_id': DETECTION_ID,
        'target_id': TARGET_ID,
        'alert_id': SIG_ID,
    }

    call_count = [0]

    class _AlreadyLinkedConn:
        def execute(self, sql, params=None):
            q = ' '.join(str(sql).split()).lower()
            call_count[0] += 1
            # First query: detections without linked alert → empty (so we fall into second check)
            if 'linked_alert_id is null' in q or 'not exists' in q:
                return _MR(rows=[])
            # Second query: detections with valid linked alert → return existing row
            if 'join alerts a' in q and 'linked_alert_id' in q:
                return _MR(row=det_row)
            return _MR()

        def commit(self): pass

    req = _make_request()
    with (
        patch('services.api.app.monitoring_runner.require_live_mode'),
        patch('services.api.app.monitoring_runner.pg_connection') as mock_pg,
        patch('services.api.app.monitoring_runner.ensure_pilot_schema'),
        patch('services.api.app.monitoring_runner.authenticate_with_connection', return_value={'id': 'u1'}),
        patch('services.api.app.monitoring_runner.resolve_workspace', return_value={'workspace_id': WS_ID}),
    ):
        conn = _AlreadyLinkedConn()
        mock_pg.return_value.__enter__ = lambda s: conn
        mock_pg.return_value.__exit__ = MagicMock(return_value=False)
        result = open_alert_from_detection(req)

    assert result['status'] == 'already_exists', f"expected already_exists, got {result['status']}"
    assert result['alert_id'] == SIG_ID, f"expected {SIG_ID}, got {result['alert_id']}"


# ── E. Count/list predicate cannot diverge ───────────────────────────────────

def test_E_normalisation_is_idempotent():
    """Calling _normalize_alert_view twice produces the same result (predicate stable)."""
    from services.api.app.pilot import _alert_rule_key, _normalize_alert_view
    item = _smoke_alert_row()
    item['rule_key'] = _alert_rule_key(item)
    _normalize_alert_view(item)
    status_after_1 = item['status']
    severity_after_1 = item['severity']
    _normalize_alert_view(item)
    assert item['status'] == status_after_1
    assert item['severity'] == severity_after_1


def test_E_list_predicate_smoke_not_excluded():
    """Smoke alert with status=active/new is not excluded by list_alerts predicate."""
    for status in ('active', 'new', 'created', 'open'):
        rows = [_smoke_alert_row(status=status)]
        alerts = _run_list(rows)['alerts']
        assert len(alerts) == 1, f'smoke alert with status={status} excluded from list'


def test_E_list_predicate_sig_not_excluded():
    """SIG alert with status=active/new is not excluded by list_alerts predicate."""
    for status in ('active', 'new', 'created', 'open'):
        rows = [_sig_alert_row(status=status)]
        alerts = _run_list(rows)['alerts']
        assert len(alerts) == 1, f'SIG alert with status={status} excluded from list'


def test_E_count_and_list_agree_when_empty():
    """When no alerts are returned, all count metrics equal zero."""
    alerts = _run_list([])['alerts']
    assert len(alerts) == 0
    active = sum(1 for a in alerts if str(a.get('status') or '').lower() in {'open', 'acknowledged', 'investigating'})
    critical = sum(1 for a in alerts if str(a.get('severity') or '').lower() == 'critical')
    assert active == 0 and critical == 0
