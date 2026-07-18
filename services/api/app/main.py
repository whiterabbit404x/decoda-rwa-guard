from __future__ import annotations

import importlib.util
import asyncio
import json
import logging
import os
import subprocess
import sys
import time
import types
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager, suppress
from copy import deepcopy
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal, TypedDict
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request as UrlRequest, urlopen

from contextlib import asynccontextmanager

import hmac as _hmac_mod
import uuid as _uuid_mod
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

from services.api.app.domains import alert_stream, alert_delivery
from services.api.app.domains.rate_limit import rate_limit_connectivity
from services.api.app.quicknode_streams import (
    QUICKNODE_STREAMS_WEBHOOK_VERSION,
    process_quicknode_base_stream_webhook,
    run_quicknode_debug_tx,
    verify_quicknode_ops_token,
)

from services.api.app.pilot import (
    accept_workspace_invitation,
    auth_token_secret_configured,
    authenticate_request,
    issue_csrf_token,
    validate_csrf_token,
    authenticate_with_connection,
    build_history_response,
    create_governance_action_record,
    create_incident_record,
    create_workspace_for_user,
    database_url as pilot_database_url,
    create_checkout_session,
    create_portal_session,
    create_webhook,
    create_slack_integration,
    begin_slack_oauth_install,
    complete_slack_oauth_install,
    create_workspace_invitation,
    list_workspace_invitations,
    revoke_workspace_invitation,
    resend_workspace_invitation,
    update_workspace_member,
    remove_workspace_member,
    get_team_seats,
    enforce_auth_rate_limit,
    ensure_pilot_schema,
    ensure_monitoring_proof_chain,
    runtime_allows_simulator_proof_chain,
    get_monitoring_investigation_timeline,
    list_user_workspaces,
    live_mode_enabled,
    log_audit,
    maybe_insert_alert,
    normalize_workspace_header_value,
    parse_csv_env,
    pilot_schema_status,
    persist_analysis_run,
    pilot_mode,
    pg_connection,
    resolve_workspace,
    runtime_environment_identity,
    run_startup_migrations_if_enabled,
    validate_runtime_configuration,
    billing_runtime_status,
    request_email_verification,
    request_password_reset,
    list_active_sessions,
    list_plan_entitlements,
    revoke_session,
    mfa_begin_enrollment,
    mfa_confirm_enrollment,
    mfa_complete_signin,
    oidc_begin_signin,
    oidc_complete_signin,
    mfa_disable,
    mfa_regenerate_recovery_codes,
    reauthenticate_user,
    get_workspace_access_control,
    update_workspace_auth_policy,
    get_workspace_oidc_config,
    upsert_workspace_oidc_config,
    delete_workspace_oidc_config,
    list_workspace_scim_tokens,
    create_workspace_scim_token,
    revoke_workspace_scim_token,
    scim_list_users,
    scim_create_user,
    scim_replace_user,
    scim_patch_user,
    scim_delete_user,
    scim_list_groups,
    scim_create_group,
    scim_replace_group,
    scim_delete_group,
    run_background_jobs,
    reconcile_monitored_systems_for_enabled_targets,
    reconcile_workspace_monitored_systems,
    get_latest_workspace_reconcile_run,
    get_workspace_reconcile_status,
    get_workspace_reconcile_status_by_idempotency_key,
    get_latest_workspace_reconcile_result,
    get_workspace_reconcile_result,
    get_workspace_reconcile_events,
    get_workspace_subscription,
    list_workspace_members,
    list_workspace_api_keys,
    create_workspace_api_key,
    revoke_workspace_api_key,
    rotate_workspace_api_key,
    list_credential_rotation_policies,
    upsert_credential_rotation_policy,
    list_credential_rotation_history,
    rotate_workspace_credential,
    revoke_workspace_credential,
    claim_rotated_credential_secret,
    trigger_due_credential_rotations,
    list_webhook_deliveries,
    list_slack_integrations,
    list_slack_deliveries,
    list_alert_routing_rules,
    list_webhooks,
    list_notification_configuration,
    create_notification_destination,
    upsert_notification_policy,
    list_notification_attempts,
    acknowledge_notification_attempt,
    process_stripe_webhook,
    process_paddle_webhook,
    rotate_webhook_secret,
    test_slack_integration,
    select_workspace_for_user,
    demo_seed_status,
    schema_missing_error_payload,
    signin_user,
    signout_user,
    signout_all_sessions,
    signup_user,
    verify_email_token,
    reset_password,
    update_webhook,
    update_slack_integration,
    delete_slack_integration,
    upsert_alert_routing_rule,
    list_targets,
    list_monitoring_sources,
    get_source_optimization_settings,
    update_source_optimization_settings,
    run_source_health_check,
    run_source_diagnostic,
    list_source_agent_decisions,
    list_assets,
    create_asset,
    get_asset,
    update_asset,
    verify_asset,
    delete_asset,
    resolve_asset_onchain,
    bind_asset_wallets,
    bind_asset_chainlink_feeds,
    create_target,
    get_target,
    update_target,
    delete_target,
    repair_orphan_target,
    get_module_config,
    put_module_config,
    list_detections,
    get_detection,
    get_detection_evidence,
    list_alerts,
    get_alert,
    patch_alert,
    escalate_alert_to_incident,
    create_alert_suppression,
    list_alert_evidence,
    list_incidents,
    get_incident,
    patch_incident,
    list_incident_timeline,
    list_action_history,
    append_incident_timeline_note,
    create_action_history_entry,
    create_enforcement_action,
    recommend_response_action_for_incident,
    list_response_action_capabilities,
    approve_enforcement_action,
    execute_enforcement_action,
    list_enforcement_actions,
    rollback_enforcement_action,
    create_export_job,
    create_proof_bundle_export,
    create_incident_report_export,
    simulate_response_action,
    create_evidence_package_from_response_action,
    get_mttd_metrics,
    list_exports,
    list_audit_events,
    get_export,
    get_export_artifact_content,
    get_history_item,
    list_templates,
    apply_template,
    create_finding_decision,
    create_finding_action,
    patch_finding_action,
    list_finding_actions,
    list_finding_decisions,
    get_integration_health,
    get_workspace_readiness,
    get_admin_readiness,
    get_recovery_drill_status,
    schedule_recovery_drill,
    test_integration_email,
    test_integration_slack,
    get_onboarding_state,
    update_onboarding_state,
    get_onboarding_progress,
    get_current_workspace,
    set_target_enabled,
    get_workspace_monitoring_debug,
    list_monitored_systems,
    list_monitoring_runs,
    get_monitoring_run,
    create_monitored_system,
    patch_monitored_system,
    delete_monitored_system,
    run_guided_threat_workflow,
    delete_account,
    get_workspace_retention_policies,
    update_workspace_retention_policies,
    list_workspace_legal_holds,
    create_workspace_legal_hold,
    release_workspace_legal_hold,
    create_data_deletion_request,
    list_data_deletion_requests,
    approve_and_execute_data_deletion_request,
    get_retention_worker_health,
    require_ops_rbac_guard,
    promote_wallet_transfer_alerts,
)
from services.api.app.monitoring_runner import (
    backfill_missing_alerts_for_target,
    backfill_target_block_range,
    diagnose_wallet_transaction,
    get_background_loop_health,
    get_monitoring_health,
    ingest_tx_by_hash,
    inspect_target_dead_letter_state,
    list_monitoring_evidence,
    list_monitoring_heartbeats,
    list_monitoring_worker_errors,
    list_monitoring_targets,
    list_target_telemetry,
    monitoring_runtime_debug_payload,
    monitoring_runtime_status,
    patch_monitoring_target,
    production_claim_validator,
    recover_target_dead_letter,
    open_alert_from_detection,
    run_detection_from_existing_telemetry,
    run_monitoring_cycle,
    run_monitoring_once,
    set_background_loop_health,
)
from services.api.app import ai_triage
from services.api.app import onboarding_agent
from services.api.app.workspace_monitoring_summary import build_workspace_monitoring_summary_fallback
from services.api.app.threat_payloads import normalize_threat_payload
from services.api.app.db_failure import (
    classify_db_error,
    db_error_classification_context,
    db_error_reason_label,
    extract_db_host_from_dsn,
    normalize_db_error_snippet,
)
from services.api.app.secret_crypto import validate_secret_encryption_key_at_startup
from services.api.app.evidence_signing import validate_signing_secret_at_startup
from services.api.app.structured_logging import configure_logging
from services.api.app.observability import bind_trace, reset_trace, increment, observe, prometheus_metrics, report_error


def _find_repo_root(start: Path) -> Path:
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / 'phase1_local').is_dir():
            return candidate
    raise RuntimeError(f"Unable to locate repo root from {start} via a phase1_local directory search.")


def _ensure_repo_root_on_path() -> Path:
    repo_root = _find_repo_root(Path(__file__))
    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)
    return repo_root


REPO_ROOT = _ensure_repo_root_on_path()

from phase1_local.dev_support import (
    dashboard_payload,
    database_url as local_database_url,
    load_all_services,
    load_env_file,
    load_service,
    resolve_sqlite_path,
    seed_service,
    upsert_service,
    replace_metrics,
)

load_env_file()

logger = logging.getLogger(__name__)
configure_logging(service='api')

# Mandatory QuickNode Streams webhook diagnostics. Kept at INFO on a dedicated
# child logger so `quicknode_stream_route_hit` (every POST) and the startup
# `quicknode_streams_webhook_version=...` marker are always provable from
# Railway logs, even when the global log level is raised to WARNING — without
# forcing the rest of the API's INFO logs on. See CLAUDE.md truthfulness rules:
# a QuickNode POST 200 with no quicknode_stream_* line is a bug.
_quicknode_streams_logger = logging.getLogger(f'{__name__}.quicknode_streams')
_quicknode_streams_logger.setLevel(logging.INFO)

SERVICE_NAME = 'api'
PORT = int(os.getenv('PORT', 8000))
DETAIL = 'FastAPI gateway serving the local Phase 1 dashboard API.'
DEFAULT_METRICS = [
    {
        'metric_key': 'api_status',
        'label': 'API Gateway',
        'value': 'Serving local dashboard and service registry endpoints.',
        'status': 'Healthy',
    },
    {
        'metric_key': 'local_mode',
        'label': 'Local Mode',
        'value': 'SQLite-backed development mode is enabled without Docker.',
        'status': 'Ready',
    },
]
RISK_ENGINE_URL_ENV = os.getenv('RISK_ENGINE_URL')
RISK_ENGINE_URL = (RISK_ENGINE_URL_ENV or 'http://localhost:8001').rstrip('/')
RISK_ENGINE_TIMEOUT_SECONDS = float(os.getenv('RISK_ENGINE_TIMEOUT_SECONDS', '1.5'))
RISK_ENGINE_DATA_DIR = Path(__file__).resolve().parents[2] / 'risk-engine' / 'data'
THREAT_ENGINE_URL_ENV = os.getenv('THREAT_ENGINE_URL')
THREAT_ENGINE_URL = (THREAT_ENGINE_URL_ENV or 'http://localhost:8002').rstrip('/')
THREAT_ENGINE_TIMEOUT_SECONDS = float(os.getenv('THREAT_ENGINE_TIMEOUT_SECONDS', '1.5'))
THREAT_ENGINE_DATA_DIR = Path(__file__).resolve().parents[2] / 'threat-engine' / 'data'
COMPLIANCE_SERVICE_URL_ENV = os.getenv('COMPLIANCE_SERVICE_URL')
COMPLIANCE_SERVICE_URL = (COMPLIANCE_SERVICE_URL_ENV or 'http://localhost:8004').rstrip('/')
COMPLIANCE_SERVICE_TIMEOUT_SECONDS = float(os.getenv('COMPLIANCE_SERVICE_TIMEOUT_SECONDS', '1.5'))
COMPLIANCE_DATA_DIR = Path(__file__).resolve().parents[2] / 'compliance-service' / 'data'
RECONCILIATION_SERVICE_URL_ENV = os.getenv('RECONCILIATION_SERVICE_URL')
RECONCILIATION_SERVICE_URL = (RECONCILIATION_SERVICE_URL_ENV or 'http://localhost:8005').rstrip('/')
RECONCILIATION_SERVICE_TIMEOUT_SECONDS = float(os.getenv('RECONCILIATION_SERVICE_TIMEOUT_SECONDS', '1.5'))
RECONCILIATION_DATA_DIR = Path(__file__).resolve().parents[2] / 'reconciliation-service' / 'data'
OPTIONAL_FIXTURE_WARNINGS_EMITTED: set[tuple[str, str]] = set()
STARTUP_BOOTSTRAP_STATUS: dict[str, Any] = {'enabled': False, 'ran': False, 'applied_versions': []}
MONITORING_BACKGROUND_TASK: asyncio.Task[Any] | None = None
ALERT_EVENT_BACKGROUND_TASK: asyncio.Task[Any] | None = None
DEFERRED_STARTUP_RECONCILE_TASK: asyncio.Task[Any] | None = None
HAS_EMITTED_INITIAL_MONITORING_DB_DEGRADED_EVENT = False
HAS_EMITTED_INITIAL_STARTUP_RECONCILE_DB_DEGRADED_EVENT = False
MONITORING_LOOP_RUNTIME_STATE: dict[str, Any] = {
    'loop_running': False,
    'last_successful_cycle': None,
    'consecutive_failures': 0,
    'degraded': False,
    'classification': None,
    'reason': None,
    'backoff_seconds': None,
    'next_retry_at': None,
    'db_host': extract_db_host_from_dsn(pilot_database_url()),
    'updated_at': None,
}
RUNTIME_MARKER_ENV_VARS = (
    'APP_VERSION',
    'APP_BUILD_COMMIT',
    'RAILWAY_GIT_COMMIT_SHA',
    'SOURCE_COMMIT',
    'COMMIT_SHA',
    'VERCEL_GIT_COMMIT_SHA',
)
HARD_CODED_BACKEND_BUILD_MARKER = 'backend-build-2026-03-20-fixture-diagnostics-v2'
FIXTURE_FILES = {
    'risk_engine': ('sample_risk_request.json',),
    'reconciliation': (
        'critical_supply_divergence_double_count_risk.json',
        'critical_mismatch_paused_bridge.json',
    ),
}
DEFAULT_RISK_SAMPLE_REQUEST = {
    'transaction_payload': {
        'tx_hash': '0xphase1sample',
        'from_address': '0x1111111111111111111111111111111111111111',
        'to_address': '0x2222222222222222222222222222222222222222',
        'value': 1850000.0,
        'gas_price': 57.0,
        'gas_limit': 900000,
        'chain_id': 1,
        'calldata_size': 644,
        'token_transfers': [
            {'token': 'USTB', 'amount': 550000},
            {'token': 'WETH', 'amount': 1200},
        ],
        'metadata': {
            'contains_flash_loan_hop': True,
            'entrypoint': 'aggregator-router',
        },
    },
    'decoded_function_call': {
        'function_name': 'flashLoan',
        'contract_name': 'LiquidityRouter',
        'arguments': {
            'receiver': '0x3333333333333333333333333333333333333333',
            'owner': '0x4444444444444444444444444444444444444444',
            'assets': ['USTB', 'WETH'],
        },
        'selectors': ['0xabcd1234'],
    },
    'wallet_reputation': {
        'address': '0x1111111111111111111111111111111111111111',
        'score': 22,
        'prior_flags': 3,
        'account_age_days': 5,
        'kyc_verified': False,
        'sanctions_hits': 0,
        'known_safe': False,
        'recent_counterparties': 27,
        'metadata': {'watchlist': 'elevated'},
    },
    'contract_metadata': {
        'address': '0x2222222222222222222222222222222222222222',
        'contract_name': 'LiquidityRouter',
        'verified_source': False,
        'proxy': True,
        'created_days_ago': 3,
        'tvl': 2800000.0,
        'audit_count': 0,
        'categories': ['dex-router'],
        'static_flags': {'hidden_owner': False},
        'metadata': {'upgradeability': 'mutable'},
    },
    'recent_market_events': [],
}
DEFAULT_NORMAL_MARKET_EVENTS = [
    {
        'timestamp': '2026-03-18T00:00:00Z',
        'event_type': 'trade',
        'asset': 'USTB',
        'venue': 'dex-alpha',
        'price': 1.0001,
        'volume': 120000.0,
        'side': 'buy',
        'trader_id': 'maker-1',
        'cancellation_rate': 0.05,
        'liquidity_change': 0.01,
        'metadata': {},
    }
]
DEFAULT_SUSPICIOUS_MARKET_EVENTS = [
    {
        'timestamp': '2026-03-18T01:00:00Z',
        'event_type': 'borrow',
        'asset': 'USTB',
        'venue': 'dex-beta',
        'price': 1.0,
        'volume': 250000.0,
        'trader_id': 'actor-7',
        'liquidity_change': -0.38,
        'metadata': {},
    }
]
DEFAULT_RECONCILIATION_STATE = {
    'asset_id': 'USTB-2026',
    'expected_total_supply': 1000000,
    'ledgers': [
        {
            'ledger_name': 'ethereum',
            'reported_supply': 740000,
            'locked_supply': 10000,
            'pending_settlement': 45000,
            'last_updated_at': '2026-03-18T11:40:00Z',
            'transfer_count': 125,
            'reconciliation_weight': 1.0,
        },
        {
            'ledger_name': 'avalanche',
            'reported_supply': 510000,
            'locked_supply': 5000,
            'pending_settlement': 38000,
            'last_updated_at': '2026-03-18T11:42:00Z',
            'transfer_count': 118,
            'reconciliation_weight': 1.0,
        },
        {
            'ledger_name': 'private-bank-ledger',
            'reported_supply': 210000,
            'locked_supply': 0,
            'pending_settlement': 12000,
            'last_updated_at': '2026-03-18T09:10:00Z',
            'transfer_count': 21,
            'reconciliation_weight': 1.0,
        },
    ],
}
DEFAULT_BACKSTOP_STATE = {
    'asset_id': 'USTB-2026',
    'volatility_score': 71,
    'cyber_alert_score': 89,
    'reconciliation_severity': 81,
    'oracle_confidence_score': 36,
    'compliance_incident_score': 74,
    'current_market_mode': 'restricted',
}
DEFAULT_LOCAL_CORS_ORIGINS = [
    'http://localhost:3000',
    'http://127.0.0.1:3000',
]
DEFAULT_PRODUCTION_CORS_ORIGINS = [
    'https://rwa.decodasecurity.com',
]


_RUNTIME_STATUS_REQUIRED_TOP_LEVEL_KEYS = [
    'workspace_configured',
    'runtime_status',
    'configured_systems',
    'reporting_systems',
    'protected_assets',
    'last_poll_at',
    'last_heartbeat_at',
    'last_telemetry_at',
    'last_detection_at',
    'freshness_status',
    'confidence_status',
    'evidence_source',
    'status_reason',
    'contradiction_flags',
    'summary_generated_at',
    'provider_health',
    'target_coverage',
    'provider_health_records',
    'target_coverage_records',
    'provider_health_status',
    'target_coverage_status',
    'runtime_setup_chain',
    'next_required_action',
    'worker_status',
    'realtime_enabled',
    'last_stable_poll_at',
    'last_rpc_polling_heartbeat_at',
    'stable_poll_age_seconds',
    'stable_poll_stale_threshold_seconds',
    'stable_polling_status',
]


def _is_production_like_runtime() -> bool:
    mode = os.getenv('APP_ENV', os.getenv('APP_MODE', 'development')).strip().lower()
    return mode in {'production', 'prod'}


def _is_local_dev_mode() -> bool:
    app_env = os.getenv('APP_ENV', os.getenv('APP_MODE', 'development')).strip().lower()
    enable_flag = os.getenv('ENABLE_LOCAL_DEV_SUPPORT', '').strip().lower()
    return app_env in {'local', 'development', 'dev'} or enable_flag == 'true'


def _require_debug_endpoint_allowed() -> None:
    """Raise 404 for debug endpoints unless explicitly enabled in non-production environments."""
    env = os.getenv('APP_ENV', os.getenv('APP_MODE', 'development')).strip().lower()
    if env in {'production', 'prod', 'staging'}:
        enabled = os.getenv('ENABLE_DEBUG_ENDPOINTS', '').strip().lower() in {'1', 'true', 'yes', 'on'}
        if not enabled:
            raise HTTPException(status_code=404, detail='Not found.')
    elif os.getenv('ENABLE_DEBUG_ENDPOINTS', '').strip().lower() == 'false':
        raise HTTPException(status_code=404, detail='Not found.')


def _normalize_origin(origin: str) -> str | None:
    value = (origin or '').strip()
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        return None
    return f'{parsed.scheme}://{parsed.netloc}'.rstrip('/')


def resolve_allowed_origins() -> list[str]:
    configured_values = parse_csv_env('CORS_ALLOWED_ORIGINS', [])
    if not configured_values:
        configured_values = parse_csv_env('ALLOWED_ORIGINS', [])
    if not configured_values and _is_production_like_runtime():
        configured_values = list(DEFAULT_PRODUCTION_CORS_ORIGINS)
    if not configured_values and not _is_production_like_runtime():
        configured_values = list(DEFAULT_LOCAL_CORS_ORIGINS)

    normalized_origins: list[str] = []
    for raw_origin in configured_values:
        normalized = _normalize_origin(raw_origin)
        if not normalized:
            logger.warning('Ignoring invalid CORS origin value: %s', raw_origin)
            continue
        if normalized not in normalized_origins:
            normalized_origins.append(normalized)
    if not normalized_origins and _is_production_like_runtime():
        logger.warning('No CORS origins configured for production-like runtime. Set CORS_ALLOWED_ORIGINS or ALLOWED_ORIGINS.')
    if _is_production_like_runtime():
        for required_origin in DEFAULT_PRODUCTION_CORS_ORIGINS:
            if required_origin not in normalized_origins:
                normalized_origins.append(required_origin)
                logger.warning(
                    'Appending required production CORS origin=%s to keep authenticated product monitoring endpoints accessible.',
                    required_origin,
                )
    return normalized_origins


def resolve_cors_allow_credentials() -> bool:
    default_value = 'true' if _is_production_like_runtime() else 'false'
    return os.getenv('CORS_ALLOW_CREDENTIALS', default_value).strip().lower() in {'1', 'true', 'yes', 'on'}


ALLOWED_ORIGINS = resolve_allowed_origins()
CORS_ALLOW_CREDENTIALS = resolve_cors_allow_credentials()
CORS_ALLOWED_METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS']
CORS_ALLOWED_HEADERS = [
    'Accept',
    'Authorization',
    'Cache-Control',
    'Content-Type',
    'Pragma',
    'X-CSRF-Token',
    'X-Workspace-Id',
    'X-API-Key',
]

# ---------------------------------------------------------------------------
# Redis Streams alert transport (workspace-scoped, bounded, multi-replica)
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Prometheus-style in-memory counters (no external library required)
# ---------------------------------------------------------------------------
_REQUEST_METRICS: dict[str, int] = {}   # "method:path_pattern:status"
_AUTH_FAILURE_COUNT: int = 0
_ALERTS_PUBLISHED_COUNT: int = 0
_SSE_CONNECTION_COUNT: int = 0
_SSE_EVENTS_DELIVERED: int = 0


def alert_delivery_health() -> dict[str, Any]:
    try:
        with pg_connection() as connection:
            return alert_delivery.health_snapshot(connection)
    except Exception as exc:
        snapshot = alert_delivery.health_snapshot()
        snapshot['error'] = type(exc).__name__
        return snapshot


def database_url() -> str | None:
    return pilot_database_url()


def resolved_database_url() -> str | None:
    return database_url()


def masked_database_url() -> str | None:
    return '[configured]' if resolved_database_url() else None


def resolve_runtime_marker() -> str:
    for env_var in RUNTIME_MARKER_ENV_VARS:
        value = os.getenv(env_var, '').strip()
        if value:
            return f'{env_var.lower()}:{value[:12]}'
    return f'code-sha:{sha256(Path(__file__).read_bytes()).hexdigest()[:12]}'


def resolve_git_commit() -> str | None:
    for env_var in RUNTIME_MARKER_ENV_VARS:
        value = os.getenv(env_var, '').strip()
        if value:
            return value
    try:
        result = subprocess.run(
            ['git', 'rev-parse', 'HEAD'],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=1,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None

    commit = result.stdout.strip()
    return commit or None


BACKEND_BUILD_ID = HARD_CODED_BACKEND_BUILD_MARKER
BACKEND_GIT_COMMIT = resolve_git_commit()
RUNTIME_MARKER = f'{BACKEND_BUILD_ID}:{resolve_runtime_marker()}'


def mode_flags() -> dict[str, Any]:
    live_enabled = live_mode_enabled()
    return {
        'app_mode': os.getenv('APP_MODE', 'local'),
        'pilot_mode': pilot_mode(),
        'live_mode_enabled': live_enabled,
        'demo_mode': not live_enabled,
    }


DEPENDENCY_CONFIG = {
    'risk_engine': {
        'env_value_key': 'RISK_ENGINE_URL_ENV',
        'url_key': 'RISK_ENGINE_URL',
        'service_slug': 'risk-engine',
    },
    'threat_engine': {
        'env_value_key': 'THREAT_ENGINE_URL_ENV',
        'url_key': 'THREAT_ENGINE_URL',
        'service_slug': 'threat-engine',
    },
    'compliance_service': {
        'env_value_key': 'COMPLIANCE_SERVICE_URL_ENV',
        'url_key': 'COMPLIANCE_SERVICE_URL',
        'service_slug': 'compliance-service',
    },
    'reconciliation_service': {
        'env_value_key': 'RECONCILIATION_SERVICE_URL_ENV',
        'url_key': 'RECONCILIATION_SERVICE_URL',
        'service_slug': 'reconciliation-service',
    },
}
DEPENDENCY_RUNTIME_STATUS: dict[str, dict[str, Any]] = {}
EMBEDDED_SERVICE_STATUS_DETAIL = 'Embedded local execution active'
EMBEDDED_ALIAS_MODULE_NAMES = ('app', 'app.main', 'app.engine', 'app.schemas', 'app.store')


def _is_embedded_alias_module(module: Any) -> bool:
    module_name = str(getattr(module, '__name__', '') or '')
    if module_name.startswith('_embedded_'):
        return True
    module_file = str(getattr(module, '__file__', '') or '')
    return '/services/' in module_file and '/app/' in module_file and module_name.startswith('app')


def _drop_embedded_alias_leaks() -> None:
    for alias_name in EMBEDDED_ALIAS_MODULE_NAMES:
        existing = sys.modules.get(alias_name)
        if existing is not None and _is_embedded_alias_module(existing):
            sys.modules.pop(alias_name, None)
DEPENDENCY_SERVICE_REGISTRY = {
    'risk_engine': {
        'service_name': 'risk-engine',
        'service_slug': 'risk-engine',
        'default_port': 8001,
    },
    'threat_engine': {
        'service_name': 'threat-engine',
        'service_slug': 'threat-engine',
        'default_port': 8002,
    },
    'compliance_service': {
        'service_name': 'compliance-service',
        'service_slug': 'compliance-service',
        'default_port': 8004,
    },
    'reconciliation_service': {
        'service_name': 'reconciliation-service',
        'service_slug': 'reconciliation-service',
        'default_port': 8005,
    },
}


def resolve_service_port(url: str, default_port: int) -> int:
    parsed = urlparse(url)
    if parsed.port is not None:
        return parsed.port
    if parsed.scheme == 'https':
        return 443
    if parsed.scheme == 'http':
        return 80
    return default_port


def dependency_service_name(dependency_name: str) -> str:
    return str(DEPENDENCY_SERVICE_REGISTRY[dependency_name]['service_name'])


def registry_metrics_for_dependency(dependency_name: str) -> list[dict[str, str]]:
    runtime = DEPENDENCY_RUNTIME_STATUS.get(dependency_name, {})
    selected_mode = str(runtime.get('selected_mode') or dependency_mode(dependency_name))
    last_used_mode = str(runtime.get('last_used_mode') or selected_mode)
    payload_source = str(runtime.get('payload_source') or ('live' if selected_mode == 'embedded_local' else 'unavailable'))
    degraded = bool(runtime.get('degraded', False))
    last_error = runtime.get('last_error')
    configured_url = str(runtime.get('configured_url') or globals()[DEPENDENCY_CONFIG[dependency_name]['url_key']])
    return [
        {
            'metric_key': 'execution_mode',
            'label': 'Execution mode',
            'value': 'Embedded local execution active' if selected_mode == 'embedded_local' else f'Remote proxy to {configured_url}',
            'status': 'Live' if payload_source == 'live' and not degraded else 'Monitoring',
        },
        {
            'metric_key': 'payload_source',
            'label': 'Payload source',
            'value': payload_source,
            'status': 'Live' if payload_source == 'live' and not degraded else 'Fallback' if payload_source == 'fallback' or degraded else 'Pending',
        },
        {
            'metric_key': 'runtime_status',
            'label': 'Runtime status',
            'value': f'last_used_mode={last_used_mode}' + (f'; last_error={last_error}' if last_error else ''),
            'status': 'Healthy' if not degraded else 'Degraded',
        },
    ]


def update_dependency_registry_entry(
    dependency_name: str,
    *,
    payload_source: str | None = None,
    degraded: bool | None = None,
    detail: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    registry_config = DEPENDENCY_SERVICE_REGISTRY[dependency_name]
    runtime = DEPENDENCY_RUNTIME_STATUS.setdefault(dependency_name, {})
    selected_mode = dependency_mode(dependency_name)
    last_used_mode = str(runtime.get('last_used_mode') or selected_mode)
    payload_source_value = payload_source or str(runtime.get('payload_source') or ('live' if selected_mode == 'embedded_local' else 'unavailable'))
    degraded_value = bool(runtime.get('degraded', False) if degraded is None else degraded)
    detail_value = detail or (EMBEDDED_SERVICE_STATUS_DETAIL if selected_mode == 'embedded_local' else 'Remote proxy configured')
    if error is not None:
        runtime['last_error'] = error
    configured_url = globals()[DEPENDENCY_CONFIG[dependency_name]['url_key']]
    runtime.update(
        {
            'configured_url': configured_url,
            'selected_mode': selected_mode,
            'last_used_mode': last_used_mode,
            'payload_source': payload_source_value,
            'degraded': degraded_value,
            'detail': detail_value,
        }
    )
    service_name = str(registry_config['service_name'])
    status = 'ok' if selected_mode == 'embedded_local' and not degraded_value else 'degraded' if degraded_value else 'ok'
    if _is_local_dev_mode():
        upsert_service(
            service_name,
            resolve_service_port(str(configured_url), int(registry_config['default_port'])),
            status,
            detail_value,
        )
        replace_metrics(service_name, registry_metrics_for_dependency(dependency_name))
        return load_service(service_name) or {
            'service_name': service_name,
            'status': status,
            'detail': detail_value,
        }
    return {
        'service_name': service_name,
        'status': status,
        'detail': detail_value,
    }


def seed_embedded_dependency_registry() -> None:
    for dependency_name in DEPENDENCY_SERVICE_REGISTRY:
        update_dependency_registry_entry(dependency_name)


def dependency_debug_snapshot() -> dict[str, Any]:
    # SQLite dev registry is only available in local/dev mode; skip in production to avoid
    # resolve_sqlite_path() raising on a remote DATABASE_URL (railway crash fix).
    if _is_local_dev_mode():
        registry_services = {service['service_name']: service for service in load_all_services()}
    else:
        registry_services = {}
    snapshot: dict[str, Any] = {}
    for dependency_name in DEPENDENCY_SERVICE_REGISTRY:
        runtime = DEPENDENCY_RUNTIME_STATUS.get(dependency_name, {})
        service_name = dependency_service_name(dependency_name)
        snapshot[dependency_name] = {
            'selected_mode': runtime.get('selected_mode', dependency_mode(dependency_name)),
            'last_used_mode': runtime.get('last_used_mode', dependency_mode(dependency_name)),
            'last_error': runtime.get('last_error'),
            'registry_status': registry_services.get(service_name, {}).get('status'),
            'registry_detail': registry_services.get(service_name, {}).get('detail'),
            'payload_source': runtime.get('payload_source'),
            'degraded': runtime.get('degraded'),
        }
    return snapshot


def is_remote_service_url(configured_url: str | None) -> bool:
    if not configured_url or not configured_url.strip():
        return False
    parsed = urlparse(configured_url.strip())
    hostname = (parsed.hostname or '').lower()
    return hostname not in {'', 'localhost', '127.0.0.1', '::1'}


def embedded_service_namespace(service_slug: str) -> str:
    return f"_embedded_{service_slug.replace('-', '_')}_app"


def _embedded_service_app_dir(service_slug: str) -> Path:
    return REPO_ROOT / 'services' / service_slug / 'app'


def _ensure_embedded_service_package(service_slug: str) -> types.ModuleType:
    package_name = embedded_service_namespace(service_slug)
    package = sys.modules.get(package_name)
    if isinstance(package, types.ModuleType):
        return package
    service_app_dir = _embedded_service_app_dir(service_slug)
    package = types.ModuleType(package_name)
    package.__path__ = [str(service_app_dir)]
    package.__file__ = str(service_app_dir / '__init__.py')
    package.__package__ = package_name
    sys.modules[package_name] = package
    return package


@contextmanager
def embedded_service_import_context(service_slug: str):
    package_name = embedded_service_namespace(service_slug)
    previous_aliases = {
        name: module
        for name in EMBEDDED_ALIAS_MODULE_NAMES
        if (module := sys.modules.get(name)) is not None and not _is_embedded_alias_module(module)
    }
    package = _ensure_embedded_service_package(service_slug)
    try:
        for alias_name in EMBEDDED_ALIAS_MODULE_NAMES:
            sys.modules.pop(alias_name, None)
        sys.modules['app'] = package
        for alias_name in EMBEDDED_ALIAS_MODULE_NAMES[1:]:
            suffix = alias_name.split('.', 1)[1]
            unique_name = f'{package_name}.{suffix}'
            unique_module = sys.modules.get(unique_name)
            if unique_module is not None:
                sys.modules[alias_name] = unique_module
        yield package_name
    finally:
        for alias_name in EMBEDDED_ALIAS_MODULE_NAMES:
            sys.modules.pop(alias_name, None)
        for alias_name, previous in previous_aliases.items():
            if previous is not None:
                sys.modules[alias_name] = previous


@lru_cache(maxsize=None)
def load_embedded_service_main(service_slug: str):
    service_app_dir = _embedded_service_app_dir(service_slug)
    package_name = embedded_service_namespace(service_slug)
    main_module_name = f'{package_name}.main'
    _drop_embedded_alias_leaks()
    if main_module_name in sys.modules:
        return sys.modules[main_module_name]

    _ensure_embedded_service_package(service_slug)
    with embedded_service_import_context(service_slug):
        spec = importlib.util.spec_from_file_location(main_module_name, service_app_dir / 'main.py')
        if spec is None or spec.loader is None:
            raise RuntimeError(f'Unable to load embedded service module for {service_slug}.')
        module = importlib.util.module_from_spec(spec)
        sys.modules[main_module_name] = module
        sys.modules['app.main'] = module
        spec.loader.exec_module(module)
        for alias_name in EMBEDDED_ALIAS_MODULE_NAMES[1:]:
            alias_module = sys.modules.get(alias_name)
            if alias_module is not None:
                suffix = alias_name.split('.', 1)[1]
                alias_module.__name__ = f'{package_name}.{suffix}'
                alias_module.__package__ = package_name
                sys.modules[f'{package_name}.{suffix}'] = alias_module

    return module


def _model_dump(value: Any) -> Any:
    return value.model_dump() if hasattr(value, 'model_dump') else value


def _build_embedded_request(module: Any, model_name: str, payload: dict[str, Any]) -> tuple[Any, str]:
    request_model = getattr(module, model_name, None)
    if request_model is None or not hasattr(request_model, 'model_validate'):
        return payload, 'raw-payload'
    return request_model.model_validate(payload), model_name


def _log_embedded_adapter_path(service_slug: str, operation: str, adapter_path: str) -> None:
    logger.info('Embedded %s adapter path used for %s: %s', service_slug, operation, adapter_path)


def _resolve_embedded_callable(module: Any, adapter_candidates: list[tuple[str, ...]]) -> tuple[Any, str]:
    for candidate in adapter_candidates:
        current = module
        path_parts: list[str] = ['module']
        for segment in candidate:
            current = getattr(current, segment, None)
            path_parts.append(segment)
            if current is None:
                break
        if callable(current):
            return current, '.'.join(path_parts)
    raise AttributeError(f'Embedded module {getattr(module, "__name__", "<unknown>")} has no compatible adapter.')


def _invoke_embedded_callable(
    service_slug: str,
    operation: str,
    adapter_candidates: list[tuple[str, ...]],
    *args: Any,
) -> Any:
    module = load_embedded_service_main(service_slug)
    with embedded_service_import_context(service_slug):
        adapter, adapter_path = _resolve_embedded_callable(module, adapter_candidates)
        suffix = '' if not args else '(' + ', '.join(arg for arg in ('request',)[: len(args)]) + ')'
        _log_embedded_adapter_path(service_slug, operation, f'{adapter_path}{suffix}')
        return adapter(*args)


EMBEDDED_ADAPTER_CANDIDATES: dict[str, dict[str, list[tuple[str, ...]]]] = {
    'risk-engine': {
        'evaluate': [('embedded_evaluate',), ('evaluate_risk_internal',), ('evaluate_risk',), ('engine', 'evaluate')],
    },
    'threat-engine': {
        'dashboard': [('embedded_dashboard',), ('internal_dashboard',), ('dashboard',), ('engine', 'build_dashboard')],
        'contract': [('embedded_analyze_contract',), ('internal_analyze_contract',), ('analyze_contract',), ('engine', 'analyze_contract')],
        'transaction': [('embedded_analyze_transaction',), ('internal_analyze_transaction',), ('analyze_transaction',), ('engine', 'analyze_transaction')],
        'market': [('embedded_analyze_market',), ('internal_analyze_market',), ('analyze_market',), ('engine', 'analyze_market')],
    },
    'compliance-service': {
        'dashboard': [('embedded_dashboard',), ('dashboard',), ('internal_dashboard',), ('engine', 'dashboard')],
        'policy_state': [('embedded_policy_state',), ('policy_state',), ('engine', 'get_policy_state')],
        'governance_actions': [('embedded_governance_actions',), ('governance_actions',), ('engine', 'list_actions')],
        'governance_action': [('embedded_governance_action',), ('governance_action',), ('engine', 'get_action')],
        'screen/transfer': [('embedded_screen_transfer',), ('screen_transfer',), ('internal_screen_transfer',), ('engine', 'screen_transfer')],
        'screen/residency': [('embedded_screen_residency',), ('screen_residency',), ('internal_screen_residency',), ('engine', 'screen_residency')],
        'governance/actions': [('embedded_create_governance_action',), ('create_governance_action',), ('internal_create_governance_action',), ('engine', 'apply_governance_action')],
    },
    'reconciliation-service': {
        'dashboard': [('embedded_dashboard',), ('dashboard',), ('internal_dashboard',), ('engine', 'dashboard')],
        'incidents': [('embedded_list_incidents',), ('list_incidents',), ('engine', 'list_incidents')],
        'incident': [('embedded_get_incident',), ('get_incident',), ('engine', 'get_incident')],
        'reconcile/state': [('embedded_reconcile_state',), ('reconcile_state',), ('internal_reconcile_state',), ('engine', 'reconcile')],
        'backstop/evaluate': [('embedded_evaluate_backstop',), ('evaluate_backstop',), ('internal_evaluate_backstop',), ('engine', 'evaluate_backstop')],
        'incidents/record': [('embedded_record_incident',), ('record_incident',), ('internal_record_incident',), ('engine', 'record_incident')],
    },
}


def execute_embedded_risk_evaluation(payload: dict[str, Any]) -> dict[str, Any]:
    module = load_embedded_service_main('risk-engine')
    request, request_source = _build_embedded_request(module, 'RiskEvaluationRequest', payload)
    response = _invoke_embedded_callable('risk-engine', 'evaluate', EMBEDDED_ADAPTER_CANDIDATES['risk-engine']['evaluate'], request)
    _log_embedded_adapter_path('risk-engine', 'evaluate', f'request_source={request_source}')
    return _model_dump(response)


def execute_embedded_threat_dashboard() -> dict[str, Any]:
    module = load_embedded_service_main('threat-engine')
    scenarios = module.load_demo_requests() if callable(getattr(module, 'load_demo_requests', None)) else {}
    try:
        response = _invoke_embedded_callable(
            'threat-engine',
            'dashboard',
            [('embedded_dashboard',), ('internal_dashboard',), ('dashboard',)],
        )
        return _model_dump(response)
    except AttributeError:
        response = _invoke_embedded_callable('threat-engine', 'dashboard', [('engine', 'build_dashboard')], scenarios)
        return _model_dump(response)


def execute_embedded_threat_request(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    module = load_embedded_service_main('threat-engine')
    match kind:
        case 'contract':
            request, request_source = _build_embedded_request(module, 'ContractAnalysisRequest', payload)
            response = _invoke_embedded_callable('threat-engine', kind, EMBEDDED_ADAPTER_CANDIDATES['threat-engine']['contract'], request)
            return _model_dump(response)
        case 'transaction':
            request, request_source = _build_embedded_request(module, 'TransactionAnalysisRequest', payload)
            response = _invoke_embedded_callable('threat-engine', kind, EMBEDDED_ADAPTER_CANDIDATES['threat-engine']['transaction'], request)
            return _model_dump(response)
        case 'market':
            request, request_source = _build_embedded_request(module, 'MarketAnalysisRequest', payload)
            response = _invoke_embedded_callable('threat-engine', kind, EMBEDDED_ADAPTER_CANDIDATES['threat-engine']['market'], request)
            return _model_dump(response)
        case _:
            raise ValueError(f'Unsupported threat analysis kind: {kind}')
    raise AttributeError(f'Embedded threat-engine module has no compatible adapter for {kind}; request_source={request_source}.')


def execute_embedded_compliance_dashboard() -> dict[str, Any]:
    response = _invoke_embedded_callable('compliance-service', 'dashboard', EMBEDDED_ADAPTER_CANDIDATES['compliance-service']['dashboard'])
    return _model_dump(response)


def execute_embedded_compliance_policy_state() -> dict[str, Any] | None:
    response = _invoke_embedded_callable('compliance-service', 'policy_state', EMBEDDED_ADAPTER_CANDIDATES['compliance-service']['policy_state'])
    return _model_dump(response)


def execute_embedded_compliance_governance_actions() -> list[dict[str, Any]] | None:
    response = _invoke_embedded_callable('compliance-service', 'governance_actions', EMBEDDED_ADAPTER_CANDIDATES['compliance-service']['governance_actions'])
    return _model_dump(response)


def execute_embedded_compliance_governance_action(action_id: str) -> dict[str, Any] | None:
    response = _invoke_embedded_callable('compliance-service', 'governance_action', EMBEDDED_ADAPTER_CANDIDATES['compliance-service']['governance_action'], action_id)
    return _model_dump(response)


def execute_embedded_compliance_request(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    module = load_embedded_service_main('compliance-service')
    match path:
        case 'screen/transfer':
            request, request_source = _build_embedded_request(module, 'TransferScreeningRequest', payload)
            response = _invoke_embedded_callable('compliance-service', path, EMBEDDED_ADAPTER_CANDIDATES['compliance-service'][path], request)
            return _model_dump(response)
        case 'screen/residency':
            request, request_source = _build_embedded_request(module, 'ResidencyScreeningRequest', payload)
            response = _invoke_embedded_callable('compliance-service', path, EMBEDDED_ADAPTER_CANDIDATES['compliance-service'][path], request)
            return _model_dump(response)
        case 'governance/actions':
            request, request_source = _build_embedded_request(module, 'GovernanceActionRequest', payload)
            response = _invoke_embedded_callable('compliance-service', path, EMBEDDED_ADAPTER_CANDIDATES['compliance-service'][path], request)
            return _model_dump(response)
        case _:
            raise ValueError(f'Unsupported compliance path: {path}')
    raise AttributeError(f'Embedded compliance-service module has no compatible adapter for {path}; request_source={request_source}.')


def execute_embedded_resilience_dashboard() -> dict[str, Any]:
    response = _invoke_embedded_callable('reconciliation-service', 'dashboard', EMBEDDED_ADAPTER_CANDIDATES['reconciliation-service']['dashboard'])
    return _model_dump(response)


def execute_embedded_resilience_get(path: str) -> dict[str, Any] | list[dict[str, Any]] | None:
    match path:
        case 'incidents':
            response = _invoke_embedded_callable('reconciliation-service', path, EMBEDDED_ADAPTER_CANDIDATES['reconciliation-service']['incidents'])
            return _model_dump(response)
        case _ if path.startswith('incidents/'):
            event_id = path.split('/', 1)[1]
            response = _invoke_embedded_callable('reconciliation-service', path, EMBEDDED_ADAPTER_CANDIDATES['reconciliation-service']['incident'], event_id)
            return _model_dump(response)
        case _:
            raise ValueError(f'Unsupported resilience GET path: {path}')
    raise AttributeError(f'Embedded reconciliation-service module has no compatible GET adapter for {path}.')


def execute_embedded_resilience_post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    module = load_embedded_service_main('reconciliation-service')
    match path:
        case 'reconcile/state':
            request, request_source = _build_embedded_request(module, 'ReconciliationRequest', payload)
            response = _invoke_embedded_callable('reconciliation-service', path, EMBEDDED_ADAPTER_CANDIDATES['reconciliation-service'][path], request)
            return _model_dump(response)
        case 'backstop/evaluate':
            request, request_source = _build_embedded_request(module, 'BackstopRequest', payload)
            response = _invoke_embedded_callable('reconciliation-service', path, EMBEDDED_ADAPTER_CANDIDATES['reconciliation-service'][path], request)
            return _model_dump(response)
        case 'incidents/record':
            request, request_source = _build_embedded_request(module, 'IncidentRecordRequest', payload)
            response = _invoke_embedded_callable('reconciliation-service', path, EMBEDDED_ADAPTER_CANDIDATES['reconciliation-service'][path], request)
            return _model_dump(response)
        case _:
            raise ValueError(f'Unsupported resilience POST path: {path}')
    raise AttributeError(f'Embedded reconciliation-service module has no compatible POST adapter for {path}; request_source={request_source}.')


def dependency_mode(dependency_name: str) -> str:
    config = DEPENDENCY_CONFIG[dependency_name]
    env_value = globals()[config['env_value_key']]
    return 'remote_proxy' if is_remote_service_url(env_value) else 'embedded_local'


def record_dependency_runtime(
    dependency_name: str,
    mode: str,
    error: str | None = None,
    *,
    payload_source: str | None = None,
    degraded: bool | None = None,
    detail: str | None = None,
) -> None:
    status = DEPENDENCY_RUNTIME_STATUS.setdefault(dependency_name, {})
    status.update(
        {
            'configured_url': globals()[DEPENDENCY_CONFIG[dependency_name]['url_key']],
            'selected_mode': dependency_mode(dependency_name),
            'last_used_mode': mode,
            'last_error': error,
        }
    )
    if payload_source is not None:
        status['payload_source'] = payload_source
    if degraded is not None:
        status['degraded'] = degraded
    if detail is not None:
        status['detail'] = detail
    update_dependency_registry_entry(
        dependency_name,
        payload_source=payload_source,
        degraded=degraded,
        detail=detail,
        error=error,
    )


def dependency_diagnostics() -> dict[str, Any]:
    diagnostics: dict[str, Any] = {}
    registry_snapshot = dependency_debug_snapshot()
    for dependency_name, config in DEPENDENCY_CONFIG.items():
        existing = DEPENDENCY_RUNTIME_STATUS.get(dependency_name, {})
        diagnostics[dependency_name] = {
            'configured_url': globals()[config['url_key']],
            'remote_configured': is_remote_service_url(globals()[config['env_value_key']]),
            'selected_mode': dependency_mode(dependency_name),
            'last_used_mode': existing.get('last_used_mode', dependency_mode(dependency_name)),
            'last_error': existing.get('last_error'),
            'registry_status': registry_snapshot[dependency_name]['registry_status'],
            'payload_source': existing.get('payload_source'),
            'degraded': existing.get('degraded'),
        }
    return diagnostics


def embedded_service_health(service_slug: str, operation: str) -> dict[str, Any]:
    try:
        module = load_embedded_service_main(service_slug)
        candidates = EMBEDDED_ADAPTER_CANDIDATES[service_slug][operation]
        with embedded_service_import_context(service_slug):
            _resolve_embedded_callable(module, candidates)
        return {'ready': True, 'reason': None}
    except Exception as exc:
        return {'ready': False, 'reason': str(exc)}


def pilot_runtime_diagnostics() -> dict[str, Any]:
    schema = pilot_schema_status()
    demo = demo_seed_status(os.getenv('PILOT_DEMO_EMAIL', 'demo@decoda.app'))
    embedded_status = {
        'threat': embedded_service_health('threat-engine', 'dashboard'),
        'compliance': embedded_service_health('compliance-service', 'dashboard'),
        'resilience': embedded_service_health('reconciliation-service', 'dashboard'),
        'risk': embedded_service_health('risk-engine', 'evaluate'),
    }
    last_failure_reason = {
        'threat': DEPENDENCY_RUNTIME_STATUS.get('threat_engine', {}).get('last_error') or embedded_status['threat']['reason'],
        'compliance': DEPENDENCY_RUNTIME_STATUS.get('compliance_service', {}).get('last_error') or embedded_status['compliance']['reason'],
        'resilience': DEPENDENCY_RUNTIME_STATUS.get('reconciliation_service', {}).get('last_error') or embedded_status['resilience']['reason'],
        'risk': DEPENDENCY_RUNTIME_STATUS.get('risk_engine', {}).get('last_error') or embedded_status['risk']['reason'],
    }
    return {
        'pilotSchemaReady': bool(schema['ready']),
        'pilotSchemaStatus': schema['status'],
        'missingPilotTables': schema.get('missing_tables', []),
        'pilotSchemaMissingTables': schema.get('missing_tables', []),
        'pilotSchemaDiagnostics': schema,
        'demoSeedPresent': bool(demo['present']),
        'demoSeedStatus': demo['status'],
        'demoSeedEmail': demo['email'],
        'demoSeedDiagnostics': demo,
        'embeddedThreatReady': bool(embedded_status['threat']['ready']),
        'embeddedComplianceReady': bool(embedded_status['compliance']['ready']),
        'embeddedResilienceReady': bool(embedded_status['resilience']['ready']),
        'embeddedRiskReady': bool(embedded_status['risk']['ready']),
        'lastEmbeddedFailureReason': last_failure_reason,
    }


def auth_schema_error_response(exc: HTTPException) -> JSONResponse | None:
    if exc.status_code != 503:
        return None
    error_code = (exc.headers or {}).get('X-Decoda-Error-Code')
    if error_code != 'pilot_schema_missing' and 'Pilot auth schema is not initialized.' not in str(exc.detail):
        return None
    missing_tables = [
        table.strip()
        for table in ((exc.headers or {}).get('X-Decoda-Missing-Tables') or '').split(',')
        if table.strip()
    ]
    if not missing_tables and isinstance(exc.detail, str):
        marker = 'Missing required tables:'
        if marker in exc.detail:
            suffix = exc.detail.split(marker, 1)[1].split('.', 1)[0]
            missing_tables = [table.strip() for table in suffix.split(',') if table.strip()]
    payload = schema_missing_error_payload(missing_tables or ['users'])
    return JSONResponse(payload, status_code=exc.status_code, headers={'Cache-Control': 'no-store'})


def auth_backend_error_response(exc: HTTPException) -> JSONResponse | None:
    if exc.status_code != 503:
        return None
    error_code = (exc.headers or {}).get('X-Decoda-Error-Code')
    if error_code not in {'AUTH_BACKEND_UNAVAILABLE', 'AUTH_DB_QUOTA_EXCEEDED'}:
        return None
    detail = str(exc.detail or 'Authentication is temporarily unavailable. Please retry in a moment.')
    classification = (exc.headers or {}).get('X-Decoda-DB-Classification') or None
    # Contract choice: return HTTP 503 for infrastructure/database outages so clients do not mistake it for credential failure.
    payload: dict[str, Any] = {
        'code': error_code,
        'detail': detail,
        'message': detail,
        'retryable': True,
    }
    if classification:
        payload['classification'] = classification
    return JSONResponse(payload, status_code=exc.status_code, headers={'Cache-Control': 'no-store'})


def with_auth_schema_json(handler):
    try:
        return handler()
    except HTTPException as exc:
        backend_response = auth_backend_error_response(exc)
        if backend_response is not None:
            return backend_response
        response = auth_schema_error_response(exc)
        if response is not None:
            return response
        raise


def _normalize_action_route_response(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload or {})
    normalized_mode = str(normalized.get('mode') or ('live' if normalized.get('dry_run') is False else 'simulated'))
    normalized_execution_mode = str(
        normalized.get('execution_mode')
        or (
            (normalized.get('execution_metadata') or {}).get('execution_mode')
            if isinstance(normalized.get('execution_metadata'), dict)
            else ''
        )
        or normalized_mode
    )
    normalized['mode'] = normalized_mode
    normalized['execution_mode'] = normalized_execution_mode
    normalized['result_status'] = normalized.get('result_status') or normalized.get('status')
    tx_hash = normalized.get('tx_hash') or normalized.get('safe_tx_hash')
    execution_provenance = {
        **(normalized.get('execution_provenance') if isinstance(normalized.get('execution_provenance'), dict) else {}),
        'mode': normalized_mode,
        'execution_mode': normalized_execution_mode,
        'execution_state': normalized.get('execution_state'),
        'status': normalized.get('status'),
        'result_status': normalized.get('result_status'),
        'provider_request_id': normalized.get('provider_request_id'),
        'provider_response_id': normalized.get('provider_response_id'),
        'provider_id': normalized.get('provider_id') or normalized.get('provider_response_id') or normalized.get('provider_request_id'),
        'tx_hash': tx_hash,
        'safe_tx_hash': normalized.get('safe_tx_hash'),
        'result_code': normalized.get('result_code'),
        'error_code': normalized.get('error_code'),
        'error_reason': normalized.get('error_reason'),
        'failure_reason': normalized.get('failure_reason') or normalized.get('error_reason'),
        'executed_at': normalized.get('executed_at'),
        'approved_at': normalized.get('approved_at'),
        'failed_at': normalized.get('failed_at'),
        'created_at': normalized.get('created_at'),
        'provider_receipts': normalized.get('provider_receipts') if isinstance(normalized.get('provider_receipts'), list) else [],
        'execution_artifacts': normalized.get('execution_artifacts') if isinstance(normalized.get('execution_artifacts'), dict) else {},
    }
    normalized['execution_provenance'] = execution_provenance
    normalized['execution_evidence'] = {
        **(normalized.get('execution_evidence') if isinstance(normalized.get('execution_evidence'), dict) else {}),
        **execution_provenance,
    }
    existing_execution_metadata = normalized.get('execution_metadata') if isinstance(normalized.get('execution_metadata'), dict) else {}
    normalized['execution_metadata'] = {
        **existing_execution_metadata,
        'mode': execution_provenance.get('mode'),
        'execution_mode': execution_provenance.get('execution_mode'),
        'status': execution_provenance.get('status'),
        'result_status': execution_provenance.get('result_status'),
        'execution_state': execution_provenance.get('execution_state'),
        'provider_request_id': execution_provenance.get('provider_request_id'),
        'provider_response_id': execution_provenance.get('provider_response_id'),
        'provider_id': execution_provenance.get('provider_id'),
        'tx_hash': execution_provenance.get('tx_hash'),
        'safe_tx_hash': execution_provenance.get('safe_tx_hash'),
        'result_code': execution_provenance.get('result_code'),
        'error_code': execution_provenance.get('error_code'),
        'error_reason': execution_provenance.get('error_reason'),
        'failure_reason': execution_provenance.get('failure_reason'),
        'approved_at': execution_provenance.get('approved_at'),
        'executed_at': execution_provenance.get('executed_at'),
        'failed_at': execution_provenance.get('failed_at'),
        'result_summary': normalized.get('result_summary'),
        'final_status': execution_provenance.get('status'),
        'finalized_at': execution_provenance.get('executed_at') or execution_provenance.get('failed_at') or execution_provenance.get('approved_at'),
    }
    existing_audit = normalized.get('audit_metadata') if isinstance(normalized.get('audit_metadata'), dict) else {}
    normalized['audit_metadata'] = {
        **existing_audit,
        'mode': normalized_mode,
        'execution_mode': normalized_execution_mode,
        'status': normalized.get('status'),
        'result_status': normalized.get('result_status'),
        'execution_state': normalized.get('execution_state'),
        'action_id': normalized.get('id'),
        'incident_id': normalized.get('incident_id'),
        'alert_id': normalized.get('alert_id'),
        'created_by_user_id': normalized.get('created_by_user_id'),
        'approved_by_user_id': normalized.get('approved_by_user_id'),
        'provider_request_id': execution_provenance.get('provider_request_id'),
        'provider_response_id': execution_provenance.get('provider_response_id'),
        'provider_id': execution_provenance.get('provider_id'),
        'tx_hash': execution_provenance.get('tx_hash'),
        'error_reason': execution_provenance.get('error_reason'),
        'failure_reason': execution_provenance.get('failure_reason'),
        'error_code': execution_provenance.get('error_code'),
        'route_normalized_at': datetime.now(timezone.utc).isoformat(),
    }
    return normalized


def _normalize_action_list_route_response(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload or {})
    actions = normalized.get('actions') if isinstance(normalized.get('actions'), list) else []
    normalized['actions'] = [_normalize_action_route_response(item if isinstance(item, dict) else {}) for item in actions]
    return normalized


def fixture_diagnostics() -> dict[str, Any]:
    ingestion_runtime: dict[str, Any] = {}
    try:
        from services.api.app.activity_providers import monitoring_ingestion_runtime
        ingestion_runtime = monitoring_ingestion_runtime()
    except Exception as exc:  # pragma: no cover - defensive startup guard
        logger.warning('failed to compute monitoring ingestion runtime diagnostics: %s', exc)

    directories = {
        'risk_engine': {
            'path': str(RISK_ENGINE_DATA_DIR),
            'exists': RISK_ENGINE_DATA_DIR.is_dir(),
        },
        'reconciliation': {
            'path': str(RECONCILIATION_DATA_DIR),
            'exists': RECONCILIATION_DATA_DIR.is_dir(),
        },
    }
    files: dict[str, dict[str, dict[str, Any]]] = {}
    data_dirs = {
        'risk_engine': RISK_ENGINE_DATA_DIR,
        'reconciliation': RECONCILIATION_DATA_DIR,
    }
    for directory_name, filenames in FIXTURE_FILES.items():
        data_dir = data_dirs[directory_name]
        files[directory_name] = {
            filename: {
                'path': str(data_dir / filename),
                'exists': (data_dir / filename).is_file(),
            }
            for filename in filenames
        }
    return {
        'backend_build_id': BACKEND_BUILD_ID,
        'backend_git_commit': BACKEND_GIT_COMMIT,
        'version_marker': RUNTIME_MARKER,
        'directories': directories,
        'files': files,
        'modes': mode_flags(),
        'config': {
            'app_mode': os.getenv('APP_MODE', 'local'),
            'live_mode_enabled': live_mode_enabled(),
            'auth_token_secret_configured': auth_token_secret_configured(),
            'database_url_configured': resolved_database_url() is not None,
            'allowed_origins': ALLOWED_ORIGINS,
            'cors_allow_credentials': CORS_ALLOW_CREDENTIALS,
        },
        **pilot_runtime_diagnostics(),
        'startupBootstrap': STARTUP_BOOTSTRAP_STATUS,
        'dependencies': dependency_diagnostics(),
        'monitoring_ingestion_mode': ingestion_runtime.get('source', 'unknown'),
        'monitoring_ingestion_degraded': ingestion_runtime.get('degraded'),
        'monitoring_ingestion_reason': ingestion_runtime.get('reason'),
    }


def emit_quicknode_streams_webhook_version() -> None:
    """Log a stable marker proving which QuickNode Streams webhook build is live.

    Emitted once at startup so an operator can confirm from Railway logs alone
    that the deployed API commit actually includes the current webhook
    ingestion + diagnostic-logging code (services/api/app/quicknode_streams.py),
    without shell access to the running container. If a QuickNode POST returns
    200 but emits no quicknode_stream_* lines, this marker's git_commit tells
    you immediately whether the deploy is simply stale.
    """
    _quicknode_streams_logger.info(
        'quicknode_streams_webhook_version=%s git_commit=%s build_id=%s',
        QUICKNODE_STREAMS_WEBHOOK_VERSION,
        BACKEND_GIT_COMMIT or 'unavailable',
        BACKEND_BUILD_ID,
    )


def emit_quicknode_live_lane_started_at_startup() -> None:
    """Emit the mandatory quicknode_live_lane_started readiness marker at API boot.

    The live chain-tip lane is served by the /base-live route (this API process), which
    is event-driven and has no "start", so this boot-time emission is the proof — from
    Railway logs alone — that the live lane is deployed and its configuration is valid,
    with the live checkpoint identity, observed chain head, current checkpoint block,
    and lag. Best-effort: a missing DB/RPC never blocks or fails startup.
    """
    try:
        from services.api.app.quicknode_streams import (
            _make_base_rpc_client,
            emit_quicknode_live_lane_started,
        )
        from services.api.app.pilot import pg_connection

        rpc_client = None
        try:
            rpc_client = _make_base_rpc_client()
        except Exception:  # pragma: no cover - RPC resolution must never fail boot
            rpc_client = None
        with pg_connection() as connection:
            emit_quicknode_live_lane_started(
                connection,
                rpc_client=rpc_client,
                deployment_commit_sha=BACKEND_GIT_COMMIT or None,
            )
    except Exception:  # pragma: no cover - readiness marker is best-effort at boot
        _quicknode_streams_logger.warning('quicknode_live_lane_started_emit_failed', exc_info=True)


def emit_startup_fixture_diagnostics() -> None:
    # Emitted first, outside the try below, so a fixture-diagnostics failure can
    # never suppress the deployed-build marker.
    emit_quicknode_streams_webhook_version()
    # Live chain-tip lane readiness marker (task step 4). After the version marker so a
    # live-lane readiness failure can never suppress the deployed-build proof.
    emit_quicknode_live_lane_started_at_startup()
    # Polling-only MVP posture: emit the canonical monitoring_mode_resolved line so the
    # API process's active mode (polling vs. real-time Streams) is provable from logs.
    try:
        from services.api.app.monitoring_runtime_mode import log_monitoring_mode_resolved
        log_monitoring_mode_resolved(logger)
    except Exception as exc:  # pragma: no cover - defensive startup guard
        logger.warning('startup monitoring_mode_resolved emission skipped: %s', exc)
    try:
        diagnostics = fixture_diagnostics()
        identity = runtime_environment_identity()
        logger.info(
            'startup_git_commit_sha service_role=api git_commit_sha=%s',
            diagnostics['backend_git_commit'] or 'unavailable',
        )
        logger.info(
            'startup version=%s risk_dir=%s exists=%s sample_risk_request=%s '
            'reconciliation_dir=%s exists=%s critical_supply_divergence=%s '
            'critical_mismatch_paused_bridge=%s git_commit=%s app_mode=%s pilot_mode=%s live_mode=%s demo_mode=%s',
            diagnostics['backend_build_id'],
            diagnostics['directories']['risk_engine']['path'],
            diagnostics['directories']['risk_engine']['exists'],
            diagnostics['files']['risk_engine']['sample_risk_request.json']['exists'],
            diagnostics['directories']['reconciliation']['path'],
            diagnostics['directories']['reconciliation']['exists'],
            diagnostics['files']['reconciliation']['critical_supply_divergence_double_count_risk.json']['exists'],
            diagnostics['files']['reconciliation']['critical_mismatch_paused_bridge.json']['exists'],
            diagnostics['backend_git_commit'],
            diagnostics['modes']['app_mode'],
            diagnostics['modes']['pilot_mode'],
            diagnostics['modes']['live_mode_enabled'],
            diagnostics['modes']['demo_mode'],
        )
        logger.info(
            'api runtime identity app_mode=%s live_mode=%s railway_environment=%s railway_service=%s database_backend=%s database_fingerprint=%s',
            identity['app_mode'],
            identity['live_mode_enabled'],
            identity['railway_environment'] or 'unknown',
            identity['railway_service'] or 'unknown',
            identity['database_backend'],
            identity['database_fingerprint'],
        )
        logger.info(
            'event=startup_auth_env database_url_configured=%s auth_token_secret_configured=%s '
            'app_public_url=%s cors_allowed_origins=%s',
            resolved_database_url() is not None,
            auth_token_secret_configured(),
            os.getenv('APP_PUBLIC_URL', 'not_set').strip() or 'not_set',
            ','.join(ALLOWED_ORIGINS) or 'none',
        )
    except Exception as exc:  # pragma: no cover - defensive startup guard
        logger.warning('startup fixture diagnostics emission skipped: %s', exc)


def bootstrap_live_pilot() -> dict[str, Any]:
    global STARTUP_BOOTSTRAP_STATUS, HAS_EMITTED_INITIAL_STARTUP_RECONCILE_DB_DEGRADED_EVENT
    runtime_validation = validate_runtime_configuration()
    for warning in runtime_validation.get('warnings', []):
        logger.warning('startup configuration warning: %s', warning)
    errors = runtime_validation.get('errors', [])
    if errors:
        raise RuntimeError('; '.join(errors))
    _startup_env = os.getenv('APP_ENV', os.getenv('APP_MODE', 'development')).strip().lower()
    if _startup_env in {'production', 'prod', 'staging'} and os.getenv('REDIS_URL', '').strip():
        _redis_conn = rate_limit_connectivity()
        if not _redis_conn.get('connected'):
            raise RuntimeError(
                f'Production startup blocked: REDIS_URL is set but Redis is not reachable '
                f'(status={_redis_conn.get("status", "unknown")}, '
                f'error={_redis_conn.get("error", "none")}). '
                f'Verify REDIS_URL and Redis server health before deploying.'
            )
    STARTUP_BOOTSTRAP_STATUS = run_startup_migrations_if_enabled(process_role='api')
    applied_versions = STARTUP_BOOTSTRAP_STATUS.get('applied_versions', [])
    _all_applied = set(STARTUP_BOOTSTRAP_STATUS.get('all_applied_versions', []))
    if STARTUP_BOOTSTRAP_STATUS.get('ran'):
        logger.info('startup pilot bootstrap ran migrations: %s', ', '.join(applied_versions) or 'none')
        _MIG_0116 = '0116_normalize_sig_wallet_transfer_alert_payloads.sql'
        if _MIG_0116 in _all_applied:
            logger.info('startup_critical_migration_applied migration=%s', _MIG_0116)
        else:
            logger.warning(
                'startup_critical_migration_missing migration=%s action=run_migration_manually',
                _MIG_0116,
            )
    else:
        logger.info(
            'startup pilot bootstrap skipped for role=%s: %s',
            STARTUP_BOOTSTRAP_STATUS.get('process_role', 'api'),
            STARTUP_BOOTSTRAP_STATUS.get('reason', 'migration startup is disabled'),
        )
    logger.info('startup monitored_systems_reconcile=deferred reason=non_blocking_startup')
    return STARTUP_BOOTSTRAP_STATUS


@asynccontextmanager
async def lifespan(_: FastAPI):
    global MONITORING_BACKGROUND_TASK, MONITORING_LOOP_RUNTIME_STATE, ALERT_EVENT_BACKGROUND_TASK, DEFERRED_STARTUP_RECONCILE_TASK
    validate_secret_encryption_key_at_startup()
    validate_signing_secret_at_startup()
    for _warning in ai_triage.configuration_warnings():
        logger.warning('event=ai_triage_configuration_warning detail=%s', _warning)
    if _is_local_dev_mode():
        seed_service(SERVICE_NAME, PORT, DETAIL, DEFAULT_METRICS)
        seed_embedded_dependency_registry()
    bootstrap_live_pilot()
    emit_startup_fixture_diagnostics()
    set_background_loop_health(
        loop_running=False,
        last_successful_cycle=MONITORING_LOOP_RUNTIME_STATE.get('last_successful_cycle'),
        consecutive_failures=int(MONITORING_LOOP_RUNTIME_STATE.get('consecutive_failures') or 0),
        next_retry_at=MONITORING_LOOP_RUNTIME_STATE.get('next_retry_at'),
        backoff_seconds=MONITORING_LOOP_RUNTIME_STATE.get('backoff_seconds'),
    )
    _api_worker_enabled_val = (os.getenv('WORKER_ENABLED') or 'not_set').strip()
    _api_worker_disabled = _api_worker_enabled_val.lower() in {'0', 'false', 'no', 'off'}
    _api_chain_id_configured = (os.getenv('STAGING_EVM_CHAIN_ID') or os.getenv('EVM_CHAIN_ID') or 'not_set').strip()
    _api_rpc_raw = (os.getenv('STAGING_EVM_RPC_URL') or os.getenv('EVM_RPC_URL') or '').strip()
    try:
        from urllib.parse import urlparse as _urlparse
        _api_rpc_host = _urlparse(_api_rpc_raw).hostname or 'unconfigured'
    except Exception:
        _api_rpc_host = 'unconfigured'
    logger.info(
        'startup service_role=api WORKER_ENABLED=%s resolved_chain_id=%s rpc_host=%s',
        _api_worker_enabled_val,
        _api_chain_id_configured,
        _api_rpc_host,
    )
    if _api_worker_disabled:
        logger.info('startup monitoring_loop_skipped reason=WORKER_ENABLED=%s', _api_worker_enabled_val)
    if str(os.getenv('LIVE_MONITORING_ENABLED', 'true')).strip().lower() in {'1', 'true', 'yes', 'on'} and not _api_worker_disabled:
        async def _monitoring_loop() -> None:
            global MONITORING_LOOP_RUNTIME_STATE, HAS_EMITTED_INITIAL_MONITORING_DB_DEGRADED_EVENT
            interval = max(10, int(os.getenv('MONITOR_POLL_INTERVAL_SECONDS', '30')))
            default_backoff_base_seconds = max(5, int(os.getenv('MONITOR_DB_RETRY_BASE_SECONDS', '15')))
            default_backoff_cap_seconds = max(default_backoff_base_seconds, int(os.getenv('MONITOR_DB_RETRY_CAP_SECONDS', '300')))
            network_backoff_base_seconds = max(3, int(os.getenv('MONITOR_DB_RETRY_NETWORK_BASE_SECONDS', '10')))
            network_backoff_cap_seconds = max(network_backoff_base_seconds, int(os.getenv('MONITOR_DB_RETRY_NETWORK_CAP_SECONDS', '120')))
            quota_backoff_base_seconds = max(30, int(os.getenv('MONITOR_DB_RETRY_QUOTA_BASE_SECONDS', '60')))
            quota_backoff_cap_seconds = max(quota_backoff_base_seconds, int(os.getenv('MONITOR_DB_RETRY_QUOTA_CAP_SECONDS', '900')))
            consecutive_db_failures = 0
            last_db_failure_classification: str | None = None
            last_emitted_degraded_warning_state: tuple[str, int] | None = None
            while True:
                try:
                    run_monitoring_cycle(worker_name='monitoring-worker', limit=100, trigger_type='scheduler')
                    consecutive_db_failures = 0
                    last_db_failure_classification = None
                    last_emitted_degraded_warning_state = None
                    MONITORING_LOOP_RUNTIME_STATE = {
                        'loop_running': True,
                        'last_successful_cycle': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                        'consecutive_failures': 0,
                        'degraded': False,
                        'classification': None,
                        'reason': None,
                        'backoff_seconds': None,
                        'next_retry_at': None,
                        'db_host': extract_db_host_from_dsn(resolved_database_url()),
                        'updated_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                    }
                    set_background_loop_health(
                        loop_running=True,
                        last_successful_cycle=MONITORING_LOOP_RUNTIME_STATE['last_successful_cycle'],
                        consecutive_failures=0,
                        next_retry_at=None,
                        backoff_seconds=None,
                    )
                    await asyncio.sleep(interval)
                    continue
                except Exception as exc:
                    error_context = db_error_classification_context(exc)
                    classification = error_context['classification']
                    if classification in {'quota_exceeded', 'network_unreachable', 'db_unavailable', 'auth_error'}:
                        consecutive_db_failures += 1
                        state_downgraded = not bool(MONITORING_LOOP_RUNTIME_STATE.get('degraded'))
                        last_classification = last_db_failure_classification
                        last_db_failure_classification = classification
                        backoff_base_seconds = (
                            quota_backoff_base_seconds
                            if classification == 'quota_exceeded'
                            else network_backoff_base_seconds
                            if classification == 'network_unreachable'
                            else default_backoff_base_seconds
                        )
                        backoff_cap_seconds = (
                            quota_backoff_cap_seconds
                            if classification == 'quota_exceeded'
                            else network_backoff_cap_seconds
                            if classification == 'network_unreachable'
                            else default_backoff_cap_seconds
                        )
                        backoff_seconds = min(backoff_cap_seconds, backoff_base_seconds * (2 ** (consecutive_db_failures - 1)))
                        next_retry_at_dt = datetime.now(timezone.utc) + timedelta(seconds=backoff_seconds)
                        next_retry_at = next_retry_at_dt.isoformat().replace('+00:00', 'Z')
                        db_host = extract_db_host_from_dsn(resolved_database_url())
                        MONITORING_LOOP_RUNTIME_STATE = {
                            'loop_running': False,
                            'last_successful_cycle': MONITORING_LOOP_RUNTIME_STATE.get('last_successful_cycle'),
                            'consecutive_failures': consecutive_db_failures,
                            'degraded': True,
                            'classification': classification,
                            'reason': db_error_reason_label(classification),
                            'backoff_seconds': backoff_seconds,
                            'next_retry_at': next_retry_at,
                            'db_host': db_host,
                            'state_downgraded': state_downgraded,
                            'updated_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                        }
                        set_background_loop_health(
                            loop_running=False,
                            last_successful_cycle=MONITORING_LOOP_RUNTIME_STATE.get('last_successful_cycle'),
                            consecutive_failures=consecutive_db_failures,
                            next_retry_at=next_retry_at,
                            backoff_seconds=backoff_seconds,
                        )
                        warning_state = (classification, backoff_seconds)
                        should_emit_degraded_warning = (
                            not HAS_EMITTED_INITIAL_MONITORING_DB_DEGRADED_EVENT
                            or state_downgraded
                            or last_emitted_degraded_warning_state is None
                            or last_emitted_degraded_warning_state[0] != classification
                            or last_emitted_degraded_warning_state[1] != backoff_seconds
                        )
                        if should_emit_degraded_warning:
                            warning_details = ''
                            if error_context.get('classification_source'):
                                warning_details += ' classification_source=%s'
                            if error_context.get('raw_error_snippet'):
                                warning_details += ' raw_error_snippet=%s'
                            logger.info(
                                f'event=background_monitoring_db_degraded classification=%s reason=%s db_host=%s '
                                f'backoff_seconds=%s next_retry_at=%s state_downgraded=%s{warning_details}',
                                classification,
                                error_context['reason'],
                                db_host or 'unknown',
                                backoff_seconds,
                                next_retry_at,
                                state_downgraded,
                                *(value for value in (error_context.get('classification_source'), error_context.get('raw_error_snippet')) if value),
                            )
                            last_emitted_degraded_warning_state = warning_state
                            HAS_EMITTED_INITIAL_MONITORING_DB_DEGRADED_EVENT = True
                        if last_classification is None or last_classification != classification:
                            condensed_error = normalize_db_error_snippet(str(exc)) or 'unknown_error'
                            cause_details = ''
                            if error_context.get('classification_source'):
                                cause_details += ' classification_source=%s'
                            if error_context.get('raw_error_snippet'):
                                cause_details += ' raw_error_snippet=%s'
                            logger.info(
                                f'event=background_monitoring_db_degraded_cause classification=%s reason=%s db_host=%s '
                                f'condensed_error=%s{cause_details}',
                                classification,
                                error_context['reason'],
                                db_host or 'unknown',
                                condensed_error,
                                *(value for value in (error_context.get('classification_source'), error_context.get('raw_error_snippet')) if value),
                            )
                        await asyncio.sleep(backoff_seconds)
                        continue
                    logger.exception('background_monitoring_cycle_failed')
                    await asyncio.sleep(interval)
        MONITORING_BACKGROUND_TASK = asyncio.create_task(_monitoring_loop())
    if str(os.getenv('ALERT_EVENT_WORKER_ENABLED', 'true')).strip().lower() in {'1', 'true', 'yes', 'on'} and resolved_database_url():
        async def _alert_event_loop() -> None:
            interval = max(1, int(os.getenv('ALERT_EVENT_WORKER_INTERVAL_SECONDS', '2')))
            while True:
                try:
                    with pg_connection() as connection:
                        alert_delivery.publish_outbox_batch(connection)
                        alert_delivery.consume_bus_batch(connection)
                        connection.commit()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception('alert_event_worker_cycle_failed')
                await asyncio.sleep(interval)
        ALERT_EVENT_BACKGROUND_TASK = asyncio.create_task(_alert_event_loop())
    if str(os.getenv('LIVE_MONITORING_ENABLED', 'true')).strip().lower() in {'1', 'true', 'yes', 'on'} and resolved_database_url():
        _reconcile_timeout_seconds = max(10, int(os.getenv('STARTUP_RECONCILE_TIMEOUT_SECONDS', '60')))

        async def _deferred_startup_reconcile() -> None:
            await asyncio.sleep(0)  # yield so startup completes and "Application startup complete" is logged first
            loop = asyncio.get_running_loop()
            try:
                reconcile_result = await asyncio.wait_for(
                    loop.run_in_executor(None, reconcile_monitored_systems_for_enabled_targets),
                    timeout=_reconcile_timeout_seconds,
                )
                logger.info(
                    'startup_reconcile_deferred_complete scanned=%s upserted=%s invalid=%s auth_available=True',
                    reconcile_result.get('enabled_targets_scanned', 0),
                    reconcile_result.get('created_or_updated', 0),
                    len(reconcile_result.get('invalid_targets', [])),
                )
            except asyncio.TimeoutError:
                logger.warning(
                    'startup_reconcile_deferred_timeout timeout_seconds=%s auth_available=True monitoring_reconcile=skipped',
                    _reconcile_timeout_seconds,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning('startup_reconcile_deferred_failed auth_available=True monitoring_reconcile=failed', exc_info=True)

        DEFERRED_STARTUP_RECONCILE_TASK = asyncio.create_task(_deferred_startup_reconcile())
    yield
    if DEFERRED_STARTUP_RECONCILE_TASK is not None:
        DEFERRED_STARTUP_RECONCILE_TASK.cancel()
        with suppress(asyncio.CancelledError):
            await DEFERRED_STARTUP_RECONCILE_TASK
    if ALERT_EVENT_BACKGROUND_TASK is not None:
        ALERT_EVENT_BACKGROUND_TASK.cancel()
        with suppress(asyncio.CancelledError):
            await ALERT_EVENT_BACKGROUND_TASK
    if MONITORING_BACKGROUND_TASK is not None:
        MONITORING_BACKGROUND_TASK.cancel()
        with suppress(asyncio.CancelledError):
            await MONITORING_BACKGROUND_TASK


app = FastAPI(
    title='api service',
    summary='Phase 1 gateway for dashboard and live risk-engine / threat-engine data.',
    description='Aggregates shared local service state, proxies dashboard feeds to the risk-engine and threat-engine, and returns explicit fallback metadata when backend services are unavailable.',
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=CORS_ALLOW_CREDENTIALS,
    allow_methods=CORS_ALLOWED_METHODS,
    allow_headers=CORS_ALLOWED_HEADERS,
)
logger.info(
    'configured CORS origins=%s allow_credentials=%s methods=%s headers=%s',
    ALLOWED_ORIGINS,
    CORS_ALLOW_CREDENTIALS,
    CORS_ALLOWED_METHODS,
    CORS_ALLOWED_HEADERS,
)


# ---------------------------------------------------------------------------
# Task 4: Trace-ID middleware (added last so it runs first in ASGI chain)
# ---------------------------------------------------------------------------
@app.middleware('http')
async def trace_id_middleware(request: Request, call_next):
    incoming = request.headers.get('traceparent', '') or request.headers.get('X-Request-ID', '') or request.headers.get('X-Trace-ID', '')
    if incoming.startswith('00-') and len(incoming.split('-')) >= 4:
        trace_id = incoming.split('-')[1]
    else:
        trace_id = incoming.strip()[:64] if incoming.strip() else _uuid_mod.uuid4().hex
    correlation_id = (request.headers.get('X-Correlation-ID', '').strip()[:64] or trace_id)
    tokens = bind_trace(trace_id)
    request.state.trace_id = trace_id
    request.state.correlation_id = correlation_id
    started = asyncio.get_running_loop().time()
    try:
        response = await call_next(request)
    except Exception as exc:
        increment('decoda_http_requests_total', method=request.method, route=request.url.path, status='500')
        report_error(exc, operation='http_request', route=request.url.path, method=request.method, correlation_id=correlation_id)
        raise
    finally:
        observe('decoda_http_request_duration_seconds', asyncio.get_running_loop().time() - started, method=request.method, route=request.url.path)
        reset_trace(tokens)
    response.headers['X-Trace-ID'] = trace_id
    response.headers['X-Correlation-ID'] = correlation_id
    response.headers['traceparent'] = f'00-{trace_id[:32].ljust(32, "0")}-{_uuid_mod.uuid4().hex[:16]}-01'
    return response


# ---------------------------------------------------------------------------
# Task 5: Request metrics middleware
# ---------------------------------------------------------------------------
@app.middleware('http')
async def request_metrics_middleware(request: Request, call_next):
    response = await call_next(request)
    path_pattern = request.url.path
    key = f"{request.method}:{path_pattern}:{response.status_code}"
    _REQUEST_METRICS[key] = _REQUEST_METRICS.get(key, 0) + 1
    increment('decoda_http_requests_total', method=request.method, route=path_pattern, status=response.status_code)
    if response.status_code in {401, 403, 429}:
        increment('decoda_auth_anomalies_total', route=path_pattern, status=response.status_code)
    return response


# ---------------------------------------------------------------------------
# Task 3: API key enforcement middleware for /api/v1/* routes
# ---------------------------------------------------------------------------
_API_KEY_EXEMPT_PREFIXES = ('/health', '/ops/', '/auth/', '/billing/webhooks/', '/api/billing/')


@app.middleware('http')
async def api_key_enforcement_middleware(request: Request, call_next):
    global _AUTH_FAILURE_COUNT
    path = request.url.path
    if not path.startswith('/api/v1/'):
        return await call_next(request)
    # Check exempt prefixes (none start with /api/v1/ but kept for extensibility)
    for prefix in _API_KEY_EXEMPT_PREFIXES:
        if path == prefix or path.startswith(prefix):
            return await call_next(request)
    api_key_header = request.headers.get('X-API-Key', '').strip()
    if not api_key_header:
        _AUTH_FAILURE_COUNT += 1
        return JSONResponse(
            {'detail': 'X-API-Key required', 'code': 'API_KEY_MISSING'},
            status_code=401,
        )
    try:
        enforce_auth_rate_limit(request, 'api_key', api_key_header)
    except HTTPException as exc:
        return JSONResponse(
            {'detail': exc.detail, 'code': 'RATE_LIMIT_BACKEND_UNAVAILABLE' if exc.status_code == 503 else 'RATE_LIMITED'},
            status_code=exc.status_code,
        )
    # Query DB for matching key by prefix
    secret_prefix = api_key_header[:12]
    from services.api.app.pilot import _hash_workspace_api_key_secret as _hash_key
    candidate_hash = _hash_key(api_key_header)
    try:
        with pg_connection() as conn:
            rows = conn.execute(
                '''
                SELECT id, workspace_id, secret_hash, revoked_at, scopes
                FROM api_keys
                WHERE secret_prefix = %s AND revoked_at IS NULL
                LIMIT 5
                ''',
                (secret_prefix,),
            ).fetchall()
            matched = None
            for row in rows:
                stored_hash = str(row['secret_hash'] or '')
                if _hmac_mod.compare_digest(stored_hash.encode(), candidate_hash.encode()):
                    matched = row
                    break
            if matched is None:
                _AUTH_FAILURE_COUNT += 1
                return JSONResponse(
                    {'detail': 'Invalid API key', 'code': 'API_KEY_INVALID'},
                    status_code=401,
                )
            # Update last_used_at
            conn.execute(
                'UPDATE api_keys SET last_used_at = NOW() WHERE id = %s',
                (matched['id'],),
            )
            conn.commit()
            request.state.workspace_id = str(matched['workspace_id'])
    except Exception:
        logger.exception('api_key_enforcement_middleware_db_error')
        _AUTH_FAILURE_COUNT += 1
        return JSONResponse(
            {'detail': 'Invalid API key', 'code': 'API_KEY_INVALID'},
            status_code=401,
        )
    return await call_next(request)


@app.middleware('http')
async def log_monitoring_reconcile_transport(request: Request, call_next):
    path = request.url.path
    is_reconcile_request = path == '/monitoring/systems/reconcile'
    is_reconcile_preflight = request.method == 'OPTIONS' and request.headers.get('access-control-request-method', '').strip().upper() == 'POST' and path == '/monitoring/systems/reconcile'

    if is_reconcile_request or is_reconcile_preflight:
        logger.info(
            'monitoring_reconcile_transport method=%s path=%s origin=%s access_control_request_method=%s access_control_request_headers=%s',
            request.method,
            path,
            request.headers.get('origin', ''),
            request.headers.get('access-control-request-method', ''),
            request.headers.get('access-control-request-headers', ''),
        )
    try:
        response = await call_next(request)
    except Exception:
        if is_reconcile_request or is_reconcile_preflight:
            logger.exception(
                'monitoring_reconcile_transport_failed method=%s path=%s',
                request.method,
                path,
            )
        raise

    if is_reconcile_request or is_reconcile_preflight:
        logger.info(
            'monitoring_reconcile_transport_response method=%s path=%s status=%s',
            request.method,
            path,
            response.status_code,
        )

    return response


async def _call_next_safe(request: Request, call_next):
    """Wrap call_next so that a client disconnect / No-response-returned RuntimeError
    returns a 503 instead of propagating as an unhandled exception that floods logs."""
    try:
        return await call_next(request)
    except RuntimeError as exc:
        if 'No response returned' in str(exc):
            logger.debug(
                'middleware_client_disconnect_no_response method=%s path=%s',
                request.method,
                request.url.path,
            )
            return Response(status_code=503)
        raise


@app.middleware('http')
async def log_disallowed_cors_origin(request: Request, call_next):
    origin = request.headers.get('origin', '').strip()
    if origin:
        normalized_origin = _normalize_origin(origin)
        if normalized_origin and normalized_origin not in ALLOWED_ORIGINS:
            logger.warning(
                'CORS origin blocked origin=%s method=%s path=%s',
                normalized_origin,
                request.method,
                request.url.path,
            )
    return await _call_next_safe(request, call_next)


_CSRF_SAFE_METHODS = frozenset({'GET', 'HEAD', 'OPTIONS'})
# Paths exempt from CSRF enforcement even when authenticated.
# Billing webhooks use provider-signed payloads; auth bootstrap endpoints
# are unauthenticated by definition.
_CSRF_EXEMPT_PREFIXES = (
    '/health',
    '/billing/webhooks',
    '/api/billing',
    '/auth/signin',
    '/auth/signup',
    '/auth/verify-email',
    '/auth/forgot-password',
    '/auth/reset-password',
    '/auth/mfa/complete-signin',
    '/auth/resend-verification',
    '/auth/csrf-token',
)


@app.middleware('http')
async def enforce_csrf_on_mutations(request: Request, call_next):
    if request.method in _CSRF_SAFE_METHODS:
        return await _call_next_safe(request, call_next)
    # CSRF enforcement requires AUTH_TOKEN_SECRET to sign/validate tokens.
    # Without it the entire auth system is non-functional, so skip in that case.
    if not auth_token_secret_configured():
        return await _call_next_safe(request, call_next)
    path = request.url.path
    for prefix in _CSRF_EXEMPT_PREFIXES:
        if path == prefix or path.startswith(prefix + '/'):
            return await _call_next_safe(request, call_next)
    authorization = request.headers.get('authorization', '')
    if not authorization.startswith('Bearer '):
        return await _call_next_safe(request, call_next)
    csrf_token = request.headers.get('x-csrf-token', '').strip()
    if not csrf_token or not validate_csrf_token(csrf_token):
        return JSONResponse(
            {'detail': 'CSRF token missing or invalid.', 'code': 'CSRF_INVALID'},
            status_code=403,
        )
    return await _call_next_safe(request, call_next)


# ---------------------------------------------------------------------------
# P7: Request body size limits
# ---------------------------------------------------------------------------
_BODY_SIZE_EXEMPT_PREFIXES = ('/health', '/metrics', '/stream/')

_DEFAULT_MAX_REQUEST_BODY_BYTES = 10 * 1024 * 1024   # 10 MiB for general JSON
_DEFAULT_MAX_UPLOAD_BODY_BYTES  = 50 * 1024 * 1024   # 50 MiB for uploads
_DEFAULT_MAX_INGEST_BODY_BYTES  = 20 * 1024 * 1024   # 20 MiB for telemetry ingest

_UPLOAD_PATH_PREFIXES  = ('/uploads/', '/export/', '/exports/')
_INGEST_PATH_PREFIXES  = ('/telemetry/', '/monitoring/ingest/', '/ingest/', '/api/integrations/quicknode/')


def _resolve_body_limit(path: str) -> int:
    for prefix in _UPLOAD_PATH_PREFIXES:
        if path.startswith(prefix):
            return int(os.getenv('MAX_UPLOAD_BODY_BYTES', _DEFAULT_MAX_UPLOAD_BODY_BYTES))
    for prefix in _INGEST_PATH_PREFIXES:
        if path.startswith(prefix):
            return int(os.getenv('MAX_INGEST_BODY_BYTES', _DEFAULT_MAX_INGEST_BODY_BYTES))
    return int(os.getenv('MAX_REQUEST_BODY_BYTES', _DEFAULT_MAX_REQUEST_BODY_BYTES))


@app.middleware('http')
async def body_size_limit_middleware(request: Request, call_next):
    path = request.url.path
    for prefix in _BODY_SIZE_EXEMPT_PREFIXES:
        if path == prefix or path.startswith(prefix):
            return await _call_next_safe(request, call_next)
    content_length_header = request.headers.get('content-length', '').strip()
    if content_length_header:
        try:
            declared_size = int(content_length_header)
        except ValueError:
            declared_size = 0
        limit = _resolve_body_limit(path)
        if declared_size > limit:
            return JSONResponse(
                {'detail': f'Request body too large. Maximum {limit} bytes allowed.', 'code': 'PAYLOAD_TOO_LARGE'},
                status_code=413,
            )
    return await _call_next_safe(request, call_next)


@app.get('/auth/csrf-token', summary='Issue a CSRF token for state-changing requests')
def auth_csrf_token_endpoint() -> dict[str, Any]:
    return {'csrf_token': issue_csrf_token()}


@app.get('/health', summary='API health check', description='Returns the API runtime mode and local persistence configuration.')
def health() -> dict[str, object]:
    from services.api.app.activity_providers import monitoring_ingestion_runtime
    ingestion_runtime = monitoring_ingestion_runtime()
    retention_worker = get_retention_worker_health()
    return {
        'status': 'ok',
        'service': SERVICE_NAME,
        'port': PORT,
        'app_mode': os.getenv('APP_MODE', 'local'),
        'database_url': masked_database_url(),
        'database_url_configured': resolved_database_url() is not None,
        'database_backend': runtime_environment_identity()['database_backend'],
        'redis_enabled': os.getenv('REDIS_ENABLED', 'false').lower() == 'true',
        'shared_backends': {
            'rate_limit': rate_limit_connectivity(),
            'alert_stream': alert_stream.connectivity_sync(),
        },
        'alert_subscribers': alert_stream.subscriber_health(),
        'alert_delivery': alert_delivery_health(),
        'risk_engine_url': RISK_ENGINE_URL,
        'threat_engine_url': THREAT_ENGINE_URL,
        'compliance_service_url': COMPLIANCE_SERVICE_URL,
        'reconciliation_service_url': RECONCILIATION_SERVICE_URL,
        'pilot_mode': pilot_mode(),
        'live_mode_enabled': live_mode_enabled(),
        'backend_build_id': BACKEND_BUILD_ID,
        'backend_git_commit': BACKEND_GIT_COMMIT,
        'dependencies': dependency_diagnostics(),
        'monitoring_ingestion_mode': ingestion_runtime.get('source'),
        'monitoring_ingestion_degraded': ingestion_runtime.get('degraded'),
        'monitoring_ingestion_reason': ingestion_runtime.get('reason'),
        'billing': billing_runtime_status(),
        'retention_worker': retention_worker,
    }


@app.get('/auth/health', summary='Authentication service health check', description='Returns authentication service availability. Must respond quickly regardless of background monitoring reconcile state.')
def auth_health() -> dict[str, object]:
    return {
        'status': 'ok',
        'service': 'auth',
        'auth_token_configured': auth_token_secret_configured(),
        'database_url_configured': resolved_database_url() is not None,
    }


@app.get('/ops/runtime/cors', summary='Runtime CORS diagnostics', description='Returns safe runtime CORS origin configuration for operators.')
def runtime_cors_diagnostics() -> dict[str, Any]:
    return {
        'allowed_origins': ALLOWED_ORIGINS,
        'allow_credentials': CORS_ALLOW_CREDENTIALS,
        'allowed_methods': CORS_ALLOWED_METHODS,
        'allowed_headers': CORS_ALLOWED_HEADERS,
        'runtime': {
            'app_env': os.getenv('APP_ENV', os.getenv('APP_MODE', 'development')),
            'source_env': 'CORS_ALLOWED_ORIGINS' if parse_csv_env('CORS_ALLOWED_ORIGINS', []) else (
                'ALLOWED_ORIGINS' if parse_csv_env('ALLOWED_ORIGINS', []) else 'default'
            ),
        },
    }


@app.get('/health/readiness', summary='Production readiness health check', description='Returns readiness status and remediation guidance for production-only dependencies.')
def health_readiness() -> dict[str, Any]:
    validation = validate_runtime_configuration()
    errors = validation.get('errors', [])
    warnings = validation.get('warnings', [])
    checks = validation.get('checks', {})
    app_env = os.getenv('APP_ENV', os.getenv('APP_MODE', 'development')).strip().lower()
    is_production_like = app_env in {'production', 'prod'}
    live_mode_check = checks.get('live_mode_enabled', {})
    production_live_mode_drift = bool(is_production_like and not live_mode_check.get('ok', True))

    if errors:
        status_value = 'not_ready'
    elif warnings:
        status_value = 'degraded'
    else:
        status_value = 'healthy'

    rate_limit_backend = checks.get('rate_limit_backend', 'redis' if checks.get('distributed_rate_limiter', {}).get('ok') else 'memory')
    rate_limit_enterprise_ready = checks.get('rate_limit_enterprise_ready', checks.get('distributed_rate_limiter', {}).get('ok', False))
    rate_limit_health = rate_limit_connectivity()
    alert_stream_health = alert_stream.connectivity_sync()
    shared_backends_ready = bool(rate_limit_health.get('connected') and alert_stream_health.get('connected'))
    delivery_health = alert_delivery_health()
    durable_alert_delivery_ready = bool(delivery_health.get('ready'))
    if is_production_like and (not shared_backends_ready or not durable_alert_delivery_ready):
        status_value = 'not_ready'
    retention_worker = get_retention_worker_health()
    return {
        'status': status_value,
        'service': SERVICE_NAME,
        'app_mode': os.getenv('APP_MODE', 'local'),
        'app_env': app_env,
        'production_live_mode_drift': production_live_mode_drift,
        'redis_configured': bool(checks.get('redis_configured', rate_limit_backend == 'redis')),
        'redis_status': checks.get('redis_status', 'configured' if rate_limit_backend == 'redis' else 'not_configured'),
        'rate_limit_backend': rate_limit_backend,
        'rate_limit_enterprise_ready': bool(rate_limit_enterprise_ready),
        'enterprise_ready': bool(rate_limit_enterprise_ready) and shared_backends_ready and durable_alert_delivery_ready and not errors,
        'shared_backends': {
            'rate_limit': rate_limit_health,
            'alert_stream': alert_stream_health,
        },
        'alert_subscribers': alert_stream.subscriber_health(),
        'alert_delivery': delivery_health,
        'warning': None,
        'errors': errors,
        'warnings': warnings,
        'checks': checks,
        'billing': billing_runtime_status(),
        'retention_worker': retention_worker,
        'checked_at': datetime.now(timezone.utc).isoformat(),
    }


@app.get('/health/diagnostics', summary='Machine-readable startup diagnostics', description='Returns explicit pass/fail checks for production startup dependencies without leaking secret values.')
def health_diagnostics() -> dict[str, Any]:
    readiness = health_readiness()
    return {
        'status': readiness['status'],
        'service': readiness['service'],
        'app_mode': readiness['app_mode'],
        'app_env': readiness['app_env'],
        'production_live_mode_drift': readiness['production_live_mode_drift'],
        'checked_at': readiness['checked_at'],
        'checks': readiness['checks'],
        'shared_backends': readiness['shared_backends'],
        'alert_subscribers': readiness['alert_subscribers'],
        'billing': readiness['billing'],
        'retention_worker': readiness['retention_worker'],
    }


@app.get('/debug/fixtures', summary='Read-only fixture diagnostics', description='Returns the deployed backend build id plus resolved fixture directories and file existence flags for deploy verification.')
def debug_fixtures() -> dict[str, Any]:
    _require_debug_endpoint_allowed()
    return {
        'status': 'ok',
        'service': SERVICE_NAME,
        **fixture_diagnostics(),
    }


@app.get('/debug/downstream-status', summary='Downstream dependency diagnostics', description='Returns dependency mode, registry state, and payload truth for each embedded or proxied downstream service.')
def debug_downstream_status() -> dict[str, Any]:
    _require_debug_endpoint_allowed()
    seed_embedded_dependency_registry()
    return {
        'status': 'ok',
        'service': SERVICE_NAME,
        'dependencies': dependency_debug_snapshot(),
    }


@app.get('/health/details', summary='Deployment verification details', description='Returns a safe runtime marker plus resolved fixture paths and mode flags for deploy verification.')
def health_details() -> dict[str, Any]:
    return {
        'status': 'ok',
        'service': SERVICE_NAME,
        **fixture_diagnostics(),
    }


@app.get('/state', summary='API seeded state', description='Returns the service registry row written into the shared local SQLite file.')
def state() -> dict[str, object]:
    if _is_local_dev_mode():
        return {
            'service': load_service(SERVICE_NAME),
            'sqlite_path': str(resolve_sqlite_path()),
            'local_registry_database_url': local_database_url(),
        }
    return {'service': None, 'sqlite_path': None, 'local_registry_database_url': None}


@app.get('/services', summary='List registered local services', description='Returns the shared local service registry used to populate the dashboard status cards.')
def services() -> dict[str, object]:
    if _is_local_dev_mode():
        seed_service(SERVICE_NAME, PORT, DETAIL, DEFAULT_METRICS)
        seed_embedded_dependency_registry()
        payload = dashboard_payload()
        return {
            'mode': payload['mode'],
            'database_url': masked_database_url(),
            'services': payload['services'],
        }
    return {'mode': 'production', 'database_url': masked_database_url(), 'services': []}


@app.get('/dashboard', summary='Dashboard service snapshot', description='Returns the local dashboard summary cards and service registry information for the frontend.')
def dashboard() -> dict[str, object]:
    if _is_local_dev_mode():
        seed_service(SERVICE_NAME, PORT, DETAIL, DEFAULT_METRICS)
        seed_embedded_dependency_registry()
        payload = dict(dashboard_payload())
        payload['database_url'] = masked_database_url()
        return payload
    return {'mode': 'production', 'database_url': masked_database_url(), 'services': [], 'cards': []}


@app.get('/risk/dashboard', summary='Dashboard risk feed', description='Builds the dashboard transaction queue from live risk-engine evaluations and falls back to explicit demo-safe records when the risk-engine is unavailable.')
def risk_dashboard(request: Request) -> dict[str, object]:
    authenticate_request(request)
    queue = build_risk_dashboard_queue()
    live_count = sum(1 for item in queue if item['live_data'])
    degraded = live_count != len(queue)
    message = 'Live risk-engine data loaded successfully.' if not degraded else 'Risk-engine unavailable or timed out for one or more queue items. Returning fallback-safe dashboard records.'
    record_dependency_runtime(
        'risk_engine',
        dependency_mode('risk_engine') if not degraded else 'fallback',
        None if not degraded else 'One or more embedded or proxied risk evaluations failed.',
        payload_source='live' if not degraded else 'fallback',
        degraded=degraded,
        detail=EMBEDDED_SERVICE_STATUS_DETAIL if not degraded and dependency_mode('risk_engine') == 'embedded_local' else message,
    )
    payload = {
        'source': 'live' if not degraded else 'fallback',
        'degraded': degraded,
        'message': message,
        'risk_engine': {
            'url': RISK_ENGINE_URL,
            'timeout_seconds': RISK_ENGINE_TIMEOUT_SECONDS,
            'mode': dependency_mode('risk_engine'),
            'live_items': live_count,
            'fallback_items': len(queue) - live_count,
        },
        'generated_at': queue[0]['updated_at'] if queue else None,
        'summary': build_risk_summary(queue),
        'transaction_queue': [serialize_queue_item(item) for item in queue],
        'risk_alerts': build_risk_alerts(queue),
        'contract_scan_results': build_contract_scan_results(queue),
        'decisions_log': build_decisions_log(queue),
    }
    return attach_dependency_diagnostics(payload, 'risk_engine', fallback_reason=None if not degraded else 'One or more risk queue evaluations used fallback data.')


@app.get('/threat/dashboard', summary='Feature 2 threat dashboard feed', description='Returns the threat-engine dashboard payload when available and explicit fallback demo data when the threat-engine is unavailable.')
def threat_dashboard(request: Request) -> dict[str, Any]:
    authenticate_request(request)
    payload = fetch_threat_dashboard()
    if payload is not None:
        return payload
    raise HTTPException(status_code=503, detail={'code': 'THREAT_ENGINE_UNAVAILABLE', 'message': 'Threat dashboard unavailable without live threat-engine evidence.'})


def _require_threat_response(kind: str, normalized: dict[str, Any]) -> dict[str, Any]:
    response = proxy_threat(kind, normalized)
    if response is None:
        raise HTTPException(status_code=503, detail={'code': 'THREAT_ENGINE_UNAVAILABLE', 'analysis_type': kind, 'message': 'Live threat provider unavailable; fallback payloads are disabled in production.'})
    return _attach_rule_evidence_catalog(response)


THREAT_RULE_IDENTIFIER_CATALOG: tuple[tuple[str, str], ...] = (
    ('oracle_nav_divergence', 'Oracle NAV divergence'),
    ('proof_of_reserve_stale', 'Proof of reserve stale'),
    ('custody_wallet_movement_anomaly', 'Custody wallet movement anomaly'),
    ('unauthorized_mint_burn', 'Unauthorized mint/burn'),
    ('abnormal_redemption_activity', 'Abnormal redemption activity'),
    ('compliance_exposure', 'Compliance exposure'),
    ('monitoring_coverage_gap', 'Monitoring coverage gap'),
)


def _attach_rule_evidence_catalog(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload or {})
    evidence_source = str(normalized.get('evidence_source') or normalized.get('source') or 'unknown').strip().lower() or 'unknown'
    active_rule_ids: set[str] = set()
    for candidate in (normalized.get('triggered_rule_ids'), normalized.get('rule_ids'), normalized.get('triggered_rules')):
        if isinstance(candidate, list):
            for value in candidate:
                key = str(value or '').strip().lower()
                if key:
                    active_rule_ids.add(key)
    if isinstance(normalized.get('rule_id'), str):
        active_rule_ids.add(str(normalized.get('rule_id')).strip().lower())
    normalized['rule_catalog'] = [
        {
            'id': rule_id,
            'label': label,
            'status': 'triggered' if rule_id in active_rule_ids else 'visible_stub',
            'evidence_source': evidence_source,
            'provenance': 'threat_engine_response',
        }
        for rule_id, label in THREAT_RULE_IDENTIFIER_CATALOG
    ]
    return normalized


@app.post('/threat/analyze/contract', summary='Feature 2 contract analysis', description='Proxies a contract analysis request to the threat-engine and falls back to a conservative local rule summary if the engine is unavailable.')
def threat_analyze_contract(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    authenticate_request(request)
    normalized, _ = normalize_threat_payload('contract', payload)
    return _require_threat_response('contract', normalized)


@app.post('/threat/analyze/transaction', summary='Feature 2 transaction analysis', description='Proxies a transaction intent analysis request to the threat-engine and falls back to a conservative local rule summary if the engine is unavailable.')
def threat_analyze_transaction(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    authenticate_request(request)
    normalized, _ = normalize_threat_payload('transaction', payload)
    return _require_threat_response('transaction', normalized)


@app.post('/threat/analyze/market', summary='Feature 2 market anomaly analysis', description='Proxies a market anomaly request to the threat-engine and falls back to a conservative local rule summary if the engine is unavailable.')
def threat_analyze_market(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    authenticate_request(request)
    normalized, _ = normalize_threat_payload('market', payload)
    return _require_threat_response('market', normalized)


@app.get('/compliance/dashboard', summary='Feature 3 compliance dashboard feed', description='Returns the compliance-service dashboard payload when available and explicit fallback demo data when the compliance service is unavailable.')
def compliance_dashboard(request: Request) -> dict[str, Any]:
    authenticate_request(request)
    payload = fetch_compliance_dashboard()
    if payload is not None:
        return payload
    record_dependency_runtime('compliance_service', 'fallback', 'Compliance dashboard request failed; serving fallback dashboard.', payload_source='fallback', degraded=True, detail='Compliance dashboard fallback active')
    return attach_dependency_diagnostics(fallback_compliance_dashboard(), 'compliance_service', fallback_reason='Compliance dashboard fell back after embedded or remote execution failed.')


@app.post('/compliance/screen/transfer', summary='Feature 3 transfer compliance screening', description='Proxies a transfer screening request to the compliance service and falls back to a conservative deterministic local decision if the service is unavailable.')
def compliance_screen_transfer(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    authenticate_request(request)
    response = proxy_compliance('screen/transfer', payload)
    return response or fallback_transfer_screening(payload)


@app.post('/compliance/screen/residency', summary='Feature 3 residency compliance screening', description='Proxies a residency screening request to the compliance service and falls back to a deterministic local policy response if the service is unavailable.')
def compliance_screen_residency(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    authenticate_request(request)
    response = proxy_compliance('screen/residency', payload)
    return response or fallback_residency_screening(payload)


@app.get('/compliance/policy/state', summary='Feature 3 compliance policy state', description='Returns live compliance policy state when the compliance service is available and fallback demo policy state otherwise.')
def compliance_policy_state(request: Request) -> dict[str, Any]:
    authenticate_request(request)
    response = fetch_compliance_policy_state()
    return response or fallback_compliance_dashboard()['policy_state']


@app.get('/compliance/governance/actions', summary='Feature 3 governance actions list', description='Returns governance actions from the compliance service or fallback demo ledger actions when unavailable.')
def compliance_governance_actions(request: Request) -> list[dict[str, Any]]:
    authenticate_request(request)
    response = fetch_compliance_governance_actions()
    return response or fallback_compliance_dashboard()['latest_governance_actions']


@app.get('/compliance/governance/actions/{action_id}', summary='Feature 3 governance action detail', description='Returns one governance action from the compliance service or fallback data when unavailable.')
def compliance_governance_action(action_id: str, request: Request) -> dict[str, Any]:
    authenticate_request(request)
    response = fetch_compliance_governance_action(action_id)
    if response is not None:
        return response
    for action in fallback_compliance_dashboard()['latest_governance_actions']:
        if action['action_id'] == action_id:
            return action
    return {'detail': f'Unknown action_id: {action_id}', 'source': 'fallback', 'degraded': True}


@app.post('/compliance/governance/actions', summary='Feature 3 governance action create', description='Creates a governance action via the compliance service or records a deterministic fallback action when the service is unavailable.')
def compliance_create_governance_action(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    authenticate_request(request)
    response = proxy_compliance('governance/actions', payload)
    return response or fallback_governance_action(payload)


@app.get('/resilience/dashboard', summary='Feature 4 resilience dashboard feed', description='Returns the reconciliation-service dashboard payload when available and explicit fallback resilience data when the service is unavailable.')
def resilience_dashboard(request: Request) -> dict[str, Any]:
    authenticate_request(request)
    payload = fetch_resilience_dashboard()
    if payload is not None:
        return with_resilience_normalized_risk(payload)
    record_dependency_runtime('reconciliation_service', 'fallback', 'Resilience dashboard request failed; serving fallback dashboard.', payload_source='fallback', degraded=True, detail='Resilience dashboard fallback active')
    return attach_dependency_diagnostics(with_resilience_normalized_risk(fallback_resilience_dashboard()), 'reconciliation_service', fallback_reason='Resilience dashboard fell back after embedded or remote execution failed.')


@app.get(
    '/ops/dashboard-page-data',
    summary='Aggregated dashboard page payload',
    description='Returns dashboard + risk + threat + compliance + resilience payloads in a single backend response for initial authenticated dashboard render.',
)
def ops_dashboard_page_data(request: Request) -> dict[str, Any]:
    runtime_payload = with_auth_schema_json(lambda: monitoring_runtime_status(request))
    return {
        'dashboard': dashboard(),
        'risk_dashboard': risk_dashboard(request),
        'threat_dashboard': threat_dashboard(request),
        'compliance_dashboard': compliance_dashboard(request),
        'resilience_dashboard': resilience_dashboard(request),
        'workspace_monitoring_summary': runtime_payload.get('workspace_monitoring_summary'),
        'background_loop_health': runtime_payload.get('background_loop_health'),
    }


@app.post('/resilience/reconcile/state', summary='Feature 4 cross-chain reconciliation', description='Proxies a reconciliation request to the reconciliation-service and falls back to a deterministic local reconciliation summary if the service is unavailable.')
def resilience_reconcile_state(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    authenticate_request(request)
    response = proxy_resilience_post('reconcile/state', payload)
    return response or fallback_reconcile_state(payload)


@app.post('/resilience/backstop/evaluate', summary='Feature 4 liquidity backstop evaluation', description='Proxies a backstop evaluation request to the reconciliation-service and falls back to deterministic local safeguards when the service is unavailable.')
def resilience_backstop_evaluate(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    authenticate_request(request)
    response = proxy_resilience_post('backstop/evaluate', payload)
    return response or fallback_backstop_evaluate(payload)


@app.post('/resilience/incidents/record', summary='Feature 4 resilience incident create', description='Creates a resilience incident via the reconciliation-service or records a deterministic fallback incident when the service is unavailable.')
def resilience_record_incident(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    authenticate_request(request)
    response = proxy_resilience_post('incidents/record', payload)
    return response or fallback_incident_record(payload)


@app.get('/resilience/incidents', summary='Feature 4 resilience incident list', description='Returns resilience incidents from the reconciliation-service. Returns empty list with unavailable status in production when service is unreachable.')
def resilience_incidents(request: Request) -> list[dict[str, Any]]:
    authenticate_request(request)
    response = proxy_resilience_get('incidents')
    if response is None:
        if _is_production_like_runtime():
            return []
        return [with_resilience_incident_normalized_risk(i) for i in fallback_resilience_dashboard()['latest_incidents']]
    return [with_resilience_incident_normalized_risk(incident) for incident in response]


@app.get('/resilience/incidents/{event_id}', summary='Feature 4 resilience incident detail', description='Returns one resilience incident from the reconciliation-service or fallback data when unavailable.')
def resilience_incident(event_id: str, request: Request) -> dict[str, Any]:
    authenticate_request(request)
    response = proxy_resilience_get(f'incidents/{event_id}')
    if response is not None:
        return with_resilience_incident_normalized_risk(response)
    if not _is_production_like_runtime():
        for incident in fallback_resilience_dashboard()['latest_incidents']:
            if incident['event_id'] == event_id:
                return with_resilience_incident_normalized_risk(incident)
    return {'detail': f'Unknown event_id: {event_id}', 'source': 'unavailable', 'degraded': True}


@app.post('/auth/signup', summary='Create a live-mode pilot user')
def auth_signup(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    enforce_auth_rate_limit(request, 'signup', payload.get('email'))
    return with_auth_schema_json(lambda: signup_user(payload, request))


@app.post('/auth/signin', summary='Sign in a live-mode pilot user')
def auth_signin(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    enforce_auth_rate_limit(request, 'signin', payload.get('email'))
    return with_auth_schema_json(lambda: signin_user(payload, request))


@app.post('/auth/mfa/complete-signin', summary='Complete MFA challenge for sign in')
def auth_mfa_complete_signin(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    enforce_auth_rate_limit(request, 'mfa_complete_signin')
    return with_auth_schema_json(lambda: mfa_complete_signin(payload, request))


@app.post('/auth/oidc/start', summary='Start a workspace OIDC authorization-code flow')
def auth_oidc_start(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    enforce_auth_rate_limit(request, 'oidc_start')
    return with_auth_schema_json(lambda: oidc_begin_signin(payload, request))


@app.post('/auth/oidc/callback', summary='Complete a workspace OIDC authorization-code flow')
def auth_oidc_callback(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    enforce_auth_rate_limit(request, 'oidc_callback')
    return with_auth_schema_json(lambda: oidc_complete_signin(payload, request))


@app.post('/auth/signout', summary='Sign out a live-mode pilot user')
def auth_signout(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: signout_user(request))


@app.post('/auth/signout-all', summary='Sign out all sessions for authenticated user')
def auth_signout_all(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: signout_all_sessions(request))


@app.get('/auth/sessions', summary='List active sessions for authenticated user')
def auth_sessions(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: list_active_sessions(request))


@app.post('/auth/sessions/revoke', summary='Revoke an individual session')
def auth_sessions_revoke(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    session_id = str(payload.get('session_id', '')).strip()
    if not session_id:
        raise HTTPException(status_code=400, detail='session_id is required')
    return with_auth_schema_json(lambda: revoke_session(request, session_id))


@app.get('/auth/me', summary='Current authenticated live-mode user')
def auth_me(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: {'mode': pilot_mode(), 'user': authenticate_request(request)})


@app.post('/auth/resend-verification', summary='Resend email verification link')
def auth_resend_verification(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    enforce_auth_rate_limit(request, 'resend_verification', payload.get('email'))
    return with_auth_schema_json(lambda: request_email_verification(payload, request))


@app.post('/auth/verify-email', summary='Verify account email using one-time token')
def auth_verify_email(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: verify_email_token(payload, request))


@app.post('/auth/forgot-password', summary='Request password reset token')
def auth_forgot_password(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    enforce_auth_rate_limit(request, 'forgot_password', payload.get('email'))
    return with_auth_schema_json(lambda: request_password_reset(payload, request))


@app.post('/auth/reset-password', summary='Reset password using one-time token')
def auth_reset_password(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    enforce_auth_rate_limit(request, 'reset_password')
    return with_auth_schema_json(lambda: reset_password(payload, request))


@app.post('/auth/mfa/enroll', summary='Begin TOTP MFA enrollment')
def auth_mfa_enroll(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: mfa_begin_enrollment(request))


@app.post('/auth/mfa/confirm', summary='Confirm TOTP MFA enrollment')
def auth_mfa_confirm(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    enforce_auth_rate_limit(request, 'mfa_confirm')
    return with_auth_schema_json(lambda: mfa_confirm_enrollment(payload, request))


@app.post('/auth/mfa/disable', summary='Disable TOTP MFA')
def auth_mfa_disable(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    enforce_auth_rate_limit(request, 'mfa_disable')
    return with_auth_schema_json(lambda: mfa_disable(payload, request))


@app.post('/auth/mfa/recovery-codes/regenerate', summary='Regenerate one-time MFA recovery codes')
def auth_mfa_recovery_codes_regenerate(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    enforce_auth_rate_limit(request, 'mfa_recovery_codes')
    return with_auth_schema_json(lambda: mfa_regenerate_recovery_codes(payload, request))


@app.post('/auth/reauthenticate', summary='Reauthenticate the current session for sensitive actions')
def auth_reauthenticate(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    enforce_auth_rate_limit(request, 'reauthenticate')
    return with_auth_schema_json(lambda: reauthenticate_user(payload, request))


@app.post('/ops/jobs/run', summary='Run queued background jobs (operator)')
def ops_run_jobs(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    enforce_auth_rate_limit(request, 'ops_jobs_run')
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        require_ops_rbac_guard(connection, request)
    worker_id = str(payload.get('worker_id', 'api-sync-worker')).strip() or 'api-sync-worker'
    limit = int(payload.get('limit', 20))
    return with_auth_schema_json(lambda: run_background_jobs(worker_id=worker_id, limit=max(1, min(limit, 100))))


@app.post('/ops/monitoring/run', summary='Run monitoring worker cycle (operator)')
def ops_run_monitoring(payload: dict[str, Any], request: Request) -> Any:
    enforce_auth_rate_limit(request, 'ops_monitoring_run')
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        require_ops_rbac_guard(connection, request)
    worker_name = str(payload.get('worker_name', 'monitoring-worker')).strip() or 'monitoring-worker'
    limit = int(payload.get('limit', 50))
    try:
        return with_auth_schema_json(lambda: run_monitoring_cycle(worker_name=worker_name, limit=max(1, min(limit, 200)), trigger_type='system'))
    except HTTPException as exc:
        if isinstance(exc.detail, dict):
            return JSONResponse(exc.detail, status_code=exc.status_code)
        raise
    except Exception as exc:
        logger.exception(
            'ops_monitoring_run_unexpected_error method=%s path=%s worker_name=%s limit=%s error_type=%s',
            request.method,
            request.url.path,
            worker_name,
            limit,
            type(exc).__name__,
        )
        error_payload: dict[str, Any] = {
            'code': 'monitoring_run_failed',
            'detail': 'Unexpected backend error during monitoring run.',
            'stage': 'run_monitoring_cycle',
        }
        if not _is_production_like_runtime():
            error_payload['debug_error_type'] = type(exc).__name__
            error_payload['debug_error_message'] = str(exc)
        return JSONResponse(error_payload, status_code=500)


@app.get('/ops/monitoring/health', summary='Monitoring worker health snapshot')
def ops_monitoring_health() -> dict[str, Any]:
    def _snapshot() -> dict[str, Any]:
        monitoring = get_monitoring_health()
        delivery = alert_delivery_health()
        enterprise_ready = bool(monitoring.get('enterprise_ready', True) and delivery.get('ready'))
        return {
            **monitoring,
            'alert_delivery': delivery,
            'enterprise_ready': enterprise_ready,
            'status': monitoring.get('status', 'healthy') if enterprise_ready else 'not_ready',
        }
    return with_auth_schema_json(_snapshot)


@app.get('/ops/production-claim-validator', summary='Strategic Infrastructure Guard production claim validator')
def ops_production_claim_validator() -> dict[str, Any]:
    return with_auth_schema_json(production_claim_validator)


@app.get('/ops/monitoring/runtime-status', summary='Monitoring runtime status for admin/settings surfaces')
def ops_monitoring_runtime_status(request: Request) -> dict[str, Any]:
    try:
        payload = with_auth_schema_json(lambda: monitoring_runtime_status(request))
        emit_legacy_fields = str(os.getenv('MONITORING_RUNTIME_LEGACY_FIELDS', '')).strip().lower() in {'1', 'true', 'yes', 'on'}
        provider_health = payload.get('provider_health')
        target_coverage = payload.get('target_coverage')
        provider_health_status = payload.get('provider_health_status')
        target_coverage_status = payload.get('target_coverage_status')
        if isinstance(provider_health, (str, int, float, bool)) and provider_health_status is None:
            provider_health_status = str(provider_health)
            provider_health = []
        if isinstance(target_coverage, (str, int, float, bool)) and target_coverage_status is None:
            target_coverage_status = str(target_coverage)
            target_coverage = []
        # Upgrade provider_health_status to 'healthy' when live telemetry proves provider is active,
        # so empty provider_health_records does not degrade status when canonical facts are live.
        if provider_health_status in {'degraded', None}:
            _ph_rpt = int(payload.get('reporting_systems') or 0)
            _ph_ev = str(payload.get('evidence_source') or '').lower()
            _ph_fresh = str(payload.get('freshness_status') or '').lower()
            if _ph_rpt > 0 and _ph_ev in {'live', 'live_provider'} and _ph_fresh == 'fresh':
                provider_health_status = 'healthy'
        # Derive target_coverage_status from target_coverage entries, or from canonical runtime facts
        # when target_coverage_records is empty but reporting systems are confirmed live.
        if target_coverage_status is None:
            _tc_list = target_coverage if isinstance(target_coverage, list) else []
            if _tc_list:
                _all_reporting = all(
                    isinstance(e, dict) and (
                        str(e.get('provider_status') or '').lower() in {'live', 'reporting', 'active'}
                        or str(e.get('coverage_status') or '').lower() == 'reporting'
                        or str((e.get('metadata') or {}).get('provider_status') or '').lower() in {'live', 'reporting', 'active'}
                    )
                    for e in _tc_list
                )
                target_coverage_status = 'reporting' if _all_reporting else 'partial'
            else:
                _tc_rpt = int(payload.get('reporting_systems') or 0)
                _tc_cfg = int(payload.get('configured_systems') or payload.get('enabled_systems') or 0)
                _tc_ev = str(payload.get('evidence_source') or '').lower()
                _tc_fresh = str(payload.get('freshness_status') or '').lower()
                if _tc_rpt > 0 and _tc_cfg > 0 and _tc_rpt >= _tc_cfg and _tc_ev in {'live', 'live_provider'} and _tc_fresh == 'fresh':
                    target_coverage_status = 'reporting'
        summary = payload.get('workspace_monitoring_summary') if isinstance(payload.get('workspace_monitoring_summary'), dict) else {}
        summary_payload = summary if isinstance(summary, dict) else {}

        canonical_runtime_summary = {
            'workspace': dict(summary_payload.get('workspace') or {
                'id': payload.get('workspace_id'),
                'name': payload.get('workspace_name'),
                'configured': bool(summary_payload.get('workspace_configured', payload.get('workspace_configured'))),
            }),
            'statuses': dict(summary_payload.get('statuses') or {
                'runtime': str(summary_payload.get('runtime_status') or payload.get('runtime_status') or 'offline'),
                'monitoring': str(summary_payload.get('monitoring_status') or payload.get('monitoring_status') or 'offline'),
                'freshness': str(summary_payload.get('freshness_status') or payload.get('freshness_status') or 'unavailable'),
                'confidence': str(summary_payload.get('confidence_status') or payload.get('confidence_status') or 'unavailable'),
            }),
            'counts': dict(summary_payload.get('counts') or {
                'protected_assets': int(summary_payload.get('protected_assets') or payload.get('protected_assets') or 0),
                'monitoring_targets': int(summary_payload.get('monitoring_targets') or payload.get('monitoring_targets') or payload.get('raw_enabled_targets') or payload.get('monitorable_enabled_targets') or 0),
                'monitored_systems': int(summary_payload.get('monitored_systems') or payload.get('monitored_systems') or payload.get('configured_systems') or 0),
                'reporting_systems': int(summary_payload.get('reporting_systems') or payload.get('reporting_systems') or 0),
                'active_alerts': int(summary_payload.get('active_alerts') or payload.get('open_alerts') or 0),
                'open_incidents': int(summary_payload.get('active_incidents_count') or payload.get('raw_open_incidents') or payload.get('open_incidents') or payload.get('active_incidents') or 0),
            }),
            'timestamps': dict(summary_payload.get('timestamps') or {}),
            'evidence_source': str(summary_payload.get('evidence_source') or payload.get('evidence_source') or 'none'),
            'reason_codes': list(summary_payload.get('reason_codes') or payload.get('reason_codes') or payload.get('continuity_reason_codes') or []),
            'contradiction_flags': list(summary_payload.get('contradiction_flags') or payload.get('contradiction_flags') or []),
            'next_required_action': str(summary_payload.get('next_required_action') or payload.get('next_required_action') or 'review_reason_codes'),
            'next_action': str(summary_payload.get('next_required_action') or payload.get('next_required_action') or 'review_reason_codes'),
            'current_step': str(summary_payload.get('current_step') or 'asset_created'),
            'workflow_steps': list(summary_payload.get('workflow_steps') or []),
            'workflow': dict(summary_payload.get('workflow') or {
                'steps': list(summary_payload.get('workflow_steps') or []),
                'current_step': str(summary_payload.get('current_step') or 'asset_created'),
                'next_required_action': str(summary_payload.get('next_required_action') or payload.get('next_required_action') or 'review_reason_codes'),
            }),
        }
        canonical_runtime_summary['contradiction_flags'] = list(summary_payload.get('contradiction_flags') or payload.get('contradiction_flags') or [])
        _backend_runtime = str(payload.get('runtime_status') or 'offline').lower()
        if canonical_runtime_summary['contradiction_flags'] and _backend_runtime != 'live':
            canonical_runtime_summary['statuses']['runtime'] = 'degraded'
            canonical_runtime_summary['statuses']['monitoring'] = 'offline'
            canonical_runtime_summary['next_required_action'] = 'resolve_runtime_contradictions'
            canonical_runtime_summary['next_action'] = 'resolve_runtime_contradictions'

        canonical_runtime = {
            'workspace_monitoring_runtime': canonical_runtime_summary,
            'workspace_id': payload.get('workspace_id'),
            'workspace_slug': payload.get('workspace_slug'),
            'workspace_name': payload.get('workspace_name'),
            'workspace_configured': bool(summary_payload.get('workspace_configured', payload.get('workspace_configured'))),
            'runtime_status': str(payload.get('runtime_status') or 'offline'),
            'monitoring_status': str(summary_payload.get('monitoring_status') or payload.get('monitoring_status') or 'offline'),
            'configured_systems': int(summary_payload.get('configured_systems') or payload.get('enabled_systems') or payload.get('configured_systems') or 0),
            'reporting_systems': int(summary_payload.get('reporting_systems') or payload.get('reporting_systems') or 0),
            'protected_assets': int(summary_payload.get('protected_assets') or payload.get('protected_assets') or 0),
            'monitored_systems': int(summary_payload.get('monitored_systems') or payload.get('monitored_systems') or payload.get('configured_systems') or 0),
            'monitoring_targets': int(summary_payload.get('monitoring_targets') or payload.get('monitoring_targets') or 0),
            'last_poll_at': payload.get('last_poll_at'),
            'last_heartbeat_at': payload.get('last_heartbeat_at'),
            'last_telemetry_at': payload.get('last_telemetry_at'),
            'last_detection_at': payload.get('last_detection_at'),
            'timestamps': dict(summary_payload.get('timestamps') or {}),
            'freshness_status': str(summary_payload.get('freshness_status') or payload.get('freshness_status') or 'unavailable'),
            'confidence_status': str(summary_payload.get('confidence_status') or payload.get('confidence_status') or 'unavailable'),
            'evidence_source': str(summary_payload.get('evidence_source') or payload.get('evidence_source') or 'none'),
            'status_reason': str(payload.get('status_reason') or 'unknown'),
            'reason_codes': list(summary_payload.get('reason_codes') or payload.get('reason_codes') or payload.get('continuity_reason_codes') or []),
            'next_required_action': str(summary_payload.get('next_required_action') or payload.get('next_required_action') or 'review_reason_codes'),
            'runtime_setup_chain': dict(summary.get('runtime_setup_chain') or {}),
            'contradiction_flags': list(summary_payload.get('contradiction_flags') or payload.get('contradiction_flags') or []),
            'workflow_steps': list(summary_payload.get('workflow_steps') or []),
            'current_step': str(summary_payload.get('current_step') or 'asset_created'),
            'active_alerts': int(summary_payload.get('active_alerts') or payload.get('open_alerts') or 0),
            'open_incidents': int(summary_payload.get('active_incidents_count') or payload.get('raw_open_incidents') or payload.get('open_incidents') or payload.get('active_incidents') or 0),
            'summary_generated_at': payload.get('summary_generated_at') or datetime.now(timezone.utc).isoformat(),
            'provider_health': provider_health if provider_health is not None else [],
            'target_coverage': target_coverage if target_coverage is not None else [],
            'provider_health_records': list(payload.get('provider_health_records') or []),
            'target_coverage_records': list(payload.get('target_coverage_records') or target_coverage or []),
            'provider_health_status': str(provider_health_status or 'unknown'),
            'target_coverage_status': str(target_coverage_status or 'unknown'),
        }
        if _is_production_like_runtime():
            emit_legacy_fields = False
        _frc = dict(summary_payload.get('field_reason_codes') or payload.get('field_reason_codes') or {})
        if summary_payload:
            for _frc_key in ('protected_assets', 'configured_systems', 'reporting_systems', 'last_poll_at', 'last_heartbeat_at', 'last_telemetry_at'):
                _frc.setdefault(_frc_key, [])
        canonical_runtime_summary['field_reason_codes'] = _frc
        canonical_runtime_summary['count_reason_codes'] = dict(
            payload.get('count_reason_codes') or summary_payload.get('count_reason_codes') or {}
        )
        canonical_runtime_summary['continuity_freshness_ages_seconds'] = dict(
            summary_payload.get('continuity_freshness_ages_seconds') or {}
        )
        canonical_runtime_summary['continuity_configured_thresholds_seconds'] = dict(
            summary_payload.get('continuity_configured_thresholds_seconds') or {}
        )
        canonical_runtime_summary['continuity_breach_reasons'] = list(
            summary_payload.get('continuity_breach_reasons') or []
        )
        canonical_runtime['workspace_monitoring_summary'] = dict(canonical_runtime_summary)
        canonical_runtime['configuration_reason'] = payload.get('configuration_reason')
        canonical_runtime['configuration_reason_codes'] = list(payload.get('configuration_reason_codes') or [])
        canonical_runtime['count_reason_codes'] = dict(payload.get('count_reason_codes') or {})
        canonical_runtime['field_reason_codes'] = dict(payload.get('field_reason_codes') or {})
        canonical_runtime['runtime_status_summary'] = (
            payload.get('runtime_status_summary')
            or summary_payload.get('runtime_status')
            or payload.get('runtime_status')
            or 'offline'
        )
        canonical_runtime['configuration_diagnostics'] = dict(payload.get('configuration_diagnostics') or {})
        canonical_runtime['enabled_systems'] = int(
            payload.get('enabled_systems')
            or payload.get('enabled_system_count')
            or payload.get('configured_systems')
            or summary_payload.get('configured_systems')
            or 0
        )
        canonical_runtime['runtime_error_code'] = payload.get('runtime_error_code')
        canonical_runtime['runtime_degraded_reason'] = payload.get('runtime_degraded_reason')
        if payload.get('error') is not None:
            canonical_runtime['error'] = payload.get('error')
        canonical_runtime['status'] = payload.get('status') or str(
            summary_payload.get('monitoring_status') or payload.get('monitoring_status') or 'offline'
        ).capitalize()
        canonical_runtime['systems_with_recent_heartbeat'] = payload.get('systems_with_recent_heartbeat')
        # Separated worker status (stable polling / realtime websocket / provider realtime)
        # so the UI can distinguish a paused or rate-limited realtime worker from a dead
        # monitoring source. Passed through verbatim from the canonical runtime builder.
        if payload.get('worker_status') is not None:
            canonical_runtime['worker_status'] = payload.get('worker_status')
        canonical_runtime['realtime_enabled'] = bool(payload.get('realtime_enabled'))
        # Stable-polling debug fields: surface the timestamps/threshold/status that drove
        # the stable-polling verdict so the top banner, worker-status card, limitation text,
        # and runtime summary can be reconciled against one canonical set of facts.
        for _stable_debug_key in (
            'last_stable_poll_at', 'last_rpc_polling_heartbeat_at', 'stable_poll_age_seconds',
            'stable_poll_stale_threshold_seconds', 'stable_polling_status',
        ):
            canonical_runtime[_stable_debug_key] = payload.get(_stable_debug_key)
        for _counter_key in (
            'raw_enabled_targets', 'monitorable_enabled_targets', 'valid_asset_linked_targets',
            'enabled_monitored_systems', 'valid_target_system_links',
        ):
            canonical_runtime[_counter_key] = int(payload.get(_counter_key) or 0)
        _check_name_aliases = {
            'evidence_chain_completeness': 'linked_fresh_evidence',
            'linked_fresh_evidence_chain': 'linked_fresh_evidence',
            'linked_evidence_freshness': 'linked_fresh_evidence',
        }
        _erp = payload.get('enterprise_ready_pass')
        if _erp is None:
            _erp = summary.get('enterprise_ready_pass')
        canonical_runtime['enterprise_ready_pass'] = bool(_erp) if _erp is not None else False
        _failed_checks = payload.get('failed_checks')
        if not isinstance(_failed_checks, list):
            _failed_checks = summary.get('failed_checks')
        canonical_runtime['failed_checks'] = [
            _check_name_aliases.get(str(_c).strip(), str(_c).strip())
            for _c in (_failed_checks or [])
            if str(_c).strip()
        ]
        _check_results = payload.get('check_results')
        if not isinstance(_check_results, list):
            _check_results = summary.get('check_results')
        _norm_checks: list[dict] = []
        for _chk in list(_check_results or []):
            if not isinstance(_chk, dict):
                continue
            _nc = dict(_chk)
            _nn = str(_nc.get('name') or '').strip()
            if _nn:
                _nc['name'] = _check_name_aliases.get(_nn, _nn)
            _norm_checks.append(_nc)
        canonical_runtime['check_results'] = _norm_checks
        _remediation_links = payload.get('remediation_links')
        if not isinstance(_remediation_links, dict):
            _remediation_links = summary.get('remediation_links')
        canonical_runtime['remediation_links'] = {
            _check_name_aliases.get(str(_rn).strip(), str(_rn).strip()): str(_ru)
            for _rn, _ru in dict(_remediation_links or {}).items()
            if str(_rn).strip() and str(_ru).strip()
        }
        _background_loop_health = get_background_loop_health()
        canonical_runtime['background_loop_health'] = dict(_background_loop_health)
        canonical_runtime['loop_running'] = bool(_background_loop_health.get('loop_running'))
        canonical_runtime['last_successful_cycle'] = _background_loop_health.get('last_successful_cycle')
        canonical_runtime['consecutive_failures'] = int(_background_loop_health.get('consecutive_failures') or 0)
        canonical_runtime['next_retry_at'] = _background_loop_health.get('next_retry_at')
        canonical_runtime['backoff_seconds'] = _background_loop_health.get('backoff_seconds')
        if payload.get('mode') is not None:
            canonical_runtime['mode'] = payload.get('mode')
        if payload.get('sales_claims_allowed') is not None:
            canonical_runtime['sales_claims_allowed'] = payload.get('sales_claims_allowed')
        # Preserve continuity contract fields from the raw payload so they are available in both legacy and non-legacy paths.
        _continuity_passthrough_keys = (
            'continuity_slo', 'continuity_slo_pass', 'continuity_contract',
            'continuity_status', 'continuity_reason_codes', 'continuity_signals',
            'continuity_failed_checks', 'continuity_freshness_ages_seconds',
            'continuity_configured_thresholds_seconds', 'continuity_breach_reasons',
            'heartbeat_age_seconds', 'worker_heartbeat_age_seconds',
            'telemetry_age_seconds', 'event_ingestion_age_seconds',
            'detection_age_seconds', 'detection_pipeline_age_seconds', 'detection_eval_age_seconds',
            'heartbeat_threshold_seconds', 'telemetry_threshold_seconds',
            'detection_threshold_seconds', 'event_ingestion_threshold_seconds',
            'thresholds_seconds', 'required_thresholds_seconds', 'continuity_thresholds_seconds',
            'runtime_degraded_reason_codes', 'runtime_status_reason_codes',
        )
        _continuity_slo_from_payload = payload.get('continuity_slo') if isinstance(payload.get('continuity_slo'), dict) else {}
        for _ck in _continuity_passthrough_keys:
            _cv = payload.get(_ck)
            if _cv is None:
                _cv = summary_payload.get(_ck)
            if _cv is None:
                _cv = _continuity_slo_from_payload.get(_ck)
            if _cv is not None:
                canonical_runtime[_ck] = _cv
        if not emit_legacy_fields:
            if _is_production_like_runtime():
                return {k: canonical_runtime[k] for k in _RUNTIME_STATUS_REQUIRED_TOP_LEVEL_KEYS if k in canonical_runtime}
            return canonical_runtime
        payload.update(canonical_runtime)
        payload['canonical_monitoring_runtime'] = dict(canonical_runtime)
        background_loop_health = get_background_loop_health()
        payload['background_loop_health'] = dict(background_loop_health)
        payload['loop_running'] = bool(background_loop_health.get('loop_running'))
        payload['last_successful_cycle'] = background_loop_health.get('last_successful_cycle')
        payload['consecutive_failures'] = int(background_loop_health.get('consecutive_failures') or 0)
        payload['next_retry_at'] = background_loop_health.get('next_retry_at')
        payload['backoff_seconds'] = background_loop_health.get('backoff_seconds')
        check_name_aliases = {
            'evidence_chain_completeness': 'linked_fresh_evidence',
            'linked_fresh_evidence_chain': 'linked_fresh_evidence',
            'linked_evidence_freshness': 'linked_fresh_evidence',
        }
        payload['enterprise_ready_pass'] = bool(
            payload.get('enterprise_ready_pass')
            if payload.get('enterprise_ready_pass') is not None
            else summary.get('enterprise_ready_pass')
        )
        failed_checks = payload.get('failed_checks')
        if not isinstance(failed_checks, list):
            failed_checks = summary.get('failed_checks')
        payload['failed_checks'] = [
            check_name_aliases.get(str(check).strip(), str(check).strip())
            for check in (failed_checks or [])
            if str(check).strip()
        ]
        check_results = payload.get('check_results')
        if not isinstance(check_results, list):
            check_results = summary.get('check_results')
        normalized_check_results = []
        for check in list(check_results or []):
            if not isinstance(check, dict):
                continue
            normalized = dict(check)
            normalized_name = str(normalized.get('name') or '').strip()
            if normalized_name:
                normalized['name'] = check_name_aliases.get(normalized_name, normalized_name)
            normalized_check_results.append(normalized)
        payload['check_results'] = normalized_check_results
        remediation_links = payload.get('remediation_links')
        if not isinstance(remediation_links, dict):
            remediation_links = summary.get('remediation_links')
        payload['remediation_links'] = {
            check_name_aliases.get(str(name).strip(), str(name).strip()): str(url)
            for name, url in dict(remediation_links or {}).items()
            if str(name).strip() and str(url).strip()
        }
        continuity_slo = payload.get('continuity_slo') if isinstance(payload.get('continuity_slo'), dict) else {}
        if not continuity_slo:
            thresholds = dict(payload.get('thresholds_seconds') or summary.get('thresholds_seconds') or {})
            continuity_slo = {
                'pass': bool(payload.get('continuity_slo_pass', summary.get('continuity_slo_pass')) is True),
                'heartbeat_age_seconds': payload.get('heartbeat_age_seconds', summary.get('heartbeat_age_seconds')),
                'worker_heartbeat_age_seconds': payload.get('worker_heartbeat_age_seconds', summary.get('worker_heartbeat_age_seconds', summary.get('heartbeat_age_seconds'))),
                'telemetry_age_seconds': payload.get('telemetry_age_seconds', summary.get('telemetry_age_seconds')),
                'event_ingestion_age_seconds': payload.get('event_ingestion_age_seconds', summary.get('event_ingestion_age_seconds')),
                'detection_age_seconds': payload.get('detection_age_seconds', summary.get('detection_age_seconds', summary.get('detection_eval_age_seconds'))),
                'detection_pipeline_age_seconds': payload.get('detection_pipeline_age_seconds', summary.get('detection_pipeline_age_seconds', summary.get('detection_eval_age_seconds'))),
                'detection_eval_age_seconds': payload.get('detection_eval_age_seconds', summary.get('detection_eval_age_seconds')),
                'heartbeat_threshold_seconds': payload.get('heartbeat_threshold_seconds', summary.get('heartbeat_threshold_seconds', thresholds.get('heartbeat'))),
                'telemetry_threshold_seconds': payload.get('telemetry_threshold_seconds', summary.get('telemetry_threshold_seconds', thresholds.get('telemetry', thresholds.get('event_ingestion')))),
                'event_ingestion_threshold_seconds': payload.get('event_ingestion_threshold_seconds', summary.get('event_ingestion_threshold_seconds', thresholds.get('event_ingestion'))),
                'detection_threshold_seconds': payload.get('detection_threshold_seconds', summary.get('detection_threshold_seconds', thresholds.get('detection_eval'))),
                'thresholds_seconds': thresholds,
                'required_thresholds_seconds': dict(payload.get('required_thresholds_seconds') or summary.get('required_thresholds_seconds') or {}),
                'continuity_thresholds_seconds': dict(payload.get('continuity_thresholds_seconds') or summary.get('continuity_thresholds_seconds') or payload.get('required_thresholds_seconds') or summary.get('required_thresholds_seconds') or thresholds),
                'reason_codes': list(payload.get('continuity_reason_codes') or summary.get('continuity_reason_codes') or []),
                'checks': dict((payload.get('continuity_contract') or summary.get('continuity_contract') or {}).get('checks') or {}),
                'failed_checks': list(payload.get('continuity_failed_checks') or summary.get('continuity_failed_checks') or payload.get('continuity_reason_codes') or summary.get('continuity_reason_codes') or []),
            }
        payload['continuity_slo'] = continuity_slo
        payload['continuity_contract'] = {
            'pass': bool(payload.get('continuity_slo_pass', summary.get('continuity_slo_pass', continuity_slo.get('pass'))) is True),
            'checks': dict((payload.get('continuity_contract') or continuity_slo).get('checks') or {}),
        }
        payload['continuity_slo_pass'] = bool(
            payload.get('continuity_slo_pass', summary.get('continuity_slo_pass', continuity_slo.get('pass'))) is True
        )
        payload['heartbeat_age_seconds'] = payload.get('heartbeat_age_seconds', continuity_slo.get('heartbeat_age_seconds'))
        payload['worker_heartbeat_age_seconds'] = payload.get('worker_heartbeat_age_seconds', payload.get('heartbeat_age_seconds'))
        payload['telemetry_age_seconds'] = payload.get('telemetry_age_seconds', continuity_slo.get('telemetry_age_seconds'))
        payload['event_ingestion_age_seconds'] = payload.get('event_ingestion_age_seconds', continuity_slo.get('event_ingestion_age_seconds'))
        payload['detection_age_seconds'] = payload.get('detection_age_seconds', continuity_slo.get('detection_age_seconds'))
        payload['detection_pipeline_age_seconds'] = payload.get('detection_pipeline_age_seconds', continuity_slo.get('detection_pipeline_age_seconds'))
        payload['detection_eval_age_seconds'] = payload.get('detection_eval_age_seconds', continuity_slo.get('detection_eval_age_seconds'))
        payload['heartbeat_threshold_seconds'] = payload.get('heartbeat_threshold_seconds', continuity_slo.get('heartbeat_threshold_seconds'))
        payload['telemetry_threshold_seconds'] = payload.get('telemetry_threshold_seconds', continuity_slo.get('telemetry_threshold_seconds'))
        payload['event_ingestion_threshold_seconds'] = payload.get('event_ingestion_threshold_seconds', continuity_slo.get('event_ingestion_threshold_seconds'))
        payload['detection_threshold_seconds'] = payload.get('detection_threshold_seconds', continuity_slo.get('detection_threshold_seconds'))
        payload['thresholds_seconds'] = payload.get('thresholds_seconds', continuity_slo.get('thresholds_seconds'))
        payload['required_thresholds_seconds'] = payload.get('required_thresholds_seconds', continuity_slo.get('required_thresholds_seconds'))
        payload['continuity_thresholds_seconds'] = payload.get('continuity_thresholds_seconds', continuity_slo.get('continuity_thresholds_seconds'))
        payload['continuity_reason_codes'] = list(payload.get('continuity_reason_codes') or continuity_slo.get('reason_codes') or [])
        payload['continuity_slo']['reason_codes'] = list(payload['continuity_reason_codes'])
        payload['continuity_slo']['failed_checks'] = list(
            payload.get('continuity_failed_checks')
            or continuity_slo.get('failed_checks')
            or summary.get('continuity_failed_checks')
            or payload.get('continuity_reason_codes')
            or []
        )
        payload['continuity_freshness_ages_seconds'] = dict(
            payload.get('continuity_freshness_ages_seconds')
            or continuity_slo.get('freshness_ages_seconds')
            or summary.get('continuity_freshness_ages_seconds')
            or {}
        )
        payload['continuity_configured_thresholds_seconds'] = dict(
            payload.get('continuity_configured_thresholds_seconds')
            or continuity_slo.get('configured_thresholds_seconds')
            or summary.get('continuity_configured_thresholds_seconds')
            or payload.get('continuity_thresholds_seconds')
            or {}
        )
        payload['continuity_breach_reasons'] = list(
            payload.get('continuity_breach_reasons')
            or continuity_slo.get('breach_reasons')
            or summary.get('continuity_breach_reasons')
            or []
        )
        payload['continuity_failed_checks'] = list(
            payload.get('continuity_failed_checks')
            or summary.get('continuity_failed_checks')
            or payload.get('continuity_reason_codes')
            or []
        )
        payload['continuity'] = {
            'status': payload.get('continuity_status'),
            'slo': payload.get('continuity_slo'),
            'contract': payload.get('continuity_contract'),
            'signals': dict(payload.get('continuity_signals') or {}),
            'reason_codes': list(payload.get('continuity_reason_codes') or []),
            'freshness_ages_seconds': dict(payload.get('continuity_freshness_ages_seconds') or {}),
            'configured_thresholds_seconds': dict(payload.get('continuity_configured_thresholds_seconds') or {}),
            'breach_reasons': list(payload.get('continuity_breach_reasons') or []),
            'failed_checks': list(payload.get('continuity_failed_checks') or []),
        }
        payload['runtime_degraded_reason_codes'] = list(
            payload.get('runtime_degraded_reason_codes')
            or summary.get('runtime_degraded_reason_codes')
            or []
        )
        payload['runtime_status_reason_codes'] = list(
            payload.get('runtime_status_reason_codes')
            or summary.get('runtime_status_reason_codes')
            or []
        )
        compatibility_summary = payload.get('workspace_monitoring_summary')
        summary = compatibility_summary if isinstance(compatibility_summary, dict) else {}
        if payload.get('enterprise_ready_pass') is None:
            payload['enterprise_ready_pass'] = summary.get('enterprise_ready_pass')
        if payload.get('loop_running') is None:
            payload['loop_running'] = summary.get('loop_running')
        if payload.get('count_reason_codes') is None:
            compatibility_reason_codes = (
                payload.get('reason_codes')
                or summary.get('reason_codes')
                or payload.get('continuity_reason_codes')
                or summary.get('continuity_reason_codes')
                or []
            )
            payload['count_reason_codes'] = (
                len(compatibility_reason_codes) if isinstance(compatibility_reason_codes, list) else 0
            )
        if payload.get('workspace_slug') is None:
            payload['workspace_slug'] = summary.get('workspace_slug')
        if payload.get('enabled_systems') is None:
            payload['enabled_systems'] = (
                summary.get('enabled_systems')
                if summary.get('enabled_systems') is not None
                else summary.get('monitored_systems')
            )
        if payload.get('continuity_freshness_ages_seconds') is None:
            payload['continuity_freshness_ages_seconds'] = dict(
                summary.get('continuity_freshness_ages_seconds')
                or summary.get('freshness_ages_seconds')
                or {}
            )
        return payload
    except Exception as exc:
        logger.exception('ops_monitoring_runtime_status_route_failed')
        background_loop_health = get_background_loop_health()
        fallback_summary = build_workspace_monitoring_summary_fallback(
            status_reason='runtime_status_route_error',
            workspace_configured=False,
            runtime_status='offline',
            monitoring_status='offline',
            telemetry_freshness='unavailable',
            confidence='unavailable',
        )
        return {
            'monitoring_status': 'offline',
            'status': 'Offline',
            'workspace_configured': False,
            'status_reason': 'runtime_status_route_error',
            'error': {
                'code': 'runtime_status_route_failed',
                'type': type(exc).__name__,
                'message': 'Monitoring runtime endpoint degraded due to unexpected route error.',
            },
            'background_loop_health': dict(background_loop_health),
            'loop_running': bool(background_loop_health.get('loop_running')),
            'last_successful_cycle': background_loop_health.get('last_successful_cycle'),
            'consecutive_failures': int(background_loop_health.get('consecutive_failures') or 0),
            'next_retry_at': background_loop_health.get('next_retry_at'),
            'backoff_seconds': background_loop_health.get('backoff_seconds'),
            'workspace_monitoring_summary': fallback_summary,
            'workspace_monitoring_runtime': dict(fallback_summary.get('summary_v2') or {}),
            'continuity_status': fallback_summary.get('continuity_status'),
            'continuity_reason_codes': list(fallback_summary.get('continuity_reason_codes') or []),
            'continuity_signals': dict(fallback_summary.get('continuity_signals') or {}),
            'continuity_slo_pass': fallback_summary.get('continuity_slo_pass'),
            'continuity_freshness_ages_seconds': dict(fallback_summary.get('continuity_freshness_ages_seconds') or {}),
            'continuity_configured_thresholds_seconds': dict(
                fallback_summary.get('continuity_configured_thresholds_seconds')
                or fallback_summary.get('continuity_thresholds_seconds')
                or fallback_summary.get('required_thresholds_seconds')
                or {}
            ),
            'continuity_breach_reasons': list(fallback_summary.get('continuity_breach_reasons') or []),
            'heartbeat_age_seconds': fallback_summary.get('heartbeat_age_seconds'),
            'worker_heartbeat_age_seconds': fallback_summary.get('heartbeat_age_seconds'),
            'telemetry_age_seconds': fallback_summary.get('telemetry_age_seconds'),
            'event_ingestion_age_seconds': fallback_summary.get('event_ingestion_age_seconds'),
            'detection_age_seconds': fallback_summary.get('detection_age_seconds', fallback_summary.get('detection_eval_age_seconds')),
            'detection_pipeline_age_seconds': fallback_summary.get('detection_pipeline_age_seconds', fallback_summary.get('detection_eval_age_seconds')),
            'detection_eval_age_seconds': fallback_summary.get('detection_eval_age_seconds'),
            'continuity_failed_checks': list(fallback_summary.get('continuity_reason_codes') or []),
            'heartbeat_threshold_seconds': fallback_summary.get('heartbeat_threshold_seconds'),
            'telemetry_threshold_seconds': fallback_summary.get('telemetry_threshold_seconds'),
            'event_ingestion_threshold_seconds': fallback_summary.get('event_ingestion_threshold_seconds'),
            'detection_threshold_seconds': fallback_summary.get('detection_threshold_seconds'),
            'thresholds_seconds': fallback_summary.get('thresholds_seconds'),
            'continuity_thresholds_seconds': fallback_summary.get('continuity_thresholds_seconds', fallback_summary.get('required_thresholds_seconds')),
            'continuity_slo': {
                'pass': bool(fallback_summary.get('continuity_slo_pass') is True),
                'heartbeat_age_seconds': fallback_summary.get('heartbeat_age_seconds'),
                'telemetry_age_seconds': fallback_summary.get('telemetry_age_seconds'),
                'event_ingestion_age_seconds': fallback_summary.get('event_ingestion_age_seconds'),
                'detection_age_seconds': fallback_summary.get('detection_age_seconds', fallback_summary.get('detection_eval_age_seconds')),
                'detection_pipeline_age_seconds': fallback_summary.get('detection_pipeline_age_seconds', fallback_summary.get('detection_eval_age_seconds')),
                'detection_eval_age_seconds': fallback_summary.get('detection_eval_age_seconds'),
                'heartbeat_threshold_seconds': fallback_summary.get('heartbeat_threshold_seconds'),
                'telemetry_threshold_seconds': fallback_summary.get('telemetry_threshold_seconds'),
                'event_ingestion_threshold_seconds': fallback_summary.get('event_ingestion_threshold_seconds'),
                'detection_threshold_seconds': fallback_summary.get('detection_threshold_seconds'),
                'thresholds_seconds': dict(fallback_summary.get('thresholds_seconds') or {}),
                'required_thresholds_seconds': dict(fallback_summary.get('required_thresholds_seconds') or {}),
                'continuity_thresholds_seconds': dict(fallback_summary.get('continuity_thresholds_seconds') or fallback_summary.get('required_thresholds_seconds') or {}),
                'reason_codes': list(fallback_summary.get('continuity_reason_codes') or []),
                'checks': dict((fallback_summary.get('continuity_contract') or {}).get('checks') or {}),
                'freshness_ages_seconds': dict(fallback_summary.get('continuity_freshness_ages_seconds') or {}),
                'configured_thresholds_seconds': dict(
                    fallback_summary.get('continuity_configured_thresholds_seconds')
                    or fallback_summary.get('continuity_thresholds_seconds')
                    or fallback_summary.get('required_thresholds_seconds')
                    or {}
                ),
                'breach_reasons': list(fallback_summary.get('continuity_breach_reasons') or []),
            },
            'continuity_contract': {
                'pass': bool(fallback_summary.get('continuity_slo_pass') is True),
                'checks': dict((fallback_summary.get('continuity_contract') or {}).get('checks') or {}),
            },
            'continuity': {
                'status': fallback_summary.get('continuity_status'),
                'slo': {
                    'pass': bool(fallback_summary.get('continuity_slo_pass') is True),
                    'reason_codes': list(fallback_summary.get('continuity_reason_codes') or []),
                },
                'contract': {
                    'pass': bool(fallback_summary.get('continuity_slo_pass') is True),
                    'checks': dict((fallback_summary.get('continuity_contract') or {}).get('checks') or {}),
                },
                'signals': dict(fallback_summary.get('continuity_signals') or {}),
                'reason_codes': list(fallback_summary.get('continuity_reason_codes') or []),
                'freshness_ages_seconds': dict(fallback_summary.get('continuity_freshness_ages_seconds') or {}),
                'configured_thresholds_seconds': dict(
                    fallback_summary.get('continuity_configured_thresholds_seconds')
                    or fallback_summary.get('continuity_thresholds_seconds')
                    or fallback_summary.get('required_thresholds_seconds')
                    or {}
                ),
                'breach_reasons': list(fallback_summary.get('continuity_breach_reasons') or []),
            },
            'ingestion_freshness': fallback_summary.get('ingestion_freshness'),
            'detection_pipeline_freshness': fallback_summary.get('detection_pipeline_freshness'),
            'worker_heartbeat_freshness': fallback_summary.get('worker_heartbeat_freshness'),
            'event_throughput_window': fallback_summary.get('event_throughput_window'),
            'event_throughput_window_seconds': fallback_summary.get('event_throughput_window_seconds'),
            'enterprise_ready_pass': False,
            'failed_checks': [
                'continuity_slo_pass',
                'linked_fresh_evidence',
                'stable_monitored_systems',
                'live_action_capability_readiness',
            ],
            'check_results': [
                {'name': 'continuity_slo_pass', 'pass': False, 'remediation_url': '/threat#continuity-slo'},
                {'name': 'linked_fresh_evidence', 'pass': False, 'remediation_url': '/threat#telemetry-freshness'},
                {'name': 'stable_monitored_systems', 'pass': False, 'remediation_url': '/threat#monitored-system-state'},
                {'name': 'live_action_capability_readiness', 'pass': False, 'remediation_url': '/threat#response-actions'},
            ],
            'remediation_links': {
                'continuity_slo_pass': '/threat#continuity-slo',
                'linked_fresh_evidence': '/threat#telemetry-freshness',
                'stable_monitored_systems': '/threat#monitored-system-state',
                'live_action_capability_readiness': '/threat#response-actions',
            },
        }


@app.get('/ops/monitoring/runtime-debug', summary='Canonical monitoring runtime debug payload')
def ops_monitoring_runtime_debug(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: monitoring_runtime_debug_payload(request))


@app.get('/ops/monitoring/evidence', summary='Latest monitoring evidence stream')
def ops_monitoring_evidence(request: Request, limit: int = 50) -> dict[str, Any]:
    return with_auth_schema_json(lambda: list_monitoring_evidence(request, limit=limit))


@app.post('/ops/monitoring/proof-chain/ensure', summary='Ensure monitoring proof-chain records for current workspace')
def ops_monitoring_proof_chain_ensure(request: Request) -> dict[str, Any]:
    workspace_id = normalize_workspace_header_value(request.headers.get('x-workspace-id'))
    if not workspace_id:
        raise HTTPException(status_code=400, detail='x-workspace-id header is required')
    def _handler() -> dict[str, Any]:
        runtime_status = monitoring_runtime_status(request)
        if not runtime_allows_simulator_proof_chain(runtime_status):
            raise HTTPException(status_code=409, detail='Simulator-only action unavailable in live mode')
        payload = ensure_monitoring_proof_chain(workspace_id, request)
        chain_ids = {
            'monitoring_run_id': payload.get('monitoring_run_id'),
            'telemetry_event_id': payload.get('telemetry_event_id'),
            'detection_id': payload.get('detection_id'),
            'detection_evidence_id': payload.get('detection_evidence_id'),
            'alert_id': payload.get('alert_id'),
            'incident_id': payload.get('incident_id'),
            'response_action_id': payload.get('response_action_id'),
        }
        return {
            **payload,
            'chain_ids': chain_ids,
            'completion_status': payload.get('completion_status') or payload.get('status'),
            'reason': payload.get('reason'),
            'evidence_source': payload.get('evidence_source'),
        }

    return with_auth_schema_json(_handler)


@app.get('/ops/monitoring/investigation-timeline', summary='Linked monitoring proof-chain investigation timeline')
def ops_monitoring_investigation_timeline(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: get_monitoring_investigation_timeline(request))


@app.get('/ops/system-health', summary='SaaS-grade system health snapshot', description='Returns component-level infrastructure health, live chain monitoring status, events, providers, and reliability metrics.')
def ops_system_health(request: Request) -> dict[str, Any]:
    from services.api.app.system_health import build_system_health_snapshot
    return with_auth_schema_json(lambda: build_system_health_snapshot(request))


@app.get('/ops/monitoring/heartbeats', summary='Latest monitoring heartbeat rows')
def ops_monitoring_heartbeats(request: Request, limit: int = 50) -> dict[str, Any]:
    return with_auth_schema_json(lambda: list_monitoring_heartbeats(request, limit=limit))


@app.get('/ops/monitoring/worker-errors', summary='Recent monitoring worker and target errors')
def ops_monitoring_worker_errors(request: Request, limit: int = 50) -> dict[str, Any]:
    return with_auth_schema_json(lambda: list_monitoring_worker_errors(request, limit=limit))


@app.post('/ops/monitoring/targets/{target_id}/backfill', summary='Manually backfill a block range for a wallet monitoring target')
def ops_monitoring_target_backfill(target_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    """Scan a historic block range and persist any native transfers matching the monitored wallet.

    Body params: from_block (int), to_block (int).
    Requires x-workspace-id header.
    Dedupes by idempotency key — safe to call multiple times.
    """
    enforce_auth_rate_limit(request, 'ops_monitoring_backfill')
    from_block = int(payload.get('from_block') or 0)
    to_block = int(payload.get('to_block') or 0)
    if from_block <= 0 or to_block <= 0:
        raise HTTPException(status_code=400, detail='from_block and to_block must be positive integers')
    return with_auth_schema_json(lambda: backfill_target_block_range(request, target_id, from_block, to_block))


@app.post('/ops/monitoring/targets/{target_id}/backfill-alerts', summary='Create missing alerts for existing wallet_transfer_detected telemetry')
def ops_monitoring_target_backfill_alerts(target_id: str, request: Request) -> dict[str, Any]:
    """Scan existing live wallet_transfer_detected telemetry for the target and create
    any missing alerts via the smoke-rule and Strategic Infrastructure Guard rule.

    Idempotent — safe to call multiple times; dedup prevents duplicate alerts.
    Only processes evidence_source='live' rows; never creates alerts from simulator data.
    Requires x-workspace-id header.
    """
    enforce_auth_rate_limit(request, 'ops_monitoring_backfill_alerts')

    def _backfill_and_promote():
        result = backfill_missing_alerts_for_target(request, target_id=target_id)
        workspace_id = str(result.get('workspace_id') or '')
        if workspace_id:
            with pg_connection() as conn:
                promoted = promote_wallet_transfer_alerts(conn, workspace_id=workspace_id, target_id=target_id)
                result['promoted_count'] = promoted
        return result

    return with_auth_schema_json(_backfill_and_promote)


@app.post('/ops/monitoring/targets/{target_id}/import-tx', summary='Import a single transaction by hash for a wallet monitoring target')
def ops_monitoring_target_import_tx(target_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    """Import a known transaction by hash without requiring a full block-range backfill.

    Useful when a transaction block is older than the current worker scan window.
    Fetches the tx via eth_getTransactionByHash + eth_getTransactionReceipt, verifies
    the chain and monitored wallet match, then persists wallet_transfer_detected telemetry.
    Idempotent — safe to call multiple times for the same tx_hash.

    Body params: tx_hash (str, 66-char 0x-prefixed).
    Requires x-workspace-id header.
    """
    enforce_auth_rate_limit(request, 'ops_monitoring_import_tx')
    tx_hash = str(payload.get('tx_hash') or '').strip()
    if not tx_hash:
        raise HTTPException(status_code=400, detail='tx_hash is required')
    return with_auth_schema_json(lambda: ingest_tx_by_hash(request, target_id, tx_hash))


@app.post('/ops/monitoring/diagnose-tx', summary='Explain whether a tx involves any active monitored wallet (read-only)')
def ops_monitoring_diagnose_tx(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    """Read-only diagnostic for "I sent ETH but Decoda did not detect it".

    Given a tx_hash, reports chain_id, block_number, from, to, value and — for every
    active wallet target in the workspace — whether from/to matches the monitored
    wallet and whether a telemetry row was already persisted, with a per-target
    persist_reason. Never persists anything. Requires x-workspace-id header.

    Body params: tx_hash (str, 66-char 0x-prefixed).
    """
    enforce_auth_rate_limit(request, 'ops_monitoring_diagnose_tx')
    tx_hash = str(payload.get('tx_hash') or '').strip()
    if not tx_hash:
        raise HTTPException(status_code=400, detail='tx_hash is required')
    return with_auth_schema_json(lambda: diagnose_wallet_transaction(request, tx_hash))


@app.get('/ops/monitoring/targets/{target_id}/state', summary='Inspect dead-letter and skip state for a monitoring target')
def ops_monitoring_target_state(target_id: str, request: Request) -> dict[str, Any]:
    """Return dead-letter status, delivery attempts, last run status, and recent telemetry.

    Requires x-workspace-id header.  Read-only — does not modify any state.
    """
    enforce_auth_rate_limit(request, 'ops_monitoring_target_state')
    return with_auth_schema_json(lambda: inspect_target_dead_letter_state(request, target_id))


@app.post('/ops/monitoring/targets/{target_id}/recover-dead-letter', summary='Clear dead-letter state for a monitoring target')
def ops_monitoring_target_recover_dead_letter(target_id: str, request: Request) -> dict[str, Any]:
    """Reset monitoring_dead_lettered_at and delivery_attempts so the target is picked up next cycle.

    Idempotent — safe to call even if the target is not currently dead-lettered.
    Requires x-workspace-id header.
    """
    enforce_auth_rate_limit(request, 'ops_monitoring_recover_dead_letter')
    return with_auth_schema_json(lambda: recover_target_dead_letter(request, target_id))


@app.get('/ops/metrics/mttd', summary='Detection MTTD metrics')
def ops_metrics_mttd(request: Request, window_days: int = 7) -> dict[str, Any]:
    return with_auth_schema_json(lambda: get_mttd_metrics(request, window_days=window_days))


@app.get('/workspaces', summary='List workspaces for the authenticated user')
def workspaces(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: list_user_workspaces(request))


@app.post('/workspaces', summary='Create a workspace for the authenticated user')
def workspace_create(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: {'user': create_workspace_for_user(payload, request)})


@app.post('/auth/select-workspace', summary='Select the active workspace for the authenticated user')
def auth_select_workspace(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    workspace_id = str(payload.get('workspace_id', '')).strip()
    if not workspace_id:
        raise HTTPException(status_code=400, detail='workspace_id is required')
    return with_auth_schema_json(lambda: {'user': select_workspace_for_user(workspace_id, request)})


@app.get('/workspace/access-control', summary='Get explicit role permissions and workspace MFA policy')
def workspace_access_control(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: get_workspace_access_control(request))


@app.put('/workspace/auth-policy', summary='Update workspace MFA and reauthentication policy')
def workspace_auth_policy_update(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: update_workspace_auth_policy(payload, request))


@app.get('/workspace/sso/oidc', summary='Get workspace OIDC configuration')
def workspace_oidc_get(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: get_workspace_oidc_config(request))


@app.put('/workspace/sso/oidc', summary='Create or update workspace OIDC configuration')
def workspace_oidc_put(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: upsert_workspace_oidc_config(payload, request))


@app.delete('/workspace/sso/oidc', summary='Delete workspace OIDC configuration')
def workspace_oidc_delete(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: delete_workspace_oidc_config(request))


@app.get('/workspace/scim/tokens', summary='List workspace SCIM bearer tokens')
def workspace_scim_tokens_list(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: list_workspace_scim_tokens(request))


@app.post('/workspace/scim/tokens', summary='Create a workspace SCIM bearer token')
def workspace_scim_tokens_create(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: create_workspace_scim_token(payload, request))


@app.delete('/workspace/scim/tokens/{token_id}', summary='Revoke a workspace SCIM bearer token')
def workspace_scim_tokens_revoke(token_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: revoke_workspace_scim_token(token_id, request))


@app.get('/scim/v2/Users', summary='SCIM 2.0 list users')
def scim_users_list(request: Request, startIndex: int = 1, count: int = 100) -> dict[str, Any]:
    return with_auth_schema_json(lambda: scim_list_users(request, start_index=startIndex, count=count))


@app.post('/scim/v2/Users', summary='SCIM 2.0 provision user', status_code=201)
def scim_users_create(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: scim_create_user(payload, request))


@app.put('/scim/v2/Users/{user_id}', summary='SCIM 2.0 replace user')
def scim_users_replace(user_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: scim_replace_user(user_id, payload, request))


@app.patch('/scim/v2/Users/{user_id}', summary='SCIM 2.0 update user')
def scim_users_patch(user_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: scim_patch_user(user_id, payload, request))


@app.delete('/scim/v2/Users/{user_id}', summary='SCIM 2.0 deprovision user')
def scim_users_delete(user_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: scim_delete_user(user_id, request))


@app.get('/scim/v2/Groups', summary='SCIM 2.0 list groups')
def scim_groups_list(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: scim_list_groups(request))


@app.post('/scim/v2/Groups', summary='SCIM 2.0 provision group', status_code=201)
def scim_groups_create(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: scim_create_group(payload, request))


@app.put('/scim/v2/Groups/{group_id}', summary='SCIM 2.0 replace group')
def scim_groups_replace(group_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: scim_replace_group(group_id, payload, request))


@app.patch('/scim/v2/Groups/{group_id}', summary='SCIM 2.0 update group')
def scim_groups_patch(group_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: scim_replace_group(group_id, payload, request))


@app.delete('/scim/v2/Groups/{group_id}', summary='SCIM 2.0 delete group')
def scim_groups_delete(group_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: scim_delete_group(group_id, request))


@app.get('/workspace/retention-policies', summary='Get workspace retention policies')
def workspace_retention_policies_get(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: get_workspace_retention_policies(request))


@app.put('/workspace/retention-policies', summary='Update workspace retention policies')
def workspace_retention_policies_put(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: update_workspace_retention_policies(payload, request))


@app.get('/workspace/legal-holds', summary='List workspace legal holds')
def workspace_legal_holds_get(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: list_workspace_legal_holds(request))


@app.post('/workspace/legal-holds', summary='Create workspace legal hold')
def workspace_legal_holds_post(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: create_workspace_legal_hold(payload, request))


@app.post('/workspace/legal-holds/{hold_id}/release', summary='Release workspace legal hold')
def workspace_legal_hold_release(hold_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: release_workspace_legal_hold(hold_id, payload, request))


@app.get('/workspace/deletion-requests', summary='List auditable data deletion requests')
def workspace_deletion_requests_get(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: list_data_deletion_requests(request))


@app.post('/workspace/deletion-requests', summary='Create auditable data deletion request')
def workspace_deletion_requests_post(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: create_data_deletion_request(payload, request))


@app.post('/workspace/deletion-requests/{request_id}/approve-and-execute', summary='Approve and execute a data deletion request')
def workspace_deletion_request_execute(request_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: approve_and_execute_data_deletion_request(request_id, request))


@app.get('/workspace/members', summary='List members for current workspace')
def workspace_members(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: list_workspace_members(request))


@app.get('/workspace/api-keys', summary='List workspace API keys')
def workspace_api_keys_list(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: list_workspace_api_keys(request))


@app.get('/api-keys', summary='List workspace API keys')
def api_keys_list(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: list_workspace_api_keys(request))


@app.post('/workspace/api-keys', summary='Create workspace API key')
def workspace_api_keys_create(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: create_workspace_api_key(payload, request))


@app.post('/api-keys', summary='Create workspace API key')
def api_keys_create(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: create_workspace_api_key(payload, request))


@app.post('/workspace/api-keys/{api_key_id}/rotate', summary='Rotate workspace API key')
def workspace_api_keys_rotate(api_key_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: rotate_workspace_api_key(api_key_id, request))


@app.post('/api-keys/{api_key_id}/rotate', summary='Rotate workspace API key')
def api_keys_rotate(api_key_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: rotate_workspace_api_key(api_key_id, request))


@app.get('/workspace/security/credential-rotation/policies', summary='List credential rotation policies')
def credential_rotation_policies_list(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: list_credential_rotation_policies(request))


@app.put('/workspace/security/credential-rotation/policies', summary='Create or update a credential rotation policy')
def credential_rotation_policies_put(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: upsert_credential_rotation_policy(payload, request))


@app.get('/workspace/security/credential-rotation/history', summary='List auditable credential rotation history')
def credential_rotation_history_list(request: Request, limit: int = 200) -> dict[str, Any]:
    return with_auth_schema_json(lambda: list_credential_rotation_history(request, limit=limit))


@app.post('/workspace/security/credentials/{credential_type}/{resource_id}/rotate', summary='Rotate or replace a versioned credential')
def credential_version_rotate(credential_type: str, resource_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: rotate_workspace_credential(credential_type, resource_id, payload, request))


@app.post('/workspace/security/credentials/{credential_type}/{resource_id}/revoke', summary='Revoke a credential version')
def credential_version_revoke(credential_type: str, resource_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: revoke_workspace_credential(credential_type, resource_id, payload, request))


@app.post('/workspace/security/credential-versions/{version_id}/claim', summary='Claim an automatically rotated credential once')
def credential_version_claim(version_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: claim_rotated_credential_secret(version_id, request))


@app.post('/workspace/security/credential-rotation/run', summary='Run due credential rotations')
def credential_rotation_run(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: trigger_due_credential_rotations(request))


@app.delete('/workspace/api-keys/{api_key_id}', summary='Revoke workspace API key')
def workspace_api_keys_revoke(api_key_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: revoke_workspace_api_key(api_key_id, request))


@app.post('/api-keys/{api_key_id}/revoke', summary='Revoke workspace API key')
def api_keys_revoke(api_key_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: revoke_workspace_api_key(api_key_id, request))


@app.post('/workspace/invitations', summary='Create workspace invitation')
def workspace_invite(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: create_workspace_invitation(payload, request))


@app.get('/workspace/invitations', summary='List workspace invitations')
def workspace_invites_list(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: list_workspace_invitations(request))


@app.get('/workspaces/current', summary='Get current workspace context')
def workspaces_current(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: get_current_workspace(request))


@app.post('/workspace/invitations/{invitation_id}/resend', summary='Resend workspace invitation')
def workspace_invites_resend(invitation_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: resend_workspace_invitation(invitation_id, request))


@app.delete('/workspace/invitations/{invitation_id}', summary='Revoke workspace invitation')
def workspace_invites_revoke(invitation_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: revoke_workspace_invitation(invitation_id, request))


@app.post('/workspace/invitations/accept', summary='Accept workspace invitation')
def workspace_invite_accept(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: accept_workspace_invitation(payload, request))


@app.patch('/workspace/members/{member_id}', summary='Update workspace member')
def workspace_member_patch(member_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: update_workspace_member(member_id, payload, request))


@app.delete('/workspace/members/{member_id}', summary='Remove workspace member')
def workspace_member_delete(member_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: remove_workspace_member(member_id, request))


@app.get('/team/seats', summary='Workspace seat usage')
def team_seats(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: get_team_seats(request))


@app.get('/onboarding/state', summary='Get onboarding checklist status for current workspace')
def onboarding_state(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: get_onboarding_state(request))


@app.get('/onboarding/progress', summary='Get derived onboarding progress for current workspace')
def onboarding_progress(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: get_onboarding_progress(request))


@app.patch('/onboarding/state', summary='Update onboarding checklist step for current workspace')
def onboarding_state_patch(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: update_onboarding_state(payload, request))


# ---------------------------------------------------------------------------
# Autonomous Onboarding Agent (Screen 1) — durable session lifecycle.
# ---------------------------------------------------------------------------
@app.post('/api/onboarding/sessions', summary='Create or resume an onboarding session')
def onboarding_agent_create_session(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: onboarding_agent.create_or_resume_session(payload, request))


@app.get('/api/onboarding/sessions/{session_id}', summary='Get onboarding session state, steps, findings, proposal')
def onboarding_agent_get_session(session_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: onboarding_agent.get_session(session_id, request))


@app.post('/api/onboarding/sessions/{session_id}/discover', summary='Start deterministic discovery for a session')
def onboarding_agent_discover(session_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: onboarding_agent.start_discovery(session_id, request))


@app.post('/api/onboarding/sessions/{session_id}/rpc-benchmark', summary='Re-benchmark configured RPC endpoints')
def onboarding_agent_rpc_benchmark(session_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: onboarding_agent.rerun_rpc_benchmark(session_id, request))


@app.post('/api/onboarding/sessions/{session_id}/generate-proposal', summary='Generate the proposed workspace configuration')
def onboarding_agent_generate_proposal(session_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: onboarding_agent.generate_proposal(session_id, request))


@app.post('/api/onboarding/sessions/{session_id}/approve', summary='Record approval of the generated proposal')
def onboarding_agent_approve(payload: dict[str, Any], session_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: onboarding_agent.approve_session(session_id, payload, request))


@app.post('/api/onboarding/sessions/{session_id}/activate', summary='Idempotently activate the approved proposal')
def onboarding_agent_activate(session_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: onboarding_agent.activate_session(session_id, request))


@app.post('/api/onboarding/sessions/{session_id}/retry', summary='Retry only failed or incomplete onboarding steps')
def onboarding_agent_retry(session_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: onboarding_agent.retry_session(session_id, request))


@app.get('/api/onboarding/sessions/{session_id}/report', summary='Export the onboarding discovery report (SHA-256 hashed)')
def onboarding_agent_report(session_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: onboarding_agent.export_report(session_id, request))


@app.get('/api/onboarding/sessions/{session_id}/events', summary='SSE stream of live onboarding progress for a session')
async def onboarding_agent_events(session_id: str, request: Request):
    """Server-Sent Events stream of Onboarding Agent progress. Authenticates via
    Bearer token + X-Workspace-Id and is workspace-scoped (Redis-backed, resumable,
    multi-replica). The frontend falls back to polling GET /sessions/{id} when SSE
    is unavailable."""
    try:
        user = authenticate_request(request)
    except HTTPException as exc:
        return JSONResponse({'detail': exc.detail, 'code': 'UNAUTHENTICATED'}, status_code=401)
    requested_workspace_id = request.headers.get('x-workspace-id', '').strip()
    try:
        with pg_connection() as connection:
            ensure_pilot_schema(connection)
            workspace_context = resolve_workspace(connection, str(user['id']), requested_workspace_id)
            # Enforce that the session belongs to this workspace before streaming.
            session_row = connection.execute(
                'SELECT id FROM onboarding_sessions WHERE id = %s AND workspace_id = %s',
                (session_id, workspace_context['workspace_id']),
            ).fetchone()
        workspace_id = str(workspace_context['workspace_id'])
    except HTTPException as exc:
        return JSONResponse({'detail': exc.detail, 'code': 'WORKSPACE_ACCESS_DENIED'}, status_code=exc.status_code)
    if session_row is None:
        return JSONResponse({'detail': 'Onboarding session not found.', 'code': 'NOT_FOUND'}, status_code=404)
    backend = await alert_stream.connectivity()
    if not backend['connected']:
        return JSONResponse(
            {'detail': 'Shared event stream backend unavailable; use polling fallback.', 'code': 'ONBOARDING_STREAM_UNAVAILABLE'},
            status_code=503,
        )
    last_event_id = request.headers.get('last-event-id', '').strip() or '$'
    headers = _sse_response_headers(request)
    return StreamingResponse(
        _sse_heartbeat_generator(workspace_id, last_event_id, request,
                                 subscribe_factory=alert_stream.subscribe_onboarding, stream_name='onboarding'),
        media_type='text/event-stream',
        headers=headers,
    )


@app.get('/billing/plans', summary='List billing plans')
def billing_plans() -> dict[str, Any]:
    return with_auth_schema_json(list_plan_entitlements)


@app.get('/billing/subscription', summary='Get workspace subscription')
def billing_subscription(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: get_workspace_subscription(request))


@app.post('/billing/checkout-session', summary='Create checkout session')
def billing_checkout(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: create_checkout_session(payload, request))


@app.post('/billing/portal-session', summary='Create billing portal session')
def billing_portal(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: create_portal_session(request))


@app.post('/billing/webhooks/stripe', summary='Stripe billing webhook')
def billing_webhook_stripe(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    signature = request.headers.get('stripe-signature')
    return with_auth_schema_json(lambda: process_stripe_webhook(payload, signature))


@app.post('/billing/webhooks/paddle', summary='Paddle billing webhook')
async def billing_webhook_paddle(request: Request) -> dict[str, Any]:
    raw = await request.body()
    payload = json.loads(raw.decode('utf-8') or '{}')
    signature = request.headers.get('paddle-signature')
    timestamp = request.headers.get('paddle-timestamp')
    return with_auth_schema_json(lambda: process_paddle_webhook(payload, signature_header=signature, timestamp_header=timestamp, raw_body=raw))


@app.get('/api/billing/paddle/webhook', summary='Paddle webhook endpoint health check')
def billing_paddle_webhook_health() -> dict[str, Any]:
    return {'status': 'paddle_webhook_endpoint_ready'}


@app.post('/api/billing/paddle/webhook', summary='Paddle billing webhook (canonical production URL)')
async def billing_webhook_paddle_canonical(request: Request) -> dict[str, Any]:
    raw = await request.body()
    payload = json.loads(raw.decode('utf-8') or '{}')
    signature = request.headers.get('paddle-signature')
    timestamp = request.headers.get('paddle-timestamp')
    return with_auth_schema_json(lambda: process_paddle_webhook(payload, signature_header=signature, timestamp_header=timestamp, raw_body=raw))


@app.get('/api/integrations/quicknode/streams/base', summary='QuickNode Streams (Base) webhook health check')
def quicknode_streams_base_health() -> dict[str, Any]:
    return {'status': 'quicknode_streams_base_endpoint_ready'}


@app.post('/api/integrations/quicknode/streams/base', summary='QuickNode Streams webhook (Base chain wallet transfers)')
async def quicknode_streams_base_webhook(request: Request) -> dict[str, Any]:
    raw = await request.body()
    signature = request.headers.get('x-qn-signature')
    nonce = request.headers.get('x-qn-nonce')
    timestamp = request.headers.get('x-qn-timestamp')
    content_encoding = request.headers.get('content-encoding')
    # Mandatory route-hit marker, logged before any return (including
    # signature/validation failures) so every QuickNode POST 200 (and every
    # rejected one) is provable from logs alone. Uses the INFO-pinned QuickNode
    # diagnostics logger so it survives a global LOG_LEVEL=WARNING.
    _quicknode_streams_logger.info(
        'quicknode_stream_route_hit content_length=%s content_encoding=%s '
        'has_nonce=%s has_timestamp=%s has_signature=%s',
        len(raw), content_encoding, bool(nonce), bool(timestamp), bool(signature),
    )
    return with_auth_schema_json(lambda: process_quicknode_base_stream_webhook(
        raw_body=raw,
        signature_header=signature,
        nonce_header=nonce,
        timestamp_header=timestamp,
        content_encoding=content_encoding,
    ))


@app.get('/api/integrations/quicknode/streams/base-live', summary='QuickNode live chain-tip stream webhook health check')
def quicknode_streams_base_live_health() -> dict[str, Any]:
    return {'status': 'quicknode_streams_base_live_endpoint_ready'}


@app.post('/api/integrations/quicknode/streams/base-live', summary='QuickNode live chain-tip stream webhook (Base current-block wallet transfers)')
async def quicknode_streams_base_live_webhook(request: Request) -> dict[str, Any]:
    """Dedicated LIVE lane: a QuickNode stream configured to start at the CURRENT Base
    block posts here. The lane is explicit (``lane='live'``) — never inferred from a
    mutable checkpoint — so it advances only quicknode:base:live and reports degraded
    when the pushed blocks fall behind the chain head. Stable RPC Polling is unchanged."""
    raw = await request.body()
    signature = request.headers.get('x-qn-signature')
    nonce = request.headers.get('x-qn-nonce')
    timestamp = request.headers.get('x-qn-timestamp')
    content_encoding = request.headers.get('content-encoding')
    _quicknode_streams_logger.info(
        'quicknode_stream_route_hit stream_lane=live stream_key=base-live content_length=%s '
        'content_encoding=%s has_nonce=%s has_timestamp=%s has_signature=%s',
        len(raw), content_encoding, bool(nonce), bool(timestamp), bool(signature),
    )
    return with_auth_schema_json(lambda: process_quicknode_base_stream_webhook(
        raw_body=raw,
        signature_header=signature,
        nonce_header=nonce,
        timestamp_header=timestamp,
        content_encoding=content_encoding,
        lane='live',
    ))


@app.get('/api/integrations/quicknode/streams/base-backfill', summary='QuickNode historical backfill stream webhook health check')
def quicknode_streams_base_backfill_health() -> dict[str, Any]:
    return {'status': 'quicknode_streams_base_backfill_endpoint_ready'}


@app.post('/api/integrations/quicknode/streams/base-backfill', summary='QuickNode historical backfill stream webhook (Base wallet transfers)')
async def quicknode_streams_base_backfill_webhook(request: Request) -> dict[str, Any]:
    """Dedicated BACKFILL lane: an optional second QuickNode stream replaying history
    posts here (``lane='backfill'``). It advances only quicknode:base:backfill,
    persists detected_by=quicknode_stream_backfill, and NEVER controls live health — so
    a historical catch-up can never paint the UI's QuickNode status green."""
    raw = await request.body()
    signature = request.headers.get('x-qn-signature')
    nonce = request.headers.get('x-qn-nonce')
    timestamp = request.headers.get('x-qn-timestamp')
    content_encoding = request.headers.get('content-encoding')
    _quicknode_streams_logger.info(
        'quicknode_stream_route_hit stream_lane=backfill stream_key=base-backfill content_length=%s '
        'content_encoding=%s has_nonce=%s has_timestamp=%s has_signature=%s',
        len(raw), content_encoding, bool(nonce), bool(timestamp), bool(signature),
    )
    return with_auth_schema_json(lambda: process_quicknode_base_stream_webhook(
        raw_body=raw,
        signature_header=signature,
        nonce_header=nonce,
        timestamp_header=timestamp,
        content_encoding=content_encoding,
        lane='backfill',
    ))


@app.get('/api/integrations/quicknode/streams/base/debug-tx', summary='Replay QuickNode matcher/dedupe for a tx fetched from Base RPC (ops, read-only by default)')
def quicknode_streams_base_debug_tx(request: Request, tx_hash: str, dry_run: bool = True) -> dict[str, Any]:
    """Safe ops diagnostic for "QuickNode Stream missed a fresh tx".

    Fetches the transaction + receipt from the configured Base RPC, normalizes it
    exactly as the webhook does, and re-runs the identical (intentionally unscoped)
    target load + wallet match + duplicate check across every active Base wallet
    target. Reports whether the tx matches a monitored wallet and whether a telemetry
    row already exists — WITHOUT writing anything unless ``dry_run=false``, in which
    case it persists via the same path the live webhook uses.

    Gated by the QuickNode Streams secret (``x-quicknode-ops-token`` header), because
    it replays the webhook's unscoped matcher rather than a workspace-scoped query.

    Query params: ``tx_hash`` (66-char 0x-prefixed, required), ``dry_run`` (bool,
    default true).
    """
    enforce_auth_rate_limit(request, 'quicknode_streams_debug_tx')
    verify_quicknode_ops_token(request.headers.get('x-quicknode-ops-token'))
    return with_auth_schema_json(lambda: run_quicknode_debug_tx(tx_hash=tx_hash, dry_run=dry_run))


@app.get('/webhooks', summary='List workspace webhooks')
def webhooks_list(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: list_webhooks(request))


@app.post('/webhooks', summary='Create workspace webhook')
def webhooks_create(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: create_webhook(payload, request))


@app.patch('/webhooks/{webhook_id}', summary='Update workspace webhook')
def webhooks_patch(webhook_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: update_webhook(webhook_id, payload, request))


@app.post('/webhooks/{webhook_id}/rotate-secret', summary='Rotate webhook secret')
def webhooks_rotate_secret(webhook_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: rotate_webhook_secret(webhook_id, request))



@app.get('/webhooks/{webhook_id}/deliveries', summary='List webhook deliveries')
def webhooks_deliveries(webhook_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: list_webhook_deliveries(webhook_id, request))


@app.get('/targets', summary='List workspace targets')
def targets_list(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: list_targets(request))


@app.get('/monitoring/targets', summary='List target monitoring settings')
def monitoring_targets_list(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: list_monitoring_targets(request))


@app.get('/monitoring/sources', summary='List monitoring sources linkage')
def monitoring_sources_list(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: list_monitoring_sources(request))


@app.get('/monitoring/sources/settings', summary='Read Auto-Routing / failover / threshold settings')
def monitoring_source_settings_get(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: get_source_optimization_settings(request))


@app.put('/monitoring/sources/settings', summary='Update Auto-Routing / failover / threshold settings (admin)')
def monitoring_source_settings_update(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: update_source_optimization_settings(payload, request))


@app.post('/monitoring/sources/health-check', summary='Run a deterministic source health check and record agent decisions')
def monitoring_source_health_check(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: run_source_health_check(request))


@app.post('/monitoring/sources/diagnostic', summary='Run a real bounded provider diagnostic that persists live runtime evidence')
def monitoring_source_diagnostic(request: Request, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return with_auth_schema_json(lambda: run_source_diagnostic(request, payload))


@app.get('/monitoring/sources/decisions', summary='List Source Optimization Agent decisions')
def monitoring_source_decisions_list(request: Request, limit: int = 50) -> dict[str, Any]:
    return with_auth_schema_json(lambda: list_source_agent_decisions(request, limit=limit))


@app.get('/monitoring/runs', summary='List recent monitoring runs for workspace')
def monitoring_runs_list(request: Request, limit: int = 20) -> dict[str, Any]:
    return with_auth_schema_json(lambda: list_monitoring_runs(request, limit=limit))


@app.get('/monitoring/runs/{run_id}', summary='Get a monitoring run for workspace')
def monitoring_run_get(run_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: get_monitoring_run(run_id, request))


@app.get('/monitoring/targets/{target_id}/telemetry', summary='List telemetry events for a target')
def monitoring_target_telemetry(
    target_id: str,
    request: Request,
    limit: int = 50,
    offset: int = 0,
    q: str | None = None,
    event_type_filter: str | None = None,
) -> dict[str, Any]:
    return with_auth_schema_json(
        lambda: list_target_telemetry(
            request,
            target_id=target_id,
            limit=limit,
            offset=offset,
            q=q,
            event_type_filter=event_type_filter,
        )
    )


@app.patch('/monitoring/targets/{target_id}', summary='Update target monitoring settings')
def monitoring_targets_patch(target_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: patch_monitoring_target(target_id, payload, request))


@app.get('/monitoring/systems', summary='List monitored systems')
def monitoring_systems_list(request: Request) -> dict[str, Any]:
    try:
        systems_payload = with_auth_schema_json(lambda: list_monitored_systems(request))
    except Exception as exc:
        logger.exception('monitoring_systems_list_failed')
        # Surface a stable error code + correlation id so the client can render a distinct
        # API-failure state (never the genuine-empty state or an asset-creation CTA).
        detail = getattr(exc, 'detail', None)
        error_obj: dict[str, Any] = {
            'code': 'monitoring_systems_route_failed',
            'type': type(exc).__name__,
            'message': 'Monitored systems temporarily unavailable.',
        }
        if isinstance(detail, dict):
            if detail.get('code'):
                error_obj['code'] = str(detail['code'])
            if detail.get('correlation_id'):
                error_obj['correlation_id'] = str(detail['correlation_id'])
            if detail.get('stage'):
                error_obj['stage'] = str(detail['stage'])
        systems_payload = {
            'systems': [],
            'workspace': None,
            'error': error_obj,
        }
    try:
        runtime_payload = with_auth_schema_json(lambda: monitoring_runtime_status(request))
        systems_payload['workspace_monitoring_summary'] = runtime_payload.get('workspace_monitoring_summary')
    except Exception:
        logger.exception('monitoring_systems_list_runtime_summary_failed')
        systems_payload['workspace_monitoring_summary'] = None
    return systems_payload


@app.post('/monitoring/systems/reconcile', summary='Repair monitored systems from eligible targets')
def monitoring_systems_reconcile(request: Request) -> Any:
    workspace_hint = (request.headers.get('x-workspace-id') or '').strip() or None
    user_hint = (request.headers.get('x-user-id') or '').strip() or None
    logger.info(
        'monitoring_reconcile_handler_entered method=%s path=%s origin=%s workspace_id_hint=%s user_id_hint=%s',
        request.method,
        request.url.path,
        request.headers.get('origin', ''),
        workspace_hint,
        user_hint,
    )
    try:
        reconcile_payload = with_auth_schema_json(lambda: reconcile_workspace_monitored_systems(request))
    except HTTPException as exc:
        if isinstance(exc.detail, dict):
            logger.error(
                'monitoring_reconcile_http_exception method=%s path=%s workspace_id=%s user_id=%s stage=%s code=%s',
                request.method,
                request.url.path,
                workspace_hint,
                user_hint,
                exc.detail.get('stage'),
                exc.detail.get('code'),
            )
            return JSONResponse(exc.detail, status_code=exc.status_code)
        raise
    except Exception as exc:
        logger.exception(
            'monitoring_reconcile_unexpected_error method=%s path=%s origin=%s workspace_id=%s user_id=%s stage=%s error_type=%s error_message=%s has_auth_header=%s has_workspace_header=%s has_user_header=%s',
            request.method,
            request.url.path,
            request.headers.get('origin', ''),
            workspace_hint,
            user_hint,
            'reconcile_workspace_monitored_systems',
            type(exc).__name__,
            str(exc),
            bool((request.headers.get('authorization') or '').strip()),
            bool((request.headers.get('x-workspace-id') or '').strip()),
            bool((request.headers.get('x-user-id') or '').strip()),
        )
        payload: dict[str, Any] = {
            'code': 'monitoring_reconcile_failed',
            'state': 'failure',
            'reconcile_id': None,
            'detail': 'Unexpected backend error during monitored systems reconcile.',
            'stage': 'reconcile_workspace_monitored_systems',
        }
        if not _is_production_like_runtime():
            payload['debug_error_type'] = type(exc).__name__
            payload['debug_error_message'] = str(exc)
        return JSONResponse(payload, status_code=500)
    try:
        runtime_payload: dict[str, Any] | None = None
        runtime_error: dict[str, Any] | None = None
        try:
            runtime_payload = with_auth_schema_json(lambda: monitoring_runtime_status(request))
        except Exception as runtime_exc:
            logger.exception(
                'monitoring_reconcile_runtime_status_after_repair_failed method=%s path=%s workspace_id=%s user_id=%s error_type=%s',
                request.method,
                request.url.path,
                workspace_hint,
                user_hint,
                type(runtime_exc).__name__,
            )
            runtime_error = {'error_type': type(runtime_exc).__name__, 'error_message': str(runtime_exc)}
        if isinstance(reconcile_payload, dict):
            diagnostics = reconcile_payload.setdefault('diagnostics', {})
            if isinstance(diagnostics, dict):
                diagnostics['runtime_status_after_repair'] = runtime_payload
                if runtime_error is not None:
                    diagnostics['runtime_status_after_repair_error'] = runtime_error
        return reconcile_payload
    except Exception as exc:
        resolved_path = request.url.path
        logger.exception(
            'monitoring_reconcile_unexpected_error method=%s path=%s origin=%s workspace_id=%s user_id=%s stage=%s error_type=%s error_message=%s has_auth_header=%s has_workspace_header=%s has_user_header=%s',
            request.method,
            resolved_path,
            request.headers.get('origin', ''),
            workspace_hint,
            user_hint,
            'attach_runtime_status_after_repair',
            type(exc).__name__,
            str(exc),
            bool((request.headers.get('authorization') or '').strip()),
            bool((request.headers.get('x-workspace-id') or '').strip()),
            bool((request.headers.get('x-user-id') or '').strip()),
        )
        payload: dict[str, Any] = {
            'code': 'monitoring_reconcile_failed',
            'state': 'failure',
            'reconcile_id': None,
            'detail': 'Unexpected backend error during monitored systems reconcile.',
            'stage': 'attach_runtime_status_after_repair',
        }
        if not _is_production_like_runtime():
            payload['debug_error_type'] = type(exc).__name__
            payload['debug_error_message'] = str(exc)
        return JSONResponse(payload, status_code=500)



@app.post('/monitoring/systems/repair/treasury-settlement-target', summary='Create or repair the US Treasury Settlement Contract monitoring target')
def monitoring_systems_repair_treasury_settlement_target(request: Request) -> dict[str, Any]:
    payload = with_auth_schema_json(lambda: create_or_repair_treasury_settlement_monitoring_target(request))
    runtime_payload = with_auth_schema_json(lambda: monitoring_runtime_status(request))
    payload['workspace_monitoring_summary'] = runtime_payload.get('workspace_monitoring_summary')
    return payload

@app.post('/monitoring/systems/reconcile/start', summary='Start monitored systems reconcile job')
def monitoring_systems_reconcile_start(request: Request) -> Any:
    return monitoring_systems_reconcile(request)


@app.get('/monitoring/systems/reconcile/latest', summary='Latest monitored systems reconcile status')
def monitoring_systems_reconcile_latest(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: get_latest_workspace_reconcile_run(request))


@app.get('/monitoring/systems/reconcile/status', summary='Latest monitored systems reconcile status')
def monitoring_systems_reconcile_latest_status_alias(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: get_latest_workspace_reconcile_run(request))


@app.get('/monitoring/systems/reconcile/status/latest', summary='Latest monitored systems reconcile status')
def monitoring_systems_reconcile_latest_status(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: get_latest_workspace_reconcile_run(request))


@app.get('/monitoring/systems/reconcile/status/result', summary='Latest monitored systems reconcile status and result')
def monitoring_systems_reconcile_status_result(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(
        lambda: {
            'job': get_latest_workspace_reconcile_run(request).get('job'),
            'result': get_latest_workspace_reconcile_result(request).get('result'),
        }
    )


@app.get('/monitoring/systems/reconcile/{reconcile_id}', summary='Get monitored systems reconcile job status')
def monitoring_systems_reconcile_status(reconcile_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: get_workspace_reconcile_status(request, reconcile_id))


@app.get('/monitoring/systems/reconcile/status/{reconcile_id}', summary='Get monitored systems reconcile job status')
def monitoring_systems_reconcile_status_alias(reconcile_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: get_workspace_reconcile_status(request, reconcile_id))


@app.get('/monitoring/systems/reconcile/idempotency/{idempotency_key}', summary='Get monitored systems reconcile job status by idempotency key')
def monitoring_systems_reconcile_status_by_idempotency(idempotency_key: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: get_workspace_reconcile_status_by_idempotency_key(request, idempotency_key))


@app.get('/monitoring/systems/reconcile/{reconcile_id}/events', summary='Get monitored systems reconcile job events')
def monitoring_systems_reconcile_events(reconcile_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: get_workspace_reconcile_events(request, reconcile_id))


@app.get('/monitoring/systems/reconcile/latest/result', summary='Get latest monitored systems reconcile result summary')
def monitoring_systems_reconcile_latest_result(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: get_latest_workspace_reconcile_result(request))


@app.get('/monitoring/systems/reconcile/outcome/latest', summary='Get latest monitored systems reconcile outcome')
def monitoring_systems_reconcile_latest_outcome(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: get_latest_workspace_reconcile_result(request))


@app.get('/monitoring/systems/reconcile/result/{reconcile_id}', summary='Get monitored systems reconcile result summary')
def monitoring_systems_reconcile_result(reconcile_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: get_workspace_reconcile_result(request, reconcile_id))


@app.get('/monitoring/systems/reconcile/{reconcile_id}/result', summary='Get monitored systems reconcile result summary')
def monitoring_systems_reconcile_result_alias(reconcile_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: get_workspace_reconcile_result(request, reconcile_id))


@app.get('/monitoring/systems/reconcile/result/latest', summary='Get latest monitored systems reconcile result summary')
def monitoring_systems_reconcile_latest_result_alias(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: get_latest_workspace_reconcile_result(request))


@app.get('/monitoring/workspace-debug', summary='Workspace monitoring source-of-truth debug snapshot')
def monitoring_workspace_debug(request: Request) -> dict[str, Any]:
    snapshot_payload = with_auth_schema_json(lambda: get_workspace_monitoring_debug(request))
    runtime_payload = with_auth_schema_json(lambda: monitoring_runtime_status(request))
    list_route_snapshot = snapshot_payload.get('list_route_snapshot') if isinstance(snapshot_payload.get('list_route_snapshot'), dict) else {}
    configuration_diagnostics = runtime_payload.get('configuration_diagnostics')
    if not isinstance(configuration_diagnostics, dict):
        runtime_summary = runtime_payload.get('workspace_monitoring_summary') if isinstance(runtime_payload.get('workspace_monitoring_summary'), dict) else {}
        configuration_diagnostics = runtime_summary.get('configuration_diagnostics') if isinstance(runtime_summary.get('configuration_diagnostics'), dict) else {}
    return {
        **snapshot_payload,
        'configuration_diagnostics': configuration_diagnostics,
        'status_decision_inputs': {
            'resolved_workspace_id_runtime': runtime_payload.get('resolved_workspace_id'),
            'resolved_workspace_id_list_route': list_route_snapshot.get('resolved_workspace_id'),
            'list_route_monitored_systems_count': int(list_route_snapshot.get('monitored_systems_count') or 0),
            'list_route_enabled_monitored_systems_count': int(list_route_snapshot.get('enabled_monitored_systems_count') or 0),
            'list_route_protected_asset_count': int(list_route_snapshot.get('protected_asset_count') or 0),
            'healthy_enabled_targets': int(runtime_payload.get('healthy_enabled_targets') or 0),
            'invalid_enabled_targets': int(runtime_payload.get('invalid_enabled_targets') or 0),
            'monitored_systems_count': int(runtime_payload.get('monitored_systems_count') or runtime_payload.get('monitored_systems') or 0),
            'protected_assets_count': int(runtime_payload.get('protected_assets_count') or runtime_payload.get('protected_assets') or 0),
            'systems_with_recent_heartbeat': int(runtime_payload.get('systems_with_recent_heartbeat') or 0),
            'runtime_enabled_systems_count': int(runtime_payload.get('enabled_systems') or 0),
            'runtime_status': runtime_payload.get('status'),
            'monitoring_status': runtime_payload.get('monitoring_status'),
        },
        'runtime_summary': runtime_payload,
    }


@app.post('/monitoring/systems', summary='Create monitored system')
def monitoring_systems_create(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: create_monitored_system(payload, request))


@app.patch('/monitoring/systems/{system_id}', summary='Update monitored system status')
def monitoring_systems_patch(system_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: patch_monitored_system(system_id, payload, request))


@app.delete('/monitoring/systems/{system_id}', summary='Delete monitored system')
def monitoring_systems_delete(system_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: delete_monitored_system(system_id, request))


@app.post('/targets/{target_id}/enable', summary='Enable target and monitoring')
def targets_enable(target_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: set_target_enabled(target_id, True, request))


@app.post('/targets/{target_id}/disable', summary='Disable target and monitoring')
def targets_disable(target_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: set_target_enabled(target_id, False, request))


@app.post('/targets/{target_id}/repair', summary='Repair orphaned monitoring target by relinking to matching asset')
def targets_repair(target_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: repair_orphan_target(target_id, request))


@app.post('/monitoring/run-once/{target_id}', summary='Debug-only: trigger one manual monitoring run for target (not enterprise-proof eligible)')
def monitoring_run_once(target_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: run_monitoring_once(target_id, request))


@app.post('/workflow/guided-threat-chain', summary='Run guided end-to-end threat workflow chain')
def workflow_guided_threat_chain(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: run_guided_threat_workflow(payload, request))


@app.get('/assets', summary='List workspace assets')
def assets_list(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: list_assets(request))


@app.post('/assets', summary='Create workspace asset')
def assets_create(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: create_asset(payload, request))


@app.get('/asset-profiles', summary='List workspace asset profiles')
def asset_profiles_list(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: list_assets(request))


@app.post('/asset-profiles', summary='Create workspace asset profile')
def asset_profiles_create(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: create_asset(payload, request))


@app.get('/assets/{asset_id}', summary='Get workspace asset')
def assets_get(asset_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: get_asset(asset_id, request))


@app.patch('/assets/{asset_id}', summary='Update workspace asset')
def assets_patch(asset_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: update_asset(asset_id, payload, request))


@app.post('/assets/{asset_id}/verify', summary='Verify workspace asset and enable monitoring prerequisites')
def assets_verify(asset_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: verify_asset(asset_id, request))


@app.patch('/asset-profiles/{asset_id}', summary='Update workspace asset profile')
def asset_profiles_patch(asset_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: update_asset(asset_id, payload, request))


@app.delete('/assets/{asset_id}', summary='Delete workspace asset')
def assets_delete(asset_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: delete_asset(asset_id, request))


@app.post('/assets/{asset_id}/bind/resolve-onchain', summary='Resolve asset on-chain token metadata')
def assets_bind_resolve_onchain(asset_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: resolve_asset_onchain(asset_id, payload, request))


@app.post('/assets/{asset_id}/bind/wallets', summary='Bind treasury/custody/counterparty wallets')
def assets_bind_wallets(asset_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: bind_asset_wallets(asset_id, payload, request))


@app.post('/assets/{asset_id}/bind/chainlink', summary='Bind chainlink feeds to asset')
def assets_bind_chainlink(asset_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: bind_asset_chainlink_feeds(asset_id, payload, request))


@app.post('/targets', summary='Create workspace target')
def targets_create(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: create_target(payload, request))


@app.get('/targets/{target_id}', summary='Get workspace target')
def targets_get(target_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: get_target(target_id, request))


@app.patch('/targets/{target_id}', summary='Update workspace target')
def targets_patch(target_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: update_target(target_id, payload, request))


@app.delete('/targets/{target_id}', summary='Delete workspace target')
def targets_delete(target_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: delete_target(target_id, request))


@app.get('/modules/{module_key}/config', summary='Get module config')
def modules_get_config(module_key: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: get_module_config(module_key, request))


@app.put('/modules/{module_key}/config', summary='Save module config')
def modules_put_config(module_key: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: put_module_config(module_key, payload, request))


@app.post('/run-detection', summary='Run detection on existing live telemetry')
def run_detection(request: Request) -> dict[str, Any]:
    try:
        return with_auth_schema_json(lambda: run_detection_from_existing_telemetry(request))
    except HTTPException:
        raise
    except Exception as exc:
        logger.error('run_detection_failed method=%s error_type=%s error=%s', request.method, exc.__class__.__name__, exc)
        raise HTTPException(status_code=500, detail='Unable to run detection at this time.') from None


@app.post('/alerts/open-from-detection', summary='Open alert from an existing detection')
def alerts_open_from_detection(request: Request, response: Response) -> Any:
    try:
        result = with_auth_schema_json(lambda: open_alert_from_detection(request))
    except HTTPException:
        raise
    except Exception as exc:
        # Requirement 6: surface the exact backend error on a 500 so the failure is
        # diagnosable instead of being masked as a generic / network error.
        logger.error('open_alert_failed method=%s error_type=%s error=%s', request.method, exc.__class__.__name__, exc)
        raise HTTPException(status_code=500, detail=f'Open alert failed: {exc.__class__.__name__}: {exc}') from None

    # Map the canonical status to a truthful HTTP code (requirement 6):
    #   created       -> 201 (a new alert row was inserted)
    #   already_exists -> 409 (an alert already exists for this detection; no duplicate)
    #   suppressed / no_detection -> 200 (nothing to create)
    if isinstance(result, dict):
        status_value = str(result.get('status') or '')
        if status_value == 'created':
            response.status_code = 201
        elif status_value == 'already_exists':
            response.status_code = 409
    return result


@app.get('/alerts', summary='List alerts')
def alerts_list(request: Request, severity: str | None = None, module: str | None = None, target_id: str | None = None, status_value: str | None = None, source: str | None = None, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    try:
        return with_auth_schema_json(lambda: list_alerts(request, severity=severity, module=module, target_id=target_id, status_value=status_value, source=source, limit=limit, offset=offset))
    except HTTPException:
        raise
    except Exception as exc:
        logger.error('monitoring_list_failed path=/alerts method=%s error_type=%s error=%s', request.method, exc.__class__.__name__, exc)
        raise HTTPException(status_code=500, detail='Unable to list alerts at this time.') from None


@app.get('/detections', summary='List detections')
def detections_list(
    request: Request,
    limit: int = 50,
    severity: str | None = None,
    status_value: str | None = None,
    evidence_source: str | None = None,
    monitored_system_id: str | None = None,
    protected_asset_id: str | None = None,
) -> dict[str, Any]:
    try:
        return with_auth_schema_json(
            lambda: list_detections(
                request,
                limit=limit,
                severity=severity,
                status_value=status_value,
                evidence_source=evidence_source,
                monitored_system_id=monitored_system_id,
                protected_asset_id=protected_asset_id,
            )
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error('monitoring_list_failed path=/detections method=%s error_type=%s error=%s', request.method, exc.__class__.__name__, exc)
        raise HTTPException(status_code=500, detail='Unable to list detections at this time.') from None


@app.get('/detections/{detection_id}', summary='Detection detail')
def detections_get(detection_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: get_detection(detection_id, request))


@app.get('/detections/{detection_id}/evidence', summary='Detection evidence detail')
def detections_evidence_get(detection_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: get_detection_evidence(detection_id, request))


@app.get('/alerts/{alert_id}', summary='Alert detail')
def alerts_get(alert_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: get_alert(alert_id, request))


@app.patch('/alerts/{alert_id}', summary='Acknowledge or resolve alert')
def alerts_patch(alert_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: patch_alert(alert_id, payload, request))


@app.post('/alerts/{alert_id}/acknowledge', summary='Acknowledge alert')
def alerts_acknowledge(alert_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: patch_alert(alert_id, {'status': 'acknowledged'}, request))


@app.post('/alerts/{alert_id}/resolve', summary='Resolve alert')
def alerts_resolve(alert_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: patch_alert(alert_id, {'status': 'resolved'}, request))


@app.post('/alerts/{alert_id}/escalate', summary='Escalate alert to incident')
def alerts_escalate(alert_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: escalate_alert_to_incident(alert_id, payload, request))


@app.get('/alerts/{alert_id}/evidence', summary='List alert evidence payload')
def alerts_evidence(alert_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: list_alert_evidence(alert_id, request))


@app.post('/alerts/suppressions', summary='Create alert suppression rule')
def alerts_suppressions_create(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: create_alert_suppression(payload, request))


@app.get('/incidents', summary='List incidents')
def incidents_list(request: Request, severity: str | None = None, target_id: str | None = None, status_value: str | None = None, assignee_user_id: str | None = None, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    try:
        return with_auth_schema_json(lambda: list_incidents(request, severity=severity, target_id=target_id, status_value=status_value, assignee_user_id=assignee_user_id, limit=limit, offset=offset))
    except HTTPException:
        raise
    except Exception as exc:
        logger.error('monitoring_list_failed path=/incidents method=%s error_type=%s error=%s', request.method, exc.__class__.__name__, exc)
        raise HTTPException(status_code=500, detail='Unable to list incidents at this time.') from None


@app.get('/incidents/{incident_id}', summary='Get incident detail')
def incidents_get(incident_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: get_incident(incident_id, request))


@app.patch('/incidents/{incident_id}', summary='Update incident status/owner')
def incidents_patch(incident_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: patch_incident(incident_id, payload, request))

@app.get('/incidents/{incident_id}/timeline', summary='List incident timeline entries')
def incidents_timeline_list(incident_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: list_incident_timeline(incident_id, request))


@app.post('/incidents/{incident_id}/timeline', summary='Append incident timeline note')
def incidents_timeline_create(incident_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: append_incident_timeline_note(incident_id, payload, request))


@app.get('/history/actions', summary='List action history for alert/incident workflows')
def history_actions(request: Request, object_type: str | None = None, object_id: str | None = None, limit: int = 200) -> dict[str, Any]:
    return with_auth_schema_json(lambda: list_action_history(request, object_type=object_type, object_id=object_id, limit=limit))


@app.post('/history/actions', summary='Create an action history entry')
def history_actions_create(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: create_action_history_entry(payload, request))


@app.get('/enforcement/actions', summary='List enforcement actions with capability and execution state details')
def enforcement_actions_list(request: Request, incident_id: str | None = None, action_id: str | None = None, alert_id: str | None = None, status_value: str | None = None, limit: int = 200) -> dict[str, Any]:
    return with_auth_schema_json(lambda: _normalize_action_list_route_response(list_enforcement_actions(request, incident_id=incident_id, action_id=action_id, alert_id=alert_id, status_value=status_value, limit=limit)))


@app.get('/response/actions', summary='List response actions')
def response_actions_list(request: Request, incident_id: str | None = None, action_id: str | None = None, alert_id: str | None = None, status_value: str | None = None, limit: int = 200) -> dict[str, Any]:
    return with_auth_schema_json(lambda: _normalize_action_list_route_response(list_enforcement_actions(request, incident_id=incident_id, action_id=action_id, alert_id=alert_id, status_value=status_value, limit=limit)))


@app.get('/response/action-capabilities', summary='List response action capabilities and live execution routing for the active workspace')
def response_action_capabilities(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: list_response_action_capabilities(request))


@app.post('/enforcement/actions', summary='Plan a workspace enforcement action')
def enforcement_actions_create(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: _normalize_action_route_response(create_enforcement_action(payload, request)))

@app.post('/response/actions', summary='Plan a workspace response action')
def response_actions_create(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: _normalize_action_route_response(create_enforcement_action(payload, request)))


@app.post('/enforcement/actions/{action_id}/approve', summary='Approve a planned enforcement action')
def enforcement_actions_approve(action_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: _normalize_action_route_response(approve_enforcement_action(action_id, request)))

@app.post('/response/actions/{action_id}/approve', summary='Approve a planned response action')
def response_actions_approve(action_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: _normalize_action_route_response(approve_enforcement_action(action_id, request)))


@app.post('/enforcement/actions/{action_id}/execute', summary='Execute an approved enforcement action')
def enforcement_actions_execute(action_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: _normalize_action_route_response(execute_enforcement_action(action_id, request)))

@app.post('/response/actions/{action_id}/execute', summary='Execute an approved response action')
def response_actions_execute(action_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: _normalize_action_route_response(execute_enforcement_action(action_id, request)))


@app.post('/enforcement/actions/{action_id}/rollback', summary='Rollback enforcement action by creating a compensating action')
def enforcement_actions_rollback(action_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: _normalize_action_route_response(rollback_enforcement_action(action_id, request)))

@app.post('/response/actions/{action_id}/rollback', summary='Rollback response action by creating a compensating action')
def response_actions_rollback(action_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: _normalize_action_route_response(rollback_enforcement_action(action_id, request)))


@app.post('/incidents/{incident_id}/response-actions/recommend', summary='Create or return a recommended response action for an incident')
def incident_recommend_response_action(incident_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: recommend_response_action_for_incident(incident_id, request))


# --- Evidence-grounded AI incident investigation (policy-controlled) ---------
@app.post('/incidents/{incident_id}/ai-triage', summary='Queue an evidence-grounded AI triage job for an incident')
def incident_ai_triage_create(incident_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: ai_triage.request_triage(incident_id, request))


@app.get('/incidents/{incident_id}/ai-triage', summary='Get the latest AI triage job, structured result, and recommendations')
def incident_ai_triage_get(incident_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: ai_triage.get_triage(incident_id, request))


@app.post('/incidents/{incident_id}/ai-triage/regenerate', summary='Regenerate AI triage (requires a reason)')
def incident_ai_triage_regenerate(incident_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: ai_triage.regenerate_triage(incident_id, payload, request))


@app.get('/incidents/{incident_id}/ai-report', summary='Get the AI incident report (machine JSON + human markdown)')
def incident_ai_report_get(incident_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: ai_triage.get_report(incident_id, request))


@app.post('/incidents/{incident_id}/recommendations/{recommendation_id}/approve', summary='Approve an AI recommendation (records decision; does not execute)')
def incident_recommendation_approve(incident_id: str, recommendation_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: ai_triage.approve_recommendation(incident_id, recommendation_id, payload, request))


@app.post('/incidents/{incident_id}/recommendations/{recommendation_id}/reject', summary='Reject an AI recommendation')
def incident_recommendation_reject(incident_id: str, recommendation_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: ai_triage.reject_recommendation(incident_id, recommendation_id, payload, request))


@app.get('/incidents/ai-triage/usage', summary='Workspace AI triage usage and estimated cost')
def incident_ai_triage_usage(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: ai_triage.usage_metrics(request))


@app.post('/response/actions/{action_id}/simulate', summary='Mark response action as simulated (dry-run, no on-chain effect)')
def response_action_simulate(action_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: simulate_response_action(action_id, request))


@app.post('/response/actions/{action_id}/evidence-package', summary='Create evidence package from response action (idempotent)')
def response_action_evidence_package(action_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: create_evidence_package_from_response_action(action_id, request))


@app.post('/exports/history', summary='Export analysis history')
def exports_history(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: create_export_job('history', payload, request))


@app.post('/exports/alerts', summary='Export alerts')
def exports_alerts(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: create_export_job('alerts', payload, request))


@app.post('/exports/findings', summary='Export findings')
def exports_findings(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: create_export_job('findings', payload, request))


@app.post('/exports/report', summary='Export report')
def exports_report(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: create_export_job('report', payload, request))


@app.post('/exports/feature1-evidence', summary='Export Feature 1 asset evidence bundle')
def exports_feature1_evidence(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: create_export_job('feature1_evidence', payload, request))


@app.post('/exports/proof-bundle', summary='Export incident proof bundle')
def exports_proof_bundle(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: create_proof_bundle_export(payload, request))


@app.post('/exports/incident-report', summary='Export incident investigation report')
def exports_incident_report(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: create_incident_report_export(payload, request))


@app.get('/events', summary='List workspace audit events')
def events_list(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: list_audit_events(request))


@app.get('/exports', summary='List workspace exports')
def exports_list(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: list_exports(request))


@app.get('/exports/{export_id}', summary='Export detail')
def exports_get(export_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: get_export(export_id, request))


@app.get('/exports/{export_id}/download', summary='Download export artifact')
def exports_download(export_id: str, request: Request) -> Response:
    content, filename = with_auth_schema_json(lambda: get_export_artifact_content(export_id, request))
    media_type = 'application/json' if filename.endswith('.json') else 'text/csv'
    return Response(content=content, media_type=media_type, headers={'Content-Disposition': f'attachment; filename={filename}'})


@app.get('/metrics', include_in_schema=False)
def metrics() -> Response:
    subscriber_snapshot = alert_stream.subscriber_health()
    delivery_snapshot = alert_delivery_health()
    outbox_depth = delivery_snapshot.get('outbox') or {}
    supplemental = [
        f'decoda_stream_connections_active {_SSE_CONNECTION_COUNT}',
        f'decoda_auth_failures_total {_AUTH_FAILURE_COUNT}',
        f'decoda_alerts_published_total {_ALERTS_PUBLISHED_COUNT}',
        f"decoda_alert_stream_subscribers_connected {subscriber_snapshot['connected_subscribers']}",
        f"decoda_alert_stream_reconnects_total {subscriber_snapshot['reconnects_total']}",
        f"decoda_alert_outbox_pending {outbox_depth.get('pending') or 0}",
        f"decoda_alert_bus_pending_delivery {outbox_depth.get('published') or 0}",
        f"decoda_alert_dead_letter_total {outbox_depth.get('dead_letter') or 0}",
    ]
    return Response(prometheus_metrics() + '\n'.join(supplemental) + '\n', media_type='text/plain; version=0.0.4')


@app.get('/integrations/notifications', summary='List workspace notification destinations and policies')
def integrations_notifications_list(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: list_notification_configuration(request))


@app.post('/integrations/notifications/destinations', summary='Create notification destination')
def integrations_notifications_destination_create(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: create_notification_destination(payload, request))


@app.post('/integrations/notifications/policies', summary='Create notification policy')
def integrations_notifications_policy_create(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: upsert_notification_policy(payload, request))


@app.patch('/integrations/notifications/policies/{policy_id}', summary='Update notification policy')
def integrations_notifications_policy_patch(policy_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: upsert_notification_policy(payload, request, policy_id))


@app.get('/integrations/notifications/attempts', summary='List notification delivery attempts')
def integrations_notifications_attempts(request: Request, limit: int = 100) -> dict[str, Any]:
    return with_auth_schema_json(lambda: list_notification_attempts(request, limit))


@app.post('/integrations/notifications/attempts/{attempt_id}/acknowledge', summary='Acknowledge a notification')
def integrations_notifications_acknowledge(attempt_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: acknowledge_notification_attempt(attempt_id, payload, request))


@app.get('/integrations/webhooks', summary='List outbound integration webhooks')
def integrations_webhooks_list(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: list_webhooks(request))


@app.post('/integrations/webhooks', summary='Create outbound integration webhook')
def integrations_webhooks_create(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: create_webhook(payload, request))


@app.patch('/integrations/webhooks/{webhook_id}', summary='Update outbound integration webhook')
def integrations_webhooks_patch(webhook_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: update_webhook(webhook_id, payload, request))


@app.post('/integrations/webhooks/{webhook_id}/rotate-secret', summary='Rotate integration webhook secret')
def integrations_webhooks_rotate(webhook_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: rotate_webhook_secret(webhook_id, request))


@app.get('/integrations/webhooks/{webhook_id}/deliveries', summary='List integration webhook deliveries')
def integrations_webhooks_deliveries(webhook_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: list_webhook_deliveries(webhook_id, request))


@app.get('/integrations/slack', summary='List workspace Slack integrations')
def integrations_slack_list(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: list_slack_integrations(request))


@app.post('/integrations/slack', summary='Create workspace Slack integration')
def integrations_slack_create(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: create_slack_integration(payload, request))


@app.post('/integrations/slack/oauth/start', summary='Start Slack OAuth install')
def integrations_slack_oauth_start(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: begin_slack_oauth_install(payload, request))


@app.get('/integrations/slack/oauth/callback', summary='Complete Slack OAuth install')
def integrations_slack_oauth_callback(code: str = '', state: str = '') -> Response:
    result = with_auth_schema_json(lambda: complete_slack_oauth_install(state_token=state, code=code))
    redirect_after = str(result.get('redirect_after_install') or '/integrations')
    suffix = '&' if '?' in redirect_after else '?'
    target = f'{redirect_after}{suffix}slack_oauth=connected'
    return RedirectResponse(url=target, status_code=302)


@app.patch('/integrations/slack/{integration_id}', summary='Update workspace Slack integration')
def integrations_slack_patch(integration_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: update_slack_integration(integration_id, payload, request))


@app.delete('/integrations/slack/{integration_id}', summary='Delete workspace Slack integration')
def integrations_slack_delete(integration_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: delete_slack_integration(integration_id, request))


@app.post('/integrations/slack/{integration_id}/test', summary='Queue Slack test notification')
def integrations_slack_test(integration_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: test_slack_integration(integration_id, request))


@app.get('/integrations/slack/{integration_id}/deliveries', summary='List Slack delivery attempts')
def integrations_slack_deliveries(integration_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: list_slack_deliveries(integration_id, request))


@app.get('/system/integrations/health', summary='Integration health diagnostics')
def system_integrations_health(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: get_integration_health(request))


@app.get('/system/readiness', summary='Workspace readiness diagnostics with gates, reasons, and dependency checks')
def system_workspace_readiness(request: Request) -> dict[str, Any]:
    def _workspace_snapshot() -> dict[str, Any]:
        readiness = get_workspace_readiness(request)
        delivery = alert_delivery_health()
        delivery_ready = bool(delivery.get('ready'))
        checks = list(readiness.get('checks') or [])
        checks.append({
            'key': 'durable_alert_delivery',
            'pass': delivery_ready,
            'blocking': True,
            'reason_code': None if delivery_ready else 'durable_alert_delivery_unavailable',
            'reason': 'Shared event bus, outbox publisher, and stream consumer are healthy.' if delivery_ready else 'Shared event bus, outbox publisher, or stream consumer is unavailable.',
        })
        readiness['checks'] = checks
        readiness.setdefault('dependency_checks', {})['durable_alert_delivery'] = {
            'pass': delivery_ready,
            'blocking': True,
            'reason_code': None if delivery_ready else 'durable_alert_delivery_unavailable',
        }
        readiness['alert_delivery'] = delivery
        if not delivery_ready:
            readiness['status'] = 'fail'
            readiness['enterprise_procurement_ready'] = False
            reasons = list(readiness.get('enterprise_procurement_blocking_reason_codes') or [])
            if 'durable_alert_delivery_unavailable' not in reasons:
                reasons.append('durable_alert_delivery_unavailable')
            readiness['enterprise_procurement_blocking_reason_codes'] = reasons
            blocking_reasons = list(readiness.get('blocking_failure_reason_codes') or [])
            if 'durable_alert_delivery_unavailable' not in blocking_reasons:
                blocking_reasons.append('durable_alert_delivery_unavailable')
            readiness['blocking_failure_reason_codes'] = blocking_reasons
            blocking_failures = list(readiness.get('blocking_failures') or [])
            if 'Shared event bus, durable worker, or outbox consumer is unavailable.' not in blocking_failures:
                blocking_failures.append('Shared event bus, durable worker, or outbox consumer is unavailable.')
            readiness['blocking_failures'] = blocking_failures
        return readiness
    return with_auth_schema_json(_workspace_snapshot)

@app.get('/admin/readiness', summary='Internal admin production readiness snapshot')
def admin_readiness(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: get_admin_readiness(request))


@app.get('/system/recovery-drills', summary='Recovery drill schedules, results, and enterprise gate status')
def system_recovery_drills(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: get_recovery_drill_status(request))


@app.post('/system/recovery-drills/{run_type}/schedule', summary='Schedule a recovery drill for the worker')
def system_schedule_recovery_drill(run_type: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: schedule_recovery_drill(run_type, request))


@app.post('/system/integrations/test-email', summary='Send integration test email')
def system_integrations_test_email(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: test_integration_email(request))


@app.post('/system/integrations/test-slack', summary='Send integration test Slack message')
def system_integrations_test_slack(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: test_integration_slack(payload, request))


@app.get('/integrations/routing', summary='List workspace alert routing rules')
def integrations_routing_list(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: list_alert_routing_rules(request))


@app.put('/integrations/routing/{channel_type}', summary='Create or update a channel routing rule')
def integrations_routing_upsert(channel_type: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: upsert_alert_routing_rule(channel_type, payload, request))


@app.get('/templates', summary='List onboarding templates')
def templates_list() -> dict[str, Any]:
    return with_auth_schema_json(list_templates)


@app.post('/templates/{template_id}/apply', summary='Apply onboarding template to current workspace')
def templates_apply(template_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: apply_template(template_id, request))

@app.get('/pilot/history', summary='Workspace-scoped persisted live-mode history')
def pilot_history(request: Request, limit: int = 25) -> dict[str, Any]:
    return with_auth_schema_json(lambda: build_history_response(request, limit=limit))


@app.get('/history', summary='Workspace history')
def history_list(request: Request, limit: int = 25) -> dict[str, Any]:
    return with_auth_schema_json(lambda: build_history_response(request, limit=limit))


@app.get('/history/{history_id}', summary='History detail')
def history_get(history_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: get_history_item(history_id, request))


@app.post('/findings/{finding_id}/decision', summary='Create a finding decision')
def findings_decision(finding_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: create_finding_decision(finding_id, payload, request))


@app.post('/findings/{finding_id}/actions', summary='Create a finding action item')
def findings_action_create(finding_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: create_finding_action(finding_id, payload, request))


@app.patch('/actions/{action_id}', summary='Update finding action item')
def findings_action_update(action_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: patch_finding_action(action_id, payload, request))


@app.get('/actions', summary='List finding action items')
def findings_action_list(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: list_finding_actions(request))


@app.get('/decisions', summary='List finding decisions')
def findings_decision_list(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: list_finding_decisions(request))


def _persist_live_analysis(request: Request, payload: dict[str, Any], response_payload: dict[str, Any], *, analysis_type: str, service_name: str, title: str) -> dict[str, Any]:
    if not live_mode_enabled():
        raise HTTPException(status_code=503, detail='Live pilot mode is not enabled.')
    if 'authorization' not in request.headers:
        raise HTTPException(status_code=401, detail='Authorization is required for live pilot actions.')
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        analysis_run_id = persist_analysis_run(
            connection,
            workspace_id=workspace_context['workspace_id'],
            user_id=user['id'],
            analysis_type=analysis_type,
            service_name=service_name,
            title=title,
            status_value='completed',
            request_payload=payload,
            response_payload=response_payload,
            request=request,
        )
        maybe_insert_alert(
            connection,
            workspace_id=workspace_context['workspace_id'],
            user_id=user['id'],
            analysis_run_id=analysis_run_id,
            alert_type=analysis_type,
            title=title,
            response_payload=response_payload,
        )
        connection.commit()
        return {
            **response_payload,
            'pilot_saved': True,
            'analysis_run_id': analysis_run_id,
            'workspace': workspace_context['workspace'],
        }


@app.post('/pilot/threat/analyze/contract', summary='Run and persist a contract threat analysis for live mode')
def pilot_threat_analyze_contract(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    normalized, _ = normalize_threat_payload('contract', payload, include_original=True)
    response = _require_threat_response('contract', normalized)
    return _persist_live_analysis(request, normalized, response, analysis_type='threat_contract', service_name='threat-engine', title='Threat contract analysis')


@app.post('/pilot/threat/analyze/transaction', summary='Run and persist a transaction threat analysis for live mode')
def pilot_threat_analyze_transaction(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    normalized, _ = normalize_threat_payload('transaction', payload, include_original=True)
    response = _require_threat_response('transaction', normalized)
    return _persist_live_analysis(request, normalized, response, analysis_type='threat_transaction', service_name='threat-engine', title='Threat transaction analysis')


@app.post('/pilot/threat/analyze/market', summary='Run and persist a market threat analysis for live mode')
def pilot_threat_analyze_market(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    normalized, _ = normalize_threat_payload('market', payload, include_original=True)
    response = _require_threat_response('market', normalized)
    return _persist_live_analysis(request, normalized, response, analysis_type='threat_market', service_name='threat-engine', title='Threat market analysis')


@app.post('/pilot/compliance/screen/transfer', summary='Run and persist a transfer compliance screen for live mode')
def pilot_compliance_screen_transfer(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    response = proxy_compliance('screen/transfer', payload) or fallback_transfer_screening(payload)
    return _persist_live_analysis(request, payload, response, analysis_type='compliance_transfer', service_name='compliance-service', title='Compliance transfer screening')


@app.post('/pilot/compliance/screen/residency', summary='Run and persist a residency compliance screen for live mode')
def pilot_compliance_screen_residency(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    response = proxy_compliance('screen/residency', payload) or fallback_residency_screening(payload)
    return _persist_live_analysis(request, payload, response, analysis_type='compliance_residency', service_name='compliance-service', title='Compliance residency screening')


@app.post('/pilot/compliance/governance/actions', summary='Create and persist a governance action for live mode')
def pilot_compliance_governance_action(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    response = proxy_compliance('governance/actions', payload) or fallback_governance_action(payload)
    with pg_connection() as connection:
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        analysis_run_id = persist_analysis_run(
            connection,
            workspace_id=workspace_context['workspace_id'],
            user_id=user['id'],
            analysis_type='governance_action',
            service_name='compliance-service',
            title='Governance action',
            status_value='completed',
            request_payload=payload,
            response_payload=response,
            request=request,
        )
        governance_action_id = create_governance_action_record(
            connection,
            workspace_id=workspace_context['workspace_id'],
            user_id=user['id'],
            analysis_run_id=analysis_run_id,
            payload=payload,
            response_payload=response,
        )
        maybe_insert_alert(
            connection,
            workspace_id=workspace_context['workspace_id'],
            user_id=user['id'],
            analysis_run_id=analysis_run_id,
            alert_type='governance_action',
            title=str(response.get('action_type') or payload.get('action_type') or 'Governance action'),
            response_payload=response,
        )
        log_audit(connection, action='governance.action', entity_type='governance_action', entity_id=governance_action_id, request=request, user_id=user['id'], workspace_id=workspace_context['workspace_id'], metadata={'target_id': response.get('target_id') or payload.get('target_id')})
        connection.commit()
    return {**response, 'pilot_saved': True, 'analysis_run_id': analysis_run_id, 'governance_action_id': governance_action_id, 'workspace': workspace_context['workspace']}


@app.post('/pilot/resilience/reconcile/state', summary='Run and persist a reconciliation analysis for live mode')
def pilot_resilience_reconcile_state(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    response = proxy_resilience_post('reconcile/state', payload) or fallback_reconcile_state(payload)
    return _persist_live_analysis(request, payload, response, analysis_type='resilience_reconcile', service_name='reconciliation-service', title='Resilience reconciliation')


@app.post('/pilot/resilience/backstop/evaluate', summary='Run and persist a backstop analysis for live mode')
def pilot_resilience_backstop_evaluate(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    response = proxy_resilience_post('backstop/evaluate', payload) or fallback_backstop_evaluate(payload)
    return _persist_live_analysis(request, payload, response, analysis_type='resilience_backstop', service_name='reconciliation-service', title='Resilience backstop evaluation')


@app.post('/pilot/resilience/incidents/record', summary='Create and persist a resilience incident for live mode')
def pilot_resilience_record_incident(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    response = proxy_resilience_post('incidents/record', payload) or fallback_incident_record(payload)
    with pg_connection() as connection:
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        analysis_run_id = persist_analysis_run(
            connection,
            workspace_id=workspace_context['workspace_id'],
            user_id=user['id'],
            analysis_type='resilience_incident',
            service_name='reconciliation-service',
            title='Resilience incident',
            status_value='completed',
            request_payload=payload,
            response_payload=response,
            request=request,
        )
        incident_id = create_incident_record(
            connection,
            workspace_id=workspace_context['workspace_id'],
            user_id=user['id'],
            analysis_run_id=analysis_run_id,
            payload=payload,
            response_payload=response,
        )
        maybe_insert_alert(
            connection,
            workspace_id=workspace_context['workspace_id'],
            user_id=user['id'],
            analysis_run_id=analysis_run_id,
            alert_type='resilience_incident',
            title=str(response.get('event_type') or payload.get('event_type') or 'Resilience incident'),
            response_payload=response,
        )
        log_audit(connection, action='incident.record', entity_type='incident', entity_id=incident_id, request=request, user_id=user['id'], workspace_id=workspace_context['workspace_id'], metadata={'severity': response.get('severity') or payload.get('severity')})
        connection.commit()
    return {**response, 'pilot_saved': True, 'analysis_run_id': analysis_run_id, 'incident_id': incident_id, 'workspace': workspace_context['workspace']}


def build_risk_dashboard_queue() -> list[dict[str, Any]]:
    sample_request = load_json_file(RISK_ENGINE_DATA_DIR, 'sample_risk_request.json', DEFAULT_RISK_SAMPLE_REQUEST)
    suspicious_events = load_json_file(RISK_ENGINE_DATA_DIR, 'suspicious_market_events.json', DEFAULT_SUSPICIOUS_MARKET_EVENTS)
    normal_events = load_json_file(RISK_ENGINE_DATA_DIR, 'normal_market_events.json', DEFAULT_NORMAL_MARKET_EVENTS)

    definitions = [
        {
            'id': 'txn-001',
            'label': 'Flash-loan router rebalance',
            'request': build_flash_loan_request(sample_request, suspicious_events),
            'fallback': {
                'risk_score': 100,
                'recommendation': 'BLOCK',
                'explanation': 'Aggregate score 100 produced recommendation BLOCK. Primary drivers: low-level liquidity drain, flash-loan routing, and weak wallet reputation.',
                'triggered_rules': [
                    {'rule_id': 'runtime:liquidity-drain', 'severity': 'critical', 'summary': 'Observed recent liquidity contraction matches flash-loan drain behavior.'},
                    {'rule_id': 'pre:wallet-reputation', 'severity': 'high', 'summary': 'Wallet reputation is weak relative to defensive transaction policy.'},
                    {'rule_id': 'market:cancel-burst', 'severity': 'medium', 'summary': 'Elevated order cancellation ratio suggests quote stuffing or spoofing.'},
                ],
            },
        },
        {
            'id': 'txn-002',
            'label': 'Treasury settlement transfer',
            'request': build_allow_request(sample_request, normal_events),
            'fallback': {
                'risk_score': 6,
                'recommendation': 'ALLOW',
                'explanation': 'Known-safe treasury settlement has verified contract metadata and no defensive heuristics triggered.',
                'triggered_rules': [],
            },
        },
        {
            'id': 'txn-003',
            'label': 'Proxy rebalance multicall',
            'request': build_review_request(sample_request, normal_events),
            'fallback': {
                'risk_score': 52,
                'recommendation': 'REVIEW',
                'explanation': 'Aggregate score 52 produced recommendation REVIEW. Primary drivers: privileged arguments, unaudited proxy behavior, and weak wallet reputation.',
                'triggered_rules': [
                    {'rule_id': 'pre:wallet-reputation', 'severity': 'high', 'summary': 'Wallet reputation is weak relative to defensive transaction policy.'},
                    {'rule_id': 'pre:privileged-args', 'severity': 'medium', 'summary': 'Call arguments include privileged control fields.'},
                    {'rule_id': 'static:unaudited-proxy', 'severity': 'medium', 'summary': 'Proxy contract without audits increases implementation-switch risk.'},
                ],
            },
        },
        {
            'id': 'txn-004',
            'label': 'Mixer withdrawal sweep',
            'request': build_mixer_request(sample_request, suspicious_events),
            'fallback': {
                'risk_score': 93,
                'recommendation': 'BLOCK',
                'explanation': 'Mixer-associated sweep touches laundering indicators and elevated market anomalies, so the engine recommends BLOCK.',
                'triggered_rules': [
                    {'rule_id': 'static:mixer-category', 'severity': 'critical', 'summary': 'Contract category is associated with obfuscation or laundering workflows.'},
                    {'rule_id': 'pre:high-value', 'severity': 'high', 'summary': 'Transaction notional exceeds the Phase 1 high-value threshold.'},
                    {'rule_id': 'market:spoofing-reversal', 'severity': 'high', 'summary': 'Price moved sharply and reverted quickly, consistent with spoofing pressure.'},
                ],
            },
        },
    ]

    queue: list[dict[str, Any]] = []
    for offset, definition in enumerate(definitions):
        evaluation = evaluate_live_risk(definition['request'])
        live_data = evaluation is not None
        result = evaluation or definition['fallback']
        queue.append(
            {
                'id': definition['id'],
                'label': definition['label'],
                'request': definition['request'],
                'evaluation': result,
                'live_data': live_data,
                'updated_at': iso_timestamp(offset),
            }
        )
    return queue


def build_flash_loan_request(sample_request: dict[str, Any], suspicious_events: list[dict[str, Any]]) -> dict[str, Any]:
    request = deepcopy(sample_request)
    request['recent_market_events'] = suspicious_events
    request['transaction_payload']['metadata']['queue_position'] = 1
    return request


def build_allow_request(sample_request: dict[str, Any], normal_events: list[dict[str, Any]]) -> dict[str, Any]:
    request = deepcopy(sample_request)
    request['transaction_payload'].update(
        {
            'tx_hash': '0xphase1allow',
            'from_address': '0x5555555555555555555555555555555555555555',
            'to_address': '0x6666666666666666666666666666666666666666',
            'value': 125000.0,
            'gas_price': 18.0,
            'token_transfers': [{'token': 'USTB', 'amount': 125000}],
            'metadata': {'contains_flash_loan_hop': False, 'entrypoint': 'treasury-settlement'},
        }
    )
    request['decoded_function_call'].update(
        {
            'function_name': 'settle',
            'contract_name': 'TreasurySettlement',
            'arguments': {'beneficiary': '0x7777777777777777777777777777777777777777', 'amount': 125000},
            'selectors': ['0xfeedbeef'],
        }
    )
    request['wallet_reputation'].update(
        {
            'address': '0x5555555555555555555555555555555555555555',
            'score': 92,
            'prior_flags': 0,
            'account_age_days': 640,
            'kyc_verified': True,
            'known_safe': True,
            'recent_counterparties': 4,
            'metadata': {'desk': 'treasury-ops'},
        }
    )
    request['contract_metadata'].update(
        {
            'address': '0x6666666666666666666666666666666666666666',
            'contract_name': 'TreasurySettlement',
            'verified_source': True,
            'proxy': False,
            'created_days_ago': 410,
            'audit_count': 3,
            'categories': ['treasury', 'settlement'],
            'static_flags': {},
            'metadata': {'review_status': 'approved'},
        }
    )
    request['recent_market_events'] = normal_events
    return request


def build_review_request(sample_request: dict[str, Any], normal_events: list[dict[str, Any]]) -> dict[str, Any]:
    request = deepcopy(sample_request)
    request['transaction_payload'].update(
        {
            'tx_hash': '0xphase1review',
            'from_address': '0x8888888888888888888888888888888888888888',
            'to_address': '0x9999999999999999999999999999999999999999',
            'value': 420000.0,
            'gas_price': 31.0,
            'token_transfers': [
                {'token': 'USTB', 'amount': 300000},
                {'token': 'USDC', 'amount': 120000},
            ],
            'metadata': {'contains_flash_loan_hop': False, 'entrypoint': 'rebalance-router'},
        }
    )
    request['decoded_function_call'].update(
        {
            'function_name': 'multicall',
            'contract_name': 'ProxyPortfolioManager',
            'arguments': {
                'owner': '0x1010101010101010101010101010101010101010',
                'router': '0x1212121212121212121212121212121212121212',
                'steps': 2,
            },
            'selectors': ['0x5ae401dc'],
        }
    )
    request['wallet_reputation'].update(
        {
            'address': '0x8888888888888888888888888888888888888888',
            'score': 32,
            'prior_flags': 1,
            'account_age_days': 38,
            'kyc_verified': False,
            'known_safe': False,
            'recent_counterparties': 14,
            'metadata': {'desk': 'external-rebalancer'},
        }
    )
    request['contract_metadata'].update(
        {
            'address': '0x9999999999999999999999999999999999999999',
            'contract_name': 'ProxyPortfolioManager',
            'verified_source': True,
            'proxy': True,
            'created_days_ago': 180,
            'audit_count': 0,
            'categories': ['portfolio', 'router'],
            'static_flags': {'obfuscated_storage': True},
            'metadata': {'upgrade_notice': 'pending governance review'},
        }
    )
    request['recent_market_events'] = normal_events
    return request


def build_mixer_request(sample_request: dict[str, Any], suspicious_events: list[dict[str, Any]]) -> dict[str, Any]:
    request = deepcopy(sample_request)
    request['transaction_payload'].update(
        {
            'tx_hash': '0xphase1block',
            'from_address': '0x1313131313131313131313131313131313131313',
            'to_address': '0x1414141414141414141414141414141414141414',
            'value': 1450000.0,
            'gas_price': 64.0,
            'token_transfers': [
                {'token': 'USTB', 'amount': 800000},
                {'token': 'USDC', 'amount': 350000},
                {'token': 'DAI', 'amount': 300000},
                {'token': 'WETH', 'amount': 180},
            ],
            'metadata': {'contains_flash_loan_hop': False, 'entrypoint': 'withdrawal-sweeper'},
        }
    )
    request['decoded_function_call'].update(
        {
            'function_name': 'withdrawAll',
            'contract_name': 'PrivacyMixerVault',
            'arguments': {'admin': '0x1515151515151515151515151515151515151515', 'receiver': '0x1616161616161616161616161616161616161616'},
            'selectors': ['0xdeadc0de'],
        }
    )
    request['wallet_reputation'].update(
        {
            'address': '0x1313131313131313131313131313131313131313',
            'score': 28,
            'prior_flags': 2,
            'account_age_days': 11,
            'kyc_verified': False,
            'known_safe': False,
            'recent_counterparties': 31,
            'metadata': {'watchlist': 'mixer-monitor'},
        }
    )
    request['contract_metadata'].update(
        {
            'address': '0x1414141414141414141414141414141414141414',
            'contract_name': 'PrivacyMixerVault',
            'verified_source': False,
            'proxy': False,
            'created_days_ago': 9,
            'audit_count': 0,
            'categories': ['mixer', 'vault'],
            'static_flags': {'selfdestruct_enabled': True, 'hidden_owner': True},
            'metadata': {'screening_status': 'escalated'},
        }
    )
    request['recent_market_events'] = suspicious_events
    return request


def attach_dependency_diagnostics(payload: dict[str, Any], dependency_name: str, *, fallback_reason: str | None = None) -> dict[str, Any]:
    runtime = DEPENDENCY_RUNTIME_STATUS.get(dependency_name, {})
    # The SQLite dev registry (phase1_local.dev_support.load_service) is only available in
    # local/dev mode. In production DATABASE_URL is a remote Postgres URL, so calling
    # load_service() here makes resolve_sqlite_path() raise RuntimeError. Because this helper
    # runs inside mark_live_payload() -> proxy_threat(), that crash made live threat analysis
    # return None, surfacing as analysis_unavailable:live_engine_unavailable and rolling back
    # detected wallet-transfer telemetry. Skip the SQLite lookup outside local/dev mode.
    registry_status = load_service(dependency_service_name(dependency_name)) if _is_local_dev_mode() else None
    payload['diagnostics'] = {
        'dependency': dependency_service_name(dependency_name),
        'selected_mode': runtime.get('selected_mode', dependency_mode(dependency_name)),
        'last_used_mode': runtime.get('last_used_mode', dependency_mode(dependency_name)),
        'last_error': runtime.get('last_error'),
        'registry_status': registry_status,
        'payload_source': payload.get('source'),
        'degraded': payload.get('degraded'),
        'fallback_reason': fallback_reason,
    }
    return payload


THREAT_DASHBOARD_LIVE_MESSAGE = 'Threat dashboard is driven by deterministic weighted rules so each score remains explainable and demoable.'
THREAT_DASHBOARD_LIVE_CARD_DETAILS = {
    'Threat score': 'Contract scan composite score from deterministic rules.',
    'Active alerts': 'Critical and high-confidence exploit or anomaly detections.',
    'Blocked / reviewed': 'Action decisions produced by the explainable scoring layer.',
    'Market anomaly avg': 'Average anomaly score across bundled treasury-token scenarios.',
}
THREAT_FALLBACK_MARKERS = ('fallback', 'unavailable', 'timed out', 'offline')


def contains_threat_fallback_copy(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    lowered = value.strip().lower()
    return any(marker in lowered for marker in THREAT_FALLBACK_MARKERS)


def normalize_live_threat_dashboard_payload(payload: dict[str, Any]) -> dict[str, Any]:
    payload['source'] = 'live'
    payload['degraded'] = False

    if contains_threat_fallback_copy(payload.get('message')):
        payload['message'] = THREAT_DASHBOARD_LIVE_MESSAGE

    cards = payload.get('cards')
    if isinstance(cards, list):
        for card in cards:
            if not isinstance(card, dict):
                continue
            if contains_threat_fallback_copy(card.get('detail')):
                card['detail'] = THREAT_DASHBOARD_LIVE_CARD_DETAILS.get(str(card.get('label')), str(card.get('detail') or ''))

    for key in ('active_alerts', 'recent_detections'):
        records = payload.get(key)
        if not isinstance(records, list):
            continue
        for record in records:
            if isinstance(record, dict):
                record['source'] = 'live'

    return payload


def threat_dashboard_payload_is_fallback(payload: dict[str, Any]) -> bool:
    return str(payload.get('source') or '').lower() == 'fallback' or bool(payload.get('degraded'))


def mark_live_payload(payload: dict[str, Any], dependency_name: str) -> dict[str, Any]:
    payload['source'] = 'live'
    payload['degraded'] = False
    payload.setdefault('metadata', {})
    if isinstance(payload['metadata'], dict):
        payload['metadata'].setdefault('dependency_mode', dependency_mode(dependency_name))
    record_dependency_runtime(
        dependency_name,
        dependency_mode(dependency_name),
        payload_source='live',
        degraded=False,
        detail=EMBEDDED_SERVICE_STATUS_DETAIL if dependency_mode(dependency_name) == 'embedded_local' else 'Remote proxy responding normally',
    )
    return attach_dependency_diagnostics(payload, dependency_name)


def evaluate_live_risk(payload: dict[str, Any]) -> dict[str, Any] | None:
    mode = dependency_mode('risk_engine')
    try:
        if mode == 'remote_proxy':
            response = request_json('POST', f'{RISK_ENGINE_URL}/v1/risk/evaluate', payload, RISK_ENGINE_TIMEOUT_SECONDS)
            if response is None:
                record_dependency_runtime('risk_engine', 'fallback', 'Remote risk-engine request failed.')
                return None
            record_dependency_runtime('risk_engine', mode)
            return response

        response = execute_embedded_risk_evaluation(payload)
        record_dependency_runtime('risk_engine', mode)
        return response
    except Exception as exc:  # pragma: no cover - exercised through fallback assertions
        record_dependency_runtime('risk_engine', 'fallback', str(exc))
        logger.exception('Embedded risk-engine execution failed; falling back to safe dashboard payloads.')
        return None


def fetch_compliance_dashboard() -> dict[str, Any] | None:
    mode = dependency_mode('compliance_service')
    try:
        if mode == 'remote_proxy':
            payload = request_json('GET', f'{COMPLIANCE_SERVICE_URL}/dashboard', None, COMPLIANCE_SERVICE_TIMEOUT_SECONDS)
            if payload is None:
                record_dependency_runtime('compliance_service', 'fallback', 'Remote compliance dashboard request failed.')
                return None
            record_dependency_runtime('compliance_service', mode)
            return mark_live_payload(payload, 'compliance_service')

        payload = execute_embedded_compliance_dashboard()
        record_dependency_runtime('compliance_service', mode)
        return mark_live_payload(payload, 'compliance_service')
    except Exception as exc:  # pragma: no cover - exercised through fallback assertions
        record_dependency_runtime('compliance_service', 'fallback', str(exc))
        logger.exception('Embedded compliance-service dashboard execution failed; using fallback payload.')
        return None


def fetch_compliance_policy_state() -> dict[str, Any] | None:
    mode = dependency_mode('compliance_service')
    try:
        if mode == 'remote_proxy':
            response = request_json('GET', f'{COMPLIANCE_SERVICE_URL}/policy/state', None, COMPLIANCE_SERVICE_TIMEOUT_SECONDS)
            if response is None:
                record_dependency_runtime('compliance_service', 'fallback', 'Remote compliance policy-state request failed.')
                return None
            record_dependency_runtime('compliance_service', mode)
            return response

        response = execute_embedded_compliance_policy_state()
        record_dependency_runtime('compliance_service', mode)
        return response
    except Exception as exc:  # pragma: no cover
        record_dependency_runtime('compliance_service', 'fallback', str(exc))
        logger.exception('Embedded compliance-service policy-state execution failed; using fallback payload.')
        return None


def fetch_compliance_governance_actions() -> list[dict[str, Any]] | None:
    mode = dependency_mode('compliance_service')
    try:
        if mode == 'remote_proxy':
            response = request_json('GET', f'{COMPLIANCE_SERVICE_URL}/governance/actions', None, COMPLIANCE_SERVICE_TIMEOUT_SECONDS)
            if response is None:
                record_dependency_runtime('compliance_service', 'fallback', 'Remote governance-actions request failed.')
                return None
            record_dependency_runtime('compliance_service', mode)
            return response

        response = execute_embedded_compliance_governance_actions()
        record_dependency_runtime('compliance_service', mode)
        return response
    except Exception as exc:  # pragma: no cover
        record_dependency_runtime('compliance_service', 'fallback', str(exc))
        logger.exception('Embedded compliance-service governance-actions execution failed; using fallback payload.')
        return None


def fetch_compliance_governance_action(action_id: str) -> dict[str, Any] | None:
    mode = dependency_mode('compliance_service')
    try:
        if mode == 'remote_proxy':
            response = request_json('GET', f'{COMPLIANCE_SERVICE_URL}/governance/actions/{action_id}', None, COMPLIANCE_SERVICE_TIMEOUT_SECONDS)
            if response is None:
                record_dependency_runtime('compliance_service', 'fallback', f'Remote governance action request failed for {action_id}.')
                return None
            record_dependency_runtime('compliance_service', mode)
            return mark_live_payload(response, 'compliance_service')

        response = execute_embedded_compliance_governance_action(action_id)
        if response is None:
            return None
        record_dependency_runtime('compliance_service', mode)
        return mark_live_payload(response, 'compliance_service')
    except Exception as exc:  # pragma: no cover
        record_dependency_runtime('compliance_service', 'fallback', str(exc))
        logger.exception('Embedded compliance-service governance-action execution failed; using fallback payload.')
        return None


def proxy_compliance(path: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    mode = dependency_mode('compliance_service')
    try:
        if mode == 'remote_proxy':
            response = request_json('POST', f'{COMPLIANCE_SERVICE_URL}/{path}', payload, COMPLIANCE_SERVICE_TIMEOUT_SECONDS)
            if response is None:
                record_dependency_runtime('compliance_service', 'fallback', f'Remote compliance request failed for {path}.')
                return None
            record_dependency_runtime('compliance_service', mode)
            return mark_live_payload(response, 'compliance_service')

        response = execute_embedded_compliance_request(path, payload)
        record_dependency_runtime('compliance_service', mode)
        return mark_live_payload(response, 'compliance_service')
    except Exception as exc:  # pragma: no cover - covered by fallback tests via monkeypatch
        record_dependency_runtime('compliance_service', 'fallback', str(exc))
        logger.exception('Embedded compliance-service request failed for %s; using fallback payload.', path)
        return None


def fetch_resilience_dashboard() -> dict[str, Any] | None:
    mode = dependency_mode('reconciliation_service')
    try:
        if mode == 'remote_proxy':
            payload = request_json('GET', f'{RECONCILIATION_SERVICE_URL}/dashboard', None, RECONCILIATION_SERVICE_TIMEOUT_SECONDS)
            if payload is None:
                record_dependency_runtime('reconciliation_service', 'fallback', 'Remote resilience dashboard request failed.')
                return None
            record_dependency_runtime('reconciliation_service', mode)
            return mark_live_payload(payload, 'reconciliation_service')

        payload = execute_embedded_resilience_dashboard()
        record_dependency_runtime('reconciliation_service', mode)
        return mark_live_payload(payload, 'reconciliation_service')
    except Exception as exc:  # pragma: no cover
        record_dependency_runtime('reconciliation_service', 'fallback', str(exc))
        logger.exception('Embedded reconciliation-service dashboard execution failed; using fallback payload.')
        return None


def proxy_resilience_get(path: str) -> dict[str, Any] | list[dict[str, Any]] | None:
    mode = dependency_mode('reconciliation_service')
    try:
        if mode == 'remote_proxy':
            response = request_json('GET', f'{RECONCILIATION_SERVICE_URL}/{path}', None, RECONCILIATION_SERVICE_TIMEOUT_SECONDS)
            if response is None:
                record_dependency_runtime('reconciliation_service', 'fallback', f'Remote resilience GET request failed for {path}.')
                return None
            record_dependency_runtime('reconciliation_service', mode)
            if isinstance(response, dict):
                return mark_live_payload(response, 'reconciliation_service')
            return response

        response = execute_embedded_resilience_get(path)
        if response is None:
            return None
        record_dependency_runtime('reconciliation_service', mode)
        if isinstance(response, dict):
            return mark_live_payload(response, 'reconciliation_service')
        return response
    except Exception as exc:  # pragma: no cover
        record_dependency_runtime('reconciliation_service', 'fallback', str(exc))
        logger.exception('Embedded reconciliation-service GET request failed for %s; using fallback payload.', path)
        return None


def proxy_resilience_post(path: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    mode = dependency_mode('reconciliation_service')
    try:
        if mode == 'remote_proxy':
            response = request_json('POST', f'{RECONCILIATION_SERVICE_URL}/{path}', payload, RECONCILIATION_SERVICE_TIMEOUT_SECONDS)
            if response is None:
                record_dependency_runtime('reconciliation_service', 'fallback', f'Remote resilience POST request failed for {path}.')
                return None
            record_dependency_runtime('reconciliation_service', mode)
            return mark_live_payload(response, 'reconciliation_service')

        response = execute_embedded_resilience_post(path, payload)
        record_dependency_runtime('reconciliation_service', mode)
        return mark_live_payload(response, 'reconciliation_service')
    except Exception as exc:  # pragma: no cover - covered by fallback tests via monkeypatch
        record_dependency_runtime('reconciliation_service', 'fallback', str(exc))
        logger.exception('Embedded reconciliation-service POST request failed for %s; using fallback payload.', path)
        return None


def fetch_threat_dashboard() -> dict[str, Any] | None:
    mode = dependency_mode('threat_engine')
    try:
        if mode == 'remote_proxy':
            payload = request_json('GET', f'{THREAT_ENGINE_URL}/dashboard', None, THREAT_ENGINE_TIMEOUT_SECONDS)
            if payload is None:
                record_dependency_runtime('threat_engine', 'fallback', 'Remote threat dashboard request failed.')
                return None
            if threat_dashboard_payload_is_fallback(payload):
                record_dependency_runtime(
                    'threat_engine',
                    'fallback',
                    'Remote threat dashboard returned a fallback payload.',
                    payload_source='fallback',
                    degraded=True,
                    detail='Threat dashboard fallback active',
                )
                return attach_dependency_diagnostics(
                    payload,
                    'threat_engine',
                    fallback_reason='Threat dashboard remained in fallback mode after remote execution.',
                )
            record_dependency_runtime('threat_engine', mode)
            return mark_live_payload(normalize_live_threat_dashboard_payload(payload), 'threat_engine')

        payload = execute_embedded_threat_dashboard()
        record_dependency_runtime('threat_engine', mode)
        return mark_live_payload(normalize_live_threat_dashboard_payload(payload), 'threat_engine')
    except Exception as exc:  # pragma: no cover
        record_dependency_runtime('threat_engine', 'fallback', str(exc))
        logger.exception('Embedded threat-engine dashboard execution failed; using fallback payload.')
        return None


def proxy_threat(kind: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    mode = dependency_mode('threat_engine')
    try:
        if mode == 'remote_proxy':
            response = request_json('POST', f'{THREAT_ENGINE_URL}/analyze/{kind}', payload, THREAT_ENGINE_TIMEOUT_SECONDS)
            if response is None:
                record_dependency_runtime('threat_engine', 'fallback', f'Remote threat-engine request failed for {kind}.')
                return None
            record_dependency_runtime('threat_engine', mode)
            return mark_live_payload(response, 'threat_engine')

        response = execute_embedded_threat_request(kind, payload)
        record_dependency_runtime('threat_engine', mode)
        return mark_live_payload(response, 'threat_engine')
    except Exception as exc:  # pragma: no cover - covered by fallback tests via monkeypatch
        record_dependency_runtime('threat_engine', 'fallback', str(exc))
        logger.exception('Embedded threat-engine request failed for %s; using fallback payload.', kind)
        return None


def request_json(method: str, url: str, payload: dict[str, Any] | None, timeout_seconds: float) -> dict[str, Any] | None:
    request = UrlRequest(
        url,
        data=json.dumps(payload).encode('utf-8') if payload is not None else None,
        headers={'Content-Type': 'application/json'},
        method=method,
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode('utf-8'))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
        return None


def fallback_compliance_dashboard() -> dict[str, Any]:
    return {
        'source': 'fallback',
        'degraded': True,
        'generated_at': '2026-03-18T11:00:00Z',
        'summary': {
            'allowlisted_wallet_count': 2,
            'blocklisted_wallet_count': 1,
            'frozen_wallet_count': 1,
            'review_required_wallet_count': 1,
            'paused_asset_count': 1,
            'latest_transfer_decision': 'review',
            'latest_residency_decision': 'denied',
            'triggered_rule_count': 3,
        },
        'cards': [
            {'label': 'Transfer decision', 'value': 'review', 'detail': 'Fallback wrapper decision indicates manual review until the compliance service is back online.', 'tone': 'high'},
            {'label': 'Compliance risk', 'value': 'high', 'detail': 'Fallback deterministic wrapper rules remain available at the gateway.', 'tone': 'high'},
            {'label': 'Governance actions', 'value': '3', 'detail': 'Fallback immutable-style action log stays visible in degraded mode.', 'tone': 'medium'},
            {'label': 'Residency decision', 'value': 'denied', 'detail': 'Fallback residency routing keeps sovereignty restrictions explainable.', 'tone': 'critical'},
        ],
        'transfer_screening': {
            'decision': 'review',
            'risk_level': 'high',
            'reasons': ['One or more wallets have incomplete or pending KYC status.', 'A participating jurisdiction requires manual review.'],
            'triggered_rules': [
                {'rule_id': 'kyc-status', 'outcome': 'review', 'summary': 'One or more wallets have incomplete or pending KYC status.'},
                {'rule_id': 'jurisdiction-policy', 'outcome': 'review', 'summary': 'A participating jurisdiction requires manual review.'},
                {'rule_id': 'wallet-allowlist', 'outcome': 'pass', 'summary': 'At least one participating wallet is allowlisted or tagged as trusted.'},
            ],
            'recommended_action': 'Escalate to compliance operations for manual approval.',
            'wrapper_status': 'wrapper-hold',
            'explainability_summary': 'Decision review: One or more wallets have incomplete or pending KYC status.',
            'policy_snapshot': {
                'allowlisted_wallets': 2,
                'blocklisted_wallets': 1,
                'frozen_wallets': 1,
                'review_required_wallets': 1,
                'paused_assets': ['USTB-2026'],
            },
        },
        'residency_screening': {
            'residency_decision': 'denied',
            'policy_violations': ['Requested processing region is on the restricted region list.', 'Requested processing region is not on the approved cloud region list.'],
            'routing_recommendation': 'Route processing to eu-west or request governance override.',
            'governance_status': 'restricted',
            'explainability_summary': 'Requested processing region is on the restricted region list.; Requested processing region is not on the approved cloud region list.',
            'allowed_region_outcome': 'eu-west',
        },
        'policy_state': {
            'allowlisted_wallets': ['0xaaa0000000000000000000000000000000000101', '0xbbb0000000000000000000000000000000000202'],
            'blocklisted_wallets': ['0xblocked000000000000000000000000000000003'],
            'frozen_wallets': ['0xddd0000000000000000000000000000000000404'],
            'review_required_wallets': ['0xreview000000000000000000000000000000004'],
            'paused_assets': ['USTB-2026'],
            'approved_cloud_regions': ['us-east', 'us-central', 'eu-west'],
            'friendly_regions': ['us-east', 'us-central', 'eu-west', 'sg-gov'],
            'restricted_regions': ['cn-north', 'ru-central', 'ir-gov'],
            'action_count': 3,
            'latest_action_id': 'gov-fallback-003',
        },
        'latest_governance_actions': [
            {'action_id': 'gov-fallback-003', 'created_at': '2026-03-18T11:02:00Z', 'action_type': 'pause_asset_transfers', 'target_type': 'asset', 'target_id': 'USTB-2026', 'status': 'applied', 'reason': 'Pause asset transfers while wrapper thresholds are recalibrated.', 'actor': 'governance-multisig', 'related_asset_id': 'USTB-2026', 'metadata': {'ticket': 'CMP-1043'}, 'attestation_hash': 'fallback-003', 'policy_effects': ['Asset USTB-2026 transfer activity paused.']},
            {'action_id': 'gov-fallback-002', 'created_at': '2026-03-18T11:01:00Z', 'action_type': 'allowlist_wallet', 'target_type': 'wallet', 'target_id': '0xeee0000000000000000000000000000000000505', 'status': 'applied', 'reason': 'Approved new qualified custodian wallet for primary market settlements.', 'actor': 'governance-multisig', 'related_asset_id': 'USTB-2026', 'metadata': {'ticket': 'CMP-1044'}, 'attestation_hash': 'fallback-002', 'policy_effects': ['Wallet 0xeee0000000000000000000000000000000000505 added to allowlist.']},
            {'action_id': 'gov-fallback-001', 'created_at': '2026-03-18T11:00:00Z', 'action_type': 'freeze_wallet', 'target_type': 'wallet', 'target_id': '0xddd0000000000000000000000000000000000404', 'status': 'applied', 'reason': 'Escalated compliance review after repeated sanctions-adjacent transfers.', 'actor': 'governance-multisig', 'related_asset_id': 'USTB-2026', 'metadata': {'ticket': 'CMP-1042'}, 'attestation_hash': 'fallback-001', 'policy_effects': ['Wallet 0xddd0000000000000000000000000000000000404 frozen.']},
        ],
        'asset_transfer_status': [
            {'asset_id': 'USTB-2026', 'status': 'paused'},
            {'asset_id': 'USTB-2027', 'status': 'active'},
        ],
        'sample_scenarios': {
            'compliant-transfer-approved': 'Compliant transfer that should be approved.',
            'blocked-transfer-sanctions': 'Transfer blocked because sanctions screening failed.',
            'blocked-transfer-blocklist': 'Transfer blocked because a wallet is blocklisted.',
            'review-transfer-incomplete-kyc': 'Transfer sent to review because KYC is incomplete.',
            'review-transfer-restricted-jurisdiction': 'Transfer sent to review due to restricted jurisdiction policy.',
            'denied-residency-restricted-region': 'Residency request denied due to restricted processing region.',
            'governance-freeze-wallet': 'Governance action freezing a wallet.',
            'governance-pause-asset': 'Governance action pausing asset transfers.',
            'governance-allowlist-wallet': 'Governance action allowlisting a wallet.',
            'transfer-blocked-because-asset-paused': 'Transfer blocked because the asset is paused.',
        },
        'message': 'Compliance service unavailable or timed out. Returning explicit fallback policy wrappers and governance ledger records so Feature 3 remains demoable.',
        'evidence_state': 'FALLBACK_EVIDENCE',
        'verified_live': False,
        'exportable_as_verified': False,
        'reason': 'compliance-service unreachable; deterministic gateway wrappers returned as fallback',
    }


def fallback_transfer_screening(payload: dict[str, Any]) -> dict[str, Any]:
    policy = payload.get('asset_transfer_policy', {})
    sanctions = payload.get('sender_sanctions_flag') or payload.get('receiver_sanctions_flag')
    blocklisted = bool(payload.get('sender_blocklist_match') or payload.get('receiver_blocklist_match'))
    asset_paused = policy.get('asset_status') == 'paused'
    incomplete_kyc = payload.get('sender_kyc_status') != 'verified' or payload.get('receiver_kyc_status') != 'verified'
    review_jurisdictions = set(policy.get('review_jurisdictions', []))
    restricted_jurisdictions = set(policy.get('restricted_jurisdictions', []))
    jurisdictions = {payload.get('sender_jurisdiction'), payload.get('receiver_jurisdiction')}
    triggered_rules = []
    reasons = []
    decision = 'approved'
    risk_level = 'low'

    def add(rule_id: str, outcome: str, summary: str) -> None:
        nonlocal decision, risk_level
        triggered_rules.append({'rule_id': rule_id, 'outcome': outcome, 'summary': summary})
        if outcome != 'pass':
            reasons.append(summary)
        if outcome == 'block':
            decision = 'blocked'
            risk_level = 'critical'
        elif outcome == 'review' and decision != 'blocked':
            decision = 'review'
            risk_level = 'high'

    add('sanctions-screen', 'block' if sanctions else 'pass', 'Sanctions/watchlist screening failed for one or more wallets.' if sanctions else 'No sanctions/watchlist hits detected.')
    add('wallet-blocklist', 'block' if blocklisted else 'pass', 'A participating wallet is currently blocklisted by governance policy.' if blocklisted else 'No participating wallets are blocklisted.')
    add('asset-transfer-status', 'block' if asset_paused else 'pass', 'Asset transfers are currently paused for this asset.' if asset_paused else 'Asset transfer status is active.')
    add('kyc-status', 'review' if incomplete_kyc else 'pass', 'One or more wallets have incomplete or pending KYC status.' if incomplete_kyc else 'Sender and receiver KYC controls are complete.')
    jurisdiction_review = bool(jurisdictions & (restricted_jurisdictions | review_jurisdictions))
    add('jurisdiction-policy', 'review' if jurisdiction_review and decision != 'blocked' else 'pass', 'A participating jurisdiction requires manual review.' if jurisdiction_review and decision != 'blocked' else 'Jurisdiction controls passed.')

    return {
        'decision': decision,
        'risk_level': risk_level,
        'reasons': reasons or ['All required compliance controls passed.'],
        'triggered_rules': triggered_rules,
        'recommended_action': 'Reject the transfer and record an exception in governance audit logs.' if decision == 'blocked' else 'Escalate to compliance operations for manual approval.' if decision == 'review' else 'Proceed with wrapped transfer execution.',
        'wrapper_status': 'wrapper-blocked' if decision == 'blocked' else 'wrapper-hold' if decision == 'review' else 'wrapper-clear',
        'explainability_summary': f"Decision {decision}: {(reasons or ['all required compliance controls passed'])[0]}",
        'policy_snapshot': fallback_compliance_dashboard()['policy_state'],
        'source': 'fallback',
        'degraded': True,
        'evidence_state': 'FALLBACK_EVIDENCE',
        'verified_live': False,
        'exportable_as_verified': False,
        'reason': 'compliance-service unreachable; deterministic transfer rules applied as fallback',
    }


def fallback_residency_screening(payload: dict[str, Any]) -> dict[str, Any]:
    approved = set(payload.get('approved_regions', []))
    restricted = set(payload.get('restricted_regions', []))
    requested = payload.get('requested_processing_region')
    violations = []
    if requested in restricted:
        violations.append('Requested processing region is on the restricted region list.')
    if requested not in approved:
        violations.append('Requested processing region is not on the approved cloud region list.')
    if payload.get('sensitivity_level') == 'sovereign' and not str(payload.get('cloud_environment', '')).startswith('sovereign'):
        violations.append('Sovereign data requires a sovereign cloud environment.')
    decision = 'denied' if violations else 'allowed'
    return {
        'residency_decision': decision,
        'policy_violations': violations,
        'routing_recommendation': 'Route processing to eu-west or request governance override.' if violations else f"Route processing to {requested} in {payload.get('cloud_environment')}",
        'governance_status': 'restricted' if violations else 'normal',
        'explainability_summary': '; '.join(violations) if violations else 'Residency controls passed without violations.',
        'allowed_region_outcome': 'eu-west' if violations else requested,
        'source': 'fallback',
        'degraded': True,
        'evidence_state': 'FALLBACK_EVIDENCE',
        'verified_live': False,
        'exportable_as_verified': False,
        'reason': 'compliance-service unreachable; deterministic residency rules applied as fallback',
    }


def fallback_governance_action(payload: dict[str, Any]) -> dict[str, Any]:
    attestation = f"fallback-{payload.get('action_type', 'action')}-{payload.get('target_id', 'target')}"
    effect = f"Fallback governance action {payload.get('action_type')} applied to {payload.get('target_id')}."
    return {
        **payload,
        'action_id': 'gov-fallback-new',
        'created_at': '2026-03-18T11:05:00Z',
        'status': 'applied',
        'attestation_hash': attestation,
        'policy_effects': [effect],
        'source': 'fallback',
        'degraded': True,
        'evidence_state': 'FALLBACK_EVIDENCE',
        'verified_live': False,
        'exportable_as_verified': False,
        'reason': 'compliance-service unreachable; governance action recorded locally as fallback',
    }


def fallback_resilience_dashboard() -> dict[str, Any]:
    reconciliation_payload = load_json_file(
        RECONCILIATION_DATA_DIR,
        'critical_supply_divergence_double_count_risk.json',
        DEFAULT_RECONCILIATION_STATE,
    )
    backstop_payload = load_json_file(
        RECONCILIATION_DATA_DIR,
        'critical_mismatch_paused_bridge.json',
        DEFAULT_BACKSTOP_STATE,
    )
    return {
        'source': 'fallback',
        'degraded': True,
        'generated_at': '2026-03-18T12:00:00Z',
        'summary': {
            'reconciliation_status': 'critical',
            'severity_score': 82,
            'mismatch_amount': 191400.0,
            'stale_ledger_count': 1,
            'backstop_decision': 'paused',
            'incident_count': 2,
        },
        'cards': [
            {'label': 'Reconciliation', 'value': 'critical', 'detail': 'Fallback resilience dashboard detected material supply divergence across multiple ledgers.', 'tone': 'critical'},
            {'label': 'Mismatch amount', 'value': '191,400', 'detail': 'Fallback normalized supply mismatch vs expected total supply.', 'tone': 'critical'},
            {'label': 'Stale ledgers', 'value': '1', 'detail': 'Fallback stale-ledger penalty remains visible when the service is offline.', 'tone': 'warning'},
            {'label': 'Backstop', 'value': 'paused', 'detail': 'Fallback safeguards paused bridge and settlement lanes.', 'tone': 'critical'},
        ],
        'reconciliation_result': fallback_reconcile_state(reconciliation_payload),
        'backstop_result': fallback_backstop_evaluate(backstop_payload),
        'latest_incidents': [
            {'event_id': 'evt-fallback-0002', 'created_at': '2026-03-18T11:52:00Z', 'event_type': 'market-circuit-breaker', 'trigger_source': 'backstop-engine', 'related_asset_id': 'USTB-2026', 'affected_assets': ['USTB-2026'], 'affected_ledgers': ['ethereum', 'avalanche'], 'severity': 'high', 'status': 'contained', 'summary': 'Fallback circuit breaker event kept trading paused while cyber scores were elevated.', 'metadata': {'scenario': 'cyber-triggered-restricted-mode'}, 'attestation_hash': 'fallback-event-0002', 'fingerprint': 'fallback-event-00', 'source': 'fallback', 'degraded': True},
            {'event_id': 'evt-fallback-0001', 'created_at': '2026-03-18T11:45:00Z', 'event_type': 'reconciliation-failure', 'trigger_source': 'reconciliation-engine', 'related_asset_id': 'USTB-2026', 'affected_assets': ['USTB-2026'], 'affected_ledgers': ['ethereum', 'avalanche', 'private-bank-ledger'], 'severity': 'critical', 'status': 'open', 'summary': 'Fallback reconciliation incident preserved duplicate mint risk context during service outage.', 'metadata': {'scenario': 'critical-supply-divergence-double-count-risk'}, 'attestation_hash': 'fallback-event-0001', 'fingerprint': 'fallback-event-00', 'source': 'fallback', 'degraded': True},
        ],
        'sample_scenarios': {
            'healthy-matched-multi-ledger-state': 'Healthy matched supply across ethereum, avalanche, and private-bank-ledger.',
            'mild-mismatch-warning': 'Small mismatch with manageable settlement lag.',
            'critical-supply-divergence-double-count-risk': 'Critical over-reporting across ledgers indicating double-count risk.',
            'stale-private-ledger-data': 'Private ledger data is stale and penalized.',
            'high-volatility-alert': 'High volatility produces a deterministic alert decision.',
            'cyber-triggered-restricted-mode': 'Cyber + volatility combination restricts controls.',
            'critical-mismatch-paused-bridge': 'Critical reconciliation mismatch pauses bridge and settlement.',
            'incident-record-reconciliation-failure': 'Incident example for a reconciliation failure.',
            'incident-record-market-circuit-breaker': 'Incident example for a market circuit breaker.',
            'recovery-normal-mode-after-alert': 'Recovery scenario returning to normal mode after prior alert.',
        },
        'message': 'Reconciliation-service unavailable or timed out. Returning explicit fallback resilience data so Feature 4 remains demoable.',
        'evidence_state': 'FALLBACK_EVIDENCE',
        'verified_live': False,
        'exportable_as_verified': False,
        'reason': 'reconciliation-service unreachable; deterministic backstop and reconciliation data returned as fallback',
    }


def fallback_reconcile_state(payload: dict[str, Any]) -> dict[str, Any]:
    expected = float(payload.get('expected_total_supply', 0) or 0)
    ledgers = payload.get('ledgers', [])
    observed_total = sum(float(item.get('reported_supply', 0)) for item in ledgers)
    normalized_total = 0.0
    stale_count = 0
    settlement_lag_ledgers: list[str] = []
    over_reporting: list[str] = []
    assessments: list[dict[str, Any]] = []

    for item in ledgers:
        effective = max(float(item.get('reported_supply', 0)) - float(item.get('locked_supply', 0)) - float(item.get('pending_settlement', 0)), 0)
        staleness_minutes = 180 if item.get('ledger_name') == 'private-bank-ledger' and '09:' in str(item.get('last_updated_at', '')) else 20
        penalty = 0.12 if staleness_minutes >= 120 else 0.05 if staleness_minutes >= 45 else 0.0
        normalized_total += effective * float(item.get('reconciliation_weight', 1.0)) * (1 - penalty)
        if penalty:
            stale_count += 1
        lag_flag = float(item.get('pending_settlement', 0)) >= 20000
        if lag_flag:
            settlement_lag_ledgers.append(item.get('ledger_name', 'unknown'))
        over_reported = float(item.get('reported_supply', 0)) > expected * 0.55 if expected else False
        if over_reported:
            over_reporting.append(item.get('ledger_name', 'unknown'))
        assessments.append({
            'ledger_name': item.get('ledger_name', 'unknown'),
            'normalized_effective_supply': round(effective, 2),
            'accepted': True,
            'status': 'penalized' if penalty or lag_flag else 'accepted',
            'staleness_minutes': staleness_minutes,
            'staleness_penalty': penalty,
            'settlement_lag_flag': lag_flag,
            'over_reported_against_expected': over_reported,
            'explanation': 'Fallback reconciliation logic normalized reported supply and applied stale / settlement penalties where necessary.',
        })

    mismatch_amount = round(normalized_total - expected, 2)
    mismatch_percent = round((abs(mismatch_amount) / expected) * 100, 2) if expected else 0.0
    duplicate_risk = len(over_reporting) >= 2
    severity_score = min(100, int(round(mismatch_percent * 4 + stale_count * 12 + len(settlement_lag_ledgers) * 8 + (24 if duplicate_risk else 0))))
    status = 'critical' if severity_score >= 70 or mismatch_percent >= 8 or duplicate_risk else 'warning' if severity_score >= 25 or stale_count or settlement_lag_ledgers else 'matched'

    return {
        'asset_id': payload.get('asset_id', 'USTB-2026'),
        'reconciliation_status': status,
        'expected_total_supply': expected,
        'observed_total_supply': round(observed_total, 2),
        'normalized_effective_supply': round(normalized_total, 2),
        'mismatch_amount': mismatch_amount,
        'mismatch_percent': mismatch_percent,
        'severity_score': severity_score,
        'duplicate_or_double_count_risk': duplicate_risk,
        'stale_ledger_count': stale_count,
        'settlement_lag_ledgers': settlement_lag_ledgers,
        'mismatch_summary': ['Fallback gateway detected supply drift requiring operator review.'],
        'recommendations': ['Refresh stale ledgers.', 'Investigate bridge mint/burn drift before restoring throughput.'] if status != 'matched' else ['Continue scheduled monitoring.'],
        'explainability_summary': f"Fallback reconciliation {status}: expected {expected:,.0f}, observed {observed_total:,.0f}, normalized {normalized_total:,.0f}.",
        'per_ledger_balances': [
            {'ledger_name': item.get('ledger_name', 'unknown'), 'reported_supply': item.get('reported_supply', 0), 'locked_supply': item.get('locked_supply', 0), 'pending_settlement': item.get('pending_settlement', 0), 'effective_supply': max(float(item.get('reported_supply', 0)) - float(item.get('locked_supply', 0)) - float(item.get('pending_settlement', 0)), 0), 'transfer_count': item.get('transfer_count', 0), 'last_updated_at': item.get('last_updated_at', '')}
            for item in ledgers
        ],
        'ledger_assessments': assessments,
        'source': 'fallback',
        'degraded': True,
        'evidence_state': 'FALLBACK_EVIDENCE',
        'verified_live': False,
        'exportable_as_verified': False,
        'reason': 'reconciliation-service unreachable; gateway normalization applied as fallback',
    }


def fallback_backstop_evaluate(payload: dict[str, Any]) -> dict[str, Any]:
    triggered: list[str] = []
    actions: list[str] = []
    decision = 'normal'
    trading_status = 'active'
    bridge_status = 'active'
    settlement_status = 'active'

    if float(payload.get('volatility_score', 0)) >= 60:
        triggered.append('soft alert')
        decision = 'alert'
    if float(payload.get('volatility_score', 0)) >= 80:
        triggered.extend(['high-volatility mode', 'reduce transfer threshold'])
        decision = 'restricted'
        trading_status = 'guarded'
    if float(payload.get('cyber_alert_score', 0)) >= 75:
        triggered.append('pause trading')
        decision = 'restricted' if decision != 'paused' else decision
        trading_status = 'paused'
    if float(payload.get('reconciliation_severity', 0)) >= 70:
        triggered.extend(['pause bridge / settlement lane', 'circuit breaker triggered'])
        decision = 'paused'
        bridge_status = 'paused'
        settlement_status = 'paused'
    if float(payload.get('oracle_confidence_score', 100)) <= 45:
        triggered.append('soft alert')
        if decision == 'normal':
            decision = 'alert'
    if float(payload.get('compliance_incident_score', 0)) >= 60:
        triggered.append('reduce transfer threshold')
        if decision == 'normal':
            decision = 'alert'

    if not actions:
        actions = ['Maintain normal operations and keep baseline telemetry active.'] if decision == 'normal' else ['Escalate treasury operations and keep deterministic backstop controls engaged.']
    if bridge_status == 'active' and decision in {'alert', 'restricted'}:
        bridge_status = 'guarded'
    if settlement_status == 'active' and decision == 'restricted':
        settlement_status = 'guarded'
    if trading_status == 'active' and decision == 'alert':
        trading_status = 'watch'

    operational_status = {'normal': 'normal', 'alert': 'stressed', 'restricted': 'restricted', 'paused': 'paused'}[decision]
    return {
        'asset_id': payload.get('asset_id', 'USTB-2026'),
        'backstop_decision': decision,
        'triggered_safeguards': list(dict.fromkeys(triggered)),
        'recommended_actions': actions,
        'operational_status': operational_status,
        'trading_status': trading_status,
        'bridge_status': bridge_status,
        'settlement_status': settlement_status,
        'explainability_summary': f"Fallback backstop decision {decision} for {payload.get('asset_id', 'USTB-2026')}.",
        'source': 'fallback',
        'degraded': True,
        'evidence_state': 'FALLBACK_EVIDENCE',
        'verified_live': False,
        'exportable_as_verified': False,
        'reason': 'reconciliation-service unreachable; deterministic safeguard rules applied as fallback',
    }


def fallback_incident_record(payload: dict[str, Any]) -> dict[str, Any]:
    summary = payload.get('summary', 'Fallback resilience incident recorded locally at the API gateway.')
    return {
        'event_id': 'evt-fallback-new',
        'created_at': '2026-03-18T12:01:00Z',
        'event_type': payload.get('event_type', 'resilience-event'),
        'trigger_source': payload.get('trigger_source', 'api-gateway-fallback'),
        'related_asset_id': payload.get('related_asset_id', 'USTB-2026'),
        'affected_assets': payload.get('affected_assets', [payload.get('related_asset_id', 'USTB-2026')]),
        'affected_ledgers': payload.get('affected_ledgers', []),
        'severity': payload.get('severity', 'medium'),
        'status': payload.get('status', 'open'),
        'summary': summary,
        'metadata': payload.get('metadata', {}),
        'attestation_hash': 'fallback-incident-hash',
        'fingerprint': 'fallback-inciden',
        'source': 'fallback',
        'degraded': True,
        'evidence_state': 'FALLBACK_EVIDENCE',
        'verified_live': False,
        'exportable_as_verified': False,
        'reason': 'reconciliation-service unreachable; incident recorded locally at API gateway as fallback',
    }


def build_risk_summary(queue: list[dict[str, Any]]) -> dict[str, Any]:
    if not queue:
        return {
            'total_transactions': 0,
            'allow_count': 0,
            'review_count': 0,
            'block_count': 0,
            'avg_risk_score': 0,
            'high_alert_count': 0,
        }

    risk_scores = [item['evaluation']['risk_score'] for item in queue]
    return {
        'total_transactions': len(queue),
        'allow_count': sum(item['evaluation']['recommendation'] == 'ALLOW' for item in queue),
        'review_count': sum(item['evaluation']['recommendation'] == 'REVIEW' for item in queue),
        'block_count': sum(item['evaluation']['recommendation'] == 'BLOCK' for item in queue),
        'avg_risk_score': round(sum(risk_scores) / len(risk_scores), 1),
        'high_alert_count': sum(item['evaluation']['risk_score'] >= 75 for item in queue),
    }


def _sanitize_demo_tx_hash(tx_hash: str) -> str:
    """In production, demo placeholder hashes must not appear in API responses."""
    if tx_hash and str(tx_hash).lower().startswith('0xphase1'):
        app_mode = os.getenv('APP_MODE', 'local').strip().lower()
        if app_mode in {'production', 'staging'}:
            return '[demo-placeholder-redacted]'
    return tx_hash


def serialize_queue_item(item: dict[str, Any]) -> dict[str, Any]:
    request = item['request']
    evaluation = item['evaluation']
    return {
        'id': item['id'],
        'label': item['label'],
        'tx_hash': _sanitize_demo_tx_hash(request['transaction_payload']['tx_hash']),
        'from_address': request['transaction_payload']['from_address'],
        'to_address': request['transaction_payload']['to_address'],
        'contract_name': request['contract_metadata']['contract_name'],
        'contract_address': request['contract_metadata']['address'],
        'function_name': request['decoded_function_call']['function_name'],
        'risk_score': evaluation['risk_score'],
        'recommendation': evaluation['recommendation'],
        'triggered_rules': [rule['summary'] for rule in evaluation.get('triggered_rules', [])],
        'explanation': evaluation['explanation'],
        'updated_at': item['updated_at'],
        'source': 'live' if item['live_data'] else 'fallback',
        'normalized_risk': build_normalized_risk(item['evaluation'], degraded=not item['live_data']),
    }


def build_risk_alerts(queue: list[dict[str, Any]]) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    for item in queue:
        if item['evaluation']['risk_score'] < 45:
            continue
        top_rule = next(iter(item['evaluation'].get('triggered_rules', [])), None)
        alerts.append(
            {
                'id': f"alert-{item['id']}",
                'title': item['label'],
                'severity': recommendation_severity(item['evaluation']['recommendation']),
                'risk_score': item['evaluation']['risk_score'],
                'recommendation': item['evaluation']['recommendation'],
                'rule': top_rule['summary'] if top_rule else 'Manual review requested.',
                'explanation': item['evaluation']['explanation'],
                'tx_hash': _sanitize_demo_tx_hash(item['request']['transaction_payload']['tx_hash']),
                'status': 'Open' if item['evaluation']['recommendation'] == 'BLOCK' else 'Reviewing',
                'normalized_risk': build_normalized_risk(item['evaluation'], degraded=not item['live_data']),
            }
        )
    return alerts


def build_contract_scan_results(queue: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            'id': f"contract-{item['id']}",
            'contract_name': item['request']['contract_metadata']['contract_name'],
            'contract_address': item['request']['contract_metadata']['address'],
            'function_name': item['request']['decoded_function_call']['function_name'],
            'risk_score': item['evaluation']['risk_score'],
            'recommendation': item['evaluation']['recommendation'],
            'triggered_rules': [rule['summary'] for rule in item['evaluation'].get('triggered_rules', [])],
            'explanation': item['evaluation']['explanation'],
            'source': 'live' if item['live_data'] else 'fallback',
            'normalized_risk': build_normalized_risk(item['evaluation'], degraded=not item['live_data']),
        }
        for item in queue
    ]


def build_decisions_log(queue: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            'id': f"decision-{item['id']}",
            'decided_at': item['updated_at'],
            'tx_hash': item['request']['transaction_payload']['tx_hash'],
            'contract_name': item['request']['contract_metadata']['contract_name'],
            'risk_score': item['evaluation']['risk_score'],
            'recommendation': item['evaluation']['recommendation'],
            'triggered_rules': [rule['summary'] for rule in item['evaluation'].get('triggered_rules', [])],
            'explanation': item['evaluation']['explanation'],
            'source': 'live' if item['live_data'] else 'fallback',
            'normalized_risk': build_normalized_risk(item['evaluation'], degraded=not item['live_data']),
        }
        for item in reversed(queue)
    ]


def build_normalized_risk(evaluation: dict[str, Any], degraded: bool) -> CanonicalRiskResponse:
    risk_score = max(0, min(100, int(evaluation.get('risk_score', 0))))
    recommendation = str(evaluation.get('recommendation', 'REVIEW')).upper()
    telemetry_guard_disagrees = degraded or bool(evaluation.get('telemetry_guard_disagrees'))
    asset_criticality_score = max(1, min(100, risk_score if recommendation != 'ALLOW' else max(1, risk_score // 2)))
    exposure_severity = 'critical' if risk_score >= 85 else 'high' if risk_score >= 65 else 'medium' if risk_score >= 40 else 'low'
    market_confidence_impact = max(0, min(100, risk_score + (15 if telemetry_guard_disagrees else 0)))
    redemption_liquidity_stress = max(0, min(100, risk_score + (20 if recommendation == 'BLOCK' else 8 if recommendation == 'REVIEW' else -20) + (10 if telemetry_guard_disagrees else 0)))
    if telemetry_guard_disagrees:
        contagion_risk_label = 'guarded_due_to_stale_telemetry'
        regulatory_evidence_priority = 'high'
    elif recommendation == 'BLOCK':
        contagion_risk_label = 'elevated'
        regulatory_evidence_priority = 'high'
    elif recommendation == 'REVIEW':
        contagion_risk_label = 'contained'
        regulatory_evidence_priority = 'medium'
    else:
        contagion_risk_label = 'isolated'
        regulatory_evidence_priority = 'low'
    return {
        'asset_criticality_score': asset_criticality_score,
        'exposure_severity': exposure_severity,
        'market_confidence_impact': market_confidence_impact,
        'redemption_liquidity_stress': redemption_liquidity_stress,
        'contagion_risk_label': contagion_risk_label,
        'regulatory_evidence_priority': regulatory_evidence_priority,
    }


def with_resilience_incident_normalized_risk(incident: dict[str, Any]) -> dict[str, Any]:
    severity = str(incident.get('severity', 'medium')).lower()
    status = str(incident.get('status', 'open')).lower()
    severity_score_map = {'low': 25, 'medium': 50, 'high': 75, 'critical': 92}
    risk_score = severity_score_map.get(severity, 50)
    evaluation = {
        'risk_score': risk_score,
        'recommendation': 'BLOCK' if severity in {'critical', 'high'} or status in {'open', 'active'} else 'REVIEW',
    }
    evaluation['telemetry_guard_disagrees'] = bool(incident.get('degraded')) or str(incident.get('source', '')).lower() == 'fallback'
    normalized = build_normalized_risk(evaluation, degraded=bool(incident.get('degraded')))
    return {**incident, 'normalized_risk': normalized}


def with_resilience_normalized_risk(payload: dict[str, Any]) -> dict[str, Any]:
    incidents = payload.get('latest_incidents')
    if isinstance(incidents, list):
        payload = {**payload, 'latest_incidents': [with_resilience_incident_normalized_risk(item) for item in incidents if isinstance(item, dict)]}
    return payload


def recommendation_severity(recommendation: str) -> str:
    if recommendation == 'BLOCK':
        return 'critical'
    if recommendation == 'REVIEW':
        return 'high'
    return 'low'


def iso_timestamp(offset: int) -> str:
    return f'2026-03-18T09:0{offset}:00Z'


def log_optional_fixture_warning_once(path: Path, reason: str, message: str) -> None:
    warning_key = (str(path), reason)
    if warning_key in OPTIONAL_FIXTURE_WARNINGS_EMITTED:
        return
    OPTIONAL_FIXTURE_WARNINGS_EMITTED.add(warning_key)
    logger.warning(message, path)


def load_json_file(data_dir: Path, filename: str, default: Any | None = None) -> Any:
    path = data_dir / filename
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        log_optional_fixture_warning_once(path, 'missing', 'Optional JSON fixture missing at %s; using built-in fallback.')
    except json.JSONDecodeError:
        log_optional_fixture_warning_once(path, 'invalid-json', 'Optional JSON fixture at %s is invalid JSON; using built-in fallback.')

    if default is None:
        return {}
    return deepcopy(default)
class CanonicalRiskResponse(TypedDict):
    asset_criticality_score: int
    exposure_severity: Literal['low', 'medium', 'high', 'critical']
    market_confidence_impact: int
    redemption_liquidity_stress: int
    contagion_risk_label: str
    regulatory_evidence_priority: Literal['low', 'medium', 'high']


# ---------------------------------------------------------------------------
# Task 1: SSE streaming endpoint
# ---------------------------------------------------------------------------
def _sse_response_headers(request: Request) -> dict[str, str]:
    """Response headers that keep an SSE stream unbuffered end-to-end.

    ``no-transform`` (in addition to ``no-cache``) stops an intermediary proxy
    (Railway edge / Cloudflare-type) from compressing or coalescing the event
    stream, which delays or closes it; ``X-Accel-Buffering: no`` disables nginx-style
    proxy buffering; ``Connection: keep-alive`` asks the hop to hold the socket open.
    Together with the immediate connect preamble + 15s heartbeat these are the fix
    for the production "Reconnecting…" flap.
    """
    headers: dict[str, str] = {
        'Cache-Control': 'no-cache, no-transform',
        'Connection': 'keep-alive',
        'X-Accel-Buffering': 'no',
    }
    trace_id = getattr(request.state, 'trace_id', None)
    if trace_id:
        headers['X-Trace-ID'] = trace_id
    return headers


async def _sse_heartbeat_generator(
    workspace_id: str, last_event_id: str, request: Request, subscribe_factory=None,
    stream_name: str = 'alerts',
):
    """Yield resumable Redis Stream events and heartbeats.

    ``subscribe_factory`` selects which workspace stream to read (defaults to the
    alert stream); the telemetry SSE endpoint passes ``alert_stream.subscribe_telemetry``
    so the same heartbeat/replay/backpressure machinery serves both streams.

    An immediate ``retry:`` + ``: connected`` preamble is written BEFORE the first
    (blocking) Redis read so proxy buffers (Railway edge / Next.js fetch) flush and
    the browser sees a live, non-empty stream within milliseconds instead of after a
    full heartbeat window — the root of the production "Reconnecting…" flap where the
    connection idled long enough for a proxy to drop it before the first byte.
    """
    global _SSE_CONNECTION_COUNT, _SSE_EVENTS_DELIVERED
    _SSE_CONNECTION_COUNT += 1
    if subscribe_factory is None:
        subscribe_factory = alert_stream.subscribe
    connected_at = time.monotonic()
    disconnect_reason = 'client_closed'
    # Structured connect evidence (task: event=telemetry_sse_connected workspace_id
    # deployment_commit_sha). Names the stream so alert vs telemetry connections are
    # distinguishable in Railway logs.
    logger.info(
        'event=%s_sse_connected workspace_id=%s deployment_commit_sha=%s',
        stream_name, workspace_id, BACKEND_GIT_COMMIT or 'unavailable',
    )
    iterator = subscribe_factory(workspace_id, last_event_id=last_event_id).__aiter__()
    try:
        # retry: hints the browser's native EventSource reconnect backoff; the
        # comment frame proves liveness and forces the proxy chain to flush headers.
        yield 'retry: 3000\n: connected\n\n'
        while True:
            try:
                event_id, data = await iterator.__anext__()
            except StopAsyncIteration:
                break
            if event_id is None or data is None:
                yield ': heartbeat\n\n'
            else:
                payload = json.dumps(data, separators=(',', ':'))
                _SSE_EVENTS_DELIVERED += 1
                # Per-row delivery evidence (task: event=telemetry_sse_delivery
                # telemetry_id workspace_id success). Only meaningful for telemetry
                # events, which carry telemetry_id; harmless (telemetry_id=none) for
                # alerts.
                if stream_name == 'telemetry':
                    logger.info(
                        'event=telemetry_sse_delivery telemetry_id=%s workspace_id=%s '
                        'redis_event_id=%s success=true',
                        data.get('telemetry_id'), workspace_id, event_id,
                    )
                yield f'id: {event_id}\ndata: {payload}\n\n'
            if await request.is_disconnected():
                break
    except asyncio.CancelledError:
        disconnect_reason = 'cancelled'
    except Exception as exc:  # pragma: no cover - defensive; never leak a 500 mid-stream
        disconnect_reason = f'error:{type(exc).__name__}'
    finally:
        _SSE_CONNECTION_COUNT = max(0, _SSE_CONNECTION_COUNT - 1)
        logger.info(
            'event=%s_sse_disconnected workspace_id=%s duration_seconds=%.1f reason=%s',
            stream_name, workspace_id, max(0.0, time.monotonic() - connected_at), disconnect_reason,
        )
        with suppress(Exception):
            await iterator.aclose()


@app.get('/stream/alerts', summary='SSE stream of real-time alerts for a workspace')
async def stream_alerts(request: Request):
    """Server-Sent Events stream. Authenticates via Bearer token and X-Workspace-Id."""
    try:
        user = authenticate_request(request)
    except HTTPException as exc:
        return JSONResponse({'detail': exc.detail, 'code': 'UNAUTHENTICATED'}, status_code=401)
    requested_workspace_id = request.headers.get('x-workspace-id', '').strip()
    try:
        with pg_connection() as connection:
            ensure_pilot_schema(connection)
            workspace_context = resolve_workspace(connection, str(user['id']), requested_workspace_id)
        workspace_id = str(workspace_context['workspace_id'])
    except HTTPException as exc:
        return JSONResponse({'detail': exc.detail, 'code': 'WORKSPACE_ACCESS_DENIED'}, status_code=exc.status_code)
    backend = await alert_stream.connectivity()
    if not backend['connected']:
        return JSONResponse(
            {'detail': 'Shared alert stream backend unavailable', 'code': 'ALERT_STREAM_UNAVAILABLE'},
            status_code=503,
        )
    last_event_id = request.headers.get('last-event-id', '').strip() or '$'
    headers = _sse_response_headers(request)
    return StreamingResponse(
        _sse_heartbeat_generator(workspace_id, last_event_id, request, stream_name='alert'),
        media_type='text/event-stream',
        headers=headers,
    )


@app.get('/stream/telemetry', summary='SSE stream of real-time telemetry events for a workspace')
async def stream_telemetry(request: Request):
    """Server-Sent Events stream of live telemetry rows for the Target Telemetry page.

    Same authenticated, workspace-scoped, Redis-backed (multi-replica), resumable
    transport as ``/stream/alerts`` but reads the workspace ``:telemetry`` stream, so
    a newly persisted wallet-transfer row is pushed to the open page within a few
    seconds — no manual refresh, tx-hash search, or wait for the next stable poll.
    Telemetry events carry ``type='telemetry'`` and are scoped by workspace here; the
    frontend additionally filters to the current ``target_id``. No cross-workspace
    leakage: the stream key embeds the resolved workspace id.
    """
    try:
        user = authenticate_request(request)
    except HTTPException as exc:
        return JSONResponse({'detail': exc.detail, 'code': 'UNAUTHENTICATED'}, status_code=401)
    requested_workspace_id = request.headers.get('x-workspace-id', '').strip()
    try:
        with pg_connection() as connection:
            ensure_pilot_schema(connection)
            workspace_context = resolve_workspace(connection, str(user['id']), requested_workspace_id)
        workspace_id = str(workspace_context['workspace_id'])
    except HTTPException as exc:
        return JSONResponse({'detail': exc.detail, 'code': 'WORKSPACE_ACCESS_DENIED'}, status_code=exc.status_code)
    backend = await alert_stream.connectivity()
    if not backend['connected']:
        return JSONResponse(
            {'detail': 'Shared telemetry stream backend unavailable', 'code': 'TELEMETRY_STREAM_UNAVAILABLE'},
            status_code=503,
        )
    last_event_id = request.headers.get('last-event-id', '').strip() or '$'
    headers = _sse_response_headers(request)
    return StreamingResponse(
        _sse_heartbeat_generator(
            workspace_id, last_event_id, request,
            subscribe_factory=alert_stream.subscribe_telemetry,
            stream_name='telemetry',
        ),
        media_type='text/event-stream',
        headers=headers,
    )


@app.get('/stream/sources', summary='SSE stream of Source Optimization Agent events for a workspace')
async def stream_sources(request: Request):
    """Server-Sent Events stream of Source Optimization Agent events (Screen 4).

    Reuses the same authenticated, workspace-scoped, Redis-backed, resumable
    backbone as ``/stream/alerts``. Source events (``type='source'``) are published
    to the workspace stream by the health-check / routing decision paths; the
    frontend filters to ``type='source'`` and falls back to polling when the shared
    stream backend is unavailable. No cross-workspace leakage: the stream key embeds
    the resolved workspace id.
    """
    try:
        user = authenticate_request(request)
    except HTTPException as exc:
        return JSONResponse({'detail': exc.detail, 'code': 'UNAUTHENTICATED'}, status_code=401)
    requested_workspace_id = request.headers.get('x-workspace-id', '').strip()
    try:
        with pg_connection() as connection:
            ensure_pilot_schema(connection)
            workspace_context = resolve_workspace(connection, str(user['id']), requested_workspace_id)
        workspace_id = str(workspace_context['workspace_id'])
    except HTTPException as exc:
        return JSONResponse({'detail': exc.detail, 'code': 'WORKSPACE_ACCESS_DENIED'}, status_code=exc.status_code)
    backend = await alert_stream.connectivity()
    if not backend['connected']:
        return JSONResponse(
            {'detail': 'Shared source stream backend unavailable; use polling fallback.', 'code': 'SOURCE_STREAM_UNAVAILABLE'},
            status_code=503,
        )
    last_event_id = request.headers.get('last-event-id', '').strip() or '$'
    headers = _sse_response_headers(request)
    return StreamingResponse(
        _sse_heartbeat_generator(
            workspace_id, last_event_id, request,
            subscribe_factory=alert_stream.subscribe_onboarding,
            stream_name='onboarding',
        ),
        media_type='text/event-stream',
        headers=headers,
    )


# ---------------------------------------------------------------------------
# Task 6: GDPR delete account endpoint
# ---------------------------------------------------------------------------
@app.delete('/auth/delete-account', summary='Permanently delete account and all personal data (GDPR)')
async def delete_account_endpoint(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    return with_auth_schema_json(lambda: delete_account(payload, request))


# ---------------------------------------------------------------------------
# /api/v1 versioned route aliases (P1-3)
# Authentication for /api/v1/* is enforced by api_key_enforcement_middleware.
# ---------------------------------------------------------------------------


@app.get('/api/v1/alerts', summary='[v1] List alerts', include_in_schema=False)
def v1_alerts_list(
    request: Request,
    severity: str | None = None,
    module: str | None = None,
    target_id: str | None = None,
    status_value: str | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    try:
        return with_auth_schema_json(
            lambda: list_alerts(request, severity=severity, module=module, target_id=target_id, status_value=status_value, source=source)
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error('v1_alerts_list_failed error=%s', exc)
        raise HTTPException(status_code=500, detail='Unable to list alerts.') from None


@app.get('/api/v1/alerts/{alert_id}', summary='[v1] Alert detail', include_in_schema=False)
def v1_alert_detail(alert_id: str, request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: get_alert(alert_id, request))


@app.get('/api/v1/incidents', summary='[v1] List incidents', include_in_schema=False)
def v1_incidents_list(request: Request, status_value: str | None = None) -> dict[str, Any]:
    return with_auth_schema_json(lambda: list_incidents(request, status_value=status_value))


@app.get('/api/v1/assets', summary='[v1] List assets', include_in_schema=False)
def v1_assets_list(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: list_assets(request))


@app.get('/api/v1/targets', summary='[v1] List targets', include_in_schema=False)
def v1_targets_list(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: list_targets(request))


@app.get('/api/v1/detections', summary='[v1] List detections', include_in_schema=False)
def v1_detections_list(
    request: Request,
    limit: int = 50,
    severity: str | None = None,
    status_value: str | None = None,
) -> dict[str, Any]:
    return with_auth_schema_json(
        lambda: list_detections(request, limit=limit, severity=severity, status_value=status_value)
    )


@app.get('/api/v1/monitoring/targets', summary='[v1] List monitoring targets', include_in_schema=False)
def v1_monitoring_targets(request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: list_monitoring_targets(request))


# ---------------------------------------------------------------------------
# v1 Response Action execution endpoints (P0-1)
# Truthful execution state machine: simulated → proposal_created → awaiting_approval
#   → submitted → confirmed / failed / cancelled / unsupported
# ---------------------------------------------------------------------------

@app.get('/api/v1/response-actions', summary='[v1] List response actions', include_in_schema=False)
def v1_response_actions_list(
    request: Request,
    incident_id: str | None = None,
    alert_id: str | None = None,
    status_value: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    return with_auth_schema_json(
        lambda: _normalize_action_list_route_response(
            list_enforcement_actions(request, incident_id=incident_id, alert_id=alert_id, status_value=status_value, limit=limit)
        )
    )


@app.post('/api/v1/response-actions', summary='[v1] Create response action', include_in_schema=False)
def v1_response_actions_create(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return with_auth_schema_json(lambda: _normalize_action_route_response(create_enforcement_action(payload, request)))


@app.post('/api/v1/response-actions/{action_id}/simulate', summary='[v1] Simulate response action (dry-run, no on-chain effect)', include_in_schema=False)
def v1_response_actions_simulate(action_id: str, request: Request) -> dict[str, Any]:
    """
    Simulate a response action. Sets execution_state='simulated'.
    Never submits an on-chain transaction. Safe to call at any time.
    """
    return with_auth_schema_json(lambda: _normalize_action_route_response(execute_enforcement_action(action_id, request)))


@app.post('/api/v1/response-actions/{action_id}/proposal', summary='[v1] Create on-chain proposal for response action', include_in_schema=False)
def v1_response_actions_proposal(action_id: str, request: Request) -> dict[str, Any]:
    """
    Create a Safe/multisig proposal for a live response action.
    Requires LIVE_ACTION_EXECUTION_ENABLED=true and workspace Safe configuration.
    Sets execution_state='proposal_created' on success.
    Does NOT auto-submit destructive actions.
    """
    return with_auth_schema_json(lambda: _normalize_action_route_response(execute_enforcement_action(action_id, request)))


@app.post('/api/v1/response-actions/{action_id}/approve', summary='[v1] Approve response action', include_in_schema=False)
def v1_response_actions_approve(action_id: str, request: Request) -> dict[str, Any]:
    """
    Approve a response action for execution.
    Required for recommended-mode and live destructive actions.
    """
    return with_auth_schema_json(lambda: _normalize_action_route_response(approve_enforcement_action(action_id, request)))


@app.post('/api/v1/response-actions/{action_id}/submit', summary='[v1] Submit approved response action', include_in_schema=False)
def v1_response_actions_submit(action_id: str, request: Request) -> dict[str, Any]:
    """
    Submit an approved proposal for on-chain execution.
    Requires prior approval. Sets execution_state='submitted'.
    """
    return with_auth_schema_json(lambda: _normalize_action_route_response(execute_enforcement_action(action_id, request)))


@app.get('/api/v1/response-actions/{action_id}/status', summary='[v1] Response action execution status', include_in_schema=False)
def v1_response_actions_status(action_id: str, request: Request) -> dict[str, Any]:
    """
    Returns current execution state for a response action.
    Execution states: simulated | proposal_created | awaiting_approval |
    submitted | confirmed | failed | cancelled | unsupported
    """
    def _get_status():
        result = list_enforcement_actions(request, limit=500)
        actions = result.get('actions') or []
        action = next((a for a in actions if str(a.get('id', '')) == action_id), None)
        if action is None:
            raise HTTPException(status_code=404, detail=f'Response action {action_id!r} not found.')
        return _normalize_action_route_response(action)
    return with_auth_schema_json(_get_status)


@app.get('/api/v1/response-actions/{action_id}', summary='[v1] Response action detail', include_in_schema=False)
def v1_response_action_detail(action_id: str, request: Request) -> dict[str, Any]:
    def _get_detail():
        result = list_enforcement_actions(request, limit=500)
        actions = result.get('actions') or []
        action = next((a for a in actions if str(a.get('id', '')) == action_id), None)
        if action is None:
            raise HTTPException(status_code=404, detail=f'Response action {action_id!r} not found.')
        return _normalize_action_route_response(action)
    return with_auth_schema_json(_get_detail)
