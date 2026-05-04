#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

REQUIRED_ARRAY_ARTIFACTS = (
    'telemetry_events.json',
    'detections.json',
    'alerts.json',
    'incidents.json',
    'response_actions.json',
    'runs.json',
)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding='utf-8'))


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Assert required readiness artifacts are non-empty JSON arrays.'
    )
    parser.add_argument(
        '--artifacts-dir',
        default='services/api/artifacts/live_evidence/latest',
        help='Directory containing readiness artifact JSON files.',
    )
    args = parser.parse_args()

    artifacts_dir = Path(args.artifacts_dir).resolve()
    failures: list[str] = []

    for filename in REQUIRED_ARRAY_ARTIFACTS:
        artifact_path = artifacts_dir / filename
        if not artifact_path.exists():
            failures.append(f'missing required artifact: {artifact_path}')
            continue

        payload = _load_json(artifact_path)
        if not isinstance(payload, list):
            failures.append(f'{artifact_path} must be a JSON array (got {type(payload).__name__})')
            continue

        if len(payload) == 0:
            failures.append(f'{artifact_path} must be a non-empty JSON array')

    if failures:
        print('[assert-readiness-artifacts-non-empty] FAIL')
        for failure in failures:
            print(f' - {failure}')
        return 1

    print('[assert-readiness-artifacts-non-empty] PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
