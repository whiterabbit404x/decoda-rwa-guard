"""
Tests for scripts/validate_launch_proof_completeness.py

Verifies that:
- Missing proof artifact is always a blocker (fail-closed)
- Simulator/demo/fixture evidence cannot satisfy the live proof gate
- Proof claiming live evidence without a backing artifact is blocked
- Valid proof passes
- Missing required fields are reported
- Local/CI proof modes trigger warnings, not failures on their own
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))

from scripts.validate_launch_proof_completeness import validate_launch_proof_completeness


def _write_proof(tmp_path: Path, data: dict) -> Path:
    proof_dir = tmp_path / 'launch-proof' / 'test-run'
    proof_dir.mkdir(parents=True)
    (proof_dir / 'summary.json').write_text(json.dumps(data))
    return proof_dir


def _write_live_evidence(tmp_path: Path, data: dict) -> Path:
    live_dir = tmp_path / 'live-evidence-proof' / 'latest'
    live_dir.mkdir(parents=True)
    (live_dir / 'summary.json').write_text(json.dumps(data))
    return live_dir


def _abs_source(path: Path) -> str:
    """Return an absolute path string that can be used in a proof's 'source' field."""
    return str(path)


_BASE_PROOF = {
    'schema_version': 1,
    'generated_at': '2026-06-04T06:25:44+00:00',
    'launch_mode': 'paid_saas',
    'proof_mode': 'staging',
}

_BASE_LIVE_EVIDENCE = {
    'schema_version': 1,
    'generated_at': '2026-06-04T06:20:00+00:00',
    'live_provider_evidence': {
        'evidence_source': 'live',
        'provider_ready': True,
        'block_number_observed': '19000000',
        'latest_live_telemetry_at': '2026-06-04T06:19:00+00:00',
        'live_evidence_ready': True,
        'contradiction_flags': [],
        'chain': {
            'telemetry_event_id': 'te-abc123',
            'detection_id': 'det-abc123',
            'alert_id': 'al-abc123',
            'evidence_package_id': 'ep-abc123',
        },
    },
}


def test_missing_proof_directory_is_blocked():
    """No proof artifact at all must always fail-closed."""
    passed, blockers, _ = validate_launch_proof_completeness(proof_dir_override='/tmp/nonexistent_proof_dir')
    assert not passed
    assert any('No launch proof artifact found' in b or 'not found' in b.lower() for b in blockers), blockers


def test_missing_summary_json_is_blocked(tmp_path):
    proof_dir = tmp_path / 'launch-proof' / 'run1'
    proof_dir.mkdir(parents=True)
    # No summary.json written
    passed, blockers, _ = validate_launch_proof_completeness(proof_dir_override=str(proof_dir))
    assert not passed
    assert any('summary.json' in b for b in blockers), blockers


def test_minimal_proof_without_live_evidence_passes(tmp_path, monkeypatch):
    """Proof without live_provider_evidence_ready=true should pass (warns about missing live evidence)."""
    proof_dir = _write_proof(tmp_path, {
        **_BASE_PROOF,
        'readiness_categories': {'live_provider_evidence_ready': False},
        'live_provider_evidence': {'evidence_source': ''},
    })
    # Monkeypatch _LIVE_EVIDENCE_PROOF_LATEST so it doesn't check the real filesystem
    passed, blockers, warnings = validate_launch_proof_completeness(proof_dir_override=str(proof_dir))
    # Should pass (only warns about missing live evidence)
    assert not blockers, f'Unexpected blockers: {blockers}'
    assert any('live_provider_evidence_ready' in w or 'live readiness' in w for w in warnings)


def test_proof_claiming_live_evidence_blocked_when_source_missing(tmp_path):
    """If live_provider_evidence_ready=true but source artifact missing, must fail."""
    proof_dir = _write_proof(tmp_path, {
        **_BASE_PROOF,
        'readiness_categories': {'live_provider_evidence_ready': True},
        'live_provider_evidence': {
            'evidence_source': 'live',
            'source': 'artifacts/live-evidence-proof/latest/summary.json',
        },
    })
    # Don't write the live evidence artifact — it's missing
    passed, blockers, _ = validate_launch_proof_completeness(proof_dir_override=str(proof_dir))
    assert not passed
    assert any('live_provider_evidence_ready=true' in b or 'live evidence source artifact is missing' in b for b in blockers), blockers


def test_simulator_evidence_in_launch_proof_is_blocked(tmp_path):
    """Simulator evidence in the launch proof itself is a hard blocker."""
    proof_dir = _write_proof(tmp_path, {
        **_BASE_PROOF,
        'readiness_categories': {'live_provider_evidence_ready': True},
        'live_provider_evidence': {
            'evidence_source': 'simulator',
        },
    })
    passed, blockers, _ = validate_launch_proof_completeness(proof_dir_override=str(proof_dir))
    assert not passed
    assert any('simulator' in b for b in blockers), blockers


def test_demo_evidence_is_blocked(tmp_path):
    """Demo evidence cannot satisfy the live proof gate."""
    proof_dir = _write_proof(tmp_path, {
        **_BASE_PROOF,
        'readiness_categories': {'live_provider_evidence_ready': True},
        'live_provider_evidence': {
            'evidence_source': 'demo',
        },
    })
    passed, blockers, _ = validate_launch_proof_completeness(proof_dir_override=str(proof_dir))
    assert not passed
    assert any('simulator' in b or 'demo' in b for b in blockers), blockers


def test_guided_simulator_evidence_is_blocked(tmp_path):
    """guided_simulator evidence cannot satisfy the live proof gate."""
    proof_dir = _write_proof(tmp_path, {
        **_BASE_PROOF,
        'readiness_categories': {'live_provider_evidence_ready': True},
        'live_provider_evidence': {
            'evidence_source': 'guided_simulator',
        },
    })
    passed, blockers, _ = validate_launch_proof_completeness(proof_dir_override=str(proof_dir))
    assert not passed
    assert any('simulator' in b or 'guided_simulator' in b for b in blockers), blockers


def test_live_evidence_source_missing_block_number_is_blocked(tmp_path):
    """Live evidence without block_number_observed cannot prove real RPC response."""
    live_dir = _write_live_evidence(tmp_path, {
        'schema_version': 1,
        'generated_at': '2026-06-04T06:20:00+00:00',
        'live_provider_evidence': {
            'evidence_source': 'live',
            'provider_ready': True,
            'block_number_observed': None,  # missing
            'latest_live_telemetry_at': '2026-06-04T06:19:00+00:00',
            'live_evidence_ready': False,
            'contradiction_flags': [],
            'chain': {},
        },
    })
    proof_dir = _write_proof(tmp_path, {
        **_BASE_PROOF,
        'readiness_categories': {'live_provider_evidence_ready': True},
        'live_provider_evidence': {
            'evidence_source': 'live',
            'source': _abs_source(live_dir / 'summary.json'),
        },
    })
    passed, blockers, _ = validate_launch_proof_completeness(proof_dir_override=str(proof_dir))
    assert not passed
    assert any('block_number_observed' in b for b in blockers), blockers


def test_live_evidence_source_missing_telemetry_is_blocked(tmp_path):
    """Live evidence without latest_live_telemetry_at is blocked — heartbeat alone is not proof."""
    live_dir = _write_live_evidence(tmp_path, {
        'schema_version': 1,
        'generated_at': '2026-06-04T06:20:00+00:00',
        'live_provider_evidence': {
            'evidence_source': 'live',
            'provider_ready': True,
            'block_number_observed': '19000000',
            'latest_live_telemetry_at': None,  # missing
            'live_evidence_ready': False,
            'contradiction_flags': [],
            'chain': {},
        },
    })
    proof_dir = _write_proof(tmp_path, {
        **_BASE_PROOF,
        'readiness_categories': {'live_provider_evidence_ready': True},
        'live_provider_evidence': {
            'evidence_source': 'live',
            'source': _abs_source(live_dir / 'summary.json'),
        },
    })
    passed, blockers, _ = validate_launch_proof_completeness(proof_dir_override=str(proof_dir))
    assert not passed
    assert any('latest_live_telemetry_at' in b or 'telemetry' in b for b in blockers), blockers


def test_live_evidence_source_with_contradiction_flags_is_blocked(tmp_path):
    """Contradiction flags in the live evidence source invalidate the proof."""
    live_dir = _write_live_evidence(tmp_path, {
        'schema_version': 1,
        'generated_at': '2026-06-04T06:20:00+00:00',
        'live_provider_evidence': {
            'evidence_source': 'live',
            'provider_ready': True,
            'block_number_observed': '19000000',
            'latest_live_telemetry_at': '2026-06-04T06:19:00+00:00',
            'live_evidence_ready': True,
            'contradiction_flags': ['live_mode_with_simulator'],
            'chain': {
                'telemetry_event_id': 'te-abc123',
                'detection_id': 'det-abc123',
                'alert_id': 'al-abc123',
                'evidence_package_id': 'ep-abc123',
            },
        },
    })
    proof_dir = _write_proof(tmp_path, {
        **_BASE_PROOF,
        'readiness_categories': {'live_provider_evidence_ready': True},
        'live_provider_evidence': {
            'evidence_source': 'live',
            'source': _abs_source(live_dir / 'summary.json'),
        },
    })
    passed, blockers, _ = validate_launch_proof_completeness(proof_dir_override=str(proof_dir))
    assert not passed
    assert any('contradiction' in b for b in blockers), blockers


def test_broad_saas_ready_without_live_evidence_is_contradiction(tmp_path):
    """broad_paid_saas_ready=true without live evidence is a contradiction."""
    proof_dir = _write_proof(tmp_path, {
        **_BASE_PROOF,
        'broad_paid_saas_ready': True,
        'readiness_categories': {'live_provider_evidence_ready': False},
        'live_provider_evidence': {'evidence_source': ''},
    })
    passed, blockers, _ = validate_launch_proof_completeness(proof_dir_override=str(proof_dir))
    assert not passed
    assert any('contradiction' in b or 'broad_paid_saas_ready' in b for b in blockers), blockers


def test_local_proof_mode_generates_warning(tmp_path, monkeypatch):
    """Local proof mode should warn but not block on its own."""
    proof_dir = _write_proof(tmp_path, {
        **_BASE_PROOF,
        'proof_mode': 'local',
        'readiness_categories': {'live_provider_evidence_ready': False},
        'live_provider_evidence': {'evidence_source': ''},
    })
    _, blockers, warnings = validate_launch_proof_completeness(proof_dir_override=str(proof_dir))
    assert any('local' in w or 'ci' in w or 'proof_mode' in w for w in warnings), f'No proof_mode warning, got: {warnings}'


def test_missing_generated_at_is_blocked(tmp_path):
    """Proof without generated_at is invalid."""
    proof_dir = _write_proof(tmp_path, {
        'schema_version': 1,
        'launch_mode': 'paid_saas',
        'proof_mode': 'staging',
        # generated_at missing
    })
    passed, blockers, _ = validate_launch_proof_completeness(proof_dir_override=str(proof_dir))
    assert not passed
    assert any('generated_at' in b for b in blockers), blockers


def test_live_evidence_source_simulator_via_source_artifact_is_blocked(tmp_path):
    """Even if launch proof says live, if the source artifact says simulator, block it."""
    live_dir = _write_live_evidence(tmp_path, {
        'schema_version': 1,
        'generated_at': '2026-06-04T06:20:00+00:00',
        'live_provider_evidence': {
            'evidence_source': 'guided_simulator',  # simulator!
            'provider_ready': True,
            'block_number_observed': '19000000',
            'latest_live_telemetry_at': '2026-06-04T06:19:00+00:00',
            'live_evidence_ready': True,
            'contradiction_flags': [],
            'chain': {},
        },
    })
    proof_dir = _write_proof(tmp_path, {
        **_BASE_PROOF,
        'readiness_categories': {'live_provider_evidence_ready': True},
        'live_provider_evidence': {
            'evidence_source': 'live',
            'source': _abs_source(live_dir / 'summary.json'),
        },
    })
    passed, blockers, _ = validate_launch_proof_completeness(proof_dir_override=str(proof_dir))
    assert not passed
    assert any('simulator' in b or 'guided_simulator' in b for b in blockers), blockers
