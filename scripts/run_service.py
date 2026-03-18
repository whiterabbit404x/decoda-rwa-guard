from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import uvicorn

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.bootstrap_paths import ensure_repo_on_path

REPO_ROOT = ensure_repo_on_path()

SERVICE_CONFIG = {
    'api': {
        'port': 8000,
        'app_dir': REPO_ROOT / 'services' / 'api',
        'env_file': REPO_ROOT / 'services' / 'api' / '.env',
    },
    'risk-engine': {
        'port': 8001,
        'app_dir': REPO_ROOT / 'services' / 'risk-engine',
        'env_file': REPO_ROOT / 'services' / 'risk-engine' / '.env',
    },
    'threat-engine': {
        'port': 8002,
        'app_dir': REPO_ROOT / 'services' / 'threat-engine',
        'env_file': REPO_ROOT / 'services' / 'threat-engine' / '.env',
    },
    'oracle-service': {
        'port': 8003,
        'app_dir': REPO_ROOT / 'services' / 'oracle-service',
        'env_file': REPO_ROOT / 'services' / 'oracle-service' / '.env',
    },
    'compliance-service': {
        'port': 8004,
        'app_dir': REPO_ROOT / 'services' / 'compliance-service',
        'env_file': REPO_ROOT / 'services' / 'compliance-service' / '.env',
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Run a Phase 1 service from the repository root.')
    parser.add_argument('service', choices=sorted(SERVICE_CONFIG), help='Service name to launch.')
    parser.add_argument('--host', default='0.0.0.0', help='Host interface to bind.')
    parser.add_argument('--port', type=int, help='Override the default service port.')
    parser.add_argument('--reload', action='store_true', help='Enable auto-reload for local development.')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = SERVICE_CONFIG[args.service]
    env_file = config['env_file']

    if env_file.exists():
        os.environ.setdefault('ENV_FILE', str(env_file))

    uvicorn.run(
        'app.main:app',
        host=args.host,
        port=args.port or config['port'],
        reload=args.reload,
        app_dir=str(config['app_dir']),
        env_file=str(env_file) if env_file.exists() else None,
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
