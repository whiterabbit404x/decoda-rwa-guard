"""Make ``tools/`` importable so the standalone verifier can be tested in place.

This is the ONLY thing the test run needs: the verifier itself imports nothing
from the Decoda application, and nothing in ``tools/`` is on the service's import
path at runtime.
"""
from __future__ import annotations

import sys
from pathlib import Path

TOOLS_ROOT = Path(__file__).resolve().parent
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))
