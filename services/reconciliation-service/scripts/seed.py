"""Seed script for reconciliation-service."""

from phase1_local.dev_support import load_env_file, pretty_json, seed_service

load_env_file()

SERVICE_NAME = 'reconciliation-service'
PORT = 8004
DETAIL = 'Reconciliation worker validating ledger parity from local sample records.'
DEFAULT_METRICS = [{'metric_key': 'reconciliation', 'label': 'Reconciliation', 'value': 'Cash and token ledgers are aligned in the local dev dataset.', 'status': 'In Sync'}]


def seed() -> None:
    state = seed_service(SERVICE_NAME, PORT, DETAIL, DEFAULT_METRICS)
    print(pretty_json(state))


if __name__ == '__main__':
    seed()
