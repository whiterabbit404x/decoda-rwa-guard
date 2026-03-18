from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException

from phase1_local.dev_support import load_env_file, database_url, load_service, resolve_sqlite_path, seed_service

from .engine import RiskEngine
from .schemas import RiskEvaluationRequest, RiskEvaluationResponse, ScenarioSummary

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

app = FastAPI(title=f'{SERVICE_NAME} service')
engine = RiskEngine()


@app.on_event('startup')
def startup() -> None:
    seed_service(SERVICE_NAME, PORT, DETAIL, DEFAULT_METRICS)


@app.get('/health')
def health() -> dict[str, object]:
    return {
        'status': 'ok',
        'service': SERVICE_NAME,
        'port': PORT,
        'app_mode': os.getenv('APP_MODE', 'local'),
        'database_url': database_url(),
        'redis_enabled': os.getenv('REDIS_ENABLED', 'false').lower() == 'true',
    }


@app.get('/state')
def state() -> dict[str, object]:
    return {
        'service': load_service(SERVICE_NAME),
        'sqlite_path': str(resolve_sqlite_path()),
    }


@app.get('/v1/risk/scenarios', response_model=list[ScenarioSummary])
def list_scenarios() -> list[ScenarioSummary]:
    return [
        ScenarioSummary(
            scenario=name,
            description=details['description'],
            sample_path=str(DATA_DIR / details['file']),
        )
        for name, details in SCENARIOS.items()
    ]


@app.get('/v1/risk/scenarios/{scenario_name}')
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


@app.post('/v1/risk/evaluate', response_model=RiskEvaluationResponse)
def evaluate_risk(request: RiskEvaluationRequest) -> RiskEvaluationResponse:
    return engine.evaluate(request)


@app.post('/internal/risk/evaluate', response_model=RiskEvaluationResponse)
def evaluate_risk_internal(request: RiskEvaluationRequest) -> RiskEvaluationResponse:
    return engine.evaluate(request)
