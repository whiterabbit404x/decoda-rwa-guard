"""Seed script for compliance-service."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from phase1_local.dev_support import load_env_file, pretty_json, seed_service

load_env_file()

SERVICE_NAME = 'compliance-service'
PORT = 8004
DETAIL = 'Sovereign-grade compliance wrapper and governance service for deterministic transfer, residency, and policy action demos.'
DEFAULT_METRICS = [
    {
        'metric_key': 'compliance_wrappers',
        'label': 'Compliance Wrappers',
        'value': 'Deterministic transfer screening rules are active for KYC, sanctions, jurisdiction, and thresholds.',
        'status': 'Ready',
    },
    {
        'metric_key': 'governance_ledger',
        'label': 'Governance Ledger',
        'value': 'Wallet freezes, allowlists, blocklists, and asset pauses are recorded locally with fingerprints.',
        'status': 'Tracking',
    },
]


def seed() -> None:
    state = seed_service(SERVICE_NAME, PORT, DETAIL, DEFAULT_METRICS)
    print(pretty_json(state))


if __name__ == '__main__':
    seed()
