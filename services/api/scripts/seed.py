from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _find_repo_root(start: Path) -> Path:
    for candidate in start.resolve().parents:
        if (candidate / 'phase1_local').is_dir():
            return candidate
    raise RuntimeError(f'Unable to locate repo root from {start} via a phase1_local directory search.')


def _ensure_repo_root_on_path() -> Path:
    repo_root = _find_repo_root(Path(__file__))
    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)
    return repo_root


REPO_ROOT = _ensure_repo_root_on_path()

from phase1_local.dev_support import load_env_file, pretty_json, seed_service
from services.api.app.pilot import DEFAULT_DEMO_EMAIL, demo_seed_status, pilot_schema_status, run_migrations, seed_demo_workspace

load_env_file()

SERVICE_NAME = 'api'
PORT = 8000
DETAIL = 'FastAPI gateway serving the local Phase 1 dashboard API.'
DEFAULT_METRICS = [
    {'metric_key': 'api_status', 'label': 'API Gateway', 'value': 'Serving local dashboard and service registry endpoints.', 'status': 'Healthy'},
    {'metric_key': 'local_mode', 'label': 'Local Mode', 'value': 'SQLite-backed development mode is enabled without Docker.', 'status': 'Ready'},
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Seed the API gateway local registry and optional live pilot demo data.')
    parser.add_argument('--pilot-demo', action='store_true', help='Seed a demo live-mode workspace/user into Postgres after migrations run.')
    parser.add_argument('--demo-email', default=os.getenv('PILOT_DEMO_EMAIL', DEFAULT_DEMO_EMAIL), help='Demo user email for live pilot seeding.')
    parser.add_argument('--demo-password', default='PilotDemoPass123!', help='Demo user password for live pilot seeding.')
    parser.add_argument('--demo-workspace', default='Decoda Demo Workspace', help='Demo workspace name for live pilot seeding.')
    parser.add_argument('--demo-full-name', default='Decoda Demo User', help='Demo full name for live pilot seeding.')
    return parser.parse_args()


def seed_local_state() -> dict[str, object]:
    return seed_service(SERVICE_NAME, PORT, DETAIL, DEFAULT_METRICS)


def seed() -> None:
    args = parse_args()
    local_state = seed_local_state()
    print(pretty_json(local_state))
    if args.pilot_demo:
        applied = run_migrations()
        if applied:
            print('Applied migrations before seeding live pilot data:')
            for version in applied:
                print(f'- {version}')
        print(pretty_json({'pilot_schema_status': pilot_schema_status()}))
        seeded = seed_demo_workspace(args.demo_email, args.demo_password, args.demo_workspace, args.demo_full_name)
        print(pretty_json(seeded))
        monitoring_bootstrap = seeded.get('monitoring_bootstrap') if isinstance(seeded, dict) else None
        if isinstance(monitoring_bootstrap, dict) and monitoring_bootstrap.get('bootstrapped'):
            print(
                pretty_json(
                    {
                        'deterministic_demo_chain': {
                            'workspace_id': monitoring_bootstrap.get('workspace_id'),
                            'target_id': monitoring_bootstrap.get('target_id'),
                            'monitored_system_id': monitoring_bootstrap.get('monitored_system_id'),
                            'steps': [
                                {'name': 'detection', 'id': monitoring_bootstrap.get('detection_id')},
                                {'name': 'alert', 'id': monitoring_bootstrap.get('alert_id')},
                                {'name': 'incident', 'id': monitoring_bootstrap.get('incident_id')},
                                {'name': 'response_action', 'id': monitoring_bootstrap.get('response_action_id'), 'mode': 'simulated'},
                                {'name': 'action_history', 'id': monitoring_bootstrap.get('response_action_history_id'), 'action_type': 'response_action.executed'},
                            ],
                            'evidence_source': monitoring_bootstrap.get('evidence_source'),
                            'telemetry_event_observed_at': monitoring_bootstrap.get('telemetry_event_observed_at'),
                        }
                    }
                )
            )
        print(pretty_json({'demo_seed_status': demo_seed_status(args.demo_email)}))


if __name__ == '__main__':
    seed()
