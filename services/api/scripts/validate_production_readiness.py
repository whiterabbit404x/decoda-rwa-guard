#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.api.scripts.validate_staging import run_validation


if __name__ == '__main__':
    os.environ['VALIDATION_MODE'] = 'production'
    raise SystemExit(run_validation(mode='production'))
