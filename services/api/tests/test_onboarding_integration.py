"""Real end-to-end integration test for the Onboarding Agent against Postgres.

Runs only when ONBOARDING_INTEGRATION_DB=1 and DATABASE_URL points at a live
Postgres with the migrations applied. It exercises the ACTUAL orchestration SQL
(session lifecycle, durable job claim, discovery persistence, benchmark
persistence, proposal, approval, idempotent activation, audit) with a fake RPC
transport — no real network, no LLM.

Locally:
    DATABASE_URL=postgresql://postgres@127.0.0.1:55432/decoda \
    LIVE_MODE_ENABLED=true ONBOARDING_INTEGRATION_DB=1 \
    python -m pytest services/api/tests/test_onboarding_integration.py -q
"""
from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest

RUN = os.getenv('ONBOARDING_INTEGRATION_DB') == '1' and bool(os.getenv('DATABASE_URL'))
pytestmark = pytest.mark.skipif(not RUN, reason='requires ONBOARDING_INTEGRATION_DB=1 and a live DATABASE_URL')

if RUN:
    from services.api.app import onboarding_agent as oa
    from services.api.app import onboarding_discovery as od
    from services.api.app import pilot

    def _enc_uint(n):
        return '0x' + format(n, 'x').rjust(64, '0')

    def _enc_addr(a):
        return '0x' + a[2:].rjust(64, '0')

    def _enc_str(s):
        raw = s.encode()
        return '0x' + _enc_uint(32)[2:] + _enc_uint(len(raw))[2:] + raw.hex().ljust(64, '0')

    # --- fake RPC transport describing an upgradeable, ownable, pausable ERC-20 ---
    class _FakeT(od.RpcTransport):
        def __init__(self, host, block=2000):
            self.host = host
            self._block = block

        def call(self, method, params, timeout=None):
            if method == 'eth_chainId':
                return _enc_uint(8453)
            if method == 'eth_blockNumber':
                return _enc_uint(self._block)
            if method == 'eth_getCode':
                return '0x' + od.SELECTORS['transfer'][2:] + od.SELECTORS['balanceOf'][2:] + \
                       od.SELECTORS['mint'][2:] + od.SELECTORS['upgradeTo'][2:]
            if method == 'eth_getStorageAt':
                if params[1] == od.EIP1967_IMPLEMENTATION_SLOT:
                    return _enc_addr('0x' + 'c' * 40)
                return _enc_uint(0)
            if method == 'eth_call':
                sel = params[0]['data'][:10]
                return {
                    od.SELECTORS['name']: _enc_str('Acme RWA Token'),
                    od.SELECTORS['symbol']: _enc_str('ARWA'),
                    od.SELECTORS['decimals']: _enc_uint(18),
                    od.SELECTORS['totalSupply']: _enc_uint(10 ** 24),
                    od.SELECTORS['owner']: _enc_addr('0x' + 'd' * 40),
                    od.SELECTORS['supportsInterface']: _enc_uint(0),
                }.get(sel) or (_ for _ in ()).throw(od.RpcError('revert', kind='rpc_error'))
            raise od.RpcError('unsupported', kind='rpc_error')


@pytest.fixture()
def seeded(monkeypatch):
    """Seed a workspace + user, patch auth to that identity, patch RPC endpoints to fakes."""
    ws_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    with pilot.pg_connection() as conn:
        conn.execute('INSERT INTO users (id, email, password_hash, full_name, session_version, created_at, updated_at) '
                     "VALUES (%s, %s, 'x', 'Test', 1, NOW(), NOW())", (user_id, f'u{user_id[:8]}@example.com'))
        conn.execute('INSERT INTO workspaces (id, name, slug, created_by_user_id, created_at) VALUES (%s, %s, %s, %s, NOW())',
                     (ws_id, 'Acme Capital', f'acme-{ws_id[:8]}', user_id))
        conn.execute('UPDATE users SET current_workspace_id = %s WHERE id = %s', (ws_id, user_id))
        conn.execute('INSERT INTO workspace_members (id, workspace_id, user_id, role, created_at) VALUES (%s, %s, %s, %s, NOW())',
                     (str(uuid.uuid4()), ws_id, user_id, 'owner'))
        conn.commit()

    user = {'id': user_id, 'mfa_enabled': False, 'auth_provider': 'password'}
    wsctx = {'workspace_id': ws_id, 'role': 'owner', 'workspace': {'id': ws_id, 'name': 'Acme Capital', 'slug': 'acme'}}
    monkeypatch.setattr(pilot, '_require_workspace_permission', lambda *a, **k: (user, wsctx))
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *a, **k: user)
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda *a, **k: wsctx)

    def _fake_endpoints(connection, *, session_id, chain_id):
        return [
            oa.BenchmarkEndpoint('alchemy.test', 'https://alchemy.test', _FakeT('alchemy.test', block=2000)),
            oa.BenchmarkEndpoint('infura.test', 'https://infura.test', _FakeT('infura.test', block=1990)),
        ]
    monkeypatch.setattr(oa, '_build_benchmark_endpoints', _fake_endpoints)
    monkeypatch.setattr(oa, 'publish_event', lambda *a, **k: None)
    return SimpleNamespace(ws_id=ws_id, user_id=user_id)


def _req(ws_id):
    return SimpleNamespace(headers={'x-workspace-id': ws_id, 'x-request-id': 'itest'},
                          client=SimpleNamespace(host='127.0.0.1'), method='POST')


def test_full_onboarding_flow(seeded):
    req = _req(seeded.ws_id)
    contract = '0x' + 'a' * 40

    # 1. Create session
    snap = oa.create_or_resume_session(
        {'workspace_name': 'Acme', 'chain_id': 8453, 'primary_contract': contract,
         'rpc_endpoints': ['https://alchemy.test/v2/SECRETKEY0123456789abcdef'],
         'monitoring_mode': 'recommended'}, req)
    session_id = snap['session']['id']
    assert snap['session']['status'] == 'draft'
    assert len(snap['steps']) == len(oa.STEP_DEFS)
    # RPC key must never be stored/returned in the clear.
    assert all('SECRETKEY0123456789' not in str(i.get('value')) for i in snap['inputs'])

    # 2. Discover (inline worker runs the durable pipeline)
    snap = oa.start_discovery(session_id, req)
    assert snap['session']['status'] == 'proposal_ready', snap['session']
    fmap = {f['finding_type']: f for f in snap['findings']}
    assert fmap['token_standard']['value'] == 'ERC-20'
    assert fmap['token_symbol']['value'] == 'ARWA'
    assert fmap['proxy_type']['value'] == 'uups'
    assert fmap['owner_address']['confidence'] == 'confirmed'
    # Benchmark persisted with a deterministic primary.
    assert snap['benchmark']['run']['primary_host'] == 'alchemy.test'
    assert snap['benchmark']['run']['fallback_host'] == 'infura.test'
    # Proposal grounded.
    assert snap['proposal']['version'] == 1
    rule_keys = {r['key'] for r in snap['proposal']['proposal']['baseline_rules']}
    assert 'proxy_implementation_upgrade' in rule_keys
    assert 'abnormal_minting' in rule_keys

    # 3. Findings do not duplicate on retry
    before = len(snap['findings'])
    oa.retry_session(session_id, req)
    snap2 = oa.get_session(session_id, req)
    assert len(snap2['findings']) == before

    # 4. Activation requires approval
    with pytest.raises(pilot.HTTPException) as ei:
        oa.activate_session(session_id, req)
    assert ei.value.status_code == 409

    # 5. Approve then activate
    oa.approve_session(session_id, {'decision': 'approved'}, req)
    result = oa.activate_session(session_id, req)
    assert result['assets_protected'] >= 1
    assert result['targets_created'] >= 1
    assert result['session']['status'] == 'completed'

    # 6. Real rows exist in the production tables
    with pilot.pg_connection() as conn:
        assets = conn.execute('SELECT COUNT(*) AS c FROM assets WHERE workspace_id = %s', (seeded.ws_id,)).fetchone()
        targets = conn.execute('SELECT COUNT(*) AS c FROM targets WHERE workspace_id = %s AND monitoring_enabled = TRUE',
                               (seeded.ws_id,)).fetchone()
        assert assets['c'] == 1
        assert targets['c'] == 1

    # 7. Activation is idempotent — replay creates nothing new
    result2 = oa.activate_session(session_id, req)
    assert result2.get('idempotent_replay') is True
    with pilot.pg_connection() as conn:
        assets2 = conn.execute('SELECT COUNT(*) AS c FROM assets WHERE workspace_id = %s', (seeded.ws_id,)).fetchone()
        assert assets2['c'] == 1  # no duplicate asset

    # 8. Discovery report carries a SHA-256 digest and no secrets
    report = oa.export_report(session_id, _req(seeded.ws_id))
    assert report['sha256'].startswith('sha256:')
    assert 'SECRETKEY0123456789' not in str(report)


def test_wrong_chain_marks_session_partial(seeded, monkeypatch):
    """A chain mismatch is a recoverable failure that stops the pipeline."""
    def _wrong_chain(connection, *, session_id, chain_id):
        class _T(_FakeT):
            def call(self, method, params, timeout=None):
                if method == 'eth_chainId':
                    return _enc_uint(1)  # returns Ethereum, not Base
                return super().call(method, params, timeout)
        return [oa.BenchmarkEndpoint('wrong.test', 'https://wrong.test', _T('wrong.test'))]
    monkeypatch.setattr(oa, '_build_benchmark_endpoints', _wrong_chain)

    req = _req(seeded.ws_id)
    snap = oa.create_or_resume_session({'chain_id': 8453, 'primary_contract': '0x' + 'b' * 40,
                                        'rpc_endpoints': ['https://wrong.test/rpc']}, req)
    snap = oa.start_discovery(snap['session']['id'], req)
    assert snap['session']['status'] == 'partial'
    assert snap['session']['error_code'] == 'chain_mismatch'
    connect_step = next(s for s in snap['steps'] if s['step_key'] == 'connect_chain')
    assert connect_step['status'] == 'failed'
