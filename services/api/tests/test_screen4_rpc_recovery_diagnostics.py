"""Screen 4 RPC recovery diagnostics + truthful backoff/stream semantics.

Covers the production-deploy tasks for restoring a valid Base RPC route and making
provider health truthful in logs and the UI:

  Task 1 — live RPC endpoint validation (rpc_endpoint_validation) + safe disable of a
           known-invalid (TLS-broken) route so it is not re-dialed every cycle.
  Task 2 — bounded rpc_request_volume_summary instrumentation (which method / caller
           drives the request volume that trips a provider rate limit).
  Task 3 — backoff events separate a REAL network 429 (network_attempted=true) from a
           request skipped without dialing (network_attempted=false).
  Task 4 — stream chain-head UNKNOWN is a first-class health state and never
           serializes as degraded=false (which would read as healthy/green).

All logs and returned dicts are HOST-ONLY: no URL, path, or API token ever appears.
"""
from __future__ import annotations

import logging
import socket
import ssl
import urllib.error
from unittest.mock import patch

import pytest

from services.api.app import evm_activity_provider as eap
from services.api.app import quicknode_streams as qn

EAP_LOGGER = 'services.api.app.evm_activity_provider'
QN_LOGGER = 'services.api.app.quicknode_streams'
ALCHEMY_HOST = 'base-mainnet.g.alchemy.com'
QUICKNODE_HOST = 'holy-proportionate-dust.base-mainnet.quiknode.pro'


def _messages(caplog) -> str:
    return '\n'.join(r.getMessage() for r in caplog.records)


@pytest.fixture(autouse=True)
def _reset_state():
    eap.reset_rpc_provider_state()
    yield
    eap.reset_rpc_provider_state()


# ===========================================================================
# Task 1 — safe_error_category classification (no secret / URL leakage)
# ===========================================================================

def test_classify_probe_error_tls_internal():
    exc = ssl.SSLError('[SSL: TLSV1_ALERT_INTERNAL_ERROR] tlsv1 alert internal error')
    assert eap._classify_probe_error(exc) == 'tls_internal_error'


def test_classify_probe_error_certificate_and_dns_and_rate_limit_and_timeout():
    assert eap._classify_probe_error(ssl.SSLError('certificate verify failed')) == 'tls_certificate_invalid'
    assert eap._classify_probe_error(socket.gaierror(-2, 'Name or service not known')) == 'dns_failure'
    assert eap._classify_probe_error(
        urllib.error.HTTPError('u', 429, 'Too Many Requests', {}, None)
    ) == 'rate_limited'
    assert eap._classify_probe_error(TimeoutError('timed out')) == 'timeout'


# ===========================================================================
# Task 1 — probe_rpc_endpoint emits rpc_endpoint_validation, host-only
# ===========================================================================

class _TlsFailCtx:
    def wrap_socket(self, sock, server_hostname=None):
        raise ssl.SSLError('[SSL: TLSV1_ALERT_INTERNAL_ERROR] tlsv1 alert internal error (_ssl.c:1000)')


class _Conn:
    def __enter__(self):
        return object()

    def __exit__(self, *exc):
        return False


class _TlsOkCtx:
    """A stand-in SSL context whose handshake succeeds (returns a context manager)."""

    def wrap_socket(self, sock, server_hostname=None):
        return _Conn()


def test_probe_endpoint_tls_internal_error_is_classified_and_logged(caplog):
    url = f'https://{QUICKNODE_HOST}/SECRET-TOKEN-abc'
    with caplog.at_level(logging.INFO, logger=EAP_LOGGER):
        with (
            patch('socket.getaddrinfo', return_value=[('x',)]),
            patch('socket.create_connection', return_value=_Conn()),
            patch('ssl.create_default_context', return_value=_TlsFailCtx()),
        ):
            report = eap.probe_rpc_endpoint(url, expected_chain_id=8453)

    assert report['host'] == QUICKNODE_HOST
    assert report['dns_ok'] is True
    assert report['tls_ok'] is False
    assert report['http_ok'] is False
    assert report['json_rpc_ok'] is False
    assert report['safe_error_category'] == 'tls_internal_error'
    text = _messages(caplog)
    assert 'event=rpc_endpoint_validation' in text
    assert f'rpc_host={QUICKNODE_HOST}' in text
    assert 'dns_ok=true tls_ok=false' in text
    assert 'safe_error_category=tls_internal_error' in text
    # The token / full URL never leak.
    assert 'SECRET-TOKEN-abc' not in text and 'SECRET-TOKEN-abc' not in str(report)


def test_probe_endpoint_full_success_reports_chain_and_block(caplog):
    url = f'https://{ALCHEMY_HOST}/v2/SECRET-KEY'

    class _OkClient:
        def __init__(self, u):
            self.u = u

        def call(self, method, params):
            return '0x2105' if method == 'eth_chainId' else '0x2c68af0'  # 8453 / a block

    with caplog.at_level(logging.INFO, logger=EAP_LOGGER):
        with (
            patch('socket.getaddrinfo', return_value=[('x',)]),
            patch('socket.create_connection', return_value=_Conn()),
            patch('ssl.create_default_context', return_value=_TlsOkCtx()),
            patch.object(eap, 'JsonRpcClient', _OkClient),
        ):
            report = eap.probe_rpc_endpoint(url, expected_chain_id=8453)

    assert report['dns_ok'] and report['tls_ok'] and report['http_ok'] and report['json_rpc_ok']
    assert report['chain_id'] == 8453
    assert report['chain_id_matches'] is True
    assert report['latest_block'] == 0x2c68af0
    assert report['safe_error_category'] == 'ok'
    assert 'json_rpc_ok=true' in _messages(caplog)
    assert 'SECRET-KEY' not in _messages(caplog)


# ===========================================================================
# Task 1 — known-invalid route can be disabled safely (not re-dialed)
# ===========================================================================

def test_disable_route_makes_failover_skip_it_without_dialing():
    eap.disable_rpc_route(QUICKNODE_HOST, reason='tls_internal_error')
    assert eap.is_rpc_route_disabled(QUICKNODE_HOST) is True
    assert eap.disabled_rpc_routes() == [QUICKNODE_HOST]

    dialed: list[str] = []

    class _Fake:
        def __init__(self, url):
            self.url = url

        def call(self, method, params):
            dialed.append(eap._host_of(self.url))
            return '0x2105' if method == 'eth_chainId' else '0x1'

    client = eap.FailoverJsonRpcClient([f'https://{QUICKNODE_HOST}/x', f'https://{ALCHEMY_HOST}/v2/k'])
    with patch.object(eap, 'JsonRpcClient', _Fake):
        result = client.call('eth_blockNumber', [])

    assert result == '0x1'
    assert QUICKNODE_HOST not in dialed, 'a disabled route must never be dialed'
    assert ALCHEMY_HOST in dialed


def test_enable_route_restores_dialing():
    eap.disable_rpc_route(QUICKNODE_HOST, reason='tls_internal_error')
    eap.enable_rpc_route(QUICKNODE_HOST)
    assert eap.is_rpc_route_disabled(QUICKNODE_HOST) is False
    assert eap.disabled_rpc_routes() == []


def test_disable_route_is_safe_on_empty_host():
    eap.disable_rpc_route('', reason='x')          # no-op, must not raise
    eap.disable_rpc_route(None)                     # type: ignore[arg-type]
    assert eap.disabled_rpc_routes() == []


def test_probe_worker_endpoints_disables_tls_broken_route_only():
    reports = {
        f'https://{QUICKNODE_HOST}/x': {'host': QUICKNODE_HOST, 'json_rpc_ok': False,
                                        'safe_error_category': 'tls_internal_error'},
        f'https://{ALCHEMY_HOST}/v2/k': {'host': ALCHEMY_HOST, 'json_rpc_ok': True,
                                         'safe_error_category': 'ok'},
    }

    def _fake_probe(url, **kw):
        return reports[url]

    with (
        patch.object(eap, '_resolve_evm_rpc_urls', return_value=list(reports.keys())),
        patch.object(eap, 'probe_rpc_endpoint', side_effect=_fake_probe),
    ):
        out = eap.probe_worker_rpc_endpoints(expected_chain_id=8453)

    # The TLS-broken QuickNode route is disabled; the healthy Alchemy route is not.
    assert QUICKNODE_HOST in out['disabled_rpc_routes']
    assert ALCHEMY_HOST not in out['disabled_rpc_routes']
    assert out['all_operational'] is False


def test_rate_limited_route_is_not_disabled():
    """A 429 is a transient throttle (handled by the 429 backoff), NOT a broken route —
    probe_worker_rpc_endpoints must never bench it."""
    url = f'https://{ALCHEMY_HOST}/v2/k'
    with (
        patch.object(eap, '_resolve_evm_rpc_urls', return_value=[url]),
        patch.object(eap, 'probe_rpc_endpoint',
                     return_value={'host': ALCHEMY_HOST, 'json_rpc_ok': False,
                                   'safe_error_category': 'rate_limited'}),
    ):
        out = eap.probe_worker_rpc_endpoints()
    assert ALCHEMY_HOST not in out['disabled_rpc_routes']


# ===========================================================================
# Task 2 — rpc_request_volume_summary (bounded, once per window)
# ===========================================================================

def test_volume_summary_not_emitted_within_window(caplog):
    with caplog.at_level(logging.INFO, logger=EAP_LOGGER):
        eap._record_rpc_volume(ALCHEMY_HOST, method='eth_blockNumber', caller='scheduled_poll')
        eap._record_rpc_volume(ALCHEMY_HOST, method='eth_chainId', caller='scheduled_poll')
    assert 'event=rpc_request_volume_summary' not in _messages(caplog)
    snap = eap.rpc_request_volume_snapshot()
    assert snap['hosts'][ALCHEMY_HOST]['calls_total'] == 2
    assert snap['hosts'][ALCHEMY_HOST]['by_method'] == {'eth_blockNumber': 1, 'eth_chainId': 1}
    assert snap['hosts'][ALCHEMY_HOST]['by_caller'] == {'scheduled_poll': 2}


def test_volume_summary_emitted_once_window_elapsed(caplog):
    eap._record_rpc_volume(ALCHEMY_HOST, method='eth_getLogs', caller='scheduled_poll')
    eap._record_rpc_volume(ALCHEMY_HOST, method='eth_getLogs', caller='chain_head_refresh')
    eap._record_rpc_volume(ALCHEMY_HOST, method='eth_blockNumber', caller='chain_head_refresh',
                           rate_limited=True, retry=True)
    # Age the window past the emit threshold.
    with eap._RPC_PROVIDER_LOCK:
        eap._RPC_VOLUME['window_start_monotonic'] -= (eap._RPC_VOLUME_WINDOW_SECONDS + 1.0)

    with caplog.at_level(logging.INFO, logger=EAP_LOGGER):
        eap._record_rpc_volume(ALCHEMY_HOST, method='eth_blockNumber', caller='scheduled_poll')

    text = _messages(caplog)
    assert 'event=rpc_request_volume_summary' in text
    assert f'rpc_host={ALCHEMY_HOST}' in text
    assert 'calls_by_method=' in text and 'eth_getLogs' in text
    assert 'calls_by_caller=' in text and 'chain_head_refresh' in text
    assert 'rate_limited=1' in text
    assert 'retries=1' in text
    assert 'calls_total=4' in text          # the triggering call is included in the window
    # After emit the window resets to empty; the next window starts fresh.
    snap = eap.rpc_request_volume_snapshot()
    assert ALCHEMY_HOST not in snap['hosts']


def test_jsonrpc_call_records_volume_with_caller(monkeypatch):
    url = f'https://{ALCHEMY_HOST}/v2/k'
    monkeypatch.setenv('EVM_RPC_MAX_RETRIES', '0')

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"jsonrpc":"2.0","id":1,"result":"0x2105"}'

    with patch.object(eap.request, 'urlopen', return_value=_Resp()):
        with eap.rpc_caller_scope('scheduled_poll'):
            eap.JsonRpcClient(url).call('eth_chainId', [])

    snap = eap.rpc_request_volume_snapshot()
    assert snap['hosts'][ALCHEMY_HOST]['by_caller'] == {'scheduled_poll': 1}
    assert snap['hosts'][ALCHEMY_HOST]['by_method'] == {'eth_chainId': 1}


# ===========================================================================
# Task 3 — failover skip is network_attempted=false (distinct from a real 429)
# ===========================================================================

def test_failover_skip_logs_no_network_attempt(monkeypatch, caplog):
    monkeypatch.setenv('APP_ENV', 'production')
    eap.record_rpc_rate_limited(None, host=ALCHEMY_HOST)   # bench Alchemy (real prior 429)

    class _Fake:
        def __init__(self, url):
            self.url = url

        def call(self, method, params):
            return '0x2105' if method == 'eth_chainId' else '0x1'

    client = eap.FailoverJsonRpcClient([f'https://{ALCHEMY_HOST}/v2/k', f'https://{QUICKNODE_HOST}/x'])
    with caplog.at_level(logging.INFO, logger=EAP_LOGGER):
        with patch.object(eap, 'JsonRpcClient', _Fake):
            client.call('eth_blockNumber', [])

    text = _messages(caplog)
    # The benched Alchemy provider was skipped WITHOUT a network attempt.
    assert 'event=rpc_call_skipped_existing_backoff' in text
    assert 'network_attempted=false' in text
    assert f'rpc_host={ALCHEMY_HOST}' in text


# ===========================================================================
# Task 4 — stream chain-head UNKNOWN is never serialized as degraded=false
# ===========================================================================

def test_stream_health_status_enum_mapping():
    assert qn.stream_health_status('live') == 'healthy'
    assert qn.stream_health_status('degraded') == 'degraded'
    assert qn.stream_health_status('unknown') == 'unknown'
    assert qn.stream_health_status('not_applicable') is None


def test_stream_degraded_flag_is_null_when_unknown():
    assert qn.stream_degraded_flag('degraded') is True
    assert qn.stream_degraded_flag('live') is False
    assert qn.stream_degraded_flag('unknown') is None          # NOT False
    assert qn.stream_degraded_flag('not_applicable') is None


# --- integration: a live batch with an UNKNOWN head serializes health/degraded truthfully

import hashlib
import hmac
import json
import time
from contextlib import contextmanager

_QN_SECRET = 'test-quicknode-secret'
_QN_NONCE = 'nonce-unknown-head'


class _LaneConn:
    def execute(self, query, params=None):
        q = (query or '').strip().lower()

        class _Rows:
            def __init__(self, rows):
                self._rows = rows

            def fetchone(self):
                return self._rows[0] if self._rows else None

            def fetchall(self):
                return list(self._rows)

        if q.startswith('create table') or 'advisory' in q:
            return _Rows([{'acquired': True}])
        return _Rows([])

    def commit(self):
        pass

    def rollback(self):
        pass


@contextmanager
def _mock_pg(conn):
    yield conn


def _live_body(block: int) -> bytes:
    return json.dumps({
        'tx_hash': '0x' + 'a' * 64, 'from': '0x' + '1' * 40, 'to': '0x' + '2' * 40,
        'value': '1000000000000000000', 'block_number': block, 'chain_id': 8453,
    }).encode()


def test_live_batch_unknown_head_logs_health_unknown_not_degraded_false(monkeypatch, caplog):
    """A live-lane batch whose chain head is UNKNOWN must log health_status=unknown and
    degraded=null — never degraded=false (which the UI would paint green)."""
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', _QN_SECRET)
    qn.reset_chain_head_cache()
    conn = _LaneConn()
    body = _live_body(49_999_950)
    ts = str(int(time.time()))
    sig = hmac.new(_QN_SECRET.encode(), _QN_NONCE.encode() + ts.encode() + body, hashlib.sha256).hexdigest()

    with caplog.at_level(logging.INFO, logger=QN_LOGGER):
        with (
            patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)),
            patch.object(qn, 'ensure_pilot_schema', lambda _c: None),
            patch.object(qn, '_make_base_rpc_client', lambda: None),   # head unavailable
        ):
            qn.process_quicknode_base_stream_webhook(
                raw_body=body, signature_header=sig, nonce_header=_QN_NONCE,
                timestamp_header=ts, lane=qn.LANE_LIVE,
            )

    batch_lines = [ln for ln in _messages(caplog).splitlines() if 'event=quicknode_stream_batch' in ln]
    assert batch_lines, 'the batch summary must be logged'
    batch = batch_lines[0]
    assert 'lag_status=unknown' in batch
    assert 'health_status=unknown' in batch
    assert 'degraded=null' in batch
    assert 'degraded=false' not in batch
