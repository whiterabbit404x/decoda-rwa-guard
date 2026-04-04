from __future__ import annotations

from services.api.app.monitoring_runner import _load_target_asset_context


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
    context = _load_target_asset_context(_Conn(row), workspace_id='w1', target={'asset_id': 'a1'})
    assert context is not None
    assert context['treasury_ops_wallets'] == []
    assert context['custody_wallets'] == []
    assert context['oracle_sources'] == []
    assert context['venue_labels'] == []
    assert context['expected_flow_patterns'] == []
    assert context['expected_approval_patterns'] == {}
    assert context['expected_oracle_update_cadence_seconds'] == 60
