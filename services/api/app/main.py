from __future__ import annotations

import json
import os
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from phase1_local.dev_support import (
    dashboard_payload,
    database_url,
    load_env_file,
    load_service,
    resolve_sqlite_path,
    seed_service,
)

load_env_file()

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
ALLOWED_ORIGINS = [
    'http://localhost:3000',
    'http://127.0.0.1:3000',
]

app = FastAPI(
    title='api service',
    summary='Phase 1 gateway for dashboard and live risk-engine / threat-engine data.',
    description='Aggregates shared local service state, proxies dashboard feeds to the risk-engine and threat-engine, and returns explicit fallback metadata when backend services are unavailable.',
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)


@app.on_event('startup')
def startup() -> None:
    seed_service(SERVICE_NAME, PORT, DETAIL, DEFAULT_METRICS)


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


def build_risk_dashboard_queue() -> list[dict[str, Any]]:
    sample_request = load_json_file(RISK_ENGINE_DATA_DIR, 'sample_risk_request.json')
    suspicious_events = load_json_file(RISK_ENGINE_DATA_DIR, 'suspicious_market_events.json')
    normal_events = load_json_file(RISK_ENGINE_DATA_DIR, 'normal_market_events.json')

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
    request = Request(
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


def load_json_file(data_dir: Path, filename: str) -> Any:
    return json.loads((data_dir / filename).read_text())
