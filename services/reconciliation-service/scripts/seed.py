"""Seed script for reconciliation-service."""

from phase1_local.dev_support import load_env_file, pretty_json, seed_service

load_env_file()

SERVICE_NAME = 'reconciliation-service'
PORT = 8005
DETAIL = 'Interoperability and systemic resilience service for deterministic cross-chain reconciliation, liquidity backstop evaluation, and local incident logging.'
DEFAULT_METRICS = [
    {
        'metric_key': 'reconciliation_status',
        'label': 'Reconciliation Status',
        'value': 'Multi-ledger reconciliation and incident logging controls are active for ethereum, avalanche, and private-bank-ledger.',
        'status': 'Ready',
    },
    {
        'metric_key': 'backstop_controls',
        'label': 'Backstop Controls',
        'value': 'Circuit-breaker, bridge-pause, and threshold-reduction rules are loaded for deterministic stress scenarios.',
        'status': 'Configured',
    },
]


def seed() -> None:
    state = seed_service(SERVICE_NAME, PORT, DETAIL, DEFAULT_METRICS)
    print(pretty_json(state))


if __name__ == '__main__':
    seed()
