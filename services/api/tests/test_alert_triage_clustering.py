"""Pure-logic tests for the Alert Triage Agent clustering policy (Screen 6).

No DB / network / AI: exercises the deterministic fingerprint, severity policy,
confidence policy, recommendation catalogue, and reason codes directly.
"""
from __future__ import annotations

from services.api.app.domains.alert_triage import clustering as cl
from services.api.app.domains.alert_triage import config as cfg


def _alert(**over):
    base = {
        'id': 'a1', 'workspace_id': 'ws1', 'severity': 'high', 'status': 'open',
        'title': 'Monitored wallet transfer', 'alert_type': 'wallet_transfer',
        'detection_type': 'monitored_wallet_transfer', 'chain_id': '8453',
        'primary_asset_id': 'asset1', 'primary_asset_name': 'US Treasury Bond',
        'asset_risk_tier': 'high', 'target_id': 'tgt1', 'occurrence_count': 1,
        'created_at': '2026-07-29T00:00:00+00:00',
        'payload': {'rule_key': 'smoke_wallet_transfer', 'from_address': '0xAAA', 'tx_hash': '0x1', 'confidence': '0.9'},
    }
    base.update(over)
    return base


def _norm(**over):
    return cl.normalize_alert(_alert(**over))


# ── Fingerprint ────────────────────────────────────────────────────────────
def test_fingerprint_is_stable_across_repeated_runs():
    a = _alert()
    assert cl.cluster_fingerprint(cl.normalize_alert(a)) == cl.cluster_fingerprint(cl.normalize_alert(dict(a)))


def test_fingerprint_ignores_mutable_display_and_per_event_fields():
    # Different title, tx_hash, timestamp, occurrence_count => SAME fingerprint
    # (those are not part of the root-cause identity).
    base = _norm()
    other = _norm(title='A totally different title', created_at='2026-08-01T12:00:00+00:00',
                  payload={'rule_key': 'smoke_wallet_transfer', 'from_address': '0xAAA', 'tx_hash': '0xZZZ', 'confidence': '0.1'})
    assert cl.cluster_fingerprint(base) == cl.cluster_fingerprint(other)


def test_fingerprint_is_workspace_scoped_no_cross_tenant_collision():
    a = cl.cluster_fingerprint(_norm(workspace_id='ws1'))
    b = cl.cluster_fingerprint(_norm(workspace_id='ws2'))
    assert a is not None and b is not None and a != b


def test_fingerprint_differs_by_asset_and_family():
    base = cl.cluster_fingerprint(_norm())
    other_asset = cl.cluster_fingerprint(_norm(primary_asset_id='asset2'))
    other_family = cl.cluster_fingerprint(_norm(detection_type='oracle_price_deviation', payload={'rule_key': 'oracle_dev'}))
    assert base != other_asset
    assert base != other_family


def test_alert_without_stable_anchor_is_unclusterable():
    n = _norm(primary_asset_id=None, target_id=None, payload={'rule_key': 'smoke_wallet_transfer'})
    assert cl.cluster_anchor(n) is None
    assert cl.cluster_fingerprint(n) is None


# ── Grouping ───────────────────────────────────────────────────────────────
def test_related_alerts_cluster_and_unrelated_stay_separate():
    a1 = _norm(id='a1', payload={'rule_key': 'smoke_wallet_transfer', 'from_address': '0xAAA', 'tx_hash': '0x1'})
    a2 = _norm(id='a2', payload={'rule_key': 'smoke_wallet_transfer', 'from_address': '0xAAA', 'tx_hash': '0x2'})
    unrelated = _norm(id='a3', primary_asset_id='asset2', primary_asset_name='Stablecoin X',
                      detection_type='oracle_price_deviation', chain_id='1', payload={'rule_key': 'oracle_dev'})
    res = cl.build_clusters([a1, a2, unrelated])
    assert len(res['clusters']) == 1
    assert res['clusters'][0]['member_count'] == 2
    assert res['unclustered_alert_ids'] == ['a3']


def test_singleton_is_unclustered_not_a_cluster():
    res = cl.build_clusters([_norm(id='solo')])
    assert res['clusters'] == []
    assert res['unclustered_alert_ids'] == ['solo']


def test_duplicate_alert_id_never_appears_twice():
    a1 = _norm(id='dup')
    res = cl.build_clusters([a1, dict(a1)])  # same id twice
    # Only counted once -> singleton -> unclustered, never a 2-member cluster.
    assert res['clusters'] == []
    assert res['unclustered_alert_ids'] == ['dup']


def test_empty_alert_set():
    res = cl.build_clusters([])
    assert res == {'clusters': [], 'unclustered_alert_ids': []}


# ── Severity policy ────────────────────────────────────────────────────────
def test_severity_floor_is_highest_member_severity():
    members = [_norm(id='m1', severity='medium'), _norm(id='m2', severity='high')]
    sev, factors = cl.derive_cluster_severity(members)
    assert cfg.SEVERITY_RANK[sev] >= cfg.SEVERITY_RANK['high']
    assert any(f['factor'] == 'highest_member_severity' for f in factors)


def test_asset_criticality_bumps_severity():
    members = [_norm(id='m1', severity='low', asset_risk_tier='critical'),
               _norm(id='m2', severity='low', asset_risk_tier='critical')]
    sev, factors = cl.derive_cluster_severity(members)
    assert sev == 'medium'  # low floor + critical asset -> +1
    assert any(f['factor'] == 'asset_criticality' for f in factors)


def test_privileged_family_floors_at_high():
    members = [
        _norm(id='m1', severity='low', asset_risk_tier='low', detection_type='ownership_transferred',
              payload={'rule_key': 'privileged_action'}),
        _norm(id='m2', severity='low', asset_risk_tier='low', detection_type='ownership_transferred',
              payload={'rule_key': 'privileged_action'}),
    ]
    assert members[0]['detection_family'] in cfg.PRIVILEGED_FAMILIES
    sev, _ = cl.derive_cluster_severity(members)
    assert cfg.SEVERITY_RANK[sev] >= cfg.SEVERITY_RANK['high']


def test_severity_is_bounded_to_critical():
    members = [_norm(id=f'm{i}', severity='critical', asset_risk_tier='critical') for i in range(6)]
    sev, _ = cl.derive_cluster_severity(members)
    assert sev == 'critical'  # never overflows past the canonical top


def test_recurrence_bumps_severity():
    members = [_norm(id=f'm{i}', severity='low', asset_risk_tier='low') for i in range(5)]
    sev, factors = cl.derive_cluster_severity(members)
    assert cfg.SEVERITY_RANK[sev] >= cfg.SEVERITY_RANK['medium']
    assert any(f['factor'] == 'recurrence_frequency' for f in factors)


# ── Confidence policy ──────────────────────────────────────────────────────
def test_confidence_is_reproducible_and_bounded():
    members = [_norm(id='m1', payload={'from_address': '0xAAA', 'tx_hash': '0x1', 'confidence': '0.9'}),
               _norm(id='m2', payload={'from_address': '0xAAA', 'tx_hash': '0x2', 'confidence': '0.8'})]
    v1, band1, factors = cl.compute_confidence(members)
    v2, band2, _ = cl.compute_confidence(members)
    assert v1 == v2 and band1 == band2       # reproducible, not random
    assert 0.0 <= v1 <= 1.0
    assert band1 in ('low', 'medium', 'high')
    assert any(f['factor'] == 'evidence_completeness' for f in factors)


def test_confidence_lower_when_evidence_incomplete():
    strong = [_norm(id='m1', payload={'from_address': '0xAAA', 'tx_hash': '0x1'}),
              _norm(id='m2', payload={'from_address': '0xAAA', 'tx_hash': '0x2'})]
    weak = [_norm(id='m1', payload={'from_address': '0xAAA'}),
            _norm(id='m2', payload={'from_address': '0xBBB'})]
    vs, _, _ = cl.compute_confidence(strong)
    vw, _, _ = cl.compute_confidence(weak)
    assert vs > vw


def test_confidence_none_for_empty():
    v, band, _ = cl.compute_confidence([])
    assert v is None and band is None


# ── Recommendation catalogue ───────────────────────────────────────────────
def test_recommendation_categories_are_evidence_routed():
    oracle = [_norm(id='o1', detection_type='oracle_price_deviation', payload={'rule_key': 'oracle_dev'})]
    assert cl.recommendation_category(family='oracle_deviation', severity='high', members=oracle) == 'validate_oracle_source'

    reserve_members = [_norm(id='r1', detection_type='reserve_shortfall', payload={'rule_key': 'reserve'})]
    assert cl.recommendation_category(family='reserve_discrepancy', severity='high', members=reserve_members) == 'check_reserve_discrepancy'

    priv = [_norm(id='p1', payload={'rule_key': 'privileged_action'})]
    assert cl.recommendation_category(family='privileged_action', severity='critical', members=priv) == 'escalate_to_incident'

    crit = [_norm(id='c1', severity='critical')]
    assert cl.recommendation_category(family='wallet_transfer', severity='critical', members=crit) == 'review_immediately'


def test_linked_incident_recommends_continue_monitoring():
    members = [_norm(id='m1', incident_id='inc-1'), _norm(id='m2')]
    assert cl.recommendation_category(family='wallet_transfer', severity='critical', members=members) == 'continue_monitoring'


def test_reason_codes_are_derived_from_real_fields_only():
    members = [_norm(id='m1', payload={'from_address': '0xAAA', 'tx_hash': '0x1'}),
               _norm(id='m2', payload={'from_address': '0xAAA', 'tx_hash': '0x2'})]
    codes = cl.reason_codes(members)
    assert 'repeated_detector_signature' in codes
    assert 'same_asset_and_contract' in codes
    assert 'common_actor' in codes
    assert 'evidence_completeness' in codes
    # A single member with no shared actor => no 'common_actor'.
    solo = cl.reason_codes([_norm(id='m1', payload={})])
    assert 'common_actor' not in solo


def test_full_cluster_assembly_is_grounded():
    a1 = _norm(id='a1', severity='high', asset_risk_tier='critical', payload={'from_address': '0xAAA', 'tx_hash': '0x1', 'rule_key': 'smoke_wallet_transfer'})
    a2 = _norm(id='a2', severity='medium', asset_risk_tier='critical', payload={'from_address': '0xAAA', 'tx_hash': '0x2', 'rule_key': 'smoke_wallet_transfer'})
    res = cl.build_clusters([a1, a2])
    c = res['clusters'][0]
    assert c['derived_severity'] == 'critical'
    assert c['primary_asset_name'] == 'US Treasury Bond'
    assert c['confidence'] is not None
    assert c['recommendation_category'] in cfg.RECOMMENDATION_LABELS
    assert c['member_alert_ids'] == ['a1', 'a2']
    assert isinstance(c['recommendation_summary'], str) and c['recommendation_summary']
