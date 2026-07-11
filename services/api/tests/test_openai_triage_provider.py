"""Tests for the OpenAI Responses-API incident-triage provider.

Every test uses a MOCKED OpenAI client (injected) or exercises pure functions, so
no automated test imports or calls the real OpenAI SDK / API. The backend
validator (``ai_triage.validate_triage_output``) is still the source of truth —
a schema-shaped response is never trusted merely because it parsed.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from contextlib import contextmanager

import pytest

from services.api.app import ai_triage, ai_providers, pilot


# --------------------------------------------------------------------------
# Snapshot + mocked OpenAI client
# --------------------------------------------------------------------------
def _snapshot(**over):
    snap = {
        'schema_version': '1.0', 'workspace_id': 'ws-1', 'incident_id': 'inc-1',
        'alert': {'alert_id': 'alert-1', 'severity': 'high', 'created_at': '2026-07-11T00:00:00+00:00', 'rule_id': 'wallet_transfer'},
        'rule': {'rule_id': 'wallet_transfer', 'name': 'Wallet transfer', 'description': 'Monitored wallet transfer detected.', 'conditions': {}, 'version': '1'},
        'target': {'target_id': 'tgt-1', 'asset_id': None, 'chain_id': 8453, 'address': '0xtarget', 'asset_type': 'wallet'},
        'telemetry': [{
            'telemetry_id': 'tel-1', 'event_type': 'wallet_transfer_detected', 'detected_by': 'quicknode_stream',
            'tx_hash': '0xdead', 'from': '0xfrom', 'to': '0xto', 'value': '100', 'block_number': 123,
            'chain_id': 8453, 'observed_at': '2026-07-11T00:00:00+00:00', 'ingested_at': '2026-07-11T00:00:01+00:00',
            'evidence_source': 'live_provider',
        }],
        'provider_observations': [],
        'policies': [{'policy_version': '1.0'}],
        'available_runbooks': [{'runbook_id': rid, 'action_type': m['action_type'], 'risk_level': m['risk_level'], 'name': m['name']} for rid, m in ai_triage.RUNBOOK_CATALOG.items()],
        'audit_references': [],
    }
    snap.update(over)
    return snap


def _grounded_result_json(snap):
    return json.dumps(ai_providers._deterministic_result_from_snapshot(snap))


class _FakeUsage:
    def __init__(self, input_tokens, output_tokens):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeResponse:
    def __init__(self, text, *, model='openai-test-model', usage=(120, 60), output=None):
        self.output_text = text
        self.model = model
        self.usage = _FakeUsage(*usage)
        self.output = output or []


class _FakeResponses:
    def __init__(self, handler):
        self._handler = handler
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._handler(kwargs, len(self.calls))


class _FakeOpenAIClient:
    """Minimal stand-in for ``openai.OpenAI`` exposing ``.responses.create``."""

    def __init__(self, handler):
        self.responses = _FakeResponses(handler)


class _FakeAPIError(Exception):
    """Duck-typed OpenAI error carrying a status_code (and a leak-y body)."""

    def __init__(self, status_code, body='INTERNAL-PROVIDER-BODY-LEAK'):
        super().__init__(f'{status_code}: {body}')
        self.status_code = status_code


def _returns(text):
    return lambda kwargs, n: _FakeResponse(text)


def _openai(handler, **kw):
    return ai_providers.OpenAITriageProvider(client=_FakeOpenAIClient(handler), sleep=lambda *_: None, **kw)


def _prompt(snap):
    return ai_triage.build_prompt(snap, ai_triage.AGENT_POLICY, prompt_version='v1')


def _analyze(provider, snap, *, model='openai-test-model'):
    return provider.analyze(prompt=_prompt(snap), model=model, timeout_seconds=30, max_output_tokens=2000)


# --------------------------------------------------------------------------
# Provider selection (fail-closed)
# --------------------------------------------------------------------------
def test_get_triage_provider_selects_openai():
    assert isinstance(ai_providers.get_triage_provider('openai'), ai_providers.OpenAITriageProvider)
    assert isinstance(ai_providers.get_triage_provider('OpenAI'), ai_providers.OpenAITriageProvider)
    assert isinstance(ai_providers.get_triage_provider('mock'), ai_providers.MockTriageProvider)
    assert isinstance(ai_providers.get_triage_provider(''), ai_providers.MockTriageProvider)
    assert isinstance(ai_providers.get_triage_provider(None), ai_providers.MockTriageProvider)


def test_unknown_provider_fails_closed():
    provider = ai_providers.get_triage_provider('gpt-9000-turbo')
    assert isinstance(provider, ai_providers._UnknownTriageProvider)
    with pytest.raises(ai_providers.TriageProviderError) as exc:
        provider.analyze(prompt={}, model='x', timeout_seconds=1, max_output_tokens=1)
    assert exc.value.error_code == 'unknown_provider'
    assert exc.value.retryable is False


def test_blocking_configuration_errors_for_openai(monkeypatch):
    for var in ('AI_API_KEY', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY'):
        monkeypatch.delenv(var, raising=False)
    cfg = {'enabled': True, 'provider': 'openai', 'has_api_key': False, 'model': ''}
    errors = ai_triage.blocking_configuration_errors(cfg)
    assert any('API key' in e for e in errors) or any('AI_MODEL_TRIAGE' in e for e in errors)
    # unknown provider is a hard error
    assert ai_triage.blocking_configuration_errors({'enabled': True, 'provider': 'weird', 'has_api_key': True, 'model': 'm'})
    # disabled -> never a hard error
    assert ai_triage.blocking_configuration_errors({'enabled': False, 'provider': 'openai', 'has_api_key': False, 'model': ''}) == []


# --------------------------------------------------------------------------
# Valid structured response
# --------------------------------------------------------------------------
def test_openai_valid_structured_response_validates_and_is_grounded():
    snap = _snapshot()
    provider = _openai(_returns(_grounded_result_json(snap)))
    raw = _analyze(provider, snap)
    assert raw.provider == 'openai'
    assert raw.input_tokens == 120 and raw.output_tokens == 60
    validated = ai_triage.validate_triage_output(raw.raw_text, snap, ai_triage.AGENT_POLICY)
    result = validated['result']
    assert result['incident_id'] == 'inc-1'
    valid = ai_triage.derive_valid_references(snap)['refs']
    for c in result['citations']:
        assert c['ref'] in valid
    assert validated['recommendations'][0]['action_type'] in ai_triage.ALLOWED_ACTION_TYPES


def test_openai_request_uses_strict_schema_stateless_and_no_tools():
    snap = _snapshot()
    provider = _openai(_returns(_grounded_result_json(snap)))
    _analyze(provider, snap)
    call = provider._client.responses.calls[0]
    assert call['store'] is False                       # stateless: no stored prompt/reasoning
    assert 'tools' not in call                          # no tools / web / shell / code exec
    assert call['timeout'] == 30                         # explicit request timeout
    fmt = call['text']['format']
    assert fmt['type'] == 'json_schema' and fmt['strict'] is True
    assert fmt['schema'] == ai_triage.INCIDENT_TRIAGE_RESULT_SCHEMA
    roles = [m['role'] for m in call['input']]
    assert roles == ['system', 'user']


def test_openai_output_text_walks_content_blocks_and_ignores_reasoning():
    # output_text absent -> walk output[].content[], picking only output_text parts.
    class _Part:
        def __init__(self, type_, text=None):
            self.type = type_
            self.text = text

    class _Item:
        def __init__(self, content):
            self.content = content

    class _Resp:
        output_text = None
        model = 'm'
        usage = _FakeUsage(1, 1)
        output = [_Item([_Part('reasoning'), _Part('output_text', '{"ok":1}')])]

    assert ai_providers._openai_output_text(_Resp()) == '{"ok":1}'


# --------------------------------------------------------------------------
# Malformed / failure transport paths
# --------------------------------------------------------------------------
def test_openai_malformed_response_rejected_by_validator():
    snap = _snapshot()
    provider = _openai(_returns('{ this is not valid json'))
    raw = _analyze(provider, snap)
    with pytest.raises(ai_triage.TriageValidationError) as exc:
        ai_triage.validate_triage_output(raw.raw_text, snap, ai_triage.AGENT_POLICY)
    assert exc.value.error_code == 'malformed_json'


def test_openai_timeout_retries_to_limit_then_raises():
    snap = _snapshot()

    def _always_timeout(kwargs, n):
        raise TimeoutError('slow')

    provider = _openai(_always_timeout, max_attempts=3)
    with pytest.raises(ai_providers.TriageProviderError) as exc:
        _analyze(provider, snap)
    assert exc.value.error_code == 'provider_timeout'
    assert exc.value.retryable is True
    assert len(provider._client.responses.calls) == 3  # bounded retry limit honored


def test_openai_rate_limit_is_retryable():
    snap = _snapshot()

    def _rate_limited(kwargs, n):
        raise _FakeAPIError(429)

    provider = _openai(_rate_limited, max_attempts=1)
    with pytest.raises(ai_providers.TriageProviderError) as exc:
        _analyze(provider, snap)
    assert exc.value.error_code == 'provider_rate_limited'
    assert exc.value.retryable is True


def test_openai_provider_unavailable_on_5xx():
    snap = _snapshot()
    provider = _openai(lambda k, n: (_ for _ in ()).throw(_FakeAPIError(503)), max_attempts=1)
    with pytest.raises(ai_providers.TriageProviderError) as exc:
        _analyze(provider, snap)
    assert exc.value.error_code == 'provider_unavailable'
    assert exc.value.retryable is True


def test_openai_retries_then_succeeds():
    snap = _snapshot()
    good = _grounded_result_json(snap)

    def _flaky(kwargs, n):
        if n == 1:
            raise _FakeAPIError(503)
        return _FakeResponse(good)

    provider = _openai(_flaky, max_attempts=3)
    raw = _analyze(provider, snap)
    assert len(provider._client.responses.calls) == 2
    ai_triage.validate_triage_output(raw.raw_text, snap, ai_triage.AGENT_POLICY)


def test_openai_4xx_is_not_retried():
    snap = _snapshot()
    provider = _openai(lambda k, n: (_ for _ in ()).throw(_FakeAPIError(400)), max_attempts=3)
    with pytest.raises(ai_providers.TriageProviderError) as exc:
        _analyze(provider, snap)
    assert exc.value.error_code == 'provider_bad_request'
    assert exc.value.retryable is False
    assert len(provider._client.responses.calls) == 1  # non-retryable -> single attempt


# --------------------------------------------------------------------------
# Missing configuration fails closed (no injected client -> reads env)
# --------------------------------------------------------------------------
def test_openai_missing_api_key_fails_closed(monkeypatch):
    for var in ('AI_API_KEY', 'OPENAI_API_KEY'):
        monkeypatch.delenv(var, raising=False)
    provider = ai_providers.OpenAITriageProvider()  # no injected client
    with pytest.raises(ai_providers.TriageProviderError) as exc:
        provider.analyze(prompt=_prompt(_snapshot()), model='some-model', timeout_seconds=5, max_output_tokens=100)
    assert exc.value.error_code == 'missing_api_key'
    assert exc.value.retryable is False


def test_openai_missing_model_fails_closed(monkeypatch):
    monkeypatch.setenv('AI_API_KEY', 'sk-does-not-matter')
    provider = ai_providers.OpenAITriageProvider()
    with pytest.raises(ai_providers.TriageProviderError) as exc:
        provider.analyze(prompt=_prompt(_snapshot()), model='', timeout_seconds=5, max_output_tokens=100)
    assert exc.value.error_code == 'missing_model'


# --------------------------------------------------------------------------
# Grounding: a schema-valid but ungrounded / invented response is rejected
# --------------------------------------------------------------------------
def _tamper(snap, mutate):
    result = ai_providers._deterministic_result_from_snapshot(snap)
    mutate(result)
    return json.dumps(result)


def test_openai_invalid_citation_rejected():
    snap = _snapshot()
    text = _tamper(snap, lambda r: r['citations'].append({'ref': 'telemetry:ghost', 'description': 'x'}))
    provider = _openai(_returns(text))
    raw = _analyze(provider, snap)
    with pytest.raises(ai_triage.TriageValidationError) as exc:
        ai_triage.validate_triage_output(raw.raw_text, snap, ai_triage.AGENT_POLICY)
    assert exc.value.error_code == 'invalid_evidence_reference'


def test_openai_invented_transaction_rejected():
    snap = _snapshot()
    text = _tamper(snap, lambda r: r['affected_entities'].append({'type': 'transaction', 'value': '0xfabricated', 'evidence_refs': ['telemetry:tel-1']}))
    provider = _openai(_returns(text))
    raw = _analyze(provider, snap)
    with pytest.raises(ai_triage.TriageValidationError) as exc:
        ai_triage.validate_triage_output(raw.raw_text, snap, ai_triage.AGENT_POLICY)
    assert exc.value.error_code == 'invented_transaction'


def test_openai_invented_telemetry_id_rejected():
    snap = _snapshot()
    text = _tamper(snap, lambda r: r['timeline'].append({'timestamp': None, 'event': 'x', 'evidence_refs': ['telemetry:does-not-exist']}))
    provider = _openai(_returns(text))
    raw = _analyze(provider, snap)
    with pytest.raises(ai_triage.TriageValidationError) as exc:
        ai_triage.validate_triage_output(raw.raw_text, snap, ai_triage.AGENT_POLICY)
    assert exc.value.error_code == 'invalid_evidence_reference'


def test_openai_unsupported_runbook_rejected():
    snap = _snapshot()
    text = _tamper(snap, lambda r: r.__setitem__('recommended_runbook_id', 'made_up_runbook_v9'))
    provider = _openai(_returns(text))
    raw = _analyze(provider, snap)
    with pytest.raises(ai_triage.TriageValidationError) as exc:
        ai_triage.validate_triage_output(raw.raw_text, snap, ai_triage.AGENT_POLICY)
    assert exc.value.error_code == 'unsupported_runbook'


def test_openai_prohibited_action_rejected():
    snap = _snapshot()
    text = _tamper(snap, lambda r: r.__setitem__('recommended_actions', [{'action_type': 'transfer_funds', 'reason': 'x', 'risk_level': 'high', 'requires_human_approval': True, 'evidence_refs': ['telemetry:tel-1']}]))
    provider = _openai(_returns(text))
    raw = _analyze(provider, snap)
    with pytest.raises(ai_triage.TriageValidationError) as exc:
        ai_triage.validate_triage_output(raw.raw_text, snap, ai_triage.AGENT_POLICY)
    assert exc.value.error_code == 'prohibited_action'


# --------------------------------------------------------------------------
# Job processing integration (mocked DB + mocked OpenAI client)
# --------------------------------------------------------------------------
class _FakeResult:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, **kw):
        self.inserts = defaultdict(list)
        self.claim_row = kw.get('claim_row')
        self.snapshot_json = kw.get('snapshot_json')
        self.budget_ws = kw.get('budget_ws', 0)
        self.budget_global = kw.get('budget_global', 0)

    def execute(self, query, params=None):
        n = ' '.join(str(query).split())
        if n.startswith("UPDATE ai_triage_jobs SET status = 'running'"):
            return _FakeResult(row=self.claim_row)
        if 'FROM incident_evidence_snapshots WHERE id' in n:
            return _FakeResult(row={'id': 'snap-1', 'snapshot_json': self.snapshot_json, 'snapshot_hash': 'sha256:abc', 'schema_version': '1.0'})
        if 'FROM ai_usage_events' in n and 'workspace_id = %s AND created_at' in n:
            return _FakeResult(row={'spent': self.budget_ws})
        if 'FROM ai_usage_events' in n and 'WHERE created_at' in n and 'workspace_id' not in n.split('WHERE', 1)[1]:
            return _FakeResult(row={'spent': self.budget_global})
        for table in ('ai_triage_results', 'ai_triage_citations', 'ai_recommendations', 'ai_usage_events'):
            if n.startswith(f'INSERT INTO {table}'):
                self.inserts[table].append(params)
                return _FakeResult()
        if n.startswith('UPDATE ai_triage_jobs'):
            self.inserts['job_update'].append((n, params))
            return _FakeResult()
        return _FakeResult()


@contextmanager
def _fake_pg(conn):
    yield conn


def _claim_row(**over):
    row = {'id': 'job-1', 'workspace_id': 'ws-1', 'incident_id': 'inc-1', 'evidence_snapshot_id': 'snap-1',
           'retry_count': 0, 'max_retries': 2, 'provider': 'openai', 'model': 'openai-test-model', 'prompt_version': 'v1'}
    row.update(over)
    return row


def _cfg(**over):
    cfg = ai_triage.triage_config()
    cfg['enabled'] = True
    cfg['provider'] = 'openai'
    cfg['model'] = 'openai-test-model'
    cfg.update(over)
    return cfg


def test_process_job_completes_with_openai_mock_client(monkeypatch):
    snap = _snapshot()
    conn = _FakeConn(claim_row=_claim_row(), snapshot_json=snap)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(conn))
    provider = _openai(_returns(_grounded_result_json(snap)))
    out = ai_triage.process_triage_job('job-1', provider_override=provider, config_override=_cfg())
    assert out['status'] in ('completed', 'completed_with_warnings')
    assert len(conn.inserts['ai_triage_results']) == 1
    assert len(conn.inserts['ai_recommendations']) == 1
    assert len(conn.inserts['ai_usage_events']) == 1


def test_process_job_budget_blocked_before_openai_call(monkeypatch):
    snap = _snapshot()
    conn = _FakeConn(claim_row=_claim_row(), snapshot_json=snap, budget_ws=999999)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(conn))

    def _must_not_call(kwargs, n):
        raise AssertionError('provider must not be called when budget blocked')

    provider = _openai(_must_not_call)
    out = ai_triage.process_triage_job('job-1', provider_override=provider, config_override=_cfg(daily_budget_usd=1.0))
    assert out['status'] == 'budget_blocked'
    assert conn.inserts['ai_triage_results'] == []
    assert provider._client.responses.calls == []


def test_process_job_unknown_provider_fails_closed(monkeypatch):
    # No provider_override -> real get_triage_provider('mystery') -> fail closed.
    snap = _snapshot()
    conn = _FakeConn(claim_row=_claim_row(provider='mystery'), snapshot_json=snap)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(conn))
    out = ai_triage.process_triage_job('job-1', config_override=_cfg(provider='mystery', max_retries=0))
    assert out['status'] == 'failed'
    assert out['error_code'] == 'unknown_provider'
    assert conn.inserts['ai_triage_results'] == []


def test_no_secrets_or_provider_body_in_logs(monkeypatch, caplog):
    monkeypatch.setenv('AI_API_KEY', 'sk-SUPER-SECRET-KEY-123')
    snap = _snapshot()
    conn = _FakeConn(claim_row=_claim_row(), snapshot_json=snap)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(conn))

    def _leaky_failure(kwargs, n):
        raise _FakeAPIError(400, body='INTERNAL-PROVIDER-BODY-LEAK secret=sk-SUPER-SECRET-KEY-123')

    provider = _openai(_leaky_failure)
    with caplog.at_level(logging.INFO):
        out = ai_triage.process_triage_job('job-1', provider_override=provider, config_override=_cfg(max_retries=0))
    assert out['status'] == 'failed'
    assert out['error_code'] == 'provider_bad_request'
    combined = '\n'.join(r.getMessage() for r in caplog.records)
    assert 'sk-SUPER-SECRET-KEY-123' not in combined
    assert 'INTERNAL-PROVIDER-BODY-LEAK' not in combined
    # the stable error_code is still observable for diagnosis
    assert 'provider_bad_request' in combined


# --------------------------------------------------------------------------
# The mock provider remains the default in tests and stays grounded.
# --------------------------------------------------------------------------
def test_mock_provider_still_default_and_grounded():
    snap = _snapshot()
    provider = ai_providers.get_triage_provider('')
    raw = provider.analyze(prompt=_prompt(snap), model='mock', timeout_seconds=5, max_output_tokens=1000)
    validated = ai_triage.validate_triage_output(raw.raw_text, snap, ai_triage.AGENT_POLICY)
    assert validated['result']['incident_id'] == 'inc-1'
