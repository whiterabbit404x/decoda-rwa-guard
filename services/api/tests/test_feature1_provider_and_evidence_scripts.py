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
    assert evidence[0]['record_type'] == 'coverage_evaluation'
    assert 'protected_asset_context' in evidence[0]


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


def test_feature1_evidence_script_confirms_live_coverage_with_real_provider_observations(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('FEATURE1_EVIDENCE_DIR', str(tmp_path / 'evidence'))

    def _mock_request(url: str, **kwargs):  # noqa: ANN003,ANN202
        path = url.replace('http://127.0.0.1:8000', '')
        if path == '/ops/monitoring/runtime-status':
            return 200, {'configured_mode': 'LIVE'}
        if path == '/targets':
            return 200, {'targets': [{'id': 't1', 'asset_id': 'a1', 'name': 'Treasury', 'target_type': 'wallet', 'chain_network': 'ethereum'}]}
        if path == '/ops/monitoring/run':
            return 200, {'run_id': 'run-1'}
        if path == '/alerts?target_id=t1':
            return 200, {'alerts': []}
        if path == '/incidents?target_id=t1':
            return 200, {'incidents': []}
        if path == '/pilot/history?kind=analysis_runs':
            return 200, {'analysis_runs': [{
                'id': 'r1',
                'target_id': 't1',
                'response_payload': {
                    'monitoring_path': 'worker',
                    'protected_asset_coverage_record': {
                        'protected_asset_context': {
                            'asset_id': 'a1',
                            'asset_identifier': 'USTB-REAL',
                            'symbol': 'USTB',
                            'chain_id': 1,
                            'contract_address': '0x' + 'a' * 40,
                            'treasury_ops_wallets': ['0x' + '1' * 40],
                            'custody_wallets': ['0x' + '2' * 40],
                            'expected_flow_patterns': [{'source_class': 'treasury_ops'}],
                            'expected_counterparties': ['0x' + '3' * 40],
                            'expected_approval_patterns': {'allowed_spenders': ['0x' + '4' * 40]},
                            'venue_labels': ['venue-a'],
                            'expected_liquidity_baseline': {'minimum_transfer_count': 1},
                            'oracle_sources': ['oracle-a'],
                            'expected_oracle_freshness_seconds': 120,
                            'expected_oracle_update_cadence_seconds': 120,
                        },
                        'market_coverage_status': 'real_external_market_observation',
                        'oracle_coverage_status': 'real_oracle_observations_present',
                        'market_provider_count': 1,
                        'market_provider_reachable_count': 1,
                        'market_provider_fresh_count': 1,
                        'market_provider_names': ['market-a'],
                        'market_observation_count': 2,
                        'oracle_provider_count': 1,
                        'oracle_provider_reachable_count': 1,
                        'oracle_provider_fresh_count': 1,
                        'oracle_provider_names': ['oracle-a'],
                        'oracle_observation_count': 2,
                        'market_claim_eligible': True,
                        'oracle_claim_eligible': True,
                        'enterprise_claim_eligibility': True,
                        'claim_ineligibility_reasons': [],
                    },
                },
            }]}
        return 404, {}

    monkeypatch.setattr(run_feature1_real_asset_evidence, '_request_json', _mock_request)
    monkeypatch.setattr('sys.argv', ['run_feature1_real_asset_evidence.py'])
    code = run_feature1_real_asset_evidence.main()
    assert code == 0
    summary = json.loads((tmp_path / 'evidence' / 'summary.json').read_text())
    assert summary['status'] == 'live_coverage_confirmed'
    assert summary['target_identity']['target_id'] == 't1'
    assert summary['target_identity']['target_name_or_label'] == 'Treasury'
    assert summary['enterprise_claim_eligibility'] is True
    assert summary['external_market_telemetry_present'] is True
    assert summary['real_oracle_observations_present'] is True
    assert summary['worker_monitoring_executed'] is True
    assert summary['lifecycle_checks_executed'] is True
    evidence = json.loads((tmp_path / 'evidence' / 'evidence.json').read_text())
    assert evidence and evidence[0]['record_type'] == 'coverage_evaluation'


def test_feature1_evidence_script_marks_monitoring_execution_failed_when_run_request_fails(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('FEATURE1_EVIDENCE_DIR', str(tmp_path / 'evidence'))
    monkeypatch.setattr(
        run_feature1_real_asset_evidence,
        '_request_json',
        lambda url, **kwargs: (
            500 if url.endswith('/ops/monitoring/run') else 200,
            {
                '/ops/monitoring/runtime-status': {'configured_mode': 'LIVE'},
                '/targets': {'targets': [{'id': 't1', 'asset_id': 'a1', 'name': 'Treasury', 'target_type': 'wallet', 'chain_network': 'ethereum'}]},
                '/ops/monitoring/run': {'run_id': 'run-1'},
                '/alerts?target_id=t1': {'alerts': []},
                '/incidents?target_id=t1': {'incidents': []},
                '/pilot/history?kind=analysis_runs': {'analysis_runs': []},
            }.get(url.replace('http://127.0.0.1:8000', ''), {}),
        ),
    )
    monkeypatch.setattr('sys.argv', ['run_feature1_real_asset_evidence.py'])
    code = run_feature1_real_asset_evidence.main()
    assert code == 2
    summary = json.loads((tmp_path / 'evidence' / 'summary.json').read_text())
    assert summary['status'] == 'monitoring_execution_failed'
    assert summary['reason'] == 'monitoring_run_request_failed'
    assert 'inconclusive' not in json.dumps(summary).lower()
    assert 'dry_run' not in json.dumps(summary).lower()


def test_feature1_evidence_script_marks_monitoring_execution_failed_when_worker_runs_missing_after_attempt(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('FEATURE1_EVIDENCE_DIR', str(tmp_path / 'evidence'))

    def _mock_request(url: str, **kwargs):  # noqa: ANN003,ANN202
        path = url.replace('http://127.0.0.1:8000', '')
        if path == '/ops/monitoring/runtime-status':
            return 200, {'configured_mode': 'LIVE'}
        if path == '/targets':
            return 200, {'targets': [{'id': 't1', 'asset_id': 'a1', 'name': 'Treasury', 'target_type': 'wallet', 'chain_network': 'ethereum', 'wallet_address': '0x' + '1' * 40}]}
        if path == '/assets':
            return 200, {'assets': [{
                'id': 'a1',
                'asset_identifier': 'USTB-REAL',
                'asset_symbol': 'USTB',
                'chain_id': 1,
                'token_contract_address': '0x' + 'a' * 40,
                'treasury_ops_wallets': ['0x' + '1' * 40],
                'custody_wallets': ['0x' + '2' * 40],
                'expected_flow_patterns': [{'source_class': 'treasury_ops', 'destination_class': 'custody'}],
                'expected_counterparties': ['0x' + '3' * 40],
                'expected_approval_patterns': {'allowed_spenders': ['0x' + '4' * 40]},
                'venue_labels': ['venue-a'],
                'expected_liquidity_baseline': {'minimum_transfer_count': 1},
                'oracle_sources': ['oracle-a'],
                'expected_oracle_freshness_seconds': 120,
                'expected_oracle_update_cadence_seconds': 120,
            }]}
        if path == '/ops/monitoring/run':
            return 200, {'run_id': 'run-1'}
        if path == '/alerts?target_id=t1':
            return 200, {'alerts': []}
        if path == '/incidents?target_id=t1':
            return 200, {'incidents': []}
        if path == '/pilot/history?kind=analysis_runs':
            return 200, {'analysis_runs': []}
        return 404, {}

    monkeypatch.setattr(run_feature1_real_asset_evidence, '_request_json', _mock_request)
    monkeypatch.setattr('sys.argv', ['run_feature1_real_asset_evidence.py'])
    code = run_feature1_real_asset_evidence.main()
    assert code == 2
    summary = json.loads((tmp_path / 'evidence' / 'summary.json').read_text())
    assert summary['status'] == 'monitoring_execution_failed'
    assert summary['reason'] == 'worker_monitoring_not_executed'
    assert summary['target_identity']['target_id'] == 't1'
    assert summary['target_identity']['target_locator']
    assert summary['missing_asset_context_fields'] == []


def test_feature1_evidence_script_denies_live_coverage_without_provider_observations(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('FEATURE1_EVIDENCE_DIR', str(tmp_path / 'evidence'))

    def _mock_request(url: str, **kwargs):  # noqa: ANN003,ANN202
        path = url.replace('http://127.0.0.1:8000', '')
        if path == '/ops/monitoring/runtime-status':
            return 200, {'configured_mode': 'LIVE'}
        if path == '/targets':
            return 200, {'targets': [{'id': 't1', 'asset_id': 'a1', 'name': 'Treasury', 'target_type': 'wallet', 'chain_network': 'ethereum'}]}
        if path == '/ops/monitoring/run':
            return 200, {'run_id': 'run-1'}
        if path == '/alerts?target_id=t1':
            return 200, {'alerts': []}
        if path == '/incidents?target_id=t1':
            return 200, {'incidents': []}
        if path == '/pilot/history?kind=analysis_runs':
            return 200, {'analysis_runs': [{
                'id': 'r1',
                'target_id': 't1',
                'response_payload': {
                    'monitoring_path': 'worker',
                    'protected_asset_coverage_record': {
                        'protected_asset_context': {
                            'asset_id': 'a1',
                            'asset_identifier': 'USTB-REAL',
                            'symbol': 'USTB',
                            'chain_id': 1,
                            'contract_address': '0x' + 'a' * 40,
                            'treasury_ops_wallets': ['0x' + '1' * 40],
                            'custody_wallets': ['0x' + '2' * 40],
                            'expected_flow_patterns': [{'source_class': 'treasury_ops'}],
                            'expected_counterparties': ['0x' + '3' * 40],
                            'expected_approval_patterns': {'allowed_spenders': ['0x' + '4' * 40]},
                            'venue_labels': ['venue-a'],
                            'expected_liquidity_baseline': {'minimum_transfer_count': 1},
                            'oracle_sources': ['oracle-a'],
                            'expected_oracle_freshness_seconds': 120,
                            'expected_oracle_update_cadence_seconds': 120,
                        },
                        'market_coverage_status': 'insufficient_real_evidence',
                        'oracle_coverage_status': 'insufficient_real_evidence',
                        'market_provider_names': ['market-a'],
                        'oracle_provider_names': ['oracle-a'],
                        'market_observation_count': 0,
                        'oracle_observation_count': 0,
                        'market_claim_eligible': False,
                        'oracle_claim_eligible': False,
                        'enterprise_claim_eligibility': False,
                        'claim_ineligibility_reasons': ['insufficient_market_observations', 'insufficient_oracle_observations'],
                    },
                },
            }]}
        return 404, {}

    monkeypatch.setattr(run_feature1_real_asset_evidence, '_request_json', _mock_request)
    monkeypatch.setattr('sys.argv', ['run_feature1_real_asset_evidence.py'])
    code = run_feature1_real_asset_evidence.main()
    assert code == 0
    summary = json.loads((tmp_path / 'evidence' / 'summary.json').read_text())
    assert summary['status'] == 'live_coverage_denied'
    assert summary['enterprise_claim_eligibility'] is False
    assert 'missing_real_provider_observations' in summary['claim_ineligibility_reasons']
    assert summary['target_identity']['target_id'] == 't1'
    assert summary['worker_monitoring_executed'] is True
    assert summary['lifecycle_checks_executed'] is True


def test_feature1_evidence_script_marks_missing_oracle_timing_fields_ineligible(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('FEATURE1_EVIDENCE_DIR', str(tmp_path / 'evidence'))

    def _mock_request(url: str, **kwargs):  # noqa: ANN003,ANN202
        path = url.replace('http://127.0.0.1:8000', '')
        if path == '/ops/monitoring/runtime-status':
            return 200, {'configured_mode': 'LIVE'}
        if path == '/targets':
            return 200, {'targets': [{'id': 't1', 'asset_id': 'a1', 'name': 'Treasury', 'target_type': 'wallet', 'chain_network': 'ethereum'}]}
        if path == '/ops/monitoring/run':
            return 200, {'run_id': 'run-1'}
        if path == '/alerts?target_id=t1':
            return 200, {'alerts': []}
        if path == '/incidents?target_id=t1':
            return 200, {'incidents': []}
        if path == '/pilot/history?kind=analysis_runs':
            return 200, {'analysis_runs': [{
                'id': 'r1',
                'target_id': 't1',
                'response_payload': {
                    'monitoring_path': 'worker',
                    'protected_asset_coverage_record': {
                        'protected_asset_context': {
                            'asset_id': 'a1',
                            'asset_identifier': 'USTB-REAL',
                            'symbol': 'USTB',
                            'chain_id': 1,
                            'contract_address': '0x' + 'a' * 40,
                            'treasury_ops_wallets': ['0x' + '1' * 40],
                            'custody_wallets': ['0x' + '2' * 40],
                            'expected_flow_patterns': [{'source_class': 'treasury_ops'}],
                            'expected_counterparties': ['0x' + '3' * 40],
                            'expected_approval_patterns': {'allowed_spenders': ['0x' + '4' * 40]},
                            'venue_labels': ['venue-a'],
                            'expected_liquidity_baseline': {'minimum_transfer_count': 1},
                            'oracle_sources': ['oracle-a'],
                        },
                    },
                },
            }]}
        return 404, {}

    monkeypatch.setattr(run_feature1_real_asset_evidence, '_request_json', _mock_request)
    monkeypatch.setattr('sys.argv', ['run_feature1_real_asset_evidence.py'])
    code = run_feature1_real_asset_evidence.main()
    assert code == 2
    summary = json.loads((tmp_path / 'evidence' / 'summary.json').read_text())
    assert summary['status'] == 'asset_configuration_incomplete'
    assert 'missing_expected_oracle_freshness_seconds' in summary['claim_ineligibility_reasons']
    assert 'missing_expected_oracle_update_cadence_seconds' in summary['claim_ineligibility_reasons']


def test_feature1_evidence_script_marks_target_identity_missing_fields_ineligible(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('FEATURE1_EVIDENCE_DIR', str(tmp_path / 'evidence'))

    def _mock_request(url: str, **kwargs):  # noqa: ANN003,ANN202
        path = url.replace('http://127.0.0.1:8000', '')
        if path == '/ops/monitoring/runtime-status':
            return 200, {'configured_mode': 'LIVE'}
        if path == '/targets':
            return 200, {'targets': [{'id': 't1', 'asset_id': 'a1', 'target_type': 'wallet', 'chain_network': 'ethereum'}]}
        if path == '/ops/monitoring/run':
            return 200, {'run_id': 'run-1'}
        if path == '/alerts?target_id=t1':
            return 200, {'alerts': []}
        if path == '/incidents?target_id=t1':
            return 200, {'incidents': []}
        if path == '/pilot/history?kind=analysis_runs':
            return 200, {'analysis_runs': [{
                'id': 'r1',
                'target_id': 't1',
                'response_payload': {
                    'monitoring_path': 'worker',
                    'protected_asset_coverage_record': {
                        'protected_asset_context': {
                            'asset_id': 'a1',
                            'asset_identifier': 'USTB-REAL',
                            'symbol': 'USTB',
                            'chain_id': 1,
                            'contract_address': '0x' + 'a' * 40,
                            'treasury_ops_wallets': ['0x' + '1' * 40],
                            'custody_wallets': ['0x' + '2' * 40],
                            'expected_flow_patterns': [{'source_class': 'treasury_ops'}],
                            'expected_counterparties': ['0x' + '3' * 40],
                            'expected_approval_patterns': {'allowed_spenders': ['0x' + '4' * 40]},
                            'venue_labels': ['venue-a'],
                            'expected_liquidity_baseline': {'minimum_transfer_count': 1},
                            'oracle_sources': ['oracle-a'],
                            'expected_oracle_freshness_seconds': 120,
                            'expected_oracle_update_cadence_seconds': 120,
                        },
                        'market_coverage_status': 'real_external_market_observation',
                        'oracle_coverage_status': 'real_oracle_observations_present',
                        'market_observation_count': 1,
                        'oracle_observation_count': 1,
                        'market_claim_eligible': True,
                        'oracle_claim_eligible': True,
                        'enterprise_claim_eligibility': True,
                        'claim_ineligibility_reasons': [],
                    },
                },
            }]}
        return 404, {}

    monkeypatch.setattr(run_feature1_real_asset_evidence, '_request_json', _mock_request)
    monkeypatch.setattr('sys.argv', ['run_feature1_real_asset_evidence.py'])
    code = run_feature1_real_asset_evidence.main()
    assert code == 2
    summary = json.loads((tmp_path / 'evidence' / 'summary.json').read_text())
    assert summary['status'] == 'asset_configuration_incomplete'
    assert 'missing_target_name_or_label' in summary['claim_ineligibility_reasons']
    assert sorted(summary['missing_target_identity_fields']) == ['target_locator', 'target_name_or_label']


def test_feature1_evidence_script_normal_mode_status_is_never_placeholder(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('FEATURE1_EVIDENCE_DIR', str(tmp_path / 'evidence'))
    monkeypatch.setattr(
        run_feature1_real_asset_evidence,
        '_request_json',
        lambda url, **kwargs: (
            200,
            {
                '/ops/monitoring/runtime-status': {'configured_mode': 'LIVE'},
                '/targets': {'targets': [{'id': 't1', 'asset_id': 'a1', 'name': 'Treasury', 'target_type': 'wallet', 'chain_network': 'ethereum'}]},
                '/assets': {'assets': [{
                    'id': 'a1',
                    'asset_identifier': 'USTB-REAL',
                    'asset_symbol': 'USTB',
                    'chain_id': 1,
                    'token_contract_address': '0x' + 'a' * 40,
                    'treasury_ops_wallets': ['0x' + '1' * 40],
                    'custody_wallets': ['0x' + '2' * 40],
                    'expected_flow_patterns': [{'source_class': 'treasury_ops', 'destination_class': 'custody'}],
                    'expected_counterparties': ['0x' + '3' * 40],
                    'expected_approval_patterns': {'allowed_spenders': ['0x' + '4' * 40]},
                    'venue_labels': ['venue-a'],
                    'expected_liquidity_baseline': {'minimum_transfer_count': 1},
                    'oracle_sources': ['oracle-a'],
                    'expected_oracle_freshness_seconds': 120,
                    'expected_oracle_update_cadence_seconds': 120,
                }]},
                '/ops/monitoring/run': {'run_id': 'run-1'},
                '/alerts?target_id=t1': {'alerts': []},
                '/incidents?target_id=t1': {'incidents': []},
                '/pilot/history?kind=analysis_runs': {'analysis_runs': []},
            }.get(url.replace('http://127.0.0.1:8000', ''), {}),
        ),
    )
    monkeypatch.setattr('sys.argv', ['run_feature1_real_asset_evidence.py'])
    run_feature1_real_asset_evidence.main()
    summary = json.loads((tmp_path / 'evidence' / 'summary.json').read_text())
    assert summary['status'] in {
        'live_coverage_confirmed',
        'live_coverage_denied',
        'asset_configuration_incomplete',
        'monitoring_execution_failed',
    }
    assert summary['status'] not in {'dry_run_requested', 'inconclusive'}
