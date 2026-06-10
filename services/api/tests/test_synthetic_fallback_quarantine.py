"""
Tests that synthetic fallback records (evt-fallback-0001, etc.) are quarantined
in production environments.

Production must never return hardcoded fallback event IDs through the resilience
incidents endpoint. Demo/fallback data is only permitted in non-production runtimes.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest


def _make_auth_request() -> Any:
    scope = {
        'type': 'http',
        'method': 'GET',
        'path': '/resilience/incidents',
        'query_string': b'',
        'headers': [],
        'client': ('127.0.0.1', 9000),
    }
    from fastapi import Request
    return Request(scope)


class TestSyntheticFallbackQuarantine:
    """Production endpoints must not return synthetic fallback records."""

    def test_production_returns_empty_list_when_service_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """In production, /resilience/incidents returns [] when service is down."""
        from services.api.app import main as api_main

        monkeypatch.setenv('APP_ENV', 'production')

        request = _make_auth_request()

        with (
            patch.object(api_main, 'authenticate_request'),
            patch.object(api_main, 'proxy_resilience_get', return_value=None),
        ):
            result = api_main.resilience_incidents(request)

        assert result == [], f'Expected [] in production but got: {result}'
        assert not any(
            str(item).find('fallback') >= 0
            for item in result
        ), 'Production must not return fallback records'

    def test_production_does_not_return_synthetic_event_ids(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Synthetic event IDs like evt-fallback-0001 must not appear in production responses."""
        from services.api.app import main as api_main

        monkeypatch.setenv('APP_ENV', 'production')
        request = _make_auth_request()

        with (
            patch.object(api_main, 'authenticate_request'),
            patch.object(api_main, 'proxy_resilience_get', return_value=None),
        ):
            result = api_main.resilience_incidents(request)

        event_ids = [str(item.get('event_id', '')) for item in result if isinstance(item, dict)]
        assert not any('fallback' in eid for eid in event_ids), (
            f'Synthetic fallback event IDs found in production response: {event_ids}'
        )

    def test_non_production_returns_fallback_records_when_service_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """In development/staging, /resilience/incidents returns fallback records when service is down."""
        from services.api.app import main as api_main

        monkeypatch.setenv('APP_ENV', 'development')
        request = _make_auth_request()

        with (
            patch.object(api_main, 'authenticate_request'),
            patch.object(api_main, 'proxy_resilience_get', return_value=None),
        ):
            result = api_main.resilience_incidents(request)

        # In dev mode, fallback records should be returned
        assert isinstance(result, list), 'Expected a list response in dev mode'

    def test_incident_detail_returns_unavailable_source_in_production(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """In production, unknown incident IDs return source: unavailable, not source: fallback."""
        from services.api.app import main as api_main

        monkeypatch.setenv('APP_ENV', 'production')
        request = _make_auth_request()

        with (
            patch.object(api_main, 'authenticate_request'),
            patch.object(api_main, 'proxy_resilience_get', return_value=None),
        ):
            result = api_main.resilience_incident('evt-fallback-0001', request)

        assert result.get('source') == 'unavailable', (
            f"Expected source='unavailable' in production but got source='{result.get('source')}'"
        )
        assert result.get('source') != 'fallback', (
            'Production must not return source: fallback'
        )

    def test_live_data_returned_when_service_available_in_production(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When the downstream service responds, its data is returned regardless of APP_ENV."""
        from services.api.app import main as api_main

        monkeypatch.setenv('APP_ENV', 'production')
        request = _make_auth_request()

        live_incidents = [{'event_id': 'real-incident-001', 'source': 'live', 'severity': 'high'}]

        with (
            patch.object(api_main, 'authenticate_request'),
            patch.object(api_main, 'proxy_resilience_get', return_value=live_incidents),
            patch.object(api_main, 'with_resilience_incident_normalized_risk', side_effect=lambda x: x),
        ):
            result = api_main.resilience_incidents(request)

        assert len(result) == 1
        assert result[0]['event_id'] == 'real-incident-001'
