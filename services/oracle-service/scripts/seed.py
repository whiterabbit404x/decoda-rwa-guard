"""Seed script for oracle-service."""

from phase1_local.dev_support import load_env_file, pretty_json, seed_service

load_env_file()

SERVICE_NAME = 'oracle-service'
PORT = 8002
DETAIL = 'Oracle data worker storing mock market snapshots in local SQLite.'
DEFAULT_METRICS = [{'metric_key': 'oracle_feed', 'label': 'Oracle Data Feed', 'value': 'Treasury market data refreshed from deterministic local fixtures.', 'status': 'Live'}]


def seed() -> None:
    state = seed_service(SERVICE_NAME, PORT, DETAIL, DEFAULT_METRICS)
    print(pretty_json(state))


if __name__ == '__main__':
    seed()
