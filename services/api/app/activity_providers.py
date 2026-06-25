from __future__ import annotations

import hashlib
import logging
import importlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
import os

from services.api.app.evm_activity_provider import (
    MonitoredWalletNotConfigured,
    evaluate_chain_mismatch,
    fetch_evm_activity,
    probe_rpc_health,
    rpc_provider_backoff_active,
    rpc_provider_backoff_status,
    _resolve_evm_rpc_url,
)
from services.api.app.monitoring_mode import (
    MonitoringModeError,
    resolve_monitoring_mode,
)
from services.api.app.monitorable_target_types import is_monitorable_target_type, normalize_target_type
from services.api.app.monitoring_truth import api_mode

logger = logging.getLogger(__name__)

@dataclass
class ActivityEvent:
    event_id: str
    kind: str
    observed_at: datetime
    ingestion_source: str
    cursor: str
    payload: dict[str, Any]


@dataclass
class ActivityProviderResult:
    mode: str
    status: str
    evidence_state: str
    truthfulness_state: str
    synthetic: bool
    provider_name: str
    provider_kind: str
    evidence_present: bool
    recent_real_event_count: int
    last_real_event_at: datetime | None
    events: list[ActivityEvent]
    latest_block: int | None
    checkpoint: str | None
    checkpoint_age_seconds: int | None
    degraded_reason: str | None
    error_code: str | None
    source_type: str
    reason_code: str | None
    claim_safe: bool
    detection_outcome: str

    def __post_init__(self) -> None:
        normalized_mode = api_mode(self.mode)
        if normalized_mode in {'LIVE', 'HYBRID'} and self.synthetic:
            raise MonitoringModeError('live/hybrid monitoring result cannot be synthetic')
        if normalized_mode == 'DEMO' and not self.synthetic:
            raise MonitoringModeError('demo monitoring result must be synthetic')
        if self.status == 'live' and not self.evidence_present:
            raise MonitoringModeError('live monitoring result requires provider evidence')
        if not self.evidence_present and self.claim_safe:
            raise MonitoringModeError('claim_safe cannot be true when evidence is missing')
        if self.evidence_state not in {'REAL_EVIDENCE', 'NO_EVIDENCE', 'DEGRADED_EVIDENCE', 'FAILED_EVIDENCE', 'DEMO_EVIDENCE'}:
            raise MonitoringModeError(f'invalid evidence_state: {self.evidence_state}')
        if self.truthfulness_state not in {'CLAIM_SAFE', 'NOT_CLAIM_SAFE', 'UNKNOWN_RISK'}:
            raise MonitoringModeError(f'invalid truthfulness_state: {self.truthfulness_state}')
        if normalized_mode in {'LIVE', 'HYBRID'} and self.evidence_state == 'REAL_EVIDENCE' and not self.evidence_present:
            raise MonitoringModeError('REAL_EVIDENCE requires evidence_present=true')
        if normalized_mode in {'LIVE', 'HYBRID'} and not self.evidence_present and self.truthfulness_state == 'CLAIM_SAFE':
            raise MonitoringModeError('live/hybrid cannot claim safe without real evidence')
        if self.detection_outcome not in {
            'DETECTION_CONFIRMED',
            'NO_CONFIRMED_ANOMALY_FROM_REAL_EVIDENCE',
            'NO_EVIDENCE',
            'MONITORING_DEGRADED',
            'ANALYSIS_FAILED',
            'DEMO_ONLY',
        }:
            raise MonitoringModeError(f'invalid detection_outcome: {self.detection_outcome}')


def monitoring_ingestion_mode() -> str:
    return resolve_monitoring_mode()


def live_monitoring_enabled() -> bool:
    return str(os.getenv('LIVE_MONITORING_ENABLED', 'true')).strip().lower() in {'1', 'true', 'yes', 'on'}


def live_monitoring_requirements() -> dict[str, bool]:
    return {
        'evm_rpc_url': bool(_resolve_evm_rpc_url()),
    }


def monitoring_ingestion_runtime() -> dict[str, Any]:
    mode = monitoring_ingestion_mode()
    req = live_monitoring_requirements()
    live_enabled = live_monitoring_enabled()
    ws_url = bool((os.getenv('EVM_WS_URL') or '').strip())
    if mode == 'demo':
        return {'mode': mode, 'source': 'demo', 'degraded': False, 'reason': None}
    if not live_enabled:
        return {'mode': mode, 'source': 'degraded', 'degraded': True, 'reason': 'LIVE_MONITORING_ENABLED=false'}
    if not req['evm_rpc_url']:
        return {'mode': mode, 'source': 'degraded', 'degraded': True, 'reason': 'STAGING_EVM_RPC_URL / EVM_RPC_URL missing'}
    source = 'websocket' if ws_url else 'polling'
    return {'mode': mode, 'source': source, 'degraded': False, 'reason': None}


def validate_monitoring_config_or_raise() -> None:
    runtime = monitoring_ingestion_runtime()
    if runtime['mode'] == 'live' and runtime['degraded']:
        raise RuntimeError(f"Live monitoring mode is misconfigured: {runtime['reason']}")
def _build_event(
    target: dict[str, Any],
    *,
    kind: str,
    observed_at: datetime,
    payload: dict[str, Any],
    evidence_origin: str = 'real',
    provider_name: str = 'evm_activity_provider',
) -> ActivityEvent:
    slot = observed_at.replace(second=0, microsecond=0).isoformat()
    event_id = hashlib.sha256(f"{target['id']}:{kind}:{slot}".encode('utf-8')).hexdigest()[:20]
    payload_metadata = payload.get('metadata') if isinstance(payload.get('metadata'), dict) else {}
    payload['metadata'] = {
        **payload_metadata,
        'evidence_origin': evidence_origin,
        'provider_name': provider_name,
        'production_claim_eligible': evidence_origin == 'real',
    }
    return ActivityEvent(
        event_id=event_id,
        kind=kind,
        observed_at=observed_at,
        ingestion_source=evidence_origin,
        cursor=slot,
        payload=payload,
    )

def _demo_mode_allowed() -> bool:
    env = str(os.getenv('ENV') or os.getenv('APP_ENV') or '').strip().lower()
    return str(os.getenv('ALLOW_DEMO_MODE', 'false')).strip().lower() in {'1', 'true', 'yes', 'on'} and env not in {'prod', 'production'}


def _load_demo_activity_providers() -> Any:
    if not _demo_mode_allowed():
        raise MonitoringModeError('demo activity providers are disabled for this environment')
    return importlib.import_module('services.api.app.demo_activity_providers')


def fetch_target_activity(target: dict[str, Any], since_ts: datetime | None) -> list[ActivityEvent]:
    return fetch_target_activity_result(target, since_ts).events


def fetch_target_activity_result(target: dict[str, Any], since_ts: datetime | None) -> ActivityProviderResult:
    target_type = normalize_target_type(target.get('target_type'))
    runtime = monitoring_ingestion_runtime()
    mode = runtime['mode']
    can_use_live = (not runtime['degraded']) and is_monitorable_target_type(target_type)
    if mode in {'live', 'hybrid'} and can_use_live:
        # Hard skip: a target on a different chain than this worker's RPC must not
        # trigger ANY RPC/backfill/coverage work. Return a non-claim-safe failed
        # result (never a success) so the runner marks it misconfigured — and we
        # never call fetch_evm_activity, eth_blockNumber, or the coverage probe.
        _hard_skip, _t_chain, _rpc_chain = evaluate_chain_mismatch(target.get('chain_network'))
        if _hard_skip:
            target['_evm_chain_mismatch'] = True
            target['_evm_chain_mismatch_reason'] = (
                f'chain_mismatch target_chain_id={_t_chain} rpc_chain_id={_rpc_chain}'
            )
            logger.warning(
                'chain_mismatch_hard_skip target_id=%s configured_chain=%s '
                'target_chain_id=%s rpc_chain_id=%s action=hard_skip_no_rpc',
                target.get('id'), str(target.get('chain_network') or '').strip().lower(),
                _t_chain, _rpc_chain,
            )
            return ActivityProviderResult(
                mode=mode,
                status='failed',
                evidence_state='FAILED_EVIDENCE',
                truthfulness_state='UNKNOWN_RISK',
                synthetic=False,
                provider_name='evm_activity_provider',
                provider_kind='rpc',
                evidence_present=False,
                recent_real_event_count=0,
                last_real_event_at=None,
                events=[],
                latest_block=None,
                checkpoint=None,
                checkpoint_age_seconds=None,
                degraded_reason='chain_mismatch',
                error_code='ChainRpcMismatch',
                source_type='unknown',
                reason_code='CHAIN_RPC_MISMATCH',
                claim_safe=False,
                detection_outcome='MONITORING_DEGRADED',
            )
        # Provider 429 backoff: skip live RPC entirely this cycle so we never
        # re-hit eth_blockNumber while the provider is rate-limiting.
        if rpc_provider_backoff_active():
            _bo = rpc_provider_backoff_status()
            logger.warning(
                'provider_backoff_skip target_id=%s reason=provider_backoff_active backoff_until=%s',
                target.get('id'), _bo.get('backoff_until') or 'unknown',
            )
            return ActivityProviderResult(
                mode=mode,
                status='degraded',
                evidence_state='DEGRADED_EVIDENCE',
                truthfulness_state='UNKNOWN_RISK',
                synthetic=False,
                provider_name='evm_activity_provider',
                provider_kind='rpc',
                evidence_present=False,
                recent_real_event_count=0,
                last_real_event_at=None,
                events=[],
                latest_block=None,
                checkpoint=None,
                checkpoint_age_seconds=None,
                degraded_reason='provider_backoff_active',
                error_code=None,
                source_type='rpc_polling',
                reason_code='PROVIDER_BACKOFF_ACTIVE',
                claim_safe=False,
                detection_outcome='MONITORING_DEGRADED',
            )
        try:
            live_events = fetch_evm_activity(target, since_ts)
        except MonitoredWalletNotConfigured:
            # Fail closed: a wallet target with no resolvable monitored wallet is a
            # misconfiguration, not "no activity". Surface a clear, distinct reason
            # so runtime status shows the target as misconfigured rather than healthy.
            logger.warning(
                'monitored_wallet_not_configured target_id=%s target_type=%s — '
                'set targets.wallet_address to the monitored EVM address',
                target.get('id'),
                target_type,
            )
            return ActivityProviderResult(
                mode=mode,
                status='failed',
                evidence_state='FAILED_EVIDENCE',
                truthfulness_state='UNKNOWN_RISK',
                synthetic=False,
                provider_name='evm_activity_provider',
                provider_kind='rpc',
                evidence_present=False,
                recent_real_event_count=0,
                last_real_event_at=None,
                events=[],
                latest_block=None,
                checkpoint=None,
                checkpoint_age_seconds=None,
                degraded_reason='monitored_wallet_not_configured',
                error_code='MonitoredWalletNotConfigured',
                source_type='unknown',
                reason_code='MONITORED_WALLET_NOT_CONFIGURED',
                claim_safe=False,
                detection_outcome='ANALYSIS_FAILED',
            )
        except Exception as exc:
            logger.exception(
                'evm_provider_error target_id=%s error_type=%s error=%s',
                target.get('id'),
                type(exc).__name__,
                str(exc)[:200],
            )
            return ActivityProviderResult(
                mode=mode,
                status='failed',
                evidence_state='FAILED_EVIDENCE',
                truthfulness_state='UNKNOWN_RISK',
                synthetic=False,
                provider_name='evm_activity_provider',
                provider_kind='rpc',
                evidence_present=False,
                recent_real_event_count=0,
                last_real_event_at=None,
                events=[],
                latest_block=None,
                checkpoint=None,
                checkpoint_age_seconds=None,
                degraded_reason='provider_error',
                error_code=exc.__class__.__name__,
                source_type='rpc_polling',
                reason_code='PROVIDER_FAILED',
                claim_safe=False,
                detection_outcome='ANALYSIS_FAILED',
            )
        has_evidence = bool(live_events)
        coverage_evidence_present = True
        # Always use the exact scan ceiling (safe_to) written by fetch_evm_activity
        # so the runner advances the cursor to where we scanned, not just to the
        # highest event block. This prevents gaps between consecutive polls.
        latest_block: int | None = target.get('_evm_scan_to_block')
        checkpoint = None
        if has_evidence:
            for event in live_events:
                if isinstance(event.payload, dict):
                    metadata = event.payload.get('metadata') if isinstance(event.payload.get('metadata'), dict) else {}
                    event.payload['metadata'] = {
                        **metadata,
                        'evidence_origin': 'real',
                        'provider_name': 'evm_activity_provider',
                        'production_claim_eligible': True,
                    }
            checkpoint = live_events[-1].cursor
            if any(event.ingestion_source == 'demo' for event in live_events):
                raise MonitoringModeError('synthetic event leaked into live/hybrid provider stream')
        # Log-scan coverage truthfulness (fail-closed): eth_blockNumber succeeded (the
        # provider is reachable) but the eth_getLogs scan did NOT fully cover this cycle's
        # range — either a 413 stayed too large at the minimum chunk ('degraded' /
        # query_too_large) or a non-413 error stopped the scan ('failed' /
        # logs_fetch_failed). This must be reported as DEGRADED, never a live success:
        # provider_observation -> degraded, provider_fetch_checkpoint status -> degraded,
        # and the cursor must not advance past the last fully-scanned block (which
        # fetch_evm_activity already enforced via _evm_scan_to_block).
        _logs_fetch_status = str(target.get('_evm_logs_fetch_status') or 'ok').strip().lower()
        _logs_status_reason = str(target.get('_evm_logs_status_reason') or '').strip() or None
        if _logs_fetch_status in {'failed', 'degraded'}:
            if _logs_fetch_status == 'failed':
                # Hard non-413 failure: fail closed. Emit no events and do NOT advance the
                # cursor (latest_block=None) so the whole range is re-scanned next cycle.
                _deg_events: list[ActivityEvent] = []
                _deg_latest: int | None = None
                _deg_reason = _logs_status_reason or 'logs_fetch_failed'
                _deg_code = 'LOG_SCAN_FAILED'
            else:
                # 413 query-too-large at the min chunk: blocks up to _evm_scan_to_block were
                # fully scanned, so advance only to there and keep the real events found in
                # that covered window (re-emitted idempotently if re-scanned next cycle).
                _deg_events = live_events
                _deg_latest = target.get('_evm_scan_to_block')
                _deg_reason = _logs_status_reason or 'query_too_large'
                _deg_code = 'LOG_SCAN_DEGRADED'
            logger.warning(
                'provider_log_scan_degraded target_id=%s logs_fetch_status=%s status_reason=%s '
                'events_emitted=%s scan_to_block=%s action=degraded_not_live_success',
                target.get('id'), _logs_fetch_status, _deg_reason, len(_deg_events),
                _deg_latest if _deg_latest is not None else 'no_advance',
            )
            return ActivityProviderResult(
                mode=mode,
                status='degraded',
                evidence_state='DEGRADED_EVIDENCE',
                truthfulness_state='UNKNOWN_RISK',
                synthetic=False,
                provider_name='evm_activity_provider',
                provider_kind='rpc',
                evidence_present=bool(_deg_events),
                recent_real_event_count=len(_deg_events),
                last_real_event_at=_deg_events[-1].observed_at if _deg_events else None,
                events=_deg_events,
                latest_block=_deg_latest,
                checkpoint=(_deg_events[-1].cursor if _deg_events else None),
                checkpoint_age_seconds=0 if _deg_events else None,
                degraded_reason=_deg_reason,
                error_code=None,
                source_type='websocket' if bool((os.getenv('EVM_WS_URL') or '').strip()) else 'rpc_polling',
                reason_code=_deg_code,
                claim_safe=False,
                detection_outcome='MONITORING_DEGRADED',
            )
        if has_evidence:
            return ActivityProviderResult(
                mode=mode,
                status='live',
                evidence_state='REAL_EVIDENCE',
                truthfulness_state='NOT_CLAIM_SAFE',
                synthetic=False,
                provider_name='evm_activity_provider',
                provider_kind='rpc',
                evidence_present=True,
                recent_real_event_count=len(live_events),
                last_real_event_at=live_events[-1].observed_at,
                events=live_events,
                latest_block=latest_block,
                checkpoint=checkpoint,
                checkpoint_age_seconds=0,
                degraded_reason=None,
                error_code=None,
                source_type='websocket' if bool((os.getenv('EVM_WS_URL') or '').strip()) else 'rpc_polling',
                reason_code=None,
                claim_safe=False,
                detection_outcome='NO_CONFIRMED_ANOMALY_FROM_REAL_EVIDENCE',
            )
        if coverage_evidence_present:
            # No blockchain events found, but RPC is reachable (fetch_evm_activity succeeded).
            # Probe eth_chainId + eth_blockNumber to get the real current block for telemetry.
            if rpc_provider_backoff_active():
                # Provider is rate-limiting. This fires both when no scan happened
                # (latest_block is None) AND when fetch_evm_activity advanced the scan
                # ceiling for a few chunks before a 429 armed the backoff mid-scan.
                # In the latter case latest_block is set but the scan was cut short, so
                # we must NOT present it as verified coverage — that partial ceiling
                # would otherwise be persisted as the cursor and skip the unscanned
                # blocks. Return a backoff-degraded result (never a coverage "success")
                # so the runner skips the poll and re-scans once the backoff clears.
                _bo = rpc_provider_backoff_status()
                logger.warning(
                    'coverage_rpc_probe_skipped target_id=%s reason=provider_backoff_active backoff_until=%s',
                    target.get('id'), _bo.get('backoff_until') or 'unknown',
                )
                return ActivityProviderResult(
                    mode=mode,
                    status='degraded',
                    evidence_state='DEGRADED_EVIDENCE',
                    truthfulness_state='UNKNOWN_RISK',
                    synthetic=False,
                    provider_name='evm_activity_provider',
                    provider_kind='rpc',
                    evidence_present=False,
                    recent_real_event_count=0,
                    last_real_event_at=None,
                    events=[],
                    latest_block=None,
                    checkpoint=None,
                    checkpoint_age_seconds=None,
                    degraded_reason='provider_backoff_active',
                    error_code=None,
                    source_type='rpc_polling',
                    reason_code='PROVIDER_BACKOFF_ACTIVE',
                    claim_safe=False,
                    detection_outcome='MONITORING_DEGRADED',
                )
            if latest_block is None:
                rpc_probe = probe_rpc_health()
                if rpc_probe['ok']:
                    # Fail closed when the RPC serves a different chain than this
                    # target is labeled with: a block height from the wrong chain
                    # (e.g. a Base height written under chain_id=1) must never be
                    # persisted as coverage telemetry. Mark the target misconfigured.
                    from services.api.app.evm_activity_provider import CHAIN_MAP as _CHAIN_MAP
                    _target_network = str(target.get('chain_network') or 'ethereum').strip().lower()
                    _expected_chain_id = (_CHAIN_MAP.get(_target_network) or {}).get('chain_id')
                    _probed_chain_id = rpc_probe.get('chain_id_int')
                    if _expected_chain_id is not None and _probed_chain_id is not None and _probed_chain_id != _expected_chain_id:
                        logger.error(
                            'coverage_rpc_chain_mismatch_fail_closed target_id=%s configured_chain=%s '
                            'resolved_chain_id=%s rpc_chain_id=%s action=skip_no_telemetry',
                            target.get('id'), _target_network, _expected_chain_id, _probed_chain_id,
                        )
                        return ActivityProviderResult(
                            mode=mode,
                            status='failed',
                            evidence_state='FAILED_EVIDENCE',
                            truthfulness_state='UNKNOWN_RISK',
                            synthetic=False,
                            provider_name='evm_activity_provider',
                            provider_kind='rpc',
                            evidence_present=False,
                            recent_real_event_count=0,
                            last_real_event_at=None,
                            events=[],
                            latest_block=None,
                            checkpoint=None,
                            checkpoint_age_seconds=None,
                            degraded_reason='chain_rpc_mismatch',
                            error_code='ChainRpcMismatch',
                            source_type='rpc_polling',
                            reason_code='CHAIN_RPC_MISMATCH',
                            claim_safe=False,
                            detection_outcome='MONITORING_DEGRADED',
                        )
                    latest_block = rpc_probe['block_number_int']
                    logger.info(
                        'coverage_rpc_probe_ok target_id=%s configured_chain=%s resolved_chain_id=%s '
                        'rpc_chain_id=%s block_number=%s',
                        target.get('id'), _target_network, _expected_chain_id,
                        _probed_chain_id, latest_block,
                    )
                else:
                    logger.warning('coverage_rpc_probe_failed error=%s', rpc_probe.get('error'))
            return ActivityProviderResult(
                mode=mode,
                status='live',
                evidence_state='REAL_EVIDENCE',
                truthfulness_state='NOT_CLAIM_SAFE',
                synthetic=False,
                provider_name='evm_activity_provider',
                provider_kind='rpc',
                evidence_present=True,
                recent_real_event_count=0,
                last_real_event_at=None,
                events=[],
                latest_block=latest_block,
                checkpoint=f'coverage:{latest_block}',
                checkpoint_age_seconds=0,
                degraded_reason=None,
                error_code=None,
                source_type='websocket' if bool((os.getenv('EVM_WS_URL') or '').strip()) else 'rpc_polling',
                reason_code='PROVIDER_COVERAGE_VERIFIED',
                claim_safe=False,
                detection_outcome='NO_CONFIRMED_ANOMALY_FROM_REAL_EVIDENCE',
            )
        return ActivityProviderResult(
            mode=mode,
            status='no_evidence',
            evidence_state='NO_EVIDENCE',
            truthfulness_state='UNKNOWN_RISK',
            synthetic=False,
            provider_name='evm_activity_provider',
            provider_kind='rpc',
            evidence_present=False,
            recent_real_event_count=0,
            last_real_event_at=None,
            events=[],
            latest_block=None,
            checkpoint=None,
            checkpoint_age_seconds=None,
            degraded_reason='no_real_provider_evidence',
            error_code=None,
            source_type='unknown',
            reason_code='NO_PROVIDER_EVIDENCE',
            claim_safe=False,
            detection_outcome='NO_EVIDENCE',
        )
    if mode == 'live' and runtime['degraded']:
        raise RuntimeError(str(runtime.get('reason') or 'live ingestion degraded'))
    if mode == 'degraded' and runtime['degraded']:
        return ActivityProviderResult(
            mode=mode,
            status='degraded',
            evidence_state='DEGRADED_EVIDENCE',
            truthfulness_state='UNKNOWN_RISK',
            synthetic=False,
            provider_name='evm_activity_provider',
            provider_kind='rpc',
            evidence_present=False,
            recent_real_event_count=0,
            last_real_event_at=None,
            events=[],
            latest_block=None,
            checkpoint=None,
            checkpoint_age_seconds=None,
            degraded_reason=str(runtime.get('reason') or 'live ingestion degraded'),
            error_code=None,
            source_type='unknown',
            reason_code='RUNTIME_DEGRADED',
            claim_safe=False,
            detection_outcome='MONITORING_DEGRADED',
        )
    if mode == 'degraded':
        return ActivityProviderResult(
            mode=mode,
            status='degraded',
            evidence_state='DEGRADED_EVIDENCE',
            truthfulness_state='UNKNOWN_RISK',
            synthetic=False,
            provider_name='evm_activity_provider',
            provider_kind='rpc',
            evidence_present=False,
            recent_real_event_count=0,
            last_real_event_at=None,
            events=[],
            latest_block=None,
            checkpoint=None,
            checkpoint_age_seconds=None,
            degraded_reason=str(runtime.get('reason') or 'degraded_mode_active'),
            error_code=None,
            source_type='unknown',
            reason_code='RUNTIME_DEGRADED',
            claim_safe=False,
            detection_outcome='MONITORING_DEGRADED',
        )
    if mode == 'hybrid':
        return ActivityProviderResult(
            mode=mode,
            status='no_evidence',
            evidence_state='NO_EVIDENCE',
            truthfulness_state='UNKNOWN_RISK',
            synthetic=False,
            provider_name='evm_activity_provider',
            provider_kind='rpc',
            evidence_present=False,
            recent_real_event_count=0,
            last_real_event_at=None,
            events=[],
            latest_block=None,
            checkpoint=None,
            checkpoint_age_seconds=None,
            degraded_reason='no_live_events_observed',
            error_code=None,
            source_type='unknown',
            reason_code='NO_PROVIDER_EVIDENCE',
            claim_safe=False,
            detection_outcome='NO_EVIDENCE',
        )
    if mode == 'demo' and _demo_mode_allowed():
        demo_activity_providers = _load_demo_activity_providers()
        return demo_activity_providers.fetch_demo_target_activity_result(target, since_ts)
    raise MonitoringModeError('demo activity providers are disabled for this environment')
