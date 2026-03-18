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
RISK_ENGINE_DATA_DIR = Path(__file__).resolve().parents[2] / 'risk-engine' / 'data'

app = FastAPI(title='api service')


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


@app.get('/services')
def services() -> dict[str, object]:
    payload = dashboard_payload()
    return {
        'mode': payload['mode'],
        'database_url': payload['database_url'],
        'services': payload['services'],
    }


@app.get('/dashboard')
def dashboard() -> dict[str, object]:
    return dashboard_payload()


@app.get('/risk/dashboard')
def risk_dashboard() -> dict[str, object]:
    queue = build_risk_dashboard_queue()
    return {
        'source': 'live' if all(item['live_data'] for item in queue) else 'fallback',
        'generated_at': queue[0]['updated_at'] if queue else None,
        'summary': build_risk_summary(queue),
        'transaction_queue': [serialize_queue_item(item) for item in queue],
        'risk_alerts': build_risk_alerts(queue),
        'contract_scan_results': build_contract_scan_results(queue),
        'decisions_log': build_decisions_log(queue),
    }


def build_risk_dashboard_queue() -> list[dict[str, Any]]:
    sample_request = load_json_file('sample_risk_request.json')
    suspicious_events = load_json_file('suspicious_market_events.json')
    normal_events = load_json_file('normal_market_events.json')

    definitions = [
        {
            'id': 'txn-001',
            'label': 'Flash-loan router rebalance',
            'request': build_flash_loan_request(sample_request, suspicious_events),
            'fallback': {
                'risk_score': 100,
                'recommendation': 'BLOCK',
                'explanation': 'Aggregate score 100 produced recommendation BLOCK. Primary drivers: Low-level liquidity drain, flash-loan routing, and weak wallet reputation.',
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
    endpoint = f'{RISK_ENGINE_URL}/v1/risk/evaluate'
    request = Request(
        endpoint,
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urlopen(request, timeout=1.5) as response:
            return json.loads(response.read().decode('utf-8'))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
        return None


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
        'source': 'live' if item['live_data'] else 'mock',
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
            'source': 'live' if item['live_data'] else 'mock',
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
            'source': 'live' if item['live_data'] else 'mock',
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


def load_json_file(filename: str) -> Any:
    return json.loads((RISK_ENGINE_DATA_DIR / filename).read_text())
