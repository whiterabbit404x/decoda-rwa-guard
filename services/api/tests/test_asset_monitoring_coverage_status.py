"""
Tests for _compute_asset_monitoring_coverage in pilot.py.

Coverage rules (fail-closed):
- live_verified:          target + monitored system + live telemetry
- waiting_for_telemetry:  target + monitored system but no heartbeat or no live telemetry
- not_configured:         no linked target or no monitored system
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock


def _install_fastapi_stubs() -> None:
    if 'fastapi' in sys.modules:
        return
    fastapi_stub = types.ModuleType('fastapi')
    fastapi_stub.HTTPException = Exception
    fastapi_stub.Request = object
    fastapi_stub.status = MagicMock()
    fastapi_stub.FastAPI = MagicMock()
    sys.modules['fastapi'] = fastapi_stub
    sys.modules['fastapi.middleware.cors'] = MagicMock()
    sys.modules['fastapi.responses'] = MagicMock()
    sys.modules['fastapi.staticfiles'] = MagicMock()
    sys.modules['starlette'] = MagicMock()
    sys.modules['starlette.responses'] = MagicMock()
    sys.modules['psycopg'] = MagicMock()
    sys.modules['psycopg.rows'] = MagicMock()
    sys.modules['psycopg_pool'] = MagicMock()
    sys.modules['uvicorn'] = MagicMock()
    for dep in (
        'anthropic', 'stripe', 'boto3', 'botocore',
        'httpx', 'aiofiles', 'pydantic',
        'pydantic.functional_validators',
    ):
        sys.modules.setdefault(dep, MagicMock())


_install_fastapi_stubs()

from services.api.app.pilot import _compute_asset_monitoring_coverage  # noqa: E402


def _asset(
    *,
    monitoring_link_status: str = 'not_configured',
    has_linked_monitored_system: bool = False,
    has_heartbeat: bool = False,
    has_telemetry: bool = False,
    linked_target_id: str | None = None,
    linked_target_name: str | None = None,
    last_telemetry_at: object = None,
    live_telemetry_count: int = 0,
) -> dict:
    return {
        'monitoring_link_status': monitoring_link_status,
        'has_linked_monitored_system': has_linked_monitored_system,
        'has_heartbeat': has_heartbeat,
        'has_telemetry': has_telemetry,
        'linked_target_id': linked_target_id,
        'linked_target_name': linked_target_name,
        'last_telemetry_at': last_telemetry_at,
        'live_telemetry_count': live_telemetry_count,
    }


def test_no_linked_target_returns_not_configured() -> None:
    result = _compute_asset_monitoring_coverage(_asset())
    assert result['monitoring_status'] == 'not_configured'
    assert result['next_action'] == 'Add monitoring source'


def test_target_missing_link_status_returns_not_configured() -> None:
    result = _compute_asset_monitoring_coverage(_asset(
        monitoring_link_status='target_missing',
        linked_target_id='target-1',
    ))
    assert result['monitoring_status'] == 'not_configured'
    assert result['next_action'] == 'Add monitoring source'


def test_system_missing_returns_not_configured() -> None:
    result = _compute_asset_monitoring_coverage(_asset(
        monitoring_link_status='system_missing',
        linked_target_id='target-1',
        has_linked_monitored_system=False,
    ))
    assert result['monitoring_status'] == 'not_configured'
    assert result['next_action'] == 'Add monitoring source'


def test_attached_target_no_heartbeat_returns_waiting_for_telemetry() -> None:
    result = _compute_asset_monitoring_coverage(_asset(
        monitoring_link_status='attached',
        linked_target_id='target-1',
        has_linked_monitored_system=True,
        has_heartbeat=False,
        has_telemetry=False,
    ))
    assert result['monitoring_status'] == 'waiting_for_telemetry'
    assert result['next_action'] == 'Verify telemetry'


def test_attached_with_heartbeat_but_no_telemetry_returns_waiting() -> None:
    result = _compute_asset_monitoring_coverage(_asset(
        monitoring_link_status='attached',
        linked_target_id='target-1',
        has_linked_monitored_system=True,
        has_heartbeat=True,
        has_telemetry=False,
    ))
    assert result['monitoring_status'] == 'waiting_for_telemetry'
    assert result['next_action'] == 'Verify telemetry'


def test_live_telemetry_returns_live_verified() -> None:
    result = _compute_asset_monitoring_coverage(_asset(
        monitoring_link_status='attached',
        linked_target_id='target-1',
        has_linked_monitored_system=True,
        has_heartbeat=True,
        has_telemetry=True,
        live_telemetry_count=5,
    ))
    assert result['monitoring_status'] == 'live_verified'
    assert result['monitoring_label'] == 'Live telemetry verified'
    assert result['next_action'] == 'View telemetry'


def test_simulator_telemetry_not_counted_as_live() -> None:
    # has_telemetry=False means SQL found no evidence_source='live' rows
    result = _compute_asset_monitoring_coverage(_asset(
        monitoring_link_status='attached',
        linked_target_id='target-1',
        has_linked_monitored_system=True,
        has_heartbeat=True,
        has_telemetry=False,  # simulator rows don't set has_telemetry=True
    ))
    assert result['monitoring_status'] == 'waiting_for_telemetry'
    assert result['monitoring_status'] != 'live_verified'


def test_live_verified_label_is_human_readable() -> None:
    result = _compute_asset_monitoring_coverage(_asset(
        monitoring_link_status='attached',
        linked_target_id='target-uuid',
        has_linked_monitored_system=True,
        has_heartbeat=True,
        has_telemetry=True,
    ))
    assert result['monitoring_label'] == 'Live telemetry verified'


def test_not_configured_label_is_human_readable() -> None:
    result = _compute_asset_monitoring_coverage(_asset())
    assert result['monitoring_label'] == 'Not configured'
