"""Deterministic threat-detection scoring — severity, confidence, evidence
quality, and the stable cluster key.

Pure unit tests (no DB, no fastapi). Cover the Screen 5 guarantees: severity and
confidence are separate concepts, thin evidence never yields high confidence,
confidence is never 1.0, and the cluster key is stable + sensitive to the right
inputs (so re-processing dedups but distinct patterns don't collide).
"""

from __future__ import annotations

from services.api.app.domains.threat_detection import scoring as s


# --------------------------------------------------------------------------
# Evidence quality
# --------------------------------------------------------------------------
def test_evidence_quality_rank_ordering():
    assert s.evidence_quality_rank('transaction_trace') > s.evidence_quality_rank('decoded_call')
    assert s.evidence_quality_rank('decoded_call') > s.evidence_quality_rank('event_logs')
    assert s.evidence_quality_rank('event_logs') > s.evidence_quality_rank('normalized_telemetry')
    assert s.evidence_quality_rank('normalized_telemetry') > s.evidence_quality_rank('partial_metadata')
    assert s.evidence_quality_rank('nonsense') == 0


def test_best_evidence_quality_picks_strongest():
    assert s.best_evidence_quality(['normalized_telemetry', 'decoded_call', 'partial_metadata']) == 'decoded_call'
    assert s.best_evidence_quality([]) == 'partial_metadata'


# --------------------------------------------------------------------------
# Severity (potential impact)
# --------------------------------------------------------------------------
def test_severity_privileged_action_is_high_by_default():
    result = s.compute_severity(detection_type='privileged_action', privileged=True)
    assert result['level'] == 'high'
    assert result['points'] >= 55


def test_severity_scales_to_critical_with_large_value():
    result = s.compute_severity(detection_type='mint_burn_irregularity', value_usd=2_000_000)
    assert result['level'] == 'critical'
    assert result['points'] >= 80


def test_severity_low_for_minor_single_signal():
    result = s.compute_severity(detection_type='unusual_transfer')
    assert result['level'] == 'low'


def test_severity_amount_deviation_and_persistence_escalate():
    low = s.compute_severity(detection_type='unusual_transfer')
    high = s.compute_severity(
        detection_type='unusual_transfer', amount_deviation=12, actor_count=4, persistence_windows=3
    )
    assert s.severity_rank(high['level']) > s.severity_rank(low['level'])


def test_severity_points_are_bounded():
    result = s.compute_severity(
        detection_type='privileged_action', value_usd=10_000_000, amount_deviation=100,
        privileged=True, affected_asset_count=20, actor_count=50, persistence_windows=10, asset_importance=20,
    )
    assert 0 <= result['points'] <= 100
    assert result['level'] == 'critical'


# --------------------------------------------------------------------------
# Confidence (evidence strength) — separate from severity
# --------------------------------------------------------------------------
def test_confidence_low_for_thin_evidence():
    result = s.compute_confidence(evidence_quality='normalized_telemetry', evidence_count=1, signal_count=1)
    assert result['confidence'] < 0.5


def test_confidence_rises_with_corroboration():
    thin = s.compute_confidence(evidence_quality='normalized_telemetry', evidence_count=1, signal_count=1)
    strong = s.compute_confidence(
        evidence_quality='event_logs', evidence_count=6, signal_count=2, baseline_deviation=10,
        actor_repetition=4, temporal_correlation=True, baseline_samples=12, min_baseline_samples=8,
    )
    assert strong['confidence'] > thin['confidence']
    assert strong['confidence'] >= 0.5


def test_confidence_never_reaches_one():
    result = s.compute_confidence(
        evidence_quality='transaction_trace', evidence_count=100, signal_count=10, baseline_deviation=100,
        actor_repetition=100, temporal_correlation=True, baseline_samples=100, min_baseline_samples=8,
    )
    assert result['confidence'] <= 0.98


def test_confidence_penalizes_deviation_without_baseline():
    with_baseline = s.compute_confidence(
        evidence_quality='normalized_telemetry', evidence_count=3, baseline_deviation=8,
        baseline_samples=12, min_baseline_samples=8,
    )
    learning = s.compute_confidence(
        evidence_quality='normalized_telemetry', evidence_count=3, baseline_deviation=8,
        baseline_samples=2, min_baseline_samples=8,
    )
    assert learning['confidence'] < with_baseline['confidence']


def test_high_severity_can_have_low_confidence():
    """A high-impact detection built on thin evidence must not read as high
    confidence — severity and confidence are independent."""
    severity = s.compute_severity(detection_type='privileged_action', privileged=True)
    confidence = s.compute_confidence(evidence_quality='decoded_call', evidence_count=1, signal_count=1)
    assert severity['level'] == 'high'
    assert confidence['confidence'] < 0.5


# --------------------------------------------------------------------------
# Cluster key + signatures
# --------------------------------------------------------------------------
def test_actor_signature_is_order_independent():
    a = s.actor_signature(['0xAAA', '0xBBB'])
    b = s.actor_signature(['0xbbb', '0xaaa'])
    assert a == b
    assert s.actor_signature([]) == 'noactor'


def test_window_bucket_floors_to_boundary():
    assert s.window_bucket(3661, 3600) == 3600
    assert s.window_bucket(7205, 3600) == 7200
    assert s.window_bucket(None, 3600) == 0


def test_cluster_key_stable_for_same_inputs():
    kwargs = dict(
        workspace_id='ws-1', detection_type='unusual_transfer', chain_id=8453,
        asset_id='asset-1', actor_signature_value='sig', window_bucket_value=1000,
    )
    assert s.cluster_key(**kwargs) == s.cluster_key(**kwargs)


def test_cluster_key_changes_with_type_asset_actor_window():
    base = dict(
        workspace_id='ws-1', detection_type='unusual_transfer', chain_id=8453,
        asset_id='asset-1', actor_signature_value='sig', window_bucket_value=1000,
    )
    key = s.cluster_key(**base)
    assert s.cluster_key(**{**base, 'detection_type': 'coordinated_activity'}) != key
    assert s.cluster_key(**{**base, 'asset_id': 'asset-2'}) != key
    assert s.cluster_key(**{**base, 'actor_signature_value': 'other'}) != key
    assert s.cluster_key(**{**base, 'window_bucket_value': 2000}) != key


def test_cluster_key_isolated_by_workspace():
    base = dict(detection_type='unusual_transfer', asset_id='asset-1', actor_signature_value='sig', window_bucket_value=1000)
    assert s.cluster_key(workspace_id='ws-1', **base) != s.cluster_key(workspace_id='ws-2', **base)
