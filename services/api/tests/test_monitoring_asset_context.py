from __future__ import annotations

from services.api.app.monitoring_runner import _build_protected_asset_context, _load_target_asset_context, _provider_coverage_status


class _Result:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _Conn:
    def __init__(self, row):
        self.row = row

    def execute(self, *_args, **_kwargs):
        return _Result(self.row)


def test_load_target_asset_context_normalizes_required_fields() -> None:
    row = {
        'id': 'a1',
        'name': 'asset',
        'asset_class': 'rwa',
        'asset_symbol': 'USTB',
        'identifier': 'asset-1',
        'asset_identifier': 'asset-1',
        'token_contract_address': '0xabc',
        'treasury_ops_wallets': None,
        'custody_wallets': None,
        'oracle_sources': None,
        'venue_labels': None,
        'expected_flow_patterns': None,
        'expected_counterparties': None,
        'expected_approval_patterns': None,
        'expected_liquidity_baseline': None,
        'expected_oracle_freshness_seconds': 30,
        'expected_oracle_update_cadence_seconds': 60,
        'baseline_status': 'observed',
        'baseline_source': 'live',
        'baseline_updated_at': None,
        'baseline_confidence': 0.9,
        'baseline_coverage': 0.8,
    }
    context = _load_target_asset_context(_Conn(row), workspace_id='w1', target={'asset_id': 'a1', 'chain_id': 1})
    assert context is not None
    assert context['treasury_ops_wallets'] == []
    assert context['custody_wallets'] == []
    assert context['oracle_sources'] == []
    assert context['venue_labels'] == []
    assert context['expected_flow_patterns'] == []
    assert context['expected_approval_patterns'] == {}
    assert context['expected_oracle_update_cadence_seconds'] == 60
    assert context['chain_id'] == 1
    assert context['identifier'] == 'asset-1'
    assert context['asset_identifier'] == 'asset-1'
    assert context['asset_id'] == 'a1'
    assert context['symbol'] == 'USTB'
    assert context['contract_address'] == '0xabc'


def test_protected_asset_context_contract_and_coverage_fail_closed() -> None:
    asset = {
        'id': 'a1',
        'asset_identifier': 'USTB',
        'asset_symbol': 'USTB',
        'chain_id': 1,
        'token_contract_address': '0xabc',
        'treasury_ops_wallets': ['0x1'],
        'custody_wallets': ['0x2'],
        'expected_counterparties': ['0x3'],
        'expected_flow_patterns': [{'source_class': 'treasury_ops', 'destination_class': 'custody'}],
        'expected_approval_patterns': {'allowed_spenders': ['0x4']},
        'expected_liquidity_baseline': {'baseline_outflow_volume': 100},
        'oracle_sources': ['oracle-a'],
        'expected_oracle_freshness_seconds': 30,
        'expected_oracle_update_cadence_seconds': 30,
        'venue_labels': ['0x5'],
        'baseline_status': 'ready',
        'baseline_confidence': 0.9,
        'baseline_coverage': 0.8,
    }
    context = _build_protected_asset_context(asset)
    assert context['contract_complete'] is True
    coverage = _provider_coverage_status(
        event_payload={'market_observations': [], 'oracle_observations': []},
        protected_asset_context=context,
    )
    assert coverage['enterprise_claim_eligibility'] is False
    assert coverage['market_coverage_status'] == 'insufficient_real_evidence'
    assert coverage['oracle_coverage_status'] == 'insufficient_real_evidence'
    assert coverage['provider_coverage_status']['market_claim_eligible'] is False
    assert coverage['provider_coverage_status']['oracle_claim_eligible'] is False
    assert 'market_provider_not_configured_or_no_observation' in coverage['claim_ineligibility_reasons']
