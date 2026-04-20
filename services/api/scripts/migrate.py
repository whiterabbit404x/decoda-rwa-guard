from __future__ import annotations

import os
import sys
from pathlib import Path


def _find_repo_root(start: Path) -> Path:
    for candidate in start.resolve().parents:
        if (candidate / 'phase1_local').is_dir():
            return candidate
    raise RuntimeError(f'Unable to locate repo root from {start}.')


def _ensure_repo_root_on_path() -> Path:
    repo_root = _find_repo_root(Path(__file__))
    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)
    return repo_root


REPO_ROOT = _ensure_repo_root_on_path()

from phase1_local.dev_support import load_env_file
from services.api.app.db_failure import classify_db_error
from services.api.app.pilot import pilot_schema_status, run_migrations


def _migration_fail_open_enabled() -> bool:
    value = os.getenv('MIGRATION_FAIL_OPEN', 'true').strip().lower()
    return value not in {'0', 'false', 'no', 'off'}


def _is_database_bootstrap_unavailable_error(exc: Exception) -> bool:
    return classify_db_error(exc) in {
        'quota_exceeded',
        'network_unreachable',
        'db_unavailable',
    }


if __name__ == '__main__':
    load_env_file()
    try:
        applied = run_migrations()
        if applied:
            print('Applied migrations:')
            for version in applied:
                print(f'- {version}')
        else:
            print('No pending migrations.')
        print('Pilot schema status:')
        print(pilot_schema_status())
    except Exception as exc:
        if _migration_fail_open_enabled() and _is_database_bootstrap_unavailable_error(exc):
            print(
                'Migration skipped (fail-open): database is currently unreachable or over quota. '
                'Retry once database connectivity and quota are restored.'
            )
            print(f'Underlying error: {exc}')
            sys.exit(0)
        raise
