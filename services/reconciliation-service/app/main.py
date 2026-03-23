from __future__ import annotations

import os
import sys
from pathlib import Path


def _find_repo_root(start: Path) -> Path:
    for candidate in start.resolve().parents:
        if (candidate / 'phase1_local').is_dir():
            return candidate
    raise RuntimeError(f"Unable to locate repo root from {start} via a phase1_local directory search.")


def _ensure_repo_root_on_path() -> Path:
    repo_root = _find_repo_root(Path(__file__))
    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)
    return repo_root


from typing import Any

from fastapi import Body, FastAPI, HTTPException

REPO_ROOT = _ensure_repo_root_on_path()

from phase1_local.dev_support import database_url, load_env_file, load_service, resolve_sqlite_path, seed_service

from app.engine import ReconciliationEngine
from app.schemas import (
    CRITICAL_RECONCILIATION_EXAMPLE,
    CYBER_PAUSE_BACKSTOP_EXAMPLE,
    HEALTHY_RECONCILIATION_EXAMPLE,
    HIGH_VOLATILITY_BACKSTOP_EXAMPLE,
    INCIDENT_RECORD_EXAMPLE,
    BackstopRequest,
    BackstopResponse,
    IncidentRecord,
    IncidentRecordRequest,
    ReconciliationRequest,
    ReconciliationResponse,
)

load_env_file()

SERVICE_NAME = 'reconciliation-service'
PORT = int(os.getenv('PORT', 8005))
DETAIL = 'Interoperability and systemic resilience service for deterministic cross-chain reconciliation, liquidity backstop evaluation, and local incident logging.'
DEFAULT_METRICS = [
    {
        'metric_key': 'reconciliation_status',
        'label': 'Reconciliation Status',
        'value': 'Multi-ledger reconciliation and incident logging controls are active for ethereum, avalanche, and private-bank-ledger.',
        'status': 'Ready',
    },
    {
        'metric_key': 'backstop_controls',
        'label': 'Backstop Controls',
        'value': 'Circuit-breaker, bridge-pause, and threshold-reduction rules are loaded for deterministic stress scenarios.',
        'status': 'Configured',
    },
]
engine = ReconciliationEngine()

app = FastAPI(
    title='reconciliation-service service',
    summary='Feature 4 interoperability and systemic resilience service.',
    description='Simulates deterministic cross-chain reconciliation, liquidity backstop protocols, and a local incident ledger with explainable outputs and no real blockchain/network dependencies.',
)


@app.on_event('startup')
def startup() -> None:
    seed_service(SERVICE_NAME, PORT, DETAIL, DEFAULT_METRICS)


@app.get('/health', summary='Reconciliation-service health check', description='Returns runtime details for the reconciliation and resilience service.')
def health() -> dict[str, object]:
    return {
        'status': 'ok',
        'service': SERVICE_NAME,
        'port': PORT,
        'app_mode': os.getenv('APP_MODE', 'local'),
        'database_url': database_url(),
        'redis_enabled': os.getenv('REDIS_ENABLED', 'false').lower() == 'true',
    }


@app.get('/state', summary='Reconciliation-service seeded state', description='Returns the service registry row written to the shared local SQLite file.')
def state() -> dict[str, object]:
    return {
        'service': load_service(SERVICE_NAME),
        'sqlite_path': str(resolve_sqlite_path()),
    }


@app.get('/dashboard', summary='Feature 4 dashboard payload', description='Returns the default Feature 4 reconciliation snapshot, backstop decision, and latest local incident records for the frontend dashboard.')
def dashboard() -> dict[str, Any]:
    return engine.dashboard()


@app.post('/reconcile/state', response_model=ReconciliationResponse, summary='Run cross-chain reconciliation', description='Compares deterministic ledger supply snapshots, normalizes effective supply, and explains mismatches, stale ledgers, settlement lag, and duplicate mint risk.')
def reconcile_state(
    request: ReconciliationRequest = Body(
        ...,
        openapi_examples={
            'healthy_matched_state': {
                'summary': 'Healthy matched multi-ledger state',
                'description': 'Expected to return a matched reconciliation result.',
                'value': HEALTHY_RECONCILIATION_EXAMPLE,
            },
            'critical_divergence': {
                'summary': 'Critical supply divergence and double-count risk',
                'description': 'Expected to return a critical result with pause recommendations.',
                'value': CRITICAL_RECONCILIATION_EXAMPLE,
            },
        },
    )
) -> ReconciliationResponse:
    return ReconciliationResponse(**engine.reconcile(request))


@app.post('/backstop/evaluate', response_model=BackstopResponse, summary='Evaluate systemic resilience controls', description='Applies deterministic liquidity backstop thresholds to volatility, cyber, reconciliation, oracle confidence, and compliance incident inputs.')
def evaluate_backstop(
    request: BackstopRequest = Body(
        ...,
        openapi_examples={
            'high_volatility': {
                'summary': 'High volatility alert',
                'description': 'Expected to trigger alert or restricted safeguards.',
                'value': HIGH_VOLATILITY_BACKSTOP_EXAMPLE,
            },
            'cyber_pause': {
                'summary': 'Cyber-triggered restricted or paused mode',
                'description': 'Expected to trigger paused bridge / trading controls.',
                'value': CYBER_PAUSE_BACKSTOP_EXAMPLE,
            },
        },
    )
) -> BackstopResponse:
    return BackstopResponse(**engine.evaluate_backstop(request))


@app.post('/incidents/record', response_model=IncidentRecord, summary='Record a resilience incident', description='Writes a deterministic resilience incident entry to the local JSON-backed incident ledger and returns its attestation hash / fingerprint.')
def record_incident(
    request: IncidentRecordRequest = Body(
        ...,
        openapi_examples={
            'reconciliation_failure': {
                'summary': 'Reconciliation failure incident',
                'description': 'Expected to create a critical incident with deterministic fingerprinting.',
                'value': INCIDENT_RECORD_EXAMPLE,
            }
        },
    )
) -> IncidentRecord:
    return engine.record_incident(request)


@app.get('/incidents', summary='List resilience incidents', description='Returns the local incident ledger in reverse chronological order.')
def list_incidents() -> list[dict[str, Any]]:
    return engine.list_incidents()


@app.get('/incidents/{event_id}', summary='Load a resilience incident', description='Returns one incident ledger entry by event_id.')
def get_incident(event_id: str) -> dict[str, Any]:
    incident = engine.get_incident(event_id)
    if incident is None:
        raise HTTPException(status_code=404, detail=f'Unknown event_id: {event_id}')
    return incident


@app.get('/scenarios', summary='List bundled Feature 4 scenarios', description='Returns reconciliation, backstop, and incident demo scenarios bundled with the reconciliation-service.')
def list_scenarios() -> list[dict[str, str]]:
    return engine.list_scenarios()


@app.get('/scenarios/{scenario_name}', summary='Load a bundled Feature 4 scenario', description='Returns the raw JSON body for one bundled Feature 4 demo scenario.')
def get_scenario(scenario_name: str) -> dict[str, Any]:
    scenario = engine.scenario(scenario_name)
    if scenario is None:
        raise HTTPException(status_code=404, detail=f'Unknown scenario: {scenario_name}')
    return scenario


embedded_dashboard = dashboard
embedded_reconcile_state = reconcile_state
embedded_evaluate_backstop = evaluate_backstop
embedded_record_incident = record_incident
embedded_list_incidents = list_incidents
embedded_get_incident = get_incident

__all__ = [
    'app',
    'engine',
    'BackstopRequest',
    'BackstopResponse',
    'IncidentRecord',
    'IncidentRecordRequest',
    'ReconciliationRequest',
    'ReconciliationResponse',
    'dashboard',
    'embedded_dashboard',
    'reconcile_state',
    'embedded_reconcile_state',
    'evaluate_backstop',
    'embedded_evaluate_backstop',
    'record_incident',
    'embedded_record_incident',
    'list_incidents',
    'embedded_list_incidents',
    'get_incident',
    'embedded_get_incident',
]
