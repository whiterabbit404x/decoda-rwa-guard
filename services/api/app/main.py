from __future__ import annotations

import os

from fastapi import FastAPI

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
