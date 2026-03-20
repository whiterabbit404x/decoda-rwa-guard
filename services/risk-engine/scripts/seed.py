"""Seed script for risk-engine."""

import sys
from pathlib import Path


def _find_repo_root(start: Path) -> Path:
    for candidate in start.resolve().parents:
        if (candidate / 'phase1_local').is_dir():
            return candidate
    raise RuntimeError(f"Unable to locate repo root from {start} via a phase1_local directory search.")


def _ensure_repo_root_on_path() -> Path:
    repo_root = _find_repo_root(Path(__file__))
    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)
    return repo_root


REPO_ROOT = _ensure_repo_root_on_path()

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
