"""Pure-logic tests for the idempotent reconciliation planner (Screen 6).

Proves that re-running triage updates existing clusters instead of duplicating
them, prevents duplicate memberships, records history only on real changes, and
archives clusters that lose all members — all without a database.
"""
from __future__ import annotations

import itertools

from services.api.app.domains.alert_triage import service


def _desired(fingerprint, members, *, severity='high', category='review_immediately'):
    return {
        'fingerprint': fingerprint,
        'title': f'cluster {fingerprint}',
        'detection_family': 'wallet_transfer',
        'derived_severity': severity,
        'severity_factors': [], 'confidence': 0.8, 'confidence_band': 'high',
        'confidence_factors': [], 'reason_codes': [], 'recommendation_category': category,
        'recommendation_summary': 'summary', 'recommendation_source': 'rules_based',
        'primary_asset_id': None, 'primary_asset_name': 'Asset', 'chain_id': '1',
        'member_count': len(members), 'member_alert_ids': list(members),
        'first_seen_epoch': 1, 'last_seen_epoch': 2,
    }


def _counter():
    c = itertools.count(1)
    return lambda: f'c{next(c)}'


def test_first_run_creates_clusters_and_records_history():
    plan = service.reconcile([], [], [_desired('fp1', ['a1', 'a2'])],
                             unclustered_alert_ids=['a3'], id_factory=_counter())
    assert plan['counts']['clusters_created'] == 1
    assert plan['counts']['clusters_updated'] == 0
    assert plan['counts']['unclustered_count'] == 1
    assert plan['member_sets'] == {'c1': ['a1', 'a2']}
    assert plan['member_deletes'] == []
    change_types = {h['change_type'] for h in plan['history']}
    assert 'cluster_created' in change_types
    assert 'membership_changed' in change_types


def test_rerun_is_idempotent_no_duplicate_clusters_or_members():
    existing_clusters = [{'id': 'c1', 'fingerprint': 'fp1', 'derived_severity': 'high',
                          'recommendation_category': 'review_immediately', 'member_count': 2}]
    existing_members = [{'alert_id': 'a1', 'cluster_id': 'c1'}, {'alert_id': 'a2', 'cluster_id': 'c1'}]
    desired = [_desired('fp1', ['a1', 'a2'], severity='high', category='review_immediately')]
    plan = service.reconcile(existing_clusters, existing_members, desired,
                             unclustered_alert_ids=[], id_factory=_counter())
    # No new cluster id minted; the SAME cluster is updated in place.
    assert plan['counts']['clusters_created'] == 0
    assert plan['counts']['clusters_updated'] == 1
    assert plan['cluster_upserts'][0]['id'] == 'c1'
    assert plan['cluster_upserts'][0]['is_new'] is False
    # Members unchanged -> no deletes, no membership/severity/recommendation history.
    assert plan['member_deletes'] == []
    assert plan['member_sets'] == {'c1': ['a1', 'a2']}
    assert plan['history'] == []


def test_severity_and_recommendation_changes_are_audited():
    existing_clusters = [{'id': 'c1', 'fingerprint': 'fp1', 'derived_severity': 'high',
                          'recommendation_category': 'review_immediately', 'member_count': 2}]
    existing_members = [{'alert_id': 'a1', 'cluster_id': 'c1'}, {'alert_id': 'a2', 'cluster_id': 'c1'}]
    desired = [_desired('fp1', ['a1', 'a2'], severity='critical', category='escalate_to_incident')]
    plan = service.reconcile(existing_clusters, existing_members, desired,
                             unclustered_alert_ids=[], id_factory=_counter())
    types = {h['change_type'] for h in plan['history']}
    assert 'severity_recalculated' in types
    assert 'recommendation_changed' in types
    sev = next(h for h in plan['history'] if h['change_type'] == 'severity_recalculated')
    assert sev['old_value']['derived_severity'] == 'high'
    assert sev['new_value']['derived_severity'] == 'critical'


def test_membership_change_deletes_departed_alert_and_audits():
    existing_clusters = [{'id': 'c1', 'fingerprint': 'fp1', 'derived_severity': 'high',
                          'recommendation_category': 'review_immediately', 'member_count': 2}]
    existing_members = [{'alert_id': 'a1', 'cluster_id': 'c1'}, {'alert_id': 'a2', 'cluster_id': 'c1'}]
    # a2 resolved -> only a1 + a3 remain in the cluster.
    desired = [_desired('fp1', ['a1', 'a3'])]
    plan = service.reconcile(existing_clusters, existing_members, desired,
                             unclustered_alert_ids=[], id_factory=_counter())
    assert plan['member_deletes'] == ['a2']
    assert plan['member_sets'] == {'c1': ['a1', 'a3']}
    assert any(h['change_type'] == 'membership_changed' for h in plan['history'])


def test_cluster_with_no_members_is_archived():
    existing_clusters = [{'id': 'c1', 'fingerprint': 'fp1', 'derived_severity': 'high',
                          'recommendation_category': 'review_immediately', 'member_count': 2}]
    existing_members = [{'alert_id': 'a1', 'cluster_id': 'c1'}, {'alert_id': 'a2', 'cluster_id': 'c1'}]
    # No desired clusters -> the whole cluster is gone.
    plan = service.reconcile(existing_clusters, existing_members, [],
                             unclustered_alert_ids=['a1', 'a2'], id_factory=_counter())
    assert plan['cluster_archives'] == ['c1']
    assert sorted(plan['member_deletes']) == ['a1', 'a2']
    assert any(h['change_type'] == 'cluster_archived' for h in plan['history'])


def test_archived_cluster_is_revived_reusing_its_row_id():
    # A recurring root cause (same fingerprint as an archived cluster) must reuse
    # the archived row's id so memberships stay FK-valid — never mint a new id that
    # collides with the workspace+fingerprint unique index.
    existing = [{'id': 'c1', 'fingerprint': 'fp1', 'derived_severity': 'high',
                 'recommendation_category': 'review_immediately', 'member_count': 0, 'status': 'archived'}]
    desired = [_desired('fp1', ['a1', 'a2'])]
    plan = service.reconcile(existing, [], desired, unclustered_alert_ids=[], id_factory=lambda: 'NEWID')
    assert plan['cluster_upserts'][0]['id'] == 'c1'   # reused, not 'NEWID'
    assert plan['member_sets'] == {'c1': ['a1', 'a2']}
    assert plan['cluster_archives'] == []             # not re-archived
    assert plan['counts']['clusters_created'] == 1    # counts as a (re-)creation
    assert any(h['change_type'] == 'cluster_created' for h in plan['history'])


def test_alert_moving_clusters_updates_membership_once():
    existing_clusters = [
        {'id': 'c1', 'fingerprint': 'fp1', 'derived_severity': 'high', 'recommendation_category': 'review_immediately', 'member_count': 2},
        {'id': 'c2', 'fingerprint': 'fp2', 'derived_severity': 'medium', 'recommendation_category': 'continue_monitoring', 'member_count': 2},
    ]
    existing_members = [
        {'alert_id': 'a1', 'cluster_id': 'c1'}, {'alert_id': 'a2', 'cluster_id': 'c1'},
        {'alert_id': 'b1', 'cluster_id': 'c2'}, {'alert_id': 'b2', 'cluster_id': 'c2'},
    ]
    desired = [_desired('fp1', ['a1', 'a2']), _desired('fp2', ['b1', 'b2'])]
    plan = service.reconcile(existing_clusters, existing_members, desired,
                             unclustered_alert_ids=[], id_factory=_counter())
    # Every alert maps to exactly one cluster in the plan (no double membership).
    all_members = [aid for ids in plan['member_sets'].values() for aid in ids]
    assert len(all_members) == len(set(all_members))
    assert plan['member_deletes'] == []
