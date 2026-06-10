"""
Pagination tests for the /incidents endpoint.

Tests workspace isolation, limit/offset parameter behaviour, and max page size.
"""
from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


def _make_request(workspace_id: str | None = None) -> Any:
    scope = {
        'type': 'http',
        'method': 'GET',
        'path': '/incidents',
        'query_string': b'',
        'headers': [],
        'client': ('127.0.0.1', 9000),
    }
    if workspace_id:
        scope['headers'] = [(b'x-workspace-id', workspace_id.encode())]
    from fastapi import Request
    return Request(scope)


class TestIncidentsPagination:
    """Test pagination parameters are validated and forwarded correctly."""

    def test_limit_capped_at_200(self):
        """list_incidents must clamp limit to max 200."""
        from services.api.app.pilot import list_incidents

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

            list_incidents(request, limit=9999, offset=0)

        limit_in_params = executed_params[-2]
        assert limit_in_params <= 200

    def test_offset_cannot_be_negative(self):
        """list_incidents must floor negative offsets to 0."""
        from services.api.app.pilot import list_incidents

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

            list_incidents(request, limit=50, offset=-50)

        offset_in_params = executed_params[-1]
        assert offset_in_params >= 0

    def test_pagination_metadata_returned(self):
        """list_incidents must return a pagination dict with limit, offset, has_more."""
        from services.api.app.pilot import list_incidents

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

            result = list_incidents(request, limit=10, offset=20)

        assert 'incidents' in result
        assert 'pagination' in result
        assert result['pagination']['limit'] == 10
        assert result['pagination']['offset'] == 20
        assert 'has_more' in result['pagination']

    def test_workspace_isolation_enforced(self):
        """Workspace ID is always the first query parameter."""
        from services.api.app.pilot import list_incidents

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

            list_incidents(request, limit=50, offset=0)

        assert executed_params[0] == ws_id

    def test_has_more_false_when_partial_page(self):
        """has_more must be False when fewer rows than limit are returned."""
        from services.api.app.pilot import list_incidents

        ws_id = str(uuid.uuid4())
        request = _make_request(ws_id)
        limit = 50

        class CapturingConn:
            def execute(self_inner, sql, params):
                class R:
                    def fetchall(self_inner2):
                        # Return only 3 rows — less than limit
                        rows = []
                        for i in range(3):
                            row = {
                                'id': str(uuid.uuid4()), 'event_type': 'test', 'title': f'Inc {i}',
                                'severity': 'low', 'status': 'open', 'workflow_status': 'open',
                                'target_id': None, 'source_alert_id': None, 'linked_alert_ids': None,
                                'owner': None, 'owner_user_id': None, 'assignee_user_id': None,
                                'summary': None, 'resolution_note': None, 'resolution_notes': None,
                                'timeline': None, 'created_at': '2026-01-01T00:00:00Z', 'updated_at': None,
                                'linked_detection_id': None, 'linked_evidence_count': 0,
                                'last_evidence_at': None, 'evidence_source': None, 'tx_hash': None,
                                'block_number': None, 'detector_kind': None, 'evidence_origin': None,
                                'linked_action_id': None, 'response_action_mode': None,
                                'workspace_id': ws_id,
                            }
                            m = MagicMock()
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

            result = list_incidents(request, limit=limit, offset=0)

        assert result['pagination']['has_more'] is False
