from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from services.api.app import evm_activity_provider
from services.api.scripts import run_feature1_real_asset_evidence


class _Rpc:
    def call(self, method: str, params: list[object]) -> object:  # noqa: ANN401
        if method == 'eth_blockNumber':
            return hex(100)
        if method == 'eth_getLogs':
            return [{
                'transactionHash': '0xabc',
                'logIndex': hex(0),
                'blockNumber': hex(99),
                'blockHash': '0xblock',
                'topics': [
                    evm_activity_provider.TRANSFER_TOPIC,
                    f"0x{'0'*24}{'1'*40}",
                    f"0x{'0'*24}{'2'*40}",
                ],
                'data': hex(500),
                'address': '0x' + 'a' * 40,
            }]
        if method == 'eth_getBlockByNumber':
            return {
                'hash': '0xblock',
                'timestamp': hex(int(datetime.now(timezone.utc).timestamp())),
                'transactions': [{
                    'hash': '0xabc',
                    'from': '0x' + '1' * 40,
                    'to': '0x' + '2' * 40,
                    'value': hex(500),
                    'input': '0x23b872dd',
                    'blockHash': '0xblock',
                }],
            }
        if method == 'eth_getTransactionByHash':
            return {'hash': '0xabc', 'from': '0x' + '1' * 40, 'to': '0x' + '2' * 40, 'value': hex(500), 'input': '0x23b872dd'}
        if method == 'eth_getBlockByHash':
            return {'timestamp': hex(int(datetime.now(timezone.utc).timestamp()))}
        return {}


def test_evm_activity_provider_populates_oracle_liquidity_and_venue_observations(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'ethereum')
    monkeypatch.setattr(
        evm_activity_provider,
        '_fetch_oracle_observations',
        lambda _target: [{
            'source_name': 'chainlink',
            'source_type': 'oracle_api',
            'asset_identifier': 'USTB',
            'observed_value': 1.0,
            'observed_at': datetime.now(timezone.utc).isoformat(),
            'block_number': 100,
            'freshness_seconds': 1,
            'status': 'ok',
        }],
    )
    target = {
        'id': 'target-1',
        'target_type': 'wallet',
        'chain_network': 'ethereum',
        'wallet_address': '0x' + '2' * 40,
        'venue_labels': ['0x' + '2' * 40],
        'asset_identifier': 'USTB',
    }
    events = evm_activity_provider.fetch_evm_activity(target, None, rpc_client=_Rpc())
    assert events
    payload = events[0].payload
    assert payload['oracle_observations']
    assert payload['liquidity_observations']
    assert payload['venue_observations']


def test_feature1_evidence_script_fails_without_worker_generated_real_evidence(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('FEATURE1_EVIDENCE_DIR', str(tmp_path / 'evidence'))
    monkeypatch.setattr(
        run_feature1_real_asset_evidence,
        '_request_json',
        lambda url, **kwargs: (
            200,
            {
                '/ops/monitoring/runtime-status': {'configured_mode': 'LIVE'},
                '/targets': {'targets': [{'id': 't1', 'asset_id': 'a1', 'asset_symbol': 'USTB', 'asset_identifier': 'USTB', 'name': 'Treasury', 'target_type': 'wallet', 'chain_network': 'ethereum'}]},
                '/alerts?target_id=t1': {'alerts': []},
                '/incidents?target_id=t1': {'incidents': []},
                '/pilot/history?kind=analysis_runs': {'analysis_runs': [{'id': 'r1', 'response_payload': {'monitoring_path': 'manual_run_once'}}]},
            }.get(url.replace('http://127.0.0.1:8000', ''), {}),
        ),
    )
    monkeypatch.setattr('sys.argv', ['run_feature1_real_asset_evidence.py'])
    code = run_feature1_real_asset_evidence.main()
    assert code == 2
    summary = json.loads((tmp_path / 'evidence' / 'summary.json').read_text())
    assert summary['status'] == 'asset_configuration_incomplete'
    assert summary['enterprise_claim_eligibility'] is False
    assert summary['market_coverage_status'] == 'insufficient_real_evidence'
    assert summary['oracle_coverage_status'] == 'insufficient_real_evidence'
    assert 'dry_run' not in json.dumps(summary).lower()
    evidence = json.loads((tmp_path / 'evidence' / 'evidence.json').read_text())
    assert isinstance(evidence, list)
    assert evidence


def test_feature1_evidence_script_dry_run_exports_explicit_state(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('FEATURE1_EVIDENCE_DIR', str(tmp_path / 'evidence'))
    monkeypatch.setattr('sys.argv', ['run_feature1_real_asset_evidence.py', '--dry-run'])
    code = run_feature1_real_asset_evidence.main()
    assert code == 0
    summary = json.loads((tmp_path / 'evidence' / 'summary.json').read_text())
    assert summary['status'] == 'dry_run_requested'
    assert summary['reason'] == 'dry_run_requested'
    evidence = json.loads((tmp_path / 'evidence' / 'evidence.json').read_text())
    assert isinstance(evidence, list)
    assert evidence and evidence[0]['record_type'] == 'dry_run_requested'
