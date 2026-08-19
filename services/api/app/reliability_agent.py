"""Screen 12 — Self-Healing Reliability Agent.

An autonomous operational layer that monitors Decoda RWA Guard *itself* and
answers one question: is Decoda healthy enough to be trusted to monitor
customers' tokenized assets right now?

Design (see CLAUDE.md truthfulness rules):

    OBSERVE  -> canonical ``system_health.build_system_health_snapshot`` (the ONE
                health evaluator; this module never invents component status).
    CORRELATE-> deterministic ``correlate_findings`` turns non-healthy canonical
                components into structured findings with a machine ``signal`` and
                operational ``severity``.
    ROOT CAUSE-> ``derive_root_cause`` picks the deepest failing dependency layer
                so a monitoring-worker stall is not mislabelled a platform outage.
    POLICY   -> ``classify_action`` + ``remediation_capability`` +
                ``evaluate_remediation_gate`` decide whether an action is safe to
                automate, needs approval, is forbidden, or is unsupported by the
                current deployment. A recommendation is NOT an executed action.
    EXECUTE  -> only where a REAL mechanism exists (an env-configured command,
                mirroring recovery_drills). Otherwise ``manual_required``.
    VERIFY   -> ``verify_recovery`` re-reads canonical health. Execution returning
                without exception is NOT recovery; recovery requires verification.
    AUDIT    -> every attempt flows to the canonical hash-chained ``audit_logs``
                via ``pilot.log_audit`` and to the ``reliability_events`` ledger.

Nothing here fakes uptime, response time, error rate, restart counts, or health.
Unknown never becomes healthy; disabled never becomes crashed.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

# ---------------------------------------------------------------------------
# Normalized vocabulary
# ---------------------------------------------------------------------------
# The canonical snapshot classifies components as healthy/degraded/failing/
# unavailable. The agent normalizes to a richer operational enum that can tell
# "we cannot observe it" (unknown) and "intentionally off" (disabled) apart from
# "broken" (unhealthy) — the distinction the truthfulness rules demand.
STATUS_HEALTHY = 'healthy'
STATUS_DEGRADED = 'degraded'
STATUS_UNHEALTHY = 'unhealthy'
STATUS_UNKNOWN = 'unknown'
STATUS_DISABLED = 'disabled'

SEVERITY_LOW = 'low'
SEVERITY_MEDIUM = 'medium'
SEVERITY_HIGH = 'high'
SEVERITY_CRITICAL = 'critical'
_SEVERITY_ORDER = {SEVERITY_LOW: 0, SEVERITY_MEDIUM: 1, SEVERITY_HIGH: 2, SEVERITY_CRITICAL: 3}

# Canonical component catalog. Keys match ``system_health`` snapshot component
# keys exactly (we consume its facts, we do not rename its truth). ``layer`` is
# the dependency depth used for root-cause correlation (lower = deeper infra).
COMPONENT_CATALOG: dict[str, dict[str, Any]] = {
    'database':       {'label': 'Database',             'layer': 0, 'critical': True,  'monitoring_path': True},
    'redis':          {'label': 'Redis / Queue',        'layer': 0, 'critical': False, 'monitoring_path': True},
    'api':            {'label': 'API Gateway',          'layer': 1, 'critical': True,  'monitoring_path': False},
    'worker':         {'label': 'Monitoring Worker',    'layer': 2, 'critical': False, 'monitoring_path': True},
    'base_rpc':       {'label': 'Base RPC',             'layer': 3, 'critical': False, 'monitoring_path': True},
    'live_polling':   {'label': 'Monitoring Loop',      'layer': 4, 'critical': False, 'monitoring_path': True},
    'telemetry':      {'label': 'Telemetry Ingestion',  'layer': 5, 'critical': False, 'monitoring_path': True},
    'detection':      {'label': 'Detection Engine',     'layer': 6, 'critical': False, 'monitoring_path': True},
    'alert_delivery': {'label': 'Alert Delivery',       'layer': 7, 'critical': False, 'monitoring_path': True},
}
# Stable display/report order.
COMPONENT_ORDER = ['api', 'telemetry', 'detection', 'database', 'redis', 'worker', 'base_rpc', 'live_polling', 'alert_delivery']


# ---------------------------------------------------------------------------
# Configuration (documented, centralized — no scattered literals)
# ---------------------------------------------------------------------------

def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


class RemediationConfig:
    """Bounded self-healing policy configuration.

    Every value is env-overridable and documented; none is a buried literal.
    Defaults are conservative and fail-safe: without an explicitly configured
    restart command and an explicit opt-in, the agent stays recommendation-only.
    """

    def __init__(self) -> None:
        # Loop-guard: at most N automatic restarts of a component per window.
        self.max_restarts_per_window = _env_int('RELIABILITY_MAX_RESTARTS_PER_WINDOW', 3, minimum=1)
        self.restart_window_seconds = _env_int('RELIABILITY_RESTART_WINDOW_SECONDS', 3600, minimum=60)
        # Minimum spacing between two restart attempts of the same component.
        self.restart_cooldown_seconds = _env_int('RELIABILITY_RESTART_COOLDOWN_SECONDS', 300, minimum=30)
        # How long a supported command may run before we treat it as failed.
        self.command_timeout_seconds = _env_int('RELIABILITY_COMMAND_TIMEOUT_SECONDS', 120, minimum=5)
        # After this many consecutive failed remediations, stop and escalate.
        self.escalate_after_failures = _env_int('RELIABILITY_ESCALATE_AFTER_FAILURES', 2, minimum=1)
        # Minimum persisted samples / span before Uptime can be reported (else
        # "Insufficient history"). Prevents a fabricated 99.97% on a fresh deploy.
        self.min_uptime_samples = _env_int('RELIABILITY_MIN_UPTIME_SAMPLES', 5, minimum=1)

    @property
    def auto_healing_mode(self) -> str:
        """Effective autonomous-healing posture, derived from real capability.

        ``enabled`` only when the operator explicitly opted in AND at least one
        real remediation command is configured — otherwise ``recommendation_only``
        (or ``disabled`` if the operator turned it off). This never claims an
        automatic capability the deployment cannot actually perform.
        """
        raw = (os.getenv('RELIABILITY_AUTO_HEALING', '') or '').strip().lower()
        if raw in {'disabled', 'off', 'false', '0'}:
            return 'disabled'
        if raw in {'enabled', 'auto', 'true', '1', 'on'} and configured_remediation_commands():
            return 'enabled'
        return 'recommendation_only'


# Real, supported remediation mechanisms. A component is auto-remediable ONLY if
# its command env var is set to an executable command (same contract as
# recovery_drills). No command configured => the action is ``unsupported`` and the
# UI truthfully says "Manual execution required" — we never fake a restart.
REMEDIATION_COMMAND_ENV: dict[str, str] = {
    'worker': 'RELIABILITY_WORKER_RESTART_COMMAND',
}


def configured_remediation_commands() -> dict[str, str]:
    """Return {component: command} for every configured, non-empty command."""
    out: dict[str, str] = {}
    for component, env_name in REMEDIATION_COMMAND_ENV.items():
        command = (os.getenv(env_name, '') or '').strip()
        if command:
            out[component] = command
    return out


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        text = str(value).replace('Z', '+00:00')
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _iso(value: Any) -> str | None:
    parsed = _parse_ts(value)
    return parsed.isoformat().replace('+00:00', 'Z') if parsed else None


def human_ago(value: Any, *, now: datetime | None = None) -> str | None:
    ts = _parse_ts(value)
    if ts is None:
        return None
    now = now or _utc_now()
    seconds = max(0, int((now - ts).total_seconds()))
    if seconds < 60:
        return f'{seconds}s ago'
    if seconds < 3600:
        return f'{seconds // 60}m ago'
    if seconds < 86400:
        return f'{seconds // 3600}h ago'
    return f'{seconds // 86400}d ago'


# ---------------------------------------------------------------------------
# OBSERVE / NORMALIZE — canonical snapshot component -> normalized fact
# ---------------------------------------------------------------------------

def _worker_enabled(snapshot: Mapping[str, Any]) -> bool | None:
    chain = snapshot.get('live_chain_monitoring') or {}
    if 'worker_enabled' in chain:
        return bool(chain.get('worker_enabled'))
    return None


def normalize_component(component_key: str, snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Map one canonical snapshot component onto the normalized operational enum.

    Consumes only canonical facts. Crucially distinguishes:
      * disabled (intentionally off — e.g. worker-enable flag unset),
      * unknown  (no observable signal / dependency disabled upstream),
      * unhealthy (a real failure),
    so that "we cannot see it" is never rendered as healthy and "it is off on
    purpose" is never rendered as a crash.
    """
    meta = COMPONENT_CATALOG.get(component_key, {'label': component_key, 'layer': 9, 'critical': False, 'monitoring_path': False})
    comp = dict((snapshot.get('components') or {}).get(component_key) or {})
    raw = str(comp.get('status') or 'unavailable')
    message = str(comp.get('message') or '')
    chain = snapshot.get('live_chain_monitoring') or {}
    worker_on = _worker_enabled(snapshot)

    status = STATUS_UNKNOWN
    signal: str | None = None
    disabled_reason: str | None = None

    lower_msg = message.lower()

    if component_key == 'worker':
        if worker_on is False:
            status, disabled_reason = STATUS_DISABLED, 'Worker-enable flag not set (WORKER_ENABLED/STAGING_WORKER_ENABLED).'
        elif raw == 'healthy':
            status = STATUS_HEALTHY
        elif raw == 'failing':
            status, signal = STATUS_UNHEALTHY, 'heartbeat_missing'
        elif raw == 'degraded':
            status, signal = STATUS_DEGRADED, 'heartbeat_stale'
        else:
            status = STATUS_UNKNOWN
    elif component_key == 'redis':
        if raw == 'unavailable' and 'not configured' in lower_msg:
            status, disabled_reason = STATUS_DISABLED, 'Redis is not configured (REDIS_URL unset).'
        elif raw == 'healthy':
            status = STATUS_HEALTHY
        elif raw == 'failing':
            status, signal = STATUS_UNHEALTHY, 'redis_unreachable'
        elif raw == 'degraded':
            status, signal = STATUS_DEGRADED, 'redis_degraded'
        else:
            status = STATUS_UNKNOWN
    elif component_key in ('telemetry', 'detection', 'live_polling'):
        # Downstream of the monitoring worker. If the worker is intentionally
        # disabled, absence of data is EXPECTED (unknown/disabled), never a crash.
        if worker_on is False:
            status = STATUS_DISABLED
            disabled_reason = 'Monitoring worker disabled — no live monitoring is expected to flow.'
        elif raw == 'healthy':
            status = STATUS_HEALTHY
        elif raw == 'degraded':
            status = STATUS_DEGRADED
            signal = {'telemetry': 'telemetry_stale', 'detection': 'detection_stall', 'live_polling': 'poll_stale'}[component_key]
        elif raw == 'failing':
            status = STATUS_UNHEALTHY
            signal = {'telemetry': 'telemetry_gap', 'detection': 'detection_stall', 'live_polling': 'poll_stopped'}[component_key]
        else:  # unavailable => no records observed
            status = STATUS_UNKNOWN
            signal = {'telemetry': 'telemetry_absent', 'detection': 'detection_absent', 'live_polling': 'poll_absent'}[component_key]
    elif component_key == 'base_rpc':
        if raw == 'unavailable' and ('not configured' in lower_msg or 'no ' in lower_msg):
            status, disabled_reason = STATUS_DISABLED, 'Base RPC endpoint not configured.'
        elif raw == 'healthy':
            status = STATUS_HEALTHY
        elif raw == 'degraded':
            status, signal = STATUS_DEGRADED, 'rpc_degraded'
        elif raw == 'failing':
            status, signal = STATUS_UNHEALTHY, 'rpc_unreachable'
        else:
            status = STATUS_UNKNOWN
    else:
        # api, database, alert_delivery and any other component: direct mapping.
        if raw == 'healthy':
            status = STATUS_HEALTHY
        elif raw == 'degraded':
            status, signal = STATUS_DEGRADED, f'{component_key}_degraded'
        elif raw == 'failing':
            status, signal = STATUS_UNHEALTHY, f'{component_key}_unreachable'
        else:
            status = STATUS_UNKNOWN
            signal = f'{component_key}_unobservable'

    # Freshness age (numeric) for monitoring-path components, from canonical chain
    # monitoring facts where available.
    age_seconds: int | None = None
    last_healthy_at: str | None = None
    if component_key == 'worker':
        age_seconds = chain.get('heartbeat_age_seconds')
        last_healthy_at = _iso(chain.get('last_heartbeat_at'))
    elif component_key == 'telemetry':
        last_healthy_at = _iso(chain.get('last_telemetry_at'))
    elif component_key == 'detection':
        last_healthy_at = _iso(chain.get('last_detection_at'))
    elif component_key == 'live_polling':
        last_healthy_at = _iso(chain.get('last_successful_poll_at') or chain.get('last_poll_at'))

    return {
        'component': component_key,
        'label': meta['label'],
        'status': status,
        'raw_status': raw,
        'signal': signal if status not in (STATUS_HEALTHY, STATUS_DISABLED) else None,
        'reason': message or None,
        'action_hint': comp.get('action'),
        'age_human': comp.get('age'),
        'age_seconds': int(age_seconds) if isinstance(age_seconds, (int, float)) else None,
        'last_healthy_at': last_healthy_at,
        'last_event': comp.get('last_event'),
        'metric': comp.get('metric'),
        'disabled_reason': disabled_reason,
        'critical': bool(meta['critical']),
        'monitoring_path': bool(meta['monitoring_path']),
        'layer': meta['layer'],
    }


def normalize_all(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    keys = list((snapshot.get('components') or {}).keys())
    ordered = [k for k in COMPONENT_ORDER if k in keys] + [k for k in keys if k not in COMPONENT_ORDER]
    return [normalize_component(k, snapshot) for k in ordered]


# ---------------------------------------------------------------------------
# CORRELATE / CLASSIFY — findings + severity
# ---------------------------------------------------------------------------

# Operational severity is a function of monitoring/security impact, NOT UI colour.
# (component, normalized_status) -> severity.
_SEVERITY_TABLE: dict[tuple[str, str], str] = {
    ('api', STATUS_UNHEALTHY): SEVERITY_CRITICAL,
    ('api', STATUS_UNKNOWN): SEVERITY_HIGH,
    ('database', STATUS_UNHEALTHY): SEVERITY_CRITICAL,
    ('database', STATUS_UNKNOWN): SEVERITY_HIGH,
    ('database', STATUS_DEGRADED): SEVERITY_HIGH,
    ('redis', STATUS_UNHEALTHY): SEVERITY_HIGH,
    ('redis', STATUS_DEGRADED): SEVERITY_MEDIUM,
    ('worker', STATUS_UNHEALTHY): SEVERITY_CRITICAL,   # heartbeat missing while enabled => monitoring blind
    ('worker', STATUS_DEGRADED): SEVERITY_HIGH,        # heartbeat stale while enabled => imminent gap
    ('worker', STATUS_UNKNOWN): SEVERITY_MEDIUM,
    ('telemetry', STATUS_UNHEALTHY): SEVERITY_HIGH,
    ('telemetry', STATUS_DEGRADED): SEVERITY_HIGH,
    ('telemetry', STATUS_UNKNOWN): SEVERITY_MEDIUM,
    ('detection', STATUS_UNHEALTHY): SEVERITY_HIGH,
    ('detection', STATUS_DEGRADED): SEVERITY_MEDIUM,
    ('detection', STATUS_UNKNOWN): SEVERITY_MEDIUM,
    ('live_polling', STATUS_UNHEALTHY): SEVERITY_HIGH,
    ('live_polling', STATUS_DEGRADED): SEVERITY_HIGH,
    ('live_polling', STATUS_UNKNOWN): SEVERITY_MEDIUM,
    ('base_rpc', STATUS_UNHEALTHY): SEVERITY_HIGH,
    ('base_rpc', STATUS_DEGRADED): SEVERITY_MEDIUM,
    ('alert_delivery', STATUS_UNHEALTHY): SEVERITY_HIGH,
    ('alert_delivery', STATUS_DEGRADED): SEVERITY_MEDIUM,
}


def _finding_severity(component: str, status: str) -> str:
    return _SEVERITY_TABLE.get((component, status), SEVERITY_MEDIUM if status in (STATUS_DEGRADED, STATUS_UNKNOWN) else SEVERITY_HIGH)


def _finding_summary(norm: dict[str, Any]) -> str:
    label = norm['label']
    status = norm['status']
    if status == STATUS_UNHEALTHY:
        base = f'{label} is unhealthy'
    elif status == STATUS_DEGRADED:
        base = f'{label} is degraded'
    else:
        base = f'{label} state cannot be observed'
    age = norm.get('age_human')
    if age:
        base += f' ({age})'
    return base + '.'


def correlate_findings(snapshot: Mapping[str, Any], *, slo_metrics: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Deterministically turn non-healthy canonical components into structured
    findings. Healthy and intentionally-disabled components produce no finding.

    ``slo_metrics`` (optional, from monitoring_reliability.monitoring_slo_snapshot)
    adds a backlog finding when the durable queue exceeds its bound.
    """
    findings: list[dict[str, Any]] = []
    for norm in normalize_all(snapshot):
        if norm['status'] in (STATUS_HEALTHY, STATUS_DISABLED):
            continue
        component = norm['component']
        status = norm['status']
        severity = _finding_severity(component, status)
        findings.append({
            'component': component,
            'label': norm['label'],
            'signal': norm['signal'] or f'{component}_{status}',
            'severity': severity,
            'status': status,
            'summary': _finding_summary(norm),
            'reason': norm['reason'],
            'age_seconds': norm['age_seconds'],
            'age_human': norm['age_human'],
            'last_healthy_at': norm['last_healthy_at'],
            'monitoring_path': norm['monitoring_path'],
            'critical': norm['critical'],
            'layer': norm['layer'],
            'evidence': {
                'raw_status': norm['raw_status'],
                'message': norm['reason'],
                'age_seconds': norm['age_seconds'],
                'last_healthy_at': norm['last_healthy_at'],
            },
            'fingerprint': f'{component}:{norm["signal"] or status}',
        })

    # Backlog finding from durable-queue depth (a real, measurable signal).
    if slo_metrics is not None:
        metrics = slo_metrics.get('metrics') if isinstance(slo_metrics, Mapping) else None
        checks = slo_metrics.get('checks') if isinstance(slo_metrics, Mapping) else None
        depth = None
        bound = None
        if isinstance(metrics, Mapping):
            depth = metrics.get('queue_depth')
        if isinstance(checks, Mapping) and isinstance(checks.get('queue_depth'), Mapping):
            bound = checks['queue_depth'].get('target')
            if checks['queue_depth'].get('compliant') is False and depth is not None:
                findings.append({
                    'component': 'worker',
                    'label': COMPONENT_CATALOG['worker']['label'],
                    'signal': 'backlog_high',
                    'severity': SEVERITY_HIGH,
                    'status': STATUS_DEGRADED,
                    'summary': f'Monitoring queue backlog is {depth} (bound {bound}).',
                    'reason': 'Durable monitoring queue depth exceeds its safe bound.',
                    'age_seconds': None,
                    'age_human': None,
                    'last_healthy_at': None,
                    'monitoring_path': True,
                    'critical': False,
                    'layer': COMPONENT_CATALOG['worker']['layer'],
                    'evidence': {'queue_depth': depth, 'bound': bound},
                    'fingerprint': 'worker:backlog_high',
                })

    findings.sort(key=lambda f: (-_SEVERITY_ORDER[f['severity']], f['layer']))
    return findings


# ---------------------------------------------------------------------------
# ROOT CAUSE CORRELATION
# ---------------------------------------------------------------------------

def derive_root_cause(findings: list[dict[str, Any]], normalized: list[dict[str, Any]]) -> dict[str, Any]:
    """Pick the deepest failing dependency layer as the probable failure domain.

    A monitoring-worker stall that also makes telemetry/detection stale must read
    as "monitoring worker execution failure", not "entire platform outage": the
    worker sits *below* telemetry/detection in the dependency graph, so it wins.
    A failing database (deeper still) wins over both.
    """
    if not findings:
        return {'domain': None, 'summary': 'No active reliability findings.', 'confidence': 'high', 'symptoms': []}

    # Consider only genuine failures/degradation as candidate causes (not unknowns
    # that are merely unobservable). Deepest layer (smallest) is the root.
    candidates = [f for f in findings if f['status'] in (STATUS_UNHEALTHY, STATUS_DEGRADED)]
    pool = candidates or findings
    root = min(pool, key=lambda f: (f['layer'], -_SEVERITY_ORDER[f['severity']]))
    downstream = [f for f in findings if f is not root and f['layer'] > root['layer']]

    label = root['label']
    if root['component'] == 'database':
        summary = 'Database dependency failure. Downstream persistence and processing degrade as a consequence.'
    elif root['component'] == 'redis':
        summary = 'Redis/queue dependency failure. Queue-backed processing is affected.'
    elif root['component'] == 'worker':
        summary = 'Monitoring worker execution failure. Telemetry and detection freshness degrade because the loop stopped, not because the whole platform is down.'
    elif root['component'] == 'api':
        summary = 'API front door is unreachable.'
    elif root['component'] == 'base_rpc':
        summary = 'Base RPC dependency failure. Chain data cannot be ingested.'
    else:
        summary = f'{label} is the deepest failing component; downstream stages degrade as a result.'

    return {
        'domain': root['component'],
        'domain_label': label,
        'severity': root['severity'],
        'summary': summary,
        'confidence': 'high' if len(candidates) <= 2 else 'medium',
        'symptoms': [f['component'] for f in downstream],
    }


# ---------------------------------------------------------------------------
# REMEDIATION POLICY
# ---------------------------------------------------------------------------
# Action classification table. Every action Screen 12 could recommend maps to
# exactly one policy class. Forbidden actions are enumerated so the LLM/agent can
# never smuggle one through.
POLICY_NONE = 'none'
POLICY_AUTO = 'auto'
POLICY_APPROVAL = 'approval_required'
POLICY_FORBIDDEN = 'forbidden'
POLICY_UNSUPPORTED = 'unsupported'

# Actions that MAY be automated IF a real mechanism exists (capability-gated).
_SAFE_AUTO_ACTIONS = {'restart_worker'}
# Actions that always require an authorized approval workflow (Screen 11).
_APPROVAL_ACTIONS = {'scale_memory', 'change_config', 'change_routing', 'rotate_secret'}
# Actions the agent must NEVER take autonomously (belt-and-braces enumeration).
FORBIDDEN_ACTIONS = {
    'delete_customer_data', 'drop_table', 'truncate_table', 'rotate_customer_secret',
    'disable_security_control', 'alter_audit_history', 'suppress_alert',
    'manufacture_telemetry', 'modify_incident_evidence', 'execute_arbitrary_command',
}

# component+signal -> the recommended action key.
_ACTION_FOR_SIGNAL: dict[str, str] = {
    'heartbeat_stale': 'restart_worker',
    'heartbeat_missing': 'restart_worker',
    'backlog_high': 'reprocess_backlog',
    'poll_stopped': 'restart_worker',
    'poll_stale': 'restart_worker',
}


def remediation_capability(component: str) -> dict[str, Any]:
    """Whether a REAL programmatic remediation mechanism exists for a component."""
    commands = configured_remediation_commands()
    supported = component in commands
    return {
        'component': component,
        'supported': supported,
        'mechanism': 'configured_command' if supported else None,
        'env_var': REMEDIATION_COMMAND_ENV.get(component),
    }


def classify_action(component: str, signal: str | None) -> dict[str, Any]:
    """Classify the recommended remediation for a finding into a policy class.

    Deterministic policy sits between diagnosis and any change action — a local
    AI model can never decide an infrastructure command directly.
    """
    action_key = _ACTION_FOR_SIGNAL.get(signal or '', None)

    # Memory pressure (if ever surfaced) is approval-required and unsupported
    # unless a real control plane exists — never a fake "optimization".
    if action_key == 'scale_memory':
        return {'action_key': action_key, 'recommended_action': 'Scale worker memory allocation',
                'policy': POLICY_APPROVAL, 'requires_approval': True,
                'note': 'Automatic resource scaling unavailable — requires operator/infrastructure approval.'}

    if action_key in _APPROVAL_ACTIONS:
        return {'action_key': action_key, 'recommended_action': action_key.replace('_', ' ').title(),
                'policy': POLICY_APPROVAL, 'requires_approval': True,
                'note': 'Governed configuration change — routes through the Screen 11 approval path.'}

    if action_key in _SAFE_AUTO_ACTIONS:
        cap = remediation_capability(component)
        if cap['supported']:
            return {'action_key': action_key, 'recommended_action': 'Restart monitoring worker',
                    'policy': POLICY_AUTO, 'requires_approval': False, 'mechanism': cap['mechanism']}
        # Safe in principle, but no real mechanism configured for this deployment.
        return {'action_key': action_key, 'recommended_action': 'Restart monitoring worker',
                'policy': POLICY_UNSUPPORTED, 'requires_approval': False,
                'note': 'Restart recommended — manual execution required (no supported restart mechanism configured).',
                'env_var': cap['env_var']}

    if action_key == 'reprocess_backlog':
        # Reprocessing is handled by the durable queue's own leased retry loop; the
        # agent recommends operator attention rather than claiming a control it lacks.
        return {'action_key': action_key, 'recommended_action': 'Investigate worker throughput / add worker capacity',
                'policy': POLICY_UNSUPPORTED, 'requires_approval': False,
                'note': 'Backlog drains via the durable queue; scaling worker capacity requires operator action.'}

    # No safe automatic action for this signal (e.g. db/redis/api/rpc failures):
    # recommend investigation; do not claim a mechanism.
    return {'action_key': None, 'recommended_action': 'Investigate and remediate at the infrastructure layer',
            'policy': POLICY_NONE, 'requires_approval': False,
            'note': 'No supported automatic remediation for this component — operator action required.'}


def evaluate_remediation_gate(
    *,
    component: str,
    policy: str,
    worker_status: str,
    recent_attempts: list[Mapping[str, Any]],
    config: RemediationConfig,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Decide whether an automatic/authorized execution may proceed RIGHT NOW.

    Denies when: the action is not auto-executable, the target component is
    intentionally disabled, a cooldown is active, the per-window attempt cap is
    exhausted, or repeated failures require escalation. This is the guard that
    prevents restart -> unhealthy -> restart -> ... loops.
    """
    now = now or _utc_now()

    if policy != POLICY_AUTO:
        return {'allowed': False, 'reason_code': 'not_auto_executable',
                'reason': f'Action policy is "{policy}", not an executable automatic action.'}

    # Never restart a component that is intentionally disabled — its absence of
    # heartbeat is expected, not a crash.
    if worker_status == STATUS_DISABLED:
        return {'allowed': False, 'reason_code': 'component_disabled',
                'reason': 'Component is intentionally disabled; a restart would fight configuration.'}

    window_start = now - timedelta(seconds=config.restart_window_seconds)
    attempts_in_window = [a for a in recent_attempts if (_parse_ts(a.get('detected_at')) or now) >= window_start]
    if len(attempts_in_window) >= config.max_restarts_per_window:
        return {'allowed': False, 'reason_code': 'max_attempts',
                'reason': f'Max {config.max_restarts_per_window} automatic restarts in '
                          f'{config.restart_window_seconds}s already used for {component}.'}

    last = max(recent_attempts, key=lambda a: _parse_ts(a.get('detected_at')) or datetime.min.replace(tzinfo=timezone.utc), default=None) if recent_attempts else None
    if last is not None:
        last_ts = _parse_ts(last.get('detected_at'))
        if last_ts and (now - last_ts).total_seconds() < config.restart_cooldown_seconds:
            remaining = int(config.restart_cooldown_seconds - (now - last_ts).total_seconds())
            return {'allowed': False, 'reason_code': 'cooldown',
                    'reason': f'Restart cooldown active ({remaining}s remaining).'}

    recent_failures = [a for a in attempts_in_window if a.get('action_result') == 'failed']
    if len(recent_failures) >= config.escalate_after_failures:
        return {'allowed': False, 'reason_code': 'escalation_required',
                'reason': f'{len(recent_failures)} recent remediation failures — escalation required.'}

    return {'allowed': True, 'reason_code': 'ok', 'reason': 'Within policy: cooldown clear and attempt budget available.'}


# ---------------------------------------------------------------------------
# VERIFY — execution success is NOT recovery success
# ---------------------------------------------------------------------------

def verify_recovery(finding_component: str, before_signal: str | None, after_snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Confirm the finding's component actually returned to health AFTER an action.

    An action is verified ONLY when the canonical evaluator now reports the target
    component healthy (and, for the worker, its downstream telemetry/detection are
    no longer unhealthy). Execution returning without an exception is never enough.
    """
    norm = normalize_component(finding_component, after_snapshot)
    component_ok = norm['status'] == STATUS_HEALTHY
    downstream_ok = True
    downstream_states: dict[str, str] = {}
    if finding_component == 'worker':
        for dep in ('telemetry', 'detection', 'live_polling'):
            dep_norm = normalize_component(dep, after_snapshot)
            downstream_states[dep] = dep_norm['status']
            if dep_norm['status'] == STATUS_UNHEALTHY:
                downstream_ok = False
    recovered = component_ok and downstream_ok
    return {
        'recovered': recovered,
        'component_status': norm['status'],
        'downstream': downstream_states,
        'evidence': {
            'component': finding_component,
            'component_status_after': norm['status'],
            'component_age_after': norm['age_human'],
            'downstream_after': downstream_states,
        },
    }


# ---------------------------------------------------------------------------
# DIAGNOSIS (deterministic prose from structured facts)
# ---------------------------------------------------------------------------

def summarize_diagnosis(
    *,
    overall_status: str,
    findings: list[dict[str, Any]],
    root_cause: dict[str, Any],
    normalized: list[dict[str, Any]],
    auto_healing_mode: str,
) -> dict[str, Any]:
    """Build the Self-Healing Reliability Agent's operational diagnosis.

    Deterministic: the canonical status/root evidence stays factual. (An optional
    AI layer may later rephrase this prose, but it never changes the facts and is
    not required for the page to function.)
    """
    disabled = [n for n in normalized if n['status'] == STATUS_DISABLED]
    unknown = [n for n in normalized if n['status'] == STATUS_UNKNOWN]

    if not findings:
        if unknown:
            state = 'partial_observability'
            headline = 'Operating, with unobservable components'
        else:
            state = 'nominal'
            headline = 'All systems operating normally'
        observations = ['No active reliability anomalies.']
        if any(n['component'] == 'worker' and n['status'] == STATUS_HEALTHY for n in normalized):
            observations.append('Worker heartbeats are within expected intervals.')
        if any(n['component'] == 'telemetry' and n['status'] == STATUS_HEALTHY for n in normalized):
            observations.append('Monitoring coverage is current.')
        for n in unknown:
            observations.append(f'{n["label"]}: state cannot currently be observed.')
        recommended = 'No remediation required.'
    else:
        top = findings[0]
        if top['severity'] == SEVERITY_CRITICAL:
            state, headline = 'action_required', 'Action required'
        else:
            state, headline = 'degraded', 'Degraded reliability'
        observations = [f['summary'] for f in findings[:4]]
        action = classify_action(top['component'], top['signal'])
        recommended = action['recommended_action']
        if action.get('note'):
            recommended = f'{recommended} — {action["note"]}'

    for n in disabled:
        note = f'{n["label"]}: disabled'
        if n.get('disabled_reason'):
            note += f' ({n["disabled_reason"]})'
        observations.append(note + '.')

    return {
        'state': state,
        'headline': headline,
        'overall_status': overall_status,
        'observations': observations,
        'root_cause': root_cause,
        'auto_healing_mode': auto_healing_mode,
    }


# ---------------------------------------------------------------------------
# METRICS — truthful uptime / response / error from persisted samples
# ---------------------------------------------------------------------------

def _trend(current: float | None, previous: float | None, *, lower_is_better: bool) -> str | None:
    if current is None or previous is None:
        return None  # never show an arrow without comparison data
    delta = current - previous
    if abs(delta) < 1e-9:
        return 'unchanged'
    improved = delta < 0 if lower_is_better else delta > 0
    return 'improving' if improved else 'worsening'


def compute_reliability_metrics(samples: list[Mapping[str, Any]], *, now: datetime | None = None, config: RemediationConfig | None = None) -> dict[str, Any]:
    """Aggregate Uptime / Avg Response / Error Rate from persisted infra-health
    samples. With too few samples the values are ``insufficient_history`` /
    ``no_data`` — never a fabricated number.
    """
    now = now or _utc_now()
    config = config or RemediationConfig()

    rows = []
    for s in samples:
        ts = _parse_ts(s.get('sampled_at'))
        if ts is None:
            continue
        rows.append({'ts': ts, 'critical_ok': bool(s.get('critical_ok')), 'error': bool(s.get('error')),
                     'response_ms': s.get('response_ms')})
    rows.sort(key=lambda r: r['ts'])

    window_start_30d = now - timedelta(days=30)
    win = [r for r in rows if r['ts'] >= window_start_30d]

    # Uptime over the available window (<= 30d).
    if len(win) < config.min_uptime_samples:
        uptime = {'value': None, 'state': 'insufficient_history', 'sample_count': len(win),
                  'window_label': None, 'source': 'reliability_health_samples'}
    else:
        earliest = win[0]['ts']
        span_days = max(0.0, (now - earliest).total_seconds() / 86400.0)
        window_label = f'{min(30, max(1, round(span_days)))}d' if span_days >= 1 else f'{max(1, round((now - earliest).total_seconds() / 3600))}h'
        pct = 100.0 * sum(1 for r in win if r['critical_ok']) / len(win)
        uptime = {'value': round(pct, 3), 'state': 'ok', 'sample_count': len(win),
                  'window_label': window_label, 'source': 'reliability_health_samples'}

    # Avg response time over last 24h (measured probe round-trip), else window.
    last24 = [r for r in rows if r['ts'] >= now - timedelta(hours=24)]
    prev24 = [r for r in rows if now - timedelta(hours=48) <= r['ts'] < now - timedelta(hours=24)]

    def _avg_resp(bucket: list[dict[str, Any]]) -> float | None:
        vals = [float(r['response_ms']) for r in bucket if isinstance(r['response_ms'], (int, float))]
        return round(sum(vals) / len(vals), 1) if vals else None

    resp_now = _avg_resp(last24) if last24 else _avg_resp(win)
    resp_prev = _avg_resp(prev24)
    response = {
        'value_ms': resp_now,
        'state': 'ok' if resp_now is not None else 'no_data',
        'window_label': '24h' if last24 else ('30d' if win else None),
        'source': 'health snapshot evaluation time (DB+Redis+RPC probes)',
        'trend': _trend(resp_now, resp_prev, lower_is_better=True),
    }

    # Error rate over last 24h.
    if last24:
        err_pct = 100.0 * sum(1 for r in last24 if r['error']) / len(last24)
        err_prev = (100.0 * sum(1 for r in prev24 if r['error']) / len(prev24)) if prev24 else None
        error_rate = {'value': round(err_pct, 3), 'state': 'ok', 'sample_count': len(last24),
                      'window_label': '24h', 'trend': _trend(err_pct, err_prev, lower_is_better=True),
                      'source': 'reliability_health_samples'}
    else:
        error_rate = {'value': None, 'state': 'no_data', 'sample_count': 0, 'window_label': '24h',
                      'trend': None, 'source': 'reliability_health_samples'}

    return {'uptime': uptime, 'response_time': response, 'error_rate': error_rate}


# ---------------------------------------------------------------------------
# Persistence helpers (ledger + samples). Thin glue over the canonical schema.
# ---------------------------------------------------------------------------

def _json(value: Any) -> str:
    import json
    return json.dumps(value, separators=(',', ':'), sort_keys=True, default=str)


def _find_active_finding(connection: Any, fingerprint: str) -> dict[str, Any] | None:
    row = connection.execute(
        '''SELECT * FROM reliability_events
             WHERE fingerprint = %s AND event_type = 'finding'
               AND status IN ('detected', 'remediation_attempted', 'verification_pending',
                              'verification_failed', 'escalation_required', 'manual_required')
             ORDER BY detected_at DESC LIMIT 1''',
        (fingerprint,),
    ).fetchone()
    return dict(row) if row else None


def _upsert_finding(connection: Any, finding: Mapping[str, Any], *, now: datetime) -> str:
    """Insert a new finding row, or refresh the existing active one in place."""
    action = classify_action(finding['component'], finding.get('signal'))
    existing = _find_active_finding(connection, finding['fingerprint'])
    if existing:
        connection.execute(
            '''UPDATE reliability_events
                 SET severity = %s, summary = %s, diagnosis = %s, recommended_action = %s,
                     action_policy = %s, evidence = %s::jsonb, updated_at = %s
               WHERE id = %s''',
            (finding['severity'], finding['summary'], finding.get('reason'), action['recommended_action'],
             action['policy'], _json(finding.get('evidence') or {}), now, existing['id']),
        )
        return str(existing['id'])
    event_id = str(uuid.uuid4())
    # Map the finding's policy to a lifecycle status: a recommended-but-unsupported
    # action is recorded as manual_required so the ledger never implies it will
    # self-heal automatically. Everything else starts as 'detected'.
    lifecycle = 'manual_required' if action['policy'] == POLICY_UNSUPPORTED else 'detected'
    connection.execute(
        '''INSERT INTO reliability_events
             (id, workspace_id, component, event_type, signal, severity, status, summary,
              diagnosis, recommended_action, action_policy, evidence, fingerprint,
              detected_at, last_healthy_at, created_at, updated_at)
           VALUES (%s, NULL, %s, 'finding', %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s)''',
        (event_id, finding['component'], finding.get('signal'), finding['severity'], lifecycle,
         finding['summary'], finding.get('reason'), action['recommended_action'], action['policy'],
         _json(finding.get('evidence') or {}), finding['fingerprint'], now,
         _parse_ts(finding.get('last_healthy_at')), now, now),
    )
    return event_id


def _recent_remediation_attempts(connection: Any, component: str, *, window_seconds: int, now: datetime) -> list[dict[str, Any]]:
    since = now - timedelta(seconds=window_seconds)
    rows = connection.execute(
        '''SELECT id, detected_at, action_result, status FROM reliability_events
             WHERE component = %s AND event_type = 'remediation' AND detected_at >= %s
             ORDER BY detected_at DESC''',
        (component, since),
    ).fetchall()
    return [dict(r) for r in (rows or [])]


def _record_sample(connection: Any, snapshot: Mapping[str, Any], *, response_ms: int | None, source: str, now: datetime) -> None:
    components = snapshot.get('components') or {}
    api = str((components.get('api') or {}).get('status') or 'unavailable')
    db = str((components.get('database') or {}).get('status') or 'unavailable')
    critical_ok = api == 'healthy' and db == 'healthy'
    error = api == 'failing' or db == 'failing'
    status_map = {k: str((v or {}).get('status')) for k, v in components.items()}
    connection.execute(
        '''INSERT INTO reliability_health_samples
             (id, workspace_id, sampled_at, overall_status, critical_ok, error, response_ms, components, source, created_at)
           VALUES (%s, NULL, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)''',
        (str(uuid.uuid4()), now, str(snapshot.get('overall_status') or 'unavailable'), critical_ok, error,
         response_ms, _json(status_map), source, now),
    )


def _load_samples(connection: Any, *, days: int = 30) -> list[dict[str, Any]]:
    since = _utc_now() - timedelta(days=days)
    rows = connection.execute(
        '''SELECT sampled_at, critical_ok, error, response_ms FROM reliability_health_samples
             WHERE sampled_at >= %s ORDER BY sampled_at ASC''',
        (since,),
    ).fetchall()
    return [dict(r) for r in (rows or [])]


def _open_finding_ids(connection: Any) -> dict[str, str]:
    """Read-only map of {fingerprint: event_id} for currently-open findings.

    Lets the overview attach a persisted id to a live finding WITHOUT writing —
    so a finding already recorded by a prior diagnostic can be remediated, while
    a never-observed finding correctly has no id until a diagnostic runs. A GET
    must never persist (Rule 7), so this only reads.
    """
    rows = connection.execute(
        '''SELECT DISTINCT ON (fingerprint) fingerprint, id FROM reliability_events
             WHERE event_type = 'finding' AND fingerprint IS NOT NULL
               AND status IN ('detected', 'remediation_attempted', 'verification_pending',
                              'verification_failed', 'escalation_required', 'manual_required')
             ORDER BY fingerprint, detected_at DESC''',
    ).fetchall()
    return {str(r['fingerprint']): str(r['id']) for r in (rows or []) if r.get('fingerprint')}


def _load_events(connection: Any, *, limit: int = 50) -> list[dict[str, Any]]:
    rows = connection.execute(
        '''SELECT id, parent_event_id, component, event_type, signal, severity, status, summary,
                  diagnosis, recommended_action, action_policy, action_attempted, action_result,
                  attempt_count, evidence, detected_at, resolved_at, verified_at, created_at
             FROM reliability_events ORDER BY detected_at DESC LIMIT %s''',
        (max(1, min(int(limit), 200)),),
    ).fetchall()
    return [dict(r) for r in (rows or [])]


def _autonomous_status(connection: Any, *, config: RemediationConfig, now: datetime) -> dict[str, Any]:
    since = now - timedelta(hours=24)
    events_24h = connection.execute(
        "SELECT COUNT(*) AS c FROM reliability_events WHERE event_type IN ('finding','remediation','verification') AND detected_at >= %s",
        (since,),
    ).fetchone()
    restarts = connection.execute(
        '''SELECT COUNT(*) AS c FROM reliability_events
             WHERE event_type = 'remediation' AND action_attempted = TRUE AND detected_at >= %s''',
        (since,),
    ).fetchone()
    last = connection.execute(
        '''SELECT component, event_type, status, summary, detected_at FROM reliability_events
             ORDER BY detected_at DESC LIMIT 1''',
    ).fetchone()
    active_policies = len(configured_remediation_commands()) if config.auto_healing_mode == 'enabled' else 0
    last_event = None
    if last:
        last = dict(last)
        last_event = {
            'component': last.get('component'),
            'summary': last.get('summary'),
            'status': last.get('status'),
            'at': _iso(last.get('detected_at')),
            'ago': human_ago(last.get('detected_at'), now=now),
        }
    return {
        'auto_healing': config.auto_healing_mode,
        'events_24h': int((dict(events_24h) if events_24h else {}).get('c') or 0),
        'restarts': int((dict(restarts) if restarts else {}).get('c') or 0),
        'policies': active_policies,
        'configured_mechanisms': sorted(configured_remediation_commands().keys()),
        'last_event': last_event,
    }


# ---------------------------------------------------------------------------
# Snapshot acquisition
# ---------------------------------------------------------------------------

def _snapshot(request: Any = None, *, allow_live_rpc_probe: bool = False) -> tuple[dict[str, Any], int]:
    """Build the canonical health snapshot and measure its evaluation time (ms).

    The measured elapsed time is a real, clearly-sourced response-time metric —
    it is the health evaluator's own DB+Redis round-trip, never a frontend render
    time.

    ``allow_live_rpc_probe`` defaults to ``False`` so read handlers (the Screen 12
    overview GET) resolve Base RPC health from observed state and never make a paid
    provider call. The explicit diagnostic action passes ``True`` to permit one live
    probe.
    """
    from services.api.app.system_health import build_system_health_snapshot
    start = time.monotonic()
    snapshot = build_system_health_snapshot(request, allow_live_rpc_probe=allow_live_rpc_probe)
    elapsed_ms = int(round((time.monotonic() - start) * 1000))
    return snapshot, elapsed_ms


def _slo_metrics_safe(connection: Any) -> dict[str, Any] | None:
    try:
        from services.api.app.monitoring_reliability import monitoring_slo_snapshot
        return monitoring_slo_snapshot(connection)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def _serialize_event(row: Mapping[str, Any], *, now: datetime) -> dict[str, Any]:
    return {
        'id': str(row.get('id')),
        'parent_event_id': str(row['parent_event_id']) if row.get('parent_event_id') else None,
        'component': row.get('component'),
        'component_label': COMPONENT_CATALOG.get(str(row.get('component')), {}).get('label', row.get('component')),
        'event_type': row.get('event_type'),
        'signal': row.get('signal'),
        'severity': row.get('severity'),
        'status': row.get('status'),
        'summary': row.get('summary'),
        'diagnosis': row.get('diagnosis'),
        'recommended_action': row.get('recommended_action'),
        'action_policy': row.get('action_policy'),
        'action_attempted': bool(row.get('action_attempted')),
        'action_result': row.get('action_result'),
        'attempt_count': int(row.get('attempt_count') or 0),
        'evidence': row.get('evidence') or {},
        'detected_at': _iso(row.get('detected_at')),
        'detected_ago': human_ago(row.get('detected_at'), now=now),
        'resolved_at': _iso(row.get('resolved_at')),
        'verified_at': _iso(row.get('verified_at')),
    }


def _components_view(normalized: list[dict[str, Any]], *, now: datetime) -> list[dict[str, Any]]:
    view = []
    for n in normalized:
        view.append({
            'component': n['component'],
            'label': n['label'],
            'status': n['status'],
            'reason': n['reason'],
            'age_human': n['age_human'],
            'last_healthy_at': n['last_healthy_at'],
            'last_healthy_ago': human_ago(n['last_healthy_at'], now=now) if n['last_healthy_at'] else None,
            'metric': n['metric'],
            'disabled_reason': n['disabled_reason'],
            'monitoring_path': n['monitoring_path'],
            'critical': n['critical'],
        })
    return view


# ---------------------------------------------------------------------------
# READ handlers (ops-accessible, read-only, NO side effects — like system-health)
# ---------------------------------------------------------------------------

def build_reliability_overview(request: Any = None) -> dict[str, Any]:
    """GET: the Screen 12 overview — summary cards, components, agent diagnosis,
    and autonomous status. Pure read: performs no remediation and writes nothing.
    """
    from services.api.app.pilot import pg_connection
    now = _utc_now()
    config = RemediationConfig()
    snapshot, elapsed_ms = _snapshot(request)
    normalized = normalize_all(snapshot)

    metrics: dict[str, Any] = {}
    autonomous: dict[str, Any] = {}
    slo = None
    open_ids: dict[str, str] = {}
    ledger_available = True
    try:
        with pg_connection() as connection:
            slo = _slo_metrics_safe(connection)
            try:
                metrics = compute_reliability_metrics(_load_samples(connection), now=now, config=config)
            except Exception:
                ledger_available = False
            try:
                autonomous = _autonomous_status(connection, config=config, now=now)
            except Exception:
                ledger_available = False
            try:
                open_ids = _open_finding_ids(connection)
            except Exception:
                open_ids = {}
    except Exception:
        ledger_available = False

    if not metrics:
        # Ledger unavailable — be truthful, do not invent metrics.
        metrics = {
            'uptime': {'value': None, 'state': 'unavailable', 'source': 'reliability_health_samples'},
            'response_time': {'value_ms': elapsed_ms, 'state': 'point_sample',
                              'window_label': 'now', 'source': 'health snapshot evaluation time', 'trend': None},
            'error_rate': {'value': None, 'state': 'unavailable', 'window_label': '24h', 'trend': None},
        }
    elif isinstance(metrics.get('response_time'), dict) and metrics['response_time'].get('value_ms') is None:
        # Ledger reachable but no persisted response samples yet (e.g. fresh deploy
        # before any diagnostic ran). Surface THIS request's real measured probe
        # time as a clearly-labelled live point sample instead of "No data" — it is
        # a genuine measurement, not a fabricated window average.
        metrics['response_time'] = {
            'value_ms': elapsed_ms, 'state': 'point_sample', 'window_label': 'now',
            'source': 'health snapshot evaluation time', 'trend': None,
        }
    if not autonomous:
        autonomous = {'auto_healing': config.auto_healing_mode, 'events_24h': None, 'restarts': None,
                      'policies': len(configured_remediation_commands()) if config.auto_healing_mode == 'enabled' else 0,
                      'configured_mechanisms': sorted(configured_remediation_commands().keys()), 'last_event': None,
                      'ledger_available': False}

    findings = correlate_findings(snapshot, slo_metrics=slo)
    # Attach each finding's remediation policy + (read-only) persisted id so the UI
    # can render a truthful control: an executable action, an approval path, or a
    # "manual required" note — never a decorative button.
    for f in findings:
        action = classify_action(f['component'], f.get('signal'))
        f['recommended_action'] = action['recommended_action']
        f['action_policy'] = action['policy']
        f['action_note'] = action.get('note')
        f['finding_id'] = open_ids.get(f['fingerprint'])
    root_cause = derive_root_cause(findings, normalized)
    diagnosis = summarize_diagnosis(overall_status=str(snapshot.get('overall_status')), findings=findings,
                                    root_cause=root_cause, normalized=normalized, auto_healing_mode=config.auto_healing_mode)

    return {
        'generated_at': now.isoformat().replace('+00:00', 'Z'),
        'overall_status': snapshot.get('overall_status'),
        'summary': snapshot.get('summary'),
        'environment': snapshot.get('environment'),
        'api_commit': snapshot.get('git_commit'),
        'version': snapshot.get('version'),
        'ledger_available': ledger_available,
        'metrics': metrics,
        'components': _components_view(normalized, now=now),
        'findings': findings,
        'diagnosis': diagnosis,
        'autonomous': autonomous,
    }


def list_reliability_events(request: Any = None, *, limit: int = 50) -> dict[str, Any]:
    """GET: durable reliability event ledger (findings + remediation history)."""
    from services.api.app.pilot import pg_connection
    now = _utc_now()
    try:
        with pg_connection() as connection:
            events = _load_events(connection, limit=limit)
        return {'events': [_serialize_event(e, now=now) for e in events], 'count': len(events), 'ledger_available': True}
    except Exception:
        return {'events': [], 'count': 0, 'ledger_available': False}


def build_health_report(request: Any = None) -> dict[str, Any]:
    """GET: the detailed Full Health Report. Read-only; extends the overview with
    dependency/queue/freshness evidence, recent failures, remediation history and
    build/runtime identifiers.
    """
    from services.api.app.pilot import pg_connection
    now = _utc_now()
    overview = build_reliability_overview(request)
    snapshot, _ = _snapshot(request)

    slo_public: dict[str, Any] = {}
    recent_events: list[dict[str, Any]] = []
    try:
        with pg_connection() as connection:
            slo = _slo_metrics_safe(connection)
            if slo:
                slo_public = {'compliant': slo.get('compliant'), 'failed': slo.get('failed'),
                              'metrics': slo.get('metrics'), 'objectives': slo.get('objectives')}
            recent_events = [_serialize_event(e, now=now) for e in _load_events(connection, limit=50)]
    except Exception:
        pass

    unresolved = [e for e in recent_events if e['event_type'] == 'finding'
                  and e['status'] in ('detected', 'remediation_attempted', 'verification_pending',
                                      'verification_failed', 'escalation_required', 'manual_required')]
    remediations = [e for e in recent_events if e['event_type'] in ('remediation', 'verification')]

    # Web build SHA is provided by the caller-side (frontend already has it); the
    # report exposes the API SHA truthfully (unknown if the snapshot could not read it).
    return {
        'generated_at': now.isoformat().replace('+00:00', 'Z'),
        'overall_status': overview['overall_status'],
        'summary': overview['summary'],
        'metrics': overview['metrics'],
        'components': overview['components'],
        'findings': overview['findings'],
        'diagnosis': overview['diagnosis'],
        'autonomous': overview['autonomous'],
        'live_chain_monitoring': snapshot.get('live_chain_monitoring'),
        'slo': slo_public,
        'unresolved_findings': unresolved,
        'remediation_history': remediations,
        'runtime': {
            'environment': snapshot.get('environment'),
            'api_commit': snapshot.get('git_commit'),
            'api_version': snapshot.get('version'),
        },
    }


# ---------------------------------------------------------------------------
# WRITE handlers (authenticated + RBAC + audited). POST only — no GET side effects.
# ---------------------------------------------------------------------------

def run_diagnostic(request: Any) -> dict[str, Any]:
    """POST /ops/reliability/diagnostics/run — a bounded, explicit diagnostic.

    Re-reads canonical health, persists a health sample + any structured findings,
    and audits the run. This is the explicit, RBAC-gated action where a live Base RPC
    connectivity probe is intended — unlike a page GET, which never touches the
    provider. Only Decoda's own configured RPC endpoint is probed (never arbitrary
    networks), and provider health is never fabricated.
    """
    from services.api.app import pilot
    pilot.require_live_mode()
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        user, workspace_context = pilot._require_workspace_permission(connection, request, 'monitoring.configure')
        workspace_id = workspace_context['workspace_id']
        now = pilot.utc_now()

        # Explicit operator-initiated diagnostic → a live RPC probe is permitted here.
        snapshot, elapsed_ms = _snapshot(request, allow_live_rpc_probe=True)
        normalized = normalize_all(snapshot)
        slo = _slo_metrics_safe(connection)
        findings = correlate_findings(snapshot, slo_metrics=slo)

        _record_sample(connection, snapshot, response_ms=elapsed_ms, source='diagnostic', now=now)
        finding_ids: list[str] = []
        for f in findings:
            finding_ids.append(_upsert_finding(connection, f, now=now))

        # Auto-resolve findings whose component is now healthy again.
        healthy_components = {n['component'] for n in normalized if n['status'] == STATUS_HEALTHY}
        if healthy_components:
            connection.execute(
                '''UPDATE reliability_events SET status = 'resolved', resolved_at = %s, updated_at = %s
                     WHERE event_type = 'finding' AND component = ANY(%s)
                       AND status IN ('detected','manual_required','verification_failed','escalation_required')''',
                (now, now, list(healthy_components)),
            )

        root_cause = derive_root_cause(findings, normalized)
        pilot.log_audit(
            connection, action='reliability.diagnostic_run', entity_type='platform', entity_id='system-health',
            request=request, user_id=user['id'], workspace_id=workspace_id,
            metadata={'overall_status': snapshot.get('overall_status'), 'findings': len(findings),
                      'response_ms': elapsed_ms, 'root_cause': root_cause.get('domain')},
        )
        connection.commit()
        return {
            'ran_at': now.isoformat().replace('+00:00', 'Z'),
            'overall_status': snapshot.get('overall_status'),
            'response_ms': elapsed_ms,
            'findings': findings,
            'finding_ids': finding_ids,
            'root_cause': root_cause,
            'diagnosis': summarize_diagnosis(overall_status=str(snapshot.get('overall_status')), findings=findings,
                                             root_cause=root_cause, normalized=normalized,
                                             auto_healing_mode=RemediationConfig().auto_healing_mode),
        }


def execute_remediation(finding_id: str, payload: dict[str, Any], request: Any) -> dict[str, Any]:
    """POST /ops/reliability/findings/{id}/remediate — policy-gated execution.

    Requires ``security.manage`` (owner/admin) — the frontend never authorizes a
    restart. Executes ONLY when: the action is policy=auto, a real mechanism is
    configured, the target is not intentionally disabled, and the cooldown / max-
    attempts / escalation guards pass. Execution success is recorded distinctly
    from recovery success; recovery is confirmed only by post-action verification.
    Unsupported actions are recorded as ``manual_required`` — never faked.
    """
    from services.api.app import pilot
    from fastapi import HTTPException, status as http_status
    pilot.require_live_mode()
    config = RemediationConfig()
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        user, workspace_context = pilot._require_workspace_permission(
            connection, request, 'security.manage', require_reauthentication=True)
        workspace_id = workspace_context['workspace_id']
        now = pilot.utc_now()

        finding = connection.execute(
            "SELECT * FROM reliability_events WHERE id = %s AND event_type = 'finding'", (finding_id,),
        ).fetchone()
        if not finding:
            raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail='Reliability finding not found.')
        finding = dict(finding)
        component = str(finding['component'])
        action = classify_action(component, finding.get('signal'))

        # Forbidden / approval / unsupported are never executed here.
        if action['policy'] in (POLICY_FORBIDDEN, POLICY_APPROVAL):
            raise HTTPException(status_code=http_status.HTTP_403_FORBIDDEN,
                                detail=f'Action requires the approval workflow (policy={action["policy"]}).')
        if action['policy'] in (POLICY_NONE, POLICY_UNSUPPORTED):
            connection.execute(
                "UPDATE reliability_events SET status='manual_required', updated_at=%s WHERE id=%s", (now, finding_id))
            pilot.log_audit(connection, action='reliability.remediation_manual_required', entity_type='reliability_event',
                            entity_id=finding_id, request=request, user_id=user['id'], workspace_id=workspace_id,
                            metadata={'component': component, 'policy': action['policy'], 'reason': action.get('note')})
            connection.commit()
            return {'status': 'manual_required', 'policy': action['policy'], 'component': component,
                    'recommended_action': action['recommended_action'], 'note': action.get('note'),
                    'executed': False, 'verified': False}

        # policy == auto: run the loop guards.
        worker_status = normalize_component(component, _snapshot(request)[0])['status']
        recent = _recent_remediation_attempts(connection, component, window_seconds=config.restart_window_seconds, now=now)
        gate = evaluate_remediation_gate(component=component, policy=action['policy'], worker_status=worker_status,
                                         recent_attempts=recent, config=config, now=now)
        if not gate['allowed']:
            escalate = gate['reason_code'] in ('max_attempts', 'escalation_required')
            new_status = 'escalation_required' if escalate else 'remediation_attempted'
            connection.execute("UPDATE reliability_events SET status=%s, updated_at=%s WHERE id=%s",
                               (new_status, now, finding_id))
            pilot.log_audit(connection, action='reliability.remediation_denied', entity_type='reliability_event',
                            entity_id=finding_id, request=request, user_id=user['id'], workspace_id=workspace_id,
                            metadata={'component': component, 'reason_code': gate['reason_code'], 'reason': gate['reason']})
            connection.commit()
            return {'status': 'denied', 'reason_code': gate['reason_code'], 'reason': gate['reason'],
                    'component': component, 'executed': False, 'verified': False}

        # --- EXECUTE (real mechanism) --------------------------------------
        remediation_id = str(uuid.uuid4())
        connection.execute(
            '''INSERT INTO reliability_events
                 (id, workspace_id, parent_event_id, component, event_type, signal, severity, status,
                  summary, action_policy, action_attempted, action_result, evidence, fingerprint,
                  detected_at, created_by_user_id, created_at, updated_at)
               VALUES (%s, NULL, %s, %s, 'remediation', %s, %s, 'remediation_attempted', %s, %s, TRUE, 'pending',
                       %s::jsonb, %s, %s, %s, %s, %s)''',
            (remediation_id, finding_id, component, finding.get('signal'), finding['severity'],
             f'Attempting: {action["recommended_action"]}', action['policy'],
             _json({'gate': gate, 'mechanism': action.get('mechanism')}), finding.get('fingerprint'),
             now, user['id'], now, now),
        )
        connection.execute("UPDATE reliability_events SET status='verification_pending', updated_at=%s WHERE id=%s",
                           (now, finding_id))
        # Record the pre-action attempt in the canonical audit trail BEFORE running.
        pilot.log_audit(connection, action='reliability.remediation_attempted', entity_type='reliability_event',
                        entity_id=finding_id, request=request, user_id=user['id'], workspace_id=workspace_id,
                        metadata={'component': component, 'action': action['action_key'],
                                  'remediation_id': remediation_id, 'policy': action['policy']})
        connection.commit()

        command = configured_remediation_commands().get(component)
        exec_ok, exec_detail = _run_command(command, timeout=config.command_timeout_seconds)

        # --- VERIFY (execution success is NOT recovery success) ------------
        after_snapshot, _ = _snapshot(request)
        verification = verify_recovery(component, finding.get('signal'), after_snapshot)
        action_result = 'succeeded' if exec_ok else 'failed'
        if exec_ok and verification['recovered']:
            finding_status = 'recovered'
        elif exec_ok and not verification['recovered']:
            finding_status = 'verification_failed'
        else:
            finding_status = 'escalation_required'

        verify_id = str(uuid.uuid4())
        connection.execute(
            '''INSERT INTO reliability_events
                 (id, workspace_id, parent_event_id, component, event_type, signal, severity, status,
                  summary, action_result, evidence, fingerprint, detected_at, verified_at, created_at, updated_at)
               VALUES (%s, NULL, %s, %s, 'verification', %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s)''',
            (verify_id, remediation_id, component, finding.get('signal'), finding['severity'],
             'recovered' if verification['recovered'] else 'verification_failed',
             ('Recovery verified.' if verification['recovered'] else 'Post-action verification did not confirm recovery.'),
             action_result, _json(verification['evidence']), finding.get('fingerprint'), now,
             now if verification['recovered'] else None, now, now),
        )
        connection.execute(
            '''UPDATE reliability_events SET action_result=%s, status=%s, verified_at=%s, updated_at=%s WHERE id=%s''',
            (action_result, 'recovered' if verification['recovered'] else 'verification_failed', now, now, remediation_id))
        connection.execute(
            '''UPDATE reliability_events SET status=%s, attempt_count = attempt_count + 1,
                     resolved_at=%s, verified_at=%s, updated_at=%s WHERE id=%s''',
            (finding_status, now if verification['recovered'] else None,
             now if verification['recovered'] else None, now, finding_id))
        _record_sample(connection, after_snapshot, response_ms=None, source='remediation_verify', now=now)

        pilot.log_audit(connection, action='reliability.remediation_result', entity_type='reliability_event',
                        entity_id=finding_id, request=request, user_id=user['id'], workspace_id=workspace_id,
                        metadata={'component': component, 'execution': action_result, 'exec_detail': exec_detail,
                                  'recovered': verification['recovered'], 'component_status_after': verification['component_status'],
                                  'remediation_id': remediation_id})
        connection.commit()

        return {
            'status': finding_status,
            'component': component,
            'executed': True,
            'execution_result': action_result,
            'execution_detail': exec_detail,
            'verified': verification['recovered'],
            'verification': verification,
            'recommended_action': action['recommended_action'],
        }


def _run_command(command: str | None, *, timeout: int) -> tuple[bool, str]:
    """Run a configured remediation command safely (bounded, no shell). Returns
    (ok, sanitized_detail). Never runs an LLM-produced string — only the operator-
    configured command for this component."""
    if not command:
        return False, 'no command configured'
    try:
        completed = subprocess.run(shlex.split(command), capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return False, f'command timed out after {timeout}s'
    except Exception as exc:  # pragma: no cover - defensive
        return False, f'{type(exc).__name__}'
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or f'exit {completed.returncode}').strip()
        return False, detail[-300:]
    return True, (completed.stdout or 'ok').strip()[-300:]
