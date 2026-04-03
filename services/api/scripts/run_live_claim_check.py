#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    api_url = (os.getenv('API_URL') or 'http://localhost:8000').rstrip('/')
    token = os.getenv('PILOT_AUTH_TOKEN', '').strip()
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    req = Request(f'{api_url}/ops/production-claim-validator', headers=headers)
    with urlopen(req, timeout=20) as resp:  # nosec B310
        payload = json.loads(resp.read().decode('utf-8'))
    print(json.dumps(payload, indent=2))
    return 0 if payload.get('status') == 'PASS' else 2


if __name__ == '__main__':
    raise SystemExit(main())
