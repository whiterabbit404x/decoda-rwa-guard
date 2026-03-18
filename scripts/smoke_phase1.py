from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
RISK_PORT = 8101
API_PORT = 8100


def wait_for_http(url: str, timeout_seconds: float = 20.0) -> str:
    deadline = time.time() + timeout_seconds
    last_error = None
    while time.time() < deadline:
        try:
            with urlopen(url, timeout=1.5) as response:
                return response.read().decode('utf-8')
        except Exception as exc:  # pragma: no cover - smoke helper
            last_error = exc
            time.sleep(0.5)
    raise RuntimeError(f'Timed out waiting for {url}: {last_error}')


def spawn(service: str, port: int, extra_env: dict[str, str] | None = None) -> subprocess.Popen[str]:
    env = os.environ.copy()
    env.setdefault('APP_MODE', 'local')
    env.setdefault('REDIS_ENABLED', 'false')
    env.setdefault('SQLITE_PATH', '.data/phase1.db')
    if extra_env:
        env.update(extra_env)
    return subprocess.Popen(
        [
            PYTHON,
            'scripts/run_service.py',
            service,
            '--host',
            '127.0.0.1',
            '--port',
            str(port),
        ],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def stop_process(process: subprocess.Popen[str] | None) -> None:
    if process is None or process.poll() is not None:
        return
    if os.name == 'nt':
        process.terminate()
    else:
        process.send_signal(signal.SIGINT)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def main() -> int:
    risk_process = None
    api_process = None
    try:
        risk_process = spawn('risk-engine', RISK_PORT)
        wait_for_http(f'http://127.0.0.1:{RISK_PORT}/health')
        wait_for_http(f'http://127.0.0.1:{RISK_PORT}/docs')

        api_process = spawn('api', API_PORT, {'RISK_ENGINE_URL': f'http://127.0.0.1:{RISK_PORT}'})
        wait_for_http(f'http://127.0.0.1:{API_PORT}/health')

        with urlopen(f'http://127.0.0.1:{API_PORT}/risk/dashboard', timeout=2.0) as response:
            live_payload = json.loads(response.read().decode('utf-8'))
        assert live_payload['source'] == 'live', live_payload
        assert live_payload['degraded'] is False, live_payload
        assert live_payload['risk_engine']['fallback_items'] == 0, live_payload
        assert all(item['source'] == 'live' for item in live_payload['transaction_queue']), live_payload

        stop_process(risk_process)
        risk_process = None
        time.sleep(1.0)

        with urlopen(f'http://127.0.0.1:{API_PORT}/risk/dashboard', timeout=2.0) as response:
            fallback_payload = json.loads(response.read().decode('utf-8'))
        assert fallback_payload['source'] == 'fallback', fallback_payload
        assert fallback_payload['degraded'] is True, fallback_payload
        assert fallback_payload['risk_engine']['live_items'] == 0, fallback_payload
        assert all(item['source'] == 'fallback' for item in fallback_payload['transaction_queue']), fallback_payload
        assert 'message' in fallback_payload, fallback_payload

        print('Smoke check passed: risk-engine, API, live dashboard fetch, and fallback dashboard shape all succeeded.')
        return 0
    except (AssertionError, URLError, RuntimeError) as exc:
        print(f'Smoke check failed: {exc}')
        return 1
    finally:
        stop_process(api_process)
        stop_process(risk_process)


if __name__ == '__main__':
    raise SystemExit(main())
