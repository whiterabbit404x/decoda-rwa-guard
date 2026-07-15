"""Tests for the Onboarding Agent orchestration layer (DB + policy + activation).

Follows the repo's fake-connection unit style (no real DB / network / LLM):
  * idempotent activation (replay returns the stored result, creates nothing new)
  * cross-workspace access is denied (session scoped to workspace -> 404)
  * discovery retries never duplicate findings (ON CONFLICT DO NOTHING)
  * an interrupted session retries only failed / incomplete steps
  * AI-summary failure never blocks deterministic onboarding
  * proposal rules are grounded in confirmed capabilities
  * RPC secrets are redacted from audit metadata / free text
"""
from __future__ import annotations

import json
from collections import defaultdict
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from services.api.app import onboarding_agent as oa
from services.api.app import onboarding_discovery as od
from services.api.app import pilot


# ---------------------------------------------------------------------------
# Fake connection
# ---------------------------------------------------------------------------
class FakeResult:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class FakeConn:
    def __init__(self, **kw):
        self.executed = []
        self.inserts = defaultdict(list)
        self.session_row = kw.get('session_row')
        self.proposal_row = kw.get('proposal_row')
        self.approved = kw.get('approved', True)
        self.existing_activation = kw.get('existing_activation')  # {'status','result'}
        self.existing_asset = kw.get('existing_asset')            # row or None
        self.existing_target = kw.get('existing_target')          # row or None
        self._finding_keys = set()

    def execute(self, query, params=None):
        n = ' '.join(str(query).split())
        self.executed.append((n, params))

        if n.startswith('SELECT * FROM onboarding_sessions WHERE id'):
            return FakeResult(row=self.session_row)
        if n.startswith('SELECT * FROM generated_workspace_proposals WHERE session_id') or \
           n.startswith('SELECT version FROM generated_workspace_proposals'):
            return FakeResult(row=self.proposal_row)
        if 'FROM onboarding_approvals WHERE session_id' in n:
            return FakeResult(row=({'x': 1} if self.approved else None))
        if 'FROM onboarding_agent_runs WHERE idempotency_key' in n:
            return FakeResult(row=self.existing_activation)
        if n.startswith('SELECT id FROM assets WHERE workspace_id'):
            return FakeResult(row=self.existing_asset)
        if n.startswith('SELECT id FROM targets WHERE workspace_id'):
            return FakeResult(row=self.existing_target)
        if n.startswith('INSERT INTO assets'):
            self.inserts['assets'].append(params)
            return FakeResult()
        if n.startswith('INSERT INTO targets'):
            self.inserts['targets'].append(params)
            return FakeResult()
        if n.startswith('INSERT INTO onboarding_agent_runs'):
            self.inserts['runs'].append(params)
            return FakeResult()
        # ON CONFLICT DO NOTHING RETURNING id for findings — dedupe by (type, value).
        if n.startswith('INSERT INTO discovery_findings'):
            ftype, value = params[3], params[4]
            key = (ftype, value)
            if key in self._finding_keys:
                return FakeResult(row=None)
            self._finding_keys.add(key)
            self.inserts['findings'].append(params)
            return FakeResult(row={'id': params[0]})
        if n.startswith('UPDATE onboarding_steps'):
            self.inserts['step_updates'].append((n, params))
            return FakeResult()
        if n.startswith('UPDATE onboarding_sessions'):
            self.inserts['session_updates'].append((n, params))
            return FakeResult()
        if n.startswith('UPDATE onboarding_agent_runs'):
            self.inserts['run_updates'].append((n, params))
            return FakeResult()
        return FakeResult()

    def commit(self):
        self.inserts['commits'].append(True)


@contextmanager
def _fake_pg(conn):
    yield conn


def _bootstrap(monkeypatch, conn):
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(pilot, '_require_workspace_permission',
                        lambda *a, **k: ({'id': 'user-1'}, {'workspace_id': 'ws-1', 'role': 'owner'}))
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *a, **k: {'id': 'user-1'})
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda *a, **k: {'workspace_id': 'ws-1', 'role': 'owner'})
    monkeypatch.setattr(pilot, 'log_audit', lambda *a, **k: None)
    monkeypatch.setattr(pilot, '_sync_canonical_monitoring_target_state', lambda *a, **k: None)
    monkeypatch.setattr(pilot, 'ensure_monitored_system_for_target', lambda *a, **k: {'status': 'ok'})


def _req():
    return SimpleNamespace(headers={'x-workspace-id': 'ws-1'}, client=SimpleNamespace(host='127.0.0.1'), method='POST')


def _session_row(**over):
    row = {
        'id': 'sess-1', 'workspace_id': 'ws-1', 'user_id': 'user-1', 'status': 'approved',
        'current_step': 'create_config', 'selected_chain_id': 8453, 'chain_network': 'base-mainnet',
        'primary_contract': '0x' + 'a' * 40, 'protocol_name': None, 'monitoring_mode': 'recommended',
        'workspace_name': 'Acme', 'proposal_version': 1, 'activation_status': 'pending',
    }
    row.update(over)
    return row


def _proposal():
    return {
        'protected_assets': [{'name': 'USDC', 'symbol': 'USDC', 'identifier': '0x' + 'a' * 40,
                              'asset_type': 'tokenized_rwa', 'asset_class': 'ERC-20', 'chain_network': 'base-mainnet',
                              'risk_tier': 'high'}],
        'monitoring_targets': [{'name': 'USDC monitor', 'contract_identifier': '0x' + 'a' * 40,
                                'target_type': 'contract', 'monitoring_interval_seconds': 300}],
        'baseline_rules': [{'key': 'rpc_block_lag', 'enabled': True},
                           {'key': 'unexpected_owner_change', 'enabled': True}],
        'rpc_sources': {'primary_host': 'alchemy.test', 'fallback_host': 'infura.test'},
    }


# ---------------------------------------------------------------------------
# Activation idempotency + workspace scoping
# ---------------------------------------------------------------------------
def test_activation_creates_assets_and_targets(monkeypatch):
    conn = FakeConn(session_row=_session_row(),
                    proposal_row={'version': 1, 'proposal': _proposal()},
                    approved=True, existing_activation=None)
    _bootstrap(monkeypatch, conn)
    monkeypatch.setattr(oa, 'build_session_snapshot', lambda *a, **k: {'session': {}})
    monkeypatch.setattr(oa, 'publish_event', lambda *a, **k: None)

    result = oa.activate_session('11111111-1111-1111-1111-111111111111', _req())
    assert result['assets_protected'] == 1
    assert result['assets_created'] == 1
    assert result['targets_created'] == 1
    assert result['monitoring_sources_active'] == 1
    assert result['rules_enabled'] == 2
    assert len(conn.inserts['assets']) == 1
    assert len(conn.inserts['targets']) == 1


def test_activation_idempotent_replay_creates_nothing(monkeypatch):
    stored = {'assets_protected': 1, 'assets_created': 1, 'targets_created': 1,
              'monitoring_sources_active': 1, 'rules_enabled': 2, 'coverage_status': 'provisioning',
              'proposal_version': 1}
    conn = FakeConn(session_row=_session_row(status='completed'),
                    proposal_row={'version': 1, 'proposal': _proposal()},
                    approved=True, existing_activation={'status': 'completed', 'result': stored})
    _bootstrap(monkeypatch, conn)
    monkeypatch.setattr(oa, 'build_session_snapshot', lambda *a, **k: {'session': {}})
    monkeypatch.setattr(oa, 'publish_event', lambda *a, **k: None)

    result = oa.activate_session('11111111-1111-1111-1111-111111111111', _req())
    assert result['idempotent_replay'] is True
    assert result['assets_protected'] == 1
    # No new assets / targets / runs were inserted on replay.
    assert conn.inserts['assets'] == []
    assert conn.inserts['targets'] == []
    assert conn.inserts['runs'] == []


def test_activation_reuses_existing_asset(monkeypatch):
    conn = FakeConn(session_row=_session_row(),
                    proposal_row={'version': 1, 'proposal': _proposal()},
                    approved=True, existing_activation=None,
                    existing_asset={'id': 'asset-existing'})
    _bootstrap(monkeypatch, conn)
    monkeypatch.setattr(oa, 'build_session_snapshot', lambda *a, **k: {'session': {}})
    monkeypatch.setattr(oa, 'publish_event', lambda *a, **k: None)

    result = oa.activate_session('11111111-1111-1111-1111-111111111111', _req())
    assert result['assets_reused'] == 1
    assert result['assets_created'] == 0
    assert conn.inserts['assets'] == []  # reused, not re-created


def test_activation_requires_approval(monkeypatch):
    conn = FakeConn(session_row=_session_row(),
                    proposal_row={'version': 1, 'proposal': _proposal()},
                    approved=False)
    _bootstrap(monkeypatch, conn)
    with pytest.raises(pilot.HTTPException) as ei:
        oa.activate_session('11111111-1111-1111-1111-111111111111', _req())
    assert ei.value.status_code == 409


def test_cross_workspace_access_denied(monkeypatch):
    # Session lookup scoped by workspace returns None -> 404.
    conn = FakeConn(session_row=None)
    _bootstrap(monkeypatch, conn)
    with pytest.raises(pilot.HTTPException) as ei:
        oa.get_session('11111111-1111-1111-1111-111111111111', _req())
    assert ei.value.status_code == 404


def test_get_session_builds_snapshot(monkeypatch):
    conn = FakeConn(session_row=None)
    _bootstrap(monkeypatch, conn)
    monkeypatch.setattr(oa, 'build_session_snapshot', lambda *a, **k: {'session': {'id': 'sess-1'}})
    out = oa.get_session('11111111-1111-1111-1111-111111111111', _req())
    assert out['session']['id'] == 'sess-1'


# ---------------------------------------------------------------------------
# Duplicate finding prevention
# ---------------------------------------------------------------------------
def test_findings_are_deduped_on_retry():
    conn = FakeConn()
    findings = [
        od.Finding('token_standard', 'ERC-20', 'heuristic', od.PROBABLE),
        od.Finding('token_symbol', 'USDC', 'view_call', od.CONFIRMED),
    ]
    first = oa._persist_findings(conn, session_id='s1', workspace_id='ws-1', findings=findings)
    second = oa._persist_findings(conn, session_id='s1', workspace_id='ws-1', findings=findings)
    assert first == 2
    assert second == 0  # ON CONFLICT DO NOTHING -> no duplicates on re-discovery


# ---------------------------------------------------------------------------
# Retry only failed/incomplete steps
# ---------------------------------------------------------------------------
def test_retry_resets_only_failed_steps(monkeypatch):
    conn = FakeConn(session_row=_session_row(status='partial'))
    _bootstrap(monkeypatch, conn)
    monkeypatch.setattr(oa, 'build_session_snapshot', lambda *a, **k: {'session': {}})
    monkeypatch.setattr(oa, 'publish_event', lambda *a, **k: None)
    monkeypatch.setattr(oa, '_maybe_run_inline', lambda *a, **k: None)
    monkeypatch.setattr(oa, '_reload_snapshot', lambda *a, **k: {'session': {}})

    oa.retry_session('11111111-1111-1111-1111-111111111111', _req())
    step_updates = [sql for sql, _ in conn.inserts['step_updates']]
    assert any("status = 'pending'" in s and "status IN ('failed', 'running', 'needs_attention')" in s
               for s in step_updates)
    assert len(conn.inserts['runs']) == 1  # a new discover run was enqueued


# ---------------------------------------------------------------------------
# AI-summary fallback
# ---------------------------------------------------------------------------
def test_ai_summary_falls_back_when_disabled(monkeypatch):
    monkeypatch.delenv('ONBOARDING_AI_SUMMARY_ENABLED', raising=False)
    session = _session_row()
    findings = [{'finding_type': 'token_standard', 'value': 'ERC-20', 'confidence': 'probable'},
                {'finding_type': 'token_symbol', 'value': 'USDC', 'confidence': 'confirmed'}]
    proposal = oa._compose_proposal(session=session, fmap={f['finding_type']: f for f in findings},
                                    findings=findings, bench_run={'primary_host': 'alchemy.test'}, bench_results=[])
    summary, available = oa.build_ai_summary(session=session, proposal=proposal, findings=findings)
    assert available is False
    assert 'ERC-20' in summary and 'base-mainnet' in summary


def test_ai_summary_failure_does_not_break_onboarding(monkeypatch):
    monkeypatch.setenv('ONBOARDING_AI_SUMMARY_ENABLED', 'true')
    monkeypatch.setattr(oa, '_invoke_ai_summary', lambda **k: (_ for _ in ()).throw(RuntimeError('provider down')))
    session = _session_row()
    findings = [{'finding_type': 'token_standard', 'value': 'ERC-20', 'confidence': 'probable'}]
    proposal = oa._compose_proposal(session=session, fmap={f['finding_type']: f for f in findings},
                                    findings=findings, bench_run=None, bench_results=[])
    summary, available = oa.build_ai_summary(session=session, proposal=proposal, findings=findings)
    assert available is False           # AI failed, but...
    assert summary                       # deterministic summary is still produced


# ---------------------------------------------------------------------------
# Proposal grounding
# ---------------------------------------------------------------------------
def test_proposal_rules_grounded_in_capabilities():
    findings = [
        {'finding_type': 'token_standard', 'value': 'ERC-20', 'confidence': 'probable'},
        {'finding_type': 'owner_address', 'value': '0x' + '1' * 40, 'confidence': 'confirmed'},
        {'finding_type': 'proxy_type', 'value': 'transparent', 'confidence': 'confirmed'},
        {'finding_type': 'implementation_address', 'value': '0x' + '2' * 40, 'confidence': 'confirmed'},
        {'finding_type': 'pausable', 'value': 'Pausable', 'confidence': 'probable'},
        {'finding_type': 'mint_capability', 'value': 'Mint', 'confidence': 'probable'},
    ]
    fmap = {f['finding_type']: f for f in findings}
    proposal = oa._compose_proposal(session=_session_row(), fmap=fmap, findings=findings,
                                    bench_run={'primary_host': 'a'}, bench_results=[{'x': 1}])
    rule_keys = {r['key'] for r in proposal['baseline_rules']}
    assert 'unexpected_owner_change' in rule_keys      # from owner/access
    assert 'proxy_implementation_upgrade' in rule_keys  # from proxy
    assert 'pause_unpause_event' in rule_keys           # from pausable
    assert 'abnormal_minting' in rule_keys              # from mint capability
    assert 'abnormal_burning' not in rule_keys          # no burn capability detected
    # Every rule cites its source findings.
    for rule in proposal['baseline_rules']:
        assert 'source_findings' in rule


def test_proposal_flags_heuristic_limitations():
    findings = [{'finding_type': 'token_standard', 'value': 'ERC-20', 'confidence': 'probable'}]
    proposal = oa._compose_proposal(session=_session_row(), fmap={f['finding_type']: f for f in findings},
                                    findings=findings, bench_run=None, bench_results=[])
    assert any('heuristic' in lim.lower() for lim in proposal['limitations'])


def test_strict_mode_adds_sensitive_rule():
    findings = [{'finding_type': 'owner_address', 'value': '0x' + '1' * 40, 'confidence': 'confirmed'},
                {'finding_type': 'access_model', 'value': 'AccessControl', 'confidence': 'confirmed'}]
    proposal = oa._compose_proposal(session=_session_row(monitoring_mode='strict'),
                                    fmap={f['finding_type']: f for f in findings}, findings=findings,
                                    bench_run=None, bench_results=[])
    assert any(r['key'] == 'any_privileged_call' for r in proposal['baseline_rules'])


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------
def test_redact_json_strips_rpc_key():
    text = json.dumps({'rpc': 'https://base-mainnet.g.alchemy.com/v2/SUPERSECRETKEY0123456789'})
    redacted = oa.redact_json(text)
    assert 'SUPERSECRETKEY0123456789' not in redacted
    assert 'alchemy.com' in redacted


def test_redact_text_passthrough_for_plain_text():
    assert oa.redact_text('chain mismatch: 1 != 8453') == 'chain mismatch: 1 != 8453'
    assert oa.redact_text(None) is None


# ---------------------------------------------------------------------------
# Input parsing / validation
# ---------------------------------------------------------------------------
def test_parse_inputs_rejects_zero_address():
    with pytest.raises(pilot.HTTPException) as ei:
        oa._parse_session_inputs({'chain_id': 8453, 'primary_contract': od.ZERO_ADDRESS})
    assert ei.value.status_code == 400


def test_parse_inputs_rejects_private_rpc(monkeypatch):
    monkeypatch.delenv('ONBOARDING_ALLOW_PRIVATE_RPC', raising=False)
    with pytest.raises(pilot.HTTPException) as ei:
        oa._parse_session_inputs({'chain_id': 8453, 'primary_contract': '0x' + 'a' * 40,
                                  'rpc_endpoints': ['http://169.254.169.254/']})
    assert ei.value.status_code == 400


def test_parse_inputs_resolves_chain_alias():
    parsed = oa._parse_session_inputs({'network': 'base', 'primary_contract': '0x' + 'a' * 40})
    assert parsed['chain_id'] == 8453
    assert parsed['chain_network'] == 'base-mainnet'
