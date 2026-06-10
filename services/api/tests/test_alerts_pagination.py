"""
Pagination tests for the /alerts endpoint.

Tests workspace isolation, limit/offset parameter behaviour, and max page size.
"""
from __future__ import annotations

import uuid
from collections.abc import Generator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


def _make_request(workspace_id: str | None = None) -> Any:
    scope = {
        'type': 'http',
        'method': 'GET',
        'path': '/alerts',
        'query_string': b'',
        'headers': [],
        'client': ('127.0.0.1', 9000),
    }
    if workspace_id:
        scope['headers'] = [(b'x-workspace-id', workspace_id.encode())]
    from fastapi import Request
    return Request(scope)


def _fake_alert_rows(count: int, workspace_id: str) -> list[dict[str, Any]]:
    return [
        {
            'id': str(uuid.uuid4()),
            'alert_type': 'test',
            'title': f'Alert {i}',
            'severity': 'medium',
            'status': 'open',
            'summary': None,
            'module_key': None,
            'target_id': None,
            'detection_id': None,
            'incident_id': None,
            'assigned_to': None,
            'evidence_summary': None,
            'source': 'live',
            'source_service': None,
            'recommended_action': None,
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
            'created_at': f'2026-01-{i + 1:02d}T00:00:00Z',
            'updated_at': None,
            'linked_evidence_count': 0,
            'last_evidence_at': None,
            'evidence_source': None,
            'tx_hash': None,
            'block_number': None,
            'detector_kind': None,
            'evidence_origin': None,
            'linked_action_id': None,
            'response_action_mode': None,
            'workspace_id': workspace_id,
        }
        for i in range(count)
    ]


def _make_fake_connection(rows: list[dict[str, Any]], workspace_id: str):
    class FakeCursor:
        def fetchall(self_inner):
            return [MagicMock(**row, **{'__iter__': lambda s: iter(row.items()), 'keys': lambda: row.keys()}) for row in rows]

    class FakeResult:
        def fetchall(self_inner):
            # Return objects that support dict() conversion
            result = []
            for row in rows:
                m = MagicMock()
                m.__iter__ = lambda s, r=row: iter(r.items())
                m.keys = lambda r=row: r.keys()
                result.append(m)
            return result

    class FakeConn:
        def execute(self_inner, sql, params):
            return FakeResult()

    return FakeConn()


class TestAlertsPagination:
    """Test pagination parameters are validated and forwarded correctly."""

    def test_limit_capped_at_200(self):
        """list_alerts must clamp limit to max 200."""
        from services.api.app.pilot import list_alerts

        ws_id = str(uuid.uuid4())
        request = _make_request(ws_id)

        executed_params: list = []

        class CapturingConn:
            def execute(self_inner, sql, params):
                executed_params.extend(params)

                class R:
                    def fetchall(self_inner2):
                        return []
                return R()

        with (
            patch('services.api.app.pilot.require_live_mode'),
            patch('services.api.app.pilot.pg_connection') as mock_pg,
            patch('services.api.app.pilot.ensure_pilot_schema'),
            patch('services.api.app.pilot.authenticate_with_connection', return_value={'id': str(uuid.uuid4())}),
            patch('services.api.app.pilot.resolve_workspace', return_value={'workspace_id': ws_id}),
        ):
            mock_pg.return_value.__enter__ = lambda s: CapturingConn()
            mock_pg.return_value.__exit__ = MagicMock(return_value=False)

            list_alerts(request, limit=9999, offset=0)

        # The effective limit passed to SQL must be <= 200
        limit_in_params = executed_params[-2]
        assert limit_in_params <= 200, f'Expected limit <= 200 but got {limit_in_params}'

    def test_offset_cannot_be_negative(self):
        """list_alerts must floor negative offsets to 0."""
        from services.api.app.pilot import list_alerts

        ws_id = str(uuid.uuid4())
        request = _make_request(ws_id)
        executed_params: list = []

        class CapturingConn:
            def execute(self_inner, sql, params):
                executed_params.extend(params)

                class R:
                    def fetchall(self_inner2):
                        return []
                return R()

        with (
            patch('services.api.app.pilot.require_live_mode'),
            patch('services.api.app.pilot.pg_connection') as mock_pg,
            patch('services.api.app.pilot.ensure_pilot_schema'),
            patch('services.api.app.pilot.authenticate_with_connection', return_value={'id': str(uuid.uuid4())}),
            patch('services.api.app.pilot.resolve_workspace', return_value={'workspace_id': ws_id}),
        ):
            mock_pg.return_value.__enter__ = lambda s: CapturingConn()
            mock_pg.return_value.__exit__ = MagicMock(return_value=False)

            list_alerts(request, limit=50, offset=-100)

        offset_in_params = executed_params[-1]
        assert offset_in_params >= 0, f'Expected offset >= 0 but got {offset_in_params}'

    def test_pagination_metadata_returned(self):
        """list_alerts must return a pagination dict with limit, offset, has_more."""
        from services.api.app.pilot import list_alerts

        ws_id = str(uuid.uuid4())
        request = _make_request(ws_id)

        class CapturingConn:
            def execute(self_inner, sql, params):
                class R:
                    def fetchall(self_inner2):
                        return []
                return R()

        with (
            patch('services.api.app.pilot.require_live_mode'),
            patch('services.api.app.pilot.pg_connection') as mock_pg,
            patch('services.api.app.pilot.ensure_pilot_schema'),
            patch('services.api.app.pilot.authenticate_with_connection', return_value={'id': str(uuid.uuid4())}),
            patch('services.api.app.pilot.resolve_workspace', return_value={'workspace_id': ws_id}),
        ):
            mock_pg.return_value.__enter__ = lambda s: CapturingConn()
            mock_pg.return_value.__exit__ = MagicMock(return_value=False)

            result = list_alerts(request, limit=25, offset=50)

        assert 'alerts' in result
        assert 'pagination' in result
        pagination = result['pagination']
        assert pagination['limit'] == 25
        assert pagination['offset'] == 50
        assert 'has_more' in pagination

    def test_has_more_true_when_full_page_returned(self):
        """has_more must be True when len(alerts) == limit."""
        from services.api.app.pilot import list_alerts

        ws_id = str(uuid.uuid4())
        request = _make_request(ws_id)
        limit = 5

        class CapturingConn:
            def execute(self_inner, sql, params):
                class R:
                    def fetchall(self_inner2):
                        # Return exactly `limit` rows to simulate a full page
                        rows = []
                        for i in range(limit):
                            m = MagicMock()
                            row = {
                                'id': str(uuid.uuid4()), 'alert_type': 'test', 'title': f'A{i}',
                                'severity': 'low', 'status': 'open', 'summary': None, 'module_key': None,
                                'target_id': None, 'detection_id': None, 'incident_id': None,
                                'assigned_to': None, 'evidence_summary': None, 'source': 'live',
                                'source_service': None, 'recommended_action': None, 'degraded': False,
                                'occurrence_count': 1, 'last_seen_at': None, 'findings': None,
                                'owner_user_id': None, 'triage_status': None, 'resolution_note': None,
                                'suppressed_until': None, 'acknowledged_at': None, 'resolved_at': None,
                                'created_at': '2026-01-01T00:00:00Z', 'updated_at': None,
                                'linked_evidence_count': 0, 'last_evidence_at': None,
                                'evidence_source': None, 'tx_hash': None, 'block_number': None,
                                'detector_kind': None, 'evidence_origin': None,
                                'linked_action_id': None, 'response_action_mode': None,
                                'workspace_id': ws_id,
                            }
                            m.__iter__ = lambda s, r=row: iter(r.items())
                            m.keys = lambda r=row: r.keys()
                            rows.append(m)
                        return rows
                return R()

        with (
            patch('services.api.app.pilot.require_live_mode'),
            patch('services.api.app.pilot.pg_connection') as mock_pg,
            patch('services.api.app.pilot.ensure_pilot_schema'),
            patch('services.api.app.pilot.authenticate_with_connection', return_value={'id': str(uuid.uuid4())}),
            patch('services.api.app.pilot.resolve_workspace', return_value={'workspace_id': ws_id}),
        ):
            mock_pg.return_value.__enter__ = lambda s: CapturingConn()
            mock_pg.return_value.__exit__ = MagicMock(return_value=False)

            result = list_alerts(request, limit=limit, offset=0)

        assert result['pagination']['has_more'] is True
        assert len(result['alerts']) == limit

    def test_workspace_isolation_enforced(self):
        """Workspace ID is always included in query params."""
        from services.api.app.pilot import list_alerts

        ws_id = str(uuid.uuid4())
        request = _make_request(ws_id)
        executed_params: list = []

        class CapturingConn:
            def execute(self_inner, sql, params):
                executed_params.extend(params)

                class R:
                    def fetchall(self_inner2):
                        return []
                return R()

        with (
            patch('services.api.app.pilot.require_live_mode'),
            patch('services.api.app.pilot.pg_connection') as mock_pg,
            patch('services.api.app.pilot.ensure_pilot_schema'),
            patch('services.api.app.pilot.authenticate_with_connection', return_value={'id': str(uuid.uuid4())}),
            patch('services.api.app.pilot.resolve_workspace', return_value={'workspace_id': ws_id}),
        ):
            mock_pg.return_value.__enter__ = lambda s: CapturingConn()
            mock_pg.return_value.__exit__ = MagicMock(return_value=False)

            list_alerts(request, limit=50, offset=0)

        # First param must be workspace_id
        assert executed_params[0] == ws_id
