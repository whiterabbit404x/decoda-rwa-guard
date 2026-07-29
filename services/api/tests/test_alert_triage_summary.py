"""Read-model tests for the Screen 6 composed summary (Active Alerts).

Proves that the severity tab totals and the paginated rows come from the SAME
workspace-scoped active population (they can never disagree), that pagination is
accurate, that filters scope the counts, and that cluster membership + the
truthful "Unassigned asset" fallback are surfaced.
"""
from __future__ import annotations

from contextlib import contextmanager

from services.api.app import pilot
from services.api.app.domains.alert_triage import summary


class FakeResult:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


def _alert_row(**over):
    base = {
        'id': 'a1', 'workspace_id': 'ws1', 'alert_type': 'wallet_transfer',
        'title': 'Monitored wallet transfer', 'severity': 'high', 'status': 'open',
        'module_key': None, 'target_id': 'tgt1', 'detection_id': 'det1', 'incident_id': None,
        'occurrence_count': 1, 'created_at': '2026-07-29T00:00:00+00:00', 'opened_at': None,
        'payload': {'from_address': '0xAAA', 'tx_hash': '0x1'}, 'findings': None,
        'tx_hash': '0x1', 'from_address': '0xAAA', 'to_address': '0xBBB', 'contract_address': None,
        'chain_id': '8453', 'confidence': '0.9', 'detection_type': 'monitored_wallet_transfer',
        'detector_kind': None, 'primary_asset_id': 'asset1', 'primary_asset_name': 'US Treasury Bond',
        'asset_risk_tier': 'high',
    }
    base.update(over)
    return base


class SummaryFakeConn:
    def __init__(self, *, active_alerts, membership_rows=None, clusters=None,
                 last_run=None, last_success=None, running=None, schema_ok=True):
        self.active_alerts = active_alerts
        self.membership_rows = membership_rows or []
        self.clusters = clusters or []
        self.last_run = last_run
        self.last_success = last_success
        self.running = running
        self.schema_ok = schema_ok

    def execute(self, query, params=None):
        n = ' '.join(str(query).split())
        if 'to_regclass' in n:
            return FakeResult(row={'ok': self.schema_ok})
        if 'FROM alerts a' in n and 'LEFT JOIN targets' in n:
            return FakeResult(rows=[dict(r) for r in self.active_alerts])
        if 'FROM alert_cluster_members m' in n and 'JOIN alert_clusters c' in n:
            return FakeResult(rows=[dict(r) for r in self.membership_rows])
        if "status IN ('completed', 'failed')" in n:
            return FakeResult(row=self.last_run)
        if n.count("status = 'completed'") and 'FROM alert_triage_runs' in n:
            return FakeResult(row=self.last_success)
        if "status = 'running'" in n and 'FROM alert_triage_runs' in n:
            return FakeResult(row=self.running)
        if 'FROM alert_clusters' in n and "status = 'active'" in n:
            return FakeResult(rows=[dict(r) for r in self.clusters])
        return FakeResult()

    def commit(self):
        pass

    def rollback(self):
        pass


class _Req:
    def __init__(self):
        self.headers = {}


@contextmanager
def _fake_pg(conn):
    yield conn


def _bootstrap(monkeypatch, conn):
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda c: None)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda c, r: {'id': 'u1'})
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda c, uid, wid: {'workspace_id': 'ws1'})


def _population():
    # 2 wallet-transfer alerts (normalized to critical) + 1 medium oracle + 1 low.
    return [
        _alert_row(id='a1', payload={'rule_key': 'smoke_wallet_transfer', 'from_address': '0xAAA', 'tx_hash': '0x1'}),
        _alert_row(id='a2', payload={'rule_key': 'smoke_wallet_transfer', 'from_address': '0xAAA', 'tx_hash': '0x2'}),
        _alert_row(id='a3', severity='medium', alert_type='oracle', detection_type='oracle_price_deviation',
                   primary_asset_id='asset2', primary_asset_name='Stablecoin X', payload={}),
        _alert_row(id='a4', severity='low', alert_type='anomaly', detection_type='behavioral_anomaly',
                   primary_asset_id=None, primary_asset_name=None, target_id=None, payload={}),
    ]


def test_severity_totals_match_the_returned_population(monkeypatch):
    conn = SummaryFakeConn(active_alerts=_population())
    _bootstrap(monkeypatch, conn)
    out = summary.build_screen6_summary(_Req())
    totals = out['severity_totals']
    # Per-severity totals sum to the "all" total (single consistent population).
    assert totals['all'] == 4
    assert totals['critical'] + totals['high'] + totals['medium'] + totals['low'] == totals['all']
    # Wallet-transfer alerts normalize to critical (canonical view), so 2 critical.
    assert totals['critical'] == 2
    assert totals['medium'] == 1
    assert totals['low'] == 1
    assert out['active_alert_count'] == 4


def test_severity_tab_filters_rows_but_counts_stay_scoped(monkeypatch):
    conn = SummaryFakeConn(active_alerts=_population())
    _bootstrap(monkeypatch, conn)
    out = summary.build_screen6_summary(_Req(), severity='critical')
    # Rows are only the critical ones...
    assert all(a['severity'] == 'critical' for a in out['alerts'])
    assert out['pagination']['total'] == 2
    # ...but the tab badges still report every severity's count.
    assert out['severity_totals']['all'] == 4
    assert out['severity_totals']['critical'] == 2


def test_pagination_range_is_accurate(monkeypatch):
    conn = SummaryFakeConn(active_alerts=_population())
    _bootstrap(monkeypatch, conn)
    out = summary.build_screen6_summary(_Req(), limit=2, offset=0)
    assert len(out['alerts']) == 2
    assert out['pagination']['total'] == 4
    assert out['pagination']['showing_from'] == 1
    assert out['pagination']['showing_to'] == 2
    assert out['pagination']['has_more'] is True
    # Never "Showing 1–5 of 0".
    empty = SummaryFakeConn(active_alerts=[])
    _bootstrap(monkeypatch, empty)
    out2 = summary.build_screen6_summary(_Req())
    assert out2['pagination']['total'] == 0
    assert out2['pagination']['showing_from'] == 0


def test_cluster_membership_and_unassigned_asset_fallback(monkeypatch):
    membership = [{'alert_id': 'a1', 'cluster_id': 'c1', 'title': 'Wallet Transfer — Bond',
                   'derived_severity': 'critical', 'member_count': 2}]
    conn = SummaryFakeConn(active_alerts=_population(), membership_rows=membership)
    _bootstrap(monkeypatch, conn)
    out = summary.build_screen6_summary(_Req())
    by_id = {a['id']: a for a in out['alerts']}
    assert by_id['a1']['cluster'] is not None
    assert by_id['a1']['cluster']['id'] == 'c1'
    assert by_id['a3']['cluster'] is None  # unclustered
    # Missing asset relationship -> truthful fallback, never a made-up asset.
    assert by_id['a4']['asset_name'] == 'Unassigned asset'
    assert by_id['a4']['asset_known'] is False


def test_triage_state_reports_top_recommendation_and_mode(monkeypatch):
    clusters = [{
        'id': 'c1', 'title': 'Wallet Transfer — Bond', 'derived_severity': 'critical',
        'confidence': 0.9, 'confidence_band': 'high', 'confidence_factors': [], 'severity_factors': [],
        'reason_codes': ['same_asset_and_contract'], 'recommendation_category': 'review_immediately',
        'recommendation_summary': 'Review immediately.', 'recommendation_source': 'rules_based',
        'primary_asset_id': None, 'primary_asset_name': 'US Treasury Bond', 'detection_family': 'wallet_transfer',
        'chain_id': '8453', 'member_count': 2,
    }]
    last_success = {'id': 'r1', 'mode': 'rules_based', 'clusters_total': 1, 'unclustered_count': 2,
                    'alerts_considered': 4, 'ai_enriched_count': 0, 'completed_at': '2026-07-29T01:00:00+00:00'}
    last_run = {'id': 'r1', 'status': 'completed', 'mode': 'rules_based', 'alerts_considered': 4,
                'clusters_total': 1, 'clusters_created': 1, 'unclustered_count': 2, 'ai_enriched_count': 0,
                'error_code': None, 'started_at': None, 'completed_at': '2026-07-29T01:00:00+00:00'}
    conn = SummaryFakeConn(active_alerts=_population(), clusters=clusters, last_run=last_run, last_success=last_success)
    _bootstrap(monkeypatch, conn)
    out = summary.build_screen6_summary(_Req())
    triage = out['triage']
    assert triage['schema_ready'] is True
    assert triage['mode'] == 'rules_based'
    assert triage['cluster_count'] == 1
    assert triage['unclustered_count'] == 2
    assert triage['top_recommendation']['id'] == 'c1'
    assert triage['top_recommendation']['recommendation_label'] == 'Review immediately'
    assert triage['state'] == 'ready'


def test_triage_state_not_run_when_no_successful_run(monkeypatch):
    conn = SummaryFakeConn(active_alerts=_population())
    _bootstrap(monkeypatch, conn)
    out = summary.build_screen6_summary(_Req())
    assert out['triage']['state'] == 'not_run'
    assert out['triage']['top_recommendation'] is None
    assert out['triage']['unclustered_count'] is None  # not calculated yet


def test_schema_provisioning_degrades_triage_but_still_lists_alerts(monkeypatch):
    conn = SummaryFakeConn(active_alerts=_population(), schema_ok=False)
    _bootstrap(monkeypatch, conn)
    out = summary.build_screen6_summary(_Req())
    # Core alert listing works without the triage schema...
    assert out['severity_totals']['all'] == 4
    # ...and triage is explicitly provisioning, not silently "healthy".
    assert out['triage']['schema_ready'] is False
    assert out['triage']['state'] == 'provisioning'
