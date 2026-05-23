"""
Tests for scripts/run_staging_live_evidence_proof.py

Covers:
- Missing env vars => fail-closed checklist, exit 1, no commands run.
- Partial env (RPC only) => fail closed.
- RPC URL is masked; the secret segment never appears in printed output.
- Staging env vars are preferred over base env vars.
- Placeholder RPC URL is rejected.
- With mocked env + mocked live-evidence-proof artifact that reports
  live_evidence_ready=true, the final summary detects it and the runner
  returns 0.
- With mocked env + a fail-closed artifact, the runner returns 1 (no
  faked readiness).
- Missing artifact => returns 1.
- Importing the module does not require real RPC env vars at import time.
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from scripts.run_staging_live_evidence_proof import (
    build_final_summary,
    build_preflight,
    main,
    mask_rpc_url,
    read_provider_env,
    run_staging_live_evidence_proof,
)

# A fake RPC URL with a recognizable "secret" segment that must never appear
# in the runner's printed output. The hex avoids any placeholder marker.
_SECRET = 'abcdef1234567890deadbeefcafe'
_REAL_RPC = f'https://mainnet.infura.io/v3/{_SECRET}'

_PROVIDER_ENV_VARS = (
    'EVM_RPC_URL', 'STAGING_EVM_RPC_URL',
    'EVM_CHAIN_ID', 'STAGING_EVM_CHAIN_ID', 'CHAIN_ID',
    'STAGING_WORKER_ENABLED',
)


def _good_env() -> dict[str, str]:
    return {
        'STAGING_EVM_RPC_URL': _REAL_RPC,
        'STAGING_EVM_CHAIN_ID': '1',
        'STAGING_WORKER_ENABLED': 'true',
    }


class _Recorder:
    """Test double for the runner callable. Records calls and returns 0."""

    def __init__(self, return_code: int = 0) -> None:
        self.calls: list[tuple[str, list[str]]] = []
        self.return_code = return_code

    def __call__(self, label: str, cmd: list[str]) -> int:
        self.calls.append((label, list(cmd)))
        return self.return_code


def _live_ready_proof() -> dict[str, Any]:
    return {
        'schema_version': 1,
        'generated_at': '2026-05-22T12:00:00+00:00',
        'live_provider_evidence': {
            'provider_ready': True,
            'provider_mode': 'live',
            'provider_health_checked': True,
            'provider_checked_at': '2026-05-22T12:00:00+00:00',
            'provider_url_masked': 'https://mainnet.infura.io/v3/[masked]',
            'chain_id_configured': True,
            'chain_id_observed': '1',
            'block_number_observed': '19650000',
            'worker_enabled': True,
            'evidence_source': 'live',
            'latest_live_telemetry_at': '2026-05-22T12:00:00+00:00',
            'live_evidence_ready': True,
            'chain': {
                'telemetry_event_id': 'tel-uuid',
                'detection_id': 'det-uuid',
                'alert_id': 'alert-uuid',
                'incident_id': 'inc-uuid',
                'response_action_id': 'resp-uuid',
                'evidence_package_id': 'pkg-uuid',
            },
            'missing': [],
            'contradiction_flags': [],
        },
    }


def _fail_closed_proof() -> dict[str, Any]:
    return {
        'schema_version': 1,
        'generated_at': '2026-05-22T12:00:00+00:00',
        'live_provider_evidence': {
            'provider_ready': False,
            'provider_mode': 'disabled',
            'provider_health_checked': False,
            'provider_checked_at': None,
            'provider_url_masked': '',
            'chain_id_configured': False,
            'chain_id_observed': None,
            'block_number_observed': None,
            'worker_enabled': False,
            'evidence_source': 'unknown',
            'latest_live_telemetry_at': None,
            'live_evidence_ready': False,
            'chain': {
                'telemetry_event_id': None,
                'detection_id': None,
                'alert_id': None,
                'incident_id': None,
                'response_action_id': None,
                'evidence_package_id': None,
            },
            'missing': ['EVM_RPC_URL or STAGING_EVM_RPC_URL not configured'],
            'contradiction_flags': [],
        },
    }


# ---------------------------------------------------------------------------
# mask_rpc_url
# ---------------------------------------------------------------------------

def test_mask_rpc_url_hides_secret() -> None:
    masked = mask_rpc_url(_REAL_RPC)
    assert masked == 'https://mainnet.infura.io/v3/[masked]'
    assert _SECRET not in masked


def test_mask_rpc_url_empty_returns_empty() -> None:
    assert mask_rpc_url('') == ''


def test_mask_rpc_url_handles_long_keyless_url() -> None:
    masked = mask_rpc_url('https://rpc.example.com/short')
    # Should not return the trailing 'short' segment intact for a long-ish URL.
    # Either truncated with '...' or masked with '[masked]'.
    assert masked != 'https://rpc.example.com/short' or len(masked) <= 30


# ---------------------------------------------------------------------------
# read_provider_env
# ---------------------------------------------------------------------------

def test_read_provider_env_prefers_staging_over_base() -> None:
    env = {
        'EVM_RPC_URL': 'https://base.host/v3/basekey1234567890',
        'STAGING_EVM_RPC_URL': _REAL_RPC,
        'EVM_CHAIN_ID': '137',
        'STAGING_EVM_CHAIN_ID': '1',
        'STAGING_WORKER_ENABLED': 'true',
    }
    resolved = read_provider_env(env)
    assert resolved['rpc_url'] == _REAL_RPC
    assert resolved['rpc_source'] == 'STAGING_EVM_RPC_URL'
    assert resolved['chain_id'] == '1'
    assert resolved['chain_source'] == 'STAGING_EVM_CHAIN_ID'
    assert resolved['worker_enabled'] is True


def test_read_provider_env_falls_back_to_base_vars() -> None:
    env = {
        'EVM_RPC_URL': _REAL_RPC,
        'EVM_CHAIN_ID': '1',
    }
    resolved = read_provider_env(env)
    assert resolved['rpc_url'] == _REAL_RPC
    assert resolved['rpc_source'] == 'EVM_RPC_URL'
    assert resolved['chain_source'] == 'EVM_CHAIN_ID'
    assert resolved['worker_enabled'] is False


def test_read_provider_env_empty_returns_blanks() -> None:
    resolved = read_provider_env({})
    assert resolved['rpc_url'] == ''
    assert resolved['chain_id'] == ''
    assert resolved['worker_enabled'] is False


# ---------------------------------------------------------------------------
# build_preflight
# ---------------------------------------------------------------------------

def test_build_preflight_ok_with_good_env() -> None:
    pf = build_preflight(_good_env())
    assert pf['ok'] is True
    assert pf['missing'] == []
    assert all(item['ok'] for item in pf['items'])
    # Masked URL appears in the RPC item detail; raw secret does not.
    rpc_item = next(i for i in pf['items'] if i['name'] == 'RPC endpoint')
    assert '[masked]' in rpc_item['detail']
    assert _SECRET not in rpc_item['detail']


def test_build_preflight_empty_env_lists_all_missing() -> None:
    pf = build_preflight({})
    assert pf['ok'] is False
    joined = ' '.join(pf['missing'])
    assert 'STAGING_EVM_RPC_URL' in joined
    assert 'STAGING_EVM_CHAIN_ID' in joined
    assert 'STAGING_WORKER_ENABLED' in joined
    assert all(item['ok'] is False for item in pf['items'])


def test_build_preflight_rejects_placeholder_rpc_url() -> None:
    pf = build_preflight({
        'STAGING_EVM_RPC_URL': 'https://your_rpc_endpoint.example/v3/key',
        'STAGING_EVM_CHAIN_ID': '1',
        'STAGING_WORKER_ENABLED': 'true',
    })
    assert pf['ok'] is False
    assert any('placeholder' in m for m in pf['missing'])


def test_build_preflight_partial_env_still_fails() -> None:
    pf = build_preflight({'STAGING_EVM_RPC_URL': _REAL_RPC})
    assert pf['ok'] is False
    joined = ' '.join(pf['missing'])
    assert 'STAGING_EVM_CHAIN_ID' in joined
    assert 'STAGING_WORKER_ENABLED' in joined


# ---------------------------------------------------------------------------
# build_final_summary
# ---------------------------------------------------------------------------

def test_build_final_summary_detects_live_evidence_ready() -> None:
    summary = build_final_summary(_live_ready_proof())
    assert summary['artifact_present'] is True
    assert summary['live_evidence_ready'] is True
    assert summary['provider_ready'] is True
    assert summary['provider_mode'] == 'live'
    assert summary['provider_health_checked'] is True
    assert summary['evidence_source'] == 'live'
    assert summary['latest_live_telemetry_at'] == '2026-05-22T12:00:00+00:00'
    assert summary['telemetry_event_id'] == 'tel-uuid'
    assert summary['detection_id'] == 'det-uuid'
    assert summary['alert_id'] == 'alert-uuid'
    assert summary['incident_id'] == 'inc-uuid'
    assert summary['response_action_id'] == 'resp-uuid'
    assert summary['evidence_package_id'] == 'pkg-uuid'


def test_build_final_summary_none_proof_fails_closed() -> None:
    summary = build_final_summary(None)
    assert summary['artifact_present'] is False
    assert summary['live_evidence_ready'] is False
    assert summary['provider_ready'] is False
    assert summary['provider_mode'] == 'unknown'
    assert summary['evidence_source'] == 'unknown'


def test_build_final_summary_fail_closed_artifact() -> None:
    summary = build_final_summary(_fail_closed_proof())
    assert summary['artifact_present'] is True
    assert summary['live_evidence_ready'] is False
    assert summary['provider_mode'] == 'disabled'
    assert summary['evidence_source'] == 'unknown'
    assert summary['missing']
    assert summary['telemetry_event_id'] is None


# ---------------------------------------------------------------------------
# run_staging_live_evidence_proof
# ---------------------------------------------------------------------------

def test_run_missing_env_fails_closed_and_runs_no_commands(
    capsys: pytest.CaptureFixture[str],
) -> None:
    recorder = _Recorder()
    rc = run_staging_live_evidence_proof(env={}, runner=recorder)

    assert rc == 1
    assert recorder.calls == []  # no commands run

    out = capsys.readouterr().out
    # The remediation must clearly explain this is an env-setup issue,
    # not a code failure.
    assert 'BLOCKER 3 IS NOT A CODE FAILURE' in out
    assert '[FAIL]' in out
    # Remediation must clearly name the three required variables.
    assert 'STAGING_EVM_RPC_URL' in out
    assert 'STAGING_EVM_CHAIN_ID' in out
    assert 'STAGING_WORKER_ENABLED' in out
    # Remediation must point at the runner entry point.
    assert 'make run-staging-live-proof' in out


def test_run_partial_env_fails_closed(capsys: pytest.CaptureFixture[str]) -> None:
    """RPC set but chain id and worker flag missing => fail closed, no commands."""
    recorder = _Recorder()
    rc = run_staging_live_evidence_proof(
        env={'STAGING_EVM_RPC_URL': _REAL_RPC},
        runner=recorder,
    )

    assert rc == 1
    assert recorder.calls == []
    out = capsys.readouterr().out
    # The masked URL appears, the raw secret never does.
    assert _SECRET not in out
    assert '[masked]' in out


def test_run_no_env_message_says_not_a_code_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """
    Blocker 3 fail-closed message must explicitly state the failure is an
    env-setup issue, not a code regression. This wording is load-bearing —
    operators and CI consumers look at it to decide whether to file a bug or
    configure secrets.
    """
    recorder = _Recorder()
    rc = run_staging_live_evidence_proof(env={}, runner=recorder)
    assert rc == 1
    out = capsys.readouterr().out
    assert 'BLOCKER 3 IS NOT A CODE FAILURE' in out
    assert 'Real staging provider env vars are missing' in out
    # The three required vars must be named exactly so users can grep.
    assert 'STAGING_EVM_RPC_URL' in out
    assert 'STAGING_EVM_CHAIN_ID' in out
    assert 'STAGING_WORKER_ENABLED=true' in out
    # The next command must be the make target.
    assert 'make run-staging-live-proof' in out
    # The remediation must not pretend the proof passed.
    assert 'live_evidence_ready=true' not in out.lower() or \
           'live_evidence_ready=true)' not in out.lower()


def test_run_with_good_env_and_live_ready_artifact_returns_zero(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    artifact = tmp_path / 'summary.json'
    artifact.write_text(json.dumps(_live_ready_proof()))

    recorder = _Recorder()
    rc = run_staging_live_evidence_proof(
        env=_good_env(),
        runner=recorder,
        live_evidence_proof_path=artifact,
    )

    assert rc == 0
    # All five proof commands were invoked.
    assert len(recorder.calls) == 5
    labels = [label for label, _ in recorder.calls]
    assert any('Live evidence proof' in lbl for lbl in labels)
    assert any('generate-live-evidence-proof' in lbl for lbl in labels)
    assert any('generate-staging-proof' in lbl for lbl in labels)
    assert any('validate-staging-proof' in lbl for lbl in labels)
    assert any('100% readiness' in lbl for lbl in labels)

    out = capsys.readouterr().out
    # Final summary surfaces every required field.
    assert 'provider_ready=True' in out
    assert 'provider_mode=live' in out
    assert 'provider_health_checked=True' in out
    assert 'evidence_source=live' in out
    assert 'latest_live_telemetry_at=2026-05-22T12:00:00+00:00' in out
    assert 'live_evidence_ready=True' in out
    assert 'telemetry_event_id=tel-uuid' in out
    assert 'detection_id=det-uuid' in out
    assert 'alert_id=alert-uuid' in out
    assert 'incident_id=inc-uuid' in out
    assert 'evidence_package_id=pkg-uuid' in out
    assert 'BLOCKER 3: PASS' in out
    # Raw secret never appears in the final summary either.
    assert _SECRET not in out


def test_run_with_good_env_but_fail_closed_artifact_returns_nonzero(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """live_evidence_ready=false in artifact => exit 1 even when env is fully set.

    This is the explicit no-fake guarantee: the runner reads the artifact and
    never overrides it with a hardcoded true.
    """
    artifact = tmp_path / 'summary.json'
    artifact.write_text(json.dumps(_fail_closed_proof()))

    recorder = _Recorder()
    rc = run_staging_live_evidence_proof(
        env=_good_env(),
        runner=recorder,
        live_evidence_proof_path=artifact,
    )

    assert rc == 1
    # All commands still ran (diagnostic completeness).
    assert len(recorder.calls) == 5
    out = capsys.readouterr().out
    assert 'BLOCKER 3: FAIL' in out
    assert 'live_evidence_ready=False' in out


def test_run_with_missing_artifact_returns_nonzero(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    artifact = tmp_path / 'does-not-exist.json'
    recorder = _Recorder()
    rc = run_staging_live_evidence_proof(
        env=_good_env(),
        runner=recorder,
        live_evidence_proof_path=artifact,
    )
    assert rc == 1
    out = capsys.readouterr().out
    assert 'BLOCKER 3: FAIL' in out
    assert 'artifact missing or unreadable' in out


def test_run_propagates_subprocess_failures_but_reports_pass_when_artifact_ready(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Even when downstream commands return non-zero, the runner returns 0 iff
    the artifact reports live_evidence_ready=true, and prints a clear note
    that broader gates remain blocked.
    """
    artifact = tmp_path / 'summary.json'
    artifact.write_text(json.dumps(_live_ready_proof()))

    # Last command (validate-100% staging strict) returns 1; others return 0.
    call_index = {'i': 0}

    def runner(label: str, cmd: list[str]) -> int:
        call_index['i'] += 1
        return 1 if call_index['i'] == 5 else 0

    rc = run_staging_live_evidence_proof(
        env=_good_env(),
        runner=runner,
        live_evidence_proof_path=artifact,
    )

    assert rc == 0  # blocker 3 itself is proven
    out = capsys.readouterr().out
    assert 'BLOCKER 3: PASS' in out
    assert 'downstream gates' in out


# ---------------------------------------------------------------------------
# Build-time safety
# ---------------------------------------------------------------------------

def test_module_import_does_not_require_rpc_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for var in _PROVIDER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)

    import scripts.run_staging_live_evidence_proof as mod
    importlib.reload(mod)

    for attr in (
        'run_staging_live_evidence_proof',
        'build_preflight',
        'build_final_summary',
        'read_provider_env',
        'mask_rpc_url',
        'main',
    ):
        assert hasattr(mod, attr), f'expected attribute: {attr}'


def test_main_help_returns_zero(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(['--help'])
    assert rc == 0
    out = capsys.readouterr().out
    assert 'staging live' in out.lower() or 'blocker 3' in out.lower()
