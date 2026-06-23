"""
Task 7 tests for RPC health check in system_health._check_rpc.

Covers:
1. missing RPC URL → unavailable with remediation action
2. invalid RPC URL / bad hostname → failing (no URL leak)
3. unauthorized RPC key (HTTP 401/403) → failing with masked error
4. successful eth_blockNumber → healthy with block metric
5. stale telemetry despite fresh worker heartbeat
"""
from __future__ import annotations

import importlib.util
import json
import sys
import urllib.error
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SH_MODULE_PATH = Path(__file__).resolve().parents[1] / 'app' / 'system_health.py'
sys.path.insert(0, str(REPO_ROOT))

_RPC_ENV_VARS = (
    'EVM_RPC_URL', 'STAGING_EVM_RPC_URL',
    'EVM_RPC_URL_8453', 'EVM_RPC_URL_1', 'EVM_RPC_URL_42161',
    'BASE_EVM_RPC_URL', 'EVM_BASE_RPC_URL',
    'ETHEREUM_EVM_RPC_URL', 'ETH_EVM_RPC_URL',
    'EVM_CHAIN_ID', 'STAGING_EVM_CHAIN_ID',
)


def _load_sh():
    module_name = f'system_health_rpc_test_{uuid.uuid4().hex}'
    spec = importlib.util.spec_from_file_location(module_name, SH_MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope='module')
def sh():
    return _load_sh()


def _clear_rpc_env(monkeypatch):
    for var in _RPC_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def _make_urlopen_response(block_hex: str = '0x1312d00'):
    mock_resp = MagicMock()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.read.return_value = json.dumps(
        {'jsonrpc': '2.0', 'id': 1, 'result': block_hex}
    ).encode()
    return mock_resp


# ---------------------------------------------------------------------------
# 1. Missing RPC URL
# ---------------------------------------------------------------------------

class TestMissingRpcUrl:
    def test_all_rpc_env_vars_unset_returns_unavailable(self, sh, monkeypatch):
        _clear_rpc_env(monkeypatch)
        result = sh._check_rpc()
        assert result['status'] == 'unavailable', f'Got: {result}'

    def test_unavailable_includes_remediation_action(self, sh, monkeypatch):
        _clear_rpc_env(monkeypatch)
        result = sh._check_rpc()
        action = result.get('action', '')
        assert 'EVM_RPC_URL' in action, f'Action should mention EVM_RPC_URL: {action}'

    def test_chain_specific_url_satisfies_rpc_configured(self, sh, monkeypatch):
        _clear_rpc_env(monkeypatch)
        monkeypatch.setenv('EVM_CHAIN_ID', '8453')
        monkeypatch.setenv('BASE_EVM_RPC_URL', 'https://base-mainnet.g.alchemy.com/v2/key')

        with patch.object(sh, 'urlopen', return_value=_make_urlopen_response()):
            result = sh._check_rpc()
        # Worker's URL resolution includes BASE_EVM_RPC_URL → system health must agree
        assert result['status'] == 'healthy', (
            'BASE_EVM_RPC_URL+EVM_CHAIN_ID=8453 should be found by the same resolver the worker uses. '
            f'Got: {result}'
        )


# ---------------------------------------------------------------------------
# 2. Invalid RPC URL / bad hostname
# ---------------------------------------------------------------------------

class TestInvalidRpcUrl:
    def test_bad_hostname_returns_failing(self, sh, monkeypatch):
        _clear_rpc_env(monkeypatch)
        monkeypatch.setenv('EVM_RPC_URL', 'https://this-host-does-not-exist-decoda-test.invalid')

        result = sh._check_rpc()
        assert result['status'] == 'failing', f'Expected failing for bad hostname: {result}'

    def test_bad_url_message_does_not_leak_path_credential(self, sh, monkeypatch):
        _clear_rpc_env(monkeypatch)
        monkeypatch.setenv('EVM_RPC_URL', 'https://secret-cred@bad-host.invalid/private-path/secret-key')

        result = sh._check_rpc()
        msg = result.get('message', '')
        assert 'secret-cred' not in msg, f'Credential leaked: {msg}'
        assert 'secret-key' not in msg, f'Key path leaked: {msg}'
        assert 'private-path' not in msg, f'Path leaked: {msg}'


# ---------------------------------------------------------------------------
# 3. Unauthorized RPC key
# ---------------------------------------------------------------------------

class TestUnauthorizedRpcKey:
    def test_http_401_returns_failing_with_unauthorized(self, sh, monkeypatch):
        _clear_rpc_env(monkeypatch)
        monkeypatch.setenv('EVM_RPC_URL', 'https://base-mainnet.g.alchemy.com/v2/secret-key-abc')

        err = urllib.error.HTTPError(
            'https://base-mainnet.g.alchemy.com/v2/secret-key-abc',
            401, 'Unauthorized', {}, None,
        )
        with patch.object(sh, 'urlopen', side_effect=err):
            result = sh._check_rpc()

        assert result['status'] == 'failing'
        msg = result.get('message', '').lower()
        assert 'secret-key-abc' not in msg, f'API key leaked in message: {result["message"]}'
        assert 'unauthorized' in msg or '401' in msg, f'Expected unauthorized/401 in: {msg}'

    def test_http_403_returns_failing(self, sh, monkeypatch):
        _clear_rpc_env(monkeypatch)
        monkeypatch.setenv('EVM_RPC_URL', 'https://base-mainnet.g.alchemy.com/v2/secret-key-abc')

        err = urllib.error.HTTPError(
            'https://base-mainnet.g.alchemy.com/v2/secret-key-abc',
            403, 'Forbidden', {}, None,
        )
        with patch.object(sh, 'urlopen', side_effect=err):
            result = sh._check_rpc()

        assert result['status'] == 'failing'
        msg = result.get('message', '')
        assert 'secret-key-abc' not in msg, f'API key leaked in message: {msg}'

    def test_no_field_in_result_exposes_api_key(self, sh, monkeypatch):
        _clear_rpc_env(monkeypatch)
        secret = 'ultra-secret-api-key-xyz'
        monkeypatch.setenv('EVM_RPC_URL', f'https://base-mainnet.g.alchemy.com/v2/{secret}')

        err = urllib.error.HTTPError(
            f'https://base-mainnet.g.alchemy.com/v2/{secret}',
            401, 'Unauthorized', {}, None,
        )
        with patch.object(sh, 'urlopen', side_effect=err):
            result = sh._check_rpc()

        result_str = str(result)
        assert secret not in result_str, f'API key leaked somewhere in result: {result_str}'


# ---------------------------------------------------------------------------
# 4. Successful eth_blockNumber
# ---------------------------------------------------------------------------

class TestSuccessfulEthBlockNumber:
    def test_successful_call_returns_healthy(self, sh, monkeypatch):
        _clear_rpc_env(monkeypatch)
        monkeypatch.setenv('EVM_RPC_URL', 'https://base-mainnet.g.alchemy.com/v2/key')

        with patch.object(sh, 'urlopen', return_value=_make_urlopen_response('0x2D16800')):
            result = sh._check_rpc()

        assert result['status'] == 'healthy', f'Expected healthy: {result}'

    def test_healthy_result_includes_block_number_metric(self, sh, monkeypatch):
        _clear_rpc_env(monkeypatch)
        monkeypatch.setenv('EVM_RPC_URL', 'https://base-mainnet.g.alchemy.com/v2/key')

        with patch.object(sh, 'urlopen', return_value=_make_urlopen_response('0x2D16800')):
            result = sh._check_rpc()

        metric = result.get('metric', '')
        assert metric, 'Healthy RPC must include a block number metric'
        assert 'block' in metric.lower() or '#' in metric, f'Metric should reference block number: {metric}'

    def test_api_key_not_in_success_message(self, sh, monkeypatch):
        _clear_rpc_env(monkeypatch)
        monkeypatch.setenv('EVM_RPC_URL', 'https://base-mainnet.g.alchemy.com/v2/super-secret-key')

        with patch.object(sh, 'urlopen', return_value=_make_urlopen_response()):
            result = sh._check_rpc()

        assert result['status'] == 'healthy'
        msg = result.get('message', '')
        assert 'super-secret-key' not in msg, f'API key leaked in success message: {msg}'
        # Path segment must not appear; only hostname is allowed
        assert '/v2/' not in msg, f'URL path leaked in success message: {msg}'

    def test_rate_limited_429_returns_failing(self, sh, monkeypatch):
        _clear_rpc_env(monkeypatch)
        monkeypatch.setenv('EVM_RPC_URL', 'https://base-mainnet.g.alchemy.com/v2/key')

        err = urllib.error.HTTPError(
            'https://base-mainnet.g.alchemy.com/v2/key',
            429, 'Too Many Requests', {}, None,
        )
        with patch.object(sh, 'urlopen', side_effect=err):
            result = sh._check_rpc()

        assert result['status'] == 'failing'
        assert '429' in result.get('message', '') or 'rate' in result.get('message', '').lower()


# ---------------------------------------------------------------------------
# 5. Stale telemetry despite fresh worker heartbeat
# ---------------------------------------------------------------------------

class TestStaleTelemetryDespiteWorkerHeartbeat:
    def _make_conn(self, *, heartbeat_ts, telemetry_ts):
        conn = MagicMock()

        def _execute(sql, params=None):
            cursor = MagicMock()
            sql_lower = sql.strip().lower()
            if 'select 1' in sql_lower:
                cursor.fetchone.return_value = {'value': 1}
            elif 'monitoring_heartbeats' in sql_lower:
                cursor.fetchone.return_value = (
                    {'last_heartbeat_at': heartbeat_ts} if heartbeat_ts else None
                )
            elif 'monitoring_polls' in sql_lower or 'monitoring_runs' in sql_lower:
                cursor.fetchone.return_value = None
            elif 'telemetry_events' in sql_lower and 'count' not in sql_lower:
                cursor.fetchone.return_value = (
                    {'observed_at': telemetry_ts} if telemetry_ts else None
                )
            elif 'telemetry_events' in sql_lower and 'count' in sql_lower:
                cursor.fetchone.return_value = {'cnt': 0}
            elif 'detection_events' in sql_lower or 'detections' in sql_lower:
                cursor.fetchone.return_value = None
                cursor.fetchall.return_value = []
            elif 'provider_health_records' in sql_lower:
                cursor.fetchall.return_value = []
                cursor.fetchone.return_value = {'ok_cnt': 0, 'total': 0}
            elif 'monitoring_targets' in sql_lower:
                cursor.fetchone.return_value = {'cnt': 1}
            else:
                cursor.fetchone.return_value = None
                cursor.fetchall.return_value = []
            return cursor

        conn.execute.side_effect = _execute
        return conn

    def test_fresh_heartbeat_stale_telemetry_gives_different_statuses(self, sh):
        fresh_hb = datetime.now(timezone.utc) - timedelta(seconds=15)
        stale_tel = datetime.now(timezone.utc) - timedelta(hours=3)
        conn = self._make_conn(heartbeat_ts=fresh_hb, telemetry_ts=stale_tel)

        worker = sh._check_worker(conn, None)
        telemetry = sh._check_telemetry(conn, None)

        assert worker['status'] == 'healthy', (
            f'Worker must be healthy with a 15s-old heartbeat: {worker}'
        )
        assert telemetry['status'] == 'degraded', (
            f'Telemetry must be degraded with a 3h-old timestamp: {telemetry}'
        )

    def test_missing_telemetry_with_fresh_heartbeat(self, sh):
        fresh_hb = datetime.now(timezone.utc) - timedelta(seconds=15)
        conn = self._make_conn(heartbeat_ts=fresh_hb, telemetry_ts=None)

        worker = sh._check_worker(conn, None)
        telemetry = sh._check_telemetry(conn, None)

        assert worker['status'] == 'healthy', (
            f'Worker must be healthy with fresh heartbeat: {worker}'
        )
        assert telemetry['status'] == 'unavailable', (
            f'No telemetry at all must be unavailable: {telemetry}'
        )

    def test_stale_telemetry_message_mentions_rpc(self, sh):
        stale_tel = datetime.now(timezone.utc) - timedelta(hours=3)
        conn = self._make_conn(
            heartbeat_ts=datetime.now(timezone.utc) - timedelta(seconds=10),
            telemetry_ts=stale_tel,
        )
        result = sh._check_telemetry(conn, None)
        msg_lower = result.get('message', '').lower()
        # The message or action should point the operator toward the RPC/worker
        combined = msg_lower + (result.get('action', '') or '').lower()
        assert 'rpc' in combined or 'worker' in combined or 'evm' in combined, (
            f'Stale-telemetry guidance should mention RPC/worker: {result}'
        )
