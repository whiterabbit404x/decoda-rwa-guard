from __future__ import annotations

import hashlib
import logging
import importlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
import os

from services.api.app.evm_activity_provider import fetch_evm_activity
from services.api.app.monitoring_mode import (
    MonitoringModeError,
    resolve_monitoring_mode,
)
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
        'evm_rpc_url': bool((os.getenv('EVM_RPC_URL') or '').strip()),
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
        return {'mode': mode, 'source': 'degraded', 'degraded': True, 'reason': 'EVM_RPC_URL missing'}
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
    target_type = str(target.get('target_type') or '').lower()
    runtime = monitoring_ingestion_runtime()
    mode = runtime['mode']
    can_use_live = (not runtime['degraded']) and target_type in {'wallet', 'contract'}
    if mode in {'live', 'hybrid'} and can_use_live:
        try:
            live_events = fetch_evm_activity(target, since_ts)
        except Exception as exc:
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
                source_type='unknown',
                reason_code='PROVIDER_FAILED',
                claim_safe=False,
                detection_outcome='ANALYSIS_FAILED',
            )
        has_evidence = bool(live_events)
        latest_block = None
        checkpoint = None
        if has_evidence:
            block_candidates: list[int] = []
            for event in live_events:
                block_number = event.payload.get('block_number') if isinstance(event.payload, dict) else None
                if isinstance(block_number, int):
                    block_candidates.append(block_number)
                if isinstance(event.payload, dict):
                    metadata = event.payload.get('metadata') if isinstance(event.payload.get('metadata'), dict) else {}
                    event.payload['metadata'] = {
                        **metadata,
                        'evidence_origin': 'real',
                        'provider_name': 'evm_activity_provider',
                        'production_claim_eligible': True,
                    }
            latest_block = max(block_candidates) if block_candidates else None
            checkpoint = live_events[-1].cursor
            if any(event.ingestion_source == 'demo' for event in live_events):
                raise MonitoringModeError('synthetic event leaked into live/hybrid provider stream')
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
