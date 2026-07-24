"""Asset Risk Assessor service layer — evidence gathering, findings, dedup alerts.

Uses a lightweight fake connection (the repository's DB-test convention) so the
persistence + alert-reconciliation paths are covered without a live Postgres.
"""

from __future__ import annotations

from decimal import Decimal

from services.api.app.domains.asset_risk import scoring, service


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeConn:
    """Matches executed queries on normalized substrings and returns configured
    rows. Records every write so tests can assert on them."""

    def __init__(self, tables_exist=True, matchers=None):
        self.tables_exist = tables_exist
        self.matchers = matchers or []
        self.writes = []  # (normalized_query, params)
        self.committed = False

    def execute(self, query, params=None):
        q = ' '.join(str(query).split())
        ql = q.lower()
        if 'to_regclass' in ql:
            return _Result([{'ok': bool(self.tables_exist)}])
        # user-provided matchers (substring -> rows) take priority
        for needle, rows in self.matchers:
            if needle in q:
                if any(kw in ql for kw in ('insert', 'update', 'delete')):
                    self.writes.append((q, params))
                return _Result(rows)
        if any(kw in ql for kw in ('insert into', 'update ', 'delete ')):
            self.writes.append((q, params))
            return _Result([])
        return _Result([])

    def commit(self):
        self.committed = True

    def writes_matching(self, needle):
        return [(q, p) for (q, p) in self.writes if needle in q]


def _asset_row(**overrides):
    row = {
        'id': 'asset-1', 'workspace_id': 'ws-1', 'name': 'US Treasury Bond #123',
        'created_by_user_id': 'user-1', 'rwa_asset_type': 'stablecoin',
        'reserve_feed_type': 'attestation', 'reserve_value_usd': Decimal('128000000'),
        'reserve_verified_at': None, 'reserve_min_coverage_ratio': Decimal('1.0'),
        'reserve_update_interval_seconds': None, 'circulating_supply': Decimal('100000000000000'),
        'token_decimals': 6, 'reference_price_usd': Decimal('1.00'), 'value_usd': Decimal('100000000'),
        'price_source': 'chainlink', 'oracle_sources': [], 'chainlink_feeds': [],
        'token_contract_address': '0x' + 'a' * 40, 'token_standard': 'erc20',
        'verification_status': 'verified', 'verification_summary': {},
    }
    row.update(overrides)
    return row


def _now():
    from datetime import datetime, timezone
    return datetime(2026, 7, 23, 12, 0, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# gather_inputs
# --------------------------------------------------------------------------
def test_gather_inputs_builds_verified_reserve_and_liability():
    now = _now()
    from datetime import timedelta
    conn = FakeConn(matchers=[
        ('EXISTS(SELECT 1 FROM targets', [{'has_target': True, 'has_system': True, 'has_telemetry': True, 'telemetry_fresh': True}]),
        ('FROM asset_valuation_snapshots', [{'n': 30, 'mean_30d': Decimal('1.00'), 'std_30d': Decimal('0.001'), 'mean_7d': Decimal('1.00')}]),
        ('FROM alerts', [{'n': 0}]),
    ])
    row = _asset_row(reserve_verified_at=now - timedelta(minutes=5))
    cfg = _cfg()
    out = service.gather_inputs(conn, workspace_id='ws-1', asset_row=row, config=cfg, now=now)
    inp = out['inputs']
    assert inp.reserve_required is True
    assert inp.reserve_verified is True
    # liability = 100,000,000 tokens (6 decimals) x $1 = $100,000,000
    assert inp.liability_value_usd == Decimal('100000000.00')
    assert inp.reserve_value_usd == Decimal('128000000')
    assert inp.has_monitoring_target is True
    assert out['reserve_snapshot_to_record'] is not None
    # feed identifier is hashed, never raw
    assert out['reserve_snapshot_to_record']['feed_identifier_hash'] is None  # no identifier set


def test_gather_inputs_missing_supply_falls_back_to_declared_value():
    now = _now()
    from datetime import timedelta
    conn = FakeConn(matchers=[
        ('EXISTS(SELECT 1 FROM targets', [{'has_target': True, 'has_system': True, 'has_telemetry': False, 'telemetry_fresh': False}]),
        ('FROM asset_valuation_snapshots', [{'n': 0, 'mean_30d': None, 'std_30d': None, 'mean_7d': None}]),
        ('FROM alerts', [{'n': 0}]),
    ])
    row = _asset_row(circulating_supply=None, reserve_verified_at=now - timedelta(minutes=1))
    out = service.gather_inputs(conn, workspace_id='ws-1', asset_row=row, config=_cfg(), now=now)
    assert out['inputs'].liability_value_usd == Decimal('100000000')
    assert any('declared value' in g.lower() for g in out['data_gaps'])


def test_gather_inputs_unconfigured_reserve_is_insufficient():
    now = _now()
    conn = FakeConn(matchers=[
        ('EXISTS(SELECT 1 FROM targets', [{'has_target': False, 'has_system': False, 'has_telemetry': False, 'telemetry_fresh': False}]),
        ('FROM asset_valuation_snapshots', [{'n': 0, 'mean_30d': None, 'std_30d': None, 'mean_7d': None}]),
        ('FROM alerts', [{'n': 0}]),
    ])
    row = _asset_row(reserve_feed_type='none', reserve_value_usd=None)
    out = service.gather_inputs(conn, workspace_id='ws-1', asset_row=row, config=_cfg(), now=now)
    result = scoring.compute_asset_risk(out['inputs'])
    assert result.reserve.status == scoring.RESERVE_INSUFFICIENT
    assert result.risk_score >= 40


# --------------------------------------------------------------------------
# derive_findings
# --------------------------------------------------------------------------
def test_derive_findings_reserve_shortfall_is_critical():
    inp = scoring.AssetRiskInputs(
        reserve_required=True, reserve_feed_configured=True, reserve_verified=True,
        reserve_value_usd=Decimal('80'), liability_value_usd=Decimal('100'), reserve_age_seconds=10,
        price_source_configured=True, price_usd=Decimal('1'), baseline_30d=Decimal('1'), price_sample_count=30, price_age_seconds=10,
        monitoring_controls=[('target', True, True)], has_monitoring_target=True,
    )
    result = scoring.compute_asset_risk(inp)
    findings = service.derive_findings(result, {'reserve': {'feed_type': 'attestation'}})
    types = {f['finding_type']: f['severity'] for f in findings}
    assert types.get('asset_reserve_shortfall') == 'critical'


def test_derive_findings_monitoring_gap():
    inp = scoring.AssetRiskInputs(
        reserve_required=False, price_source_configured=True, price_usd=Decimal('1'),
        baseline_30d=Decimal('1'), price_sample_count=30, price_age_seconds=10,
        monitoring_controls=[('target', True, False), ('recent_telemetry', True, False)], has_monitoring_target=False,
    )
    result = scoring.compute_asset_risk(inp)
    findings = service.derive_findings(result, {})
    assert any(f['finding_type'] == 'asset_monitoring_gap' for f in findings)


# --------------------------------------------------------------------------
# Fingerprints (dedup stability)
# --------------------------------------------------------------------------
def test_fingerprint_and_alert_id_are_stable_and_scoped():
    fp1 = service._fingerprint('ws-1', 'asset-1', 'asset_reserve_shortfall')
    fp2 = service._fingerprint('ws-1', 'asset-1', 'asset_reserve_shortfall')
    fp_other = service._fingerprint('ws-2', 'asset-1', 'asset_reserve_shortfall')
    assert fp1 == fp2 and fp1 != fp_other
    aid1 = service._deterministic_alert_id('ws-1', 'asset-1', 'asset_reserve_shortfall')
    aid2 = service._deterministic_alert_id('ws-1', 'asset-1', 'asset_reserve_shortfall')
    assert aid1 == aid2  # same finding -> same alert id -> ON CONFLICT dedup


# --------------------------------------------------------------------------
# reconcile_findings — create, dedup, resolve
# --------------------------------------------------------------------------
def test_reconcile_findings_creates_alert_and_finding():
    now = _now()
    conn = FakeConn(matchers=[
        ('SELECT id, occurrence_count FROM alerts', []),  # no existing alert
        ('SELECT id, finding_type, alert_id FROM asset_risk_findings', []),  # nothing to resolve
    ])
    findings = [{'finding_type': 'asset_reserve_shortfall', 'severity': 'critical', 'title': 'Reserve shortfall', 'detail': 'x', 'evidence': {}}]
    out = service.reconcile_findings(conn, workspace_id='ws-1', asset_id='asset-1', asset_name='A',
                                     assessment_id='assess-1', user_id='user-1', findings=findings, now=now)
    assert out['alerts_created'] == 1
    assert conn.writes_matching('INSERT INTO alerts')
    assert conn.writes_matching('INSERT INTO asset_risk_findings')


def test_reconcile_findings_resolves_cleared_finding_and_alert():
    now = _now()
    # No active findings now, but a previously-active finding exists -> resolve it + its alert.
    conn = FakeConn(matchers=[
        ('SELECT id, finding_type, alert_id FROM asset_risk_findings', [{'id': 'f-old', 'finding_type': 'asset_price_deviation', 'alert_id': 'alert-old'}]),
    ])
    out = service.reconcile_findings(conn, workspace_id='ws-1', asset_id='asset-1', asset_name='A',
                                     assessment_id='assess-2', user_id='user-1', findings=[], now=now)
    assert out['alerts_resolved'] == 1
    resolves = conn.writes_matching("UPDATE asset_risk_findings SET status = 'resolved'")
    assert resolves
    assert conn.writes_matching("UPDATE alerts SET status = 'resolved'")


def test_low_severity_finding_does_not_raise_alert():
    now = _now()
    conn = FakeConn(matchers=[('SELECT id, finding_type, alert_id FROM asset_risk_findings', [])])
    findings = [{'finding_type': 'asset_contract_exposure', 'severity': 'low', 'title': 'x', 'detail': '', 'evidence': {}}]
    out = service.reconcile_findings(conn, workspace_id='ws-1', asset_id='asset-1', asset_name='A',
                                     assessment_id='a', user_id='user-1', findings=findings, now=now)
    assert out['alerts_created'] == 0
    assert not conn.writes_matching('INSERT INTO alerts')
    # the finding itself is still recorded
    assert conn.writes_matching('INSERT INTO asset_risk_findings')


# --------------------------------------------------------------------------
# assess_asset — full flow, persists assessment
# --------------------------------------------------------------------------
def test_assess_asset_persists_snapshot_and_returns_score():
    now = _now()
    from datetime import timedelta
    conn = FakeConn(matchers=[
        ('EXISTS(SELECT 1 FROM targets', [{'has_target': True, 'has_system': True, 'has_telemetry': True, 'telemetry_fresh': True}]),
        ('FROM asset_valuation_snapshots', [{'n': 30, 'mean_30d': Decimal('1.00'), 'std_30d': Decimal('0.001'), 'mean_7d': Decimal('1.00')}]),
        ('FROM alerts', [{'n': 0}]),
        ('SELECT id, occurrence_count FROM alerts', []),
        ('SELECT id, finding_type, alert_id FROM asset_risk_findings', []),
    ])
    row = _asset_row(reserve_verified_at=now - timedelta(minutes=5))
    out = service.assess_asset(conn, workspace_id='ws-1', asset_row=row, config=_cfg(), trigger_source='manual', now=now)
    assert out['risk_level'] == 'low'
    assert out['reserve_status'] == 'healthy'
    assert out['status'] == 'completed'
    assert conn.writes_matching('INSERT INTO asset_risk_assessments')
    # a healthy asset raises no alert
    assert out['alerts_created'] == 0


def test_assess_wallet_reserve_is_not_applicable_and_not_diluted():
    # A plain wallet (no reserve, no token contract, no price source) must be
    # assessed on its monitoring coverage — reserve is "not applicable", and the
    # score is not diluted to "low" by unscored reserve/market dimensions.
    now = _now()
    conn = FakeConn(matchers=[
        ('EXISTS(SELECT 1 FROM targets', [{'has_target': True, 'has_system': True, 'has_telemetry': False, 'telemetry_fresh': False}]),
        ('FROM asset_valuation_snapshots', [{'n': 0, 'mean_30d': None, 'std_30d': None, 'mean_7d': None}]),
        ('FROM alerts', [{'n': 0}]),
        ('SELECT id, occurrence_count FROM alerts', []),
        ('SELECT id, finding_type, alert_id FROM asset_risk_findings', []),
    ])
    row = _asset_row(
        rwa_asset_type='other', reserve_feed_type='none', reserve_value_usd=None,
        token_contract_address=None, price_source='', reference_price_usd=None,
        circulating_supply=None, value_usd=None, verification_status='unknown',
    )
    gathered = service.gather_inputs(conn, workspace_id='ws-1', asset_row=row, config=_cfg(), now=now)
    assert gathered['inputs'].contract_applicable is False
    out = service.assess_asset(conn, workspace_id='ws-1', asset_row=row, config=_cfg(), trigger_source='manual', now=now)
    assert out['reserve_status'] == scoring.RESERVE_NOT_APPLICABLE
    # Not diluted to low: half-covered monitoring keeps the score at medium+.
    assert out['risk_level'] in ('medium', 'high', 'critical')
    assert conn.writes_matching('INSERT INTO asset_risk_assessments')


def _cfg():
    return {
        'baseline_days': 30, 'min_baseline_samples': 5, 'reserve_stale_seconds': 86400,
        'price_stale_seconds': 3600, 'deviation_medium_percent': Decimal('5'),
        'deviation_high_percent': Decimal('15'), 'zscore_high': Decimal('3'),
        'oracle_disagreement_percent': Decimal('2'), 'default_min_coverage_ratio': Decimal('1.0'),
        'over_collateralization_ratio': Decimal('2.0'), 'batch_size': 25, 'job_lease_seconds': 300,
        'max_attempts': 3, 'assessment_stale_seconds': 3600,
    }
