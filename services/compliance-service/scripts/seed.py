"""Seed script for compliance-service."""

from phase1_local.dev_support import load_env_file, pretty_json, seed_service

load_env_file()

SERVICE_NAME = 'compliance-service'
PORT = 8003
DETAIL = 'Compliance policy worker persisting local rule evaluations to SQLite.'
DEFAULT_METRICS = [{'metric_key': 'compliance_monitor', 'label': 'Compliance Monitor', 'value': 'Policy checks are passing against the local sample portfolio.', 'status': 'Passing'}]


def seed() -> None:
    state = seed_service(SERVICE_NAME, PORT, DETAIL, DEFAULT_METRICS)
    print(pretty_json(state))


if __name__ == '__main__':
    seed()
