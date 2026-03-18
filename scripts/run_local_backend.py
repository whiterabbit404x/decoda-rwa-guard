from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

SERVICES = [
    ('api', 8000),
    ('risk-engine', 8001),
    ('oracle-service', 8002),
    ('compliance-service', 8003),
    ('reconciliation-service', 8004),
    ('event-watcher', 8005),
]


def spawn(service_name: str, port: int) -> subprocess.Popen[str]:
    service_dir = REPO_ROOT / 'services' / service_name
    env = os.environ.copy()
    env.setdefault('APP_MODE', 'local')
    env.setdefault('REDIS_ENABLED', 'false')
    env.setdefault('PORT', str(port))
    existing_path = env.get('PYTHONPATH', '')
    env['PYTHONPATH'] = str(REPO_ROOT) if not existing_path else f"{REPO_ROOT}:{existing_path}"
    return subprocess.Popen(
        [
            sys.executable,
            '-m',
            'uvicorn',
            'app.main:app',
            '--env-file',
            '.env',
            '--reload',
            '--host',
            '0.0.0.0',
            '--port',
            str(port),
        ],
        cwd=service_dir,
        env=env,
    )


def main() -> int:
    processes = [(name, spawn(name, port)) for name, port in SERVICES]
    print('Started local backend services:')
    for name, process in processes:
        print(f'  - {name} (pid={process.pid})')

    try:
        while True:
            failed = [(name, process.returncode) for name, process in processes if process.poll() is not None]
            if failed:
                name, returncode = failed[0]
                print(f'Service {name} exited early with code {returncode}. Shutting down the stack...')
                return returncode or 1
            time.sleep(1)
    except KeyboardInterrupt:
        print('\nStopping local backend services...')
        return 0
    finally:
        for _, process in processes:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
        for _, process in processes:
            if process.poll() is None:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()


if __name__ == '__main__':
    raise SystemExit(main())
