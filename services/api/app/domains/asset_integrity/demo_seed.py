"""Demo-only seeding for the Screen 3 Integrity scenario.

This module exists so a non-production demo workspace can DEMONSTRATE the
operational-integrity architecture end to end. Every row it writes is stamped
``evidence_source='simulator'``, which means:

  * the UI labels it "Simulator" instead of presenting it as live evidence,
  * the canonical event it produces is NOT alert-eligible,
  * it can never satisfy a live-evidence readiness check.

It is a hard no-op when the runtime is production, and it never overwrites an
asset's real observations: it seeds only its own dedicated demo asset and only
when that asset has no integrity rows yet.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any

from services.api.app import pilot
from services.api.app.domains.asset_integrity import config as aic
from services.api.app.domains.asset_integrity import service

logger = logging.getLogger(__name__)

# The demo asset this module owns. Keyed by normalized_identifier so it is never
# confused with a customer-registered asset.
DEMO_ASSET_IDENTIFIER = 'demo-seed-integrity-rwa'
DEMO_ASSET_NAME = 'Demo Seed Tokenized Bond'
DEMO_AUTHORITATIVE_SOURCE = 'Demo Transfer Agent (simulated)'

# The scenario: observed supply exceeds authorized issuance with no matching
# authorization -> UNEXPLAINED_VARIANCE / NO_MATCHING_AUTHORIZED_ISSUANCE.
DEMO_OBSERVED_SUPPLY = 5_000_000
DEMO_EXPECTED_SUPPLY = 4_500_000
DEMO_MINT_DELTA = 500_000


def _existing_demo_asset(connection: Any, workspace_id: str) -> str | None:
    row = connection.execute(
        '''
        SELECT id FROM assets
        WHERE workspace_id = %s AND deleted_at IS NULL AND normalized_identifier = %s
        ORDER BY created_at ASC LIMIT 1
        ''',
        (workspace_id, DEMO_ASSET_IDENTIFIER),
    ).fetchone()
    return str(row['id']) if row else None


def seed_demo_integrity_scenario(
    connection: Any,
    *,
    workspace_id: str,
    user_id: str,
    allowed: bool,
    now: Any = None,
) -> dict[str, Any]:
    """Seed the demo reconciliation scenario. No-op outside demo runtimes."""
    if not allowed:
        return {'seeded': False, 'reason': 'production_runtime'}
    for table in ('asset_onchain_supply_observations', 'asset_authoritative_state',
                  'asset_authorized_issuances', 'asset_reconciliation_snapshots'):
        if not service._table_exists(connection, table):
            return {'seeded': False, 'reason': 'schema_provisioning'}

    now = now or pilot.utc_now()
    asset_id = _existing_demo_asset(connection, workspace_id)
    if asset_id is None:
        asset_id = str(uuid.uuid4())
        connection.execute(
            '''
            INSERT INTO assets (
                id, workspace_id, name, description, asset_type, chain_network, identifier,
                normalized_identifier, verification_status, enabled, rwa_asset_type, custodian,
                token_symbol, token_contract_address, token_decimals,
                created_by_user_id, updated_by_user_id
            ) VALUES (
                %s, %s, %s,
                'Simulator-backed demo asset for the Screen 3 reconciliation scenario. Not customer evidence.',
                'contract', 'base-mainnet', %s,
                %s, 'verified', TRUE, 'tokenized_treasury', %s,
                'DSTB', '0x0000000000000000000000000000000000000002', 0,
                %s, %s
            )
            ''',
            (
                asset_id, workspace_id, DEMO_ASSET_NAME, DEMO_ASSET_IDENTIFIER,
                DEMO_ASSET_IDENTIFIER, DEMO_AUTHORITATIVE_SOURCE, user_id, user_id,
            ),
        )

    # Idempotent: seed only when this demo asset has no integrity evidence yet.
    if service.load_onchain_observation(connection, workspace_id=workspace_id, asset_id=asset_id) is not None:
        return {'seeded': False, 'reason': 'already_seeded', 'asset_id': asset_id}

    connection.execute(
        '''
        INSERT INTO asset_onchain_supply_observations (
            id, workspace_id, asset_id, total_supply, token_decimals, chain_network, contract_address,
            block_number, tx_hash, last_delta, last_delta_operation, last_delta_at,
            provider_type, evidence_source, observed_at, created_at
        ) VALUES (%s, %s, %s, %s, 0, 'base-mainnet', '0x0000000000000000000000000000000000000002',
                  %s, %s, %s, 'mint', %s, 'demo_simulator', 'simulator', %s, %s)
        ''',
        (
            str(uuid.uuid4()), workspace_id, asset_id, DEMO_OBSERVED_SUPPLY,
            21_000_000, '0x' + 'de' * 32, DEMO_MINT_DELTA, now - timedelta(seconds=8),
            now - timedelta(seconds=8), now,
        ),
    )
    connection.execute(
        '''
        INSERT INTO asset_authoritative_state (
            id, workspace_id, asset_id, expected_total_supply, token_decimals, settlement_state,
            source_name, source_kind, source_status, external_reference, evidence_source, observed_at, created_at
        ) VALUES (%s, %s, %s, %s, 0, 'settled', %s, 'transfer_agent', 'reported', 'SUB-DEMO-0001',
                  'simulator', %s, %s)
        ''',
        (
            str(uuid.uuid4()), workspace_id, asset_id, DEMO_EXPECTED_SUPPLY,
            DEMO_AUTHORITATIVE_SOURCE, now - timedelta(seconds=12), now,
        ),
    )
    # Deliberately NO authorized issuance for the observed mint — that absence is
    # what makes the deterministic engine return NO_MATCHING_AUTHORIZED_ISSUANCE.

    outcome = service.evaluate_and_persist(
        connection, workspace_id=workspace_id, asset_id=asset_id, asset_name=DEMO_ASSET_NAME,
        trigger_source='demo_seed', config=aic.integrity_config(), now=now,
    )
    logger.info(
        'event=asset_integrity_demo_scenario_seeded workspace_id=%s asset_id=%s status=%s evidence_source=simulator',
        workspace_id, asset_id, outcome['result'].status,
    )
    return {
        'seeded': True,
        'asset_id': asset_id,
        'reconciliation_id': outcome['snapshot_id'],
        'status': outcome['result'].status,
        'reason_code': outcome['result'].reason_code,
        'evidence_source': 'simulator',
    }
