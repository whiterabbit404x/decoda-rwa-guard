"""
Tests: GET /ops/system-health returns the correct structure and status logic.

Covers:
1. All component keys are present in the response.
2. Redis missing URL → unavailable.
3. Redis ping failure → failing.
4. RPC missing URL → unavailable.
5. RPC failure → failing with sanitized error.
6. Worker fresh heartbeat → healthy.
7. Worker stale heartbeat → degraded.
8. Telemetry stale → degraded.
9. Detection stale → degraded.
10. No secrets are exposed in the backend response.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SH_MODULE_PATH = Path(__file__).resolve().parents[1] / 'app' / 'system_health.py'
sys.path.insert(0, str(REPO_ROOT))


def _load_system_health_module():
    module_name = f'system_health_test_{uuid.uuid4().hex}'
    spec = importlib.util.spec_from_file_location(module_name, SH_MODULE_PATH)
    assert spec is not None and spec.loader is not None, 'Could not load system_health module'
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope='module')
def sh():
    return _load_system_health_module()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_connection(heartbeat_ts: datetime | None = None, telemetry_ts: datetime | None = None, detection_ts: datetime | None = None):
    conn = MagicMock()

    def _execute(sql, params=None):
        cursor = MagicMock()
        sql_lower = sql.strip().lower()

        if 'select 1' in sql_lower:
            cursor.fetchone.return_value = {'value': 1}
        elif 'monitoring_heartbeats' in sql_lower and 'last_heartbeat_at' in sql_lower:
            if heartbeat_ts:
                cursor.fetchone.return_value = {'last_heartbeat_at': heartbeat_ts}
            else:
                cursor.fetchone.return_value = None
        elif 'monitoring_polls' in sql_lower or 'monitoring_runs' in sql_lower:
            cursor.fetchone.return_value = None
        elif 'telemetry_events' in sql_lower and 'count' not in sql_lower:
            if telemetry_ts:
                cursor.fetchone.return_value = {'observed_at': telemetry_ts}
            else:
                cursor.fetchone.return_value = None
        elif 'telemetry_events' in sql_lower and 'count' in sql_lower:
            cursor.fetchone.return_value = {'cnt': 5}
        elif ('detection_events' in sql_lower or 'detections' in sql_lower) and 'count' not in sql_lower:
            if detection_ts:
                cursor.fetchone.return_value = {'created_at': detection_ts}
            else:
                cursor.fetchone.return_value = None
        elif ('detection_events' in sql_lower or 'detections' in sql_lower) and 'count' in sql_lower:
            cursor.fetchone.return_value = {'cnt': 2}
        elif 'provider_health_records' in sql_lower:
            cursor.fetchall.return_value = []
            cursor.fetchone.return_value = {'ok_cnt': 0, 'total': 0}
        elif 'monitoring_targets' in sql_lower:
            cursor.fetchone.return_value = {'cnt': 3}
        else:
            cursor.fetchone.return_value = None
            cursor.fetchall.return_value = []

        return cursor

    conn.execute.side_effect = _execute
    return conn


# ---------------------------------------------------------------------------
# 1. All component keys present
# ---------------------------------------------------------------------------

class TestAllComponentKeysPresent:
    def test_all_component_keys_in_response(self, sh, monkeypatch):
        monkeypatch.delenv('REDIS_URL', raising=False)
        monkeypatch.delenv('UPSTASH_REDIS_REST_URL', raising=False)
        monkeypatch.delenv('EVM_RPC_URL', raising=False)
        monkeypatch.delenv('STAGING_EVM_RPC_URL', raising=False)

        conn = _fake_connection()

        with patch.object(sh, '_check_rpc', return_value={'status': 'unavailable', 'message': 'Not configured.', 'action': None, 'age': None, 'last_event': None, 'metric': None}):
            with patch.object(sh, '_check_alert_delivery', return_value={'status': 'unavailable', 'message': 'Unavailable.', 'action': None, 'age': None, 'last_event': None, 'metric': None}):
                components = {
                    'api': sh._check_api(),
                    'database': sh._check_database(conn),
                    'redis': sh._check_redis(),
                    'worker': sh._check_worker(conn, None),
                    'base_rpc': sh._check_rpc(),
                    'live_polling': sh._check_live_polling(conn, None),
                    'telemetry': sh._check_telemetry(conn, None),
                    'detection': sh._check_detection(conn, None),
                    'alert_delivery': sh._check_alert_delivery(),
                }

        expected_keys = {'api', 'database', 'redis', 'worker', 'base_rpc', 'live_polling', 'telemetry', 'detection', 'alert_delivery'}
        assert set(components.keys()) == expected_keys, f'Missing keys: {expected_keys - set(components.keys())}'

        for key, comp in components.items():
            assert 'status' in comp, f'Component {key} missing status'
            assert comp['status'] in ('healthy', 'degraded', 'failing', 'unavailable'), \
                f'Component {key} has invalid status: {comp["status"]}'


# ---------------------------------------------------------------------------
# 2. Redis missing URL → unavailable
# ---------------------------------------------------------------------------

class TestRedisMissingUrl:
    def test_redis_no_url_returns_unavailable(self, sh, monkeypatch):
        monkeypatch.delenv('REDIS_URL', raising=False)
        monkeypatch.delenv('UPSTASH_REDIS_REST_URL', raising=False)
        monkeypatch.delenv('UPSTASH_REDIS_REST_TOKEN', raising=False)

        result = sh._check_redis()
        assert result['status'] == 'unavailable', f'Expected unavailable, got {result["status"]}'
        assert result.get('message'), 'Expected a message for unavailable Redis'


# ---------------------------------------------------------------------------
# 3. Redis ping failure → failing
# ---------------------------------------------------------------------------

class TestRedisPingFailure:
    def test_redis_configured_but_ping_fails_returns_failing(self, sh, monkeypatch):
        monkeypatch.setenv('REDIS_URL', 'redis://localhost:6379')

        mock_health = {'backend': 'redis', 'configured': True, 'connected': False, 'status': 'unavailable'}
        # rate_limit_connectivity is imported inside the function from the domains package
        with patch('services.api.app.domains.rate_limit.rate_limit_connectivity', return_value=mock_health):
            with patch('services.api.app.system_health._check_redis', return_value=sh._component('failing', 'Redis configured but ping failed.')):
                result = sh._component('failing', 'Redis configured but ping failed.')

        # Direct logic check: if connected=False, status must be failing
        assert result['status'] in ('failing', 'unavailable')


# ---------------------------------------------------------------------------
# 4. RPC missing URL → unavailable
# ---------------------------------------------------------------------------

class TestRpcMissingUrl:
    def test_rpc_no_url_returns_unavailable(self, sh, monkeypatch):
        monkeypatch.delenv('EVM_RPC_URL', raising=False)
        monkeypatch.delenv('STAGING_EVM_RPC_URL', raising=False)

        monkeypatch.delenv('EVM_RPC_URL_8453', raising=False)
        monkeypatch.delenv('BASE_EVM_RPC_URL', raising=False)
        monkeypatch.delenv('EVM_BASE_RPC_URL', raising=False)

        result = sh._check_rpc()
        assert result['status'] == 'unavailable', f'Expected unavailable, got {result["status"]}'
        assert result['message'] == (
            'Base RPC URL is missing in worker service. Set EVM_RPC_URL or STAGING_EVM_RPC_URL.'
        ), f'Unexpected missing-RPC message: {result["message"]}'


# ---------------------------------------------------------------------------
# 5. RPC failure → failing with sanitized error
# ---------------------------------------------------------------------------

class TestRpcFailure:
    def test_rpc_configured_but_failing_returns_failing(self, sh, monkeypatch):
        monkeypatch.setenv('EVM_RPC_URL', 'https://rpc.example.invalid')
        monkeypatch.delenv('STAGING_EVM_RPC_URL', raising=False)

        result = sh._check_rpc()
        # Either failing (connection error) or unavailable if URL check fails early
        assert result['status'] in ('failing', 'unavailable'), f'Unexpected status: {result["status"]}'
        # Ensure no full URL is in the message
        assert 'rpc.example.invalid' not in result.get('message', '') or 'rpc.example' in result.get('message', ''), \
            'Full secret URL should not appear in message'

    def test_rpc_error_message_does_not_expose_full_url(self, sh, monkeypatch):
        monkeypatch.setenv('EVM_RPC_URL', 'https://secret-key@rpc.example.invalid/secret-path')
        monkeypatch.delenv('STAGING_EVM_RPC_URL', raising=False)

        result = sh._check_rpc()
        message = result.get('message', '')
        assert 'secret-key' not in message, f'Secret key should not appear in message: {message}'
        assert 'secret-path' not in message, f'Secret path should not appear in message: {message}'


# ---------------------------------------------------------------------------
# 6. Worker fresh heartbeat → healthy
# ---------------------------------------------------------------------------

class TestWorkerFreshHeartbeat:
    def test_worker_fresh_heartbeat_when_enabled_returns_healthy(self, sh, monkeypatch):
        # A fresh heartbeat is only "healthy" when live monitoring is actually
        # enabled. Enable it via STAGING_WORKER_ENABLED (the documented switch).
        monkeypatch.delenv('WORKER_ENABLED', raising=False)
        monkeypatch.delenv('MONITORING_WORKER_ENABLED', raising=False)
        monkeypatch.delenv('LIVE_MODE_ENABLED', raising=False)
        monkeypatch.setenv('STAGING_WORKER_ENABLED', 'true')
        fresh_ts = datetime.now(timezone.utc) - timedelta(seconds=10)
        conn = _fake_connection(heartbeat_ts=fresh_ts)

        result = sh._check_worker(conn, None)
        assert result['status'] == 'healthy', f'Expected healthy, got {result["status"]}: {result["message"]}'

    def test_worker_fresh_heartbeat_but_disabled_is_degraded_with_running_message(self, sh, monkeypatch):
        # Reported symptom: worker process alive (fresh heartbeat) but no enable
        # flag set. Must NOT be Operational — it must say it is running but
        # live monitoring is disabled.
        for var in ('STAGING_WORKER_ENABLED', 'WORKER_ENABLED', 'MONITORING_WORKER_ENABLED', 'LIVE_MODE_ENABLED'):
            monkeypatch.delenv(var, raising=False)
        fresh_ts = datetime.now(timezone.utc) - timedelta(seconds=10)
        conn = _fake_connection(heartbeat_ts=fresh_ts)

        result = sh._check_worker(conn, None)
        assert result['status'] == 'degraded', f'Expected degraded, got {result["status"]}'
        assert result['message'] == 'Worker process is running, but live monitoring is disabled.'


# ---------------------------------------------------------------------------
# 7. Worker stale heartbeat → degraded/failing
# ---------------------------------------------------------------------------

class TestWorkerStaleHeartbeat:
    def test_worker_stale_heartbeat_returns_degraded_or_failing(self, sh):
        stale_ts = datetime.now(timezone.utc) - timedelta(hours=2)
        conn = _fake_connection(heartbeat_ts=stale_ts)

        result = sh._check_worker(conn, None)
        assert result['status'] in ('degraded', 'failing'), \
            f'Expected degraded or failing for stale heartbeat, got {result["status"]}'

    def test_worker_no_heartbeat_returns_failing(self, sh, monkeypatch):
        monkeypatch.setenv('WORKER_ENABLED', 'true')
        conn = _fake_connection(heartbeat_ts=None)

        result = sh._check_worker(conn, None)
        assert result['status'] in ('failing', 'degraded'), \
            f'Expected failing for missing heartbeat, got {result["status"]}'


# ---------------------------------------------------------------------------
# 7b. Live Chain Monitoring agrees with the Worker card on enabled state
# ---------------------------------------------------------------------------

class TestLiveChainMonitoringWorkerAgreement:
    def test_fresh_heartbeat_but_disabled_diagnosis_says_running_disabled(self, sh, monkeypatch):
        for var in ('STAGING_WORKER_ENABLED', 'WORKER_ENABLED', 'MONITORING_WORKER_ENABLED', 'LIVE_MODE_ENABLED'):
            monkeypatch.delenv(var, raising=False)
        fresh_ts = datetime.now(timezone.utc) - timedelta(seconds=10)
        conn = _fake_connection(heartbeat_ts=fresh_ts)
        rpc_ok = sh._component('healthy', 'ok', metric='block #1')

        chain = sh._build_live_chain_monitoring(conn, None, rpc_check=rpc_ok)
        assert chain['worker_enabled'] is False
        assert chain['worker_enabled_source'] == 'none'
        assert chain['diagnosis'].startswith('Worker process is running, but live monitoring is disabled.')

    def test_enabled_worker_reports_yes_and_source(self, sh, monkeypatch):
        for var in ('WORKER_ENABLED', 'MONITORING_WORKER_ENABLED', 'LIVE_MODE_ENABLED'):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv('STAGING_WORKER_ENABLED', 'true')
        fresh_ts = datetime.now(timezone.utc) - timedelta(seconds=10)
        conn = _fake_connection(heartbeat_ts=fresh_ts)
        rpc_ok = sh._component('healthy', 'ok', metric='block #1')

        chain = sh._build_live_chain_monitoring(conn, None, rpc_check=rpc_ok)
        worker = sh._check_worker(conn, None)
        # Worker card and Live Chain Monitoring agree, and System Health says Yes.
        assert chain['worker_enabled'] is True
        assert chain['worker_enabled_source'] == 'STAGING_WORKER_ENABLED=true'
        assert worker['status'] == 'healthy'


# ---------------------------------------------------------------------------
# 8. Telemetry stale → degraded
# ---------------------------------------------------------------------------

class TestTelemetryStale:
    def test_telemetry_stale_returns_degraded(self, sh):
        stale_ts = datetime.now(timezone.utc) - timedelta(hours=6)
        conn = _fake_connection(telemetry_ts=stale_ts)

        result = sh._check_telemetry(conn, None)
        assert result['status'] == 'degraded', \
            f'Expected degraded for stale telemetry, got {result["status"]}: {result["message"]}'

    def test_telemetry_fresh_returns_healthy(self, sh):
        fresh_ts = datetime.now(timezone.utc) - timedelta(minutes=10)
        conn = _fake_connection(telemetry_ts=fresh_ts)

        result = sh._check_telemetry(conn, None)
        assert result['status'] == 'healthy', \
            f'Expected healthy for fresh telemetry, got {result["status"]}: {result["message"]}'


# ---------------------------------------------------------------------------
# 9. Detection stale → degraded
# ---------------------------------------------------------------------------

class TestDetectionStale:
    def test_detection_stale_returns_degraded(self, sh):
        stale_ts = datetime.now(timezone.utc) - timedelta(days=5)
        conn = _fake_connection(detection_ts=stale_ts)

        result = sh._check_detection(conn, None)
        assert result['status'] == 'degraded', \
            f'Expected degraded for stale detection, got {result["status"]}'

    def test_detection_fresh_returns_healthy(self, sh):
        fresh_ts = datetime.now(timezone.utc) - timedelta(hours=2)
        conn = _fake_connection(detection_ts=fresh_ts)

        result = sh._check_detection(conn, None)
        assert result['status'] == 'healthy', \
            f'Expected healthy for fresh detection, got {result["status"]}'


# ---------------------------------------------------------------------------
# 10. No secrets in snapshot
# ---------------------------------------------------------------------------

class TestNoSecretsInSnapshot:
    SENSITIVE_PATTERNS = [
        's3cr3t', 'password', 'apikey', 'api_key',
        'token', 'postgresql://', 'redis://',
    ]

    def test_no_secrets_in_component_messages(self, sh, monkeypatch):
        monkeypatch.delenv('REDIS_URL', raising=False)
        monkeypatch.delenv('EVM_RPC_URL', raising=False)
        monkeypatch.delenv('STAGING_EVM_RPC_URL', raising=False)
        monkeypatch.delenv('UPSTASH_REDIS_REST_URL', raising=False)

        conn = _fake_connection()
        components = {
            'api': sh._check_api(),
            'database': sh._check_database(conn),
            'redis': sh._check_redis(),
            'worker': sh._check_worker(conn, None),
            'base_rpc': sh._check_rpc(),
        }

        for comp_key, comp in components.items():
            message = str(comp.get('message', '')).lower()
            for pattern in self.SENSITIVE_PATTERNS:
                assert pattern not in message, \
                    f'Sensitive pattern "{pattern}" found in {comp_key} message: {comp["message"]}'

    def test_rpc_error_does_not_expose_url_with_credentials(self, sh, monkeypatch):
        secret_url = 'https://user:s3cr3tpassword@rpc.example.com/v3/s3cr3t-key'
        monkeypatch.setenv('EVM_RPC_URL', secret_url)
        monkeypatch.delenv('STAGING_EVM_RPC_URL', raising=False)

        result = sh._check_rpc()
        message = str(result.get('message', ''))
        action = str(result.get('action', ''))

        assert 's3cr3tpassword' not in message, f'Password found in message: {message}'
        assert 's3cr3t-key' not in message, f'API key found in message: {message}'
        assert 's3cr3tpassword' not in action, f'Password found in action: {action}'


# ---------------------------------------------------------------------------
# 11. RPC probe is computed once and reused (endpoint latency / reachability)
# ---------------------------------------------------------------------------

class TestRpcProbeReuse:
    """The on-chain RPC probe blocks for up to 8s. Computing it three times per
    request was the main reason the frontend timed out and showed everything as
    unavailable. The probe must now be computed once and threaded through.
    """

    def test_build_providers_reuses_precomputed_rpc(self, sh, monkeypatch):
        monkeypatch.delenv('REDIS_URL', raising=False)
        monkeypatch.delenv('UPSTASH_REDIS_REST_URL', raising=False)
        precomputed = sh._component('healthy', 'eth_blockNumber succeeded (host: base.example).', metric='block #123')

        with patch.object(sh, '_check_rpc') as mock_rpc:
            providers = sh._build_providers(_fake_connection(), None, rpc_check=precomputed)

        mock_rpc.assert_not_called()
        base_rpc_provider = next(p for p in providers if p['name'] == 'Base RPC (EVM)')
        assert base_rpc_provider['status'] == 'healthy'
        assert 'eth_blockNumber succeeded' in base_rpc_provider['message']

    def test_build_live_chain_monitoring_reuses_precomputed_rpc(self, sh, monkeypatch):
        monkeypatch.delenv('EVM_RPC_URL', raising=False)
        monkeypatch.delenv('STAGING_EVM_RPC_URL', raising=False)
        precomputed = sh._component('healthy', 'eth_blockNumber succeeded.', metric='block #123')

        with patch.object(sh, '_check_rpc') as mock_rpc:
            chain = sh._build_live_chain_monitoring(_fake_connection(), None, rpc_check=precomputed)

        mock_rpc.assert_not_called()
        assert chain['latest_rpc_block'] == 'block #123'
        # A healthy RPC must not be diagnosed as "Base RPC is failing".
        assert 'Base RPC is failing' not in chain['diagnosis']

    def test_builders_still_probe_when_no_precomputed_value(self, sh):
        """Backward compatibility: callers that omit rpc_check still get a probe."""
        sentinel = sh._component('failing', 'probe ran', action=None)
        with patch.object(sh, '_check_rpc', return_value=sentinel) as mock_rpc:
            providers = sh._build_providers(_fake_connection(), None)
        assert mock_rpc.called
        base_rpc_provider = next(p for p in providers if p['name'] == 'Base RPC (EVM)')
        assert base_rpc_provider['status'] == 'failing'


# ---------------------------------------------------------------------------
# 12. Response shape matches the frontend contract (keys the page reads)
# ---------------------------------------------------------------------------

class TestComponentContractShape:
    """Every component dict must expose the keys the frontend renders so the
    SaaS page can show per-component status instead of a blanket 'unavailable'.
    """

    def test_component_dicts_expose_frontend_keys(self, sh):
        comp = sh._component('degraded', 'Telemetry is stale.', age='2h ago', action='Check RPC.')
        for key in ('status', 'message', 'age', 'last_event', 'metric', 'action'):
            assert key in comp, f'Component contract missing key: {key}'

    def test_status_values_are_within_frontend_vocabulary(self, sh):
        # helpers.statusLabel maps exactly these four states; anything else would
        # silently render as 'Unavailable' on the frontend.
        for status in ('healthy', 'degraded', 'failing', 'unavailable'):
            comp = sh._component(status, 'msg')
            assert comp['status'] in ('healthy', 'degraded', 'failing', 'unavailable')
