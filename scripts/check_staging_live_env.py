#!/usr/bin/env python3
"""
Preflight env check for the staging live evidence proof (blocker 3).

This script only inspects environment variables. It does NOT perform any
JSON-RPC call, network I/O, or proof generation. Its sole purpose is to
give a fast, unambiguous answer to the question: "do I have the env vars
needed to even attempt the staging live evidence proof?"

Checked variable groups:
  - STAGING_EVM_RPC_URL  or  EVM_RPC_URL   (real JSON-RPC endpoint)
  - STAGING_EVM_CHAIN_ID or  EVM_CHAIN_ID  (chain id, e.g. 1 for mainnet)
  - STAGING_WORKER_ENABLED = true          (worker confirmed on)

Output (always written to stdout):
  - A masked RPC URL (the trailing secret segment is replaced with [masked]).
    The full RPC URL is NEVER printed.
  - Whether the chain id is present and non-placeholder.
  - Whether STAGING_WORKER_ENABLED is set to a truthy value.
  - The exact next command to run when all checks pass:
      make run-staging-live-proof
  - If any check fails, a clear "BLOCKER 3 IS NOT A CODE FAILURE" message
    naming the missing variables and the remediation steps.

Exit codes:
  - 0  All required env vars are present and non-placeholder.
  - 1  At least one required env var is missing or a placeholder.

Usage:
  python scripts/check_staging_live_env.py
  make check-staging-live-env
"""
from __future__ import annotations

import os
import sys
from typing import Mapping

_PLACEHOLDER_MARKERS = frozenset({
    'example', 'changeme', 'replace-me', 'placeholder', 'test-key', 'your_',
})

_TRUTHY = frozenset({'1', 'true', 'yes', 'on'})


def _env_val(env: Mapping[str, str], name: str) -> str:
    return (env.get(name) or '').strip()


def _has_placeholder(val: str) -> bool:
    return any(m in val.lower() for m in _PLACEHOLDER_MARKERS)


def mask_rpc_url(url: str) -> str:
    """
    Mask the secret segment of a JSON-RPC URL for safe display.

    https://host/v3/SECRET -> https://host/v3/[masked]

    Never returns the trailing key segment. Safe to print/log.
    """
    if not url:
        return ''
    parts = url.rstrip('/').rsplit('/', 1)
    if len(parts) == 2 and len(parts[1]) > 6:
        return parts[0] + '/[masked]'
    if len(url) > 20:
        return url[:20] + '...'
    return url


def check_env(env: Mapping[str, str] | None = None) -> dict:
    """
    Build a structured preflight report.

    Returns a dict with:
      ok: bool                      - True only when every required var passes
      rpc:    {present, ok, source, masked, detail}
      chain:  {present, ok, source, value, detail}
      worker: {present, ok, raw, detail}
      missing: list[str]            - human-readable missing items
    """
    if env is None:
        env = os.environ

    staging_rpc = _env_val(env, 'STAGING_EVM_RPC_URL')
    base_rpc = _env_val(env, 'EVM_RPC_URL')
    if staging_rpc:
        rpc_url, rpc_source = staging_rpc, 'STAGING_EVM_RPC_URL'
    elif base_rpc:
        rpc_url, rpc_source = base_rpc, 'EVM_RPC_URL'
    else:
        rpc_url, rpc_source = '', 'STAGING_EVM_RPC_URL / EVM_RPC_URL'

    staging_chain = _env_val(env, 'STAGING_EVM_CHAIN_ID')
    base_chain = _env_val(env, 'EVM_CHAIN_ID')
    if staging_chain:
        chain_id, chain_source = staging_chain, 'STAGING_EVM_CHAIN_ID'
    elif base_chain:
        chain_id, chain_source = base_chain, 'EVM_CHAIN_ID'
    else:
        chain_id, chain_source = '', 'STAGING_EVM_CHAIN_ID / EVM_CHAIN_ID'

    worker_raw = _env_val(env, 'STAGING_WORKER_ENABLED')

    missing: list[str] = []

    rpc_present = bool(rpc_url)
    rpc_ok = rpc_present and not _has_placeholder(rpc_url)
    rpc_masked = mask_rpc_url(rpc_url) if rpc_url else ''
    if not rpc_present:
        rpc_detail = 'STAGING_EVM_RPC_URL / EVM_RPC_URL not set'
        missing.append('STAGING_EVM_RPC_URL or EVM_RPC_URL not configured')
    elif not rpc_ok:
        rpc_detail = f'{rpc_source} is a placeholder value'
        missing.append(
            f'{rpc_source} is a placeholder; '
            'set a real JSON-RPC endpoint'
        )
    else:
        rpc_detail = f'{rpc_source} = {rpc_masked}'

    chain_present = bool(chain_id)
    chain_ok = chain_present and not _has_placeholder(chain_id)
    if not chain_present:
        chain_detail = 'STAGING_EVM_CHAIN_ID / EVM_CHAIN_ID not set'
        missing.append('STAGING_EVM_CHAIN_ID or EVM_CHAIN_ID not configured')
    elif not chain_ok:
        chain_detail = f'{chain_source} is a placeholder value'
        missing.append(f'{chain_source} is a placeholder')
    else:
        chain_detail = f'{chain_source} = {chain_id}'

    worker_present = bool(worker_raw)
    worker_ok = worker_raw.lower() in _TRUTHY
    if worker_ok:
        worker_detail = 'STAGING_WORKER_ENABLED = true'
    elif worker_present:
        worker_detail = (
            f"STAGING_WORKER_ENABLED = {worker_raw!r} (must be 'true')"
        )
        missing.append('STAGING_WORKER_ENABLED not set to true')
    else:
        worker_detail = 'STAGING_WORKER_ENABLED not set'
        missing.append('STAGING_WORKER_ENABLED not set to true')

    return {
        'ok': not missing,
        'rpc': {
            'present': rpc_present,
            'ok': rpc_ok,
            'source': rpc_source,
            'masked': rpc_masked,
            'detail': rpc_detail,
        },
        'chain': {
            'present': chain_present,
            'ok': chain_ok,
            'source': chain_source,
            'value': chain_id if chain_ok else '',
            'detail': chain_detail,
        },
        'worker': {
            'present': worker_present,
            'ok': worker_ok,
            'raw': worker_raw,
            'detail': worker_detail,
        },
        'missing': missing,
    }


def _hr(char: str = '=') -> str:
    return char * 70


def print_report(report: dict) -> None:
    print(_hr())
    print(' Decoda RWA Guard - Staging Live Env Preflight (blocker 3)')
    print(_hr())
    print('')
    print('Required env var groups:')
    for name, item in (
        ('RPC endpoint', report['rpc']),
        ('Chain ID', report['chain']),
        ('Worker enabled', report['worker']),
    ):
        marker = '[ OK ]' if item['ok'] else '[FAIL]'
        print(f"  {marker}  {name:<16}{item['detail']}")
    print('')

    if report['ok']:
        print('All required env vars are present.')
        print('')
        print('Next command:')
        print('  make run-staging-live-proof')
        print('')
        return

    print('BLOCKER 3 IS NOT A CODE FAILURE.')
    print('Real staging provider env vars are missing.')
    print('')
    print('Required:')
    print('  - STAGING_EVM_RPC_URL or EVM_RPC_URL')
    print('  - STAGING_EVM_CHAIN_ID or EVM_CHAIN_ID')
    print('  - STAGING_WORKER_ENABLED=true')
    print('')
    print('Missing:')
    for item in report['missing']:
        print(f'  - {item}')
    print('')
    print('Set these in GitHub Actions secrets / Railway / local shell,')
    print('then run:')
    print('')
    print('  make run-staging-live-proof')
    print('')
    print('Do not commit real provider secrets. See .env.staging.example.')


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if '-h' in argv or '--help' in argv:
        print(__doc__ or '')
        return 0
    report = check_env()
    print_report(report)
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
