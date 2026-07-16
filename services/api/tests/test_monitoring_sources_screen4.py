"""
Screen 4 (Monitoring Sources) regression tests.

Covers two behaviours the production rebuild depends on:

1. Orphan-target relink must identify the asset by contract address + chain and must
   NEVER link a USDC target to an unrelated treasury asset via a first-row fallback.
2. `/monitoring/sources` enrichment must derive every source/provider/agent value from
   canonical backend records (targets, monitored_systems, monitor_checkpoint,
   provider_health_records, target_coverage_records) — no invented health values.
"""
from __future__ import annotations

from datetime import datetime, timezone

from services.api.app import pilot


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


# ---------------------------------------------------------------------------
# 1. Relink correctness
# ---------------------------------------------------------------------------
def test_relink_matches_by_token_contract_address():
    """A contract target links to the asset carrying the same token_contract_address."""
    linked: list[str] = []

    class _Conn:
        def execute(self, query, params=None):
            q = ' '.join(str(query).split()).upper()
            if 'FROM TARGETS' in q and 'DELETED_AT IS NULL' in q:
                return _Result([{
                    'id': 't_usdc', 'name': 'Base USDC monitor', 'target_type': 'contract',
                    'chain_network': 'base', 'contract_identifier': '0x833589fcd6edb6e08f4c7c32d4f71b54bda02913',
                    'wallet_address': None,
                }])
            # Chain-specific asset search matches on token_contract_address column.
            if 'FROM ASSETS' in q and 'LOWER(CHAIN_NETWORK)' in q:
                return _Result([{'id': 'a_usdc', 'name': 'USDC'}])
            if 'UPDATE TARGETS' in q:
                linked.append(str(params[0]) if params else '')
                return _Result([])
            return _Result([])

    result = pilot._try_relink_orphan_target(_Conn(), target_id='t_usdc', workspace_id='ws1', user_id='u1')
    assert result['status'] == 'relinked'
    assert result['asset_id'] == 'a_usdc'
    assert linked == ['a_usdc']


def test_relink_never_links_usdc_to_unrelated_treasury():
    """A USDC target with no matching asset must CREATE its own asset, never adopt Treasury."""
    treasury_asset_id = 'a_treasury_0000'
    updated_asset_ids: list[str] = []
    created_asset = {'called': False, 'id': None}

    class _Conn:
        def execute(self, query, params=None):
            q = ' '.join(str(query).split()).upper()
            if 'FROM TARGETS' in q and 'DELETED_AT IS NULL' in q:
                return _Result([{
                    'id': 't_usdc', 'name': 'Base USDC monitor', 'target_type': 'contract',
                    'chain_network': 'base', 'contract_identifier': '0x833589fcd6edb6e08f4c7c32d4f71b54bda02913',
                    'wallet_address': None,
                }])
            # No asset matches the USDC address on any chain (only a Treasury asset exists).
            if 'FROM ASSETS' in q and ('LOWER(IDENTIFIER)' in q or 'LOWER(CHAIN_NETWORK)' in q):
                return _Result([])
            if 'INSERT INTO ASSETS' in q:
                created_asset['called'] = True
                created_asset['id'] = str(params[0]) if params else None
                return _Result([])
            if 'UPDATE TARGETS' in q:
                updated_asset_ids.append(str(params[0]) if params else '')
                return _Result([])
            # The single-workspace fallback query must never be reached for an identifier-bearing
            # target, but if it is, it would return the treasury asset — assert we never link it.
            if 'FROM ASSETS' in q and 'ORDER BY CREATED_AT' in q:
                return _Result([{'id': treasury_asset_id, 'name': 'US Treasury Settlement Contract', 'chain_network': 'ethereum'}])
            return _Result([])

    result = pilot._try_relink_orphan_target(_Conn(), target_id='t_usdc', workspace_id='ws1', user_id='u1')
    assert result['status'] == 'created', f'expected created, got {result}'
    assert created_asset['called'] is True
    # The target is linked to the newly created asset, and the treasury id is never used.
    assert updated_asset_ids and updated_asset_ids[-1] == created_asset['id']
    assert treasury_asset_id not in updated_asset_ids


def test_single_asset_fallback_skips_cross_chain():
    """A no-identifier target must not adopt the sole workspace asset on a different chain."""
    updated: list[str] = []

    class _Conn:
        def execute(self, query, params=None):
            q = ' '.join(str(query).split()).upper()
            if 'FROM TARGETS' in q and 'DELETED_AT IS NULL' in q:
                return _Result([{
                    'id': 't_noid', 'name': '', 'target_type': 'contract',
                    'chain_network': 'base', 'contract_identifier': None, 'wallet_address': None,
                }])
            if 'FROM ASSETS' in q and 'LOWER(NAME)' in q:
                return _Result([])
            # Sole workspace asset is on ethereum, target is on base → must NOT link.
            if 'FROM ASSETS' in q and 'ORDER BY CREATED_AT' in q:
                return _Result([{'id': 'a_eth', 'name': 'Ethereum Asset', 'chain_network': 'ethereum'}])
            if 'UPDATE TARGETS' in q:
                updated.append(str(params[0]) if params else '')
                return _Result([])
            return _Result([])

    result = pilot._try_relink_orphan_target(_Conn(), target_id='t_noid', workspace_id='ws1', user_id='u1')
    assert result['status'] == 'multiple_candidates', f'expected multiple_candidates, got {result}'
    assert updated == [], 'must not relink across mismatched chains'


def test_single_asset_fallback_links_same_chain():
    """A no-identifier target still adopts the sole workspace asset when the chain matches."""
    updated: list[str] = []

    class _Conn:
        def execute(self, query, params=None):
            q = ' '.join(str(query).split()).upper()
            if 'FROM TARGETS' in q and 'DELETED_AT IS NULL' in q:
                return _Result([{
                    'id': 't_noid', 'name': '', 'target_type': 'contract',
                    'chain_network': 'base', 'contract_identifier': None, 'wallet_address': None,
                }])
            if 'FROM ASSETS' in q and 'LOWER(NAME)' in q:
                return _Result([])
            if 'FROM ASSETS' in q and 'ORDER BY CREATED_AT' in q:
                return _Result([{'id': 'a_base', 'name': 'Base Asset', 'chain_network': 'base'}])
            if 'UPDATE TARGETS' in q:
                updated.append(str(params[0]) if params else '')
                return _Result([])
            return _Result([])

    result = pilot._try_relink_orphan_target(_Conn(), target_id='t_noid', workspace_id='ws1', user_id='u1')
    assert result['status'] == 'relinked'
    assert result['asset_id'] == 'a_base'
    assert updated == ['a_base']


# ---------------------------------------------------------------------------
# 2. Enriched /monitoring/sources payload
# ---------------------------------------------------------------------------
def _enrichment_conn(workspace_id: str, canonical_id: str, now: datetime):
    class _Conn:
        def execute(self, query, params=None):
            q = ' '.join(str(query).split()).upper()
            if 'FROM MONITOR_CHECKPOINT' in q:
                return _Result([{'monitored_system_id': 'sys1', 'latest_block': 8453123}])
            if 'FROM PROVIDER_HEALTH_RECORDS' in q:
                return _Result([{
                    'target_id': canonical_id, 'status': 'healthy', 'latency_ms': 42,
                    'checked_at': now, 'evidence_source': 'live',
                    'provider_type': 'base-mainnet.g.alchemy.com', 'error_message': None,
                }])
            if 'FROM TARGET_COVERAGE_RECORDS' in q:
                return _Result([{
                    'target_id': canonical_id, 'coverage_status': 'reporting',
                    'last_poll_at': now, 'last_heartbeat_at': now, 'last_telemetry_at': now,
                    'last_detection_at': None, 'evidence_source': 'live', 'computed_at': now,
                }])
            return _Result([])

    return _Conn()


def test_enrichment_derives_source_provider_and_agent_from_records():
    workspace_id = 'ws1'
    target_id = 't1'
    canonical_id = pilot._canonical_target_uuid(workspace_id, target_id)
    now = datetime.now(timezone.utc)

    targets = [{
        'id': target_id, 'name': 'Base USDC monitor', 'target_type': 'contract',
        'chain_network': 'base', 'chain_id': 8453,
        'contract_identifier': '0x833589fcd6edb6e08f4c7c32d4f71b54bda02913', 'wallet_address': None,
        'asset_id': 'a_usdc', 'asset_name': 'USDC', 'asset_missing': False,
        'monitoring_mode': 'poll', 'monitoring_enabled': True, 'enabled': True,
        'monitored_system_id': 'sys1',
        'target_metadata': {'rpc_sources': {'primary_host': 'base-mainnet.g.alchemy.com',
                                            'fallback_host': 'base.publicnode.com',
                                            'explanation': 'Selected as primary (lowest latency).'}},
    }]
    systems = [{
        'id': 'sys1', 'target_id': target_id, 'asset_id': 'a_usdc', 'chain': 'base',
        'is_enabled': True, 'runtime_status': 'healthy', 'last_heartbeat': now.isoformat(),
        'last_event_at': now.isoformat(), 'coverage_reason': None, 'freshness_status': 'fresh',
        'asset_name': 'USDC', 'target_name': 'Base USDC monitor',
    }]

    enrichment = pilot._build_monitoring_sources_enrichment(
        _enrichment_conn(workspace_id, canonical_id, now),
        workspace_id=workspace_id, assets=[{'id': 'a_usdc', 'name': 'USDC'}],
        targets=targets, systems=systems,
    )

    assert len(enrichment['sources']) == 1
    source = enrichment['sources'][0]
    assert source['asset_name'] == 'USDC'
    assert source['network'] == 'base'
    assert source['chain_id'] == 8453
    assert source['address'] == '0x833589fcd6edb6e08f4c7c32d4f71b54bda02913'
    assert source['provider'] == 'base-mainnet.g.alchemy.com'
    assert source['status'] == 'healthy'
    assert source['latest_block'] == 8453123
    assert source['median_latency_ms'] == 42
    assert source['routing'] == 'primary'
    assert source['coverage_state'] == 'reporting'
    # Block lag must not be fabricated without a live chain head.
    assert source['block_lag'] is None

    ph = enrichment['provider_health']
    assert ph['total'] == 1
    assert ph['healthy_count'] == 1
    assert ph['providers'][0]['host'] == 'base-mainnet.g.alchemy.com'

    agent = enrichment['agent']
    assert agent['state'] == 'monitoring'
    assert agent['missing_target_links'] == 0
    assert agent['primary_provider'] == 'base-mainnet.g.alchemy.com'
    assert agent['recommended_fallback'] == 'base.publicnode.com'
    assert agent['confidence'] == 'high'


def test_enrichment_flags_missing_asset_link_without_inventing_health():
    """A target whose asset is missing is 'missing_configuration' and drives agent attention."""
    workspace_id = 'ws1'
    target_id = 't2'
    canonical_id = pilot._canonical_target_uuid(workspace_id, target_id)
    now = datetime.now(timezone.utc)

    targets = [{
        'id': target_id, 'name': 'Orphan monitor', 'target_type': 'contract',
        'chain_network': 'base', 'chain_id': 8453,
        'contract_identifier': '0xabc', 'wallet_address': None,
        'asset_id': None, 'asset_name': None, 'asset_missing': True,
        'monitoring_mode': 'poll', 'monitoring_enabled': True, 'enabled': True,
        'monitored_system_id': None, 'target_metadata': {},
    }]

    class _Conn:
        def execute(self, query, params=None):
            return _Result([])

    enrichment = pilot._build_monitoring_sources_enrichment(
        _Conn(), workspace_id=workspace_id, assets=[], targets=targets, systems=[],
    )
    source = enrichment['sources'][0]
    assert source['status'] == 'missing_configuration'
    assert source['provider'] is None
    assert source['median_latency_ms'] is None
    assert enrichment['agent']['state'] == 'attention_required'
    assert enrichment['agent']['missing_target_links'] == 1
    # Confidence must degrade, never assert health on missing data.
    assert enrichment['agent']['confidence'] in {'low', 'unavailable'}
