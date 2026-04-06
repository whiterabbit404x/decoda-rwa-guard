#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shutil import which

REPO_ROOT = Path(__file__).resolve().parents[3]
ARTIFACT_DIR = REPO_ROOT / 'services' / 'api' / 'artifacts' / 'live_evidence' / 'latest'


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _request_json(url: str, *, method: str = 'GET', payload: dict[str, Any] | None = None, token: str | None = None, workspace_id: str | None = None, timeout: int = 30) -> tuple[int, dict[str, Any]]:
    headers = {'Accept': 'application/json'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    if workspace_id:
        headers['x-workspace-id'] = workspace_id
    body = None
    if payload is not None:
        headers['Content-Type'] = 'application/json'
        body = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode('utf-8') or '{}')
    except urllib.error.HTTPError as exc:
        data = exc.read().decode('utf-8', errors='ignore')
        try:
            return exc.code, json.loads(data or '{}')
        except Exception:
            return exc.code, {'error': data}


def _wait_for_http(url: str, *, timeout_seconds: int = 60) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            code, _ = _request_json(url, timeout=3)
            if code < 500:
                return
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError(f'timed out waiting for {url}')


def _require_cmd(name: str) -> None:
    if which(name) is None:
        raise RuntimeError(f'required command not found: {name}')


def _rpc_call(rpc_url: str, method: str, params: list[Any]) -> Any:
    req = urllib.request.Request(
        rpc_url,
        data=json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = json.loads(resp.read().decode('utf-8') or '{}')
    if body.get('error'):
        raise RuntimeError(f"rpc error for {method}: {body['error']}")
    return body.get('result')


def _signup_proof_user(api_url: str, *, email: str, password: str) -> dict[str, Any]:
    status, signup = _request_json(
        f'{api_url}/auth/signup',
        method='POST',
        payload={'email': email, 'password': password, 'full_name': 'Feature1 Live Proof'},
    )
    if status == 409:
        raise RuntimeError(
            'failed to sign up proof user: email already exists (status=409). '
            f'Use FEATURE1_PROOF_EMAIL to override for reruns. email={email} body={signup}'
        )
    if status not in {200, 201}:
        raise RuntimeError(f'failed to sign up proof user: status={status} body={signup}')
    return signup


def _verify_proof_user_email(api_url: str, *, verification_token: str) -> None:
    status, payload = _request_json(
        f'{api_url}/auth/verify-email',
        method='POST',
        payload={'token': verification_token},
    )
    if status not in {200, 201}:
        raise RuntimeError(f'failed to verify proof user email: status={status} body={payload}')


def _signin_proof_user(api_url: str, *, email: str, password: str) -> dict[str, Any]:
    status, signin = _request_json(
        f'{api_url}/auth/signin',
        method='POST',
        payload={'email': email, 'password': password},
    )
    if status not in {200, 201}:
        raise RuntimeError(f'failed to sign in proof user: status={status} body={signin}')
    return signin


def _bootstrap_auth(api_url: str, *, email: str, password: str) -> tuple[str, str]:
    signup = _signup_proof_user(api_url, email=email, password=password)
    verification_token = str(signup.get('verification_token') or '')
    if not verification_token:
        raise RuntimeError(
            'proof signup did not return verification_token. '
            'Ensure AUTH_EXPOSE_DEBUG_TOKENS=true for the API process running this local proof harness. '
            f'body={signup}'
        )
    _verify_proof_user_email(api_url, verification_token=verification_token)
    signin = _signin_proof_user(api_url, email=email, password=password)
    token = str(signin.get('access_token') or '')
    if not token:
        raise RuntimeError(f'proof signin did not return access_token: {signin}')
    user = signin.get('user') if isinstance(signin.get('user'), dict) else {}
    workspace = user.get('current_workspace') if isinstance(user.get('current_workspace'), dict) else {}
    workspace_id = str(workspace.get('id') or '')
    if not workspace_id:
        raise RuntimeError(f'proof signin response missing user.current_workspace.id: {signin}')
    return token, workspace_id


def _proof_email() -> str:
    override = os.getenv('FEATURE1_PROOF_EMAIL', '').strip()
    if override:
        return override
    return f'feature1-proof+{uuid.uuid4().hex[:12]}@decoda.local'


def _create_asset_and_target(api_url: str, *, token: str, workspace_id: str, chain_id: int, contract_address: str, treasury_wallet: str, custody_wallet: str, expected_counterparty: str, allowed_spender: str) -> tuple[str, str]:
    asset_payload = {
        'name': 'Feature1 Live Proof Protected Asset',
        'asset_symbol': 'USTB',
        'asset_identifier': 'USTB-LIVE-PROOF',
        'chain_network': 'ethereum',
        'chain_id': chain_id,
        'token_contract_address': contract_address,
        'treasury_ops_wallets': [treasury_wallet],
        'custody_wallets': [custody_wallet],
        'expected_counterparties': [expected_counterparty],
        'expected_flow_patterns': [{'source_class': 'treasury_ops', 'destination_class': 'custody'}],
        'expected_approval_patterns': {'allowed_spenders': [allowed_spender], 'max_amount': 1000},
        'venue_labels': [custody_wallet],
        'expected_liquidity_baseline': {
            'baseline_outflow_volume': 1,
            'baseline_transfer_count': 1,
            'baseline_unique_counterparties': 1,
            'max_concentration_ratio': 0.95,
            'minimum_transfer_count': 1,
        },
        'oracle_sources': ['lab-oracle-a'],
        'expected_oracle_freshness_seconds': 300,
        'expected_oracle_update_cadence_seconds': 300,
        'baseline_status': 'ready',
        'baseline_confidence': 0.99,
        'baseline_coverage': 0.95,
    }
    status, asset = _request_json(f'{api_url}/assets', method='POST', payload=asset_payload, token=token, workspace_id=workspace_id)
    if status not in {200, 201}:
        raise RuntimeError(f'failed creating asset: status={status} body={asset}')
    asset_id = str(asset.get('id') or '')
    if not asset_id:
        raise RuntimeError(f'asset id missing from response: {asset}')

    target_payload = {
        'name': 'feature1-live-proof-target',
        'target_type': 'wallet',
        'chain_network': 'ethereum',
        'chain_id': chain_id,
        'wallet_address': treasury_wallet,
        'asset_id': asset_id,
        'asset_label': 'proof treasury path',
        'monitoring_enabled': True,
        'monitoring_mode': 'stream',
        'severity_threshold': 'high',
        'auto_create_alerts': True,
        'auto_create_incidents': True,
        'enabled': True,
        'is_active': True,
    }
    t_status, target = _request_json(f'{api_url}/targets', method='POST', payload=target_payload, token=token, workspace_id=workspace_id)
    if t_status not in {200, 201}:
        raise RuntimeError(f'failed creating target: status={t_status} body={target}')
    target_id = str(target.get('id') or '')
    if not target_id:
        raise RuntimeError(f'target id missing from response: {target}')
    return asset_id, target_id


def _assert_artifacts_non_empty() -> dict[str, Any]:
    summary = json.loads((ARTIFACT_DIR / 'summary.json').read_text())
    alerts = json.loads((ARTIFACT_DIR / 'alerts.json').read_text())
    runs = json.loads((ARTIFACT_DIR / 'runs.json').read_text())
    incidents = json.loads((ARTIFACT_DIR / 'incidents.json').read_text())
    evidence = json.loads((ARTIFACT_DIR / 'evidence.json').read_text())

    def _require(condition: bool, message: str) -> None:
        if not condition:
            raise RuntimeError(message)

    _require(summary.get('status') != 'asset_configuration_incomplete', f"invalid summary.status={summary.get('status')}")
    _require(bool(summary.get('worker_monitoring_executed')), 'worker_monitoring_executed=false')
    _require(bool(summary.get('lifecycle_checks_executed')), 'lifecycle_checks_executed=false')
    _require(bool(summary.get('anomalies_observed')), 'anomalies_observed=false')
    _require(isinstance(alerts, list) and bool(alerts), 'alerts.json must be non-empty')
    _require(isinstance(runs, list) and bool(runs), 'runs.json must be non-empty')
    _require(isinstance(incidents, list) and bool(incidents), 'incidents.json must be non-empty')
    _require(isinstance(evidence, list) and bool(evidence), 'evidence.json must be non-empty')

    tx_evidence_found = False
    for item in evidence:
        if not isinstance(item, dict):
            continue
        if item.get('tx_hash') and item.get('block_number') is not None:
            tx_evidence_found = True
            break
        snapshot = item.get('normalized_event_snapshot') if isinstance(item.get('normalized_event_snapshot'), dict) else {}
        if snapshot.get('tx_hash') and snapshot.get('block_number') is not None:
            tx_evidence_found = True
            break
    _require(tx_evidence_found, 'evidence.json missing tx_hash/block_number linked evidence')
    report_path = ARTIFACT_DIR / 'report.md'
    _require(report_path.exists(), 'report.md missing')
    _require(bool(report_path.read_text().strip()), 'report.md must be non-empty')

    high_or_critical_alerts = [
        item
        for item in alerts
        if isinstance(item, dict)
        and str(item.get('severity') or '').lower() in {'high', 'critical'}
    ]
    if high_or_critical_alerts:
        _require(len(incidents) >= 1, 'high/critical alerts observed but incidents.json is empty')
    return {
        'summary': summary,
        'alerts_count': len(alerts),
        'runs_count': len(runs),
        'incidents_count': len(incidents),
        'evidence_count': len(evidence),
    }


def _resolve_evm_command(port: int) -> list[str]:
    override = os.getenv('FEATURE1_EVM_CMD', '').strip()
    if override:
        return override.split()
    ganache = which('ganache')
    if ganache:
        return [ganache, '--wallet.deterministic', '--chain.chainId', '1', '--server.port', str(port)]
    anvil = which('anvil')
    if anvil:
        return [anvil, '--chain-id', '1', '--port', str(port)]
    raise RuntimeError(
        'No local EVM executable found. Install `ganache` or `anvil`, or set FEATURE1_EVM_CMD to a custom command.'
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Run deterministic local Feature 1 live proof using worker monitoring path.')
    parser.add_argument('--api-port', type=int, default=8000)
    parser.add_argument('--evm-port', type=int, default=8545)
    parser.add_argument('--telemetry-port', type=int, default=8011)
    parser.add_argument('--skip-compose', action='store_true', help='Do not run docker compose for postgres/redis.')
    parser.add_argument('--skip-api-start', action='store_true', help='Use an already running API process.')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    _require_cmd('npm')

    api_url = f'http://127.0.0.1:{args.api_port}'
    rpc_url = f'http://127.0.0.1:{args.evm_port}'
    telemetry_url = f'http://127.0.0.1:{args.telemetry_port}'

    env = os.environ.copy()
    env.setdefault('PYTHONPATH', str(REPO_ROOT))
    env.setdefault('APP_MODE', 'local')
    env.setdefault('APP_ENV', 'development')
    env.setdefault('LIVE_MODE_ENABLED', 'true')
    env.setdefault('MONITORING_MODE', 'live')
    env.setdefault('LIVE_MONITORING_ENABLED', 'true')
    env.setdefault('LIVE_MONITORING_CHAINS', 'ethereum')
    env.setdefault('EVM_RPC_URL', rpc_url)
    env.setdefault('EVM_CONFIRMATIONS_REQUIRED', '0')
    env.setdefault('EVM_BLOCK_LOOKBACK', '50')
    env.setdefault('EVM_BLOCK_SCAN_CHUNK_SIZE', '20')
    env.setdefault('MARKET_TELEMETRY_SOURCE_URLS', f'proof-market={telemetry_url}/market/observations')
    env.setdefault('ORACLE_API_URL', telemetry_url)
    env.setdefault('RUN_MIGRATIONS_ON_STARTUP', 'true')
    env.setdefault('DATABASE_URL', 'postgresql://postgres:postgres@127.0.0.1:5432/treasury')
    env.setdefault('FEATURE1_API_URL', api_url)
    env.setdefault('FEATURE1_EVIDENCE_DIR', str(ARTIFACT_DIR))
    env.setdefault('AUTH_EXPOSE_DEBUG_TOKENS', 'true')
    env.setdefault('AUTH_TOKEN_SECRET', 'feature1-local-proof-secret')
    env.setdefault('SECRET_ENCRYPTION_KEY', 'feature1-local-proof-encryption-key-32')
    env.setdefault('EMAIL_PROVIDER', 'console')
    env.setdefault('EMAIL_FROM', 'proof@decoda.local')
    env.setdefault('REDIS_URL', 'redis://127.0.0.1:6379/0')

    processes: list[tuple[str, Any]] = []
    try:
        if not args.skip_compose:
            if which('docker') is None:
                raise RuntimeError('docker is required unless --skip-compose is passed')
            subprocess.run(['docker', 'compose', 'up', '-d', 'postgres', 'redis'], cwd=str(REPO_ROOT), check=True)

        telemetry_script = REPO_ROOT / 'services' / 'api' / 'tests' / 'fixtures' / 'feature1_live_proof_telemetry_server.py'
        evm_cmd = _resolve_evm_command(args.evm_port)
        tele_cmd = [sys.executable, str(telemetry_script), '--port', str(args.telemetry_port)]
        processes.append(('evm', subprocess.Popen(evm_cmd, cwd=str(REPO_ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)))
        processes.append(('telemetry', subprocess.Popen(tele_cmd, cwd=str(REPO_ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)))
        if not args.skip_api_start:
            api_cmd = [sys.executable, 'scripts/run_service.py', 'api', '--host', '127.0.0.1', '--port', str(args.api_port)]
            processes.append(('api', subprocess.Popen(api_cmd, cwd=str(REPO_ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)))

        _wait_for_http(f'{api_url}/ops/monitoring/runtime-status', timeout_seconds=90)

        chain_id = int(_rpc_call(rpc_url, 'eth_chainId', []), 16)
        accounts = _rpc_call(rpc_url, 'eth_accounts', [])
        if not isinstance(accounts, list) or len(accounts) < 4:
            raise RuntimeError(f'ganache did not expose deterministic accounts: {accounts}')
        treasury = str(accounts[0]).lower()
        custody = str(accounts[1]).lower()
        expected_counterparty = str(accounts[2]).lower()
        unexpected_counterparty = str(accounts[3]).lower()
        proof_email = _proof_email()
        token, workspace_id = _bootstrap_auth(api_url, email=proof_email, password='ProofPass123!')

        _asset_id, target_id = _create_asset_and_target(
            api_url,
            token=token,
            workspace_id=workspace_id,
            chain_id=chain_id,
            contract_address='0x' + 'a' * 40,
            treasury_wallet=treasury,
            custody_wallet=custody,
            expected_counterparty=expected_counterparty,
            allowed_spender=expected_counterparty,
        )

        tx_hash = _rpc_call(
            rpc_url,
            'eth_sendTransaction',
            [{'from': treasury, 'to': unexpected_counterparty, 'value': hex(2_000_000_000_000_000_000)}],
        )
        _ = _rpc_call(rpc_url, 'evm_mine', [])

        worker_cmd = [
            sys.executable,
            '-m',
            'services.api.app.run_monitoring_worker',
            '--once',
            '--worker-name',
            'feature1-live-proof-worker',
            '--limit',
            '100',
        ]
        subprocess.run(worker_cmd, cwd=str(REPO_ROOT), env=env, check=True)

        evidence_cmd = [
            sys.executable,
            'services/api/scripts/run_feature1_real_asset_evidence.py',
            '--api-url',
            api_url,
            '--token',
            token,
            '--workspace-id',
            workspace_id,
            '--target-id',
            target_id,
        ]
        subprocess.run(evidence_cmd, cwd=str(REPO_ROOT), env=env, check=True)
        stats = _assert_artifacts_non_empty()
        print(
            json.dumps(
                {
                    'generated_at': _now_iso(),
                    'status': 'feature1_live_proof_completed',
                    'api_url': api_url,
                    'rpc_url': rpc_url,
                    'workspace_id': workspace_id,
                    'target_id': target_id,
                    'anomalous_tx_hash': tx_hash,
                    **stats,
                },
                indent=2,
            )
        )
        return 0
    finally:
        for name, proc in reversed(processes):
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    proc.kill()
            if proc.stdout:
                output = proc.stdout.read()[-3000:]
                if output.strip():
                    print(f'--- {name} tail ---\n{output}\n--- end {name} tail ---', file=sys.stderr)


if __name__ == '__main__':
    raise SystemExit(main())
