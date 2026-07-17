"""Tests for the container start-command dispatch (services/api/docker-entrypoint.sh).

Root cause these guard against: the Dockerfile CMD previously defaulted to uvicorn
whenever APP_START_COMMAND was unset, so a dedicated monitoring-worker Railway service
that was not pointed at railway-worker.json (and had no Custom Start Command) silently
booted the API — its logs looked like API/QuickNode traffic and it NEVER emitted
event=monitoring_worker_process_boot.

The entrypoint now resolves the command from, in priority order:
  1. APP_START_COMMAND — explicit override always wins (back-compat).
  2. SERVICE_ROLE      — role name mapped to the matching module command.
  3. default           — uvicorn (the API).

These run the real script in print-only mode (CONTAINER_ENTRYPOINT_PRINT_ONLY=1) so the
exact dispatch an operator's Railway service would get is asserted, without exec-ing.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

API_ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = API_ROOT / 'docker-entrypoint.sh'

MONITORING_WORKER_CMD = 'python -m services.api.app.run_monitoring_worker'
API_CMD = 'uvicorn services.api.app.main:app --host 0.0.0.0 --port 8000'


def _run(env_overrides: dict[str, str]) -> str:
    """Run the entrypoint in print-only mode and return the emitted line."""
    env = {'PATH': '/usr/bin:/bin', 'CONTAINER_ENTRYPOINT_PRINT_ONLY': '1'}
    env.update(env_overrides)
    result = subprocess.run(
        ['sh', str(ENTRYPOINT)],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def test_entrypoint_script_exists() -> None:
    assert ENTRYPOINT.is_file(), 'services/api/docker-entrypoint.sh must exist for the Dockerfile CMD'


@pytest.mark.skipif(sys.platform.startswith('win'), reason='POSIX shell entrypoint')
def test_unset_defaults_to_api() -> None:
    line = _run({})
    assert f'command={API_CMD}' in line
    assert 'source=SERVICE_ROLE' in line
    assert 'service_role=unset' in line


@pytest.mark.skipif(sys.platform.startswith('win'), reason='POSIX shell entrypoint')
@pytest.mark.parametrize('role', ['worker', 'monitoring-worker', 'monitoring_worker'])
def test_worker_role_runs_monitoring_worker(role: str) -> None:
    line = _run({'SERVICE_ROLE': role})
    assert f'command={MONITORING_WORKER_CMD}' in line
    assert f'service_role={role}' in line
    # This is the whole point: a worker service can NEVER resolve to uvicorn/the API.
    assert 'uvicorn' not in line


@pytest.mark.skipif(sys.platform.startswith('win'), reason='POSIX shell entrypoint')
@pytest.mark.parametrize('role', ['api', 'web'])
def test_api_role_runs_uvicorn(role: str) -> None:
    line = _run({'SERVICE_ROLE': role})
    assert f'command={API_CMD}' in line
    assert 'run_monitoring_worker' not in line


@pytest.mark.skipif(sys.platform.startswith('win'), reason='POSIX shell entrypoint')
@pytest.mark.parametrize('role,module', [
    ('ai-triage-worker', 'run_ai_triage_worker'),
    ('onboarding-worker', 'run_onboarding_worker'),
    ('quicknode-live-worker', 'run_quicknode_live_worker'),
    ('realtime-worker', 'run_realtime_worker'),
    ('recovery-drill-worker', 'run_recovery_drill_worker'),
    ('retention-worker', 'retention_worker'),
])
def test_other_worker_roles_map_to_their_module(role: str, module: str) -> None:
    line = _run({'SERVICE_ROLE': role})
    assert f'command=python -m services.api.app.{module}' in line


@pytest.mark.skipif(sys.platform.startswith('win'), reason='POSIX shell entrypoint')
def test_app_start_command_overrides_service_role() -> None:
    # Explicit APP_START_COMMAND must win even when SERVICE_ROLE would map elsewhere.
    line = _run({'SERVICE_ROLE': 'worker', 'APP_START_COMMAND': 'python -m custom.entry'})
    assert 'command=python -m custom.entry' in line
    assert 'source=APP_START_COMMAND' in line


@pytest.mark.skipif(sys.platform.startswith('win'), reason='POSIX shell entrypoint')
def test_unknown_role_defaults_to_api_and_is_flagged() -> None:
    line = _run({'SERVICE_ROLE': 'totally-bogus'})
    assert f'command={API_CMD}' in line
    assert 'SERVICE_ROLE_unknown_defaulted_to_api' in line


@pytest.mark.skipif(sys.platform.startswith('win'), reason='POSIX shell entrypoint')
def test_port_is_respected_for_api() -> None:
    line = _run({'SERVICE_ROLE': 'api', 'PORT': '9000'})
    assert '--port 9000' in line


@pytest.mark.skipif(sys.platform.startswith('win'), reason='POSIX shell entrypoint')
def test_emits_container_start_command_marker() -> None:
    # The container-level proof line must be present so operators can grep, before Python
    # even starts, which process the service launched and why.
    line = _run({'SERVICE_ROLE': 'worker'})
    assert line.startswith('event=container_start_command ')
