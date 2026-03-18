from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from phase1_local.dev_support import database_url, load_env_file, load_service, resolve_sqlite_path, seed_service

from .engine import RiskEngine
from .schemas import RISK_EVALUATION_EXAMPLE, RiskEvaluationRequest, RiskEvaluationResponse, ScenarioSummary

load_env_file()

SERVICE_NAME = 'risk-engine'
PORT = int(os.getenv('PORT', 8001))
DETAIL = 'Defensive transaction analysis service with heuristic risk scoring for smart contract interactions.'
DEFAULT_METRICS = [
    {
        'metric_key': 'risk_engine_mode',
        'label': 'Risk Engine',
        'value': 'Phase 1 defensive transaction analysis heuristics enabled.',
        'status': 'Ready',
    },
    {
        'metric_key': 'decision_space',
        'label': 'Decisions',
        'value': 'ALLOW / REVIEW / BLOCK recommendations available.',
        'status': 'Configured',
    },
]
DATA_DIR = Path(__file__).resolve().parents[1] / 'data'
SCENARIOS = {
    'normal': {
        'description': 'Baseline healthy market behavior with ordinary contract interaction patterns.',
        'file': 'normal_market_events.json',
    },
    'suspicious': {
        'description': 'Synthetic spoofing, wash-trading, and flash-loan style anomalies.',
        'file': 'suspicious_market_events.json',
    },
    'sample-request': {
        'description': 'End-to-end sample payload for /v1/risk/evaluate.',
        'file': 'sample_risk_request.json',
    },
}

app = FastAPI(
    title=f'{SERVICE_NAME} service',
    summary='Phase 1 heuristic risk-engine for treasury transaction screening.',
    description='Evaluate treasury-related transactions, inspect bundled scenarios, and surface rule-driven ALLOW / REVIEW / BLOCK recommendations for the dashboard and API gateway.',
)
engine = RiskEngine()


@app.on_event('startup')
def startup() -> None:
    seed_service(SERVICE_NAME, PORT, DETAIL, DEFAULT_METRICS)


@app.get(
    '/health',
    summary='Risk-engine health check',
    description='Returns the runtime mode and local persistence configuration for the risk-engine service.',
)
def health() -> dict[str, object]:
    return {
        'status': 'ok',
        'service': SERVICE_NAME,
        'port': PORT,
        'app_mode': os.getenv('APP_MODE', 'local'),
        'database_url': database_url(),
        'redis_enabled': os.getenv('REDIS_ENABLED', 'false').lower() == 'true',
    }


@app.get(
    '/state',
    summary='Risk-engine seeded state',
    description='Returns the service registry row written into the shared local SQLite file.',
)
def state() -> dict[str, object]:
    return {
        'service': load_service(SERVICE_NAME),
        'sqlite_path': str(resolve_sqlite_path()),
    }


@app.get(
    '/v1/risk/scenarios',
    response_model=list[ScenarioSummary],
    summary='List bundled risk scenarios',
    description='Returns the bundled scenario files that can be used for local demos, docs, and smoke checks.',
)
def list_scenarios() -> list[ScenarioSummary]:
    return [
        ScenarioSummary(
            scenario=name,
            description=details['description'],
            sample_path=str(DATA_DIR / details['file']),
        )
        for name, details in SCENARIOS.items()
    ]


@app.get(
    '/v1/risk/scenarios/{scenario_name}',
    summary='Fetch a bundled scenario payload',
    description='Loads one of the bundled JSON scenario payloads so it can be replayed locally or copied into the evaluate endpoint docs.',
)
def get_scenario(scenario_name: str) -> dict[str, object]:
    details = SCENARIOS.get(scenario_name)
    if details is None:
        raise HTTPException(status_code=404, detail=f'Unknown scenario: {scenario_name}')
    path = DATA_DIR / details['file']
    return {
        'scenario': scenario_name,
        'description': details['description'],
        'data': json.loads(path.read_text()),
    }


@app.post(
    '/v1/risk/evaluate',
    response_model=RiskEvaluationResponse,
    summary='Evaluate a transaction risk payload',
    description='Runs the main Phase 1 heuristic scoring engine and returns the triggered rules, score breakdown, and recommendation used by the dashboard.',
)
def evaluate_risk(
    request: RiskEvaluationRequest = Body(
        ...,
        openapi_examples={
            'sample_request': {
                'summary': 'Sample treasury transaction payload',
                'description': 'Matches services/risk-engine/data/sample_risk_request.json.',
                'value': RISK_EVALUATION_EXAMPLE,
            }
        },
    )
) -> RiskEvaluationResponse:
    return engine.evaluate(request)


@app.post(
    '/internal/risk/evaluate',
    response_model=RiskEvaluationResponse,
    summary='Internal risk evaluation endpoint',
    description='Internal-use endpoint consumed by the API gateway when it assembles the live dashboard risk queue.',
)
def evaluate_risk_internal(
    request: RiskEvaluationRequest = Body(
        ...,
        openapi_examples={
            'sample_request': {
                'summary': 'Gateway-compatible payload',
                'description': 'Same body as the public evaluate endpoint for local Phase 1 usage.',
                'value': RISK_EVALUATION_EXAMPLE,
            }
        },
    )
) -> RiskEvaluationResponse:
    return engine.evaluate(request)
