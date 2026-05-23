"""
Tests for scripts/check_staging_live_env.py

This script is an env-only preflight for the staging live evidence proof
(blocker 3). It must:
  - exit 0 only when all three required env vars are present and non-placeholder
  - exit non-zero (1) when any required env var is missing or a placeholder
  - never print the full RPC URL (only a masked form)
  - explain in plain text that the failure is an env-setup issue, not a code bug
  - point at `make run-staging-live-proof` as the next command when ready

The script must not require any RPC connectivity to run.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from scripts.check_staging_live_env import (
    check_env,
    main,
    mask_rpc_url,
    print_report,
)

# A recognizable secret segment that must never appear in printed output.
_SECRET = 'abcdef1234567890deadbeefcafe'
_REAL_RPC = f'https://mainnet.infura.io/v3/{_SECRET}'


def _good_env() -> dict[str, str]:
    return {
        'STAGING_EVM_RPC_URL': _REAL_RPC,
        'STAGING_EVM_CHAIN_ID': '1',
        'STAGING_WORKER_ENABLED': 'true',
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


def test_mask_rpc_url_keyless_url_truncated() -> None:
    """Long URLs without a clear secret segment still get truncated for safety."""
    masked = mask_rpc_url('https://my-very-long-rpc-host.example.com/short')
    # Should never return the trailing 'short' segment plus full host intact.
    assert masked != 'https://my-very-long-rpc-host.example.com/short'


# ---------------------------------------------------------------------------
# check_env
# ---------------------------------------------------------------------------

def test_check_env_all_set_returns_ok() -> None:
    report = check_env(_good_env())
    assert report['ok'] is True
    assert report['missing'] == []
    assert report['rpc']['ok'] is True
    assert report['chain']['ok'] is True
    assert report['worker']['ok'] is True
    # Masked URL, never raw secret.
    assert '[masked]' in report['rpc']['masked']
    assert _SECRET not in report['rpc']['masked']
    assert _SECRET not in report['rpc']['detail']


def test_check_env_missing_rpc_fails() -> None:
    report = check_env({
        'STAGING_EVM_CHAIN_ID': '1',
        'STAGING_WORKER_ENABLED': 'true',
    })
    assert report['ok'] is False
    assert report['rpc']['ok'] is False
    assert any('RPC' in m or 'EVM_RPC_URL' in m for m in report['missing'])


def test_check_env_missing_chain_id_fails() -> None:
    report = check_env({
        'STAGING_EVM_RPC_URL': _REAL_RPC,
        'STAGING_WORKER_ENABLED': 'true',
    })
    assert report['ok'] is False
    assert report['chain']['ok'] is False
    assert any('CHAIN_ID' in m for m in report['missing'])


def test_check_env_worker_disabled_fails() -> None:
    report = check_env({
        'STAGING_EVM_RPC_URL': _REAL_RPC,
        'STAGING_EVM_CHAIN_ID': '1',
    })
    assert report['ok'] is False
    assert report['worker']['ok'] is False
    assert any('WORKER_ENABLED' in m for m in report['missing'])


def test_check_env_worker_false_string_fails() -> None:
    env = _good_env()
    env['STAGING_WORKER_ENABLED'] = 'false'
    report = check_env(env)
    assert report['ok'] is False
    assert report['worker']['ok'] is False
    assert any('WORKER_ENABLED' in m for m in report['missing'])


def test_check_env_placeholder_rpc_rejected() -> None:
    report = check_env({
        'STAGING_EVM_RPC_URL': 'https://your_rpc_endpoint.example/v3/key',
        'STAGING_EVM_CHAIN_ID': '1',
        'STAGING_WORKER_ENABLED': 'true',
    })
    assert report['ok'] is False
    assert any('placeholder' in m for m in report['missing'])


def test_check_env_staging_preferred_over_base() -> None:
    report = check_env({
        'EVM_RPC_URL': 'https://base.host/v3/baseSECRET1234567',
        'STAGING_EVM_RPC_URL': _REAL_RPC,
        'EVM_CHAIN_ID': '137',
        'STAGING_EVM_CHAIN_ID': '1',
        'STAGING_WORKER_ENABLED': 'true',
    })
    assert report['ok'] is True
    assert report['rpc']['source'] == 'STAGING_EVM_RPC_URL'
    assert report['chain']['source'] == 'STAGING_EVM_CHAIN_ID'
    assert report['chain']['value'] == '1'


def test_check_env_base_vars_accepted_as_fallback() -> None:
    report = check_env({
        'EVM_RPC_URL': _REAL_RPC,
        'EVM_CHAIN_ID': '1',
        'STAGING_WORKER_ENABLED': 'true',
    })
    assert report['ok'] is True
    assert report['rpc']['source'] == 'EVM_RPC_URL'
    assert report['chain']['source'] == 'EVM_CHAIN_ID'


# ---------------------------------------------------------------------------
# print_report / main (output assertions)
# ---------------------------------------------------------------------------

def test_print_report_missing_env_explains_not_a_code_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    report = check_env({})
    print_report(report)
    out = capsys.readouterr().out
    # Plain-text remediation must say this is an env issue, not a code bug.
    assert 'BLOCKER 3 IS NOT A CODE FAILURE' in out
    # All three required vars must be named.
    assert 'STAGING_EVM_RPC_URL' in out
    assert 'STAGING_EVM_CHAIN_ID' in out
    assert 'STAGING_WORKER_ENABLED' in out
    # The next command must be the exact `make` target the user should run
    # once they have configured the env vars.
    assert 'make run-staging-live-proof' in out


def test_print_report_masks_rpc_url(
    capsys: pytest.CaptureFixture[str],
) -> None:
    report = check_env({
        'STAGING_EVM_RPC_URL': _REAL_RPC,
        'STAGING_EVM_CHAIN_ID': '1',
        'STAGING_WORKER_ENABLED': 'true',
    })
    print_report(report)
    out = capsys.readouterr().out
    # The raw secret never appears anywhere in output; the masked form does.
    assert _SECRET not in out
    assert '[masked]' in out


def test_print_report_ok_path_shows_next_command(
    capsys: pytest.CaptureFixture[str],
) -> None:
    print_report(check_env(_good_env()))
    out = capsys.readouterr().out
    assert 'All required env vars are present' in out
    assert 'make run-staging-live-proof' in out


# ---------------------------------------------------------------------------
# main exit codes
# ---------------------------------------------------------------------------

def test_main_missing_env_returns_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for var in (
        'STAGING_EVM_RPC_URL', 'EVM_RPC_URL',
        'STAGING_EVM_CHAIN_ID', 'EVM_CHAIN_ID',
        'STAGING_WORKER_ENABLED',
    ):
        monkeypatch.delenv(var, raising=False)
    rc = main([])
    assert rc == 1
    out = capsys.readouterr().out
    assert 'BLOCKER 3 IS NOT A CODE FAILURE' in out


def test_main_all_present_returns_zero(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv('STAGING_EVM_RPC_URL', _REAL_RPC)
    monkeypatch.setenv('STAGING_EVM_CHAIN_ID', '1')
    monkeypatch.setenv('STAGING_WORKER_ENABLED', 'true')
    rc = main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert _SECRET not in out
    assert '[masked]' in out


def test_main_help_returns_zero(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(['--help'])
    assert rc == 0
    out = capsys.readouterr().out
    # __doc__ should describe the script.
    assert 'blocker 3' in out.lower() or 'preflight' in out.lower()
