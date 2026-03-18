from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from phase1_local.dev_support import database_url, load_env_file, load_service, resolve_sqlite_path, seed_service

from app.engine import ComplianceEngine
from app.schemas import (
    GOVERNANCE_ACTION_EXAMPLE,
    RESIDENCY_ALLOWED_EXAMPLE,
    TRANSFER_APPROVED_EXAMPLE,
    TRANSFER_REVIEW_EXAMPLE,
    GovernanceActionRequest,
    GovernanceActionRecord,
    ResidencyScreeningRequest,
    ResidencyScreeningResponse,
    TransferScreeningRequest,
    TransferScreeningResponse,
)

load_env_file()

SERVICE_NAME = 'compliance-service'
PORT = int(os.getenv('PORT', 8004))
DETAIL = 'Sovereign-grade compliance policy wrapper and governance service with deterministic transfer screening, residency controls, and immutable-style local audit trail.'
DEFAULT_METRICS = [
    {
        'metric_key': 'compliance_wrappers',
        'label': 'Compliance Wrappers',
        'value': 'Deterministic transfer screening rules are active for KYC, sanctions, jurisdiction, and threshold controls.',
        'status': 'Ready',
    },
    {
        'metric_key': 'governance_ledger',
        'label': 'Governance Ledger',
        'value': 'Local immutable-style action log is available for freezes, allowlists, blocklists, and asset pauses.',
        'status': 'Tracking',
    },
]
engine = ComplianceEngine()

app = FastAPI(
    title='compliance-service service',
    summary='Feature 3 sovereign-grade compliance and governance service.',
    description='Simulates explainable regulatory wrappers, geopatriation controls, and local governance actions for treasury-token transfers while remaining deterministic and demo-friendly.',
)


@app.on_event('startup')
def startup() -> None:
    seed_service(SERVICE_NAME, PORT, DETAIL, DEFAULT_METRICS)


@app.get('/health', summary='Compliance-service health check', description='Returns runtime details for the compliance service.')
def health() -> dict[str, object]:
    return {
        'status': 'ok',
        'service': SERVICE_NAME,
        'port': PORT,
        'app_mode': os.getenv('APP_MODE', 'local'),
        'database_url': database_url(),
        'redis_enabled': os.getenv('REDIS_ENABLED', 'false').lower() == 'true',
    }


@app.get('/state', summary='Compliance-service seeded state', description='Returns the service registry row written to the shared local SQLite file.')
def state() -> dict[str, object]:
    return {
        'service': load_service(SERVICE_NAME),
        'sqlite_path': str(resolve_sqlite_path()),
    }


@app.get('/dashboard', summary='Compliance dashboard payload', description='Returns Feature 3 dashboard cards, policy state, residency results, governance actions, and explainable transfer screening.')
def dashboard() -> dict[str, Any]:
    return engine.dashboard()


@app.post('/screen/transfer', response_model=TransferScreeningResponse, summary='Screen a transfer request', description='Evaluates deterministic compliance wrapper rules before a transfer is allowed.')
def screen_transfer(
    request: TransferScreeningRequest = Body(
        ...,
        openapi_examples={
            'approved_transfer': {
                'summary': 'Compliant transfer approved',
                'description': 'All required wrapper checks pass.',
                'value': TRANSFER_APPROVED_EXAMPLE,
            },
            'review_transfer': {
                'summary': 'Review due to incomplete KYC and jurisdiction controls',
                'description': 'Demonstrates explainable review outcomes.',
                'value': TRANSFER_REVIEW_EXAMPLE,
            },
        },
    )
) -> TransferScreeningResponse:
    return engine.screen_transfer(request)


@app.post('/screen/residency', response_model=ResidencyScreeningResponse, summary='Screen a residency / geopatriation request', description='Evaluates deterministic region, cloud, and sensitivity controls before processing is allowed.')
def screen_residency(
    request: ResidencyScreeningRequest = Body(
        ...,
        openapi_examples={
            'allowed_residency': {
                'summary': 'Allowed sovereign-aligned residency request',
                'description': 'Matches the approved residency sample.',
                'value': RESIDENCY_ALLOWED_EXAMPLE,
            },
            'denied_residency': {
                'summary': 'Denied due to restricted region',
                'description': 'Matches the bundled denied residency scenario.',
                'value': engine.load_scenario_data('denied-residency-restricted-region'),
            },
        },
    )
) -> ResidencyScreeningResponse:
    return engine.screen_residency(request)


@app.get('/policy/state', summary='Current policy state', description='Returns the live demo policy state derived from governance actions and default compliance policy data.')
def policy_state() -> dict[str, Any]:
    return engine.get_policy_state()


@app.get('/governance/actions', summary='List governance actions', description='Returns governance actions from the local immutable-style audit trail in reverse chronological order.')
def governance_actions() -> list[dict[str, Any]]:
    return engine.list_actions()


@app.get('/governance/actions/{action_id}', summary='Load a governance action', description='Returns one governance action from the local audit trail by ID.')
def governance_action(action_id: str) -> dict[str, Any]:
    action = engine.get_action(action_id)
    if action is None:
        raise HTTPException(status_code=404, detail=f'Unknown action_id: {action_id}')
    return action


@app.post('/governance/actions', response_model=GovernanceActionRecord, summary='Create a governance action', description='Applies a deterministic governance action and persists it to the local immutable-style audit trail.')
def create_governance_action(
    request: GovernanceActionRequest = Body(
        ...,
        openapi_examples={
            'freeze_wallet': {
                'summary': 'Freeze a wallet',
                'description': 'Matches the bundled governance freeze scenario.',
                'value': GOVERNANCE_ACTION_EXAMPLE,
            },
            'pause_asset': {
                'summary': 'Pause asset transfers',
                'description': 'Matches the bundled pause asset scenario.',
                'value': engine.load_scenario_data('governance-pause-asset'),
            },
        },
    )
) -> GovernanceActionRecord:
    return engine.apply_governance_action(request)


@app.get('/scenarios', summary='List bundled compliance scenarios', description='Returns transfer, residency, and governance demo scenarios bundled with the compliance service.')
def list_scenarios() -> list[dict[str, str]]:
    return engine.list_scenarios()


@app.get('/scenarios/{scenario_name}', summary='Load a bundled compliance scenario', description='Returns the raw JSON body for one bundled compliance scenario.')
def get_scenario(scenario_name: str) -> dict[str, Any]:
    scenario = engine.scenario(scenario_name)
    if scenario is None:
        raise HTTPException(status_code=404, detail=f'Unknown scenario: {scenario_name}')
    return scenario
