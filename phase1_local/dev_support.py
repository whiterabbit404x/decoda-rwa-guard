from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SQLITE_PATH = REPO_ROOT / '.data' / 'phase1.db'


def load_env_file(env_path: Path | None = None) -> None:
    candidate = env_path or Path.cwd() / '.env'
    if not candidate.exists():
        return
    for raw_line in candidate.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        os.environ.setdefault(key.strip(), value.strip())


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_sqlite_path() -> Path:
    configured = os.getenv('SQLITE_PATH') or os.getenv('DATABASE_URL', '').removeprefix('sqlite:///')
    raw_path = configured or str(DEFAULT_SQLITE_PATH)
    path = Path(raw_path)
    if not path.is_absolute():
        path = REPO_ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def database_url() -> str:
    return f"sqlite:///{resolve_sqlite_path()}"


@contextmanager
def sqlite_connection() -> Iterable[sqlite3.Connection]:
    connection = sqlite3.connect(resolve_sqlite_path())
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def ensure_schema() -> None:
    with sqlite_connection() as connection:
        connection.executescript(
            '''
            CREATE TABLE IF NOT EXISTS service_status (
                service_name TEXT PRIMARY KEY,
                port INTEGER NOT NULL,
                status TEXT NOT NULL,
                detail TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS service_metrics (
                service_name TEXT NOT NULL,
                metric_key TEXT NOT NULL,
                label TEXT NOT NULL,
                value TEXT NOT NULL,
                status TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (service_name, metric_key)
            );
            '''
        )


def upsert_service(service_name: str, port: int, status: str, detail: str) -> None:
    ensure_schema()
    with sqlite_connection() as connection:
        connection.execute(
            '''
            INSERT INTO service_status (service_name, port, status, detail, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(service_name) DO UPDATE SET
                port = excluded.port,
                status = excluded.status,
                detail = excluded.detail,
                updated_at = excluded.updated_at
            ''',
            (service_name, port, status, detail, utc_now()),
        )


def replace_metrics(service_name: str, metrics: list[dict[str, str]]) -> None:
    ensure_schema()
    with sqlite_connection() as connection:
        connection.execute('DELETE FROM service_metrics WHERE service_name = ?', (service_name,))
        connection.executemany(
            '''
            INSERT INTO service_metrics (service_name, metric_key, label, value, status, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ''',
            [
                (
                    service_name,
                    metric['metric_key'],
                    metric['label'],
                    metric['value'],
                    metric['status'],
                    utc_now(),
                )
                for metric in metrics
            ],
        )


def load_service(service_name: str) -> dict[str, Any] | None:
    ensure_schema()
    with sqlite_connection() as connection:
        row = connection.execute(
            'SELECT service_name, port, status, detail, updated_at FROM service_status WHERE service_name = ?',
            (service_name,),
        ).fetchone()
        if row is None:
            return None
        metrics = connection.execute(
            '''
            SELECT metric_key, label, value, status, updated_at
            FROM service_metrics
            WHERE service_name = ?
            ORDER BY label
            ''',
            (service_name,),
        ).fetchall()
    return {
        'service_name': row['service_name'],
        'port': row['port'],
        'status': row['status'],
        'detail': row['detail'],
        'updated_at': row['updated_at'],
        'metrics': [dict(metric) for metric in metrics],
    }


def load_all_services() -> list[dict[str, Any]]:
    ensure_schema()
    with sqlite_connection() as connection:
        rows = connection.execute(
            'SELECT service_name, port, status, detail, updated_at FROM service_status ORDER BY port'
        ).fetchall()
        metrics = connection.execute(
            '''
            SELECT service_name, metric_key, label, value, status, updated_at
            FROM service_metrics
            ORDER BY service_name, label
            '''
        ).fetchall()
    metric_map: dict[str, list[dict[str, Any]]] = {}
    for metric in metrics:
        metric_map.setdefault(metric['service_name'], []).append(dict(metric))
    return [
        {
            'service_name': row['service_name'],
            'port': row['port'],
            'status': row['status'],
            'detail': row['detail'],
            'updated_at': row['updated_at'],
            'metrics': metric_map.get(row['service_name'], []),
        }
        for row in rows
    ]


def seed_service(service_name: str, port: int, detail: str, metrics: list[dict[str, str]]) -> dict[str, Any]:
    upsert_service(service_name, port, 'ok', detail)
    replace_metrics(service_name, metrics)
    state = load_service(service_name)
    if state is None:
        raise RuntimeError(f'Unable to seed state for {service_name}')
    return state


def dashboard_payload() -> dict[str, Any]:
    services = load_all_services()
    cards = []
    for service in services:
        cards.extend(
            {
                'title': metric['label'],
                'status': metric['status'],
                'detail': metric['value'],
                'service': service['service_name'],
            }
            for metric in service['metrics']
        )
    return {
        'mode': os.getenv('APP_MODE', 'local'),
        'database_url': database_url(),
        'redis_enabled': os.getenv('REDIS_ENABLED', 'false').lower() == 'true',
        'services': services,
        'cards': cards,
    }


def pretty_json(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2)
