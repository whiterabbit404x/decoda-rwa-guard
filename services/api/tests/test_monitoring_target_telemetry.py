from __future__ import annotations

import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from services.api.app import main as api_main
from services.api.app.monitoring_runner import (
    _TELEMETRY_ALLOWED_EVENT_TYPE_FILTERS,
    list_target_telemetry,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

# --- Route spelling ---

def test_telemetry_route_is_spelled_correctly_in_main():
    content = (REPO_ROOT / 'services/api/app/main.py').read_text(encoding='utf-8')
    assert "@app.get('/monitoring/targets/{target_id}/telemetry'" in content
    assert "telemtry" not in content


def test_telemetry_function_is_exported_from_monitoring_runner():
    import services.api.app.monitoring_runner as mr
    assert hasattr(mr, 'list_target_telemetry')
    assert callable(mr.list_target_telemetry)


# --- Allowed filter constants ---

def test_allowed_filters_include_wallet_transfers():
    assert 'wallet_transfers' in _TELEMETRY_ALLOWED_EVENT_TYPE_FILTERS


def test_allowed_filters_include_alerts_only():
    assert 'alerts_only' in _TELEMETRY_ALLOWED_EVENT_TYPE_FILTERS


def test_allowed_filters_include_wallet_transfer_detected():
    assert 'wallet_transfer_detected' in _TELEMETRY_ALLOWED_EVENT_TYPE_FILTERS


def test_allowed_filters_include_rpc_polling():
    assert 'rpc_polling' in _TELEMETRY_ALLOWED_EVENT_TYPE_FILTERS


# --- UUID validation ---

def test_invalid_uuid_raises_400():
    fake_request = MagicMock()
    fake_request.headers = {}
    with pytest.raises(HTTPException) as exc_info:
        list_target_telemetry(fake_request, target_id='not-a-uuid')
    assert exc_info.value.status_code == 400
    assert 'UUID' in str(exc_info.value.detail)


def test_empty_string_uuid_raises_400():
    fake_request = MagicMock()
    fake_request.headers = {}
    with pytest.raises(HTTPException) as exc_info:
        list_target_telemetry(fake_request, target_id='')
    assert exc_info.value.status_code == 400


def test_non_uuid_does_not_reach_database(monkeypatch):
    db_called = []

    def fake_pg_connection():
        db_called.append(True)
        return MagicMock()

    monkeypatch.setattr('services.api.app.monitoring_runner.pg_connection', fake_pg_connection)
    fake_request = MagicMock()
    fake_request.headers = {}
    with pytest.raises(HTTPException):
        list_target_telemetry(fake_request, target_id='invalid-uuid-value')
    assert not db_called, 'DB must not be called for invalid UUID'


# --- DB-level filter tests (mock pg_connection) ---

def _make_request(workspace_id: str) -> Any:
    scope = {
        'type': 'http',
        'method': 'GET',
        'path': '/monitoring/targets/x/telemetry',
        'query_string': b'',
        'headers': [(b'x-workspace-id', workspace_id.encode())],
        'client': ('127.0.0.1', 9000),
    }
    from fastapi import Request
    return Request(scope)


def _make_dummy_row(workspace_id: str, target_id: str, event_type: str = 'wallet_transfer_detected') -> dict:
    return {
        'id': str(uuid.uuid4()), 'workspace_id': workspace_id, 'target_id': target_id,
        'provider_type': 'evm_rpc', 'source_type': event_type,
        'evidence_source': 'live', 'observed_at': '2026-06-01T10:00:00Z',
        'ingested_at': '2026-06-01T10:00:01Z',
        'payload_json': {'tx_hash': '0xabc', 'block_number': 1000},
        'chain_network': 'base', 'receipt_block_number': None,
    }


class CapturingConn:
    """Minimal fake DB connection that records executed SQL and returns configurable data."""

    def __init__(self, rows: list[dict] | None = None, count: int = 0,
                 workspace_id: str = '', target_id: str = ''):
        # If rows not specified, auto-generate dummy rows matching the count
        if rows is None and count > 0:
            ws = workspace_id or str(uuid.uuid4())
            tgt = target_id or str(uuid.uuid4())
            self._rows = [_make_dummy_row(ws, tgt) for _ in range(count)]
        else:
            self._rows = rows or []
        self._count = count
        self.executed_sqls: list[str] = []
        self.executed_params: list[Any] = []
        self._call_num = 0

    def execute(self, sql: str, params: Any = None):
        self.executed_sqls.append(sql)
        self.executed_params.append(params or [])
        self._call_num += 1
        call_num = self._call_num
        rows = self._rows
        count = self._count

        class _Result:
            def fetchone(inner_self):
                return {'cnt': count}

            def fetchall(inner_self):
                result = []
                for row in rows:
                    m = MagicMock()
                    m.__iter__ = lambda s, r=row: iter(r.items())
                    m.keys = lambda r=row: r.keys()
                    result.append(m)
                return result

        return _Result()


def _patch_monitoring_runner(conn: CapturingConn, workspace_id: str):
    """Return a context manager that patches pg_connection + auth helpers."""
    mock_pg = MagicMock()
    mock_pg.return_value.__enter__ = lambda s: conn
    mock_pg.return_value.__exit__ = MagicMock(return_value=False)

    return (
        patch('services.api.app.monitoring_runner.pg_connection', mock_pg),
        patch('services.api.app.monitoring_runner.ensure_pilot_schema'),
        patch(
            'services.api.app.monitoring_runner.authenticate_with_connection',
            return_value={'id': str(uuid.uuid4())},
        ),
        patch(
            'services.api.app.monitoring_runner.resolve_workspace',
            return_value={'workspace_id': workspace_id, 'workspace': {}},
        ),
    )


def _run_telemetry(
    target_id: str,
    workspace_id: str,
    conn: CapturingConn,
    **kwargs: Any,
) -> dict:
    request = _make_request(workspace_id)
    patches = _patch_monitoring_runner(conn, workspace_id)
    with patches[0], patches[1], patches[2], patches[3]:
        return list_target_telemetry(request, target_id=target_id, **kwargs)


# --- All tab: no event_type filter ---

def test_all_tab_no_event_type_filter_in_sql():
    target_id = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    conn = CapturingConn(count=0)

    _run_telemetry(target_id, ws_id, conn)

    # Neither wallet_transfer_detected nor rpc_polling should be constrained
    for sql in conn.executed_sqls:
        assert 'event_type' not in sql or 'event_type' in sql  # SELECT columns may mention it
    data_sql = conn.executed_sqls[-1]
    assert "event_type = %s" not in data_sql
    assert "event_type IN" not in data_sql


def test_all_tab_returns_pagination_fields():
    target_id = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    conn = CapturingConn(count=3)

    result = _run_telemetry(target_id, ws_id, conn, limit=50, offset=0)

    assert 'total_count' in result
    assert 'has_next' in result
    assert 'has_prev' in result
    assert 'page' in result
    assert 'page_size' in result
    assert result['total_count'] == 3
    assert result['has_next'] is False
    assert result['has_prev'] is False


# --- Wallet transfers tab ---

def test_wallet_transfers_filter_uses_in_clause():
    target_id = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    conn = CapturingConn(count=2)

    _run_telemetry(target_id, ws_id, conn, event_type_filter='wallet_transfers')

    data_sql = conn.executed_sqls[-1]
    assert "wallet_transfer_detected" in data_sql
    assert "native_transfer" in data_sql
    assert "IN" in data_sql.upper()


def test_wallet_transfers_filter_returns_wallet_transfer_detected_rows():
    target_id = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    row = {
        'id': str(uuid.uuid4()), 'workspace_id': ws_id, 'target_id': target_id,
        'provider_type': 'evm_rpc', 'source_type': 'wallet_transfer_detected',
        'evidence_source': 'live', 'observed_at': '2026-06-01T10:00:00Z',
        'ingested_at': '2026-06-01T10:00:01Z',
        'payload_json': {'tx_hash': '0xb212', 'block_number': 1000},
        'chain_network': 'base', 'receipt_block_number': None,
    }
    conn = CapturingConn(rows=[row], count=1)

    result = _run_telemetry(target_id, ws_id, conn, event_type_filter='wallet_transfers')

    assert result['total_count'] == 1
    assert len(result['telemetry']) == 1


def test_wallet_transfer_detected_exact_filter_still_works():
    """Direct event_type=wallet_transfer_detected (exact) still queries only that type."""
    target_id = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    conn = CapturingConn(count=1)

    _run_telemetry(target_id, ws_id, conn, event_type_filter='wallet_transfer_detected')

    data_sql = conn.executed_sqls[-1]
    assert "te.event_type = %s" in data_sql
    # Should NOT use the IN clause (that's only for wallet_transfers alias)
    assert "native_transfer" not in data_sql


# --- RPC polling tab ---

def test_rpc_polling_filter_uses_exact_match():
    target_id = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    conn = CapturingConn(count=1)

    _run_telemetry(target_id, ws_id, conn, event_type_filter='rpc_polling')

    data_sql = conn.executed_sqls[-1]
    assert "te.event_type = %s" in data_sql

    # The param for the event_type clause must be 'rpc_polling'
    all_params_flat = [p for params in conn.executed_params for p in (params if isinstance(params, list) else [])]
    assert 'rpc_polling' in all_params_flat


# --- Alerts only tab ---

def test_alerts_only_filter_uses_exists_join():
    target_id = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    conn = CapturingConn(count=0)

    _run_telemetry(target_id, ws_id, conn, event_type_filter='alerts_only')

    data_sql = conn.executed_sqls[-1]
    assert 'EXISTS' in data_sql.upper()
    assert 'alerts' in data_sql
    assert "telemetry_id" in data_sql


def test_alerts_only_empty_when_no_alert_linked():
    target_id = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    conn = CapturingConn(rows=[], count=0)

    result = _run_telemetry(target_id, ws_id, conn, event_type_filter='alerts_only')

    assert result['total_count'] == 0
    assert result['telemetry'] == []


# --- Unknown / invalid filter is silently ignored ---

def test_unknown_event_type_filter_is_ignored():
    target_id = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    conn = CapturingConn(count=0)

    _run_telemetry(target_id, ws_id, conn, event_type_filter='some_unknown_filter')

    data_sql = conn.executed_sqls[-1]
    assert "some_unknown_filter" not in data_sql


# --- Pagination ---

def test_pagination_has_next_when_more_rows_exist():
    target_id = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    # Simulate DB returning 50 rows (page 1) while total is 55
    page_rows = [_make_dummy_row(ws_id, target_id) for _ in range(50)]
    conn = CapturingConn(rows=page_rows, count=55)

    result = _run_telemetry(target_id, ws_id, conn, limit=50, offset=0)

    assert result['has_next'] is True
    assert result['has_prev'] is False
    assert result['total_count'] == 55


def test_pagination_has_prev_on_second_page():
    target_id = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    conn = CapturingConn(count=55)

    result = _run_telemetry(target_id, ws_id, conn, limit=50, offset=50)

    assert result['has_prev'] is True
    assert result['has_next'] is False
    assert result['page'] == 1


def test_pagination_both_false_on_single_page():
    target_id = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    conn = CapturingConn(count=3)

    result = _run_telemetry(target_id, ws_id, conn, limit=50, offset=0)

    assert result['has_next'] is False
    assert result['has_prev'] is False
    assert result['page'] == 0
    assert result['page_size'] == 50


# --- Empty state via TestClient (requires real FastAPI) ---

def _make_empty_telemetry_response(target_id: str) -> dict:
    return {
        'telemetry': [],
        'target_id': target_id,
        'workspace_id': str(uuid.uuid4()),
        'live_telemetry_ready': False,
        'total_count': 0,
        'page': 0,
        'page_size': 50,
        'has_next': False,
        'has_prev': False,
        'has_more': False,
        'message': 'No live telemetry has been persisted for this target yet.',
    }


def _make_telemetry_rows(target_id: str, workspace_id: str) -> dict:
    row_id = str(uuid.uuid4())
    return {
        'telemetry': [
            {
                'id': row_id,
                'workspace_id': workspace_id,
                'target_id': target_id,
                'provider_type': 'rpc',
                'source_type': 'wallet_transfer_detected',
                'evidence_source': 'live',
                'chain_id': '8453',
                'block_number': 19000000,
                'observed_at': '2026-05-26T10:00:00+00:00',
                'ingested_at': '2026-05-26T10:00:01+00:00',
                'payload_json': {'tx_hash': '0xabc', 'block_number': 19000000},
            }
        ],
        'target_id': target_id,
        'workspace_id': workspace_id,
        'live_telemetry_ready': True,
        'total_count': 1,
        'page': 0,
        'page_size': 50,
        'has_next': False,
        'has_prev': False,
        'has_more': False,
    }


def test_no_telemetry_returns_200_empty_state(monkeypatch):
    valid_id = str(uuid.uuid4())
    client = TestClient(api_main.app)
    monkeypatch.setattr(
        api_main,
        'list_target_telemetry',
        lambda request, target_id, limit=50, offset=0, q=None, event_type_filter=None:
            _make_empty_telemetry_response(target_id),
    )
    response = client.get(f'/monitoring/targets/{valid_id}/telemetry')
    assert response.status_code == 200
    data = response.json()
    assert data['telemetry'] == []
    assert data['live_telemetry_ready'] is False
    assert 'No live telemetry' in data['message']
    assert data['target_id'] == valid_id


def test_with_telemetry_returns_200_with_rows(monkeypatch):
    valid_id = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    client = TestClient(api_main.app)
    monkeypatch.setattr(
        api_main,
        'list_target_telemetry',
        lambda request, target_id, limit=50, offset=0, q=None, event_type_filter=None:
            _make_telemetry_rows(target_id, ws_id),
    )
    response = client.get(f'/monitoring/targets/{valid_id}/telemetry')
    assert response.status_code == 200
    data = response.json()
    assert len(data['telemetry']) == 1
    assert data['live_telemetry_ready'] is True
    row = data['telemetry'][0]
    for field in ('id', 'provider_type', 'source_type', 'evidence_source', 'chain_id',
                  'block_number', 'observed_at', 'payload_json'):
        assert field in row, f'Expected field {field!r} in telemetry row'


def test_with_telemetry_returns_pagination_fields(monkeypatch):
    valid_id = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    client = TestClient(api_main.app)
    monkeypatch.setattr(
        api_main,
        'list_target_telemetry',
        lambda request, target_id, limit=50, offset=0, q=None, event_type_filter=None:
            _make_telemetry_rows(target_id, ws_id),
    )
    response = client.get(f'/monitoring/targets/{valid_id}/telemetry')
    assert response.status_code == 200
    data = response.json()
    assert 'total_count' in data
    assert 'has_next' in data
    assert 'has_prev' in data
    assert 'page' in data
    assert 'page_size' in data


def test_invalid_uuid_via_http_returns_400():
    client = TestClient(api_main.app)
    response = client.get('/monitoring/targets/not-a-uuid/telemetry')
    assert response.status_code == 400
