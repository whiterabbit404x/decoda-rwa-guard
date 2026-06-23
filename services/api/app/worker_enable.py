"""Single source of truth for whether the monitoring worker / live monitoring
is enabled.

Kept dependency-free (only ``os``) on purpose so both the worker startup
(``run_monitoring_worker``) and the status page (``system_health``) can import
it without dragging in heavier modules. ``pilot`` re-exports these symbols for
backwards compatibility.
"""
from __future__ import annotations

import os
from typing import Any

# Env vars that enable the monitoring worker loop, in priority order. The worker
# startup folds any truthy one into LIVE_MODE_ENABLED (the canonical gate consumed
# by pilot.live_mode_enabled()), and System Health reports the SAME effective value
# via resolve_worker_enabled() — so the Worker card and the Live Chain Monitoring
# panel can never disagree about whether live monitoring is enabled.
#
# LIVE_MONITORING_ENABLED is intentionally NOT in this list: that flag controls
# provider/ingestion mode (live vs degraded), not whether the worker loop runs.
WORKER_ENABLED_ENV_VARS: tuple[str, ...] = (
    'STAGING_WORKER_ENABLED',
    'WORKER_ENABLED',
    'MONITORING_WORKER_ENABLED',
    'LIVE_MODE_ENABLED',
)

_TRUTHY = {'1', 'true', 'yes', 'on'}


def resolve_worker_enabled() -> dict[str, Any]:
    """Resolve whether the monitoring worker is enabled, and from which flag.

    Returns ``{'enabled': bool, 'source': str, 'env_var': str | None}``. ``source``
    is a human string like ``'STAGING_WORKER_ENABLED=true'`` (or ``'none'`` when no
    enabling var is set); ``env_var`` is just the variable name (or ``None``).

    Fail-closed: defaults to disabled when nothing is set, matching the worker —
    which logs ``enabled_reason=none_set`` and will not run its loop — so System
    Health never claims live monitoring is running when it is not.
    """
    for name in WORKER_ENABLED_ENV_VARS:
        if (os.getenv(name) or '').strip().lower() in _TRUTHY:
            return {'enabled': True, 'source': f'{name}=true', 'env_var': name}
    return {'enabled': False, 'source': 'none', 'env_var': None}
