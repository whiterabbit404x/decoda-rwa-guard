"""Seed script for threat-engine."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from phase1_local.dev_support import load_env_file, pretty_json, seed_service

load_env_file()

SERVICE_NAME = 'threat-engine'
PORT = 8002
DETAIL = 'Threat analysis worker using deterministic exploit and anomaly heuristics for local development.'
DEFAULT_METRICS = [
    {
        'metric_key': 'threat_score_mode',
        'label': 'Threat Scoring',
        'value': 'Weighted rules for contract, transaction, and market anomaly analysis are active.',
        'status': 'Ready',
    }
]


def seed() -> None:
    state = seed_service(SERVICE_NAME, PORT, DETAIL, DEFAULT_METRICS)
    print(pretty_json(state))


if __name__ == '__main__':
    seed()
