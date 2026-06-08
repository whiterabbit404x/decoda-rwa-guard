"""One-shot entrypoint for the credential rotation scheduler."""
from __future__ import annotations

import json

from services.api.app.pilot import run_due_credential_rotations


def main() -> int:
    result = run_due_credential_rotations()
    print(json.dumps(result, sort_keys=True))
    return 1 if result.get('failed', 0) else 0


if __name__ == '__main__':
    raise SystemExit(main())
