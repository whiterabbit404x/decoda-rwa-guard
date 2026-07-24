"""Canonical risk summary + registry enrichment/filtering + AI fallback."""

from __future__ import annotations

from decimal import Decimal

from services.api.app.domains.asset_risk import ai_explanation, registry, summary


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeConn:
    def __init__(self, tables_exist=True, matchers=None):
        self.tables_exist = tables_exist
        self.matchers = matchers or []
        self.writes = []

    def execute(self, query, params=None):
        q = ' '.join(str(query).split())
        ql = q.lower()
        if 'to_regclass' in ql:
            return _Result([{'ok': bool(self.tables_exist)}])
        for needle, rows in self.matchers:
            if needle in q:
                if any(k in ql for k in ('insert', 'update', 'delete')):
                    self.writes.append((q, params))
                return _Result(rows)
        if any(k in ql for k in ('insert into', 'update ', 'delete ')):
            self.writes.append((q, params))
        return _Result([])

    def commit(self):
        pass

    def writes_matching(self, needle):
        return [(q, p) for (q, p) in self.writes if needle in q]


def _dt(day=23):
    from datetime import datetime, timezone
    return datetime(2026, 7, day, 12, 0, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------
def test_summary_empty_workspace_is_truthful():
    # An empty workspace has no reserve-backed assets -> not_configured, never a
    # coverage percent and never "insufficient evidence" (which would imply a
    # reserve-backed asset is missing its proof).
    conn = FakeConn(tables_exist=False, matchers=[('FROM assets', [{'total_assets': 0, 'total_value': 0}])])
    out = summary.build_risk_summary(conn, workspace_id='ws-1')
    assert out['reserve_coverage']['status'] == 'not_configured'
    assert out['reserve_coverage']['coverage_percent'] is None
    assert out['assessed_assets'] == 0
    assert out['assessment_status'] == 'not_started'
    assert 'No protected assets' in out['ai_summary']


def test_summary_wallet_only_workspace_is_not_configured_not_insufficient():
    # A workspace that contains a wallet but no reserve-backed asset must report
    # reserve coverage as not_configured, not insufficient_evidence.
    conn = FakeConn(matchers=[
        ('COALESCE(SUM(value_usd)', [{'total_assets': 1, 'total_value': Decimal('0')}]),
        # No reserve-backed assets configured -> reserve_backed count query returns 0.
        ('lower(COALESCE(rwa_asset_type', [{'n': 0}]),
        ('DISTINCT ON (a.asset_id)', [
            {'asset_id': 'w1', 'risk_score': 35, 'risk_level': 'medium', 'confidence': 0.6, 'data_completeness': 0.6,
             'reserve_status': 'not_applicable', 'reserve_value_usd': None, 'liability_value_usd': None,
             'reserve_coverage_percent': None, 'monitoring_health': 'warning', 'status': 'completed', 'assessed_at': _dt(), 'feed_freshness': {}},
        ]),
        ("status = 'active'", []),
        ('GROUP BY status', []),
    ])
    out = summary.build_risk_summary(conn, workspace_id='ws-1')
    assert out['reserve_coverage']['status'] == 'not_configured'
    assert out['reserve_coverage']['coverage_percent'] is None
    assert out['reserve_backed_count'] == 0
    assert 'reserve-backed' in out['ai_summary'].lower()


def test_summary_reserve_backed_without_evidence_is_insufficient():
    # A reserve-backed asset exists (config count = 1) but no verified reserve
    # evidence -> insufficient_evidence (NOT not_configured, NOT a 0%).
    conn = FakeConn(matchers=[
        ('COALESCE(SUM(value_usd)', [{'total_assets': 1, 'total_value': Decimal('1000000')}]),
        ('lower(COALESCE(rwa_asset_type', [{'n': 1}]),
        ('DISTINCT ON (a.asset_id)', [
            {'asset_id': 's1', 'risk_score': 65, 'risk_level': 'high', 'confidence': 0.5, 'data_completeness': 0.5,
             'reserve_status': 'insufficient_evidence', 'reserve_value_usd': None, 'liability_value_usd': None,
             'reserve_coverage_percent': None, 'monitoring_health': 'warning', 'status': 'completed', 'assessed_at': _dt(), 'feed_freshness': {}},
        ]),
        ("status = 'active'", []),
        ('GROUP BY status', []),
    ])
    out = summary.build_risk_summary(conn, workspace_id='ws-1')
    assert out['reserve_coverage']['status'] == 'insufficient_evidence'
    assert out['reserve_coverage']['coverage_percent'] is None
    assert out['reserve_backed_count'] == 1


def test_summary_worker_disabled_is_reported():
    conn = FakeConn(tables_exist=False, matchers=[('FROM assets', [{'total_assets': 0, 'total_value': 0}])])
    out = summary.build_risk_summary(conn, workspace_id='ws-1')
    assert out['worker']['enabled'] is False
    assert out['worker']['queued'] == 0 and out['worker']['running'] == 0


def test_summary_aggregates_reserve_coverage_from_verified_only():
    conn = FakeConn(matchers=[
        ('COALESCE(SUM(value_usd)', [{'total_assets': 2, 'total_value': Decimal('200000000')}]),
        ('DISTINCT ON (a.asset_id)', [
            {'asset_id': 'a1', 'risk_score': 10, 'risk_level': 'low', 'confidence': 0.9, 'data_completeness': 1.0,
             'reserve_status': 'healthy', 'reserve_value_usd': Decimal('128000000'), 'liability_value_usd': Decimal('100000000'),
             'reserve_coverage_percent': Decimal('128'), 'monitoring_health': 'healthy', 'status': 'completed', 'assessed_at': _dt(), 'feed_freshness': {}},
            {'asset_id': 'a2', 'risk_score': 20, 'risk_level': 'low', 'confidence': 0.8, 'data_completeness': 1.0,
             'reserve_status': 'insufficient_evidence', 'reserve_value_usd': None, 'liability_value_usd': None,
             'reserve_coverage_percent': None, 'monitoring_health': 'warning', 'status': 'completed', 'assessed_at': _dt(), 'feed_freshness': {}},
        ]),
        ("status = 'active'", []),
    ])
    out = summary.build_risk_summary(conn, workspace_id='ws-1')
    # Only the verified asset (a1) is in the aggregate; a2 is not folded in as healthy.
    assert out['reserve_coverage']['assets_included'] == 1
    assert out['reserve_coverage']['coverage_percent'] == 128.0
    assert out['reserve_coverage']['status'] == 'healthy'
    assert out['assessed_assets'] == 2


def test_summary_counts_anomalies_and_gaps():
    conn = FakeConn(matchers=[
        ('COALESCE(SUM(value_usd)', [{'total_assets': 3, 'total_value': Decimal('0')}]),
        ('DISTINCT ON (a.asset_id)', [
            {'asset_id': 'a1', 'risk_score': 82, 'risk_level': 'critical', 'confidence': 0.7, 'data_completeness': 0.8,
             'reserve_status': 'critical', 'reserve_value_usd': Decimal('80'), 'liability_value_usd': Decimal('100'),
             'reserve_coverage_percent': Decimal('80'), 'monitoring_health': 'warning', 'status': 'completed', 'assessed_at': _dt(), 'feed_freshness': {}},
        ]),
        ("status = 'active'", [
            {'asset_id': 'a1', 'finding_type': 'asset_reserve_shortfall', 'severity': 'critical'},
            {'asset_id': 'a2', 'finding_type': 'asset_reserve_feed_stale', 'severity': 'high'},
        ]),
    ])
    out = summary.build_risk_summary(conn, workspace_id='ws-1')
    assert out['anomaly_warnings']['assets'] == 1
    assert out['anomaly_warnings']['highest_severity'] == 'critical'
    assert out['stale_feed_count'] == 1
    assert out['risk_level_counts']['critical'] == 1
    assert 'review' in out['ai_summary'].lower()


def test_summary_narrative_healthy():
    nar = summary.build_summary_narrative({
        'risk_level_counts': {'low': 5, 'medium': 0, 'high': 0, 'critical': 0},
        'anomaly_warnings': {'assets': 0}, 'monitoring_gaps': {'assets': 0}, 'stale_feed_count': 0,
        'reserve_coverage': {'status': 'healthy', 'coverage_percent': 128.0},
    })
    assert '128' in nar and 'within expected ranges' in nar


# --------------------------------------------------------------------------
# Registry enrichment / filter / sort / paginate
# --------------------------------------------------------------------------
def _assets():
    return [
        {'id': 'a1', 'name': 'Treasury A', 'chain_network': 'ethereum-mainnet', 'custodian': 'BNY', 'rwa_asset_type': 'tokenized_treasury', 'value_usd': Decimal('900'), 'asset_type': 'contract'},
        {'id': 'a2', 'name': 'Real Estate B', 'chain_network': 'polygon', 'custodian': 'Acme', 'rwa_asset_type': 'real_estate', 'value_usd': Decimal('300'), 'asset_type': 'contract'},
        {'id': 'a3', 'name': 'Bond C', 'chain_network': 'ethereum-mainnet', 'custodian': 'BNY', 'rwa_asset_type': 'corporate_bond', 'value_usd': Decimal('500'), 'asset_type': 'contract'},
    ]


def _registry_conn():
    return FakeConn(matchers=[
        ('DISTINCT ON (asset_id)', [
            {'asset_id': 'a1', 'risk_score': 12, 'risk_level': 'low', 'confidence': 0.9, 'reserve_status': 'healthy', 'reserve_coverage_percent': Decimal('128'), 'monitoring_health': 'healthy', 'status': 'completed', 'assessed_at': _dt()},
            {'asset_id': 'a2', 'risk_score': 85, 'risk_level': 'critical', 'confidence': 0.6, 'reserve_status': None, 'reserve_coverage_percent': None, 'monitoring_health': 'warning', 'status': 'degraded', 'assessed_at': _dt(22)},
            {'asset_id': 'a3', 'risk_score': 45, 'risk_level': 'medium', 'confidence': 0.8, 'reserve_status': 'warning', 'reserve_coverage_percent': Decimal('98'), 'monitoring_health': 'healthy', 'status': 'completed', 'assessed_at': _dt()},
        ]),
        ("status = 'active'", [{'asset_id': 'a2', 'n': 2}]),
    ])


class _QP(dict):
    def get(self, k, default=None):
        return dict.get(self, k, default)


def test_registry_enriches_risk_fields():
    out = registry.attach_risk_and_filter(_registry_conn(), workspace_id='ws-1', assets=_assets(), query_params=_QP())
    by_id = {a['id']: a for a in out['assets']}
    assert by_id['a1']['risk_score'] == 12 and by_id['a1']['risk_level'] == 'low'
    assert by_id['a2']['risk_level'] == 'critical' and by_id['a2']['active_findings_count'] == 2
    assert by_id['a1']['rwa_asset_type_label'] == 'Tokenized Treasury'


def test_registry_filters_by_risk_level():
    out = registry.attach_risk_and_filter(_registry_conn(), workspace_id='ws-1', assets=_assets(), query_params=_QP(risk_level='critical'))
    assert out['filtered_total'] == 1
    assert out['assets'][0]['id'] == 'a2'


def test_registry_search_and_custodian_filter():
    out = registry.attach_risk_and_filter(_registry_conn(), workspace_id='ws-1', assets=_assets(), query_params=_QP(custodian='BNY'))
    assert {a['id'] for a in out['assets']} == {'a1', 'a3'}
    out2 = registry.attach_risk_and_filter(_registry_conn(), workspace_id='ws-1', assets=_assets(), query_params=_QP(search='real estate'))
    assert [a['id'] for a in out2['assets']] == ['a2']


def test_registry_sort_by_value_desc_and_pagination():
    out = registry.attach_risk_and_filter(_registry_conn(), workspace_id='ws-1', assets=_assets(), query_params=_QP(sort='value', dir='desc', page='1', page_size='2'))
    assert [a['id'] for a in out['assets']] == ['a1', 'a3']  # 900, 500
    assert out['filtered_total'] == 3 and out['page_size'] == 2
    page2 = registry.attach_risk_and_filter(_registry_conn(), workspace_id='ws-1', assets=_assets(), query_params=_QP(sort='value', dir='desc', page='2', page_size='2'))
    assert [a['id'] for a in page2['assets']] == ['a2']


def test_registry_sort_by_risk_desc():
    out = registry.attach_risk_and_filter(_registry_conn(), workspace_id='ws-1', assets=_assets(), query_params=_QP(sort='risk', dir='desc'))
    assert [a['id'] for a in out['assets']] == ['a2', 'a3', 'a1']


def test_registry_no_params_returns_all_enriched():
    out = registry.attach_risk_and_filter(_registry_conn(), workspace_id='ws-1', assets=_assets(), query_params=_QP())
    assert out['total'] == 3 and out['filtered_total'] == 3
    assert len(out['assets']) == 3


# --------------------------------------------------------------------------
# Registry create-field validation + SSRF guard
# --------------------------------------------------------------------------
def test_validate_registry_payload_rejects_bad_type_and_negative_value():
    import pytest
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        registry.validate_registry_payload({'rwa_asset_type': 'not_a_type'})
    with pytest.raises(HTTPException):
        registry.validate_registry_payload({'value_usd': '-5'})


def test_validate_registry_payload_ssrf_blocks_internal_url():
    import pytest
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        registry.validate_registry_payload({'reserve_feed_type': 'api', 'reserve_feed_identifier': 'http://169.254.169.254/latest/meta-data'})
    with pytest.raises(HTTPException):
        registry.validate_registry_payload({'reserve_feed_type': 'api', 'reserve_feed_identifier': 'http://localhost:8000/x'})


def test_validate_registry_payload_accepts_public_https_and_opaque_id():
    ok = registry.validate_registry_payload({'reserve_feed_type': 'api', 'reserve_feed_identifier': 'https://reserves.example.com/attest'})
    assert ok['reserve_feed_type'] == 'api'
    ok2 = registry.validate_registry_payload({'rwa_asset_type': 'stablecoin', 'reserve_feed_type': 'attestation', 'reserve_feed_identifier': 'attestation-feed-123', 'value_usd': '1000000', 'reserve_value_usd': '1280000', 'reserve_verified': True})
    assert ok2['reserve_verified'] is True


def test_validate_registry_payload_token_decimals():
    import pytest
    from fastapi import HTTPException
    ok = registry.validate_registry_payload({'token_decimals': '18'})
    assert ok['token_decimals'] == 18
    ok2 = registry.validate_registry_payload({})
    assert ok2['token_decimals'] is None
    with pytest.raises(HTTPException):
        registry.validate_registry_payload({'token_decimals': '99'})
    with pytest.raises(HTTPException):
        registry.validate_registry_payload({'token_decimals': 'abc'})


def test_registry_marks_wallet_reserve_not_applicable_when_unassessed():
    # A wallet-type asset with no assessment must report reserve status
    # not_applicable (never null/insufficient), and reserve_required False.
    conn = FakeConn(matchers=[('DISTINCT ON (asset_id)', []), ("status = 'active'", [])])
    wallet = [{'id': 'w1', 'name': 'Test Wallet', 'chain_network': 'ethereum-mainnet', 'asset_type': 'wallet',
               'rwa_asset_type': None, 'reserve_feed_type': 'none', 'value_usd': None}]
    out = registry.attach_risk_and_filter(conn, workspace_id='ws-1', assets=wallet, query_params=_QP())
    row = out['assets'][0]
    assert row['reserve_required'] is False
    assert row['reserve_status'] == 'not_applicable'
    assert row['assessment_status'] == 'not_assessed'


def test_registry_reserve_backed_asset_stays_null_until_assessed():
    conn = FakeConn(matchers=[('DISTINCT ON (asset_id)', []), ("status = 'active'", [])])
    backed = [{'id': 's1', 'name': 'Stable', 'chain_network': 'ethereum-mainnet', 'asset_type': 'contract',
               'rwa_asset_type': 'stablecoin', 'reserve_feed_type': 'none', 'value_usd': Decimal('1000000')}]
    out = registry.attach_risk_and_filter(conn, workspace_id='ws-1', assets=backed, query_params=_QP())
    row = out['assets'][0]
    assert row['reserve_required'] is True
    assert row['reserve_status'] is None  # pending assessment, not "not applicable"


def test_health_reconcile_never_overstates():
    # Assessment said healthy, but asset currently has no live telemetry -> warning.
    a = {'monitoring_status': 'live_verified', 'has_monitoring_target': True, 'has_telemetry': False}
    assert registry._reconcile_health('healthy', a) == 'warning'
    # No target -> not_configured regardless of stored value.
    b = {'monitoring_status': 'not_configured', 'has_monitoring_target': False}
    assert registry._reconcile_health('healthy', b) == 'not_configured'


# --------------------------------------------------------------------------
# AI explanation — deterministic grounding + schema + fallback
# --------------------------------------------------------------------------
def test_ai_summary_is_deterministic_by_default_and_grounded():
    facts = {
        'asset_name': 'US Treasury Bond #123', 'risk_score': 80, 'risk_level': 'critical', 'confidence': 0.7,
        'reserve': {'status': 'critical', 'coverage_percent': 82.0, 'required': True, 'evidence_fresh': True},
        'market': {'status': 'normal', 'deviation_30d_percent': 0.5},
        'monitoring': {'health': 'healthy', 'coverage_percent': 100.0, 'missing_controls': [], 'has_target': True},
        'findings': [{'finding_type': 'asset_reserve_shortfall', 'severity': 'critical', 'title': 'Reserve shortfall'}],
        'data_gaps': [], 'assessment_status': 'completed',
    }
    out = ai_explanation.generate_summary(facts)
    assert out['source'] == 'deterministic'
    assert '82.0%' in out['risk_drivers'][0] or '82' in ' '.join(out['risk_drivers'])
    assert out['executive_summary']
    # Every required key present.
    for k in ('executive_summary', 'risk_drivers', 'investigation_steps', 'data_gaps', 'confidence_explanation'):
        assert k in out


def test_ai_summary_schema_rejects_malformed():
    import pytest
    with pytest.raises(ai_explanation.SummaryValidationError):
        ai_explanation.validate_summary_schema({'executive_summary': 'ok', 'risk_drivers': 'not-a-list', 'investigation_steps': [], 'data_gaps': [], 'confidence_explanation': 'c'})


def test_ai_summary_does_not_recommend_moving_funds():
    facts = {'asset_name': 'X', 'risk_score': 90, 'risk_level': 'critical', 'confidence': 0.5,
             'reserve': {'status': 'critical', 'coverage_percent': 50.0, 'required': True, 'evidence_fresh': True},
             'market': {'status': 'normal'}, 'monitoring': {'health': 'healthy', 'missing_controls': [], 'has_target': True},
             'findings': [{'finding_type': 'asset_reserve_shortfall', 'severity': 'critical', 'title': 'Reserve shortfall'}],
             'data_gaps': [], 'assessment_status': 'completed'}
    out = ai_explanation.generate_summary(facts)
    joined = ' '.join(out['investigation_steps']).lower()
    for banned in ('transfer funds', 'move funds', 'withdraw', 'liquidate'):
        assert banned not in joined
