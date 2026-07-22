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

# The single authoritative master switch. An EXPLICIT falsy value here is the
# unambiguous production kill switch: it disables the worker regardless of any other
# enabling flag (STAGING_WORKER_ENABLED / MONITORING_WORKER_ENABLED / LIVE_MODE_ENABLED /
# LIVE_MONITORING_ENABLED). Setting STAGING_WORKER_ENABLED=false alone does NOT disable a
# worker while WORKER_ENABLED (or another enabling alias) is truthy — that OR-semantics
# gap is exactly what left the production worker running; the fix is WORKER_ENABLED=false.
MASTER_WORKER_ENABLED_ENV_VAR = 'WORKER_ENABLED'

_TRUTHY = {'1', 'true', 'yes', 'on'}
_FALSY = {'0', 'false', 'no', 'off'}


def worker_explicitly_disabled() -> bool:
    """True iff ``WORKER_ENABLED`` is EXPLICITLY set to a recognized falsy value.

    This is the hard kill switch. It is deliberately narrow — only the master switch
    set to an unambiguous false (``false`` / ``0`` / ``no`` / ``off``) counts, never an
    unset/blank/garbage value — so a kill is always an explicit operator decision.
    """
    return (os.getenv(MASTER_WORKER_ENABLED_ENV_VAR) or '').strip().lower() in _FALSY


def resolve_worker_enabled() -> dict[str, Any]:
    """Resolve whether the monitoring worker is enabled, and from which flag.

    Returns ``{'enabled': bool, 'source': str, 'env_var': str | None,
    'explicit_disable': bool}``. ``source`` is a human string like
    ``'STAGING_WORKER_ENABLED=true'`` / ``'WORKER_ENABLED=false'`` (or ``'none'`` when no
    var is set); ``env_var`` is just the variable name (or ``None``).

    Precedence (fail-closed):
      1. An EXPLICIT ``WORKER_ENABLED=false`` is authoritative — the worker is disabled
         and NO other flag can re-enable it (``explicit_disable=True``). This is the
         production kill switch.
      2. Otherwise any truthy enabling flag turns the worker on (OR over
         WORKER_ENABLED_ENV_VARS; STAGING_WORKER_ENABLED wins the source attribution).
      3. Nothing set → disabled, source ``'none'`` — so System Health never claims live
         monitoring is running when it is not.
    """
    if worker_explicitly_disabled():
        return {
            'enabled': False,
            'source': 'WORKER_ENABLED=false',
            'env_var': MASTER_WORKER_ENABLED_ENV_VAR,
            'explicit_disable': True,
        }
    for name in WORKER_ENABLED_ENV_VARS:
        if (os.getenv(name) or '').strip().lower() in _TRUTHY:
            return {'enabled': True, 'source': f'{name}=true', 'env_var': name, 'explicit_disable': False}
    return {'enabled': False, 'source': 'none', 'env_var': None, 'explicit_disable': False}
