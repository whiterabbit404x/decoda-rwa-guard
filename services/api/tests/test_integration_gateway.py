"""Tests for the Integration Gateway (Screen 10) backend.

Covers:
  * pure derivation — provider/API/webhook/connection rows from CANONICAL data only,
    truthful unknowns, and secret redaction (no credential ever reaches a payload);
  * deterministic recommendation detectors — dependency concentration (with the §40
    no-false-positive guard), degraded-provider failover, unstable webhook;
  * connection-risk derivation (never hard-coded);
  * idempotent recommendation persistence (one open row per dedupe_key, obsolete on
    recovery) via a lightweight fake connection — no live database required;
  * the webhook read query selects only safe columns (never a secret);
  * GET read path issues no writes.

These mirror the fake-connection style already used by test_source_optimization_agent.py.
"""
from __future__ import annotations

import json

import pytest

from services.api.app import integration_gateway as ig
from services.api.app import pilot


NOW_ISO = '2026-08-17T12:00:00+00:00'


def _provider_health(providers):
    return {
        'providers': providers,
        'healthy_count': sum(1 for p in providers if p['status'] == 'healthy'),
        'degraded_count': sum(1 for p in providers if p['status'] in {'degraded', 'unavailable', 'error'}),
        'unknown_count': sum(1 for p in providers if p['status'] is None),
        'total': len(providers),
    }


# ---------------------------------------------------------------------------
# Provider rows — canonical status mapping + truthful unknowns.
# ---------------------------------------------------------------------------
def test_provider_rows_map_canonical_status_and_never_invent_health():
    sources = [
        {'provider': 'alchemy', 'operational_primary_provider': 'alchemy', 'primary_provider': 'alchemy',
         'fallback_provider': None, 'status': 'healthy', 'network': 'Base Mainnet', 'chain_id': 8453,
         'provider_checked_at': NOW_ISO, 'successful_latency_ms': 42, 'is_oracle': False, 'evidence_source': 'live'},
        {'provider': 'quicknode', 'operational_primary_provider': None, 'primary_provider': 'quicknode',
         'fallback_provider': None, 'status': 'provider_unavailable', 'status_reason': 'rpc_providers_unavailable',
         'network': 'Ethereum', 'chain_id': 1, 'is_oracle': False},
    ]
    ph = _provider_health([
        {'host': 'alchemy', 'status': 'healthy', 'latency_ms': 42, 'checked_at': NOW_ISO, 'evidence_source': 'live', 'target_count': 1},
        {'host': 'quicknode', 'status': 'unavailable', 'latency_ms': None, 'checked_at': NOW_ISO, 'evidence_source': 'live', 'target_count': 1},
    ])
    rows = ig.build_provider_rows(sources=sources, provider_health=ph)
    by_host = {r['host']: r for r in rows}
    assert by_host['alchemy']['status'] == 'healthy'
    assert by_host['alchemy']['type'] == 'Blockchain RPC'
    # 'unavailable' canonical status maps to Screen-10 'disconnected', never healthy.
    assert by_host['quicknode']['status'] == 'disconnected'
    # Credential age is not measured for RPC providers -> honest null (never fabricated).
    assert by_host['alchemy']['credential_age_days'] is None


def test_provider_with_no_observation_is_unknown_not_healthy():
    sources = [{'provider': 'infura', 'primary_provider': 'infura', 'status': 'provisioning',
                'network': 'Base Mainnet', 'chain_id': 8453, 'is_oracle': False}]
    ph = _provider_health([])  # no canonical observation for infura
    rows = ig.build_provider_rows(sources=sources, provider_health=ph)
    assert rows[0]['status'] == 'unknown'


def test_provider_backoff_downgrades_headline_to_degraded():
    sources = [{'provider': 'alchemy', 'primary_provider': 'alchemy', 'status': 'provider_unavailable',
                'status_reason': 'provider_backoff_active', 'provider_backoff_active': True,
                'network': 'Base Mainnet', 'chain_id': 8453, 'is_oracle': False}]
    # Canonical says healthy (stale pre-backoff snapshot) but backoff is active.
    ph = _provider_health([{'host': 'alchemy', 'status': 'healthy', 'latency_ms': 10, 'checked_at': NOW_ISO, 'evidence_source': 'live', 'target_count': 1}])
    rows = ig.build_provider_rows(sources=sources, provider_health=ph)
    assert rows[0]['status'] == 'degraded'
    assert 'backoff' in (rows[0]['degraded_reason'] or '')


# ---------------------------------------------------------------------------
# Recommendation detectors.
# ---------------------------------------------------------------------------
def test_single_provider_no_fallback_recommends_adding_one():
    sources = [{'provider': 'alchemy', 'operational_primary_provider': 'alchemy', 'primary_provider': 'alchemy',
                'fallback_provider': None, 'status': 'healthy', 'network': 'Base Mainnet', 'chain_id': 8453,
                'provider_checked_at': NOW_ISO, 'successful_latency_ms': 42, 'is_oracle': False}]
    ph = _provider_health([{'host': 'alchemy', 'status': 'healthy', 'latency_ms': 42, 'checked_at': NOW_ISO, 'evidence_source': 'live', 'target_count': 1}])
    rows = ig.build_provider_rows(sources=sources, provider_health=ph)
    recs = ig.evaluate_recommendations(sources=sources, provider_rows=rows, webhook_rows=[])
    types = {r['recommendation_type'] for r in recs}
    assert 'add_fallback_provider' in types
    rec = next(r for r in recs if r['recommendation_type'] == 'add_fallback_provider')
    assert rec['severity'] == 'high'
    assert rec['evidence']['provider'] == 'alchemy'


def test_configured_fallback_suppresses_false_positive():
    # §40: QuickNode already configured as fallback -> do NOT recommend adding one.
    sources = [{'provider': 'alchemy', 'operational_primary_provider': 'alchemy', 'primary_provider': 'alchemy',
                'fallback_provider': 'quicknode', 'status': 'healthy', 'network': 'Base Mainnet', 'chain_id': 8453,
                'provider_checked_at': NOW_ISO, 'successful_latency_ms': 42, 'is_oracle': False}]
    ph = _provider_health([
        {'host': 'alchemy', 'status': 'healthy', 'latency_ms': 42, 'checked_at': NOW_ISO, 'evidence_source': 'live', 'target_count': 1},
        {'host': 'quicknode', 'status': 'healthy', 'latency_ms': 55, 'checked_at': NOW_ISO, 'evidence_source': 'live', 'target_count': 0},
    ])
    rows = ig.build_provider_rows(sources=sources, provider_health=ph)
    recs = ig.evaluate_recommendations(sources=sources, provider_rows=rows, webhook_rows=[])
    assert [r for r in recs if r['recommendation_type'] == 'add_fallback_provider'] == []


def test_degraded_provider_with_healthy_alternate_requires_approval_failover():
    sources = [
        {'provider': 'alchemy', 'primary_provider': 'alchemy', 'fallback_provider': None, 'status': 'degraded',
         'status_reason': 'provider_backoff_active', 'network': 'Base Mainnet', 'chain_id': 8453, 'is_oracle': False},
        {'provider': 'quicknode', 'operational_primary_provider': 'quicknode', 'primary_provider': 'quicknode',
         'fallback_provider': None, 'status': 'healthy', 'network': 'Ethereum', 'chain_id': 1, 'is_oracle': False,
         'provider_checked_at': NOW_ISO, 'successful_latency_ms': 40},
    ]
    ph = _provider_health([
        {'host': 'alchemy', 'status': 'degraded', 'latency_ms': None, 'checked_at': NOW_ISO, 'evidence_source': 'live', 'target_count': 1},
        {'host': 'quicknode', 'status': 'healthy', 'latency_ms': 40, 'checked_at': NOW_ISO, 'evidence_source': 'live', 'target_count': 1},
    ])
    rows = ig.build_provider_rows(sources=sources, provider_health=ph)
    recs = ig.evaluate_recommendations(sources=sources, provider_rows=rows, webhook_rows=[])
    failover = next(r for r in recs if r['recommendation_type'] == 'enable_failover')
    # Switching a live primary route is Level-2 -> approval required (§18/§19).
    assert failover['execution_mode'] == 'approval_required'
    assert failover['evidence']['healthy_alternate'] == 'quicknode'


def test_disconnected_provider_no_alternate_is_critical_advisory():
    sources = [{'provider': 'alchemy', 'primary_provider': 'alchemy', 'fallback_provider': None,
                'status': 'provider_unavailable', 'status_reason': 'rpc_providers_unavailable',
                'network': 'Base Mainnet', 'chain_id': 8453, 'is_oracle': False}]
    ph = _provider_health([{'host': 'alchemy', 'status': 'unavailable', 'latency_ms': None, 'checked_at': NOW_ISO, 'evidence_source': 'live', 'target_count': 1}])
    rows = ig.build_provider_rows(sources=sources, provider_health=ph)
    recs = ig.evaluate_recommendations(sources=sources, provider_rows=rows, webhook_rows=[])
    rec = next(r for r in recs if r['recommendation_type'] == 'investigate_provider_degradation')
    assert rec['severity'] == 'critical'
    assert rec['evidence']['healthy_alternate'] is None


def test_failing_webhook_recommends_review():
    webhook_rows = ig.build_webhook_rows([
        {'id': 'wh1', 'target_url': 'https://siem.example.com/ingest?token=SECRET', 'description': 'SIEM',
         'event_types': ['alert', 'incident'], 'enabled': True, 'secret_configured': True,
         'delivery_total': 12, 'delivery_succeeded': 8, 'delivery_failed': 4, 'delivery_pending': 0,
         'last_delivery_at': NOW_ISO},
    ])
    assert webhook_rows[0]['status'] == 'Failed'  # 8/12 = 67% success
    recs = ig.evaluate_recommendations(sources=[], provider_rows=[], webhook_rows=webhook_rows)
    rec = next(r for r in recs if r['recommendation_type'] == 'review_webhook')
    assert rec['integration_kind'] == 'webhook'
    # The recommendation must not leak the secret-bearing query string.
    assert 'SECRET' not in json.dumps(rec)


# ---------------------------------------------------------------------------
# Connection risk (derived, never hard-coded).
# ---------------------------------------------------------------------------
def _row(host, status, role='primary'):
    return {'integration_key': f'provider:{host}', 'provider': host.capitalize(), 'host': host,
            'type': 'Blockchain RPC', 'status': status, 'role': role, 'networks': ['Base Mainnet'],
            'chains': ['8453'], 'target_count': 1, 'last_check_at': NOW_ISO, 'latency_ms': 10,
            'evidence_source': 'live', 'degraded_reason': None, 'credential_age_days': None, 'credential_configured': None}


def test_connection_risk_unknown_without_integrations():
    risk = ig.compute_connection_risk(provider_rows=[], webhook_rows=[], recommendations=[], has_scan=False, has_any_integration=False)
    assert risk['level'] == 'unknown'


def test_connection_risk_low_when_all_healthy():
    rows = [_row('alchemy', 'healthy'), _row('quicknode', 'healthy', role='fallback')]
    risk = ig.compute_connection_risk(provider_rows=rows, webhook_rows=[], recommendations=[], has_scan=True, has_any_integration=True)
    assert risk['level'] == 'low'


def test_connection_risk_critical_when_disconnected_no_alternate():
    rows = [_row('alchemy', 'disconnected')]
    risk = ig.compute_connection_risk(provider_rows=rows, webhook_rows=[], recommendations=[], has_scan=True, has_any_integration=True)
    assert risk['level'] == 'critical'


def test_connection_risk_high_when_disconnected_with_alternate():
    rows = [_row('alchemy', 'disconnected'), _row('quicknode', 'healthy', role='fallback')]
    risk = ig.compute_connection_risk(provider_rows=rows, webhook_rows=[], recommendations=[], has_scan=True, has_any_integration=True)
    assert risk['level'] == 'high'


def test_connection_risk_medium_when_degraded():
    rows = [_row('alchemy', 'degraded'), _row('quicknode', 'healthy', role='fallback')]
    risk = ig.compute_connection_risk(provider_rows=rows, webhook_rows=[], recommendations=[], has_scan=True, has_any_integration=True)
    assert risk['level'] == 'medium'


# ---------------------------------------------------------------------------
# API rows + secret safety.
# ---------------------------------------------------------------------------
def test_api_rows_never_expose_credentials():
    infra = {
        'email': {'status': 'healthy', 'provider': 'resend', 'checks': {'provider_key_present': True, 'from_address_present': True}},
        'stripe': {'status': 'warning', 'checks': {'secret_key_present': False, 'webhook_secret_present': False}},
        'auth_rate_limiter': {'status': 'healthy', 'checks': {'redis_url_present': True}},
        'slack': {'status': 'warning', 'checks': {'oauth_configured': False}},
    }
    rows = ig.build_api_rows(infra)
    by_key = {r['integration_key']: r for r in rows}
    assert by_key['api:email']['status'] == 'Healthy'
    assert by_key['api:email']['credential_configured'] is True
    assert by_key['api:stripe']['status'] == 'Missing'
    # Never a hint/secret value.
    for row in rows:
        assert row['credential_hint'] is None
        assert 'secret' not in json.dumps(row).lower() or row['credential_hint'] is None


def test_webhook_rows_drop_any_secret_material():
    # Even if upstream accidentally carried a secret, the row builder reads only safe keys.
    rows = ig.build_webhook_rows([
        {'id': 'wh1', 'target_url': 'https://hooks.example.com/x', 'secret_hash': 'DEADBEEF',
         'secret_token': 'sk_live_LEAK', 'enabled': True, 'event_types': ['alert'],
         'delivery_total': 10, 'delivery_succeeded': 10, 'delivery_failed': 0, 'delivery_pending': 0,
         'last_delivery_at': NOW_ISO, 'secret_configured': True},
    ])
    blob = json.dumps(rows)
    assert 'sk_live_LEAK' not in blob
    assert 'DEADBEEF' not in blob
    assert rows[0]['status'] == 'Healthy'
    assert rows[0]['signing_secret_configured'] is True


def test_summarize_counts():
    rows = [_row('alchemy', 'healthy'), _row('quicknode', 'degraded'), _row('infura', 'disconnected')]
    summary = ig.summarize(provider_rows=rows, webhook_rows=[], api_rows=[], open_recommendation_count=2)
    assert summary['healthy'] == 1
    assert summary['degraded'] == 1
    assert summary['disconnected'] == 1
    assert summary['recommendations'] == 2


# ---------------------------------------------------------------------------
# Idempotent persistence (fake connection).
# ---------------------------------------------------------------------------
class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _RecConn:
    """Simulates the integration_recommendations table for persistence tests."""

    def __init__(self, open_rows=None):
        # open_rows: [{'id':..., 'dedupe_key':...}] currently unresolved.
        self.open_rows = list(open_rows or [])
        self.inserts: list[tuple] = []
        self.refresh_updates: list[tuple] = []
        self.obsolete_updates: list[tuple] = []

    def execute(self, query, params=None):
        q = ' '.join(str(query).split())
        up = q.upper()
        params = params or ()
        if up.startswith('SELECT ID, DEDUPE_KEY FROM INTEGRATION_RECOMMENDATIONS'):
            return _Result([{'id': r['id'], 'dedupe_key': r['dedupe_key']} for r in self.open_rows])
        if up.startswith('SELECT ID FROM INTEGRATION_RECOMMENDATIONS'):
            dedupe = params[1]
            match = next((r for r in self.open_rows if r['dedupe_key'] == dedupe), None)
            return _Result([{'id': match['id']}] if match else [])
        if up.startswith('INSERT INTO INTEGRATION_RECOMMENDATIONS'):
            self.inserts.append(params)
            return _Result([])
        if up.startswith('UPDATE INTEGRATION_RECOMMENDATIONS'):
            if "STATUS = 'OBSOLETE'" in up:
                self.obsolete_updates.append(params)
            else:
                self.refresh_updates.append(params)
            return _Result([])
        return _Result([])

    def commit(self):
        pass


def _detected(dedupe_key='add_fallback_provider:dependency:Base Mainnet'):
    return [{
        'integration_key': 'dependency:Base Mainnet', 'integration_kind': 'dependency',
        'recommendation_type': 'add_fallback_provider', 'severity': 'high', 'title': 'Add fallback',
        'description': 'desc', 'reason': 'reason', 'recommended_action': 'action',
        'execution_mode': 'manual', 'risk_if_ignored': 'risk', 'evidence': {'network': 'Base Mainnet'},
        'confidence': 'high', 'dedupe_key': dedupe_key,
    }]


def test_persist_inserts_new_recommendation_once():
    conn = _RecConn(open_rows=[])
    result = pilot._persist_integration_recommendations(
        conn, workspace_id='ws1', detected=_detected(), correlation_id='c1', now=pilot.utc_now(),
    )
    assert result == {'upserted': 1, 'obsoleted': 0}
    assert len(conn.inserts) == 1
    assert len(conn.refresh_updates) == 0


def test_persist_updates_existing_open_recommendation_no_duplicate():
    # An open row for the same dedupe_key already exists -> UPDATE, never a 2nd INSERT (§53).
    conn = _RecConn(open_rows=[{'id': 'rec-1', 'dedupe_key': 'add_fallback_provider:dependency:Base Mainnet'}])
    result = pilot._persist_integration_recommendations(
        conn, workspace_id='ws1', detected=_detected(), correlation_id='c2', now=pilot.utc_now(),
    )
    assert result['upserted'] == 1
    assert len(conn.inserts) == 0
    assert len(conn.refresh_updates) == 1
    assert result['obsoleted'] == 0  # still detected -> not obsoleted


def test_persist_obsoletes_cleared_recommendation_on_recovery():
    # A previously-open recommendation whose condition cleared this scan is obsoleted (§D).
    conn = _RecConn(open_rows=[{'id': 'rec-stale', 'dedupe_key': 'review_webhook:webhook:old'}])
    result = pilot._persist_integration_recommendations(
        conn, workspace_id='ws1', detected=[], correlation_id='c3', now=pilot.utc_now(),
    )
    assert result['obsoleted'] == 1
    assert len(conn.obsolete_updates) == 1


# ---------------------------------------------------------------------------
# Webhook read query selects only safe columns (never a secret) + no writes.
# ---------------------------------------------------------------------------
class _QueryCaptureConn:
    def __init__(self):
        self.queries: list[str] = []

    def execute(self, query, params=None):
        self.queries.append(' '.join(str(query).split()))
        return _Result([])  # no webhooks

    def commit(self):
        pass


def test_build_integration_webhooks_never_selects_secret_columns_and_reads_only():
    conn = _QueryCaptureConn()
    result = pilot._build_integration_webhooks(conn, workspace_id='ws1')
    assert result == []
    joined = ' '.join(conn.queries).lower()
    assert 'secret_hash' not in joined
    assert 'secret_token' not in joined
    # Read path must never mutate.
    assert all(not q.upper().startswith(('INSERT', 'UPDATE', 'DELETE')) for q in conn.queries)


# ---------------------------------------------------------------------------
# §7: a skipped/unmeasured probe carries latency_ms = None (rendered "—"), never 0.
# ---------------------------------------------------------------------------
def test_provider_row_latency_is_null_when_unmeasured_never_zero():
    sources = [{'provider': 'alchemy', 'primary_provider': 'alchemy', 'status': 'degraded',
                'status_reason': 'provider_backoff_active', 'provider_backoff_active': True,
                'network': 'Base Mainnet', 'chain_id': 8453, 'is_oracle': False,
                'successful_latency_ms': None}]
    # Canonical degraded observation from a skipped 429 probe: latency was NOT measured.
    ph = _provider_health([{'host': 'alchemy', 'status': 'degraded', 'latency_ms': None,
                            'checked_at': NOW_ISO, 'evidence_source': 'replay', 'target_count': 1}])
    rows = ig.build_provider_rows(sources=sources, provider_health=ph)
    assert rows[0]['status'] == 'degraded'
    # Null latency stays null (frontend renders "—"); a skipped probe is never "0 ms".
    assert rows[0]['latency_ms'] is None


def test_legacy_zero_latency_from_provider_backoff_normalizes_to_unavailable():
    """Screen-10 cleanup: a persisted failed observation with latency_ms == 0 from a
    provider-backoff / unavailable condition never measured latency. The read model
    normalizes that legacy 0 to None so the operator sees "—", never "0 ms" — WITHOUT
    a new live health check. Mirrors the reported Alchemy Disconnected / 0 ms row."""
    sources = [{'provider': 'alchemy', 'primary_provider': 'alchemy', 'status': 'provider_unavailable',
                'status_reason': 'provider_backoff_active', 'provider_backoff_active': True,
                'network': 'Base Mainnet', 'chain_id': 8453, 'is_oracle': False,
                'successful_latency_ms': None}]
    # Legacy persisted failed record: status unavailable, latency_ms = 0 (placeholder).
    ph = _provider_health([{'host': 'alchemy', 'status': 'unavailable', 'latency_ms': 0,
                            'checked_at': NOW_ISO, 'evidence_source': 'replay', 'target_count': 1}])
    rows = ig.build_provider_rows(sources=sources, provider_health=ph)
    assert rows[0]['status'] == 'disconnected'
    # 0 ms from an unmeasured/backoff observation → None. The frontend fmtLatency()
    # renders None as "—" (proven in apps/web/tests/integrations-screen10.spec.ts),
    # so the operator never sees a misleading "0 ms".
    assert rows[0]['latency_ms'] is None


def test_successful_probe_latency_is_preserved_and_rendered_ms():
    """The complementary truth: a real measured latency (125 ms) is NEVER discarded —
    it is passed through unchanged and rendered as "125 ms"."""
    sources = [{'provider': 'alchemy', 'operational_primary_provider': 'alchemy', 'primary_provider': 'alchemy',
                'status': 'healthy', 'network': 'Base Mainnet', 'chain_id': 8453, 'is_oracle': False,
                'provider_checked_at': NOW_ISO, 'successful_latency_ms': 125, 'evidence_source': 'live'}]
    ph = _provider_health([{'host': 'alchemy', 'status': 'healthy', 'latency_ms': 125,
                            'checked_at': NOW_ISO, 'evidence_source': 'live', 'target_count': 1}])
    rows = ig.build_provider_rows(sources=sources, provider_health=ph)
    assert rows[0]['status'] == 'healthy'
    # A real measured 125 is preserved; frontend fmtLatency() renders it "125 ms".
    assert rows[0]['latency_ms'] == 125


def test_healthy_zero_latency_is_not_blindly_nulled():
    """Guard against over-reach: a legitimate 0 on a HEALTHY/measured observation is
    preserved — normalization is evidence-aware, not a blanket every-zero → None."""
    row = ig._effective_latency_ms(
        latency_ms=0, status='healthy', backoff=False,
        degraded_reason=None, evidence_source='live')
    assert row == 0


# ---------------------------------------------------------------------------
# Scenario G: ONE canonical provider state -> semantically compatible Screen 4 vs
# Screen 10 output. Never "healthy on one screen, disconnected on another".
# ---------------------------------------------------------------------------
# The diagnostic/worker write monitored_systems.runtime_status from the SAME provider
# verdict Screen 10 reads, so feed the row that canonical status would produce.
_RUNTIME_FOR = {'healthy': 'healthy', 'degraded': 'degraded', 'unavailable': 'failed', 'error': 'failed'}


def _screen4_source_status(canonical_status, *, backoff, error_message=None):
    """Screen-4 row status from the same canonical provider record Screen 10 consumes."""
    target = {'enabled': True, 'monitoring_enabled': True, 'asset_id': 'a1'}
    system = {'is_enabled': True, 'runtime_status': _RUNTIME_FOR.get(canonical_status, 'idle'),
              'last_heartbeat': NOW_ISO, 'coverage_reason': None, 'freshness_status': 'fresh'}
    provider_health = {'status': canonical_status, 'error_message': error_message}
    return pilot._derive_source_status(
        target, system, provider_health=provider_health, provider_backoff_active=backoff,
    )[0]


def _screen10_provider_status(canonical_status, *, backoff):
    sources = [{'provider': 'alchemy', 'primary_provider': 'alchemy', 'status': 'degraded',
                'provider_backoff_active': backoff, 'network': 'Base Mainnet', 'chain_id': 8453,
                'is_oracle': False}]
    ph = _provider_health([{'host': 'alchemy', 'status': canonical_status, 'latency_ms': None,
                            'checked_at': NOW_ISO, 'evidence_source': 'live', 'target_count': 1}])
    return ig.build_provider_rows(sources=sources, provider_health=ph)[0]['status']


_UNHEALTHY10 = {'degraded', 'disconnected'}


def test_cross_screen_backoff_is_degraded_on_both_never_contradictory():
    # Same 429/backoff evidence -> Screen 4 non-healthy AND Screen 10 'degraded'.
    s4 = _screen4_source_status('degraded', backoff=True, error_message='provider_backoff_active')
    s10 = _screen10_provider_status('degraded', backoff=True)
    assert s4 != 'healthy'
    assert s10 == 'degraded'
    # The forbidden contradiction (one healthy, one disconnected) never occurs.
    assert not (s4 == 'healthy' and s10 == 'disconnected')


def test_cross_screen_healthy_agrees_on_both():
    s4 = _screen4_source_status('healthy', backoff=False)
    s10 = _screen10_provider_status('healthy', backoff=False)
    assert s4 == 'healthy'
    assert s10 == 'healthy'


def test_cross_screen_genuine_outage_is_down_on_both():
    # A genuine (non-backoff) all-providers outage stays error/Disconnected on both.
    s4 = _screen4_source_status('unavailable', backoff=False)
    s10 = _screen10_provider_status('unavailable', backoff=False)
    assert s4 != 'healthy'
    assert s10 in _UNHEALTHY10 and s10 == 'disconnected'


# ---------------------------------------------------------------------------
# Scenario H: repeated scans of the SAME unresolved condition keep exactly ONE
# open recommendation — never REC-001..REC-00N duplicates.
# ---------------------------------------------------------------------------
class _DedupeConn:
    """Stateful fake of integration_recommendations enforcing the partial-unique
    (workspace, dedupe_key) WHERE resolved_at IS NULL predicate across many scans."""

    def __init__(self):
        self.open_by_key: dict[str, str] = {}   # dedupe_key -> id
        self.insert_count = 0

    def execute(self, query, params=None):
        q = ' '.join(str(query).split())
        up = q.upper()
        params = params or ()
        if up.startswith('SELECT ID, DEDUPE_KEY FROM INTEGRATION_RECOMMENDATIONS'):
            return _Result([{'id': i, 'dedupe_key': k} for k, i in self.open_by_key.items()])
        if up.startswith('SELECT ID FROM INTEGRATION_RECOMMENDATIONS'):
            dedupe = params[1]
            existing = self.open_by_key.get(dedupe)
            return _Result([{'id': existing}] if existing else [])
        if up.startswith('INSERT INTO INTEGRATION_RECOMMENDATIONS'):
            # params: (id, workspace_id, integration_key, kind, type, severity, ... dedupe_key, ...)
            self.insert_count += 1
            self.open_by_key[params[15]] = params[0]
            return _Result([])
        if up.startswith('UPDATE INTEGRATION_RECOMMENDATIONS'):
            return _Result([])
        return _Result([])

    def commit(self):
        pass


def test_recommendation_dedupe_is_idempotent_across_repeated_scans():
    conn = _DedupeConn()
    for _ in range(4):  # four consecutive health checks of the same unresolved condition
        pilot._persist_integration_recommendations(
            conn, workspace_id='ws1', detected=_detected(), correlation_id='c', now=pilot.utc_now(),
        )
    # Exactly ONE insert total; the other three scans UPDATE the same open row.
    assert conn.insert_count == 1
    assert len(conn.open_by_key) == 1


# ---------------------------------------------------------------------------
# Scenario I: GET control-plane is READ-ONLY — it never runs a diagnostic, writes
# provider observations, clears failures, creates recommendations, or moves the scan.
# ---------------------------------------------------------------------------
def test_get_control_plane_is_read_only_no_mutations(monkeypatch):
    writes: list[str] = []

    class _ReadConn:
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if q.upper().startswith(('INSERT', 'UPDATE', 'DELETE')):
                writes.append(q)
            return _Result([])

        def commit(self):
            pass

    class _Ctx:
        def __enter__(self):
            return _ReadConn()

        def __exit__(self, *a):
            return False

    # Any provider-mutating path invoked from a GET is a hard failure.
    def _forbidden(*_a, **_k):
        raise AssertionError('GET control-plane must not mutate provider/scan/recommendation state')

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _Ctx())
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda c: None)
    monkeypatch.setattr(pilot, 'list_monitoring_sources', lambda request: {'sources': [], 'provider_health': None, 'settings': {}})
    monkeypatch.setattr(pilot, 'resolve_workspace_context_for_request', lambda conn, request: ({'id': 'u1'}, {'workspace_id': 'ws1', 'role': 'owner'}, None))
    monkeypatch.setattr(pilot, '_normalize_workspace_role', lambda role: 'owner')
    monkeypatch.setattr(pilot, '_workspace_permission_granted', lambda *a, **k: True)
    monkeypatch.setattr(pilot, 'integration_health_snapshot', lambda conn: {})
    monkeypatch.setattr(pilot, '_build_integration_webhooks', lambda conn, *, workspace_id: [])
    monkeypatch.setattr(pilot, '_load_last_integration_scan', lambda conn, *, workspace_id: None)
    monkeypatch.setattr(pilot, '_load_integration_recommendations', lambda conn, *, workspace_id, **k: [])
    # These MUST NOT be called by a GET.
    monkeypatch.setattr(pilot, 'run_source_diagnostic', _forbidden)
    monkeypatch.setattr(pilot, '_persist_integration_recommendations', _forbidden)
    monkeypatch.setattr(pilot, '_record_integration_scan', _forbidden)
    monkeypatch.setattr(pilot, 'persist_provider_health_evidence', _forbidden)

    result = pilot.get_integration_control_plane(request=object())

    assert 'providers' in result and 'connection_risk' in result
    assert writes == [], f'GET must issue no writes, saw: {writes}'


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(pytest.main([__file__, '-q']))
