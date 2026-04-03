#!/usr/bin/env python3
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
LOG_DIR = REPO_ROOT / 'artifacts' / 'web-local-smoke'
LOG_FILE = LOG_DIR / 'next-web.log'

BASE_URL = os.getenv('WEB_LOCAL_SMOKE_BASE_URL', 'http://127.0.0.1:3000').rstrip('/')
HEALTH_PATH = os.getenv('WEB_LOCAL_SMOKE_HEALTH_PATH', '/api/health')
WAIT_SECONDS = float(os.getenv('WEB_LOCAL_SMOKE_WAIT_SECONDS', '120'))
POLL_SECONDS = float(os.getenv('WEB_LOCAL_SMOKE_POLL_SECONDS', '2'))
REQUEST_TIMEOUT_SECONDS = float(os.getenv('WEB_LOCAL_SMOKE_REQUEST_TIMEOUT_SECONDS', '15'))
PLAYWRIGHT_SPEC = os.getenv('WEB_LOCAL_SMOKE_SPEC', 'apps/web/tests/feature4-smoke.spec.ts')


def probe_once(url: str) -> tuple[bool, str]:
    try:
        with urlopen(url, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            body = response.read(2048).decode('utf-8', errors='ignore').lower()
            if response.status >= 400:
                return False, f'HTTP {response.status}'
            if 'starting...' in body or 'compiling' in body:
                return False, 'app is still booting'
            return True, f'HTTP {response.status}'
    except HTTPError as exc:
        return False, f'HTTP {exc.code}'
    except (TimeoutError, socket.timeout):
        return False, f'timed out after {REQUEST_TIMEOUT_SECONDS:.0f}s'
    except URLError as exc:
        reason = getattr(exc, 'reason', exc)
        return False, f'{type(reason).__name__}: {reason}'


def wait_for_url(label: str, url: str) -> None:
    deadline = time.monotonic() + WAIT_SECONDS
    attempt = 0
    last_status = 'no response yet'
    print(f'>>> Waiting for {label} at {url} (timeout={WAIT_SECONDS:.0f}s)')
    while time.monotonic() < deadline:
        attempt += 1
        ok, status = probe_once(url)
        last_status = status
        if ok:
            print(f'>>> {label} ready after attempt {attempt}: {status}')
            return
        print(f'>>> {label} not ready yet (attempt {attempt}): {status}')
        time.sleep(POLL_SECONDS)
    raise RuntimeError(f'{label} did not become ready at {url} within {WAIT_SECONDS:.0f}s (last status: {last_status})')


def terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def print_log_tail() -> None:
    if not LOG_FILE.exists():
        print('>>> No Next.js log file found.')
        return
    print(f'>>> Last lines from {LOG_FILE.relative_to(REPO_ROOT)}:')
    lines = LOG_FILE.read_text(encoding='utf-8', errors='ignore').splitlines()
    for line in lines[-80:]:
        print(line)


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    health_url = urljoin(f'{BASE_URL}/', HEALTH_PATH.lstrip('/'))

    start_command = ['make', 'run-web-smoke']

    print(f'>>> Starting local web app: {" ".join(start_command)}')
    with LOG_FILE.open('w', encoding='utf-8') as log_stream:
        web_process = subprocess.Popen(
            start_command,
            cwd=REPO_ROOT,
            stdout=log_stream,
            stderr=subprocess.STDOUT,
            text=True,
        )

        try:
            wait_for_url('Next.js health route', health_url)
            wait_for_url('Next.js homepage', BASE_URL)

            playwright_env = os.environ.copy()
            playwright_env['PLAYWRIGHT_LOCAL_WEB_SERVER'] = 'false'
            playwright_env['PLAYWRIGHT_BASE_URL'] = BASE_URL

            test_command = ['npx', 'playwright', 'test', PLAYWRIGHT_SPEC]
            print(f'>>> Running Playwright smoke test: {" ".join(test_command)}')
            result = subprocess.run(test_command, cwd=REPO_ROOT, env=playwright_env)
            if result.returncode != 0:
                print_log_tail()
            return result.returncode
        except Exception as exc:  # noqa: BLE001
            print(f'>>> web_local_smoke setup failed: {exc}', file=sys.stderr)
            print_log_tail()
            return 1
        finally:
            terminate_process(web_process)


if __name__ == '__main__':
    raise SystemExit(main())
