"""Seed script for risk-engine."""

from phase1_local.dev_support import load_env_file, pretty_json, seed_service

load_env_file()

SERVICE_NAME = 'risk-engine'
PORT = 8001
DETAIL = 'Risk scoring worker using shared SQLite state for local development.'
DEFAULT_METRICS = [{'metric_key': 'portfolio_risk', 'label': 'Portfolio Risk', 'value': 'VaR and stress thresholds loaded from local sample data.', 'status': 'Nominal'}]


def seed() -> None:
    state = seed_service(SERVICE_NAME, PORT, DETAIL, DEFAULT_METRICS)
    print(pretty_json(state))


if __name__ == '__main__':
    seed()
