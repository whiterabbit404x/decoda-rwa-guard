from __future__ import annotations

import os

from fastapi import FastAPI

from phase1_local.dev_support import load_env_file, database_url, load_service, resolve_sqlite_path, seed_service

load_env_file()

SERVICE_NAME = 'compliance-service'
PORT = int(os.getenv('PORT', 8003))
DETAIL = 'Compliance policy worker persisting local rule evaluations to SQLite.'
DEFAULT_METRICS = [{'metric_key': 'compliance_monitor', 'label': 'Compliance Monitor', 'value': 'Policy checks are passing against the local sample portfolio.', 'status': 'Passing'}]

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
