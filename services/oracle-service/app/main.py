from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

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
    sources = [item.strip() for item in (os.getenv('ORACLE_SOURCE_URLS', '')).split(',') if item.strip()]
    expected_freshness = int(os.getenv('ORACLE_EXPECTED_FRESHNESS_SECONDS', '120') or '120')
    expected_cadence = int(os.getenv('ORACLE_EXPECTED_CADENCE_SECONDS', '120') or '120')
    observations = _observations()
    if not sources and not _demo_allowed():
        return {
            'status': 'degraded',
            'reason': 'no_real_oracle_sources_configured',
            'detector_status': 'insufficient_real_evidence',
            'sources': [],
            'checked_at': now.isoformat(),
        }
    if not observations and not _demo_allowed():
        return {
            'status': 'degraded',
            'reason': 'no_real_oracle_observations_available',
            'detector_status': 'insufficient_real_evidence',
            'sources': sources,
            'checked_at': now.isoformat(),
        }

    stale_sources: list[str] = []
    cadence_violations: list[str] = []
    prices: list[float] = []
    for obs in observations:
        src = str(obs.get('source') or '')
        observed_at = obs.get('observed_at')
        timestamp = None
        if observed_at:
            try:
                timestamp = datetime.fromisoformat(str(observed_at).replace('Z', '+00:00'))
            except ValueError:
                timestamp = None
        if timestamp and (now - timestamp).total_seconds() > expected_freshness:
            stale_sources.append(src)
        try:
            interval = int(obs.get('update_interval_seconds') or 0)
        except Exception:
            interval = 0
        if interval and interval > expected_cadence:
            cadence_violations.append(src)
        try:
            prices.append(float(obs.get('price')))
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
        'sources': sources,
        'observations': observations,
        'stale_sources': stale_sources,
        'cadence_violations': cadence_violations,
        'divergence_detected': divergence,
        'checked_at': now.isoformat(),
    }
