from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_URL = os.getenv('FEATURE2_FRONTEND_URL', 'http://127.0.0.1:3000')
FRONTEND_WAIT_SECONDS = float(os.getenv('FEATURE2_FRONTEND_WAIT_SECONDS', '180'))
FRONTEND_POLL_SECONDS = float(os.getenv('FEATURE2_FRONTEND_POLL_SECONDS', '2'))
FRONTEND_REQUEST_TIMEOUT_SECONDS = float(os.getenv('FEATURE2_FRONTEND_REQUEST_TIMEOUT_SECONDS', '20'))
FRONTEND_HEALTH_PATH = os.getenv('FEATURE2_FRONTEND_HEALTH_PATH', '/api/health')


def run_step(command: list[str]) -> int:
    print(f"\n>>> Running: {' '.join(command)}")
    completed = subprocess.run(command, cwd=REPO_ROOT)
    return completed.returncode


def build_frontend_url(path: str) -> str:
    return urljoin(f'{FRONTEND_URL.rstrip("/")}/', path.lstrip('/'))


def check_frontend_once(url: str) -> tuple[bool, str]:
    try:
        with urlopen(url, timeout=FRONTEND_REQUEST_TIMEOUT_SECONDS) as response:
            body = response.read(2048).decode('utf-8', errors='ignore').lower()
            if response.status >= 400:
                return False, f'HTTP {response.status}'
            if 'compiling' in body or 'starting...' in body or 'ready in' in body:
                return False, 'Next.js responded but still appears to be compiling.'
            return True, f'HTTP {response.status}'
    except HTTPError as exc:
        return False, f'HTTP {exc.code}'
    except (TimeoutError, socket.timeout):
        return False, f'timed out after {FRONTEND_REQUEST_TIMEOUT_SECONDS:.0f}s'
    except URLError as exc:
        reason = getattr(exc, 'reason', exc)
        return False, f'{type(reason).__name__}: {reason}'


def wait_for_frontend_probe(label: str, url: str) -> None:
    deadline = time.monotonic() + FRONTEND_WAIT_SECONDS
    attempt = 0
    last_status = 'no response yet'

    print(
        f'\n>>> Waiting up to {FRONTEND_WAIT_SECONDS:.0f}s for {label} at {url} '
        f'(poll every {FRONTEND_POLL_SECONDS:.0f}s, request timeout {FRONTEND_REQUEST_TIMEOUT_SECONDS:.0f}s).'
    )

    while time.monotonic() < deadline:
        attempt += 1
        is_ready, status = check_frontend_once(url)
        last_status = status
        if is_ready:
            print(f'>>> {label} ready after attempt {attempt}: {status}.')
            return
        print(f'>>> {label} not ready yet (attempt {attempt}): {status}')
        time.sleep(FRONTEND_POLL_SECONDS)

    raise SystemExit(
        f'Feature 2 smoke suite could not confirm that {label} is ready at '
        f'{url} within {FRONTEND_WAIT_SECONDS:.0f}s. '
        'If `npm run dev --workspace apps/web` is still compiling, wait a bit longer and retry. '
        'If it is not running yet, start it in another terminal first. '
        f'Last observed status: {last_status}'
    )


def ensure_frontend_is_running() -> None:
    wait_for_frontend_probe('the lightweight Next.js readiness route', build_frontend_url(FRONTEND_HEALTH_PATH))
    wait_for_frontend_probe('the homepage', FRONTEND_URL)


if __name__ == '__main__':
    backend_rc = run_step([sys.executable, '-m', 'pytest', 'services/api/tests/test_feature2_smoke.py', '-q'])
    if backend_rc != 0:
        raise SystemExit(backend_rc)

    ensure_frontend_is_running()

    frontend_rc = run_step(['npx', 'playwright', 'test', 'apps/web/tests/feature2-smoke.spec.ts'])
    raise SystemExit(frontend_rc)
