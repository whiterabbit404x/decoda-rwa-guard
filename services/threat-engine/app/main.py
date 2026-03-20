from __future__ import annotations

import json
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

from .engine import ThreatEngine
from .schemas import (
    ADMIN_PRIVILEGE_TRANSACTION_EXAMPLE,
    CONTRACT_ANALYSIS_EXAMPLE,
    FLASH_LOAN_TRANSACTION_EXAMPLE,
    NORMAL_MARKET_EXAMPLE,
    SAFE_TRANSACTION_EXAMPLE,
    SPOOFING_MARKET_EXAMPLE,
    WASH_TRADING_MARKET_EXAMPLE,
    ContractAnalysisRequest,
    MarketAnalysisRequest,
    ThreatDashboardResponse,
    TransactionAnalysisRequest,
)

load_env_file()

SERVICE_NAME = 'threat-engine'
PORT = int(os.getenv('PORT', 8002))
DETAIL = 'Preemptive cybersecurity and AI threat defense engine using deterministic rules for contract, transaction, and market anomaly analysis.'
DEFAULT_METRICS = [
    {
        'metric_key': 'threat_engine_mode',
        'label': 'Threat Engine',
        'value': 'Explainable contract, transaction, and market anomaly scoring is active.',
        'status': 'Ready',
    },
    {
        'metric_key': 'threat_patterns',
        'label': 'Threat Patterns',
        'value': 'Flash-loan, drain, privilege abuse, spoofing, and wash-trading heuristics loaded.',
        'status': 'Configured',
    },
]
DATA_DIR = Path(__file__).resolve().parents[1] / 'data'
SCENARIOS: dict[str, dict[str, str]] = {
    'safe-transaction': {'description': 'Safe treasury settlement transaction.', 'file': 'safe_transaction.json'},
    'flash-loan-transaction': {'description': 'Suspicious flash-loan-like transaction.', 'file': 'flash_loan_transaction.json'},
    'admin-privilege-transaction': {'description': 'Unexpected admin privilege abuse scenario.', 'file': 'admin_privilege_abuse.json'},
    'normal-market': {'description': 'Normal treasury-token market behavior.', 'file': 'normal_market_behavior.json'},
    'spoofing-market': {'description': 'Spoofing-like order behavior.', 'file': 'spoofing_market_behavior.json'},
    'wash-trading-market': {'description': 'Wash-trading-like circular activity.', 'file': 'wash_trading_market_behavior.json'},
    'sample-contract': {'description': 'Contract scan example with privilege and drain indicators.', 'file': 'sample_contract.json'},
}
engine = ThreatEngine()

app = FastAPI(
    title='threat-engine service',
    summary='Feature 2 preemptive cybersecurity and market anomaly analysis service.',
    description='Evaluates smart contract logic summaries, transaction intent, and treasury-token market behavior with deterministic weighted rules that explain every score, severity bucket, and action recommendation.',
)


@app.on_event('startup')
def startup() -> None:
    seed_service(SERVICE_NAME, PORT, DETAIL, DEFAULT_METRICS)


@app.get('/health', summary='Threat-engine health check', description='Returns runtime details and confirms that the deterministic threat analysis engine is available.')
def health() -> dict[str, object]:
    return {
        'status': 'ok',
        'service': SERVICE_NAME,
        'port': PORT,
        'app_mode': os.getenv('APP_MODE', 'local'),
        'database_url': database_url(),
        'redis_enabled': os.getenv('REDIS_ENABLED', 'false').lower() == 'true',
    }


@app.get('/state', summary='Threat-engine seeded state', description='Returns the service registry row written to the shared local SQLite file.')
def state() -> dict[str, object]:
    return {
        'service': load_service(SERVICE_NAME),
        'sqlite_path': str(resolve_sqlite_path()),
    }


@app.get('/scenarios', summary='List bundled threat scenarios', description='Lists the bundled contract, transaction, and market scenarios that power Feature 2 demos and docs.')
def list_scenarios() -> list[dict[str, str]]:
    return [
        {'scenario': name, 'description': details['description'], 'sample_path': str(DATA_DIR / details['file'])}
        for name, details in SCENARIOS.items()
    ]


@app.get('/scenarios/{scenario_name}', summary='Load a bundled threat scenario', description='Returns the raw JSON body for one bundled demo scenario.')
def get_scenario(scenario_name: str) -> dict[str, Any]:
    details = SCENARIOS.get(scenario_name)
    if details is None:
        raise HTTPException(status_code=404, detail=f'Unknown scenario: {scenario_name}')
    return {
        'scenario': scenario_name,
        'description': details['description'],
        'data': json.loads((DATA_DIR / details['file']).read_text()),
    }


@app.post(
    '/analyze/contract',
    summary='Analyze smart contract logic and findings',
    description='Scores contract metadata, flags, and function summaries to highlight flash-loan indicators, drain paths, privilege escalation, and suspicious interaction sequences.',
)
def analyze_contract(
    request: ContractAnalysisRequest = Body(
        ...,
        openapi_examples={
            'sample_contract': {
                'summary': 'Proxy router with privileged and drain indicators',
                'description': 'Matches services/threat-engine/data/sample_contract.json.',
                'value': CONTRACT_ANALYSIS_EXAMPLE,
            }
        },
    )
) -> dict[str, Any]:
    return engine.analyze_contract(request).model_dump()


@app.post(
    '/analyze/transaction',
    summary='Analyze transaction intent',
    description='Scores wallet, action, protocol, amount, and call sequence data to recommend allow, review, or block decisions.',
)
def analyze_transaction(
    request: TransactionAnalysisRequest = Body(
        ...,
        openapi_examples={
            'safe_transaction': {
                'summary': 'Safe treasury settlement',
                'description': 'Expected allow decision.',
                'value': SAFE_TRANSACTION_EXAMPLE,
            },
            'flash_loan_transaction': {
                'summary': 'Suspicious flash-loan-like flow',
                'description': 'Expected block decision.',
                'value': FLASH_LOAN_TRANSACTION_EXAMPLE,
            },
            'admin_privilege_transaction': {
                'summary': 'Admin privilege abuse flow',
                'description': 'Expected review or block decision.',
                'value': ADMIN_PRIVILEGE_TRANSACTION_EXAMPLE,
            },
        },
    )
) -> dict[str, Any]:
    return engine.analyze_transaction(request).model_dump()


@app.post(
    '/analyze/market',
    summary='Analyze treasury-token market behavior',
    description='Scores simplified candles, order-flow summaries, and wallet-cluster activity to flag spoofing, wash trading, abnormal volume spikes, and rapid swings.',
)
def analyze_market(
    request: MarketAnalysisRequest = Body(
        ...,
        openapi_examples={
            'normal_market': {
                'summary': 'Normal treasury-token market behavior',
                'description': 'Expected low anomaly score.',
                'value': NORMAL_MARKET_EXAMPLE,
            },
            'spoofing_market': {
                'summary': 'Spoofing-like order activity',
                'description': 'Expected high anomaly score.',
                'value': SPOOFING_MARKET_EXAMPLE,
            },
            'wash_trading_market': {
                'summary': 'Wash-trading-like circular activity',
                'description': 'Expected high anomaly score.',
                'value': WASH_TRADING_MARKET_EXAMPLE,
            },
        },
    )
) -> dict[str, Any]:
    return engine.analyze_market(request).model_dump()


@app.get('/dashboard', response_model=ThreatDashboardResponse, summary='Feature 2 threat dashboard payload', description='Returns current Feature 2 cards, alerts, recent detections, and scenario metadata for the frontend dashboard.')
def dashboard() -> ThreatDashboardResponse:
    return engine.build_dashboard(load_demo_requests())


@app.get('/internal/dashboard', response_model=ThreatDashboardResponse, summary='Internal dashboard endpoint', description='Internal-use endpoint consumed by the API gateway.')
def internal_dashboard() -> ThreatDashboardResponse:
    return dashboard()


@app.post('/internal/analyze/contract', summary='Internal contract analysis endpoint', description='Internal-use endpoint for the API gateway.')
def internal_analyze_contract(request: ContractAnalysisRequest) -> dict[str, Any]:
    return engine.analyze_contract(request).model_dump()


@app.post('/internal/analyze/transaction', summary='Internal transaction analysis endpoint', description='Internal-use endpoint for the API gateway.')
def internal_analyze_transaction(request: TransactionAnalysisRequest) -> dict[str, Any]:
    return engine.analyze_transaction(request).model_dump()


@app.post('/internal/analyze/market', summary='Internal market analysis endpoint', description='Internal-use endpoint for the API gateway.')
def internal_analyze_market(request: MarketAnalysisRequest) -> dict[str, Any]:
    return engine.analyze_market(request).model_dump()


def load_demo_requests() -> dict[str, Any]:
    return {
        'safe_transaction': TransactionAnalysisRequest.model_validate(SAFE_TRANSACTION_EXAMPLE),
        'flash_loan_transaction': TransactionAnalysisRequest.model_validate(FLASH_LOAN_TRANSACTION_EXAMPLE),
        'admin_privilege_transaction': TransactionAnalysisRequest.model_validate(ADMIN_PRIVILEGE_TRANSACTION_EXAMPLE),
        'normal_market': MarketAnalysisRequest.model_validate(NORMAL_MARKET_EXAMPLE),
        'spoofing_market': MarketAnalysisRequest.model_validate(SPOOFING_MARKET_EXAMPLE),
        'wash_trading_market': MarketAnalysisRequest.model_validate(WASH_TRADING_MARKET_EXAMPLE),
        'contract': ContractAnalysisRequest.model_validate(CONTRACT_ANALYSIS_EXAMPLE),
    }
