"""Executive Brief generation + get-or-create idempotency.

Covers required backend tests:

  9.  Executive brief generation is idempotent.
  10. Invalid AI source references are removed or rejected.
  11. OpenAI failure produces the deterministic fallback.

Plus citation workspace-scoping (defense for required test 12) and the
AI-success storage path.
"""

from __future__ import annotations

import json

from services.api.app import dashboard_summary as ds
from services.api.app.ai_providers import MockTriageProvider, ProviderRawResult
from services.api.app.dashboard_executive_brief import (
    brief_idempotency_key,
    build_deterministic_brief,
    build_source_index,
    generate_executive_brief,
    validate_citations,
)


def _aggregates(**over):
    agg = {
        'period_start': '2026-07-22T00:00:00+00:00',
        'period_end': '2026-07-23T00:00:00+00:00',
        'metrics': {'active_alert_count': 3, 'open_incident_count': 1, 'monitored_asset_count': 5, 'total_asset_value_usd': None},
        'risk': {
            'score': 42, 'band': 'moderate', 'evidence_quality': 'complete',
            'top_risk_drivers': [{'key': 'alert_pressure', 'label': 'Active alert severity & volume', 'percent': 55, 'detail': '3 clusters'}],
        },
        'health': {'score': 88, 'status': 'degraded', 'insights': [{'severity': 'warning', 'message': 'Telemetry is stale.', 'source_type': 'monitoring_target', 'source_id': 't1'}]},
        'deltas': {'risk_score': 5, 'system_health_score': -3, 'active_alert_count': 2, 'open_incident_count': 0},
        'citations': [
            {'source_type': 'alert', 'source_id': 'a1', 'label': 'Oracle deviation', 'occurred_at': '2026-07-22T10:00:00Z', 'url': '/alerts/a1'},
            {'source_type': 'incident', 'source_id': 'i1', 'label': 'Unusual transfer', 'occurred_at': '2026-07-22T11:00:00Z', 'url': '/incidents/i1'},
            {'source_type': 'monitoring_target', 'source_id': 't1', 'label': 'Datto USDC', 'occurred_at': None, 'url': '/monitoring-sources'},
        ],
    }
    agg.update(over)
    return agg


class _CountingProvider:
    """Provider that returns a valid, grounded brief and counts invocations."""

    def __init__(self):
        self.calls = 0

    def analyze(self, **_kw):
        self.calls += 1
        payload = {
            'headline': 'One incident open',
            'summary': 'Risk 42/100 moderate. Health degraded. Review the open incident.',
            'key_findings': [{'title': 'Oracle deviation', 'description': 'Alert fired on oracle feed.', 'severity': 'high', 'source_refs': [{'source_type': 'alert', 'source_id': 'a1'}]}],
            'recommended_focus': [{'title': 'Triage alerts', 'reason': 'Active alerts await review.', 'destination': 'alerts'}],
            'confidence': 0.8,
        }
        return ProviderRawResult(raw_text=json.dumps(payload), provider='openai', model='gpt-x', simulated=False)


class _FailingProvider:
    def analyze(self, **_kw):
        raise RuntimeError('provider timeout')


class _HallucinatingProvider:
    """Cites an id that does not belong to this workspace."""

    def analyze(self, **_kw):
        payload = {
            'headline': 'Fabricated', 'summary': 'Should not be trusted.',
            'key_findings': [{'title': 'x', 'description': 'y', 'severity': 'critical', 'source_refs': [{'source_type': 'alert', 'source_id': 'OTHER-WORKSPACE-ALERT'}]}],
            'recommended_focus': [], 'confidence': 0.99,
        }
        return ProviderRawResult(raw_text=json.dumps(payload), provider='openai', model='gpt-x', simulated=False)


# --------------------------------------------------------------------------
# Idempotency
# --------------------------------------------------------------------------


def test_idempotency_key_is_stable_and_workspace_scoped():
    a = brief_idempotency_key('ws-1', '2026-07-23')
    b = brief_idempotency_key('ws-1', '2026-07-23')
    c = brief_idempotency_key('ws-2', '2026-07-23')
    d = brief_idempotency_key('ws-1', '2026-07-24')
    assert a == b
    assert a != c and a != d
    assert 'ws-1' in a


class _IdempotentConn:
    """Stores one brief row; SELECT returns it after INSERT (get-or-create)."""

    def __init__(self):
        self.stored = None

    class _R:
        def __init__(self, row):
            self._row = row

        def fetchone(self):
            return self._row

        def fetchall(self):
            return []

    def execute(self, query, params=None):
        n = ' '.join(str(query).split())
        if n.startswith('SELECT') and 'FROM dashboard_executive_briefs' in n:
            return self._R(self.stored)
        if n.startswith('INSERT INTO dashboard_executive_briefs'):
            # Persist a row mirroring what get-or-create would read back.
            self.stored = {
                'headline': params[6], 'summary': params[7],
                'key_findings': params[8], 'recommended_focus': params[9], 'citations': params[10],
                'confidence': params[11], 'generation_mode': params[12], 'provider': params[13],
                'model': params[14], 'prompt_version': params[15], 'created_at': '2026-07-23T12:00:00+00:00',
            }
            return self._R(None)
        return self._R(None)


def test_get_or_create_executive_brief_is_idempotent():
    from datetime import datetime, timezone

    conn = _IdempotentConn()
    provider = _CountingProvider()
    now = datetime(2026, 7, 23, 12, 0, 0, tzinfo=timezone.utc)
    agg = _aggregates()

    first = ds.get_or_create_executive_brief(conn, workspace_id='ws-1', aggregates=agg, provider=provider, now=now)
    second = ds.get_or_create_executive_brief(conn, workspace_id='ws-1', aggregates=agg, provider=provider, now=now)

    # The provider is called exactly once; the second call reads the stored row.
    assert provider.calls == 1
    assert first['headline'] == second['headline']
    assert second['generation_mode'] == first['generation_mode']


# --------------------------------------------------------------------------
# Citation validation (invalid / cross-workspace refs removed)
# --------------------------------------------------------------------------


def test_validate_citations_drops_unknown_and_cross_workspace_refs():
    index = build_source_index(_aggregates()['citations'])
    supplied = [
        {'source_type': 'alert', 'source_id': 'a1'},                 # valid
        {'source_type': 'alert', 'source_id': 'UNKNOWN'},            # invalid id
        {'source_type': 'incident', 'source_id': 'a1'},             # right id wrong type
        {'source_type': 'asset', 'source_id': 'from-other-ws'},      # unknown type+id
    ]
    validated = validate_citations(supplied, index)
    assert len(validated) == 1
    assert validated[0]['source_id'] == 'a1'
    assert validated[0]['source_type'] == 'alert'


def test_ai_finding_with_only_invalid_refs_is_rejected_to_fallback():
    brief = generate_executive_brief(aggregates=_aggregates(), provider=_HallucinatingProvider())
    # Ungrounded AI output must degrade to the deterministic fallback.
    assert brief['generation_mode'] == 'deterministic_fallback'
    # No fabricated cross-workspace citation survives.
    all_ids = {c['source_id'] for c in brief['citations']}
    assert 'OTHER-WORKSPACE-ALERT' not in all_ids


def test_valid_ai_output_is_grounded_and_stored_as_ai():
    brief = generate_executive_brief(aggregates=_aggregates(), provider=_CountingProvider())
    assert brief['generation_mode'] == 'ai'
    assert brief['provider'] == 'openai'
    assert brief['citations'], 'AI brief must carry validated citations'
    assert all(c['source_id'] in {'a1', 'i1', 't1'} for c in brief['citations'])


# --------------------------------------------------------------------------
# Provider failure / offline -> deterministic fallback
# --------------------------------------------------------------------------


def test_provider_failure_produces_deterministic_fallback():
    brief = generate_executive_brief(aggregates=_aggregates(), provider=_FailingProvider())
    assert brief['generation_mode'] == 'deterministic_fallback'
    assert brief['headline']
    assert brief['summary']


def test_offline_mock_provider_produces_deterministic_fallback():
    # The offline mock is not a real brief generator; never store its output.
    brief = generate_executive_brief(aggregates=_aggregates(), provider=MockTriageProvider())
    assert brief['generation_mode'] == 'deterministic_fallback'


def test_invalid_json_from_provider_falls_back():
    class _BadJson:
        def analyze(self, **_kw):
            return ProviderRawResult(raw_text='not json{', provider='openai', model='m', simulated=False)

    brief = generate_executive_brief(aggregates=_aggregates(), provider=_BadJson())
    assert brief['generation_mode'] == 'deterministic_fallback'


def test_deterministic_brief_is_grounded_and_actionable():
    brief = build_deterministic_brief(_aggregates())
    assert brief['generation_mode'] == 'deterministic_fallback'
    assert brief['recommended_focus']
    # Focus destinations are always real routes.
    assert all(f['destination'] in {'alerts', 'incidents', 'monitoring', 'assets', 'system-health'} for f in brief['recommended_focus'])
    # A brief that references an incident cites a real workspace source id.
    for finding in brief['key_findings']:
        for ref in finding.get('source_refs', []):
            assert ref['source_id'] in {'a1', 'i1', 't1'}
