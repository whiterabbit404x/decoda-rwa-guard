#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def main() -> int:
    root_package = read_json(REPO_ROOT / 'package.json')
    web_package = read_json(REPO_ROOT / 'apps/web/package.json')
    lock = read_json(REPO_ROOT / 'package-lock.json')

    root_playwright_declared = str(root_package.get('devDependencies', {}).get('playwright', '')).strip()
    root_playwright_test_declared = str(root_package.get('devDependencies', {}).get('@playwright/test', '')).strip()
    web_next_declared = str(web_package.get('dependencies', {}).get('next', '')).strip()

    lock_packages = lock.get('packages', {})
    lock_next_workspace = str(lock_packages.get('apps/web', {}).get('dependencies', {}).get('next', '')).strip()
    lock_next_installed = str(lock_packages.get('node_modules/next', {}).get('version', '')).strip()
    lock_playwright_installed = str(lock_packages.get('node_modules/playwright', {}).get('version', '')).strip()
    lock_playwright_test_installed = str(lock_packages.get('node_modules/@playwright/test', {}).get('version', '')).strip()

    installed_next = ''
    installed_playwright = ''
    installed_playwright_test = ''
    next_package_json = REPO_ROOT / 'node_modules/next/package.json'
    pw_package_json = REPO_ROOT / 'node_modules/playwright/package.json'
    pw_test_package_json = REPO_ROOT / 'node_modules/@playwright/test/package.json'

    if next_package_json.exists():
        installed_next = str(read_json(next_package_json).get('version', '')).strip()
    if pw_package_json.exists():
        installed_playwright = str(read_json(pw_package_json).get('version', '')).strip()
    if pw_test_package_json.exists():
        installed_playwright_test = str(read_json(pw_test_package_json).get('version', '')).strip()

    checks: list[tuple[str, str, str]] = [
        ('web.next declared vs lock workspace', web_next_declared, lock_next_workspace),
        ('web.next declared vs lock installed', web_next_declared, lock_next_installed),
        ('root playwright declared vs lock installed', root_playwright_declared, lock_playwright_installed),
        ('root @playwright/test declared vs lock installed', root_playwright_test_declared, lock_playwright_test_installed),
    ]

    if installed_next:
        checks.append(('web.next declared vs installed runtime', web_next_declared, installed_next))
    if installed_playwright:
        checks.append(('root playwright declared vs installed runtime', root_playwright_declared, installed_playwright))
    if installed_playwright_test:
        checks.append(('root @playwright/test declared vs installed runtime', root_playwright_test_declared, installed_playwright_test))

    failures = [name for name, expected, actual in checks if not expected or not actual or expected != actual]
    payload = {
        'ok': not failures,
        'checks': [
            {'name': name, 'expected': expected, 'actual': actual, 'ok': bool(expected and actual and expected == actual)}
            for name, expected, actual in checks
        ],
        'remediation': [
            'Update package.json versions to match the lockfile or regenerate package-lock.json from a trusted npm install.',
            'Run `npm install --package-lock-only` after adjusting versions, then rerun this check.',
            'If installed runtime differs, delete node_modules and run `npm ci`.',
        ],
    }
    print(json.dumps(payload, indent=2))
    return 0 if payload['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
