"""Autonomous Onboarding Agent — durable orchestration, API, worker, activation.

This module owns persistence, workspace authorization, the background job
lifecycle, live SSE fan-out, proposal generation, idempotent activation and the
downloadable discovery report. All deterministic blockchain inspection and RPC
benchmarking lives in :mod:`onboarding_discovery` (no DB, no framework, no LLM).

Design rules honored here:
  * Postgres is the source of truth for every session / step / finding / benchmark
    / proposal / approval / job row. Redis carries only live SSE fan-out.
  * Every step is persisted BEFORE its SSE event is published, so a refresh always
    restores the true state.
  * Discovery + benchmark run as a durable, distributed-safe background job
    (claimed via a conditional UPDATE) so they survive an API restart and are
    multi-pod safe.
  * Activation is idempotent (unique idempotency key + upserts) and writes through
    the SAME assets → targets → monitored-system path the UI and workers consume.
  * RPC endpoint URLs (which may embed API keys) are encrypted at rest via
    secret_crypto; only host + a redacted URL are ever stored or returned.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import uuid
from typing import Any

from services.api.app import pilot
from services.api.app import onboarding_discovery as disc
from services.api.app import secret_crypto
from services.api.app.onboarding_discovery import (
    BenchmarkEndpoint, HttpRpcTransport, RpcTransport,
)

logger = logging.getLogger(__name__)

HTTPException = pilot.HTTPException
status = pilot.status

# ---------------------------------------------------------------------------
# Step catalog (the AI Onboarding Agent execution timeline).
# ---------------------------------------------------------------------------
STEP_DEFS: list[tuple[str, str]] = [
    ('validate_inputs', 'Validating contract address'),
    ('connect_chain', 'Connecting to network'),
    ('verify_bytecode', 'Verifying deployed bytecode'),
    ('detect_standard', 'Detecting ERC standard'),
    ('resolve_proxy', 'Resolving proxy implementation'),
    ('discover_roles', 'Discovering privileged roles'),
    ('benchmark_rpc', 'Benchmarking RPC providers'),
    ('discover_oracles', 'Discovering oracle dependencies'),
    ('generate_policies', 'Generating monitoring policies'),
    ('create_config', 'Creating workspace configuration'),
]
STEP_TITLES = dict(STEP_DEFS)
STEP_ORDER = [key for key, _ in STEP_DEFS]

MONITORING_MODES = ('recommended', 'strict', 'custom')

_ENCRYPTION_AAD = 'onboarding_rpc_endpoint'


def _json_dumps(value: Any) -> str:
    return json.dumps(value, separators=(',', ':'), default=str)


def _now_iso() -> str:
    return pilot.utc_now().isoformat()


def _commit_sha() -> str | None:
    return (os.getenv('RAILWAY_GIT_COMMIT_SHA') or os.getenv('GIT_COMMIT_SHA') or os.getenv('SOURCE_VERSION') or '').strip() or None


def _worker_id() -> str:
    return f"{os.getenv('HOSTNAME', 'local')}:{os.getpid()}"


# ---------------------------------------------------------------------------
# Redaction (defense in depth on top of secret_crypto).
# ---------------------------------------------------------------------------
def redact_text(value: str | None) -> str | None:
    """Strip anything that looks like an embedded RPC key from free text."""
    if not value:
        return value
    return disc.redact_rpc_url(value) if '://' in str(value) else str(value)


# ===========================================================================
# Input parsing / validation
# ===========================================================================
def _parse_session_inputs(payload: dict[str, Any]) -> dict[str, Any]:
    workspace_name = str(payload.get('workspace_name', '')).strip()
    chain_id = disc.resolve_chain_id(payload.get('chain_id') if payload.get('chain_id') is not None else payload.get('network'))
    if chain_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail='A supported network / chain id is required.')
    primary_contract_raw = str(payload.get('primary_contract', '')).strip()
    try:
        primary_contract = disc.validate_contract_address(primary_contract_raw)
    except disc.AddressValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail={'code': exc.code, 'message': exc.message}) from exc

    additional: list[str] = []
    for raw in (payload.get('additional_contracts') or []):
        try:
            additional.append(disc.validate_contract_address(str(raw)))
        except disc.AddressValidationError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail={'code': exc.code, 'message': f'Additional contract invalid: {exc.message}'}) from exc

    rpc_endpoints: list[dict[str, str]] = []
    seen_hosts: set[str] = set()
    for raw in (payload.get('rpc_endpoints') or []):
        url = str(raw).strip()
        if not url:
            continue
        try:
            host, redacted = disc.validate_rpc_url(url)
        except disc.SsrfValidationError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail={'code': exc.code, 'message': exc.message}) from exc
        key = redacted.lower()
        if key in seen_hosts:
            continue
        seen_hosts.add(key)
        rpc_endpoints.append({'url': url, 'host': host, 'redacted': redacted})

    oracle_addresses: list[str] = []
    for raw in (payload.get('oracle_addresses') or []):
        try:
            oracle_addresses.append(disc.validate_contract_address(str(raw)))
        except disc.AddressValidationError:
            continue  # oracle addresses are advisory; skip invalid rather than fail
    admin_address = None
    if payload.get('admin_address'):
        try:
            admin_address = disc.validate_contract_address(str(payload.get('admin_address')))
        except disc.AddressValidationError:
            admin_address = None

    monitoring_mode = str(payload.get('monitoring_mode', 'recommended')).strip().lower()
    if monitoring_mode not in MONITORING_MODES:
        monitoring_mode = 'recommended'

    return {
        'workspace_name': workspace_name or None,
        'chain_id': chain_id,
        'chain_network': disc.chain_network_name(chain_id),
        'primary_contract': primary_contract,
        'additional_contracts': additional,
        'rpc_endpoints': rpc_endpoints,
        'oracle_addresses': oracle_addresses,
        'admin_address': admin_address,
        'protocol_name': (str(payload.get('protocol_name', '')).strip() or None),
        'expected_standard': (str(payload.get('expected_standard', '')).strip() or None),
        'monitoring_mode': monitoring_mode,
    }


# ===========================================================================
# Persistence helpers
# ===========================================================================
def _insert_input(connection: Any, *, session_id: str, workspace_id: str, input_type: str,
                  value: str | None, encrypted_value: str | None = None, endpoint_host: str | None = None,
                  label: str | None = None, metadata: dict[str, Any] | None = None) -> None:
    connection.execute(
        '''
        INSERT INTO onboarding_inputs (id, session_id, workspace_id, input_type, value, encrypted_value, endpoint_host, label, metadata)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
        ON CONFLICT (session_id, input_type, value) DO NOTHING
        ''',
        (str(uuid.uuid4()), session_id, workspace_id, input_type, value, encrypted_value, endpoint_host, label,
         _json_dumps(metadata or {})),
    )


def _seed_steps(connection: Any, *, session_id: str, workspace_id: str, chain_network: str) -> None:
    for seq, (key, title) in enumerate(STEP_DEFS):
        if key == 'connect_chain':
            title = f'Connecting to {chain_network}'
        connection.execute(
            '''
            INSERT INTO onboarding_steps (id, session_id, workspace_id, step_key, sequence, status, title)
            VALUES (%s, %s, %s, %s, %s, 'pending', %s)
            ON CONFLICT (session_id, step_key) DO NOTHING
            ''',
            (str(uuid.uuid4()), session_id, workspace_id, key, seq, title),
        )


def _set_step(connection: Any, *, session_id: str, workspace_id: str, step_key: str, status_value: str,
              result_summary: str | None = None, evidence: dict[str, Any] | None = None,
              error_code: str | None = None, error_message: str | None = None,
              increment_attempt: bool = False) -> None:
    started = 'started_at = COALESCE(started_at, NOW()),' if status_value == 'running' else ''
    completed = 'completed_at = NOW(),' if status_value in ('completed', 'failed', 'needs_attention') else ''
    attempt = 'attempts = attempts + 1,' if increment_attempt else ''
    connection.execute(
        f'''
        UPDATE onboarding_steps
        SET status = %s, {started} {completed} {attempt}
            result_summary = COALESCE(%s, result_summary),
            evidence = COALESCE(%s::jsonb, evidence),
            error_code = %s, error_message = %s, updated_at = NOW()
        WHERE session_id = %s AND step_key = %s
        ''',
        (status_value, result_summary,
         (None if evidence is None else _json_dumps(evidence)),
         error_code, redact_text(error_message), session_id, step_key),
    )


def _persist_findings(connection: Any, *, session_id: str, workspace_id: str, findings: list[disc.Finding]) -> int:
    inserted = 0
    for f in findings:
        d = f.to_dict()
        value_text = d['value'] if isinstance(d['value'], str) else _json_dumps(d['value'])
        res = connection.execute(
            '''
            INSERT INTO discovery_findings
                (id, session_id, workspace_id, finding_type, value, detection_method, source_contract,
                 block_number, rpc_source_host, evidence, evidence_hash, confidence)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
            ON CONFLICT (session_id, finding_type, value) DO NOTHING
            RETURNING id
            ''',
            (str(uuid.uuid4()), session_id, workspace_id, d['finding_type'], value_text, d['detection_method'],
             d['source_contract'], d['block_number'], d['rpc_source_host'], _json_dumps(d['evidence']),
             d['evidence_hash'], d['confidence']),
        ).fetchone()
        if res is not None:
            inserted += 1
    return inserted


def _load_session_row(connection: Any, *, session_id: str, workspace_id: str) -> dict[str, Any] | None:
    return connection.execute(
        'SELECT * FROM onboarding_sessions WHERE id = %s AND workspace_id = %s',
        (session_id, workspace_id),
    ).fetchone()


def _update_session(connection: Any, *, session_id: str, **fields: Any) -> None:
    if not fields:
        return
    sets = ', '.join(f'{k} = %s' for k in fields)
    connection.execute(
        f'UPDATE onboarding_sessions SET {sets}, updated_at = NOW() WHERE id = %s',
        (*fields.values(), session_id),
    )


# ===========================================================================
# SSE fan-out
# ===========================================================================
def publish_event(workspace_id: str, session_id: str, event_type: str, extra: dict[str, Any] | None = None) -> None:
    """Publish a live onboarding event to the workspace Redis stream (best effort)."""
    try:
        from services.api.app.domains import alert_stream
        payload = {'type': 'onboarding', 'event_type': event_type, 'session_id': session_id,
                   'workspace_id': workspace_id, 'at': _now_iso()}
        if extra:
            payload.update(extra)
        alert_stream.publish_onboarding(workspace_id, payload)
    except Exception as exc:  # pragma: no cover - Redis optional; DB remains source of truth
        logger.info('onboarding_sse_publish_skipped session_id=%s event=%s reason=%s', session_id, event_type, type(exc).__name__)


# ===========================================================================
# Serialization
# ===========================================================================
def _serialize_step(row: dict[str, Any]) -> dict[str, Any]:
    return {
        'step_key': row.get('step_key'),
        'title': row.get('title'),
        'sequence': row.get('sequence'),
        'status': row.get('status'),
        'result_summary': row.get('result_summary'),
        'evidence': row.get('evidence') or {},
        'error_code': row.get('error_code'),
        'error_message': row.get('error_message'),
        'attempts': row.get('attempts'),
        'started_at': _iso(row.get('started_at')),
        'completed_at': _iso(row.get('completed_at')),
    }


def _iso(value: Any) -> Any:
    if value is None:
        return None
    try:
        return value.isoformat()
    except AttributeError:
        return str(value)


def _serialize_finding(row: dict[str, Any]) -> dict[str, Any]:
    return {
        'finding_type': row.get('finding_type'),
        'value': _maybe_json(row.get('value')),
        'detection_method': row.get('detection_method'),
        'confidence': row.get('confidence'),
        'source_contract': row.get('source_contract'),
        'block_number': row.get('block_number'),
        'rpc_source_host': row.get('rpc_source_host'),
        'evidence': row.get('evidence') or {},
        'evidence_hash': row.get('evidence_hash'),
        'created_at': _iso(row.get('created_at')),
    }


def _maybe_json(value: Any) -> Any:
    if isinstance(value, str) and value and value[0] in '[{':
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


def _serialize_benchmark_result(row: dict[str, Any]) -> dict[str, Any]:
    return {
        'endpoint_host': row.get('endpoint_host'),
        'redacted_url': row.get('redacted_url'),
        'connection_status': row.get('connection_status'),
        'median_latency_ms': row.get('median_latency_ms'),
        'p95_latency_ms': row.get('p95_latency_ms'),
        'success_rate': _num(row.get('success_rate')),
        'error_rate': _num(row.get('error_rate')),
        'timeout_count': row.get('timeout_count'),
        'error_count': row.get('error_count'),
        'latest_block': row.get('latest_block'),
        'block_lag': row.get('block_lag'),
        'chain_id_returned': row.get('chain_id_returned'),
        'chain_id_ok': row.get('chain_id_ok'),
        'rate_limited': row.get('rate_limited'),
        'archive_supported': row.get('archive_supported'),
        'score': _num(row.get('score')),
        'recommendation': row.get('recommendation'),
        'reason': row.get('reason'),
    }


def _num(value: Any) -> Any:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def _serialize_session(row: dict[str, Any]) -> dict[str, Any]:
    return {
        'id': str(row.get('id')),
        'workspace_id': str(row.get('workspace_id')),
        'status': row.get('status'),
        'current_step': row.get('current_step'),
        'selected_chain_id': row.get('selected_chain_id'),
        'chain_network': row.get('chain_network'),
        'primary_contract': row.get('primary_contract'),
        'protocol_name': row.get('protocol_name'),
        'monitoring_mode': row.get('monitoring_mode'),
        'workspace_name': row.get('workspace_name'),
        'proposal_version': row.get('proposal_version'),
        'activation_status': row.get('activation_status'),
        'error_code': row.get('error_code'),
        'error_message': row.get('error_message'),
        'correlation_id': row.get('correlation_id'),
        'created_at': _iso(row.get('created_at')),
        'updated_at': _iso(row.get('updated_at')),
        'completed_at': _iso(row.get('completed_at')),
    }


def build_session_snapshot(connection: Any, *, session_id: str, workspace_id: str) -> dict[str, Any]:
    session = _load_session_row(connection, session_id=session_id, workspace_id=workspace_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Onboarding session not found.')
    steps = connection.execute(
        'SELECT * FROM onboarding_steps WHERE session_id = %s ORDER BY sequence ASC', (session_id,),
    ).fetchall()
    findings = connection.execute(
        'SELECT * FROM discovery_findings WHERE session_id = %s ORDER BY created_at ASC', (session_id,),
    ).fetchall()
    bench_run = connection.execute(
        'SELECT * FROM rpc_benchmark_runs WHERE session_id = %s ORDER BY created_at DESC LIMIT 1', (session_id,),
    ).fetchone()
    bench_results = []
    if bench_run is not None:
        bench_results = connection.execute(
            'SELECT * FROM rpc_benchmark_results WHERE run_id = %s ORDER BY score DESC NULLS LAST', (bench_run['id'],),
        ).fetchall()
    proposal = connection.execute(
        'SELECT * FROM generated_workspace_proposals WHERE session_id = %s ORDER BY version DESC LIMIT 1', (session_id,),
    ).fetchone()
    approvals = connection.execute(
        'SELECT proposal_version, decision, notes, created_at FROM onboarding_approvals WHERE session_id = %s ORDER BY created_at DESC',
        (session_id,),
    ).fetchall()

    inputs = connection.execute(
        'SELECT input_type, value, endpoint_host, label FROM onboarding_inputs WHERE session_id = %s ORDER BY created_at ASC',
        (session_id,),
    ).fetchall()

    latest_version = int(proposal['version']) if proposal else 0
    approved_versions = {int(a['proposal_version']) for a in approvals if a['decision'] == 'approved'}
    proposal_json = proposal['proposal'] if proposal else None
    if isinstance(proposal_json, str):
        proposal_json = json.loads(proposal_json)
    summary_json = proposal['summary'] if proposal else None
    if isinstance(summary_json, str):
        summary_json = json.loads(summary_json)

    findings_serialized = [_serialize_finding(dict(f)) for f in findings]
    review_findings = [f for f in findings_serialized if f['confidence'] in ('unknown', 'requires_review')]
    verified = [f for f in findings_serialized if f['confidence'] == 'confirmed']

    return {
        'session': _serialize_session(dict(session)),
        'steps': [_serialize_step(dict(s)) for s in steps],
        'findings': findings_serialized,
        'benchmark': {
            'run': (_serialize_benchmark_run(dict(bench_run)) if bench_run else None),
            'results': [_serialize_benchmark_result(dict(r)) for r in bench_results],
        },
        'proposal': ({
            'version': latest_version,
            'proposal': proposal_json,
            'summary': summary_json,
            'ai_summary': proposal['ai_summary'] if proposal else None,
            'ai_available': bool(proposal['ai_available']) if proposal else False,
            'approved': latest_version in approved_versions,
        } if proposal else None),
        'approvals': [{'proposal_version': int(a['proposal_version']), 'decision': a['decision'],
                       'notes': a['notes'], 'created_at': _iso(a['created_at'])} for a in approvals],
        'inputs': [{'input_type': i['input_type'], 'value': i['value'], 'endpoint_host': i['endpoint_host'],
                    'label': i['label']} for i in inputs],
        'agent': {
            'verified_findings': len(verified),
            'review_findings': len(review_findings),
            'total_findings': len(findings_serialized),
            'total_steps': len(STEP_DEFS),
            'completed_steps': sum(1 for s in steps if s['status'] == 'completed'),
        },
    }


def _serialize_benchmark_run(row: dict[str, Any]) -> dict[str, Any]:
    return {
        'status': row.get('status'),
        'best_block': row.get('best_block'),
        'primary_host': row.get('primary_host'),
        'fallback_host': row.get('fallback_host'),
        'explanation': row.get('explanation'),
        'completed_at': _iso(row.get('completed_at')),
    }


# ===========================================================================
# Route: create / resume session
# ===========================================================================
def create_or_resume_session(payload: dict[str, Any], request: Any) -> dict[str, Any]:
    pilot.require_live_mode()
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        user, workspace_context = pilot._require_workspace_permission(connection, request, 'monitoring.configure')
        workspace_id = workspace_context['workspace_id']

        resume = bool(payload.get('resume', True)) and not payload.get('force_new')
        if resume:
            existing = connection.execute(
                '''SELECT id FROM onboarding_sessions
                   WHERE workspace_id = %s AND status NOT IN ('completed', 'failed')
                   ORDER BY updated_at DESC LIMIT 1''',
                (workspace_id,),
            ).fetchone()
            if existing is not None and not payload.get('primary_contract'):
                snapshot = build_session_snapshot(connection, session_id=str(existing['id']), workspace_id=workspace_id)
                connection.commit()
                return snapshot

        parsed = _parse_session_inputs(payload)

        # Duplicate-contract guard within the workspace (existing production asset registry).
        dup = connection.execute(
            '''SELECT id FROM assets WHERE workspace_id = %s AND deleted_at IS NULL
               AND lower(identifier) = lower(%s)''',
            (workspace_id, parsed['primary_contract']),
        ).fetchone()
        duplicate_warning = None
        if dup is not None:
            duplicate_warning = 'This contract is already registered as a protected asset in this workspace.'

        # Close any prior active session so the one-active-per-workspace invariant holds.
        connection.execute(
            '''UPDATE onboarding_sessions SET status = 'failed', error_code = 'superseded',
                   error_message = 'Superseded by a new onboarding session', updated_at = NOW(), completed_at = NOW()
               WHERE workspace_id = %s AND status NOT IN ('completed', 'failed')''',
            (workspace_id,),
        )

        session_id = str(uuid.uuid4())
        correlation_id = str(request.headers.get('x-request-id') or '').strip() or f'onb_{secrets.token_hex(6)}'
        connection.execute(
            '''
            INSERT INTO onboarding_sessions
                (id, workspace_id, user_id, status, current_step, selected_chain_id, chain_network,
                 primary_contract, protocol_name, monitoring_mode, workspace_name, correlation_id)
            VALUES (%s, %s, %s, 'draft', %s, %s, %s, %s, %s, %s, %s, %s)
            ''',
            (session_id, workspace_id, user['id'], STEP_ORDER[0], parsed['chain_id'], parsed['chain_network'],
             parsed['primary_contract'], parsed['protocol_name'], parsed['monitoring_mode'],
             parsed['workspace_name'], correlation_id),
        )
        _seed_steps(connection, session_id=session_id, workspace_id=workspace_id, chain_network=parsed['chain_network'])

        _insert_input(connection, session_id=session_id, workspace_id=workspace_id,
                      input_type='primary_contract', value=parsed['primary_contract'])
        for addr in parsed['additional_contracts']:
            _insert_input(connection, session_id=session_id, workspace_id=workspace_id,
                          input_type='additional_contract', value=addr)
        for ep in parsed['rpc_endpoints']:
            encrypted = secret_crypto.encrypt_secret(ep['url'], aad=_ENCRYPTION_AAD)
            _insert_input(connection, session_id=session_id, workspace_id=workspace_id,
                          input_type='rpc_endpoint', value=ep['redacted'], encrypted_value=encrypted,
                          endpoint_host=ep['host'])
        for addr in parsed['oracle_addresses']:
            _insert_input(connection, session_id=session_id, workspace_id=workspace_id,
                          input_type='oracle_address', value=addr)
        if parsed['admin_address']:
            _insert_input(connection, session_id=session_id, workspace_id=workspace_id,
                          input_type='admin_address', value=parsed['admin_address'])
        if parsed['expected_standard']:
            _insert_input(connection, session_id=session_id, workspace_id=workspace_id,
                          input_type='expected_standard', value=parsed['expected_standard'])

        _audit(connection, request=request, user_id=user['id'], workspace_id=workspace_id, session_id=session_id,
               action='onboarding.session_created',
               metadata={'chain_id': parsed['chain_id'], 'primary_contract': parsed['primary_contract'],
                         'monitoring_mode': parsed['monitoring_mode'], 'rpc_endpoint_count': len(parsed['rpc_endpoints'])})
        snapshot = build_session_snapshot(connection, session_id=session_id, workspace_id=workspace_id)
        if duplicate_warning:
            snapshot['warnings'] = [duplicate_warning]
        connection.commit()
        publish_event(workspace_id, session_id, 'session_created')
        return snapshot


def get_session(session_id: str, request: Any) -> dict[str, Any]:
    pilot.require_live_mode()
    _validate_uuid(session_id)
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        user = pilot.authenticate_with_connection(connection, request)
        workspace_context = pilot.resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        snapshot = build_session_snapshot(connection, session_id=session_id,
                                          workspace_id=workspace_context['workspace_id'])
        connection.commit()
        return snapshot


# ===========================================================================
# Route: start discovery (enqueue durable job)
# ===========================================================================
def start_discovery(session_id: str, request: Any) -> dict[str, Any]:
    pilot.require_live_mode()
    _validate_uuid(session_id)
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        user, workspace_context = pilot._require_workspace_permission(connection, request, 'monitoring.configure')
        workspace_id = workspace_context['workspace_id']
        session = _load_session_row(connection, session_id=session_id, workspace_id=workspace_id)
        if session is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Onboarding session not found.')
        if session['status'] in ('activating', 'completed'):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail='Discovery cannot run once activation has started.')
        run = _enqueue_run(connection, session_id=session_id, workspace_id=workspace_id, run_type='discover',
                           idempotency_key=f'discover:{session_id}:{secrets.token_hex(4)}',
                           dedupe_active=True)
        _update_session(connection, session_id=session_id, status='discovering', current_step=STEP_ORDER[0],
                        error_code=None, error_message=None)
        _audit(connection, request=request, user_id=user['id'], workspace_id=workspace_id, session_id=session_id,
               action='onboarding.discovery_started', metadata={'run_id': run['id']})
        snapshot = build_session_snapshot(connection, session_id=session_id, workspace_id=workspace_id)
        connection.commit()
        publish_event(workspace_id, session_id, 'discovery_queued', {'run_id': run['id']})
    # Best-effort inline execution for single-process / test deployments where no
    # dedicated worker is running. Safe + idempotent: the worker claim is a
    # conditional UPDATE, so a concurrent dedicated worker still processes at most once.
    _maybe_run_inline(session_id)
    return _reload_snapshot(session_id, workspace_id)


def retry_session(session_id: str, request: Any) -> dict[str, Any]:
    pilot.require_live_mode()
    _validate_uuid(session_id)
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        user, workspace_context = pilot._require_workspace_permission(connection, request, 'monitoring.configure')
        workspace_id = workspace_context['workspace_id']
        session = _load_session_row(connection, session_id=session_id, workspace_id=workspace_id)
        if session is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Onboarding session not found.')
        # Reset only failed / incomplete steps to pending so the pipeline resumes them.
        connection.execute(
            '''UPDATE onboarding_steps SET status = 'pending', error_code = NULL, error_message = NULL, updated_at = NOW()
               WHERE session_id = %s AND status IN ('failed', 'running', 'needs_attention')''',
            (session_id,),
        )
        run = _enqueue_run(connection, session_id=session_id, workspace_id=workspace_id, run_type='discover',
                           idempotency_key=f'retry:{session_id}:{secrets.token_hex(4)}', dedupe_active=True)
        _update_session(connection, session_id=session_id, status='discovering', error_code=None, error_message=None)
        _audit(connection, request=request, user_id=user['id'], workspace_id=workspace_id, session_id=session_id,
               action='onboarding.retry', metadata={'run_id': run['id']})
        connection.commit()
        publish_event(workspace_id, session_id, 'retry_queued', {'run_id': run['id']})
    _maybe_run_inline(session_id)
    return _reload_snapshot(session_id, workspace_id)


def rerun_rpc_benchmark(session_id: str, request: Any) -> dict[str, Any]:
    """Re-test providers only (does not re-run contract discovery)."""
    pilot.require_live_mode()
    _validate_uuid(session_id)
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        user, workspace_context = pilot._require_workspace_permission(connection, request, 'monitoring.configure')
        workspace_id = workspace_context['workspace_id']
        session = _load_session_row(connection, session_id=session_id, workspace_id=workspace_id)
        if session is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Onboarding session not found.')
        _set_step(connection, session_id=session_id, workspace_id=workspace_id, step_key='benchmark_rpc', status_value='pending')
        _audit(connection, request=request, user_id=user['id'], workspace_id=workspace_id, session_id=session_id,
               action='onboarding.rpc_benchmark_rerun', metadata={})
        connection.commit()
    # Run the benchmark phase inline (bounded, quick).
    with pilot.pg_connection() as connection:
        session = _load_session_row(connection, session_id=session_id, workspace_id=workspace_id)
        endpoints = _build_benchmark_endpoints(connection, session_id=session_id, chain_id=session['selected_chain_id'])
        _run_benchmark_phase(connection, session=session, endpoints=endpoints)
        connection.commit()
    publish_event(workspace_id, session_id, 'rpc_benchmark_completed')
    return _reload_snapshot(session_id, workspace_id)


def generate_proposal(session_id: str, request: Any) -> dict[str, Any]:
    pilot.require_live_mode()
    _validate_uuid(session_id)
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        user, workspace_context = pilot._require_workspace_permission(connection, request, 'monitoring.configure')
        workspace_id = workspace_context['workspace_id']
        session = _load_session_row(connection, session_id=session_id, workspace_id=workspace_id)
        if session is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Onboarding session not found.')
        version = _build_and_store_proposal(connection, session=session, user_id=user['id'], request=request)
        connection.commit()
        publish_event(workspace_id, session_id, 'proposal_generated', {'version': version})
    return _reload_snapshot(session_id, workspace_id)


# ===========================================================================
# Route: approve
# ===========================================================================
def approve_session(session_id: str, payload: dict[str, Any], request: Any) -> dict[str, Any]:
    pilot.require_live_mode()
    _validate_uuid(session_id)
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        user, workspace_context = pilot._require_workspace_permission(connection, request, 'monitoring.configure')
        workspace_id = workspace_context['workspace_id']
        session = _load_session_row(connection, session_id=session_id, workspace_id=workspace_id)
        if session is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Onboarding session not found.')
        proposal = connection.execute(
            'SELECT version FROM generated_workspace_proposals WHERE session_id = %s ORDER BY version DESC LIMIT 1',
            (session_id,),
        ).fetchone()
        if proposal is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail='Generate a proposal before approving.')
        version = int(proposal['version'])
        decision = str(payload.get('decision', 'approved')).strip().lower()
        if decision not in ('approved', 'rejected'):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='decision must be approved or rejected.')
        connection.execute(
            '''INSERT INTO onboarding_approvals (id, session_id, workspace_id, user_id, proposal_version, decision, notes)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (session_id, proposal_version, decision) DO UPDATE SET notes = EXCLUDED.notes''',
            (str(uuid.uuid4()), session_id, workspace_id, user['id'], version, decision,
             redact_text(str(payload.get('notes', '')).strip() or None)),
        )
        if decision == 'approved':
            _update_session(connection, session_id=session_id, status='approved', activation_status='pending')
        _audit(connection, request=request, user_id=user['id'], workspace_id=workspace_id, session_id=session_id,
               action='onboarding.proposal_reviewed', previous_state={'status': session['status']},
               new_state={'decision': decision, 'proposal_version': version},
               metadata={'decision': decision, 'proposal_version': version})
        snapshot = build_session_snapshot(connection, session_id=session_id, workspace_id=workspace_id)
        connection.commit()
        publish_event(workspace_id, session_id, 'proposal_reviewed', {'decision': decision, 'version': version})
        return snapshot


# ===========================================================================
# Route: activate (idempotent)
# ===========================================================================
def activate_session(session_id: str, request: Any) -> dict[str, Any]:
    pilot.require_live_mode()
    _validate_uuid(session_id)
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        user, workspace_context = pilot._require_workspace_permission(connection, request, 'monitoring.configure')
        workspace_id = workspace_context['workspace_id']
        session = _load_session_row(connection, session_id=session_id, workspace_id=workspace_id)
        if session is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Onboarding session not found.')

        proposal_row = connection.execute(
            'SELECT * FROM generated_workspace_proposals WHERE session_id = %s ORDER BY version DESC LIMIT 1',
            (session_id,),
        ).fetchone()
        if proposal_row is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='No proposal to activate.')
        version = int(proposal_row['version'])
        approved = connection.execute(
            '''SELECT 1 FROM onboarding_approvals WHERE session_id = %s AND proposal_version = %s AND decision = 'approved' ''',
            (session_id, version),
        ).fetchone()
        if approved is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail='Activation requires an approved proposal. Approve the configuration first.')

        idem_key = f'activate:{session_id}:v{version}'
        # Idempotency: a completed activate run replays its stored result.
        existing = connection.execute(
            'SELECT status, result FROM onboarding_agent_runs WHERE idempotency_key = %s', (idem_key,),
        ).fetchone()
        if existing is not None and existing['status'] == 'completed':
            result = existing['result']
            if isinstance(result, str):
                result = json.loads(result)
            connection.commit()
            return {'idempotent_replay': True, **(result or {}),
                    **build_session_snapshot(connection, session_id=session_id, workspace_id=workspace_id)}

        run = _enqueue_run(connection, session_id=session_id, workspace_id=workspace_id, run_type='activate',
                           idempotency_key=idem_key, dedupe_active=False, claim=True)
        _update_session(connection, session_id=session_id, status='activating', activation_status='in_progress')

        proposal_json = proposal_row['proposal']
        if isinstance(proposal_json, str):
            proposal_json = json.loads(proposal_json)

        try:
            result = _perform_activation(connection, session=session, workspace_id=workspace_id,
                                         user=user, request=request, proposal=proposal_json, version=version)
        except HTTPException:
            raise
        except Exception as exc:
            connection.execute(
                "UPDATE onboarding_agent_runs SET status = 'failed', error_message = %s, finished_at = NOW(), updated_at = NOW() WHERE id = %s",
                (redact_text(str(exc)[:300]), run['id']),
            )
            _update_session(connection, session_id=session_id, status='failed', activation_status='failed',
                            error_code='activation_failed', error_message='Activation failed. No partial changes were committed.')
            _audit(connection, request=request, user_id=user['id'], workspace_id=workspace_id, session_id=session_id,
                   action='onboarding.activation_failed', metadata={'error': type(exc).__name__})
            connection.commit()
            logger.exception('onboarding_activation_failed session_id=%s', session_id)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                                detail='Activation failed. No partial changes were committed.') from exc

        connection.execute(
            "UPDATE onboarding_agent_runs SET status = 'completed', result = %s::jsonb, finished_at = NOW(), updated_at = NOW() WHERE id = %s",
            (_json_dumps(result), run['id']),
        )
        _update_session(connection, session_id=session_id, status='completed', activation_status='completed',
                        current_step='create_config')
        connection.execute(
            "UPDATE onboarding_sessions SET completed_at = NOW() WHERE id = %s AND completed_at IS NULL", (session_id,),
        )
        _audit(connection, request=request, user_id=user['id'], workspace_id=workspace_id, session_id=session_id,
               action='onboarding.workspace_activated',
               new_state={'assets': result['assets_protected'], 'targets': result['monitoring_sources_active'],
                          'rules': result['rules_enabled']},
               metadata=result)
        snapshot = build_session_snapshot(connection, session_id=session_id, workspace_id=workspace_id)
        connection.commit()
        publish_event(workspace_id, session_id, 'workspace_activated', result)
        return {**result, **snapshot}


def _perform_activation(connection: Any, *, session: dict[str, Any], workspace_id: str, user: dict[str, Any],
                        request: Any, proposal: dict[str, Any], version: int) -> dict[str, Any]:
    """Idempotently create assets, targets and monitoring through the production path."""
    chain_network = session['chain_network'] or disc.chain_network_name(session['selected_chain_id'])
    chain_id = session['selected_chain_id']
    assets_created = 0
    assets_reused = 0
    targets_created = 0
    monitoring_active = 0
    rules_enabled = 0
    created_asset_ids: dict[str, str] = {}

    for asset_spec in proposal.get('protected_assets', []):
        identifier = str(asset_spec.get('identifier') or '').strip()
        if not identifier:
            continue
        existing = connection.execute(
            '''SELECT id FROM assets WHERE workspace_id = %s AND deleted_at IS NULL
               AND lower(identifier) = lower(%s) AND lower(chain_network) = lower(%s)''',
            (workspace_id, identifier, chain_network),
        ).fetchone()
        if existing is not None:
            created_asset_ids[identifier] = str(existing['id'])
            assets_reused += 1
            continue
        asset_id = str(uuid.uuid4())
        connection.execute(
            '''
            INSERT INTO assets (id, workspace_id, name, description, asset_type, chain_network, identifier,
                                asset_class, risk_tier, enabled, asset_symbol, token_contract_address,
                                created_by_user_id, updated_by_user_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s, %s, %s, %s)
            ''',
            (asset_id, workspace_id, asset_spec.get('name') or identifier[:10],
             asset_spec.get('description') or 'Created by Decoda Onboarding Agent',
             asset_spec.get('asset_type') or 'smart_contract', chain_network, identifier,
             asset_spec.get('asset_class'), asset_spec.get('risk_tier') or 'medium',
             asset_spec.get('symbol'), identifier, user['id'], user['id']),
        )
        created_asset_ids[identifier] = asset_id
        assets_created += 1

    for target_spec in proposal.get('monitoring_targets', []):
        identifier = str(target_spec.get('contract_identifier') or '').strip()
        asset_id = created_asset_ids.get(identifier)
        if not asset_id:
            continue
        name = target_spec.get('name') or f'{chain_network} monitor'
        target_type = target_spec.get('target_type') or 'contract'
        existing_target = connection.execute(
            '''SELECT id FROM targets WHERE workspace_id = %s AND asset_id = %s AND name = %s
               AND target_type = %s AND deleted_at IS NULL LIMIT 1''',
            (workspace_id, asset_id, name, target_type),
        ).fetchone()
        target_metadata = {
            'token_address': identifier,
            'asset_label': target_spec.get('name'),
            'onboarding_session_id': str(session['id']),
            'baseline_rules': proposal.get('baseline_rules', []),
            'rpc_sources': proposal.get('rpc_sources', {}),
        }
        interval = int(target_spec.get('monitoring_interval_seconds') or 300)
        if existing_target is not None:
            target_id = str(existing_target['id'])
            connection.execute(
                '''UPDATE targets SET monitoring_enabled = TRUE, enabled = TRUE, is_active = TRUE,
                       target_metadata = %s::jsonb, updated_at = NOW() WHERE id = %s AND workspace_id = %s''',
                (_json_dumps(target_metadata), target_id, workspace_id),
            )
        else:
            target_id = str(uuid.uuid4())
            connection.execute(
                '''
                INSERT INTO targets (id, workspace_id, name, target_type, chain_network, contract_identifier,
                                     asset_type, severity_preference, enabled, asset_id, chain_id, target_metadata,
                                     monitoring_enabled, monitoring_mode, monitoring_interval_seconds, severity_threshold,
                                     auto_create_alerts, auto_create_incidents, notification_channels,
                                     monitored_by_workspace_id, is_active, created_by_user_id, updated_by_user_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s::uuid, %s, %s::jsonb, TRUE, %s, %s, %s, TRUE, FALSE, %s::jsonb, %s, TRUE, %s, %s)
                ''',
                (target_id, workspace_id, name, target_type, chain_network, identifier,
                 target_spec.get('asset_type') or 'tokenized_rwa',
                 target_spec.get('severity_preference') or 'medium', asset_id, chain_id,
                 _json_dumps(target_metadata),
                 session['monitoring_mode'] if session['monitoring_mode'] != 'custom' else 'manual',
                 interval, target_spec.get('severity_threshold') or 'medium',
                 _json_dumps([]), workspace_id, user['id'], user['id']),
            )
            targets_created += 1

        # Wire the canonical monitoring registry + monitored system (production path).
        try:
            pilot._sync_canonical_monitoring_target_state(
                connection, workspace_id=workspace_id, target_id=target_id, asset_id=asset_id,
                enabled=True, monitoring_enabled=True, chain_network=chain_network,
            )
            bridge = pilot.ensure_monitored_system_for_target(connection, target_id=target_id, workspace_id=workspace_id)
            if bridge.get('status') == 'ok':
                monitoring_active += 1
        except Exception:
            logger.warning('onboarding_activation_monitoring_bridge_failed target_id=%s', target_id)

    rules_enabled = sum(1 for r in proposal.get('baseline_rules', []) if r.get('enabled'))

    return {
        'assets_protected': assets_created + assets_reused,
        'assets_created': assets_created,
        'assets_reused': assets_reused,
        'monitoring_sources_active': monitoring_active,
        'targets_created': targets_created,
        'rules_enabled': rules_enabled,
        'coverage_status': 'provisioning' if monitoring_active else 'pending',
        'proposal_version': version,
    }


# ===========================================================================
# Durable job claim + worker pipeline
# ===========================================================================
def _enqueue_run(connection: Any, *, session_id: str, workspace_id: str, run_type: str, idempotency_key: str,
                 dedupe_active: bool = False, claim: bool = False) -> dict[str, Any]:
    if dedupe_active:
        active = connection.execute(
            '''SELECT id, idempotency_key FROM onboarding_agent_runs
               WHERE session_id = %s AND run_type = %s AND status IN ('queued', 'running')
               ORDER BY created_at DESC LIMIT 1''',
            (session_id, run_type),
        ).fetchone()
        if active is not None:
            return {'id': str(active['id']), 'idempotency_key': active['idempotency_key'], 'deduped': True}
    run_id = str(uuid.uuid4())
    initial_status = 'running' if claim else 'queued'
    connection.execute(
        '''
        INSERT INTO onboarding_agent_runs (id, session_id, workspace_id, run_type, status, idempotency_key,
                                           worker_id, commit_sha, started_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CASE WHEN %s THEN NOW() ELSE NULL END)
        ''',
        (run_id, session_id, workspace_id, run_type, initial_status, idempotency_key, _worker_id(), _commit_sha(), claim),
    )
    return {'id': run_id, 'idempotency_key': idempotency_key, 'deduped': False}


def claim_and_run_once(*, worker_id: str | None = None, session_id: str | None = None) -> dict[str, Any]:
    """Claim a due queued discover/benchmark run and execute it.

    Distributed-safe: the claim is a conditional UPDATE ... WHERE status='queued'
    so only one worker proceeds for a given run. The dedicated worker calls this
    with no ``session_id`` and drains the oldest run globally; the API's inline
    best-effort path passes the just-enqueued ``session_id`` so it always
    processes that session's own run rather than an unrelated queued one.
    """
    wid = worker_id or _worker_id()
    with pilot.pg_connection() as connection:
        if session_id is not None:
            row = connection.execute(
                '''SELECT id FROM onboarding_agent_runs
                   WHERE status = 'queued' AND run_type IN ('discover', 'rpc_benchmark')
                     AND next_attempt_at <= NOW() AND session_id = %s
                   ORDER BY next_attempt_at ASC, created_at ASC LIMIT 1''',
                (session_id,),
            ).fetchone()
        else:
            row = connection.execute(
                '''SELECT id FROM onboarding_agent_runs
                   WHERE status = 'queued' AND run_type IN ('discover', 'rpc_benchmark') AND next_attempt_at <= NOW()
                   ORDER BY next_attempt_at ASC, created_at ASC LIMIT 1''',
            ).fetchone()
        if row is None:
            connection.commit()
            return {'processed': 0}
        claimed = connection.execute(
            '''UPDATE onboarding_agent_runs
               SET status = 'running', lease_owner = %s, worker_id = %s, commit_sha = %s,
                   lease_expires_at = NOW() + INTERVAL '10 minutes', started_at = COALESCE(started_at, NOW()), updated_at = NOW()
               WHERE id = %s AND status = 'queued'
               RETURNING id, session_id, workspace_id, run_type, retry_count, max_retries''',
            (wid, wid, _commit_sha(), row['id']),
        ).fetchone()
        connection.commit()
    if claimed is None:
        return {'processed': 0}
    run_id = str(claimed['id'])
    session_id = str(claimed['session_id'])
    try:
        run_discovery_pipeline(session_id)
        with pilot.pg_connection() as connection:
            connection.execute(
                "UPDATE onboarding_agent_runs SET status = 'completed', finished_at = NOW(), updated_at = NOW() WHERE id = %s",
                (run_id,),
            )
            connection.commit()
        return {'processed': 1, 'run_id': run_id, 'session_id': session_id}
    except Exception as exc:
        _fail_run(run_id, claimed, exc)
        return {'processed': 1, 'run_id': run_id, 'session_id': session_id, 'error': type(exc).__name__}


def _fail_run(run_id: str, claimed: dict[str, Any], exc: Exception) -> None:
    with pilot.pg_connection() as connection:
        retry_count = int(claimed.get('retry_count') or 0) + 1
        terminal = retry_count > int(claimed.get('max_retries') or 3)
        backoff = min(300, 2 ** retry_count)
        connection.execute(
            '''UPDATE onboarding_agent_runs
               SET status = %s, retry_count = %s, error_message = %s,
                   next_attempt_at = NOW() + (%s || ' seconds')::interval,
                   lease_owner = NULL, lease_expires_at = NULL, updated_at = NOW()
               WHERE id = %s''',
            ('failed' if terminal else 'queued', retry_count, redact_text(str(exc)[:300]), backoff, run_id),
        )
        connection.commit()
    logger.exception('onboarding_run_failed run_id=%s terminal_retry=%s', run_id, retry_count)


def _maybe_run_inline(session_id: str) -> None:
    if str(os.getenv('ONBOARDING_INLINE_WORKER', 'true')).strip().lower() in ('0', 'false', 'no'):
        return
    try:
        claim_and_run_once(session_id=session_id)
    except Exception:  # pragma: no cover - inline convenience only; the dedicated worker is authoritative
        logger.info('onboarding_inline_worker_deferred session_id=%s', session_id)


def _reload_snapshot(session_id: str, workspace_id: str) -> dict[str, Any]:
    with pilot.pg_connection() as connection:
        snapshot = build_session_snapshot(connection, session_id=session_id, workspace_id=workspace_id)
        connection.commit()
        return snapshot


def run_discovery_pipeline(session_id: str) -> dict[str, Any]:
    """Execute the durable discovery + benchmark + proposal pipeline for a session.

    Each step is persisted BEFORE its SSE event is published. A gating failure
    (chain mismatch, EOA, RPC unreachable) marks the step failed, records a
    structured error code, and stops — the frontend can retry only failed steps.
    """
    with pilot.pg_connection() as connection:
        session = connection.execute('SELECT * FROM onboarding_sessions WHERE id = %s', (session_id,)).fetchone()
        if session is None:
            return {'ok': False, 'reason': 'session_not_found'}
        session = dict(session)
        workspace_id = str(session['workspace_id'])
        chain_id = session['selected_chain_id']
        contract = session['primary_contract']
        endpoints = _build_benchmark_endpoints(connection, session_id=session_id, chain_id=chain_id)

        def step(step_key: str, status_value: str, **kw: Any) -> None:
            _set_step(connection, session_id=session_id, workspace_id=workspace_id, step_key=step_key,
                      status_value=status_value, **kw)
            _update_session(connection, session_id=session_id, current_step=step_key)
            connection.commit()  # persist BEFORE publish
            publish_event(workspace_id, session_id, 'step_update',
                          {'step_key': step_key, 'status': status_value})

        # --- validate_inputs ---
        step('validate_inputs', 'running', increment_attempt=True)
        try:
            disc.validate_contract_address(contract)
            step('validate_inputs', 'completed', result_summary=f'Address {contract[:10]}… validated for chain {chain_id}.')
        except disc.AddressValidationError as exc:
            step('validate_inputs', 'failed', error_code=exc.code, error_message=exc.message)
            _finalize_failure(connection, session_id, workspace_id, exc.code, exc.message)
            return {'ok': False, 'reason': exc.code}

        if not endpoints:
            step('connect_chain', 'failed', error_code='no_rpc_endpoint',
                 error_message='No RPC endpoint is available for this chain. Add a custom RPC endpoint and retry.')
            _finalize_failure(connection, session_id, workspace_id, 'no_rpc_endpoint',
                              'No RPC endpoint available. Add a custom RPC endpoint and retry.')
            return {'ok': False, 'reason': 'no_rpc_endpoint'}

        # --- connect_chain: pick the first endpoint that returns the correct chain id ---
        step('connect_chain', 'running', increment_attempt=True)
        transport, connect_error = _select_working_transport(endpoints, chain_id)
        if transport is None:
            step('connect_chain', 'failed', error_code=connect_error[0], error_message=connect_error[1])
            _finalize_failure(connection, session_id, workspace_id, connect_error[0], connect_error[1])
            return {'ok': False, 'reason': connect_error[0]}
        step('connect_chain', 'completed',
             result_summary=f'Connected to {disc.chain_network_name(chain_id)} via {transport.host}.',
             evidence={'rpc_host': transport.host, 'chain_id': chain_id})

        # --- full deterministic discovery ---
        step('verify_bytecode', 'running', increment_attempt=True)
        result = disc.discover_contract(transport, address=contract, selected_chain_id=chain_id)
        if not result.ok:
            failed_step = 'verify_bytecode' if result.error_code in ('no_deployed_contract', 'rpc_unreachable') else 'connect_chain'
            step(failed_step, 'failed', error_code=result.error_code, error_message=result.error_message)
            _finalize_failure(connection, session_id, workspace_id, result.error_code or 'discovery_failed',
                              result.error_message or 'Discovery failed.')
            return {'ok': False, 'reason': result.error_code}

        _persist_findings(connection, session_id=session_id, workspace_id=workspace_id, findings=result.findings)
        connection.commit()
        fmap = result.finding_map()
        step('verify_bytecode', 'completed', result_summary='Deployed bytecode verified.',
             evidence=fmap['bytecode'].evidence if 'bytecode' in fmap else {})

        # --- detect_standard ---
        step('detect_standard', 'running')
        std = fmap.get('token_standard')
        step('detect_standard', 'completed' if std else 'needs_attention',
             result_summary=(f'{std.value} ({std.confidence})' if std else 'No token standard confidently detected.'),
             evidence=(std.evidence if std else {}))

        # --- resolve_proxy ---
        step('resolve_proxy', 'running')
        proxy = fmap.get('proxy_type')
        impl = fmap.get('implementation_address')
        step('resolve_proxy', 'completed',
             result_summary=(f'Proxy: {proxy.value}' + (f' → {impl.value[:10]}…' if impl else '')) if proxy else 'No proxy detected.',
             evidence={'proxy_type': proxy.value if proxy else 'none', 'implementation': impl.value if impl else None})

        # --- discover_roles ---
        step('discover_roles', 'running')
        role_types = ['owner_address', 'access_model', 'proxy_admin', 'pausable', 'mint_capability',
                      'burn_capability', 'upgrade_capability', 'blacklist_capability', 'freeze_capability']
        roles_found = [t for t in role_types if t in fmap]
        step('discover_roles', 'completed',
             result_summary=f'{len(roles_found)} privileged capabilities / identities detected.',
             evidence={'capabilities': roles_found})

        # --- benchmark_rpc ---
        step('benchmark_rpc', 'running')
        bench_summary = _run_benchmark_phase(connection, session=session, endpoints=endpoints)
        connection.commit()
        step('benchmark_rpc', 'completed',
             result_summary=(bench_summary.get('explanation') or 'RPC providers benchmarked.'),
             evidence={'primary_host': bench_summary.get('primary_host'),
                       'fallback_host': bench_summary.get('fallback_host')})

        # --- discover_oracles ---
        step('discover_oracles', 'running')
        oracle = fmap.get('oracle_dependency')
        step('discover_oracles', 'completed' if (oracle and oracle.value != 'none_detected') else 'needs_attention',
             result_summary=(f'Oracle interface: {oracle.value}' if oracle else 'No oracle dependency detected.'),
             evidence=(oracle.evidence if oracle else {}))

        # --- generate_policies + create_config ---
        step('generate_policies', 'running')
        version = _build_and_store_proposal(connection, session=session, user_id=session.get('user_id'), request=None)
        connection.commit()
        step('generate_policies', 'completed', result_summary=f'Baseline monitoring policies generated (proposal v{version}).')

        step('create_config', 'running')
        _update_session(connection, session_id=session_id, status='proposal_ready', error_code=None, error_message=None)
        step('create_config', 'completed', result_summary='Draft workspace configuration ready for review.')
        connection.commit()
        publish_event(workspace_id, session_id, 'proposal_ready', {'version': version})
        return {'ok': True, 'proposal_version': version, 'findings': len(result.findings)}


def _finalize_failure(connection: Any, session_id: str, workspace_id: str, code: str, message: str) -> None:
    # A gating failure is recoverable: the frontend can retry only the failed step.
    _update_session(connection, session_id=session_id, status='partial', error_code=code, error_message=redact_text(message))
    connection.commit()
    publish_event(workspace_id, session_id, 'discovery_failed', {'error_code': code})


# ===========================================================================
# RPC benchmark phase (persistence)
# ===========================================================================
def _build_benchmark_endpoints(connection: Any, *, session_id: str, chain_id: int | None) -> list[BenchmarkEndpoint]:
    endpoints: list[BenchmarkEndpoint] = []
    rows = connection.execute(
        "SELECT value, encrypted_value, endpoint_host FROM onboarding_inputs WHERE session_id = %s AND input_type = 'rpc_endpoint'",
        (session_id,),
    ).fetchall()
    seen: set[str] = set()
    for row in rows:
        if not row.get('encrypted_value'):
            continue
        try:
            url = secret_crypto.decrypt_secret(row['encrypted_value'], aad=_ENCRYPTION_AAD)
        except Exception:
            continue
        host = row.get('endpoint_host') or disc._host_from_url(url)
        if host in seen:
            continue
        seen.add(host)
        endpoints.append(BenchmarkEndpoint(host=host, redacted_url=row.get('value') or disc.redact_rpc_url(url),
                                           transport=HttpRpcTransport(url, host=host)))
    # Server-configured default endpoints (never expose their URLs / keys).
    for url in _default_rpc_urls(chain_id):
        host = disc._host_from_url(url)
        if host in seen:
            continue
        seen.add(host)
        endpoints.append(BenchmarkEndpoint(host=host, redacted_url=disc.redact_rpc_url(url),
                                           transport=HttpRpcTransport(url, host=host)))
    return endpoints


def _default_rpc_urls(chain_id: int | None) -> list[str]:
    urls: list[str] = []
    for var in ('ONBOARDING_RPC_URLS', f'ONBOARDING_RPC_URLS_{chain_id}'):
        raw = (os.getenv(var) or '').strip()
        if raw:
            urls.extend(part.strip() for part in raw.split(',') if part.strip())
    if not urls:
        try:
            from services.api.app import evm_activity_provider
            configured_chain = evm_activity_provider.worker_rpc_chain_id()
            if chain_id is None or configured_chain == chain_id:
                urls.extend(evm_activity_provider._resolve_evm_rpc_urls())
        except Exception:
            pass
    return list(dict.fromkeys(u for u in urls if u))


def _select_working_transport(endpoints: list[BenchmarkEndpoint], chain_id: int | None) -> tuple[RpcTransport | None, tuple[str, str]]:
    last_error = ('rpc_unreachable', 'No RPC endpoint responded to eth_chainId.')
    for ep in endpoints:
        res = ep.transport.timed_call('eth_chainId', [])
        if not res.ok:
            last_error = ('rpc_unreachable', f'RPC endpoint {ep.host} did not respond ({res.kind or "error"}).')
            continue
        returned = disc.decode_uint(res.result if isinstance(res.result, str) else None)
        if returned is None:
            last_error = ('invalid_chain_response', f'RPC endpoint {ep.host} returned an invalid chain id.')
            continue
        if chain_id is not None and returned != chain_id:
            last_error = ('chain_mismatch',
                          f'RPC endpoint {ep.host} reports chain id {returned} but chain id {chain_id} was selected.')
            continue
        return ep.transport, last_error
    return None, last_error


def _run_benchmark_phase(connection: Any, *, session: dict[str, Any], endpoints: list[BenchmarkEndpoint]) -> dict[str, Any]:
    session_id = str(session['id'])
    workspace_id = str(session['workspace_id'])
    chain_id = session['selected_chain_id']
    contract = session['primary_contract']
    results, summary = disc.run_rpc_benchmark(endpoints, selected_chain_id=chain_id, target_address=contract)

    run_id = str(uuid.uuid4())
    connection.execute(
        '''INSERT INTO rpc_benchmark_runs (id, session_id, workspace_id, status, selected_chain_id, best_block,
                                           primary_host, fallback_host, explanation, completed_at)
           VALUES (%s, %s, %s, 'completed', %s, %s, %s, %s, %s, NOW())''',
        (run_id, session_id, workspace_id, chain_id, summary.get('best_block'),
         summary.get('primary_host'), summary.get('fallback_host'), summary.get('explanation')),
    )
    for r in results:
        d = r.to_dict()
        connection.execute(
            '''INSERT INTO rpc_benchmark_results
                   (id, run_id, session_id, workspace_id, endpoint_host, redacted_url, connection_status,
                    median_latency_ms, p95_latency_ms, success_rate, error_rate, timeout_count, error_count,
                    latest_block, block_lag, chain_id_returned, chain_id_ok, rate_limited, archive_supported,
                    score, recommendation, reason, evidence)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)''',
            (str(uuid.uuid4()), run_id, session_id, workspace_id, d['host'], d['redacted_url'], d['connection_status'],
             d['median_latency_ms'], d['p95_latency_ms'], d['success_rate'], d['error_rate'], d['timeout_count'],
             d['error_count'], d['latest_block'], d['block_lag'], d['chain_id_returned'], d['chain_id_ok'],
             d['rate_limited'], d['archive_supported'], d['score'], d['recommendation'], d['reason'],
             _json_dumps(d['evidence'])),
        )
    return summary


# ===========================================================================
# Proposal builder
# ===========================================================================
def _build_and_store_proposal(connection: Any, *, session: dict[str, Any], user_id: Any, request: Any) -> int:
    session_id = str(session['id'])
    workspace_id = str(session['workspace_id'])
    finding_rows = connection.execute(
        'SELECT * FROM discovery_findings WHERE session_id = %s', (session_id,),
    ).fetchall()
    findings = [_serialize_finding(dict(r)) for r in finding_rows]
    fmap = {f['finding_type']: f for f in findings}

    bench_run = connection.execute(
        'SELECT * FROM rpc_benchmark_runs WHERE session_id = %s ORDER BY created_at DESC LIMIT 1', (session_id,),
    ).fetchone()
    bench_results = []
    if bench_run is not None:
        bench_results = [dict(r) for r in connection.execute(
            'SELECT * FROM rpc_benchmark_results WHERE run_id = %s ORDER BY score DESC NULLS LAST', (bench_run['id'],),
        ).fetchall()]

    proposal = _compose_proposal(session=session, fmap=fmap, findings=findings,
                                 bench_run=(dict(bench_run) if bench_run else None), bench_results=bench_results)
    summary = _proposal_summary(proposal, findings)

    ai_summary, ai_available = build_ai_summary(session=session, proposal=proposal, findings=findings)

    version = int(session.get('proposal_version') or 0) + 1
    proposal_hash = 'sha256:' + hashlib.sha256(_json_dumps(proposal).encode('utf-8')).hexdigest()
    connection.execute(
        '''INSERT INTO generated_workspace_proposals
               (id, session_id, workspace_id, version, proposal, summary, ai_summary, ai_available, proposal_hash)
           VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s)
           ON CONFLICT (session_id, version) DO UPDATE SET proposal = EXCLUDED.proposal, summary = EXCLUDED.summary,
               ai_summary = EXCLUDED.ai_summary, ai_available = EXCLUDED.ai_available, proposal_hash = EXCLUDED.proposal_hash''',
        (str(uuid.uuid4()), session_id, workspace_id, version, _json_dumps(proposal), _json_dumps(summary),
         ai_summary, ai_available, proposal_hash),
    )
    _update_session(connection, session_id=session_id, proposal_version=version)
    if request is not None:
        _audit(connection, request=request, user_id=user_id, workspace_id=workspace_id, session_id=session_id,
               action='onboarding.proposal_generated', metadata={'version': version, 'rules': len(proposal['baseline_rules'])})
    return version


def _compose_proposal(*, session: dict[str, Any], fmap: dict[str, Any], findings: list[dict[str, Any]],
                      bench_run: dict[str, Any] | None, bench_results: list[dict[str, Any]]) -> dict[str, Any]:
    mode = session['monitoring_mode']
    chain_network = session['chain_network'] or disc.chain_network_name(session['selected_chain_id'])
    contract = session['primary_contract']
    symbol = fmap.get('token_symbol', {}).get('value')
    name = fmap.get('token_name', {}).get('value')
    std = fmap.get('token_standard', {}).get('value')

    protected_assets = [{
        'name': name or (symbol or f'{chain_network} asset'),
        'symbol': symbol,
        'identifier': contract,
        'asset_type': 'tokenized_rwa' if std in ('ERC-20', 'ERC-4626') else 'smart_contract',
        'asset_class': std,
        'chain_network': chain_network,
        'risk_tier': 'high' if _has(fmap, 'mint_capability') or _has(fmap, 'upgrade_capability') else 'medium',
        'description': f'{std or "Contract"} on {chain_network} discovered by the Onboarding Agent.',
    }]
    monitoring_targets = [{
        'name': f'{symbol or name or "Contract"} monitor',
        'contract_identifier': contract,
        'target_type': 'contract',  # canonical monitorable type (see monitorable_target_types)
        'asset_type': 'tokenized_rwa' if std in ('ERC-20', 'ERC-4626') else 'smart_contract',
        'severity_preference': 'high' if mode == 'strict' else 'medium',
        'severity_threshold': 'low' if mode == 'strict' else 'medium',
        'monitoring_interval_seconds': 120 if mode == 'strict' else 300,
    }]

    rpc_sources = {
        'primary_host': (bench_run or {}).get('primary_host'),
        'fallback_host': (bench_run or {}).get('fallback_host'),
        'explanation': (bench_run or {}).get('explanation'),
    }

    privileged = []
    for t in ('owner_address', 'proxy_admin'):
        if t in fmap:
            privileged.append({'role': t, 'address': fmap[t]['value'], 'confidence': fmap[t]['confidence']})

    baseline_rules = _baseline_rules(fmap, mode=mode, std=std)
    event_subscriptions = _event_subscriptions(fmap)

    limitations: list[str] = []
    if std in ('ERC-20', 'ERC-4626'):
        limitations.append('ERC-20/4626 classification is heuristic (no on-chain introspection standard); confirm before relying on supply rules.')
    if not _has(fmap, 'oracle_dependency', exclude_value='none_detected'):
        limitations.append('No standard oracle interface was detected in bytecode; confirm off-chain price dependencies manually.')
    if not bench_results:
        limitations.append('No RPC benchmark results are available yet; add RPC endpoints and re-test providers.')
    if not rpc_sources['fallback_host']:
        limitations.append('Only one healthy RPC provider was found; add a second provider for failover resilience.')

    findings_requiring_review = [f for f in findings if f['confidence'] in ('unknown', 'requires_review')]

    return {
        'protected_assets': protected_assets,
        'monitoring_targets': monitoring_targets,
        'contract_relationships': _contract_relationships(fmap, contract),
        'rpc_sources': rpc_sources,
        'oracle_sources': [f['value'] for f in findings
                           if f['finding_type'] in ('oracle_dependency', 'oracle_address') and f['value'] != 'none_detected'],
        'privileged_identities': privileged,
        'event_subscriptions': event_subscriptions,
        'polling': {'interval_seconds': monitoring_targets[0]['monitoring_interval_seconds'], 'mode': mode},
        'baseline_rules': baseline_rules,
        'alert_severity_mappings': {'critical': ['owner_change', 'proxy_upgrade', 'admin_role_change', 'unpause'],
                                    'high': ['abnormal_mint', 'abnormal_burn', 'blacklist_change', 'oracle_deviation'],
                                    'medium': ['large_transfer', 'treasury_movement', 'rpc_block_lag'],
                                    'low': ['rpc_connection_failure']},
        'telemetry_retention_days': 90 if mode == 'strict' else 30,
        'evidence_requirements': ['tx_hash', 'block_number', 'rpc_source', 'payload_hash'],
        'initial_health_checks': ['rpc_primary_reachable', 'chain_id_match', 'monitored_system_provisioned'],
        'limitations': limitations,
        'findings_requiring_review': findings_requiring_review,
        'monitoring_mode': mode,
    }


def _has(fmap: dict[str, Any], key: str, *, exclude_value: str | None = None) -> bool:
    if key not in fmap:
        return False
    if exclude_value is not None and fmap[key]['value'] == exclude_value:
        return False
    return True


def _contract_relationships(fmap: dict[str, Any], contract: str) -> list[dict[str, Any]]:
    rels = []
    if 'implementation_address' in fmap:
        rels.append({'from': contract, 'to': fmap['implementation_address']['value'], 'relationship': 'proxy_implementation'})
    if 'proxy_admin' in fmap:
        rels.append({'from': contract, 'to': fmap['proxy_admin']['value'], 'relationship': 'proxy_admin'})
    if 'vault_asset' in fmap:
        rels.append({'from': contract, 'to': fmap['vault_asset']['value'], 'relationship': 'vault_underlying_asset'})
    return rels


def _event_subscriptions(fmap: dict[str, Any]) -> list[str]:
    subs = set()
    if 'event_signatures' in fmap:
        val = fmap['event_signatures']['value']
        if isinstance(val, list):
            subs.update(val)
    if _has(fmap, 'owner_address') or _has(fmap, 'access_model'):
        subs.add('OwnershipTransferred(address,address)')
    if _has(fmap, 'access_model'):
        subs.update(['RoleGranted(bytes32,address,address)', 'RoleRevoked(bytes32,address,address)'])
    if _has(fmap, 'proxy_type', exclude_value='none'):
        subs.add('Upgraded(address)')
    if _has(fmap, 'pausable'):
        subs.update(['Paused(address)', 'Unpaused(address)'])
    return sorted(subs)


def _baseline_rules(fmap: dict[str, Any], *, mode: str, std: str | None) -> list[dict[str, Any]]:
    strict = mode == 'strict'
    rules: list[dict[str, Any]] = []

    def rule(key: str, title: str, severity: str, sources: list[str], rationale: str, enabled: bool = True) -> None:
        rules.append({'key': key, 'title': title, 'severity': severity, 'enabled': enabled,
                      'source_findings': sources, 'rationale': rationale,
                      'requires_review': mode == 'custom'})

    # Always-on infra rules.
    rule('rpc_block_lag', 'RPC block lag', 'medium', ['chain_id'], 'Detect a monitoring provider falling behind the chain tip.')
    rule('rpc_connection_failure', 'RPC connection failure', 'low', ['chain_id'], 'Alert when the monitoring provider becomes unreachable.')
    rule('large_transfer', 'Large transfer', 'medium', ['token_standard'] if std else [], 'Flag transfers above the configured value threshold.')

    if _has(fmap, 'owner_address') or _has(fmap, 'access_model'):
        rule('unexpected_owner_change', 'Unexpected owner change', 'critical', ['owner_address', 'access_model'],
             'The contract exposes ownership/admin controls; an owner change is high-impact.')
    if _has(fmap, 'access_model'):
        rule('admin_role_change', 'Admin role granted or revoked', 'critical', ['access_model'],
             'AccessControl roles govern privileged operations.')
    if _has(fmap, 'proxy_type', exclude_value='none'):
        rule('proxy_implementation_upgrade', 'Proxy implementation upgrade', 'critical', ['proxy_type', 'implementation_address'],
             'An upgradeable proxy can change all logic; upgrades must be watched.')
    if _has(fmap, 'pausable'):
        rule('pause_unpause_event', 'Pause / unpause event', 'critical', ['pausable'],
             'Pausing halts transfers; unpausing resumes them.')
    if _has(fmap, 'mint_capability'):
        rule('abnormal_minting', 'Abnormal minting', 'high', ['mint_capability'], 'Mint capability can inflate supply.')
        rule('supply_deviation', 'Supply deviation', 'high', ['total_supply', 'mint_capability'], 'Detect unexpected total-supply changes.')
    if _has(fmap, 'burn_capability'):
        rule('abnormal_burning', 'Abnormal burning', 'high', ['burn_capability'], 'Burn capability can reduce supply.')
    if _has(fmap, 'oracle_dependency', exclude_value='none_detected'):
        rule('oracle_heartbeat_missed', 'Oracle heartbeat missed', 'high', ['oracle_dependency'], 'A stale oracle can mis-price the asset.')
        rule('oracle_price_deviation', 'Oracle price deviation', 'high', ['oracle_dependency'], 'Detect large oracle price jumps.')
    if _has(fmap, 'blacklist_capability'):
        rule('blacklist_change', 'Blacklist change', 'high', ['blacklist_capability'], 'Blacklist controls can freeze holders.')
    if _has(fmap, 'freeze_capability'):
        rule('freeze_event', 'Freeze event', 'high', ['freeze_capability'], 'Freeze controls can lock balances.')

    if strict:
        rule('any_privileged_call', 'Any privileged function call', 'high', ['access_model', 'owner_address'],
             'Strict mode: alert on any privileged call.', enabled=True)
    return rules


def _proposal_summary(proposal: dict[str, Any], findings: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        'assets_to_create': len(proposal['protected_assets']),
        'targets_to_create': len(proposal['monitoring_targets']),
        'rules_to_enable': sum(1 for r in proposal['baseline_rules'] if r['enabled']),
        'event_subscriptions': len(proposal['event_subscriptions']),
        'privileged_identities': len(proposal['privileged_identities']),
        'primary_rpc': proposal['rpc_sources'].get('primary_host'),
        'fallback_rpc': proposal['rpc_sources'].get('fallback_host'),
        'confirmed_findings': sum(1 for f in findings if f['confidence'] == 'confirmed'),
        'findings_requiring_review': len(proposal['findings_requiring_review']),
        'limitations': len(proposal['limitations']),
    }


# ===========================================================================
# AI summary (optional, grounded, graceful fallback)
# ===========================================================================
def build_ai_summary(*, session: dict[str, Any], proposal: dict[str, Any],
                     findings: list[dict[str, Any]]) -> tuple[str, bool]:
    """Return (summary_text, ai_available). Always returns a deterministic summary;
    an AI enhancement is attempted only when configured and never blocks onboarding."""
    deterministic = _deterministic_summary(session=session, proposal=proposal, findings=findings)
    if str(os.getenv('ONBOARDING_AI_SUMMARY_ENABLED', '')).strip().lower() not in ('1', 'true', 'yes'):
        return deterministic, False
    try:
        enhanced = _invoke_ai_summary(session=session, proposal=proposal, findings=findings)
        if enhanced:
            return enhanced, True
    except Exception as exc:  # pragma: no cover - AI failure must never block onboarding
        logger.info('onboarding_ai_summary_unavailable reason=%s', type(exc).__name__)
    return deterministic, False


def _invoke_ai_summary(*, session: dict[str, Any], proposal: dict[str, Any], findings: list[dict[str, Any]]) -> str | None:
    # Intentionally left as a monkeypatch/extension seam. Any AI provider wired
    # here MUST only summarize the verified findings/benchmark passed in and cite
    # finding types — it must never invent facts. Default: no AI call.
    raise RuntimeError('ai_summary_provider_not_configured')


def _deterministic_summary(*, session: dict[str, Any], proposal: dict[str, Any], findings: list[dict[str, Any]]) -> str:
    confirmed = [f for f in findings if f['confidence'] == 'confirmed']
    std = next((f['value'] for f in findings if f['finding_type'] == 'token_standard'), None)
    symbol = next((f['value'] for f in findings if f['finding_type'] == 'token_symbol'), None)
    caps = [f['finding_type'].replace('_capability', '') for f in findings
            if f['finding_type'].endswith('_capability')]
    parts = []
    net = session['chain_network'] or disc.chain_network_name(session['selected_chain_id'])
    parts.append(f'Discovered a {std or "contract"}{" (" + symbol + ")" if symbol else ""} on {net}.')
    if caps:
        parts.append('Privileged capabilities detected: ' + ', '.join(sorted(set(caps))) + '.')
    primary = proposal['rpc_sources'].get('primary_host')
    if primary:
        parts.append(f'Recommended primary RPC provider: {primary}.')
    parts.append(f'{sum(1 for r in proposal["baseline_rules"] if r["enabled"])} baseline monitoring rules proposed '
                 f'from {len(confirmed)} confirmed findings.')
    if proposal['limitations']:
        parts.append(f'{len(proposal["limitations"])} limitation(s) require manual review before activation.')
    return ' '.join(parts)


# ===========================================================================
# Discovery report export (SHA-256 hashed)
# ===========================================================================
def export_report(session_id: str, request: Any) -> dict[str, Any]:
    pilot.require_live_mode()
    _validate_uuid(session_id)
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        user = pilot.authenticate_with_connection(connection, request)
        workspace_context = pilot.resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        workspace_id = workspace_context['workspace_id']
        snapshot = build_session_snapshot(connection, session_id=session_id, workspace_id=workspace_id)
        report_body = {
            'report_type': 'onboarding_discovery_report',
            'generated_at': _now_iso(),
            'workspace_id': workspace_id,
            'session': snapshot['session'],
            'inputs': snapshot['inputs'],
            'findings': snapshot['findings'],
            'rpc_benchmark': snapshot['benchmark'],
            'proposal': snapshot['proposal'],
            'approvals': snapshot['approvals'],
            'agent': snapshot['agent'],
        }
        canonical = json.dumps(report_body, sort_keys=True, separators=(',', ':'), default=str)
        report_hash = 'sha256:' + hashlib.sha256(canonical.encode('utf-8')).hexdigest()
        _audit(connection, request=request, user_id=user['id'], workspace_id=workspace_id, session_id=session_id,
               action='onboarding.report_exported', metadata={'report_hash': report_hash})
        connection.commit()
        return {'report': report_body, 'sha256': report_hash}


# ===========================================================================
# Audit + misc
# ===========================================================================
def _audit(connection: Any, *, request: Any, user_id: Any, workspace_id: str, session_id: str, action: str,
           metadata: dict[str, Any] | None = None, previous_state: dict[str, Any] | None = None,
           new_state: dict[str, Any] | None = None) -> None:
    meta = dict(metadata or {})
    meta['session_id'] = session_id
    if previous_state is not None:
        meta['previous_state'] = previous_state
    if new_state is not None:
        meta['new_state'] = new_state
    # Defense in depth: never let a raw RPC URL slip into an audit record.
    meta = json.loads(redact_json(json.dumps(meta, default=str)))
    try:
        pilot.log_audit(connection, action=action, entity_type='onboarding_session', entity_id=session_id,
                        request=request, user_id=(str(user_id) if user_id else None), workspace_id=workspace_id,
                        metadata=meta)
    except Exception:  # pragma: no cover - audit must not break the primary operation
        logger.warning('onboarding_audit_failed action=%s session_id=%s', action, session_id)


def redact_json(text: str) -> str:
    """Redact obvious embedded secrets (http(s) URLs with long tokens) from a JSON string."""
    import re
    def _sub(m: 're.Match[str]') -> str:
        return disc.redact_rpc_url(m.group(0))
    return re.sub(r'https?://[^\s"\\]+', _sub, text)


def _validate_uuid(value: str) -> None:
    try:
        uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid session id.') from exc
