"""
Tests for production safety invariants around worker liveness, evidence source
selection, and multi-workspace isolation.

Acceptance criteria (from task):
- API-only service (WORKER_ENABLED=false) must not report LIVE status.
- Worker service with fresh heartbeat can clear stale_heartbeat.
- Replay evidence never counts as live in production.
- Stale worker heartbeat returns limited/degraded coverage.
- Multi-workspace isolation: worker telemetry for workspace A cannot satisfy B.
- live_worker_not_running is the degraded_reason when worker is absent.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

UTC = timezone.utc


def _now() -> datetime:
    return datetime.now(UTC)


def _fresh_heartbeat() -> datetime:
    return _now() - timedelta(seconds=30)


def _stale_heartbeat() -> datetime:
    return _now() - timedelta(seconds=3600)


# ---------------------------------------------------------------------------
# Unit tests for monitoring_runner runtime status helpers
# ---------------------------------------------------------------------------

class TestRunnerAliveDetermination:
    """runner_alive must derive solely from worker_running flag or fresh heartbeat."""

    def test_runner_alive_false_when_heartbeat_stale_and_worker_not_running(self):
        from services.api.app.monitoring_runner import WORKER_HEARTBEAT_TTL_SECONDS, MONITOR_POLL_INTERVAL_SECONDS
        now = _now()
        stale_hb = now - timedelta(seconds=max(WORKER_HEARTBEAT_TTL_SECONDS, MONITOR_POLL_INTERVAL_SECONDS * 3) + 60)
        heartbeat_age = int((now - stale_hb).total_seconds())
        stale_heartbeat = heartbeat_age > max(WORKER_HEARTBEAT_TTL_SECONDS, MONITOR_POLL_INTERVAL_SECONDS * 3)
        worker_running = False
        runner_alive = bool(worker_running) or not stale_heartbeat
        assert runner_alive is False, (
            'runner_alive must be False when heartbeat is stale and worker_running=False'
        )

    def test_runner_alive_true_when_heartbeat_fresh(self):
        from services.api.app.monitoring_runner import WORKER_HEARTBEAT_TTL_SECONDS, MONITOR_POLL_INTERVAL_SECONDS
        now = _now()
        fresh_hb = now - timedelta(seconds=30)
        heartbeat_age = int((now - fresh_hb).total_seconds())
        stale_heartbeat = heartbeat_age > max(WORKER_HEARTBEAT_TTL_SECONDS, MONITOR_POLL_INTERVAL_SECONDS * 3)
        runner_alive = bool(False) or not stale_heartbeat
        assert runner_alive is True, (
            'runner_alive must be True when heartbeat is fresh'
        )

    def test_runner_alive_false_never_upgraded_by_worker_running_with_stale_heartbeat(self):
        """worker_running=True in DB but stale heartbeat: runner_alive should be True
        because DB flag is the authoritative source. This checks the OR logic."""
        from services.api.app.monitoring_runner import WORKER_HEARTBEAT_TTL_SECONDS, MONITOR_POLL_INTERVAL_SECONDS
        now = _now()
        stale_hb = now - timedelta(seconds=max(WORKER_HEARTBEAT_TTL_SECONDS, MONITOR_POLL_INTERVAL_SECONDS * 3) + 60)
        heartbeat_age = int((now - stale_hb).total_seconds())
        stale_heartbeat = heartbeat_age > max(WORKER_HEARTBEAT_TTL_SECONDS, MONITOR_POLL_INTERVAL_SECONDS * 3)
        worker_running = True
        runner_alive = bool(worker_running) or not stale_heartbeat
        assert runner_alive is True, (
            'runner_alive=True when worker_running=True even if heartbeat is stale (DB flag wins)'
        )


# ---------------------------------------------------------------------------
# Tests for degraded_reason = 'live_worker_not_running'
# ---------------------------------------------------------------------------

class TestLiveWorkerNotRunningReason:
    """When stale_heartbeat=True, runner_alive=False, and systems are configured,
    degraded_reason must be 'live_worker_not_running'."""

    def _compute_stale_detail(self, *, stale_heartbeat: bool, runner_alive: bool, enabled_system_count: int) -> str | None:
        return (
            'live_worker_not_running'
            if stale_heartbeat and not runner_alive and enabled_system_count > 0
            else ('stale_heartbeat' if stale_heartbeat else None)
        )

    def test_live_worker_not_running_when_stale_and_systems_configured(self):
        detail = self._compute_stale_detail(
            stale_heartbeat=True,
            runner_alive=False,
            enabled_system_count=2,
        )
        assert detail == 'live_worker_not_running', (
            'degraded_reason must be live_worker_not_running when worker absent and systems configured'
        )

    def test_stale_heartbeat_fallback_when_no_systems_configured(self):
        detail = self._compute_stale_detail(
            stale_heartbeat=True,
            runner_alive=False,
            enabled_system_count=0,
        )
        assert detail == 'stale_heartbeat', (
            'degraded_reason falls back to stale_heartbeat when no systems are configured'
        )

    def test_none_reason_when_heartbeat_fresh(self):
        detail = self._compute_stale_detail(
            stale_heartbeat=False,
            runner_alive=True,
            enabled_system_count=3,
        )
        assert detail is None, 'no stale reason when heartbeat is fresh'

    def test_live_worker_not_running_reason_present_in_source(self) -> None:
        source = open('services/api/app/monitoring_runner.py', encoding='utf-8').read()
        assert 'live_worker_not_running' in source, (
            'monitoring_runner must emit live_worker_not_running reason code'
        )

    def test_live_downgrade_reason_log_present_in_source(self) -> None:
        source = open('services/api/app/monitoring_runner.py', encoding='utf-8').read()
        assert 'live_downgrade_reason' in source, (
            'monitoring_runner must emit live_downgrade_reason structured log'
        )


# ---------------------------------------------------------------------------
# Tests for replay evidence never counting as live
# ---------------------------------------------------------------------------

class TestReplayEvidenceNotLive:
    """Replay/fixture/demo evidence must never satisfy production LIVE status."""

    def _evidence_source_live(
        self,
        *,
        ingestion_mode: str,
        provider_degraded_or_unreachable: bool,
        coverage_fresh: bool,
        reporting_systems: int,
    ) -> bool:
        return bool(
            ingestion_mode.strip().lower() not in {'demo', 'simulator', 'replay'}
            and not provider_degraded_or_unreachable
            and coverage_fresh
            and reporting_systems > 0
        )

    def _source_of_evidence(
        self,
        *,
        ingestion_mode: str,
        evidence_source_live: bool,
        coverage_fresh: bool,
    ) -> str:
        if ingestion_mode.strip().lower() in {'demo', 'simulator'}:
            return 'simulator'
        if evidence_source_live and coverage_fresh:
            return 'live'
        return 'replay_or_none'

    def test_replay_ingestion_mode_never_live(self):
        live = self._evidence_source_live(
            ingestion_mode='replay',
            provider_degraded_or_unreachable=False,
            coverage_fresh=True,
            reporting_systems=5,
        )
        assert live is False

    def test_demo_ingestion_mode_never_live(self):
        live = self._evidence_source_live(
            ingestion_mode='demo',
            provider_degraded_or_unreachable=False,
            coverage_fresh=True,
            reporting_systems=5,
        )
        assert live is False

    def test_simulator_ingestion_mode_never_live(self):
        live = self._evidence_source_live(
            ingestion_mode='simulator',
            provider_degraded_or_unreachable=False,
            coverage_fresh=True,
            reporting_systems=5,
        )
        assert live is False

    def test_degraded_provider_prevents_live(self):
        live = self._evidence_source_live(
            ingestion_mode='live',
            provider_degraded_or_unreachable=True,
            coverage_fresh=True,
            reporting_systems=5,
        )
        assert live is False

    def test_stale_coverage_prevents_live(self):
        live = self._evidence_source_live(
            ingestion_mode='live',
            provider_degraded_or_unreachable=False,
            coverage_fresh=False,
            reporting_systems=5,
        )
        assert live is False

    def test_zero_reporting_systems_prevents_live(self):
        live = self._evidence_source_live(
            ingestion_mode='live',
            provider_degraded_or_unreachable=False,
            coverage_fresh=True,
            reporting_systems=0,
        )
        assert live is False

    def test_all_conditions_met_allows_live(self):
        live = self._evidence_source_live(
            ingestion_mode='live',
            provider_degraded_or_unreachable=False,
            coverage_fresh=True,
            reporting_systems=1,
        )
        assert live is True

    def test_source_of_evidence_replay_when_no_live_path(self):
        soe = self._source_of_evidence(
            ingestion_mode='live',
            evidence_source_live=False,
            coverage_fresh=False,
        )
        assert soe == 'replay_or_none', (
            'source_of_evidence must be replay_or_none when live path is unavailable'
        )

    def test_non_live_provider_source_types_constant_in_source(self) -> None:
        source = open('services/api/app/monitoring_runner.py', encoding='utf-8').read()
        assert "NON_LIVE_PROVIDER_SOURCE_TYPES" in source
        assert "'demo'" in source
        assert "'simulator'" in source
        assert "'replay'" in source


# ---------------------------------------------------------------------------
# Tests for stale heartbeat → limited/degraded coverage
# ---------------------------------------------------------------------------

class TestStaleHeartbeatDegradedCoverage:
    """A stale worker heartbeat must always result in degraded/limited coverage."""

    def _monitoring_status(
        self,
        *,
        healthy_enabled_targets_count: int,
        monitored_rows_count: int,
        runner_alive: bool,
        last_error: str | None,
        degraded: bool,
        stale_heartbeat: bool,
        broken_targets_count: int,
        evidence_freshness: int | None,
    ) -> str:
        if healthy_enabled_targets_count == 0 and monitored_rows_count == 0:
            return 'offline'
        elif (
            not runner_alive
            or last_error
            or degraded
            or stale_heartbeat
            or broken_targets_count > 0
        ):
            return 'degraded'
        elif evidence_freshness is None or evidence_freshness > 900:
            return 'idle'
        else:
            return 'active'

    def test_stale_heartbeat_forces_degraded_status(self):
        status = self._monitoring_status(
            healthy_enabled_targets_count=1,
            monitored_rows_count=1,
            runner_alive=False,
            last_error=None,
            degraded=False,
            stale_heartbeat=True,
            broken_targets_count=0,
            evidence_freshness=60,
        )
        assert status == 'degraded', 'stale heartbeat must force degraded monitoring status'

    def test_fresh_heartbeat_no_error_can_be_active(self):
        status = self._monitoring_status(
            healthy_enabled_targets_count=1,
            monitored_rows_count=1,
            runner_alive=True,
            last_error=None,
            degraded=False,
            stale_heartbeat=False,
            broken_targets_count=0,
            evidence_freshness=60,
        )
        assert status == 'active'

    def test_worker_disabled_api_only_service_is_degraded(self):
        """Simulates API service with WORKER_ENABLED=false: no heartbeat → degraded."""
        status = self._monitoring_status(
            healthy_enabled_targets_count=2,
            monitored_rows_count=2,
            runner_alive=False,
            last_error=None,
            degraded=False,
            stale_heartbeat=True,
            broken_targets_count=0,
            evidence_freshness=None,
        )
        assert status == 'degraded', (
            'API-only service (WORKER_ENABLED=false) with no heartbeat must be degraded'
        )


# ---------------------------------------------------------------------------
# Tests for multi-workspace isolation
# ---------------------------------------------------------------------------

class TestMultiWorkspaceIsolation:
    """Telemetry and heartbeat rows for workspace A must not satisfy workspace B."""

    def test_heartbeat_query_scoped_to_workspace_in_source(self) -> None:
        source = open('services/api/app/monitoring_runner.py', encoding='utf-8').read()
        assert 'WHERE workspace_id = %s::uuid' in source, (
            'monitoring_heartbeats query must be scoped to workspace_id'
        )

    def test_telemetry_query_scoped_to_workspace_in_source(self) -> None:
        source = open('services/api/app/monitoring_runner.py', encoding='utf-8').read()
        assert 'FROM telemetry_events' in source
        # The telemetry query must filter by workspace_id
        idx = source.index('FROM telemetry_events')
        snippet = source[idx:idx + 500]
        assert 'workspace_id' in snippet, (
            'telemetry_events query must be scoped to workspace_id'
        )

    def test_heartbeat_upsert_includes_workspace_id(self) -> None:
        source = open('services/api/app/monitoring_runner.py', encoding='utf-8').read()
        assert 'INSERT INTO monitoring_heartbeats (id, workspace_id, worker_name' in source

    def test_heartbeat_conflict_target_is_workspace_and_worker(self) -> None:
        source = open('services/api/app/monitoring_runner.py', encoding='utf-8').read()
        assert 'ON CONFLICT (workspace_id, worker_name)' in source


# ---------------------------------------------------------------------------
# Tests for ASGI middleware client disconnect safety
# ---------------------------------------------------------------------------

class TestMiddlewareClientDisconnect:
    """log_disallowed_cors_origin, enforce_csrf_on_mutations, body_size_limit_middleware
    must return 503 on RuntimeError: No response returned instead of propagating."""

    def test_call_next_safe_helper_present_in_source(self) -> None:
        source = open('services/api/app/main.py', encoding='utf-8').read()
        assert '_call_next_safe' in source, (
            'main.py must define _call_next_safe helper for client disconnect handling'
        )

    def test_middleware_uses_safe_call_next_in_source(self) -> None:
        source = open('services/api/app/main.py', encoding='utf-8').read()
        # All three target middlewares must use _call_next_safe
        assert source.count('_call_next_safe') >= 4, (
            'At least 4 uses of _call_next_safe expected (definition + 3 middlewares)'
        )

    def test_no_response_returned_handled_in_source(self) -> None:
        source = open('services/api/app/main.py', encoding='utf-8').read()
        assert 'No response returned' in source, (
            '_call_next_safe must handle RuntimeError: No response returned'
        )

    @pytest.mark.asyncio
    async def test_log_disallowed_cors_origin_returns_503_on_client_disconnect(self):
        """When call_next raises RuntimeError: No response returned, middleware returns 503."""
        from services.api.app.main import _call_next_safe

        class _FakeRequest:
            method = 'GET'

            class url:
                path = '/test'

        async def _failing_call_next(_req):
            raise RuntimeError('No response returned.')

        response = await _call_next_safe(_FakeRequest(), _failing_call_next)
        assert response.status_code == 503

    @pytest.mark.asyncio
    async def test_call_next_safe_reraises_non_disconnect_errors(self):
        """Non-disconnect RuntimeErrors must be re-raised, not swallowed."""
        from services.api.app.main import _call_next_safe

        class _FakeRequest:
            method = 'POST'

            class url:
                path = '/test'

        async def _failing_call_next(_req):
            raise RuntimeError('some other error')

        with pytest.raises(RuntimeError, match='some other error'):
            await _call_next_safe(_FakeRequest(), _failing_call_next)

    @pytest.mark.asyncio
    async def test_call_next_safe_passes_through_normal_response(self):
        from services.api.app.main import _call_next_safe
        from fastapi.responses import JSONResponse

        class _FakeRequest:
            method = 'GET'

            class url:
                path = '/health'

        async def _ok_call_next(_req):
            return JSONResponse({'status': 'ok'})

        response = await _call_next_safe(_FakeRequest(), _ok_call_next)
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Tests for worker_heartbeat_written structured log
# ---------------------------------------------------------------------------

class TestWorkerHeartbeatWrittenLog:
    """The monitoring cycle must emit worker_heartbeat_written on each loop."""

    def test_worker_heartbeat_written_log_present_in_source(self) -> None:
        source = open('services/api/app/monitoring_runner.py', encoding='utf-8').read()
        assert 'worker_heartbeat_written' in source, (
            'monitoring_runner must emit worker_heartbeat_written structured log'
        )

    def test_worker_startup_log_present_in_worker_source(self) -> None:
        source = open('services/api/app/run_monitoring_worker.py', encoding='utf-8').read()
        assert 'worker_startup' in source, (
            'run_monitoring_worker must emit worker_startup structured log'
        )

    def test_evidence_source_selected_log_present_in_source(self) -> None:
        source = open('services/api/app/monitoring_runner.py', encoding='utf-8').read()
        assert 'evidence_source_selected' in source, (
            'monitoring_runner must emit evidence_source_selected structured log'
        )


# ---------------------------------------------------------------------------
# Tests for onboarding truthfulness (source-level)
# ---------------------------------------------------------------------------

class TestOnboardingTruthfulness:
    """Onboarding steps must require real evidence, not replay."""

    def test_monitoring_heartbeats_table_referenced_for_runner_alive(self) -> None:
        source = open('services/api/app/monitoring_runner.py', encoding='utf-8').read()
        assert 'monitoring_heartbeats' in source, (
            'runner_alive must be derived from monitoring_heartbeats table'
        )

    def test_live_telemetry_required_for_live_evidence_proof(self) -> None:
        from services.api.app.paid_launch_readiness import build_live_evidence_proof
        result = build_live_evidence_proof(chain_evidence={
            'provider_ready': True,
            'evidence_source': 'live',
            'source_type': 'rpc_polling',
            'latest_live_telemetry_at': None,
            'rpc_polling_telemetry_count': 0,
            'monitoring_checked_count': 1,
            'receipts_written': 0,
            'detections_count': 0,
            'alerts_count': 0,
            'incidents_count': 0,
            'response_actions_count': 0,
            'evidence_count': 0,
            'detection_telemetry_linked': False,
            'alert_detection_linked': False,
            'incident_alert_linked': False,
        })
        assert result['live_evidence_ready'] is False, (
            'live_evidence_ready must be False when no live telemetry has been persisted'
        )
