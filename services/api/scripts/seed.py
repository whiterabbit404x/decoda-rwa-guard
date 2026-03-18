"""Seed script for api."""

from phase1_local.dev_support import load_env_file, pretty_json, seed_service

load_env_file()

SERVICE_NAME = 'api'
PORT = 8000
DETAIL = 'FastAPI gateway serving the local Phase 1 dashboard API.'
DEFAULT_METRICS = [{'metric_key': 'api_status', 'label': 'API Gateway', 'value': 'Serving local dashboard and service registry endpoints.', 'status': 'Healthy'}, {'metric_key': 'local_mode', 'label': 'Local Mode', 'value': 'SQLite-backed development mode is enabled without Docker.', 'status': 'Ready'}]


def seed() -> None:
    state = seed_service(SERVICE_NAME, PORT, DETAIL, DEFAULT_METRICS)
    print(pretty_json(state))


if __name__ == '__main__':
    seed()
