from __future__ import annotations

import os
from datetime import datetime, timezone

from fastapi import FastAPI

from phase1_local.dev_support import load_env_file, database_url, load_service, resolve_sqlite_path, seed_service

load_env_file()

SERVICE_NAME = 'oracle-service'
PORT = int(os.getenv('PORT', 8002))
DETAIL = 'Oracle data worker storing mock market snapshots in local SQLite.'
DEFAULT_METRICS = [{'metric_key': 'oracle_feed', 'label': 'Oracle Data Feed', 'value': 'Treasury market data refreshed from deterministic local fixtures.', 'status': 'Live'}]

app = FastAPI(title=f'{SERVICE_NAME} service')


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


@app.get('/oracle/check')
def oracle_check() -> dict[str, object]:
    sources = [item.strip() for item in (os.getenv('ORACLE_SOURCE_URLS', '')).split(',') if item.strip()]
    if not sources:
        return {
            'status': 'unavailable',
            'reason': 'no_real_oracle_sources_configured',
            'sources': [],
            'checked_at': datetime.now(timezone.utc).isoformat(),
        }
    return {
        'status': 'ok',
        'sources': sources,
        'checked_at': datetime.now(timezone.utc).isoformat(),
    }
