from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from time import perf_counter, sleep
from typing import Any
from fastapi import HTTPException, Request, status
import psycopg
from psycopg import errors as psycopg_errors

from services.api.app.activity_providers import (
    ActivityEvent,
    ActivityProviderResult,
    fetch_target_activity_result,
    monitoring_ingestion_runtime,
)
from services.api.app.evm_activity_provider import (
    JsonRpcClient,
    evaluate_chain_mismatch,
    resolve_monitored_wallet,
    rpc_provider_backoff_active,
    rpc_provider_backoff_status,
)
from services.api.app.monitoring_truth import ui_evidence_state, ui_truthfulness_state
from services.api.app.monitoring_reliability import MonitoringSLOs, evaluate_monitoring_slos, monitoring_slo_snapshot
from services.api.app.monitorable_target_types import (
    is_monitorable_target_type,
    monitorable_target_types_sql_clause,
)
from services.api.app.db_failure import classify_db_error
from services.api.app.worker_status import (
    build_worker_status,
    classify_realtime_tx_verdict,
    classify_wallet_transfer_detected_by,
    detected_by_from_ingestion_source as worker_status_detected_by,
    live_coverage_gap_reason,
    realtime_active_by_watcher_facts,
    realtime_enabled,
    resolve_telemetry_detected_by,
    stable_poll_stale_threshold_seconds,
    DETECTED_BY_BASIS_UNCLASSIFIED,
    REALTIME_DETECTED_BY,
    STABLE_DETECTED_BY,
    WALLET_TRANSFER_EVENT_TYPES,
)
from services.api.app.workspace_monitoring_summary import (
    build_runtime_setup_chain,
    resolve_next_required_action,
    build_workspace_monitoring_summary,
    build_workspace_monitoring_summary_fallback,
)
from services.api.app.pilot import (
    _json_dumps,
    _json_safe_value,
    _require_workspace_permission,
    _severity_meets_threshold,
    authenticate_with_connection,
    ensure_pilot_schema,
    live_mode_enabled,
    require_live_mode,
    log_audit,
    list_workspace_monitored_system_rows,
    monitored_system_row_enabled,
    persist_analysis_run,
    pg_connection,
    resolve_workspace,
    resolve_workspace_context_for_request,
    ensure_monitored_system_for_target,
    ensure_monitoring_runtime_schema_capabilities,
    reconcile_enabled_targets_monitored_systems,
    _target_health_payload,
    evaluate_workspace_monitoring_continuity,
    resolve_response_action_capability,
    normalize_workspace_header_value,
    promote_wallet_transfer_alerts,
)
from services.api.app.threat_payloads import ThreatKind, normalize_threat_payload

THREAT_ENGINE_URL = (os.getenv('THREAT_ENGINE_URL') or 'http://localhost:8002').rstrip('/')
ALERT_DEDUPE_WINDOW_SECONDS = int(
    os.getenv('ALERT_DEDUP_WINDOW_SECONDS', os.getenv('MONITORING_ALERT_DEDUPE_WINDOW_SECONDS', '900'))
)
WORKER_HEARTBEAT_TTL_SECONDS = int(os.getenv('MONITORING_WORKER_HEARTBEAT_TTL_SECONDS', '180'))
MONITOR_POLL_INTERVAL_SECONDS = int(os.getenv('MONITOR_POLL_INTERVAL_SECONDS', '30'))


def _min_monitoring_interval_seconds() -> int:
    """Minimum effective per-target poll interval (seconds). Default 60.

    Production targets configured below this floor are capped to it so a worker
    never polls a Base target — and re-hits eth_blockNumber — more often than once
    per minute. Configurable via MIN_EVM_POLLING_INTERVAL_SECONDS.
    """
    try:
        return max(1, int(os.getenv('MIN_EVM_POLLING_INTERVAL_SECONDS', '60')))
    except (TypeError, ValueError):
        return 60
MONITORED_SYSTEM_HEARTBEAT_TOUCH_SECONDS = max(15, int(os.getenv('MONITORED_SYSTEM_HEARTBEAT_TOUCH_SECONDS', '60')))
MONITORING_DUE_SELECTION_BACKFILL_COOLDOWN_SECONDS = max(
    120,
    int(os.getenv('MONITORING_DUE_SELECTION_BACKFILL_COOLDOWN_SECONDS', '180')),
)
MONITORING_DUE_SELECTION_BACKFILL_MIN_AGE_SECONDS = max(
    30,
    int(os.getenv('MONITORING_DUE_SELECTION_BACKFILL_MIN_AGE_SECONDS', '60')),
)
# Fast self-healing for dead-lettered targets: a target that hit the delivery-attempt
# ceiling is retried after this many seconds (backoff) instead of staying blocked for
# MONITORING_DEAD_LETTER_RECOVERY_HOURS. This keeps a valid target that failed on a
# transient error (e.g. an RPC blip) from being silently excluded from the worker loop
# for a full day, while still providing backoff so a genuinely broken target is not hot-looped.
MONITORING_DEAD_LETTER_RETRY_SECONDS = max(
    60,
    int(os.getenv('MONITORING_DEAD_LETTER_RETRY_SECONDS', '600')),
)

logger = logging.getLogger(__name__)

PREREQUISITE_COUNTER_KEYS: tuple[str, ...] = (
    'raw_enabled_targets',
    'monitorable_enabled_targets',
    'valid_asset_linked_targets',
    'enabled_monitored_systems',
    'valid_target_system_links',
)

NON_LIVE_PROVIDER_SOURCE_TYPES: set[str] = {'demo', 'simulator', 'replay', 'unknown'}
RUNTIME_STATUS_PROXY_TIMEOUT_SECONDS = int(os.getenv('RUNTIME_STATUS_PROXY_TIMEOUT_SECONDS', os.getenv('PROXY_TIMEOUT_SECONDS', '30')))
RUNTIME_STATUS_QUERY_PROFILE_MAX_SAMPLES = int(os.getenv('RUNTIME_STATUS_QUERY_PROFILE_MAX_SAMPLES', '200'))
RUNTIME_STATUS_CACHE_TTL_SECONDS = max(1, int(os.getenv('RUNTIME_STATUS_CACHE_TTL_SECONDS', '15')))
RUNTIME_STATUS_SUMMARY_CACHE_TTL_SECONDS = max(1, int(os.getenv('RUNTIME_STATUS_SUMMARY_CACHE_TTL_SECONDS', '12')))
RUNTIME_STATUS_PRECOMPUTED_COUNTERS_MAX_AGE_SECONDS = max(1, int(os.getenv('RUNTIME_STATUS_PRECOMPUTED_COUNTERS_MAX_AGE_SECONDS', '60')))
RUNTIME_STATUS_P95_ALERT_THRESHOLD_MS = max(1, int(os.getenv('RUNTIME_STATUS_P95_ALERT_THRESHOLD_MS', '10000')))
RUNTIME_STATUS_P99_ALERT_THRESHOLD_MS = max(1, int(os.getenv('RUNTIME_STATUS_P99_ALERT_THRESHOLD_MS', str(max(RUNTIME_STATUS_PROXY_TIMEOUT_SECONDS, 1) * 1000))))
RUNTIME_STATUS_ALERT_WINDOW_SAMPLES = max(1, int(os.getenv('RUNTIME_STATUS_ALERT_WINDOW_SAMPLES', '6')))
RUNTIME_STATUS_ALERT_REQUIRED_BREACHES = max(1, int(os.getenv('RUNTIME_STATUS_ALERT_REQUIRED_BREACHES', '4')))
RUNTIME_STATUS_QUERY_PROFILE_HISTORY: dict[str, deque[float]] = defaultdict(
    lambda: deque(maxlen=max(RUNTIME_STATUS_QUERY_PROFILE_MAX_SAMPLES, 20))
)
RUNTIME_STATUS_WORKSPACE_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
RUNTIME_STATUS_SUMMARY_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
RUNTIME_STATUS_ALERT_BREACH_HISTORY: dict[str, dict[str, deque[bool]]] = defaultdict(
    lambda: {
        'p95': deque(maxlen=max(RUNTIME_STATUS_ALERT_WINDOW_SAMPLES, 1)),
        'p99': deque(maxlen=max(RUNTIME_STATUS_ALERT_WINDOW_SAMPLES, 1)),
    }
)
BACKGROUND_LOOP_HEALTH: dict[str, Any] = {
    'loop_running': False,
    'last_successful_cycle': None,
    'consecutive_failures': 0,
    'next_retry_at': None,
    'backoff_seconds': None,
    'updated_at': None,
}

RUNTIME_STATUS_DEEP_DIAGNOSTICS_ENABLED = os.getenv('RUNTIME_STATUS_DEEP_DIAGNOSTICS_ENABLED', '0').strip().lower() in {'1', 'true', 'yes', 'on'}
MONITORING_COVERAGE_ONLY_WARNING_SECONDS = max(
    60,
    int(float(os.getenv('MONITORING_COVERAGE_ONLY_WARNING_MINUTES', '30')) * 60),
)
_LAST_MONITORED_SYSTEM_HEARTBEAT_TOUCH_AT: datetime | None = None
_LAST_MONITORING_DUE_SELECTION_BACKFILL_AT: dict[str, datetime] = {}
_WORKSPACE_COVERAGE_ONLY_STREAK: dict[str, dict[str, Any]] = {}

ENTERPRISE_READY_REMEDIATION_LINKS: dict[str, str] = {
    'continuity_slo_pass': '/threat#continuity-slo',
    'linked_fresh_evidence': '/threat#telemetry-freshness',
    'stable_monitored_systems': '/threat#monitored-system-state',
    'live_action_capability_readiness': '/threat#response-actions',
}
ENTERPRISE_CRITERIA_REMEDIATION_LINKS: dict[str, str] = {
    'criterion_b_continuity_slos': '/threat#continuity-slo',
    'criterion_c_reconcile_stability': '/threat#monitored-system-state',
    'criterion_d_evidence_chain_hydration': '/threat#telemetry-freshness',
    'criterion_e_live_action_governance': '/threat#response-actions',
    'criterion_f_state_model_ux': '/threat#state-model-ux',
    'hidden_architecture': '/threat#hidden-architecture',
}
ENTERPRISE_READY_LIVE_ACTION_TYPES: tuple[str, ...] = (
    'freeze_wallet',
    'revoke_approval',
    'notify_team',
    'disable_monitored_system',
    'suppress_rule',
    'block_transaction',
)
ENTERPRISE_READY_VALIDATED_LIVE_PATHS: set[str] = {'safe', 'governance', 'manual_only'}




def is_canonical_runtime_truth_enabled() -> bool:
    """Whether monitoring runtime status should derive truth fields from canonical sources only."""
    raw = os.getenv('CANONICAL_RUNTIME_TRUTH_ENABLED')
    if raw is None:
        return True

    runtime_live_or_production = bool(live_mode_enabled()) or not _runtime_status_debug_enabled()
    if runtime_live_or_production:
        return True

    normalized = raw.strip().lower()
    if normalized in {'0', 'false', 'no', 'off'}:
        return False
    if normalized in {'1', 'true', 'yes', 'on'}:
        return True
    return True


def _count_persisted_enabled_monitoring_configs(conn: Any, workspace_id: str) -> int:
    # Migration 0084 created direct monitoring_configs with target_id = targets.id.
    # The old JOIN to monitored_targets always fails for these rows because the UUIDs
    # differ. Use targets directly to mirror the candidate_systems worker query.
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS count
                FROM monitoring_configs mc
                JOIN targets t
                  ON t.id = mc.target_id
                 AND t.workspace_id = mc.workspace_id
                 AND t.enabled = TRUE
                 AND t.deleted_at IS NULL
                WHERE mc.workspace_id = %s
                  AND mc.enabled = TRUE
                  AND mc.provider_type NOT IN ('demo', 'simulator', 'replay', 'unknown', 'target_bridge', 'guided_workflow')
                """,
                (workspace_id,),
            )
            row = cur.fetchone()
    except Exception:
        return 0
    try:
        if isinstance(row, dict):
            return max(int(row.get('count') or 0), 0)
        return max(int((row or [0])[0] or 0), 0)
    except Exception:
        return 0


def _evaluate_enterprise_ready_gate(
    *,
    continuity_slo_pass: bool,
    telemetry_freshness: Any,
    ingestion_freshness: Any,
    detection_pipeline_freshness: Any,
    proof_chain_status: Any,
    runtime_status: Any,
    monitoring_status: Any,
    reporting_systems_count: int,
    monitored_systems_count: int,
    contradiction_flags: list[str] | None,
    guard_flags: list[str] | None,
) -> dict[str, Any]:
    fresh_states = {'fresh', 'in_window'}
    validated_live_action_paths: list[str] = []
    for action_type in ENTERPRISE_READY_LIVE_ACTION_TYPES:
        try:
            capability = resolve_response_action_capability(action_type, 'live')
        except Exception:
            continue
        if not capability.get('supports_mode'):
            continue
        live_execution_path = str(capability.get('live_execution_path') or '').strip().lower()
        if live_execution_path in ENTERPRISE_READY_VALIDATED_LIVE_PATHS:
            validated_live_action_paths.append(action_type)
    live_action_capability_readiness = len(validated_live_action_paths) > 0
    stable_monitored_systems = (
        str(runtime_status or '').strip().lower() == 'live'
        and str(monitoring_status or '').strip().lower() == 'live'
        and int(reporting_systems_count or 0) > 0
        and int(monitored_systems_count or 0) > 0
        and not [str(flag).strip() for flag in (contradiction_flags or []) if str(flag).strip()]
        and not [str(flag).strip() for flag in (guard_flags or []) if str(flag).strip()]
    )
    checks: list[tuple[str, bool]] = [
        ('continuity_slo_pass', bool(continuity_slo_pass)),
        (
            'linked_fresh_evidence',
            str(proof_chain_status or '').strip().lower() == 'complete'
            and str(telemetry_freshness or '').strip().lower() in fresh_states
            and str(ingestion_freshness or '').strip().lower() in fresh_states
            and str(detection_pipeline_freshness or '').strip().lower() in fresh_states,
        ),
        ('stable_monitored_systems', stable_monitored_systems),
        ('live_action_capability_readiness', live_action_capability_readiness),
    ]
    failed_checks = [name for name, passed in checks if not passed]
    criteria_checks: list[tuple[str, bool, list[str]]] = [
        ('criterion_b_continuity_slos', bool(continuity_slo_pass), ['continuity_slo_pass']),
        ('criterion_c_reconcile_stability', stable_monitored_systems, ['stable_monitored_systems']),
        (
            'criterion_d_evidence_chain_hydration',
            any(name == 'linked_fresh_evidence' and passed for name, passed in checks),
            ['linked_fresh_evidence', 'telemetry_freshness', 'ingestion_freshness', 'detection_pipeline_freshness', 'proof_chain_status'],
        ),
        ('criterion_e_live_action_governance', live_action_capability_readiness, ['validated_live_action_paths']),
        (
            'criterion_f_state_model_ux',
            stable_monitored_systems and bool(continuity_slo_pass),
            ['runtime_status', 'monitoring_status', 'contradiction_flags', 'guard_flags', 'continuity_slo_pass'],
        ),
    ]
    hidden_architecture_pass = all(passed for _, passed, _ in criteria_checks)
    criteria_checks.append(
        (
            'hidden_architecture',
            hidden_architecture_pass,
            ['criterion_b_continuity_slos', 'criterion_c_reconcile_stability', 'criterion_d_evidence_chain_hydration', 'criterion_e_live_action_governance', 'criterion_f_state_model_ux'],
        )
    )
    failed_criteria = [name for name, passed, _ in criteria_checks if not passed]
    return {
        'enterprise_ready_pass': len(failed_checks) == 0,
        'failed_checks': failed_checks,
        'check_results': [{'name': name, 'pass': passed, 'remediation_url': ENTERPRISE_READY_REMEDIATION_LINKS.get(name)} for name, passed in checks],
        'remediation_links': {name: ENTERPRISE_READY_REMEDIATION_LINKS[name] for name in failed_checks if name in ENTERPRISE_READY_REMEDIATION_LINKS},
        'validated_live_action_paths': validated_live_action_paths,
        'enterprise_criteria_pass': len(failed_criteria) == 0,
        'enterprise_criteria_failed': failed_criteria,
        'enterprise_criteria': [
            {
                'name': name,
                'pass': passed,
                'requires_measurable_evidence': True,
                'evidence_basis': evidence_basis,
                'remediation_url': ENTERPRISE_CRITERIA_REMEDIATION_LINKS.get(name),
            }
            for name, passed, evidence_basis in criteria_checks
        ],
    }


def _workspace_coverage_only_state(
    *,
    workspace_id: str,
    cycle_at: datetime,
    provider_reachable: bool,
    coverage_heartbeat_updates: int,
    real_events_detected: int,
) -> dict[str, Any]:
    workspace_key = str(workspace_id or '').strip()
    if not workspace_key:
        return {
            'state': None,
            'active': False,
            'cycle_count': 0,
            'duration_seconds': 0,
            'threshold_seconds': MONITORING_COVERAGE_ONLY_WARNING_SECONDS,
        }
    # Coverage telemetry rows written to telemetry_events (coverage_heartbeat_updates > 0)
    # are live evidence. Only flag the streak when the provider is reachable but nothing
    # at all was persisted — neither real blockchain events nor coverage telemetry.
    condition_met = bool(provider_reachable and int(real_events_detected) <= 0 and int(coverage_heartbeat_updates) <= 0)
    existing = _WORKSPACE_COVERAGE_ONLY_STREAK.get(workspace_key)
    if condition_met:
        if isinstance(existing, dict):
            first_seen_at = _parse_ts(existing.get('first_seen_at')) or cycle_at
            cycle_count = int(existing.get('cycle_count') or 0) + 1
        else:
            first_seen_at = cycle_at
            cycle_count = 1
        duration_seconds = max(0, int((cycle_at - first_seen_at).total_seconds()))
        active = duration_seconds >= MONITORING_COVERAGE_ONLY_WARNING_SECONDS and cycle_count > 1
        next_state = {
            'first_seen_at': first_seen_at.isoformat(),
            'last_cycle_at': cycle_at.isoformat(),
            'cycle_count': cycle_count,
            'duration_seconds': duration_seconds,
            'threshold_seconds': MONITORING_COVERAGE_ONLY_WARNING_SECONDS,
            'active': active,
            'state': 'coverage_only_persistent_no_evidence' if active else None,
        }
        _WORKSPACE_COVERAGE_ONLY_STREAK[workspace_key] = next_state
        return dict(next_state)

    if workspace_key in _WORKSPACE_COVERAGE_ONLY_STREAK:
        _WORKSPACE_COVERAGE_ONLY_STREAK.pop(workspace_key, None)
    return {
        'first_seen_at': None,
        'last_cycle_at': cycle_at.isoformat(),
        'cycle_count': 0,
        'duration_seconds': 0,
        'threshold_seconds': MONITORING_COVERAGE_ONLY_WARNING_SECONDS,
        'active': False,
        'state': None,
    }


def _resolve_target_coverage_state(
    *,
    provider_status: str,
    telemetry_row: dict[str, Any] | None,
    provider_evidence_source: str,
    source_status: str,
) -> tuple[str, datetime | None, str, dict[str, Any]]:
    last_telemetry_at = _parse_ts((telemetry_row or {}).get('observed_at')) if isinstance(telemetry_row, dict) else None
    telemetry_event_id = str((telemetry_row or {}).get('id') or '').strip() if isinstance(telemetry_row, dict) else ''
    telemetry_event_source = str((telemetry_row or {}).get('evidence_source') or '').strip().lower() if isinstance(telemetry_row, dict) else ''
    has_real_telemetry = bool(last_telemetry_at and telemetry_event_id)
    if has_real_telemetry:
        coverage_status = 'reporting'
        evidence_source = telemetry_event_source if telemetry_event_source in {'live', 'simulator', 'replay'} else provider_evidence_source
    else:
        last_telemetry_at = None
        if provider_status == 'no_evidence':
            coverage_status = 'silent'
        elif provider_status in {'live', 'degraded'}:
            coverage_status = 'stale'
        else:
            coverage_status = 'unavailable'
        evidence_source = 'none'
    metadata: dict[str, Any] = {
        'provider_status': provider_status,
        'source_status': source_status,
        'telemetry_basis': (
            {'kind': 'telemetry_event', 'event_id': telemetry_event_id}
            if has_real_telemetry
            else {'kind': 'none'}
        ),
    }
    return coverage_status, last_telemetry_at, evidence_source, metadata


def _resolve_coverage_asset_id(connection: Any, target: dict[str, Any]) -> str | None:
    """Return a safe asset_id for target_coverage_records, verifying it exists in assets.

    targets.asset_id references assets(id).  After migration 0083,
    target_coverage_records.asset_id also references assets(id).  If the asset
    row is missing (e.g. migration not yet applied, or stale FK reference),
    return None so the nullable column receives NULL rather than raising a
    ForeignKeyViolation that would roll back the whole target cycle.
    """
    asset_id = target.get('asset_id')
    if not asset_id:
        return None
    asset_id_str = str(asset_id).strip()
    if not asset_id_str:
        return None
    try:
        row = connection.execute(
            'SELECT id FROM assets WHERE id = %s::uuid LIMIT 1',
            (asset_id_str,),
        ).fetchone()
        if row:
            return asset_id_str
    except Exception:
        pass
    logger.warning(
        'code=TARGET_COVERAGE_ASSET_PARENT_MISSING workspace_id=%s target_id=%s asset_id=%s '
        'action=null_out_coverage_asset_id',
        target.get('workspace_id'),
        target.get('id'),
        asset_id_str,
    )
    return None


def _provider_source_is_live(source_type: Any) -> bool:
    return str(source_type or '').strip().lower() not in NON_LIVE_PROVIDER_SOURCE_TYPES


def _telemetry_event_evidence_source(*, provider_result: ActivityProviderResult, source_type: str) -> str:
    normalized_mode = str(provider_result.mode or '').strip().lower()
    normalized_source = str(source_type or '').strip().lower()
    if provider_result.synthetic or normalized_mode in {'demo', 'simulator'}:
        return 'simulator'
    if normalized_mode == 'replay' or normalized_source == 'replay':
        return 'replay'
    if _provider_source_is_live(normalized_source):
        return 'live'
    return 'simulator'


def _telemetry_idempotency_key(*, workspace_id: Any, target_id: Any, event: ActivityEvent) -> str:
    workspace_part = str(workspace_id or '').strip().lower()
    target_part = str(target_id or '').strip().lower()
    cursor_part = str(event.cursor or '').strip().lower()
    tx_hash = ''
    if isinstance(event.payload, dict):
        tx_hash = str(event.payload.get('tx_hash') or event.payload.get('transaction_hash') or '').strip().lower()
    event_part = cursor_part or tx_hash or hashlib.sha256(
        _json_dumps(event.payload if isinstance(event.payload, dict) else {}).encode('utf-8')
    ).hexdigest()
    return f'{workspace_part}:{target_part}:{event_part}'


_TELEMETRY_EVENT_INSERT_SQL = """
INSERT INTO telemetry_events (
    id, workspace_id, asset_id, target_id, provider_type, event_type, observed_at, evidence_source, payload_hash, payload_json, idempotency_key
)
VALUES (%s::uuid, %s::uuid, %s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s::jsonb, %s)
ON CONFLICT (workspace_id, target_id, idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING
"""

_TELEMETRY_TX_PERSISTED_VERIFY_SQL = """
SELECT COUNT(*) AS c
FROM telemetry_events
WHERE workspace_id = %s::uuid
  AND target_id = %s::uuid
  AND (
    id = %s::uuid
    OR (%s <> '' AND lower(payload_json->>'tx_hash') = lower(%s))
  )
"""


def _persist_raw_wallet_transfer_telemetry(
    connection: Any,
    *,
    telemetry_id: str,
    workspace_id: str,
    asset_id: str | None,
    target_id: str,
    provider_type: str,
    event_type: str,
    observed_at: Any,
    evidence_source: str,
    payload: dict[str, Any],
    idempotency_key: str | None,
) -> bool:
    """Persist a detected wallet transfer as raw live evidence in its own committed transaction.

    A detected wallet transfer is canonical live evidence. It must survive even when the
    downstream threat analysis raises (e.g. ``analysis_unavailable``) and the surrounding
    monitoring transaction rolls back. This helper writes and COMMITS the telemetry row on a
    dedicated connection so the commit is independent of ``connection``'s transaction, then
    verifies the row is durably present (count by target_id + tx_hash). It returns ``True``
    only when persistence is confirmed.

    If the dedicated commit fails (e.g. transient connection error), it falls back to inserting
    on the shared ``connection`` as a best effort so the row is not silently dropped, and
    returns ``False`` to signal the independent commit did not hold.
    """
    safe_payload = payload if isinstance(payload, dict) else {}
    # Acceptance rule: every persisted LIVE wallet-transfer row must carry a
    # canonical detected_by (realtime_websocket / realtime_backfill /
    # realtime_tx_import / stable_rpc_polling / fast-tail). When the caller's
    # payload has no top-level detected_by, resolve it from the payload's own
    # source_type / details / metadata / ingestion facts and stamp it BEFORE the
    # row is written, so the customer-facing "Detected By" column is never blank.
    # Never invented: an unresolvable path stays absent and is logged loudly
    # instead of guessed. Simulator/replay rows are excluded — a demo row must
    # never claim a live detection path (CLAUDE.md truthfulness).
    if (
        evidence_source == 'live'
        and event_type in WALLET_TRANSFER_EVENT_TYPES
        and not safe_payload.get('detected_by')
    ):
        _resolved_detected_by = resolve_telemetry_detected_by(safe_payload)
        if _resolved_detected_by:
            safe_payload['detected_by'] = _resolved_detected_by
        else:
            logger.warning(
                'wallet_transfer_missing_detected_by telemetry_id=%s target_id=%s tx_hash=%s '
                'event_type=%s source_type=%s ingestion_source=%s',
                telemetry_id, target_id,
                str(safe_payload.get('tx_hash') or safe_payload.get('hash') or 'unknown'),
                event_type,
                str(safe_payload.get('source_type') or 'none'),
                str(safe_payload.get('ingestion_source') or 'none'),
            )
    payload_json = _json_dumps(safe_payload)
    payload_hash = hashlib.sha256(payload_json.encode('utf-8')).hexdigest()
    tx_hash = str(safe_payload.get('tx_hash') or safe_payload.get('hash') or '')
    insert_params = (
        telemetry_id,
        workspace_id,
        asset_id,
        target_id,
        provider_type,
        event_type,
        observed_at,
        evidence_source,
        payload_hash,
        payload_json,
        idempotency_key,
    )
    try:
        with pg_connection() as raw_conn:
            raw_conn.execute(_TELEMETRY_EVENT_INSERT_SQL, insert_params)
            raw_conn.commit()
            verify_row = raw_conn.execute(
                _TELEMETRY_TX_PERSISTED_VERIFY_SQL,
                (workspace_id, target_id, telemetry_id, tx_hash, tx_hash),
            ).fetchone()
        persisted = int((verify_row or {}).get('c') or 0) > 0
        logger.info(
            'wallet_transfer_telemetry_committed telemetry_id=%s target_id=%s tx_hash=%s block=%s persisted=%s',
            telemetry_id,
            target_id,
            tx_hash or 'unknown',
            safe_payload.get('block_number'),
            str(persisted).lower(),
        )
        return persisted
    except Exception:
        logger.exception(
            'wallet_transfer_telemetry_commit_failed telemetry_id=%s target_id=%s tx_hash=%s '
            'fallback=shared_connection',
            telemetry_id,
            target_id,
            tx_hash or 'unknown',
        )
        # Best effort: keep the evidence on the shared connection rather than dropping it.
        connection.execute(_TELEMETRY_EVENT_INSERT_SQL, insert_params)
        logger.info(
            'wallet_transfer_telemetry_committed telemetry_id=%s target_id=%s tx_hash=%s persisted=false '
            'durable_commit=false',
            telemetry_id,
            target_id,
            tx_hash or 'unknown',
        )
        return False


def _maybe_persist_ingested_wallet_transfer(
    connection: Any, *, target: dict[str, Any], event: ActivityEvent
) -> str | None:
    """Persist a ``wallet_transfer_detected`` / ``native_transfer`` telemetry row for
    a real-time–ingested wallet transaction.

    The 300 s polling worker persists this row inside ``process_monitoring_target``.
    The real-time worker reaches the DB through ``process_ingested_event`` instead,
    which historically wrote only receipts/detections — so a native ETH transfer the
    realtime worker detected never produced a customer-visible telemetry row, even
    though detection ran. This mirrors the polling worker's event_type selection and
    writes the row on a dedicated committed connection (idempotent via
    idempotency_key, same key shape as polling), so the realtime and polling paths
    converge on one deduped row per tx rather than two.

    Returns the persisted event_type, or None when the event is not a directioned
    wallet transfer.

    Scope: only events that carry an explicit ``wallet_transfer_direction`` (the
    native-ETH events the realtime worker builds) are persisted here. ERC-20 log
    events keep their existing realtime behaviour untouched, so this is a additive
    fix for native transfers, not a change to token-transfer handling.
    """
    payload = event.payload if isinstance(event.payload, dict) else {}
    direction = str(payload.get('wallet_transfer_direction') or '').strip().lower()
    if direction not in {'inbound', 'outbound'}:
        return None
    # Classify against the RESOLVED monitored wallet, not just the raw wallet_address
    # column. Realtime targets frequently carry the monitored address in a fallback
    # location (contract_identifier / the linked asset's identifier / target_metadata);
    # the realtime worker already MATCHED the transfer against that resolved address,
    # so re-deriving the match from wallet_address alone would silently drop a
    # genuinely-matched native transfer (detection ran, but no customer-visible
    # telemetry row appeared). resolve_monitored_wallet is the same canonical resolver
    # the scan and import-tx paths use, so all three stay consistent.
    from services.api.app.evm_activity_provider import resolve_monitored_wallet
    target_wallet = (resolve_monitored_wallet(target) or '').lower()
    ev_from = str(payload.get('from') or '').lower()
    ev_to = str(payload.get('to') or '').lower()
    is_wallet_tx = (
        str(target.get('target_type') or '').lower() == 'wallet'
        and bool(target_wallet)
        and target_wallet in {ev_from, ev_to}
    )
    if not is_wallet_tx:
        return None
    raw_event_type = str(payload.get('event_type') or event.kind or '').lower()
    if raw_event_type == 'transaction':
        event_type = 'native_transfer'
    else:
        event_type = 'wallet_transfer_detected'
    idempotency_key = _telemetry_idempotency_key(
        workspace_id=target.get('workspace_id'), target_id=target.get('id'), event=event,
    )
    telem_id = str(uuid.uuid4())
    detected_by = str(payload.get('detected_by') or event.ingestion_source or 'realtime')
    # Stamp the resolved tag INTO the persisted payload (not just the log line
    # below): event.ingestion_source lives on the ActivityEvent object, so a
    # payload that lacks its own detection markers would otherwise persist bare
    # and render a customer-facing "Unknown".
    if not payload.get('detected_by') and event.ingestion_source:
        payload['detected_by'] = worker_status_detected_by(event.ingestion_source)
    provider_name = str(
        (payload.get('metadata') or {}).get('provider_name')
        if isinstance(payload.get('metadata'), dict) else ''
    ) or str(event.ingestion_source or 'realtime_websocket')
    _persist_raw_wallet_transfer_telemetry(
        connection,
        telemetry_id=telem_id,
        workspace_id=str(target['workspace_id']),
        asset_id=str(target.get('asset_id')) if target.get('asset_id') else None,
        target_id=str(target['id']),
        provider_type=provider_name,
        event_type=event_type,
        observed_at=event.observed_at,
        evidence_source='live',
        payload=payload,
        idempotency_key=idempotency_key,
    )
    logger.info(
        'wallet_transfer_detected target_id=%s tx_hash=%s detected_by=%s event_type=%s telemetry_id=%s',
        target.get('id'), str(payload.get('tx_hash') or 'unknown'), detected_by, event_type, telem_id,
    )
    logger.info(
        'realtime_event_persisted tx_hash=%s target_id=%s detected_by=%s',
        str(payload.get('tx_hash') or 'unknown'), target.get('id'), detected_by,
    )
    return event_type


def _wallet_transfer_smoke_alert(
    *,
    workspace_id: str,
    user_id: str,
    target_id: str,
    target_name: str,
    payload: dict[str, Any],
    evidence_source: str,
    telemetry_id: str | None = None,
    monitored_system_id: str | None = None,
    protected_asset_id: str | None = None,
) -> str | None:
    """Create a detection + low/info alert for every live wallet_transfer_detected event.

    Uses a dedicated pg_connection() so the detection and alert are committed
    independently of the surrounding monitoring transaction. This guarantees the
    evidence survives even when downstream threat-engine analysis raises.

    Never fires on simulator, replay, or demo evidence — only 'live' evidence
    creates detections/alerts through this rule. Does NOT create an incident.
    """
    if evidence_source != 'live':
        return None
    tx_hash = str(payload.get('tx_hash') or payload.get('hash') or '')
    from_address = str(payload.get('from') or payload.get('owner') or '')
    to_address = str(payload.get('to') or '')
    amount_wei = str(payload.get('value') or payload.get('amount_wei') or payload.get('amount') or '0')
    chain_id = payload.get('chain_id')
    block_number = payload.get('block_number')
    direction = str(payload.get('wallet_transfer_direction') or 'unknown')
    explanation = (
        f'Wallet transfer detected on chain {chain_id}: '
        f'{from_address[:10]}…→{to_address[:10]}… '
        f'({direction}) block={block_number}'
    )
    response: dict[str, Any] = {
        'severity': 'critical',
        'confidence': 'high',
        'detection_type': 'monitored_wallet_transfer',
        'recommended_action': 'review_wallet_transfer',
        'explanation': explanation,
        'matched_patterns': [
            {'label': 'wallet_transfer_detected', 'rule_id': 'smoke_wallet_transfer', 'severity': 'critical'}
        ],
        'reasons': ['wallet_transfer_detected'],
        'source': 'live',
        'degraded': False,
        'evidence_source': evidence_source,
        'tx_hash': tx_hash,
        'from_address': from_address,
        'to_address': to_address,
        'amount_wei': amount_wei,
        'chain_id': chain_id,
        'block_number': block_number,
        'telemetry_id': telemetry_id,
        'target_id': target_id,
    }
    # Detection ID uses narrow seed so existing detection rows remain findable.
    _detection_seed = json.dumps(
        {'target_id': target_id, 'tx_hash': tx_hash, 'rule': 'smoke_wallet_transfer'},
        sort_keys=True,
    )
    smoke_detection_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f'detection:{_detection_seed}'))
    # Alert signature includes workspace_id + chain_id: workspace_id + target_id + chain_id + tx_hash + rule_key.
    signature = _smoke_dedupe_signature(
        workspace_id=workspace_id, target_id=target_id, chain_id=chain_id, tx_hash=tx_hash,
    )
    title = f'Monitored wallet transfer detected: {target_name} (chain {chain_id})'
    raw_evidence = {
        'event_type': 'wallet_transfer_detected',
        'detection_type': 'monitored_wallet_transfer',
        'tx_hash': tx_hash,
        'from_address': from_address,
        'to_address': to_address,
        'amount_wei': amount_wei,
        'chain_id': chain_id,
        'block_number': block_number,
        'evidence_source': evidence_source,
        'telemetry_id': telemetry_id,
        'target_id': target_id,
    }
    try:
        with pg_connection() as conn:
            # Step 1: insert a committed monitoring_run BEFORE the detection so the FK
            # constraint (detections.monitoring_run_id → monitoring_runs.id) is satisfied
            # within this transaction even on a fresh connection.
            smoke_run_id = str(uuid.uuid4())
            conn.execute(
                '''
                INSERT INTO monitoring_runs (id, workspace_id, status, trigger_type, notes)
                VALUES (%s::uuid, %s::uuid, 'completed', 'smoke_rule', %s)
                ''',
                (smoke_run_id, workspace_id, f'smoke_wallet_transfer target_id={target_id} tx={tx_hash[:20]}'),
            )
            # Step 2: insert detection — ON CONFLICT DO NOTHING provides tx_hash idempotency
            # (smoke_detection_id is UUID5-deterministic on target_id+tx_hash+rule).
            det_cur = conn.execute(
                '''
                INSERT INTO detections (
                    id, workspace_id, monitored_system_id, protected_asset_id,
                    detection_type, severity, confidence, title, evidence_summary,
                    evidence_source, source_rule, status, detected_at,
                    raw_evidence_json, monitoring_run_id, linked_alert_id,
                    created_at, updated_at
                )
                VALUES (
                    %s::uuid, %s::uuid, %s::uuid, %s::uuid,
                    %s, %s, %s, %s, %s,
                    %s, %s, \'open\', NOW(),
                    %s::jsonb, %s::uuid, NULL,
                    NOW(), NOW()
                )
                ON CONFLICT (id) DO NOTHING
                ''',
                (
                    smoke_detection_id, workspace_id,
                    monitored_system_id or None, protected_asset_id or None,
                    'monitored_wallet_transfer', 'critical', 1.0, title, explanation,
                    evidence_source, 'smoke_wallet_transfer', _json_dumps(raw_evidence),
                    smoke_run_id,
                ),
            )
            if det_cur.rowcount == 0:
                # Detection already exists. Commit the monitoring_run, then check whether a
                # linked alert was created. If the alert is missing (e.g. it rolled back on a
                # prior poll), create it now — this is the recovery / backfill path.
                conn.commit()
                det_row = conn.execute(
                    'SELECT linked_alert_id FROM detections WHERE id = %s::uuid',
                    (smoke_detection_id,),
                ).fetchone()
                existing_alert_id = (
                    str(det_row['linked_alert_id'])
                    if (det_row and det_row.get('linked_alert_id'))
                    else None
                )
                if existing_alert_id:
                    logger.info(
                        'wallet_transfer_alert_skipped_duplicate workspace_id=%s target_id=%s '
                        'detection_id=%s tx_hash=%s reason=alert_already_linked alert_id=%s',
                        workspace_id, target_id, smoke_detection_id, tx_hash or 'unknown', existing_alert_id,
                    )
                    return existing_alert_id
                # Detection exists but has no linked alert — recover by creating it now.
                logger.info(
                    'wallet_transfer_alert_recovery workspace_id=%s target_id=%s '
                    'detection_id=%s tx_hash=%s reason=detection_exists_no_alert',
                    workspace_id, target_id, smoke_detection_id, tx_hash or 'unknown',
                )
                response['monitoring_run_id'] = smoke_run_id
                recovery_alert_id = _upsert_alert(
                    conn,
                    workspace_id=workspace_id,
                    user_id=user_id,
                    target_id=target_id,
                    analysis_run_id=None,
                    title=title,
                    response=response,
                    signature=signature,
                    detection_id=smoke_detection_id,
                )
                if recovery_alert_id:
                    conn.execute(
                        "UPDATE detections SET linked_alert_id = %s::uuid, status = 'escalated', updated_at = NOW() WHERE id = %s::uuid",
                        (recovery_alert_id, smoke_detection_id),
                    )
                conn.commit()
                if recovery_alert_id:
                    logger.info(
                        'wallet_transfer_alert_recovered workspace_id=%s target_id=%s '
                        'detection_id=%s alert_id=%s tx_hash=%s',
                        workspace_id, target_id, smoke_detection_id, recovery_alert_id, tx_hash or 'unknown',
                    )
                return recovery_alert_id or None
            logger.info(
                'wallet_transfer_detection_created workspace_id=%s target_id=%s '
                'detection_id=%s monitoring_run_id=%s tx_hash=%s chain_id=%s block=%s',
                workspace_id, target_id, smoke_detection_id, smoke_run_id,
                tx_hash or 'unknown', chain_id, block_number,
            )
            # Step 3: create or dedup the alert.
            # analysis_run_id is NULL for live smoke-rule alerts: no analysis_runs row
            # exists for this path, and the FK allows NULL (ON DELETE SET NULL).
            # Add monitoring_run_id to the payload so the alert links back to the
            # monitoring_runs row (satisfying requirement 4 for the evidence chain).
            response['monitoring_run_id'] = smoke_run_id
            alert_id = _upsert_alert(
                conn,
                workspace_id=workspace_id,
                user_id=user_id,
                target_id=target_id,
                analysis_run_id=None,
                title=title,
                response=response,
                signature=signature,
                detection_id=smoke_detection_id,
            )
            if alert_id:
                conn.execute(
                    "UPDATE detections SET linked_alert_id = %s::uuid, status = 'escalated', updated_at = NOW() WHERE id = %s::uuid",
                    (alert_id, smoke_detection_id),
                )
            conn.commit()
        if alert_id:
            logger.info(
                'wallet_transfer_alert_created workspace_id=%s target_id=%s '
                'detection_id=%s alert_id=%s tx_hash=%s chain_id=%s block=%s evidence_source=%s',
                workspace_id, target_id, smoke_detection_id, alert_id,
                tx_hash or 'unknown', chain_id, block_number, evidence_source,
            )
        return alert_id or None
    except Exception as exc:
        logger.exception(
            'wallet_transfer_alert_failed workspace_id=%s target_id=%s tx_hash=%s sql_error=%s',
            workspace_id, target_id, tx_hash or 'unknown', str(exc),
        )
        return None


# ---------------------------------------------------------------------------
# Rule: Strategic Infrastructure Guard — outbound ETH from Base wallet
# ---------------------------------------------------------------------------

_SIG_RULE_KEY = 'strategic_infrastructure_guard_wallet_outbound_transfer'
_SIG_ALERT_TITLE = 'Strategic Infrastructure Guard: Treasury RWA Control Wallet Movement Detected'
_SIG_ALERT_REASON = 'Outbound ETH movement from a wallet classified as Treasury RWA operational infrastructure.'
_SMOKE_RULE_KEY = 'smoke_wallet_transfer'


def _sig_dedupe_signature(*, workspace_id: str, target_id: str, chain_id: Any, tx_hash: str) -> str:
    """Canonical Strategic Infrastructure Guard alert dedupe key.

    Key = workspace_id + target_id + chain_id + tx_hash + rule_key.

    tx_hash is part of the key, so two different transactions from the same
    wallet/target/rule always produce two different signatures. Alerts are NEVER
    collapsed by target_id alone, target_id + rule_key, target_id + event_type,
    or wallet address alone. Different tx_hash = different alert.
    """
    seed = json.dumps(
        {
            'workspace_id': str(workspace_id),
            'target_id': str(target_id),
            'chain_id': int(chain_id or 0),
            'tx_hash': str(tx_hash or ''),
            'rule': _SIG_RULE_KEY,
        },
        sort_keys=True,
    )
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


def _smoke_dedupe_signature(*, workspace_id: str, target_id: str, chain_id: Any, tx_hash: str) -> str:
    """Smoke-rule wallet-transfer alert dedupe key.

    Same construction as the SIG key (workspace_id + target_id + chain_id +
    tx_hash + rule_key) but scoped to the direction-agnostic smoke rule, which
    fires for every live wallet transfer. tx_hash keeps each transaction
    distinct so inbound/outbound rows never share an alert.
    """
    seed = json.dumps(
        {
            'workspace_id': str(workspace_id),
            'target_id': str(target_id),
            'chain_id': str(chain_id or ''),
            'tx_hash': str(tx_hash or ''),
            'rule': _SMOKE_RULE_KEY,
        },
        sort_keys=True,
    )
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


def _strategic_infrastructure_guard_alert(
    *,
    workspace_id: str,
    user_id: str,
    target_id: str,
    target_name: str,
    target_wallet_address: str,
    payload: dict[str, Any],
    evidence_source: str,
    telemetry_id: str | None = None,
    monitored_system_id: str | None = None,
    protected_asset_id: str | None = None,
) -> str | None:
    """Create a Critical alert for outbound ETH transfers from monitored Base chain wallets.

    Fires only when ALL of the following are true:
    - evidence_source == 'live'
    - chain_id == 8453 (Base mainnet)
    - from_address == target_wallet_address (outbound transfer)
    - tx_hash is present (no fake or demo alerts)
    - value > 0 when value field is present

    Uses UUID5-deterministic IDs for detection and dedup signature, so re-polling
    the same tx_hash never creates duplicate alerts or detections.
    Never fires on simulator, replay, or demo evidence.
    """
    if evidence_source != 'live':
        return None

    tx_hash = str(payload.get('tx_hash') or payload.get('hash') or '').strip()
    if not tx_hash:
        return None

    chain_id = payload.get('chain_id')
    if int(chain_id or 0) != 8453:
        return None

    from_address = str(payload.get('from') or payload.get('owner') or '').strip().lower()
    target_addr = str(target_wallet_address or '').strip().lower()
    if not target_addr or from_address != target_addr:
        return None

    _raw_value = payload.get('value') or payload.get('amount_wei') or payload.get('amount')
    if _raw_value is not None:
        try:
            if int(_raw_value) == 0:
                return None
        except (ValueError, TypeError):
            pass

    to_address = str(payload.get('to') or '').strip()
    amount_wei = str(payload.get('value') or payload.get('amount_wei') or payload.get('amount') or '0')
    block_number = payload.get('block_number')

    # Detection ID seed kept stable so existing detection rows remain findable.
    _detection_seed = json.dumps(
        {'target_id': target_id, 'tx_hash': tx_hash, 'rule': _SIG_RULE_KEY, 'chain_id': 8453},
        sort_keys=True,
    )
    sig_detection_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f'detection:{_detection_seed}'))
    # Alert signature: workspace_id + target_id + chain_id + tx_hash + rule_key.
    # tx_hash is in the key, so a different transaction never collapses into an
    # existing alert (different tx_hash = different alert).
    signature = _sig_dedupe_signature(
        workspace_id=workspace_id, target_id=target_id, chain_id=8453, tx_hash=tx_hash,
    )

    raw_evidence = {
        'evidence_type': 'live_onchain_transaction',
        'event_type': 'wallet_transfer_detected',
        'source': 'rpc_polling',
        'detection_type': 'strategic_infrastructure_guard_outbound_transfer',
        'tx_hash': tx_hash,
        'from_address': from_address,
        'to_address': to_address,
        'value_wei': amount_wei,
        'chain_id': chain_id,
        'block_number': block_number,
        'evidence_source': evidence_source,
        'telemetry_id': telemetry_id,
        'target_id': target_id,
        'asset_classification': 'rwa_treasury_control_wallet',
        'program': 'Strategic Infrastructure Guard',
    }
    response: dict[str, Any] = {
        'severity': 'critical',
        'confidence': 'high',
        'detection_type': 'strategic_infrastructure_guard_outbound_transfer',
        'recommended_action': 'review_wallet_transfer',
        'explanation': _SIG_ALERT_REASON,
        'matched_patterns': [
            {
                'label': _SIG_RULE_KEY,
                'rule_id': _SIG_RULE_KEY,
                'severity': 'critical',
            }
        ],
        'reasons': [_SIG_ALERT_REASON],
        'source': 'rpc_polling',
        'degraded': False,
        'evidence_source': evidence_source,
        'tx_hash': tx_hash,
        'from_address': from_address,
        'to_address': to_address,
        'value_wei': amount_wei,
        'chain_id': chain_id,
        'block_number': block_number,
        'telemetry_id': telemetry_id,
        'target_id': target_id,
        'evidence_type': 'live_onchain_transaction',
        'asset_classification': 'rwa_treasury_control_wallet',
        'program': 'Strategic Infrastructure Guard',
        'rule_key': _SIG_RULE_KEY,
    }
    try:
        with pg_connection() as conn:
            sig_run_id = str(uuid.uuid4())
            conn.execute(
                '''
                INSERT INTO monitoring_runs (id, workspace_id, status, trigger_type, notes)
                VALUES (%s::uuid, %s::uuid, 'completed', 'sig_rule', %s)
                ''',
                (sig_run_id, workspace_id, f'{_SIG_RULE_KEY} target_id={target_id} tx={tx_hash[:20]}'),
            )
            det_cur = conn.execute(
                '''
                INSERT INTO detections (
                    id, workspace_id, monitored_system_id, protected_asset_id,
                    detection_type, severity, confidence, title, evidence_summary,
                    evidence_source, source_rule, status, detected_at,
                    raw_evidence_json, monitoring_run_id, linked_alert_id,
                    created_at, updated_at
                )
                VALUES (
                    %s::uuid, %s::uuid, %s::uuid, %s::uuid,
                    %s, %s, %s, %s, %s,
                    %s, %s, \'open\', NOW(),
                    %s::jsonb, %s::uuid, NULL,
                    NOW(), NOW()
                )
                ON CONFLICT (id) DO NOTHING
                ''',
                (
                    sig_detection_id, workspace_id,
                    monitored_system_id or None, protected_asset_id or None,
                    'strategic_infrastructure_guard_outbound_transfer', 'critical', 1.0,
                    _SIG_ALERT_TITLE, _SIG_ALERT_REASON,
                    evidence_source, _SIG_RULE_KEY, _json_dumps(raw_evidence),
                    sig_run_id,
                ),
            )
            if det_cur.rowcount == 0:
                # Detection already exists. Check if a linked alert was created.
                # If alert is missing (prior rollback), create it now — recovery path.
                conn.commit()
                det_row = conn.execute(
                    'SELECT linked_alert_id FROM detections WHERE id = %s::uuid',
                    (sig_detection_id,),
                ).fetchone()
                existing_alert_id = (
                    str(det_row['linked_alert_id'])
                    if (det_row and det_row.get('linked_alert_id'))
                    else None
                )
                if existing_alert_id:
                    logger.info(
                        'strategic_guard_alert_deduped workspace_id=%s target_id=%s '
                        'detection_id=%s tx_hash=%s dedupe_key=%s reason=alert_already_linked alert_id=%s',
                        workspace_id, target_id, sig_detection_id, tx_hash, signature, existing_alert_id,
                    )
                    return existing_alert_id
                # Detection exists but no alert — recover.
                logger.info(
                    'sig_alert_recovery workspace_id=%s target_id=%s '
                    'detection_id=%s tx_hash=%s reason=detection_exists_no_alert',
                    workspace_id, target_id, sig_detection_id, tx_hash,
                )
                response['monitoring_run_id'] = sig_run_id
                recovery_alert_id = _upsert_alert(
                    conn,
                    workspace_id=workspace_id,
                    user_id=user_id,
                    target_id=target_id,
                    analysis_run_id=None,
                    title=_SIG_ALERT_TITLE,
                    response=response,
                    signature=signature,
                    detection_id=sig_detection_id,
                    module_key='strategic_infrastructure_guard',
                )
                if recovery_alert_id:
                    conn.execute(
                        "UPDATE detections SET linked_alert_id = %s::uuid, status = 'escalated', updated_at = NOW() WHERE id = %s::uuid",
                        (recovery_alert_id, sig_detection_id),
                    )
                conn.commit()
                if recovery_alert_id:
                    logger.info(
                        'strategic_guard_alert_recovered workspace_id=%s target_id=%s '
                        'detection_id=%s alert_id=%s tx_hash=%s dedupe_key=%s',
                        workspace_id, target_id, sig_detection_id, recovery_alert_id, tx_hash, signature,
                    )
                return recovery_alert_id or None
            logger.info(
                'sig_detection_created workspace_id=%s target_id=%s '
                'detection_id=%s monitoring_run_id=%s tx_hash=%s chain_id=%s block=%s',
                workspace_id, target_id, sig_detection_id, sig_run_id,
                tx_hash, chain_id, block_number,
            )
            response['monitoring_run_id'] = sig_run_id
            alert_id = _upsert_alert(
                conn,
                workspace_id=workspace_id,
                user_id=user_id,
                target_id=target_id,
                analysis_run_id=None,
                title=_SIG_ALERT_TITLE,
                response=response,
                signature=signature,
                detection_id=sig_detection_id,
                module_key='strategic_infrastructure_guard',
            )
            if alert_id:
                conn.execute(
                    "UPDATE detections SET linked_alert_id = %s::uuid, status = 'escalated', updated_at = NOW() WHERE id = %s::uuid",
                    (alert_id, sig_detection_id),
                )
            conn.commit()
        if alert_id:
            logger.info(
                'strategic_guard_alert_created workspace_id=%s target_id=%s '
                'detection_id=%s alert_id=%s tx_hash=%s chain_id=%s block=%s evidence_source=%s dedupe_key=%s',
                workspace_id, target_id, sig_detection_id, alert_id,
                tx_hash, chain_id, block_number, evidence_source, signature,
            )
        return alert_id or None
    except Exception as exc:
        logger.exception(
            'sig_alert_failed workspace_id=%s target_id=%s tx_hash=%s sql_error=%s',
            workspace_id, target_id, tx_hash, str(exc),
        )
        return None


def set_background_loop_health(
    *,
    loop_running: bool,
    last_successful_cycle: str | None = None,
    consecutive_failures: int | None = None,
    next_retry_at: str | None = None,
    backoff_seconds: int | None = None,
) -> dict[str, Any]:
    if last_successful_cycle is not None:
        BACKGROUND_LOOP_HEALTH['last_successful_cycle'] = str(last_successful_cycle)
    if consecutive_failures is not None:
        BACKGROUND_LOOP_HEALTH['consecutive_failures'] = max(0, int(consecutive_failures))
    BACKGROUND_LOOP_HEALTH['loop_running'] = bool(loop_running)
    BACKGROUND_LOOP_HEALTH['next_retry_at'] = str(next_retry_at) if next_retry_at else None
    BACKGROUND_LOOP_HEALTH['backoff_seconds'] = int(backoff_seconds) if isinstance(backoff_seconds, (int, float)) else None
    BACKGROUND_LOOP_HEALTH['updated_at'] = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    return dict(BACKGROUND_LOOP_HEALTH)


def get_background_loop_health() -> dict[str, Any]:
    return dict(BACKGROUND_LOOP_HEALTH)


def _normalize_detection_evidence_source(*, ingestion_source: Any, analysis_source: Any, ingestion_mode: Any) -> str:
    normalized_ingestion_source = str(ingestion_source or '').strip().lower()
    normalized_analysis_source = str(analysis_source or '').strip().lower()
    normalized_ingestion_mode = str(ingestion_mode or '').strip().lower()
    ingestion_source = normalized_ingestion_source
    if (
        ingestion_source in {'demo', 'simulator'}
        or ingestion_source == 'synthetic'
        or normalized_analysis_source in {'demo', 'simulator', 'fallback', 'replay'}
        or normalized_ingestion_mode in {'demo', 'simulator'}
    ):
        return 'simulator'
    return 'live'


def _normalize_monitoring_runtime_contract(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload or {})
    summary_payload = normalized.get('workspace_monitoring_summary')
    summary = dict(summary_payload) if isinstance(summary_payload, dict) else {}
    _summary_has_monitoring_content = bool(summary)

    configuration_reason = normalized.get('configuration_reason')
    if not configuration_reason:
        configuration_reason = summary.get('configuration_reason')
    normalized['configuration_reason'] = configuration_reason

    configuration_reason_codes = normalized.get('configuration_reason_codes')
    if not isinstance(configuration_reason_codes, list):
        configuration_reason_codes = summary.get('configuration_reason_codes')
    if not isinstance(configuration_reason_codes, list):
        configuration_diagnostics = summary.get('configuration_diagnostics')
        if isinstance(configuration_diagnostics, dict):
            configuration_reason_codes = configuration_diagnostics.get('reason_codes')
    if not isinstance(configuration_reason_codes, list):
        configuration_reason_codes = [str(configuration_reason)] if configuration_reason else []
    configuration_reason_codes = [str(code) for code in configuration_reason_codes if str(code).strip()]

    count_reason_codes = normalized.get('count_reason_codes')
    if not isinstance(count_reason_codes, dict):
        count_reason_codes = summary.get('count_reason_codes')
    if not isinstance(count_reason_codes, dict):
        count_reason_codes = {}
    count_reason_codes = dict(count_reason_codes)

    for key in PREREQUISITE_COUNTER_KEYS:
        value = normalized.get(key, summary.get(key, 0))
        try:
            normalized_value = int(value or 0)
        except Exception:
            normalized_value = 0
        normalized[key] = normalized_value
        summary[key] = normalized_value
        if key not in count_reason_codes and isinstance(summary.get('count_reason_codes'), dict):
            inherited_reason = summary['count_reason_codes'].get(key)
            if inherited_reason:
                count_reason_codes[key] = inherited_reason

    field_reason_codes = normalized.get('field_reason_codes')
    if not isinstance(field_reason_codes, dict):
        field_reason_codes = summary.get('field_reason_codes')
    if not isinstance(field_reason_codes, dict):
        field_reason_codes = {}
    field_reason_codes = dict(field_reason_codes)

    required_runtime_fields = {
        'proof_chain_status': 'unavailable',
        'proof_chain_correlation_id': None,
        'evidence_source': summary.get('evidence_source') or 'none',
        'status_reason': normalized.get('status_reason'),
        'contradiction_flags': [],
        'enterprise_ready_pass': False,
        'failed_checks': [],
    }
    for field_name, default_value in required_runtime_fields.items():
        if field_name not in normalized:
            normalized[field_name] = default_value

    passthrough_summary_keys = (
        'workspace_configured',
        'status_reason',
        'valid_protected_assets',
        'linked_monitored_systems',
        'enabled_configs',
        'valid_link_count',
        'configured_systems',
        'reporting_systems',
        'last_poll_at',
        'last_heartbeat_at',
        'last_coverage_telemetry_at',
        'last_telemetry_at',
        'telemetry_kind',
        'evidence_source',
        'confidence_status',
        'continuity_slo_pass',
        'heartbeat_age_seconds',
        'telemetry_age_seconds',
        'event_ingestion_age_seconds',
        'detection_age_seconds',
        'detection_pipeline_age_seconds',
        'detection_eval_age_seconds',
        'heartbeat_threshold_seconds',
        'telemetry_threshold_seconds',
        'event_ingestion_threshold_seconds',
        'detection_threshold_seconds',
        'thresholds_seconds',
        'required_thresholds_seconds',
        'continuity_thresholds_seconds',
        'runtime_degraded_reason_codes',
        'runtime_status_reason_codes',
        'runtime_status_summary',
        'configuration_diagnostics',
    )
    for key in passthrough_summary_keys:
        if key not in normalized and key in summary:
            normalized[key] = summary.get(key)
    if 'runtime_status_summary' not in normalized:
        normalized['runtime_status_summary'] = summary.get('runtime_status') or 'offline'
    if not isinstance(normalized.get('configuration_diagnostics'), dict):
        normalized['configuration_diagnostics'] = _workspace_configuration_diagnostics(
            valid_protected_asset_count=int(normalized.get('valid_protected_assets') or 0),
            linked_monitored_system_count=int(normalized.get('linked_monitored_systems') or 0),
            persisted_enabled_config_count=int(normalized.get('enabled_configs') or 0),
            valid_target_system_link_count=int(normalized.get('valid_link_count') or 0),
        )

    normalized['count_reason_codes'] = count_reason_codes
    normalized['configuration_reason_codes'] = configuration_reason_codes
    normalized['field_reason_codes'] = field_reason_codes
    if not normalized.get('runtime_status'):
        normalized['runtime_status'] = normalized.get('runtime_status_summary') or summary.get('runtime_status') or 'offline'
    if not normalized.get('summary_generated_at'):
        normalized['summary_generated_at'] = utc_now().isoformat()
    summary['count_reason_codes'] = dict(count_reason_codes)
    summary_field_reason_codes = dict(field_reason_codes)
    if _summary_has_monitoring_content:
        for _frc_key in ('protected_assets', 'configured_systems', 'reporting_systems', 'last_poll_at', 'last_heartbeat_at', 'last_telemetry_at'):
            summary_field_reason_codes.setdefault(_frc_key, [])
    summary['field_reason_codes'] = summary_field_reason_codes
    if 'evidence_source' not in summary:
        summary['evidence_source'] = normalized.get('evidence_source') or 'none'
    normalized['workspace_monitoring_summary'] = summary
    return normalized


WORKER_STATE: dict[str, Any] = {
    'worker_name': os.getenv('MONITORING_WORKER_NAME', 'monitoring-worker'),
    'worker_running': False,
    'last_cycle_at': None,
    'last_cycle_due_targets': 0,
    'last_cycle_targets_checked': 0,
    'last_cycle_alerts_generated': 0,
    'last_error': None,
    'ingestion_mode': None,
    'degraded': False,
    'metrics': {
        'live_events_ingested': 0,
        'analysis_failures': 0,
        'degraded_runs': 0,
    },
}

MONITORING_RUN_TRIGGER_TYPES: set[str] = {'scheduler', 'system', 'manual', 'bootstrap'}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_monitoring_run_trigger_type(value: Any) -> str:
    normalized = str(value or '').strip().lower()
    if normalized in MONITORING_RUN_TRIGGER_TYPES:
        return normalized
    return 'scheduler'


def _runtime_status_debug_enabled() -> bool:
    app_env = os.getenv('APP_ENV', os.getenv('APP_MODE', 'development')).strip().lower()
    return app_env not in {'production', 'prod'}


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace('Z', '+00:00'))
    except ValueError:
        return None


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    if percentile <= 0:
        return min(values)
    if percentile >= 100:
        return max(values)
    ordered = sorted(values)
    rank = (len(ordered) - 1) * (percentile / 100.0)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    if lower == upper:
        return ordered[lower]
    weight = rank - lower
    return ordered[lower] + ((ordered[upper] - ordered[lower]) * weight)


def _latency_alert_state(*, workspace_key: str, metric: str, breached: bool) -> tuple[bool, int, int]:
    history = RUNTIME_STATUS_ALERT_BREACH_HISTORY[workspace_key][metric]
    history.append(bool(breached))
    breach_count = sum(1 for item in history if item)
    total_samples = len(history)
    sustained = (
        total_samples >= RUNTIME_STATUS_ALERT_WINDOW_SAMPLES
        and breach_count >= min(RUNTIME_STATUS_ALERT_REQUIRED_BREACHES, RUNTIME_STATUS_ALERT_WINDOW_SAMPLES)
    )
    return sustained, breach_count, total_samples


def _compute_mttd_seconds(*, observed_at: datetime, detected_at: datetime) -> int:
    return max(0, int((detected_at - observed_at).total_seconds()))


def _workspace_configuration_truth(
    *,
    valid_protected_asset_count: int,
    linked_monitored_system_count: int,
    persisted_enabled_config_count: int,
    valid_target_system_link_count: int,
) -> tuple[bool, str | None]:
    diagnostics = _workspace_configuration_diagnostics(
        valid_protected_asset_count=valid_protected_asset_count,
        linked_monitored_system_count=linked_monitored_system_count,
        persisted_enabled_config_count=persisted_enabled_config_count,
        valid_target_system_link_count=valid_target_system_link_count,
    )
    return bool(diagnostics.get('workspace_configured')), diagnostics.get('configuration_reason')


def _workspace_configuration_diagnostics(
    *,
    valid_protected_asset_count: int,
    linked_monitored_system_count: int,
    persisted_enabled_config_count: int,
    valid_target_system_link_count: int,
) -> dict[str, Any]:
    normalized_valid_assets = max(int(valid_protected_asset_count), 0)
    normalized_linked_systems = max(int(linked_monitored_system_count), 0)
    normalized_persisted_configs = max(int(persisted_enabled_config_count), 0)
    normalized_valid_links = max(int(valid_target_system_link_count), 0)
    reason_codes: list[str] = []
    if normalized_valid_assets <= 0:
        reason_codes.append('no_valid_protected_assets')
    if normalized_linked_systems <= 0:
        reason_codes.append('no_linked_monitored_systems')
    if normalized_persisted_configs <= 0:
        reason_codes.append('no_persisted_enabled_monitoring_config')
    if normalized_valid_links <= 0:
        reason_codes.append('target_system_linkage_invalid')
    return {
        'valid_protected_assets': normalized_valid_assets,
        'linked_monitored_systems': normalized_linked_systems,
        'enabled_configs': normalized_persisted_configs,
        'valid_link_count': normalized_valid_links,
        'workspace_configured': len(reason_codes) == 0,
        'configuration_reason': reason_codes[0] if reason_codes else None,
        'reason_codes': reason_codes,
    }


def _record_detection_metric(
    connection: Any,
    *,
    workspace_id: str,
    alert_id: str,
    incident_id: str | None,
    target_id: str,
    asset_id: str | None,
    event: ActivityEvent,
    response: dict[str, Any],
    policy_snapshot_hash: str,
) -> None:
    payload = event.payload if isinstance(event.payload, dict) else {}
    metadata = payload.get('metadata') if isinstance(payload.get('metadata'), dict) else {}
    evidence = {
        'tx_hash': payload.get('tx_hash'),
        'block_number': payload.get('block_number'),
        'log_index': payload.get('log_index'),
        'ingestion_source': event.ingestion_source,
        'detector_family': response.get('detector_family') or response.get('detection_family'),
        'policy_snapshot_hash': policy_snapshot_hash,
        'truthfulness_state': ((response.get('metadata') or {}).get('truthfulness_state') if isinstance(response.get('metadata'), dict) else None) or 'not_claim_safe',
        'provider_name': metadata.get('provider_name'),
        'event_id': event.event_id,
        'event_cursor': event.cursor,
    }
    detected_at = utc_now()
    connection.execute(
        '''
        INSERT INTO detection_metrics (
            id, workspace_id, alert_id, incident_id, target_id, asset_id,
            event_observed_at, detected_at, mttd_seconds, evidence, created_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, NOW())
        ''',
        (
            str(uuid.uuid4()),
            workspace_id,
            alert_id,
            incident_id,
            target_id,
            asset_id,
            event.observed_at,
            detected_at,
            _compute_mttd_seconds(observed_at=event.observed_at, detected_at=detected_at),
            _json_dumps(evidence),
        ),
    )


def _persist_evidence(
    connection: Any,
    *,
    workspace_id: str,
    target: dict[str, Any],
    event: ActivityEvent,
    response: dict[str, Any],
    alert_id: str | None,
) -> str:
    payload = event.payload if isinstance(event.payload, dict) else {}
    counterparty = payload.get('to') or payload.get('spender') or payload.get('from')
    evidence_id = str(uuid.uuid4())
    connection.execute(
        '''
        INSERT INTO evidence (
            id, workspace_id, asset_id, target_id, alert_id, chain, block_number, tx_hash, log_index, event_type,
            monitored_system_id, severity, risk_score, summary, counterparty, amount_text, token_address, contract_address, source_provider,
            raw_payload_json, observed_at, created_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, NOW())
        ON CONFLICT (target_id, tx_hash, log_index, event_type)
        DO UPDATE SET
            alert_id = COALESCE(EXCLUDED.alert_id, evidence.alert_id),
            severity = EXCLUDED.severity,
            risk_score = EXCLUDED.risk_score,
            summary = EXCLUDED.summary,
            counterparty = EXCLUDED.counterparty,
            amount_text = EXCLUDED.amount_text,
            token_address = EXCLUDED.token_address,
            contract_address = EXCLUDED.contract_address,
            source_provider = EXCLUDED.source_provider,
            raw_payload_json = EXCLUDED.raw_payload_json
        RETURNING id
        ''',
        (
            evidence_id,
            workspace_id,
            target.get('asset_id'),
            target['id'],
            alert_id,
            target.get('chain_network'),
            payload.get('block_number'),
            payload.get('tx_hash'),
            payload.get('log_index'),
            payload.get('event_type') or event.kind,
            target.get('monitored_system_id'),
            str(response.get('severity') or 'low'),
            response.get('score'),
            str(response.get('explanation') or 'Observed monitored activity'),
            counterparty,
            payload.get('amount'),
            payload.get('asset_address'),
            payload.get('contract_address'),
            event.ingestion_source,
            _json_dumps(payload),
            event.observed_at,
        ),
    ).fetchone()
    return evidence_id


def _persist_no_threat_evaluation_marker(
    connection: Any,
    *,
    workspace_id: str,
    target: dict[str, Any],
    observed_at: datetime | None,
    monitoring_run_id: str,
    events_ingested: int,
    telemetry_records_seen: int,
) -> str:
    marker_payload = {
        'telemetry_kind': 'evaluation',
        'proof_kind': 'monitoring_evaluation_no_threat',
        'observation_type': 'monitoring_evaluation_no_threat',
        'monitoring_run_id': monitoring_run_id,
        'events_ingested': int(events_ingested),
        'telemetry_records_seen': int(telemetry_records_seen),
        'target_id': str(target.get('id') or ''),
    }
    marker_id = str(uuid.uuid4())
    marker_observed_at = observed_at or utc_now()
    connection.execute(
        '''
        INSERT INTO evidence (
            id, workspace_id, asset_id, target_id, alert_id, chain, block_number, tx_hash, log_index, event_type,
            monitored_system_id, severity, risk_score, summary, counterparty, amount_text, token_address, contract_address, source_provider,
            raw_payload_json, observed_at, created_at
        )
        VALUES (%s, %s, %s, %s, NULL, %s, NULL, NULL, NULL, %s, %s, 'low', NULL, %s, NULL, NULL, NULL, %s, %s, %s::jsonb, %s, NOW())
        ''',
        (
            marker_id,
            workspace_id,
            target.get('asset_id'),
            target.get('id'),
            target.get('chain_network'),
            'monitoring_evaluation_no_threat',
            target.get('monitored_system_id'),
            'Monitoring evaluation completed with no anomaly detections from current telemetry.',
            target.get('contract_identifier') or target.get('wallet_address'),
            'monitoring-worker',
            _json_dumps(marker_payload),
            marker_observed_at,
        ),
    )
    return marker_id


def _load_checkpoint(connection: Any, *, workspace_id: str, monitored_system_id: str | None, chain: str, fallback_block: int) -> int:
    row = connection.execute(
        '''
        SELECT last_processed_block
        FROM monitor_checkpoint
        WHERE workspace_id = %s
          AND ((%s::uuid IS NULL AND monitored_system_id IS NULL) OR monitored_system_id = %s::uuid)
          AND chain = %s
        ''',
        (workspace_id, monitored_system_id, monitored_system_id, chain),
    ).fetchone()
    value = (row or {}).get('last_processed_block')
    try:
        block = max(int(value), fallback_block)
    except Exception:
        return fallback_block
    # Guardrail: Unix timestamps (~1.78B for 2026) are not valid block heights.
    # Base mainnet is at ~47M blocks; Ethereum at ~20M as of June 2026.
    # Any stored value above 500_000_000 is a corrupted timestamp, not a real block.
    # Resetting to fallback causes the scanner to re-scan the replay window safely.
    if block > 500_000_000:
        logger.warning(
            'code=CURSOR_CORRUPTION_DETECTED chain=%s workspace_id=%s corrupt_cursor=%s '
            'fallback=%s action=reset_to_fallback',
            chain, workspace_id, block, fallback_block,
        )
        return fallback_block
    return block


def _upsert_checkpoint(connection: Any, *, workspace_id: str, monitored_system_id: str | None, chain: str, last_processed_block: int) -> None:
    _block = max(0, int(last_processed_block or 0))
    if _block > 500_000_000:
        logger.error(
            'code=UPSERT_CHECKPOINT_REJECT_TIMESTAMP source=_upsert_checkpoint '
            'workspace_id=%s chain=%s corrupt_block=%s action=reset_corrupt_row',
            workspace_id, chain, _block,
        )
        # Reset any existing corrupt row to 0 so the scanner can recover on the next cycle
        connection.execute(
            '''
            UPDATE monitor_checkpoint
            SET last_processed_block = 0, updated_at = NOW()
            WHERE workspace_id = %s
              AND ((%s::uuid IS NULL AND monitored_system_id IS NULL) OR monitored_system_id = %s::uuid)
              AND chain = %s
              AND last_processed_block > 500000000
            ''',
            (workspace_id, monitored_system_id, monitored_system_id, chain),
        )
        return
    connection.execute(
        '''
        INSERT INTO monitor_checkpoint (id, workspace_id, monitored_system_id, chain, last_processed_block, updated_at)
        VALUES (%s, %s, %s::uuid, %s, %s, NOW())
        ON CONFLICT (workspace_id, monitored_system_id, chain)
        DO UPDATE SET
            last_processed_block = CASE
                WHEN monitor_checkpoint.last_processed_block > 500000000 THEN EXCLUDED.last_processed_block
                ELSE GREATEST(monitor_checkpoint.last_processed_block, EXCLUDED.last_processed_block)
            END,
            updated_at = NOW()
        ''',
        (str(uuid.uuid4()), workspace_id, monitored_system_id, chain, _block),
    )

def mark_receipt_removed(connection: Any, *, target_id: str, event_cursor: str, tx_hash: str | None, log_index: int | None, metadata: dict) -> None:
    receipt = connection.execute(
        '''
        SELECT id, workspace_id
        FROM monitoring_event_receipts
        WHERE target_id = %s
          AND (
            event_cursor = %s
            OR ((%s IS NOT NULL AND tx_hash = %s) AND (%s IS NULL OR log_index = %s))
          )
        ORDER BY processed_at DESC
        LIMIT 1
        ''',
        (target_id, event_cursor, tx_hash, tx_hash, log_index, log_index),
    ).fetchone()
    if receipt is None:
        return
    connection.execute('UPDATE monitoring_event_receipts SET removed = TRUE WHERE id = %s', (receipt['id'],))
    connection.execute(
        '''
        INSERT INTO monitoring_reorg_events (id, chain_network, block_number, tx_hash, log_index, observed_at, payload)
        VALUES (%s, %s, %s, %s, %s, NOW(), %s::jsonb)
        ''',
        (
            str(uuid.uuid4()),
            str(metadata.get('chain_network') or 'unknown'),
            metadata.get('block_number'),
            tx_hash,
            log_index,
            _json_dumps({**metadata, 'target_id': target_id, 'event_cursor': event_cursor}),
        ),
    )
    connection.execute(
        '''
        UPDATE incidents
        SET timeline = COALESCE(timeline, '[]'::jsonb) || %s::jsonb,
            updated_at = NOW()
        WHERE workspace_id = %s
          AND status IN ('open', 'acknowledged')
        ''',
        (_json_dumps([{'event': 'chain_reorg_invalidated_evidence', 'at': utc_now().isoformat(), 'event_cursor': event_cursor}]), receipt['workspace_id']),
    )
    logger.info('reorg_removed_receipt target_id=%s cursor=%s tx=%s log_index=%s', target_id, event_cursor, tx_hash, log_index)


def monitoring_operational_mode(runtime: dict[str, Any], *, degraded: bool, degraded_reason: str | None) -> str:
    if degraded or degraded_reason:
        return 'DEGRADED'
    mode = str(runtime.get('mode') or 'demo').strip().lower()
    if mode == 'live':
        return 'LIVE'
    if mode == 'hybrid':
        return 'HYBRID'
    return 'DEMO'


def _safe_error_message(exc: Exception) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    return text[:240]


# Substrings that indicate the DB connection itself is dead and must not be reused.
# Covers psycopg's post-mortem messages after an idle-in-transaction termination or a
# server-side socket close, plus the raw server notice that triggers them.
_CONNECTION_LOST_MESSAGE_MARKERS: tuple[str, ...] = (
    'idle-in-transaction',
    'idle in transaction',
    'terminating connection',
    'connection is closed',
    'connection already closed',
    'the connection was closed',
    'connection not open',
    'server closed the connection',
    'consuming input failed',
    'ssl connection has been closed',
    'ssl syscall',
    'lost synchronization',
)


def _is_connection_lost_error(exc: BaseException | None) -> bool:
    """Return True when ``exc`` proves the DB connection is dead/closed.

    A dead connection must never be reused for recovery work: issuing another
    statement on a socket the server already tore down raises
    ``the connection is closed`` again. When this returns True the caller opens a
    *fresh* connection for error logging/heartbeat/coverage retries instead.

    Detection combines the shared ``classify_db_error`` classifier with an explicit
    walk of the exception cause/context chain for idle-in-transaction markers (the
    classifier does not key on the idle-in-transaction notice directly).
    """
    if exc is None:
        return False
    if isinstance(exc, Exception):
        try:
            if classify_db_error(exc) in {'connection_closed', 'db_unavailable', 'network_unreachable'}:
                return True
        except Exception:
            pass
    seen: set[int] = set()
    stack: list[BaseException | None] = [exc]
    while stack:
        current = stack.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        text = ' '.join(str(current).strip().lower().split())
        if any(marker in text for marker in _CONNECTION_LOST_MESSAGE_MARKERS):
            return True
        stack.append(getattr(current, '__cause__', None))
        stack.append(getattr(current, '__context__', None))
    return False


def _persist_live_coverage_telemetry_resilient(
    connection: Any,
    *,
    target: dict[str, Any],
    provider_result: ActivityProviderResult,
    observed_at: datetime,
) -> bool:
    """Persist live coverage telemetry, isolating its failures from the target poll.

    A coverage-telemetry write must never sink an already-successful event
    detection/alert for the same target (those are committed on their own
    connections before this runs). The primary attempt uses the live worker
    ``connection``. If that connection has been torn down — idle-in-transaction
    timeout, server-side close — the write is retried exactly once on a fresh
    autocommit connection. Any remaining failure is logged as
    ``coverage_telemetry_write_failed`` and swallowed so the caller still returns a
    truthful degraded/partial summary instead of crashing the cycle.

    Returns True only when the coverage write completed (so the caller can keep the
    coverage timestamp truthful and avoid reporting a heartbeat that did not persist).
    """
    try:
        _persist_live_coverage_telemetry(
            connection,
            target=target,
            provider_result=provider_result,
            observed_at=observed_at,
        )
        return True
    except Exception as exc:  # noqa: BLE001 - coverage write must never crash the poll
        connection_lost = _is_connection_lost_error(exc)
        logger.warning(
            'coverage_telemetry_write_primary_failed workspace_id=%s target_id=%s '
            'connection_lost=%s error=%s action=%s',
            target.get('workspace_id'), target.get('id'),
            str(connection_lost).lower(), _safe_error_message(exc),
            'retry_fresh_connection' if connection_lost else 'skip_isolated',
        )
        if not connection_lost:
            logger.warning(
                'coverage_telemetry_write_failed workspace_id=%s target_id=%s '
                'reason=non_connection_error error=%s action=continue_target_poll',
                target.get('workspace_id'), target.get('id'), _safe_error_message(exc),
            )
            return False
    # Dead-connection case only: reconnect once on a fresh autocommit connection.
    try:
        with pg_connection() as _fresh_conn:
            try:
                _fresh_conn.autocommit = True
            except Exception:
                pass
            _persist_live_coverage_telemetry(
                _fresh_conn,
                target=target,
                provider_result=provider_result,
                observed_at=observed_at,
            )
        logger.info(
            'coverage_telemetry_write_recovered workspace_id=%s target_id=%s '
            'action=persisted_on_fresh_connection',
            target.get('workspace_id'), target.get('id'),
        )
        return True
    except Exception as retry_exc:  # noqa: BLE001
        logger.warning(
            'coverage_telemetry_write_failed workspace_id=%s target_id=%s '
            'reason=retry_fresh_connection_failed error=%s action=continue_target_poll',
            target.get('workspace_id'), target.get('id'), _safe_error_message(retry_exc),
        )
        return False


def _derive_system_runtime_state(result: dict[str, Any], *, is_enabled: bool) -> tuple[str, str, str, str | None]:
    if not is_enabled:
        return 'disabled', 'unavailable', 'unavailable', 'monitoring_disabled'
    target_type = result.get('target_type')
    unsupported_target_type = not is_monitorable_target_type(target_type)
    provider_status = str(result.get('provider_status') or '').lower()
    events_ingested = int(result.get('events_ingested', 0) or 0)
    recent_real_event_count = int(result.get('recent_real_event_count', 0) or 0)
    source_status = str(result.get('source_status') or '').lower()
    degraded_reason = str(result.get('degraded_reason') or '').strip() or None
    if provider_status == 'failed':
        return 'failed', 'unavailable', 'low', degraded_reason or 'provider_failed'
    if provider_status == 'degraded' or source_status == 'degraded':
        if unsupported_target_type:
            return 'degraded', 'stale', 'low', 'unsupported_target_type_for_live_coverage'
        return 'degraded', 'stale', 'low', degraded_reason or 'monitoring_degraded'
    if provider_status == 'no_evidence':
        if unsupported_target_type:
            return 'degraded', 'stale', 'low', 'unsupported_target_type_for_live_coverage'
        return 'degraded', 'stale', 'low', degraded_reason or 'no_evidence'
    if events_ingested > 0 or recent_real_event_count > 0:
        return 'healthy', 'fresh', 'high', None
    if result.get('live_coverage_telemetry_at'):
        return 'degraded', 'fresh', 'low', 'no_evidence'
    return 'idle', 'stale', 'medium', 'no_events_detected_yet'


def _persist_live_coverage_telemetry(
    connection: Any,
    *,
    target: dict[str, Any],
    provider_result: ActivityProviderResult,
    observed_at: datetime,
) -> None:
    from services.api.app.evm_activity_provider import CHAIN_MAP as _CHAIN_MAP
    _chain_network = str(target.get('chain_network') or 'ethereum').strip().lower()
    _env_chain_id_str = (os.getenv('STAGING_EVM_CHAIN_ID') or os.getenv('EVM_CHAIN_ID') or '').strip()
    _env_chain_id = int(_env_chain_id_str) if _env_chain_id_str.isdigit() else 1
    _chain_id_int = (_CHAIN_MAP.get(_chain_network) or {}).get('chain_id') or _env_chain_id
    _chain_id_hex = hex(_chain_id_int)

    # Guard: only persist with a real chain block number from the RPC probe.
    # Do NOT fall back to Unix timestamp — timestamps in block_number corrupt
    # the scanner cursor (from_block > latest_block → empty scan forever).
    _effective_block = provider_result.latest_block
    if _effective_block is None:
        logger.warning(
            'code=COVERAGE_TELEMETRY_SKIP_NO_BLOCK workspace_id=%s target_id=%s '
            'chain=%s source=%s reason=latest_block_unavailable',
            target.get('workspace_id'), target.get('id'),
            target.get('chain_network'), provider_result.source_type,
        )
        return
    if _chain_id_int == 8453 and _effective_block > 100_000_000:
        logger.error(
            'invalid_base_block_number source=_persist_live_coverage_telemetry '
            'workspace_id=%s target_id=%s chain_id=%s chain=%s '
            'block_number=%s action=skip_telemetry_write',
            target.get('workspace_id'), target.get('id'),
            _chain_id_int, target.get('chain_network'), _effective_block,
        )
        return
    if _effective_block > 500_000_000:
        logger.error(
            'code=COVERAGE_TELEMETRY_BLOCK_CORRUPT_REJECTED source=_persist_live_coverage_telemetry '
            'workspace_id=%s target_id=%s chain_id=%s chain=%s '
            'corrupt_block=%s observed_at=%s source_type=%s '
            'action=skip_telemetry_write',
            target.get('workspace_id'), target.get('id'),
            _chain_id_int, target.get('chain_network'),
            _effective_block, observed_at.isoformat(),
            provider_result.source_type,
        )
        return

    logger.info(
        'code=COVERAGE_TELEMETRY_POLL_CYCLE '
        'workspace_id=%s target_id=%s chain=%s chain_id=%s source_type=%s '
        'eth_blockNumber_hex=%s latest_block_decimal=%s block_number_to_insert=%s '
        'observed_at=%s',
        target.get('workspace_id'), target.get('id'),
        target.get('chain_network'), _chain_id_int,
        provider_result.source_type,
        hex(_effective_block), _effective_block, _effective_block,
        observed_at.isoformat(),
    )

    payload = {
        'telemetry_kind': 'coverage',
        'proof_kind': 'coverage_telemetry',
        'observation_type': 'provider_checkpoint',
        'provider_name': provider_result.provider_name,
        'provider_kind': provider_result.provider_kind,
        'source_type': provider_result.source_type,
        'latest_block': provider_result.latest_block,
        'checkpoint': provider_result.checkpoint,
        'checkpoint_age_seconds': provider_result.checkpoint_age_seconds,
        'target_id': target.get('id'),
    }
    event_id = f"coverage:{provider_result.provider_name}:{observed_at.isoformat()}"
    event_cursor = provider_result.checkpoint or f"coverage:{_effective_block}:{int(observed_at.timestamp())}"
    connection.execute(
        '''
        INSERT INTO monitoring_event_receipts (
            id, workspace_id, target_id, event_id, event_cursor, tx_hash, block_number, log_index, ingestion_source, receipt_kind, evidence_source, telemetry_kind
        )
        VALUES (%s, %s, %s, %s, %s, NULL, %s, -1, %s, %s, %s, %s)
        ON CONFLICT (target_id, event_id)
        DO NOTHING
        ''',
        (
            str(uuid.uuid4()),
            target['workspace_id'],
            target['id'],
            event_id,
            event_cursor,
            _effective_block,
            'live_coverage',
            'coverage_telemetry',
            'live',
            'coverage',
        ),
    )
    connection.execute(
        '''
        INSERT INTO evidence (
            id, workspace_id, asset_id, target_id, alert_id, chain, block_number, tx_hash, log_index, event_type,
            monitored_system_id, severity, risk_score, summary, counterparty, amount_text, token_address, contract_address, source_provider,
            raw_payload_json, observed_at, created_at
        )
        VALUES (%s, %s, %s, %s, NULL, %s, %s, NULL, -1, %s, %s, %s, NULL, %s, NULL, NULL, NULL, %s, %s, %s::jsonb, %s, NOW())
        ''',
        (
            str(uuid.uuid4()),
            target['workspace_id'],
            target.get('asset_id'),
            target['id'],
            target.get('chain_network'),
            _effective_block,
            'coverage_telemetry',
            target.get('monitored_system_id'),
            'low',
            'Live provider coverage telemetry verified',
            target.get('contract_identifier') or target.get('wallet_address'),
            provider_result.provider_name,
            _json_dumps(payload),
            observed_at,
        ),
    )
    # Persist a telemetry_events row so the telemetry page and runtime summary
    # canonical_last_telemetry_at reflect successful live RPC polls even when
    # no blockchain events (transfers, etc.) were observed in this cycle.
    logger.info(
        'code=COVERAGE_TELEMETRY_BLOCK workspace_id=%s target_id=%s chain=%s '
        'latest_block_decimal=%s latest_block_hex=%s source=%s',
        target.get('workspace_id'), target.get('id'),
        target.get('chain_network'), _effective_block, hex(_effective_block),
        provider_result.source_type,
    )
    _telem_payload = {
        'telemetry_kind': 'coverage',
        'chain_id': _chain_id_int,
        'block_number': _effective_block,
        'latest_block': _effective_block,
        'provider_name': provider_result.provider_name,
        'source_type': provider_result.source_type,
        'checkpoint': provider_result.checkpoint,
        'raw_response': {
            'eth_chainId': _chain_id_hex,
            'eth_blockNumber': hex(_effective_block),
            'result': hex(_effective_block),
        },
        'monitored_system_id': str(target.get('monitored_system_id') or '') or None,
        'target_id': str(target['id']),
        'workspace_id': str(target['workspace_id']),
    }
    _telem_payload_json = _json_dumps(_telem_payload)
    # Collapsed key (no block number) so all future polls DO UPDATE a single row
    # per target rather than inserting a new row for every polled block.
    # Migration 0113 already remapped existing rows to this format.
    _telem_idempotency = (
        f"{target['workspace_id']}:{target['id']}:coverage_poll"
    )
    # Validate that target.asset_id exists in asset_registry before inserting
    # telemetry_events (which has a FK to asset_registry, not to assets).
    # targets.asset_id → assets(id), but telemetry_events.asset_id → asset_registry(id).
    # If the UUID is missing from asset_registry, repair by inserting a row with
    # the same UUID so the FK is satisfied.  If repair fails, persist telemetry
    # with asset_id=NULL (nullable column) and log a structured warning.
    _raw_asset_id = target.get('asset_id')
    _asset_id_str = str(_raw_asset_id) if _raw_asset_id else None
    _telem_asset_id: str | None = None
    if _asset_id_str:
        _ws_id_str = str(target['workspace_id'])
        _ar_row = connection.execute(
            'SELECT id FROM asset_registry WHERE id = %s::uuid LIMIT 1',
            (_asset_id_str,),
        ).fetchone()
        if _ar_row:
            _telem_asset_id = _asset_id_str
        else:
            _contract_id = str(target.get('contract_identifier') or '').strip()
            _wallet_addr = str(target.get('wallet_address') or '').strip()
            _ar_type = 'smart_contract' if _contract_id else ('wallet' if _wallet_addr else 'smart_contract')
            _ar_addr = _contract_id or _wallet_addr or str(target['id'])
            _ar_chain = _chain_network or 'ethereum'
            try:
                connection.execute(
                    '''
                    INSERT INTO asset_registry (
                        id, workspace_id, type, address_or_identifier, chain, status, created_at, updated_at
                    )
                    VALUES (%s::uuid, %s::uuid, %s, %s, %s, 'active', NOW(), NOW())
                    ON CONFLICT DO NOTHING
                    ''',
                    (_asset_id_str, _ws_id_str, _ar_type, _ar_addr, _ar_chain),
                )
                _ar_verify = connection.execute(
                    'SELECT id FROM asset_registry WHERE id = %s::uuid LIMIT 1',
                    (_asset_id_str,),
                ).fetchone()
                if _ar_verify:
                    _telem_asset_id = _asset_id_str
                    logger.info(
                        'code=LIVE_TELEMETRY_ASSET_REGISTRY_REPAIRED asset_id=%s workspace_id=%s target_id=%s chain=%s',
                        _asset_id_str, _ws_id_str, target.get('id'), _ar_chain,
                    )
                else:
                    logger.warning(
                        'code=LIVE_TELEMETRY_ASSET_FK_MISSING asset_id=%s workspace_id=%s target_id=%s bad_asset_id=%s',
                        _asset_id_str, _ws_id_str, target.get('id'), _asset_id_str,
                    )
            except Exception as _ar_exc:
                logger.warning(
                    'code=LIVE_TELEMETRY_ASSET_FK_MISSING asset_id=%s workspace_id=%s target_id=%s error=%s',
                    _asset_id_str, _ws_id_str, target.get('id'), _safe_error_message(_ar_exc),
                )
    connection.execute(
        """
        INSERT INTO telemetry_events (
            id, workspace_id, asset_id, target_id, provider_type, event_type,
            observed_at, evidence_source, payload_hash, payload_json, idempotency_key
        )
        VALUES (%s::uuid, %s::uuid, %s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s::jsonb, %s)
        ON CONFLICT (workspace_id, target_id, idempotency_key) WHERE idempotency_key IS NOT NULL
        DO UPDATE SET
            observed_at = EXCLUDED.observed_at,
            payload_json = EXCLUDED.payload_json,
            payload_hash = EXCLUDED.payload_hash,
            ingested_at = NOW()
        """,
        (
            str(uuid.uuid4()),
            str(target['workspace_id']),
            _telem_asset_id,
            str(target['id']),
            'evm_rpc',
            'rpc_polling',
            observed_at,
            'live',
            hashlib.sha256(_telem_payload_json.encode('utf-8')).hexdigest(),
            _telem_payload_json,
            _telem_idempotency,
        ),
    )
    logger.info(
        'telemetry_event_persisted workspace_id=%s target_id=%s provider_type=evm_rpc '
        'event_type=rpc_polling inserted_telemetry_block_number=%s',
        target.get('workspace_id'),
        target.get('id'),
        provider_result.latest_block,
    )


def _payload_shape(payload: dict[str, Any]) -> dict[str, Any]:
    metadata = payload.get('metadata') if isinstance(payload.get('metadata'), dict) else {}
    return {
        'top_level_keys': sorted(payload.keys()),
        'metadata_keys': sorted(metadata.keys()),
    }


def _threat_call(kind: ThreatKind, payload: dict[str, Any], *, target_id: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    logger.info('monitoring live analysis request target=%s analysis_type=%s payload_shape=%s', target_id, kind, _payload_shape(payload))
    try:
        from services.api.app import main as api_main

        response = api_main.proxy_threat(kind, payload)
        if isinstance(response, dict):
            logger.info(
                'monitoring live analysis succeeded target=%s source=%s score=%s',
                target_id,
                str(response.get('source') or 'live'),
                response.get('score'),
            )
            return response, {'live_invocation': 'proxy_threat', 'live_invocation_succeeded': True}
        logger.warning(
            'monitoring live analysis failed target=%s reason=%s; using fallback',
            target_id,
            'proxy_threat returned no payload',
        )
        return None, {
            'live_invocation': 'proxy_threat',
            'live_invocation_succeeded': False,
            'fallback_reason': 'live_engine_unavailable',
            'fallback_exception_type': 'NoLiveResponse',
            'fallback_exception_message': 'proxy_threat returned no payload',
        }
    except Exception as exc:  # pragma: no cover - defensive logging around runtime import/invocation
        logger.exception('monitoring live analysis failed target=%s reason=%s; using fallback', target_id, exc.__class__.__name__)
        return None, {
            'live_invocation': 'proxy_threat',
            'live_invocation_succeeded': False,
            'fallback_reason': 'live_engine_exception',
            'fallback_exception_type': exc.__class__.__name__,
            'fallback_exception_message': _safe_error_message(exc),
        }


def _normalize_event(target: dict[str, Any], event: ActivityEvent, monitoring_run_id: str, workspace: dict[str, Any]) -> tuple[ThreatKind, dict[str, Any]]:
    kind = event.kind if event.kind in {'contract', 'transaction', 'market'} else 'transaction'
    payload = {
        **event.payload,
        'target_id': str(target['id']),
        'target_name': str(target.get('name') or ''),
        'target_type': str(target.get('target_type') or ''),
        'chain_network': str(target.get('chain_network') or ''),
        'severity_preference': str(target.get('severity_preference') or 'medium'),
        'metadata': {
            'workspace_id': str(target['workspace_id']),
            'workspace_name': workspace.get('name'),
            'target_id': str(target['id']),
            'target_name': str(target.get('name') or ''),
            'target_type': str(target.get('target_type') or ''),
            'chain_network': str(target.get('chain_network') or ''),
            'monitoring_run_id': monitoring_run_id,
            'ingestion_source': event.ingestion_source,
            'observed_at': event.observed_at.isoformat(),
            'severity_threshold': str(target.get('severity_threshold') or 'medium'),
            'policy_snapshot': {
                'auto_create_alerts': bool(target.get('auto_create_alerts')),
                'auto_create_incidents': bool(target.get('auto_create_incidents')),
            },
            'provider_cursor': event.cursor,
            'event_id': event.event_id,
        },
    }
    normalized, _ = normalize_threat_payload(kind, payload, include_original=False)
    logger.info(
        'monitoring payload built target=%s event=%s analysis_type=%s payload_shape=%s',
        target.get('id'),
        event.event_id,
        kind,
        _payload_shape(normalized),
    )
    return kind, normalized


def _load_target_asset_context(connection: Any, *, workspace_id: str, target: dict[str, Any]) -> dict[str, Any] | None:
    asset_id = target.get('asset_id')
    if not asset_id:
        return None
    row = connection.execute(
        '''
        SELECT id, name, asset_class, asset_symbol, identifier, asset_identifier, token_contract_address,
               chain_network, treasury_ops_wallets, custody_wallets, oracle_sources, venue_labels, expected_flow_patterns,
               expected_counterparties, expected_approval_patterns, expected_liquidity_baseline,
               expected_oracle_freshness_seconds, expected_oracle_update_cadence_seconds,
               baseline_status, baseline_source, baseline_updated_at, baseline_confidence, baseline_coverage
        FROM assets
        WHERE id = %s AND workspace_id = %s AND deleted_at IS NULL
        ''',
        (asset_id, workspace_id),
    ).fetchone()
    if row is None:
        return None
    context = _json_safe_value(dict(row))
    for key in ('treasury_ops_wallets', 'custody_wallets', 'oracle_sources', 'venue_labels', 'expected_flow_patterns', 'expected_counterparties'):
        if not isinstance(context.get(key), list):
            context[key] = []
    for key in ('expected_approval_patterns', 'expected_liquidity_baseline'):
        if not isinstance(context.get(key), dict):
            context[key] = {}
    if not context.get('identifier') and context.get('asset_identifier'):
        context['identifier'] = context['asset_identifier']
    if not context.get('asset_identifier'):
        context['asset_identifier'] = context.get('identifier') or context.get('name')
    if not context.get('asset_symbol'):
        context['asset_symbol'] = context.get('symbol')
    context['chain_id'] = target.get('chain_id') or context.get('chain_id')
    if not context.get('token_contract_address'):
        context['token_contract_address'] = target.get('contract_identifier')
    context['asset_id'] = context.get('id')
    context['symbol'] = context.get('asset_symbol')
    context['contract_address'] = context.get('token_contract_address')
    return context


ASSET_DETECTOR_FAMILIES = {
    'counterparty',
    'flow_pattern',
    'approval_pattern',
    'liquidity_venue',
    'oracle_integrity',
}

CLAIM_REASON_EXPLANATIONS: dict[str, str] = {
    'missing_asset_identity': 'Protected asset identity is incomplete (asset_id/identifier/symbol/chain/contract required).',
    'missing_protected_path_context': 'Treasury/custody protected path context is incomplete.',
    'lifecycle_context_incomplete': 'Lifecycle routing, approvals, or baseline rules are incomplete.',
    'missing_market_provider_config': 'No real external market provider is configured for this asset.',
    'market_provider_unreachable': 'Configured market providers could not be reached.',
    'market_provider_stale': 'Market telemetry is stale for configured providers.',
    'insufficient_market_observations': 'Not enough real external market observations were available.',
    'detector_relied_on_internal_rollups_only': 'Only internal rollups were available; no eligible external market telemetry was present.',
    'missing_oracle_provider_config': 'No real oracle provider/source is configured for this asset.',
    'oracle_provider_unreachable': 'Configured oracle providers could not be reached.',
    'oracle_provider_stale': 'Oracle observations are stale.',
    'insufficient_oracle_observations': 'Oracle observations are missing or insufficient for independent source coverage.',
}


def _normalize_addr(value: Any) -> str:
    return str(value or '').strip().lower()


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _normalized_asset_model(asset: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(asset, dict):
        return None
    treasury_wallets = {_normalize_addr(item) for item in asset.get('treasury_ops_wallets', []) if _normalize_addr(item)}
    custody_wallets = {_normalize_addr(item) for item in asset.get('custody_wallets', []) if _normalize_addr(item)}
    expected_counterparties = {_normalize_addr(item) for item in asset.get('expected_counterparties', []) if _normalize_addr(item)}
    venue_labels = {_normalize_addr(item) for item in asset.get('venue_labels', []) if _normalize_addr(item)}
    flow_patterns = [item for item in asset.get('expected_flow_patterns', []) if isinstance(item, dict)]
    allowed_routes = {
        (str(item.get('source_class') or '').strip().lower(), str(item.get('destination_class') or '').strip().lower())
        for item in flow_patterns
        if item.get('source_class') and item.get('destination_class')
    }
    approval_patterns = asset.get('expected_approval_patterns') if isinstance(asset.get('expected_approval_patterns'), dict) else {}
    liquidity_baseline = asset.get('expected_liquidity_baseline') if isinstance(asset.get('expected_liquidity_baseline'), dict) else {}
    oracle_sources = [str(item).strip().lower() for item in asset.get('oracle_sources', []) if str(item).strip()]
    return {
        'asset_id': asset.get('id'),
        'asset_identifier': asset.get('asset_identifier') or asset.get('identifier') or asset.get('name'),
        'symbol': asset.get('asset_symbol') or asset.get('symbol'),
        'chain_id': asset.get('chain_id'),
        'contract_address': _normalize_addr(asset.get('token_contract_address')),
        'treasury_ops_wallets': treasury_wallets,
        'custody_wallets': custody_wallets,
        'expected_counterparties': expected_counterparties,
        'expected_flow_patterns': flow_patterns,
        'allowed_routes': allowed_routes,
        'expected_approval_patterns': approval_patterns,
        'expected_liquidity_baseline': liquidity_baseline,
        'oracle_sources': oracle_sources,
        'expected_oracle_freshness_seconds': int(asset.get('expected_oracle_freshness_seconds') or 0),
        'expected_oracle_update_cadence_seconds': int(asset.get('expected_oracle_update_cadence_seconds') or 0),
        'venue_labels': venue_labels,
        'baseline_status': asset.get('baseline_status'),
        'baseline_confidence': asset.get('baseline_confidence'),
        'baseline_coverage': asset.get('baseline_coverage'),
    }


def _build_protected_asset_context(asset: dict[str, Any] | None, *, target: dict[str, Any] | None = None) -> dict[str, Any]:
    model = _normalized_asset_model(asset)
    context: dict[str, Any] = {
        'asset_id': None,
        'asset_identifier': None,
        'symbol': None,
        'chain_id': None,
        'contract_address': None,
        'treasury_ops_wallets': [],
        'custody_wallets': [],
        'expected_counterparties': [],
        'expected_flow_patterns': [],
        'expected_approval_patterns': {},
        'venue_labels': [],
        'expected_liquidity_baseline': {},
        'baseline_status': None,
        'baseline_confidence': None,
        'baseline_coverage': None,
        'oracle_sources': [],
        'expected_oracle_freshness_seconds': 0,
        'expected_oracle_update_cadence_seconds': 0,
        'contract_complete': False,
        'missing_contract_fields': [],
    }
    if not model:
        context['missing_contract_fields'] = ['asset_profile']
        return context
    context.update(
        {
            'asset_id': model.get('asset_id'),
            'asset_identifier': model.get('asset_identifier'),
            'symbol': model.get('symbol'),
            'chain_id': model.get('chain_id') or ((target or {}).get('chain_id') if isinstance(target, dict) else None),
            'contract_address': model.get('contract_address'),
            'treasury_ops_wallets': sorted(model.get('treasury_ops_wallets', set())),
            'custody_wallets': sorted(model.get('custody_wallets', set())),
            'expected_counterparties': sorted(model.get('expected_counterparties', set())),
            'expected_flow_patterns': model.get('expected_flow_patterns', []),
            'expected_approval_patterns': model.get('expected_approval_patterns', {}),
            'venue_labels': sorted(model.get('venue_labels', set())),
            'expected_liquidity_baseline': model.get('expected_liquidity_baseline', {}),
            'baseline_status': model.get('baseline_status'),
            'baseline_confidence': model.get('baseline_confidence'),
            'baseline_coverage': model.get('baseline_coverage'),
            'oracle_sources': model.get('oracle_sources', []),
            'expected_oracle_freshness_seconds': model.get('expected_oracle_freshness_seconds', 0),
            'expected_oracle_update_cadence_seconds': model.get('expected_oracle_update_cadence_seconds', 0),
        }
    )
    required = {
        'asset_id': context.get('asset_id'),
        'asset_identifier': context.get('asset_identifier'),
        'symbol': context.get('symbol'),
        'chain_id': context.get('chain_id'),
        'contract_address': context.get('contract_address'),
        'treasury_ops_wallets': context.get('treasury_ops_wallets'),
        'custody_wallets': context.get('custody_wallets'),
        'expected_counterparties': context.get('expected_counterparties'),
        'expected_flow_patterns': context.get('expected_flow_patterns'),
        'expected_approval_patterns': context.get('expected_approval_patterns'),
        'venue_labels': context.get('venue_labels'),
        'expected_liquidity_baseline': context.get('expected_liquidity_baseline'),
        'baseline_status': context.get('baseline_status'),
        'baseline_confidence': context.get('baseline_confidence'),
        'baseline_coverage': context.get('baseline_coverage'),
        'oracle_sources': context.get('oracle_sources'),
        'expected_oracle_freshness_seconds': context.get('expected_oracle_freshness_seconds'),
        'expected_oracle_update_cadence_seconds': context.get('expected_oracle_update_cadence_seconds'),
    }
    missing: list[str] = []
    for key, value in required.items():
        if value is None:
            missing.append(key)
        elif isinstance(value, (list, dict)) and len(value) == 0:
            missing.append(key)
        elif isinstance(value, str) and not value.strip():
            missing.append(key)
        elif isinstance(value, (int, float)) and key in {'expected_oracle_freshness_seconds', 'expected_oracle_update_cadence_seconds'} and value <= 0:
            missing.append(key)
    context['missing_contract_fields'] = missing
    context['contract_complete'] = not missing
    return context


def _provider_coverage_status(*, event_payload: dict[str, Any], protected_asset_context: dict[str, Any]) -> dict[str, Any]:
    market_observations = event_payload.get('market_observations') if isinstance(event_payload.get('market_observations'), list) else []
    oracle_observations = event_payload.get('oracle_observations') if isinstance(event_payload.get('oracle_observations'), list) else []
    required_oracles = {str(item).strip().lower() for item in protected_asset_context.get('oracle_sources', []) if str(item).strip()}
    claim_ineligibility_reasons: list[str] = []
    missing_contract_fields = set(protected_asset_context.get('missing_contract_fields') or [])
    if missing_contract_fields & {'asset_id', 'asset_identifier', 'symbol', 'chain_id', 'contract_address'}:
        claim_ineligibility_reasons.append('missing_asset_identity')
    if missing_contract_fields & {'treasury_ops_wallets', 'custody_wallets', 'expected_counterparties'}:
        claim_ineligibility_reasons.append('missing_protected_path_context')
    if missing_contract_fields & {'expected_flow_patterns', 'expected_approval_patterns', 'expected_liquidity_baseline'}:
        claim_ineligibility_reasons.append('lifecycle_context_incomplete')

    market_provider_names = sorted(
        {
            str(item.get('provider_name') or item.get('source_name') or '').strip().lower()
            for item in market_observations
            if isinstance(item, dict) and str(item.get('provider_name') or item.get('source_name') or '').strip()
        }
    )
    market_statuses = {str(item.get('status') or '').lower() for item in market_observations if isinstance(item, dict)}
    market_freshness_limit = max(1, int(os.getenv('FEATURE1_MARKET_FRESHNESS_SECONDS', '300')))
    external_market_observations = [
        item for item in market_observations
        if isinstance(item, dict) and str(item.get('telemetry_kind') or 'external_market').lower() == 'external_market'
    ]
    market_realtime_external = [
        item for item in external_market_observations
        if str(item.get('observation_kind') or ('real_external_market_observation' if str(item.get('status') or '').lower() == 'ok' else '')).lower() == 'real_external_market_observation'
    ]
    market_fresh = [
        item for item in market_realtime_external
        if str(item.get('status') or '').lower() == 'ok'
        and int(item.get('freshness_seconds') or 0) <= market_freshness_limit
    ]
    market_reachable = [item for item in market_observations if isinstance(item, dict) and str(item.get('status') or '').lower() != 'unavailable']
    market_claim_ineligibility_reasons: list[str] = []
    if not market_provider_names:
        market_coverage_status = 'insufficient_real_evidence'
        market_claim_eligible = False
        market_claim_ineligibility_reasons.append('missing_market_provider_config')
    elif not market_reachable:
        market_coverage_status = 'provider_configured_but_unreachable'
        market_claim_eligible = False
        market_claim_ineligibility_reasons.append('market_provider_unreachable')
    elif external_market_observations and not market_realtime_external:
        market_coverage_status = 'insufficient_real_evidence'
        market_claim_eligible = False
        market_claim_ineligibility_reasons.append('detector_relied_on_internal_rollups_only')
    elif not market_fresh:
        market_coverage_status = 'insufficient_real_evidence'
        market_claim_eligible = False
        market_claim_ineligibility_reasons.extend(['market_provider_stale', 'insufficient_market_observations'])
    elif 'ok' in market_statuses:
        market_coverage_status = 'real_external_market_observation'
        market_claim_eligible = True
    else:
        market_coverage_status = 'insufficient_real_evidence'
        market_claim_eligible = False
        market_claim_ineligibility_reasons.append('insufficient_market_observations')

    oracle_provider_names = sorted(
        {
            str(item.get('provider_name') or item.get('source_name') or '').strip().lower()
            for item in oracle_observations
            if isinstance(item, dict) and str(item.get('provider_name') or item.get('source_name') or '').strip()
        }
    )
    oracle_statuses = {str(item.get('status') or '').lower() for item in oracle_observations if isinstance(item, dict)}
    oracle_reachable = [item for item in oracle_observations if isinstance(item, dict) and str(item.get('status') or '').lower() != 'unavailable']
    oracle_fresh = [item for item in oracle_observations if isinstance(item, dict) and str(item.get('status') or '').lower() == 'ok' and int(item.get('freshness_seconds') or 0) >= 0]
    observed_sources = {
        str(item.get('source_name') or item.get('provider_name') or '').strip().lower()
        for item in oracle_observations
        if isinstance(item, dict) and str(item.get('source_name') or item.get('provider_name') or '').strip()
    }
    oracle_claim_ineligibility_reasons: list[str] = []
    if not required_oracles:
        oracle_coverage_status = 'insufficient_real_evidence'
        oracle_claim_eligible = False
        oracle_claim_ineligibility_reasons.append('missing_oracle_provider_config')
    elif not oracle_provider_names:
        oracle_coverage_status = 'insufficient_real_evidence'
        oracle_claim_eligible = False
        oracle_claim_ineligibility_reasons.append('insufficient_oracle_observations')
    elif not oracle_reachable:
        oracle_coverage_status = 'provider_configured_but_unreachable'
        oracle_claim_eligible = False
        oracle_claim_ineligibility_reasons.append('oracle_provider_unreachable')
    elif 'stale' in oracle_statuses:
        oracle_coverage_status = 'provider_returned_stale_data'
        oracle_claim_eligible = False
        oracle_claim_ineligibility_reasons.append('oracle_provider_stale')
    elif 'divergent' in oracle_statuses:
        oracle_coverage_status = 'provider_returned_divergent_values'
        oracle_claim_eligible = False
        oracle_claim_ineligibility_reasons.append('insufficient_oracle_observations')
    elif len(observed_sources) < max(1, len(required_oracles)):
        oracle_coverage_status = 'insufficient_real_evidence'
        oracle_claim_eligible = False
        oracle_claim_ineligibility_reasons.append('insufficient_oracle_observations')
    elif not oracle_fresh:
        oracle_coverage_status = 'insufficient_real_evidence'
        oracle_claim_eligible = False
        oracle_claim_ineligibility_reasons.append('oracle_provider_stale')
    elif 'ok' in oracle_statuses:
        oracle_coverage_status = 'real_oracle_observations_present'
        oracle_claim_eligible = True
    else:
        oracle_coverage_status = 'insufficient_real_evidence'
        oracle_claim_eligible = False
        oracle_claim_ineligibility_reasons.append('insufficient_oracle_observations')

    claim_ineligibility_reasons.extend(market_claim_ineligibility_reasons)
    claim_ineligibility_reasons.extend(oracle_claim_ineligibility_reasons)

    enterprise_claim_eligibility = bool(
        protected_asset_context.get('contract_complete')
        and market_claim_eligible
        and oracle_claim_eligible
    )
    distinct_reasons = sorted({item for item in claim_ineligibility_reasons if item})
    return {
        'provider_coverage_status': {
            'market_coverage_status': market_coverage_status,
            'oracle_coverage_status': oracle_coverage_status,
            'market_provider_count': len(market_provider_names),
            'market_provider_reachable_count': len(market_reachable),
            'market_provider_fresh_count': len(market_fresh),
            'market_provider_names': market_provider_names,
            'market_observation_count': len(market_observations),
            'market_claim_eligible': market_claim_eligible,
            'market_claim_ineligibility_reasons': sorted(set(market_claim_ineligibility_reasons)),
            'oracle_provider_count': len(oracle_provider_names),
            'oracle_provider_reachable_count': len(oracle_reachable),
            'oracle_provider_fresh_count': len(oracle_fresh),
            'oracle_provider_names': oracle_provider_names,
            'oracle_observation_count': len(oracle_observations),
            'oracle_claim_eligible': oracle_claim_eligible,
            'oracle_claim_ineligibility_reasons': sorted(set(oracle_claim_ineligibility_reasons)),
        },
        'market_coverage_status': market_coverage_status,
        'oracle_coverage_status': oracle_coverage_status,
        'provider_coverage_summary': {
            'market_provider_count': len(market_provider_names),
            'market_provider_reachable_count': len(market_reachable),
            'market_provider_fresh_count': len(market_fresh),
            'market_provider_names': market_provider_names,
            'market_observation_count': len(market_observations),
            'market_claim_eligible': market_claim_eligible,
            'market_claim_ineligibility_reasons': sorted(set(market_claim_ineligibility_reasons)),
            'oracle_provider_count': len(oracle_provider_names),
            'oracle_provider_reachable_count': len(oracle_reachable),
            'oracle_provider_fresh_count': len(oracle_fresh),
            'oracle_provider_names': oracle_provider_names,
            'oracle_observation_count': len(oracle_observations),
            'oracle_claim_eligible': oracle_claim_eligible,
            'oracle_claim_ineligibility_reasons': sorted(set(oracle_claim_ineligibility_reasons)),
            'external_market_telemetry_present': bool(market_realtime_external),
            'real_oracle_observations_present': bool(oracle_fresh),
        },
        'enterprise_claim_eligibility': enterprise_claim_eligibility,
        'claim_ineligibility_reasons': distinct_reasons,
        'claim_ineligibility_details': [
            {'code': code, 'message': CLAIM_REASON_EXPLANATIONS.get(code, code.replace('_', ' '))}
            for code in distinct_reasons
        ],
    }


def _resolve_flow_classification(source_class: str, destination_class: str) -> str:
    if source_class == destination_class == 'treasury_ops':
        return 'treasury_ops_internal'
    if source_class == destination_class == 'custody':
        return 'custody_internal'
    if destination_class == 'approved_external_counterparty':
        return 'approved_external_counterparty'
    if destination_class == 'monitored_venue':
        return 'monitored_venue'
    return 'unknown_external'


def _classify_endpoint(address: str, model: dict[str, Any]) -> str:
    if address in model['treasury_ops_wallets']:
        return 'treasury_ops'
    if address in model['custody_wallets']:
        return 'custody'
    if address in model['expected_counterparties']:
        return 'approved_external_counterparty'
    if address in model['venue_labels']:
        return 'monitored_venue'
    return 'unknown_external'


def _asset_detection_summary(*, asset: dict[str, Any] | None, event: ActivityEvent) -> dict[str, Any]:
    results = _enforce_asset_detectors(asset=asset, event=event)
    anomalous = [item for item in results if item['detector_status'] == 'anomaly_detected']
    insufficient = [item for item in results if item['detector_status'] == 'insufficient_real_evidence']
    highest = anomalous[0] if anomalous else (insufficient[0] if insufficient else results[0])
    summary_reason = highest.get('anomaly_reason') or 'detectors_completed_without_confirmed_anomaly'
    protected_asset_context = highest.get('protected_asset_context') if isinstance(highest.get('protected_asset_context'), dict) else _build_protected_asset_context(asset)
    return {
        'detection_family': highest.get('detector_family'),
        'detector_results': results,
        'detector_status': highest.get('detector_status'),
        'anomaly_basis': summary_reason,
        'baseline_reference': {
            'baseline_status': (asset or {}).get('baseline_status'),
            'baseline_confidence': (asset or {}).get('baseline_confidence'),
            'baseline_coverage': (asset or {}).get('baseline_coverage'),
        },
        'confidence_basis': highest.get('confidence'),
        'recommended_action': highest.get('recommended_action'),
        'severity': highest.get('severity', 'low'),
        'protected_asset_context': protected_asset_context,
        'market_coverage_status': highest.get('market_coverage_status'),
        'oracle_coverage_status': highest.get('oracle_coverage_status'),
        'provider_coverage_status': highest.get('provider_coverage_status') or {},
        'provider_coverage_summary': highest.get('provider_coverage_summary'),
        'enterprise_claim_eligibility': bool(highest.get('enterprise_claim_eligibility')),
        'claim_ineligibility_reasons': highest.get('claim_ineligibility_reasons') or [],
        'claim_ineligibility_details': highest.get('claim_ineligibility_details') or [],
    }


def _protected_asset_coverage_record(*, protected_asset_context: dict[str, Any], coverage_status: dict[str, Any]) -> dict[str, Any]:
    provider_summary = coverage_status.get('provider_coverage_summary') if isinstance(coverage_status.get('provider_coverage_summary'), dict) else {}
    reasons = coverage_status.get('claim_ineligibility_reasons') if isinstance(coverage_status.get('claim_ineligibility_reasons'), list) else []
    reason_details = coverage_status.get('claim_ineligibility_details') if isinstance(coverage_status.get('claim_ineligibility_details'), list) else []
    return {
        'asset_id': protected_asset_context.get('asset_id'),
        'asset_identifier': protected_asset_context.get('asset_identifier'),
        'symbol': protected_asset_context.get('symbol'),
        'chain_id': protected_asset_context.get('chain_id'),
        'contract_address': protected_asset_context.get('contract_address'),
        'protected_asset_context': protected_asset_context,
        'treasury_ops_wallets': protected_asset_context.get('treasury_ops_wallets') or [],
        'custody_wallets': protected_asset_context.get('custody_wallets') or [],
        'expected_counterparties': protected_asset_context.get('expected_counterparties') or [],
        'expected_flow_patterns': protected_asset_context.get('expected_flow_patterns') or [],
        'expected_approval_patterns': protected_asset_context.get('expected_approval_patterns') or {},
        'venue_labels': protected_asset_context.get('venue_labels') or [],
        'expected_liquidity_baseline': protected_asset_context.get('expected_liquidity_baseline') or {},
        'oracle_sources': protected_asset_context.get('oracle_sources') or [],
        'expected_oracle_freshness_seconds': int(protected_asset_context.get('expected_oracle_freshness_seconds') or 0),
        'expected_oracle_update_cadence_seconds': int(protected_asset_context.get('expected_oracle_update_cadence_seconds') or 0),
        'market_coverage_status': coverage_status.get('market_coverage_status') or 'insufficient_real_evidence',
        'oracle_coverage_status': coverage_status.get('oracle_coverage_status') or 'insufficient_real_evidence',
        'market_provider_count': int(provider_summary.get('market_provider_count') or 0),
        'market_provider_reachable_count': int(provider_summary.get('market_provider_reachable_count') or 0),
        'market_provider_fresh_count': int(provider_summary.get('market_provider_fresh_count') or 0),
        'market_provider_names': provider_summary.get('market_provider_names') or [],
        'market_observation_count': int(provider_summary.get('market_observation_count') or 0),
        'oracle_provider_count': int(provider_summary.get('oracle_provider_count') or 0),
        'oracle_provider_reachable_count': int(provider_summary.get('oracle_provider_reachable_count') or 0),
        'oracle_provider_fresh_count': int(provider_summary.get('oracle_provider_fresh_count') or 0),
        'oracle_provider_names': provider_summary.get('oracle_provider_names') or [],
        'oracle_observation_count': int(provider_summary.get('oracle_observation_count') or 0),
        'enterprise_claim_eligibility': bool(coverage_status.get('enterprise_claim_eligibility')),
        'market_claim_eligible': bool(provider_summary.get('market_claim_eligible')),
        'oracle_claim_eligible': bool(provider_summary.get('oracle_claim_eligible')),
        'claim_ineligibility_reasons': reasons,
        'claim_ineligibility_details': reason_details,
    }


def _enforce_asset_detectors(asset: dict[str, Any] | None, event: ActivityEvent) -> list[dict[str, Any]]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    model = _normalized_asset_model(asset)
    protected_asset_context = _build_protected_asset_context(asset)
    coverage_status = _provider_coverage_status(event_payload=payload, protected_asset_context=protected_asset_context)
    if not model:
        return [{
            'asset_id': None,
            'asset_identifier': None,
            'symbol': None,
            'target_id': payload.get('target_id'),
            'detector_family': 'counterparty',
            'detector_status': 'insufficient_real_evidence',
            'anomaly_reason': 'missing_asset_profile',
            'severity': 'high',
            'confidence': 'low',
            'recommended_action': 'attach_asset_profile',
            'violated_asset_rule': 'asset_profile_required',
            'proof_eligibility': {'production_claim_eligible': False, 'reason': 'missing_asset_profile'},
            'evidence_origin': 'fallback',
            'provider_name': 'asset_detector',
            'observed_at': event.observed_at.isoformat(),
            'chain_id': payload.get('chain_id'),
            'tx_hash': payload.get('tx_hash'),
            'block_number': payload.get('block_number'),
            'log_index': payload.get('log_index'),
            'source_address': payload.get('from') or payload.get('owner'),
            'destination_address': payload.get('to'),
            'spender': payload.get('spender'),
            'contract_address': payload.get('contract_address'),
            'raw_event_type': payload.get('event_type') or event.kind,
            'normalized_event_snapshot': payload,
            'baseline_comparison': {'status': 'missing_asset_profile'},
            'oracle_observation_details': {},
            'liquidity_observation_details': {},
            'protected_asset_context': protected_asset_context,
            'market_coverage_status': coverage_status['market_coverage_status'],
            'oracle_coverage_status': coverage_status['oracle_coverage_status'],
            'provider_coverage_status': coverage_status['provider_coverage_status'],
            'provider_coverage_summary': coverage_status['provider_coverage_summary'],
            'enterprise_claim_eligibility': coverage_status['enterprise_claim_eligibility'],
            'claim_ineligibility_reasons': coverage_status['claim_ineligibility_reasons'],
            'claim_ineligibility_details': coverage_status.get('claim_ineligibility_details') or [],
        }]

    source = _normalize_addr(payload.get('from') or payload.get('owner'))
    destination = _normalize_addr(payload.get('to'))
    spender = _normalize_addr(payload.get('spender'))
    amount = _to_float(payload.get('amount') or payload.get('approval_amount'))

    source_class = _classify_endpoint(source, model) if source else 'unknown_external'
    destination_class = _classify_endpoint(destination, model) if destination else 'unknown_external'
    route_tuple = (source_class, destination_class)
    route_valid = (not model['allowed_routes']) or route_tuple in model['allowed_routes']
    flow_classification = _resolve_flow_classification(source_class, destination_class)
    route_stage = f'{source_class}->{destination_class}'
    lifecycle_stage = (
        'treasury_ops_egress' if source_class == 'treasury_ops'
        else ('custody_egress' if source_class == 'custody' else ('treasury_ops_ingress' if destination_class == 'treasury_ops' else ('custody_ingress' if destination_class == 'custody' else 'external_flow')))
    )

    touches_protected_path = source_class in {'treasury_ops', 'custody'} or destination_class in {'treasury_ops', 'custody'}
    unknown_counterparty = destination_class == 'unknown_external'
    high_value = amount >= 100000
    counterparty_violation = (
        (source_class == 'treasury_ops' and destination_class == 'unknown_external')
        or (source_class == 'custody' and destination_class == 'unknown_external')
        or (touches_protected_path and not route_valid)
    )
    severity = 'high' if (counterparty_violation and (high_value or unknown_counterparty or touches_protected_path)) else ('medium' if counterparty_violation else 'low')
    lifecycle_rule = (
        'treasury_ops_unknown_external_shortcut'
        if source_class == 'treasury_ops' and destination_class == 'unknown_external'
        else (
            'custody_unknown_external_shortcut'
            if source_class == 'custody' and destination_class == 'unknown_external'
            else ('unapproved_protected_route' if (touches_protected_path and not route_valid) else None)
        )
    )
    counterparty = {
        'detector_family': 'counterparty',
        'detector_status': 'anomaly_detected' if counterparty_violation else 'real_event_no_anomaly',
        'anomaly_reason': (
            'treasury_ops_to_unknown_external'
            if source_class == 'treasury_ops' and destination_class == 'unknown_external'
            else (
                'custody_to_unexpected_external'
                if source_class == 'custody' and destination_class == 'unknown_external'
                else ('protected_route_unapproved_counterparty' if counterparty_violation else 'counterparty_in_expected_profile')
            )
        ),
        'severity': severity,
        'confidence': 'high' if counterparty_violation else 'medium',
        'recommended_action': 'pause_outbound_transfer_and_review' if counterparty_violation else 'continue_monitoring',
        'violated_asset_rule': 'counterparty_allowlist',
        'route_classification': flow_classification,
        'lifecycle_stage': lifecycle_stage,
        'route_stage': route_stage,
        'violated_lifecycle_rule': lifecycle_rule,
        'endangered_asset_path': route_stage if lifecycle_rule else None,
        'venue_classification': destination_class,
        'baseline_comparison': {
            'expected_counterparties': sorted(model['expected_counterparties']),
            'treasury_ops_wallets': sorted(model['treasury_ops_wallets']),
            'custody_wallets': sorted(model['custody_wallets']),
            'observed_route': [source_class, destination_class],
        },
    }

    bypassed_checkpoint = False
    for pattern in model['expected_flow_patterns']:
        if not isinstance(pattern, dict):
            continue
        if str(pattern.get('source_class') or '').strip().lower() != source_class:
            continue
        if str(pattern.get('destination_class') or '').strip().lower() != destination_class:
            continue
        required_checkpoint = str(pattern.get('required_checkpoint') or '').strip().lower()
        if required_checkpoint and required_checkpoint not in {source_class, destination_class}:
            bypassed_checkpoint = True
    prohibited_route_shortcut = (
        source_class == 'treasury_ops' and destination_class in {'approved_external_counterparty', 'unknown_external'} and any(
            str(item.get('source_class') or '').strip().lower() == 'treasury_ops' and str(item.get('destination_class') or '').strip().lower() == 'custody'
            for item in model['expected_flow_patterns']
        )
    )
    flow_violation = touches_protected_path and (not route_valid or bypassed_checkpoint or prohibited_route_shortcut)
    flow_lifecycle_rule = (
        'bypassed_required_checkpoint'
        if bypassed_checkpoint
        else ('prohibited_route_shortcut' if prohibited_route_shortcut else ('invalid_lifecycle_transition' if flow_violation else None))
    )
    flow = {
        'detector_family': 'flow_pattern',
        'detector_status': 'anomaly_detected' if flow_violation else 'real_event_no_anomaly',
            'anomaly_reason': (
                'asset_movement_bypassed_required_checkpoint'
                if bypassed_checkpoint
                else ('invalid_protected_asset_routing' if flow_violation else 'route_matches_expected_flow_pattern')
            ),
        'severity': 'high' if flow_violation else 'low',
        'confidence': 'high',
        'recommended_action': 'block_route_and_escalate' if flow_violation else 'continue_monitoring',
        'violated_asset_rule': 'expected_flow_patterns',
        'route_classification': flow_classification,
        'lifecycle_stage': lifecycle_stage,
        'route_stage': route_stage,
        'violated_lifecycle_rule': flow_lifecycle_rule,
        'endangered_asset_path': route_stage if flow_lifecycle_rule else None,
        'venue_classification': destination_class,
            'route_classification_details': {
                'source_class': source_class,
                'destination_class': destination_class,
                'route_valid': route_valid,
                'bypassed_checkpoint': bypassed_checkpoint,
                'prohibited_route_shortcut': prohibited_route_shortcut,
                'allowed_routes': [list(item) for item in sorted(model['allowed_routes'])],
                'violated_pattern': list(route_tuple) if flow_violation else None,
            },
        'baseline_comparison': {'allowed_routes': [list(item) for item in sorted(model['allowed_routes'])], 'observed_route': [source_class, destination_class]},
    }

    approval_cfg = model['expected_approval_patterns']
    allowed_spenders = {_normalize_addr(v) for v in approval_cfg.get('allowed_spenders', []) if _normalize_addr(v)}
    max_approval = _to_float(approval_cfg.get('max_amount'))
    approval_amount = _to_float(payload.get('approval_amount') or payload.get('amount'))
    unlimited = bool(payload.get('is_unlimited_approval')) or approval_amount >= 2**255
    approval_type = str(payload.get('approval_type') or ('unlimited' if unlimited else 'bounded'))
    repeated_approval_count = int(payload.get('approval_churn_count') or 1)
    unexpected_spender = bool(spender) and allowed_spenders and spender not in allowed_spenders
    unexpected_token = bool(model.get('contract_address')) and _normalize_addr(payload.get('contract_address') or payload.get('asset_address')) not in {'', model['contract_address']}
    churn_violation = repeated_approval_count > int(approval_cfg.get('max_churn_count') or 5)
    over_limit = max_approval > 0 and approval_amount > max_approval
    approval_event = str(payload.get('kind_hint') or '').lower() == 'erc20_approval'
    has_approval_telemetry = approval_event or str(payload.get('event_type') or '').lower() == 'approval'
    approval_violation = has_approval_telemetry and (unexpected_spender or unlimited or over_limit or churn_violation or unexpected_token)
    approval = {
        'detector_family': 'approval_pattern',
        'detector_status': (
            'insufficient_real_evidence'
            if not has_approval_telemetry
            else ('anomaly_detected' if approval_violation else 'real_event_no_anomaly')
        ),
        'anomaly_reason': 'unexpected_unlimited_approval_on_protected_asset' if (approval_violation and unlimited) else ('approval_pattern_violation' if approval_violation else 'approval_within_expected_pattern'),
        'severity': (
            'medium'
            if not has_approval_telemetry
            else ('high' if (approval_violation and unlimited) else ('medium' if approval_violation else 'low'))
        ),
        'confidence': 'low' if not has_approval_telemetry else ('high' if approval_violation else 'medium'),
        'recommended_action': (
            'collect_more_real_approval_telemetry'
            if not has_approval_telemetry
            else ('revoke_approval_and_rotate_keys' if approval_violation else 'continue_monitoring')
        ),
        'violated_asset_rule': 'expected_approval_patterns',
        'lifecycle_stage': lifecycle_stage,
        'route_stage': route_stage,
        'violated_lifecycle_rule': (
            'approval_inconsistent_with_treasury_custody_lifecycle'
            if approval_violation and touches_protected_path
            else None
        ),
        'endangered_asset_path': route_stage if (approval_violation and touches_protected_path) else None,
        'baseline_comparison': {
            'allowed_spenders': sorted(allowed_spenders),
            'max_approval': max_approval,
            'approval_amount': approval_amount,
            'approval_type': approval_type,
            'unlimited': unlimited,
            'repeated_approval_count': repeated_approval_count,
            'unexpected_token': unexpected_token,
        },
    }

    liquidity_cfg = model['expected_liquidity_baseline']
    baseline_volume = _to_float(liquidity_cfg.get('baseline_outflow_volume'))
    baseline_transfer_count = int(liquidity_cfg.get('baseline_transfer_count') or 0)
    baseline_unique_counterparties = int(liquidity_cfg.get('baseline_unique_counterparties') or 0)
    baseline_max_concentration = _to_float(liquidity_cfg.get('max_concentration_ratio'))
    liquidity_observations = payload.get('liquidity_observations') if isinstance(payload.get('liquidity_observations'), list) else []
    venue_observations = payload.get('venue_observations') if isinstance(payload.get('venue_observations'), list) else []
    market_observations = payload.get('market_observations') if isinstance(payload.get('market_observations'), list) else []
    liquidity_obs = liquidity_observations[0] if liquidity_observations and isinstance(liquidity_observations[0], dict) else {}
    venue_obs = venue_observations[0] if venue_observations and isinstance(venue_observations[0], dict) else {}
    observed_volume = _to_float(liquidity_obs.get('rolling_volume'))
    transfer_count = int(liquidity_obs.get('rolling_transfer_count') or liquidity_obs.get('transfer_count') or 0)
    unique_counterparties = int(liquidity_obs.get('unique_counterparties') or 0)
    concentration_ratio = _to_float(liquidity_obs.get('concentration_ratio'))
    abnormal_outflow_ratio = _to_float(liquidity_obs.get('abnormal_outflow_ratio'))
    burst_score = _to_float(liquidity_obs.get('burst_score'))
    route_distribution = liquidity_obs.get('route_distribution') if isinstance(liquidity_obs.get('route_distribution'), dict) else {}
    observed_distribution = venue_obs.get('venue_distribution') if isinstance(venue_obs.get('venue_distribution'), dict) else {}
    expected_venues = model['venue_labels']
    unexpected_venue_share = _to_float(observed_distribution.get('unknown'))
    min_transfer_evidence = int(liquidity_cfg.get('minimum_transfer_count') or 3)
    has_distribution = bool(route_distribution) or bool(observed_distribution)
    telemetry_status = str(liquidity_obs.get('status') or 'unknown').lower()
    external_market_ready = any(
        isinstance(item, dict) and str(item.get('status') or '').lower() == 'ok'
        for item in market_observations
    )
    baseline_state = str(model.get('baseline_status') or '').lower()
    baseline_ready = baseline_state in {'ready', 'observed', 'active'} or (not baseline_state and baseline_volume > 0)
    if (
        (not baseline_ready)
        or baseline_volume <= 0
        or transfer_count < min_transfer_evidence
        or not has_distribution
        or (not external_market_ready)
        or telemetry_status in {'insufficient_real_evidence', 'unavailable', 'no_real_telemetry'}
    ):
        liquidity = {
            'detector_family': 'liquidity_venue',
            'detector_status': 'insufficient_real_evidence',
            'anomaly_reason': 'missing_real_liquidity_baseline_or_external_market_telemetry',
            'severity': 'medium',
            'confidence': 'low',
            'recommended_action': 'collect_more_real_liquidity_evidence',
            'violated_asset_rule': 'expected_liquidity_baseline',
            'lifecycle_stage': lifecycle_stage,
            'route_stage': route_stage,
            'violated_lifecycle_rule': 'insufficient_real_market_coverage',
            'endangered_asset_path': route_stage,
            'liquidity_observation_details': liquidity_obs,
            'venue_classification': destination_class,
            'route_classification': flow_classification,
            'route_classification_details': {'source_class': source_class, 'destination_class': destination_class, 'route_valid': route_valid},
            'baseline_comparison': {
                'baseline_status': model.get('baseline_status'),
                'baseline_outflow_volume': baseline_volume,
                'baseline_transfer_count': baseline_transfer_count,
                'observed_volume': observed_volume,
                'transfer_count': transfer_count,
                'external_market_observations': market_observations,
                'external_market_ready': external_market_ready,
            },
        }
    else:
        abnormal_outflow = observed_volume > baseline_volume * float(liquidity_cfg.get('abnormal_outflow_multiplier') or 2.0) or abnormal_outflow_ratio > float(liquidity_cfg.get('max_abnormal_outflow_ratio') or 0.7)
        burst_activity = (baseline_transfer_count > 0 and transfer_count > baseline_transfer_count * float(liquidity_cfg.get('burst_transfer_multiplier') or 2.0)) or burst_score > float(liquidity_cfg.get('burst_score_threshold') or 2.0)
        concentration_spike = baseline_max_concentration > 0 and concentration_ratio > baseline_max_concentration
        venue_shift = bool(expected_venues) and unexpected_venue_share > float(liquidity_cfg.get('max_unknown_venue_share') or 0.25)
        route_inconsistent = touches_protected_path and not route_valid
        counterparty_drop = baseline_unique_counterparties > 0 and unique_counterparties < baseline_unique_counterparties * float(liquidity_cfg.get('min_counterparty_ratio') or 0.5)
        reasons = [
            label for flag, label in (
                (abnormal_outflow, 'abnormal_outflow'),
                (burst_activity, 'burst_activity'),
                (concentration_spike, 'concentration_spike'),
                (venue_shift, 'unexpected_venue_shift'),
                (route_inconsistent, 'route_inconsistent_with_baseline'),
                (counterparty_drop, 'counterparty_collapse'),
            ) if flag
        ]
        liquidity_anomaly = bool(reasons)
        liquidity = {
            'detector_family': 'liquidity_venue',
            'detector_status': 'anomaly_detected' if liquidity_anomaly else 'real_event_no_anomaly',
            'anomaly_reason': '+'.join(reasons) if reasons else 'liquidity_within_baseline',
            'severity': 'high' if (liquidity_anomaly and (abnormal_outflow or route_inconsistent)) else ('medium' if liquidity_anomaly else 'low'),
            'confidence': 'high' if len(reasons) >= 2 else 'medium',
            'recommended_action': 'throttle_venue_and_investigate' if liquidity_anomaly else 'continue_monitoring',
            'violated_asset_rule': 'expected_liquidity_baseline',
            'lifecycle_stage': lifecycle_stage,
            'route_stage': route_stage,
            'violated_lifecycle_rule': 'route_inconsistent_with_protected_lifecycle' if route_inconsistent else None,
            'endangered_asset_path': route_stage if route_inconsistent else None,
            'route_classification': flow_classification,
            'venue_classification': destination_class,
            'liquidity_observation_details': liquidity_obs,
            'baseline_comparison': {
                'baseline_outflow_volume': baseline_volume,
                'baseline_transfer_count': baseline_transfer_count,
                'baseline_unique_counterparties': baseline_unique_counterparties,
                'baseline_max_concentration': baseline_max_concentration,
                'observed_volume': observed_volume,
                'transfer_count': transfer_count,
                'unique_counterparties': unique_counterparties,
                'concentration_ratio': concentration_ratio,
                'route_distribution': route_distribution,
                'venue_distribution': observed_distribution,
                'venue_labels': sorted(expected_venues),
                'unexpected_venue_share': unexpected_venue_share,
                'abnormal_outflow_ratio': abnormal_outflow_ratio,
                'burst_score': burst_score,
                'external_market_observations': market_observations,
            },
        }

    oracle_observations = payload.get('oracle_observations') if isinstance(payload.get('oracle_observations'), list) else []
    expected_freshness = int(model.get('expected_oracle_freshness_seconds') or 0)
    expected_cadence = int(model.get('expected_oracle_update_cadence_seconds') or 0)
    now = utc_now()
    observed_sources = {str(item.get('source_name') or item.get('source') or '').strip().lower() for item in oracle_observations if isinstance(item, dict)}
    required_sources = set(model['oracle_sources'])
    insufficient_oracle_telemetry = (
        not required_sources
        or not oracle_observations
        or any(str(item.get('status') or '').lower() in {'insufficient_real_evidence', 'unavailable', 'no_real_telemetry'} for item in oracle_observations if isinstance(item, dict))
        or len(observed_sources) < max(1, len(required_sources))
    )
    if insufficient_oracle_telemetry:
        if not required_sources:
            reason = 'no_oracle_provider_configured_for_asset'
        elif not oracle_observations:
            reason = 'oracle_provider_configured_but_no_observations'
        elif len(observed_sources) < max(1, len(required_sources)):
            reason = 'insufficient_oracle_source_coverage'
        else:
            reason = 'oracle_provider_unavailable_or_unreachable'
        oracle = {
            'detector_family': 'oracle_integrity',
            'detector_status': 'insufficient_real_evidence',
            'anomaly_reason': reason,
            'severity': 'high',
            'confidence': 'low',
            'recommended_action': 'restore_oracle_sources',
            'violated_asset_rule': 'oracle_sources_required',
            'lifecycle_stage': lifecycle_stage,
            'route_stage': route_stage,
            'violated_lifecycle_rule': 'oracle_coverage_missing_for_protected_asset',
            'endangered_asset_path': route_stage,
            'oracle_observation_details': {'required_sources': sorted(required_sources), 'observed_sources': sorted(observed_sources), 'observations': oracle_observations},
        }
    else:
        stale = False
        missing_update = False
        cadence_violation = False
        divergence = False
        prices: list[float] = []
        for item in oracle_observations:
            observed_ts = _parse_ts(item.get('observed_at'))
            freshness_seconds = int(item.get('freshness_seconds') or 0)
            status = str(item.get('status') or 'ok').strip().lower()
            if status in {'unavailable', 'insufficient_real_evidence'}:
                missing_update = True
            if expected_freshness and ((freshness_seconds and freshness_seconds > expected_freshness) or (observed_ts and (now - observed_ts).total_seconds() > expected_freshness)):
                stale = True
            if observed_ts is None:
                missing_update = True
            update_interval = int(item.get('update_interval_seconds') or expected_cadence or 0)
            if expected_cadence and update_interval and update_interval > expected_cadence:
                cadence_violation = True
            try:
                prices.append(float(item.get('observed_value') or item.get('price')))
            except Exception:
                continue
        if len(prices) >= 2:
            low = min(prices)
            high = max(prices)
            divergence = low > 0 and ((high - low) / low) > float(os.getenv('ORACLE_DIVERGENCE_THRESHOLD', '0.02'))
        oracle_anomaly = stale or missing_update or cadence_violation or divergence
        reasons = [
            label for flag, label in (
                (stale, 'stale_oracle'),
                (missing_update, 'missing_update'),
                (cadence_violation, 'cadence_violation'),
                (divergence, 'source_divergence'),
            ) if flag
        ]
        oracle = {
            'detector_family': 'oracle_integrity',
            'detector_status': 'anomaly_detected' if oracle_anomaly else 'real_event_no_anomaly',
            'anomaly_reason': '+'.join(reasons) if reasons else 'oracle_integrity_normal',
            'severity': 'high' if oracle_anomaly else 'low',
            'confidence': 'high' if oracle_anomaly else 'medium',
            'recommended_action': 'pause_sensitive_routes_and_reconcile_oracles' if oracle_anomaly else 'continue_monitoring',
            'violated_asset_rule': 'oracle_integrity',
            'lifecycle_stage': lifecycle_stage,
            'route_stage': route_stage,
            'violated_lifecycle_rule': 'oracle_divergence_on_protected_lifecycle' if oracle_anomaly else None,
            'endangered_asset_path': route_stage if oracle_anomaly else None,
            'oracle_observation_details': {
                'required_sources': sorted(required_sources),
                'observations': oracle_observations,
                'stale': stale,
                'missing_update': missing_update,
                'cadence_violation': cadence_violation,
                'divergence': divergence,
                'prices': prices,
            },
        }

    base = {
        'asset_id': model.get('asset_id'),
        'asset_identifier': model.get('asset_identifier'),
        'symbol': model.get('symbol'),
        'target_id': payload.get('target_id'),
        'evidence_origin': str((payload.get('metadata') or {}).get('evidence_origin') or 'real'),
        'provider_name': str((payload.get('metadata') or {}).get('provider_name') or 'unknown'),
        'observed_at': event.observed_at.isoformat(),
        'chain_id': payload.get('chain_id') or model.get('chain_id'),
        'tx_hash': payload.get('tx_hash'),
        'block_number': payload.get('block_number'),
        'log_index': payload.get('log_index'),
        'source_address': payload.get('from') or payload.get('owner'),
        'destination_address': payload.get('to'),
        'spender': payload.get('spender'),
        'contract_address': payload.get('contract_address') or model.get('contract_address'),
        'raw_event_type': payload.get('event_type') or event.kind,
        'normalized_event_snapshot': payload,
        'liquidity_observation_details': {},
        'oracle_observation_details': {},
        'route_classification_details': {'source_class': source_class, 'destination_class': destination_class, 'route_valid': route_valid},
        'proof_eligibility': {
            'production_claim_eligible': bool(coverage_status.get('enterprise_claim_eligibility')),
            'has_real_telemetry': bool(payload.get('oracle_observations') or payload.get('liquidity_observations') or payload.get('venue_observations') or payload.get('market_observations')),
        },
        'protected_asset_context': protected_asset_context,
        'market_coverage_status': coverage_status['market_coverage_status'],
        'oracle_coverage_status': coverage_status['oracle_coverage_status'],
        'provider_coverage_status': coverage_status['provider_coverage_status'],
        'provider_coverage_summary': coverage_status['provider_coverage_summary'],
        'enterprise_claim_eligibility': coverage_status['enterprise_claim_eligibility'],
        'claim_ineligibility_reasons': coverage_status['claim_ineligibility_reasons'],
        'claim_ineligibility_details': coverage_status.get('claim_ineligibility_details') or [],
    }

    # Wallet transfer detector: fires for any transaction/transfer where the monitored
    # wallet address (the asset's identifier) is the sender or recipient.
    # Only activates for wallet-type assets (no token_contract_address), preventing
    # false positives on ERC-20 contract assets.
    _wt_addr = _normalize_addr(model.get('asset_identifier'))
    _wt_is_evm_wallet = (
        bool(_wt_addr)
        and _wt_addr.startswith('0x')
        and len(_wt_addr) == 42
        and not model.get('contract_address')  # wallet assets have no contract
    )
    _wt_event_type = str(payload.get('event_type') or event.kind or '').lower()
    _wt_tx_from = _normalize_addr(payload.get('from') or payload.get('owner'))
    _wt_tx_to = _normalize_addr(payload.get('to'))
    _wt_involved = _wt_is_evm_wallet and _wt_addr in {_wt_tx_from, _wt_tx_to}
    detectors: tuple[Any, ...] = (counterparty, flow, approval, liquidity, oracle)
    if _wt_involved and _wt_event_type in {'transaction', 'transfer'}:
        _wt_direction = 'outbound' if _wt_addr == _wt_tx_from else 'inbound'
        wallet_transfer: dict[str, Any] = {
            'detector_family': 'wallet_transfer',
            'detector_status': 'anomaly_detected',
            'anomaly_reason': f'wallet_transfer_{_wt_direction}',
            'severity': 'high',
            'confidence': 'high',
            'recommended_action': 'review_wallet_transfer',
            'violated_asset_rule': 'wallet_activity_monitoring',
            'lifecycle_stage': lifecycle_stage,
            'route_stage': route_stage,
            'violated_lifecycle_rule': None,
            'endangered_asset_path': None,
            'wallet_transfer_direction': _wt_direction,
            'monitored_wallet': _wt_addr,
            'tx_hash': payload.get('tx_hash'),
            'chain_id': payload.get('chain_id'),
            'block_number': payload.get('block_number'),
            'value': payload.get('amount'),
            'event_type': 'wallet_transfer_detected',
        }
        # Prepend so wallet_transfer is the first anomalous result picked by _asset_detection_summary
        detectors = (wallet_transfer,) + detectors
    return [{**base, **item} for item in detectors]


def _signature(target_id: str, payload: dict[str, Any], response: dict[str, Any]) -> str:
    marker = {
        'target_id': target_id,
        'severity': response.get('severity'),
        'action': response.get('recommended_action'),
        'patterns': [str(item.get('label') or item) for item in (response.get('matched_patterns') or [])],
        'reason': response.get('explanation'),
        'event_id': payload.get('metadata', {}).get('event_id'),
    }
    return uuid.uuid5(uuid.NAMESPACE_DNS, json.dumps(marker, sort_keys=True)).hex


def _upsert_alert(
    connection: Any,
    *,
    workspace_id: str,
    user_id: str,
    target_id: str,
    analysis_run_id: str | None,
    title: str,
    response: dict[str, Any],
    signature: str,
    detection_id: str | None = None,
    out: dict[str, Any] | None = None,
    module_key: str | None = None,
) -> str:
    # `out` is an optional mutable dict the caller can pass to learn whether a new
    # alert row was inserted ({'created': True}) or an existing alert was reused
    # ({'created': False}). Used by open_alert_from_detection to return an accurate
    # 201 (created) vs 409 (already exists) HTTP status. Backward compatible: callers
    # that omit `out` are unaffected.
    if out is not None:
        out['created'] = False
    suppression = connection.execute(
        '''
        SELECT id
        FROM alert_suppression_rules
        WHERE workspace_id = %s
          AND (target_id IS NULL OR target_id = %s::uuid)
          AND (dedupe_signature IS NULL OR dedupe_signature = %s)
          AND (mute_until IS NULL OR mute_until >= NOW())
        LIMIT 1
        ''',
        (workspace_id, target_id, signature),
    ).fetchone()
    if suppression is not None:
        return ''
    cutoff = utc_now() - timedelta(seconds=ALERT_DEDUPE_WINDOW_SECONDS)
    existing = connection.execute(
        '''
        SELECT id, occurrence_count
        FROM alerts
        WHERE workspace_id = %s AND target_id = %s AND dedupe_signature = %s AND created_at >= %s
        ORDER BY created_at DESC
        LIMIT 1
        ''',
        (workspace_id, target_id, signature, cutoff),
    ).fetchone()
    if existing is not None:
        connection.execute(
            '''
            UPDATE alerts
            SET occurrence_count = COALESCE(occurrence_count, 1) + 1,
                last_seen_at = NOW(),
                updated_at = NOW(),
                summary = %s,
                reasons = %s::jsonb,
                matched_patterns = %s::jsonb,
                recommended_action = %s,
                degraded = %s,
                detection_id = COALESCE(%s::uuid, detection_id)
            WHERE id = %s
            ''',
            (
                str(response.get('explanation') or title),
                _json_dumps(response.get('reasons') or []),
                _json_dumps(response.get('matched_patterns') or []),
                str(response.get('recommended_action') or 'review'),
                bool(response.get('degraded', False)),
                detection_id,
                existing['id'],
            ),
        )
        return str(existing['id'])

    alert_id = str(uuid.uuid4())
    if out is not None:
        out['created'] = True
    connection.execute(
        '''
        INSERT INTO alerts (
            id, workspace_id, user_id, analysis_run_id, target_id, module_key, alert_type, title, severity, status,
            source_service, source, summary, payload, matched_patterns, reasons, recommended_action,
            degraded, dedupe_signature, detection_id, occurrence_count, first_seen_at, last_seen_at, created_at, updated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'open', %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, %s, %s::uuid, 1, NOW(), NOW(), NOW(), NOW())
        ''',
        (
            alert_id,
            workspace_id,
            user_id,
            analysis_run_id,
            target_id,
            module_key,
            'threat_monitoring',
            title,
            str(response.get('severity') or 'medium'),
            'threat-engine',
            str(response.get('source') or 'live'),
            str(response.get('explanation') or title),
            _json_dumps(response),
            _json_dumps(response.get('matched_patterns') or []),
            _json_dumps(response.get('reasons') or []),
            str(response.get('recommended_action') or 'review'),
            bool(response.get('degraded', False)),
            signature,
            detection_id,
        ),
    )
    return alert_id


def _create_detection(
    connection: Any,
    *,
    workspace_id: str,
    monitored_system_id: str | None,
    protected_asset_id: str | None,
    detection_type: str,
    severity: str,
    confidence: float | None,
    title: str,
    evidence_summary: str,
    evidence_source: str,
    source_rule: str | None,
    raw_evidence_json: dict[str, Any],
    monitoring_run_id: str | None,
) -> str:
    detection_id = str(uuid.uuid4())
    connection.execute(
        '''
        INSERT INTO detections (
            id,
            workspace_id,
            monitored_system_id,
            protected_asset_id,
            detection_type,
            severity,
            confidence,
            title,
            evidence_summary,
            evidence_source,
            source_rule,
            status,
            detected_at,
            raw_evidence_json,
            monitoring_run_id,
            linked_alert_id,
            created_at,
            updated_at
        )
        VALUES (
            %s,
            %s::uuid,
            %s::uuid,
            %s::uuid,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            'open',
            NOW(),
            %s::jsonb,
            %s::uuid,
            NULL,
            NOW(),
            NOW()
        )
        ''',
        (
            detection_id,
            workspace_id,
            monitored_system_id,
            protected_asset_id,
            detection_type,
            severity,
            confidence,
            title,
            evidence_summary,
            evidence_source,
            source_rule,
            _json_dumps(raw_evidence_json),
            monitoring_run_id,
        ),
    )
    return detection_id


def _maybe_create_incident(connection: Any, *, workspace_id: str, user_id: str, target_id: str, analysis_run_id: str, alert_id: str, response: dict[str, Any], auto_create: bool) -> str | None:
    severity = str(response.get('severity') or 'low').lower()
    if not (severity == 'critical' or auto_create):
        return None
    incident_id = str(uuid.uuid4())
    title = f"{severity.upper()} monitoring incident"
    connection.execute(
        '''
        INSERT INTO incidents (
            id, workspace_id, user_id, analysis_run_id, target_id, event_type, title, severity, status,
            source_alert_id, summary, linked_alert_ids, timeline, payload, created_at, updated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'open', %s::uuid, %s, %s::jsonb, %s::jsonb, %s::jsonb, NOW(), NOW())
        ''',
        (
            incident_id,
            workspace_id,
            user_id,
            analysis_run_id,
            target_id,
            'threat_monitoring_incident',
            title,
            severity,
            alert_id,
            str(response.get('explanation') or title),
            _json_dumps([alert_id]),
            _json_dumps([{'event': 'incident.created', 'at': utc_now().isoformat(), 'alert_id': alert_id}]),
            _json_dumps(response),
        ),
    )
    return incident_id


def _process_single_event(
    connection: Any,
    *,
    target: dict[str, Any],
    workspace: dict[str, Any],
    user_id: str,
    monitoring_run_id: str,
    event: ActivityEvent,
    monitoring_path: str = 'worker',
    configured_scenario: str | None = None,
) -> dict[str, Any]:
    asset = _load_target_asset_context(connection, workspace_id=str(target['workspace_id']), target=target)
    kind, normalized = _normalize_event(target, event, monitoring_run_id, workspace)
    ingestion_runtime = monitoring_ingestion_runtime()
    if ingestion_runtime.get('mode') in {'live', 'hybrid'} and str(event.ingestion_source or '').lower() == 'demo':
        raise RuntimeError('synthetic event leakage blocked in live/hybrid monitoring')
    response, diagnostics = _threat_call(kind, normalized, target_id=str(target['id']))
    if response is None:
        WORKER_STATE['metrics']['analysis_failures'] += 1
        raise RuntimeError(f"analysis_unavailable:{diagnostics.get('fallback_reason') or 'threat_engine_unavailable'}")
    else:
        response['analysis_source'] = str(response.get('source') or 'live')
        response['analysis_status'] = 'completed'
        response['degraded_reason'] = None
    response['ingestion_mode'] = ingestion_runtime.get('mode')
    response['monitoring_path'] = monitoring_path
    response_metadata = response.get('metadata') if isinstance(response.get('metadata'), dict) else {}
    has_confirmed_anomaly = bool(response.get('matched_patterns')) or str(response.get('severity') or '').lower() in {'high', 'critical'}
    detection_outcome = (
        'DEMO_ONLY'
        if response_metadata.get('ingestion_source') == 'demo'
        else (
            'ANALYSIS_FAILED'
            if response.get('analysis_status') == 'analysis_failed'
            else ('DETECTION_CONFIRMED' if has_confirmed_anomaly else 'NO_CONFIRMED_ANOMALY_FROM_REAL_EVIDENCE')
        )
    )
    truthfulness_state = 'not_claim_safe'
    response['claim_safe'] = False
    response_metadata.update(
        {
            'monitoring_analysis_type': f'monitoring_{kind}',
            'monitoring_request_keys': sorted(normalized.keys()),
            'monitoring_request_metadata_keys': sorted((normalized.get('metadata') or {}).keys()) if isinstance(normalized.get('metadata'), dict) else [],
            'evidence_state': 'demo' if response_metadata.get('ingestion_source') == 'demo' else ('degraded' if response.get('degraded') else 'real'),
            'confidence_basis': 'demo_scenario' if response_metadata.get('ingestion_source') == 'demo' else ('none' if response.get('degraded') else 'provider_evidence'),
            'truthfulness_state': truthfulness_state,
            'detection_outcome': detection_outcome,
        }
    )
    response['metadata'] = response_metadata
    detector_results = _enforce_asset_detectors(asset=asset, event=event)
    asset_detection = _asset_detection_summary(asset=asset, event=event)
    response['asset_profile_id'] = (asset or {}).get('id')
    response['asset_label'] = (asset or {}).get('name') or target.get('name')
    response['detection_family'] = asset_detection.get('detection_family')
    response['detector_family'] = asset_detection.get('detection_family')
    response['detector_status'] = asset_detection.get('detector_status')
    response['detector_results'] = detector_results
    response['anomaly_basis'] = asset_detection.get('anomaly_basis')
    response['baseline_reference'] = asset_detection.get('baseline_reference') or {
        'status': (asset or {}).get('baseline_status', 'missing'),
        'source': (asset or {}).get('baseline_source'),
        'updated_at': (asset or {}).get('baseline_updated_at'),
        'confidence': (asset or {}).get('baseline_confidence'),
        'coverage': (asset or {}).get('baseline_coverage'),
    }
    response['confidence_basis'] = asset_detection.get('confidence_basis')
    response['recommended_action'] = asset_detection.get('recommended_action') or response.get('recommended_action')
    response['protected_asset_context'] = asset_detection.get('protected_asset_context') or _build_protected_asset_context(asset, target=target)
    response['market_coverage_status'] = asset_detection.get('market_coverage_status') or 'insufficient_real_evidence'
    response['oracle_coverage_status'] = asset_detection.get('oracle_coverage_status') or 'insufficient_real_evidence'
    response['provider_coverage_status'] = asset_detection.get('provider_coverage_status') or {}
    response['provider_coverage_summary'] = asset_detection.get('provider_coverage_summary') or {}
    response['enterprise_claim_eligibility'] = bool(asset_detection.get('enterprise_claim_eligibility'))
    response['claim_ineligibility_reasons'] = asset_detection.get('claim_ineligibility_reasons') or []
    response['claim_ineligibility_details'] = asset_detection.get('claim_ineligibility_details') or []
    response['protected_asset_coverage_record'] = _protected_asset_coverage_record(
        protected_asset_context=response['protected_asset_context'],
        coverage_status={
            'market_coverage_status': response['market_coverage_status'],
            'oracle_coverage_status': response['oracle_coverage_status'],
            'provider_coverage_summary': response['provider_coverage_summary'],
            'enterprise_claim_eligibility': response['enterprise_claim_eligibility'],
            'claim_ineligibility_reasons': response['claim_ineligibility_reasons'],
            'claim_ineligibility_details': response['claim_ineligibility_details'],
        },
    )
    if asset_detection.get('severity'):
        response['severity'] = asset_detection['severity']
    payload = event.payload if isinstance(event.payload, dict) else {}
    response['observed_evidence'] = {
        'event_id': event.event_id,
        'tx_hash': payload.get('tx_hash'),
        'block_number': payload.get('block_number'),
        'log_index': payload.get('log_index'),
        'observed_at': event.observed_at.isoformat(),
        'ingestion_source': event.ingestion_source,
        'evidence_origin': str((payload.get('metadata') or {}).get('evidence_origin') or event.ingestion_source),
        'provider_name': str((payload.get('metadata') or {}).get('provider_name') or 'unknown'),
    }
    response['evidence_window'] = {'start': event.observed_at.isoformat(), 'end': event.observed_at.isoformat()}
    analysis_run_id = persist_analysis_run(
        connection,
        workspace_id=str(target['workspace_id']),
        user_id=user_id,
        analysis_type=f'monitoring_{kind}',
        service_name='threat-engine',
        title=f'Automatic {kind} monitoring run',
        status_value='completed',
        request_payload=normalized,
        response_payload=response,
        request=None,
    )
    alert_id = None
    detection_id = None
    incident_id = None
    severity_threshold = str(target.get('severity_threshold') or 'medium')
    matched_patterns = response.get('matched_patterns') if isinstance(response.get('matched_patterns'), list) else []
    should_create_detection = bool(matched_patterns) or _severity_meets_threshold(
        str(response.get('severity') or 'low'),
        severity_threshold,
    )
    if should_create_detection:
        confidence_raw = response.get('confidence')
        confidence = float(confidence_raw) if isinstance(confidence_raw, (int, float)) else None
        source_rule = None
        first_match = matched_patterns[0] if matched_patterns else None
        if isinstance(first_match, dict):
            source_rule = str(first_match.get('label') or first_match.get('rule_id') or '').strip() or None
        elif first_match is not None:
            source_rule = str(first_match).strip() or None
        evidence_source = _normalize_detection_evidence_source(
            ingestion_source=event.ingestion_source,
            analysis_source=response.get('source'),
            ingestion_mode=response.get('ingestion_mode'),
        )
        try:
            with connection.transaction():
                detection_id = _create_detection(
                    connection,
                    workspace_id=str(target['workspace_id']),
                    monitored_system_id=str(target.get('monitored_system_id') or '') or None,
                    protected_asset_id=str(target.get('asset_id') or '') or None,
                    detection_type=f'monitoring_{kind}',
                    severity=str(response.get('severity') or 'medium'),
                    confidence=confidence,
                    title=f"{target.get('name')}: {response.get('severity', 'medium')} risk",
                    evidence_summary=str(response.get('explanation') or response.get('summary') or 'Rule matched from monitored evidence.'),
                    evidence_source=evidence_source,
                    source_rule=source_rule,
                    raw_evidence_json={
                        'event': normalized,
                        'response': response,
                        'event_id': event.event_id,
                    },
                    monitoring_run_id=monitoring_run_id,
                )
        except Exception as _det_exc:
            logger.warning(
                'detection_insert_recoverable target_id=%s monitoring_run_id=%s '
                'error=%s action=skip_detection_continue',
                target.get('id'), monitoring_run_id, str(_det_exc)[:300],
            )
            detection_id = None
    if bool(target.get('auto_create_alerts', True)) and _severity_meets_threshold(str(response.get('severity') or 'low'), severity_threshold):
        signature = _signature(str(target['id']), normalized, response)
        try:
            with connection.transaction():
                alert_id = _upsert_alert(
                    connection,
                    workspace_id=str(target['workspace_id']),
                    user_id=user_id,
                    target_id=str(target['id']),
                    analysis_run_id=analysis_run_id,
                    title=f"{target.get('name')}: {response.get('severity', 'medium')} risk",
                    response=response,
                    signature=signature,
                    detection_id=detection_id,
                )
        except Exception as _alert_exc:
            logger.warning(
                'alert_insert_recoverable target_id=%s monitoring_run_id=%s '
                'error=%s action=skip_alert_continue',
                target.get('id'), monitoring_run_id, str(_alert_exc)[:300],
            )
            alert_id = None
        if alert_id:
            if detection_id:
                try:
                    with connection.transaction():
                        connection.execute(
                            '''
                            UPDATE detections
                            SET linked_alert_id = %s::uuid,
                                status = 'escalated',
                                updated_at = NOW()
                            WHERE id = %s::uuid
                            ''',
                            (alert_id, detection_id),
                        )
                except Exception as _link_exc:
                    logger.warning(
                        'detection_alert_link_recoverable target_id=%s detection_id=%s alert_id=%s '
                        'error=%s action=skip_link_continue',
                        target.get('id'), detection_id, alert_id, str(_link_exc)[:200],
                    )
            incident_id = _maybe_create_incident(
                connection,
                workspace_id=str(target['workspace_id']),
                user_id=user_id,
                target_id=str(target['id']),
                analysis_run_id=analysis_run_id,
                alert_id=alert_id,
                response=response,
                auto_create=bool(target.get('auto_create_incidents'))
                and str(response.get('severity') or 'low').lower() in {'high', 'critical'}
                and _severity_meets_threshold(str(response.get('severity') or 'low'), severity_threshold),
            )
            _record_detection_metric(
                connection,
                workspace_id=str(target['workspace_id']),
                alert_id=alert_id,
                incident_id=incident_id,
                target_id=str(target['id']),
                asset_id=str(target.get('asset_id')) if target.get('asset_id') else None,
                event=event,
                response=response,
                policy_snapshot_hash=signature,
            )
    _persist_evidence(
        connection,
        workspace_id=str(target['workspace_id']),
        target=target,
        event=event,
        response=response,
        alert_id=alert_id,
    )
    response['monitoring_state'] = (
        'anomaly_escalated_to_incident' if incident_id else (
            'real_event_anomaly_detected' if asset_detection.get('detector_status') == 'anomaly_detected' else (
                'insufficient_real_evidence' if asset_detection.get('detector_status') == 'insufficient_real_evidence' else 'real_event_no_anomaly'
            )
        )
    )
    return {
        'analysis_run_id': analysis_run_id,
        'detection_id': detection_id,
        'alert_id': alert_id,
        'incident_id': incident_id,
        'monitoring_state': response.get('monitoring_state'),
        'protected_asset_coverage_record': response.get('protected_asset_coverage_record') or {},
    }


def _persist_detection_evaluation_checkpoint(
    connection: Any,
    *,
    workspace_id: str,
    user_id: str,
    target: dict[str, Any],
    monitoring_run_id: str,
    monitoring_path: str,
    provider_result: ActivityProviderResult,
    events_ingested: int,
    detections_created: int,
    alerts_generated: int,
    incidents_created: int,
) -> str:
    if provider_result.status == 'failed':
        detection_outcome = 'ANALYSIS_FAILED'
    elif detections_created > 0:
        detection_outcome = 'DETECTION_CONFIRMED'
    else:
        detection_outcome = 'NO_CONFIRMED_ANOMALY_FROM_REAL_EVIDENCE'
    checkpoint_payload = {
        'analysis_source': 'monitoring_worker_checkpoint',
        'analysis_status': 'completed' if provider_result.status != 'failed' else 'analysis_failed',
        'degraded': provider_result.status in {'degraded', 'failed'},
        'degraded_reason': provider_result.degraded_reason if provider_result.status in {'degraded', 'failed'} else None,
        'monitoring_path': monitoring_path,
        'ingestion_mode': provider_result.mode,
        'metadata': {
            'monitoring_analysis_type': 'monitoring_detection_evaluation',
            'detection_outcome': detection_outcome,
            'evidence_state': ui_evidence_state(provider_result.evidence_state).lower(),
            'confidence_basis': (
                'demo_scenario'
                if provider_result.synthetic
                else ('provider_evidence' if events_ingested > 0 else ('provider_coverage' if provider_result.status == 'live' else 'none'))
            ),
            'truthfulness_state': ui_truthfulness_state(provider_result.truthfulness_state),
            'recent_real_event_count': int(provider_result.recent_real_event_count),
            'last_real_event_at': provider_result.last_real_event_at.isoformat() if provider_result.last_real_event_at else None,
            'monitoring_run_id': monitoring_run_id,
            'target_id': str(target.get('id') or ''),
            'status': str(provider_result.status or 'unknown'),
            'evaluation_completed': True,
            'no_detections': detections_created == 0,
            'events_ingested': int(events_ingested),
            'detections_created': int(detections_created),
            'alerts_generated': int(alerts_generated),
            'incidents_created': int(incidents_created),
        },
    }
    return persist_analysis_run(
        connection,
        workspace_id=workspace_id,
        user_id=user_id,
        analysis_type='monitoring_detection_evaluation',
        service_name='monitoring-worker',
        title=f"{target.get('name') or 'Target'} detection evaluation checkpoint",
        status_value='completed' if provider_result.status != 'failed' else 'failed',
        request_payload={
            'target_id': str(target.get('id') or ''),
            'monitoring_run_id': monitoring_run_id,
            'provider_status': provider_result.status,
        },
        response_payload=checkpoint_payload,
        request=None,
    )


def _provider_backoff_skip_active(provider_result: Any) -> bool:
    """True when this cycle's target poll must be SKIPPED because the Base RPC 429
    backoff is active — no real RPC poll happened, so no poll-shaped work may run.

    Detects the backoff three ways so a rate-limited poll can never be presented as a
    completed poll (and its partial scan ceiling can never be persisted as the cursor):

      * the provider result is explicitly tagged ``PROVIDER_BACKOFF_ACTIVE``; or
      * it is ``degraded`` with ``degraded_reason='provider_backoff_active'``; or
      * the process-wide RPC backoff is active right now. A 429 received *during* this
        fetch arms the backoff after ``fetch_target_activity_result`` has already shaped
        a partial 'live'/coverage result (e.g. PROVIDER_COVERAGE_VERIFIED, or even
        REAL_EVIDENCE from blocks scanned before the 429). Such a result is not tagged
        as backoff, so we fall back to the canonical global backoff fact here.
    """
    reason_code = getattr(provider_result, 'reason_code', None)
    if reason_code == 'PROVIDER_BACKOFF_ACTIVE':
        return True
    degraded_reason = str(getattr(provider_result, 'degraded_reason', '') or '').strip().lower()
    if getattr(provider_result, 'status', None) == 'degraded' and degraded_reason == 'provider_backoff_active':
        return True
    try:
        return bool(rpc_provider_backoff_active())
    except Exception:
        return False


def _provider_observation_outcome(provider_result: Any, *, chain_mismatch: bool) -> str:
    """Map a provider result to a truthful ``provider_observation`` outcome.

    A chain mismatch or an active provider 429 backoff means NO real provider
    observation happened this cycle — no RPC call was made — so it is reported as
    ``skipped``, never ``success`` (which would let a rate-limited or wrong-chain
    target masquerade as a healthy live observation). A reachable provider whose
    eth_getLogs scan failed or was reduced for a 413 (``LOG_SCAN_FAILED`` /
    ``LOG_SCAN_DEGRADED``) is ``degraded`` — the provider answered but did not deliver
    verified log coverage, so it must never be ``success``. Otherwise a reachable
    provider (live / no_evidence / degraded) is ``success`` and a hard failure is
    ``failure``.
    """
    reason_code = getattr(provider_result, 'reason_code', None)
    if chain_mismatch or reason_code in {'CHAIN_RPC_MISMATCH', 'PROVIDER_BACKOFF_ACTIVE'}:
        return 'skipped'
    # An active process-wide RPC backoff means no real observation happened this cycle,
    # even when a 429 mid-fetch left the result shaped as live/coverage — report skipped.
    if _provider_backoff_skip_active(provider_result):
        return 'skipped'
    # Log-scan coverage degraded/failed (eth_blockNumber works, but eth_getLogs returned
    # 413 query-too-large or a non-413 error): the provider is REACHABLE but did not deliver
    # verified log coverage. Report 'degraded' — never 'success' — so a failed/partial log
    # scan can never masquerade as a healthy live observation (status_reason carries
    # query_too_large / logs_fetch_failed on the provider_observation log line below).
    if reason_code in {'LOG_SCAN_FAILED', 'LOG_SCAN_DEGRADED'}:
        return 'degraded'
    _degraded_reason = str(getattr(provider_result, 'degraded_reason', '') or '').strip().lower()
    if reason_code is None and _degraded_reason in {'logs_fetch_failed', 'query_too_large'}:
        return 'degraded'
    if getattr(provider_result, 'status', None) in {'live', 'no_evidence', 'degraded'}:
        return 'success'
    return 'failure'


def process_monitoring_target(
    connection: Any,
    target: dict[str, Any],
    *,
    triggered_by_user_id: str | None = None,
    monitoring_run_id: str | None = None,
) -> dict[str, Any]:
    workspace_row = connection.execute('SELECT id, name FROM workspaces WHERE id = %s', (target['workspace_id'],)).fetchone() or {'id': target['workspace_id'], 'name': 'Workspace'}
    workspace = _json_safe_value(dict(workspace_row))
    user_id = triggered_by_user_id or str(target.get('updated_by_user_id') or target.get('created_by_user_id'))
    if monitoring_run_id is None:
        monitoring_run_id = str(uuid.uuid4())
        connection.execute(
            '''
            INSERT INTO monitoring_runs (
                id, workspace_id, started_at, status, trigger_type,
                systems_checked_count, assets_checked_count, detections_created_count,
                alerts_created_count, telemetry_records_seen_count, notes
            )
            VALUES (%s::uuid, %s::uuid, NOW(), 'running', %s, 0, 0, 0, 0, 0, %s)
            ''',
            (
                monitoring_run_id,
                str(target['workspace_id']),
                'manual' if triggered_by_user_id else 'worker_direct',
                f'target_id={target.get("id")}',
            ),
        )
    logger.info(
        'monitoring_run_bound target_id=%s workspace_id=%s monitoring_run_id=%s',
        target.get('id'), target.get('workspace_id'), monitoring_run_id,
    )
    monitoring_path = 'manual_run_once' if triggered_by_user_id else 'worker'
    checkpoint = _parse_ts(target.get('monitoring_checkpoint_at') or target.get('last_checked_at'))
    chain = str(target.get('chain_network') or os.getenv('EVM_CHAIN_NETWORK', 'ethereum')).strip().lower()
    monitored_system_id = str(target.get('monitored_system_id') or '') or None
    checkpoint_block = _load_checkpoint(
        connection,
        workspace_id=str(target['workspace_id']),
        monitored_system_id=monitored_system_id,
        chain=chain,
        fallback_block=int(target.get('watcher_last_observed_block') or 0),
    )
    if checkpoint_block > 0:
        target['monitoring_checkpoint_cursor'] = f"{checkpoint_block}:checkpoint:-1"
    # For wallet targets whose canonical wallet_address column is empty, try to
    # resolve the monitored wallet from a fallback location (address typed into
    # contract_identifier, or the linked asset's identifier). This keeps the
    # provider scan, downstream detection, and polling-cycle logs consistent
    # (showing the real wallet instead of n/a). Scoped to the wallet+empty case
    # to avoid changing behavior for correctly configured or contract targets.
    if str(target.get('target_type') or '').lower() == 'wallet' and not target.get('wallet_address'):
        if target.get('asset_context') is None:
            _wallet_asset_context = _load_target_asset_context(
                connection, workspace_id=str(target['workspace_id']), target=target
            )
            if isinstance(_wallet_asset_context, dict):
                target['asset_context'] = _wallet_asset_context
        _resolved_wallet = resolve_monitored_wallet(target)
        if _resolved_wallet:
            logger.info(
                'monitored_wallet_resolved_from_fallback target_id=%s monitored_wallet=%s',
                target.get('id'), _resolved_wallet,
            )
            target['wallet_address'] = _resolved_wallet
    provider_result: ActivityProviderResult = fetch_target_activity_result(target, checkpoint)
    provider_checked_at = utc_now()
    # Detect chain mismatch set by evm_activity_provider — mark target unhealthy and log it
    # so operators can identify misconfigured ethereum-mainnet targets sharing a Base RPC.
    # This does not affect correctly configured Base targets.
    if target.get('_evm_chain_mismatch'):
        logger.warning(
            'target_chain_mismatch_detected target_id=%s workspace_id=%s chain=%s mismatch_reason=%s '
            'action=mark_unhealthy_skip_coverage',
            target.get('id'), target.get('workspace_id'), chain,
            target.get('_evm_chain_mismatch_reason', 'unknown'),
        )
    provider_error_message = str(provider_result.degraded_reason or '').strip() or None
    if target.get('_evm_chain_mismatch') and not provider_error_message:
        provider_error_message = str(target.get('_evm_chain_mismatch_reason') or 'chain_mismatch')
    provider_health_status = 'healthy' if provider_result.status == 'live' else ('degraded' if provider_result.status in {'no_evidence', 'degraded'} else 'error')
    if target.get('_evm_chain_mismatch'):
        provider_health_status = 'error'
    provider_evidence_source = _telemetry_event_evidence_source(provider_result=provider_result, source_type=str(provider_result.source_type or '').strip().lower())
    if provider_evidence_source not in {'live', 'simulator', 'replay'}:
        provider_evidence_source = 'none'
    connection.execute(
        '''
        INSERT INTO provider_health_records (
            id, workspace_id, provider_type, target_id, status, checked_at, latency_ms, error_message, evidence_source, metadata
        )
        VALUES (%s::uuid, %s::uuid, %s, %s::uuid, %s, %s, %s, %s, %s, %s::jsonb)
        ''',
        (
            str(uuid.uuid4()),
            str(target['workspace_id']),
            str(provider_result.provider_name or provider_result.source_type or 'monitoring_provider'),
            str(target['id']),
            provider_health_status,
            provider_checked_at,
            None,
            provider_error_message,
            provider_evidence_source,
            _json_dumps({'provider_status': provider_result.status, 'mode': provider_result.mode, 'source_type': provider_result.source_type}),
        ),
    )
    events = provider_result.events
    source_type = str(provider_result.source_type or '').strip().lower()
    live_source_eligible = _provider_source_is_live(source_type)
    # Truthfulness: a chain mismatch or an active provider 429 backoff means NO real
    # provider observation happened this cycle — no RPC call was made. It must never be
    # recorded as result=success (that would let a rate-limited / wrong-chain target look
    # like a healthy live observation). Surface it as result=skipped with the reason.
    provider_observation_outcome = _provider_observation_outcome(
        provider_result, chain_mismatch=bool(target.get('_evm_chain_mismatch'))
    )
    logger.info(
        'provider_observation workspace_id=%s target_id=%s result=%s source_type=%s status_reason=%s',
        target.get('workspace_id'),
        target.get('id'),
        provider_observation_outcome,
        provider_result.source_type or 'unknown',
        provider_result.degraded_reason,
    )
    logger.info(
        'provider_fetch_checkpoint workspace_id=%s target_id=%s mode=%s status=%s source_type=%s live_source_eligible=%s event_count=%s',
        target.get('workspace_id'),
        target.get('id'),
        provider_result.mode,
        provider_result.status,
        source_type or 'unknown',
        live_source_eligible,
        len(events),
    )
    # Provider 429 backoff: the process-wide RPC backoff is active, so NO RPC poll
    # happened this cycle. This is detected via _provider_backoff_skip_active, which
    # catches both an explicitly backoff-tagged provider result AND the case where a 429
    # armed the backoff *during* this fetch (leaving a partial 'live'/coverage-verified
    # result whose advanced scan ceiling must never be persisted). Short-circuit before
    # any poll-shaped work so we never present a poll that did not occur. Specifically we
    # do NOT: log polling_cycle_start, advance/persist the scan cursor, write a receipt
    # checkpoint or coverage telemetry, mark the target checked, touch
    # latest_processed_block, or count it as checked (run_monitoring_cycle treats a
    # provider_poll_skipped result as skipped_provider_backoff, not checked). Only release
    # the worker's claim so the next cycle can re-poll once the backoff clears — the target
    # stays "due" because last_checked_at is deliberately left unchanged.
    if _provider_backoff_skip_active(provider_result):
        try:
            _backoff_until = rpc_provider_backoff_status().get('backoff_until')
        except Exception:
            _backoff_until = None
        logger.warning(
            'provider_poll_skipped target_id=%s workspace_id=%s reason=provider_backoff_active backoff_until=%s',
            target.get('id'), target.get('workspace_id'), _backoff_until or 'unknown',
        )
        connection.execute(
            '''
            UPDATE targets
            SET watcher_source_status = 'degraded',
                watcher_degraded_reason = 'provider_backoff_active',
                monitoring_claimed_by = NULL,
                monitoring_claimed_at = NULL,
                monitoring_lease_token = NULL,
                monitoring_lease_expires_at = NULL,
                updated_at = NOW()
            WHERE id = %s
              AND workspace_id = %s
            ''',
            (target['id'], target['workspace_id']),
        )
        return {
            'target_id': str(target['id']),
            'target_type': str(target.get('target_type') or ''),
            'monitoring_run_id': monitoring_run_id,
            'runs': [],
            'alerts_generated': 0,
            'incidents_created': 0,
            'detections_created': 0,
            'events_ingested': 0,
            'real_events_detected': 0,
            'real_event_count': 0,
            'coverage_heartbeat_updates': 0,
            'coverage_heartbeat_count': 0,
            'telemetry_records_seen': 0,
            'evaluated_no_threat_marker_id': None,
            'stale_open_alerts_closed': 0,
            'status': 'provider_backoff_skipped',
            'status_reason_code': 'provider_backoff_active',
            'provider_poll_skipped': True,
            'provider_backoff_active': True,
            'backoff_until': _backoff_until,
            'latest_processed_block': int(target.get('watcher_last_observed_block') or 0),
            'source_status': 'degraded',
            'degraded_reason': 'provider_backoff_active',
            # A backoff-skipped poll is never 'live' and never carries real evidence, even
            # when a 429 mid-fetch left provider_result shaped as live/REAL_EVIDENCE. Pin
            # these to truthful degraded/unknown values so a skipped poll cannot surface as
            # healthy live customer evidence anywhere this result is consumed.
            'provider_status': 'degraded',
            'provider_source_type': provider_result.source_type,
            'synthetic': False,
            'recent_evidence_state': ui_evidence_state('DEGRADED_EVIDENCE'),
            'recent_truthfulness_state': ui_truthfulness_state('UNKNOWN_RISK'),
            'recent_real_event_count': 0,
            'last_event_at': None,
            'last_real_event_at': None,
            'live_coverage_telemetry_at': None,
            'protected_asset_coverage_record': {},
        }
    evaluation_id = str(uuid.uuid4())
    connection.execute(
        '''
        INSERT INTO target_evaluation (id, target_id, status, started_at, events_seen, matches_found)
        VALUES (%s, %s, %s, NOW(), 0, 0)
        ''',
        (evaluation_id, target['id'], 'running'),
    )

    alerts_generated = 0
    incidents_created = 0
    detections_created = 0
    evaluated_no_threat_marker_id: str | None = None
    monitored_systems_updated = 0
    run_ids: list[str] = []
    last_status = 'no_evidence' if provider_result.status == 'no_evidence' else str(provider_result.status or 'no_evidence')
    last_run_id: str | None = None
    last_alert_at: datetime | None = None
    checkpoint_cursor = target.get('monitoring_checkpoint_cursor')
    checkpoint_at = checkpoint
    last_observed_event_at = provider_result.last_real_event_at
    _raw_watcher_block = int(target.get('watcher_last_observed_block') or 0)
    if _raw_watcher_block > 500_000_000:
        logger.error(
            'code=WATCHER_BLOCK_CORRUPT_RESET source=process_monitoring_target '
            'target_id=%s chain=%s corrupt_block=%s action=reset_to_zero',
            target.get('id'), chain, _raw_watcher_block,
        )
        _raw_watcher_block = 0
    latest_processed_block = _raw_watcher_block
    last_protected_asset_coverage_record: dict[str, Any] = {}
    source_status = (
        'active'
        if provider_result.evidence_state in {'REAL_EVIDENCE', 'DEMO_EVIDENCE'}
        else ('no_evidence' if provider_result.evidence_state == 'NO_EVIDENCE' else ('failed' if provider_result.evidence_state == 'FAILED_EVIDENCE' else 'degraded'))
    )
    degraded_reason: str | None = provider_result.degraded_reason
    logger.info('monitoring target fetched target=%s threshold=%s auto_create_alerts=%s', target.get('id'), str(target.get('severity_threshold') or 'medium'), bool(target.get('auto_create_alerts', True)))

    logger.info(
        'polling_cycle_start target_id=%s wallet_address=%s latest_block=%s events_from_provider=%s source_type=%s',
        target.get('id'),
        str(target.get('wallet_address') or 'n/a'),
        provider_result.latest_block,
        len(events),
        source_type or 'unknown',
    )
    wallet_transfers_detected = 0
    inserted_telemetry_ids: list[str] = []
    telemetry_evidence_source = _telemetry_event_evidence_source(provider_result=provider_result, source_type=source_type)
    for event in events:
        if telemetry_evidence_source not in {'live', 'simulator', 'replay'}:
            telemetry_evidence_source = 'simulator'
        telemetry_idempotency_key = _telemetry_idempotency_key(
            workspace_id=target.get('workspace_id'),
            target_id=target.get('id'),
            event=event,
        )
        telemetry_evidence_source = _telemetry_event_evidence_source(provider_result=provider_result, source_type=source_type)
        if telemetry_evidence_source not in {'live', 'simulator', 'replay'}:
            telemetry_evidence_source = 'simulator'
        telemetry_idempotency_key = _telemetry_idempotency_key(
            workspace_id=target.get('workspace_id'),
            target_id=target.get('id'),
            event=event,
        )
        _ev_payload = event.payload if isinstance(event.payload, dict) else {}
        _ev_from = str(_ev_payload.get('from') or '').lower()
        _ev_to = str(_ev_payload.get('to') or '').lower()
        _target_wallet = str(target.get('wallet_address') or '').lower()
        _is_wallet_tx = (
            str(target.get('target_type') or '').lower() == 'wallet'
            and bool(_target_wallet)
            and _target_wallet in {_ev_from, _ev_to}
            and str(_ev_payload.get('event_type') or event.kind or '').lower() in {'transaction', 'transfer', 'native_transfer'}
        )
        if _is_wallet_tx:
            _raw_event_type = str(_ev_payload.get('event_type') or event.kind or '').lower()
            if _raw_event_type == 'transaction' and _ev_payload.get('wallet_transfer_direction'):
                _telem_event_type = 'native_transfer'
            else:
                _telem_event_type = 'wallet_transfer_detected'
        else:
            _telem_event_type = str(event.kind or 'target_event')
        _telem_id = str(uuid.uuid4())
        if _is_wallet_tx:
            # This IS the stable RPC polling loop persisting its own detection, so a
            # live payload that carries no detection-path fact (a provider that
            # forgot to stamp) is truthfully stamped stable_rpc_polling here —
            # never left blank for the API to guess at later. Simulator/replay
            # payloads are never stamped with a live path.
            if (
                telemetry_evidence_source == 'live'
                and not _ev_payload.get('detected_by')
                and resolve_telemetry_detected_by(_ev_payload) is None
            ):
                _ev_payload['detected_by'] = STABLE_DETECTED_BY
                _ev_payload['detected_by_source'] = 'stable_polling_loop'
            # Detected wallet transfers are canonical live evidence. Persist and COMMIT the
            # raw telemetry on a dedicated connection BEFORE threat analysis runs. If analysis
            # later raises (e.g. analysis_unavailable) the surrounding monitoring transaction
            # rolls back, but this committed evidence row survives and stays searchable.
            _wallet_transfer_persisted = _persist_raw_wallet_transfer_telemetry(
                connection,
                telemetry_id=_telem_id,
                workspace_id=str(target['workspace_id']),
                asset_id=str(target.get('asset_id')) if target.get('asset_id') else None,
                target_id=str(target['id']),
                provider_type=str(provider_result.provider_name or 'monitoring_provider'),
                event_type=_telem_event_type,
                observed_at=event.observed_at,
                evidence_source=telemetry_evidence_source,
                payload=_ev_payload,
                idempotency_key=telemetry_idempotency_key,
            )
            wallet_transfers_detected += 1
            inserted_telemetry_ids.append(_telem_id)
            logger.info(
                'wallet_transfer_detected target_id=%s tx_hash=%s from=%s to=%s block=%s telemetry_id=%s '
                'detected_by=%s evidence_source=%s persisted=%s',
                target.get('id'),
                str(_ev_payload.get('tx_hash') or _ev_payload.get('hash') or 'unknown'),
                str(_ev_from or 'unknown'),
                str(_ev_to or 'unknown'),
                _ev_payload.get('block_number'),
                _telem_id,
                str(_ev_payload.get('detected_by') or 'stable_rpc_polling'),
                telemetry_evidence_source,
                str(_wallet_transfer_persisted).lower(),
            )
            # Smoke-test rule: create a detection + low/info alert for every live wallet
            # transfer, committed on its own connection so it survives threat-engine failures.
            _smoke_alert_id = _wallet_transfer_smoke_alert(
                workspace_id=str(target['workspace_id']),
                user_id=user_id,
                target_id=str(target['id']),
                target_name=str(target.get('name') or target.get('id') or ''),
                payload=_ev_payload,
                evidence_source=telemetry_evidence_source,
                telemetry_id=_telem_id,
                monitored_system_id=str(target['monitored_system_id']) if target.get('monitored_system_id') else None,
                protected_asset_id=str(target['asset_id']) if target.get('asset_id') else None,
            )
            if _smoke_alert_id:
                alerts_generated += 1
                last_alert_at = utc_now()
            # Strategic Infrastructure Guard rule: critical alert for outbound Base ETH transfers.
            _sig_alert_id = _strategic_infrastructure_guard_alert(
                workspace_id=str(target['workspace_id']),
                user_id=user_id,
                target_id=str(target['id']),
                target_name=str(target.get('name') or target.get('id') or ''),
                target_wallet_address=_target_wallet,
                payload=_ev_payload,
                evidence_source=telemetry_evidence_source,
                telemetry_id=_telem_id,
                monitored_system_id=str(target['monitored_system_id']) if target.get('monitored_system_id') else None,
                protected_asset_id=str(target['asset_id']) if target.get('asset_id') else None,
            )
            if _sig_alert_id and _sig_alert_id != _smoke_alert_id:
                alerts_generated += 1
                last_alert_at = utc_now()
        else:
            connection.execute(
                """
                INSERT INTO telemetry_events (
                    id, workspace_id, asset_id, target_id, provider_type, event_type, observed_at, evidence_source, payload_hash, payload_json, idempotency_key
                )
                VALUES (%s::uuid, %s::uuid, %s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s::jsonb, %s)
                ON CONFLICT (workspace_id, target_id, idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING
                """,
                (
                    _telem_id,
                    str(target['workspace_id']),
                    str(target.get('asset_id')) if target.get('asset_id') else None,
                    str(target['id']),
                    str(provider_result.provider_name or 'monitoring_provider'),
                    _telem_event_type,
                    event.observed_at,
                    telemetry_evidence_source,
                    hashlib.sha256(_json_dumps(event.payload if isinstance(event.payload, dict) else {}).encode('utf-8')).hexdigest(),
                    _json_dumps(event.payload if isinstance(event.payload, dict) else {}),
                    telemetry_idempotency_key,
                ),
            )
        try:
            processed = _process_single_event(
                connection,
                target=target,
                workspace=workspace,
                user_id=user_id,
                monitoring_run_id=monitoring_run_id,
                event=event,
                monitoring_path=monitoring_path,
            )
        except Exception as _single_event_exc:
            _tx_hash_for_log = str(_ev_payload.get('tx_hash') or _ev_payload.get('hash') or 'unknown')
            # A dead connection must fail the whole target fast: never fall through to the
            # per-event "continue" path, which would compute a cursor advance and then try
            # to persist it on a closed socket. The wallet-transfer telemetry/alert for this
            # event were already committed on their own connections, so a retry next cycle is
            # idempotent (dedupe keys) and the cursor stays put until it persists cleanly.
            if _is_connection_lost_error(_single_event_exc):
                logger.warning(
                    'event_processing_failed target_id=%s tx_hash=%s monitoring_run_id=%s '
                    'error=%s action=fail_target_connection_lost',
                    target.get('id'), _tx_hash_for_log, monitoring_run_id,
                    _safe_error_message(_single_event_exc),
                )
                raise
            logger.warning(
                'event_processing_failed target_id=%s tx_hash=%s monitoring_run_id=%s '
                'error=%s action=skip_event_continue_cursor',
                target.get('id'), _tx_hash_for_log, monitoring_run_id,
                str(_single_event_exc)[:300],
            )
            processed = {
                'analysis_run_id': None,
                'monitoring_state': 'event_error',
                'alert_id': None,
                'incident_id': None,
                'detection_id': None,
                'protected_asset_coverage_record': None,
            }
        analysis_run_id = processed.get('analysis_run_id')
        run_ids.append(analysis_run_id)
        event_state = str(processed.get('monitoring_state') or 'real_event_no_anomaly')
        if event_state in {'anomaly_escalated_to_incident', 'real_event_anomaly_detected'}:
            last_status = event_state
        elif last_status not in {'anomaly_escalated_to_incident', 'real_event_anomaly_detected'}:
            last_status = event_state
        last_run_id = analysis_run_id
        checkpoint_at = event.observed_at
        if last_observed_event_at is None or event.observed_at > last_observed_event_at:
            last_observed_event_at = event.observed_at
        checkpoint_cursor = event.cursor
        block_number = event.payload.get('block_number') if isinstance(event.payload, dict) else None
        if block_number is not None:
            try:
                _bn_int = int(block_number)
                if _bn_int > 500_000_000:
                    logger.error(
                        'code=EVENT_BLOCK_NUMBER_CORRUPT_REJECTED source=process_monitoring_target '
                        'target_id=%s chain=%s corrupt_block_number=%s observed_at=%s '
                        'action=skip_update latest_processed_block_unchanged=%s',
                        target.get('id'), chain, _bn_int,
                        event.observed_at.isoformat() if hasattr(event.observed_at, 'isoformat') else str(event.observed_at),
                        latest_processed_block,
                    )
                else:
                    latest_processed_block = max(latest_processed_block, _bn_int)
            except Exception:
                pass
        alert_id = processed.get('alert_id')
        if alert_id:
            alerts_generated += 1
            last_alert_at = utc_now()
        if processed.get('incident_id'):
            incidents_created += 1
        if processed.get('detection_id'):
            detections_created += 1
        coverage_record = processed.get('protected_asset_coverage_record')
        if isinstance(coverage_record, dict) and coverage_record:
            last_protected_asset_coverage_record = coverage_record

    # Advance cursor from scan ceiling even when no events were detected.
    # Without this, empty scans leave the checkpoint unchanged and every poll
    # repeats the same replay window instead of scanning forward through new blocks.
    _scan_top = int(provider_result.latest_block or 0)
    _checkpoint_before = checkpoint_cursor
    if 0 < _scan_top <= 500_000_000:
        latest_processed_block = max(latest_processed_block, _scan_top)
        _cursor_block = int((checkpoint_cursor or '0').split(':')[0] or '0')
        if _scan_top > _cursor_block:
            checkpoint_cursor = f"{_scan_top}:checkpoint:-1"
    logger.info(
        'scan_cursor_persist target_id=%s chain=%s previous_cursor=%s '
        'checkpoint_before=%s checkpoint_after=%s '
        'latest_block=%s from_block=%s to_block=%s blocks_scanned=see_evm_block_scan_summary '
        'latest_processed_block=%s persisted_cursor=%s checked=%s',
        target.get('id'), chain,
        target.get('monitoring_checkpoint_cursor') or 'none',
        _checkpoint_before or 'none',
        checkpoint_cursor or 'none',
        _scan_top or 0,
        (target.get('monitoring_checkpoint_cursor') or '').split(':')[0] or 'none',
        _scan_top or 'unavailable',
        latest_processed_block,
        checkpoint_cursor or 'none',
        1,
    )
    logger.info(
        'polling_cycle_summary target_id=%s wallet_address=%s latest_block=%s '
        'events_inspected=%s wallet_transfers_detected=%s telemetry_ids=%s evidence_source=%s',
        target.get('id'),
        str(target.get('wallet_address') or 'n/a'),
        latest_processed_block,
        len(events),
        wallet_transfers_detected,
        inserted_telemetry_ids[:10],
        telemetry_evidence_source,
    )
    if not events and provider_result.mode in {'live', 'hybrid'} and is_monitorable_target_type(target.get('target_type')):
        if provider_result.status == 'failed':
            source_status = 'failed'
            degraded_reason = provider_result.degraded_reason or 'provider_failed'
            last_status = 'insufficient_real_evidence'
        elif provider_result.status == 'no_evidence':
            source_status = 'no_evidence'
            degraded_reason = provider_result.degraded_reason or 'no_live_events_observed'
            last_status = 'no_evidence'
        elif provider_result.status == 'live':
            source_status = 'active'
            degraded_reason = None
            last_status = 'no_evidence'
        else:
            source_status = 'degraded'
            degraded_reason = provider_result.degraded_reason or 'monitoring_degraded'
            last_status = 'insufficient_real_evidence'
    if provider_result.mode in {'live', 'hybrid'} and not live_source_eligible:
        source_status = 'degraded'
        degraded_reason = degraded_reason or f'provider_source_not_live:{source_type or "unknown"}'
        if last_status not in {'anomaly_escalated_to_incident', 'real_event_anomaly_detected'}:
            last_status = 'insufficient_real_evidence'
    if events and provider_result.synthetic and provider_result.mode in {'live', 'hybrid'}:
        source_status = 'degraded'
        degraded_reason = 'synthetic_leak_detected'
        last_status = 'degraded'

    live_coverage_telemetry_at: datetime | None = None
    coverage_persisted = False
    coverage_skip_reason: str | None = None
    status_reason_code: str | None = None
    if (
        provider_result.mode in {'live', 'hybrid'}
        and provider_result.status == 'live'
        and not provider_result.synthetic
        and not degraded_reason
        and live_source_eligible
    ):
        live_coverage_telemetry_at = utc_now()
        logger.info(
            'coverage_timestamp_update_checkpoint workspace_id=%s target_id=%s action=persist timestamp=%s provider_status=%s source_type=%s',
            target.get('workspace_id'),
            target.get('id'),
            live_coverage_telemetry_at.isoformat(),
            provider_result.status,
            source_type or 'unknown',
        )
        # Coverage telemetry runs AFTER any wallet-transfer detection/alert for this
        # target has already been committed (on its own connection). It must never sink
        # that successful detection: a dead worker connection is retried once on a fresh
        # connection, and a final failure is isolated (logged coverage_telemetry_write_failed)
        # so the target poll still returns a truthful degraded/partial summary.
        coverage_persisted = _persist_live_coverage_telemetry_resilient(
            connection,
            target=target,
            provider_result=provider_result,
            observed_at=live_coverage_telemetry_at,
        )
        if not coverage_persisted:
            # Truthfulness: a failed coverage write must not surface as live coverage.
            # Drop the coverage timestamp/heartbeat for this cycle and record the reason.
            coverage_skip_reason = 'coverage_telemetry_write_failed'
            live_coverage_telemetry_at = None
    else:
        if provider_result.mode not in {'live', 'hybrid'}:
            coverage_skip_reason = f"mode_{provider_result.mode or 'unknown'}"
        elif provider_result.status != 'live':
            coverage_skip_reason = f"status_{provider_result.status or 'unknown'}"
        elif provider_result.synthetic:
            coverage_skip_reason = 'synthetic_result'
        elif degraded_reason:
            coverage_skip_reason = degraded_reason
        elif not live_source_eligible:
            coverage_skip_reason = f"source_type_{source_type or 'unknown'}"
        else:
            coverage_skip_reason = 'telemetry_not_eligible'
    logger.info(
        'coverage_telemetry_write workspace_id=%s target_id=%s coverage_persisted=%s coverage_timestamp=%s status_reason=%s',
        target.get('workspace_id'),
        target.get('id'),
        coverage_persisted,
        live_coverage_telemetry_at.isoformat() if live_coverage_telemetry_at else None,
        None if coverage_persisted else coverage_skip_reason,
    )
    logger.info(
        'receipt_persist_checkpoint workspace_id=%s target_id=%s receipts_written=%s checkpoint_cursor=%s latest_processed_block=%s',
        target.get('workspace_id'),
        target.get('id'),
        len(events) + (1 if coverage_persisted else 0),
        checkpoint_cursor,
        latest_processed_block,
    )
    real_event_count = len(events)
    coverage_heartbeat_count = 1 if coverage_persisted else 0
    coverage_only_no_events = coverage_heartbeat_count > 0 and real_event_count <= 0
    if coverage_only_no_events:
        status_reason_code = 'coverage_only_no_events'
    telemetry_records_seen = real_event_count + coverage_heartbeat_count
    if real_event_count > 0 and detections_created == 0 and provider_result.status in {'live', 'no_evidence', 'degraded'}:
        evaluated_no_threat_marker_id = _persist_no_threat_evaluation_marker(
            connection,
            workspace_id=str(target['workspace_id']),
            target=target,
            observed_at=live_coverage_telemetry_at or checkpoint_at or last_observed_event_at,
            monitoring_run_id=monitoring_run_id,
            events_ingested=real_event_count,
            telemetry_records_seen=telemetry_records_seen,
        )

    recent_evidence_state = ui_evidence_state(provider_result.evidence_state)
    recent_truthfulness_state = ui_truthfulness_state(provider_result.truthfulness_state)
    recent_confidence_basis = (
        'demo_scenario'
        if provider_result.synthetic
        else ('provider_evidence' if bool(events) else ('provider_coverage' if coverage_persisted else 'none'))
    )
    last_real_event_at = provider_result.last_real_event_at
    last_no_evidence_at = utc_now() if provider_result.status == 'no_evidence' else None
    last_degraded_at = utc_now() if provider_result.status == 'degraded' else None
    last_failed_monitoring_at = utc_now() if provider_result.status == 'failed' else None
    last_synthetic_event_at = checkpoint_at if provider_result.synthetic else None

    connection.execute(
        '''
        UPDATE targets
        SET last_checked_at = NOW(),
            last_run_status = %s,
            last_run_id = %s,
            last_alert_at = COALESCE(%s, last_alert_at),
            monitoring_checkpoint_at = COALESCE(%s, monitoring_checkpoint_at),
            monitoring_checkpoint_cursor = COALESCE(%s, monitoring_checkpoint_cursor),
            watcher_last_observed_block = NULLIF(%s, 0),
            watcher_checkpoint_lag_blocks = CASE WHEN NULLIF(%s, 0) IS NULL THEN watcher_checkpoint_lag_blocks ELSE GREATEST(0, %s - %s) END,
            watcher_source_status = %s,
            watcher_degraded_reason = %s,
            watcher_last_event_at = %s,
            last_real_event_at = COALESCE(%s, last_real_event_at),
            last_no_evidence_at = COALESCE(%s, last_no_evidence_at),
            last_degraded_at = COALESCE(%s, last_degraded_at),
            last_failed_monitoring_at = COALESCE(%s, last_failed_monitoring_at),
            last_synthetic_event_at = %s,
            recent_evidence_state = %s,
            recent_truthfulness_state = %s,
            recent_real_event_count = %s,
            recent_confidence_basis = %s,
            monitoring_claimed_by = NULL,
            monitoring_claimed_at = NULL,
            monitoring_lease_token = NULL,
            monitoring_lease_expires_at = NULL,
            monitoring_delivery_attempts = 0,
            monitoring_dead_lettered_at = NULL,
            updated_at = NOW()
        WHERE id = %s
          AND workspace_id = %s
        ''',
        (
            last_status,
            last_run_id,
            last_alert_at,
            checkpoint_at,
            checkpoint_cursor,
            latest_processed_block,
            latest_processed_block,
            latest_processed_block,
            latest_processed_block,
            source_status,
            degraded_reason,
            last_observed_event_at,
            last_real_event_at,
            last_no_evidence_at,
            last_degraded_at,
            last_failed_monitoring_at,
            last_synthetic_event_at,
            recent_evidence_state,
            recent_truthfulness_state,
            int(provider_result.recent_real_event_count or real_event_count),
            recent_confidence_basis,
            target['id'],
            target['workspace_id'],
        ),
    )
    connection.execute(
        '''
        UPDATE target_evaluation
        SET status = %s,
            finished_at = NOW(),
            checkpoint_block = %s,
            events_seen = %s,
            matches_found = %s,
            error_text = %s
        WHERE id = %s
        ''',
        (
            'completed' if provider_result.status != 'failed' else 'failed',
            latest_processed_block,
            len(events),
            alerts_generated,
            provider_result.degraded_reason if provider_result.status == 'failed' else None,
            evaluation_id,
        ),
    )
    _upsert_checkpoint(
        connection,
        workspace_id=str(target['workspace_id']),
        monitored_system_id=monitored_system_id,
        chain=chain,
        last_processed_block=latest_processed_block,
    )
    _persist_detection_evaluation_checkpoint(
        connection,
        workspace_id=str(target['workspace_id']),
        user_id=user_id,
        target=target,
        monitoring_run_id=monitoring_run_id,
        monitoring_path=monitoring_path,
        provider_result=provider_result,
        events_ingested=real_event_count,
        detections_created=detections_created,
        alerts_generated=alerts_generated,
        incidents_created=incidents_created,
    )
    stale_open_alerts_closed = 0
    if provider_result.mode in {'live', 'hybrid'} and live_source_eligible and provider_result.status in {'live', 'no_evidence'}:
        _stale_result = connection.execute(
            '''
            UPDATE alerts
            SET status = 'resolved',
                resolution_note = %s,
                resolved_at = NOW(),
                updated_at = NOW()
            WHERE workspace_id = %s::uuid
              AND target_id = %s::uuid
              AND status IN ('open', 'acknowledged', 'investigating')
              AND COALESCE(alert_type, '') <> 'monitoring_proof'
              AND (
                    last_seen_at IS NULL
                    OR last_seen_at < NOW() - INTERVAL '30 seconds'
              )
            ''',
            (
                'Auto-resolved by monitoring worker: no current evidence-linked threat signal in latest evaluation.',
                str(target['workspace_id']),
                str(target['id']),
            ),
        )
        stale_open_alerts_closed = int(getattr(_stale_result, 'rowcount', 0) or 0)
    logger.info('checked target %s %s status=%s runs=%s alerts=%s incidents=%s', target['id'], target.get('name') or 'unknown', last_status, len(run_ids), alerts_generated, incidents_created)
    latest_telemetry_row = connection.execute(
        '''
        SELECT id, observed_at, evidence_source
        FROM telemetry_events
        WHERE workspace_id = %s::uuid AND target_id = %s::uuid
        ORDER BY observed_at DESC, ingested_at DESC, id DESC
        LIMIT 1
        ''',
        (str(target['workspace_id']), str(target['id'])),
    ).fetchone()
    latest_detection_at_row = connection.execute(
        'SELECT MAX(created_at) AS ts FROM detection_events WHERE workspace_id = %s::uuid AND target_id = %s::uuid',
        (str(target['workspace_id']), str(target['id'])),
    ).fetchone()
    last_detection_at = _parse_ts((latest_detection_at_row or {}).get('ts') if isinstance(latest_detection_at_row, dict) else None)
    target_coverage_status, last_telemetry_at, coverage_evidence_source, coverage_metadata = _resolve_target_coverage_state(
        provider_status=provider_result.status,
        telemetry_row=latest_telemetry_row if isinstance(latest_telemetry_row, dict) else None,
        provider_evidence_source=provider_evidence_source,
        source_status=source_status,
    )
    _coverage_asset_id = _resolve_coverage_asset_id(connection, target)
    try:
        with connection.transaction():
            connection.execute(
                '''
                INSERT INTO target_coverage_records (
                    id, workspace_id, asset_id, target_id, coverage_status, last_poll_at, last_heartbeat_at, last_telemetry_at, last_detection_at, evidence_source, computed_at, metadata
                )
                VALUES (%s::uuid, %s::uuid, %s::uuid, %s::uuid, %s, NOW(), NOW(), %s, %s, %s, NOW(), %s::jsonb)
                ''',
                (
                    str(uuid.uuid4()),
                    str(target['workspace_id']),
                    _coverage_asset_id,
                    str(target['id']),
                    target_coverage_status,
                    last_telemetry_at,
                    last_detection_at,
                    coverage_evidence_source,
                    _json_dumps(coverage_metadata),
                ),
            )
    except Exception as _tcr_exc:
        logger.warning(
            'code=TARGET_COVERAGE_ASSET_PARENT_MISSING workspace_id=%s target_id=%s asset_id=%s '
            'coverage_write_skipped=True exc=%s',
            target.get('workspace_id'),
            target.get('id'),
            target.get('asset_id'),
            type(_tcr_exc).__name__,
        )
    WORKER_STATE['metrics']['live_events_ingested'] += real_event_count
    return {
        'target_id': str(target['id']),
        'target_type': str(target.get('target_type') or ''),
        'monitoring_run_id': monitoring_run_id,
        'runs': run_ids,
        'alerts_generated': alerts_generated,
        'incidents_created': incidents_created,
        'detections_created': detections_created,
        'events_ingested': real_event_count,
        'real_events_detected': real_event_count,
        'real_event_count': real_event_count,
        'coverage_heartbeat_updates': coverage_heartbeat_count,
        'coverage_heartbeat_count': coverage_heartbeat_count,
        'telemetry_records_seen': telemetry_records_seen,
        'evaluated_no_threat_marker_id': evaluated_no_threat_marker_id,
        'stale_open_alerts_closed': stale_open_alerts_closed,
        'status': last_status,
        'status_reason_code': status_reason_code,
        'latest_processed_block': latest_processed_block,
        'source_status': source_status,
        'degraded_reason': degraded_reason,
        'provider_status': provider_result.status,
        'provider_source_type': provider_result.source_type,
        'synthetic': provider_result.synthetic,
        'recent_evidence_state': recent_evidence_state,
        'recent_truthfulness_state': recent_truthfulness_state,
        'recent_real_event_count': int(provider_result.recent_real_event_count or real_event_count),
        'last_event_at': last_observed_event_at.isoformat() if last_observed_event_at else None,
        'last_real_event_at': last_real_event_at.isoformat() if last_real_event_at else None,
        'live_coverage_telemetry_at': live_coverage_telemetry_at.isoformat() if live_coverage_telemetry_at else None,
        'protected_asset_coverage_record': last_protected_asset_coverage_record,
    }


def process_ingested_event(connection: Any, *, target: dict[str, Any], event: ActivityEvent, ingestion_mode: str = 'live') -> dict[str, Any]:
    workspace_row = connection.execute('SELECT id, name FROM workspaces WHERE id = %s', (target['workspace_id'],)).fetchone() or {'id': target['workspace_id'], 'name': 'Workspace'}
    workspace = _json_safe_value(dict(workspace_row))
    user_id = str(target.get('updated_by_user_id') or target.get('created_by_user_id'))
    monitoring_run_id = str(uuid.uuid4())
    receipt = connection.execute(
        '''
        SELECT id, ingestion_source FROM monitoring_event_receipts WHERE workspace_id = %s AND target_id = %s AND event_id = %s
        ''',
        (target['workspace_id'], target['id'], event.event_id),
    ).fetchone()
    if receipt is not None:
        receipt_dict = dict(receipt) if not isinstance(receipt, dict) else receipt
        _existing_source = receipt_dict.get('ingestion_source')
        return {
            'status': 'duplicate_suppressed',
            'event_id': event.event_id,
            'existing_ingestion_source': _existing_source,
            'existing_detected_by': worker_status_detected_by(_existing_source),
        }
    # Cross-worker dedupe: the 300 s stable polling worker persists telemetry with
    # the SAME idempotency key but writes no monitoring_event_receipts row, so the
    # receipt check above cannot see a transfer stable polling already detected.
    # Without this check the realtime worker re-ran analysis for such a tx and its
    # logs claimed realtime persistence while the customer-visible row truthfully
    # kept detected_by=stable_rpc_polling. Report it as a duplicate instead, naming
    # the existing row's detector. Best-effort: on query failure fall through to
    # normal processing (the telemetry insert itself still dedupes ON CONFLICT).
    try:
        _dup_key = _telemetry_idempotency_key(
            workspace_id=target.get('workspace_id'), target_id=target.get('id'), event=event,
        )
        _existing_telemetry = connection.execute(
            '''
            SELECT payload_json->>'detected_by' AS detected_by
            FROM telemetry_events
            WHERE workspace_id = %s AND target_id = %s AND idempotency_key = %s
            LIMIT 1
            ''',
            (target['workspace_id'], target['id'], _dup_key),
        ).fetchone()
    except Exception:
        _existing_telemetry = None
    if _existing_telemetry is not None:
        _existing_row = (
            dict(_existing_telemetry) if not isinstance(_existing_telemetry, dict) else _existing_telemetry
        )
        _existing_by = worker_status_detected_by(
            _existing_row.get('detected_by') or 'stable_rpc_polling'
        )
        logger.info(
            'realtime_duplicate_existing_tx tx_hash=%s existing_detected_by=%s '
            'attempted_ingestion_source=%s target_id=%s',
            (event.payload or {}).get('tx_hash') if isinstance(event.payload, dict) else 'unknown',
            _existing_by, event.ingestion_source, target.get('id'),
        )
        return {
            'status': 'duplicate_suppressed',
            'event_id': event.event_id,
            'existing_detected_by': _existing_by,
        }
    # Persist the customer-visible wallet-transfer telemetry row FIRST, on its own
    # committed connection, so a native ETH transfer the realtime worker just
    # detected survives even if the threat analysis below raises (analysis_unavailable).
    # Idempotent: same tx → same idempotency_key as the polling worker → ON CONFLICT.
    try:
        _maybe_persist_ingested_wallet_transfer(connection, target=target, event=event)
    except Exception:
        logger.warning(
            'realtime_wallet_transfer_telemetry_persist_failed event_id=%s',
            event.event_id, exc_info=True,
        )
    processed = _process_single_event(connection, target=target, workspace=workspace, user_id=user_id, monitoring_run_id=monitoring_run_id, event=event, monitoring_path='worker')
    payload = event.payload if isinstance(event.payload, dict) else {}
    ingestion_source = str(event.ingestion_source or '').strip().lower()
    is_live_ingestion = (
        str(ingestion_mode or 'live').strip().lower() in {'live', 'hybrid'}
        and ingestion_source in {
            'rpc_backfill', 'polling', 'websocket', 'real', 'evm_rpc',
            'realtime_websocket',  # Base real-time worker (WS logs subscription)
            'realtime_backfill',   # Base real-time worker (gap/native backfill scan)
            'realtime_tx_import',  # Base real-time worker (bounded tx-hash import)
            'quicknode_http_fast_tail',  # Base real-time worker (HTTP fast-tail fallback)
            'realtime_http_fast_tail',   # legacy tag for fast-tail rows persisted pre-rename
        }
    )
    live_coverage_telemetry_at = event.observed_at if is_live_ingestion else None
    connection.execute(
        '''
        INSERT INTO monitoring_event_receipts (
            id, workspace_id, target_id, event_id, event_cursor, tx_hash, block_number, log_index, ingestion_source, receipt_kind, evidence_source, telemetry_kind
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''',
        (
            str(uuid.uuid4()),
            target['workspace_id'],
            target['id'],
            event.event_id,
            event.cursor,
            payload.get('tx_hash'),
            payload.get('block_number'),
            payload.get('log_index'),
            event.ingestion_source,
            'target_event',
            'live' if is_live_ingestion else event.ingestion_source,
            'target_event',
        ),
    )
    if is_live_ingestion:
        metadata = payload.get('metadata') if isinstance(payload.get('metadata'), dict) else {}
        provider_name = str(metadata.get('provider_name') or event.ingestion_source or 'evm_activity_provider')
        coverage_event_id = f"coverage:{event.event_id}"
        connection.execute(
            '''
            INSERT INTO monitoring_event_receipts (
                id, workspace_id, target_id, event_id, event_cursor, tx_hash, block_number, log_index, ingestion_source, receipt_kind, evidence_source, telemetry_kind
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (target_id, event_id)
            DO NOTHING
            ''',
            (
                str(uuid.uuid4()),
                target['workspace_id'],
                target['id'],
                coverage_event_id,
                f"coverage:{event.cursor}",
                payload.get('tx_hash'),
                payload.get('block_number'),
                payload.get('log_index'),
                event.ingestion_source,
                'coverage_telemetry',
                'live',
                'coverage',
            ),
        )
        coverage_payload = {
            'telemetry_kind': 'coverage',
            'proof_kind': 'target_event',
            'observation_type': 'target_event',
            'provider_name': provider_name,
            'source_type': event.ingestion_source,
            'target_id': target.get('id'),
            'event_id': event.event_id,
        }
        connection.execute(
            '''
            INSERT INTO evidence (
                id, workspace_id, asset_id, target_id, alert_id, chain, block_number, tx_hash, log_index, event_type,
                monitored_system_id, severity, risk_score, summary, counterparty, amount_text, token_address, contract_address, source_provider,
                raw_payload_json, observed_at, created_at
            )
            VALUES (%s, %s, %s, %s, NULL, %s, %s, %s, %s, %s, %s, %s, NULL, %s, NULL, NULL, NULL, %s, %s, %s::jsonb, %s, NOW())
            ''',
            (
                str(uuid.uuid4()),
                target['workspace_id'],
                target.get('asset_id'),
                target['id'],
                target.get('chain_network'),
                payload.get('block_number'),
                payload.get('tx_hash'),
                payload.get('log_index'),
                'coverage_telemetry',
                target.get('monitored_system_id'),
                'low',
                'Live target-event telemetry verified',
                target.get('contract_identifier') or target.get('wallet_address'),
                provider_name,
                _json_dumps(coverage_payload),
                event.observed_at,
            ),
        )
    connection.execute(
        '''
        UPDATE monitored_systems
        SET last_event_at = COALESCE(%s, last_event_at),
            last_coverage_telemetry_at = COALESCE(%s, last_coverage_telemetry_at),
            last_heartbeat = NOW(),
            freshness_status = CASE WHEN %s IS NOT NULL THEN 'fresh' ELSE freshness_status END,
            confidence_status = CASE WHEN %s IS NOT NULL THEN 'high' ELSE confidence_status END,
            coverage_reason = CASE WHEN %s IS NOT NULL THEN NULL ELSE coverage_reason END
        WHERE workspace_id = %s
          AND target_id = %s
          AND COALESCE(is_enabled, TRUE) = TRUE
        ''',
        (
            event.observed_at,
            live_coverage_telemetry_at,
            live_coverage_telemetry_at,
            live_coverage_telemetry_at,
            live_coverage_telemetry_at,
            target['workspace_id'],
            target['id'],
        ),
    )
    connection.execute(
        '''
        UPDATE targets
        SET monitoring_checkpoint_at = %s,
            monitoring_checkpoint_cursor = %s,
            last_checked_at = NOW(),
            last_run_status = 'completed',
            last_run_id = %s,
            updated_at = NOW()
        WHERE id = %s
          AND workspace_id = %s
        ''',
        (event.observed_at, event.cursor, processed['analysis_run_id'], target['id'], target['workspace_id']),
    )
    return {'status': 'processed', 'event_id': event.event_id, 'analysis_run_id': processed['analysis_run_id'], 'alert_id': processed.get('alert_id')}


# Monitoring tables whose target_id FK must reference targets(id).
# Values are (constraint_name, nullable) where nullable=True means SET NULL is acceptable.
_MONITORING_TARGET_FK_TABLES: list[tuple[str, str, bool]] = [
    ('monitoring_polls', 'monitoring_polls_target_id_fkey', False),
    ('provider_health_records', 'provider_health_records_target_id_fkey', True),
    ('target_coverage_records', 'target_coverage_records_target_id_fkey', False),
]


def _verify_monitoring_fk_alignment(connection: Any) -> dict[str, Any]:
    """Check that all monitoring table target_id FKs reference targets(id).

    Logs the FK mapping on every call. Returns a dict with keys:
      - aligned: list of (table, constraint) that are correct
      - misaligned: list of (table, constraint, actual_parent) that are wrong
    """
    aligned = []
    misaligned = []
    for table, constraint, _nullable in _MONITORING_TARGET_FK_TABLES:
        try:
            row = connection.execute(
                '''
                SELECT ccu.table_name AS parent_table
                FROM information_schema.table_constraints tc
                JOIN information_schema.referential_constraints rc
                    ON tc.constraint_name = rc.constraint_name
                    AND tc.constraint_schema = rc.constraint_schema
                JOIN information_schema.constraint_column_usage ccu
                    ON rc.unique_constraint_name = ccu.constraint_name
                    AND rc.unique_constraint_schema = ccu.constraint_schema
                WHERE tc.constraint_type = 'FOREIGN KEY'
                  AND tc.table_name = %s
                  AND tc.constraint_name = %s
                LIMIT 1
                ''',
                (table, constraint),
            ).fetchone()
        except Exception:
            logger.exception(
                'code=MONITORING_TARGET_FK_CHECK_FAILED table=%s constraint=%s',
                table, constraint,
            )
            continue
        if row is None:
            logger.warning(
                'code=MONITORING_TARGET_FK_MISSING table=%s constraint=%s '
                'note=constraint_not_found_may_have_been_dropped',
                table, constraint,
            )
            continue
        parent = row['parent_table'] if isinstance(row, dict) else row[0]
        if parent == 'targets':
            aligned.append((table, constraint))
            logger.info(
                'code=MONITORING_TARGET_FK_OK table=%s constraint=%s parent=targets',
                table, constraint,
            )
        else:
            misaligned.append((table, constraint, parent))
            logger.error(
                'code=MONITORING_TARGET_FK_MISMATCH table=%s constraint=%s '
                'expected_parent=targets actual_parent=%s '
                'fix=run_migration_0082',
                table, constraint, parent,
            )
    return {'aligned': aligned, 'misaligned': misaligned}


def _telemetry_idempotency_index_guard(connection: Any) -> bool:
    """Verify telemetry_events has partial unique idempotency index required for live polling."""
    try:
        row = connection.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                JOIN pg_index i ON i.indrelid = c.oid
                JOIN pg_class idx ON idx.oid = i.indexrelid
                JOIN pg_indexes pi
                  ON pi.schemaname = n.nspname
                 AND pi.tablename = c.relname
                 AND pi.indexname = idx.relname
                WHERE c.relname = 'telemetry_events'
                  AND i.indisunique = TRUE
                  AND pg_get_indexdef(i.indexrelid) ILIKE '%(workspace_id, target_id, idempotency_key)%'
                  AND pg_get_indexdef(i.indexrelid) ILIKE '%WHERE (idempotency_key IS NOT NULL)%'
            ) AS ok
            """
        ).fetchone()
    except Exception:
        logger.exception('code=TELEMETRY_IDEMPOTENCY_INDEX_GUARD_FAILED')
        return False
    if isinstance(row, dict):
        return bool(row.get('ok'))
    return bool((row or [False])[0])


LIVE_RPC_PROOF_CHAIN_DEDUPE_WINDOW_HOURS = int(os.getenv('LIVE_RPC_PROOF_CHAIN_DEDUPE_WINDOW_HOURS', '6'))


def _ensure_workspace_live_rpc_proof_chain(
    connection: Any,
    *,
    workspace_id: str,
) -> dict[str, Any]:
    """Select existing policy-created target evidence without writing workflow rows.

    RPC coverage heartbeats call this helper, but connectivity alone is not promoted
    into a detection, alert, incident, response action, or enterprise proof chain.
    """
    from services.api.app._proof_chain_worker import (
        _ensure_workspace_live_rpc_proof_chain as _worker,
    )
    return _worker(connection, workspace_id=workspace_id, utc_now_fn=utc_now)


def run_monitoring_cycle(*, worker_name: str = 'monitoring-worker', limit: int = 50, trigger_type: str = 'scheduler') -> dict[str, Any]:
    trigger_type = _normalize_monitoring_run_trigger_type(trigger_type)
    ingestion_runtime = monitoring_ingestion_runtime()
    if not live_mode_enabled():
        return {'checked': 0, 'alerts_generated': 0, 'runs': [], 'live_mode': False, 'ingestion_mode': ingestion_runtime.get('source', 'demo')}

    checked = 0
    due_count = 0
    skipped_provider_backoff = 0
    alerts_generated = 0
    live_targets_checked = 0
    events_ingested = 0
    real_events_detected = 0
    coverage_heartbeat_updates = 0
    incidents_created = 0
    monitored_systems_updated = 0
    runs: list[dict[str, Any]] = []
    # (workspace_id, target_id) of wallet targets polled this cycle. After the cycle
    # commits (heartbeat persisted), each gets a scheduler-independent Strategic
    # Infrastructure Guard backfill so older wallet-transfer rows on a live-polled but
    # never-selected_for_backfill target still get their alerts.
    strategic_guard_backfill_targets: set[tuple[str, str]] = set()
    error_message: str | None = None
    cycle_started_at = utc_now()
    logger.info('monitoring cycle started worker=%s limit=%s', worker_name, limit)
    with pg_connection() as connection:
        # DB-safety: run the whole cycle in autocommit so the worker connection is never
        # held idle-in-transaction across slow RPC scans, threat-engine calls, or long
        # loops (the idle-in-transaction timeout that previously killed the connection
        # right after a successful detection). Each standalone statement commits
        # immediately; multi-statement write groups use explicit
        # `with connection.transaction()` blocks, which become real short transactions here.
        try:
            connection.autocommit = True
        except Exception:
            logger.warning(
                'monitoring_cycle_autocommit_unavailable worker=%s '
                'action=continue_default_isolation',
                worker_name,
            )
        ensure_pilot_schema(connection)
        _verify_monitoring_fk_alignment(connection)
        if not _telemetry_idempotency_index_guard(connection):
            error_message = (
                'Telemetry idempotency index is missing; apply migration 0088 '
                '(or 0075/0086) before live polling.'
            )
            logger.error(
                'code=TELEMETRY_IDEMPOTENCY_INDEX_MISSING '
                'hint=apply_migration_0088_fix_live_coverage_telemetry_upsert_constraint '
                'action=degrade_worker_and_skip_cycle'
            )
            connection.execute(
                '''
                INSERT INTO monitoring_worker_state (
                    worker_name,
                    running,
                    status,
                    last_started_at,
                    last_heartbeat_at,
                    last_cycle_at,
                    last_cycle_due_targets,
                    last_cycle_targets_checked,
                    last_cycle_alerts_generated,
                    last_error,
                    updated_at
                )
                VALUES (%s, TRUE, 'degraded', NOW(), NOW(), NOW(), 0, 0, 0, %s, NOW())
                ON CONFLICT (worker_name)
                DO UPDATE SET
                    running = TRUE,
                    status = 'degraded',
                    last_heartbeat_at = NOW(),
                    last_cycle_at = NOW(),
                    last_cycle_due_targets = 0,
                    last_cycle_targets_checked = 0,
                    last_cycle_alerts_generated = 0,
                    last_error = EXCLUDED.last_error,
                    updated_at = NOW()
                ''',
                (worker_name, error_message),
            )
            return {
                'checked': 0,
                'due_count': 0,
                'alerts_generated': 0,
                'live_targets_checked': 0,
                'events_ingested': 0,
                'real_events_detected': 0,
                'coverage_heartbeat_updates': 0,
                'incidents_created': 0,
                'monitored_systems_updated': 0,
                'runs': [],
                'degraded': True,
                'degraded_reason': 'telemetry_idempotency_index_missing',
                'error': error_message,
                'live_mode': True,
                'ingestion_mode': ingestion_runtime.get('source', 'live'),
            }
        workspace_run_ids: dict[str, str] = {}
        workspace_systems_checked: dict[str, int] = defaultdict(int)
        workspace_assets_checked: dict[str, set[str]] = defaultdict(set)
        workspace_detections_created: dict[str, int] = defaultdict(int)
        workspace_alerts_created: dict[str, int] = defaultdict(int)
        workspace_telemetry_seen: dict[str, int] = defaultdict(int)
        workspace_real_events_detected: dict[str, int] = defaultdict(int)
        workspace_coverage_heartbeat_updates: dict[str, int] = defaultdict(int)
        workspace_provider_reachable_cycles: dict[str, int] = defaultdict(int)
        workspace_errors: dict[str, str] = {}
        connection.execute(
            '''
            INSERT INTO monitoring_worker_state (
                worker_name,
                running,
                status,
                last_started_at,
                last_heartbeat_at,
                last_cycle_at,
                last_cycle_due_targets,
                last_cycle_targets_checked,
                last_cycle_alerts_generated,
                last_error,
                updated_at
            )
            VALUES (%s, TRUE, 'running', NOW(), NOW(), NOW(), 0, 0, 0, NULL, NOW())
            ON CONFLICT (worker_name)
            DO UPDATE SET running = TRUE, status = 'running', last_started_at = COALESCE(monitoring_worker_state.last_started_at, NOW()), last_heartbeat_at = NOW(), last_cycle_at = NOW(), last_error = NULL, updated_at = NOW()
            ''',
            (worker_name,),
        )
        # Repair misclassified default providers for real Ethereum targets so
        # live polling targets are eligible for the worker loop.
        connection.execute(
            '''
            UPDATE monitoring_configs mc
            SET provider_type = 'evm_rpc',
                updated_at = NOW()
            FROM targets t
            WHERE t.id = mc.target_id
              AND t.workspace_id = mc.workspace_id
              AND t.deleted_at IS NULL
              AND COALESCE(t.enabled, FALSE) = TRUE
              AND COALESCE(t.monitoring_enabled, FALSE) = TRUE
              AND t.workspace_id IS NOT NULL
              AND LOWER(COALESCE(t.chain_network, '')) IN ('ethereum', 'ethereum-mainnet', 'mainnet', 'eth-mainnet', 'base', 'base-mainnet')
              AND LOWER(COALESCE(mc.provider_type, '')) IN ('default', 'unknown', '')
            ''',
        )
        # Auto-recover dead-lettered targets that have been dead-lettered long enough.
        _dead_letter_recovery_hours = int(os.getenv('MONITORING_DEAD_LETTER_RECOVERY_HOURS', '24'))
        try:
            connection.execute(
                f"""
                UPDATE targets
                SET monitoring_dead_lettered_at = NULL,
                    monitoring_delivery_attempts = 0,
                    last_run_status = 'recovered',
                    updated_at = NOW()
                WHERE monitoring_dead_lettered_at IS NOT NULL
                  AND monitoring_dead_lettered_at < NOW() - INTERVAL '{_dead_letter_recovery_hours} hours'
                  AND deleted_at IS NULL
                """,
            )
        except Exception:
            logger.warning('dead_letter_auto_recovery_failed recovery_hours=%s', _dead_letter_recovery_hours)
        # Fast self-healing: retry dead-lettered targets after a short backoff so a valid
        # target that failed on a transient error re-enters normal due-selection promptly
        # instead of waiting a full MONITORING_DEAD_LETTER_RECOVERY_HOURS window. This is the
        # canonical recovery path; it does NOT depend on the backfill cooldown, so a target
        # blocked by backfill cooldown can still be recovered and live-polled normally.
        _dead_letter_retry_seconds = MONITORING_DEAD_LETTER_RETRY_SECONDS
        try:
            _recovered_rows = connection.execute(
                f"""
                UPDATE targets
                SET monitoring_dead_lettered_at = NULL,
                    monitoring_delivery_attempts = 0,
                    monitoring_claimed_by = NULL,
                    monitoring_claimed_at = NULL,
                    monitoring_lease_token = NULL,
                    monitoring_lease_expires_at = NULL,
                    last_run_status = 'recovered',
                    updated_at = NOW()
                WHERE monitoring_dead_lettered_at IS NOT NULL
                  AND monitoring_dead_lettered_at < NOW() - (%s * INTERVAL '1 second')
                  AND deleted_at IS NULL
                  AND COALESCE(enabled, FALSE) = TRUE
                  AND COALESCE(monitoring_enabled, FALSE) = TRUE
                RETURNING id, workspace_id
                """,
                (_dead_letter_retry_seconds,),
            ).fetchall()
            for _rec in (_recovered_rows or []):
                _rec_row = dict(_rec)
                logger.info(
                    'dead_letter_fast_recovery target_id=%s workspace_id=%s '
                    'retry_after_seconds=%s action=recovered_for_retry',
                    _rec_row.get('id'),
                    _rec_row.get('workspace_id'),
                    _dead_letter_retry_seconds,
                )
        except Exception:
            logger.warning(
                'dead_letter_fast_recovery_failed retry_seconds=%s', _dead_letter_retry_seconds
            )
        candidate_systems = connection.execute(
            '''
            SELECT ms.id AS monitored_system_id,
                   ms.workspace_id,
                   ms.target_id,
                   ms.asset_id,
                   COALESCE(ms.is_enabled, TRUE) AS monitored_system_enabled,
                   ms.runtime_status AS monitored_system_runtime_status,
                   ms.last_heartbeat AS monitored_system_last_heartbeat,
                   t.last_checked_at,
                   t.monitoring_interval_seconds,
                   t.monitoring_enabled,
                   t.enabled,
                   t.is_active,
                   t.monitoring_dead_lettered_at,
                   t.chain_network
            FROM monitored_systems ms
            JOIN targets t ON t.id = ms.target_id
            JOIN assets a ON a.id = t.asset_id AND a.workspace_id = t.workspace_id AND a.deleted_at IS NULL
            JOIN monitoring_configs mc ON mc.target_id = t.id AND mc.workspace_id = t.workspace_id
            WHERE t.deleted_at IS NULL
              AND t.workspace_id IS NOT NULL
              AND COALESCE(t.enabled, FALSE) = TRUE
              AND COALESCE(t.monitoring_enabled, FALSE) = TRUE
              AND LOWER(COALESCE(mc.provider_type, '')) = 'evm_rpc'
              AND LOWER(COALESCE(mc.provider_type, '')) NOT IN ('default')
              AND COALESCE(mc.enabled, FALSE) = TRUE
              AND mc.provider_type NOT IN ('demo', 'simulator', 'replay', 'unknown', 'target_bridge', 'guided_workflow')
            ORDER BY COALESCE(ms.last_heartbeat, t.last_checked_at, '1970-01-01'::timestamptz) ASC, ms.created_at ASC
            ''',
        ).fetchall()
        # Log detailed candidate breakdown for diagnostics
        try:
            _total_targets = connection.execute(
                'SELECT COUNT(*) AS c FROM targets WHERE deleted_at IS NULL',
            ).fetchone()
            _enabled_targets = connection.execute(
                'SELECT COUNT(*) AS c FROM targets WHERE deleted_at IS NULL AND enabled = TRUE',
            ).fetchone()
            _orphan_targets = connection.execute(
                '''
                SELECT COUNT(*) AS c FROM targets t
                LEFT JOIN assets a ON a.id = t.asset_id AND a.workspace_id = t.workspace_id AND a.deleted_at IS NULL
                WHERE t.deleted_at IS NULL AND t.enabled = TRUE AND (t.asset_id IS NULL OR a.id IS NULL)
                ''',
            ).fetchone()
            _valid_asset_linked = connection.execute(
                '''
                SELECT COUNT(*) AS c FROM targets t
                JOIN assets a ON a.id = t.asset_id AND a.workspace_id = t.workspace_id AND a.deleted_at IS NULL
                WHERE t.deleted_at IS NULL AND t.enabled = TRUE
                ''',
            ).fetchone()
            _enabled_monitored_systems = connection.execute(
                "SELECT COUNT(*) AS c FROM monitored_systems WHERE COALESCE(is_enabled, TRUE) = TRUE",
            ).fetchone()
            _enabled_monitoring_configs = connection.execute(
                "SELECT COUNT(*) AS c FROM monitoring_configs WHERE enabled = TRUE",
            ).fetchone()
            logger.info(
                'monitoring_candidate_breakdown total_targets=%s enabled_targets=%s orphan_targets=%s valid_asset_linked_targets=%s enabled_monitored_systems=%s enabled_monitoring_configs=%s total_candidate_targets=%s',
                int((_total_targets or {}).get('c') or 0),
                int((_enabled_targets or {}).get('c') or 0),
                int((_orphan_targets or {}).get('c') or 0),
                int((_valid_asset_linked or {}).get('c') or 0),
                int((_enabled_monitored_systems or {}).get('c') or 0),
                int((_enabled_monitoring_configs or {}).get('c') or 0),
                len(candidate_systems),
            )
        except Exception:
            pass
        cycle_workspace_ids: set[str] = set()
        for row in candidate_systems:
            workspace_id = str((dict(row)).get('workspace_id') or '').strip()
            if workspace_id:
                cycle_workspace_ids.add(workspace_id)
        for workspace_id in sorted(cycle_workspace_ids):
            logger.debug(
                'monitoring_heartbeat_upsert table=monitoring_heartbeats conflict_target=(workspace_id,worker_name) workspace_id=%s worker_name=%s',
                workspace_id,
                worker_name,
            )
            try:
                with connection.transaction():
                    connection.execute(
                        """
                        INSERT INTO monitoring_heartbeats (id, workspace_id, worker_name, last_heartbeat_at, status, metadata)
                        VALUES (%s::uuid, %s::uuid, %s, NOW(), %s, %s::jsonb)
                        ON CONFLICT (workspace_id, worker_name)
                        DO UPDATE SET last_heartbeat_at = EXCLUDED.last_heartbeat_at, status = EXCLUDED.status, metadata = EXCLUDED.metadata
                        """,
                        (str(uuid.uuid4()), workspace_id, worker_name, 'healthy', _json_dumps({'trigger_type': trigger_type})),
                    )
                logger.info(
                    'worker_heartbeat_written workspace_id=%s worker_name=%s trigger_type=%s',
                    workspace_id,
                    worker_name,
                    trigger_type,
                )
            except Exception:
                logger.warning(
                    'monitoring_heartbeat_upsert_failed table=monitoring_heartbeats conflict_target=(workspace_id,worker_name) workspace_id=%s worker_name=%s — apply migration 0080_monitoring_heartbeats_unique_constraint.sql',
                    workspace_id,
                    worker_name,
                )
        now = utc_now()
        max_targets = max(1, min(limit, 200))
        skipped_disabled = 0
        skipped_inactive = 0
        skipped_dead_lettered = 0
        skipped_missing_workspace = 0
        skipped_not_due = 0
        skipped_not_due_target_ids: set[str] = set()
        skipped_not_due_oldest_checked_at: datetime | None = None
        skipped_null_handling = 0
        interval_capped_targets = 0
        # Production floor: a target configured below this is polled no more often
        # than once per _min_interval, so the worker never re-hits eth_blockNumber
        # for it every ~30s. interval_capped_targets counts how many were capped.
        _min_interval = _min_monitoring_interval_seconds()
        due_selection_workspace_snapshot: dict[str, list[dict[str, Any]]] = defaultdict(list)
        soonest_next_due_at: datetime | None = None
        soonest_due_in_seconds: int | None = None
        should_consider_backfill = False
        backfill_attempted = 0
        backfill_evaluated = 0
        backfill_executed = 0
        backfill_blocked_not_yet_due = 0
        backfill_blocked_by_cooldown = 0
        backfill_blocked_missing_candidate = 0
        due_target_ids: list[Any] = []
        due_system_ids: dict[str, str] = {}
        for row in candidate_systems:
            system = dict(row)
            if not bool(system.get('monitored_system_enabled')):
                skipped_disabled += 1
                continue
            if not bool(system.get('monitoring_enabled')) or not bool(system.get('enabled')):
                skipped_disabled += 1
                continue
            if not bool(system.get('is_active')):
                skipped_inactive += 1
                continue
            if system.get('monitoring_dead_lettered_at') is not None:
                skipped_dead_lettered += 1
                continue
            last_checked_at = _parse_ts(system.get('last_checked_at'))
            interval_raw = system.get('monitoring_interval_seconds')
            interval_seconds = _min_interval
            if interval_raw is None:
                if last_checked_at is not None:
                    skipped_null_handling += 1
            else:
                try:
                    parsed_interval_seconds = int(interval_raw)
                    if parsed_interval_seconds < _min_interval:
                        interval_capped_targets += 1
                    interval_seconds = max(_min_interval, parsed_interval_seconds)
                except (TypeError, ValueError):
                    if last_checked_at is not None:
                        skipped_null_handling += 1
                    interval_seconds = _min_interval
            next_due_at = (
                last_checked_at + timedelta(seconds=interval_seconds)
                if last_checked_at is not None
                else now
            )
            seconds_until_due = (next_due_at - now).total_seconds()
            due_in_seconds = 0 if seconds_until_due <= 0 else int(math.ceil(seconds_until_due))
            if soonest_next_due_at is None or next_due_at < soonest_next_due_at:
                soonest_next_due_at = next_due_at
                soonest_due_in_seconds = due_in_seconds
            workspace_id = str(system.get('workspace_id') or '').strip()
            if workspace_id:
                due_selection_workspace_snapshot[workspace_id].append(
                    {
                        'target_id': str(system.get('target_id') or ''),
                        'last_checked_at': last_checked_at.isoformat() if last_checked_at else None,
                        'effective_interval_seconds': interval_seconds,
                        'next_due_at': next_due_at.isoformat(),
                        'due_in_seconds': due_in_seconds,
                    }
                )
            if last_checked_at is None:
                due_target_ids.append(system['target_id'])
                due_system_ids[str(system['target_id'])] = str(system['monitored_system_id'])
            else:
                if last_checked_at <= now - timedelta(seconds=interval_seconds):
                    due_target_ids.append(system['target_id'])
                    due_system_ids[str(system['target_id'])] = str(system['monitored_system_id'])
                else:
                    skipped_not_due += 1
                    skipped_not_due_target_ids.add(str(system.get('target_id') or '').strip())
                    if (
                        skipped_not_due_oldest_checked_at is None
                        or last_checked_at < skipped_not_due_oldest_checked_at
                    ):
                        skipped_not_due_oldest_checked_at = last_checked_at
            if len(due_target_ids) >= max_targets:
                break
        base_due_count = len(due_target_ids)
        # Exclude chain-incompatible targets from due slots to prevent starving valid targets.
        _rpc_chain_id_str = os.getenv('EVM_CHAIN_ID') or os.getenv('STAGING_EVM_CHAIN_ID') or ''
        try:
            _rpc_chain_id = int(_rpc_chain_id_str) if _rpc_chain_id_str else None
        except (ValueError, TypeError):
            _rpc_chain_id = None
        # Inline chain → chain_id table (avoids circular import from evm_activity_provider).
        # Any target whose chain_network resolves to a different chain_id is excluded
        # before consuming a due slot — regardless of which chain the RPC is configured for.
        _known_chain_ids: dict[str, int] = {
            'ethereum': 1, 'ethereum-mainnet': 1, 'mainnet': 1,
            'base': 8453, 'base-mainnet': 8453,
            'polygon': 137, 'polygon-mainnet': 137,
            'arbitrum': 42161, 'arbitrum-one': 42161,
            'optimism': 10, 'optimism-mainnet': 10,
        }
        if _rpc_chain_id is not None:
            _chain_by_target: dict[str, str] = {}
            for _row in candidate_systems:
                _sys = dict(_row)
                _tid_str = str(_sys.get('target_id') or '').strip()
                _chain_by_target[_tid_str] = str(_sys.get('chain_network') or '').lower()
            _filtered_due_ids: list[Any] = []
            _excluded_mismatch = 0
            for _tid in due_target_ids:
                _tid_str = str(_tid).strip()
                _chain = _chain_by_target.get(_tid_str, '')
                _target_chain_id = _known_chain_ids.get(_chain) if _chain else None
                if _target_chain_id is not None and _target_chain_id != _rpc_chain_id:
                    logger.warning(
                        'monitoring_chain_mismatch_excluded target_id=%s chain_network=%s '
                        'target_chain_id=%s rpc_chain_id=%s action=excluded_from_due_slots',
                        _tid_str, _chain, _target_chain_id, _rpc_chain_id,
                    )
                    _excluded_mismatch += 1
                else:
                    _filtered_due_ids.append(_tid)
            if _excluded_mismatch:
                logger.warning(
                    'monitoring_chain_mismatch_summary rpc_chain_id=%s excluded=%s remaining_due=%s',
                    _rpc_chain_id, _excluded_mismatch, len(_filtered_due_ids),
                )
                due_target_ids = _filtered_due_ids
                _filtered_due_str = {str(t) for t in due_target_ids}
                due_system_ids = {k: v for k, v in due_system_ids.items() if k in _filtered_due_str}
        for workspace_id, entries in sorted(due_selection_workspace_snapshot.items()):
            soonest_entries = sorted(
                entries,
                key=lambda item: item.get('next_due_at') or '',
            )[:3]
            logger.info(
                'monitoring due-selection snapshot worker=%s workspace_id=%s total_candidates=%s soonest_next_due_targets=%s',
                worker_name,
                workspace_id,
                len(entries),
                _json_dumps(soonest_entries),
            )
        logger.info(
            'monitoring due-selection horizon worker=%s soonest_next_due_at=%s soonest_due_in_seconds=%s',
            worker_name,
            soonest_next_due_at.isoformat() if soonest_next_due_at else None,
            soonest_due_in_seconds,
        )
        oldest_candidate: dict[str, Any] | None = None
        oldest_checked_at: datetime | None = None
        for row in candidate_systems:
            system = dict(row)
            if (
                not bool(system.get('monitored_system_enabled'))
                or not bool(system.get('monitoring_enabled'))
                or not bool(system.get('enabled'))
                or not bool(system.get('is_active'))
            ):
                continue
            # Dead-lettered targets are excluded by the FOR UPDATE claim query, so they can
            # never be claimed via backfill. Picking one as the backfill candidate would burn
            # the per-workspace backfill cooldown without ever checking a target, permanently
            # starving recovery. Skip them here — the dead-letter fast-recovery path above is
            # responsible for returning them to normal due-selection.
            if system.get('monitoring_dead_lettered_at') is not None:
                continue
            # Backfill candidate must be on this worker's chain. Never pick an
            # Ethereum target as the Base worker's backfill fallback — it would
            # only hard-skip with no RPC and burn the backfill cooldown.
            if _rpc_chain_id is not None:
                _cand_chain = str(system.get('chain_network') or '').lower()
                _cand_chain_id = _known_chain_ids.get(_cand_chain) if _cand_chain else None
                if _cand_chain_id is not None and _cand_chain_id != _rpc_chain_id:
                    continue
            parsed_checked = _parse_ts(system.get('last_checked_at'))
            if parsed_checked is None:
                continue
            if oldest_checked_at is None or parsed_checked < oldest_checked_at:
                oldest_checked_at = parsed_checked
                oldest_candidate = system
        fallback_target_id = str((oldest_candidate or {}).get('target_id') or '').strip()
        fallback_system_id = str((oldest_candidate or {}).get('monitored_system_id') or '').strip()
        fallback_workspace_id = str((oldest_candidate or {}).get('workspace_id') or '').strip()
        fallback_interval_raw = (oldest_candidate or {}).get('monitoring_interval_seconds')
        fallback_interval_seconds = _min_interval
        if fallback_interval_raw is not None:
            try:
                fallback_interval_seconds = max(_min_interval, int(fallback_interval_raw))
            except (TypeError, ValueError):
                fallback_interval_seconds = _min_interval
        fallback_next_due_at = (
            oldest_checked_at + timedelta(seconds=fallback_interval_seconds)
            if oldest_checked_at is not None
            else None
        )
        fallback_due_in_seconds = (
            int(math.ceil((fallback_next_due_at - now).total_seconds()))
            if fallback_next_due_at is not None
            else None
        )
        fallback_is_due = bool(fallback_next_due_at is not None and now >= fallback_next_due_at)
        oldest_age_seconds = (
            max(0, int((now - oldest_checked_at).total_seconds()))
            if oldest_checked_at is not None
            else None
        )
        backfill_cooldown_seconds = MONITORING_DUE_SELECTION_BACKFILL_COOLDOWN_SECONDS
        last_backfill_at = _LAST_MONITORING_DUE_SELECTION_BACKFILL_AT.get(fallback_workspace_id)
        cooldown_elapsed = (
            last_backfill_at is None
            or int((now - last_backfill_at).total_seconds()) >= backfill_cooldown_seconds
        )
        no_due_targets = not due_target_ids
        workspace_live_mode = live_mode_enabled()
        # During a provider 429 backoff we do not run live backfill — polling the
        # fallback target would only hard-skip (no RPC) and burn the cooldown.
        _provider_backoff = rpc_provider_backoff_active()
        should_consider_backfill = bool(
            no_due_targets
            and workspace_live_mode
            and fallback_is_due
            and not _provider_backoff
        )
        if no_due_targets and workspace_live_mode and fallback_is_due and _provider_backoff:
            logger.warning(
                'due_selection_backfill_suspended reason=provider_rate_limited worker=%s',
                worker_name,
            )
        has_backfill_candidate = bool(
            fallback_target_id
            and fallback_system_id
            and fallback_workspace_id
        )
        if should_consider_backfill:
            if has_backfill_candidate:
                backfill_evaluated = 1
                if not cooldown_elapsed:
                    backfill_attempted = 1
                    backfill_blocked_by_cooldown = 1
                else:
                    backfill_attempted = 1
                    due_target_ids.append(fallback_target_id)
                    due_system_ids[fallback_target_id] = fallback_system_id
                    _LAST_MONITORING_DUE_SELECTION_BACKFILL_AT[fallback_workspace_id] = now
                    backfill_executed = 1
            else:
                backfill_attempted = 0
                backfill_evaluated = 0
                backfill_executed = 0
                backfill_blocked_missing_candidate = 1
        else:
            backfill_attempted = 0
            backfill_evaluated = 0
            backfill_executed = 0
            backfill_blocked_not_yet_due = 0
            backfill_blocked_by_cooldown = 0
            backfill_blocked_missing_candidate = 0
        effective_due_count = len(due_target_ids)
        effective_skipped_not_due = skipped_not_due
        oldest_not_due_age_seconds = (
            max(0, int((now - skipped_not_due_oldest_checked_at).total_seconds()))
            if skipped_not_due_oldest_checked_at is not None
            else None
        )
        if backfill_executed > 0 and fallback_target_id in skipped_not_due_target_ids:
            effective_skipped_not_due = max(0, skipped_not_due - 1)
        # Per-target due-selection diagnostics: emit one truthful line per candidate so the
        # exact reason a target is or is not processed this cycle is visible in worker logs.
        # backfill appended fallback_target_id to due_target_ids, so distinguish it from the
        # live-poll set explicitly.
        _live_poll_id_set = {str(item) for item in due_target_ids}
        _backfill_selected_id = str(fallback_target_id) if backfill_executed > 0 else ''
        if _backfill_selected_id:
            _live_poll_id_set.discard(_backfill_selected_id)
        for row in candidate_systems:
            system = dict(row)
            _tid = str(system.get('target_id') or '').strip()
            _wsid = str(system.get('workspace_id') or '').strip()
            _dead_lettered = system.get('monitoring_dead_lettered_at') is not None
            _selected_for_live_poll = _tid in _live_poll_id_set
            _selected_for_backfill = bool(_backfill_selected_id) and _tid == _backfill_selected_id
            _last_backfill = _LAST_MONITORING_DUE_SELECTION_BACKFILL_AT.get(_wsid)
            _cooldown_until = (
                (_last_backfill + timedelta(seconds=backfill_cooldown_seconds)).isoformat()
                if _last_backfill is not None
                else None
            )
            _blocked_reason: str | None = None
            if _selected_for_live_poll or _selected_for_backfill:
                _blocked_reason = None
            elif _dead_lettered:
                _blocked_reason = 'dead_lettered'
            elif not bool(system.get('monitored_system_enabled')) or not bool(system.get('monitoring_enabled')) or not bool(system.get('enabled')):
                _blocked_reason = 'disabled'
            elif not bool(system.get('is_active')):
                _blocked_reason = 'inactive'
            elif _tid in skipped_not_due_target_ids:
                _blocked_reason = 'not_due'
            else:
                _blocked_reason = 'not_selected'
            logger.info(
                'monitoring target-selection target_id=%s workspace_id=%s '
                'selected_for_live_poll=%s selected_for_backfill=%s dead_lettered=%s '
                'blocked_reason=%s cooldown_until=%s',
                _tid,
                _wsid,
                _selected_for_live_poll,
                _selected_for_backfill,
                _dead_lettered,
                _blocked_reason,
                _cooldown_until,
            )
        due_targets = []
        if due_target_ids:
            due_target_id_set = {str(item) for item in due_target_ids}
            due_workspace_ids: set[str] = set()
            for row in candidate_systems:
                system = dict(row)
                target_id = str(system.get('target_id') or '').strip()
                workspace_id = str(system.get('workspace_id') or '').strip()
                if target_id in due_target_id_set and workspace_id:
                    due_workspace_ids.add(workspace_id)
            for workspace_id in sorted(due_workspace_ids):
                run_id = str(uuid.uuid4())
                workspace_run_ids[workspace_id] = run_id
                connection.execute(
                    '''
                    INSERT INTO monitoring_runs (
                        id,
                        workspace_id,
                        started_at,
                        status,
                        trigger_type,
                        systems_checked_count,
                        assets_checked_count,
                        detections_created_count,
                        alerts_created_count,
                        telemetry_records_seen_count,
                        notes
                    )
                    VALUES (%s::uuid, %s::uuid, NOW(), 'running', %s, 0, 0, 0, 0, 0, %s)
                    ''',
                    (run_id, workspace_id, trigger_type, f'worker_name={worker_name}'),
                )
            lease_token = str(uuid.uuid4())
            lease_seconds = max(30, int(os.getenv('MONITORING_TARGET_LEASE_SECONDS', '300')))
            # The lease is persisted before this transaction releases row locks. A second
            # deployment therefore cannot claim the same target, while a terminated worker's
            # target becomes recoverable automatically after lease_expires_at.
            due_targets = connection.execute(
                '''
                WITH candidates AS (
                    SELECT id
                    FROM targets
                    WHERE id = ANY(%s)
                      AND (monitoring_lease_expires_at IS NULL OR monitoring_lease_expires_at <= NOW())
                      AND monitoring_dead_lettered_at IS NULL
                      AND monitoring_delivery_attempts < %s
                    ORDER BY COALESCE(last_checked_at, '1970-01-01'::timestamptz) ASC, created_at ASC
                    FOR UPDATE SKIP LOCKED
                )
                UPDATE targets t
                SET monitoring_claimed_by = %s,
                    monitoring_claimed_at = NOW(),
                    monitoring_lease_token = %s::uuid,
                    monitoring_lease_expires_at = NOW() + (%s * INTERVAL '1 second')
                FROM candidates c
                WHERE t.id = c.id
                RETURNING t.id, t.workspace_id, t.name, t.target_type, t.chain_network, t.contract_identifier, t.wallet_address, t.asset_type, t.owner_notes, t.severity_preference, t.enabled,
                       t.asset_id, t.chain_id, t.target_metadata, t.monitoring_enabled, t.monitoring_mode, t.monitoring_interval_seconds, t.severity_threshold, t.auto_create_alerts,
                       t.auto_create_incidents, t.notification_channels, t.last_checked_at, t.last_run_status, t.last_run_id, t.last_alert_at, t.monitored_by_workspace_id, t.is_active,
                       t.monitoring_checkpoint_at, t.monitoring_checkpoint_cursor, t.watcher_last_observed_block, t.watcher_checkpoint_lag_blocks, t.watcher_source_status,
                       t.watcher_degraded_reason, t.recent_evidence_state, t.recent_truthfulness_state, t.recent_real_event_count, t.updated_by_user_id, t.created_by_user_id, t.created_at,
                       t.monitoring_lease_token, t.monitoring_lease_expires_at
                ''',
                (due_target_ids, max(1, int(os.getenv('MONITORING_TARGET_MAX_ATTEMPTS', '5'))), worker_name, lease_token, lease_seconds),
            ).fetchall()
            due_targets = sorted(
                (dict(row) for row in due_targets),
                key=lambda item: (item.get('last_checked_at') or datetime(1970, 1, 1, tzinfo=timezone.utc), item.get('created_at')),
            )
        else:
            due_targets = []
        due_count = len(due_targets)
        for row in due_targets:
            target = dict(row)
            target['monitored_system_id'] = due_system_ids.get(str(target['id']))
            workspace_id = str(target.get('workspace_id') or '').strip()
            poll_id = str(uuid.uuid4())
            poll_started_at = utc_now()
            try:
                # Guard: verify target_id exists in the canonical targets table.
                # monitoring_polls.target_id FK references targets(id) after migration 0081.
                # The target was just fetched via FOR UPDATE SKIP LOCKED, so this should
                # always pass; the guard handles race-condition deletes between fetch and poll.
                _poll_parent = connection.execute(
                    'SELECT 1 FROM targets WHERE id = %s LIMIT 1',
                    (target['id'],),
                ).fetchone()
                if not _poll_parent:
                    logger.warning(
                        'skip_reason=missing_poll_parent_target target_id=%s workspace_id=%s',
                        target.get('id'), workspace_id,
                    )
                    continue
                # Preflight: confirm target_id is in the canonical targets table before
                # any INSERT that carries a FK -> targets(id) (provider_health_records,
                # target_coverage_records, monitoring_polls).  The poll-parent guard above
                # already covers this for monitoring_polls; this check protects the other
                # two tables and produces an explicit log line if schema is misaligned.
                _phk_tables_with_target_fk = [
                    ('provider_health_records', 'provider_health_records_target_id_fkey'),
                    ('target_coverage_records', 'target_coverage_records_target_id_fkey'),
                ]
                for _phk_table, _phk_constraint in _phk_tables_with_target_fk:
                    try:
                        _fk_row = connection.execute(
                            '''
                            SELECT ccu.table_name AS parent_table
                            FROM information_schema.table_constraints tc
                            JOIN information_schema.referential_constraints rc
                                ON tc.constraint_name = rc.constraint_name
                                AND tc.constraint_schema = rc.constraint_schema
                            JOIN information_schema.constraint_column_usage ccu
                                ON rc.unique_constraint_name = ccu.constraint_name
                                AND rc.unique_constraint_schema = ccu.constraint_schema
                            WHERE tc.constraint_type = 'FOREIGN KEY'
                              AND tc.table_name = %s
                              AND tc.constraint_name = %s
                            LIMIT 1
                            ''',
                            (_phk_table, _phk_constraint),
                        ).fetchone()
                        _fk_parent = (_fk_row['parent_table'] if isinstance(_fk_row, dict) else _fk_row[0]) if _fk_row else None
                        if _fk_parent and _fk_parent != 'targets':
                            logger.error(
                                'code=MONITORING_TARGET_FK_MISMATCH table=%s constraint=%s '
                                'expected_parent=targets actual_parent=%s target_id=%s '
                                'fix=run_migration_0082',
                                _phk_table, _phk_constraint, _fk_parent, target.get('id'),
                            )
                    except Exception:
                        pass
                # Short transaction: persist the poll record + claim the target, then
                # COMMIT before the RPC scan. process_monitoring_target performs slow
                # RPC + threat-engine I/O and must run with NO open DB transaction so the
                # worker connection is never idle-in-transaction during the scan (autocommit
                # commits each subsequent write group on its own). The persisted lease — not
                # an open row lock — is what keeps a second worker from double-claiming.
                with connection.transaction():
                    connection.execute(
                        """
                        INSERT INTO monitoring_polls (id, workspace_id, target_id, poll_started_at, status, metadata)
                        VALUES (%s::uuid, %s::uuid, %s::uuid, %s, 'running', %s::jsonb)
                        """,
                        (poll_id, str(target['workspace_id']), str(target['id']), poll_started_at, _json_dumps({'worker_name': worker_name})),
                    )
                    connection.execute(
                        'UPDATE targets SET monitoring_claimed_by = %s, monitoring_claimed_at = NOW() WHERE id = %s AND workspace_id = %s',
                        (worker_name, target['id'], target['workspace_id']),
                    )
                result = process_monitoring_target(
                    connection,
                    target,
                    monitoring_run_id=workspace_run_ids.get(workspace_id),
                )
                if result.get('provider_poll_skipped'):
                    # Provider 429 backoff: no RPC poll happened. Record the poll as
                    # skipped (never 'completed'), count it as skipped_provider_backoff
                    # (not checked), and skip the monitored-systems heartbeat update and
                    # the poll_completed log. The target's claim was released inside
                    # process_monitoring_target so it is re-evaluated on the next cycle.
                    connection.execute(
                        "UPDATE monitoring_polls SET poll_finished_at = NOW(), status = 'skipped', error_message = NULL WHERE id = %s::uuid",
                        (poll_id,),
                    )
                    skipped_provider_backoff += 1
                    continue
                monitored_system_id = due_system_ids.get(str(target['id']))
                if monitored_system_id:
                    runtime_status, freshness_status, confidence_status, coverage_reason = _derive_system_runtime_state(
                        result,
                        is_enabled=True,
                    )
                    status_params = (runtime_status, 'active', monitored_system_id)
                    if runtime_status not in {'provisioning', 'healthy', 'idle', 'degraded'}:
                        status_params = (runtime_status, 'error' if runtime_status == 'failed' else 'paused', monitored_system_id)
                    for _deadlock_attempt in range(3):
                        try:
                            with connection.transaction():
                                connection.execute(
                                    '''
                              UPDATE monitored_systems
                              SET last_heartbeat = NOW(),
                                  runtime_status = %s,
                                  status = %s
                              WHERE id = %s::uuid
                              AND workspace_id = %s::uuid
                                    ''',
                                    (*status_params, str(target['workspace_id'])),
                                )
                                connection.execute(
                                    '''
                              UPDATE monitored_systems ms
                              SET last_heartbeat = NOW(),
                                  last_event_at = COALESCE(%s, last_event_at),
                                  last_coverage_telemetry_at = COALESCE(%s, last_coverage_telemetry_at),
                                  freshness_status = %s,
                                  confidence_status = %s,
                                  coverage_reason = %s,
                                  last_error_text = NULL
                              WHERE ms.id = %s::uuid
                              AND ms.workspace_id = %s::uuid
                                    ''',
                                    (
                                        result.get('last_event_at') or target.get('watcher_last_event_at'),
                                        result.get('live_coverage_telemetry_at'),
                                        freshness_status,
                                        confidence_status,
                                        coverage_reason,
                                        monitored_system_id,
                                        str(target['workspace_id']),
                                    ),
                                )
                            monitored_systems_updated += 1
                            break
                        except psycopg_errors.DeadlockDetected:
                            if _deadlock_attempt < 2:
                                sleep(0.05 * (2 ** _deadlock_attempt))
                            else:
                                logger.warning(
                                    'deadlock_retry_exhausted monitored_system_id=%s workspace_id=%s',
                                    monitored_system_id, str(target['workspace_id']),
                                )
                connection.execute("UPDATE monitoring_polls SET poll_finished_at = NOW(), status = %s, error_message = NULL WHERE id = %s::uuid", ('completed', poll_id))
                logger.info(
                    'poll_completed target_id=%s poll_id=%s checked=1 '
                    'alerts=%s detections=%s events_ingested=%s monitoring_run_id=%s',
                    target.get('id'), poll_id,
                    result.get('alerts_generated', 0),
                    result.get('detections_created', 0),
                    result.get('events_ingested', 0),
                    result.get('monitoring_run_id'),
                )
                # Queue a post-cycle Strategic Guard backfill for wallet targets only
                # (wallet_transfer telemetry is wallet-specific). Idempotent + create-only,
                # so this catches up historical rows the scheduled backfill never selected.
                if workspace_id and target.get('id') and str(target.get('target_type') or '').lower() == 'wallet':
                    strategic_guard_backfill_targets.add((workspace_id, str(target['id'])))
                alerts_generated += int(result['alerts_generated'])
                if workspace_id:
                    workspace_systems_checked[workspace_id] += 1
                    asset_id = str(target.get('asset_id') or '').strip()
                    if asset_id:
                        workspace_assets_checked[workspace_id].add(asset_id)
                    result_events_ingested = int(result.get('events_ingested', 0))
                    result_telemetry_records_seen = int(result.get('telemetry_records_seen', result_events_ingested))
                    workspace_detections_created[workspace_id] += int(result.get('detections_created', 0))
                    workspace_alerts_created[workspace_id] += int(result.get('alerts_generated', 0))
                    workspace_telemetry_seen[workspace_id] += result_telemetry_records_seen
                    workspace_real_events_detected[workspace_id] += int(result.get('real_events_detected', result.get('real_event_count', 0)) or 0)
                    workspace_coverage_heartbeat_updates[workspace_id] += int(result.get('coverage_heartbeat_updates', result.get('coverage_heartbeat_count', 0)) or 0)
                    provider_status = str(result.get('provider_status') or '').strip().lower()
                    source_status = str(result.get('source_status') or '').strip().lower()
                    if provider_status in {'live', 'no_evidence', 'degraded'} and source_status in {'live', 'no_evidence', 'active'}:
                        workspace_provider_reachable_cycles[workspace_id] += 1
                live_targets_checked += 1 if is_monitorable_target_type(target.get('target_type')) else 0
                events_ingested += int(result.get('events_ingested', 0))
                real_events_detected += int(result.get('real_events_detected', result.get('real_event_count', 0)))
                coverage_heartbeat_updates += int(result.get('coverage_heartbeat_updates', result.get('coverage_heartbeat_count', 0)))
                incidents_created += int(result.get('incidents_created', 0))
                runs.append(result)
                checked += 1
            except Exception as exc:
                error_message = str(exc)
                if workspace_id:
                    workspace_errors[workspace_id] = str(exc)
                logger.exception('monitoring target failed target=%s name=%s', target.get('id'), target.get('name'))
                # If the failure tore down the worker connection (idle-in-transaction
                # timeout / server close), the same connection must NOT be reused for the
                # error bookkeeping — issuing more statements on a closed socket just raises
                # "the connection is closed" again. In that case open ONE fresh autocommit
                # connection for the error-status writes; for ordinary errors keep using the
                # live connection (a new short transaction isolates any InFailedSqlTransaction).
                _conn_lost = _is_connection_lost_error(exc)
                if _conn_lost:
                    logger.warning(
                        'monitoring_worker_connection_lost target_id=%s workspace_id=%s '
                        'action=record_error_on_fresh_connection',
                        target.get('id'), workspace_id,
                    )

                def _record_target_error(_err_conn: Any) -> None:
                    nonlocal monitored_systems_updated
                    with _err_conn.transaction():
                        max_target_attempts = max(1, int(os.getenv('MONITORING_TARGET_MAX_ATTEMPTS', '5')))
                        _err_conn.execute(
                            '''
                            UPDATE targets SET
                                last_checked_at = NOW(),
                                monitoring_delivery_attempts = monitoring_delivery_attempts + 1,
                                last_run_status = CASE WHEN monitoring_delivery_attempts + 1 >= %s THEN 'dead_letter' ELSE 'error' END,
                                monitoring_dead_lettered_at = CASE WHEN monitoring_delivery_attempts + 1 >= %s THEN NOW() ELSE NULL END,
                                monitoring_claimed_by = NULL,
                                monitoring_claimed_at = NULL,
                                monitoring_lease_token = NULL,
                                monitoring_lease_expires_at = NULL
                            WHERE id = %s AND workspace_id = %s
                            ''',
                            (max_target_attempts, max_target_attempts, target['id'], target['workspace_id']),
                        )
                        monitored_system_id = due_system_ids.get(str(target['id']))
                        if monitored_system_id and not isinstance(exc, psycopg_errors.DeadlockDetected):
                            # Keep explicit status transition text stable for regression checks:
                            # 'error', status = 'error'
                            _err_conn.execute(
                                "UPDATE monitored_systems SET runtime_status = 'failed', status = 'error', freshness_status = 'unavailable', confidence_status = 'low', coverage_reason = 'monitoring_worker_error', last_error_text = %s, last_heartbeat = NOW() WHERE id = %s::uuid AND workspace_id = %s::uuid",
                                (error_message, monitored_system_id, str(target['workspace_id'])),
                            )
                            monitored_systems_updated += 1
                        _err_conn.execute("UPDATE monitoring_polls SET poll_finished_at = NOW(), status = 'degraded', error_message = %s WHERE id = %s::uuid", (error_message, poll_id))

                try:
                    if _conn_lost:
                        with pg_connection() as _err_conn:
                            try:
                                _err_conn.autocommit = True
                            except Exception:
                                pass
                            _record_target_error(_err_conn)
                    else:
                        _record_target_error(connection)
                except Exception as _err_handler_exc:
                    # Even the fresh connection failed — log once and keep shutting the cycle
                    # down safely instead of raising out of the error handler.
                    logger.warning(
                        'error_handler_failed target=%s connection_lost=%s error=%s action=continue_cycle',
                        target.get('id'), str(_conn_lost).lower(), _safe_error_message(_err_handler_exc),
                    )
        for workspace_id, monitoring_run_id in workspace_run_ids.items():
            workspace_note = f'worker_name={worker_name}'
            workspace_error = workspace_errors.get(workspace_id)
            if workspace_error:
                workspace_note = f'{workspace_note};error={workspace_error}'
            connection.execute(
                '''
                UPDATE monitoring_runs
                SET completed_at = NOW(),
                    status = %s,
                    systems_checked_count = %s,
                    assets_checked_count = %s,
                    detections_created_count = %s,
                    alerts_created_count = %s,
                    telemetry_records_seen_count = %s,
                    notes = %s
                WHERE id = %s::uuid
                  AND workspace_id = %s::uuid
                ''',
                (
                    'error' if workspace_error else 'completed',
                    int(workspace_systems_checked.get(workspace_id, 0)),
                    len(workspace_assets_checked.get(workspace_id, set())),
                    int(workspace_detections_created.get(workspace_id, 0)),
                    int(workspace_alerts_created.get(workspace_id, 0)),
                    int(workspace_telemetry_seen.get(workspace_id, 0)),
                    workspace_note,
                    monitoring_run_id,
                    workspace_id,
                ),
            )
        if checked > 0:
            for workspace_id in sorted(workspace_systems_checked.keys()):
                warning_state = _workspace_coverage_only_state(
                    workspace_id=workspace_id,
                    cycle_at=cycle_started_at,
                    provider_reachable=int(workspace_provider_reachable_cycles.get(workspace_id, 0)) > 0,
                    coverage_heartbeat_updates=int(workspace_coverage_heartbeat_updates.get(workspace_id, 0)),
                    real_events_detected=int(workspace_real_events_detected.get(workspace_id, 0)),
                )
                if warning_state.get('active'):
                    logger.warning(
                        'monitoring_workspace_no_evidence_persistent workspace_id=%s state=%s cycle_count=%s duration_seconds=%s threshold_seconds=%s',
                        workspace_id,
                        warning_state.get('state'),
                        warning_state.get('cycle_count'),
                        warning_state.get('duration_seconds'),
                        warning_state.get('threshold_seconds'),
                    )
            for workspace_id in sorted(cycle_workspace_ids):
                if int(workspace_coverage_heartbeat_updates.get(workspace_id, 0)) > 0:
                    try:
                        with connection.transaction():
                            _ensure_workspace_live_rpc_proof_chain(connection, workspace_id=workspace_id)
                    except Exception:
                        logger.warning('live_rpc_proof_chain_failed workspace_id=%s', workspace_id)
                active_alerts_row = connection.execute(
                    """
                    SELECT COUNT(*) AS c
                    FROM alerts
                    WHERE workspace_id = %s::uuid
                      AND status IN ('open', 'acknowledged', 'investigating')
                    """,
                    (workspace_id,),
                ).fetchone()
                active_incidents_row = connection.execute(
                    """
                    SELECT COUNT(*) AS c
                    FROM incidents
                    WHERE workspace_id = %s::uuid
                      AND status IN ('open', 'acknowledged')
                    """,
                    (workspace_id,),
                ).fetchone()
                connection.execute(
                    """
                    INSERT INTO monitoring_workspace_runtime_summary (
                        workspace_id,
                        active_alerts_count,
                        active_incidents_count,
                        updated_at
                    )
                    VALUES (
                        %s::uuid,
                        CAST(%s AS integer),
                        CAST(%s AS integer),
                        NOW()
                    )
                    ON CONFLICT (workspace_id)
                    DO UPDATE SET
                        active_alerts_count = EXCLUDED.active_alerts_count,
                        active_incidents_count = EXCLUDED.active_incidents_count,
                        updated_at = NOW()
                    """,
                    (
                        workspace_id,
                        int((active_alerts_row or {}).get('c') or 0),
                        int((active_incidents_row or {}).get('c') or 0),
                    ),
                )
        connection.execute(
            '''
            UPDATE monitoring_worker_state
            SET running = FALSE,
                status = CASE WHEN CAST(%s AS text) IS NULL THEN 'idle' ELSE 'error' END,
                last_heartbeat_at = NOW(),
                last_cycle_at = NOW(),
                last_cycle_due_targets = CAST(%s AS integer),
                last_cycle_targets_checked = CAST(%s AS integer),
                last_cycle_alerts_generated = CAST(%s AS integer),
                last_error = CAST(%s AS text),
                updated_at = NOW()
            WHERE worker_name = %s
            ''',
            (error_message, due_count, checked, alerts_generated, error_message, worker_name),
        )
        connection.execute(
            '''
            INSERT INTO monitor_heartbeat (
                id, workspace_id, chain, status, last_success_at, last_error_at, last_error_text, last_processed_block, provider_mode, updated_at
            )
            VALUES (%s, NULL, %s, %s, CASE WHEN %s::text IS NULL THEN NOW() ELSE NULL END, CASE WHEN %s::text IS NULL THEN NULL ELSE NOW() END, %s, %s, %s, NOW())
            ''',
            (
                str(uuid.uuid4()),
                str(os.getenv('EVM_CHAIN_NETWORK', 'ethereum')),
                'error' if error_message else ('idle' if checked == 0 else 'active'),
                error_message,
                error_message,
                error_message,
                max([int(item.get('latest_processed_block') or 0) for item in runs], default=0),
                ingestion_runtime.get('source') or 'polling',
            ),
        )
        connection.commit()
    # Scheduler-independent Strategic Infrastructure Guard backfill, run AFTER the cycle
    # committed (heartbeat persisted) on fresh connections — never holding the worker's
    # connection. The scheduled backfill selects one target per cooldown, so a Base wallet
    # that is live-polled but has selected_for_backfill=False keeps older wallet-transfer
    # rows without alerts; this closes that gap. Create-only/idempotent; best-effort so a
    # backfill error never fails the cycle.
    # During a provider 429 backoff we suspend it: do not turn old stale rows into
    # fresh-looking alerts while live monitoring is rate-limited.
    if strategic_guard_backfill_targets and rpc_provider_backoff_active():
        logger.warning(
            'strategic_guard_backfill_suspended reason=provider_rate_limited count=%s',
            len(strategic_guard_backfill_targets),
        )
        strategic_guard_backfill_targets = set()
    for _bf_workspace_id, _bf_target_id in sorted(strategic_guard_backfill_targets):
        try:
            _bf_result = backfill_strategic_guard_alerts_for_target(_bf_workspace_id, _bf_target_id)
            if int(_bf_result.get('created_count') or 0) > 0:
                alerts_generated += int(_bf_result.get('created_count') or 0)
        except Exception:
            logger.warning(
                'strategic_guard_target_backfill_failed workspace_id=%s target_id=%s',
                _bf_workspace_id, _bf_target_id, exc_info=True,
            )
    WORKER_STATE.update(
        {
            'worker_name': worker_name,
            'worker_running': False,
            'last_cycle_at': cycle_started_at.isoformat(),
            'last_cycle_due_targets': due_count,
            'last_cycle_targets_checked': checked,
            'last_cycle_alerts_generated': alerts_generated,
            'last_error': error_message,
        }
    )
    cycle_duration_ms = int((utc_now() - cycle_started_at).total_seconds() * 1000)
    backfill_cycle_state = 'normal_due_cycle'
    if due_count == 0 and not should_consider_backfill:
        backfill_cycle_state = 'normal_no_due_cycle'
    elif backfill_executed > 0:
        backfill_cycle_state = 'backfill_executed_cycle'
    elif backfill_evaluated > 0:
        backfill_cycle_state = 'backfill_evaluated_blocked'
    elif due_count == 0:
        backfill_cycle_state = 'normal_no_due_cycle'
    logger.info(
        'monitoring cycle summary worker=%s cycle_state=%s total_candidate_targets=%s base_due_count=%s '
        'effective_due_count=%s due=%s checked=%s skipped_provider_backoff=%s '
        'skipped_disabled=%s skipped_inactive=%s skipped_dead_lettered=%s skipped_missing_workspace=%s skipped_not_due=%s '
        'oldest_not_due_age_seconds=%s skipped_null_handling=%s interval_capped_targets=%s backfill_attempted=%s backfill_evaluated=%s backfill_executed=%s '
        'backfill_blocked_not_yet_due=%s backfill_blocked_by_cooldown=%s backfill_blocked_missing_candidate=%s '
        'soonest_due_in_seconds=%s next_sleep_seconds=%s '
        'live_targets=%s real_events=%s coverage_heartbeat_updates=%s alerts=%s incidents=%s monitored_systems_updated=%s duration_ms=%s',
        worker_name,
        backfill_cycle_state,
        len(candidate_systems) if 'candidate_systems' in locals() else 0,
        base_due_count if 'base_due_count' in locals() else 0,
        effective_due_count if 'effective_due_count' in locals() else due_count,
        effective_due_count if 'effective_due_count' in locals() else due_count,
        checked,
        skipped_provider_backoff,
        skipped_disabled if 'skipped_disabled' in locals() else 0,
        skipped_inactive if 'skipped_inactive' in locals() else 0,
        skipped_dead_lettered if 'skipped_dead_lettered' in locals() else 0,
        skipped_missing_workspace if 'skipped_missing_workspace' in locals() else 0,
        effective_skipped_not_due if 'effective_skipped_not_due' in locals() else 0,
        oldest_not_due_age_seconds if 'oldest_not_due_age_seconds' in locals() else None,
        skipped_null_handling if 'skipped_null_handling' in locals() else 0,
        interval_capped_targets if 'interval_capped_targets' in locals() else 0,
        backfill_attempted if 'backfill_attempted' in locals() else 0,
        backfill_evaluated if 'backfill_evaluated' in locals() else 0,
        backfill_executed if 'backfill_executed' in locals() else 0,
        backfill_blocked_not_yet_due if ('backfill_evaluated' in locals() and backfill_evaluated > 0) else 0,
        backfill_blocked_by_cooldown if ('backfill_evaluated' in locals() and backfill_evaluated > 0) else 0,
        backfill_blocked_missing_candidate if 'backfill_blocked_missing_candidate' in locals() else 0,
        soonest_due_in_seconds if 'soonest_due_in_seconds' in locals() else None,
        None,
        live_targets_checked,
        real_events_detected,
        coverage_heartbeat_updates,
        alerts_generated,
        incidents_created,
        monitored_systems_updated,
        cycle_duration_ms,
    )
    WORKER_STATE['ingestion_mode'] = ingestion_runtime.get('source')
    WORKER_STATE['degraded'] = bool(ingestion_runtime.get('degraded'))
    for workspace_id in cycle_workspace_ids:
        RUNTIME_STATUS_WORKSPACE_CACHE.pop(f'workspace:{workspace_id}', None)
        RUNTIME_STATUS_SUMMARY_CACHE.pop(f'workspace:{workspace_id}', None)
    return {
        'due_targets': due_count,
        'checked': checked,
        'skipped_provider_backoff': skipped_provider_backoff,
        'live_targets_checked': live_targets_checked,
        'events_ingested': events_ingested,
        'real_events_detected': real_events_detected,
        'coverage_heartbeat_updates': coverage_heartbeat_updates,
        'alerts_generated': alerts_generated,
        'incidents_created': incidents_created,
        'cycle_duration_ms': cycle_duration_ms,
        'runs': runs,
        'live_mode': True,
        'effective_due_count': effective_due_count if 'effective_due_count' in locals() else due_count,
        'soonest_due_in_seconds': soonest_due_in_seconds,
        'ingestion_mode': ingestion_runtime.get('source'),
        'degraded': bool(ingestion_runtime.get('degraded')),
    }


def list_monitoring_targets(request: Request) -> dict[str, Any]:
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        rows = connection.execute(
            '''
            SELECT t.id, t.workspace_id, t.name, t.target_type, t.chain_network, t.enabled, t.monitoring_enabled, t.monitoring_mode,
                   t.monitoring_interval_seconds, t.severity_threshold, t.auto_create_alerts, t.auto_create_incidents,
                   t.notification_channels, t.last_checked_at, t.last_run_status, t.last_run_id, t.last_alert_at, t.is_active,
                   t.monitoring_checkpoint_at, t.monitoring_checkpoint_cursor, t.watcher_last_observed_block, t.watcher_checkpoint_lag_blocks, t.watcher_source_status, t.watcher_degraded_reason,
                   t.last_real_event_at, t.last_no_evidence_at, t.last_degraded_at, t.last_failed_monitoring_at, t.recent_evidence_state, t.recent_truthfulness_state, t.recent_real_event_count,
                   t.asset_id, a.id AS resolved_asset_id, a.name AS asset_name, ms.id AS monitored_system_id
            FROM targets t
            LEFT JOIN assets a ON a.id = t.asset_id AND a.workspace_id = t.workspace_id AND a.deleted_at IS NULL
            LEFT JOIN monitored_systems ms ON ms.target_id = t.id AND ms.workspace_id = t.workspace_id
            WHERE t.workspace_id = %s AND t.deleted_at IS NULL
            ORDER BY t.created_at DESC
            ''',
            (workspace_context['workspace_id'],),
        ).fetchall()
        targets: list[dict[str, Any]] = []
        for row in rows:
            item = _json_safe_value(dict(row))
            health_status, health_reason = _target_health_payload(
                enabled=bool(row.get('enabled')),
                monitoring_enabled=bool(row.get('monitoring_enabled')),
                asset_id=str(row.get('asset_id') or '') or None,
                asset_exists=bool(row.get('resolved_asset_id')),
                monitored_system_id=str(row.get('monitored_system_id') or '') or None,
            )
            item['asset_missing'] = not bool(row.get('resolved_asset_id'))
            item['health_status'] = health_status
            item['health_reason'] = health_reason
            targets.append(item)
        return {'targets': targets, 'workspace': workspace_context['workspace']}


def patch_monitoring_target(target_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_permission(connection, request, 'monitoring.configure')
        row = connection.execute(
            '''
            SELECT id, asset_id, enabled, monitoring_enabled, monitoring_mode, monitoring_interval_seconds, severity_threshold,
                   auto_create_alerts, auto_create_incidents, notification_channels, is_active
            FROM targets
            WHERE id = %s AND workspace_id = %s AND deleted_at IS NULL
            ''',
            (target_id, workspace_context['workspace_id']),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Target not found.')
        current = dict(row)
        mode = str(payload.get('monitoring_mode') if 'monitoring_mode' in payload else current.get('monitoring_mode') or 'poll').strip().lower()
        if mode not in {'poll', 'stream'}:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='monitoring_mode must be poll or stream.')
        threshold = str(payload.get('severity_threshold') if 'severity_threshold' in payload else current.get('severity_threshold') or 'medium').strip().lower()
        if threshold not in {'low', 'medium', 'high', 'critical'}:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='severity_threshold must be low/medium/high/critical.')
        channels = payload.get('notification_channels') if 'notification_channels' in payload else current.get('notification_channels')
        channels = channels if isinstance(channels, list) else []
        if any(key in payload for key in ('monitoring_demo_scenario', 'monitoring_profile', 'monitoring_scenario')):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='monitoring_demo_scenario is deprecated and cannot be patched.')
        monitoring_enabled = bool(payload.get('monitoring_enabled')) if 'monitoring_enabled' in payload else bool(current.get('monitoring_enabled'))
        interval_seconds_raw = payload.get('monitoring_interval_seconds') if 'monitoring_interval_seconds' in payload else current.get('monitoring_interval_seconds')
        interval_seconds = max(30, int(interval_seconds_raw or 300))
        auto_create_alerts = bool(payload.get('auto_create_alerts')) if 'auto_create_alerts' in payload else bool(current.get('auto_create_alerts', True))
        auto_create_incidents = bool(payload.get('auto_create_incidents')) if 'auto_create_incidents' in payload else bool(current.get('auto_create_incidents', False))
        is_active = bool(payload.get('is_active')) if 'is_active' in payload else bool(current.get('is_active', True))
        connection.execute(
            '''
            UPDATE targets
            SET monitoring_enabled = %s,
                monitoring_mode = %s,
                monitoring_interval_seconds = %s,
                severity_threshold = %s,
                auto_create_alerts = %s,
                auto_create_incidents = %s,
                notification_channels = %s::jsonb,
                monitored_by_workspace_id = %s,
                is_active = %s,
                updated_by_user_id = %s,
                updated_at = NOW()
            WHERE id = %s
            ''',
            (
                monitoring_enabled,
                mode,
                interval_seconds,
                threshold,
                auto_create_alerts,
                auto_create_incidents,
                _json_dumps(channels),
                workspace_context['workspace_id'],
                is_active,
                user['id'],
                target_id,
            ),
        )
        if monitoring_enabled and bool(current.get('enabled')):
            result = ensure_monitored_system_for_target(connection, target_id=target_id, workspace_id=workspace_context['workspace_id'])
            if result.get('status') != 'ok':
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Cannot enable monitoring: linked asset is missing or deleted.')
        elif not monitoring_enabled:
            connection.execute(
                "UPDATE monitored_systems SET is_enabled = FALSE, runtime_status = 'disabled', status = 'paused', freshness_status = 'unavailable', confidence_status = 'unavailable', coverage_reason = 'monitoring_disabled' WHERE target_id = %s::uuid AND workspace_id = %s::uuid",
                (target_id, workspace_context['workspace_id']),
            )
        logger.info('monitoring config persisted target=%s monitoring_enabled=%s threshold=%s', target_id, monitoring_enabled, threshold)
        log_audit(
            connection,
            action='target.monitoring.update',
            entity_type='target',
            entity_id=target_id,
            request=request,
            user_id=user['id'],
            workspace_id=workspace_context['workspace_id'],
            metadata={'monitoring_enabled': monitoring_enabled},
        )
        connection.commit()
        updated = connection.execute(
            '''
            SELECT id, workspace_id, name, target_type, chain_network, monitoring_enabled, monitoring_mode, monitoring_interval_seconds, severity_threshold,
                   auto_create_alerts, auto_create_incidents, notification_channels, monitored_by_workspace_id, is_active, last_checked_at, last_run_status,
                   last_run_id, last_alert_at, updated_at
            FROM targets
            WHERE id = %s
            ''',
            (target_id,),
        ).fetchone()
        return {'target': _json_safe_value(dict(updated))}


def run_monitoring_once(target_id: str, request: Request) -> dict[str, Any]:
    with pg_connection() as connection:
        # Same DB-safety contract as the worker cycle: the manual run also performs slow
        # RPC + threat-engine I/O inside process_monitoring_target, so run in autocommit to
        # avoid holding the connection idle-in-transaction across the scan.
        try:
            connection.autocommit = True
        except Exception:
            logger.warning('manual_run_once_autocommit_unavailable action=continue_default_isolation')
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        row = connection.execute(
            '''
            SELECT id, workspace_id, name, target_type, chain_network, contract_identifier, wallet_address, asset_type, owner_notes, severity_preference, enabled,
                   asset_id, chain_id, target_metadata, monitoring_enabled, monitoring_mode, monitoring_interval_seconds, severity_threshold, auto_create_alerts,
                   auto_create_incidents, notification_channels, last_checked_at, last_run_status, last_run_id, last_alert_at, monitored_by_workspace_id, is_active,
                   monitoring_checkpoint_at, monitoring_checkpoint_cursor, watcher_last_observed_block, watcher_checkpoint_lag_blocks, watcher_source_status,
                   watcher_degraded_reason, recent_evidence_state, recent_truthfulness_state, recent_real_event_count, updated_by_user_id, created_by_user_id, created_at
            FROM targets
            WHERE id = %s AND workspace_id = %s AND deleted_at IS NULL
            ''',
            (target_id, workspace_context['workspace_id']),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Target not found.')
        target = dict(row)
        run_id = str(uuid.uuid4())
        workspace_id = str(target['workspace_id'])
        connection.execute(
            '''
            INSERT INTO monitoring_runs (
                id,
                workspace_id,
                started_at,
                status,
                trigger_type,
                systems_checked_count,
                assets_checked_count,
                detections_created_count,
                alerts_created_count,
                telemetry_records_seen_count,
                notes
            )
            VALUES (%s::uuid, %s::uuid, NOW(), 'running', 'manual', 0, 0, 0, 0, 0, %s)
            ''',
            (run_id, workspace_id, f'target_id={target_id}'),
        )
        try:
            result = process_monitoring_target(connection, target, triggered_by_user_id=str(user['id']))
            events_ingested = int(result.get('events_ingested', 0))
            telemetry_records_seen = int(result.get('telemetry_records_seen', events_ingested))
            connection.execute(
                '''
                UPDATE monitoring_runs
                SET completed_at = NOW(),
                    status = 'completed',
                    systems_checked_count = 1,
                    assets_checked_count = %s,
                    detections_created_count = %s,
                    alerts_created_count = %s,
                    telemetry_records_seen_count = %s,
                    notes = %s
                WHERE id = %s::uuid
                  AND workspace_id = %s::uuid
                ''',
                (
                    1 if target.get('asset_id') else 0,
                    int(result.get('detections_created', 0)),
                    int(result.get('alerts_generated', 0)),
                    telemetry_records_seen,
                    f'target_id={target_id};status=completed',
                    run_id,
                    workspace_id,
                ),
            )
            connection.commit()
            return {**result, 'debug_only': True, 'enterprise_proof_eligible': False, 'reason_code': 'manual_run_once_debug_path'}
        except Exception as exc:
            connection.execute(
                '''
                UPDATE monitoring_runs
                SET completed_at = NOW(),
                    status = 'error',
                    notes = %s
                WHERE id = %s::uuid
                  AND workspace_id = %s::uuid
                ''',
                (f'target_id={target_id};error={exc}', run_id, workspace_id),
            )
            connection.commit()
            raise


def list_incidents(request: Request, *, status_value: str | None = None, severity: str | None = None, target_id: str | None = None) -> dict[str, Any]:
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        clauses = ['workspace_id = %s']
        params: list[Any] = [workspace_context['workspace_id']]
        if status_value:
            clauses.append('status = %s')
            params.append(status_value)
        if severity:
            clauses.append('severity = %s')
            params.append(severity)
        if target_id:
            clauses.append('target_id = %s')
            params.append(target_id)
        query = f"""
            SELECT id, workspace_id, target_id, analysis_run_id, event_type, title, severity, status, summary,
                   linked_alert_ids, owner_user_id, timeline, created_at, updated_at
            FROM incidents
            WHERE {' AND '.join(clauses)}
            ORDER BY created_at DESC
            LIMIT 200
        """
        rows = connection.execute(query, tuple(params)).fetchall()
        return {'incidents': [_json_safe_value(dict(row)) for row in rows]}


def patch_incident(incident_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        row = connection.execute('SELECT id FROM incidents WHERE id = %s AND workspace_id = %s', (incident_id, workspace_context['workspace_id'])).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Incident not found.')
        next_status = str(payload.get('status') or 'open').strip().lower()
        if next_status not in {'open', 'acknowledged', 'resolved'}:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='status must be open/acknowledged/resolved.')
        owner_user_id = payload.get('owner_user_id')
        timeline = payload.get('timeline') if isinstance(payload.get('timeline'), list) else None
        connection.execute(
            '''
            UPDATE incidents
            SET status = %s,
                owner_user_id = %s,
                timeline = COALESCE(%s::jsonb, timeline),
                updated_at = NOW()
            WHERE id = %s
            ''',
            (next_status, owner_user_id, _json_dumps(timeline) if timeline is not None else None, incident_id),
        )
        log_audit(connection, action='incident.update', entity_type='incident', entity_id=incident_id, request=request, user_id=user['id'], workspace_id=workspace_context['workspace_id'], metadata={'status': next_status})
        connection.commit()
        return {'id': incident_id, 'status': next_status}


def get_monitoring_health() -> dict[str, Any]:
    if not live_mode_enabled():
        runtime = monitoring_ingestion_runtime()
        degraded_reason = str(runtime.get('reason')) if runtime.get('degraded') else None
        background_loop_health = get_background_loop_health()
        return {
            **WORKER_STATE,
            'live_mode': False,
            'mode': runtime.get('mode'),
            'operational_mode': monitoring_operational_mode(runtime, degraded=bool(runtime.get('degraded')), degraded_reason=degraded_reason),
            'ingestion_mode': runtime.get('source'),
            'source_type': runtime.get('source'),
            'degraded': runtime.get('degraded'),
            'degraded_reason': degraded_reason,
            'background_loop_health': background_loop_health,
            'slo_compliance': evaluate_monitoring_slos({}, MonitoringSLOs.from_env()),
        }
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        configured_worker_name = WORKER_STATE['worker_name']
        row = connection.execute(
            '''
            SELECT worker_name, running, status, last_started_at, last_heartbeat_at, last_cycle_at, last_cycle_due_targets,
                   last_cycle_targets_checked, last_cycle_alerts_generated, last_error, updated_at
            FROM monitoring_worker_state
            WHERE worker_name = %s
            ''',
            (configured_worker_name,),
        ).fetchone()
        using_fallback_worker = False
        if row is None:
            row = connection.execute(
                '''
                SELECT worker_name, running, status, last_started_at, last_heartbeat_at, last_cycle_at, last_cycle_due_targets,
                       last_cycle_targets_checked, last_cycle_alerts_generated, last_error, updated_at
                FROM monitoring_worker_state
                ORDER BY COALESCE(last_heartbeat_at, last_cycle_at, updated_at) DESC
                LIMIT 1
                '''
            ).fetchone()
            using_fallback_worker = row is not None
        if row is None:
            return {**WORKER_STATE, 'live_mode': True}
        normalized = _json_safe_value(dict(row))
        normalized['configured_worker_name'] = configured_worker_name
        normalized['active_worker_name'] = normalized.get('worker_name')
        normalized['worker_name_mismatch'] = bool(
            configured_worker_name
            and normalized.get('worker_name')
            and configured_worker_name != normalized.get('worker_name')
        )
        normalized['worker_state_fallback_used'] = using_fallback_worker
        last_cycle_at = _parse_ts(normalized.get('last_cycle_at'))
        worker_running = bool(normalized.get('running'))
        if last_cycle_at is not None:
            worker_running = worker_running or (utc_now() - last_cycle_at) <= timedelta(seconds=max(30, WORKER_HEARTBEAT_TTL_SECONDS))
        normalized['worker_running'] = worker_running
        normalized['last_cycle_checked_targets'] = normalized.get('last_cycle_targets_checked', 0)
        normalized['last_cycle_alerts_created'] = normalized.get('last_cycle_alerts_generated', 0)
        overdue = connection.execute(
            '''
            SELECT COUNT(*) AS overdue_count
            FROM targets
            WHERE deleted_at IS NULL
              AND monitoring_enabled = TRUE
              AND enabled = TRUE
              AND is_active = TRUE
              AND last_checked_at IS NOT NULL
              AND last_checked_at < NOW() - (GREATEST(monitoring_interval_seconds, 30) * INTERVAL '1 second')
            '''
        ).fetchone()
        job_state = connection.execute(
            '''
            SELECT
                COUNT(*) FILTER (WHERE status = 'queued') AS queued,
                COUNT(*) FILTER (WHERE status = 'running') AS running,
                COUNT(*) FILTER (WHERE status = 'failed') AS failed
            FROM background_jobs
            '''
        ).fetchone()
        normalized['overdue_targets'] = int((overdue or {}).get('overdue_count') or 0)
        normalized['job_delivery_state'] = _json_safe_value(dict(job_state)) if job_state is not None else {'queued': 0, 'running': 0, 'failed': 0}
        runtime = monitoring_ingestion_runtime()
        normalized['ingestion_mode'] = runtime.get('source')
        normalized['degraded'] = runtime.get('degraded')
        watcher_state = connection.execute(
            '''
            SELECT watcher_name, source_status, degraded, degraded_reason, last_heartbeat_at, last_processed_block, metrics
            FROM monitoring_watcher_state
            ORDER BY COALESCE(last_heartbeat_at, updated_at) DESC
            LIMIT 1
            '''
        ).fetchone()
        checkpoint_stats = connection.execute(
            '''
            SELECT
                MAX(watcher_last_observed_block) AS latest_processed_block,
                MAX(watcher_checkpoint_lag_blocks) AS max_checkpoint_lag_blocks,
                MAX(monitoring_checkpoint_at) AS latest_checkpoint_at,
                COALESCE(SUM(CASE WHEN watcher_source_status = 'degraded' THEN 1 ELSE 0 END), 0) AS degraded_targets,
                COALESCE(SUM(CASE WHEN watcher_source_status = 'active' THEN 1 ELSE 0 END), 0) AS active_targets
            FROM targets
            WHERE deleted_at IS NULL AND monitoring_enabled = TRUE AND enabled = TRUE AND is_active = TRUE
            '''
        ).fetchone()
        last_15m_events = connection.execute(
            '''
            SELECT COUNT(*) AS event_count
            FROM monitoring_event_receipts
            WHERE processed_at >= NOW() - INTERVAL '15 minutes'
            '''
        ).fetchone()
        stats = _json_safe_value(dict(checkpoint_stats or {}))
        latest_checkpoint_at = _parse_ts(stats.get('latest_checkpoint_at'))
        heartbeat_at = _parse_ts(normalized.get('last_heartbeat_at') or normalized.get('last_cycle_at'))
        heartbeat_age_seconds = int((utc_now() - heartbeat_at).total_seconds()) if heartbeat_at else None
        heartbeat_stale = heartbeat_age_seconds is None or heartbeat_age_seconds > WORKER_HEARTBEAT_TTL_SECONDS
        normalized['source_type'] = runtime.get('source')
        normalized['latest_processed_block'] = stats.get('latest_processed_block')
        normalized['checkpoint_lag_blocks'] = stats.get('max_checkpoint_lag_blocks')
        normalized['checkpoint_age_seconds'] = int((utc_now() - latest_checkpoint_at).total_seconds()) if latest_checkpoint_at else None
        normalized['event_count_last_15m'] = int((last_15m_events or {}).get('event_count') or 0)
        normalized['heartbeat_age_seconds'] = heartbeat_age_seconds
        normalized['heartbeat_stale'] = heartbeat_stale
        # monitoring_watcher_state is written ONLY by the realtime WebSocket worker.
        # Always expose it separately as `realtime_watcher` so the runtime status can
        # render a distinct Realtime WebSocket / Provider realtime status. It is only
        # authoritative for the *live source* (source_type / latest block / degraded)
        # when realtime is actually enabled — a paused or rate-limited realtime worker
        # must NOT degrade the independent stable RPC polling source.
        _realtime_is_enabled = realtime_enabled()
        if watcher_state is not None:
            watcher = _json_safe_value(dict(watcher_state))
            normalized['watcher_state'] = watcher
            normalized['realtime_watcher'] = watcher
        else:
            normalized['realtime_watcher'] = None
        if watcher_state is not None and _realtime_is_enabled:
            watcher = normalized['realtime_watcher']
            normalized['source_type'] = watcher.get('source_status') or runtime.get('source')
            normalized['latest_processed_block'] = watcher.get('last_processed_block') or normalized.get('latest_processed_block')
            if watcher.get('degraded'):
                normalized['degraded'] = True
                normalized['degraded_reason'] = watcher.get('degraded_reason') or runtime.get('reason') or 'watcher_degraded'
            else:
                normalized['degraded_reason'] = runtime.get('reason') if runtime.get('degraded') else ('target_source_degraded' if int(stats.get('degraded_targets') or 0) > 0 else None)
        else:
            # Realtime disabled (or no watcher row): stable RPC polling owns the source
            # status. Do not let a stale realtime watcher row mark the source degraded.
            normalized['degraded_reason'] = runtime.get('reason') if runtime.get('degraded') else ('target_source_degraded' if int(stats.get('degraded_targets') or 0) > 0 else None)
        normalized['mode'] = runtime.get('mode')
        normalized['ingestion_live_confirmed'] = bool(
            runtime.get('mode') in {'live', 'hybrid'}
            and not bool(normalized.get('degraded'))
            and _provider_source_is_live(normalized.get('source_type') or runtime.get('source'))
        )
        normalized['operational_mode'] = monitoring_operational_mode(
            runtime,
            degraded=bool(normalized.get('degraded')) or bool(normalized.get('degraded_reason')),
            degraded_reason=normalized.get('degraded_reason'),
        )
        normalized['background_loop_health'] = get_background_loop_health()
        try:
            normalized['slo_compliance'] = monitoring_slo_snapshot(connection)
        except Exception as exc:
            logger.warning('monitoring_slo_snapshot_unavailable error_type=%s', type(exc).__name__)
            normalized['slo_compliance'] = {
                **evaluate_monitoring_slos({}, MonitoringSLOs.from_env()),
                'unavailable_reason': 'monitoring_reliability_schema_or_database_unavailable',
            }
        return {**normalized, 'live_mode': True}


def production_claim_validator() -> dict[str, Any]:
    runtime = monitoring_ingestion_runtime()
    evidence_window_seconds = max(60, int(os.getenv('MONITORING_EVIDENCE_WINDOW_SECONDS', '900')))
    checks: dict[str, bool] = {
        'live_or_hybrid_mode': runtime.get('mode') in {'live', 'hybrid'},
        'live_monitoring_enabled': str(os.getenv('LIVE_MONITORING_ENABLED', 'true')).strip().lower() in {'1', 'true', 'yes', 'on'},
        'evm_rpc_reachable': False,
        'watcher_source_active': False,
        'provider_reachable_or_backfilling': False,
        'checkpoints_advancing': False,
        'no_silent_demo_fallback': not bool(runtime.get('degraded')),
        'no_synthetic_evidence_window': False,
        'real_target_exists': False,
        'analysis_evidence_real': False,
        'recent_evidence_state_real': False,
        'no_recent_degraded_or_missing': False,
        'truthfulness_not_unknown': False,
        'recent_real_event_count_positive': False,
        'evidence_window_recent_real_events': False,
        'oracle_sources_configured': False,
        'no_fallback_or_synthetic_sources': False,
    }
    reason = None
    if checks['live_or_hybrid_mode'] and checks['live_monitoring_enabled'] and (os.getenv('EVM_RPC_URL') or '').strip():
        try:
            chain_id_hex = JsonRpcClient((os.getenv('EVM_RPC_URL') or '').strip()).call('eth_chainId', [])
            checks['evm_rpc_reachable'] = bool(chain_id_hex)
        except Exception as exc:
            reason = f'evm_rpc_unreachable:{exc.__class__.__name__}'
    checks['oracle_sources_configured'] = bool((os.getenv('ORACLE_SOURCE_URLS') or '').strip() or (os.getenv('ORACLE_API_URL') or '').strip())
    if live_mode_enabled():
        health = get_monitoring_health()
        checks['watcher_source_active'] = bool((health.get('source_type') in {'websocket', 'polling', 'rpc_backfill'}) and not health.get('degraded'))
        checks['provider_reachable_or_backfilling'] = bool(
            checks['evm_rpc_reachable'] or health.get('source_type') in {'rpc_backfill', 'polling', 'websocket'}
        )
        age = health.get('checkpoint_age_seconds')
        checks['checkpoints_advancing'] = isinstance(age, int) and age <= 900
        if health.get('degraded_reason'):
            reason = str(health.get('degraded_reason'))
    else:
        checks['provider_reachable_or_backfilling'] = checks['evm_rpc_reachable']
    last_real_event_at = None
    last_demo_event_at = None
    recent_evidence_state = 'missing'
    recent_truthfulness_state = 'unknown_risk'
    recent_real_event_count = 0
    recent_confidence_basis = 'none'
    recent_claim_safe_window_passed = False
    try:
        with pg_connection() as connection:
            ensure_pilot_schema(connection)
            target_row = connection.execute(
                f'''
                SELECT COUNT(*) AS total
                FROM targets
                WHERE deleted_at IS NULL
                  AND monitoring_enabled = TRUE
                  AND enabled = TRUE
                  AND is_active = TRUE
                  AND {monitorable_target_types_sql_clause('target_type')}
                '''
            ).fetchone()
            checks['real_target_exists'] = int((target_row or {}).get('total') or 0) > 0
            recent = connection.execute(
                '''
                SELECT created_at, response_payload
                FROM analysis_runs
                WHERE analysis_type LIKE 'monitoring_%'
                ORDER BY created_at DESC
                LIMIT 1
                '''
            ).fetchone()
            if recent is not None:
                payload = _json_safe_value(dict(recent)).get('response_payload') or {}
                meta = payload.get('metadata') if isinstance(payload, dict) else {}
                if isinstance(meta, dict):
                    recent_evidence_state = str(meta.get('evidence_state') or 'missing')
                    recent_confidence_basis = str(meta.get('confidence_basis') or 'none')
                    recent_truthfulness_state = str(meta.get('truthfulness_state') or 'unknown_risk')
                    checks['no_fallback_or_synthetic_sources'] = (
                        str(payload.get('source') or '').lower() not in {'fallback', 'demo', 'synthetic', 'degraded'}
                        and not bool(payload.get('degraded'))
                        and str(meta.get('ingestion_source') or '').lower() not in {'demo', 'synthetic', 'fallback'}
                    )
            evidence_rollup = connection.execute(
                f'''
                SELECT
                    COALESCE(SUM(CASE WHEN recent_evidence_state = 'real' THEN 1 ELSE 0 END), 0) AS real_evidence_targets,
                    COALESCE(SUM(CASE WHEN recent_evidence_state IN ('degraded', 'no_evidence', 'failed', 'missing') THEN 1 ELSE 0 END), 0) AS degraded_or_missing_targets,
                    COALESCE(SUM(CASE WHEN recent_truthfulness_state = 'unknown_risk' THEN 1 ELSE 0 END), 0) AS unknown_risk_targets,
                    COALESCE(SUM(COALESCE(recent_real_event_count, 0)), 0) AS real_event_count_total,
                    MAX(last_real_event_at) AS latest_real_event_at
                FROM targets
                WHERE deleted_at IS NULL
                  AND monitoring_enabled = TRUE
                  AND enabled = TRUE
                  AND is_active = TRUE
                  AND {monitorable_target_types_sql_clause('target_type')}
                '''
            ).fetchone()
            evidence_stats = _json_safe_value(dict(evidence_rollup or {}))
            recent_real_event_count = int(evidence_stats.get('real_event_count_total') or 0)
            if evidence_stats.get('latest_real_event_at'):
                last_real_event_at = evidence_stats.get('latest_real_event_at')
            unknown_risk_detected = int(evidence_stats.get('unknown_risk_targets') or 0) > 0
            no_evidence_detected = int(evidence_stats.get('degraded_or_missing_targets') or 0) > 0
            degraded_window_detected = no_evidence_detected
            checks['truthfulness_not_unknown'] = not unknown_risk_detected and recent_truthfulness_state != 'unknown_risk'
            checks['recent_real_event_count_positive'] = recent_real_event_count > 0
            last_real = connection.execute(
                '''
                SELECT MAX(processed_at) AS ts
                FROM monitoring_event_receipts
                WHERE ingestion_source <> 'demo'
                '''
            ).fetchone()
            last_demo = connection.execute(
                '''
                SELECT MAX(processed_at) AS ts
                FROM monitoring_event_receipts
                WHERE ingestion_source = 'demo'
                '''
            ).fetchone()
            last_real_event_at = _json_safe_value(dict(last_real or {})).get('ts')
            last_demo_event_at = _json_safe_value(dict(last_demo or {})).get('ts')
    except Exception:
        checks['real_target_exists'] = False
    parsed_last_real = _parse_ts(last_real_event_at)
    evidence_window_passed = bool(parsed_last_real and int((utc_now() - parsed_last_real).total_seconds()) <= evidence_window_seconds)
    checks['evidence_window_recent_real_events'] = evidence_window_passed
    synthetic_leak_detected = last_demo_event_at is not None
    checks['no_synthetic_evidence_window'] = not synthetic_leak_detected
    checks['analysis_evidence_real'] = recent_evidence_state == 'real' and recent_confidence_basis in {'provider_evidence', 'backfill_evidence'}
    checks['recent_evidence_state_real'] = recent_evidence_state == 'real'
    checks['no_recent_degraded_or_missing'] = recent_evidence_state == 'real' and checks['recent_real_event_count_positive']
    recent_claim_safe_window_passed = (
        checks['analysis_evidence_real']
        and checks['recent_evidence_state_real']
        and checks['recent_real_event_count_positive']
        and checks['truthfulness_not_unknown']
        and checks['evidence_window_recent_real_events']
        and checks['no_synthetic_evidence_window']
        and checks['no_recent_degraded_or_missing']
        and checks['oracle_sources_configured']
        and checks['no_fallback_or_synthetic_sources']
    )
    passed = all(checks.values())
    failed_check_codes = [name for name, ok in checks.items() if not ok]
    if 'unknown_risk_detected' not in locals():
        unknown_risk_detected = recent_truthfulness_state == 'unknown_risk'
    if 'no_evidence_detected' not in locals():
        no_evidence_detected = recent_evidence_state in {'missing', 'no_evidence', 'degraded', 'failed'}
    if 'degraded_window_detected' not in locals():
        degraded_window_detected = recent_evidence_state in {'degraded', 'failed'}
    return {
        'status': 'PASS' if passed else 'FAIL',
        'sales_claims_allowed': passed,
        'checked_at': utc_now().isoformat(),
        'mode': runtime.get('mode'),
        'operational_mode': monitoring_operational_mode(runtime, degraded=bool(runtime.get('degraded')), degraded_reason=reason),
        'source_type': runtime.get('source'),
        'checks': checks,
        'reason': reason,
        'synthetic_leak_detected': synthetic_leak_detected,
        'last_real_event_at': last_real_event_at,
        'last_demo_event_at': last_demo_event_at,
        'recent_evidence_state': recent_evidence_state,
        'recent_truthfulness_state': recent_truthfulness_state,
        'recent_real_event_count': recent_real_event_count,
        'recent_confidence_basis': recent_confidence_basis,
        'recent_claim_safe_window_passed': recent_claim_safe_window_passed,
        'evidence_window_passed': checks['evidence_window_recent_real_events'],
        'evidence_window_seconds': evidence_window_seconds,
        'unknown_risk_detected': unknown_risk_detected,
        'no_evidence_detected': no_evidence_detected,
        'degraded_window_detected': degraded_window_detected,
        'reason_codes': failed_check_codes,
    }


def monitoring_runtime_status(request: Request | None = None) -> dict[str, Any]:
    canonical_runtime_truth_enabled = is_canonical_runtime_truth_enabled()
    last_query_checkpoint = 'not_started'
    resolved_workspace_id: str | None = None
    resolved_workspace_slug: str | None = None
    cache_key: str | None = None

    if request is not None:
        try:
            header_workspace_id = str(request.headers.get('x-workspace-id') or '').strip()
            header_workspace_slug = str(request.headers.get('x-workspace-slug') or '').strip()
            cache_lookup_keys = []
            if header_workspace_id:
                cache_lookup_keys.append(f'workspace:{header_workspace_id}')
            if header_workspace_slug:
                cache_lookup_keys.append(f'workspace_slug:{header_workspace_slug}')
            for candidate_key in cache_lookup_keys:
                cached = RUNTIME_STATUS_WORKSPACE_CACHE.get(candidate_key)
                if not cached:
                    continue
                cached_at, cached_payload = cached
                if (perf_counter() - cached_at) <= RUNTIME_STATUS_CACHE_TTL_SECONDS:
                    cache_key = candidate_key
                    return dict(cached_payload)
            if header_workspace_id:
                cache_key = f'workspace:{header_workspace_id}'
            elif header_workspace_slug:
                cache_key = f'workspace_slug:{header_workspace_slug}'
        except Exception:
            cache_key = None

    def _persist_workspace_context(
        req: Request | None,
        *,
        workspace_id: str | None,
        workspace_slug: str | None,
    ) -> None:
        if req is None:
            return
        try:
            req.state.workspace_id = workspace_id
            req.state.workspace_slug = workspace_slug
        except Exception:
            return

    def _workspace_context_from_request(req: Request | None) -> tuple[str | None, str | None]:
        nonlocal resolved_workspace_id
        nonlocal resolved_workspace_slug
        if req is None:
            return resolved_workspace_id, resolved_workspace_slug
        try:
            workspace_id_value = getattr(req.state, 'workspace_id', None)
            workspace_slug_value = getattr(req.state, 'workspace_slug', None)
        except Exception:
            workspace_id_value = None
            workspace_slug_value = None
        if workspace_id_value is None:
            try:
                workspace_id_value = req.headers.get('x-workspace-id')
            except Exception:
                workspace_id_value = None
        if workspace_slug_value is None:
            try:
                workspace_slug_value = req.headers.get('x-workspace-slug')
            except Exception:
                workspace_slug_value = None
        workspace_id_str = str(workspace_id_value).strip() if workspace_id_value is not None else ''
        workspace_slug_str = str(workspace_slug_value).strip() if workspace_slug_value is not None else ''
        workspace_id = workspace_id_str or resolved_workspace_id
        workspace_slug = workspace_slug_str or resolved_workspace_slug
        resolved_workspace_id = workspace_id
        resolved_workspace_slug = workspace_slug
        _persist_workspace_context(req, workspace_id=workspace_id, workspace_slug=workspace_slug)
        return workspace_id, workspace_slug

    def _safe_checkpoint_reason_token(checkpoint_label: str | None) -> str:
        checkpoint = str(checkpoint_label or '').strip().lower()
        if checkpoint in {'', 'none', 'not_started'}:
            checkpoint = 'init'
        checkpoint = checkpoint.replace('token', 'redacted')
        return f'checkpoint_{checkpoint}'

    def _base_runtime_failure_payload(
        *,
        workspace_id: str | None = None,
        workspace_slug: str | None = None,
        db_persistence_available: bool = True,
        db_persistence_reason: str | None = None,
        db_failure_classification: str | None = None,
        db_failure_reason: str | None = None,
    ) -> dict[str, Any]:
        field_reason_codes = {
            'protected_assets': ['query_failure'],
            'configured_systems': ['query_failure'],
            'reporting_systems': ['query_failure'],
            'last_poll_at': ['query_failure'],
            'last_heartbeat_at': ['query_failure'],
            'last_telemetry_at': ['query_failure'],
        }
        return {
            'workspace_id': workspace_id,
            'workspace_slug': workspace_slug,
            'workspace_configured': False,
            'configuration_reason': 'runtime_status_unavailable',
            'configuration_reason_codes': ['runtime_status_unavailable'],
            'status_reason': 'runtime_status_error',
            'runtime_error_code': 'runtime_status_unavailable',
            'runtime_degraded_reason': 'summary_unavailable',
            'valid_protected_assets': 0,
            'linked_monitored_systems': 0,
            'enabled_configs': 0,
            'valid_link_count': 0,
            'raw_enabled_targets': 0,
            'monitorable_enabled_targets': 0,
            'valid_asset_linked_targets': 0,
            'enabled_monitored_systems': 0,
            'valid_target_system_links': 0,
            'count_reason_codes': {
                'raw_enabled_targets': 'runtime_status_unavailable',
                'monitorable_enabled_targets': 'runtime_status_unavailable',
                'valid_asset_linked_targets': 'runtime_status_unavailable',
                'enabled_monitored_systems': 'runtime_status_unavailable',
                'valid_target_system_links': 'runtime_status_unavailable',
            },
            'configured_systems': 0,
            'reporting_systems': 0,
            'last_poll_at': None,
            'last_heartbeat_at': None,
            'last_coverage_telemetry_at': None,
            'last_telemetry_at': None,
            'coverage_receipts_last_at': None,
            'coverage_receipts_workspace_count': 0,
            'stale_heartbeat': True,
            'provider_degraded_flag': True,
            'proof_chain_status': 'unavailable',
            'proof_chain_correlation_id': None,
            'evidence_source': 'none',
            'confidence_status': 'degraded',
            'runtime_status_summary': 'offline',
            'monitoring_status': 'offline',
            'status': 'Offline',
            'workspace_monitoring_summary': build_workspace_monitoring_summary_fallback(
                status_reason='runtime_status_error',
                workspace_configured=False,
                runtime_status='offline',
                monitoring_status='offline',
                telemetry_freshness='unavailable',
                confidence='unavailable',
            ),
            'contradiction_flags': [],
            'configuration_diagnostics': {
                'valid_protected_assets': 0,
                'linked_monitored_systems': 0,
                'enabled_configs': 0,
                'valid_link_count': 0,
                'workspace_configured': False,
                'configuration_reason': 'runtime_status_unavailable',
                'reason_codes': ['runtime_status_unavailable'],
            },
            'field_reason_codes': field_reason_codes,
            'db_persistence_available': bool(db_persistence_available),
            'db_persistence_reason': db_persistence_reason,
            'db_failure_classification': db_failure_classification,
            'db_failure_reason': db_failure_reason,
        }

    def _runtime_failure_payload(
        *,
        workspace_id: str | None,
        workspace_slug: str | None,
        error_code: str,
        error_type: str,
        error_message: str,
        error_stage: str,
        error_stage_detail: str | None = None,
        error_reason_tokens: list[str] | None = None,
        status_reason: str,
        hint: str,
        db_persistence_available: bool = True,
        db_persistence_reason: str | None = None,
        db_failure_classification: str | None = None,
        db_failure_reason: str | None = None,
    ) -> dict[str, Any]:
        effective_workspace_id = workspace_id or resolved_workspace_id
        effective_workspace_slug = workspace_slug or resolved_workspace_slug
        if effective_workspace_id is None and effective_workspace_slug is None:
            request_workspace_id, request_workspace_slug = _workspace_context_from_request(request)
            effective_workspace_id = request_workspace_id or effective_workspace_id
            effective_workspace_slug = request_workspace_slug or effective_workspace_slug
        payload = _base_runtime_failure_payload(
            workspace_id=effective_workspace_id,
            workspace_slug=effective_workspace_slug,
            db_persistence_available=db_persistence_available,
            db_persistence_reason=db_persistence_reason,
            db_failure_classification=db_failure_classification,
            db_failure_reason=db_failure_reason,
        )
        payload['status_reason'] = status_reason
        payload['runtime_error_code'] = error_code
        payload['runtime_degraded_reason'] = 'summary_unavailable'
        payload['configuration_reason'] = 'runtime_status_unavailable'
        payload['configuration_reason_codes'] = ['runtime_status_unavailable']
        payload['error'] = {
            'code': error_code,
            'type': error_type,
            'message': error_message,
            'stage': error_stage,
            'stage_detail': error_stage_detail,
            'reason_tokens': [str(token) for token in (error_reason_tokens or []) if str(token).strip()],
            'hint': hint,
        }
        summary = dict(payload['workspace_monitoring_summary'])
        summary.update(
            {
                'status_reason': status_reason,
                'configuration_reason': payload['configuration_reason'],
                'configuration_reason_codes': list(payload['configuration_reason_codes']),
                'runtime_status_summary': 'offline',
                'evidence_source': payload['evidence_source'],
                'confidence_status': payload['confidence_status'],
                'configuration_diagnostics': {
                    'valid_protected_assets': 0,
                    'linked_monitored_systems': 0,
                    'enabled_configs': 0,
                    'valid_link_count': 0,
                    'workspace_configured': False,
                    'configuration_reason': payload['configuration_reason'],
                    'reason_codes': list(payload['configuration_reason_codes']),
                },
                'runtime_error_code': payload.get('runtime_error_code'),
                'runtime_degraded_reason': payload.get('runtime_degraded_reason'),
                'field_reason_codes': dict(payload.get('field_reason_codes') or {}),
            }
        )
        summary.setdefault(
            'coverage_state',
            {
                'configured_systems': 0,
                'monitored_systems_count': 0,
                'reporting_systems': 0,
                'reporting_systems_count': 0,
                'protected_assets_count': 0,
                'telemetry_freshness': str(summary.get('telemetry_freshness') or 'unavailable'),
                'confidence': str(summary.get('confidence') or 'unavailable'),
                'evidence_source_summary': str(summary.get('evidence_source_summary') or 'none'),
            },
        )
        summary.setdefault('linked_monitored_system_count', 0)
        if not bool(payload.get('db_persistence_available', True)):
            summary['runtime_status'] = 'degraded'
            summary['monitoring_status'] = 'limited'
            summary['telemetry_freshness'] = 'unavailable'
            summary['confidence'] = 'unavailable'
            summary['status_reason'] = str(payload.get('db_persistence_reason') or 'Monitoring persistence unavailable')
            summary['db_failure_classification'] = payload.get('db_failure_classification')
            summary['db_failure_reason'] = payload.get('db_failure_reason') or payload.get('db_persistence_reason')
        payload['workspace_monitoring_summary'] = summary
        payload['configuration_diagnostics'] = dict(summary.get('configuration_diagnostics') or {})
        payload.update(summary)
        return payload

    def _runtime_schema_failure_payload(
        *,
        workspace_id: str | None,
        workspace_slug: str | None,
        missing_column: str,
        error_details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = _runtime_failure_payload(
            workspace_id=workspace_id,
            workspace_slug=workspace_slug,
            error_code='runtime_schema_incomplete',
            error_type='RuntimeSchemaIncomplete',
            error_message='Monitoring runtime schema is missing required columns.',
            error_stage='schema',
            status_reason=f'runtime_schema_column_missing:{missing_column}',
            hint='run_migrations_0036_to_0039',
        )
        payload['configuration_reason'] = 'runtime_schema_incomplete'
        payload['configuration_reason_codes'] = ['runtime_schema_incomplete']
        payload['status_reason'] = f'runtime_schema_column_missing:{missing_column}'
        details = dict(error_details or {})
        details.setdefault('code', 'runtime_schema_incomplete')
        details.setdefault('missing_column', missing_column)
        payload['error'] = details
        summary = dict(payload.get('workspace_monitoring_summary') or {})
        summary['configuration_reason'] = payload['configuration_reason']
        summary['configuration_reason_codes'] = list(payload['configuration_reason_codes'])
        summary['status_reason'] = payload['status_reason']
        payload['workspace_monitoring_summary'] = summary
        payload.update(summary)
        return payload

    def _monitoring_runtime_status_impl() -> dict[str, Any]:
        nonlocal last_query_checkpoint
        nonlocal resolved_workspace_id
        nonlocal resolved_workspace_slug
        checkpoint_started_at = perf_counter()
        checkpoint_durations_ms: dict[str, float] = {}
        completed_checkpoints: list[str] = []

        def _mark_query_checkpoint(label: str) -> None:
            nonlocal last_query_checkpoint
            nonlocal checkpoint_started_at
            previous_checkpoint = last_query_checkpoint
            now_counter = perf_counter()
            if previous_checkpoint and previous_checkpoint != 'not_started':
                elapsed_ms = max(0.0, (now_counter - checkpoint_started_at) * 1000.0)
                checkpoint_durations_ms[previous_checkpoint] = checkpoint_durations_ms.get(previous_checkpoint, 0.0) + elapsed_ms
                completed_checkpoints.append(previous_checkpoint)
                RUNTIME_STATUS_QUERY_PROFILE_HISTORY[previous_checkpoint].append(elapsed_ms)
            last_query_checkpoint = label
            checkpoint_started_at = now_counter

        health = get_monitoring_health()
        now = utc_now()
        claim_validator = production_claim_validator()
        if not live_mode_enabled():
            recent_evidence_state = str(claim_validator.get('recent_evidence_state') or 'missing')
            recent_truthfulness_state = str(claim_validator.get('recent_truthfulness_state') or 'unknown_risk')
            recent_real_event_count = int(claim_validator.get('recent_real_event_count') or 0)
            provider_health = 'healthy' if recent_evidence_state == 'real' and recent_real_event_count > 0 else 'degraded'
            mode = str(health.get('operational_mode') or health.get('mode') or 'DEGRADED').upper()
            if mode == 'LIVE' and recent_real_event_count <= 0:
                mode = 'DEGRADED'
            summary = build_workspace_monitoring_summary(
                now=now,
                workspace_configured=False,
                configuration_reason_codes=['no_persisted_enabled_monitoring_config'],
                query_failure_detected=False,
                schema_drift_detected=False,
                missing_telemetry_only=False,
                monitoring_mode='simulator' if str(health.get('ingestion_mode') or '') == 'demo' else 'offline',
                runtime_status='offline',
                configured_systems=0,
                monitored_systems_count=0,
                reporting_systems=0,
                protected_assets=0,
                last_poll_at=_parse_ts(_json_safe_value(health).get('last_cycle_at')),
                last_heartbeat_at=None,
                last_telemetry_at=None,
                last_coverage_telemetry_at=None,
                telemetry_kind=None,
                last_detection_at=None,
                evidence_source='simulator' if str(health.get('ingestion_mode') or '') == 'demo' else 'none',
                status_reason='live_mode_disabled',
                configuration_reason='no_persisted_enabled_monitoring_config',
                valid_protected_asset_count=0,
                linked_monitored_system_count=0,
                persisted_enabled_config_count=0,
                valid_target_system_link_count=0,
                telemetry_window_seconds=max(300, MONITOR_POLL_INTERVAL_SECONDS * 6),
            )
            summary_reason_codes = list((summary.get('configuration_diagnostics') or {}).get('reason_codes') or [])
            if not summary_reason_codes and summary.get('configuration_reason'):
                summary_reason_codes = [str(summary.get('configuration_reason'))]
            summary['configuration_reason_codes'] = summary_reason_codes
            payload = {
                'workspace_id': None,
                'workspace_slug': None,
                'monitoring_status': 'offline',
                'status': 'Offline',
                'mode': mode,
                'provider_health': provider_health,
                'provider_reachable': bool((claim_validator.get('checks') or {}).get('evm_rpc_reachable')),
                'recent_evidence_state': recent_evidence_state,
                'evidence_state': recent_evidence_state,
                'truthfulness_state': recent_truthfulness_state,
                'claim_safe': bool(claim_validator.get('sales_claims_allowed')),
                'recent_real_event_count': recent_real_event_count,
                'last_real_event_at': claim_validator.get('last_real_event_at'),
                'sales_claims_allowed': bool(claim_validator.get('sales_claims_allowed')),
                'claim_validator_status': str(claim_validator.get('status') or 'FAIL'),
                'source_of_evidence': 'simulator' if str(health.get('ingestion_mode') or '') == 'demo' else 'replay_or_none',
                'workspace_configured': False,
                'raw_enabled_targets': 0,
                'monitorable_enabled_targets': 0,
                'valid_asset_linked_targets': 0,
                'enabled_monitored_systems': 0,
                'valid_target_system_links': 0,
                'count_reason_codes': {
                    'raw_enabled_targets': 'live_mode_disabled',
                    'monitorable_enabled_targets': 'live_mode_disabled',
                    'valid_asset_linked_targets': 'live_mode_disabled',
                    'enabled_monitored_systems': 'live_mode_disabled',
                    'valid_target_system_links': 'live_mode_disabled',
                },
                'configuration_reason': summary.get('configuration_reason'),
                'configuration_reason_codes': list(summary.get('configuration_reason_codes') or []),
                'proof_chain_status': 'unavailable',
                'proof_chain_correlation_id': None,
                'contradiction_flags': list(summary.get('contradiction_flags') or []),
                'workspace_monitoring_summary': summary,
                'canonical_runtime_truth_enabled': bool(canonical_runtime_truth_enabled),
            }
            enterprise_ready_gate = _evaluate_enterprise_ready_gate(
                continuity_slo_pass=bool(summary.get('continuity_slo_pass') is True),
                telemetry_freshness=summary.get('telemetry_freshness'),
                ingestion_freshness=summary.get('ingestion_freshness'),
                detection_pipeline_freshness=summary.get('detection_pipeline_freshness'),
                proof_chain_status=summary.get('proof_chain_status'),
                runtime_status=summary.get('runtime_status'),
                monitoring_status=summary.get('monitoring_status'),
                reporting_systems_count=int(summary.get('reporting_systems_count') or 0),
                monitored_systems_count=int(summary.get('monitored_systems_count') or 0),
                contradiction_flags=list(summary.get('contradiction_flags') or []),
                guard_flags=list(summary.get('guard_flags') or []),
            )
            payload.update(enterprise_ready_gate)
            payload.update(payload['workspace_monitoring_summary'])
            return _normalize_monitoring_runtime_contract(payload)
        workspace_id: str | None = None
        workspace_slug: str | None = None
        user_id: str | None = None
        workspace_header_present = False
        monitored_rows: list[dict[str, Any]] = []
        listed_monitored_rows: list[dict[str, Any]] = []
        latest_detection_evaluation_at = None
        latest_detection_at = None
        latest_detection_payload: dict[str, Any] | None = None
        healthy_enabled_targets_count = 0
        healthy_enabled_assets_count = 0
        verified_assets_count = 0
        detections_count = 0
        alerts_count = 0
        incidents_count = 0
        response_actions_count = 0
        evidence_count = 0
        enabled_monitored_rows_count = 0
        healthy_enabled_target_ids: set[str] = set()
        healthy_enabled_target_asset_map: dict[str, str] = {}
        telemetry_window_seconds = max(300, MONITOR_POLL_INTERVAL_SECONDS * 6)
        live_coverage_receipts_by_system: dict[str, datetime] = {}
        live_coverage_receipts_workspace_latest: datetime | None = None
        live_coverage_receipts_persisted_count = 0
        query_failure_detected = False
        schema_drift_detected = False
        db_persistence_available = True
        db_persistence_reason: str | None = None
        runtime_error_code: str | None = None
        runtime_degraded_reason: str | None = None
        field_reason_codes: dict[str, list[str]] = {}

        def _append_field_reason(field_key: str, reason_code: str) -> None:
            normalized_field = str(field_key or '').strip()
            normalized_reason = str(reason_code or '').strip()
            if not normalized_field or not normalized_reason:
                return
            existing = field_reason_codes.setdefault(normalized_field, [])
            if normalized_reason not in existing:
                existing.append(normalized_reason)

        def _is_optional_schema_error(exc: Exception) -> bool:
            if isinstance(exc, (psycopg_errors.UndefinedTable, psycopg_errors.UndefinedColumn)):
                return True
            message = str(exc).lower()
            return 'does not exist' in message and ('column' in message or 'relation' in message or 'table' in message)

        def _record_optional_query_failure(
            *,
            exc: Exception,
            checkpoint_label: str,
            impacted_fields: list[str],
            reason_code: str,
            error_code: str,
        ) -> None:
            nonlocal query_failure_detected
            nonlocal schema_drift_detected
            nonlocal runtime_error_code
            nonlocal runtime_degraded_reason
            query_failure_detected = True
            if _is_optional_schema_error(exc):
                schema_drift_detected = True
            runtime_error_code = error_code
            runtime_degraded_reason = 'partial_query_failure'
            for field in impacted_fields:
                _append_field_reason(field, reason_code)
            logger.warning(
                'monitoring_runtime_status_optional_query_failed workspace_id=%s checkpoint=%s reason_code=%s error_type=%s error_detail=%s',
                workspace_id,
                checkpoint_label,
                reason_code,
                type(exc).__name__,
                str(exc)[:500],
            )
    
        def _load_runtime_monitored_rows(connection: Any, workspace_scope_id: str | None) -> list[dict[str, Any]]:
            if workspace_scope_id:
                rows = list_workspace_monitored_system_rows(connection, workspace_scope_id)
                normalized: list[dict[str, Any]] = []
                for row in rows:
                    item = dict(row)
                    item['is_enabled'] = monitored_system_row_enabled(item)
                    normalized.append(item)
                return normalized
            try:
                rows = connection.execute(
                    '''
                    SELECT ms.id, ms.workspace_id, ms.asset_id, ms.target_id, ms.chain, COALESCE(ms.is_enabled, TRUE) AS is_enabled, ms.runtime_status, ms.status, ms.last_heartbeat, ms.last_event_at, ms.last_coverage_telemetry_at, ms.freshness_status, ms.confidence_status, ms.coverage_reason, ms.last_error_text,
                           COALESCE(t.monitoring_interval_seconds, 30) AS monitoring_interval_seconds, ms.created_at, t.target_type
                    FROM monitored_systems ms
                    LEFT JOIN targets t
                      ON t.id = ms.target_id
                     AND t.workspace_id = ms.workspace_id
                    ORDER BY ms.created_at DESC
                    '''
                ).fetchall()
            except Exception as exc:
                error_text = str(exc).lower()
                optional_columns = (
                    'is_enabled',
                    'runtime_status',
                    'status',
                    'last_coverage_telemetry_at',
                    'freshness_status',
                    'confidence_status',
                    'coverage_reason',
                    'last_error_text',
                )
                if not (
                    'does not exist' in error_text
                    and 'column' in error_text
                    and any(column in error_text for column in optional_columns)
                ):
                    raise
                logger.warning(
                    'monitoring_runtime_status_legacy_schema_fallback checkpoint=load_runtime_monitored_rows workspace_id=%s error_type=%s',
                    workspace_scope_id,
                    type(exc).__name__,
                )
                rows = connection.execute(
                    '''
                    SELECT ms.id, ms.workspace_id, ms.asset_id, ms.target_id, ms.chain, TRUE AS is_enabled, NULL::text AS runtime_status, NULL::text AS status, ms.last_heartbeat, ms.last_event_at, NULL::timestamptz AS last_coverage_telemetry_at, NULL::text AS freshness_status, NULL::text AS confidence_status, NULL::text AS coverage_reason, NULL::text AS last_error_text,
                           COALESCE(t.monitoring_interval_seconds, 30) AS monitoring_interval_seconds, ms.created_at, t.target_type
                    FROM monitored_systems ms
                    LEFT JOIN targets t
                      ON t.id = ms.target_id
                     AND t.workspace_id = ms.workspace_id
                    ORDER BY ms.created_at DESC
                    '''
                ).fetchall()
            normalized: list[dict[str, Any]] = []
            for row in rows:
                item = dict(row)
                item['is_enabled'] = monitored_system_row_enabled(item)
                normalized.append(item)
            return normalized
    
        def _load_workspace_monitored_rows_raw(connection: Any, workspace_scope_id: str) -> list[dict[str, Any]]:
            try:
                rows = connection.execute(
                    '''
                    SELECT ms.id, ms.workspace_id, ms.asset_id, ms.target_id, ms.chain, COALESCE(ms.is_enabled, TRUE) AS is_enabled, ms.runtime_status, ms.status, ms.last_heartbeat, ms.last_event_at, ms.last_coverage_telemetry_at, ms.freshness_status, ms.confidence_status, ms.coverage_reason, ms.last_error_text,
                           COALESCE(t.monitoring_interval_seconds, 30) AS monitoring_interval_seconds, ms.created_at, t.target_type
                    FROM monitored_systems ms
                    LEFT JOIN targets t
                      ON t.id = ms.target_id
                     AND t.workspace_id = ms.workspace_id
                    WHERE ms.workspace_id = %s
                    ORDER BY ms.created_at DESC
                    ''',
                    (workspace_scope_id,),
                ).fetchall()
            except Exception as exc:
                error_text = str(exc).lower()
                optional_columns = (
                    'is_enabled',
                    'runtime_status',
                    'status',
                    'last_coverage_telemetry_at',
                    'freshness_status',
                    'confidence_status',
                    'coverage_reason',
                    'last_error_text',
                )
                if not (
                    'does not exist' in error_text
                    and 'column' in error_text
                    and any(column in error_text for column in optional_columns)
                ):
                    raise
                logger.warning(
                    'monitoring_runtime_status_legacy_schema_fallback checkpoint=load_workspace_monitored_rows_raw workspace_id=%s error_type=%s',
                    workspace_scope_id,
                    type(exc).__name__,
                )
                rows = connection.execute(
                    '''
                    SELECT ms.id, ms.workspace_id, ms.asset_id, ms.target_id, ms.chain, TRUE AS is_enabled, NULL::text AS runtime_status, NULL::text AS status, ms.last_heartbeat, ms.last_event_at, NULL::timestamptz AS last_coverage_telemetry_at, NULL::text AS freshness_status, NULL::text AS confidence_status, NULL::text AS coverage_reason, NULL::text AS last_error_text,
                           COALESCE(t.monitoring_interval_seconds, 30) AS monitoring_interval_seconds, ms.created_at, t.target_type
                    FROM monitored_systems ms
                    LEFT JOIN targets t
                      ON t.id = ms.target_id
                     AND t.workspace_id = ms.workspace_id
                    WHERE ms.workspace_id = %s
                    ORDER BY ms.created_at DESC
                    ''',
                    (workspace_scope_id,),
                ).fetchall()
            normalized: list[dict[str, Any]] = []
            for row in rows:
                item = dict(row)
                item['is_enabled'] = monitored_system_row_enabled(item)
                normalized.append(item)
            return normalized
    
        with pg_connection() as connection:
            if request is not None:
                header_workspace_id, header_workspace_slug = _workspace_context_from_request(request)
                if header_workspace_id is not None or header_workspace_slug is not None:
                    _persist_workspace_context(
                        request,
                        workspace_id=header_workspace_id,
                        workspace_slug=header_workspace_slug,
                    )
                _mark_query_checkpoint('workspace_context_resolution')
                user, workspace_context, workspace_header_present = resolve_workspace_context_for_request(connection, request)
                user_id = str(user.get('id') or '')
                workspace_id = str(workspace_context['workspace_id'])
                workspace_slug = str((workspace_context.get('workspace') or {}).get('slug') or '') or None
                resolved_workspace_id = workspace_id
                resolved_workspace_slug = workspace_slug
                _persist_workspace_context(request, workspace_id=workspace_id, workspace_slug=workspace_slug)
            ensure_pilot_schema(connection)
            runtime_schema_missing_columns: list[str] = []
            runtime_schema_migration_hints: list[str] = []
            try:
                ensure_monitoring_runtime_schema_capabilities(connection)
            except HTTPException as exc:
                detail_payload = exc.detail if isinstance(exc.detail, dict) else {}
                if (
                    exc.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
                    and detail_payload.get('code') == 'runtime_schema_incomplete'
                ):
                    runtime_schema_missing_columns = [
                        str(column)
                        for column in (detail_payload.get('missing_columns') or [])
                        if str(column).strip()
                    ]
                    runtime_schema_migration_hints = list(detail_payload.get('migration_hints') or [])
                    schema_drift_detected = True
                    logger.warning(
                        'monitoring_runtime_status_schema_drift_continue workspace_id=%s workspace_slug=%s missing_columns=%s',
                        workspace_id,
                        workspace_slug,
                        runtime_schema_missing_columns,
                    )
                else:
                    raise
            if request is not None:
                _mark_query_checkpoint('load_runtime_monitored_rows')
                monitored_rows = _load_runtime_monitored_rows(connection, workspace_id)
                logger.info(
                    'monitoring_runtime_status_workspace_resolution workspace_id=%s workspace_slug=%s workspace_header_present=%s user_id=%s',
                    workspace_id,
                    workspace_slug,
                    workspace_header_present,
                    user_id,
                )
                if RUNTIME_STATUS_DEEP_DIAGNOSTICS_ENABLED:
                    try:
                        _mark_query_checkpoint('load_workspace_monitored_rows_raw')
                        raw_workspace_rows = _load_workspace_monitored_rows_raw(connection, workspace_id)
                    except Exception:
                        query_failure_detected = True
                        logger.exception('monitoring_runtime_status_raw_rows_load_failed workspace_id=%s', workspace_id)
                        raw_workspace_rows = []
                    if len(monitored_rows) == 0 and len(raw_workspace_rows) > 0:
                        monitored_rows = raw_workspace_rows
                        logger.warning(
                            'monitoring_runtime_status_workspace_rows_recovered_from_raw workspace_id=%s raw_rows=%s raw_row_ids=%s',
                            workspace_id,
                            len(raw_workspace_rows),
                            [str((row or {}).get('id') or '') for row in raw_workspace_rows if (row or {}).get('id')],
                        )
                    try:
                        _mark_query_checkpoint('list_workspace_monitored_system_rows')
                        listed_monitored_rows = list_workspace_monitored_system_rows(connection, workspace_id)
                    except Exception:
                        query_failure_detected = True
                        logger.exception('monitoring_runtime_status_list_rows_load_failed workspace_id=%s', workspace_id)
                        listed_monitored_rows = []
                    logger.info(
                        'monitoring_runtime_status_workspace_rows workspace_id=%s list_route_rows=%s list_route_row_ids=%s list_route_rows_detail=%s runtime_rows=%s runtime_row_ids=%s runtime_rows_detail=%s',
                        workspace_id,
                        len(listed_monitored_rows),
                        [str((row or {}).get('id') or '') for row in listed_monitored_rows if (row or {}).get('id')],
                        listed_monitored_rows,
                        len(monitored_rows),
                        [str((row or {}).get('id') or '') for row in monitored_rows if (row or {}).get('id')],
                        monitored_rows,
                    )
            target_workspace_filter = 'AND t.workspace_id = %s' if workspace_id else ''
            evidence_workspace_filter = 'WHERE e.workspace_id = %s' if workspace_id else ''
            scoped_params: tuple[Any, ...] = (workspace_id,) if workspace_id else ()
            precomputed_active_counts: dict[str, Any] | None = None
            if workspace_id:
                try:
                    _mark_query_checkpoint('load_workspace_runtime_summary')
                    precomputed_row = connection.execute(
                        '''
                        SELECT active_alerts_count, active_incidents_count, updated_at
                        FROM monitoring_workspace_runtime_summary
                        WHERE workspace_id = %s::uuid
                        ''',
                        (workspace_id,),
                    ).fetchone()
                    precomputed_active_counts = dict(precomputed_row) if precomputed_row else None
                except Exception:
                    precomputed_active_counts = None
            _mark_query_checkpoint('count_open_alerts')
            use_precomputed_active_counts = False
            if precomputed_active_counts:
                precomputed_updated_at = _parse_ts(precomputed_active_counts.get('updated_at'))
                if precomputed_updated_at and int((now - precomputed_updated_at).total_seconds()) <= RUNTIME_STATUS_PRECOMPUTED_COUNTERS_MAX_AGE_SECONDS:
                    use_precomputed_active_counts = True
            raw_open_alerts_count = 0
            open_alerts_without_evidence_count = 0
            if use_precomputed_active_counts:
                raw_open_alerts_count = int(precomputed_active_counts.get('active_alerts_count') or 0)
            else:
                try:
                    raw_open_alerts_row = connection.execute(
                        f"SELECT COUNT(*) AS c FROM alerts WHERE status IN ('open','acknowledged','investigating') {'AND workspace_id = %s' if workspace_id else ''}",
                        scoped_params,
                    ).fetchone()
                    raw_open_alerts_count = int((raw_open_alerts_row or {}).get('c') or 0)
                except Exception as exc:
                    _record_optional_query_failure(
                        exc=exc,
                        checkpoint_label='count_open_alerts_raw',
                        impacted_fields=['active_alerts_count'],
                        reason_code='optional_table_unavailable',
                        error_code='runtime_optional_query_failed',
                    )
                    raw_open_alerts_count = 0
            try:
                open_alerts = connection.execute(
                    f'''
                    SELECT COUNT(*) AS c
                    FROM alerts a
                    JOIN detection_events de
                      ON de.workspace_id = a.workspace_id
                     AND de.id = a.detection_event_id
                    JOIN telemetry_events te
                      ON te.workspace_id = de.workspace_id
                     AND te.id = de.telemetry_event_id
                    WHERE a.status IN ('open','acknowledged','investigating')
                      {'AND a.workspace_id = %s' if workspace_id else ''}
                    ''',
                    scoped_params,
                ).fetchone()
                open_alerts_without_evidence_count = max(raw_open_alerts_count - int((open_alerts or {}).get('c') or 0), 0)
            except Exception as exc:
                _record_optional_query_failure(
                    exc=exc,
                    checkpoint_label='count_open_alerts',
                    impacted_fields=['active_alerts_count'],
                    reason_code='optional_table_unavailable',
                    error_code='runtime_optional_query_failed',
                )
                open_alerts = {'c': 0}
                open_alerts_without_evidence_count = int(raw_open_alerts_count)
            legacy_open_alerts_row = None
            legacy_open_alerts_without_evidence_count = 0
            try:
                legacy_open_alerts_row = connection.execute(
                    f'''
                    SELECT COUNT(DISTINCT a.id) AS c
                    FROM alerts a
                    JOIN detections d
                      ON (d.id = a.detection_id OR d.linked_alert_id = a.id)
                     AND d.workspace_id = a.workspace_id
                    WHERE a.status IN ('open','acknowledged','investigating')
                      AND (
                        d.raw_evidence_json IS NOT NULL
                        OR EXISTS (
                            SELECT 1
                            FROM detection_evidence de
                            WHERE de.workspace_id = d.workspace_id
                              AND de.detection_id = d.id
                        )
                      )
                      {'AND a.workspace_id = %s' if workspace_id else ''}
                    ''',
                    scoped_params,
                ).fetchone()
                legacy_open_alerts_without_evidence_count = max(
                    raw_open_alerts_count - int((legacy_open_alerts_row or {}).get('c') or 0),
                    0,
                )
            except Exception:
                legacy_open_alerts_without_evidence_count = int(raw_open_alerts_count)
            # Alerts backed by the legacy proof-chain path (detection_id + detection_evidence) are
            # not counted in the primary detection_event_id query.  Use the most generous count.
            open_alerts_without_evidence_count = min(open_alerts_without_evidence_count, legacy_open_alerts_without_evidence_count)
            _mark_query_checkpoint('count_open_incidents_raw')
            raw_open_incidents_count = 0
            if use_precomputed_active_counts:
                raw_open_incidents_count = int(precomputed_active_counts.get('active_incidents_count') or 0)
            else:
                try:
                    raw_open_incidents_row = connection.execute(
                        f"SELECT COUNT(*) AS c FROM incidents WHERE status IN ('open','acknowledged') {'AND workspace_id = %s' if workspace_id else ''}",
                        scoped_params,
                    ).fetchone()
                    raw_open_incidents_count = int((raw_open_incidents_row or {}).get('c') or 0)
                except Exception as exc:
                    _record_optional_query_failure(
                        exc=exc,
                        checkpoint_label='count_open_incidents_raw',
                        impacted_fields=['active_incidents_count'],
                        reason_code='optional_table_unavailable',
                        error_code='runtime_optional_query_failed',
                    )
                    raw_open_incidents_count = 0
            _mark_query_checkpoint('count_open_incidents')
            try:
                open_incidents = connection.execute(
                    f'''
                    WITH proof_chain_alerts AS (
                        SELECT a.id, a.incident_id
                        FROM alerts a
                        JOIN detection_events de
                          ON de.workspace_id = a.workspace_id
                         AND de.id = a.detection_event_id
                        JOIN telemetry_events te
                          ON te.workspace_id = de.workspace_id
                         AND te.id = de.telemetry_event_id
                        WHERE a.status IN ('open','acknowledged','investigating')
                          {'AND a.workspace_id = %s' if workspace_id else ''}
                        UNION
                        SELECT a.id, a.incident_id
                        FROM alerts a
                        JOIN detections d
                          ON d.id = a.detection_id
                         AND d.workspace_id = a.workspace_id
                        WHERE a.status IN ('open','acknowledged','investigating')
                          AND EXISTS (
                              SELECT 1
                              FROM detection_evidence de
                              WHERE de.workspace_id = d.workspace_id
                                AND de.detection_id = d.id
                          )
                          {'AND a.workspace_id = %s' if workspace_id else ''}
                    )
                    SELECT COUNT(DISTINCT i.id) AS c
                    FROM incidents i
                    WHERE i.status IN ('open','acknowledged')
                      AND (
                          EXISTS (
                              SELECT 1
                              FROM proof_chain_alerts pca
                              WHERE pca.incident_id = i.id
                          )
                          OR EXISTS (
                              SELECT 1
                              FROM proof_chain_alerts pca
                              WHERE i.source_alert_id = pca.id
                          )
                      )
                      {'AND i.workspace_id = %s' if workspace_id else ''}
                    ''',
                    scoped_params + scoped_params + scoped_params if workspace_id else (),
                ).fetchone()
            except Exception as exc:
                _record_optional_query_failure(
                    exc=exc,
                    checkpoint_label='count_open_incidents',
                    impacted_fields=['active_incidents_count'],
                    reason_code='optional_table_unavailable',
                    error_code='runtime_optional_query_failed',
                )
                open_incidents = {'c': 0}
            _mark_query_checkpoint('count_incidents_without_alerts')
            incidents_without_alert_count = 0
            try:
                incident_without_alert_row = connection.execute(
                    f'''
                    SELECT COUNT(*) AS c
                    FROM incidents i
                    WHERE i.status IN ('open','acknowledged')
                      AND NOT EXISTS (
                          SELECT 1
                          FROM alerts a
                          WHERE a.workspace_id = i.workspace_id
                            AND (
                                a.incident_id = i.id
                                OR i.source_alert_id = a.id
                            )
                      )
                      {'AND i.workspace_id = %s' if workspace_id else ''}
                    ''',
                    scoped_params,
                ).fetchone()
                incidents_without_alert_count = int((incident_without_alert_row or {}).get('c') or 0)
            except Exception as exc:
                _record_optional_query_failure(
                    exc=exc,
                    checkpoint_label='count_incidents_without_alerts',
                    impacted_fields=['active_incidents_count'],
                    reason_code='optional_table_unavailable',
                    error_code='runtime_optional_query_failed',
                )
                incidents_without_alert_count = 0
            _mark_query_checkpoint('count_response_actions_without_incident')
            response_actions_without_incident_count = 0
            try:
                response_action_row = connection.execute(
                    f'''
                    SELECT COUNT(*) AS c
                    FROM response_actions ra
                    WHERE ra.incident_id IS NULL
                      {'AND ra.workspace_id = %s' if workspace_id else ''}
                    ''',
                    scoped_params,
                ).fetchone()
                response_actions_without_incident_count = int((response_action_row or {}).get('c') or 0)
            except Exception as exc:
                _record_optional_query_failure(
                    exc=exc,
                    checkpoint_label='count_response_actions_without_incident',
                    impacted_fields=['active_incidents_count'],
                    reason_code='optional_table_unavailable',
                    error_code='runtime_optional_query_failed',
                )
                response_actions_without_incident_count = 0
            _mark_query_checkpoint('count_runtime_setup_chain')
            try:
                verified_assets_count = int(
                    (
                        connection.execute(
                            f"""
                            SELECT COUNT(*) AS c FROM assets
                            WHERE workspace_id = %s
                              AND LOWER(COALESCE(verification_status, '')) IN ('verified', 'approved', 'active')
                            """,
                            (workspace_id,),
                        ).fetchone()
                        or {}
                    ).get('c')
                    or 0
                )
                latest_detection_count_row = connection.execute(
                    f'SELECT COUNT(*) AS c FROM detections {"WHERE workspace_id = %s" if workspace_id else ""}',
                    scoped_params,
                ).fetchone()
                detections_count = int((latest_detection_count_row or {}).get('c') or 0)
                alerts_count = int((open_alerts or {}).get('c') or 0)
                incidents_count = int((open_incidents or {}).get('c') or 0)
                response_actions_count = int(
                    (
                        connection.execute(
                            'SELECT COUNT(*) AS c FROM response_actions WHERE workspace_id = %s',
                            (workspace_id,),
                        ).fetchone()
                        or {}
                    ).get('c')
                    or 0
                )
                evidence_count = int(
                    (
                        connection.execute(
                            # Exclude clean monitoring health records — they are proofs
                            # that the monitoring loop ran without finding threats, not
                            # evidence packages that require a detection-alert-incident chain.
                            """SELECT COUNT(*) AS c FROM evidence
                               WHERE workspace_id = %s
                               AND event_type NOT IN (
                                   'monitoring_evaluation_no_threat',
                                   'coverage_telemetry'
                               )""",
                            (workspace_id,),
                        ).fetchone()
                        or {}
                    ).get('c')
                    or 0
                )
            except Exception:
                verified_assets_count = 0
                detections_count = 0
                alerts_count = int((open_alerts or {}).get('c') or 0)
                incidents_count = int((open_incidents or {}).get('c') or 0)
                response_actions_count = 0
                evidence_count = 0
            _mark_query_checkpoint('count_broken_targets')
            try:
                broken_targets = connection.execute(
                    f'''
                    SELECT COUNT(*) AS c
                    FROM targets t
                    LEFT JOIN assets a
                      ON a.id = t.asset_id
                     AND a.workspace_id = t.workspace_id
                     AND a.deleted_at IS NULL
                    WHERE t.deleted_at IS NULL
                      AND t.enabled = TRUE
                      AND (t.asset_id IS NULL OR a.id IS NULL)
                      {target_workspace_filter}
                    ''',
                    scoped_params,
                ).fetchone()
            except Exception as exc:
                _record_optional_query_failure(
                    exc=exc,
                    checkpoint_label='count_broken_targets',
                    impacted_fields=['invalid_enabled_targets'],
                    reason_code='optional_table_unavailable',
                    error_code='runtime_optional_query_failed',
                )
                broken_targets = {'c': 0}
            _mark_query_checkpoint('count_dead_lettered_targets')
            try:
                dead_lettered_targets = connection.execute(
                    f'''
                    SELECT COUNT(*) AS c
                    FROM targets t
                    WHERE t.deleted_at IS NULL
                      AND t.enabled = TRUE
                      AND COALESCE(t.monitoring_enabled, FALSE) = TRUE
                      AND t.monitoring_dead_lettered_at IS NOT NULL
                      {target_workspace_filter}
                    ''',
                    scoped_params,
                ).fetchone()
            except Exception as exc:
                _record_optional_query_failure(
                    exc=exc,
                    checkpoint_label='count_dead_lettered_targets',
                    impacted_fields=['dead_lettered_targets'],
                    reason_code='optional_table_unavailable',
                    error_code='runtime_optional_query_failed',
                )
                dead_lettered_targets = {'c': 0}
            summary_cache_key = f'workspace:{workspace_id}' if workspace_id else None
            cached_workspace_summary = RUNTIME_STATUS_SUMMARY_CACHE.get(summary_cache_key) if summary_cache_key else None
            summary_cache_valid = False
            if cached_workspace_summary:
                cached_at, _cached_payload = cached_workspace_summary
                summary_cache_valid = (perf_counter() - cached_at) <= RUNTIME_STATUS_SUMMARY_CACHE_TTL_SECONDS
            raw_enabled_targets = 0
            healthy_enabled_target_rows: list[dict[str, Any]] = []
            if summary_cache_valid:
                cached_payload = dict(cached_workspace_summary[1])
                raw_enabled_targets = int(cached_payload.get('raw_enabled_targets') or 0)
                healthy_enabled_target_rows = list(cached_payload.get('healthy_enabled_target_rows') or [])
            else:
                _mark_query_checkpoint('count_raw_enabled_targets')
                try:
                    raw_enabled_targets_row = connection.execute(
                        f'''
                        SELECT COUNT(*) AS c
                        FROM targets t
                        WHERE t.deleted_at IS NULL
                          AND t.enabled = TRUE
                          {target_workspace_filter}
                        ''',
                        scoped_params,
                    ).fetchone()
                except Exception as exc:
                    _record_optional_query_failure(
                        exc=exc,
                        checkpoint_label='count_raw_enabled_targets',
                        impacted_fields=['raw_enabled_targets'],
                        reason_code='optional_table_unavailable',
                        error_code='runtime_optional_query_failed',
                    )
                    raw_enabled_targets_row = {'c': 0}
                raw_enabled_targets = int((raw_enabled_targets_row or {}).get('c') or 0)
                _mark_query_checkpoint('list_healthy_enabled_target_rows')
                try:
                    healthy_enabled_target_rows = connection.execute(
                        f'''
                        SELECT t.id, t.asset_id
                        FROM targets t
                        JOIN assets a
                          ON a.id = t.asset_id
                         AND a.workspace_id = t.workspace_id
                         AND a.deleted_at IS NULL
                        WHERE t.deleted_at IS NULL
                          AND t.enabled = TRUE
                          AND t.asset_id IS NOT NULL
                          AND {monitorable_target_types_sql_clause('t.target_type')}
                          {target_workspace_filter}
                        ''',
                        scoped_params,
                    ).fetchall()
                except Exception as exc:
                    _record_optional_query_failure(
                        exc=exc,
                        checkpoint_label='list_healthy_enabled_target_rows',
                        impacted_fields=['configured_systems', 'protected_assets'],
                        reason_code='optional_table_unavailable',
                        error_code='runtime_optional_query_failed',
                    )
                    healthy_enabled_target_rows = []
                if summary_cache_key:
                    RUNTIME_STATUS_SUMMARY_CACHE[summary_cache_key] = (
                        perf_counter(),
                        {
                            'raw_enabled_targets': raw_enabled_targets,
                            'healthy_enabled_target_rows': list(healthy_enabled_target_rows),
                        },
                    )
            healthy_enabled_targets_count = len(healthy_enabled_target_rows)
            healthy_enabled_assets_count = len(
                {str(row.get('asset_id')) for row in healthy_enabled_target_rows if row.get('asset_id')}
            )
            healthy_enabled_target_ids = {str(row.get('id')) for row in healthy_enabled_target_rows if row.get('id')}
            healthy_enabled_target_asset_map = {
                str(row.get('id')): str(row.get('asset_id'))
                for row in healthy_enabled_target_rows
                if row.get('id') and row.get('asset_id')
            }
            enabled_monitored_rows_count = sum(
                1
                for row in monitored_rows
                if monitored_system_row_enabled(row) and is_monitorable_target_type(row.get('target_type'))
            )
            enabled_monitored_target_ids = {
                str(row.get('target_id'))
                for row in monitored_rows
                if monitored_system_row_enabled(row) and is_monitorable_target_type(row.get('target_type')) and row.get('target_id')
            }
            missing_healthy_target_ids = healthy_enabled_target_ids - enabled_monitored_target_ids
            logger.info(
                'monitoring_runtime_status_data_path workspace_id=%s targets_enabled_valid=%s target_ids_enabled_valid=%s monitored_rows_before=%s monitored_row_ids_before=%s enabled_monitored_rows_before=%s',
                workspace_id,
                healthy_enabled_targets_count,
                sorted(healthy_enabled_target_ids),
                len(monitored_rows),
                [str(row.get('id') or '') for row in monitored_rows if row.get('id')],
                enabled_monitored_rows_count,
            )
            if healthy_enabled_targets_count > 0 and (enabled_monitored_rows_count < healthy_enabled_targets_count or bool(missing_healthy_target_ids)):
                try:
                    reconcile_result = reconcile_enabled_targets_monitored_systems(connection, workspace_id=workspace_id)
                    logger.info(
                        'monitoring_runtime_status_reconcile workspace_id=%s healthy_enabled_targets=%s enabled_monitored_rows_before=%s missing_healthy_target_ids=%s created_or_updated=%s created_monitored_systems=%s preserved_monitored_systems=%s removed_monitored_systems=%s',
                        workspace_id,
                        healthy_enabled_targets_count,
                        enabled_monitored_rows_count,
                        len(missing_healthy_target_ids),
                        reconcile_result.get('created_or_updated'),
                        reconcile_result.get('created_monitored_systems'),
                        reconcile_result.get('preserved_monitored_systems'),
                        reconcile_result.get('removed_monitored_systems'),
                    )
                    monitored_rows = _load_runtime_monitored_rows(connection, workspace_id)
                    logger.info(
                        'monitoring_runtime_status_data_path workspace_id=%s monitored_rows_after=%s monitored_row_ids_after=%s',
                        workspace_id,
                        len(monitored_rows),
                        [str(row.get('id') or '') for row in monitored_rows if row.get('id')],
                    )
                except Exception as exc:
                    _record_optional_query_failure(
                        exc=exc,
                        checkpoint_label='reconcile_enabled_targets_monitored_systems',
                        impacted_fields=['configured_systems', 'reporting_systems'],
                        reason_code='optional_table_unavailable',
                        error_code='runtime_optional_query_failed',
                    )
            _mark_query_checkpoint('select_latest_evidence')
            try:
                latest_evidence = connection.execute(
                    f"SELECT observed_at, block_number FROM evidence e {evidence_workspace_filter} ORDER BY observed_at DESC LIMIT 1",
                    scoped_params,
                ).fetchone()
            except Exception as exc:
                _record_optional_query_failure(
                    exc=exc,
                    checkpoint_label='select_latest_evidence',
                    impacted_fields=['last_telemetry_at'],
                    reason_code='optional_table_unavailable',
                    error_code='runtime_optional_query_failed',
                )
                latest_evidence = None
            latest_detection_eval_query = '''
                SELECT created_at, response_payload
                FROM analysis_runs
                WHERE analysis_type LIKE %s
            '''
            latest_detection_eval_params: list[Any] = ['monitoring_%']
            if workspace_id:
                latest_detection_eval_query += ' AND workspace_id = %s'
                latest_detection_eval_params.append(workspace_id)
            latest_detection_eval_query += '''
                ORDER BY created_at DESC
                LIMIT 1
            '''
            _mark_query_checkpoint('select_latest_detection_eval')
            try:
                latest_detection_eval = connection.execute(
                    latest_detection_eval_query,
                    tuple(latest_detection_eval_params),
                ).fetchone()
            except Exception as exc:
                _record_optional_query_failure(
                    exc=exc,
                    checkpoint_label='select_latest_detection_eval',
                    impacted_fields=['last_detection_at'],
                    reason_code='optional_table_unavailable',
                    error_code='runtime_optional_query_failed',
                )
                latest_detection_eval = None
            latest_detection_evaluation_at = _parse_ts((latest_detection_eval or {}).get('created_at'))
            latest_detection_payload = _json_safe_value((latest_detection_eval or {}).get('response_payload') or {}) if latest_detection_eval else None
            latest_detection_query = '''
                SELECT detected_at
                FROM detections
            '''
            latest_detection_params: list[Any] = []
            if workspace_id:
                latest_detection_query += ' WHERE workspace_id = %s'
                latest_detection_params.append(workspace_id)
            latest_detection_query += '''
                ORDER BY detected_at DESC
                LIMIT 1
            '''
            _mark_query_checkpoint('select_latest_detection')
            try:
                latest_detection_row = connection.execute(
                    latest_detection_query,
                    tuple(latest_detection_params),
                ).fetchone()
            except Exception as exc:
                _record_optional_query_failure(
                    exc=exc,
                    checkpoint_label='select_latest_detection',
                    impacted_fields=['last_detection_at'],
                    reason_code='optional_table_unavailable',
                    error_code='runtime_optional_query_failed',
                )
                latest_detection_row = None
            latest_detection_at = _parse_ts((latest_detection_row or {}).get('detected_at'))
            supports_receipt_live_coverage_columns = (
                'monitoring_event_receipts.evidence_source' not in runtime_schema_missing_columns
                and 'monitoring_event_receipts.telemetry_kind' not in runtime_schema_missing_columns
            )
            live_coverage_receipts_query = f'''
                WITH filtered_receipts AS (
                    SELECT
                        e.processed_at,
                        ms.id AS monitored_system_id
                    FROM monitoring_event_receipts e
                    JOIN monitored_systems ms
                      ON ms.workspace_id = e.workspace_id
                     AND ms.target_id = e.target_id
                     AND ms.is_enabled IS DISTINCT FROM FALSE
                    JOIN targets t
                      ON t.id = e.target_id
                     AND t.workspace_id = e.workspace_id
                     AND t.deleted_at IS NULL
                     AND t.enabled = TRUE
                     AND {monitorable_target_types_sql_clause('t.target_type')}
                    WHERE (
                        (
                            {'TRUE' if supports_receipt_live_coverage_columns else 'FALSE'}
                            AND e.evidence_source = 'live'
                            AND (
                                e.telemetry_kind = 'coverage'
                                OR (e.telemetry_kind = 'target_event' AND e.receipt_kind = 'target_event')
                            )
                        )
                        OR (
                            {'FALSE' if supports_receipt_live_coverage_columns else 'TRUE'}
                            AND e.receipt_kind = 'coverage'
                        )
                    )
                      AND e.processed_at IS NOT NULL
                      AND COALESCE(LOWER(e.ingestion_source), '') NOT IN ('demo', 'simulator', 'replay', 'synthetic', 'fallback')
                      {'AND e.workspace_id = %s' if workspace_id else ''}
                ),
                rolled AS (
                    SELECT
                        monitored_system_id,
                        MAX(processed_at) AS latest_processed_at,
                        COUNT(*) AS receipt_count
                    FROM filtered_receipts
                    GROUP BY monitored_system_id
                )
                SELECT
                    monitored_system_id,
                    latest_processed_at,
                    receipt_count,
                    MAX(latest_processed_at) OVER () AS workspace_latest_processed_at,
                    SUM(receipt_count) OVER () AS workspace_receipt_count
                FROM rolled
                ORDER BY latest_processed_at DESC
            '''
            _mark_query_checkpoint('select_live_coverage_receipts')
            try:
                if workspace_id:
                    live_coverage_receipt_rows = connection.execute(
                        live_coverage_receipts_query,
                        (workspace_id,),
                    ).fetchall()
                else:
                    live_coverage_receipt_rows = connection.execute(
                        live_coverage_receipts_query,
                    ).fetchall()
            except Exception as exc:
                _record_optional_query_failure(
                    exc=exc,
                    checkpoint_label='select_live_coverage_receipts',
                    impacted_fields=['reporting_systems', 'last_coverage_telemetry_at', 'last_telemetry_at'],
                    reason_code='optional_table_unavailable',
                    error_code='runtime_coverage_query_failed',
                )
                live_coverage_receipt_rows = []
            live_coverage_receipts_persisted_count = int((live_coverage_receipt_rows[0] or {}).get('workspace_receipt_count') or 0) if live_coverage_receipt_rows else 0
            for receipt in live_coverage_receipt_rows:
                processed_at = _parse_ts((receipt or {}).get('latest_processed_at'))
                if processed_at is None:
                    continue
                live_coverage_receipts_workspace_latest = (
                    _parse_ts((receipt or {}).get('workspace_latest_processed_at'))
                    if live_coverage_receipts_workspace_latest is None
                    else live_coverage_receipts_workspace_latest
                )
                monitored_system_id = str((receipt or {}).get('monitored_system_id') or '').strip()
                if not monitored_system_id:
                    continue
                live_coverage_receipts_by_system[monitored_system_id] = processed_at
            if request is None:
                _mark_query_checkpoint('load_runtime_monitored_rows_unscoped')
                monitored_rows = _load_runtime_monitored_rows(connection, workspace_id)
        _mark_query_checkpoint('aggregation_complete')
        query_total_duration_ms = sum(checkpoint_durations_ms.values())
        RUNTIME_STATUS_QUERY_PROFILE_HISTORY['total_runtime_status_query'].append(query_total_duration_ms)
        proxy_timeout_ms = max(RUNTIME_STATUS_PROXY_TIMEOUT_SECONDS, 1) * 1000
        query_p95_ms = _percentile(list(RUNTIME_STATUS_QUERY_PROFILE_HISTORY['total_runtime_status_query']), 95)
        query_p99_ms = _percentile(list(RUNTIME_STATUS_QUERY_PROFILE_HISTORY['total_runtime_status_query']), 99)
        slow_checkpoint_summary: list[dict[str, Any]] = []
        for checkpoint in sorted(set(completed_checkpoints), key=lambda label: checkpoint_durations_ms.get(label, 0.0), reverse=True)[:5]:
            checkpoint_history = list(RUNTIME_STATUS_QUERY_PROFILE_HISTORY[checkpoint])
            slow_checkpoint_summary.append(
                {
                    'checkpoint': checkpoint,
                    'duration_ms': round(checkpoint_durations_ms.get(checkpoint, 0.0), 2),
                    'p95_ms': round(_percentile(checkpoint_history, 95) or 0.0, 2),
                }
            )
        logger.info(
            'monitoring_runtime_status_query_profile workspace_id=%s total_ms=%s p95_total_ms=%s p99_total_ms=%s proxy_timeout_ms=%s checkpoint_count=%s slowest_checkpoints=%s',
            workspace_id,
            round(query_total_duration_ms, 2),
            round(query_p95_ms, 2) if query_p95_ms is not None else None,
            round(query_p99_ms, 2) if query_p99_ms is not None else None,
            proxy_timeout_ms,
            len(completed_checkpoints),
            slow_checkpoint_summary,
        )
        if slow_checkpoint_summary:
            logger.info(
                'monitoring_runtime_status_top_slow_queries workspace_id=%s top_2=%s',
                workspace_id,
                slow_checkpoint_summary[:2],
            )
        workspace_latency_key = str(workspace_id or '__global__')
        p95_sustained = False
        p99_sustained = False
        p95_breach_count = 0
        p99_breach_count = 0
        p95_samples = 0
        p99_samples = 0
        if query_p95_ms is not None:
            p95_sustained, p95_breach_count, p95_samples = _latency_alert_state(
                workspace_key=workspace_latency_key,
                metric='p95',
                breached=query_p95_ms >= RUNTIME_STATUS_P95_ALERT_THRESHOLD_MS,
            )
        if query_p99_ms is not None:
            p99_sustained, p99_breach_count, p99_samples = _latency_alert_state(
                workspace_key=workspace_latency_key,
                metric='p99',
                breached=query_p99_ms >= RUNTIME_STATUS_P99_ALERT_THRESHOLD_MS,
            )
        if query_p95_ms is not None and p95_sustained:
            logger.warning(
                'monitoring_runtime_status_latency_regression_sustained workspace_id=%s p95_total_ms=%s threshold_ms=%s breaches=%s/%s',
                workspace_id,
                round(query_p95_ms, 2),
                RUNTIME_STATUS_P95_ALERT_THRESHOLD_MS,
                p95_breach_count,
                p95_samples,
            )
        if query_p99_ms is not None and p99_sustained:
            logger.warning(
                'monitoring_runtime_status_latency_regression_sustained_p99 workspace_id=%s p99_total_ms=%s threshold_ms=%s breaches=%s/%s',
                workspace_id,
                round(query_p99_ms, 2),
                RUNTIME_STATUS_P99_ALERT_THRESHOLD_MS,
                p99_breach_count,
                p99_samples,
            )
        if query_p99_ms is not None and query_p99_ms >= proxy_timeout_ms and p99_sustained:
            logger.warning(
                'monitoring_runtime_status_query_profile_timeout_risk_p99_sustained workspace_id=%s p99_total_ms=%s proxy_timeout_ms=%s breaches=%s/%s',
                workspace_id,
                round(query_p99_ms, 2),
                proxy_timeout_ms,
                p99_breach_count,
                p99_samples,
            )
        if query_p95_ms is not None and query_p95_ms >= proxy_timeout_ms and p95_sustained:
            logger.warning(
                'monitoring_runtime_status_query_profile_timeout_risk_sustained workspace_id=%s p95_total_ms=%s proxy_timeout_ms=%s breaches=%s/%s',
                workspace_id,
                round(query_p95_ms, 2),
                proxy_timeout_ms,
                p95_breach_count,
                p95_samples,
            )
        expected_monitored_row_fields = {
            'id',
            'asset_id',
            'target_id',
            'last_heartbeat',
            'last_event_at',
            'last_coverage_telemetry_at',
        }
        if any(not expected_monitored_row_fields.issubset(set(row.keys())) for row in monitored_rows):
            schema_drift_detected = True
        parsed_heartbeats = [_parse_ts(row.get('last_heartbeat')) for row in monitored_rows]
        recent_heartbeat_systems = 0
        for row, parsed_heartbeat in zip(monitored_rows, parsed_heartbeats):
            if not monitored_system_row_enabled(row) or parsed_heartbeat is None:
                continue
            heartbeat_window = max(int(row.get('monitoring_interval_seconds') or 30), 30) * 2
            if int((now - parsed_heartbeat).total_seconds()) <= heartbeat_window:
                recent_heartbeat_systems += 1
        last_system_heartbeat = max((ts for ts in parsed_heartbeats if ts is not None), default=None)
        worker_heartbeat = _parse_ts(health.get('last_heartbeat_at') or health.get('last_cycle_at'))
        last_heartbeat = last_system_heartbeat or worker_heartbeat
        heartbeat_age = int((now - last_heartbeat).total_seconds()) if last_heartbeat else None
        # Also check monitoring_heartbeats (canonical table the worker writes to).
        # This prevents a stale monitoring_worker_state row from wrongly reporting
        # "live worker not running" when the worker is actually healthy and has
        # a different worker_name than the API service's WORKER_STATE default.
        if workspace_id:
            try:
                _canonical_hb_row = connection.execute(
                    'SELECT MAX(last_heartbeat_at) AS ts FROM monitoring_heartbeats WHERE workspace_id = %s::uuid',
                    (workspace_id,),
                ).fetchone()
                _canonical_hb_at = _parse_ts(
                    (_canonical_hb_row or {}).get('ts') if isinstance(_canonical_hb_row, dict) else None
                )
                if _canonical_hb_at:
                    _ref = last_heartbeat or datetime(1970, 1, 1, tzinfo=timezone.utc)
                    if _canonical_hb_at > _ref:
                        last_heartbeat = _canonical_hb_at
                        heartbeat_age = int((now - last_heartbeat).total_seconds())
                        logger.debug(
                            'heartbeat_resolved_from_monitoring_heartbeats workspace_id=%s age_seconds=%s',
                            workspace_id, heartbeat_age,
                        )
            except Exception:
                pass
        # Canonical stable-polling stale threshold. The stable RPC polling loop runs on a
        # ~5-minute cadence, so a heartbeat/poll a few minutes old is HEALTHY — the tight
        # realtime heartbeat TTL (180s) must never gate it. Computed once and reused by the
        # stale_heartbeat flag, the continuity evaluator, and build_worker_status so the top
        # banner, worker-status card, limitation text, and runtime summary agree.
        _stable_poll_stale_threshold = stable_poll_stale_threshold_seconds(MONITOR_POLL_INTERVAL_SECONDS)
        # Bug 5: If still stale/missing after monitoring_heartbeats check, fallback to
        # MAX(last_heartbeat_at) FROM monitoring_worker_state (any worker name) to handle
        # Railway auto-named workers that wrote heartbeats under a different worker_name.
        if last_heartbeat is None or heartbeat_age is None or heartbeat_age > _stable_poll_stale_threshold:
            try:
                _mws_row = connection.execute(
                    'SELECT MAX(last_heartbeat_at) AS ts FROM monitoring_worker_state',
                ).fetchone()
                _mws_at = _parse_ts((_mws_row or {}).get('ts') if isinstance(_mws_row, dict) else None)
                if _mws_at and (last_heartbeat is None or _mws_at > last_heartbeat):
                    last_heartbeat = _mws_at
                    heartbeat_age = int((now - last_heartbeat).total_seconds())
                    logger.debug(
                        'heartbeat_resolved_from_worker_state_fallback workspace_id=%s age_seconds=%s',
                        workspace_id, heartbeat_age,
                    )
            except Exception:
                pass
        # The stable RPC polling worker is proven alive by EITHER a fresh heartbeat
        # OR a fresh poll cycle. CLAUDE.md keeps these as separate facts (heartbeat
        # proves the service is alive; poll proves the monitoring loop ran), but for
        # the *stable polling* verdict either one is sufficient. Only treat the worker
        # as stale when BOTH are stale, so a lagging heartbeat writer never contradicts
        # a Telemetry page that shows fresh RPC polling. last_cycle_at is the worker's
        # canonical per-cycle poll timestamp (monitoring_worker_state, written every
        # poll loop alongside the monitoring_polls completion row).
        _stable_stale_ttl = _stable_poll_stale_threshold
        _heartbeat_is_stale = heartbeat_age is None or heartbeat_age > _stable_stale_ttl
        _last_poll_cycle_at = _parse_ts(health.get('last_cycle_at'))
        _poll_cycle_age = int((now - _last_poll_cycle_at).total_seconds()) if _last_poll_cycle_at else None
        _poll_cycle_is_stale = _poll_cycle_age is None or _poll_cycle_age > _stable_stale_ttl
        stale_heartbeat = _heartbeat_is_stale and _poll_cycle_is_stale
        # Resolved once here so the live-coverage-gap reason (below) and build_worker_status
        # agree on whether the realtime WebSocket worker is paused vs enabled.
        _reason_realtime_enabled = realtime_enabled()
        def _row_tracks_valid_monitorable_target(row: dict[str, Any]) -> bool:
            target_id = str(row.get('target_id') or '')
            if target_id and target_id in healthy_enabled_target_ids:
                return True
            return is_monitorable_target_type(row.get('target_type'))
    
        enabled_rows_all = [row for row in monitored_rows if monitored_system_row_enabled(row)]
        enabled_rows = [row for row in enabled_rows_all if _row_tracks_valid_monitorable_target(row)]
        unsupported_enabled_rows = [
            row for row in monitored_rows
            if monitored_system_row_enabled(row) and (not _row_tracks_valid_monitorable_target(row))
        ]
        enabled_monitoring_intervals: list[int] = []
        for row in enabled_rows:
            try:
                interval_seconds = int(row.get('monitoring_interval_seconds') or MONITOR_POLL_INTERVAL_SECONDS)
            except Exception:
                interval_seconds = MONITOR_POLL_INTERVAL_SECONDS
            if interval_seconds > 0:
                enabled_monitoring_intervals.append(interval_seconds)
        if enabled_monitoring_intervals:
            max_enabled_interval_seconds = max(enabled_monitoring_intervals)
            telemetry_window_seconds = max(
                telemetry_window_seconds,
                max_enabled_interval_seconds + max(MONITOR_POLL_INTERVAL_SECONDS * 4, 60),
            )
            logger.info(
                'monitoring_runtime_telemetry_window workspace_id=%s telemetry_window_seconds=%s max_enabled_interval_seconds=%s',
                workspace_id,
                telemetry_window_seconds,
                max_enabled_interval_seconds,
            )
        active_rows = [row for row in enabled_rows_all if str(row.get('runtime_status') or '').strip().lower() in {'healthy', 'active'}]
        enabled_asset_rows = [row for row in enabled_rows_all if row.get('asset_id')]
        _raw_system_count = len(enabled_rows_all)
        enabled_system_count = _raw_system_count or healthy_enabled_targets_count
        active_system_count = len(active_rows)
        system_count = len(monitored_rows) or healthy_enabled_targets_count
        _raw_asset_ids = {str(row.get('asset_id') or '') for row in enabled_asset_rows if row.get('asset_id')}
        _raw_asset_count = len(_raw_asset_ids)
        protected_assets_count = _raw_asset_count or healthy_enabled_assets_count or (healthy_enabled_targets_count if not monitored_rows else 0)
        # Fallback: if monitoring hasn't produced a count yet, use direct asset registry count
        if protected_assets_count == 0 and workspace_id:
            try:
                with pg_connection() as _assets_conn:
                    _direct_row = _assets_conn.execute(
                        'SELECT COUNT(DISTINCT id) AS c FROM assets WHERE workspace_id = %s::uuid AND deleted_at IS NULL',
                        (workspace_id,),
                    ).fetchone()
                _direct_count = int((_direct_row or {}).get('c') or 0)
                if _direct_count > 0:
                    protected_assets_count = _direct_count
            except Exception:
                pass
        linked_monitored_system_count = sum(1 for row in monitored_rows if monitored_system_row_enabled(row) and str(row.get('target_id') or '') in healthy_enabled_target_ids)
        def _row_has_valid_target_asset_link(row: dict[str, Any]) -> bool:
            target_id = str(row.get('target_id') or '')
            asset_id = str(row.get('asset_id') or '')
            if target_id not in healthy_enabled_target_ids:
                return False
            expected_asset_id = healthy_enabled_target_asset_map.get(target_id)
            if expected_asset_id:
                return bool(asset_id and asset_id == expected_asset_id)
            return bool(asset_id)
    
        valid_target_system_link_count = sum(1 for row in monitored_rows if monitored_system_row_enabled(row) and _row_has_valid_target_asset_link(row))
        valid_protected_asset_count = len(
            {
                str(row.get('asset_id') or '')
                for row in monitored_rows
                if monitored_system_row_enabled(row)
                if _row_has_valid_target_asset_link(row) and row.get('asset_id')
            }
        )
        monitorable_enabled_targets = healthy_enabled_targets_count
        valid_asset_linked_targets = healthy_enabled_targets_count
        enabled_monitored_systems = sum(1 for row in monitored_rows if monitored_system_row_enabled(row))
        valid_target_system_links = valid_target_system_link_count
        logger.info('runtime_status_query_stage_start workspace_id=%s stage=persisted_config_count', workspace_id)
        try:
            with pg_connection() as _cfg_conn:
                persisted_enabled_config_count = _count_persisted_enabled_monitoring_configs(_cfg_conn, workspace_id)
            logger.info('runtime_status_query_stage_success workspace_id=%s stage=persisted_config_count result=%s', workspace_id, persisted_enabled_config_count)
        except Exception:
            persisted_enabled_config_count = 0
        if persisted_enabled_config_count == 0 and monitorable_enabled_targets > 0:
            # monitoring_configs query unavailable (missing table or test mock) - fall back to target count
            persisted_enabled_config_count = monitorable_enabled_targets
        logger.info(
            'monitoring_runtime_status_counts workspace_id=%s enabled_monitored_systems=%s protected_assets=%s runtime_rows=%s list_route_rows=%s enabled_monitored_systems_list_route=%s protected_assets_list_route=%s',
            workspace_id,
            len(enabled_rows),
            protected_assets_count,
            len(monitored_rows),
            len(listed_monitored_rows),
            sum(1 for row in listed_monitored_rows if monitored_system_row_enabled(row)),
            len({str((row or {}).get('asset_id') or '') for row in listed_monitored_rows if monitored_system_row_enabled(row) and (row or {}).get('asset_id')}),
        )
        evidence_at = _parse_ts((latest_evidence or {}).get('observed_at'))
        evidence_freshness = int((now - evidence_at).total_seconds()) if evidence_at else None
        detection_eval_freshness = int((now - latest_detection_evaluation_at).total_seconds()) if latest_detection_evaluation_at else None
        successful_detection_outcomes = {'DETECTION_CONFIRMED', 'NO_CONFIRMED_ANOMALY_FROM_REAL_EVIDENCE'}
        latest_detection_metadata = latest_detection_payload.get('metadata') if isinstance(latest_detection_payload, dict) and isinstance(latest_detection_payload.get('metadata'), dict) else {}
        latest_detection_outcome = str(
            (latest_detection_metadata or {}).get('detection_outcome')
            or ((latest_detection_payload or {}).get('detection_outcome') if isinstance(latest_detection_payload, dict) else '')
            or ''
        ).upper()
        successful_detection_evaluation = bool(latest_detection_outcome in successful_detection_outcomes)
        successful_detection_evaluation_recent = bool(
            successful_detection_evaluation
            and detection_eval_freshness is not None
            and detection_eval_freshness <= max(900, MONITOR_POLL_INTERVAL_SECONDS * 10)
        )
        detection_pipeline_checkpoint_at = latest_detection_at or latest_detection_evaluation_at
        # worker_alive is derived strictly from worker heartbeat freshness. A fresh heartbeat
        # proves the worker/service is alive even if no target was polled this cycle (e.g. every
        # target is dead-lettered/blocked). Target-level blockage must NOT be reported as
        # "worker not running" — it surfaces separately as a degraded/blocked target reason.
        runner_alive = bool(health.get('worker_running')) or not stale_heartbeat
        worker_alive = runner_alive
        dead_lettered_count = int((dead_lettered_targets or {}).get('c') or 0) if 'dead_lettered_targets' in locals() else 0
        has_monitorable_targets = healthy_enabled_targets_count > 0
        has_any_monitored_rows = len(monitored_rows) > 0
        if healthy_enabled_targets_count == 0 and len(monitored_rows) == 0:
            monitoring_status = 'offline'
        elif not runner_alive or health.get('last_error') or health.get('degraded') or stale_heartbeat or int((broken_targets or {}).get('c') or 0) > 0 or dead_lettered_count > 0:
            monitoring_status = 'degraded'
        elif evidence_freshness is None or evidence_freshness > max(900, MONITOR_POLL_INTERVAL_SECONDS * 10):
            monitoring_status = 'idle'
        else:
            monitoring_status = 'active'
        degraded_reason = health.get('degraded_reason')
        if not runner_alive and stale_heartbeat and enabled_system_count > 0:
            logger.info(
                'live_downgrade_reason workspace_id=%s reason=live_worker_not_running runner_alive=%s stale_heartbeat=%s enabled_systems=%s',
                workspace_id,
                runner_alive,
                stale_heartbeat,
                enabled_system_count,
            )
        if monitoring_status == 'offline':
            runtime_status = 'Offline'
        elif health.get('last_error'):
            runtime_status = 'Error'
        elif health.get('degraded') or degraded_reason or stale_heartbeat or int((broken_targets or {}).get('c') or 0) > 0 or dead_lettered_count > 0:
            runtime_status = 'Degraded'
            _stale_detail = (
                'live_worker_not_running'
                if stale_heartbeat and not runner_alive and enabled_system_count > 0 and last_heartbeat is None
                else ('stale_heartbeat' if stale_heartbeat else None)
            )
            # Dead-lettered/blocked targets are a target-level condition, not a worker outage.
            # Only attribute to the worker when the heartbeat is actually stale; otherwise the
            # truthful reason is that the target(s) are blocked while the worker is alive.
            degraded_reason = degraded_reason or (
                'invalid_enabled_targets' if int((broken_targets or {}).get('c') or 0) > 0
                else (_stale_detail or ('targets_blocked' if dead_lettered_count > 0 else None))
            )
        elif evidence_freshness is None or evidence_freshness > max(900, MONITOR_POLL_INTERVAL_SECONDS * 10):
            runtime_status = 'Idle'
        else:
            runtime_status = 'Active'
        recent_real_event_count_raw = (latest_detection_metadata or {}).get('recent_real_event_count')
        if recent_real_event_count_raw is None and isinstance(latest_detection_payload, dict):
            recent_real_event_count_raw = (latest_detection_payload or {}).get('recent_real_event_count')
        try:
            recent_real_event_count = int(recent_real_event_count_raw or 0)
        except Exception:
            recent_real_event_count = 0
        recent_last_real_event_at = _parse_ts(
            (latest_detection_metadata or {}).get('last_real_event_at')
            if isinstance(latest_detection_metadata, dict)
            else None
        )
        logger.info('runtime_status_query_stage_start workspace_id=%s stage=post_aggregation_canonical checkpoint=%s', workspace_id, last_query_checkpoint)
        for _post_agg_attempt in range(2):
            try:
                with pg_connection() as connection:
                    _mark_query_checkpoint('canonical_post_aggregation')
                    logger.info('runtime_status_query_stage_start workspace_id=%s stage=canonical_last_poll_row checkpoint=%s', workspace_id, last_query_checkpoint)
                    canonical_last_poll_row = connection.execute(
                        '''
                        SELECT MAX(COALESCE(poll_finished_at, poll_started_at)) AS ts
                        FROM monitoring_polls
                        WHERE workspace_id = %s::uuid
                        ''',
                        (workspace_id,),
                    ).fetchone()
                    last_poll_at = _parse_ts((canonical_last_poll_row or {}).get('ts') if isinstance(canonical_last_poll_row, dict) else None)
                    logger.info('runtime_status_query_stage_success workspace_id=%s stage=canonical_last_poll_row', workspace_id)
                    canonical_last_heartbeat_row = connection.execute(
                        '''
                        SELECT MAX(last_heartbeat_at) AS ts
                        FROM monitoring_heartbeats
                        WHERE workspace_id = %s::uuid
                        ''',
                        (workspace_id,),
                    ).fetchone()
                    canonical_last_heartbeat_at = _parse_ts((canonical_last_heartbeat_row or {}).get('ts') if isinstance(canonical_last_heartbeat_row, dict) else None)
                    # Include `last_heartbeat` (resolved from monitoring_heartbeats in the first
                    # connection) so the payload never returns null when the worker is alive.
                    canonical_last_heartbeat_at = canonical_last_heartbeat_at or last_system_heartbeat or last_heartbeat or _parse_ts(health.get('last_heartbeat_at'))
                    canonical_last_telemetry_source = 'telemetry_events.observed_at'
                    # Mirror the Target Telemetry page: same workspace-scoped live evm_rpc/rpc_polling
                    # rows that the customer sees, additionally requiring a block_number so we only
                    # count poll cycles that proved chain reachability. block_number lives in
                    # payload_json (no dedicated column), so we test for a non-empty JSON value.
                    canonical_last_telemetry_row = connection.execute(
                        '''
                        SELECT MAX(observed_at) AS ts
                        FROM telemetry_events
                        WHERE workspace_id = %s::uuid
                          AND evidence_source = 'live'
                          AND event_type IN ('rpc_polling', 'live_provider')
                          AND provider_type IN ('evm_rpc', 'live_provider')
                          AND observed_at IS NOT NULL
                          AND COALESCE(payload_json->>'block_number', '') <> ''
                        ''',
                        (workspace_id,),
                    ).fetchone()
                    canonical_last_telemetry_at = _parse_ts((canonical_last_telemetry_row or {}).get('ts') if isinstance(canonical_last_telemetry_row, dict) else None)
                    canonical_last_detection_source = 'detection_events.created_at'
                    canonical_last_detection_row = connection.execute(
                        '''
                        SELECT MAX(created_at) AS ts
                        FROM detection_events
                        WHERE workspace_id = %s::uuid
                        ''',
                        (workspace_id,),
                    ).fetchone()
                    canonical_last_detection_at = _parse_ts((canonical_last_detection_row or {}).get('ts') if isinstance(canonical_last_detection_row, dict) else None)
                    telemetry_candidates: list[tuple[datetime, str]] = []
                    coverage_telemetry_candidates: list[datetime] = []
                    receipts_reporting_systems = 0
                    for receipt_ts in live_coverage_receipts_by_system.values():
                        if int((now - receipt_ts).total_seconds()) <= telemetry_window_seconds:
                            receipts_reporting_systems += 1
                    logger.info(
                        'monitoring_runtime_coverage_receipts workspace_id=%s coverage_telemetry_persisted_count=%s receipts_reporting_systems=%s',
                        workspace_id,
                        live_coverage_receipts_persisted_count,
                        receipts_reporting_systems,
                    )
                    for row in enabled_rows:
                        system_id = str(row.get('id') or '').strip()
                        coverage_primary_ts = _parse_ts(row.get('last_coverage_telemetry_at'))
                        coverage_receipt_ts = live_coverage_receipts_by_system.get(system_id)
                        coverage_ts = coverage_primary_ts
                        if coverage_receipt_ts is not None and (coverage_ts is None or coverage_receipt_ts > coverage_ts):
                            coverage_ts = coverage_receipt_ts
                        target_event_ts = _parse_ts(row.get('last_event_at'))
                        if coverage_ts is not None:
                            coverage_telemetry_candidates.append(coverage_ts)
                            telemetry_candidates.append((coverage_ts, 'coverage'))
                        if target_event_ts is not None:
                            telemetry_candidates.append((target_event_ts, 'target_event'))
                    telemetry_candidates.sort(key=lambda item: item[0], reverse=True)
                    legacy_last_coverage_telemetry_at = max(coverage_telemetry_candidates) if coverage_telemetry_candidates else live_coverage_receipts_workspace_latest
                    legacy_last_telemetry_at = telemetry_candidates[0][0] if telemetry_candidates else None
                    legacy_telemetry_kind = telemetry_candidates[0][1] if telemetry_candidates else None
                    last_coverage_telemetry_at = live_coverage_receipts_workspace_latest or legacy_last_coverage_telemetry_at
                    # evm_rpc/rpc_polling rows in telemetry_events are coverage polls.
                    # When monitoring_event_receipts and monitored_systems haven't written a
                    # coverage timestamp yet, fall back to canonical_last_telemetry_at so
                    # coverage_fresh is accurate and freshness_status is set correctly.
                    if canonical_last_telemetry_at is not None:
                        if last_coverage_telemetry_at is None or canonical_last_telemetry_at > last_coverage_telemetry_at:
                            last_coverage_telemetry_at = canonical_last_telemetry_at
                    last_telemetry_at = canonical_last_telemetry_at or legacy_last_telemetry_at
                    # 'coverage' is the recognized kind in build_workspace_monitoring_summary;
                    # evm_rpc/rpc_polling polling events are coverage telemetry by definition.
                    telemetry_kind = (
                        'coverage' if canonical_last_telemetry_at is not None
                        else (legacy_telemetry_kind if legacy_last_telemetry_at is not None else None)
                    )
                    latest_target_coverage_rows = connection.execute(
                        '''
                        SELECT DISTINCT ON (target_id)
                            target_id,
                            coverage_status,
                            last_telemetry_at,
                            evidence_source,
                            computed_at,
                            metadata
                        FROM target_coverage_records
                        WHERE workspace_id = %s::uuid
                        ORDER BY target_id, computed_at DESC
                        ''',
                        (workspace_id,),
                    ).fetchall()
                    coverage_by_target = {
                        str((row or {}).get('target_id') or ''): dict(row)
                        for row in (latest_target_coverage_rows or [])
                        if str((row or {}).get('target_id') or '').strip()
                    }
                    canonical_reporting_event_rows = connection.execute(
                        '''
                        SELECT DISTINCT te.target_id
                        FROM telemetry_events te
                        JOIN targets t
                          ON t.workspace_id = te.workspace_id
                         AND t.id = te.target_id
                         AND COALESCE(t.enabled, FALSE) = TRUE
                         AND t.deleted_at IS NULL
                        JOIN monitoring_configs mc
                          ON mc.workspace_id = t.workspace_id
                         AND mc.target_id = t.id
                         AND COALESCE(mc.enabled, FALSE) = TRUE
                        WHERE te.workspace_id = %s::uuid
                          AND te.ingested_at >= %s
                        ''',
                        (workspace_id, now - timedelta(seconds=telemetry_window_seconds)),
                    ).fetchall()
                    canonical_reporting_targets_from_events: set[str] = {
                        str((row or {}).get('target_id') or '').strip()
                        for row in (canonical_reporting_event_rows or [])
                        if str((row or {}).get('target_id') or '').strip()
                    }
                    canonical_reporting_coverage_rows = connection.execute(
                        '''
                        WITH latest_coverage AS (
                            SELECT DISTINCT ON (tcr.target_id)
                                tcr.target_id,
                                tcr.metadata,
                                tcr.computed_at
                            FROM target_coverage_records tcr
                            JOIN targets t
                              ON t.workspace_id = tcr.workspace_id
                             AND t.id = tcr.target_id
                             AND COALESCE(t.enabled, FALSE) = TRUE
                             AND t.deleted_at IS NULL
                            JOIN monitoring_configs mc
                              ON mc.workspace_id = t.workspace_id
                             AND mc.target_id = t.id
                             AND COALESCE(mc.enabled, FALSE) = TRUE
                            WHERE tcr.workspace_id = %s::uuid
                              AND tcr.coverage_status = 'reporting'
                              AND tcr.last_telemetry_at IS NOT NULL
                            ORDER BY tcr.target_id, tcr.computed_at DESC
                        )
                        SELECT lc.target_id
                        FROM latest_coverage lc
                        JOIN telemetry_events te
                          ON te.workspace_id = %s::uuid
                         AND te.target_id = lc.target_id
                         AND te.id::text = (lc.metadata->'telemetry_basis'->>'event_id')
                        WHERE lc.computed_at >= %s
                          AND COALESCE(lc.metadata->'telemetry_basis'->>'kind', '') = 'telemetry_event'
                          AND COALESCE(lc.metadata->'telemetry_basis'->>'event_id', '') <> ''
                        ''',
                        (workspace_id, workspace_id, now - timedelta(seconds=telemetry_window_seconds)),
                    ).fetchall()
                    canonical_reporting_targets_from_coverage: set[str] = {
                        str((row or {}).get('target_id') or '').strip()
                        for row in (canonical_reporting_coverage_rows or [])
                        if str((row or {}).get('target_id') or '').strip()
                    }
                    canonical_reporting_target_ids = canonical_reporting_targets_from_events | canonical_reporting_targets_from_coverage
                    canonical_reporting_systems = int(len(canonical_reporting_target_ids))
                    # Detect contradiction: monitored_system rows exist visually but no telemetry-based
                    # reporting exists. This means targets are configured but the worker hasn't polled them.
                    _loose_target_rows_flag = bool(enabled_system_count > 0 and canonical_reporting_systems == 0)
                    target_reporting_without_telemetry_count = 0
                    for target_id, coverage_row in coverage_by_target.items():
                        if target_id in canonical_reporting_target_ids:
                            continue
                        if str((coverage_row or {}).get('coverage_status') or '').strip().lower() != 'reporting':
                            continue
                        # Skip disabled or deleted targets — only active targets constitute a contradiction.
                        # Old coverage records from previously enabled targets (e.g., an old
                        # ethereum-mainnet target superseded by a new base target) must not
                        # degrade status when the active target has fresh telemetry.
                        if target_id not in healthy_enabled_target_ids:
                            continue
                        # Skip stale coverage records whose last_telemetry_at is outside the
                        # freshness window. A target that reported on an old chain (chain_id=1)
                        # but now has no fresh telemetry is inactive, not a live contradiction.
                        _cov_last_telem = _parse_ts(coverage_row.get('last_telemetry_at'))
                        if _cov_last_telem is None or int((now - _cov_last_telem).total_seconds()) > telemetry_window_seconds:
                            continue
                        target_reporting_without_telemetry_count += 1
                    legacy_row_reporting_systems = sum(
                        1 for row in enabled_rows
                        if _parse_ts(row.get('last_coverage_telemetry_at')) is not None
                        and int((now - _parse_ts(row.get('last_coverage_telemetry_at'))).total_seconds()) <= telemetry_window_seconds
                    )
                    effective_reporting_systems = canonical_reporting_systems or legacy_row_reporting_systems or receipts_reporting_systems
                    reporting_systems = effective_reporting_systems
                    coverage_heartbeat_count = int(reporting_systems)
                    real_event_count = int(recent_real_event_count)
                    raw_recent_evidence_state = (
                        str((latest_detection_metadata or {}).get('evidence_state') or latest_detection_payload.get('evidence_state') or 'missing')
                        if isinstance(latest_detection_payload, dict)
                        else 'missing'
                    )
                    effective_recent_evidence_state = (
                        'no_evidence'
                        if coverage_heartbeat_count > 0 and real_event_count <= 0
                        else raw_recent_evidence_state
                    )
                    recent_evidence_reason_code = 'coverage_only_no_events' if coverage_heartbeat_count > 0 and real_event_count <= 0 else None
                    logger.info(
                        'monitoring_reporting_systems workspace_id=%s reporting_systems=%s status_reason=%s',
                        workspace_id,
                        reporting_systems,
                        f'fresh_coverage_window_{telemetry_window_seconds}s',
                    )
                    # Include legacy-path alerts (detection_id + detection_evidence) in the chain count
                    chain_open_alerts_count = max(
                        int((open_alerts or {}).get('c') or 0),
                        int((legacy_open_alerts_row or {}).get('c') or 0),
                    )
                    chain_open_incidents_count = int((open_incidents or {}).get('c') or 0)
                    _mark_query_checkpoint('select_proof_chain_last_detection')
                    try:
                        linked_detection_row = connection.execute(
                                f'''
                            SELECT MAX(COALESCE(de.created_at, te.ingested_at)) AS detected_at
                            FROM alerts a
                            JOIN detection_events de
                              ON de.workspace_id = a.workspace_id
                             AND de.id = a.detection_event_id
                            JOIN telemetry_events te
                              ON te.workspace_id = de.workspace_id
                             AND te.id = de.telemetry_event_id
                            WHERE a.status IN ('open','acknowledged','investigating')
                              {'AND a.workspace_id = %s' if workspace_id else ''}
                            ''',
                            scoped_params,
                        ).fetchone()
                    except Exception as exc:
                        _record_optional_query_failure(
                            exc=exc,
                            checkpoint_label='select_proof_chain_last_detection',
                            impacted_fields=['last_detection_at'],
                            reason_code='optional_table_unavailable',
                            error_code='runtime_optional_query_failed',
                        )
                        linked_detection_row = None
                    linked_detection_row_payload = linked_detection_row if isinstance(linked_detection_row, dict) else {}
                    linked_detection_timestamp_reported = 'detected_at' in linked_detection_row_payload
                    linked_last_detection_at = _parse_ts(linked_detection_row_payload.get('detected_at'))
                    if linked_last_detection_at is not None:
                        latest_detection_at = linked_last_detection_at
                    legacy_proof_chain_gaps_count = 0
                    try:
                        legacy_chain_row = connection.execute(
                            f'''
                            SELECT COUNT(*) AS c
                            FROM alerts a
                            WHERE a.status IN ('open','acknowledged','investigating')
                              AND {'a.workspace_id = %s AND' if workspace_id else ''}
                              (
                                a.detection_id IS NULL
                                OR NOT EXISTS (
                                    SELECT 1
                                    FROM detections d
                                    WHERE d.workspace_id = a.workspace_id
                                      AND d.id = a.detection_id
                                      AND EXISTS (
                                          SELECT 1
                                          FROM detection_evidence dev
                                          WHERE dev.workspace_id = d.workspace_id
                                            AND dev.detection_id = d.id
                                      )
                                )
                              )
                            ''',
                            scoped_params,
                        ).fetchone()
                        legacy_proof_chain_gaps_count = int((legacy_chain_row or {}).get('c') or 0)
                    except Exception:
                        legacy_proof_chain_gaps_count = 0
                    canonical_incident_timeline_gap_count = 0
                    try:
                        canonical_incident_timeline_gap_row = connection.execute(
                            f'''
                            SELECT COUNT(*) AS c
                            FROM incidents i
                            WHERE i.status IN ('open','acknowledged')
                              AND {'i.workspace_id = %s AND' if workspace_id else ''}
                              NOT EXISTS (
                                  SELECT 1
                                  FROM incident_timeline it
                                  WHERE it.workspace_id = i.workspace_id
                                    AND it.incident_id = i.id
                              )
                            ''',
                            scoped_params,
                        ).fetchone()
                        canonical_incident_timeline_gap_count = int((canonical_incident_timeline_gap_row or {}).get('c') or 0)
                    except Exception:
                        canonical_incident_timeline_gap_count = 0
                    canonical_governance_alert_gap_count = 0
                    try:
                        canonical_governance_alert_gap_row = connection.execute(
                            f'''
                            SELECT COUNT(*) AS c
                            FROM governance_actions ga
                            WHERE ga.alert_id IS NOT NULL
                              AND {'ga.workspace_id = %s AND' if workspace_id else ''}
                              NOT EXISTS (
                                  SELECT 1
                                  FROM alerts a
                                  WHERE a.workspace_id = ga.workspace_id
                                    AND a.id = ga.alert_id
                              )
                            ''',
                            scoped_params,
                        ).fetchone()
                        canonical_governance_alert_gap_count = int((canonical_governance_alert_gap_row or {}).get('c') or 0)
                    except Exception:
                        canonical_governance_alert_gap_count = 0
                    canonical_governance_incident_gap_count = 0
                    try:
                        canonical_governance_incident_gap_row = connection.execute(
                            f'''
                            SELECT COUNT(*) AS c
                            FROM governance_actions ga
                            WHERE ga.incident_id IS NOT NULL
                              AND {'ga.workspace_id = %s AND' if workspace_id else ''}
                              NOT EXISTS (
                                  SELECT 1
                                  FROM incidents i
                                  WHERE i.workspace_id = ga.workspace_id
                                    AND i.id = ga.incident_id
                              )
                            ''',
                            scoped_params,
                        ).fetchone()
                        canonical_governance_incident_gap_count = int((canonical_governance_incident_gap_row or {}).get('c') or 0)
                    except Exception:
                        canonical_governance_incident_gap_count = 0
                    proof_chain_missing_reason_codes: list[str] = []
                    if raw_open_alerts_count > chain_open_alerts_count:
                        proof_chain_missing_reason_codes.append('alerts_without_canonical_detection_event')
                    if raw_open_incidents_count > chain_open_incidents_count:
                        proof_chain_missing_reason_codes.append('incidents_without_proof_chain_alert')
                    # Only fire integrity gap checks when canonical evidence pipeline has real data.
                    # Without canonical data, gap queries may reflect legacy rows or mock artifacts.
                    _canonical_proof_check_enabled = bool(
                        canonical_reporting_systems > 0
                        or canonical_last_telemetry_at is not None
                        or canonical_last_detection_at is not None
                    )
                    if _canonical_proof_check_enabled and canonical_incident_timeline_gap_count > 0:
                        proof_chain_missing_reason_codes.append('incidents_without_timeline_linkage')
                    if _canonical_proof_check_enabled and canonical_governance_alert_gap_count > 0:
                        proof_chain_missing_reason_codes.append('governance_actions_without_alert_linkage')
                    if _canonical_proof_check_enabled and canonical_governance_incident_gap_count > 0:
                        proof_chain_missing_reason_codes.append('governance_actions_without_incident_linkage')
                    if chain_open_alerts_count > 0 and linked_detection_timestamp_reported and latest_detection_at is None:
                        proof_chain_missing_reason_codes.append('missing_linked_detection_timestamp')
                    proof_chain_status = 'incomplete' if proof_chain_missing_reason_codes else 'complete'
                    proof_chain_correlation_id = str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            (
                                f'monitoring-proof-chain:{workspace_id or "global"}:{now.date().isoformat()}:'
                                f'{chain_open_alerts_count}:{chain_open_incidents_count}:'
                                f'{latest_detection_at.isoformat() if latest_detection_at else "none"}:'
                                f'{",".join(sorted(proof_chain_missing_reason_codes)) or "ok"}'
                            ),
                        )
                    )
                    coverage_fresh = bool(
                        last_coverage_telemetry_at is not None
                        and int((now - last_coverage_telemetry_at).total_seconds()) <= telemetry_window_seconds
                    )
                    provider_reachable = bool(
                        (claim_validator.get('checks') or {}).get('provider_reachable_or_backfilling')
                        or str(health.get('source_type') or '').strip().lower() in {'polling', 'websocket', 'rpc_backfill'}
                    )
                    provider_degraded_or_unreachable = bool(
                        health.get('last_error')
                        or health.get('degraded')
                        or degraded_reason
                        or stale_heartbeat
                        or int((broken_targets or {}).get('c') or 0) > 0
                        or not provider_reachable
                    )
                    evidence_source_live = bool(
                        str(health.get('ingestion_mode') or '').strip().lower() not in {'demo', 'simulator', 'replay'}
                        and not provider_degraded_or_unreachable
                        and coverage_fresh
                        and reporting_systems > 0
                    )
                    source_of_evidence = (
                        'simulator'
                        if str(health.get('ingestion_mode') or '').strip().lower() in {'demo', 'simulator'}
                        else ('live' if evidence_source_live and coverage_fresh else 'replay_or_none')
                    )
                    # Guard invariant: coverage_status != 'reporting' or coverage_telemetry_at is None should never be
                    # interpreted as live target telemetry evidence.
                    if source_of_evidence == 'live':
                        telemetry_kind = 'coverage'
                    evidence_source = 'live' if source_of_evidence == 'live' else ('simulator' if source_of_evidence == 'simulator' else ('replay' if canonical_last_telemetry_at else 'none'))
                    # When the proof chain is complete and canonical live telemetry is fresh,
                    # the worker has demonstrably ingested real provider data even if the
                    # reporting_systems JOIN returned zero rows (a transient metadata race).
                    # Override evidence_source to 'live' so downstream status logic does not
                    # fall through to the idle/replay path and falsely degrade the runtime.
                    if (
                        evidence_source != 'live'
                        and source_of_evidence != 'simulator'
                        and proof_chain_status == 'complete'
                        and canonical_last_telemetry_at is not None
                        and coverage_fresh
                        and int((now - canonical_last_telemetry_at).total_seconds()) <= telemetry_window_seconds
                        and str(health.get('ingestion_mode') or '').strip().lower() not in {'demo', 'simulator', 'replay'}
                        and not provider_degraded_or_unreachable
                    ):
                        evidence_source = 'live'
                        source_of_evidence = 'live'
                        telemetry_kind = 'coverage'
                        reporting_systems = max(reporting_systems, 1)
                        canonical_reporting_systems = max(canonical_reporting_systems, 1)
                    latest_provider_rows = connection.execute(
                        '''
                        SELECT DISTINCT ON (provider_type, COALESCE(target_id, '00000000-0000-0000-0000-000000000000'::uuid))
                            provider_type,
                            target_id,
                            status,
                            checked_at,
                            latency_ms,
                            error_message,
                            evidence_source,
                            metadata
                        FROM provider_health_records
                        WHERE workspace_id = %s::uuid
                        ORDER BY provider_type, COALESCE(target_id, '00000000-0000-0000-0000-000000000000'::uuid), checked_at DESC
                        ''',
                        (workspace_id,),
                    ).fetchall()
                break
            except psycopg.Error as _post_agg_exc:
                _post_agg_exc_lower = str(_post_agg_exc).lower()
                if _post_agg_attempt == 0 and (
                    'connection is closed' in _post_agg_exc_lower
                    or 'connection already closed' in _post_agg_exc_lower
                    or 'the connection was closed' in _post_agg_exc_lower
                ):
                    logger.warning(
                        'monitoring_runtime_status_connection_closed_retry workspace_id=%s workspace_slug=%s checkpoint=canonical_post_aggregation stage=canonical_last_poll_row',
                        workspace_id,
                        workspace_slug,
                    )
                    continue
                raise
        logger.info('runtime_status_query_stage_success workspace_id=%s stage=post_aggregation_canonical', workspace_id)
        provider_health = [dict(row) for row in (latest_provider_rows or [])]
        target_coverage = [dict(row) for row in coverage_by_target.values()]
        logger.info(
            'monitoring_runtime_evidence_selection workspace_id=%s chosen_evidence_source=%s source_of_evidence=%s reporting_systems=%s receipts_reporting_systems=%s',
            workspace_id,
            evidence_source,
            source_of_evidence,
            reporting_systems,
            receipts_reporting_systems,
        )
        downgrade_reason_tokens: list[str] = []
        if not coverage_fresh:
            downgrade_reason_tokens.append('no_fresh_coverage_telemetry')
        if not evidence_source_live:
            downgrade_reason_tokens.append('evidence_source_not_live')
        if reporting_systems <= 0:
            downgrade_reason_tokens.append('no_reporting_systems_from_coverage')
        if provider_degraded_or_unreachable:
            downgrade_reason_tokens.append('provider_degraded_or_unreachable')
        if downgrade_reason_tokens:
            logger.info(
                'monitoring_runtime_live_downgrade workspace_id=%s reasons=%s',
                workspace_id,
                ','.join(downgrade_reason_tokens),
            )
        logger.info(
            'evidence_source_selected workspace_id=%s source=%s downgrade_reasons=%s runner_alive=%s stale_heartbeat=%s',
            workspace_id,
            evidence_source,
            ','.join(downgrade_reason_tokens) or 'none',
            runner_alive,
            stale_heartbeat,
        )
        configuration_diagnostics = _workspace_configuration_diagnostics(
            valid_protected_asset_count=valid_protected_asset_count,
            linked_monitored_system_count=linked_monitored_system_count,
            persisted_enabled_config_count=persisted_enabled_config_count,
            valid_target_system_link_count=valid_target_system_link_count,
        )
        workspace_configured = bool(configuration_diagnostics.get('workspace_configured'))
        configuration_reason = configuration_diagnostics.get('configuration_reason')
        primary_reason = configuration_reason
        configuration_reason_codes = list(configuration_diagnostics.get('reason_codes') or [])
        # When canonical telemetry_events has recent live rows but workspace_configured
        # is False (e.g., stale asset linkage in monitored_systems), override to prevent
        # the OFFLINE status. Telemetry being persisted proves monitoring is running;
        # this surfaces as LIMITED COVERAGE rather than OFFLINE.
        if not workspace_configured and canonical_last_telemetry_at is not None:
            _telem_age_for_config = int((now - canonical_last_telemetry_at).total_seconds())
            if _telem_age_for_config <= telemetry_window_seconds:
                workspace_configured = True
                if 'telemetry_active_configuration_incomplete' not in configuration_reason_codes:
                    configuration_reason_codes.append('telemetry_active_configuration_incomplete')
                if configuration_reason is None:
                    configuration_reason = 'telemetry_active_configuration_incomplete'
                primary_reason = configuration_reason
        if not workspace_configured:
            logger.warning(
                'monitoring_workspace_configuration_diagnostics workspace_id=%s workspace_slug=%s reason_codes=%s diagnostics=%s',
                workspace_id,
                workspace_slug,
                ','.join(configuration_diagnostics.get('reason_codes') or ['workspace_not_configured']),
                configuration_diagnostics,
            )
            for _unconfigured_field in ('protected_assets', 'configured_systems', 'reporting_systems', 'last_poll_at', 'last_heartbeat_at', 'last_telemetry_at'):
                _append_field_reason(_unconfigured_field, 'unconfigured_workspace')
        monitoring_mode_raw = str(health.get('mode') or '').strip().lower()
        degraded_signal = provider_degraded_or_unreachable
        coverage_only_warning = dict(_WORKSPACE_COVERAGE_ONLY_STREAK.get(str(workspace_id), {}))
        coverage_only_warning_state = str(coverage_only_warning.get('state') or '').strip()
        coverage_only_warning_active = bool(coverage_only_warning.get('active') and coverage_only_warning_state == 'coverage_only_persistent_no_evidence')
        if not workspace_configured:
            logger.info(
                'monitoring_runtime_status_branch workspace_id=%s workspace_slug=%s branch=offline_unconfigured configuration_reason=%s',
                workspace_id,
                workspace_slug,
                configuration_reason,
            )
            runtime_status_summary = 'offline'
        elif evidence_source != 'live':
            runtime_status_summary = 'idle'
        elif reporting_systems <= 0 or not coverage_fresh:
            runtime_status_summary = 'degraded'
        elif degraded_signal:
            runtime_status_summary = 'degraded'
        else:
            runtime_status_summary = 'healthy'
        monitoring_mode = (
            'simulator'
            if evidence_source == 'simulator'
            else ('offline' if not workspace_configured else ('hybrid' if monitoring_mode_raw == 'hybrid' else 'live'))
        )
        telemetry_countable = bool(workspace_configured and reporting_systems > 0 and evidence_source == 'live' and last_telemetry_at is not None)
        poll_window_seconds = max(120, MONITOR_POLL_INTERVAL_SECONDS * 3)
        poll_freshness_status = (
            'fresh' if last_poll_at and int((now - last_poll_at).total_seconds()) <= poll_window_seconds
            else ('stale' if last_poll_at else 'unavailable')
        )
        if not workspace_configured:
            runtime_status_reason = (
                f'workspace_configuration_invalid:{primary_reason}'
                if primary_reason
                else 'workspace_not_configured'
            )
        else:
            # Suppress the live-coverage-gap reason entirely when rpc_polling coverage is
            # fresh. When it is NOT fresh, pick a TRUTHFUL reason: only blame EVM_RPC_URL
            # ('no_fresh_live_coverage_telemetry') when the stable RPC polling worker is
            # actually stale/missing AND provider checks are failing. A fresh heartbeat/poll
            # means the worker is polling the chain — with realtime paused that is
            # "Realtime paused; stable polling active", never an RPC connectivity warning.
            runtime_status_reason = degraded_reason or (
                None if coverage_fresh else live_coverage_gap_reason(
                    stable_polling_active=not stale_heartbeat,
                    realtime_is_enabled=_reason_realtime_enabled,
                    provider_failing=provider_degraded_or_unreachable,
                )
            )
        if workspace_configured and reporting_systems <= 0 and evidence_source == 'live':
            runtime_status_summary = 'degraded'
            if not runtime_status_reason:
                runtime_status_reason = 'no_reporting_systems'
            if 'no_reporting_systems' not in proof_chain_missing_reason_codes:
                proof_chain_missing_reason_codes.append('no_reporting_systems')
        if workspace_configured and last_telemetry_at is None:
            if runtime_status_summary == 'healthy':
                runtime_status_summary = 'degraded'
            runtime_status_reason = runtime_status_reason or 'telemetry_timestamp_unavailable'
            # Only add to proof chain when live evidence is claimed — for idle/offline workspaces
            # missing telemetry is expected and should not drive proof_chain_link_missing.
            if evidence_source == 'live' and 'telemetry_timestamp_unavailable' not in proof_chain_missing_reason_codes:
                proof_chain_missing_reason_codes.append('telemetry_timestamp_unavailable')
        if workspace_configured and open_alerts_without_evidence_count > 0:
            proof_chain_status = 'incomplete'
            runtime_status_summary = 'degraded'
            runtime_status_reason = 'alerts_without_canonical_detection_event'
            if 'alerts_without_canonical_detection_event' not in proof_chain_missing_reason_codes:
                proof_chain_missing_reason_codes.append('alerts_without_canonical_detection_event')
        if workspace_configured and proof_chain_missing_reason_codes:
            runtime_status_summary = 'degraded'
            if not runtime_status_reason:
                runtime_status_reason = proof_chain_missing_reason_codes[0]
        if proof_chain_status == 'complete' and detection_pipeline_checkpoint_at is None and latest_detection_at is not None:
            detection_pipeline_checkpoint_at = latest_detection_at
        if workspace_configured and coverage_only_warning_active:
            runtime_status_summary = 'degraded'
            runtime_status_reason = 'coverage_only_persistent_no_evidence'
        if workspace_configured and runtime_error_code and not runtime_status_reason:
            runtime_status_reason = 'runtime_status_degraded:partial_query_failure'
        # Promote 'healthy' → 'live' once the full telemetry → detection → alert →
        # incident → response_action → evidence chain is verified with no proof-chain
        # gaps and no orphan alerts/incidents without detection linkage.
        if (
            runtime_status_summary == 'healthy'
            and workspace_configured
            and reporting_systems > 0
            and last_telemetry_at is not None
            and (canonical_last_detection_at is not None or latest_detection_at is not None)
            and int(alerts_count) > 0
            and int(incidents_count) > 0
            and int(response_actions_count) > 0
            and int(evidence_count) > 0
            and open_alerts_without_evidence_count == 0
            and incidents_without_alert_count == 0
            and not proof_chain_missing_reason_codes
        ):
            runtime_status_summary = 'live'
        missing_telemetry_only = bool(
            workspace_configured
            and enabled_system_count > 0
            and protected_assets_count > 0
            and reporting_systems > 0
            and last_poll_at is None
            and last_heartbeat is None
            and last_telemetry_at is None
            and not query_failure_detected
            and not schema_drift_detected
        )
        summary = build_workspace_monitoring_summary(
            now=now,
            workspace_configured=workspace_configured,
            configuration_reason_codes=list(configuration_reason_codes),
            query_failure_detected=query_failure_detected,
            schema_drift_detected=schema_drift_detected,
            missing_telemetry_only=missing_telemetry_only,
            monitoring_mode=monitoring_mode,
            runtime_status=runtime_status_summary,
            configured_systems=int(enabled_system_count),
            monitored_systems_count=int(system_count),
            reporting_systems=int(reporting_systems),
            protected_assets=int(protected_assets_count),
            last_poll_at=last_poll_at,
            last_heartbeat_at=canonical_last_heartbeat_at,
            last_telemetry_at=canonical_last_telemetry_at,
            last_coverage_telemetry_at=last_coverage_telemetry_at,
            telemetry_kind=telemetry_kind,
            last_detection_at=canonical_last_detection_at or latest_detection_at,
            evidence_source=evidence_source,
            status_reason=runtime_status_reason,
            configuration_reason=configuration_reason,
            valid_protected_asset_count=valid_protected_asset_count,
            linked_monitored_system_count=linked_monitored_system_count,
            persisted_enabled_config_count=persisted_enabled_config_count,
            valid_target_system_link_count=valid_target_system_link_count,
            telemetry_window_seconds=telemetry_window_seconds,
            active_alerts_count=int(max(
                int((open_alerts or {}).get('c') or 0),
                int((legacy_open_alerts_row or {}).get('c') or 0),
            )),
            alerts_without_detection_count=int(open_alerts_without_evidence_count),
            active_incidents_count=int((open_incidents or {}).get('c') or 0),
            raw_incidents_count=int(raw_open_incidents_count),
            response_actions_count=int(response_actions_count),
            evidence_packages_count=int(evidence_count),
            detections_count=int(detections_count),
            db_persistence_available=db_persistence_available,
            db_persistence_reason=db_persistence_reason,
        )
        summary['runtime_error_code'] = runtime_error_code
        summary['runtime_degraded_reason'] = runtime_degraded_reason
        if _loose_target_rows_flag:
            _cf = list(summary.get('contradiction_flags') or [])
            _gf = list(summary.get('guard_flags') or [])
            if 'target_rows_exist_without_reporting_systems' not in _cf:
                _cf.append('target_rows_exist_without_reporting_systems')
                summary['contradiction_flags'] = sorted(_cf)
            if 'target_rows_exist_without_reporting_systems' not in _gf:
                _gf.append('target_rows_exist_without_reporting_systems')
                summary['guard_flags'] = sorted(_gf)
        _pqf_reason = str(summary.get('status_reason', ''))
        if (
            runtime_degraded_reason == 'partial_query_failure'
            and _pqf_reason.startswith('guard:')
            and _pqf_reason != 'guard:live_proof_chain_incomplete'
        ):
            summary['status_reason'] = 'runtime_status_degraded:partial_query_failure'
        summary['field_reason_codes'] = dict(field_reason_codes)
        summary['configured_systems'] = int(enabled_system_count)
        summary['reporting_systems'] = int(reporting_systems)
        summary['monitoring_targets'] = int(raw_enabled_targets)
        summary['monitorable_enabled_targets'] = int(healthy_enabled_targets_count)
        summary['raw_enabled_targets'] = int(raw_enabled_targets)
        summary['evidence_source'] = evidence_source
        summary['telemetry_kind'] = telemetry_kind
        summary['confidence_status'] = str(summary.get('confidence') or 'unavailable')
        summary['freshness_status'] = str(summary.get('telemetry_freshness') or 'unavailable')
        summary['configuration_reason'] = configuration_reason
        summary['configuration_reason_codes'] = list(configuration_reason_codes)
        summary['monitoring_mode'] = monitoring_mode
        summary['valid_protected_assets'] = int(valid_protected_asset_count)
        summary['coverage_only_warning'] = {
            'state': coverage_only_warning_state or None,
            'active': coverage_only_warning_active,
            'cycle_count': int(coverage_only_warning.get('cycle_count') or 0),
            'duration_seconds': int(coverage_only_warning.get('duration_seconds') or 0),
            'threshold_seconds': int(coverage_only_warning.get('threshold_seconds') or MONITORING_COVERAGE_ONLY_WARNING_SECONDS),
            'first_seen_at': coverage_only_warning.get('first_seen_at'),
            'last_cycle_at': coverage_only_warning.get('last_cycle_at'),
        }
        summary['coverage_state'] = {
            'configured_systems': int(enabled_system_count),
            'monitored_systems_count': int(system_count),
            'reporting_systems': int(reporting_systems),
            'reporting_systems_count': int(reporting_systems),
            'protected_assets_count': int(protected_assets_count),
            'telemetry_freshness': str(summary.get('telemetry_freshness') or 'unavailable'),
            'confidence': str(summary.get('confidence') or 'unavailable'),
            'evidence_source_summary': str(summary.get('evidence_source_summary') or 'none'),
        }
        if last_telemetry_at is None:
            summary['telemetry_freshness'] = 'unavailable'
            summary['coverage_state']['telemetry_freshness'] = 'unavailable'
        summary['linked_monitored_system_count'] = int(linked_monitored_system_count)
        summary['linked_monitored_systems'] = int(linked_monitored_system_count)
        summary['enabled_configs'] = int(persisted_enabled_config_count)
        summary['valid_link_count'] = int(valid_target_system_link_count)
        summary['source_of_evidence'] = source_of_evidence
        summary['stale_heartbeat'] = stale_heartbeat
        summary['provider_degraded_flag'] = bool(provider_degraded_or_unreachable)
        summary['coverage_receipts_workspace_count'] = int(live_coverage_receipts_persisted_count)
        summary['coverage_receipts_last_at'] = live_coverage_receipts_workspace_latest.isoformat() if live_coverage_receipts_workspace_latest else None
        summary['last_coverage_telemetry_at'] = last_coverage_telemetry_at.isoformat() if last_coverage_telemetry_at else None
        summary['runtime_setup_chain'] = build_runtime_setup_chain(
            counters={
                'workspaces_count': 1 if workspace_configured else 0,
                'assets_count': int(protected_assets_count),
                'verified_assets_count': int(verified_assets_count),
                'targets_count': int(healthy_enabled_targets_count),
                'monitored_systems_count': int(system_count),
                'enabled_monitored_systems_count': int(enabled_system_count),
                'simulator_signals_count': 1 if evidence_source == 'simulator' else 0,
                'detections_count': int(detections_count),
                'alerts_count': int(alerts_count),
                'incidents_count': int(incidents_count),
                'response_actions_count': int(response_actions_count),
                'evidence_count': int(evidence_count),
            },
            timestamps={
                'last_heartbeat_at': summary.get('last_heartbeat_at'),
                'last_telemetry_at': summary.get('last_telemetry_at'),
            },
        )
        summary['next_required_action'] = resolve_next_required_action(summary.get('runtime_setup_chain'))
        # canonical_last_telemetry_at is already filtered to live telemetry_events in SQL,
        # so it is safe to use as the event-ingestion fallback regardless of evidence_source.
        # Gating on evidence_source == 'live' creates a circular dependency: reporting_systems
        # transiently zero → evidence_source='replay' → last_event_at=None → event_ingestion_missing.
        _continuity_last_event_at = recent_last_real_event_at or canonical_last_telemetry_at
        _continuity_last_detection_at = detection_pipeline_checkpoint_at or canonical_last_detection_at
        continuity_evaluation = evaluate_workspace_monitoring_continuity(
            now=now,
            workspace_configured=workspace_configured,
            worker_running=runner_alive,
            last_heartbeat_at=last_heartbeat,
            last_event_at=_continuity_last_event_at,
            last_detection_at=_continuity_last_detection_at,
            # Same forgiving stable-poll threshold the banner/card use, so the continuity
            # heartbeat check never emits heartbeat_stale for a healthy 5-minute cadence.
            heartbeat_ttl_seconds=_stable_poll_stale_threshold,
            telemetry_window_seconds=telemetry_window_seconds,
            detection_window_seconds=max(900, MONITOR_POLL_INTERVAL_SECONDS * 10),
        )
        summary.update(continuity_evaluation)
        continuity_contract_payload = summary.get('continuity_contract') if isinstance(summary.get('continuity_contract'), dict) else {}
        continuity_checks_payload = continuity_contract_payload.get('checks') if isinstance(continuity_contract_payload.get('checks'), dict) else {}
        continuity_breach_reasons: list[dict[str, Any]] = []
        for check_key, check_payload in continuity_checks_payload.items():
            if not isinstance(check_payload, dict) or bool(check_payload.get('pass')):
                continue
            check_state = str(check_payload.get('state') or 'missing').strip().lower() or 'missing'
            check_code = str(check_key).strip().lower()
            if check_code == 'telemetry_freshness':
                check_code = f'event_ingestion_{check_state}'
            elif check_code == 'detection_freshness':
                check_code = f'detection_pipeline_{check_state}'
            elif check_code == 'heartbeat_freshness':
                check_code = f'heartbeat_{check_state}'
            continuity_breach_reasons.append(
                {
                    'code': check_code,
                    'check': str(check_key).strip().lower(),
                    'state': check_state,
                    'age_seconds': check_payload.get('age_seconds'),
                    'threshold_seconds': check_payload.get('threshold_seconds'),
                    'reason': str(check_payload.get('label') or check_key).strip().lower(),
                }
            )
        if not continuity_breach_reasons:
            for continuity_reason_code in (summary.get('continuity_reason_codes') or []):
                normalized_code = str(continuity_reason_code).strip()
                if not normalized_code:
                    continue
                continuity_breach_reasons.append(
                    {
                        'code': normalized_code,
                        'check': normalized_code,
                        'state': 'failed',
                        'age_seconds': None,
                        'threshold_seconds': None,
                        'reason': normalized_code.replace('_', ' '),
                    }
                )
        summary['continuity_breach_reasons'] = continuity_breach_reasons
        summary['continuity_freshness_ages_seconds'] = {
            'heartbeat': summary.get('heartbeat_age_seconds'),
            'telemetry': summary.get('telemetry_age_seconds'),
            'event_ingestion': summary.get('event_ingestion_age_seconds'),
            'detection': summary.get('detection_age_seconds'),
            'detection_pipeline': summary.get('detection_pipeline_age_seconds'),
            'detection_eval': summary.get('detection_eval_age_seconds'),
        }
        summary['detection_age_seconds'] = summary.get('detection_eval_age_seconds')
        summary['detection_pipeline_age_seconds'] = summary.get('detection_eval_age_seconds')
        threshold_seconds = dict(summary.get('thresholds_seconds') or {})
        summary['heartbeat_threshold_seconds'] = summary.get('heartbeat_threshold_seconds') or threshold_seconds.get('heartbeat')
        summary['telemetry_threshold_seconds'] = summary.get('telemetry_threshold_seconds') or threshold_seconds.get('telemetry') or threshold_seconds.get('event_ingestion')
        summary['detection_threshold_seconds'] = summary.get('detection_threshold_seconds') or threshold_seconds.get('detection_eval')
        summary['continuity_thresholds_seconds'] = dict(summary.get('required_thresholds_seconds') or threshold_seconds)
        summary['continuity_configured_thresholds_seconds'] = dict(summary.get('continuity_thresholds_seconds') or {})
        continuity_contract = summary.get('continuity_contract') if isinstance(summary.get('continuity_contract'), dict) else {}
        continuity_checks = continuity_contract.get('checks') if isinstance(continuity_contract.get('checks'), dict) else {}
        summary['continuity_contract'] = {
            'pass': bool(summary.get('continuity_slo_pass') is True),
            'checks': continuity_checks,
        }
        continuity_slo_pass = bool(summary.get('continuity_slo_pass') is True)
        continuity_status = str(summary.get('continuity_status') or '').strip().lower()
        continuity_failed_checks = sorted(
            {
                str(item.get('code') or '').strip()
                for item in continuity_breach_reasons
                if isinstance(item, dict) and str(item.get('code') or '').strip()
            }
            | {
                str(code).strip()
                for code in (summary.get('continuity_reason_codes') or [])
                if str(code).strip()
            }
        )
        summary['worker_heartbeat_age_seconds'] = summary.get('heartbeat_age_seconds')
        summary['continuity_failed_checks'] = continuity_failed_checks
        # Coverage-only workspaces with fresh telemetry: exempt from event_ingestion_missing degradation.
        # coverage_fresh already proves live telemetry is flowing; requiring evidence_source == 'live'
        # here creates the same circular dependency as _continuity_last_event_at above.
        _coverage_continuity_exempt = bool(
            coverage_fresh
            and continuity_status == 'degraded'
            and all(
                r in {
                    'event_ingestion_missing', 'detection_pipeline_offline',
                    'detection_pipeline_missing', 'detection_pipeline_stale',
                    'continuity_slo_failed', 'continuity_not_evaluated',
                }
                for r in (summary.get('continuity_reason_codes') or [])
            )
        )
        if (
            runtime_status_summary != 'offline'
            and not continuity_slo_pass
            and continuity_status not in {'idle_no_telemetry', 'continuous_no_evidence'}
            and not _coverage_continuity_exempt
        ):
            continuity_reason_codes = [
                str(code)
                for code in (summary.get('continuity_reason_codes') or [])
                if str(code).strip()
            ]
            if not continuity_reason_codes:
                continuity_reason_codes = ['continuity_slo_failed']
            runtime_status_summary = 'degraded'
            monitoring_status = 'degraded'
            runtime_status = 'Degraded'
            summary['runtime_status'] = 'degraded'
            summary['monitoring_status'] = 'limited'
            summary['runtime_degraded_reason_codes'] = [
                'continuity_slo_failed',
                *continuity_reason_codes,
            ]
            runtime_degraded_reason = 'continuity_slo_failed'
            payload_reason_codes = [
                'continuity_slo_failed',
                *continuity_reason_codes,
            ]
            summary['runtime_status_reason_codes'] = payload_reason_codes
            degraded_reason = degraded_reason or (
                f"continuity_slo_failed:{continuity_reason_codes[0]}"
                if continuity_reason_codes
                else 'continuity_slo_failed'
            )
            if not runtime_status_reason:
                runtime_status_reason = (
                    f"runtime_status_degraded:continuity_slo_failed:{continuity_reason_codes[0]}"
                    if continuity_reason_codes
                    else 'runtime_status_degraded:continuity_slo_failed'
                )
                summary['status_reason'] = runtime_status_reason
            if continuity_status != 'degraded':
                summary['continuity_status'] = 'degraded'
        if coverage_only_warning_active:
            continuity_reason_codes = list(summary.get('continuity_reason_codes') or [])
            if 'coverage_only_persistent_no_evidence' not in continuity_reason_codes:
                continuity_reason_codes.append('coverage_only_persistent_no_evidence')
            summary['continuity_reason_codes'] = continuity_reason_codes
        runtime_last_telemetry_at = canonical_last_telemetry_at if canonical_runtime_truth_enabled else (canonical_last_telemetry_at or legacy_last_telemetry_at)
        # Always fall back to latest_detection_at (detections table) when canonical_last_detection_at is None.
        # Proof-chain detections land in detections, not detection_events, so canonical may be None
        # even when a real detection exists.
        runtime_last_detection_at = canonical_last_detection_at or latest_detection_at
        runtime_last_telemetry_source = canonical_last_telemetry_source if runtime_last_telemetry_at is not None else None
        runtime_last_detection_source = canonical_last_detection_source if runtime_last_detection_at is not None else None
        canonical_guard_noncanonical_timestamp = bool(
            canonical_runtime_truth_enabled
            and (
                (runtime_last_telemetry_at is not None and canonical_last_telemetry_at is not None and runtime_last_telemetry_at != canonical_last_telemetry_at)
                or (runtime_last_detection_at is not None and canonical_last_detection_at is not None and runtime_last_detection_at != canonical_last_detection_at)
            )
        )
        legacy_only_reporting_without_canonical = bool(
            canonical_reporting_systems > 0
            and canonical_last_telemetry_at is None
            and canonical_last_detection_at is None
        )
        target_reporting_without_telemetry_events = bool(
            target_reporting_without_telemetry_count > 0
        )
        live_evidence_without_canonical_events = bool(
            evidence_source == 'live'
            and canonical_reporting_systems > 0
            and canonical_last_telemetry_at is None
            and canonical_last_detection_at is None
            and last_coverage_telemetry_at is not None
        )
        healthy_without_reporting_systems = bool(runtime_status_summary == 'healthy' and reporting_systems <= 0)
        heartbeat_or_poll_without_telemetry_live_claim = bool(
            (last_heartbeat is not None or last_poll_at is not None)
            and canonical_last_telemetry_at is None
            and canonical_reporting_systems > 0
            and (runtime_status_summary == 'healthy' or evidence_source == 'live')
        )
        # Use startswith to handle source strings like 'telemetry_events.observed_at' / 'detection_events.created_at'
        telemetry_timestamp_noncanonical = bool(runtime_last_telemetry_at is not None and not str(runtime_last_telemetry_source or '').startswith('telemetry_events'))
        detection_timestamp_noncanonical = bool(runtime_last_detection_at is not None and not str(runtime_last_detection_source or '').startswith('detection_events'))

        summary_freshness_status = str(summary.get('telemetry_freshness') or '').strip().lower()
        summary_confidence_status = str(summary.get('confidence') or '').strip().lower()
        contradiction_conditions = (
            ('offline_with_live_telemetry_recently', runtime_status_summary == 'offline' and evidence_source == 'live' and coverage_fresh),
            ('reporting_systems_zero_with_healthy', healthy_without_reporting_systems),
            ('telemetry_unavailable_with_current_telemetry', summary_freshness_status == 'unavailable' and coverage_fresh),
            ('workspace_unconfigured_with_coverage', (not workspace_configured) and enabled_system_count > 0),
            ('evidence_source_none_with_high_confidence', evidence_source == 'none' and summary_confidence_status == 'high'),
            ('heartbeat_exists_while_poll_and_telemetry_null_ui_active_claim', last_heartbeat is not None and summary.get('last_poll_at') is None and summary.get('last_telemetry_at') is None and monitoring_status == 'active' and evidence_source == 'live'),
            ('telemetry_current_with_null_timestamp', coverage_fresh and last_telemetry_at is None),
            ('open_alerts_without_detection_evidence', workspace_configured and open_alerts_without_evidence_count > 0),
            ('alert_without_detection', workspace_configured and open_alerts_without_evidence_count > 0),
            ('proof_chain_link_missing', workspace_configured and bool(proof_chain_missing_reason_codes)),
            ('incident_without_alert', workspace_configured and incidents_without_alert_count > 0 and raw_open_incidents_count > int((open_incidents or {}).get('c') or 0)),
            ('response_action_without_incident', workspace_configured and response_actions_without_incident_count > 0),
            ('healthy_claim_with_reporting_systems_zero', runtime_status_summary == 'healthy' and reporting_systems <= 0),
            ('live_claim_with_no_telemetry', runtime_status_summary == 'live' and runtime_last_telemetry_at is None),
            (
                'telemetry_unavailable_live_claim_asserted',
                summary_freshness_status == 'unavailable' and evidence_source == 'live',
            ),
            (
                'simulator_evidence_rendered_as_live_provider',
                str(health.get('source_type') or '').strip().lower() in NON_LIVE_PROVIDER_SOURCE_TYPES and evidence_source == 'live',
            ),
            ('legacy_reporting_without_canonical_telemetry', legacy_only_reporting_without_canonical),
            ('target_reporting_without_telemetry_event_link', target_reporting_without_telemetry_events),
            ('live_evidence_without_live_events', live_evidence_without_canonical_events),
            ('heartbeat_or_poll_without_telemetry_live_claim', heartbeat_or_poll_without_telemetry_live_claim),
            ('last_telemetry_not_from_telemetry_events', telemetry_timestamp_noncanonical),
            ('last_detection_not_from_detection_events', detection_timestamp_noncanonical),
            ('asset_monitoring_attached_but_no_monitored_systems', protected_assets_count > 0 and enabled_system_count <= 0),
            (
                'asset_count_mismatch_runtime_vs_registry',
                int(protected_assets_count) > 0 and int(verified_assets_count) > 0 and int(protected_assets_count) != int(verified_assets_count),
            ),
            ('ui_protected_assets_positive_but_runtime_zero', protected_assets_count > 0 and runtime_status_summary == 'idle' and evidence_source == 'live'),
            ('ui_live_monitoring_claim_without_telemetry', monitoring_status == 'active' and last_telemetry_at is None and evidence_source == 'live'),
            ('ui_healthy_claim_with_zero_reporting_systems', runtime_status_summary == 'healthy' and reporting_systems <= 0),
        )
        runtime_contradiction_flags = [flag for flag, condition in contradiction_conditions if condition]
        if canonical_guard_noncanonical_timestamp:
            runtime_contradiction_flags.append('canonical_guard_noncanonical_timestamp')
        contradiction_flags = sorted(set(runtime_contradiction_flags))
        summary['contradiction_flags'] = contradiction_flags
        contradiction_banner_reason_map: dict[str, str] = {
            'asset_monitoring_attached_but_no_monitored_systems': 'Assets attached, but no monitored systems are running.',
            'ui_protected_assets_positive_but_runtime_zero': 'Protected assets are present, but runtime reports zero live coverage.',
            'ui_live_monitoring_claim_without_telemetry': 'Live monitoring is claimed, but telemetry is missing.',
            'ui_healthy_claim_with_zero_reporting_systems': 'Healthy status is claimed with zero reporting systems.',
        }
        summary['top_banner_reasons'] = [
            contradiction_banner_reason_map[flag]
            for flag in contradiction_flags
            if flag in contradiction_banner_reason_map
        ]
        contradiction_reason_overrides: dict[str, tuple[str, str]] = {
            'alert_without_detection': ('degraded', 'alerts_without_detection_evidence'),
            'incident_without_alert': ('degraded', 'runtime_contradiction_incident_without_alert'),
            'response_action_without_incident': ('degraded', 'runtime_contradiction_response_action_without_incident'),
            'healthy_claim_with_reporting_systems_zero': ('fail', 'runtime_contradiction_healthy_claim_with_reporting_systems_zero'),
            'live_claim_with_no_telemetry': ('fail', 'runtime_contradiction_live_claim_with_no_telemetry'),
            'telemetry_unavailable_live_claim_asserted': ('fail', 'runtime_contradiction_telemetry_unavailable_live_claim_asserted'),
            'simulator_evidence_rendered_as_live_provider': ('fail', 'runtime_contradiction_simulator_evidence_rendered_as_live_provider'),
            'legacy_reporting_without_canonical_telemetry': ('degraded', 'runtime_contradiction_legacy_reporting_without_canonical_telemetry'),
            'target_reporting_without_telemetry_event_link': ('fail', 'runtime_contradiction_target_reporting_without_telemetry_event_link'),
            'live_evidence_without_live_events': ('fail', 'runtime_contradiction_live_evidence_without_live_events'),
            'reporting_systems_zero_with_healthy': ('fail', 'runtime_contradiction_healthy_without_reporting_systems'),
            'heartbeat_or_poll_without_telemetry_live_claim': ('degraded', 'runtime_contradiction_heartbeat_or_poll_without_telemetry_live_claim'),
            'last_telemetry_not_from_telemetry_events': ('degraded', 'runtime_contradiction_last_telemetry_not_from_telemetry_events'),
            'last_detection_not_from_detection_events': ('degraded', 'runtime_contradiction_last_detection_not_from_detection_events'),
            'asset_monitoring_attached_but_no_monitored_systems': ('fail', 'runtime_contradiction_asset_monitoring_attached_but_no_monitored_systems'),
            'asset_count_mismatch_runtime_vs_registry': ('fail', 'runtime_contradiction_asset_count_mismatch_runtime_vs_registry'),
            'ui_protected_assets_positive_but_runtime_zero': ('degraded', 'runtime_contradiction_ui_protected_assets_positive_but_runtime_zero'),
            'ui_live_monitoring_claim_without_telemetry': ('fail', 'runtime_contradiction_ui_live_monitoring_claim_without_telemetry'),
            'ui_healthy_claim_with_zero_reporting_systems': ('fail', 'runtime_contradiction_ui_healthy_claim_with_zero_reporting_systems'),
        }
        contradiction_severity = 'healthy'
        contradiction_reason_token: str | None = None
        for flag in contradiction_flags:
            override = contradiction_reason_overrides.get(flag)
            if not override:
                continue
            severity, reason_token = override
            if contradiction_reason_token is None:
                contradiction_reason_token = reason_token
            if severity == 'fail':
                contradiction_severity = 'fail'
                break
            if contradiction_severity != 'fail':
                contradiction_severity = 'degraded'
        if contradiction_reason_token:
            runtime_status_reason = contradiction_reason_token
            summary['status_reason'] = runtime_status_reason
            runtime_reason_codes = [str(code).strip() for code in (summary.get('runtime_status_reason_codes') or []) if str(code).strip()]
            if contradiction_reason_token not in runtime_reason_codes:
                runtime_reason_codes.append(contradiction_reason_token)
            summary['runtime_status_reason_codes'] = runtime_reason_codes
        hard_contradictions = {
            'incident_without_alert',
            'response_action_without_incident',
            'telemetry_unavailable_live_claim_asserted',
            'simulator_evidence_rendered_as_live_provider',
            'asset_monitoring_attached_but_no_monitored_systems',
            'asset_count_mismatch_runtime_vs_registry',
            'healthy_claim_with_reporting_systems_zero',
            'live_claim_with_no_telemetry',
        }
        impossible_state_detected = any(
            flag in contradiction_flags
            for flag in (
                'offline_with_live_telemetry_recently',
                'reporting_systems_zero_with_healthy',
                'telemetry_unavailable_with_current_telemetry',
                'evidence_source_none_with_high_confidence',
                'heartbeat_exists_while_poll_and_telemetry_null_ui_active_claim',
            )
        )
        hard_contradiction_detected = bool(hard_contradictions.intersection(set(contradiction_flags)))
        if impossible_state_detected or contradiction_severity == 'fail' or hard_contradiction_detected:
            runtime_status_summary = 'offline'
            runtime_status = 'Offline'
            monitoring_status = 'offline'
            summary['runtime_status'] = 'offline'
            summary['monitoring_status'] = 'offline'
            runtime_status_reason = runtime_status_reason or 'impossible_contradiction_state'
            summary['status_reason'] = runtime_status_reason
            summary['next_required_action'] = 'resolve_runtime_contradictions'
        if canonical_guard_noncanonical_timestamp or contradiction_severity == 'degraded':
            runtime_status_summary = 'degraded'
            runtime_status = 'Degraded'
            monitoring_status = 'limited'
            summary['runtime_status'] = 'degraded'
            summary['monitoring_status'] = 'limited'
            runtime_status_reason = runtime_status_reason or 'canonical_guard_noncanonical_timestamp'
            summary['status_reason'] = runtime_status_reason
        strict_live_healthy_proof = bool(
            workspace_configured
            and evidence_source == 'live'
            and reporting_systems > 0
            and last_coverage_telemetry_at is not None
            and coverage_fresh
            and summary_freshness_status not in {'', 'unavailable'}
            and summary_confidence_status not in {'', 'unavailable'}
        )
        if runtime_status_summary == 'healthy' and not strict_live_healthy_proof:
            runtime_status_summary = 'degraded'
            summary['runtime_status'] = 'degraded'
            summary['monitoring_status'] = 'limited'
            if runtime_status_reason is None:
                # Only surface a coverage-gap reason when coverage is actually not fresh.
                # When coverage_fresh=True the worker is polling; the gap is a reporting_systems
                # JOIN miss, not an RPC connectivity problem. And even when coverage is not
                # fresh, only blame EVM_RPC_URL when stable polling is stale/missing AND
                # provider checks fail — a fresh heartbeat/poll with realtime paused is
                # "Realtime paused; stable polling active", not an RPC warning.
                if not coverage_fresh:
                    runtime_status_reason = live_coverage_gap_reason(
                        stable_polling_active=not stale_heartbeat,
                        realtime_is_enabled=_reason_realtime_enabled,
                        provider_failing=provider_degraded_or_unreachable,
                    )
                    summary['status_reason'] = runtime_status_reason
        if runtime_status_summary not in {'live', 'healthy', 'degraded', 'offline', 'fail', 'idle'}:
            runtime_status_summary = 'degraded' if workspace_configured else 'offline'
            summary['runtime_status'] = runtime_status_summary
            summary['monitoring_status'] = 'limited' if workspace_configured else 'offline'
            runtime_status_reason = runtime_status_reason or 'runtime_status_normalized_from_noncanonical_state'
            summary['status_reason'] = runtime_status_reason
        if runtime_status_summary == 'live':
            if not runtime_status_reason:
                runtime_status_reason = 'live_runtime_verified'
                summary['status_reason'] = runtime_status_reason
            summary['next_required_action'] = 'monitoring_live'
        if workspace_configured and runtime_status_summary == 'idle' and runtime_status_reason:
            logger.info(
                'monitoring_runtime_limited_coverage workspace_id=%s chosen_evidence_source=%s status_reason=%s reporting_systems=%s coverage_fresh=%s',
                workspace_id,
                evidence_source,
                runtime_status_reason,
                reporting_systems,
                coverage_fresh,
            )
        final_status_reason = runtime_status_reason
        logger.info(
            'monitoring_runtime_truth workspace_id=%s reporting_systems=%s configured_systems=%s evidence_source=%s last_coverage_telemetry_at=%s status_reason=%s',
            workspace_id,
            reporting_systems,
            enabled_system_count,
            evidence_source,
            last_coverage_telemetry_at.isoformat() if last_coverage_telemetry_at else None,
            runtime_status_reason or 'none',
        )
        if (
            poll_freshness_status == 'fresh'
            and not stale_heartbeat
            and runtime_status_summary in {'idle', 'degraded'}
        ):
            logger.info(
                'monitoring_runtime_downgrade workspace_id=%s runtime_status_summary=%s reporting_systems=%s status_reason=%s',
                workspace_id,
                runtime_status_summary,
                reporting_systems,
                final_status_reason or ','.join(downgrade_reason_tokens) or 'unknown',
            )
        legacy_diagnostics = {
            'legacy_last_telemetry_at': canonical_last_telemetry_at.isoformat() if canonical_last_telemetry_at else None,
            'legacy_last_coverage_telemetry_at': last_coverage_telemetry_at.isoformat() if last_coverage_telemetry_at else None,
            'legacy_reporting_systems': int(receipts_reporting_systems),
            'legacy_telemetry_kind': (
                telemetry_kind
                if telemetry_kind is not None
                else (
                    'coverage'
                    if evidence_source == 'live' and last_coverage_telemetry_at is not None
                    else ('detection' if canonical_last_detection_at is not None else None)
                )
            ),
            'runtime_status': runtime_status_summary,
            'reporting_systems': int(reporting_systems),
            'freshness_status': summary_freshness_status,
            'confidence_status': summary_confidence_status,
            'evidence_source': evidence_source,
            'last_telemetry_at': runtime_last_telemetry_at.isoformat() if runtime_last_telemetry_at else None,
            'last_detection_at': runtime_last_detection_at.isoformat() if runtime_last_detection_at else None,
            'last_telemetry_source': runtime_last_telemetry_source,
            'last_detection_source': runtime_last_detection_source,
            'contradiction_flags': list(contradiction_flags),
        }

        # Separated worker status: stable RPC polling worker vs. realtime WebSocket
        # worker vs. provider realtime health. canonical_last_heartbeat_at / last_poll_at
        # are the stable polling facts (monitoring_heartbeats / monitoring_polls); the
        # realtime watcher row is exposed separately so a paused or rate-limited realtime
        # worker is not rendered as a dead monitoring source.
        _worker_status_realtime_enabled = realtime_enabled()
        worker_status = build_worker_status(
            now=now,
            realtime_is_enabled=_worker_status_realtime_enabled,
            stable_last_heartbeat_at=canonical_last_heartbeat_at,
            stable_last_poll_at=last_poll_at,
            # Live rpc_polling coverage telemetry — the SAME canonical source the
            # Telemetry worker-status card reads for "Last stable poll" — so the banner
            # and that card agree on whether stable polling is active (requirement 1).
            stable_last_coverage_poll_at=canonical_last_telemetry_at,
            heartbeat_ttl_seconds=_stable_poll_stale_threshold,
            realtime_watcher=health.get('realtime_watcher'),
        )
        # Debug / reconciliation fields for the stable-polling verdict. Surfaced at the top
        # level of the runtime status so operators can see exactly which timestamps and
        # threshold drove `stable_polling_status` (and why the banner is/isn't stale).
        _ws_stable = worker_status.get('stable_polling', {}) if isinstance(worker_status, dict) else {}
        stable_polling_debug = {
            'last_stable_poll_at': _ws_stable.get('last_poll_at') or _ws_stable.get('last_coverage_poll_at'),
            'last_rpc_polling_heartbeat_at': _ws_stable.get('last_heartbeat_at'),
            'stable_poll_age_seconds': _ws_stable.get('age_seconds'),
            'stable_poll_stale_threshold_seconds': _stable_poll_stale_threshold,
            'stable_polling_status': _ws_stable.get('state'),
        }

        payload = {
            'workspace_id': workspace_id,
            'workspace_slug': workspace_slug,
            'monitoring_status': monitoring_status,
            'monitored_systems': system_count,
            'protected_assets': protected_assets_count,
            'enabled_systems': enabled_system_count,
            'active_systems': active_system_count,
            'last_heartbeat': last_heartbeat.isoformat() if last_heartbeat else None,
            'telemetry_available': bool(telemetry_countable or real_event_count > 0 or monitoring_status == 'active'),
            'status': runtime_status,
            'provider_mode': health.get('source_type') or health.get('ingestion_mode') or 'polling',
            'last_successful_ingest': evidence_at.isoformat() if evidence_at else None,
            'last_detection_evaluation_at': latest_detection_evaluation_at.isoformat() if latest_detection_evaluation_at else None,
            'last_confirmed_checkpoint': latest_detection_evaluation_at.isoformat() if successful_detection_evaluation else None,
            'successful_detection_evaluation': successful_detection_evaluation,
            'successful_detection_evaluation_recent': successful_detection_evaluation_recent,
            'last_processed_block': (latest_evidence or {}).get('block_number') or health.get('latest_processed_block'),
            'targets_monitored': enabled_system_count,
            'protected_assets_count': protected_assets_count,
            'monitored_systems_count': system_count,
            'systems_with_recent_heartbeat': recent_heartbeat_systems,
            'invalid_enabled_targets': int((broken_targets or {}).get('c') or 0),
            'healthy_enabled_targets': healthy_enabled_targets_count,
            'raw_enabled_targets': raw_enabled_targets,
            'monitorable_enabled_targets': monitorable_enabled_targets,
            'valid_asset_linked_targets': valid_asset_linked_targets,
            'enabled_monitored_systems': enabled_monitored_systems,
            'valid_target_system_links': valid_target_system_links,
            'active_alerts': int(max(
                int((open_alerts or {}).get('c') or 0),
                int((legacy_open_alerts_row or {}).get('c') or 0),
            )),
            'open_incidents': int((open_incidents or {}).get('c') or 0),
            'raw_open_alerts': int(raw_open_alerts_count),
            'raw_open_incidents': int(raw_open_incidents_count),
            'open_alerts_without_detection_evidence': int(open_alerts_without_evidence_count),
            'open_alerts_without_canonical_detection_event': int(open_alerts_without_evidence_count),
            'evidence_freshness_seconds': evidence_freshness,
            'degraded_reason': degraded_reason,
            'runtime_error_code': runtime_error_code,
            'runtime_degraded_reason': runtime_degraded_reason,
            'runtime_degraded_reason_codes': list(summary.get('runtime_degraded_reason_codes') or []),
            'runtime_status_reason_codes': list(summary.get('runtime_status_reason_codes') or []),
            'recent_evidence_state': effective_recent_evidence_state,
            'recent_evidence_reason_code': recent_evidence_reason_code,
            'recent_real_event_count': real_event_count,
            'real_event_count': real_event_count,
            'real_events_detected': real_event_count,
            'coverage_heartbeat_updates': coverage_heartbeat_count,
            'coverage_heartbeat_count': coverage_heartbeat_count,
            'recent_confidence_basis': str((latest_detection_metadata or {}).get('confidence_basis') or latest_detection_payload.get('confidence_basis') or 'none') if isinstance(latest_detection_payload, dict) else 'none',
            'last_real_event_at': (latest_detection_metadata or {}).get('last_real_event_at') if isinstance(latest_detection_metadata, dict) else None,
            'freshness_status': summary['telemetry_freshness'],
            'confidence_status': summary['confidence'],
            'coverage_reason': (
                degraded_reason
                or (
                    'unsupported_target_type_for_live_coverage'
                    if runtime_status_reason == 'unsupported_target_type_for_live_coverage'
                    else ('no_evidence' if monitoring_status == 'idle' else (None if monitoring_status == 'active' else 'monitoring_unavailable'))
                )
            ),
            'worker_last_error': health.get('last_error'),
            'latest_telemetry_checkpoint': (latest_detection_evaluation_at or evidence_at).isoformat() if (latest_detection_evaluation_at or evidence_at) else None,
            'source_of_evidence': source_of_evidence,
            'evidence_source': evidence_source,
            'details': {
                'compatibility': {
                    'legacy_receipts_reporting_systems': int(receipts_reporting_systems),
                    'legacy_reporting_systems': legacy_diagnostics['legacy_reporting_systems'],
                    'legacy_last_telemetry_at': legacy_diagnostics['legacy_last_telemetry_at'],
                    'legacy_last_coverage_telemetry_at': legacy_diagnostics['legacy_last_coverage_telemetry_at'],
                    'legacy_telemetry_kind': legacy_diagnostics['legacy_telemetry_kind'],
                    'legacy_monitored_systems_last_heartbeat_max': last_system_heartbeat.isoformat() if last_system_heartbeat else None,
                    'legacy_open_alerts_without_detection_evidence': int(legacy_open_alerts_without_evidence_count),
                    'legacy_proof_chain_gaps_count': int(legacy_proof_chain_gaps_count),
                    'canonical_reporting_targets_from_events': len(canonical_reporting_targets_from_events),
                    'canonical_reporting_targets_from_coverage': len(canonical_reporting_targets_from_coverage),
                    'canonical_runtime_truth_enabled': bool(canonical_runtime_truth_enabled),
                }
            },
            'legacy_diagnostics': legacy_diagnostics,
            'provider_health': provider_health,
            'target_coverage': target_coverage,
            'workspace_configured': workspace_configured,
            'configuration_reason': configuration_reason,
            'configuration_reason_codes': list(configuration_reason_codes),
            'status_reason': runtime_status_reason,
            'runtime_status': runtime_status_summary,
            'valid_protected_assets': valid_protected_asset_count,
            'linked_monitored_systems': linked_monitored_system_count,
            'enabled_configs': persisted_enabled_config_count,
            'valid_link_count': valid_target_system_link_count,
            'configuration_diagnostics': dict(configuration_diagnostics),
            'last_poll_at': last_poll_at.isoformat() if last_poll_at else None,
            'last_heartbeat_at': canonical_last_heartbeat_at.isoformat() if canonical_last_heartbeat_at else None,
            'last_telemetry_at': runtime_last_telemetry_at.isoformat() if runtime_last_telemetry_at else None,
            'last_telemetry_source': runtime_last_telemetry_source,
            'last_coverage_telemetry_at': last_coverage_telemetry_at.isoformat() if last_coverage_telemetry_at else None,
            'coverage_receipts_last_at': live_coverage_receipts_workspace_latest.isoformat() if live_coverage_receipts_workspace_latest else None,
            'coverage_receipts_workspace_count': int(live_coverage_receipts_persisted_count),
            'stale_heartbeat': stale_heartbeat,
            'worker_status': worker_status,
            **stable_polling_debug,
            'realtime_enabled': _worker_status_realtime_enabled,
            'worker_alive': bool(worker_alive),
            'dead_lettered_targets': dead_lettered_count,
            'provider_degraded_flag': provider_degraded_or_unreachable,
            'telemetry_kind': telemetry_kind,
            'last_detection_at': runtime_last_detection_at.isoformat() if runtime_last_detection_at else None,
            'last_detection_source': runtime_last_detection_source,
            'proof_chain_status': proof_chain_status,
            'proof_chain_correlation_id': proof_chain_correlation_id,
            'contradiction_flags': list(summary.get('contradiction_flags') or []),
            'top_banner_reasons': list(summary.get('top_banner_reasons') or []),
            'workspace_monitoring_summary': summary,
            'canonical_runtime_truth_enabled': bool(canonical_runtime_truth_enabled),
            'summary_generated_at': now.isoformat(),
            'continuity_status': summary.get('continuity_status'),
            'continuity_reason_codes': list(summary.get('continuity_reason_codes') or []),
            'continuity_signals': dict(summary.get('continuity_signals') or {}),
            'continuity_freshness_ages_seconds': dict(summary.get('continuity_freshness_ages_seconds') or {}),
            'continuity_configured_thresholds_seconds': dict(summary.get('continuity_configured_thresholds_seconds') or {}),
            'continuity_breach_reasons': list(summary.get('continuity_breach_reasons') or []),
            'continuity_slo': {
                'pass': bool(summary.get('continuity_slo_pass') is True),
            'heartbeat_age_seconds': summary.get('heartbeat_age_seconds'),
            'worker_heartbeat_age_seconds': summary.get('worker_heartbeat_age_seconds', summary.get('heartbeat_age_seconds')),
            'telemetry_age_seconds': summary.get('telemetry_age_seconds'),
            'event_ingestion_age_seconds': summary.get('event_ingestion_age_seconds'),
            'detection_age_seconds': summary.get('detection_age_seconds'),
            'detection_pipeline_age_seconds': summary.get('detection_pipeline_age_seconds'),
            'detection_eval_age_seconds': summary.get('detection_eval_age_seconds'),
                'heartbeat_threshold_seconds': summary.get('heartbeat_threshold_seconds'),
                'telemetry_threshold_seconds': summary.get('telemetry_threshold_seconds'),
                'event_ingestion_threshold_seconds': summary.get('event_ingestion_threshold_seconds'),
                'detection_threshold_seconds': summary.get('detection_threshold_seconds'),
                'thresholds_seconds': dict(summary.get('thresholds_seconds') or {}),
                'required_thresholds_seconds': dict(summary.get('required_thresholds_seconds') or {}),
                'continuity_thresholds_seconds': dict(summary.get('continuity_thresholds_seconds') or {}),
                'reason_codes': list(summary.get('continuity_reason_codes') or []),
                'checks': dict((summary.get('continuity_contract') or {}).get('checks') or {}),
                'freshness_ages_seconds': dict(summary.get('continuity_freshness_ages_seconds') or {}),
                'configured_thresholds_seconds': dict(summary.get('continuity_configured_thresholds_seconds') or {}),
                'breach_reasons': list(summary.get('continuity_breach_reasons') or []),
                'failed_checks': list(summary.get('continuity_failed_checks') or summary.get('continuity_reason_codes') or []),
            },
            'continuity_failed_checks': list(summary.get('continuity_failed_checks') or summary.get('continuity_reason_codes') or []),
            'continuity_contract': dict(summary.get('continuity_contract') or {}),
            'field_reason_codes': dict(field_reason_codes),
            'coverage_only_warning': dict(summary.get('coverage_only_warning') or {}),
            'db_failure_classification': None,
            'db_failure_reason': None,
        }
        background_loop_health = get_background_loop_health()
        payload['background_loop_health'] = background_loop_health
        payload['loop_running'] = bool(background_loop_health.get('loop_running'))
        payload['last_successful_cycle'] = background_loop_health.get('last_successful_cycle')
        payload['consecutive_failures'] = int(background_loop_health.get('consecutive_failures') or 0)
        payload['next_retry_at'] = background_loop_health.get('next_retry_at')
        payload['backoff_seconds'] = background_loop_health.get('backoff_seconds')
        payload.update(summary)
        if monitoring_status in {'active', 'idle'}:
            payload['monitoring_status'] = monitoring_status
        payload['reporting_systems'] = canonical_reporting_systems
        payload['last_poll_at'] = last_poll_at.isoformat() if last_poll_at else None
        payload['last_heartbeat_at'] = canonical_last_heartbeat_at.isoformat() if canonical_last_heartbeat_at else None
        payload['last_telemetry_at'] = runtime_last_telemetry_at.isoformat() if runtime_last_telemetry_at else None
        payload['last_telemetry_source'] = runtime_last_telemetry_source
        payload['last_detection_at'] = runtime_last_detection_at.isoformat() if runtime_last_detection_at else None
        payload['last_detection_source'] = runtime_last_detection_source
        # latest_live_telemetry_at: MAX(observed_at) from telemetry_events for live evm_rpc/rpc_polling rows.
        latest_live_telemetry_at = canonical_last_telemetry_at.isoformat() if canonical_last_telemetry_at else None
        payload['latest_live_telemetry_at'] = latest_live_telemetry_at
        summary['latest_live_telemetry_at'] = latest_live_telemetry_at
        # live_evidence_ready: True only when the full evidence chain is complete.
        # A telemetry row alone is NOT sufficient — the chain requires:
        # telemetry → detection → alert → incident → response action → evidence package.
        live_evidence_ready = bool(
            canonical_last_telemetry_at is not None
            and int(detections_count) > 0
            and int(alerts_count) > 0
            and int(incidents_count) > 0
            and int(response_actions_count) > 0
            and int(evidence_count) > 0
        )
        payload['live_evidence_ready'] = live_evidence_ready
        summary['live_evidence_ready'] = live_evidence_ready
        # Surface live_evidence_ready=False as an informational reason code without
        # downgrading status. Clean monitoring (no threats detected) is legitimately
        # LIVE even without a full detection→alert→incident→response→evidence chain.
        # The proof chain is only required for verified compliance export.
        recent_canonical_live_telemetry = bool(
            canonical_last_telemetry_at is not None
            and int((now - canonical_last_telemetry_at).total_seconds()) <= telemetry_window_seconds
        )
        if recent_canonical_live_telemetry and not live_evidence_ready:
            existing_reason_codes = list(summary.get('reason_codes') or [])
            if 'limited_coverage_evidence_chain_incomplete' not in existing_reason_codes:
                existing_reason_codes.append('limited_coverage_evidence_chain_incomplete')
                summary['reason_codes'] = sorted(set(existing_reason_codes))
        if isinstance(payload.get('workspace_monitoring_summary'), dict):
            payload['workspace_monitoring_summary']['live_evidence_ready'] = live_evidence_ready
            payload['workspace_monitoring_summary']['latest_live_telemetry_at'] = latest_live_telemetry_at
        if isinstance(payload.get('workspace_monitoring_summary'), dict):
            payload['workspace_monitoring_summary']['background_loop_health'] = dict(background_loop_health)
        logger.info(
            'monitoring_workspace_summary_assembly workspace_id=%s workspace_slug=%s valid_asset_count=%s linked_system_count=%s enabled_config_count=%s valid_link_count=%s configured_systems=%s reporting_systems=%s configuration_reason=%s status_reason=%s',
            workspace_id,
            workspace_slug,
            valid_protected_asset_count,
            linked_monitored_system_count,
            persisted_enabled_config_count,
            valid_target_system_link_count,
            enabled_system_count,
            reporting_systems,
            configuration_reason,
            runtime_status_reason,
        )
        provider_health = (
            'healthy' if (
                (str(payload.get('recent_evidence_state')) == 'real' and int(payload.get('recent_real_event_count') or 0) > 0)
                or (evidence_source == 'live' and reporting_systems > 0 and coverage_fresh)
            )
            else 'degraded'
        )
        live_coverage_mode = 'HYBRID' if monitoring_mode_raw == 'hybrid' else 'LIVE'
        mode = str(health.get('operational_mode') or health.get('mode') or live_coverage_mode).upper()
        active_live_coverage = bool(
            workspace_configured
            and monitoring_status == 'active'
            and source_of_evidence == 'live'
            and reporting_systems > 0
            and coverage_fresh
            and not degraded_reason
            and not provider_degraded_or_unreachable
        )
        claim_safety_risk_indicators: list[str] = []
        degraded_mode_reasons: list[str] = []
        if degraded_reason:
            degraded_mode_reasons.append(str(degraded_reason))
        if provider_degraded_or_unreachable:
            degraded_mode_reasons.append('provider_degraded_or_unreachable')
        if monitoring_status == 'degraded' or runtime_status_summary == 'degraded':
            degraded_mode_reasons.append('runtime_status_degraded')
        if int(payload.get('real_event_count') or payload.get('recent_real_event_count') or 0) <= 0:
            claim_safety_risk_indicators.append('no_recent_real_events')
        if coverage_only_warning_active:
            claim_safety_risk_indicators.append('coverage_only_persistent_no_evidence')
        if runtime_status_summary in {'healthy', 'idle'} and active_live_coverage and not degraded_mode_reasons:
            mode = live_coverage_mode
        elif degraded_mode_reasons:
            mode = 'DEGRADED'
        claim_validator_status = str(claim_validator.get('status') or 'FAIL')
        claim_validator_checks = claim_validator.get('checks') if isinstance(claim_validator.get('checks'), dict) else {}
        claim_validator_reason_codes = claim_validator.get('reason_codes') if isinstance(claim_validator.get('reason_codes'), list) else []
        if not claim_validator_reason_codes:
            claim_validator_reason_codes = [name for name, ok in claim_validator_checks.items() if not ok]
        continuity_signals = payload.get('continuity_signals') if isinstance(payload.get('continuity_signals'), dict) else {}
        no_recent_real_events = int(payload.get('real_event_count') or payload.get('recent_real_event_count') or 0) <= 0
        freshness_confirmed = (
            str(payload.get('freshness_status') or payload.get('telemetry_freshness') or '').lower() == 'fresh'
            and str(continuity_signals.get('event_ingestion_freshness') or '').lower() == 'fresh'
            and str(continuity_signals.get('detection_pipeline_freshness') or '').lower() == 'fresh'
        )
        limited_claim_condition = bool(
            claim_validator_status == 'FAIL'
            and no_recent_real_events
            and str(payload.get('continuity_status') or '').lower() == 'continuous_live'
            and freshness_confirmed
            and str(source_of_evidence or '').lower() == 'live'
        )
        allowed_limited_reason_codes = {
            'recent_real_event_count_positive',
            'evidence_window_recent_real_events',
            'no_recent_degraded_or_missing',
        }
        only_no_recent_event_failures = (
            bool(claim_validator_reason_codes)
            and set(str(code) for code in claim_validator_reason_codes).issubset(allowed_limited_reason_codes)
        )
        if limited_claim_condition and only_no_recent_event_failures:
            claim_validator_status = 'LIMITED'
        if coverage_only_warning_active:
            claim_validator_status = 'FAIL'
        if claim_validator_status != 'PASS':
            claim_safety_risk_indicators.append(f'claim_validator_{claim_validator_status.lower()}')
        for reason_code in claim_validator_reason_codes:
            normalized_reason_code = str(reason_code or '').strip().lower()
            if normalized_reason_code:
                claim_safety_risk_indicators.append(f'claim_validator_reason_{normalized_reason_code}')
        explicit_reason = str(claim_validator.get('reason') or '').strip().lower()
        if explicit_reason:
            claim_safety_risk_indicators.append(f"claim_validator_reason_{explicit_reason.replace(':', '_')}")
        payload.update(
            {
                'mode': mode,
                # Prefer the list of provider_health_records (with checked_at) when available
                # from the canonical DB query; fall back to the legacy string status.
                'provider_health': payload.get('provider_health') if isinstance(payload.get('provider_health'), list) else provider_health,
                'provider_reachable': bool((claim_validator.get('checks') or {}).get('evm_rpc_reachable')),
                'evidence_state': str(payload.get('recent_evidence_state') or 'missing'),
                'truthfulness_state': str(claim_validator.get('recent_truthfulness_state') or payload.get('recent_truthfulness_state') or 'unknown_risk'),
                'claim_safe': bool(claim_validator.get('sales_claims_allowed')),
                'sales_claims_allowed': bool(claim_validator.get('sales_claims_allowed')),
                'claim_validator_status': claim_validator_status,
                'claim_safety_risk_indicators': claim_safety_risk_indicators,
                'claim_validator_reason_codes': claim_validator_reason_codes,
            }
        )
        enterprise_ready_gate = _evaluate_enterprise_ready_gate(
            continuity_slo_pass=bool(summary.get('continuity_slo_pass') is True),
            telemetry_freshness=summary.get('telemetry_freshness'),
            ingestion_freshness=summary.get('ingestion_freshness'),
            detection_pipeline_freshness=summary.get('detection_pipeline_freshness'),
            proof_chain_status=payload.get('proof_chain_status') or summary.get('proof_chain_status'),
            runtime_status=summary.get('runtime_status'),
            monitoring_status=summary.get('monitoring_status'),
            reporting_systems_count=int(summary.get('reporting_systems_count') or 0),
            monitored_systems_count=int(summary.get('monitored_systems_count') or 0),
            contradiction_flags=list(summary.get('contradiction_flags') or []),
            guard_flags=list(summary.get('guard_flags') or []),
        )
        payload.update(enterprise_ready_gate)
        if isinstance(summary, dict):
            summary.update(enterprise_ready_gate)
        logger.info(
            'monitoring_runtime_status_summary workspace_id=%s healthy_enabled_targets=%s monitored_rows=%s enabled_rows=%s protected_assets=%s monitoring_status=%s systems_with_recent_heartbeat=%s status_inputs=%s',
            workspace_id,
            healthy_enabled_targets_count,
            len(monitored_rows),
            enabled_system_count,
            protected_assets_count,
            monitoring_status,
            recent_heartbeat_systems,
            {
                'healthy_enabled_targets': healthy_enabled_targets_count,
                'monitored_system_rows': len(monitored_rows),
                'enabled_monitored_rows': len(enabled_rows),
                'unsupported_enabled_rows': len(unsupported_enabled_rows),
                'protected_assets': protected_assets_count,
                'invalid_enabled_targets': int((broken_targets or {}).get('c') or 0),
                'runner_alive': runner_alive,
                'stale_heartbeat': stale_heartbeat,
                'workspace_id': workspace_id,
            },
        )
        logger.info(
            'monitoring_runtime_status_decision workspace_id=%s healthy_enabled_targets=%s monitored_system_rows=%s protected_assets=%s systems_with_recent_heartbeat=%s decision=%s',
            workspace_id,
            healthy_enabled_targets_count,
            len(monitored_rows),
            protected_assets_count,
            recent_heartbeat_systems,
            monitoring_status,
        )
        if _runtime_status_debug_enabled():
            monitored_system_ids = [str(row.get('id') or '') for row in monitored_rows if row.get('id')]
            enabled_monitored_system_ids = [str(row.get('id') or '') for row in enabled_rows if row.get('id')]
            target_ids = [str(row.get('target_id') or '') for row in monitored_rows if row.get('target_id')]
            payload.update(
                {
                    'workspace_id': workspace_id,
                    'resolved_workspace_id': workspace_id,
                    'request_user_resolved': bool(user_id),
                    'request_user_id': user_id,
                    'workspace_header_present': workspace_header_present,
                    'counted_monitored_systems': system_count,
                    'counted_enabled_systems': enabled_system_count,
                    'counted_active_systems': active_system_count,
                    'counted_monitored_system_ids': monitored_system_ids,
                    'counted_enabled_monitored_system_ids': enabled_monitored_system_ids,
                    'sample_target_ids': target_ids[:5],
                    'sample_target_ids_count': len(target_ids),
                    'systems_with_recent_heartbeat': recent_heartbeat_systems,
                    'has_monitorable_targets': has_monitorable_targets,
                    'has_monitored_system_rows': has_any_monitored_rows,
                }
            )
        if runtime_schema_missing_columns:
            missing_column = runtime_schema_missing_columns[0]
            schema_status_reason = f'runtime_schema_column_missing:{missing_column}'
            payload['configuration_reason'] = 'runtime_schema_incomplete'
            payload['status_reason'] = schema_status_reason
            payload['error'] = {
                'code': 'runtime_schema_incomplete',
                'missing_column': missing_column,
                'missing_columns': runtime_schema_missing_columns,
                'migration_hints': runtime_schema_migration_hints,
            }
            if isinstance(payload.get('workspace_monitoring_summary'), dict):
                payload['workspace_monitoring_summary']['configuration_reason'] = 'runtime_schema_incomplete'
                payload['workspace_monitoring_summary']['status_reason'] = schema_status_reason
        return _normalize_monitoring_runtime_contract(payload)

    def _write_runtime_cache(result: dict[str, Any]) -> None:
        _cache_ts = perf_counter()
        _cache_keys: set[str] = set()
        if cache_key:
            _cache_keys.add(cache_key)
        _cwid = str(result.get('workspace_id') or resolved_workspace_id or '').strip()
        _cwslug = str(result.get('workspace_slug') or resolved_workspace_slug or '').strip()
        if _cwid:
            _cache_keys.add(f'workspace:{_cwid}')
        if _cwslug:
            _cache_keys.add(f'workspace_slug:{_cwslug}')
        for _ck in _cache_keys:
            RUNTIME_STATUS_WORKSPACE_CACHE[_ck] = (_cache_ts, dict(result))

    try:
        payload = _monitoring_runtime_status_impl()
        _write_runtime_cache(payload)
        return payload
    except HTTPException as exc:
        detail_payload = exc.detail if isinstance(exc.detail, dict) else {}
        if (
            exc.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
            and detail_payload.get('code') == 'runtime_schema_incomplete'
        ):
            workspace_id, workspace_slug = _workspace_context_from_request(request)
            missing_columns = detail_payload.get('missing_columns') if isinstance(detail_payload.get('missing_columns'), list) else []
            missing_column = str(missing_columns[0] if missing_columns else 'unknown')
            logger.warning(
                'monitoring_runtime_status_schema_incomplete workspace_id=%s workspace_slug=%s missing_columns=%s migration_hints=%s',
                workspace_id,
                workspace_slug,
                missing_columns,
                detail_payload.get('migration_hints'),
            )
            return _normalize_monitoring_runtime_contract(
                _runtime_schema_failure_payload(
                    workspace_id=workspace_id,
                    workspace_slug=workspace_slug,
                    missing_column=missing_column,
                    error_details=detail_payload,
                )
            )
        raise
    except psycopg.Error as exc:
        workspace_id, workspace_slug = _workspace_context_from_request(request)
        _exc_msg_lower = str(exc).lower()
        _is_closed_connection = (
            'connection is closed' in _exc_msg_lower
            or 'connection already closed' in _exc_msg_lower
            or 'the connection was closed' in _exc_msg_lower
        )
        if _is_closed_connection:
            logger.warning(
                'monitoring_runtime_status_connection_closed_retry workspace_id=%s workspace_slug=%s checkpoint=%s stage=%s',
                workspace_id,
                workspace_slug,
                last_query_checkpoint,
                last_query_checkpoint or 'unknown',
            )
            try:
                _retry_result = _monitoring_runtime_status_impl()
                logger.info(
                    'monitoring_runtime_status_connection_closed_retry_success workspace_id=%s workspace_slug=%s',
                    workspace_id,
                    workspace_slug,
                )
                _write_runtime_cache(_retry_result)
                return _retry_result
            except Exception:
                logger.warning(
                    'monitoring_runtime_status_connection_closed_retry_failed workspace_id=%s workspace_slug=%s checkpoint=%s',
                    workspace_id,
                    workspace_slug,
                    last_query_checkpoint,
                )
        db_classification = 'connection_closed' if _is_closed_connection else classify_db_error(exc)
        if db_classification == 'quota_exceeded':
            db_status_reason = 'Database quota exceeded'
        elif db_classification == 'connection_closed':
            db_status_reason = 'Database connection closed unexpectedly'
        else:
            db_status_reason = 'Database unavailable'
        reason_tokens = ['runtime_status_degraded', 'database_error', 'stage_query']
        if _is_closed_connection:
            reason_tokens.append('database_error.connection_closed')
        if last_query_checkpoint:
            reason_tokens.append(_safe_checkpoint_reason_token(last_query_checkpoint))
        reason_tokens.append(f'db_classification_{db_classification}')
        logger.exception(
            'monitoring_runtime_status_db_error workspace_id=%s workspace_slug=%s checkpoint=%s classification=%s',
            workspace_id,
            workspace_slug,
            last_query_checkpoint,
            db_classification,
        )
        logger.warning(
            'monitoring_runtime_status_degraded_payload_reasons workspace_id=%s workspace_slug=%s reason_tokens=%s',
            workspace_id,
            workspace_slug,
            ','.join(reason_tokens),
        )
        return _normalize_monitoring_runtime_contract(_runtime_failure_payload(
            workspace_id=workspace_id,
            workspace_slug=workspace_slug,
            error_code='runtime_status_db_error',
            error_type=type(exc).__name__,
            error_message='Monitoring runtime data unavailable due to database connectivity or query failure.',
            error_stage='query',
            error_stage_detail=last_query_checkpoint,
            error_reason_tokens=reason_tokens,
            status_reason=db_status_reason,
            hint='retry_request_or_check_database_health',
            db_persistence_available=False,
            db_persistence_reason='Monitoring persistence unavailable',
            db_failure_classification=db_classification,
            db_failure_reason=db_status_reason,
        ))
    except RuntimeError as exc:
        workspace_id, workspace_slug = _workspace_context_from_request(request)
        stage_detail = last_query_checkpoint or type(exc).__name__
        reason_tokens = ['runtime_status_degraded', 'runtime_error', 'stage_aggregation', _safe_checkpoint_reason_token(stage_detail)]
        logger.exception('monitoring_runtime_status_runtime_error workspace_id=%s workspace_slug=%s', workspace_id, workspace_slug)
        logger.warning(
            'monitoring_runtime_status_degraded_payload_reasons workspace_id=%s workspace_slug=%s reason_tokens=%s',
            workspace_id,
            workspace_slug,
            ','.join(reason_tokens),
        )
        return _normalize_monitoring_runtime_contract(_runtime_failure_payload(
            workspace_id=workspace_id,
            workspace_slug=workspace_slug,
            error_code='runtime_status_runtime_error',
            error_type=type(exc).__name__,
            error_message='Monitoring runtime status could not be aggregated from current telemetry context.',
            error_stage='aggregation',
            error_stage_detail=stage_detail,
            error_reason_tokens=reason_tokens,
            status_reason='runtime_status_degraded:runtime_error',
            hint='retry_request_or_check_monitoring_worker_runtime',
        ))
    except Exception as exc:
        workspace_id, workspace_slug = _workspace_context_from_request(request)
        stage_detail = last_query_checkpoint or type(exc).__name__
        reason_tokens = ['runtime_status_degraded', 'internal_error', 'stage_context', _safe_checkpoint_reason_token(stage_detail)]
        logger.exception('monitoring_runtime_status_unhandled_error workspace_id=%s workspace_slug=%s', workspace_id, workspace_slug)
        logger.warning(
            'monitoring_runtime_status_degraded_payload_reasons workspace_id=%s workspace_slug=%s reason_tokens=%s',
            workspace_id,
            workspace_slug,
            ','.join(reason_tokens),
        )
        return _normalize_monitoring_runtime_contract(_runtime_failure_payload(
            workspace_id=workspace_id,
            workspace_slug=workspace_slug,
            error_code='runtime_status_unhandled_error',
            error_type=type(exc).__name__,
            error_message='Monitoring runtime status unavailable due to an unexpected internal error.',
            error_stage='context',
            error_stage_detail=stage_detail,
            error_reason_tokens=reason_tokens,
            status_reason='runtime_status_degraded:internal_error',
            hint='retry_request_or_contact_support_with_timestamp',
        ))


def monitoring_runtime_debug_payload(request: Request | None = None) -> dict[str, Any]:
    def _base_debug_payload(*, workspace_id: Any = None, workspace_slug: Any = None) -> dict[str, Any]:
        return {
            'workspace_id': workspace_id,
            'workspace_slug': workspace_slug,
            'workspace_configured': False,
            'configuration_reason': 'unavailable',
            'configuration_reason_codes': ['unavailable'],
            'status_reason': 'runtime_debug_unavailable',
            'valid_protected_assets': 0,
            'linked_monitored_systems': 0,
            'enabled_configs': 0,
            'valid_link_count': 0,
            'raw_enabled_targets': 0,
            'monitorable_enabled_targets': 0,
            'valid_asset_linked_targets': 0,
            'enabled_monitored_systems': 0,
            'valid_target_system_links': 0,
            'count_reason_codes': {
                'raw_enabled_targets': 'runtime_debug_unavailable',
                'monitorable_enabled_targets': 'runtime_debug_unavailable',
                'valid_asset_linked_targets': 'runtime_debug_unavailable',
                'enabled_monitored_systems': 'runtime_debug_unavailable',
                'valid_target_system_links': 'runtime_debug_unavailable',
            },
            'field_reason_codes': {},
            'configured_systems': 0,
            'reporting_systems': 0,
            'last_poll_at': None,
            'last_heartbeat_at': None,
            'last_coverage_telemetry_at': None,
            'last_telemetry_at': None,
            'telemetry_kind': None,
            'evidence_source': 'none',
            'confidence_status': 'unavailable',
            'runtime_status_summary': 'offline',
            'configuration_diagnostics': {
                'valid_protected_assets': 0,
                'linked_monitored_systems': 0,
                'enabled_configs': 0,
                'valid_link_count': 0,
                'workspace_configured': False,
                'configuration_reason': 'unavailable',
                'reason_codes': ['unavailable'],
            },
            'workspace_monitoring_summary': {},
        }

    def _structured_runtime_error_payload(*, configuration_reason: str, status_reason: str, exc: Exception | None = None) -> dict[str, Any]:
        payload = _base_debug_payload()
        payload['configuration_reason'] = configuration_reason
        payload['configuration_reason_codes'] = [configuration_reason]
        payload['status_reason'] = status_reason
        payload['configuration_diagnostics'] = {
            'valid_protected_assets': 0,
            'linked_monitored_systems': 0,
            'enabled_configs': 0,
            'valid_link_count': 0,
            'raw_enabled_targets': 0,
            'monitorable_enabled_targets': 0,
            'valid_asset_linked_targets': 0,
            'enabled_monitored_systems': 0,
            'valid_target_system_links': 0,
            'count_reason_codes': {
                'raw_enabled_targets': configuration_reason,
                'monitorable_enabled_targets': configuration_reason,
                'valid_asset_linked_targets': configuration_reason,
                'enabled_monitored_systems': configuration_reason,
                'valid_target_system_links': configuration_reason,
            },
            'workspace_configured': False,
            'configuration_reason': configuration_reason,
            'reason_codes': [configuration_reason],
        }
        payload['field_reason_codes'] = {}
        if exc is not None:
            payload['error'] = {
                'code': 'runtime_debug_payload_error',
                'type': type(exc).__name__,
                'message': str(exc),
            }
        return payload

    try:
        runtime_payload = monitoring_runtime_status(request)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR:
            detail_payload = exc.detail if isinstance(exc.detail, dict) else {}
            fallback_payload = _base_debug_payload(
                workspace_id=detail_payload.get('workspace_id') if isinstance(detail_payload, dict) else None,
                workspace_slug=detail_payload.get('workspace_slug') if isinstance(detail_payload, dict) else None,
            )
            if isinstance(detail_payload, dict):
                fallback_payload.update(detail_payload)
            fallback_payload['runtime_status_summary'] = 'offline'
            fallback_payload['status_reason'] = str(
                fallback_payload.get('status_reason') or 'runtime_debug_status_exception:http_500_fallback'
            )
            fallback_payload.setdefault('field_reason_codes', {})
            return _normalize_monitoring_runtime_contract(fallback_payload)
        if exc.status_code in {status.HTTP_400_BAD_REQUEST, status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN}:
            detail = str(exc.detail or '').strip() or 'workspace_or_auth_context_unavailable'
            safe_reason = detail.replace(' ', '_').lower()
            configuration_reason = 'workspace_not_resolved' if exc.status_code == status.HTTP_400_BAD_REQUEST else 'auth_context_unavailable'
            return _normalize_monitoring_runtime_contract(_structured_runtime_error_payload(
                configuration_reason=configuration_reason,
                status_reason=f'runtime_debug_context_error:{safe_reason}',
            ))
        raise
    except Exception as exc:
        logger.exception('monitoring_runtime_debug_payload_failed')
        return _normalize_monitoring_runtime_contract(_structured_runtime_error_payload(
            configuration_reason='runtime_status_exception',
            status_reason=f'runtime_debug_status_exception:{type(exc).__name__}',
            exc=exc,
        ))

    return _normalize_monitoring_runtime_contract(runtime_payload)



def list_monitoring_evidence(request: Request, *, limit: int = 50) -> dict[str, Any]:
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        rows = connection.execute(
            '''
            SELECT e.*, a.name AS asset_name, t.name AS target_name
            FROM evidence e
            LEFT JOIN assets a ON a.id = e.asset_id
            LEFT JOIN targets t ON t.id = e.target_id
            WHERE e.workspace_id = %s
            ORDER BY e.observed_at DESC
            LIMIT %s
            ''',
            (workspace_context['workspace_id'], max(1, min(limit, 200))),
        ).fetchall()
        return {'evidence': [_json_safe_value(dict(row)) for row in rows]}


def list_monitoring_heartbeats(request: Request, *, limit: int = 50) -> dict[str, Any]:
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        rows = connection.execute(
            '''
            SELECT id, workspace_id, chain, status, last_success_at, last_error_at, last_error_text, last_processed_block, provider_mode, updated_at
            FROM monitor_heartbeat
            ORDER BY updated_at DESC
            LIMIT %s
            ''',
            (max(1, min(limit, 200)),),
        ).fetchall()
        return {'heartbeats': [_json_safe_value(dict(row)) for row in rows]}


def list_monitoring_worker_errors(request: Request, *, limit: int = 50) -> dict[str, Any]:
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        worker_rows = connection.execute(
            '''
            SELECT worker_name, status, last_error, last_cycle_at, last_heartbeat_at, updated_at
            FROM monitoring_worker_state
            WHERE last_error IS NOT NULL
            ORDER BY updated_at DESC
            LIMIT %s
            ''',
            (max(1, min(limit, 200)),),
        ).fetchall()
        target_rows = connection.execute(
            '''
            SELECT t.id AS target_id, t.name AS target_name, t.last_run_status, t.watcher_degraded_reason, t.last_failed_monitoring_at, t.updated_at
            FROM targets t
            WHERE t.workspace_id = %s
              AND t.deleted_at IS NULL
              AND (t.last_run_status IN ('error', 'failed') OR t.watcher_degraded_reason IS NOT NULL)
            ORDER BY COALESCE(t.last_failed_monitoring_at, t.updated_at) DESC
            LIMIT %s
            ''',
            (workspace_context['workspace_id'], max(1, min(limit, 200))),
        ).fetchall()
        return {
            'workspace': workspace_context['workspace'],
            'worker_errors': [_json_safe_value(dict(row)) for row in worker_rows],
            'target_errors': [_json_safe_value(dict(row)) for row in target_rows],
        }


_TELEMETRY_ALLOWED_EVENT_TYPE_FILTERS = frozenset({
    'wallet_transfer_detected',
    'rpc_polling',
    'native_transfer',
    'wallet_transfers',   # friendly alias: matches wallet_transfer_detected OR native_transfer
    'alerts_only',        # telemetry rows linked to a real alert row
})


def list_target_telemetry(
    request: Request,
    *,
    target_id: str,
    limit: int = 50,
    offset: int = 0,
    q: str | None = None,
    event_type_filter: str | None = None,
) -> dict[str, Any]:
    try:
        uuid.UUID(target_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='target_id must be a valid UUID.')
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        workspace_id = workspace_context['workspace_id']

        # Resolve the target's full monitored address so the UI can show the exact
        # watched wallet (not a truncated form) and an operator can confirm it matches
        # their MetaMask wallet. Workspace-scoped; None when the target has no wallet.
        _target_row = connection.execute(
            '''
            SELECT wallet_address, contract_identifier, target_metadata, chain_network, target_type
            FROM targets
            WHERE id = %s::uuid AND workspace_id = %s::uuid AND deleted_at IS NULL
            ''',
            (target_id, workspace_id),
        ).fetchone()
        _monitored_address: str | None = None
        _monitored_chain_network: str | None = None
        if _target_row:
            from services.api.app.evm_activity_provider import resolve_monitored_wallet
            _target_for_resolve = _json_safe_value(dict(_target_row))
            _monitored_address = resolve_monitored_wallet(_target_for_resolve)
            _monitored_chain_network = str(_target_row.get('chain_network') or '').strip().lower() or None

        # Realtime WebSocket status for the Telemetry header. The env flag alone is
        # NOT authoritative: the worker may run with BASE_REALTIME_ENABLED set only on
        # the worker process while the API process does not have it. Derive from the
        # canonical watcher row (written only by the realtime worker) so the UI shows
        # "Active" when provider_mode=realtime_websocket, degraded=False, and heads are
        # increasing — instead of a false "Paused / Disabled" (requirement 5). Resolved
        # here (before the count/data queries) so the telemetry data query stays the
        # last executed statement for callers that assert on execution order.
        _realtime_env_enabled = realtime_enabled()
        _realtime_active_by_facts = False
        try:
            _watcher_row = connection.execute(
                '''
                SELECT source_status, degraded, degraded_reason, metrics
                FROM monitoring_watcher_state
                ORDER BY COALESCE(last_heartbeat_at, updated_at) DESC
                LIMIT 1
                '''
            ).fetchone()
            if _watcher_row is not None:
                _realtime_active_by_facts = realtime_active_by_watcher_facts(
                    _json_safe_value(dict(_watcher_row))
                )
        except Exception:
            logger.warning(
                'telemetry_realtime_watcher_unavailable workspace_id=%s target_id=%s',
                workspace_id, target_id, exc_info=True,
            )
        _realtime_effective_enabled = _realtime_env_enabled or _realtime_active_by_facts
        if _realtime_active_by_facts:
            _realtime_state = 'active'
        elif _realtime_env_enabled:
            _realtime_state = 'enabled'
        else:
            _realtime_state = 'paused'

        # Separated detection-path freshness for this target so the Telemetry page can
        # show "Last stable poll" vs "Last realtime event" distinctly (CLAUDE.md: poll
        # and realtime telemetry are separate facts). Workspace + target scoped.
        # detected_by lives in payload_json; stable coverage polls also use the
        # rpc_polling event_type even when detected_by is absent on older rows.
        # Resolved BEFORE the count/data queries so the telemetry data query stays
        # the last executed statement (same invariant as the watcher-state read).
        last_stable_poll_at: str | None = None
        last_realtime_event_at: str | None = None
        try:
            _rt_placeholders = ', '.join(['%s'] * len(REALTIME_DETECTED_BY))
            _freshness_row = connection.execute(
                f'''
                SELECT
                    MAX(observed_at) FILTER (
                        WHERE payload_json->>'detected_by' = %s
                           OR event_type IN ('rpc_polling', 'live_provider')
                    ) AS last_stable_poll_at,
                    MAX(observed_at) FILTER (
                        WHERE payload_json->>'detected_by' IN ({_rt_placeholders})
                    ) AS last_realtime_event_at
                FROM telemetry_events
                WHERE workspace_id = %s::uuid AND target_id = %s::uuid
                ''',
                [STABLE_DETECTED_BY, *REALTIME_DETECTED_BY, workspace_id, target_id],
            ).fetchone()
            if isinstance(_freshness_row, dict):
                _sp = _freshness_row.get('last_stable_poll_at')
                _rt = _freshness_row.get('last_realtime_event_at')
                last_stable_poll_at = _sp.isoformat() if hasattr(_sp, 'isoformat') else (_sp or None)
                last_realtime_event_at = _rt.isoformat() if hasattr(_rt, 'isoformat') else (_rt or None)
        except Exception:
            logger.warning(
                'telemetry_detection_path_freshness_unavailable workspace_id=%s target_id=%s',
                workspace_id, target_id, exc_info=True,
            )

        _q = (q or '').strip()
        _effective_limit = max(1, min(limit, 200))
        _effective_offset = max(0, offset)
        # Validate event_type_filter to prevent injection; silently ignore unknown values.
        _etf = (event_type_filter or '').strip().lower() or None
        if _etf and _etf not in _TELEMETRY_ALLOWED_EVENT_TYPE_FILTERS:
            _etf = None

        # Self-heal before filtering by alert presence: ensure every live wallet-transfer
        # row for this target has its alert. The scheduled worker backfill may never select
        # this target (selected_for_backfill=False), which would otherwise hide an older
        # tx_hash behind this filter. Create-only/idempotent; runs on its own committed
        # connections, so the new alerts are visible to the queries below (READ COMMITTED).
        # Best-effort — a backfill failure must never block the read.
        if _etf == 'alerts_only':
            try:
                backfill_strategic_guard_alerts_for_target(str(workspace_id), str(target_id))
            except Exception:
                logger.warning(
                    'strategic_guard_alerts_only_backfill_failed workspace_id=%s target_id=%s',
                    workspace_id, target_id, exc_info=True,
                )

        _select = '''
            SELECT
                te.id,
                te.workspace_id,
                te.target_id,
                te.provider_type,
                te.event_type AS source_type,
                te.evidence_source,
                te.observed_at,
                te.ingested_at,
                te.payload_json,
                t.chain_network AS chain_network,
                mer.block_number AS receipt_block_number
            FROM telemetry_events te
            LEFT JOIN targets t
              ON t.id = te.target_id
             AND t.workspace_id = te.workspace_id
            LEFT JOIN LATERAL (
                SELECT block_number
                FROM monitoring_event_receipts
                WHERE workspace_id = te.workspace_id
                  AND target_id = te.target_id
                ORDER BY id DESC
                LIMIT 1
            ) mer ON true
            WHERE te.workspace_id = %s::uuid
              AND te.target_id = %s::uuid
        '''
        # Wallet-transfer rows always surface first; within each tier sort by recency.
        _order = '''
            ORDER BY
                CASE WHEN te.event_type = 'wallet_transfer_detected' THEN 0 ELSE 1 END,
                te.observed_at DESC
        '''

        # Build additive WHERE clauses so filters compose cleanly.
        _filter_clauses: list[str] = []
        _filter_params: list[Any] = []

        if _etf == 'wallet_transfers':
            _filter_clauses.append(
                "te.event_type IN ('wallet_transfer_detected', 'native_transfer')"
            )
        elif _etf == 'alerts_only':
            # A wallet_transfer row is "alert-linked" when ANY of these hold (workspace +
            # target scoped throughout). Matching is per-row via EXISTS — never grouped by
            # target_id or rule_key, never limited to the latest alert — so every distinct
            # tx_hash that has an alert appears, not just one.
            #   1. an alert payload points back at this telemetry id, OR
            #   2. an alert carries this row's tx_hash (in payload.tx_hash or payload.evidence.tx_hash), OR
            #   3. a detection carrying this row's tx_hash is linked to an alert (alert evidence tx_hash).
            _filter_clauses.append(
                """(
                    EXISTS (
                        SELECT 1 FROM alerts a
                        WHERE a.workspace_id = te.workspace_id
                          AND a.target_id = te.target_id
                          AND (
                            a.payload->>'telemetry_id' = te.id::text
                            OR (
                              te.event_type IN ('wallet_transfer_detected', 'native_transfer')
                              AND COALESCE(te.payload_json->>'tx_hash', te.payload_json->>'hash') IS NOT NULL
                              AND (
                                lower(a.payload->>'tx_hash') = lower(
                                  COALESCE(te.payload_json->>'tx_hash', te.payload_json->>'hash')
                                )
                                OR lower(a.payload->'evidence'->>'tx_hash') = lower(
                                  COALESCE(te.payload_json->>'tx_hash', te.payload_json->>'hash')
                                )
                              )
                            )
                          )
                    )
                    OR EXISTS (
                        SELECT 1 FROM detections d
                        WHERE d.workspace_id = te.workspace_id
                          AND d.linked_alert_id IS NOT NULL
                          AND d.raw_evidence_json->>'target_id' = te.target_id::text
                          AND te.event_type IN ('wallet_transfer_detected', 'native_transfer')
                          AND COALESCE(te.payload_json->>'tx_hash', te.payload_json->>'hash') IS NOT NULL
                          AND lower(d.raw_evidence_json->>'tx_hash') = lower(
                            COALESCE(te.payload_json->>'tx_hash', te.payload_json->>'hash')
                          )
                    )
                )"""
            )
        elif _etf:
            _filter_clauses.append('te.event_type = %s')
            _filter_params.append(_etf)

        if _q:
            _like = f'%{_q}%'
            _filter_clauses.append(
                """(
                    lower(te.payload_json->>'tx_hash') LIKE lower(%s)
                    OR lower(te.payload_json->>'from') LIKE lower(%s)
                    OR lower(te.payload_json->>'to') LIKE lower(%s)
                    OR (te.payload_json->>'block_number') LIKE %s
                    OR lower(te.event_type) LIKE lower(%s)
                    OR lower(te.id::text) LIKE lower(%s)
                )"""
            )
            _filter_params.extend([_like, _like, _like, _like, _like, _like])

        _filter_sql = ''.join(f' AND {c}' for c in _filter_clauses)
        _base_params: list[Any] = [workspace_id, target_id]

        # COUNT for accurate total_count / has_next / has_prev.
        count_row = connection.execute(
            'SELECT COUNT(*) AS cnt FROM telemetry_events te'
            ' WHERE te.workspace_id = %s::uuid AND te.target_id = %s::uuid'
            + _filter_sql,
            _base_params + _filter_params,
        ).fetchone()
        total_count = int((count_row or {}).get('cnt') or 0)

        rows = connection.execute(
            _select + _filter_sql + _order + ' LIMIT %s OFFSET %s',
            _base_params + _filter_params + [_effective_limit, _effective_offset],
        ).fetchall()

        telemetry = []
        for row in rows:
            item = _json_safe_value(dict(row))
            payload = item.get('payload_json') if isinstance(item.get('payload_json'), dict) else {}
            raw_response = payload.get('raw_response') if isinstance(payload.get('raw_response'), dict) else {}
            chain_id = payload.get('chain_id')
            if chain_id in (None, ''):
                raw_chain_hex = str(raw_response.get('eth_chainId') or '').strip().lower()
                if raw_chain_hex.startswith('0x'):
                    try:
                        chain_id = str(int(raw_chain_hex, 16))
                    except Exception:
                        chain_id = None
            if chain_id in (None, ''):
                chain_network = str(item.get('chain_network') or '').strip().lower()
                chain_id = '1' if chain_network in {'ethereum', 'ethereum-mainnet', 'mainnet', 'eth-mainnet'} else (chain_network or None)
            block_number = payload.get('block_number')
            if block_number in (None, ''):
                raw_block_hex = str(raw_response.get('eth_blockNumber') or '').strip().lower()
                if raw_block_hex.startswith('0x'):
                    try:
                        block_number = int(raw_block_hex, 16)
                    except Exception:
                        block_number = None
            if block_number in (None, ''):
                block_number = item.get('receipt_block_number')
            item['chain_id'] = chain_id
            item['block_number'] = block_number
            item.pop('chain_network', None)
            item.pop('receipt_block_number', None)
            # detected_by lives in payload_json (there is no top-level column) and
            # older writers spread it across details/metadata/source_type; rows
            # persisted before ANY payload stamp existed classify by the row's
            # provider_type column / the stable-polling inference (see
            # classify_wallet_transfer_detected_by). A wallet-transfer row must
            # NEVER come back blank (acceptance rule): 'unknown' remains only for
            # rows naming a foreign writer that no fact can classify.
            _row_event_type = str(item.get('source_type') or '').lower()
            _detected_by, _detected_by_basis = classify_wallet_transfer_detected_by(
                payload=payload,
                provider_type=item.get('provider_type'),
                event_type=_row_event_type,
                evidence_source=item.get('evidence_source'),
            )
            if _detected_by is None and _row_event_type in WALLET_TRANSFER_EVENT_TYPES:
                _detected_by = 'unknown'
                _detected_by_basis = DETECTED_BY_BASIS_UNCLASSIFIED
            item['detected_by'] = _detected_by
            item['detected_by_source'] = _detected_by_basis
            # Explicit event_type alias: the legacy response reuses 'source_type'
            # for the DB event_type; keep that for compat but also name it truthfully.
            item['event_type'] = item.get('source_type')
            item['tx_hash'] = payload.get('tx_hash') or payload.get('hash')
            item['provider_mode'] = payload.get('provider_mode')
            item['observed_latency_seconds'] = payload.get('observed_latency_seconds')
            telemetry.append(item)

        # Debug/diagnosis contract for the newest row: log AND return the exact
        # detection-path fields so a blank customer-facing "Detected By" can be
        # traced to persistence vs normalization vs rendering in one place.
        top_row_detection_debug: dict[str, Any] | None = None
        if telemetry:
            _top = telemetry[0]
            _top_payload = _top.get('payload_json') if isinstance(_top.get('payload_json'), dict) else {}
            _top_details = _top_payload.get('details') if isinstance(_top_payload.get('details'), dict) else {}
            _top_metadata = _top_payload.get('metadata') if isinstance(_top_payload.get('metadata'), dict) else {}
            top_row_detection_debug = {
                'telemetry_id': _top.get('id'),
                'event_type': _top.get('event_type'),
                'tx_hash': _top.get('tx_hash'),
                'detected_by': _top.get('detected_by'),
                'detected_by_source': _top.get('detected_by_source'),
                'payload_detected_by': _top_payload.get('detected_by'),
                'source_type': _top_payload.get('source_type'),
                'evidence_source': _top.get('evidence_source'),
                'provider_type': _top.get('provider_type'),
                'detection_method': _top_payload.get('detection_method'),
                'details_detected_by': _top_details.get('detected_by'),
                'details_source_type': _top_details.get('source_type'),
                'metadata_detected_by': _top_metadata.get('detected_by'),
                'ingestion_source': _top_payload.get('ingestion_source'),
                'ingestion_method': _top_payload.get('ingestion_method'),
            }
            logger.info(
                'telemetry_top_row_debug target_id=%s telemetry_id=%s event_type=%s tx_hash=%s '
                'detected_by=%s detected_by_source=%s payload_detected_by=%s source_type=%s '
                'evidence_source=%s provider_type=%s '
                'detection_method=%s details_detected_by=%s details_source_type=%s '
                'metadata_detected_by=%s ingestion_source=%s ingestion_method=%s',
                target_id,
                top_row_detection_debug['telemetry_id'],
                top_row_detection_debug['event_type'] or 'none',
                top_row_detection_debug['tx_hash'] or 'none',
                top_row_detection_debug['detected_by'] or 'none',
                top_row_detection_debug['detected_by_source'] or 'none',
                top_row_detection_debug['payload_detected_by'] or 'none',
                top_row_detection_debug['source_type'] or 'none',
                top_row_detection_debug['evidence_source'] or 'none',
                top_row_detection_debug['provider_type'] or 'none',
                top_row_detection_debug['detection_method'] or 'none',
                top_row_detection_debug['details_detected_by'] or 'none',
                top_row_detection_debug['details_source_type'] or 'none',
                top_row_detection_debug['metadata_detected_by'] or 'none',
                top_row_detection_debug['ingestion_source'] or 'none',
                top_row_detection_debug['ingestion_method'] or 'none',
            )

        result: dict[str, Any] = {
            'telemetry': telemetry,
            'target_id': target_id,
            'workspace_id': str(workspace_id),
            'monitored_address': _monitored_address,
            'monitored_address_normalized': (_monitored_address or '').lower() or None,
            'chain_network': _monitored_chain_network,
            'live_telemetry_ready': len(telemetry) > 0,
            # Separated worker facts for the Telemetry page header. realtime_enabled
            # reflects canonical backend facts (worker actively delivering heads),
            # not just this process's env flag; realtime_state distinguishes
            # active / enabled / paused so the UI can render "Active" (requirement 5).
            'realtime_enabled': _realtime_effective_enabled,
            'realtime_state': _realtime_state,
            'last_stable_poll_at': last_stable_poll_at,
            'last_realtime_event_at': last_realtime_event_at,
            'top_row_detection_debug': top_row_detection_debug,
            'total_count': total_count,
            'page': _effective_offset // _effective_limit if _effective_limit > 0 else 0,
            'page_size': _effective_limit,
            'has_next': (_effective_offset + len(telemetry)) < total_count,
            'has_prev': _effective_offset > 0,
            'has_more': (_effective_offset + len(telemetry)) < total_count,  # compat
            'offset': _effective_offset,
            'limit': _effective_limit,
        }
        if _q:
            result['query'] = _q
        if _etf:
            result['event_type_filter'] = _etf
        if not telemetry:
            if _q:
                result['message'] = f'No telemetry found matching {_q!r}. The tx_hash will appear once the worker detects the transaction.'
            else:
                result['message'] = 'No live telemetry has been persisted for this target yet.'
        return result


def backfill_target_block_range(
    request: Request,
    target_id: str,
    from_block: int,
    to_block: int,
) -> dict[str, Any]:
    """Replay a block range for a wallet target and persist matching native transfers.

    Scans every block in [from_block, to_block] via eth_getBlockByNumber and
    persists wallet_transfer/native_transfer telemetry rows for any transaction
    whose from/to matches the monitored wallet.  Dedupes by idempotency key
    (workspace_id:target_id:block:txhash:-1) so repeated calls are safe.
    """
    from services.api.app.evm_activity_provider import (
        resolve_monitored_wallet,
        resolve_chain_rpc,
        FailoverJsonRpcClient,
        CHAIN_MAP,
        _hex_to_int,
        _iso_from_block_ts,
        _build_base_payload,
        _event_cursor,
    )

    workspace_id = normalize_workspace_header_value(request.headers.get('x-workspace-id'))
    if not workspace_id:
        raise HTTPException(status_code=400, detail='x-workspace-id header required')

    MAX_RANGE = int(os.getenv('BACKFILL_MAX_BLOCK_RANGE', '2000'))
    if to_block < from_block:
        raise HTTPException(status_code=400, detail='to_block must be >= from_block')
    if to_block - from_block + 1 > MAX_RANGE:
        raise HTTPException(status_code=400, detail=f'block range exceeds maximum of {MAX_RANGE}')

    with pg_connection() as connection:
        target_row = connection.execute(
            'SELECT * FROM targets WHERE id = %s::uuid AND workspace_id = %s::uuid AND deleted_at IS NULL',
            (target_id, workspace_id),
        ).fetchone()
        if not target_row:
            raise HTTPException(status_code=404, detail='target not found')
        target = _json_safe_value(dict(target_row))
        if str(target.get('target_type') or '').lower() != 'wallet':
            raise HTTPException(status_code=400, detail='target must be type=wallet for native transfer backfill')

        # Resolve monitored wallet, trying fallback locations
        if not target.get('wallet_address'):
            asset_ctx = _load_target_asset_context(connection, workspace_id=workspace_id, target=target)
            if isinstance(asset_ctx, dict):
                target['asset_context'] = asset_ctx
        monitored_wallet = resolve_monitored_wallet(target)
        if not monitored_wallet:
            raise HTTPException(status_code=400, detail='monitored_wallet not configured for target')

        chain_network = str(target.get('chain_network') or 'base').strip().lower()
        chain_rpc = resolve_chain_rpc(chain_network)
        if not chain_rpc.get('rpc_url'):
            raise HTTPException(status_code=503, detail=f'EVM RPC not configured for chain {chain_network}')

        client = FailoverJsonRpcClient(chain_rpc['rpc_urls'])
        chain_id_from_map = (CHAIN_MAP.get(chain_network) or {}).get('chain_id')
        env_chain_id = int(os.getenv('STAGING_EVM_CHAIN_ID') or os.getenv('EVM_CHAIN_ID') or 0) or 1
        chain_id = chain_id_from_map or env_chain_id

        # Verify RPC chain matches target chain — fail closed on mismatch
        try:
            rpc_chain_id = _hex_to_int(client.call('eth_chainId', []))
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f'RPC eth_chainId probe failed: {exc!s:.200}')
        if chain_rpc.get('expected_chain_id') and rpc_chain_id is not None and rpc_chain_id != chain_rpc['expected_chain_id']:
            raise HTTPException(
                status_code=400,
                detail=f'RPC serves chain_id={rpc_chain_id} but target expects chain_id={chain_rpc["expected_chain_id"]} ({chain_network})',
            )

        asset_id = str(target.get('asset_id')) if target.get('asset_id') else None
        found_transfers: list[dict[str, Any]] = []
        persisted_ids: list[str] = []
        blocks_scanned = 0
        failed_blocks: list[int] = []
        skipped_duplicate = 0

        for block_number in range(from_block, to_block + 1):
            try:
                block = client.call('eth_getBlockByNumber', [hex(block_number), True]) or {}
            except Exception as block_exc:
                failed_blocks.append(block_number)
                logger.warning(
                    'backfill_block_fetch_failed target_id=%s block=%s error=%s',
                    target_id, block_number, str(block_exc)[:200],
                )
                continue
            blocks_scanned += 1
            block_hash = str(block.get('hash') or '')
            block_ts = _iso_from_block_ts(block.get('timestamp'))
            txs = block.get('transactions') or []
            for tx in txs:
                tx_from = str(tx.get('from') or '').lower()
                tx_to = str(tx.get('to') or '').lower()
                if monitored_wallet not in {tx_from, tx_to}:
                    continue
                tx_hash = str(tx.get('hash') or '')
                direction = 'outbound' if tx_from == monitored_wallet else 'inbound'
                cursor_value = _event_cursor(block_number, tx_hash, None)
                # Idempotency key matches what the live worker would write
                idempotency_key = f'{workspace_id}:{target_id}:{cursor_value}'
                payload = _build_base_payload(
                    target=target,
                    network=chain_network,
                    chain_id=chain_id,
                    block_number=block_number,
                    block_hash=block_hash or str(tx.get('blockHash') or ''),
                    tx=tx,
                    tx_hash=tx_hash,
                    raw_reference=f'{chain_network}:{tx_hash}',
                )
                payload['observed_at'] = block_ts.isoformat()
                payload['event_type'] = 'transaction'
                payload['source_type'] = 'rpc_polling'
                payload['wallet_transfer_direction'] = direction
                payload['backfill'] = True
                # Block-range replay scans over the plain HTTPS RPC exactly like the
                # stable poller — tag it stable_rpc_polling so the Detected By column
                # is truthful and never blank for these native_transfer rows.
                payload['detected_by'] = 'stable_rpc_polling'
                payload['evidence_source'] = 'live'
                telem_id = str(uuid.uuid4())
                payload_json = _json_dumps(payload)
                payload_hash = hashlib.sha256(payload_json.encode('utf-8')).hexdigest()
                try:
                    with connection.transaction():
                        result_cursor = connection.execute(
                            """
                            INSERT INTO telemetry_events (
                                id, workspace_id, asset_id, target_id, provider_type, event_type,
                                observed_at, evidence_source, payload_hash, payload_json, idempotency_key
                            )
                            VALUES (%s::uuid, %s::uuid, %s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s::jsonb, %s)
                            ON CONFLICT (workspace_id, target_id, idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING
                            """,
                            (
                                telem_id,
                                workspace_id,
                                asset_id,
                                target_id,
                                'evm_rpc',
                                'native_transfer',
                                block_ts,
                                'live',
                                payload_hash,
                                payload_json,
                                idempotency_key,
                            ),
                        )
                        inserted = result_cursor.rowcount if hasattr(result_cursor, 'rowcount') else 1
                except Exception as ins_exc:
                    logger.exception(
                        'backfill_insert_failed target_id=%s tx_hash=%s block=%s error=%s',
                        target_id, tx_hash, block_number, str(ins_exc)[:200],
                    )
                    continue
                if inserted == 0:
                    skipped_duplicate += 1
                else:
                    persisted_ids.append(telem_id)
                value_wei = _hex_to_int(tx.get('value')) or 0
                found_transfers.append({
                    'tx_hash': tx_hash,
                    'block_number': block_number,
                    'from': tx_from,
                    'to': tx_to,
                    'direction': direction,
                    'amount_wei': str(value_wei),
                    'amount_eth': round(value_wei / 10 ** 18, 18),
                    'chain_id': chain_id,
                    'observed_at': block_ts.isoformat(),
                    'persisted': inserted != 0,
                })
                logger.info(
                    'backfill_transfer_found target_id=%s tx_hash=%s from=%s to=%s block=%s direction=%s persisted=%s',
                    target_id, tx_hash, tx_from, tx_to, block_number, direction, inserted != 0,
                )

    logger.info(
        'backfill_complete target_id=%s workspace_id=%s from_block=%s to_block=%s '
        'blocks_scanned=%s failed_blocks=%s transfers_found=%s persisted=%s duplicates=%s',
        target_id, workspace_id, from_block, to_block,
        blocks_scanned, len(failed_blocks), len(found_transfers), len(persisted_ids), skipped_duplicate,
    )
    return {
        'target_id': target_id,
        'workspace_id': workspace_id,
        'monitored_wallet': monitored_wallet,
        'chain_network': chain_network,
        'chain_id': chain_id,
        'from_block': from_block,
        'to_block': to_block,
        'blocks_scanned': blocks_scanned,
        'failed_blocks': failed_blocks,
        'wallet_transfers_found': len(found_transfers),
        'persisted_telemetry_ids': persisted_ids,
        'skipped_duplicates': skipped_duplicate,
        'transfers': found_transfers,
    }


def ingest_tx_by_hash(
    request: Request,
    target_id: str,
    tx_hash: str,
) -> dict[str, Any]:
    """Import a single transaction by hash without requiring a full block-range backfill.

    Useful when a transaction block is older than the current scan window.
    Fetches the transaction via eth_getTransactionByHash + eth_getTransactionReceipt,
    verifies the chain_id and monitored wallet match, then persists a
    wallet_transfer_detected telemetry row.  Idempotent — safe to call multiple times.
    """
    from services.api.app.evm_activity_provider import (
        resolve_monitored_wallet,
        resolve_chain_rpc,
        FailoverJsonRpcClient,
        CHAIN_MAP,
        _hex_to_int,
        _iso_from_block_ts,
        _build_base_payload,
        _event_cursor,
    )

    _log = logging.getLogger(__name__)

    if not tx_hash or not str(tx_hash).startswith('0x') or len(str(tx_hash)) != 66:
        raise HTTPException(status_code=400, detail='tx_hash must be a 66-char 0x-prefixed hex string')
    tx_hash_norm = str(tx_hash).lower()

    workspace_id = normalize_workspace_header_value(request.headers.get('x-workspace-id'))
    if not workspace_id:
        raise HTTPException(status_code=400, detail='x-workspace-id header required')

    _log.info(
        'tx_hash_import_started target_id=%s tx_hash=%s workspace_id=%s',
        target_id, tx_hash_norm, workspace_id,
    )

    with pg_connection() as connection:
        target_row = connection.execute(
            'SELECT * FROM targets WHERE id = %s::uuid AND workspace_id = %s::uuid AND deleted_at IS NULL',
            (target_id, workspace_id),
        ).fetchone()
        if not target_row:
            raise HTTPException(status_code=404, detail='target not found')
        target = _json_safe_value(dict(target_row))
        if str(target.get('target_type') or '').lower() != 'wallet':
            raise HTTPException(status_code=400, detail='target must be type=wallet for tx hash import')

        if not target.get('wallet_address'):
            asset_ctx = _load_target_asset_context(connection, workspace_id=workspace_id, target=target)
            if isinstance(asset_ctx, dict):
                target['asset_context'] = asset_ctx
        monitored_wallet = resolve_monitored_wallet(target)
        if not monitored_wallet:
            raise HTTPException(status_code=400, detail='monitored_wallet not configured for target')

        chain_network = str(target.get('chain_network') or 'base').strip().lower()
        chain_rpc = resolve_chain_rpc(chain_network)
        if not chain_rpc.get('rpc_url'):
            raise HTTPException(status_code=503, detail=f'EVM RPC not configured for chain {chain_network}')

        client = FailoverJsonRpcClient(chain_rpc['rpc_urls'])
        chain_id_from_map = (CHAIN_MAP.get(chain_network) or {}).get('chain_id')
        env_chain_id = int(os.getenv('STAGING_EVM_CHAIN_ID') or os.getenv('EVM_CHAIN_ID') or 0) or 1
        chain_id = chain_id_from_map or env_chain_id
        expected_chain_id = chain_rpc.get('expected_chain_id')

        try:
            rpc_chain_id = _hex_to_int(client.call('eth_chainId', []))
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f'RPC eth_chainId probe failed: {str(exc)[:200]}')
        if expected_chain_id and rpc_chain_id is not None and rpc_chain_id != expected_chain_id:
            raise HTTPException(
                status_code=400,
                detail=f'RPC serves chain_id={rpc_chain_id} but target expects chain_id={expected_chain_id} ({chain_network})',
            )

        try:
            tx = client.call('eth_getTransactionByHash', [tx_hash_norm])
        except Exception as exc:
            _log.warning(
                'tx_hash_import_skipped_reason target_id=%s tx_hash=%s reason=rpc_error error=%s',
                target_id, tx_hash_norm, str(exc)[:200],
            )
            raise HTTPException(status_code=503, detail=f'eth_getTransactionByHash failed: {str(exc)[:200]}')

        if not tx:
            _log.info(
                'tx_hash_import_skipped_reason target_id=%s tx_hash=%s reason=transaction_not_found',
                target_id, tx_hash_norm,
            )
            return {
                'target_id': target_id,
                'tx_hash': tx_hash_norm,
                'imported': False,
                'reason': 'transaction_not_found',
                'message': 'Transaction not found on the RPC endpoint. It may be on a different chain or the RPC does not index this tx.',
            }

        try:
            receipt = client.call('eth_getTransactionReceipt', [tx_hash_norm]) or {}
        except Exception:
            receipt = {}

        tx_chain_id = _hex_to_int(tx.get('chainId'))
        if tx_chain_id is not None and expected_chain_id is not None and tx_chain_id != expected_chain_id:
            _log.info(
                'tx_hash_import_skipped_reason target_id=%s tx_hash=%s reason=chain_id_mismatch '
                'tx_chain_id=%s expected_chain_id=%s',
                target_id, tx_hash_norm, tx_chain_id, expected_chain_id,
            )
            return {
                'target_id': target_id,
                'tx_hash': tx_hash_norm,
                'imported': False,
                'reason': 'chain_id_mismatch',
                'tx_chain_id': tx_chain_id,
                'expected_chain_id': expected_chain_id,
            }

        tx_from = str(tx.get('from') or '').lower()
        tx_to = str(tx.get('to') or '').lower()
        if monitored_wallet not in {tx_from, tx_to}:
            _log.info(
                'tx_hash_import_skipped_reason target_id=%s tx_hash=%s reason=wallet_not_in_tx '
                'monitored_wallet=%s tx_from=%s tx_to=%s',
                target_id, tx_hash_norm, monitored_wallet, tx_from, tx_to,
            )
            return {
                'target_id': target_id,
                'tx_hash': tx_hash_norm,
                'imported': False,
                'reason': 'wallet_not_in_tx',
                'monitored_wallet': monitored_wallet,
                'tx_from': tx_from,
                'tx_to': tx_to,
            }

        direction = 'outbound' if tx_from == monitored_wallet else 'inbound'
        _log.info(
            'tx_hash_import_match_found target_id=%s tx_hash=%s direction=%s '
            'monitored_wallet=%s tx_from=%s tx_to=%s',
            target_id, tx_hash_norm, direction, monitored_wallet, tx_from, tx_to,
        )

        block_number_hex = str(tx.get('blockNumber') or receipt.get('blockNumber') or '')
        block_number = _hex_to_int(block_number_hex) or 0
        block_hash_val = str(tx.get('blockHash') or receipt.get('blockHash') or '')

        block_ts = None
        if block_hash_val:
            try:
                blk = client.call('eth_getBlockByHash', [block_hash_val, False]) or {}
                block_ts = _iso_from_block_ts(blk.get('timestamp'))
            except Exception:
                pass
        if block_ts is None and block_number:
            try:
                blk = client.call('eth_getBlockByNumber', [hex(block_number), False]) or {}
                block_ts = _iso_from_block_ts(blk.get('timestamp'))
            except Exception:
                pass
        if block_ts is None:
            block_ts = datetime.now(timezone.utc)

        asset_id = str(target.get('asset_id')) if target.get('asset_id') else None
        cursor_value = _event_cursor(block_number, tx_hash_norm, None)
        idempotency_key = f'{workspace_id}:{target_id}:{cursor_value}'

        payload = _build_base_payload(
            target=target,
            network=chain_network,
            chain_id=chain_id,
            block_number=block_number,
            block_hash=block_hash_val or None,
            tx=tx,
            tx_hash=tx_hash_norm,
            raw_reference=f'{chain_network}:{tx_hash_norm}',
        )
        payload['observed_at'] = block_ts.isoformat()
        payload['event_type'] = 'transaction'
        payload['source_type'] = 'tx_hash_import'
        payload['wallet_transfer_direction'] = direction
        payload['ingestion_method'] = 'tx_hash_import'
        # Canonical detection-path tag (acceptance rule: wallet_transfer_detected
        # rows must never have a blank Detected By). tx-hash import is the
        # realtime_tx_import path — same tag the realtime worker's bounded
        # tx-hash backfill writes, so the UI renders "Realtime Tx Import".
        payload['detected_by'] = 'realtime_tx_import'
        payload['evidence_source'] = 'live'
        if receipt:
            payload['tx_status'] = _hex_to_int(receipt.get('status'))
            payload['gas_used'] = _hex_to_int(receipt.get('gasUsed'))

        telem_id = str(uuid.uuid4())
        payload_json = _json_dumps(payload)
        payload_hash = hashlib.sha256(payload_json.encode('utf-8')).hexdigest()

        try:
            with connection.transaction():
                result_cursor = connection.execute(
                    """
                    INSERT INTO telemetry_events (
                        id, workspace_id, asset_id, target_id, provider_type, event_type,
                        observed_at, evidence_source, payload_hash, payload_json, idempotency_key
                    )
                    VALUES (%s::uuid, %s::uuid, %s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s::jsonb, %s)
                    ON CONFLICT (workspace_id, target_id, idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING
                    """,
                    (
                        telem_id,
                        workspace_id,
                        asset_id,
                        target_id,
                        'evm_rpc',
                        'wallet_transfer_detected',
                        block_ts,
                        'live',
                        payload_hash,
                        payload_json,
                        idempotency_key,
                    ),
                )
                inserted = result_cursor.rowcount if hasattr(result_cursor, 'rowcount') else 1
        except Exception as ins_exc:
            _log.exception(
                'tx_hash_import_skipped_reason target_id=%s tx_hash=%s reason=insert_failed error=%s',
                target_id, tx_hash_norm, str(ins_exc)[:200],
            )
            raise HTTPException(status_code=500, detail=f'Failed to persist telemetry: {str(ins_exc)[:200]}')

        value_wei = _hex_to_int(tx.get('value')) or 0
        if inserted == 0:
            _log.info(
                'tx_hash_import_skipped_reason target_id=%s tx_hash=%s reason=duplicate '
                'block_number=%s direction=%s',
                target_id, tx_hash_norm, block_number, direction,
            )
            return {
                'target_id': target_id,
                'tx_hash': tx_hash_norm,
                'imported': False,
                'reason': 'duplicate',
                'message': 'Transaction already imported (idempotency key conflict).',
                'block_number': block_number,
                'direction': direction,
                'monitored_wallet': monitored_wallet,
                'chain_id': chain_id,
                'amount_wei': str(value_wei),
            }

        # Fire alert evaluators immediately after new wallet_transfer_detected telemetry.
        _ingest_user_id = str(
            target.get('updated_by_user_id') or target.get('created_by_user_id') or ''
        )
        try:
            _wallet_transfer_smoke_alert(
                workspace_id=workspace_id,
                user_id=_ingest_user_id,
                target_id=target_id,
                target_name=str(target.get('name') or target_id),
                payload=payload,
                evidence_source='live',
                telemetry_id=telem_id,
            )
        except Exception as _smoke_exc:
            _log.warning(
                'tx_hash_import_smoke_alert_failed target_id=%s tx_hash=%s error=%s',
                target_id, tx_hash_norm, str(_smoke_exc)[:200],
            )
        try:
            _strategic_infrastructure_guard_alert(
                workspace_id=workspace_id,
                user_id=_ingest_user_id,
                target_id=target_id,
                target_name=str(target.get('name') or target_id),
                target_wallet_address=monitored_wallet,
                payload=payload,
                evidence_source='live',
                telemetry_id=telem_id,
            )
        except Exception as _sig_exc:
            _log.warning(
                'tx_hash_import_sig_alert_failed target_id=%s tx_hash=%s error=%s',
                target_id, tx_hash_norm, str(_sig_exc)[:200],
            )
        _log.info(
            'tx_hash_import_persisted target_id=%s tx_hash=%s telemetry_id=%s '
            'block_number=%s direction=%s amount_wei=%s chain_id=%s',
            target_id, tx_hash_norm, telem_id, block_number, direction, value_wei, chain_id,
        )
        return {
            'target_id': target_id,
            'tx_hash': tx_hash_norm,
            'imported': True,
            'telemetry_id': telem_id,
            'block_number': block_number,
            'direction': direction,
            'monitored_wallet': monitored_wallet,
            'chain_network': chain_network,
            'chain_id': chain_id,
            'amount_wei': str(value_wei),
            'amount_eth': round(value_wei / 10 ** 18, 18),
            'observed_at': block_ts.isoformat(),
        }


def diagnose_wallet_transaction(request: Request, tx_hash: str) -> dict[str, Any]:
    """Explain, read-only, whether a tx involves any active monitored wallet.

    Answers the operator question "I sent ETH from MetaMask — why didn't Decoda
    detect it?" without mutating anything. For the given tx_hash it reports
    ``chain_id``, ``block_number``, ``from``, ``to``, ``value`` and, for every active
    wallet target in the workspace, whether ``from``/``to`` matches the monitored
    wallet (normalised) and whether a telemetry row was already persisted for it.

    Workspace-scoped — requires x-workspace-id header. Never persists telemetry; use
    POST /ops/monitoring/targets/{id}/import-tx to actually ingest a matched tx.
    """
    from services.api.app.evm_activity_provider import (
        CHAIN_MAP,
        FailoverJsonRpcClient,
        _hex_to_int,
        explain_wallet_transfer_match,
        resolve_chain_rpc,
        resolve_monitored_wallet,
    )

    _log = logging.getLogger(__name__)
    if not tx_hash or not str(tx_hash).startswith('0x') or len(str(tx_hash)) != 66:
        raise HTTPException(status_code=400, detail='tx_hash must be a 66-char 0x-prefixed hex string')
    tx_hash_norm = str(tx_hash).lower()

    workspace_id = normalize_workspace_header_value(request.headers.get('x-workspace-id'))
    if not workspace_id:
        raise HTTPException(status_code=400, detail='x-workspace-id header required')

    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        rows = connection.execute(
            '''
            SELECT id, workspace_id, name, target_type, chain_network,
                   wallet_address, contract_identifier, target_metadata, asset_id,
                   monitoring_enabled, enabled, is_active
            FROM targets
            WHERE workspace_id = %s::uuid
              AND deleted_at IS NULL
              AND target_type = 'wallet'
            ORDER BY is_active DESC, monitoring_enabled DESC
            LIMIT 100
            ''',
            (workspace_id,),
        ).fetchall()
        targets = [_json_safe_value(dict(r)) for r in rows]
        if not targets:
            return {
                'tx_hash': tx_hash_norm,
                'workspace_id': str(workspace_id),
                'tx_found': False,
                'reason': 'no_wallet_targets',
                'realtime_verdict': 'not_matched_no_watched_wallet_in_tx',
                'matches': [],
                'targets_checked': 0,
            }

        # Resolve the monitored wallet + active-state for each target up front.
        for target in targets:
            if not target.get('wallet_address'):
                asset_ctx = _load_target_asset_context(connection, workspace_id=str(workspace_id), target=target)
                if isinstance(asset_ctx, dict):
                    target['asset_context'] = asset_ctx
            target['_monitored_wallet'] = resolve_monitored_wallet(target)
            target['_active'] = bool(
                target.get('monitoring_enabled') and target.get('enabled') and target.get('is_active')
            )

        # Fetch the tx (and its receipt) once per distinct chain the targets live on.
        # The receipt gives the on-chain execution status (requirement 1): a reverted
        # send explains "no telemetry" with no Decoda-side bug.
        tx_by_chain: dict[str, dict[str, Any] | None] = {}
        receipt_by_chain: dict[str, dict[str, Any]] = {}
        chain_meta: dict[str, dict[str, Any]] = {}
        client_by_chain: dict[str, Any] = {}
        for chain_network in {str(t.get('chain_network') or 'base').strip().lower() for t in targets}:
            chain_rpc = resolve_chain_rpc(chain_network)
            expected_chain_id = chain_rpc.get('expected_chain_id') or (CHAIN_MAP.get(chain_network) or {}).get('chain_id')
            chain_meta[chain_network] = {'expected_chain_id': expected_chain_id, 'rpc_configured': bool(chain_rpc.get('rpc_url'))}
            if not chain_rpc.get('rpc_url'):
                tx_by_chain[chain_network] = None
                continue
            try:
                client = FailoverJsonRpcClient(chain_rpc['rpc_urls'])
                client_by_chain[chain_network] = client
                tx_by_chain[chain_network] = client.call('eth_getTransactionByHash', [tx_hash_norm]) or None
                if tx_by_chain[chain_network]:
                    try:
                        receipt_by_chain[chain_network] = client.call('eth_getTransactionReceipt', [tx_hash_norm]) or {}
                    except Exception:
                        receipt_by_chain[chain_network] = {}
            except Exception as exc:
                _log.warning(
                    'diagnose_tx_rpc_failed tx_hash=%s chain=%s error=%s',
                    tx_hash_norm, chain_network, str(exc)[:200],
                )
                tx_by_chain[chain_network] = None

        any_tx_chain = next((cn for cn, tx in tx_by_chain.items() if tx), None)
        any_tx = tx_by_chain.get(any_tx_chain) if any_tx_chain else None
        any_receipt = receipt_by_chain.get(any_tx_chain or '', {})
        receipt_status = _hex_to_int(any_receipt.get('status')) if isinstance(any_receipt, dict) else None
        block_number = _hex_to_int((any_tx or {}).get('blockNumber')) if any_tx else None

        # Realtime forward-scan window (requirement 1): read the realtime worker's
        # checkpoint + cold-start floor so we can report whether this tx block was ever
        # in the scan window. A tx below the cold-start floor (the production case: tx
        # block far below the realtime checkpoint) was never forward-scanned and is only
        # recoverable via import-tx. Best-effort — never breaks the read-only diagnostic.
        realtime_checkpoint_block: int | None = None
        realtime_scan_start_block: int | None = None
        realtime_scanned_spans: list[list[int]] = []
        realtime_rate_limit_windows: list[dict[str, Any]] = []
        live_tail_from_block: int | None = None
        live_tail_to_block: int | None = None
        try:
            _wr = connection.execute(
                '''
                SELECT last_processed_block, metrics
                FROM monitoring_watcher_state
                ORDER BY COALESCE(last_heartbeat_at, updated_at) DESC
                LIMIT 1
                '''
            ).fetchone()
            if _wr is not None:
                _wr = _json_safe_value(dict(_wr))
                if _wr.get('last_processed_block') is not None:
                    realtime_checkpoint_block = int(_wr['last_processed_block'])
                _metrics = _wr.get('metrics') if isinstance(_wr.get('metrics'), dict) else {}
                _ssb = _metrics.get('scan_start_block')
                if _ssb is not None:
                    realtime_scan_start_block = int(_ssb)
                # Span-truthful scan coverage + rate-limit cooldown history + live-tail
                # window persisted by the realtime worker heartbeat — the same facts the
                # worker's own tx debug uses, so the endpoint can never disagree with it.
                _raw_spans = _metrics.get('scanned_spans')
                if isinstance(_raw_spans, list):
                    for _sp in _raw_spans:
                        if isinstance(_sp, (list, tuple)) and len(_sp) == 2:
                            try:
                                realtime_scanned_spans.append([int(_sp[0]), int(_sp[1])])
                            except (TypeError, ValueError):
                                continue
                _raw_windows = _metrics.get('rate_limit_windows')
                if isinstance(_raw_windows, list):
                    realtime_rate_limit_windows = [w for w in _raw_windows if isinstance(w, dict)]
                if _metrics.get('live_tail_from_block') is not None:
                    live_tail_from_block = int(_metrics['live_tail_from_block'])
                if _metrics.get('live_tail_to_block') is not None:
                    live_tail_to_block = int(_metrics['live_tail_to_block'])
        except Exception:
            _log.warning('diagnose_tx_checkpoint_read_failed tx_hash=%s', tx_hash_norm, exc_info=True)

        # was_block_scanned: prefer the span-truthful record of what the realtime
        # worker ACTUALLY scanned. Only when no spans were ever persisted (older
        # worker build) fall back to the legacy [scan_start_block, checkpoint]
        # inference — which over-claims across rate-limit cooldown gaps.
        if realtime_scanned_spans:
            was_block_scanned = bool(
                block_number is not None
                and any(s[0] <= block_number <= s[1] for s in realtime_scanned_spans)
            )
        else:
            was_block_scanned = bool(
                block_number is not None
                and realtime_checkpoint_block is not None
                and realtime_scan_start_block is not None
                and realtime_scan_start_block <= block_number <= realtime_checkpoint_block
            )
        below_realtime_checkpoint = bool(
            block_number is not None
            and realtime_checkpoint_block is not None
            and block_number <= realtime_checkpoint_block
        )

        # Requirement 5: was the provider rate-limited when this tx landed? Answered
        # from the worker's persisted cooldown windows against the tx block's
        # on-chain timestamp. The block header is fetched lazily — only when a
        # cooldown was ever recorded — and a fetch failure yields 'unknown', never a
        # false claim.
        rate_limited_at_time: Any = False
        rate_limit_next_retry_at: str | None = None
        if realtime_rate_limit_windows and block_number is not None and any_tx_chain:
            rate_limited_at_time = 'unknown'
            _hdr_client = client_by_chain.get(any_tx_chain)
            _tx_ts: datetime | None = None
            if _hdr_client is not None:
                try:
                    _hdr = _hdr_client.call('eth_getBlockByNumber', [hex(int(block_number)), False]) or {}
                    _ts_int = _hex_to_int(_hdr.get('timestamp')) if isinstance(_hdr, dict) else None
                    if _ts_int is not None:
                        _tx_ts = datetime.fromtimestamp(_ts_int, tz=timezone.utc)
                except Exception:
                    _tx_ts = None
            if _tx_ts is not None:
                rate_limited_at_time = False
                for _win in reversed(realtime_rate_limit_windows):
                    try:
                        _w_start = datetime.fromisoformat(str(_win.get('started_at')))
                        _w_end_raw = _win.get('ended_at') or _win.get('next_retry_at')
                        _w_end = datetime.fromisoformat(str(_w_end_raw)) if _w_end_raw else None
                    except (TypeError, ValueError):
                        continue
                    if _w_start.tzinfo is None:
                        _w_start = _w_start.replace(tzinfo=timezone.utc)
                    if _w_end is not None and _w_end.tzinfo is None:
                        _w_end = _w_end.replace(tzinfo=timezone.utc)
                    if _w_start <= _tx_ts and (_w_end is None or _tx_ts <= _w_end):
                        rate_limited_at_time = True
                        rate_limit_next_retry_at = _win.get('next_retry_at')
                        break

        matches: list[dict[str, Any]] = []
        for target in targets:
            chain_network = str(target.get('chain_network') or 'base').strip().lower()
            tx = tx_by_chain.get(chain_network)
            monitored_wallet = target.get('_monitored_wallet')
            explanation = explain_wallet_transfer_match(monitored_wallet, tx if isinstance(tx, dict) else None)
            # Was a telemetry row already persisted for this tx + target — and by WHOM?
            # Inspect EVERY persisted detection-path fact (top-level detected_by,
            # source_type, details/metadata copies, ingestion markers, the
            # provider_type column = created_by_worker) and log them, so a row that
            # renders "Unknown" can be traced to exactly which fact is missing.
            persisted_row = connection.execute(
                '''
                SELECT id, event_type, provider_type, evidence_source, observed_at, payload_json
                FROM telemetry_events
                WHERE workspace_id = %s::uuid AND target_id = %s::uuid
                  AND lower(payload_json->>'tx_hash') = %s
                LIMIT 1
                ''',
                (workspace_id, target['id'], tx_hash_norm),
            ).fetchone()
            already_persisted = persisted_row is not None
            existing_detected_by = None
            persisted_row_inspection: dict[str, Any] | None = None
            if already_persisted:
                _persisted_dict = _json_safe_value(dict(persisted_row))
                _p_payload = _persisted_dict.get('payload_json') if isinstance(_persisted_dict.get('payload_json'), dict) else {}
                _p_details = _p_payload.get('details') if isinstance(_p_payload.get('details'), dict) else {}
                _p_metadata = _p_payload.get('metadata') if isinstance(_p_payload.get('metadata'), dict) else {}
                _classified_by, _classified_basis = classify_wallet_transfer_detected_by(
                    payload=_p_payload,
                    provider_type=_persisted_dict.get('provider_type'),
                    event_type=_persisted_dict.get('event_type'),
                    evidence_source=_persisted_dict.get('evidence_source'),
                )
                existing_detected_by = _classified_by or 'unknown'
                persisted_row_inspection = {
                    'telemetry_id': _persisted_dict.get('id'),
                    'event_type': _persisted_dict.get('event_type'),
                    'observed_at': _persisted_dict.get('observed_at'),
                    'evidence_source': _persisted_dict.get('evidence_source'),
                    'top_level_detected_by': _p_payload.get('detected_by'),
                    'source_type': _p_payload.get('source_type'),
                    'details_detected_by': _p_details.get('detected_by'),
                    'details_source_type': _p_details.get('source_type'),
                    'metadata_detected_by': _p_metadata.get('detected_by'),
                    'ingestion_path': _p_payload.get('ingestion_source') or _p_payload.get('ingestion_method'),
                    'created_by_worker': _persisted_dict.get('provider_type'),
                    'resolved_detected_by': existing_detected_by,
                    'resolved_basis': _classified_basis,
                }
                _log.info(
                    'tx_persisted_row_inspection tx_hash=%s target_id=%s telemetry_id=%s '
                    'top_level_detected_by=%s source_type=%s event_type=%s '
                    'details_detected_by=%s details_source_type=%s metadata_detected_by=%s '
                    'ingestion_path=%s created_by_worker=%s evidence_source=%s '
                    'resolved_detected_by=%s resolved_basis=%s',
                    tx_hash_norm, target['id'],
                    persisted_row_inspection['telemetry_id'],
                    persisted_row_inspection['top_level_detected_by'] or 'none',
                    persisted_row_inspection['source_type'] or 'none',
                    persisted_row_inspection['event_type'] or 'none',
                    persisted_row_inspection['details_detected_by'] or 'none',
                    persisted_row_inspection['details_source_type'] or 'none',
                    persisted_row_inspection['metadata_detected_by'] or 'none',
                    persisted_row_inspection['ingestion_path'] or 'none',
                    persisted_row_inspection['created_by_worker'] or 'none',
                    persisted_row_inspection['evidence_source'] or 'none',
                    existing_detected_by, _classified_basis,
                )
            # Explicit per-target match flags + normalized addresses (requirement 1) so
            # an operator sees exactly which side matched and the lowercase forms
            # compared, without inferring it from `direction`.
            norm_target = explanation.get('monitored_wallet')
            norm_from = explanation.get('tx_from')
            norm_to = explanation.get('tx_to')
            from_matches = bool(norm_target and norm_from and norm_from == norm_target)
            to_matches = bool(norm_target and norm_to and norm_to == norm_target)
            if explanation.get('matched') and not target.get('_active'):
                persist_reason = 'target_inactive_not_persisted'
            elif explanation.get('matched') and already_persisted:
                persist_reason = 'already_persisted'
            elif explanation.get('matched') and below_realtime_checkpoint and not was_block_scanned:
                # Matched, but the tx block is below the realtime cold-start floor, so
                # the forward WebSocket scan never reached it — the exact production
                # miss. import-tx (or the bounded tx-hash backfill) is the recovery.
                persist_reason = 'matched_below_realtime_checkpoint_run_import_tx'
            elif explanation.get('matched'):
                persist_reason = 'matched_not_yet_persisted_run_import_tx'
            elif not monitored_wallet:
                persist_reason = 'monitored_wallet_not_configured'
            elif tx is None:
                persist_reason = (
                    'tx_not_found_on_chain_rpc' if chain_meta.get(chain_network, {}).get('rpc_configured')
                    else 'chain_rpc_not_configured'
                )
            else:
                persist_reason = 'address_not_watched'
            matches.append({
                'target_id': str(target['id']),
                'target_name': target.get('name'),
                'chain_network': chain_network,
                'chain_id': chain_meta.get(chain_network, {}).get('expected_chain_id'),
                'monitored_address_full': monitored_wallet,
                'normalized_address_lowercase': (monitored_wallet or '').lower() or None,
                'normalized_from': norm_from,
                'normalized_to': norm_to,
                'normalized_target': norm_target,
                'from_matches': from_matches,
                'to_matches': to_matches,
                'active': bool(target.get('_active')),
                'matched': bool(explanation.get('matched')),
                'direction': explanation.get('wallet_transfer_direction'),
                'was_block_scanned': was_block_scanned,
                'already_persisted': already_persisted,
                'existing_detected_by': existing_detected_by,
                'persisted_row_inspection': persisted_row_inspection,
                # Requirement 4 (truthful UI): the tx matched but a row from another
                # detector (the 300s stable polling worker) already exists — realtime
                # skipped it as a duplicate rather than re-claiming the detection.
                'realtime_duplicate_skipped': bool(
                    explanation.get('matched')
                    and already_persisted
                    and existing_detected_by not in REALTIME_DETECTED_BY
                ),
                'persist_reason': persist_reason,
            })

        tx_chain_id = _hex_to_int((any_tx or {}).get('chainId')) if any_tx else None
        value_wei = _hex_to_int((any_tx or {}).get('value')) if any_tx else None
        tx_from = str((any_tx or {}).get('from') or '').lower() or None
        tx_to = str((any_tx or {}).get('to') or '').lower() or None
        _matched_count = sum(1 for m in matches if m['matched'])
        # WHO already owns this tx, taken from the first matched target with a
        # persisted row — drives the duplicate-skip fact and the final verdict.
        _existing_by = next(
            (m['existing_detected_by'] for m in matches if m['matched'] and m['existing_detected_by']),
            None,
        )
        if _existing_by and any(m.get('realtime_duplicate_skipped') for m in matches):
            # Requirement 4: same canonical duplicate marker the worker emits, so a
            # log query keyed on realtime_duplicate_existing_tx finds both paths.
            _log.info(
                'realtime_duplicate_existing_tx tx_hash=%s existing_detected_by=%s '
                'attempted_detected_by=diagnose_tx',
                tx_hash_norm, _existing_by,
            )
        if (
            _matched_count and _existing_by is None
            and rate_limited_at_time is True and not was_block_scanned
        ):
            # Requirement 5: the tx landed during a provider rate-limit cooldown and
            # realtime never scanned its block. Stable polling remains the fallback.
            _log.warning(
                'realtime_tx_missed_due_to_rate_limit tx_hash=%s block_number=%s next_retry_at=%s',
                tx_hash_norm, block_number if block_number is not None else 'none',
                rate_limit_next_retry_at or 'none',
            )
        if bool(any_tx) and block_number is not None and not was_block_scanned and _existing_by is None:
            # Requirement 2: canonical not-in-scanned-window marker with the exact
            # window the realtime worker actually covered.
            _scanned_from = realtime_scanned_spans[0][0] if realtime_scanned_spans else None
            _scanned_to = realtime_scanned_spans[-1][1] if realtime_scanned_spans else None
            _log.warning(
                'realtime_tx_not_in_scanned_window tx_hash=%s tx_block=%s scanned_from=%s scanned_to=%s',
                tx_hash_norm, block_number,
                _scanned_from if _scanned_from is not None else 'none',
                _scanned_to if _scanned_to is not None else 'none',
            )
        # Acceptance: one canonical verdict, shared with the worker's tx debug via
        # classify_realtime_tx_verdict so the two paths can never disagree. The
        # endpoint is read-only, so "outside scanned window" verdicts point at the
        # import-tx recovery instead of importing here.
        realtime_verdict = classify_realtime_tx_verdict(
            tx_found=bool(any_tx),
            matched=bool(_matched_count),
            existing_detected_by=_existing_by,
            was_block_scanned=was_block_scanned,
            rate_limited_at_tx_time=rate_limited_at_time is True,
            below_checkpoint=below_realtime_checkpoint,
        )
        _log.info(
            'diagnose_tx_completed tx_hash=%s workspace_id=%s tx_found=%s targets_checked=%s matched=%s',
            tx_hash_norm, workspace_id, bool(any_tx), len(targets), _matched_count,
        )
        # Canonical realtime_tx_debug marker (requirement 1) so the read-only endpoint
        # and the env-var worker path emit the same log fields — status, checkpoint +
        # live-tail context, whether the block was actually scanned, and the provider
        # mode / rate-limit state when the tx landed.
        _log.info(
            'realtime_tx_debug tx_hash=%s block_number=%s from=%s to=%s value=%s status=%s '
            'chain_id=%s live_tail_from_block=%s live_tail_to_block=%s '
            'checkpoint_block=%s scan_start_block=%s was_block_scanned=%s '
            'provider_mode_at_time=%s rate_limited_at_time=%s '
            'below_realtime_checkpoint=%s matched_target_count=%s',
            tx_hash_norm, block_number if block_number is not None else 'none',
            tx_from or 'none', tx_to or 'none',
            value_wei if value_wei is not None else 'none',
            receipt_status if receipt_status is not None else 'none',
            tx_chain_id if tx_chain_id is not None else 'none',
            live_tail_from_block if live_tail_from_block is not None else 'none',
            live_tail_to_block if live_tail_to_block is not None else 'none',
            realtime_checkpoint_block if realtime_checkpoint_block is not None else 'none',
            realtime_scan_start_block if realtime_scan_start_block is not None else 'none',
            was_block_scanned,
            'rate_limited' if rate_limited_at_time is True else (
                'realtime_scanned' if was_block_scanned else 'unknown'
            ),
            rate_limited_at_time,
            below_realtime_checkpoint, _matched_count,
        )
        _log.info(
            'realtime_tx_verdict tx_hash=%s verdict=%s block_number=%s was_block_scanned=%s '
            'rate_limited_at_time=%s existing_detected_by=%s next_retry_at=%s',
            tx_hash_norm, realtime_verdict,
            block_number if block_number is not None else 'none',
            was_block_scanned, rate_limited_at_time, _existing_by or 'none',
            rate_limit_next_retry_at or 'none',
        )
        return {
            'tx_hash': tx_hash_norm,
            'workspace_id': str(workspace_id),
            'tx_found': bool(any_tx),
            'chain_id': tx_chain_id,
            'block_number': block_number,
            'from': tx_from,
            'to': tx_to,
            'value_wei': str(value_wei) if value_wei is not None else None,
            'receipt_status': receipt_status,
            # Realtime scan-window context (requirement 1). was_block_scanned is the
            # smoking gun: False for a block the worker never actually scanned (cold
            # start skip OR rate-limit cooldown gap) → realtime structurally could not
            # catch it → run import-tx to recover.
            'realtime_checkpoint_block': realtime_checkpoint_block,
            'realtime_scan_start_block': realtime_scan_start_block,
            'realtime_scanned_spans': realtime_scanned_spans,
            'live_tail_from_block': live_tail_from_block,
            'live_tail_to_block': live_tail_to_block,
            'was_block_scanned': was_block_scanned,
            'below_realtime_checkpoint': below_realtime_checkpoint,
            'rate_limited_at_time': rate_limited_at_time,
            'rate_limit_next_retry_at': rate_limit_next_retry_at,
            'existing_detected_by': _existing_by,
            'realtime_duplicate_skipped': bool(
                any(m.get('realtime_duplicate_skipped') for m in matches)
            ),
            # Acceptance: the single clear answer for this tx hash.
            'realtime_verdict': realtime_verdict,
            'targets_checked': len(targets),
            'matched_target_count': _matched_count,
            'matches': matches,
        }


def inspect_target_dead_letter_state(
    request: Request,
    target_id: str,
) -> dict[str, Any]:
    """Return the current dead-letter / skip state for a monitoring target.

    Useful for diagnosing why a target is not being scanned without touching
    production data.  Workspace-scoped — requires x-workspace-id header.
    """
    logger = logging.getLogger(__name__)
    workspace_id = normalize_workspace_header_value(request.headers.get('x-workspace-id'))
    if not workspace_id:
        raise HTTPException(status_code=400, detail='x-workspace-id header required')

    with pg_connection() as connection:
        target_row = connection.execute(
            '''
            SELECT
                t.id AS target_id,
                t.workspace_id,
                t.target_type,
                t.chain_network,
                t.wallet_address,
                t.monitoring_enabled,
                t.monitoring_dead_lettered_at,
                t.monitoring_delivery_attempts,
                t.last_run_status,
                t.last_checked_at,
                t.next_due_at,
                t.deleted_at
            FROM targets t
            WHERE t.id = %s::uuid
              AND t.workspace_id = %s::uuid
            ''',
            (target_id, workspace_id),
        ).fetchone()
        if not target_row:
            raise HTTPException(status_code=404, detail='target not found')
        target = _json_safe_value(dict(target_row))

        # Fetch associated monitored_systems rows
        systems_rows = connection.execute(
            '''
            SELECT
                ms.id AS system_id,
                ms.is_active,
                ms.last_heartbeat_at,
                ms.created_at
            FROM monitored_systems ms
            WHERE ms.target_id = %s::uuid
              AND ms.workspace_id = %s::uuid
            ORDER BY ms.created_at DESC
            LIMIT 5
            ''',
            (target_id, workspace_id),
        ).fetchall()
        systems = [_json_safe_value(dict(r)) for r in (systems_rows or [])]

        # Fetch workspace-scoped last heartbeat
        hb_row = connection.execute(
            'SELECT MAX(last_heartbeat_at) AS ts FROM monitoring_heartbeats WHERE workspace_id = %s::uuid',
            (workspace_id,),
        ).fetchone()
        last_workspace_heartbeat = _json_safe_value((dict(hb_row) if isinstance(hb_row, dict) else {}).get('ts'))

        # Fetch recent telemetry for this target
        telemetry_rows = connection.execute(
            '''
            SELECT event_type, evidence_source, observed_at, idempotency_key
            FROM telemetry_events
            WHERE workspace_id = %s::uuid
              AND target_id = %s::uuid
            ORDER BY observed_at DESC
            LIMIT 5
            ''',
            (workspace_id, target_id),
        ).fetchall()
        recent_telemetry = [_json_safe_value(dict(r)) for r in (telemetry_rows or [])]

    dead_lettered_at = target.get('monitoring_dead_lettered_at')
    delivery_attempts = target.get('monitoring_delivery_attempts') or 0
    is_dead_lettered = dead_lettered_at is not None
    is_deleted = target.get('deleted_at') is not None
    monitoring_enabled = bool(target.get('monitoring_enabled'))

    skip_reason: str | None = None
    if is_deleted:
        skip_reason = 'deleted'
    elif not monitoring_enabled:
        skip_reason = 'monitoring_disabled'
    elif is_dead_lettered:
        skip_reason = 'dead_lettered'

    _dead_letter_recovery_hours = int(os.getenv('MONITORING_DEAD_LETTER_RECOVERY_HOURS', '24'))
    auto_recovery_after_hours: int | None = _dead_letter_recovery_hours if is_dead_lettered else None

    logger.info(
        'inspect_target_dead_letter_state target_id=%s workspace_id=%s '
        'is_dead_lettered=%s delivery_attempts=%s skip_reason=%s last_run_status=%s',
        target_id, workspace_id, is_dead_lettered, delivery_attempts, skip_reason,
        target.get('last_run_status'),
    )
    return {
        'target_id': target_id,
        'workspace_id': workspace_id,
        'target_type': target.get('target_type'),
        'chain_network': target.get('chain_network'),
        'wallet_address': target.get('wallet_address'),
        'monitoring_enabled': monitoring_enabled,
        'is_dead_lettered': is_dead_lettered,
        'monitoring_dead_lettered_at': str(dead_lettered_at) if dead_lettered_at else None,
        'monitoring_delivery_attempts': delivery_attempts,
        'auto_recovery_after_hours': auto_recovery_after_hours,
        'last_run_status': target.get('last_run_status'),
        'last_checked_at': str(target.get('last_checked_at') or ''),
        'next_due_at': str(target.get('next_due_at') or ''),
        'skip_reason': skip_reason,
        'monitored_systems': systems,
        'last_workspace_heartbeat_at': str(last_workspace_heartbeat) if last_workspace_heartbeat else None,
        'recent_telemetry': recent_telemetry,
    }


def recover_target_dead_letter(
    request: Request,
    target_id: str,
) -> dict[str, Any]:
    """Clear dead-letter state for a target so it will be picked up on the next cycle.

    Resets monitoring_dead_lettered_at to NULL and monitoring_delivery_attempts to 0.
    Safe to call multiple times — idempotent if the target is not dead-lettered.
    Workspace-scoped — requires x-workspace-id header.
    """
    logger = logging.getLogger(__name__)
    workspace_id = normalize_workspace_header_value(request.headers.get('x-workspace-id'))
    if not workspace_id:
        raise HTTPException(status_code=400, detail='x-workspace-id header required')

    with pg_connection() as connection:
        target_row = connection.execute(
            'SELECT id, workspace_id, monitoring_dead_lettered_at, monitoring_delivery_attempts, last_run_status '
            'FROM targets WHERE id = %s::uuid AND workspace_id = %s::uuid AND deleted_at IS NULL',
            (target_id, workspace_id),
        ).fetchone()
        if not target_row:
            raise HTTPException(status_code=404, detail='target not found')
        target = dict(target_row)
        was_dead_lettered = target.get('monitoring_dead_lettered_at') is not None
        prev_attempts = target.get('monitoring_delivery_attempts') or 0

        connection.execute(
            '''
            UPDATE targets
            SET
                monitoring_dead_lettered_at = NULL,
                monitoring_delivery_attempts = 0,
                last_run_status = 'recovered'
            WHERE id = %s::uuid
              AND workspace_id = %s::uuid
            ''',
            (target_id, workspace_id),
        )
        connection.commit()

    logger.info(
        'recover_target_dead_letter target_id=%s workspace_id=%s '
        'was_dead_lettered=%s prev_delivery_attempts=%s action=cleared',
        target_id, workspace_id, was_dead_lettered, prev_attempts,
    )
    return {
        'target_id': target_id,
        'workspace_id': workspace_id,
        'was_dead_lettered': was_dead_lettered,
        'previous_delivery_attempts': prev_attempts,
        'recovered': True,
        'message': (
            'Dead-letter state cleared. Target will be picked up on the next monitoring cycle.'
            if was_dead_lettered
            else 'Target was not dead-lettered; no change needed.'
        ),
    }


def run_detection_from_existing_telemetry(request: Request) -> dict[str, Any]:
    """Process existing live wallet_transfer_detected telemetry and create detections/alerts.

    Called by POST /run-detection. Idempotent — same tx on same target always produces
    the same detection UUID (UUID5-deterministic dedup in _wallet_transfer_smoke_alert).
    Only processes evidence_source='live' telemetry; never touches simulator data.
    """
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        workspace_id = workspace_context['workspace_id']
        user_id = user['id']

        logger.info('run_detection_started workspace_id=%s user_id=%s', workspace_id, user_id)

        rows = connection.execute(
            '''
            SELECT
                te.id,
                te.target_id,
                te.payload_json,
                te.evidence_source,
                COALESCE(t.name, te.target_id::text) AS target_name,
                t.wallet_address AS target_wallet_address,
                ms.id AS monitored_system_id,
                COALESCE(te.asset_id, ms.asset_id) AS protected_asset_id
            FROM telemetry_events te
            LEFT JOIN targets t ON t.id = te.target_id
            LEFT JOIN monitored_systems ms
                ON ms.target_id = te.target_id
               AND ms.workspace_id = te.workspace_id
            WHERE te.workspace_id = %s::uuid
              AND te.event_type IN ('wallet_transfer_detected', 'native_transfer')
              AND te.evidence_source = 'live'
            ORDER BY te.observed_at DESC
            LIMIT 50
            ''',
            (workspace_id,),
        ).fetchall()

    alerts_created: list[str] = []
    for row in rows:
        telemetry_id = str(row['id'])
        target_id = str(row['target_id']) if row['target_id'] else ''
        target_name = str(row['target_name'] or target_id)
        target_wallet_address = str(row['target_wallet_address'] or '') if row['target_wallet_address'] else ''
        payload = dict(row['payload_json'] or {})
        evidence_source = str(row['evidence_source'] or 'live')
        monitored_system_id = str(row['monitored_system_id']) if row['monitored_system_id'] else None
        protected_asset_id = str(row['protected_asset_id']) if row['protected_asset_id'] else None

        alert_id = _wallet_transfer_smoke_alert(
            workspace_id=workspace_id,
            user_id=user_id,
            target_id=target_id,
            target_name=target_name,
            payload=payload,
            evidence_source=evidence_source,
            telemetry_id=telemetry_id,
            monitored_system_id=monitored_system_id,
            protected_asset_id=protected_asset_id,
        )
        if alert_id:
            logger.info(
                'alert_visible_in_workspace workspace_id=%s alert_id=%s telemetry_id=%s',
                workspace_id, alert_id, telemetry_id,
            )
            alerts_created.append(alert_id)
        sig_alert_id = _strategic_infrastructure_guard_alert(
            workspace_id=workspace_id,
            user_id=user_id,
            target_id=target_id,
            target_name=target_name,
            target_wallet_address=target_wallet_address,
            payload=payload,
            evidence_source=evidence_source,
            telemetry_id=telemetry_id,
            monitored_system_id=monitored_system_id,
            protected_asset_id=protected_asset_id,
        )
        if sig_alert_id and sig_alert_id not in alerts_created:
            logger.info(
                'sig_alert_visible_in_workspace workspace_id=%s alert_id=%s telemetry_id=%s',
                workspace_id, sig_alert_id, telemetry_id,
            )
            alerts_created.append(sig_alert_id)

    logger.info(
        'run_detection_completed workspace_id=%s user_id=%s telemetry_processed=%s alerts_created=%s',
        workspace_id, user_id, len(rows), len(alerts_created),
    )

    return {
        'status': 'completed',
        'telemetry_processed': len(rows),
        'alerts_created': len(alerts_created),
        'alert_ids': alerts_created,
    }


def backfill_missing_alerts_for_target(request: Request, *, target_id: str) -> dict[str, Any]:
    """Create alerts for wallet_transfer_detected rows that have no associated alert.

    Scans live wallet_transfer_detected telemetry for a specific target and runs the
    smoke-rule and Strategic Infrastructure Guard rule on each row. Both rules use
    UUID5-deterministic detection IDs and dedupe signatures, so this is idempotent —
    safe to call multiple times; no duplicate alerts will be created.

    If a prior poll created a detection row but the alert creation rolled back, the
    recovery path in _wallet_transfer_smoke_alert and _strategic_infrastructure_guard_alert
    will create the missing alert and link it to the existing detection.

    Only processes evidence_source='live' telemetry; never creates alerts from simulator
    or replay data.
    """
    require_live_mode()
    try:
        uuid.UUID(target_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail='target_id must be a valid UUID.')

    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        workspace_id = workspace_context['workspace_id']
        user_id = user['id']

        logger.info(
            'strategic_guard_backfill_scan_started workspace_id=%s user_id=%s target_id=%s',
            workspace_id, user_id, target_id,
        )

        # Scan EVERY wallet-transfer telemetry row for this target (newest first), not
        # only the latest/first row. evidence_source is filtered in Python (not SQL) so
        # non-live rows are still seen and logged with an explicit skip_reason instead of
        # silently disappearing — this makes a missing-alert diagnosis visible in logs.
        rows = connection.execute(
            '''
            SELECT
                te.id,
                te.target_id,
                te.event_type,
                te.observed_at,
                te.payload_json,
                te.evidence_source,
                COALESCE(t.name, te.target_id::text) AS target_name,
                t.wallet_address AS target_wallet_address,
                ms.id AS monitored_system_id,
                COALESCE(te.asset_id, ms.asset_id) AS protected_asset_id
            FROM telemetry_events te
            LEFT JOIN targets t ON t.id = te.target_id
            LEFT JOIN monitored_systems ms
                ON ms.target_id = te.target_id
               AND ms.workspace_id = te.workspace_id
            WHERE te.workspace_id = %s::uuid
              AND te.target_id = %s::uuid
              AND te.event_type IN ('wallet_transfer_detected', 'native_transfer')
            ORDER BY te.observed_at DESC
            LIMIT 200
            ''',
            (workspace_id, target_id),
        ).fetchall()

        # row_count makes the scan size auditable: confirms an older tx_hash was
        # actually fetched (not dropped by a LIMIT/recency cutoff) before per-row
        # processing decides whether it is skipped.
        logger.info(
            'strategic_guard_backfill_rows_fetched workspace_id=%s target_id=%s row_count=%s',
            workspace_id, target_id, len(rows),
        )

        # Pre-load existing alert dedupe signatures for this target so we can report
        # created-vs-deduped accurately. Tolerant of fake/stub rows in tests (.get()).
        existing_signatures: dict[str, str] = {}
        try:
            sig_rows = connection.execute(
                '''
                SELECT dedupe_signature, id
                FROM alerts
                WHERE workspace_id = %s::uuid AND target_id = %s::uuid
                  AND dedupe_signature IS NOT NULL
                ''',
                (workspace_id, target_id),
            ).fetchall()
            for sig_row in sig_rows:
                _sig = sig_row.get('dedupe_signature') if hasattr(sig_row, 'get') else None
                if _sig:
                    existing_signatures[str(_sig)] = str((sig_row.get('id') if hasattr(sig_row, 'get') else '') or '')
        except Exception:  # pragma: no cover - defensive; never block backfill on accounting query
            existing_signatures = {}

    alerts_created: list[str] = []
    created_count = 0
    deduped_count = 0
    linked_count = 0
    skipped_count = 0
    for row in rows:
        telemetry_id = str(row['id'])
        row_target_id = str(row['target_id']) if row['target_id'] else ''
        target_name = str(row['target_name'] or row_target_id)
        target_wallet_address = str(row['target_wallet_address'] or '') if row['target_wallet_address'] else ''
        payload = dict(row['payload_json'] or {})
        evidence_source = str(row['evidence_source'] or '').strip().lower()
        _row_keys = row.keys() if hasattr(row, 'keys') else []
        event_type = str(row['event_type'] or '') if 'event_type' in _row_keys else ''
        observed_at = row['observed_at'] if 'observed_at' in _row_keys else None
        monitored_system_id = str(row['monitored_system_id']) if row['monitored_system_id'] else None
        protected_asset_id = str(row['protected_asset_id']) if row['protected_asset_id'] else None
        row_tx_hash = str(payload.get('tx_hash') or payload.get('hash') or '').strip()
        _raw_chain = payload.get('chain_id')
        try:
            row_chain_id = int(_raw_chain) if _raw_chain not in (None, '') else 8453
        except (ValueError, TypeError):
            row_chain_id = 8453

        # Canonical dedupe key = workspace_id + target_id + chain_id + tx_hash + rule_key.
        # Logged for every row so each tx_hash's key is auditable. For Base (8453) rows this
        # equals the signature the SIG rule stores, so an existing alert is matched exactly.
        dedupe_key = _sig_dedupe_signature(
            workspace_id=workspace_id, target_id=row_target_id, chain_id=row_chain_id, tx_hash=row_tx_hash,
        ) if row_tx_hash else ''
        smoke_key = _smoke_dedupe_signature(
            workspace_id=workspace_id, target_id=row_target_id,
            chain_id=payload.get('chain_id'), tx_hash=row_tx_hash,
        ) if row_tx_hash else ''
        pre_existing = bool(dedupe_key and dedupe_key in existing_signatures) or bool(
            smoke_key and smoke_key in existing_signatures
        )

        logger.info(
            'strategic_guard_backfill_row_seen workspace_id=%s target_id=%s telemetry_id=%s '
            'tx_hash=%s chain_id=%s event_type=%s evidence_source=%s observed_at=%s dedupe_key=%s alert_pre_exists=%s',
            workspace_id, row_target_id, telemetry_id, row_tx_hash or 'none',
            payload.get('chain_id'), event_type or 'unknown', evidence_source or 'unknown',
            observed_at if observed_at is not None else 'unknown',
            dedupe_key or 'none', str(pre_existing).lower(),
        )

        # Truthfulness: never synthesise alerts from simulator/replay/fallback evidence.
        if evidence_source != 'live':
            skipped_count += 1
            logger.info(
                'strategic_guard_backfill_row_skipped workspace_id=%s target_id=%s telemetry_id=%s '
                'tx_hash=%s observed_at=%s dedupe_key=%s skipped_reason=evidence_source_not_live',
                workspace_id, row_target_id, telemetry_id, row_tx_hash or 'none',
                observed_at if observed_at is not None else 'unknown', dedupe_key or 'none',
            )
            continue
        if not row_tx_hash:
            skipped_count += 1
            logger.info(
                'strategic_guard_backfill_row_skipped workspace_id=%s target_id=%s telemetry_id=%s '
                'tx_hash=none observed_at=%s dedupe_key=none skipped_reason=missing_tx_hash',
                workspace_id, row_target_id, telemetry_id,
                observed_at if observed_at is not None else 'unknown',
            )
            continue

        # Direction-agnostic smoke rule fires for EVERY live wallet transfer, so every
        # unique tx_hash gets at least one Critical alert even when it is inbound and the
        # outbound-only SIG rule does not apply.
        smoke_alert_id = _wallet_transfer_smoke_alert(
            workspace_id=workspace_id,
            user_id=user_id,
            target_id=row_target_id,
            target_name=target_name,
            payload=payload,
            evidence_source=evidence_source,
            telemetry_id=telemetry_id,
            monitored_system_id=monitored_system_id,
            protected_asset_id=protected_asset_id,
        )
        sig_alert_id = _strategic_infrastructure_guard_alert(
            workspace_id=workspace_id,
            user_id=user_id,
            target_id=row_target_id,
            target_name=target_name,
            target_wallet_address=target_wallet_address,
            payload=payload,
            evidence_source=evidence_source,
            telemetry_id=telemetry_id,
            monitored_system_id=monitored_system_id,
            protected_asset_id=protected_asset_id,
        )
        for _aid in (smoke_alert_id, sig_alert_id):
            if _aid and _aid not in alerts_created:
                alerts_created.append(_aid)

        if smoke_alert_id or sig_alert_id:
            linked_count += 1
            if pre_existing:
                deduped_count += 1
                logger.info(
                    'strategic_guard_alert_deduped workspace_id=%s target_id=%s telemetry_id=%s '
                    'tx_hash=%s dedupe_key=%s smoke_alert_id=%s sig_alert_id=%s',
                    workspace_id, row_target_id, telemetry_id, row_tx_hash, dedupe_key,
                    smoke_alert_id or 'none', sig_alert_id or 'none',
                )
            else:
                created_count += 1
                logger.info(
                    'strategic_guard_alert_created workspace_id=%s target_id=%s telemetry_id=%s '
                    'tx_hash=%s dedupe_key=%s smoke_alert_id=%s sig_alert_id=%s',
                    workspace_id, row_target_id, telemetry_id, row_tx_hash, dedupe_key,
                    smoke_alert_id or 'none', sig_alert_id or 'none',
                )
            # Record signatures so a duplicate row in this same scan counts as deduped.
            if dedupe_key:
                existing_signatures.setdefault(dedupe_key, sig_alert_id or smoke_alert_id or '')
            if smoke_key:
                existing_signatures.setdefault(smoke_key, smoke_alert_id or sig_alert_id or '')
            logger.info(
                'strategic_guard_telemetry_alert_linked workspace_id=%s target_id=%s telemetry_id=%s '
                'tx_hash=%s dedupe_key=%s alert_id=%s',
                workspace_id, row_target_id, telemetry_id, row_tx_hash, dedupe_key,
                sig_alert_id or smoke_alert_id,
            )
        else:
            skipped_count += 1
            logger.info(
                'strategic_guard_backfill_row_skipped workspace_id=%s target_id=%s telemetry_id=%s '
                'tx_hash=%s observed_at=%s dedupe_key=%s skipped_reason=no_alert_returned',
                workspace_id, row_target_id, telemetry_id, row_tx_hash,
                observed_at if observed_at is not None else 'unknown', dedupe_key,
            )

    logger.info(
        'strategic_guard_backfill_completed workspace_id=%s target_id=%s '
        'row_count=%s telemetry_processed=%s created_count=%s deduped_count=%s linked_count=%s '
        'skipped_count=%s unique_alert_ids=%s alert_ids=%s',
        workspace_id, target_id, len(rows), len(rows), created_count, deduped_count, linked_count,
        skipped_count, len(alerts_created),
        ','.join(alerts_created) if alerts_created else 'none',
    )
    return {
        'status': 'completed',
        'target_id': target_id,
        'workspace_id': workspace_id,
        'telemetry_processed': len(rows),
        'alerts_created': len(alerts_created),
        'created_count': created_count,
        'deduped_count': deduped_count,
        'linked_count': linked_count,
        'skipped_count': skipped_count,
        'alert_ids': alerts_created,
    }


def backfill_strategic_guard_alerts_for_target(
    workspace_id: str,
    target_id: str,
) -> dict[str, Any]:
    """Scheduler-independent, per-target Strategic Infrastructure Guard alert backfill.

    The scheduled worker backfill selects at most one target per cooldown window, so a
    Base target that is live-polled but never ``selected_for_backfill`` keeps its older
    wallet-transfer rows without alerts — they then stay hidden behind the telemetry
    "Alerts only" filter even though they carry live tx_hash evidence. This function
    ignores that scheduler/cooldown entirely and scans the EXACT workspace_id +
    target_id given:

      * every ``wallet_transfer_detected`` / ``native_transfer`` row,
      * no recency cutoff, no LIMIT 1, no newest-only collapse — so each distinct
        tx_hash is processed, not just the latest row.

    A row is backfilled only when it is ``evidence_source='live'``, has a non-null
    tx_hash, and is on chain 8453; anything else is logged with an explicit
    ``skipped_reason`` (truthfulness: simulator/replay data is never turned into an
    alert). For each qualifying row it runs the direction-agnostic smoke rule (one
    Critical alert per live transfer) and the outbound-only Strategic Infrastructure
    Guard rule. Both dedupe on workspace_id + target_id + chain_id + tx_hash +
    rule_key, so a different tx_hash never collapses into an existing alert.

    Create-only / idempotent: the alert rules are invoked only for a tx_hash whose
    dedupe key is not already present, so re-running this every poll cycle neither
    duplicates alerts nor leaks ``monitoring_runs`` rows. Worker-callable (no Request /
    auth): owns backfilled alerts with the target's user, exactly like migration 0114.
    Never touches freshness / status / banner logic. Never raises on a single row.
    """
    require_live_mode()
    workspace_id = str(workspace_id)
    target_id = str(target_id)
    try:
        uuid.UUID(workspace_id)
        uuid.UUID(target_id)
    except (ValueError, AttributeError):
        logger.warning(
            'strategic_guard_target_backfill_invalid_ids workspace_id=%s target_id=%s',
            workspace_id, target_id,
        )
        return {
            'status': 'invalid_ids', 'workspace_id': workspace_id, 'target_id': target_id,
            'telemetry_processed': 0, 'created_count': 0, 'deduped_count': 0,
            'linked_count': 0, 'skipped_count': 0, 'alert_ids': [],
        }

    def _backfill_skip_result(reason: str) -> dict[str, Any]:
        return {
            'status': f'skipped_{reason}', 'workspace_id': workspace_id, 'target_id': target_id,
            'telemetry_processed': 0, 'created_count': 0, 'deduped_count': 0,
            'linked_count': 0, 'skipped_count': 0, 'alert_ids': [],
        }

    # --- Provider 429 backoff hard stop -------------------------------------------
    # While the provider is rate-limiting, live monitoring is paused. Backfilling now
    # would mint fresh alerts from OLD telemetry during an RPC failure — exactly what
    # requirement E forbids. This guard lives in the function (not just the worker
    # cycle) so EVERY call site is covered, including the telemetry "alerts only" read
    # path which would otherwise create alerts on demand mid-backoff.
    if rpc_provider_backoff_active():
        _bo = rpc_provider_backoff_status()
        logger.warning(
            'strategic_guard_backfill_skipped reason=provider_backoff_active '
            'workspace_id=%s target_id=%s backoff_until=%s',
            workspace_id, target_id, _bo.get('backoff_until') or 'unknown',
        )
        return _backfill_skip_result('provider_backoff_active')

    logger.info(
        'strategic_guard_target_backfill_started workspace_id=%s target_id=%s',
        workspace_id, target_id,
    )

    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        # --- Chain mismatch hard stop ---------------------------------------------
        # Never turn a wrong-chain target's telemetry into alerts (e.g. an Ethereum
        # target under a Base worker). Resolve the target's labeled chain and compare
        # to the worker's configured RPC chain id; on a definite mismatch, skip before
        # scanning any rows.
        _net_row = connection.execute(
            'SELECT chain_network FROM targets WHERE id = %s::uuid LIMIT 1',
            (target_id,),
        ).fetchone()
        _network = None
        if _net_row is not None:
            _network = _net_row.get('chain_network') if hasattr(_net_row, 'get') else _net_row[0]
        _hard_skip, _t_chain, _rpc_chain = evaluate_chain_mismatch(_network)
        if _hard_skip:
            logger.warning(
                'strategic_guard_backfill_skipped reason=chain_mismatch workspace_id=%s '
                'target_id=%s configured_chain=%s target_chain_id=%s rpc_chain_id=%s',
                workspace_id, target_id, _network, _t_chain, _rpc_chain,
            )
            return _backfill_skip_result('chain_mismatch')
        # Scan EVERY wallet-transfer row for this exact workspace+target (newest first).
        # No recency cutoff and no LIMIT 1 — older tx_hashes must be fetched too. The high
        # bound is a safety cap only, never a "newest row" selector. evidence_source and
        # chain are filtered per-row (below) so non-qualifying rows are still logged.
        rows = connection.execute(
            '''
            SELECT
                te.id,
                te.target_id,
                te.event_type,
                te.observed_at,
                te.payload_json,
                te.evidence_source,
                COALESCE(t.name, te.target_id::text) AS target_name,
                t.wallet_address AS target_wallet_address,
                COALESCE(t.updated_by_user_id, t.created_by_user_id) AS owner_user_id,
                ms.id AS monitored_system_id,
                COALESCE(te.asset_id, ms.asset_id) AS protected_asset_id
            FROM telemetry_events te
            LEFT JOIN targets t ON t.id = te.target_id
            LEFT JOIN monitored_systems ms
                ON ms.target_id = te.target_id
               AND ms.workspace_id = te.workspace_id
            WHERE te.workspace_id = %s::uuid
              AND te.target_id = %s::uuid
              AND te.event_type IN ('wallet_transfer_detected', 'native_transfer')
            ORDER BY te.observed_at DESC
            LIMIT 500
            ''',
            (workspace_id, target_id),
        ).fetchall()

        # Authoritative set of alert dedupe signatures already persisted for this target.
        # The alert rules are invoked only for a key NOT in this set, which keeps the
        # backfill create-only (no monitoring_runs leak when re-run every poll cycle) and
        # lets us report created-vs-deduped truthfully. Tolerant of stub rows in tests.
        existing_signatures: dict[str, str] = {}
        try:
            sig_rows = connection.execute(
                '''
                SELECT dedupe_signature, id
                FROM alerts
                WHERE workspace_id = %s::uuid AND target_id = %s::uuid
                  AND dedupe_signature IS NOT NULL
                ''',
                (workspace_id, target_id),
            ).fetchall()
            for sig_row in sig_rows:
                _sig = sig_row.get('dedupe_signature') if hasattr(sig_row, 'get') else None
                if _sig:
                    existing_signatures[str(_sig)] = str((sig_row.get('id') if hasattr(sig_row, 'get') else '') or '')
        except Exception:  # pragma: no cover - never block backfill on the accounting query
            existing_signatures = {}

    # alerts.user_id is NOT NULL: own backfilled alerts with the target's user, matching
    # migration 0114 (updated_by_user_id, then created_by_user_id).
    owner_user_id = ''
    for row in rows:
        _keys = row.keys() if hasattr(row, 'keys') else []
        if 'owner_user_id' in _keys and row['owner_user_id']:
            owner_user_id = str(row['owner_user_id'])
            break

    created_count = 0
    deduped_count = 0
    linked_count = 0
    skipped_count = 0
    alerts_created: list[str] = []
    for row in rows:
        telemetry_id = str(row['id'])
        row_target_id = str(row['target_id']) if row['target_id'] else target_id
        target_name = str(row['target_name'] or row_target_id)
        target_wallet_address = str(row['target_wallet_address'] or '') if row['target_wallet_address'] else ''
        payload = dict(row['payload_json'] or {})
        evidence_source = str(row['evidence_source'] or '').strip().lower()
        _row_keys = row.keys() if hasattr(row, 'keys') else []
        event_type = str(row['event_type'] or '') if 'event_type' in _row_keys else ''
        observed_at = row['observed_at'] if 'observed_at' in _row_keys else None
        monitored_system_id = str(row['monitored_system_id']) if row['monitored_system_id'] else None
        protected_asset_id = str(row['protected_asset_id']) if row['protected_asset_id'] else None
        row_tx_hash = str(payload.get('tx_hash') or payload.get('hash') or '').strip()
        _raw_chain = payload.get('chain_id')
        try:
            row_chain_id = int(_raw_chain) if _raw_chain not in (None, '') else 8453
        except (ValueError, TypeError):
            row_chain_id = 8453

        # Canonical dedupe keys: workspace_id + target_id + chain_id + tx_hash + rule_key.
        # tx_hash is part of each key, so different transactions never share an alert.
        dedupe_key = _sig_dedupe_signature(
            workspace_id=workspace_id, target_id=row_target_id, chain_id=row_chain_id, tx_hash=row_tx_hash,
        ) if row_tx_hash else ''
        smoke_key = _smoke_dedupe_signature(
            workspace_id=workspace_id, target_id=row_target_id,
            chain_id=payload.get('chain_id'), tx_hash=row_tx_hash,
        ) if row_tx_hash else ''

        logger.info(
            'strategic_guard_backfill_row_seen workspace_id=%s target_id=%s telemetry_id=%s '
            'tx_hash=%s chain_id=%s event_type=%s evidence_source=%s observed_at=%s dedupe_key=%s',
            workspace_id, row_target_id, telemetry_id, row_tx_hash or 'none',
            row_chain_id, event_type or 'unknown', evidence_source or 'unknown',
            observed_at if observed_at is not None else 'unknown', dedupe_key or 'none',
        )

        # Explicit skip reasons so an absent alert (e.g. tx_hash ending a517) is diagnosable.
        skipped_reason: str | None = None
        if evidence_source != 'live':
            skipped_reason = 'evidence_source_not_live'
        elif not row_tx_hash:
            skipped_reason = 'missing_tx_hash'
        elif row_chain_id != 8453:
            skipped_reason = 'chain_id_not_8453'
        if skipped_reason:
            skipped_count += 1
            logger.info(
                'strategic_guard_backfill_row_skipped workspace_id=%s target_id=%s telemetry_id=%s '
                'tx_hash=%s observed_at=%s dedupe_key=%s skipped_reason=%s',
                workspace_id, row_target_id, telemetry_id, row_tx_hash or 'none',
                observed_at if observed_at is not None else 'unknown', dedupe_key or 'none', skipped_reason,
            )
            continue

        smoke_pre = bool(smoke_key and smoke_key in existing_signatures)
        sig_pre = bool(dedupe_key and dedupe_key in existing_signatures)

        # Smoke rule (direction-agnostic): one Critical alert per live transfer. Invoke
        # only when the alert is missing — keeps the pass create-only and idempotent.
        smoke_alert_id: str | None = None
        if smoke_pre:
            smoke_alert_id = existing_signatures.get(smoke_key) or None
            deduped_count += 1
            logger.info(
                'strategic_guard_alert_deduped workspace_id=%s target_id=%s telemetry_id=%s '
                'tx_hash=%s dedupe_key=%s rule=smoke_wallet_transfer alert_id=%s',
                workspace_id, row_target_id, telemetry_id, row_tx_hash, smoke_key,
                smoke_alert_id or 'existing',
            )
        else:
            smoke_alert_id = _wallet_transfer_smoke_alert(
                workspace_id=workspace_id,
                user_id=owner_user_id,
                target_id=row_target_id,
                target_name=target_name,
                payload=payload,
                evidence_source=evidence_source,
                telemetry_id=telemetry_id,
                monitored_system_id=monitored_system_id,
                protected_asset_id=protected_asset_id,
            )
            if smoke_alert_id:
                created_count += 1
                if smoke_key:
                    existing_signatures.setdefault(smoke_key, smoke_alert_id)
                logger.info(
                    'strategic_guard_alert_created workspace_id=%s target_id=%s telemetry_id=%s '
                    'tx_hash=%s dedupe_key=%s rule=smoke_wallet_transfer alert_id=%s',
                    workspace_id, row_target_id, telemetry_id, row_tx_hash, smoke_key, smoke_alert_id,
                )

        # Strategic Infrastructure Guard rule (outbound-only): returns None for inbound
        # transfers before any write, so calling it for an inbound row is a cheap no-op.
        sig_alert_id: str | None = None
        if sig_pre:
            sig_alert_id = existing_signatures.get(dedupe_key) or None
            deduped_count += 1
            logger.info(
                'strategic_guard_alert_deduped workspace_id=%s target_id=%s telemetry_id=%s '
                'tx_hash=%s dedupe_key=%s rule=%s alert_id=%s',
                workspace_id, row_target_id, telemetry_id, row_tx_hash, dedupe_key,
                _SIG_RULE_KEY, sig_alert_id or 'existing',
            )
        else:
            sig_alert_id = _strategic_infrastructure_guard_alert(
                workspace_id=workspace_id,
                user_id=owner_user_id,
                target_id=row_target_id,
                target_name=target_name,
                target_wallet_address=target_wallet_address,
                payload=payload,
                evidence_source=evidence_source,
                telemetry_id=telemetry_id,
                monitored_system_id=monitored_system_id,
                protected_asset_id=protected_asset_id,
            )
            if sig_alert_id:
                created_count += 1
                if dedupe_key:
                    existing_signatures.setdefault(dedupe_key, sig_alert_id)
                logger.info(
                    'strategic_guard_alert_created workspace_id=%s target_id=%s telemetry_id=%s '
                    'tx_hash=%s dedupe_key=%s rule=%s alert_id=%s',
                    workspace_id, row_target_id, telemetry_id, row_tx_hash, dedupe_key,
                    _SIG_RULE_KEY, sig_alert_id,
                )

        for _aid in (smoke_alert_id, sig_alert_id):
            if _aid and _aid not in alerts_created:
                alerts_created.append(_aid)

        if smoke_alert_id or sig_alert_id:
            linked_count += 1
            logger.info(
                'strategic_guard_telemetry_alert_linked workspace_id=%s target_id=%s telemetry_id=%s '
                'tx_hash=%s observed_at=%s dedupe_key=%s alert_id=%s',
                workspace_id, row_target_id, telemetry_id, row_tx_hash,
                observed_at if observed_at is not None else 'unknown', dedupe_key,
                sig_alert_id or smoke_alert_id,
            )
        else:
            skipped_count += 1
            logger.info(
                'strategic_guard_backfill_row_skipped workspace_id=%s target_id=%s telemetry_id=%s '
                'tx_hash=%s observed_at=%s dedupe_key=%s skipped_reason=no_alert_returned',
                workspace_id, row_target_id, telemetry_id, row_tx_hash,
                observed_at if observed_at is not None else 'unknown', dedupe_key,
            )

    logger.info(
        'strategic_guard_target_backfill_completed workspace_id=%s target_id=%s '
        'row_count=%s created_count=%s deduped_count=%s linked_count=%s skipped_count=%s '
        'unique_alert_ids=%s alert_ids=%s',
        workspace_id, target_id, len(rows), created_count, deduped_count, linked_count,
        skipped_count, len(alerts_created), ','.join(alerts_created) if alerts_created else 'none',
    )
    return {
        'status': 'completed',
        'workspace_id': workspace_id,
        'target_id': target_id,
        'telemetry_processed': len(rows),
        'alerts_created': len(alerts_created),
        'created_count': created_count,
        'deduped_count': deduped_count,
        'linked_count': linked_count,
        'skipped_count': skipped_count,
        'alert_ids': alerts_created,
    }


def open_alert_from_detection(request: Request) -> dict[str, Any]:
    """Create an alert from the most recent open detection without a linked alert.

    Called by POST /alerts/open-from-detection. Used when detections exist but
    no alert has been opened (e.g. because alert creation previously rolled back).
    Never fires on simulator evidence. Sets analysis_run_id=NULL — live smoke
    alerts do not have an analysis_runs row. The FK allows NULL (ON DELETE SET NULL).
    """
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        workspace_id = workspace_context['workspace_id']
        user_id = user['id']

        logger.info('open_alert_request_started workspace_id=%s user_id=%s', workspace_id, user_id)

        row = connection.execute(
            '''
            SELECT
                d.id            AS detection_id,
                d.target_id,
                d.detection_type,
                d.severity,
                d.title,
                d.evidence_summary,
                d.evidence_source,
                d.raw_evidence_json,
                d.monitoring_run_id,
                COALESCE(t.name, d.target_id::text) AS target_name
            FROM detections d
            LEFT JOIN targets t ON t.id = d.target_id
            WHERE d.workspace_id = %s::uuid
              AND d.evidence_source = 'live'
              AND (
                  d.linked_alert_id IS NULL
                  OR NOT EXISTS (
                      SELECT 1 FROM alerts a
                      WHERE a.id = d.linked_alert_id
                        AND a.workspace_id = d.workspace_id
                  )
              )
            ORDER BY d.created_at DESC
            LIMIT 1
            ''',
            (workspace_id,),
        ).fetchone()

        if row is None:
            # No live detection is missing an alert. Distinguish two cases so the
            # endpoint can return an accurate HTTP status (requirement 6):
            #   * a live detection already has a valid alert  -> 409 already_exists
            #   * no live detection exists at all             -> 200 no_detection
            existing = connection.execute(
                '''
                SELECT
                    d.id AS detection_id,
                    d.target_id,
                    a.id AS alert_id
                FROM detections d
                JOIN alerts a
                  ON a.id = d.linked_alert_id
                 AND a.workspace_id = d.workspace_id
                WHERE d.workspace_id = %s::uuid
                  AND d.evidence_source = 'live'
                ORDER BY d.created_at DESC
                LIMIT 1
                ''',
                (workspace_id,),
            ).fetchone()
            if existing is not None:
                logger.info(
                    'open_alert_already_exists workspace_id=%s detection_id=%s alert_id=%s',
                    workspace_id, existing['detection_id'], existing['alert_id'],
                )
                return {
                    'status': 'already_exists',
                    'alert_id': str(existing['alert_id']),
                    'detection_id': str(existing['detection_id']),
                    'target_id': str(existing['target_id']) if existing['target_id'] else None,
                }
            logger.info(
                'open_alert_no_detection workspace_id=%s reason=no_open_detection_without_alert',
                workspace_id,
            )
            return {'status': 'no_detection', 'alert_id': None, 'detection_id': None}

        detection_id = str(row['detection_id'])
        target_id = str(row['target_id']) if row['target_id'] else ''
        target_name = str(row['target_name'] or target_id)
        raw_evidence = dict(row['raw_evidence_json'] or {})
        monitoring_run_id = str(row['monitoring_run_id']) if row['monitoring_run_id'] else None

        # Extract linkage fields from raw_evidence_json so the alert carries
        # all required traceability fields (requirement 4).
        event_block = raw_evidence.get('event') or {}
        tx_hash = str(raw_evidence.get('tx_hash') or event_block.get('tx_hash') or '')
        chain_id = raw_evidence.get('chain_id') or event_block.get('chain_id')
        block_number = raw_evidence.get('block_number') or event_block.get('block_number')
        telemetry_id = str(raw_evidence.get('telemetry_id') or event_block.get('telemetry_id') or '')
        from_address = str(raw_evidence.get('from_address') or event_block.get('from') or '')
        to_address = str(raw_evidence.get('to_address') or event_block.get('to') or '')
        amount_wei = str(raw_evidence.get('amount_wei') or event_block.get('value') or '0')

        title = str(row['title'] or f'Alert from detection: {detection_id[:8]}')
        detection_type_val = str(row['detection_type'] or 'monitored_wallet_transfer')
        # Derive canonical rule_key from detection_type so _alert_rule_key / _is_wallet_transfer_rule_alert
        # recognise this alert as a wallet-transfer alert without ambiguity. Previously the payload
        # only had matched_patterns[0].rule_id='open_from_detection', which is not in the wallet-transfer
        # rule-key set and caused the Python normalisation layer to skip status/severity normalisation.
        if detection_type_val == 'strategic_infrastructure_guard_outbound_transfer':
            _rule_key = 'strategic_infrastructure_guard_wallet_outbound_transfer'
        else:
            _rule_key = 'smoke_wallet_transfer'
        response: dict[str, Any] = {
            'rule_key': _rule_key,
            'severity': str(row['severity'] or 'critical'),
            'confidence': 'high',
            'detection_type': detection_type_val,
            'recommended_action': 'review_wallet_transfer',
            'explanation': str(row['evidence_summary'] or title),
            'matched_patterns': [
                {
                    'label': detection_type_val,
                    'rule_id': _rule_key,
                    'severity': str(row['severity'] or 'critical'),
                }
            ],
            'reasons': [detection_type_val],
            'source': str(row['evidence_source'] or 'live'),
            'degraded': False,
            'evidence_source': str(row['evidence_source'] or 'live'),
            'tx_hash': tx_hash,
            'from_address': from_address,
            'to_address': to_address,
            'amount_wei': amount_wei,
            'chain_id': chain_id,
            'block_number': block_number,
            'telemetry_id': telemetry_id,
            'target_id': target_id,
            'detection_id': detection_id,
            'monitoring_run_id': monitoring_run_id,
        }

        # Dedupe signature is detection-scoped so one alert per detection from
        # this path. Does not collide with smoke_wallet_transfer signatures.
        _dedup_seed = json.dumps(
            {'detection_id': detection_id, 'rule': 'open_from_detection'},
            sort_keys=True,
        )
        signature = uuid.uuid5(uuid.NAMESPACE_DNS, _dedup_seed).hex

        upsert_out: dict[str, Any] = {}
        try:
            alert_id = _upsert_alert(
                connection,
                workspace_id=workspace_id,
                user_id=user_id,
                target_id=target_id,
                analysis_run_id=None,
                title=title,
                response=response,
                signature=signature,
                detection_id=detection_id,
                out=upsert_out,
            )
            if alert_id:
                connection.execute(
                    "UPDATE detections SET linked_alert_id = %s::uuid, status = 'escalated', updated_at = NOW() WHERE id = %s::uuid",
                    (alert_id, detection_id),
                )
            connection.commit()
        except Exception as exc:
            logger.exception(
                'open_alert_failed workspace_id=%s detection_id=%s error=%s',
                workspace_id, detection_id, str(exc),
            )
            raise

        # When _upsert_alert returns '' a suppression rule matched.  The suppression rule
        # prevents new rows but the original alert (created by the backfill or a prior
        # "Open Alert" click) still exists in the DB.  Look it up so the frontend can
        # navigate to it instead of showing an unhelpful "suppressed" toast.
        if not alert_id:
            try:
                _sup_row = connection.execute(
                    '''
                    SELECT id FROM alerts
                    WHERE workspace_id = %s
                      AND detection_id = %s::uuid
                    ORDER BY created_at DESC LIMIT 1
                    ''',
                    (workspace_id, detection_id),
                ).fetchone()
                if _sup_row is None:
                    _sup_row = connection.execute(
                        '''
                        SELECT id FROM alerts
                        WHERE workspace_id = %s
                          AND target_id = %s::uuid
                          AND dedupe_signature = %s
                        ORDER BY created_at DESC LIMIT 1
                        ''',
                        (workspace_id, target_id, signature),
                    ).fetchone()
                if _sup_row:
                    alert_id = str(_sup_row['id'])
                    logger.info(
                        'open_alert_suppressed_existing_found workspace_id=%s detection_id=%s alert_id=%s',
                        workspace_id, detection_id, alert_id,
                    )
            except Exception:
                logger.warning(
                    'open_alert_suppressed_lookup_failed workspace_id=%s detection_id=%s',
                    workspace_id, detection_id, exc_info=True,
                )

        # Third fallback: find any live wallet-transfer alert for this target/workspace.
        # Covers backfill-created alerts whose detection_id and dedupe_signature differ
        # from what open_alert_from_detection produces (different UUID5 seed paths).
        if not alert_id:
            try:
                _broad_row = connection.execute(
                    '''
                    SELECT a.id FROM alerts a
                    WHERE a.workspace_id = %s
                      AND a.target_id = %s::uuid
                      AND (
                          a.payload->>'rule_key' IN (
                              'strategic_infrastructure_guard_wallet_outbound_transfer',
                              'smoke_wallet_transfer'
                          )
                          OR a.module_key = 'strategic_infrastructure_guard'
                          OR a.payload->>'detection_type' IN (
                              'strategic_infrastructure_guard_outbound_transfer',
                              'monitored_wallet_transfer'
                          )
                          OR COALESCE(
                              a.payload->>'tx_hash',
                              a.payload->'evidence'->>'tx_hash'
                          ) IS NOT NULL
                      )
                      AND (
                          COALESCE(a.payload->>'evidence_source', '') = 'live'
                          OR a.source IN ('live', 'rpc_polling')
                          OR a.source_service = 'threat-engine'
                      )
                      AND lower(COALESCE(a.status, 'open')) NOT IN ('resolved', 'false_positive')
                    ORDER BY a.created_at DESC LIMIT 1
                    ''',
                    (workspace_id, target_id),
                ).fetchone()
                if _broad_row:
                    alert_id = str(_broad_row['id'])
                    logger.info(
                        'open_alert_suppressed_wallet_transfer_fallback_found workspace_id=%s detection_id=%s alert_id=%s',
                        workspace_id, detection_id, alert_id,
                    )
            except Exception:
                logger.warning(
                    'open_alert_suppressed_wallet_transfer_fallback_failed workspace_id=%s detection_id=%s',
                    workspace_id, detection_id, exc_info=True,
                )

        created_new = bool(alert_id) and bool(upsert_out.get('created'))
        if created_new:
            logger.info(
                'open_alert_created workspace_id=%s detection_id=%s alert_id=%s '
                'tx_hash=%s chain_id=%s block_number=%s telemetry_id=%s',
                workspace_id, detection_id, alert_id,
                tx_hash or 'unknown', chain_id, block_number, telemetry_id or 'none',
            )
        elif alert_id:
            # _upsert_alert returned an existing alert (dedupe/suppression) rather than
            # inserting a new row — surface as already_exists so the endpoint returns 409.
            logger.info(
                'open_alert_already_exists workspace_id=%s detection_id=%s alert_id=%s reason=dedupe',
                workspace_id, detection_id, alert_id,
            )

        # Promote the alert so opened_at is set and /alerts list shows it as active
        # without requiring migration re-runs. Idempotent: no-op if already promoted.
        if alert_id and target_id:
            try:
                with pg_connection() as promote_conn:
                    promote_wallet_transfer_alerts(promote_conn, workspace_id=workspace_id, target_id=target_id)
                    promote_conn.commit()
            except Exception:
                logger.warning(
                    'open_alert_promote_failed workspace_id=%s alert_id=%s',
                    workspace_id, alert_id, exc_info=True,
                )

        if alert_id:
            status_value = 'created' if created_new else 'already_exists'
        else:
            status_value = 'suppressed'

        return {
            'status': status_value,
            'alert_id': alert_id or None,
            'detection_id': detection_id,
            'target_id': target_id,
            'tx_hash': tx_hash or None,
            'chain_id': chain_id,
            'block_number': block_number,
            'telemetry_id': telemetry_id or None,
            'monitoring_run_id': monitoring_run_id,
        }
