"""Deterministic threat detectors over normalized telemetry.

Pure unit tests (no DB). Verify the SUPPORTED detectors fire on real evidence and,
critically, that:
  * a normal transfer never becomes a detection,
  * unsupported detectors (reentrancy / flash-loan / oracle) are never produced
    from transfer telemetry,
  * simulator/replay evidence downgrades a cluster's evidence_source.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from services.api.app.domains.threat_detection import config as tdc
from services.api.app.domains.threat_detection import detectors as d
from services.api.app.domains.threat_detection.detectors import AssetBaseline, TelemetryEvent

BASE = datetime(2026, 7, 25, 12, 0, 0, tzinfo=timezone.utc)


def _ev(idx=0, *, event_type='wallet_transfer_detected', asset_id='asset-1', frm='0xsource', to='0xdest',
        amount=None, source='live', tx=None, offset_seconds=0, chain_id=8453, contract='0xtoken'):
    return TelemetryEvent(
        id=f'tel-{idx}',
        workspace_id='ws-1',
        event_type=event_type,
        observed_at=BASE + timedelta(seconds=offset_seconds),
        evidence_source=source,
        asset_id=asset_id,
        chain_id=chain_id,
        tx_hash=tx or f'0xhash{idx}',
        from_address=frm,
        to_address=to,
        amount=Decimal(str(amount)) if amount is not None else None,
        contract_address=contract,
    )


def _cfg():
    return tdc.engine_config()


# --------------------------------------------------------------------------
# Normalization
# --------------------------------------------------------------------------
def test_normalize_event_extracts_payload_fields():
    row = {
        'id': 'tel-1', 'workspace_id': 'ws-1', 'asset_id': 'asset-1', 'event_type': 'wallet_transfer_detected',
        'observed_at': BASE, 'evidence_source': 'live', 'chain_id': 8453,
        'payload_json': {'tx_hash': '0xABC', 'from': '0xFrom', 'to': '0xTo', 'value': '1000', 'block_number': 42},
    }
    ev = d.normalize_event(row)
    assert ev.tx_hash == '0xabc'
    assert ev.from_address == '0xfrom'
    assert ev.to_address == '0xto'
    assert ev.amount == Decimal('1000')
    assert ev.block_number == 42


# --------------------------------------------------------------------------
# A normal transfer is not a threat
# --------------------------------------------------------------------------
def test_single_normal_transfer_produces_no_detection():
    baselines = {'asset-1': AssetBaseline(transfer_mean=Decimal('100'), transfer_samples=20)}
    events = [_ev(1, amount=110)]  # ~baseline, no fan-out
    candidates = d.extract_candidates(events, baselines=baselines, config=_cfg())
    assert candidates == []


# --------------------------------------------------------------------------
# Unusual fund movement
# --------------------------------------------------------------------------
def test_unusual_transfer_flags_large_amount():
    baselines = {'asset-1': AssetBaseline(transfer_mean=Decimal('100'), transfer_samples=20)}
    events = [_ev(1, amount=1500)]  # 15x baseline
    candidates = d.detect_unusual_transfers(events, baselines=baselines, config=_cfg())
    assert len(candidates) == 1
    c = candidates[0]
    assert c.detection_type == 'unusual_transfer'
    assert c.amount_deviation and c.amount_deviation >= 10
    assert c.evidence_quality == 'normalized_telemetry'
    assert c.evidence  # grounded in telemetry


def test_unusual_transfer_needs_enough_baseline_history():
    baselines = {'asset-1': AssetBaseline(transfer_mean=Decimal('100'), transfer_samples=2)}
    events = [_ev(1, amount=1500)]
    candidates = d.detect_unusual_transfers(events, baselines=baselines, config=_cfg())
    # Too few baseline samples -> no amount-deviation flag (baseline learning).
    assert candidates == []


def test_rapid_fan_out_flagged_without_baseline():
    events = [_ev(i, to=f'0xdest{i}', amount=10, offset_seconds=i * 30) for i in range(5)]
    candidates = d.detect_unusual_transfers(events, baselines={}, config=_cfg())
    assert len(candidates) == 1
    assert candidates[0].detection_type == 'unusual_transfer'
    assert candidates[0].temporal_correlation is True


# --------------------------------------------------------------------------
# Coordinated low-and-slow
# --------------------------------------------------------------------------
def test_coordinated_low_and_slow_cluster():
    actors = ['0xa', '0xa', '0xb', '0xb', '0xc']
    events = [_ev(i, frm=actors[i], to='0xpool', amount=5, offset_seconds=i * 60) for i in range(5)]
    candidates = d.detect_coordinated_activity(events, config=_cfg())
    assert len(candidates) == 1
    c = candidates[0]
    assert c.detection_type == 'coordinated_activity'
    assert c.actor_count >= 2
    assert 'not attributed to one controller' in c.explanation.lower() or 'not one controller' in c.explanation.lower() or 'coordinated pattern' in c.explanation.lower()


def test_coordinated_needs_min_events_and_actors():
    events = [_ev(i, frm=f'0xa{i}', to='0xpool', amount=5) for i in range(2)]
    candidates = d.detect_coordinated_activity(events, config=_cfg())
    assert candidates == []


# --------------------------------------------------------------------------
# Mint / burn irregularity
# --------------------------------------------------------------------------
def test_mint_irregularity_from_zero_address():
    baselines = {'asset-1': AssetBaseline(mint_burn_mean=Decimal('1000'), mint_burn_samples=20)}
    events = [_ev(1, frm=d.ZERO_ADDRESS, to='0xrecipient', amount=10000)]  # 10x mint baseline
    candidates = d.detect_mint_burn_irregularity(events, baselines=baselines, config=_cfg())
    assert len(candidates) == 1
    c = candidates[0]
    assert c.detection_type == 'mint_burn_irregularity'
    assert c.evidence_quality == 'event_logs'
    assert 'mint' in c.title.lower()


def test_burn_irregularity_to_dead_address():
    baselines = {'asset-1': AssetBaseline(mint_burn_mean=Decimal('1000'), mint_burn_samples=20)}
    events = [_ev(1, frm='0xholder', to='0x000000000000000000000000000000000000dead', amount=9000)]
    candidates = d.detect_mint_burn_irregularity(events, baselines=baselines, config=_cfg())
    assert len(candidates) == 1
    assert 'burn' in candidates[0].title.lower()


# --------------------------------------------------------------------------
# Privileged / admin action (decoded evidence only)
# --------------------------------------------------------------------------
def test_privileged_action_detected_on_decoded_event():
    events = [_ev(1, event_type='ownership_transferred', frm='0xadmin', to=None)]
    candidates = d.detect_privileged_actions(events, config=_cfg())
    assert len(candidates) == 1
    c = candidates[0]
    assert c.detection_type == 'privileged_action'
    assert c.privileged is True
    assert c.evidence_quality == 'decoded_call'


def test_privileged_detector_ignores_plain_transfers():
    # A plain transfer never reaches the privileged detector.
    events = [_ev(1, event_type='wallet_transfer_detected')]
    candidates = d.detect_privileged_actions(events, config=_cfg())
    assert candidates == []


# --------------------------------------------------------------------------
# Unsupported detectors are never produced (truthfulness)
# --------------------------------------------------------------------------
def test_transfer_telemetry_never_produces_reentrancy_or_flashloan():
    baselines = {'asset-1': AssetBaseline(transfer_mean=Decimal('100'), transfer_samples=20)}
    events = [_ev(i, amount=1500, to=f'0xdest{i}', offset_seconds=i) for i in range(6)]
    candidates = d.extract_candidates(events, baselines=baselines, config=_cfg())
    produced_types = {c.detection_type for c in candidates}
    assert 'reentrancy_pattern' not in produced_types
    assert 'flash_loan_pattern' not in produced_types
    assert 'oracle_deviation' not in produced_types


def test_detector_support_declares_unsupported_detectors():
    support = tdc.detector_support()
    assert support['reentrancy_pattern']['supported'] is False
    assert support['flash_loan_pattern']['supported'] is False
    assert support['oracle_deviation']['supported'] is False
    assert support['unusual_transfer']['supported'] is True
    assert support['mint_burn_irregularity']['supported'] is True


# --------------------------------------------------------------------------
# Evidence source truthfulness
# --------------------------------------------------------------------------
def test_simulator_evidence_downgrades_cluster_source():
    baselines = {'asset-1': AssetBaseline(transfer_mean=Decimal('100'), transfer_samples=20)}
    events = [_ev(1, amount=1500, source='simulator')]
    candidates = d.detect_unusual_transfers(events, baselines=baselines, config=_cfg())
    assert candidates and candidates[0].evidence_source == 'simulator'


def test_extract_candidates_is_deterministic():
    baselines = {'asset-1': AssetBaseline(transfer_mean=Decimal('100'), transfer_samples=20)}
    events = [_ev(1, amount=1500), _ev(2, event_type='paused', frm='0xadmin')]
    first = d.extract_candidates(events, baselines=baselines, config=_cfg())
    second = d.extract_candidates(events, baselines=baselines, config=_cfg())
    assert [(c.detection_type, c.title) for c in first] == [(c.detection_type, c.title) for c in second]
