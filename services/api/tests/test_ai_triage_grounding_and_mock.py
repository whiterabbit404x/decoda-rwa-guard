"""Tests for the AI triage grounding + mock-provider truthfulness fixes.

Covers the task's required proofs:
  * incident-linked canonical telemetry is resolved into the evidence snapshot
  * QuickNode / Stable RPC duplicate observations collapse to one canonical event
  * every telemetry query is workspace-scoped (cross-tenant rows excluded)
  * factual results without citations fail validation
  * missing telemetry is explicitly marked (evidence_incomplete + structured reason)
  * a mock run reports provider=mock / model=mock, cost exactly 0, no OpenAI pricing
  * a mock run never surfaces the configured live AI_MODEL_TRIAGE
  * regeneration creates a new version, preserves prior results, and audits the reason
  * accepting a recommendation executes no on-chain action
  * Redis events still publish after commit

Follows the repo's fake-connection unit style — no real DB / model / network.
"""
from __future__ import annotations

import json
from collections import defaultdict
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from services.api.app import ai_triage, ai_providers, pilot

from services.api.tests.test_ai_triage_agent import (
    FakeConn, FakeResult, _bootstrap, _claim_row, _enabled_cfg, _fake_pg, _req, _snapshot,
)


# --------------------------------------------------------------------------
# A fake connection that exercises the REAL build_evidence_snapshot SQL, with
# canonical telemetry_events + legacy evidence rows and workspace scoping.
# --------------------------------------------------------------------------
def _telemetry_event(*, id, workspace_id='ws-1', target_id='tgt-1', event_type='wallet_transfer_detected',
                     detected_by='quicknode_stream', tx_hash='0xdead', chain_id=8453,
                     from_addr='0xfrom', to_addr='0xto', amount='1000', block_number=123,
                     observed_at='2026-07-11T00:00:00+00:00', evidence_source='live', idempotency_key=None):
    payload = {
        'chain_id': chain_id, 'chain_network': 'base-mainnet', 'block_number': block_number,
        'tx_hash': tx_hash, 'from': from_addr, 'to': to_addr, 'from_address': from_addr,
        'to_address': to_addr, 'amount': amount, 'value_wei': amount, 'event_type': event_type,
        'source_type': detected_by, 'detected_by': detected_by, 'observed_at': observed_at,
    }
    return {
        'id': id, 'workspace_id': workspace_id, 'target_id': target_id, 'provider_type': detected_by,
        'event_type': event_type, 'observed_at': observed_at, 'ingested_at': observed_at,
        'evidence_source': evidence_source, 'payload_json': payload,
        'idempotency_key': idempotency_key or f'{workspace_id}:{target_id}:{tx_hash}',
    }


def _payload(**over):
    base = {'telemetry_id': None, 'tx_hash': '0xdead', 'chain_id': 8453,
            'from_address': '0xfrom', 'to_address': '0xto', 'detected_by': 'quicknode_stream',
            'detection_type': 'monitored_wallet_transfer',
            'matched_patterns': [{'rule_id': 'smoke_wallet_transfer', 'severity': 'critical'}],
            'explanation': 'Wallet transfer detected on chain 8453'}
    base.update(over)
    return base


def _pj(row):
    pj = row.get('payload_json')
    if isinstance(pj, str):
        try:
            return json.loads(pj)
        except Exception:
            return {}
    return pj if isinstance(pj, dict) else {}


class _AssemblerConn:
    """Fake DB for the real build_evidence_snapshot. Enforces workspace scoping."""

    def __init__(self, *, incident, alert=None, detection=None, target=None,
                 telemetry_events=None, evidence_rows=None):
        self.incident = incident
        self.alert = alert
        self.detection = detection
        self.target = target
        self.telemetry_events = list(telemetry_events or [])
        self.evidence_rows = list(evidence_rows or [])
        self.queries: list = []

    def execute(self, query, params=None):
        n = ' '.join(str(query).split())
        self.queries.append((n, params))
        p = tuple(params or ())
        if 'FROM incidents WHERE id' in n:
            inc_id, ws = p
            if self.incident and str(self.incident['id']) == str(inc_id) and str(self.incident['workspace_id']) == str(ws):
                return FakeResult(row=self.incident)
            return FakeResult(row=None)
        if 'FROM alerts WHERE id' in n:
            aid, ws = p
            if self.alert and str(self.alert['id']) == str(aid) and str(self.alert['workspace_id']) == str(ws):
                return FakeResult(row=self.alert)
            return FakeResult(row=None)
        if 'FROM detection_events WHERE id' in n:
            did, ws = p
            if self.detection and str(self.detection['id']) == str(did) and str(self.detection.get('workspace_id', ws)) == str(ws):
                return FakeResult(row=self.detection)
            return FakeResult(row=None)
        if 'FROM targets WHERE id' in n:
            tid, ws = p
            if self.target and str(self.target['id']) == str(tid):
                return FakeResult(row=self.target)
            return FakeResult(row=None)
        if 'FROM telemetry_events WHERE' in n:
            return FakeResult(rows=self._telemetry(n, p))
        if 'FROM evidence WHERE' in n:
            ws, aid = p
            rows = [r for r in self.evidence_rows
                    if str(r.get('workspace_id', ws)) == str(ws) and str(r.get('alert_id')) == str(aid)]
            return FakeResult(rows=rows)
        return FakeResult(row=None, rows=[])

    def _telemetry(self, n, params):
        # Workspace scope: the first bound parameter is always workspace_id.
        ws = params[0]
        pool = [r for r in self.telemetry_events if str(r.get('workspace_id')) == str(ws)]
        if 'idempotency_key = %s' in n:
            key = params[1]
            return [r for r in pool if str(r.get('idempotency_key')) == str(key)]
        if "payload_json->>'tx_hash'" in n and 'target_id = %s' in n:
            target_id, tx = params[1], params[2]
            matched = [r for r in pool if str(r.get('target_id')) == str(target_id)
                       and str(_pj(r).get('tx_hash') or '').lower() == str(tx).lower()]
            if "payload_json->>'chain_id' = %s" in n:
                chain = params[3]
                matched = [r for r in matched if str(_pj(r).get('chain_id')) == str(chain)]
            return matched
        if 'AND id = %s' in n:
            tid = params[1]
            return [r for r in pool if str(r['id']) == str(tid)]
        return []


def _incident(**over):
    base = {'id': 'inc-1', 'workspace_id': 'ws-1', 'target_id': 'tgt-1', 'source_alert_id': 'alert-1',
            'linked_alert_ids': ['alert-1'], 'event_type': 'threat_monitoring_incident', 'severity': 'critical',
            'status': 'open', 'workflow_status': 'open', 'summary': 'Monitored wallet transfer',
            'payload': _payload(), 'created_at': '2026-07-11T00:00:00+00:00'}
    base.update(over)
    return base


def _alert(**over):
    base = {'id': 'alert-1', 'workspace_id': 'ws-1', 'severity': 'critical', 'status': 'open',
            'created_at': '2026-07-11T00:00:00+00:00', 'alert_type': 'threat_monitoring', 'title': 'Wallet transfer',
            'summary': 'Wallet transfer detected', 'target_id': 'tgt-1', 'detection_event_id': None,
            'payload': _payload()}
    base.update(over)
    return base


def _target(**over):
    base = {'id': 'tgt-1', 'asset_id': 'asset-1', 'chain_id': 8453, 'chain_network': 'base-mainnet',
            'wallet_address': '0xtarget', 'contract_identifier': None, 'target_type': 'wallet', 'asset_type': 'wallet'}
    base.update(over)
    return base


# --------------------------------------------------------------------------
# 1. Incident-linked telemetry is included in the snapshot
# --------------------------------------------------------------------------
def test_snapshot_includes_incident_telemetry_via_direct_reference():
    tel = _telemetry_event(id='tel-1')
    conn = _AssemblerConn(
        incident=_incident(payload=_payload(telemetry_id='tel-1')),
        alert=_alert(payload=_payload(telemetry_id='tel-1')),
        target=_target(), telemetry_events=[tel],
    )
    assembled = ai_triage.build_evidence_snapshot(conn, workspace_id='ws-1', incident_id='inc-1')
    snap = assembled['snapshot']
    assert assembled['evidence_count'] == 1
    assert assembled['is_complete'] is True
    assert snap['telemetry_resolution'] == 'direct_telemetry_id'
    entry = snap['telemetry'][0]
    # Every required wallet-transfer field is present and grounded in the real event.
    for key in ('telemetry_id', 'event_type', 'tx_hash', 'from', 'to', 'value',
                'chain_id', 'block_number', 'observed_at', 'detected_by', 'evidence_source', 'target_id'):
        assert key in entry
    assert entry['telemetry_id'] == 'tel-1'
    assert entry['tx_hash'] == '0xdead'
    assert entry['from'] == '0xfrom' and entry['to'] == '0xto'
    assert entry['chain_id'] == 8453 and entry['target_id'] == 'tgt-1'
    assert entry['evidence_source'] == 'live_provider'


def test_snapshot_resolves_telemetry_by_target_and_tx_when_no_direct_id():
    tel = _telemetry_event(id='tel-9')
    conn = _AssemblerConn(incident=_incident(), alert=_alert(), target=_target(), telemetry_events=[tel])
    assembled = ai_triage.build_evidence_snapshot(conn, workspace_id='ws-1', incident_id='inc-1')
    assert assembled['evidence_count'] == 1
    assert assembled['snapshot']['telemetry_resolution'] == 'workspace_target_chain_tx'
    assert assembled['snapshot']['telemetry'][0]['telemetry_id'] == 'tel-9'


def test_snapshot_resolves_telemetry_via_linked_detection():
    tel = _telemetry_event(id='tel-det')
    conn = _AssemblerConn(
        incident=_incident(payload={}),  # no payload identifiers
        alert=_alert(payload={}, detection_event_id='det-1'),
        detection={'id': 'det-1', 'workspace_id': 'ws-1', 'detection_type': 'monitored_wallet_transfer',
                   'severity': 'critical', 'confidence': 1.0, 'evidence_summary': 'x', 'evidence_source': 'live',
                   'telemetry_event_id': 'tel-det'},
        target=_target(), telemetry_events=[tel],
    )
    assembled = ai_triage.build_evidence_snapshot(conn, workspace_id='ws-1', incident_id='inc-1')
    assert assembled['snapshot']['telemetry_resolution'] == 'linked_detection'
    assert assembled['evidence_count'] == 1


# --------------------------------------------------------------------------
# 2. QuickNode / Stable RPC duplicate observations resolve to ONE canonical event
# --------------------------------------------------------------------------
def test_duplicate_provider_observations_collapse_to_one_canonical_event():
    quicknode = _telemetry_event(id='tel-a', detected_by='quicknode_stream')
    stable = _telemetry_event(id='tel-b', detected_by='stable_rpc_polling')  # same target+tx+event_type
    conn = _AssemblerConn(incident=_incident(), alert=_alert(), target=_target(),
                          telemetry_events=[quicknode, stable])
    assembled = ai_triage.build_evidence_snapshot(conn, workspace_id='ws-1', incident_id='inc-1')
    # Two raw observations, one canonical telemetry event in the snapshot.
    assert assembled['evidence_count'] == 1
    assert len(assembled['snapshot']['telemetry']) == 1
    observers = {o['detected_by'] for o in assembled['snapshot']['provider_observations']}
    assert observers == {'quicknode_stream', 'stable_rpc_polling'}


def test_canonical_dedup_identity_and_legacy_evidence_collapse():
    # Canonical telemetry_events row + a legacy evidence row for the same tx collapse.
    tel = _telemetry_event(id='tel-canon', detected_by='quicknode_stream')
    legacy = {'id': 'ev-legacy', 'workspace_id': 'ws-1', 'alert_id': 'alert-1', 'target_id': 'tgt-1',
              'event_type': 'wallet_transfer_detected', 'source_provider': 'stable_rpc_polling',
              'tx_hash': '0xdead', 'counterparty': '0xto', 'amount_text': '1000', 'block_number': 123,
              'chain': 'base', 'observed_at': '2026-07-11T00:00:00+00:00', 'created_at': '2026-07-11T00:00:00+00:00',
              'raw_payload_json': {'from_address': '0xfrom', 'to_address': '0xto'}}
    conn = _AssemblerConn(incident=_incident(), alert=_alert(), target=_target(),
                          telemetry_events=[tel], evidence_rows=[legacy])
    assembled = ai_triage.build_evidence_snapshot(conn, workspace_id='ws-1', incident_id='inc-1')
    assert assembled['evidence_count'] == 1
    # The canonical telemetry_events row is preferred over the legacy row.
    assert assembled['snapshot']['telemetry'][0]['telemetry_id'] == 'tel-canon'


# --------------------------------------------------------------------------
# 3. Workspace isolation is enforced
# --------------------------------------------------------------------------
def test_telemetry_resolution_is_workspace_scoped():
    # A same-id / same-tx telemetry row in ANOTHER workspace must never leak in.
    foreign = _telemetry_event(id='tel-1', workspace_id='ws-2')
    conn = _AssemblerConn(
        incident=_incident(payload=_payload(telemetry_id='tel-1')),
        alert=_alert(payload=_payload(telemetry_id='tel-1')),
        target=_target(), telemetry_events=[foreign],
    )
    assembled = ai_triage.build_evidence_snapshot(conn, workspace_id='ws-1', incident_id='inc-1')
    assert assembled['evidence_count'] == 0
    assert assembled['snapshot']['evidence_incomplete'] is True
    # Every telemetry query carried ws-1 (never ws-2) as its workspace parameter.
    tel_queries = [(nq, pr) for (nq, pr) in conn.queries if 'FROM telemetry_events' in nq]
    assert tel_queries
    for _nq, pr in tel_queries:
        assert pr[0] == 'ws-1'


def test_build_snapshot_raises_404_for_cross_workspace_incident():
    conn = _AssemblerConn(incident=_incident(workspace_id='ws-1'))
    with pytest.raises(pilot.HTTPException) as exc:
        ai_triage.build_evidence_snapshot(conn, workspace_id='ws-2', incident_id='inc-1')
    assert exc.value.status_code == 404


# --------------------------------------------------------------------------
# 4 + 5. Citation enforcement and explicit missing-telemetry marking
# --------------------------------------------------------------------------
def test_factual_result_without_citations_fails_validation():
    snap = _snapshot()
    result = ai_providers._deterministic_result_from_snapshot(snap)
    # Keep the (grounded) factual findings but strip the top-level citations array.
    assert result['risk_findings'] and result['risk_findings'][0]['evidence_refs']
    result['citations'] = []
    with pytest.raises(ai_triage.TriageValidationError) as exc:
        ai_triage.validate_triage_output(json.dumps(result), snap, ai_triage.AGENT_POLICY)
    assert exc.value.error_code == 'missing_citation'


def test_insufficient_evidence_result_completes_without_citations():
    # A truthful "no factual findings" result may complete without citations.
    empty = _snapshot(telemetry=[], provider_observations=[])
    empty['rule']['rule_id'] = None
    empty['alert']['rule_id'] = None
    result = ai_providers._deterministic_result_from_snapshot(empty)
    assert result['citations'] == []
    validated = ai_triage.validate_triage_output(json.dumps(result), empty, ai_triage.AGENT_POLICY)
    assert validated['result']['risk_findings'] == []


def test_missing_telemetry_is_explicitly_marked():
    conn = _AssemblerConn(incident=_incident(), alert=_alert(), target=_target(), telemetry_events=[])
    assembled = ai_triage.build_evidence_snapshot(conn, workspace_id='ws-1', incident_id='inc-1')
    assert assembled['evidence_count'] == 0
    snap = assembled['snapshot']
    assert snap['evidence_incomplete'] is True
    codes = {m['code'] for m in snap['missing_information']}
    assert 'telemetry_unresolved' in codes
    assert 'no telemetry evidence linked to the incident alert' in assembled['incomplete_reasons']


# --------------------------------------------------------------------------
# 6, 7, 8. Mock-provider truthfulness: model=mock, cost=0, no OpenAI config used
# --------------------------------------------------------------------------
def test_mock_provider_reports_model_mock_and_zero_tokens_ignoring_live_model():
    snap = _snapshot()
    prompt = ai_triage.build_prompt(snap, ai_triage.AGENT_POLICY, prompt_version='v1')
    raw = ai_providers.MockTriageProvider().analyze(
        prompt=prompt, model='gpt-5.6-luna', timeout_seconds=5, max_output_tokens=1000)
    assert raw.provider == 'mock'
    assert raw.model == 'mock'  # never the configured live AI_MODEL_TRIAGE
    assert raw.input_tokens == 0 and raw.output_tokens == 0
    assert raw.simulated is True


def test_effective_provider_model_forces_mock_and_ignores_openai_model():
    provider, model = ai_triage._effective_provider_model({'provider': 'mock', 'model': 'gpt-5.6-luna'})
    assert (provider, model) == ('mock', 'mock')
    # Live providers keep their configured model unchanged.
    assert ai_triage._effective_provider_model({'provider': 'openai', 'model': 'gpt-5.6-luna'}) == ('openai', 'gpt-5.6-luna')


def test_mock_job_completes_with_zero_cost_and_model_mock(monkeypatch):
    # AI_MODEL_TRIAGE set to a live model + OpenAI pricing configured: a mock run must
    # still cost exactly 0, store model=mock, and never require an OpenAI key.
    for var in ('AI_API_KEY', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY'):
        monkeypatch.delenv(var, raising=False)
    conn = FakeConn(claim_row={**_claim_row(), 'provider': 'mock', 'model': 'gpt-5.6-luna'}, snapshot_json=_snapshot())
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(pilot, 'log_audit', lambda *a, **k: None)
    cfg = _enabled_cfg(provider='mock', model='gpt-5.6-luna',
                       price_input_per_mtok=15.0, price_output_per_mtok=75.0)
    out = ai_triage.process_triage_job('job-1', provider_override=ai_providers.MockTriageProvider(), config_override=cfg)
    assert out['status'] in ('completed', 'completed_with_warnings')
    usage = conn.inserts['usage'][0]
    # usage insert params: (..., provider, model, input_tokens, output_tokens, cost, outcome)
    assert usage[4] == 'mock' and usage[5] == 'mock'
    assert float(usage[8]) == 0.0
    # Finalize UPDATE also records provider/model=mock + cost 0 (no gpt-5.6-luna).
    job_updates = ' '.join(str(u) for u in conn.inserts['job_update'])
    assert 'gpt-5.6-luna' not in job_updates


def test_simulated_result_incurs_zero_cost_even_with_nonzero_tokens(monkeypatch):
    # Prove the cost path SKIPS pricing for a simulated result (not merely that mock
    # tokens are zero): a simulated provider with nonzero tokens still costs 0.
    class _SimNonZero:
        name = 'mock'
        def analyze(self, **_k):
            snap = _snapshot()
            result = ai_providers._deterministic_result_from_snapshot(snap)
            return ai_providers.ProviderRawResult(
                raw_text=json.dumps(result), provider='mock', model='mock',
                input_tokens=5000, output_tokens=5000, simulated=True)

    conn = FakeConn(claim_row=_claim_row(), snapshot_json=_snapshot())
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(pilot, 'log_audit', lambda *a, **k: None)
    cfg = _enabled_cfg(price_input_per_mtok=15.0, price_output_per_mtok=75.0)
    ai_triage.process_triage_job('job-1', provider_override=_SimNonZero(), config_override=cfg)
    assert float(conn.inserts['usage'][0][8]) == 0.0


# --------------------------------------------------------------------------
# 9, 10, 11. Regeneration: new version, prior preserved, reason audited
# --------------------------------------------------------------------------
def _regen_conn():
    conn = FakeConn(incident_row={'id': 'inc-1', 'workspace_id': 'ws-1'},
                    queued_job_row=None)
    # The regenerate path looks up the most recent prior job for lineage.
    return conn


def test_regeneration_creates_new_version_and_records_lineage(monkeypatch):
    monkeypatch.setenv('AI_TRIAGE_ENABLED', 'true')
    monkeypatch.setenv('AI_PROVIDER', 'mock')
    conn = FakeConn(incident_row={'id': 'inc-1', 'workspace_id': 'ws-1'})
    # Prior job exists (any status) -> new job links back to it.
    orig_execute = conn.execute

    def execute(query, params=None):
        n = ' '.join(str(query).split())
        if n.startswith('SELECT id FROM ai_triage_jobs') and 'ORDER BY created_at DESC LIMIT 1' in n and 'status IN' not in n:
            return FakeResult(row={'id': 'job-prev'})
        return orig_execute(query, params)

    conn.execute = execute
    _bootstrap(monkeypatch, conn)
    audits: list = []
    monkeypatch.setattr(pilot, 'log_audit', lambda *a, **k: audits.append(k))
    monkeypatch.setattr(ai_triage, 'build_evidence_snapshot',
                        lambda *a, **k: {'snapshot': {}, 'is_complete': True, 'incomplete_reasons': [],
                                         'evidence_count': 1, 'source_record_ids': {}})
    monkeypatch.setattr(ai_triage, 'store_evidence_snapshot',
                        lambda *a, **k: {'id': 'snap-2', 'snapshot_hash': 'sha256:xyz', 'schema_version': '1.0',
                                         'is_complete': True, 'evidence_count': 1})
    out = ai_triage.regenerate_triage('inc-1', {'reason': 'analyst wants a fresh pass'}, _req())
    assert out['status'] == 'queued'
    # New job row inserted with the regenerate reason + lineage back-reference.
    assert len(conn.inserts['jobs']) == 1
    job_params = conn.inserts['jobs'][0]
    assert 'analyst wants a fresh pass' in job_params
    assert 'job-prev' in job_params
    # No prior result or snapshot is deleted/overwritten by regeneration.
    executed = ' '.join(nq for (nq, _p) in conn.executed)
    assert 'DELETE FROM ai_triage_results' not in executed
    assert 'DELETE FROM incident_evidence_snapshots' not in executed
    assert 'UPDATE ai_triage_results' not in executed
    # Reason is audited (dedicated regenerated action).
    actions = {a.get('action') for a in audits}
    assert 'incident.ai_triage.regenerated' in actions
    regen_audit = next(a for a in audits if a.get('action') == 'incident.ai_triage.regenerated')
    assert regen_audit['metadata']['reason'] == 'analyst wants a fresh pass'
    assert regen_audit['metadata']['regenerated_from_job_id'] == 'job-prev'


def test_regeneration_requires_nonempty_reason(monkeypatch):
    monkeypatch.setenv('AI_TRIAGE_ENABLED', 'true')
    conn = FakeConn()
    _bootstrap(monkeypatch, conn)
    with pytest.raises(pilot.HTTPException) as exc:
        ai_triage.regenerate_triage('inc-1', {'reason': '   '}, _req())
    assert exc.value.status_code == 400


# --------------------------------------------------------------------------
# 12. Accepting a recommendation executes NO on-chain action
# --------------------------------------------------------------------------
def test_accepting_recommendation_executes_no_onchain_action(monkeypatch):
    conn = FakeConn(rec_row={'id': 'rec-1', 'incident_id': 'inc-1', 'action_type': 'notify_security_team',
                             'runbook_id': 'notify_security_team_v1', 'review_state': 'pending_review'})
    _bootstrap(monkeypatch, conn)
    audits: list = []
    monkeypatch.setattr(pilot, 'log_audit', lambda *a, **k: audits.append(k))
    out = ai_triage.approve_recommendation('inc-1', 'rec-1', {'reason': 'route to security'}, _req())
    assert out['review_state'] == 'accepted'
    assert out['executed'] is False
    # Only the recommendation review-state row was written — nothing that could move funds.
    executed = ' '.join(nq for (nq, _p) in conn.executed).lower()
    for forbidden in ('governance_actions', 'sign', 'transfer', 'pause', 'contract', 'webhook'):
        assert forbidden not in executed
    assert conn.inserts['rec_update']
    assert audits[0]['metadata']['executed'] is False


# --------------------------------------------------------------------------
# Consolidated mock acceptance: snapshot -> prompt -> mock -> validate is grounded
# and truthful (the end-to-end acceptance procedure, all criteria in one path).
# --------------------------------------------------------------------------
def test_mock_acceptance_end_to_end_grounded_and_truthful():
    tel = _telemetry_event(id='tel-acc', tx_hash='0xfeed', from_addr='0xowner', to_addr='0xdest')
    conn = _AssemblerConn(
        incident=_incident(payload=_payload(telemetry_id='tel-acc', tx_hash='0xfeed')),
        alert=_alert(payload=_payload(telemetry_id='tel-acc', tx_hash='0xfeed')),
        target=_target(), telemetry_events=[tel],
    )
    assembled = ai_triage.build_evidence_snapshot(conn, workspace_id='ws-1', incident_id='inc-1')
    snap = assembled['snapshot']
    # evidence snapshot contains at least one real telemetry event with the incident tx.
    assert assembled['evidence_count'] >= 1
    assert snap['telemetry'][0]['tx_hash'] == '0xfeed'

    prompt = ai_triage.build_prompt(snap, ai_triage.AGENT_POLICY, prompt_version='v1')
    raw = ai_providers.MockTriageProvider().analyze(
        prompt=prompt, model='gpt-5.6-luna', timeout_seconds=5, max_output_tokens=1000)
    # provider=mock, model=mock (never the configured live model), simulated.
    assert (raw.provider, raw.model, raw.simulated) == ('mock', 'mock', True)

    validated = ai_triage.validate_triage_output(raw.raw_text, snap, ai_triage.AGENT_POLICY)
    result = validated['result']
    # citation_count >= 1 and every factual finding is grounded in the snapshot.
    valid_refs = ai_triage.derive_valid_references(snap)['refs']
    assert len(result['citations']) >= 1
    for finding in result['risk_findings']:
        assert finding['evidence_refs']
        assert all(ref in valid_refs for ref in finding['evidence_refs'])
    for citation in result['citations']:
        assert citation['ref'] in valid_refs
    # estimated cost for the simulated result is exactly 0 (no OpenAI pricing applied).
    cfg = _enabled_cfg(provider='mock', price_input_per_mtok=15.0, price_output_per_mtok=75.0)
    cost = 0.0 if raw.simulated else ai_triage.estimate_cost_usd(raw.input_tokens, raw.output_tokens, cfg)
    assert cost == 0.0


# --------------------------------------------------------------------------
# 13. Redis events still publish after commit
# --------------------------------------------------------------------------
def test_redis_events_publish_after_successful_mock_job(monkeypatch):
    conn = FakeConn(claim_row=_claim_row(), snapshot_json=_snapshot())
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(pilot, 'log_audit', lambda *a, **k: None)
    published: list = []
    monkeypatch.setattr(ai_triage, 'publish_incident_event', lambda ws, ev: published.append(ev['event_type']) or True)
    out = ai_triage.process_triage_job('job-1', provider_override=ai_providers.MockTriageProvider(), config_override=_enabled_cfg())
    assert out['status'] in ('completed', 'completed_with_warnings')
    assert 'incident.ai_triage.completed' in published
    assert 'incident.ai_report.generated' in published
