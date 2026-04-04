"""Seed script for oracle-service."""

from phase1_local.dev_support import load_env_file, pretty_json, seed_service

load_env_file()

SERVICE_NAME = 'oracle-service'
PORT = 8002
DETAIL = 'Oracle integrity worker for configured live sources.'
DEFAULT_METRICS = [{'metric_key': 'oracle_feed', 'label': 'Oracle Data Feed', 'value': 'Oracle service reports degraded when real sources are unavailable.', 'status': 'Live'}]


def seed() -> None:
    state = seed_service(SERVICE_NAME, PORT, DETAIL, DEFAULT_METRICS)
    print(pretty_json(state))


if __name__ == '__main__':
    seed()
