from __future__ import annotations

import json
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


def _load_real_provider_observations(*, asset_identifier: str, now: datetime) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for provider in _provider_configs():
        fetcher = HttpJsonOracleProvider(
            source_name=str(provider.get('source_name') or 'oracle'),
            source_type=str(provider.get('source_type') or 'oracle_api'),
            url=str(provider.get('url') or ''),
        )
        try:
            observations.extend(fetcher.fetch(asset_identifier=asset_identifier, now=now))
        except Exception:
            continue
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
    expected_freshness = int(os.getenv('ORACLE_EXPECTED_FRESHNESS_SECONDS', '120') or '120')
    expected_cadence = int(os.getenv('ORACLE_EXPECTED_CADENCE_SECONDS', '120') or '120')
    observations: list[dict[str, Any]] = []
    if sources:
        observations = [_normalize_observation(obs, now, asset_identifier) for obs in _load_real_provider_observations(asset_identifier=asset_identifier, now=now) if isinstance(obs, dict)]
    elif _runtime_allows_demo_observations():
        observations = [_normalize_observation(obs, now, asset_identifier) for obs in _observations() if isinstance(obs, dict)]
    if not sources and not _runtime_allows_demo_observations():
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
            'sources': [item['source_name'] for item in sources],
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
        'sources': [item['source_name'] for item in sources],
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
    provider_configured = bool(_provider_configs())
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
    status_value = 'ok' if observations else 'insufficient_real_evidence'
    return {'status': status_value, 'detector_status': 'real_event_no_anomaly' if status_value == 'ok' else 'insufficient_real_evidence', 'provider_configured': provider_configured, 'asset_identifier': configured_asset or None, 'observations': observations, 'generated_at': now.isoformat()}
