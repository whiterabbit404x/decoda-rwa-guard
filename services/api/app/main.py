from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request as UrlRequest, urlopen

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from services.api.app.pilot import (
    authenticate_request,
    authenticate_with_connection,
    build_history_response,
    create_governance_action_record,
    create_incident_record,
    create_workspace_for_user,
    enforce_auth_rate_limit,
    list_user_workspaces,
    live_mode_enabled,
    log_audit,
    maybe_insert_alert,
    parse_csv_env,
    persist_analysis_run,
    pilot_mode,
    pg_connection,
    resolve_workspace,
    select_workspace_for_user,
    signin_user,
    signout_user,
    signup_user,
)


def _find_repo_root(start: Path) -> Path:
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / 'phase1_local').is_dir():
            return candidate
    raise RuntimeError(f"Unable to locate repo root from {start} via a phase1_local directory search.")


def _ensure_repo_root_on_path() -> Path:
    repo_root = _find_repo_root(Path(__file__))
    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)
    return repo_root


REPO_ROOT = _ensure_repo_root_on_path()

from phase1_local.dev_support import (
    dashboard_payload,
    database_url,
    load_env_file,
    load_service,
    resolve_sqlite_path,
    seed_service,
)

load_env_file()

logger = logging.getLogger(__name__)

SERVICE_NAME = 'api'
PORT = int(os.getenv('PORT', 8000))
DETAIL = 'FastAPI gateway serving the local Phase 1 dashboard API.'
DEFAULT_METRICS = [
    {
        'metric_key': 'api_status',
        'label': 'API Gateway',
        'value': 'Serving local dashboard and service registry endpoints.',
        'status': 'Healthy',
    },
    {
        'metric_key': 'local_mode',
        'label': 'Local Mode',
        'value': 'SQLite-backed development mode is enabled without Docker.',
        'status': 'Ready',
    },
]
RISK_ENGINE_URL = os.getenv('RISK_ENGINE_URL', 'http://localhost:8001').rstrip('/')
RISK_ENGINE_TIMEOUT_SECONDS = float(os.getenv('RISK_ENGINE_TIMEOUT_SECONDS', '1.5'))
RISK_ENGINE_DATA_DIR = Path(__file__).resolve().parents[2] / 'risk-engine' / 'data'
THREAT_ENGINE_URL = os.getenv('THREAT_ENGINE_URL', 'http://localhost:8002').rstrip('/')
THREAT_ENGINE_TIMEOUT_SECONDS = float(os.getenv('THREAT_ENGINE_TIMEOUT_SECONDS', '1.5'))
THREAT_ENGINE_DATA_DIR = Path(__file__).resolve().parents[2] / 'threat-engine' / 'data'
COMPLIANCE_SERVICE_URL = os.getenv('COMPLIANCE_SERVICE_URL', 'http://localhost:8004').rstrip('/')
COMPLIANCE_SERVICE_TIMEOUT_SECONDS = float(os.getenv('COMPLIANCE_SERVICE_TIMEOUT_SECONDS', '1.5'))
COMPLIANCE_DATA_DIR = Path(__file__).resolve().parents[2] / 'compliance-service' / 'data'
RECONCILIATION_SERVICE_URL = os.getenv('RECONCILIATION_SERVICE_URL', 'http://localhost:8005').rstrip('/')
RECONCILIATION_SERVICE_TIMEOUT_SECONDS = float(os.getenv('RECONCILIATION_SERVICE_TIMEOUT_SECONDS', '1.5'))
RECONCILIATION_DATA_DIR = Path(__file__).resolve().parents[2] / 'reconciliation-service' / 'data'
OPTIONAL_FIXTURE_WARNINGS_EMITTED: set[tuple[str, str]] = set()
RUNTIME_MARKER_ENV_VARS = (
    'APP_VERSION',
    'APP_BUILD_COMMIT',
    'RAILWAY_GIT_COMMIT_SHA',
    'SOURCE_COMMIT',
    'COMMIT_SHA',
    'VERCEL_GIT_COMMIT_SHA',
)
HARD_CODED_BACKEND_BUILD_MARKER = 'backend-build-2026-03-20-fixture-diagnostics-v2'
FIXTURE_FILES = {
    'risk_engine': ('sample_risk_request.json',),
    'reconciliation': (
        'critical_supply_divergence_double_count_risk.json',
        'critical_mismatch_paused_bridge.json',
    ),
}
DEFAULT_RISK_SAMPLE_REQUEST = {
    'transaction_payload': {
        'tx_hash': '0xphase1sample',
        'from_address': '0x1111111111111111111111111111111111111111',
        'to_address': '0x2222222222222222222222222222222222222222',
        'value': 1850000.0,
        'gas_price': 57.0,
        'gas_limit': 900000,
        'chain_id': 1,
        'calldata_size': 644,
        'token_transfers': [
            {'token': 'USTB', 'amount': 550000},
            {'token': 'WETH', 'amount': 1200},
        ],
        'metadata': {
            'contains_flash_loan_hop': True,
            'entrypoint': 'aggregator-router',
        },
    },
    'decoded_function_call': {
        'function_name': 'flashLoan',
        'contract_name': 'LiquidityRouter',
        'arguments': {
            'receiver': '0x3333333333333333333333333333333333333333',
            'owner': '0x4444444444444444444444444444444444444444',
            'assets': ['USTB', 'WETH'],
        },
        'selectors': ['0xabcd1234'],
    },
    'wallet_reputation': {
        'address': '0x1111111111111111111111111111111111111111',
        'score': 22,
        'prior_flags': 3,
        'account_age_days': 5,
        'kyc_verified': False,
        'sanctions_hits': 0,
        'known_safe': False,
        'recent_counterparties': 27,
        'metadata': {'watchlist': 'elevated'},
    },
    'contract_metadata': {
        'address': '0x2222222222222222222222222222222222222222',
        'contract_name': 'LiquidityRouter',
        'verified_source': False,
        'proxy': True,
        'created_days_ago': 3,
        'tvl': 2800000.0,
        'audit_count': 0,
        'categories': ['dex-router'],
        'static_flags': {'hidden_owner': False},
        'metadata': {'upgradeability': 'mutable'},
    },
    'recent_market_events': [],
}
DEFAULT_NORMAL_MARKET_EVENTS = [
    {
        'timestamp': '2026-03-18T00:00:00Z',
        'event_type': 'trade',
        'asset': 'USTB',
        'venue': 'dex-alpha',
        'price': 1.0001,
        'volume': 120000.0,
        'side': 'buy',
        'trader_id': 'maker-1',
        'cancellation_rate': 0.05,
        'liquidity_change': 0.01,
        'metadata': {},
    }
]
DEFAULT_SUSPICIOUS_MARKET_EVENTS = [
    {
        'timestamp': '2026-03-18T01:00:00Z',
        'event_type': 'borrow',
        'asset': 'USTB',
        'venue': 'dex-beta',
        'price': 1.0,
        'volume': 250000.0,
        'trader_id': 'actor-7',
        'liquidity_change': -0.38,
        'metadata': {},
    }
]
DEFAULT_RECONCILIATION_STATE = {
    'asset_id': 'USTB-2026',
    'expected_total_supply': 1000000,
    'ledgers': [
        {
            'ledger_name': 'ethereum',
            'reported_supply': 740000,
            'locked_supply': 10000,
            'pending_settlement': 45000,
            'last_updated_at': '2026-03-18T11:40:00Z',
            'transfer_count': 125,
            'reconciliation_weight': 1.0,
        },
        {
            'ledger_name': 'avalanche',
            'reported_supply': 510000,
            'locked_supply': 5000,
            'pending_settlement': 38000,
            'last_updated_at': '2026-03-18T11:42:00Z',
            'transfer_count': 118,
            'reconciliation_weight': 1.0,
        },
        {
            'ledger_name': 'private-bank-ledger',
            'reported_supply': 210000,
            'locked_supply': 0,
            'pending_settlement': 12000,
            'last_updated_at': '2026-03-18T09:10:00Z',
            'transfer_count': 21,
            'reconciliation_weight': 1.0,
        },
    ],
}
DEFAULT_BACKSTOP_STATE = {
    'asset_id': 'USTB-2026',
    'volatility_score': 71,
    'cyber_alert_score': 89,
    'reconciliation_severity': 81,
    'oracle_confidence_score': 36,
    'compliance_incident_score': 74,
    'current_market_mode': 'restricted',
}
ALLOWED_ORIGINS = parse_csv_env('CORS_ALLOWED_ORIGINS', [
    'http://localhost:3000',
    'http://127.0.0.1:3000',
])


def resolve_runtime_marker() -> str:
    for env_var in RUNTIME_MARKER_ENV_VARS:
        value = os.getenv(env_var, '').strip()
        if value:
            return f'{env_var.lower()}:{value[:12]}'
    return f'code-sha:{sha256(Path(__file__).read_bytes()).hexdigest()[:12]}'


def resolve_git_commit() -> str | None:
    for env_var in RUNTIME_MARKER_ENV_VARS:
        value = os.getenv(env_var, '').strip()
        if value:
            return value
    try:
        result = subprocess.run(
            ['git', 'rev-parse', 'HEAD'],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=1,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None

    commit = result.stdout.strip()
    return commit or None


BACKEND_BUILD_ID = HARD_CODED_BACKEND_BUILD_MARKER
BACKEND_GIT_COMMIT = resolve_git_commit()
RUNTIME_MARKER = f'{BACKEND_BUILD_ID}:{resolve_runtime_marker()}'


def mode_flags() -> dict[str, Any]:
    live_enabled = live_mode_enabled()
    return {
        'app_mode': os.getenv('APP_MODE', 'local'),
        'pilot_mode': pilot_mode(),
        'live_mode_enabled': live_enabled,
        'demo_mode': not live_enabled,
    }


def fixture_diagnostics() -> dict[str, Any]:
    directories = {
        'risk_engine': {
            'path': str(RISK_ENGINE_DATA_DIR),
            'exists': RISK_ENGINE_DATA_DIR.is_dir(),
        },
        'reconciliation': {
            'path': str(RECONCILIATION_DATA_DIR),
            'exists': RECONCILIATION_DATA_DIR.is_dir(),
        },
    }
    files: dict[str, dict[str, dict[str, Any]]] = {}
    data_dirs = {
        'risk_engine': RISK_ENGINE_DATA_DIR,
        'reconciliation': RECONCILIATION_DATA_DIR,
    }
    for directory_name, filenames in FIXTURE_FILES.items():
        data_dir = data_dirs[directory_name]
        files[directory_name] = {
            filename: {
                'path': str(data_dir / filename),
                'exists': (data_dir / filename).is_file(),
            }
            for filename in filenames
        }
    return {
        'backend_build_id': BACKEND_BUILD_ID,
        'backend_git_commit': BACKEND_GIT_COMMIT,
        'version_marker': RUNTIME_MARKER,
        'directories': directories,
        'files': files,
        'modes': mode_flags(),
    }


def emit_startup_fixture_diagnostics() -> None:
    diagnostics = fixture_diagnostics()
    logger.info(
        'startup version=%s risk_dir=%s exists=%s sample_risk_request=%s '
        'reconciliation_dir=%s exists=%s critical_supply_divergence=%s '
        'critical_mismatch_paused_bridge=%s git_commit=%s app_mode=%s pilot_mode=%s live_mode=%s demo_mode=%s',
        diagnostics['backend_build_id'],
        diagnostics['directories']['risk_engine']['path'],
        diagnostics['directories']['risk_engine']['exists'],
        diagnostics['files']['risk_engine']['sample_risk_request.json']['exists'],
        diagnostics['directories']['reconciliation']['path'],
        diagnostics['directories']['reconciliation']['exists'],
        diagnostics['files']['reconciliation']['critical_supply_divergence_double_count_risk.json']['exists'],
        diagnostics['files']['reconciliation']['critical_mismatch_paused_bridge.json']['exists'],
        diagnostics['backend_git_commit'],
        diagnostics['modes']['app_mode'],
        diagnostics['modes']['pilot_mode'],
        diagnostics['modes']['live_mode_enabled'],
        diagnostics['modes']['demo_mode'],
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    seed_service(SERVICE_NAME, PORT, DETAIL, DEFAULT_METRICS)
    emit_startup_fixture_diagnostics()
    yield


app = FastAPI(
    title='api service',
    summary='Phase 1 gateway for dashboard and live risk-engine / threat-engine data.',
    description='Aggregates shared local service state, proxies dashboard feeds to the risk-engine and threat-engine, and returns explicit fallback metadata when backend services are unavailable.',
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)


@app.get('/health', summary='API health check', description='Returns the API runtime mode and local persistence configuration.')
def health() -> dict[str, object]:
    return {
        'status': 'ok',
        'service': SERVICE_NAME,
        'port': PORT,
        'app_mode': os.getenv('APP_MODE', 'local'),
        'database_url': database_url(),
        'redis_enabled': os.getenv('REDIS_ENABLED', 'false').lower() == 'true',
        'risk_engine_url': RISK_ENGINE_URL,
        'threat_engine_url': THREAT_ENGINE_URL,
        'compliance_service_url': COMPLIANCE_SERVICE_URL,
        'reconciliation_service_url': RECONCILIATION_SERVICE_URL,
        'pilot_mode': pilot_mode(),
        'live_mode_enabled': live_mode_enabled(),
        'backend_build_id': BACKEND_BUILD_ID,
        'backend_git_commit': BACKEND_GIT_COMMIT,
    }


@app.get('/debug/fixtures', summary='Read-only fixture diagnostics', description='Returns the deployed backend build id plus resolved fixture directories and file existence flags for deploy verification.')
def debug_fixtures() -> dict[str, Any]:
    return {
        'status': 'ok',
        'service': SERVICE_NAME,
        **fixture_diagnostics(),
    }


@app.get('/health/details', summary='Deployment verification details', description='Returns a safe runtime marker plus resolved fixture paths and mode flags for deploy verification.')
def health_details() -> dict[str, Any]:
    return {
        'status': 'ok',
        'service': SERVICE_NAME,
        **fixture_diagnostics(),
    }


@app.get('/state', summary='API seeded state', description='Returns the service registry row written into the shared local SQLite file.')
def state() -> dict[str, object]:
    return {
        'service': load_service(SERVICE_NAME),
        'sqlite_path': str(resolve_sqlite_path()),
    }


@app.get('/services', summary='List registered local services', description='Returns the shared local service registry used to populate the dashboard status cards.')
def services() -> dict[str, object]:
    payload = dashboard_payload()
    return {
        'mode': payload['mode'],
        'database_url': payload['database_url'],
        'services': payload['services'],
    }


@app.get('/dashboard', summary='Dashboard service snapshot', description='Returns the local dashboard summary cards and service registry information for the frontend.')
def dashboard() -> dict[str, object]:
    return dashboard_payload()


@app.get('/risk/dashboard', summary='Dashboard risk feed', description='Builds the dashboard transaction queue from live risk-engine evaluations and falls back to explicit demo-safe records when the risk-engine is unavailable.')
def risk_dashboard() -> dict[str, object]:
    queue = build_risk_dashboard_queue()
    live_count = sum(1 for item in queue if item['live_data'])
    degraded = live_count != len(queue)
    return {
        'source': 'live' if not degraded else 'fallback',
        'degraded': degraded,
        'message': 'Live risk-engine data loaded successfully.' if not degraded else 'Risk-engine unavailable or timed out for one or more queue items. Returning fallback-safe dashboard records.',
        'risk_engine': {
            'url': RISK_ENGINE_URL,
            'timeout_seconds': RISK_ENGINE_TIMEOUT_SECONDS,
            'live_items': live_count,
            'fallback_items': len(queue) - live_count,
        },
        'generated_at': queue[0]['updated_at'] if queue else None,
        'summary': build_risk_summary(queue),
        'transaction_queue': [serialize_queue_item(item) for item in queue],
        'risk_alerts': build_risk_alerts(queue),
        'contract_scan_results': build_contract_scan_results(queue),
        'decisions_log': build_decisions_log(queue),
    }


@app.get('/threat/dashboard', summary='Feature 2 threat dashboard feed', description='Returns the threat-engine dashboard payload when available and explicit fallback demo data when the threat-engine is unavailable.')
def threat_dashboard() -> dict[str, Any]:
    payload = fetch_threat_dashboard()
    return payload or fallback_threat_dashboard()


@app.post('/threat/analyze/contract', summary='Feature 2 contract analysis', description='Proxies a contract analysis request to the threat-engine and falls back to a conservative local rule summary if the engine is unavailable.')
def threat_analyze_contract(payload: dict[str, Any]) -> dict[str, Any]:
    response = proxy_threat('contract', payload)
    return response or fallback_contract_analysis(payload)


@app.post('/threat/analyze/transaction', summary='Feature 2 transaction analysis', description='Proxies a transaction intent analysis request to the threat-engine and falls back to a conservative local rule summary if the engine is unavailable.')
def threat_analyze_transaction(payload: dict[str, Any]) -> dict[str, Any]:
    response = proxy_threat('transaction', payload)
    return response or fallback_transaction_analysis(payload)


@app.post('/threat/analyze/market', summary='Feature 2 market anomaly analysis', description='Proxies a market anomaly request to the threat-engine and falls back to a conservative local rule summary if the engine is unavailable.')
def threat_analyze_market(payload: dict[str, Any]) -> dict[str, Any]:
    response = proxy_threat('market', payload)
    return response or fallback_market_analysis(payload)


@app.get('/compliance/dashboard', summary='Feature 3 compliance dashboard feed', description='Returns the compliance-service dashboard payload when available and explicit fallback demo data when the compliance service is unavailable.')
def compliance_dashboard() -> dict[str, Any]:
    payload = fetch_compliance_dashboard()
    return payload or fallback_compliance_dashboard()


@app.post('/compliance/screen/transfer', summary='Feature 3 transfer compliance screening', description='Proxies a transfer screening request to the compliance service and falls back to a conservative deterministic local decision if the service is unavailable.')
def compliance_screen_transfer(payload: dict[str, Any]) -> dict[str, Any]:
    response = proxy_compliance('screen/transfer', payload)
    return response or fallback_transfer_screening(payload)


@app.post('/compliance/screen/residency', summary='Feature 3 residency compliance screening', description='Proxies a residency screening request to the compliance service and falls back to a deterministic local policy response if the service is unavailable.')
def compliance_screen_residency(payload: dict[str, Any]) -> dict[str, Any]:
    response = proxy_compliance('screen/residency', payload)
    return response or fallback_residency_screening(payload)


@app.get('/compliance/policy/state', summary='Feature 3 compliance policy state', description='Returns live compliance policy state when the compliance service is available and fallback demo policy state otherwise.')
def compliance_policy_state() -> dict[str, Any]:
    response = request_json('GET', f'{COMPLIANCE_SERVICE_URL}/policy/state', None, COMPLIANCE_SERVICE_TIMEOUT_SECONDS)
    return response or fallback_compliance_dashboard()['policy_state']


@app.get('/compliance/governance/actions', summary='Feature 3 governance actions list', description='Returns governance actions from the compliance service or fallback demo ledger actions when unavailable.')
def compliance_governance_actions() -> list[dict[str, Any]]:
    response = request_json('GET', f'{COMPLIANCE_SERVICE_URL}/governance/actions', None, COMPLIANCE_SERVICE_TIMEOUT_SECONDS)
    return response or fallback_compliance_dashboard()['latest_governance_actions']


@app.get('/compliance/governance/actions/{action_id}', summary='Feature 3 governance action detail', description='Returns one governance action from the compliance service or fallback data when unavailable.')
def compliance_governance_action(action_id: str) -> dict[str, Any]:
    response = request_json('GET', f'{COMPLIANCE_SERVICE_URL}/governance/actions/{action_id}', None, COMPLIANCE_SERVICE_TIMEOUT_SECONDS)
    if response is not None:
        return response
    for action in fallback_compliance_dashboard()['latest_governance_actions']:
        if action['action_id'] == action_id:
            return action
    return {'detail': f'Unknown action_id: {action_id}', 'source': 'fallback', 'degraded': True}


@app.post('/compliance/governance/actions', summary='Feature 3 governance action create', description='Creates a governance action via the compliance service or records a deterministic fallback action when the service is unavailable.')
def compliance_create_governance_action(payload: dict[str, Any]) -> dict[str, Any]:
    response = proxy_compliance('governance/actions', payload)
    return response or fallback_governance_action(payload)


@app.get('/resilience/dashboard', summary='Feature 4 resilience dashboard feed', description='Returns the reconciliation-service dashboard payload when available and explicit fallback resilience data when the service is unavailable.')
def resilience_dashboard() -> dict[str, Any]:
    payload = fetch_resilience_dashboard()
    return payload or fallback_resilience_dashboard()


@app.post('/resilience/reconcile/state', summary='Feature 4 cross-chain reconciliation', description='Proxies a reconciliation request to the reconciliation-service and falls back to a deterministic local reconciliation summary if the service is unavailable.')
def resilience_reconcile_state(payload: dict[str, Any]) -> dict[str, Any]:
    response = proxy_resilience_post('reconcile/state', payload)
    return response or fallback_reconcile_state(payload)


@app.post('/resilience/backstop/evaluate', summary='Feature 4 liquidity backstop evaluation', description='Proxies a backstop evaluation request to the reconciliation-service and falls back to deterministic local safeguards when the service is unavailable.')
def resilience_backstop_evaluate(payload: dict[str, Any]) -> dict[str, Any]:
    response = proxy_resilience_post('backstop/evaluate', payload)
    return response or fallback_backstop_evaluate(payload)


@app.post('/resilience/incidents/record', summary='Feature 4 resilience incident create', description='Creates a resilience incident via the reconciliation-service or records a deterministic fallback incident when the service is unavailable.')
def resilience_record_incident(payload: dict[str, Any]) -> dict[str, Any]:
    response = proxy_resilience_post('incidents/record', payload)
    return response or fallback_incident_record(payload)


@app.get('/resilience/incidents', summary='Feature 4 resilience incident list', description='Returns resilience incidents from the reconciliation-service or fallback incident ledger rows when unavailable.')
def resilience_incidents() -> list[dict[str, Any]]:
    response = proxy_resilience_get('incidents')
    return response or fallback_resilience_dashboard()['latest_incidents']


@app.get('/resilience/incidents/{event_id}', summary='Feature 4 resilience incident detail', description='Returns one resilience incident from the reconciliation-service or fallback data when unavailable.')
def resilience_incident(event_id: str) -> dict[str, Any]:
    response = proxy_resilience_get(f'incidents/{event_id}')
    if response is not None:
        return response
    for incident in fallback_resilience_dashboard()['latest_incidents']:
        if incident['event_id'] == event_id:
            return incident
    return {'detail': f'Unknown event_id: {event_id}', 'source': 'fallback', 'degraded': True}


@app.post('/auth/signup', summary='Create a live-mode pilot user')
def auth_signup(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    enforce_auth_rate_limit(request, 'signup')
    return signup_user(payload, request)


@app.post('/auth/signin', summary='Sign in a live-mode pilot user')
def auth_signin(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    enforce_auth_rate_limit(request, 'signin')
    return signin_user(payload, request)


@app.post('/auth/signout', summary='Sign out a live-mode pilot user')
def auth_signout(request: Request) -> dict[str, Any]:
    return signout_user(request)


@app.get('/auth/me', summary='Current authenticated live-mode user')
def auth_me(request: Request) -> dict[str, Any]:
    return {'mode': pilot_mode(), 'user': authenticate_request(request)}


@app.get('/workspaces', summary='List workspaces for the authenticated user')
def workspaces(request: Request) -> dict[str, Any]:
    return list_user_workspaces(request)


@app.post('/workspaces', summary='Create a workspace for the authenticated user')
def workspace_create(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return {'user': create_workspace_for_user(payload, request)}


@app.post('/auth/select-workspace', summary='Select the active workspace for the authenticated user')
def auth_select_workspace(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    workspace_id = str(payload.get('workspace_id', '')).strip()
    if not workspace_id:
        raise HTTPException(status_code=400, detail='workspace_id is required')
    return {'user': select_workspace_for_user(workspace_id, request)}


@app.get('/pilot/history', summary='Workspace-scoped persisted live-mode history')
def pilot_history(request: Request, limit: int = 25) -> dict[str, Any]:
    return build_history_response(request, limit=limit)


def _persist_live_analysis(request: Request, payload: dict[str, Any], response_payload: dict[str, Any], *, analysis_type: str, service_name: str, title: str) -> dict[str, Any]:
    if not live_mode_enabled():
        raise HTTPException(status_code=503, detail='Live pilot mode is not enabled.')
    if 'authorization' not in request.headers:
        raise HTTPException(status_code=401, detail='Authorization is required for live pilot actions.')
    with pg_connection() as connection:
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        analysis_run_id = persist_analysis_run(
            connection,
            workspace_id=workspace_context['workspace_id'],
            user_id=user['id'],
            analysis_type=analysis_type,
            service_name=service_name,
            title=title,
            status_value='completed',
            request_payload=payload,
            response_payload=response_payload,
            request=request,
        )
        maybe_insert_alert(
            connection,
            workspace_id=workspace_context['workspace_id'],
            user_id=user['id'],
            analysis_run_id=analysis_run_id,
            alert_type=analysis_type,
            title=title,
            response_payload=response_payload,
        )
        connection.commit()
        return {
            **response_payload,
            'pilot_saved': True,
            'analysis_run_id': analysis_run_id,
            'workspace': workspace_context['workspace'],
        }


@app.post('/pilot/threat/analyze/contract', summary='Run and persist a contract threat analysis for live mode')
def pilot_threat_analyze_contract(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    response = proxy_threat('contract', payload) or fallback_contract_analysis(payload)
    return _persist_live_analysis(request, payload, response, analysis_type='threat_contract', service_name='threat-engine', title='Threat contract analysis')


@app.post('/pilot/threat/analyze/transaction', summary='Run and persist a transaction threat analysis for live mode')
def pilot_threat_analyze_transaction(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    response = proxy_threat('transaction', payload) or fallback_transaction_analysis(payload)
    return _persist_live_analysis(request, payload, response, analysis_type='threat_transaction', service_name='threat-engine', title='Threat transaction analysis')


@app.post('/pilot/threat/analyze/market', summary='Run and persist a market threat analysis for live mode')
def pilot_threat_analyze_market(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    response = proxy_threat('market', payload) or fallback_market_analysis(payload)
    return _persist_live_analysis(request, payload, response, analysis_type='threat_market', service_name='threat-engine', title='Threat market analysis')


@app.post('/pilot/compliance/screen/transfer', summary='Run and persist a transfer compliance screen for live mode')
def pilot_compliance_screen_transfer(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    response = proxy_compliance('screen/transfer', payload) or fallback_transfer_screening(payload)
    return _persist_live_analysis(request, payload, response, analysis_type='compliance_transfer', service_name='compliance-service', title='Compliance transfer screening')


@app.post('/pilot/compliance/screen/residency', summary='Run and persist a residency compliance screen for live mode')
def pilot_compliance_screen_residency(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    response = proxy_compliance('screen/residency', payload) or fallback_residency_screening(payload)
    return _persist_live_analysis(request, payload, response, analysis_type='compliance_residency', service_name='compliance-service', title='Compliance residency screening')


@app.post('/pilot/compliance/governance/actions', summary='Create and persist a governance action for live mode')
def pilot_compliance_governance_action(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    response = proxy_compliance('governance/actions', payload) or fallback_governance_action(payload)
    with pg_connection() as connection:
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        analysis_run_id = persist_analysis_run(
            connection,
            workspace_id=workspace_context['workspace_id'],
            user_id=user['id'],
            analysis_type='governance_action',
            service_name='compliance-service',
            title='Governance action',
            status_value='completed',
            request_payload=payload,
            response_payload=response,
            request=request,
        )
        governance_action_id = create_governance_action_record(
            connection,
            workspace_id=workspace_context['workspace_id'],
            user_id=user['id'],
            analysis_run_id=analysis_run_id,
            payload=payload,
            response_payload=response,
        )
        maybe_insert_alert(
            connection,
            workspace_id=workspace_context['workspace_id'],
            user_id=user['id'],
            analysis_run_id=analysis_run_id,
            alert_type='governance_action',
            title=str(response.get('action_type') or payload.get('action_type') or 'Governance action'),
            response_payload=response,
        )
        log_audit(connection, action='governance.action', entity_type='governance_action', entity_id=governance_action_id, request=request, user_id=user['id'], workspace_id=workspace_context['workspace_id'], metadata={'target_id': response.get('target_id') or payload.get('target_id')})
        connection.commit()
    return {**response, 'pilot_saved': True, 'analysis_run_id': analysis_run_id, 'governance_action_id': governance_action_id, 'workspace': workspace_context['workspace']}


@app.post('/pilot/resilience/reconcile/state', summary='Run and persist a reconciliation analysis for live mode')
def pilot_resilience_reconcile_state(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    response = proxy_resilience_post('reconcile/state', payload) or fallback_reconcile_state(payload)
    return _persist_live_analysis(request, payload, response, analysis_type='resilience_reconcile', service_name='reconciliation-service', title='Resilience reconciliation')


@app.post('/pilot/resilience/backstop/evaluate', summary='Run and persist a backstop analysis for live mode')
def pilot_resilience_backstop_evaluate(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    response = proxy_resilience_post('backstop/evaluate', payload) or fallback_backstop_evaluate(payload)
    return _persist_live_analysis(request, payload, response, analysis_type='resilience_backstop', service_name='reconciliation-service', title='Resilience backstop evaluation')


@app.post('/pilot/resilience/incidents/record', summary='Create and persist a resilience incident for live mode')
def pilot_resilience_record_incident(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    response = proxy_resilience_post('incidents/record', payload) or fallback_incident_record(payload)
    with pg_connection() as connection:
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        analysis_run_id = persist_analysis_run(
            connection,
            workspace_id=workspace_context['workspace_id'],
            user_id=user['id'],
            analysis_type='resilience_incident',
            service_name='reconciliation-service',
            title='Resilience incident',
            status_value='completed',
            request_payload=payload,
            response_payload=response,
            request=request,
        )
        incident_id = create_incident_record(
            connection,
            workspace_id=workspace_context['workspace_id'],
            user_id=user['id'],
            analysis_run_id=analysis_run_id,
            payload=payload,
            response_payload=response,
        )
        maybe_insert_alert(
            connection,
            workspace_id=workspace_context['workspace_id'],
            user_id=user['id'],
            analysis_run_id=analysis_run_id,
            alert_type='resilience_incident',
            title=str(response.get('event_type') or payload.get('event_type') or 'Resilience incident'),
            response_payload=response,
        )
        log_audit(connection, action='incident.record', entity_type='incident', entity_id=incident_id, request=request, user_id=user['id'], workspace_id=workspace_context['workspace_id'], metadata={'severity': response.get('severity') or payload.get('severity')})
        connection.commit()
    return {**response, 'pilot_saved': True, 'analysis_run_id': analysis_run_id, 'incident_id': incident_id, 'workspace': workspace_context['workspace']}


def build_risk_dashboard_queue() -> list[dict[str, Any]]:
    sample_request = load_json_file(RISK_ENGINE_DATA_DIR, 'sample_risk_request.json', DEFAULT_RISK_SAMPLE_REQUEST)
    suspicious_events = load_json_file(RISK_ENGINE_DATA_DIR, 'suspicious_market_events.json', DEFAULT_SUSPICIOUS_MARKET_EVENTS)
    normal_events = load_json_file(RISK_ENGINE_DATA_DIR, 'normal_market_events.json', DEFAULT_NORMAL_MARKET_EVENTS)

    definitions = [
        {
            'id': 'txn-001',
            'label': 'Flash-loan router rebalance',
            'request': build_flash_loan_request(sample_request, suspicious_events),
            'fallback': {
                'risk_score': 100,
                'recommendation': 'BLOCK',
                'explanation': 'Aggregate score 100 produced recommendation BLOCK. Primary drivers: low-level liquidity drain, flash-loan routing, and weak wallet reputation.',
                'triggered_rules': [
                    {'rule_id': 'runtime:liquidity-drain', 'severity': 'critical', 'summary': 'Observed recent liquidity contraction matches flash-loan drain behavior.'},
                    {'rule_id': 'pre:wallet-reputation', 'severity': 'high', 'summary': 'Wallet reputation is weak relative to defensive transaction policy.'},
                    {'rule_id': 'market:cancel-burst', 'severity': 'medium', 'summary': 'Elevated order cancellation ratio suggests quote stuffing or spoofing.'},
                ],
            },
        },
        {
            'id': 'txn-002',
            'label': 'Treasury settlement transfer',
            'request': build_allow_request(sample_request, normal_events),
            'fallback': {
                'risk_score': 6,
                'recommendation': 'ALLOW',
                'explanation': 'Known-safe treasury settlement has verified contract metadata and no defensive heuristics triggered.',
                'triggered_rules': [],
            },
        },
        {
            'id': 'txn-003',
            'label': 'Proxy rebalance multicall',
            'request': build_review_request(sample_request, normal_events),
            'fallback': {
                'risk_score': 52,
                'recommendation': 'REVIEW',
                'explanation': 'Aggregate score 52 produced recommendation REVIEW. Primary drivers: privileged arguments, unaudited proxy behavior, and weak wallet reputation.',
                'triggered_rules': [
                    {'rule_id': 'pre:wallet-reputation', 'severity': 'high', 'summary': 'Wallet reputation is weak relative to defensive transaction policy.'},
                    {'rule_id': 'pre:privileged-args', 'severity': 'medium', 'summary': 'Call arguments include privileged control fields.'},
                    {'rule_id': 'static:unaudited-proxy', 'severity': 'medium', 'summary': 'Proxy contract without audits increases implementation-switch risk.'},
                ],
            },
        },
        {
            'id': 'txn-004',
            'label': 'Mixer withdrawal sweep',
            'request': build_mixer_request(sample_request, suspicious_events),
            'fallback': {
                'risk_score': 93,
                'recommendation': 'BLOCK',
                'explanation': 'Mixer-associated sweep touches laundering indicators and elevated market anomalies, so the engine recommends BLOCK.',
                'triggered_rules': [
                    {'rule_id': 'static:mixer-category', 'severity': 'critical', 'summary': 'Contract category is associated with obfuscation or laundering workflows.'},
                    {'rule_id': 'pre:high-value', 'severity': 'high', 'summary': 'Transaction notional exceeds the Phase 1 high-value threshold.'},
                    {'rule_id': 'market:spoofing-reversal', 'severity': 'high', 'summary': 'Price moved sharply and reverted quickly, consistent with spoofing pressure.'},
                ],
            },
        },
    ]

    queue: list[dict[str, Any]] = []
    for offset, definition in enumerate(definitions):
        evaluation = evaluate_live_risk(definition['request'])
        live_data = evaluation is not None
        result = evaluation or definition['fallback']
        queue.append(
            {
                'id': definition['id'],
                'label': definition['label'],
                'request': definition['request'],
                'evaluation': result,
                'live_data': live_data,
                'updated_at': iso_timestamp(offset),
            }
        )
    return queue


def build_flash_loan_request(sample_request: dict[str, Any], suspicious_events: list[dict[str, Any]]) -> dict[str, Any]:
    request = deepcopy(sample_request)
    request['recent_market_events'] = suspicious_events
    request['transaction_payload']['metadata']['queue_position'] = 1
    return request


def build_allow_request(sample_request: dict[str, Any], normal_events: list[dict[str, Any]]) -> dict[str, Any]:
    request = deepcopy(sample_request)
    request['transaction_payload'].update(
        {
            'tx_hash': '0xphase1allow',
            'from_address': '0x5555555555555555555555555555555555555555',
            'to_address': '0x6666666666666666666666666666666666666666',
            'value': 125000.0,
            'gas_price': 18.0,
            'token_transfers': [{'token': 'USTB', 'amount': 125000}],
            'metadata': {'contains_flash_loan_hop': False, 'entrypoint': 'treasury-settlement'},
        }
    )
    request['decoded_function_call'].update(
        {
            'function_name': 'settle',
            'contract_name': 'TreasurySettlement',
            'arguments': {'beneficiary': '0x7777777777777777777777777777777777777777', 'amount': 125000},
            'selectors': ['0xfeedbeef'],
        }
    )
    request['wallet_reputation'].update(
        {
            'address': '0x5555555555555555555555555555555555555555',
            'score': 92,
            'prior_flags': 0,
            'account_age_days': 640,
            'kyc_verified': True,
            'known_safe': True,
            'recent_counterparties': 4,
            'metadata': {'desk': 'treasury-ops'},
        }
    )
    request['contract_metadata'].update(
        {
            'address': '0x6666666666666666666666666666666666666666',
            'contract_name': 'TreasurySettlement',
            'verified_source': True,
            'proxy': False,
            'created_days_ago': 410,
            'audit_count': 3,
            'categories': ['treasury', 'settlement'],
            'static_flags': {},
            'metadata': {'review_status': 'approved'},
        }
    )
    request['recent_market_events'] = normal_events
    return request


def build_review_request(sample_request: dict[str, Any], normal_events: list[dict[str, Any]]) -> dict[str, Any]:
    request = deepcopy(sample_request)
    request['transaction_payload'].update(
        {
            'tx_hash': '0xphase1review',
            'from_address': '0x8888888888888888888888888888888888888888',
            'to_address': '0x9999999999999999999999999999999999999999',
            'value': 420000.0,
            'gas_price': 31.0,
            'token_transfers': [
                {'token': 'USTB', 'amount': 300000},
                {'token': 'USDC', 'amount': 120000},
            ],
            'metadata': {'contains_flash_loan_hop': False, 'entrypoint': 'rebalance-router'},
        }
    )
    request['decoded_function_call'].update(
        {
            'function_name': 'multicall',
            'contract_name': 'ProxyPortfolioManager',
            'arguments': {
                'owner': '0x1010101010101010101010101010101010101010',
                'router': '0x1212121212121212121212121212121212121212',
                'steps': 2,
            },
            'selectors': ['0x5ae401dc'],
        }
    )
    request['wallet_reputation'].update(
        {
            'address': '0x8888888888888888888888888888888888888888',
            'score': 32,
            'prior_flags': 1,
            'account_age_days': 38,
            'kyc_verified': False,
            'known_safe': False,
            'recent_counterparties': 14,
            'metadata': {'desk': 'external-rebalancer'},
        }
    )
    request['contract_metadata'].update(
        {
            'address': '0x9999999999999999999999999999999999999999',
            'contract_name': 'ProxyPortfolioManager',
            'verified_source': True,
            'proxy': True,
            'created_days_ago': 180,
            'audit_count': 0,
            'categories': ['portfolio', 'router'],
            'static_flags': {'obfuscated_storage': True},
            'metadata': {'upgrade_notice': 'pending governance review'},
        }
    )
    request['recent_market_events'] = normal_events
    return request


def build_mixer_request(sample_request: dict[str, Any], suspicious_events: list[dict[str, Any]]) -> dict[str, Any]:
    request = deepcopy(sample_request)
    request['transaction_payload'].update(
        {
            'tx_hash': '0xphase1block',
            'from_address': '0x1313131313131313131313131313131313131313',
            'to_address': '0x1414141414141414141414141414141414141414',
            'value': 1450000.0,
            'gas_price': 64.0,
            'token_transfers': [
                {'token': 'USTB', 'amount': 800000},
                {'token': 'USDC', 'amount': 350000},
                {'token': 'DAI', 'amount': 300000},
                {'token': 'WETH', 'amount': 180},
            ],
            'metadata': {'contains_flash_loan_hop': False, 'entrypoint': 'withdrawal-sweeper'},
        }
    )
    request['decoded_function_call'].update(
        {
            'function_name': 'withdrawAll',
            'contract_name': 'PrivacyMixerVault',
            'arguments': {'admin': '0x1515151515151515151515151515151515151515', 'receiver': '0x1616161616161616161616161616161616161616'},
            'selectors': ['0xdeadc0de'],
        }
    )
    request['wallet_reputation'].update(
        {
            'address': '0x1313131313131313131313131313131313131313',
            'score': 28,
            'prior_flags': 2,
            'account_age_days': 11,
            'kyc_verified': False,
            'known_safe': False,
            'recent_counterparties': 31,
            'metadata': {'watchlist': 'mixer-monitor'},
        }
    )
    request['contract_metadata'].update(
        {
            'address': '0x1414141414141414141414141414141414141414',
            'contract_name': 'PrivacyMixerVault',
            'verified_source': False,
            'proxy': False,
            'created_days_ago': 9,
            'audit_count': 0,
            'categories': ['mixer', 'vault'],
            'static_flags': {'selfdestruct_enabled': True, 'hidden_owner': True},
            'metadata': {'screening_status': 'escalated'},
        }
    )
    request['recent_market_events'] = suspicious_events
    return request


def evaluate_live_risk(payload: dict[str, Any]) -> dict[str, Any] | None:
    return request_json('POST', f'{RISK_ENGINE_URL}/v1/risk/evaluate', payload, RISK_ENGINE_TIMEOUT_SECONDS)


def fetch_compliance_dashboard() -> dict[str, Any] | None:
    payload = request_json('GET', f'{COMPLIANCE_SERVICE_URL}/dashboard', None, COMPLIANCE_SERVICE_TIMEOUT_SECONDS)
    if payload is None:
        return None
    payload['degraded'] = False
    payload['source'] = 'live'
    return payload


def proxy_compliance(path: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    response = request_json('POST', f'{COMPLIANCE_SERVICE_URL}/{path}', payload, COMPLIANCE_SERVICE_TIMEOUT_SECONDS)
    if response is None:
        return None
    response['source'] = 'live'
    response['degraded'] = False
    return response


def fetch_resilience_dashboard() -> dict[str, Any] | None:
    payload = request_json('GET', f'{RECONCILIATION_SERVICE_URL}/dashboard', None, RECONCILIATION_SERVICE_TIMEOUT_SECONDS)
    if payload is None:
        return None
    payload['degraded'] = False
    payload['source'] = 'live'
    return payload


def proxy_resilience_get(path: str) -> dict[str, Any] | list[dict[str, Any]] | None:
    response = request_json('GET', f'{RECONCILIATION_SERVICE_URL}/{path}', None, RECONCILIATION_SERVICE_TIMEOUT_SECONDS)
    if response is None:
        return None
    if isinstance(response, dict):
        response['source'] = response.get('source', 'live')
        response['degraded'] = False
    return response


def proxy_resilience_post(path: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    response = request_json('POST', f'{RECONCILIATION_SERVICE_URL}/{path}', payload, RECONCILIATION_SERVICE_TIMEOUT_SECONDS)
    if response is None:
        return None
    response['source'] = 'live'
    response['degraded'] = False
    return response


def fetch_threat_dashboard() -> dict[str, Any] | None:
    payload = request_json('GET', f'{THREAT_ENGINE_URL}/dashboard', None, THREAT_ENGINE_TIMEOUT_SECONDS)
    if payload is None:
        return None
    payload['degraded'] = False
    return payload


def proxy_threat(kind: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    response = request_json('POST', f'{THREAT_ENGINE_URL}/analyze/{kind}', payload, THREAT_ENGINE_TIMEOUT_SECONDS)
    if response is None:
        return None
    response['source'] = 'live'
    response['degraded'] = False
    return response


def request_json(method: str, url: str, payload: dict[str, Any] | None, timeout_seconds: float) -> dict[str, Any] | None:
    request = UrlRequest(
        url,
        data=json.dumps(payload).encode('utf-8') if payload is not None else None,
        headers={'Content-Type': 'application/json'},
        method=method,
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode('utf-8'))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
        return None


def fallback_compliance_dashboard() -> dict[str, Any]:
    return {
        'source': 'fallback',
        'degraded': True,
        'generated_at': '2026-03-18T11:00:00Z',
        'summary': {
            'allowlisted_wallet_count': 2,
            'blocklisted_wallet_count': 1,
            'frozen_wallet_count': 1,
            'review_required_wallet_count': 1,
            'paused_asset_count': 1,
            'latest_transfer_decision': 'review',
            'latest_residency_decision': 'denied',
            'triggered_rule_count': 3,
        },
        'cards': [
            {'label': 'Transfer decision', 'value': 'review', 'detail': 'Fallback wrapper decision indicates manual review until the compliance service is back online.', 'tone': 'high'},
            {'label': 'Compliance risk', 'value': 'high', 'detail': 'Fallback deterministic wrapper rules remain available at the gateway.', 'tone': 'high'},
            {'label': 'Governance actions', 'value': '3', 'detail': 'Fallback immutable-style action log stays visible in degraded mode.', 'tone': 'medium'},
            {'label': 'Residency decision', 'value': 'denied', 'detail': 'Fallback residency routing keeps sovereignty restrictions explainable.', 'tone': 'critical'},
        ],
        'transfer_screening': {
            'decision': 'review',
            'risk_level': 'high',
            'reasons': ['One or more wallets have incomplete or pending KYC status.', 'A participating jurisdiction requires manual review.'],
            'triggered_rules': [
                {'rule_id': 'kyc-status', 'outcome': 'review', 'summary': 'One or more wallets have incomplete or pending KYC status.'},
                {'rule_id': 'jurisdiction-policy', 'outcome': 'review', 'summary': 'A participating jurisdiction requires manual review.'},
                {'rule_id': 'wallet-allowlist', 'outcome': 'pass', 'summary': 'At least one participating wallet is allowlisted or tagged as trusted.'},
            ],
            'recommended_action': 'Escalate to compliance operations for manual approval.',
            'wrapper_status': 'wrapper-hold',
            'explainability_summary': 'Decision review: One or more wallets have incomplete or pending KYC status.',
            'policy_snapshot': {
                'allowlisted_wallets': 2,
                'blocklisted_wallets': 1,
                'frozen_wallets': 1,
                'review_required_wallets': 1,
                'paused_assets': ['USTB-2026'],
            },
        },
        'residency_screening': {
            'residency_decision': 'denied',
            'policy_violations': ['Requested processing region is on the restricted region list.', 'Requested processing region is not on the approved cloud region list.'],
            'routing_recommendation': 'Route processing to eu-west or request governance override.',
            'governance_status': 'restricted',
            'explainability_summary': 'Requested processing region is on the restricted region list.; Requested processing region is not on the approved cloud region list.',
            'allowed_region_outcome': 'eu-west',
        },
        'policy_state': {
            'allowlisted_wallets': ['0xaaa0000000000000000000000000000000000101', '0xbbb0000000000000000000000000000000000202'],
            'blocklisted_wallets': ['0xblocked000000000000000000000000000000003'],
            'frozen_wallets': ['0xddd0000000000000000000000000000000000404'],
            'review_required_wallets': ['0xreview000000000000000000000000000000004'],
            'paused_assets': ['USTB-2026'],
            'approved_cloud_regions': ['us-east', 'us-central', 'eu-west'],
            'friendly_regions': ['us-east', 'us-central', 'eu-west', 'sg-gov'],
            'restricted_regions': ['cn-north', 'ru-central', 'ir-gov'],
            'action_count': 3,
            'latest_action_id': 'gov-fallback-003',
        },
        'latest_governance_actions': [
            {'action_id': 'gov-fallback-003', 'created_at': '2026-03-18T11:02:00Z', 'action_type': 'pause_asset_transfers', 'target_type': 'asset', 'target_id': 'USTB-2026', 'status': 'applied', 'reason': 'Pause asset transfers while wrapper thresholds are recalibrated.', 'actor': 'governance-multisig', 'related_asset_id': 'USTB-2026', 'metadata': {'ticket': 'CMP-1043'}, 'attestation_hash': 'fallback-003', 'policy_effects': ['Asset USTB-2026 transfer activity paused.']},
            {'action_id': 'gov-fallback-002', 'created_at': '2026-03-18T11:01:00Z', 'action_type': 'allowlist_wallet', 'target_type': 'wallet', 'target_id': '0xeee0000000000000000000000000000000000505', 'status': 'applied', 'reason': 'Approved new qualified custodian wallet for primary market settlements.', 'actor': 'governance-multisig', 'related_asset_id': 'USTB-2026', 'metadata': {'ticket': 'CMP-1044'}, 'attestation_hash': 'fallback-002', 'policy_effects': ['Wallet 0xeee0000000000000000000000000000000000505 added to allowlist.']},
            {'action_id': 'gov-fallback-001', 'created_at': '2026-03-18T11:00:00Z', 'action_type': 'freeze_wallet', 'target_type': 'wallet', 'target_id': '0xddd0000000000000000000000000000000000404', 'status': 'applied', 'reason': 'Escalated compliance review after repeated sanctions-adjacent transfers.', 'actor': 'governance-multisig', 'related_asset_id': 'USTB-2026', 'metadata': {'ticket': 'CMP-1042'}, 'attestation_hash': 'fallback-001', 'policy_effects': ['Wallet 0xddd0000000000000000000000000000000000404 frozen.']},
        ],
        'asset_transfer_status': [
            {'asset_id': 'USTB-2026', 'status': 'paused'},
            {'asset_id': 'USTB-2027', 'status': 'active'},
        ],
        'sample_scenarios': {
            'compliant-transfer-approved': 'Compliant transfer that should be approved.',
            'blocked-transfer-sanctions': 'Transfer blocked because sanctions screening failed.',
            'blocked-transfer-blocklist': 'Transfer blocked because a wallet is blocklisted.',
            'review-transfer-incomplete-kyc': 'Transfer sent to review because KYC is incomplete.',
            'review-transfer-restricted-jurisdiction': 'Transfer sent to review due to restricted jurisdiction policy.',
            'denied-residency-restricted-region': 'Residency request denied due to restricted processing region.',
            'governance-freeze-wallet': 'Governance action freezing a wallet.',
            'governance-pause-asset': 'Governance action pausing asset transfers.',
            'governance-allowlist-wallet': 'Governance action allowlisting a wallet.',
            'transfer-blocked-because-asset-paused': 'Transfer blocked because the asset is paused.',
        },
        'message': 'Compliance service unavailable or timed out. Returning explicit fallback policy wrappers and governance ledger records so Feature 3 remains demoable.',
    }


def fallback_transfer_screening(payload: dict[str, Any]) -> dict[str, Any]:
    policy = payload.get('asset_transfer_policy', {})
    sanctions = payload.get('sender_sanctions_flag') or payload.get('receiver_sanctions_flag')
    blocklisted = payload.get('sender_wallet') == '0xblocked000000000000000000000000000000003' or payload.get('receiver_wallet') == '0xblocked000000000000000000000000000000003'
    asset_paused = policy.get('asset_status') == 'paused'
    incomplete_kyc = payload.get('sender_kyc_status') != 'verified' or payload.get('receiver_kyc_status') != 'verified'
    review_jurisdictions = set(policy.get('review_jurisdictions', []))
    restricted_jurisdictions = set(policy.get('restricted_jurisdictions', []))
    jurisdictions = {payload.get('sender_jurisdiction'), payload.get('receiver_jurisdiction')}
    triggered_rules = []
    reasons = []
    decision = 'approved'
    risk_level = 'low'

    def add(rule_id: str, outcome: str, summary: str) -> None:
        nonlocal decision, risk_level
        triggered_rules.append({'rule_id': rule_id, 'outcome': outcome, 'summary': summary})
        if outcome != 'pass':
            reasons.append(summary)
        if outcome == 'block':
            decision = 'blocked'
            risk_level = 'critical'
        elif outcome == 'review' and decision != 'blocked':
            decision = 'review'
            risk_level = 'high'

    add('sanctions-screen', 'block' if sanctions else 'pass', 'Sanctions/watchlist screening failed for one or more wallets.' if sanctions else 'No sanctions/watchlist hits detected.')
    add('wallet-blocklist', 'block' if blocklisted else 'pass', 'A participating wallet is currently blocklisted by governance policy.' if blocklisted else 'No participating wallets are blocklisted.')
    add('asset-transfer-status', 'block' if asset_paused else 'pass', 'Asset transfers are currently paused for this asset.' if asset_paused else 'Asset transfer status is active.')
    add('kyc-status', 'review' if incomplete_kyc else 'pass', 'One or more wallets have incomplete or pending KYC status.' if incomplete_kyc else 'Sender and receiver KYC controls are complete.')
    jurisdiction_review = bool(jurisdictions & (restricted_jurisdictions | review_jurisdictions))
    add('jurisdiction-policy', 'review' if jurisdiction_review and decision != 'blocked' else 'pass', 'A participating jurisdiction requires manual review.' if jurisdiction_review and decision != 'blocked' else 'Jurisdiction controls passed.')

    return {
        'decision': decision,
        'risk_level': risk_level,
        'reasons': reasons or ['All required compliance controls passed.'],
        'triggered_rules': triggered_rules,
        'recommended_action': 'Reject the transfer and record an exception in governance audit logs.' if decision == 'blocked' else 'Escalate to compliance operations for manual approval.' if decision == 'review' else 'Proceed with wrapped transfer execution.',
        'wrapper_status': 'wrapper-blocked' if decision == 'blocked' else 'wrapper-hold' if decision == 'review' else 'wrapper-clear',
        'explainability_summary': f"Decision {decision}: {(reasons or ['all required compliance controls passed'])[0]}",
        'policy_snapshot': fallback_compliance_dashboard()['policy_state'],
        'source': 'fallback',
        'degraded': True,
    }


def fallback_residency_screening(payload: dict[str, Any]) -> dict[str, Any]:
    approved = set(payload.get('approved_regions', []))
    restricted = set(payload.get('restricted_regions', []))
    requested = payload.get('requested_processing_region')
    violations = []
    if requested in restricted:
        violations.append('Requested processing region is on the restricted region list.')
    if requested not in approved:
        violations.append('Requested processing region is not on the approved cloud region list.')
    if payload.get('sensitivity_level') == 'sovereign' and not str(payload.get('cloud_environment', '')).startswith('sovereign'):
        violations.append('Sovereign data requires a sovereign cloud environment.')
    decision = 'denied' if violations else 'allowed'
    return {
        'residency_decision': decision,
        'policy_violations': violations,
        'routing_recommendation': 'Route processing to eu-west or request governance override.' if violations else f"Route processing to {requested} in {payload.get('cloud_environment')}",
        'governance_status': 'restricted' if violations else 'normal',
        'explainability_summary': '; '.join(violations) if violations else 'Residency controls passed without violations.',
        'allowed_region_outcome': 'eu-west' if violations else requested,
        'source': 'fallback',
        'degraded': True,
    }


def fallback_governance_action(payload: dict[str, Any]) -> dict[str, Any]:
    attestation = f"fallback-{payload.get('action_type', 'action')}-{payload.get('target_id', 'target')}"
    effect = f"Fallback governance action {payload.get('action_type')} applied to {payload.get('target_id')}."
    return {
        **payload,
        'action_id': 'gov-fallback-new',
        'created_at': '2026-03-18T11:05:00Z',
        'status': 'applied',
        'attestation_hash': attestation,
        'policy_effects': [effect],
        'source': 'fallback',
        'degraded': True,
    }


def fallback_resilience_dashboard() -> dict[str, Any]:
    reconciliation_payload = load_json_file(
        RECONCILIATION_DATA_DIR,
        'critical_supply_divergence_double_count_risk.json',
        DEFAULT_RECONCILIATION_STATE,
    )
    backstop_payload = load_json_file(
        RECONCILIATION_DATA_DIR,
        'critical_mismatch_paused_bridge.json',
        DEFAULT_BACKSTOP_STATE,
    )
    return {
        'source': 'fallback',
        'degraded': True,
        'generated_at': '2026-03-18T12:00:00Z',
        'summary': {
            'reconciliation_status': 'critical',
            'severity_score': 82,
            'mismatch_amount': 191400.0,
            'stale_ledger_count': 1,
            'backstop_decision': 'paused',
            'incident_count': 2,
        },
        'cards': [
            {'label': 'Reconciliation', 'value': 'critical', 'detail': 'Fallback resilience dashboard detected material supply divergence across multiple ledgers.', 'tone': 'critical'},
            {'label': 'Mismatch amount', 'value': '191,400', 'detail': 'Fallback normalized supply mismatch vs expected total supply.', 'tone': 'critical'},
            {'label': 'Stale ledgers', 'value': '1', 'detail': 'Fallback stale-ledger penalty remains visible when the service is offline.', 'tone': 'warning'},
            {'label': 'Backstop', 'value': 'paused', 'detail': 'Fallback safeguards paused bridge and settlement lanes.', 'tone': 'critical'},
        ],
        'reconciliation_result': fallback_reconcile_state(reconciliation_payload),
        'backstop_result': fallback_backstop_evaluate(backstop_payload),
        'latest_incidents': [
            {'event_id': 'evt-fallback-0002', 'created_at': '2026-03-18T11:52:00Z', 'event_type': 'market-circuit-breaker', 'trigger_source': 'backstop-engine', 'related_asset_id': 'USTB-2026', 'affected_assets': ['USTB-2026'], 'affected_ledgers': ['ethereum', 'avalanche'], 'severity': 'high', 'status': 'contained', 'summary': 'Fallback circuit breaker event kept trading paused while cyber scores were elevated.', 'metadata': {'scenario': 'cyber-triggered-restricted-mode'}, 'attestation_hash': 'fallback-event-0002', 'fingerprint': 'fallback-event-00', 'source': 'fallback', 'degraded': True},
            {'event_id': 'evt-fallback-0001', 'created_at': '2026-03-18T11:45:00Z', 'event_type': 'reconciliation-failure', 'trigger_source': 'reconciliation-engine', 'related_asset_id': 'USTB-2026', 'affected_assets': ['USTB-2026'], 'affected_ledgers': ['ethereum', 'avalanche', 'private-bank-ledger'], 'severity': 'critical', 'status': 'open', 'summary': 'Fallback reconciliation incident preserved duplicate mint risk context during service outage.', 'metadata': {'scenario': 'critical-supply-divergence-double-count-risk'}, 'attestation_hash': 'fallback-event-0001', 'fingerprint': 'fallback-event-00', 'source': 'fallback', 'degraded': True},
        ],
        'sample_scenarios': {
            'healthy-matched-multi-ledger-state': 'Healthy matched supply across ethereum, avalanche, and private-bank-ledger.',
            'mild-mismatch-warning': 'Small mismatch with manageable settlement lag.',
            'critical-supply-divergence-double-count-risk': 'Critical over-reporting across ledgers indicating double-count risk.',
            'stale-private-ledger-data': 'Private ledger data is stale and penalized.',
            'high-volatility-alert': 'High volatility produces a deterministic alert decision.',
            'cyber-triggered-restricted-mode': 'Cyber + volatility combination restricts controls.',
            'critical-mismatch-paused-bridge': 'Critical reconciliation mismatch pauses bridge and settlement.',
            'incident-record-reconciliation-failure': 'Incident example for a reconciliation failure.',
            'incident-record-market-circuit-breaker': 'Incident example for a market circuit breaker.',
            'recovery-normal-mode-after-alert': 'Recovery scenario returning to normal mode after prior alert.',
        },
        'message': 'Reconciliation-service unavailable or timed out. Returning explicit fallback resilience data so Feature 4 remains demoable.',
    }


def fallback_reconcile_state(payload: dict[str, Any]) -> dict[str, Any]:
    expected = float(payload.get('expected_total_supply', 0) or 0)
    ledgers = payload.get('ledgers', [])
    observed_total = sum(float(item.get('reported_supply', 0)) for item in ledgers)
    normalized_total = 0.0
    stale_count = 0
    settlement_lag_ledgers: list[str] = []
    over_reporting: list[str] = []
    assessments: list[dict[str, Any]] = []

    for item in ledgers:
        effective = max(float(item.get('reported_supply', 0)) - float(item.get('locked_supply', 0)) - float(item.get('pending_settlement', 0)), 0)
        staleness_minutes = 180 if item.get('ledger_name') == 'private-bank-ledger' and '09:' in str(item.get('last_updated_at', '')) else 20
        penalty = 0.12 if staleness_minutes >= 120 else 0.05 if staleness_minutes >= 45 else 0.0
        normalized_total += effective * float(item.get('reconciliation_weight', 1.0)) * (1 - penalty)
        if penalty:
            stale_count += 1
        lag_flag = float(item.get('pending_settlement', 0)) >= 20000
        if lag_flag:
            settlement_lag_ledgers.append(item.get('ledger_name', 'unknown'))
        over_reported = float(item.get('reported_supply', 0)) > expected * 0.55 if expected else False
        if over_reported:
            over_reporting.append(item.get('ledger_name', 'unknown'))
        assessments.append({
            'ledger_name': item.get('ledger_name', 'unknown'),
            'normalized_effective_supply': round(effective, 2),
            'accepted': True,
            'status': 'penalized' if penalty or lag_flag else 'accepted',
            'staleness_minutes': staleness_minutes,
            'staleness_penalty': penalty,
            'settlement_lag_flag': lag_flag,
            'over_reported_against_expected': over_reported,
            'explanation': 'Fallback reconciliation logic normalized reported supply and applied stale / settlement penalties where necessary.',
        })

    mismatch_amount = round(normalized_total - expected, 2)
    mismatch_percent = round((abs(mismatch_amount) / expected) * 100, 2) if expected else 0.0
    duplicate_risk = len(over_reporting) >= 2
    severity_score = min(100, int(round(mismatch_percent * 4 + stale_count * 12 + len(settlement_lag_ledgers) * 8 + (24 if duplicate_risk else 0))))
    status = 'critical' if severity_score >= 70 or mismatch_percent >= 8 or duplicate_risk else 'warning' if severity_score >= 25 or stale_count or settlement_lag_ledgers else 'matched'

    return {
        'asset_id': payload.get('asset_id', 'USTB-2026'),
        'reconciliation_status': status,
        'expected_total_supply': expected,
        'observed_total_supply': round(observed_total, 2),
        'normalized_effective_supply': round(normalized_total, 2),
        'mismatch_amount': mismatch_amount,
        'mismatch_percent': mismatch_percent,
        'severity_score': severity_score,
        'duplicate_or_double_count_risk': duplicate_risk,
        'stale_ledger_count': stale_count,
        'settlement_lag_ledgers': settlement_lag_ledgers,
        'mismatch_summary': ['Fallback gateway detected supply drift requiring operator review.'],
        'recommendations': ['Refresh stale ledgers.', 'Investigate bridge mint/burn drift before restoring throughput.'] if status != 'matched' else ['Continue scheduled monitoring.'],
        'explainability_summary': f"Fallback reconciliation {status}: expected {expected:,.0f}, observed {observed_total:,.0f}, normalized {normalized_total:,.0f}.",
        'per_ledger_balances': [
            {'ledger_name': item.get('ledger_name', 'unknown'), 'reported_supply': item.get('reported_supply', 0), 'locked_supply': item.get('locked_supply', 0), 'pending_settlement': item.get('pending_settlement', 0), 'effective_supply': max(float(item.get('reported_supply', 0)) - float(item.get('locked_supply', 0)) - float(item.get('pending_settlement', 0)), 0), 'transfer_count': item.get('transfer_count', 0), 'last_updated_at': item.get('last_updated_at', '')}
            for item in ledgers
        ],
        'ledger_assessments': assessments,
        'source': 'fallback',
        'degraded': True,
    }


def fallback_backstop_evaluate(payload: dict[str, Any]) -> dict[str, Any]:
    triggered: list[str] = []
    actions: list[str] = []
    decision = 'normal'
    trading_status = 'active'
    bridge_status = 'active'
    settlement_status = 'active'

    if float(payload.get('volatility_score', 0)) >= 60:
        triggered.append('soft alert')
        decision = 'alert'
    if float(payload.get('volatility_score', 0)) >= 80:
        triggered.extend(['high-volatility mode', 'reduce transfer threshold'])
        decision = 'restricted'
        trading_status = 'guarded'
    if float(payload.get('cyber_alert_score', 0)) >= 75:
        triggered.append('pause trading')
        decision = 'restricted' if decision != 'paused' else decision
        trading_status = 'paused'
    if float(payload.get('reconciliation_severity', 0)) >= 70:
        triggered.extend(['pause bridge / settlement lane', 'circuit breaker triggered'])
        decision = 'paused'
        bridge_status = 'paused'
        settlement_status = 'paused'
    if float(payload.get('oracle_confidence_score', 100)) <= 45:
        triggered.append('soft alert')
        if decision == 'normal':
            decision = 'alert'
    if float(payload.get('compliance_incident_score', 0)) >= 60:
        triggered.append('reduce transfer threshold')
        if decision == 'normal':
            decision = 'alert'

    if not actions:
        actions = ['Maintain normal operations and keep baseline telemetry active.'] if decision == 'normal' else ['Escalate treasury operations and keep deterministic backstop controls engaged.']
    if bridge_status == 'active' and decision in {'alert', 'restricted'}:
        bridge_status = 'guarded'
    if settlement_status == 'active' and decision == 'restricted':
        settlement_status = 'guarded'
    if trading_status == 'active' and decision == 'alert':
        trading_status = 'watch'

    operational_status = {'normal': 'normal', 'alert': 'stressed', 'restricted': 'restricted', 'paused': 'paused'}[decision]
    return {
        'asset_id': payload.get('asset_id', 'USTB-2026'),
        'backstop_decision': decision,
        'triggered_safeguards': list(dict.fromkeys(triggered)),
        'recommended_actions': actions,
        'operational_status': operational_status,
        'trading_status': trading_status,
        'bridge_status': bridge_status,
        'settlement_status': settlement_status,
        'explainability_summary': f"Fallback backstop decision {decision} for {payload.get('asset_id', 'USTB-2026')}.",
        'source': 'fallback',
        'degraded': True,
    }


def fallback_incident_record(payload: dict[str, Any]) -> dict[str, Any]:
    summary = payload.get('summary', 'Fallback resilience incident recorded locally at the API gateway.')
    return {
        'event_id': 'evt-fallback-new',
        'created_at': '2026-03-18T12:01:00Z',
        'event_type': payload.get('event_type', 'resilience-event'),
        'trigger_source': payload.get('trigger_source', 'api-gateway-fallback'),
        'related_asset_id': payload.get('related_asset_id', 'USTB-2026'),
        'affected_assets': payload.get('affected_assets', [payload.get('related_asset_id', 'USTB-2026')]),
        'affected_ledgers': payload.get('affected_ledgers', []),
        'severity': payload.get('severity', 'medium'),
        'status': payload.get('status', 'open'),
        'summary': summary,
        'metadata': payload.get('metadata', {}),
        'attestation_hash': 'fallback-incident-hash',
        'fingerprint': 'fallback-inciden',
        'source': 'fallback',
        'degraded': True,
    }


def fallback_threat_dashboard() -> dict[str, Any]:
    return {
        'source': 'fallback',
        'degraded': True,
        'generated_at': '2026-03-18T10:00:00Z',
        'summary': {
            'average_score': 64.3,
            'critical_or_high_alerts': 4,
            'blocked_actions': 3,
            'review_actions': 2,
            'market_anomaly_types': [
                'Abnormal volume spike',
                'Spoofing-like order behavior',
                'Wash-trading-like loops',
                'Abnormal rapid swings',
            ],
        },
        'cards': [
            {'label': 'Threat score', 'value': '82', 'detail': 'Fallback contract threat score from bundled Feature 2 scenarios.', 'tone': 'critical'},
            {'label': 'Active alerts', 'value': '4', 'detail': 'Fallback critical and high-confidence detections.', 'tone': 'high'},
            {'label': 'Blocked / reviewed', 'value': '3/2', 'detail': 'Fallback action split when threat-engine is unavailable.', 'tone': 'medium'},
            {'label': 'Market anomaly avg', 'value': '70.0', 'detail': 'Fallback anomaly average across demo market scenarios.', 'tone': 'high'},
        ],
        'active_alerts': [
            {'id': 'det-001', 'category': 'transaction', 'title': 'Suspicious flash-loan-like transaction', 'score': 88, 'severity': 'critical', 'action': 'block', 'source': 'fallback', 'explanation': 'Fallback analysis flagged flash-loan setup, rapid drain indicators, and weak counterparty reputation.', 'patterns': ['Flash-loan indicator', 'High-value drain attempt', 'Burst of high-risk actions']},
            {'id': 'det-002', 'category': 'transaction', 'title': 'Admin privilege abuse scenario', 'score': 75, 'severity': 'critical', 'action': 'block', 'source': 'fallback', 'explanation': 'Fallback analysis detected unexpected admin activity and drain path indicators.', 'patterns': ['Unexpected admin action', 'Role mismatch', 'High-value drain attempt']},
            {'id': 'det-003', 'category': 'market', 'title': 'Spoofing-like treasury token market', 'score': 80, 'severity': 'critical', 'action': 'block', 'source': 'fallback', 'explanation': 'Fallback anomaly detection found cancellation bursts, rapid swings, and concentrated volume.', 'patterns': ['Spoofing-like order behavior', 'Abnormal volume spike', 'Abnormal rapid swings']},
            {'id': 'det-004', 'category': 'market', 'title': 'Wash-trading-like treasury token market', 'score': 77, 'severity': 'critical', 'action': 'block', 'source': 'fallback', 'explanation': 'Fallback anomaly detection found circular trading and dominant wallet cluster concentration.', 'patterns': ['Wash-trading-like loops', 'Wallet cluster concentration']},
        ],
        'recent_detections': [
            {'id': 'det-001', 'category': 'transaction', 'title': 'Suspicious flash-loan-like transaction', 'score': 88, 'severity': 'critical', 'action': 'block', 'source': 'fallback', 'explanation': 'Fallback analysis flagged flash-loan setup, rapid drain indicators, and weak counterparty reputation.', 'patterns': ['Flash-loan indicator', 'Borrow / swap / repay burst', 'High-value drain attempt']},
            {'id': 'det-002', 'category': 'transaction', 'title': 'Admin privilege abuse scenario', 'score': 75, 'severity': 'critical', 'action': 'block', 'source': 'fallback', 'explanation': 'Fallback analysis detected unexpected admin activity and drain path indicators.', 'patterns': ['Unexpected admin action', 'Role mismatch']},
            {'id': 'det-003', 'category': 'market', 'title': 'Spoofing-like treasury token market', 'score': 80, 'severity': 'critical', 'action': 'block', 'source': 'fallback', 'explanation': 'Fallback anomaly detection found cancellation bursts, rapid swings, and concentrated volume.', 'patterns': ['Spoofing-like order behavior', 'Abnormal volume spike', 'Abnormal rapid swings']},
            {'id': 'det-004', 'category': 'market', 'title': 'Wash-trading-like treasury token market', 'score': 77, 'severity': 'critical', 'action': 'block', 'source': 'fallback', 'explanation': 'Fallback anomaly detection found circular trading and dominant wallet cluster concentration.', 'patterns': ['Wash-trading-like loops', 'Wallet cluster concentration']},
            {'id': 'det-005', 'category': 'contract', 'title': 'Proxy router contract scan', 'score': 82, 'severity': 'critical', 'action': 'block', 'source': 'fallback', 'explanation': 'Fallback contract analysis found privilege escalation, drain path, and untrusted integration indicators.', 'patterns': ['Unsafe admin action', 'Rapid drain path', 'Untrusted contract interaction']},
            {'id': 'det-006', 'category': 'transaction', 'title': 'Safe treasury settlement', 'score': 6, 'severity': 'low', 'action': 'allow', 'source': 'fallback', 'explanation': 'Fallback analysis found no material threat indicators in the safe settlement scenario.', 'patterns': []},
        ],
        'sample_scenarios': {
            'safe_transaction': 'Safe transaction',
            'flash_loan_transaction': 'Suspicious flash-loan-like transaction',
            'admin_privilege_transaction': 'Admin privilege abuse scenario',
            'normal_market': 'Normal market behavior',
            'spoofing_market': 'Spoofing-like market behavior',
            'wash_trading_market': 'Wash-trading-like market behavior',
        },
        'message': 'Threat-engine unavailable or timed out. Returning explicit fallback detections so the dashboard and demo panel remain usable.',
    }


def fallback_contract_analysis(payload: dict[str, Any]) -> dict[str, Any]:
    score = 0
    reasons: list[str] = []
    flags = payload.get('flags', {})
    findings = ' '.join(payload.get('findings', [])).lower()
    function_names = {item.get('name', '').lower() for item in payload.get('function_summaries', [])}

    if flags.get('unsafe_admin_action'):
        score += 24
        reasons.append('Unsafe admin action flag was supplied.')
    if flags.get('high_value_drain_path') or 'sweepfunds' in function_names or 'withdrawall' in function_names:
        score += 22
        reasons.append('Drain-style contract function or flag was supplied.')
    if flags.get('untrusted_external_call') or 'untrusted' in findings:
        score += 14
        reasons.append('Untrusted external interaction was supplied.')
    if flags.get('delegatecall') or 'delegatecall' in findings:
        score += 16
        reasons.append('Delegatecall usage was supplied.')
    if flags.get('burst_risk_actions'):
        score += 12
        reasons.append('Burst risk-action indicator was supplied.')

    return build_fallback_analysis('contract', score, reasons)


def fallback_transaction_analysis(payload: dict[str, Any]) -> dict[str, Any]:
    score = 0
    reasons: list[str] = []
    flags = payload.get('flags', {})
    sequence = {step.lower() for step in payload.get('call_sequence', [])}
    amount = float(payload.get('amount', 0) or 0)
    reputation = int(payload.get('counterparty_reputation', 50) or 50)
    burst = int(payload.get('burst_actions_last_5m', 0) or 0)
    actor_role = str(payload.get('actor_role', '')).lower()
    expected_roles = {str(role).lower() for role in payload.get('expected_actor_roles', [])}

    if flags.get('contains_flash_loan'):
        score += 28
        reasons.append('Flash-loan indicator flag was supplied.')
    if {'borrow', 'swap', 'repay'}.issubset(sequence):
        score += 18
        reasons.append('Borrow / swap / repay pattern was supplied.')
    if flags.get('rapid_drain_indicator') or amount >= 1_000_000:
        score += 22
        reasons.append('High-value drain indicator was supplied.')
    if flags.get('unexpected_admin_call'):
        score += 24
        reasons.append('Unexpected admin action flag was supplied.')
    if expected_roles and actor_role and actor_role not in expected_roles:
        score += 14
        reasons.append('Actor role does not match expected roles.')
    if flags.get('untrusted_contract'):
        score += 14
        reasons.append('Untrusted contract interaction flag was supplied.')
    if reputation < 35:
        score += 10
        reasons.append('Counterparty reputation is below the defensive threshold.')
    if burst >= 4:
        score += 12
        reasons.append('Burst-action threshold was exceeded.')

    return build_fallback_analysis('transaction', score, reasons)


def fallback_market_analysis(payload: dict[str, Any]) -> dict[str, Any]:
    score = 0
    reasons: list[str] = []
    current_volume = float(payload.get('current_volume', 0) or 0)
    baseline_volume = max(float(payload.get('baseline_volume', 1) or 1), 1.0)
    diversity = int(payload.get('participant_diversity', 0) or 0)
    cluster_share = float(payload.get('dominant_cluster_share', 0) or 0)
    order_flow = payload.get('order_flow_summary', {})
    volume_ratio = current_volume / baseline_volume

    if volume_ratio >= 3 and diversity <= 5:
        score += 22
        reasons.append('Volume spike with low participant diversity was supplied.')
    if int(order_flow.get('rapid_cancellations', 0)) >= 8 and int(order_flow.get('large_orders', 0)) >= 10:
        score += 24
        reasons.append('Spoofing-like cancellation burst was supplied.')
    if int(order_flow.get('circular_trade_loops', 0)) >= 3 or int(order_flow.get('self_trade_markers', 0)) >= 3:
        score += 26
        reasons.append('Wash-trading-like loops were supplied.')
    if int(order_flow.get('rapid_swings', 0)) >= 5:
        score += 18
        reasons.append('Rapid swing threshold was exceeded.')
    if cluster_share >= 0.55:
        score += 16
        reasons.append('Wallet cluster concentration threshold was exceeded.')

    return build_fallback_analysis('market', score, reasons)


def build_fallback_analysis(analysis_type: str, score: int, reasons: list[str]) -> dict[str, Any]:
    normalized = max(0, min(100, score))
    severity = 'critical' if normalized >= 75 else 'high' if normalized >= 50 else 'medium' if normalized >= 25 else 'low'
    action = 'block' if normalized >= 70 else 'review' if normalized >= 35 else 'allow'
    return {
        'analysis_type': analysis_type,
        'score': normalized,
        'severity': severity,
        'matched_patterns': [
            {
                'pattern_id': f'fallback:{analysis_type}:{index + 1}',
                'label': reason.split('.')[0],
                'weight': min(30, max(8, normalized // max(1, len(reasons) or 1))),
                'severity': severity,
                'reason': reason,
                'evidence': {'fallback': True},
            }
            for index, reason in enumerate(reasons)
        ],
        'explanation': (
            f'Fallback {analysis_type} analysis produced score {normalized} ({severity}) and action {action}. '
            + ('Primary drivers: ' + '; '.join(reasons[:3]) if reasons else 'No suspicious fallback rules matched this payload.')
        ),
        'recommended_action': action,
        'reasons': reasons,
        'metadata': {'source': 'fallback', 'degraded': True},
        'source': 'fallback',
        'degraded': True,
    }


def build_risk_summary(queue: list[dict[str, Any]]) -> dict[str, Any]:
    if not queue:
        return {
            'total_transactions': 0,
            'allow_count': 0,
            'review_count': 0,
            'block_count': 0,
            'avg_risk_score': 0,
            'high_alert_count': 0,
        }

    risk_scores = [item['evaluation']['risk_score'] for item in queue]
    return {
        'total_transactions': len(queue),
        'allow_count': sum(item['evaluation']['recommendation'] == 'ALLOW' for item in queue),
        'review_count': sum(item['evaluation']['recommendation'] == 'REVIEW' for item in queue),
        'block_count': sum(item['evaluation']['recommendation'] == 'BLOCK' for item in queue),
        'avg_risk_score': round(sum(risk_scores) / len(risk_scores), 1),
        'high_alert_count': sum(item['evaluation']['risk_score'] >= 75 for item in queue),
    }


def serialize_queue_item(item: dict[str, Any]) -> dict[str, Any]:
    request = item['request']
    evaluation = item['evaluation']
    return {
        'id': item['id'],
        'label': item['label'],
        'tx_hash': request['transaction_payload']['tx_hash'],
        'from_address': request['transaction_payload']['from_address'],
        'to_address': request['transaction_payload']['to_address'],
        'contract_name': request['contract_metadata']['contract_name'],
        'contract_address': request['contract_metadata']['address'],
        'function_name': request['decoded_function_call']['function_name'],
        'risk_score': evaluation['risk_score'],
        'recommendation': evaluation['recommendation'],
        'triggered_rules': [rule['summary'] for rule in evaluation.get('triggered_rules', [])],
        'explanation': evaluation['explanation'],
        'updated_at': item['updated_at'],
        'source': 'live' if item['live_data'] else 'fallback',
    }


def build_risk_alerts(queue: list[dict[str, Any]]) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    for item in queue:
        if item['evaluation']['risk_score'] < 45:
            continue
        top_rule = next(iter(item['evaluation'].get('triggered_rules', [])), None)
        alerts.append(
            {
                'id': f"alert-{item['id']}",
                'title': item['label'],
                'severity': recommendation_severity(item['evaluation']['recommendation']),
                'risk_score': item['evaluation']['risk_score'],
                'recommendation': item['evaluation']['recommendation'],
                'rule': top_rule['summary'] if top_rule else 'Manual review requested.',
                'explanation': item['evaluation']['explanation'],
                'tx_hash': item['request']['transaction_payload']['tx_hash'],
                'status': 'Open' if item['evaluation']['recommendation'] == 'BLOCK' else 'Reviewing',
            }
        )
    return alerts


def build_contract_scan_results(queue: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            'id': f"contract-{item['id']}",
            'contract_name': item['request']['contract_metadata']['contract_name'],
            'contract_address': item['request']['contract_metadata']['address'],
            'function_name': item['request']['decoded_function_call']['function_name'],
            'risk_score': item['evaluation']['risk_score'],
            'recommendation': item['evaluation']['recommendation'],
            'triggered_rules': [rule['summary'] for rule in item['evaluation'].get('triggered_rules', [])],
            'explanation': item['evaluation']['explanation'],
            'source': 'live' if item['live_data'] else 'fallback',
        }
        for item in queue
    ]


def build_decisions_log(queue: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            'id': f"decision-{item['id']}",
            'decided_at': item['updated_at'],
            'tx_hash': item['request']['transaction_payload']['tx_hash'],
            'contract_name': item['request']['contract_metadata']['contract_name'],
            'risk_score': item['evaluation']['risk_score'],
            'recommendation': item['evaluation']['recommendation'],
            'triggered_rules': [rule['summary'] for rule in item['evaluation'].get('triggered_rules', [])],
            'explanation': item['evaluation']['explanation'],
            'source': 'live' if item['live_data'] else 'fallback',
        }
        for item in reversed(queue)
    ]


def recommendation_severity(recommendation: str) -> str:
    if recommendation == 'BLOCK':
        return 'critical'
    if recommendation == 'REVIEW':
        return 'high'
    return 'low'


def iso_timestamp(offset: int) -> str:
    return f'2026-03-18T09:0{offset}:00Z'


def log_optional_fixture_warning_once(path: Path, reason: str, message: str) -> None:
    warning_key = (str(path), reason)
    if warning_key in OPTIONAL_FIXTURE_WARNINGS_EMITTED:
        return
    OPTIONAL_FIXTURE_WARNINGS_EMITTED.add(warning_key)
    logger.warning(message, path)


def load_json_file(data_dir: Path, filename: str, default: Any | None = None) -> Any:
    path = data_dir / filename
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        log_optional_fixture_warning_once(path, 'missing', 'Optional JSON fixture missing at %s; using built-in fallback.')
    except json.JSONDecodeError:
        log_optional_fixture_warning_once(path, 'invalid-json', 'Optional JSON fixture at %s is invalid JSON; using built-in fallback.')

    if default is None:
        return {}
    return deepcopy(default)
