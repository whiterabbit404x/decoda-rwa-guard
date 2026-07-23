"""Executive Brief staleness / invalidation + active-now vs period wording.

Covers the corrections for the stale-brief bug:

  * a brief is keyed on a fingerprint of the canonical state it narrates, so a
    same-day state change (e.g. the canonical active-incident fix) regenerates it
    instead of serving stale prose;
  * on a fingerprint miss the dashboard shows a deterministic brief assembled from
    the CURRENT aggregates (never contradictory) and, when a scheduler is present,
    queues the AI regeneration in the background;
  * deterministic wording distinguishes active-now counts from reporting-period
    activity and never says "no open incidents" while incidents are active.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from services.api.app import dashboard_summary as ds
from services.api.app.ai_providers import ProviderRawResult
from services.api.app.dashboard_executive_brief import (
    BRIEF_VERSION,
    brief_idempotency_key,
    brief_state_fingerprint,
    build_deterministic_brief,
)


NOW = datetime(2026, 7, 23, 12, 0, 0, tzinfo=timezone.utc)


class _R:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row

    def fetchall(self):
        return []


class BriefStore:
    """Fake connection with a keyed brief table (SELECT/INSERT honour the key).

    Unlike a "return the last row for any SELECT" stub, this stores rows by
    idempotency_key, so a changed fingerprint genuinely misses the stale row.
    """

    def __init__(self):
        self.briefs: dict[str, dict] = {}
        self.selected_keys: list[str] = []

    def execute(self, query, params=None):
        n = ' '.join(str(query).split())
        if n.startswith('SELECT') and 'FROM dashboard_executive_briefs' in n:
            key = params[1]
            self.selected_keys.append(key)
            return _R(self.briefs.get(key))
        if n.startswith('INSERT INTO dashboard_executive_briefs'):
            key = params[2]
            self.briefs.setdefault(key, {
                'headline': params[6], 'summary': params[7], 'key_findings': params[8],
                'recommended_focus': params[9], 'citations': params[10], 'confidence': params[11],
                'generation_mode': params[12], 'provider': params[13], 'model': params[14],
                'prompt_version': params[15], 'created_at': '2026-07-23T12:00:00+00:00',
            })
            return _R(None)
        return _R(None)

    def commit(self):
        pass


class _CountingProvider:
    """Returns a valid grounded AI brief and counts synchronous invocations."""

    def __init__(self):
        self.calls = 0

    def analyze(self, **_kw):
        self.calls += 1
        payload = {
            'headline': 'AI: incidents open',
            'summary': 'AI generated brief grounded in the alert.',
            'key_findings': [{'title': 'Oracle deviation', 'description': 'Alert fired.', 'severity': 'high', 'source_refs': [{'source_type': 'alert', 'source_id': 'a1'}]}],
            'recommended_focus': [{'title': 'Triage alerts', 'reason': 'Active alerts.', 'destination': 'alerts'}],
            'confidence': 0.8,
        }
        return ProviderRawResult(raw_text=json.dumps(payload), provider='openai', model='gpt-x', simulated=False)


def _aggregates(*, open_incidents=0, crit_high=0, active_alerts=0, opened=0, resolved=0, alerts_created=0, risk_score=40, health_score=90):
    return {
        'period_start': '2026-07-22T12:00:00+00:00',
        'period_end': '2026-07-23T12:00:00+00:00',
        'metrics': {'active_alert_count': active_alerts, 'open_incident_count': open_incidents, 'monitored_asset_count': 5, 'total_asset_value_usd': None},
        'incidents_critical_high': crit_high,
        'incidents_opened_24h': opened,
        'incidents_resolved_24h': resolved,
        'alerts_created_24h': alerts_created,
        'telemetry_freshness': 'fresh',
        'last_telemetry_at': '2026-07-23T11:58:00+00:00',
        'risk': {'score': risk_score, 'band': 'moderate', 'evidence_quality': 'complete', 'top_risk_drivers': [{'key': 'k', 'label': 'Incident pressure', 'percent': 50, 'detail': ''}]},
        'health': {'score': health_score, 'status': 'degraded', 'insights': []},
        'deltas': {},
        'citations': [{'source_type': 'alert', 'source_id': 'a1', 'label': 'Oracle deviation', 'occurred_at': None, 'url': '/alerts/a1'}],
    }


# --------------------------------------------------------------------------
# Fingerprint / idempotency key
# --------------------------------------------------------------------------


def test_fingerprint_moves_with_every_tracked_state_field():
    base = {
        'active_incidents_now': 4, 'critical_high_active_incidents_now': 4, 'active_alerts_now': 0,
        'risk_score': 74, 'system_health_score': 60, 'telemetry_freshness': 'fresh',
        'monitoring_state': 'degraded', 'schema_version': 'dashboard-brief-2026-07-1',
    }
    fp0 = brief_state_fingerprint(base)
    for field in list(base):
        changed = dict(base)
        changed[field] = 'x' if isinstance(base[field], str) else base[field] + 1
        assert brief_state_fingerprint(changed) != fp0, field
    # A field outside the tracked set never perturbs the fingerprint.
    noise = dict(base, unrelated_noise=999)
    assert brief_state_fingerprint(noise) == fp0


def test_idempotency_key_embeds_version_and_fingerprint():
    key = brief_idempotency_key('ws-1', '2026-07-23', 'p1', 'abc123')
    assert key == f'ws-1:2026-07-23:v{BRIEF_VERSION}:p1:abc123'
    # No fingerprint -> base key (backwards compatible).
    assert brief_idempotency_key('ws-1', '2026-07-23', 'p1') == f'ws-1:2026-07-23:v{BRIEF_VERSION}:p1'


# --------------------------------------------------------------------------
# Stale brief regenerates after a same-day state change
# --------------------------------------------------------------------------


def test_stale_brief_not_served_after_incident_state_change():
    conn = BriefStore()
    # State A: brief generated (and stored) when 0 incidents were active.
    agg_a = _aggregates(open_incidents=0, active_alerts=0)
    brief_a = ds.resolve_executive_brief(conn, workspace_id='ws-1', aggregates=agg_a, provider=None, now=NOW)
    assert 'No incidents are currently active.' in brief_a['summary']
    assert len(conn.briefs) == 1

    # State B: the canonical incident fix now reports 4 active (4 critical/high).
    agg_b = _aggregates(open_incidents=4, crit_high=4, active_alerts=0)
    brief_b = ds.resolve_executive_brief(conn, workspace_id='ws-1', aggregates=agg_b, provider=None, now=NOW)
    # The stale state-A prose is NOT reused; the brief reflects the new state.
    assert 'There are 4 active incidents, including 4 critical/high incidents.' in brief_b['summary']
    assert 'No incidents are currently active' not in brief_b['summary']
    # A distinct row was stored (fingerprint changed the key); history preserved.
    assert len(conn.briefs) == 2

    # Same state again -> idempotent hit, no new row, identical prose.
    brief_b2 = ds.resolve_executive_brief(conn, workspace_id='ws-1', aggregates=agg_b, provider=None, now=NOW)
    assert brief_b2['summary'] == brief_b['summary']
    assert len(conn.briefs) == 2


def test_miss_returns_deterministic_and_queues_background_refresh():
    conn = BriefStore()
    provider = _CountingProvider()
    scheduled: list = []
    agg = _aggregates(open_incidents=1, active_alerts=3)

    brief = ds.resolve_executive_brief(
        conn, workspace_id='ws-1', aggregates=agg, provider=provider, now=NOW,
        schedule_refresh=scheduled.append,
    )
    # Dashboard is not blocked on the model: deterministic now, no sync AI call,
    # nothing persisted yet (the queued AI brief takes the slot).
    assert brief['generation_mode'] == 'deterministic_fallback'
    assert provider.calls == 0
    assert conn.briefs == {}
    assert len(scheduled) == 1

    # Running the queued job persists the AI brief under the same fingerprinted key.
    ds.generate_and_persist_brief(conn, scheduled[0], provider=provider, now=NOW)
    assert provider.calls == 1
    assert len(conn.briefs) == 1

    # The next request in the same state now returns the stored AI brief.
    brief2 = ds.resolve_executive_brief(
        conn, workspace_id='ws-1', aggregates=agg, provider=provider, now=NOW,
        schedule_refresh=scheduled.append,
    )
    assert brief2['generation_mode'] == 'ai'
    assert provider.calls == 1  # served from storage, not regenerated


def test_inline_generation_only_on_miss_not_every_call():
    conn = BriefStore()
    provider = _CountingProvider()
    agg = _aggregates(open_incidents=1, active_alerts=3)
    # No scheduler -> inline generation, but only on the first (miss) call.
    ds.resolve_executive_brief(conn, workspace_id='ws-1', aggregates=agg, provider=provider, now=NOW)
    ds.resolve_executive_brief(conn, workspace_id='ws-1', aggregates=agg, provider=provider, now=NOW)
    ds.resolve_executive_brief(conn, workspace_id='ws-1', aggregates=agg, provider=provider, now=NOW)
    assert provider.calls == 1  # subsequent same-state calls are storage hits


# --------------------------------------------------------------------------
# Active-now vs reporting-period wording (deterministic fallback)
# --------------------------------------------------------------------------


def test_active_incidents_but_none_opened_in_period():
    agg = _aggregates(open_incidents=4, crit_high=4, active_alerts=0, opened=0, resolved=0, alerts_created=0)
    summary = build_deterministic_brief(agg)['summary']
    assert 'There are 4 active incidents, including 4 critical/high incidents.' in summary
    assert 'No new alerts were created during the last 24 hours.' in summary
    # The forbidden, contradictory phrasing is gone.
    assert 'No open incidents in the current window' not in summary
    assert 'No active alerts or open incidents in the current window' not in summary


def test_no_active_incidents_wording():
    agg = _aggregates(open_incidents=0, active_alerts=0, opened=0, resolved=0, alerts_created=0)
    summary = build_deterministic_brief(agg)['summary']
    assert 'No incidents are currently active.' in summary
    assert 'No new alerts were created during the last 24 hours.' in summary


def test_incidents_opened_and_resolved_during_period():
    agg = _aggregates(open_incidents=0, active_alerts=0, opened=3, resolved=3)
    summary = build_deterministic_brief(agg)['summary']
    assert 'No incidents are currently active; 3 opened and 3 resolved during the last 24 hours.' in summary


def test_alerts_created_but_none_active():
    agg = _aggregates(open_incidents=2, active_alerts=0, alerts_created=5)
    summary = build_deterministic_brief(agg)['summary']
    assert 'There are 2 active incidents' in summary
    assert '5 new alert' in summary and 'created during the last 24 hours' in summary


def test_deterministic_fallback_never_contradicts_current_metrics():
    for open_incidents, active_alerts in [(0, 0), (1, 0), (4, 3), (0, 5)]:
        agg = _aggregates(open_incidents=open_incidents, crit_high=min(open_incidents, 2), active_alerts=active_alerts)
        summary = build_deterministic_brief(agg)['summary']
        assert 'No active alerts or open incidents in the current window' not in summary
        if open_incidents > 0:
            assert 'No incidents are currently active' not in summary
            assert f'{open_incidents} active incident' in summary
        if active_alerts > 0:
            assert f'{active_alerts} active alert' in summary
