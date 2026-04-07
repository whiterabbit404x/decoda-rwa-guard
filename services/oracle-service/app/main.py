from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Protocol
from urllib import parse, request

from fastapi import FastAPI

from phase1_local.dev_support import load_env_file, database_url, load_service, resolve_sqlite_path, seed_service

load_env_file()

SERVICE_NAME = 'oracle-service'
PORT = int(os.getenv('PORT', 8002))
DETAIL = 'Oracle integrity worker for configured live sources.'
DEFAULT_METRICS = [
    {
        'metric_key': 'oracle_feed',
        'label': 'Oracle Data Feed',
        'value': 'Oracle service reports degraded when real sources are unavailable.',
        'status': 'Live',
    }
]

app = FastAPI(title=f'{SERVICE_NAME} service')
logger = logging.getLogger(__name__)
_CHAINLINK_LATEST_ROUND_DATA_SELECTOR = '0xfeaf968c'
_CHAINLINK_DECIMALS_SELECTOR = '0x313ce567'


@app.on_event('startup')
def startup() -> None:
    seed_service(SERVICE_NAME, PORT, DETAIL, DEFAULT_METRICS)


def _demo_allowed() -> bool:
    env = str(os.getenv('ENV') or os.getenv('APP_ENV') or '').strip().lower()
    return str(os.getenv('ALLOW_DEMO_MODE', 'false')).strip().lower() in {'1', 'true', 'yes', 'on'} and env not in {'prod', 'production'}


def _observations() -> list[dict[str, Any]]:
    raw = str(os.getenv('ORACLE_SOURCE_OBSERVATIONS_JSON') or '[]').strip()
    try:
        value = json.loads(raw)
    except Exception:
        value = []
    return value if isinstance(value, list) else []


class OracleProvider(Protocol):
    def fetch(self, *, asset_identifier: str, now: datetime) -> list[dict[str, Any]]: ...


def _eth_call(rpc_url: str, *, to_address: str, data: str) -> str:
    payload = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'eth_call', 'params': [{'to': to_address, 'data': data}, 'latest']}).encode('utf-8')
    req = request.Request(rpc_url, data=payload, headers={'Content-Type': 'application/json'})
    with request.urlopen(req, timeout=10) as resp:  # nosec B310
        body = json.loads(resp.read().decode('utf-8') or '{}')
    if body.get('error'):
        raise RuntimeError(f"json-rpc error: {body['error']}")
    return str(body.get('result') or '')


def _decode_uint256(hex_value: str) -> int:
    raw = hex_value[2:] if hex_value.startswith('0x') else hex_value
    raw = raw or '0'
    return int(raw, 16)


def _decode_latest_round_data(hex_value: str) -> dict[str, int]:
    raw = hex_value[2:] if hex_value.startswith('0x') else hex_value
    if len(raw) < 64 * 5:
        raise ValueError('latestRoundData response is too short')
    chunks = [raw[i:i + 64] for i in range(0, 64 * 5, 64)]
    round_id = int(chunks[0], 16)
    answer_raw = int(chunks[1], 16)
    if answer_raw >= (1 << 255):
        answer_raw -= (1 << 256)
    started_at = int(chunks[2], 16)
    updated_at = int(chunks[3], 16)
    answered_in_round = int(chunks[4], 16)
    return {'round_id': round_id, 'answer': answer_raw, 'started_at': started_at, 'updated_at': updated_at, 'answered_in_round': answered_in_round}


def _chainlink_env_feeds() -> list[dict[str, Any]]:
    raw = str(os.getenv('ORACLE_CHAINLINK_FEEDS_JSON') or '[]').strip()
    try:
        feeds = json.loads(raw)
    except Exception:
        feeds = []
    return [item for item in feeds if isinstance(item, dict)]


class ChainlinkOnchainProvider:
    def __init__(self, *, rpc_url: str | None = None, feeds: list[dict[str, Any]] | None = None) -> None:
        self.rpc_url = str(rpc_url or os.getenv('ORACLE_CHAINLINK_RPC_URL') or '').strip()
        self.feeds = feeds if feeds is not None else _chainlink_env_feeds()

    def fetch(self, *, asset_identifier: str, now: datetime) -> list[dict[str, Any]]:
        if not self.rpc_url or not self.feeds:
            return []
        expected_freshness = int(os.getenv('ORACLE_EXPECTED_FRESHNESS_SECONDS', '120') or '120')
        expected_cadence = int(os.getenv('ORACLE_EXPECTED_CADENCE_SECONDS', '120') or '120')
        rows: list[dict[str, Any]] = []
        for feed in self.feeds:
            if asset_identifier and str(feed.get('asset_identifier') or '').strip() not in {'', asset_identifier}:
                continue
            proxy_address = str(feed.get('proxy_address') or '').strip().lower()
            if not proxy_address.startswith('0x'):
                continue
            try:
                decimals = _decode_uint256(_eth_call(self.rpc_url, to_address=proxy_address, data=_CHAINLINK_DECIMALS_SELECTOR))
                round_data = _decode_latest_round_data(_eth_call(self.rpc_url, to_address=proxy_address, data=_CHAINLINK_LATEST_ROUND_DATA_SELECTOR))
                observed_at = datetime.fromtimestamp(round_data['updated_at'], tz=timezone.utc)
                divisor = float(10 ** max(decimals, 0))
                observed_value = float(round_data['answer']) / divisor if divisor else None
                freshness_seconds = max(0, int((now - observed_at).total_seconds()))
                status_value = 'ok' if freshness_seconds <= expected_freshness else 'stale'
                rows.append(
                    {
                        'provider_name': 'chainlink_onchain',
                        'source_name': str(feed.get('pair') or 'chainlink'),
                        'source_type': 'chainlink_onchain',
                        'asset_identifier': str(feed.get('asset_identifier') or asset_identifier),
                        'observed_value': observed_value,
                        'observed_at': observed_at.isoformat(),
                        'update_interval_seconds': expected_cadence,
                        'freshness_seconds': freshness_seconds,
                        'status': status_value,
                        'provider_status': status_value,
                        'block_number': None,
                        'external_timestamp': observed_at.isoformat(),
                        'provenance': {
                            'provider_layer': 'oracle-service',
                            'provider_kind': 'chainlink_onchain',
                            'chain_network': str(feed.get('chain_network') or ''),
                            'proxy_address': proxy_address,
                            'pair': str(feed.get('pair') or ''),
                            'round_id': round_data['round_id'],
                            'answered_in_round': round_data['answered_in_round'],
                        },
                    }
                )
                logger.info(
                    'chainlink_fetch_ok asset=%s feed=%s roundId=%s freshness=%s',
                    str(feed.get('asset_identifier') or asset_identifier or ''),
                    str(feed.get('pair') or proxy_address),
                    round_data['round_id'],
                    freshness_seconds,
                )
            except Exception:
                rows.append(
                    {
                        'provider_name': 'chainlink_onchain',
                        'source_name': str(feed.get('pair') or 'chainlink'),
                        'source_type': 'chainlink_onchain',
                        'asset_identifier': str(feed.get('asset_identifier') or asset_identifier),
                        'observed_value': None,
                        'observed_at': None,
                        'update_interval_seconds': expected_cadence,
                        'freshness_seconds': None,
                        'status': 'unavailable',
                        'provider_status': 'unavailable',
                        'provenance': {'provider_layer': 'oracle-service', 'provider_kind': 'chainlink_onchain', 'proxy_address': proxy_address, 'failure': 'eth_call_failed'},
                    }
                )
        return rows


class HttpJsonOracleProvider:
    def __init__(self, *, source_name: str, source_type: str, url: str) -> None:
        self.source_name = source_name
        self.source_type = source_type
        self.url = url

    def fetch(self, *, asset_identifier: str, now: datetime) -> list[dict[str, Any]]:
        query = parse.urlencode({'asset_identifier': asset_identifier}) if asset_identifier else ''
        url = f'{self.url}?{query}' if query else self.url
        req = request.Request(url, headers={'Accept': 'application/json'})
        with request.urlopen(req, timeout=10) as resp:  # nosec B310
            body = json.loads(resp.read().decode('utf-8') or '{}')
        observations = body.get('observations') if isinstance(body, dict) else body
        if not isinstance(observations, list):
            return []
        normalized: list[dict[str, Any]] = []
        for item in observations:
            if not isinstance(item, dict):
                continue
            normalized.append(
                {
                    **item,
                    'source_name': str(item.get('source_name') or self.source_name),
                    'source_type': str(item.get('source_type') or self.source_type),
                    'provenance': {
                        'provider_kind': 'http_json',
                        'provider_url': self.url,
                        'fetched_at': now.isoformat(),
                    },
                }
            )
        return normalized


def _environment_mode() -> str:
    return str(os.getenv('ENV') or os.getenv('APP_ENV') or '').strip().lower()


def _runtime_allows_demo_observations() -> bool:
    return _demo_allowed() and _environment_mode() not in {'prod', 'production'}


def _provider_configs() -> list[dict[str, str]]:
    raw = str(os.getenv('ORACLE_SOURCE_URLS') or '').strip()
    items: list[dict[str, str]] = []
    for chunk in [item.strip() for item in raw.split(',') if item.strip()]:
        if '=' in chunk:
            name, url = chunk.split('=', 1)
            items.append({'source_name': name.strip() or 'oracle', 'source_type': 'oracle_api', 'url': url.strip()})
        else:
            items.append({'source_name': parse.urlparse(chunk).netloc or 'oracle', 'source_type': 'oracle_api', 'url': chunk})
    return [item for item in items if item.get('url')]


def _chainlink_configured() -> bool:
    return bool(str(os.getenv('ORACLE_CHAINLINK_RPC_URL') or '').strip() and _chainlink_env_feeds())


def _load_real_provider_observations(*, asset_identifier: str, now: datetime) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    try:
        observations.extend(ChainlinkOnchainProvider().fetch(asset_identifier=asset_identifier, now=now))
    except Exception:
        pass
    for provider in _provider_configs():
        fetcher = HttpJsonOracleProvider(
            source_name=str(provider.get('source_name') or 'oracle'),
            source_type=str(provider.get('source_type') or 'oracle_api'),
            url=str(provider.get('url') or ''),
        )
        try:
            observations.extend(fetcher.fetch(asset_identifier=asset_identifier, now=now))
        except Exception:
            observations.append(
                {
                    'source_name': str(provider.get('source_name') or 'oracle'),
                    'source_type': str(provider.get('source_type') or 'oracle_api'),
                    'asset_identifier': asset_identifier or None,
                    'observed_value': None,
                    'observed_at': None,
                    'status': 'unavailable',
                    'detector_status': 'insufficient_real_evidence',
                    'freshness_seconds': None,
                    'update_interval_seconds': None,
                    'provenance': {
                        'provider_layer': 'oracle-service',
                        'provider_kind': 'http_json',
                        'provider_url': str(provider.get('url') or ''),
                        'failure': 'provider_unreachable',
                        'checked_at': now.isoformat(),
                    },
                }
            )
    return observations


def _normalize_observation(raw: dict[str, Any], now: datetime, asset_identifier: str) -> dict[str, Any]:
    observed_at_raw = raw.get('observed_at')
    observed_at = None
    if observed_at_raw:
        try:
            observed_at = datetime.fromisoformat(str(observed_at_raw).replace('Z', '+00:00'))
        except ValueError:
            observed_at = None
    freshness_seconds = None
    if observed_at is not None:
        freshness_seconds = max(0, int((now - observed_at).total_seconds()))
    return {
        'provider_name': str(raw.get('provider_name') or raw.get('source_name') or raw.get('source') or 'unknown'),
        'source_name': str(raw.get('source_name') or raw.get('source') or 'unknown'),
        'source_type': str(raw.get('source_type') or 'oracle_api'),
        'asset_identifier': str(raw.get('asset_identifier') or asset_identifier),
        'observed_value': raw.get('observed_value', raw.get('price')),
        'observed_at': observed_at.isoformat() if observed_at is not None else None,
        'block_number': raw.get('block_number'),
        'external_timestamp': raw.get('external_timestamp'),
        'update_interval_seconds': int(raw.get('update_interval_seconds') or 0),
        'freshness_seconds': freshness_seconds if freshness_seconds is not None else int(raw.get('freshness_seconds') or 0),
        'status': str(raw.get('status') or 'ok'),
        'provider_status': str(raw.get('provider_status') or raw.get('status') or 'ok'),
        'provenance': {**({'provider_layer': 'oracle-service'}), **(raw.get('provenance') if isinstance(raw.get('provenance'), dict) else {})},
    }


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


@app.get('/oracle/check')
def oracle_check() -> dict[str, object]:
    now = datetime.now(timezone.utc)
    asset_identifier = str(os.getenv('ORACLE_ASSET_IDENTIFIER', ''))
    sources = _provider_configs()
    chainlink_configured = _chainlink_configured()
    expected_freshness = int(os.getenv('ORACLE_EXPECTED_FRESHNESS_SECONDS', '120') or '120')
    expected_cadence = int(os.getenv('ORACLE_EXPECTED_CADENCE_SECONDS', '120') or '120')
    observations: list[dict[str, Any]] = []
    if sources or chainlink_configured:
        observations = [_normalize_observation(obs, now, asset_identifier) for obs in _load_real_provider_observations(asset_identifier=asset_identifier, now=now) if isinstance(obs, dict)]
    elif _runtime_allows_demo_observations():
        observations = [_normalize_observation(obs, now, asset_identifier) for obs in _observations() if isinstance(obs, dict)]
    if not (sources or chainlink_configured) and not _runtime_allows_demo_observations():
        return {
            'status': 'degraded',
            'reason': 'no_real_oracle_sources_configured',
            'detector_status': 'insufficient_real_evidence',
            'sources': [],
            'checked_at': now.isoformat(),
        }
    if not observations and not _runtime_allows_demo_observations():
        return {
            'status': 'degraded',
            'reason': 'no_real_oracle_observations_available',
            'detector_status': 'insufficient_real_evidence',
            'sources': [item['source_name'] for item in sources] + (['chainlink_onchain'] if chainlink_configured else []),
            'checked_at': now.isoformat(),
        }

    stale_sources: list[str] = []
    cadence_violations: list[str] = []
    prices: list[float] = []
    for obs in observations:
        src = str(obs.get('source_name') or '')
        freshness_seconds = int(obs.get('freshness_seconds') or 0)
        if freshness_seconds > expected_freshness:
            stale_sources.append(src)
        interval = int(obs.get('update_interval_seconds') or 0)
        if interval and interval > expected_cadence:
            cadence_violations.append(src)
        try:
            prices.append(float(obs.get('observed_value')))
        except Exception:
            pass

    divergence = False
    if len(prices) >= 2:
        low = min(prices)
        high = max(prices)
        divergence = low > 0 and ((high - low) / low) > float(os.getenv('ORACLE_DIVERGENCE_THRESHOLD', '0.02'))

    anomaly = bool(stale_sources or cadence_violations or divergence)
    return {
        'status': 'ok' if not anomaly else 'anomaly_detected',
        'detector_status': 'anomaly_detected' if anomaly else 'real_event_no_anomaly',
        'sources': [item['source_name'] for item in sources] + (['chainlink_onchain'] if chainlink_configured else []),
        'observations': observations,
        'stale_sources': stale_sources,
        'cadence_violations': cadence_violations,
        'divergence_detected': divergence,
        'checked_at': now.isoformat(),
    }


@app.get('/oracle/observations')
def oracle_observations(asset_identifier: str = '') -> dict[str, object]:
    now = datetime.now(timezone.utc)
    configured_asset = str(asset_identifier or os.getenv('ORACLE_ASSET_IDENTIFIER') or '').strip()
    provider_configured = bool(_provider_configs() or _chainlink_configured())
    observations: list[dict[str, Any]] = []
    if provider_configured:
        observations = [_normalize_observation(obs, now, configured_asset) for obs in _load_real_provider_observations(asset_identifier=configured_asset, now=now)]
    elif _runtime_allows_demo_observations():
        observations = [_normalize_observation(obs, now, configured_asset) for obs in _observations() if isinstance(obs, dict)]
    else:
        return {
            'status': 'insufficient_real_evidence',
            'detector_status': 'insufficient_real_evidence',
            'reason': 'real_oracle_providers_not_configured',
            'oracle_coverage_status': 'no_provider_configured',
            'provider_coverage_summary': {'configured_provider_count': 0, 'reachable_provider_count': 0, 'usable_observation_count': 0},
            'oracle_claim_eligible': False,
            'oracle_claim_ineligibility_reasons': ['oracle_provider_not_configured'],
            'provider_configured': False,
            'asset_identifier': configured_asset or None,
            'observations': [],
            'generated_at': now.isoformat(),
        }
    if configured_asset:
        observations = [item for item in observations if str(item.get('asset_identifier') or '').strip() in {configured_asset, ''}]
        for item in observations:
            if not item.get('asset_identifier'):
                item['asset_identifier'] = configured_asset
    available = [item for item in observations if str(item.get('status') or 'ok').lower() not in {'unavailable', 'insufficient_real_evidence', 'no_real_telemetry'}]
    unavailable = [item for item in observations if str(item.get('status') or '').lower() in {'unavailable'}]
    stale = [item for item in observations if str(item.get('status') or '').lower() in {'stale'}]
    divergent = [item for item in observations if str(item.get('status') or '').lower() in {'divergent'}]
    expected_cadence = int(os.getenv('ORACLE_EXPECTED_CADENCE_SECONDS', '120') or '120')
    cadence_violations = [
        item for item in observations
        if int(item.get('update_interval_seconds') or 0) > expected_cadence > 0
    ]
    if available:
        status_value = 'ok'
        reason = None
        coverage_status = 'real_oracle_observations_present'
        ineligibility_reasons: list[str] = []
    elif stale:
        status_value = 'insufficient_real_evidence'
        reason = 'configured_provider_returned_stale_data'
        coverage_status = 'provider_returned_stale_data'
        ineligibility_reasons = ['oracle_observation_stale']
    elif divergent:
        status_value = 'insufficient_real_evidence'
        reason = 'configured_provider_returned_divergent_values'
        coverage_status = 'provider_returned_divergent_values'
        ineligibility_reasons = ['oracle_source_divergence']
    elif cadence_violations:
        status_value = 'insufficient_real_evidence'
        reason = 'configured_provider_cadence_violation'
        coverage_status = 'provider_cadence_violation'
        ineligibility_reasons = ['oracle_provider_cadence_violation']
    elif unavailable:
        status_value = 'unavailable'
        reason = 'configured_provider_unreachable'
        coverage_status = 'provider_configured_but_unreachable'
        ineligibility_reasons = ['oracle_provider_unreachable']
    else:
        status_value = 'insufficient_real_evidence'
        reason = 'configured_provider_returned_no_usable_observations' if provider_configured else 'real_oracle_providers_not_configured'
        coverage_status = 'insufficient_real_evidence'
        ineligibility_reasons = ['oracle_provider_returned_insufficient_observations']
    oracle_claim_eligible = bool(status_value == 'ok' and provider_configured and available)
    return {
        'status': status_value,
        'detector_status': 'real_event_no_anomaly' if status_value == 'ok' else 'insufficient_real_evidence',
        'oracle_coverage_status': coverage_status,
        'oracle_claim_eligible': oracle_claim_eligible,
        'oracle_claim_ineligibility_reasons': ineligibility_reasons,
        'provider_configured': provider_configured,
        'asset_identifier': configured_asset or None,
        'observations': observations,
        'provider_coverage_summary': {
            'configured_provider_count': len(_provider_configs()) + (1 if _chainlink_configured() else 0),
            'reachable_provider_count': len([item for item in observations if str(item.get('status') or '').lower() != 'unavailable']),
            'usable_observation_count': len(available),
            'stale_observation_count': len(stale),
            'cadence_violation_count': len(cadence_violations),
            'divergent_observation_count': len(divergent),
            'provider_names': sorted(
                {
                    str(item.get('provider_name') or item.get('source_name') or '').strip().lower()
                    for item in observations
                    if str(item.get('provider_name') or item.get('source_name') or '').strip()
                }
            ),
        },
        'reason': reason,
        'generated_at': now.isoformat(),
    }
