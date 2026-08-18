"""Governance Guard — Screen 11 autonomous operational layer.

Two responsibilities:

* Job A — immutable configuration accountability. Every sensitive workspace
  configuration change produces a permanent record in the canonical, hash-chained,
  append-only ``audit_logs`` via :func:`pilot.log_audit`. HIGH/CRITICAL changes are
  gated behind a separation-of-duties approval workflow
  (``governance_change_requests``) and the canonical target state is never mutated
  while a request is pending.

* Job B — access-anomaly detection. Deterministic, auditable rules run over the
  workspace's OWN ``audit_logs`` evidence (no LLM, no external providers). Findings
  persist in ``governance_anomalies`` and are only produced by an EXPLICIT
  evaluation path — never as a side effect of a GET.

This module reuses canonical infrastructure only. It creates no parallel audit
system and no parallel MFA/session state: the MFA/reauthentication policy remains
``workspace_auth_policies`` (read/written through pilot helpers), RBAC remains
``workspace_role_permissions`` (``pilot._require_workspace_permission``), and
tenant isolation remains ``pilot.resolve_workspace``.

Truthfulness rules honoured throughout:
    * missing evidence is never rendered as safe / low / zero;
    * "evaluated, 0 findings" is distinct from "never evaluated";
    * an evaluation/DB failure surfaces as an error, never a green zero.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from fastapi import HTTPException, Request, status

from services.api.app import pilot


# ---------------------------------------------------------------------------
# Canonical constants
# ---------------------------------------------------------------------------

RISK_LOW = 'low'
RISK_MEDIUM = 'medium'
RISK_HIGH = 'high'
RISK_CRITICAL = 'critical'
RISK_LEVELS = (RISK_LOW, RISK_MEDIUM, RISK_HIGH, RISK_CRITICAL)
_RISK_ORDER = {RISK_LOW: 0, RISK_MEDIUM: 1, RISK_HIGH: 2, RISK_CRITICAL: 3}

# MFA enforcement strength ordering (higher = stronger). Decreasing this is a
# weakening of a critical security control.
_MFA_STRENGTH = {'optional': 0, 'administrators': 1, 'all_members': 2}

# Permission used to READ and to APPROVE/ACT on governance state. Reuses the
# canonical ``security.manage`` permission (owner/admin by default) rather than
# inventing a new separation-of-duties permission — the existing RBAC model is
# sufficient. General (low-risk) workspace settings writes reuse ``members.manage``.
GOVERNANCE_MANAGE_PERMISSION = 'security.manage'
SETTINGS_WRITE_PERMISSION = 'members.manage'

# Session step-up / reauthentication window options, expressed in minutes. These
# are the backend-valid subset (workspace_auth_policies.reauthentication_minutes is
# constrained to 1..120), surfaced so the UI renders options from canonical truth
# rather than hard-coding the screenshot's 4h/8h values the backend cannot store.
SESSION_TIMEOUT_OPTIONS = [
    {'value': 15, 'label': '15 minutes'},
    {'value': 30, 'label': '30 minutes'},
    {'value': 60, 'label': '1 hour'},
    {'value': 120, 'label': '2 hours'},
]

ALLOWED_TIMEZONES = [
    'UTC',
    'America/New_York',
    'America/Los_Angeles',
    'America/Chicago',
    'Europe/London',
    'Europe/Berlin',
    'Asia/Singapore',
    'Asia/Tokyo',
    'Australia/Sydney',
]
ALLOWED_CURRENCIES = ['USD', 'EUR', 'GBP', 'SGD', 'JPY', 'AUD', 'CHF']

# Audit actions that count as security-sensitive for anomaly correlation and for
# the change-log risk classification. Single source of truth — never duplicated on
# the frontend.
_SECURITY_SENSITIVE_ACTIONS = frozenset({
    'workspace.auth_policy_updated',
    'governance.change_executed',
    'governance.security_policy_updated',
    'workspace.api_key.create',
    'workspace.api_key.rotate',
    'workspace.api_key.revoke',
    'workspace.oidc_configured',
    'workspace.oidc_deleted',
    'workspace.scim_token_created',
    'workspace.scim_token_revoked',
    'auth.mfa_disabled',
    'member.role_update',
    'member.remove',
})

# Deterministic risk classification for audit actions in the change log (§8).
_ACTION_RISK: dict[str, str] = {
    # CRITICAL
    'governance.security_policy_updated': RISK_CRITICAL,
    'auth.mfa_disabled': RISK_CRITICAL,
    'workspace.oidc_deleted': RISK_HIGH,
    # HIGH
    'workspace.auth_policy_updated': RISK_HIGH,
    'member.role_update': RISK_HIGH,
    'member.remove': RISK_HIGH,
    'workspace.api_key.create': RISK_HIGH,
    'workspace.api_key.rotate': RISK_HIGH,
    'workspace.api_key.revoke': RISK_HIGH,
    'workspace.oidc_configured': RISK_HIGH,
    'workspace.scim_token_created': RISK_HIGH,
    'workspace.scim_token_revoked': RISK_HIGH,
    'credential.rotate': RISK_HIGH,
    'credential.revoke': RISK_HIGH,
    # MEDIUM
    'invitation.create': RISK_MEDIUM,
    'invitation.revoke': RISK_MEDIUM,
    'invitation.resend': RISK_MEDIUM,
    'invitation.accept': RISK_MEDIUM,
    'integration.slack.create': RISK_MEDIUM,
    'integration.slack.delete': RISK_MEDIUM,
    'integration.slack.update': RISK_MEDIUM,
    'webhook.create': RISK_MEDIUM,
    'webhook.rotate_secret': RISK_MEDIUM,
    'retention.policy.update': RISK_MEDIUM,
    # LOW
    'workspace.settings_updated': RISK_LOW,
    'auth.signin': RISK_LOW,
    'auth.signout': RISK_LOW,
    'auth.mfa_enabled': RISK_LOW,
}


def classify_audit_action(action: str, metadata: dict[str, Any] | None = None) -> str:
    """Deterministically map an audit action to a change-log risk level.

    ``governance.change_executed`` inherits the risk of the underlying change,
    which the governance flow records in metadata.risk_level.
    """
    meta = metadata if isinstance(metadata, dict) else {}
    if action == 'governance.change_executed':
        level = str(meta.get('risk_level') or '').strip().lower()
        return level if level in _RISK_ORDER else RISK_HIGH
    if action == 'governance.change_requested':
        level = str(meta.get('risk_level') or '').strip().lower()
        return level if level in _RISK_ORDER else RISK_HIGH
    return _ACTION_RISK.get(action, RISK_LOW)


# ---------------------------------------------------------------------------
# Risk classification for security-policy changes
# ---------------------------------------------------------------------------

def classify_security_policy_change(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Classify a proposed change to the workspace MFA/session policy.

    Deterministic. Weakening MFA enforcement is CRITICAL; lengthening the
    reauthentication window (a weaker session posture) is HIGH; strengthening or
    no-op changes are LOW. Returns risk_level, risk_reason, requires_approval.
    """
    before_mfa = str(before.get('mfa_enforcement') or 'optional')
    after_mfa = str(after.get('mfa_enforcement') or 'optional')
    before_minutes = int(before.get('reauthentication_minutes') or pilot.REAUTHENTICATION_DEFAULT_MINUTES)
    after_minutes = int(after.get('reauthentication_minutes') or pilot.REAUTHENTICATION_DEFAULT_MINUTES)

    reasons: list[str] = []
    level = RISK_LOW

    before_strength = _MFA_STRENGTH.get(before_mfa, 0)
    after_strength = _MFA_STRENGTH.get(after_mfa, 0)
    if after_strength < before_strength:
        level = RISK_CRITICAL
        reasons.append(
            f'MFA enforcement weakened from "{before_mfa}" to "{after_mfa}" — privileged accounts '
            'would no longer be required to use MFA.'
        )
    elif after_strength > before_strength:
        reasons.append(f'MFA enforcement strengthened from "{before_mfa}" to "{after_mfa}".')

    if after_minutes > before_minutes:
        # Longer reauthentication window = weaker step-up posture.
        level = _max_risk(level, RISK_HIGH)
        reasons.append(
            f'Reauthentication window lengthened from {before_minutes}m to {after_minutes}m, '
            'weakening step-up assurance for sensitive actions.'
        )
    elif after_minutes < before_minutes:
        reasons.append(f'Reauthentication window shortened from {before_minutes}m to {after_minutes}m.')

    if not reasons:
        reasons.append('No effective change to the security policy.')

    requires_approval = level in (RISK_HIGH, RISK_CRITICAL)
    return {
        'risk_level': level,
        'risk_reason': ' '.join(reasons),
        'requires_approval': requires_approval,
    }


def _max_risk(a: str, b: str) -> str:
    return a if _RISK_ORDER.get(a, 0) >= _RISK_ORDER.get(b, 0) else b


# ---------------------------------------------------------------------------
# Low-level canonical reads
# ---------------------------------------------------------------------------

def _settings_row(connection: Any, workspace_id: str) -> dict[str, Any] | None:
    return connection.execute(
        'SELECT workspace_id, timezone, currency, version, governance_last_evaluated_at, updated_at '
        'FROM workspace_settings WHERE workspace_id = %s',
        (workspace_id,),
    ).fetchone()


def _governance_last_evaluated_at(connection: Any, workspace_id: str) -> datetime | None:
    row = _settings_row(connection, workspace_id)
    if not row:
        return None
    return row.get('governance_last_evaluated_at')


def _open_anomaly_counts(connection: Any, workspace_id: str) -> dict[str, int]:
    rows = connection.execute(
        "SELECT severity, COUNT(*) AS count FROM governance_anomalies "
        "WHERE workspace_id = %s AND status IN ('open', 'acknowledged') GROUP BY severity",
        (workspace_id,),
    ).fetchall()
    counts = {level: 0 for level in RISK_LEVELS}
    for row in rows or []:
        sev = str(row.get('severity') or '').lower()
        if sev in counts:
            counts[sev] = int(row.get('count') or 0)
    return counts


def _dormant_privileged_count(connection: Any, workspace_id: str, *, now: datetime, dormant_days: int = 90) -> int:
    """Privileged members (owner/admin) who have not signed in within dormant_days.

    Evidence is ``users.last_sign_in_at`` (actual account access data), so this is
    always evaluable without the anomaly engine.
    """
    cutoff = now - timedelta(days=dormant_days)
    row = connection.execute(
        """
        SELECT COUNT(*) AS count
        FROM workspace_members wm
        JOIN users u ON u.id = wm.user_id
        WHERE wm.workspace_id = %s
          AND wm.role IN ('workspace_owner', 'workspace_admin', 'owner', 'admin')
          AND (
                (u.last_sign_in_at IS NOT NULL AND u.last_sign_in_at < %s)
                OR (u.last_sign_in_at IS NULL AND u.created_at < %s)
          )
        """,
        (workspace_id, cutoff, cutoff),
    ).fetchone()
    return int((row or {}).get('count') or 0)


# ---------------------------------------------------------------------------
# Policy Impact — deterministic posture scoring
# ---------------------------------------------------------------------------

def compute_policy_posture(connection: Any, workspace_id: str, *, now: datetime | None = None) -> dict[str, Any]:
    """Deterministic security-posture score for the AI Policy Impact card.

    No hard-coded percentages. Every control names its evidence source. Missing
    access evidence (anomaly engine never run) keeps those points UNEARNED (it
    never silently improves the score) and yields access_risk = 'unknown'.
    """
    now = now or pilot.utc_now()
    policy = pilot._workspace_auth_policy(connection, workspace_id)
    mfa = str(policy.get('mfa_enforcement') or 'optional')
    minutes = int(policy.get('reauthentication_minutes') or pilot.REAUTHENTICATION_DEFAULT_MINUTES)

    last_evaluated = _governance_last_evaluated_at(connection, workspace_id)
    evaluated = last_evaluated is not None
    anomaly_counts = _open_anomaly_counts(connection, workspace_id) if evaluated else {}
    dormant = _dormant_privileged_count(connection, workspace_id, now=now)

    controls: list[dict[str, Any]] = []

    # 1. MFA enforcement (configuration)
    if mfa == 'all_members':
        controls.append(_control('mfa_enforcement', 'MFA required for all members', 'pass',
                                 'configuration', 25, 25, 'MFA is enforced for every member.'))
    elif mfa == 'administrators':
        controls.append(_control('mfa_enforcement', 'MFA required for privileged users', 'attention',
                                 'configuration', 18, 25, 'MFA is enforced for administrators only.'))
    else:
        controls.append(_control('mfa_enforcement', 'MFA required for privileged users', 'fail',
                                 'configuration', 0, 25, 'MFA enforcement is optional.'))

    # 2. Session reauthentication window (configuration)
    if minutes <= 30:
        controls.append(_control('session_reauth_window', 'Session step-up window <= 30m', 'pass',
                                 'configuration', 10, 10, f'Reauthentication window is {minutes}m.'))
    elif minutes <= 60:
        controls.append(_control('session_reauth_window', 'Session step-up window <= 30m', 'attention',
                                 'configuration', 6, 10, f'Reauthentication window is {minutes}m.'))
    else:
        controls.append(_control('session_reauth_window', 'Session step-up window <= 30m', 'fail',
                                 'configuration', 0, 10, f'Reauthentication window is {minutes}m.'))

    # 3. Audit logging (configuration / infrastructure) — always on, append-only.
    controls.append(_control('audit_logging', 'Audit logging (append-only, hash-chained)', 'pass',
                             'configuration', 15, 15, 'All sensitive changes are recorded in an append-only, hash-chained audit trail.'))

    # 4. Critical-change approval (configuration) — deterministic safeguard.
    controls.append(_control('critical_change_approval', 'Critical changes require approval', 'pass',
                             'configuration', 15, 15, 'HIGH/CRITICAL security changes require a separate-duty approval.'))

    # 5. No unresolved critical access anomalies (access evidence).
    if not evaluated:
        controls.append(_control('no_critical_access_anomalies', 'Privileged access anomalies', 'unknown',
                                 'unavailable', 0, 10, 'Access anomaly evaluation has not been run for this workspace.'))
    else:
        open_critical = anomaly_counts.get('critical', 0)
        open_high = anomaly_counts.get('high', 0)
        if open_critical == 0 and open_high == 0:
            controls.append(_control('no_critical_access_anomalies', 'Privileged access anomalies', 'pass',
                                     'access_evidence', 10, 10, 'No critical or high access anomalies are open.'))
        else:
            controls.append(_control('no_critical_access_anomalies', 'Privileged access anomalies', 'fail',
                                     'access_evidence', 0, 10,
                                     f'{open_critical} critical and {open_high} high access anomalies are open.'))

    # 6. No dormant privileged accounts (access evidence — last_sign_in_at).
    if dormant == 0:
        controls.append(_control('no_dormant_privileged_accounts', 'No dormant privileged accounts', 'pass',
                                 'access_evidence', 10, 10, 'All privileged accounts have signed in recently.'))
    else:
        controls.append(_control('no_dormant_privileged_accounts', 'No dormant privileged accounts', 'attention',
                                 'access_evidence', 0, 10, f'{dormant} privileged account(s) appear dormant (90d+).'))

    # 7. IP allowlist (configuration) — not implemented; truthful unsupported.
    controls.append(_control('ip_allowlist', 'IP allowlist enforcement', 'unsupported',
                             'unavailable', 0, 0, 'IP allowlist enforcement is not configured in this deployment.'))

    earned = sum(c['points_earned'] for c in controls)
    possible = sum(c['points_possible'] for c in controls)
    risk_reduction_percent = round((earned / possible) * 100) if possible > 0 else 0

    # evidence_status: insufficient until an evaluation has established access
    # evidence; complete only when every applicable control has real evidence.
    if not evaluated:
        evidence_status = 'insufficient'
    elif any(c['status'] == 'unknown' for c in controls):
        evidence_status = 'partial'
    else:
        evidence_status = 'complete'

    # access_risk: derived from access evidence, never from missing evidence.
    if not evaluated:
        access_risk = 'unknown'
    elif anomaly_counts.get('critical', 0) > 0:
        access_risk = 'critical'
    elif anomaly_counts.get('high', 0) > 0:
        access_risk = 'high'
    elif anomaly_counts.get('medium', 0) > 0:
        access_risk = 'medium'
    else:
        access_risk = 'low'

    recommendations = _posture_recommendations(controls, mfa=mfa, minutes=minutes, evaluated=evaluated, dormant=dormant)

    return {
        'risk_reduction_percent': risk_reduction_percent,
        'access_risk': access_risk,
        'evidence_status': evidence_status,
        'controls': controls,
        'controls_passing': sum(1 for c in controls if c['status'] == 'pass'),
        'controls_total': sum(1 for c in controls if c['status'] != 'unsupported'),
        'recommendations': recommendations,
        'calculated_at': now.isoformat(),
        'last_evaluated_at': last_evaluated.isoformat() if last_evaluated else None,
    }


def _control(key: str, label: str, status_value: str, evidence_source: str,
             earned: int, possible: int, detail: str) -> dict[str, Any]:
    return {
        'key': key,
        'label': label,
        'status': status_value,
        'evidence_source': evidence_source,
        'points_earned': earned,
        'points_possible': possible,
        'detail': detail,
    }


def _posture_recommendations(controls: list[dict[str, Any]], *, mfa: str, minutes: int,
                             evaluated: bool, dormant: int) -> list[dict[str, Any]]:
    """Deterministically derive recommendations from posture (no persistence)."""
    recs: list[dict[str, Any]] = []
    if _MFA_STRENGTH.get(mfa, 0) < _MFA_STRENGTH['all_members']:
        recs.append({
            'key': 'require_mfa_all',
            'severity': RISK_HIGH if mfa == 'optional' else RISK_MEDIUM,
            'reason': 'Not all members are required to use MFA.',
            'recommended_action': 'Require MFA for all privileged users (or all members).',
        })
    if minutes > 30:
        recs.append({
            'key': 'shorten_reauth_window',
            'severity': RISK_MEDIUM,
            'reason': f'Reauthentication window is {minutes}m.',
            'recommended_action': 'Reduce the privileged session step-up window to 30 minutes or less.',
        })
    if not evaluated:
        recs.append({
            'key': 'run_access_evaluation',
            'severity': RISK_MEDIUM,
            'reason': 'Access anomaly evaluation has not been run.',
            'recommended_action': 'Run Governance Guard access evaluation to establish access-risk evidence.',
        })
    if dormant > 0:
        recs.append({
            'key': 'review_dormant_admins',
            'severity': RISK_MEDIUM,
            'reason': f'{dormant} privileged account(s) appear dormant.',
            'recommended_action': 'Review or offboard dormant privileged administrators.',
        })
    return recs


# ---------------------------------------------------------------------------
# Access-anomaly engine (deterministic, evidence-backed)
# ---------------------------------------------------------------------------

# Only rules with confirmed, trustworthy evidence in this codebase are active.
# NEW_SOURCE_IP is intentionally NOT implemented: the app records the immediate
# peer address (no trusted X-Forwarded-For handling), so login source IPs reflect
# the proxy/load-balancer, not the real client — untrustworthy evidence.
# FAILED_LOGIN_BURST and ALLOWLIST_WEAKENED have no durable evidence and are not
# implemented. UNUSUAL_LOGIN_HOUR is deferred: login timestamps exist but a
# trustworthy per-actor baseline is not yet guaranteed, and flagging on a thin
# baseline would fabricate findings.
SUPPORTED_ANOMALY_TYPES = (
    'PRIVILEGE_ESCALATION',
    'ROLE_CHANGE_BURST',
    'MFA_WEAKENED',
    'RAPID_SECURITY_CHANGES',
    'NEW_ADMIN_HIGH_RISK_ACTION',
)

_ANOMALY_LOOKBACK_DAYS = 30
_BURST_WINDOW = timedelta(minutes=60)
_NEW_ADMIN_WINDOW = timedelta(hours=24)


def _load_audit_evidence(connection: Any, workspace_id: str, *, now: datetime) -> list[dict[str, Any]]:
    cutoff = now - timedelta(days=_ANOMALY_LOOKBACK_DAYS)
    rows = connection.execute(
        '''
        SELECT id, action, entity_type, entity_id, user_id, metadata, created_at
        FROM audit_logs
        WHERE workspace_id = %s AND created_at >= %s
        ORDER BY created_at ASC, id ASC
        ''',
        (workspace_id, cutoff),
    ).fetchall()
    events: list[dict[str, Any]] = []
    for row in rows or []:
        meta = row.get('metadata')
        if not isinstance(meta, dict):
            meta = {}
        events.append({
            'id': str(row.get('id')),
            'action': str(row.get('action') or ''),
            'entity_id': str(row.get('entity_id') or ''),
            'user_id': str(row.get('user_id')) if row.get('user_id') else None,
            'metadata': meta,
            'created_at': row.get('created_at'),
        })
    return events


def detect_anomalies(events: list[dict[str, Any]], *, now: datetime) -> list[dict[str, Any]]:
    """Pure, deterministic detection over pre-loaded audit evidence.

    Returns a list of finding dicts (not yet persisted). Kept side-effect-free and
    input-driven so it is exhaustively unit-testable without a database.
    """
    findings: list[dict[str, Any]] = []
    findings.extend(_detect_privilege_escalation(events))
    findings.extend(_detect_role_change_burst(events))
    findings.extend(_detect_mfa_weakened(events))
    findings.extend(_detect_rapid_security_changes(events))
    findings.extend(_detect_new_admin_high_risk(events, now=now))
    return findings


def _finding(*, anomaly_type: str, severity: str, confidence: float, user_id: str | None,
             dedup_key: str, evidence: dict[str, Any], first_seen: datetime | None,
             last_seen: datetime | None) -> dict[str, Any]:
    return {
        'type': anomaly_type,
        'severity': severity,
        'confidence': round(float(confidence), 2),
        'user_id': user_id,
        'dedup_key': dedup_key,
        'evidence': evidence,
        'first_seen_at': first_seen,
        'last_seen_at': last_seen,
    }


def _detect_privilege_escalation(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for ev in events:
        if ev['action'] != 'member.role_update':
            continue
        meta = ev['metadata']
        prev = str(meta.get('previous_role') or '').lower()
        new = str(meta.get('new_role') or meta.get('role') or '').lower()
        target_user = meta.get('target_user_id')
        # Requires the enhanced before/after evidence; skip legacy rows that only
        # recorded the new role (cannot prove an escalation without the prior role).
        if not prev or new not in _RISK_ORDER_ROLE or prev not in _RISK_ORDER_ROLE:
            continue
        if _RISK_ORDER_ROLE[new] <= _RISK_ORDER_ROLE[prev]:
            continue
        severity = RISK_CRITICAL if new == 'owner' else RISK_HIGH
        out.append(_finding(
            anomaly_type='PRIVILEGE_ESCALATION',
            severity=severity,
            confidence=95.0,
            user_id=str(target_user) if target_user else None,
            dedup_key=f'PRIVILEGE_ESCALATION:{ev["id"]}',
            evidence={
                'audit_event_id': ev['id'],
                'previous_role': prev,
                'new_role': new,
                'actor_user_id': ev['user_id'],
                'target_user_id': str(target_user) if target_user else None,
                'occurred_at': _iso(ev['created_at']),
            },
            first_seen=ev['created_at'],
            last_seen=ev['created_at'],
        ))
    return out


_RISK_ORDER_ROLE = {'viewer': 0, 'analyst': 1, 'admin': 2, 'owner': 3}


def _detect_role_change_burst(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_actor: dict[str, list[dict[str, Any]]] = {}
    for ev in events:
        if ev['action'] != 'member.role_update' or not ev['user_id']:
            continue
        by_actor.setdefault(ev['user_id'], []).append(ev)
    out: list[dict[str, Any]] = []
    for actor, evs in by_actor.items():
        peak, window_events = _max_window_count(evs, _BURST_WINDOW)
        if peak >= 3:
            severity = RISK_HIGH if peak >= 5 else RISK_MEDIUM
            out.append(_finding(
                anomaly_type='ROLE_CHANGE_BURST',
                severity=severity,
                confidence=80.0,
                user_id=actor,
                dedup_key=f'ROLE_CHANGE_BURST:{actor}',
                evidence={
                    'actor_user_id': actor,
                    'changes_in_window': peak,
                    'window_minutes': int(_BURST_WINDOW.total_seconds() // 60),
                    'audit_event_ids': [e['id'] for e in window_events],
                },
                first_seen=window_events[0]['created_at'] if window_events else None,
                last_seen=window_events[-1]['created_at'] if window_events else None,
            ))
    return out


def _detect_mfa_weakened(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    # (a) explicit account MFA disable
    for ev in events:
        if ev['action'] == 'auth.mfa_disabled':
            out.append(_finding(
                anomaly_type='MFA_WEAKENED',
                severity=RISK_HIGH,
                confidence=90.0,
                user_id=ev['user_id'],
                dedup_key=f'MFA_WEAKENED:disable:{ev["id"]}',
                evidence={
                    'signal': 'account_mfa_disabled',
                    'audit_event_id': ev['id'],
                    'user_id': ev['user_id'],
                    'occurred_at': _iso(ev['created_at']),
                },
                first_seen=ev['created_at'],
                last_seen=ev['created_at'],
            ))
    # (b) workspace MFA enforcement decreased across policy updates
    prev_strength: int | None = None
    for ev in events:
        if ev['action'] not in ('workspace.auth_policy_updated', 'governance.security_policy_updated'):
            continue
        mfa = str(ev['metadata'].get('mfa_enforcement') or ev['metadata'].get('after_mfa_enforcement') or '').lower()
        if mfa not in _MFA_STRENGTH:
            continue
        strength = _MFA_STRENGTH[mfa]
        if prev_strength is not None and strength < prev_strength:
            out.append(_finding(
                anomaly_type='MFA_WEAKENED',
                severity=RISK_CRITICAL,
                confidence=92.0,
                user_id=ev['user_id'],
                dedup_key=f'MFA_WEAKENED:policy:{ev["id"]}',
                evidence={
                    'signal': 'workspace_mfa_enforcement_weakened',
                    'audit_event_id': ev['id'],
                    'new_enforcement': mfa,
                    'actor_user_id': ev['user_id'],
                    'occurred_at': _iso(ev['created_at']),
                },
                first_seen=ev['created_at'],
                last_seen=ev['created_at'],
            ))
        prev_strength = strength
    return out


def _detect_rapid_security_changes(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_actor: dict[str, list[dict[str, Any]]] = {}
    for ev in events:
        if ev['action'] not in _SECURITY_SENSITIVE_ACTIONS or not ev['user_id']:
            continue
        by_actor.setdefault(ev['user_id'], []).append(ev)
    out: list[dict[str, Any]] = []
    for actor, evs in by_actor.items():
        peak, window_events = _max_window_count(evs, _BURST_WINDOW)
        if peak >= 5:
            severity = RISK_HIGH if peak >= 8 else RISK_MEDIUM
            out.append(_finding(
                anomaly_type='RAPID_SECURITY_CHANGES',
                severity=severity,
                confidence=75.0,
                user_id=actor,
                dedup_key=f'RAPID_SECURITY_CHANGES:{actor}',
                evidence={
                    'actor_user_id': actor,
                    'security_changes_in_window': peak,
                    'window_minutes': int(_BURST_WINDOW.total_seconds() // 60),
                    'actions': sorted({e['action'] for e in window_events}),
                    'audit_event_ids': [e['id'] for e in window_events],
                },
                first_seen=window_events[0]['created_at'] if window_events else None,
                last_seen=window_events[-1]['created_at'] if window_events else None,
            ))
    return out


def _detect_new_admin_high_risk(events: list[dict[str, Any]], *, now: datetime) -> list[dict[str, Any]]:
    # Promotions to admin/owner keyed by the promoted (target) user.
    promotions: dict[str, dict[str, Any]] = {}
    for ev in events:
        if ev['action'] != 'member.role_update':
            continue
        meta = ev['metadata']
        new = str(meta.get('new_role') or meta.get('role') or '').lower()
        target = meta.get('target_user_id')
        if new in ('admin', 'owner') and target:
            promotions[str(target)] = ev  # keep latest promotion per target
    out: list[dict[str, Any]] = []
    for target_user, promo in promotions.items():
        promo_at = promo['created_at']
        if promo_at is None:
            continue
        followups = [
            ev for ev in events
            if ev['user_id'] == target_user
            and ev['action'] in _SECURITY_SENSITIVE_ACTIONS
            and ev['created_at'] is not None
            and promo_at < ev['created_at'] <= promo_at + _NEW_ADMIN_WINDOW
        ]
        if not followups:
            continue
        new_role = str(promo['metadata'].get('new_role') or promo['metadata'].get('role') or '').lower()
        severity = RISK_CRITICAL if new_role == 'owner' else RISK_HIGH
        out.append(_finding(
            anomaly_type='NEW_ADMIN_HIGH_RISK_ACTION',
            severity=severity,
            confidence=85.0,
            user_id=target_user,
            dedup_key=f'NEW_ADMIN_HIGH_RISK_ACTION:{target_user}',
            evidence={
                'promoted_user_id': target_user,
                'promoted_to': new_role,
                'promotion_audit_event_id': promo['id'],
                'promoted_at': _iso(promo_at),
                'followup_actions': sorted({e['action'] for e in followups}),
                'followup_audit_event_ids': [e['id'] for e in followups],
            },
            first_seen=promo_at,
            last_seen=followups[-1]['created_at'],
        ))
    return out


def _max_window_count(evs: list[dict[str, Any]], window: timedelta) -> tuple[int, list[dict[str, Any]]]:
    """Largest count of events within any sliding ``window``; returns (peak, events)."""
    dated = sorted((e for e in evs if e['created_at'] is not None), key=lambda e: e['created_at'])
    best = 0
    best_events: list[dict[str, Any]] = []
    left = 0
    for right in range(len(dated)):
        while dated[right]['created_at'] - dated[left]['created_at'] > window:
            left += 1
        span = dated[left:right + 1]
        if len(span) > best:
            best = len(span)
            best_events = span
    return best, best_events


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if value else None


# ---------------------------------------------------------------------------
# Persistence for anomaly findings (explicit evaluation path only)
# ---------------------------------------------------------------------------

def _persist_findings(connection: Any, workspace_id: str, findings: list[dict[str, Any]], *, now: datetime) -> int:
    persisted = 0
    for f in findings:
        connection.execute(
            '''
            INSERT INTO governance_anomalies
                (id, workspace_id, user_id, type, severity, confidence, status, dedup_key,
                 evidence, first_seen_at, last_seen_at, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, 'open', %s, %s::jsonb, %s, %s, %s, %s)
            ON CONFLICT (workspace_id, dedup_key) WHERE status IN ('open', 'acknowledged')
            DO UPDATE SET
                severity = EXCLUDED.severity,
                confidence = EXCLUDED.confidence,
                evidence = EXCLUDED.evidence,
                last_seen_at = EXCLUDED.last_seen_at,
                updated_at = EXCLUDED.updated_at
            ''',
            (
                str(uuid.uuid4()), workspace_id, f.get('user_id'), f['type'], f['severity'],
                f['confidence'], f['dedup_key'], pilot._json_dumps(f['evidence']),
                f.get('first_seen_at') or now, f.get('last_seen_at') or now, now, now,
            ),
        )
        persisted += 1
    return persisted


def _touch_last_evaluated(connection: Any, workspace_id: str, *, now: datetime) -> None:
    connection.execute(
        '''
        INSERT INTO workspace_settings (workspace_id, governance_last_evaluated_at, updated_at)
        VALUES (%s, %s, %s)
        ON CONFLICT (workspace_id) DO UPDATE SET
            governance_last_evaluated_at = EXCLUDED.governance_last_evaluated_at,
            updated_at = EXCLUDED.updated_at
        ''',
        (workspace_id, now, now),
    )


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _serialize_change_request(row: dict[str, Any], actor: dict[str, Any] | None = None,
                              approver: dict[str, Any] | None = None) -> dict[str, Any]:
    item = pilot._json_safe_value(dict(row))
    requester = actor or {}
    approver = approver or {}
    return {
        'id': str(item.get('id')),
        'change_type': item.get('change_type'),
        'target': item.get('target'),
        'before_value': item.get('before_value') if isinstance(item.get('before_value'), dict) else {},
        'proposed_value': item.get('proposed_value') if isinstance(item.get('proposed_value'), dict) else {},
        'risk_level': item.get('risk_level'),
        'risk_reason': item.get('risk_reason'),
        'status': item.get('status'),
        'decision_note': item.get('decision_note'),
        'requested_by_user_id': str(item.get('requested_by_user_id')) if item.get('requested_by_user_id') else None,
        'requested_by': requester.get('full_name') or requester.get('email') or (str(item.get('requested_by_user_id')) if item.get('requested_by_user_id') else 'unknown'),
        'requested_by_role': item.get('requested_by_role'),
        'approved_by_user_id': str(item.get('approved_by_user_id')) if item.get('approved_by_user_id') else None,
        'approved_by': approver.get('full_name') or approver.get('email') or (str(item.get('approved_by_user_id')) if item.get('approved_by_user_id') else None),
        'requested_at': item.get('requested_at'),
        'approved_at': item.get('approved_at'),
        'executed_at': item.get('executed_at'),
        'failure_reason': item.get('failure_reason'),
    }


def _serialize_anomaly(row: dict[str, Any], actor: dict[str, Any] | None = None) -> dict[str, Any]:
    item = pilot._json_safe_value(dict(row))
    actor = actor or {}
    return {
        'id': str(item.get('id')),
        'type': item.get('type'),
        'severity': item.get('severity'),
        'confidence': float(item.get('confidence')) if item.get('confidence') is not None else None,
        'status': item.get('status'),
        'user_id': str(item.get('user_id')) if item.get('user_id') else None,
        'actor': actor.get('full_name') or actor.get('email') or (str(item.get('user_id')) if item.get('user_id') else 'unknown'),
        'evidence': item.get('evidence') if isinstance(item.get('evidence'), dict) else {},
        'first_seen_at': item.get('first_seen_at'),
        'last_seen_at': item.get('last_seen_at'),
        'resolved_at': item.get('resolved_at'),
    }


def _lookup_users(connection: Any, user_ids: set[str]) -> dict[str, dict[str, Any]]:
    ids = [uid for uid in user_ids if uid]
    if not ids:
        return {}
    rows = connection.execute(
        'SELECT id, email, full_name FROM users WHERE id = ANY(%s)',
        (ids,),
    ).fetchall()
    return {str(r['id']): {'email': r.get('email'), 'full_name': r.get('full_name')} for r in (rows or [])}


def _validate_security_policy_payload(payload: dict[str, Any]) -> dict[str, Any]:
    enforcement = str(payload.get('mfa_enforcement', '')).strip().lower()
    if enforcement not in {'optional', 'administrators', 'all_members'}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail='mfa_enforcement must be optional, administrators, or all_members.')
    try:
        minutes = int(payload.get('reauthentication_minutes'))
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail='reauthentication_minutes must be an integer.')
    if not 1 <= minutes <= 120:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail='reauthentication_minutes must be between 1 and 120.')
    return {'mfa_enforcement': enforcement, 'reauthentication_minutes': minutes}


def _apply_security_policy(connection: Any, workspace_id: str, user_id: str, proposed: dict[str, Any]) -> None:
    """Apply a security-policy change to the CANONICAL workspace_auth_policies store."""
    connection.execute(
        """
        INSERT INTO workspace_auth_policies (workspace_id, mfa_enforcement, reauthentication_minutes, updated_by_user_id)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (workspace_id) DO UPDATE SET
            mfa_enforcement = EXCLUDED.mfa_enforcement,
            reauthentication_minutes = EXCLUDED.reauthentication_minutes,
            updated_by_user_id = EXCLUDED.updated_by_user_id,
            updated_at = NOW()
        """,
        (workspace_id, proposed['mfa_enforcement'], proposed['reauthentication_minutes'], user_id),
    )


# ---------------------------------------------------------------------------
# General workspace settings (Job A — low-risk direct update, version-guarded)
# ---------------------------------------------------------------------------

def get_workspace_settings(request: Request) -> dict[str, Any]:
    pilot.require_live_mode()
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        user = pilot.authenticate_with_connection(connection, request)
        workspace_context = pilot.resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        workspace_id = workspace_context['workspace_id']
        role = pilot._normalize_workspace_role(str(workspace_context['role']))
        settings_row = _settings_row(connection, workspace_id)
        can_manage = pilot._workspace_permission_granted(connection, workspace_id, role, SETTINGS_WRITE_PERMISSION)
        return {
            'workspace_id': workspace_id,
            'name': workspace_context['workspace'].get('name'),
            'timezone': (settings_row or {}).get('timezone') or 'UTC',
            'currency': (settings_row or {}).get('currency') or 'USD',
            'version': int((settings_row or {}).get('version') or 1),
            'allowed_timezones': ALLOWED_TIMEZONES,
            'allowed_currencies': ALLOWED_CURRENCIES,
            'can_manage': can_manage,
        }


def perform_settings_update(connection: Any, *, workspace_id: str, user_id: str, actor_role: str,
                            name: str | None, timezone: str | None, currency: str | None,
                            expected_version: int, request: Request | None) -> dict[str, Any]:
    """Core low-risk general-settings update with optimistic concurrency.

    Bumps the shared ``version`` so the whole General form (name + timezone +
    currency) is guarded together: a stale tab submitting an old version is
    rejected with 409 instead of silently overwriting a newer configuration.
    """
    current = _settings_row(connection, workspace_id)
    current_tz = (current or {}).get('timezone') or 'UTC'
    current_currency = (current or {}).get('currency') or 'USD'
    current_version = int((current or {}).get('version') or 1)

    new_tz = current_tz if timezone is None else str(timezone).strip()
    new_currency = current_currency if currency is None else str(currency).strip().upper()
    if new_tz not in ALLOWED_TIMEZONES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail='Unsupported timezone.')
    if new_currency not in ALLOWED_CURRENCIES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail='Unsupported currency.')

    workspace_row = connection.execute('SELECT name FROM workspaces WHERE id = %s', (workspace_id,)).fetchone()
    current_name = str((workspace_row or {}).get('name') or '')
    new_name = current_name if name is None else str(name).strip()
    if name is not None and not (2 <= len(new_name) <= 120):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail='Workspace name must be 2–120 characters.')

    now = pilot.utc_now()
    # Ensure a settings row exists seeded with the current effective values, so the
    # version-guarded UPDATE below is the single point that increments the version.
    connection.execute(
        '''
        INSERT INTO workspace_settings (workspace_id, timezone, currency, version, created_at, updated_at)
        VALUES (%s, %s, %s, 1, %s, %s)
        ON CONFLICT (workspace_id) DO NOTHING
        ''',
        (workspace_id, current_tz, current_currency, now, now),
    )
    updated = connection.execute(
        '''
        UPDATE workspace_settings
        SET timezone = %s, currency = %s, version = version + 1, updated_at = %s, updated_by_user_id = %s
        WHERE workspace_id = %s AND version = %s
        RETURNING version
        ''',
        (new_tz, new_currency, now, user_id, workspace_id, expected_version),
    ).fetchone()
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={'code': 'STALE_VERSION',
                    'message': 'Settings changed since you opened this page. Refresh the current configuration before retrying.',
                    'current_version': current_version},
        )
    new_version = int(updated['version'])

    if name is not None and new_name != current_name:
        connection.execute('UPDATE workspaces SET name = %s WHERE id = %s', (new_name, workspace_id))

    pilot.log_audit(
        connection, action='workspace.settings_updated', entity_type='workspace', entity_id=workspace_id,
        request=request, user_id=user_id, workspace_id=workspace_id,
        metadata={
            'risk_level': RISK_LOW,
            'before': {'name': current_name, 'timezone': current_tz, 'currency': current_currency},
            'after': {'name': new_name, 'timezone': new_tz, 'currency': new_currency},
            'version_from': expected_version, 'version_to': new_version,
        },
    )
    connection.commit()
    return {
        'workspace_id': workspace_id,
        'name': new_name,
        'timezone': new_tz,
        'currency': new_currency,
        'version': new_version,
    }


def update_workspace_settings(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    pilot.require_live_mode()
    if 'expected_version' not in payload:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail='expected_version is required.')
    try:
        expected_version = int(payload.get('expected_version'))
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail='expected_version must be an integer.')
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        user, workspace_context = pilot._require_workspace_permission(connection, request, SETTINGS_WRITE_PERMISSION)
        return perform_settings_update(
            connection,
            workspace_id=workspace_context['workspace_id'],
            user_id=user['id'],
            actor_role=pilot._normalize_workspace_role(str(workspace_context['role'])),
            name=payload.get('name'),
            timezone=payload.get('timezone'),
            currency=payload.get('currency'),
            expected_version=expected_version,
            request=request,
        )


# ---------------------------------------------------------------------------
# Security settings (canonical workspace_auth_policies + truthful states)
# ---------------------------------------------------------------------------

def get_security_settings(request: Request) -> dict[str, Any]:
    pilot.require_live_mode()
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        user = pilot.authenticate_with_connection(connection, request)
        workspace_context = pilot.resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        workspace_id = workspace_context['workspace_id']
        role = pilot._normalize_workspace_role(str(workspace_context['role']))
        policy = pilot._workspace_auth_policy(connection, workspace_id)
        can_manage = pilot._workspace_permission_granted(connection, workspace_id, role, GOVERNANCE_MANAGE_PERMISSION)
        pending = connection.execute(
            "SELECT COUNT(*) AS count FROM governance_change_requests "
            "WHERE workspace_id = %s AND status = 'pending'",
            (workspace_id,),
        ).fetchone()
        return {
            'mfa_enforcement': policy['mfa_enforcement'],
            'reauthentication_minutes': policy['reauthentication_minutes'],
            'session_timeout_options': SESSION_TIMEOUT_OPTIONS,
            'audit_logging': {
                'status': 'always_on',
                'detail': 'Audit logging is always on. Sensitive changes are recorded in an append-only, hash-chained trail and cannot be disabled.',
            },
            'ip_allowlist': {
                'status': 'not_configured',
                'supported': False,
                'detail': 'IP allowlist enforcement is not available in this deployment.',
            },
            'encryption': {
                'status': 'infrastructure',
                'detail': 'Encryption at rest and in transit is enforced at the infrastructure layer.',
            },
            'can_manage': can_manage,
            'pending_change_requests': int((pending or {}).get('count') or 0),
        }


def submit_security_change(connection: Any, *, workspace_id: str, user_id: str, actor_role: str,
                           proposed: dict[str, Any], request: Request | None) -> dict[str, Any]:
    """Core security-policy change: classify, then apply directly (low-risk) or
    open a pending approval request (HIGH/CRITICAL). Canonical store is NEVER
    mutated while a request is pending."""
    before = pilot._workspace_auth_policy(connection, workspace_id)
    classification = classify_security_policy_change(before, proposed)
    risk_level = classification['risk_level']

    if not classification['requires_approval']:
        _apply_security_policy(connection, workspace_id, user_id, proposed)
        pilot.log_audit(
            connection, action='governance.security_policy_updated', entity_type='workspace',
            entity_id=workspace_id, request=request, user_id=user_id, workspace_id=workspace_id,
            metadata={
                'risk_level': risk_level,
                'before': before, 'after': proposed,
                'mfa_enforcement': proposed['mfa_enforcement'],
                'requires_approval': False,
            },
        )
        connection.commit()
        return {'status': 'applied', 'risk_level': risk_level,
                'risk_reason': classification['risk_reason'],
                'policy': proposed}

    # HIGH/CRITICAL — do not mutate; open a pending approval request.
    existing = connection.execute(
        "SELECT id FROM governance_change_requests "
        "WHERE workspace_id = %s AND change_type = 'security_policy' AND target = 'workspace_auth_policy' AND status = 'pending'",
        (workspace_id,),
    ).fetchone()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={'code': 'PENDING_CHANGE_EXISTS',
                    'message': 'A security policy change is already awaiting approval.'},
        )
    request_id = str(uuid.uuid4())
    now = pilot.utc_now()
    connection.execute(
        '''
        INSERT INTO governance_change_requests
            (id, workspace_id, requested_by_user_id, requested_by_role, change_type, target,
             before_value, proposed_value, risk_level, risk_reason, status, requested_at, created_at, updated_at)
        VALUES (%s, %s, %s, %s, 'security_policy', 'workspace_auth_policy',
                %s::jsonb, %s::jsonb, %s, %s, 'pending', %s, %s, %s)
        ''',
        (request_id, workspace_id, user_id, actor_role, pilot._json_dumps(before),
         pilot._json_dumps(proposed), risk_level, classification['risk_reason'], now, now, now),
    )
    pilot.log_audit(
        connection, action='governance.change_requested', entity_type='governance_change_request',
        entity_id=request_id, request=request, user_id=user_id, workspace_id=workspace_id,
        metadata={'risk_level': risk_level, 'change_type': 'security_policy',
                  'before': before, 'proposed': proposed, 'risk_reason': classification['risk_reason']},
    )
    connection.commit()
    return {
        'status': 'pending_approval',
        'risk_level': risk_level,
        'risk_reason': classification['risk_reason'],
        'change_request_id': request_id,
    }


def submit_security_policy_change(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    pilot.require_live_mode()
    proposed = _validate_security_policy_payload(payload)
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        user, workspace_context = pilot._require_workspace_permission(
            connection, request, GOVERNANCE_MANAGE_PERMISSION, require_reauthentication=True
        )
        return submit_security_change(
            connection,
            workspace_id=workspace_context['workspace_id'],
            user_id=user['id'],
            actor_role=pilot._normalize_workspace_role(str(workspace_context['role'])),
            proposed=proposed,
            request=request,
        )


# ---------------------------------------------------------------------------
# Policy posture
# ---------------------------------------------------------------------------

def get_policy_posture(request: Request) -> dict[str, Any]:
    pilot.require_live_mode()
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        user = pilot.authenticate_with_connection(connection, request)
        workspace_context = pilot.resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        return compute_policy_posture(connection, workspace_context['workspace_id'])


# ---------------------------------------------------------------------------
# Governance Guard summary (posture + anomaly + safeguards, read-only)
# ---------------------------------------------------------------------------

def get_governance_summary(request: Request) -> dict[str, Any]:
    pilot.require_live_mode()
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        user = pilot.authenticate_with_connection(connection, request)
        workspace_context = pilot.resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        workspace_id = workspace_context['workspace_id']
        role = pilot._normalize_workspace_role(str(workspace_context['role']))
        can_manage = pilot._workspace_permission_granted(connection, workspace_id, role, GOVERNANCE_MANAGE_PERMISSION)
        last_evaluated = _governance_last_evaluated_at(connection, workspace_id)
        counts = _open_anomaly_counts(connection, workspace_id) if last_evaluated else {}
        pending = connection.execute(
            "SELECT COUNT(*) AS count FROM governance_change_requests WHERE workspace_id = %s AND status = 'pending'",
            (workspace_id,),
        ).fetchone()
        total_open = sum(counts.values()) if counts else 0
        return {
            'anomalies': {
                'evaluated': last_evaluated is not None,
                'last_evaluated_at': last_evaluated.isoformat() if last_evaluated else None,
                'open_total': total_open,
                'by_severity': counts or {level: 0 for level in RISK_LEVELS},
            },
            'change_safeguards': {
                # Deterministic policy: HIGH/CRITICAL security changes always
                # require a separate-duty approval. This safeguard cannot be bypassed.
                'status': 'approval_required',
                'detail': 'All HIGH and CRITICAL security changes require a separate-duty approval.',
            },
            'pending_change_requests': int((pending or {}).get('count') or 0),
            'can_manage': can_manage,
            'supported_anomaly_types': list(SUPPORTED_ANOMALY_TYPES),
        }


# ---------------------------------------------------------------------------
# Anomalies (read + status changes)
# ---------------------------------------------------------------------------

def list_governance_anomalies(request: Request) -> dict[str, Any]:
    pilot.require_live_mode()
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        _, workspace_context = pilot._require_workspace_permission(connection, request, GOVERNANCE_MANAGE_PERMISSION)
        workspace_id = workspace_context['workspace_id']
        last_evaluated = _governance_last_evaluated_at(connection, workspace_id)
        rows = connection.execute(
            '''
            SELECT id, user_id, type, severity, confidence, status, evidence, first_seen_at, last_seen_at, resolved_at
            FROM governance_anomalies
            WHERE workspace_id = %s
            ORDER BY (status IN ('open','acknowledged')) DESC,
                     CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                     last_seen_at DESC
            LIMIT 200
            ''',
            (workspace_id,),
        ).fetchall()
        actor_lookup = _lookup_users(connection, {str(r['user_id']) for r in (rows or []) if r.get('user_id')})
        return {
            'evaluated': last_evaluated is not None,
            'last_evaluated_at': last_evaluated.isoformat() if last_evaluated else None,
            'anomalies': [_serialize_anomaly(r, actor_lookup.get(str(r.get('user_id')))) for r in (rows or [])],
        }


def get_governance_anomaly(anomaly_id: str, request: Request) -> dict[str, Any]:
    pilot.require_live_mode()
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        _, workspace_context = pilot._require_workspace_permission(connection, request, GOVERNANCE_MANAGE_PERMISSION)
        workspace_id = workspace_context['workspace_id']
        row = connection.execute(
            '''
            SELECT id, user_id, type, severity, confidence, status, evidence, first_seen_at, last_seen_at, resolved_at
            FROM governance_anomalies WHERE id = %s AND workspace_id = %s
            ''',
            (anomaly_id, workspace_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Anomaly not found.')
        actor_lookup = _lookup_users(connection, {str(row['user_id'])} if row.get('user_id') else set())
        item = _serialize_anomaly(row, actor_lookup.get(str(row.get('user_id'))))
        item['recommended_actions'] = _anomaly_recommended_actions(str(row['type']))
        return item


def _anomaly_recommended_actions(anomaly_type: str) -> list[str]:
    """Only actions the backend can actually perform or that are meaningfully
    actionable in-product. No decorative buttons."""
    base = ['Acknowledge finding', 'Mark resolved', 'Dismiss finding']
    if anomaly_type in ('PRIVILEGE_ESCALATION', 'NEW_ADMIN_HIGH_RISK_ACTION', 'ROLE_CHANGE_BURST'):
        return ['Review recent role changes in the Team tab'] + base
    if anomaly_type == 'MFA_WEAKENED':
        return ['Review the MFA/session policy in the Security tab'] + base
    if anomaly_type == 'RAPID_SECURITY_CHANGES':
        return ['Review the change log for this actor'] + base
    return base


def update_governance_anomaly_status(anomaly_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    pilot.require_live_mode()
    new_status = str(payload.get('status', '')).strip().lower()
    if new_status not in {'acknowledged', 'resolved', 'dismissed', 'open'}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail='Invalid anomaly status.')
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        user, workspace_context = pilot._require_workspace_permission(connection, request, GOVERNANCE_MANAGE_PERMISSION)
        workspace_id = workspace_context['workspace_id']
        row = connection.execute(
            'SELECT id, type, severity, status FROM governance_anomalies WHERE id = %s AND workspace_id = %s',
            (anomaly_id, workspace_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Anomaly not found.')
        now = pilot.utc_now()
        resolved = new_status in ('resolved', 'dismissed')
        connection.execute(
            '''
            UPDATE governance_anomalies
            SET status = %s, updated_at = %s,
                resolved_at = CASE WHEN %s THEN %s ELSE NULL END,
                resolved_by_user_id = CASE WHEN %s THEN %s ELSE NULL END
            WHERE id = %s AND workspace_id = %s
            ''',
            (new_status, now, resolved, now, resolved, user['id'], anomaly_id, workspace_id),
        )
        pilot.log_audit(
            connection, action=f'governance.anomaly_{new_status}', entity_type='governance_anomaly',
            entity_id=anomaly_id, request=request, user_id=user['id'], workspace_id=workspace_id,
            metadata={'anomaly_type': str(row['type']), 'severity': str(row['severity']),
                      'previous_status': str(row['status']), 'new_status': new_status},
        )
        connection.commit()
        return {'id': anomaly_id, 'status': new_status}


# ---------------------------------------------------------------------------
# Change log (backed by canonical audit_logs, enriched with risk classification)
# ---------------------------------------------------------------------------

def list_governance_changes(request: Request) -> dict[str, Any]:
    pilot.require_live_mode()
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        _, workspace_context = pilot._require_workspace_permission(connection, request, GOVERNANCE_MANAGE_PERMISSION)
        workspace_id = workspace_context['workspace_id']
        rows = connection.execute(
            '''
            SELECT id, action, entity_type, entity_id, user_id, ip_address, metadata, created_at
            FROM audit_logs
            WHERE workspace_id = %s
            ORDER BY created_at DESC
            LIMIT 300
            ''',
            (workspace_id,),
        ).fetchall()
        total_row = connection.execute(
            'SELECT COUNT(*) AS total FROM audit_logs WHERE workspace_id = %s', (workspace_id,)
        ).fetchone()
        actor_lookup = _lookup_users(connection, {str(r['user_id']) for r in (rows or []) if r.get('user_id')})
        changes = []
        for row in rows or []:
            item = pilot._json_safe_value(dict(row))
            meta = item.get('metadata') if isinstance(item.get('metadata'), dict) else {}
            action = str(item.get('action') or '')
            actor = actor_lookup.get(str(item.get('user_id'))) if item.get('user_id') else None
            changes.append({
                'id': str(item.get('id')),
                'action': action,
                'change': _describe_change(action, meta),
                'target': item.get('entity_id'),
                'target_type': item.get('entity_type'),
                'risk_level': classify_audit_action(action, meta),
                'approval': _approval_label(action, meta),
                'result': meta.get('result') or 'completed',
                'actor': (actor or {}).get('full_name') or (actor or {}).get('email') or (str(item.get('user_id')) if item.get('user_id') else 'system'),
                'actor_user_id': str(item.get('user_id')) if item.get('user_id') else None,
                'source_ip': item.get('ip_address'),
                'timestamp': item.get('created_at'),
            })
        total = int((total_row or {}).get('total') or len(changes))
        return {'changes': changes, 'total': total, 'returned': len(changes), 'capped': total > len(changes)}


def _describe_change(action: str, meta: dict[str, Any]) -> str:
    before = meta.get('before') if isinstance(meta.get('before'), dict) else {}
    after = meta.get('after') if isinstance(meta.get('after'), dict) else {}
    if action == 'workspace.settings_updated':
        parts = []
        for field in ('name', 'timezone', 'currency'):
            if before.get(field) != after.get(field):
                parts.append(f'{field} {before.get(field)} → {after.get(field)}')
        return 'Updated workspace settings' + (f' ({", ".join(parts)})' if parts else '')
    if action in ('governance.security_policy_updated', 'workspace.auth_policy_updated'):
        return 'Updated security policy'
    if action == 'governance.change_requested':
        return 'Requested security policy change (awaiting approval)'
    if action == 'governance.change_executed':
        return 'Executed approved security policy change'
    if action == 'governance.change_rejected':
        return 'Rejected security policy change'
    if action == 'member.role_update':
        prev = meta.get('previous_role')
        new = meta.get('new_role') or meta.get('role')
        return f'Changed member role {prev} → {new}' if prev else f'Changed member role to {new}'
    if action == 'member.remove':
        return 'Removed workspace member'
    if action == 'auth.mfa_disabled':
        return 'Disabled account MFA'
    return action.replace('.', ' ').replace('_', ' ').strip().capitalize()


def _approval_label(action: str, meta: dict[str, Any]) -> str:
    if action in ('governance.change_requested',):
        return 'pending'
    if action in ('governance.change_executed',):
        return 'approved'
    if action in ('governance.change_rejected',):
        return 'rejected'
    return 'not_required'


# ---------------------------------------------------------------------------
# Approvals (governance_change_requests) + separation-of-duties execution
# ---------------------------------------------------------------------------

def list_governance_approvals(request: Request) -> dict[str, Any]:
    pilot.require_live_mode()
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        _, workspace_context = pilot._require_workspace_permission(connection, request, GOVERNANCE_MANAGE_PERMISSION)
        workspace_id = workspace_context['workspace_id']
        rows = connection.execute(
            '''
            SELECT id, requested_by_user_id, requested_by_role, change_type, target, before_value, proposed_value,
                   risk_level, risk_reason, status, decision_note, approved_by_user_id,
                   requested_at, approved_at, executed_at, failure_reason
            FROM governance_change_requests
            WHERE workspace_id = %s
            ORDER BY (status = 'pending') DESC, requested_at DESC
            LIMIT 200
            ''',
            (workspace_id,),
        ).fetchall()
        ids: set[str] = set()
        for r in rows or []:
            if r.get('requested_by_user_id'):
                ids.add(str(r['requested_by_user_id']))
            if r.get('approved_by_user_id'):
                ids.add(str(r['approved_by_user_id']))
        lookup = _lookup_users(connection, ids)
        approvals = [
            _serialize_change_request(
                r,
                actor=lookup.get(str(r.get('requested_by_user_id'))) if r.get('requested_by_user_id') else None,
                approver=lookup.get(str(r.get('approved_by_user_id'))) if r.get('approved_by_user_id') else None,
            )
            for r in (rows or [])
        ]
        pending_count = sum(1 for a in approvals if a['status'] == 'pending')
        return {'approvals': approvals, 'pending_count': pending_count}


def decide_change_request(connection: Any, *, workspace_id: str, approver_id: str, approver_role: str,
                          request_id: str, decision: str, note: str | None, request: Request | None) -> dict[str, Any]:
    """Core approval decision with separation of duties and execute-once semantics."""
    row = connection.execute(
        '''
        SELECT id, requested_by_user_id, change_type, target, before_value, proposed_value, risk_level, status
        FROM governance_change_requests WHERE id = %s AND workspace_id = %s
        ''',
        (request_id, workspace_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Change request not found.')

    now = pilot.utc_now()

    if decision == 'reject':
        if str(row['status']) != 'pending':
            if str(row['status']) == 'rejected':
                return {'id': request_id, 'status': 'rejected', 'idempotent': True}
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail={'code': 'ALREADY_DECIDED', 'message': f'Change request is {row["status"]}.'})
        rejected = connection.execute(
            '''
            UPDATE governance_change_requests
            SET status = 'rejected', approved_by_user_id = %s, approved_at = %s, decision_note = %s, updated_at = %s
            WHERE id = %s AND workspace_id = %s AND status = 'pending'
            RETURNING id
            ''',
            (approver_id, now, note, now, request_id, workspace_id),
        ).fetchone()
        if rejected is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail={'code': 'ALREADY_DECIDED', 'message': 'Change request is no longer pending.'})
        pilot.log_audit(
            connection, action='governance.change_rejected', entity_type='governance_change_request',
            entity_id=request_id, request=request, user_id=approver_id, workspace_id=workspace_id,
            metadata={'risk_level': str(row['risk_level']), 'change_type': str(row['change_type']), 'note': note},
        )
        connection.commit()  # rejection leaves the canonical setting unchanged
        return {'id': request_id, 'status': 'rejected'}

    # decision == 'approve'
    # Separation of duties: a requester can never approve their own request.
    if str(row['requested_by_user_id']) == str(approver_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={'code': 'SELF_APPROVAL_FORBIDDEN',
                    'message': 'You cannot approve a change you requested. A different authorized approver must approve it.'},
        )
    if str(row['status']) == 'completed':
        return {'id': request_id, 'status': 'completed', 'idempotent': True}
    if str(row['status']) != 'pending':
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail={'code': 'ALREADY_DECIDED', 'message': f'Change request is {row["status"]}.'})

    change_type = str(row['change_type'])
    if change_type != 'security_policy':
        # Unsupported change types cannot be executed — record a durable failure.
        connection.execute(
            "UPDATE governance_change_requests SET status = 'failed', approved_by_user_id = %s, approved_at = %s, "
            "failure_reason = %s, updated_at = %s WHERE id = %s AND workspace_id = %s AND status = 'pending'",
            (approver_id, now, 'unsupported_change_type', now, request_id, workspace_id),
        )
        pilot.log_audit(
            connection, action='governance.change_failed', entity_type='governance_change_request',
            entity_id=request_id, request=request, user_id=approver_id, workspace_id=workspace_id,
            metadata={'reason': 'unsupported_change_type', 'change_type': change_type},
        )
        connection.commit()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail={'code': 'UNSUPPORTED_CHANGE', 'message': 'This change type cannot be executed.'})

    # Atomic claim + execute-once: only one caller transitions pending -> completed.
    claimed = connection.execute(
        '''
        UPDATE governance_change_requests
        SET status = 'completed', approved_by_user_id = %s, approved_at = %s, executed_at = %s,
            decision_note = %s, updated_at = %s
        WHERE id = %s AND workspace_id = %s AND status = 'pending'
        RETURNING proposed_value, before_value, risk_level
        ''',
        (approver_id, now, now, note, now, request_id, workspace_id),
    ).fetchone()
    if claimed is None:
        # Lost the race — re-read for an idempotent/conflict answer.
        again = connection.execute(
            'SELECT status FROM governance_change_requests WHERE id = %s AND workspace_id = %s',
            (request_id, workspace_id),
        ).fetchone()
        if again and str(again['status']) == 'completed':
            return {'id': request_id, 'status': 'completed', 'idempotent': True}
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail={'code': 'ALREADY_DECIDED', 'message': 'Change request is no longer pending.'})

    proposed = claimed['proposed_value'] if isinstance(claimed['proposed_value'], dict) else {}
    before = claimed['before_value'] if isinstance(claimed['before_value'], dict) else {}
    risk_level = str(claimed['risk_level'])
    # Apply exactly once to the canonical store, in the SAME transaction as the
    # status transition, so configuration and audit commit together.
    _apply_security_policy(connection, workspace_id, approver_id, proposed)
    pilot.log_audit(
        connection, action='governance.change_executed', entity_type='governance_change_request',
        entity_id=request_id, request=request, user_id=approver_id, workspace_id=workspace_id,
        metadata={'risk_level': risk_level, 'change_type': change_type, 'before': before, 'after': proposed},
    )
    pilot.log_audit(
        connection, action='governance.security_policy_updated', entity_type='workspace',
        entity_id=workspace_id, request=request, user_id=approver_id, workspace_id=workspace_id,
        metadata={'risk_level': risk_level, 'before': before, 'after': proposed,
                  'mfa_enforcement': proposed.get('mfa_enforcement'), 'via_approval': request_id},
    )
    connection.commit()
    return {'id': request_id, 'status': 'completed', 'applied': proposed}


def decide_governance_approval(request_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    pilot.require_live_mode()
    decision = str(payload.get('decision', '')).strip().lower()
    if decision not in {'approve', 'reject'}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail='decision must be approve or reject.')
    note = payload.get('note')
    note = str(note).strip() if note is not None else None
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        user, workspace_context = pilot._require_workspace_permission(
            connection, request, GOVERNANCE_MANAGE_PERMISSION, require_reauthentication=True
        )
        return decide_change_request(
            connection,
            workspace_id=workspace_context['workspace_id'],
            approver_id=user['id'],
            approver_role=pilot._normalize_workspace_role(str(workspace_context['role'])),
            request_id=request_id,
            decision=decision,
            note=note,
            request=request,
        )


# ---------------------------------------------------------------------------
# Explicit evaluation (the ONLY write path for anomalies)
# ---------------------------------------------------------------------------

def evaluate_governance(request: Request) -> dict[str, Any]:
    pilot.require_live_mode()
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        user, workspace_context = pilot._require_workspace_permission(connection, request, GOVERNANCE_MANAGE_PERMISSION)
        workspace_id = workspace_context['workspace_id']
        now = pilot.utc_now()
        events = _load_audit_evidence(connection, workspace_id, now=now)
        findings = detect_anomalies(events, now=now)
        persisted = _persist_findings(connection, workspace_id, findings, now=now)
        _touch_last_evaluated(connection, workspace_id, now=now)
        by_severity = {level: 0 for level in RISK_LEVELS}
        for f in findings:
            by_severity[f['severity']] = by_severity.get(f['severity'], 0) + 1
        pilot.log_audit(
            connection, action='governance.evaluation_run', entity_type='workspace', entity_id=workspace_id,
            request=request, user_id=user['id'], workspace_id=workspace_id,
            metadata={'findings': persisted, 'by_severity': by_severity, 'events_scanned': len(events)},
        )
        connection.commit()
        return {
            'evaluated_at': now.isoformat(),
            'findings_count': persisted,
            'by_severity': by_severity,
            'events_scanned': len(events),
            'supported_anomaly_types': list(SUPPORTED_ANOMALY_TYPES),
        }
