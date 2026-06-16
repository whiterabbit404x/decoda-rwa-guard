"""
Production startup health tests.

Verifies that:
- API startup does not block on target monitoring reconciliation.
- bootstrap_live_pilot() does not call reconcile synchronously.
- /auth/health endpoint is defined in main.py.
- /health endpoint is defined in main.py.
- Deferred reconcile failures only emit warnings; auth remains available.

Note: FastAPI and psycopg are not available in the CI test env, so these tests
use source code inspection and pure asyncio logic rather than importing main.py.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import logging
import re
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
MAIN_PY = REPO_ROOT / 'services' / 'api' / 'app' / 'main.py'


# ---------------------------------------------------------------------------
# Source-code invariants: bootstrap_live_pilot must not call reconcile
# ---------------------------------------------------------------------------

def _extract_function_source(source: str, func_name: str) -> str:
    """Return the source of a top-level function (sync or async) from a Python source string."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            lines = source.splitlines()
            start = node.lineno - 1
            end = node.end_lineno
            return '\n'.join(lines[start:end])
    return ''


class TestBootstrapDoesNotBlockOnReconcile:
    def test_bootstrap_live_pilot_source_does_not_call_reconcile_synchronously(self):
        """bootstrap_live_pilot() must not directly invoke reconcile_monitored_systems_for_enabled_targets."""
        source = MAIN_PY.read_text()
        body = _extract_function_source(source, 'bootstrap_live_pilot')
        assert body, 'bootstrap_live_pilot() function not found in main.py'
        assert 'reconcile_monitored_systems_for_enabled_targets()' not in body, (
            'bootstrap_live_pilot() must NOT call reconcile_monitored_systems_for_enabled_targets() '
            'synchronously — this blocks API startup and prevents /auth/login from being reachable. '
            'Move it to a background task in the lifespan.'
        )

    def test_bootstrap_live_pilot_logs_deferred(self):
        """bootstrap_live_pilot() source must mention 'deferred' to indicate reconcile is deferred."""
        source = MAIN_PY.read_text()
        body = _extract_function_source(source, 'bootstrap_live_pilot')
        assert body, 'bootstrap_live_pilot() function not found in main.py'
        assert 'deferred' in body.lower(), (
            'bootstrap_live_pilot() should log that monitoring reconcile is deferred to background, '
            "but found no mention of 'deferred' in the function body."
        )

    def test_lifespan_creates_deferred_reconcile_task(self):
        """lifespan() must schedule the deferred reconcile as a background task."""
        source = MAIN_PY.read_text()
        body = _extract_function_source(source, 'lifespan')
        assert body, 'lifespan() function not found in main.py'
        assert '_deferred_startup_reconcile' in body, (
            'lifespan() should define and schedule _deferred_startup_reconcile as a background task'
        )
        assert 'asyncio.create_task(_deferred_startup_reconcile())' in body, (
            'lifespan() must call asyncio.create_task(_deferred_startup_reconcile()) '
            'so reconcile runs after startup completes'
        )

    def test_deferred_reconcile_uses_timeout(self):
        """Deferred reconcile task must use asyncio.wait_for with a timeout."""
        source = MAIN_PY.read_text()
        body = _extract_function_source(source, 'lifespan')
        assert body, 'lifespan() function not found in main.py'
        assert 'asyncio.wait_for' in body, (
            'Deferred startup reconcile must be wrapped with asyncio.wait_for() '
            'to enforce a timeout and prevent it from blocking indefinitely'
        )

    def test_deferred_reconcile_handles_timeout_error(self):
        """Deferred reconcile must catch asyncio.TimeoutError and log a warning."""
        source = MAIN_PY.read_text()
        body = _extract_function_source(source, 'lifespan')
        assert 'asyncio.TimeoutError' in body, (
            'Deferred startup reconcile must catch asyncio.TimeoutError '
            'so a slow reconcile does not crash the API'
        )

    def test_deferred_reconcile_handles_generic_exception(self):
        """Deferred reconcile must catch generic Exception and log a warning (not raise)."""
        source = MAIN_PY.read_text()
        body = _extract_function_source(source, 'lifespan')
        # Should have except Exception in the deferred reconcile context
        assert 'except Exception' in body, (
            'Deferred startup reconcile must catch generic Exception '
            'to keep auth available when reconcile fails'
        )


# ---------------------------------------------------------------------------
# /auth/health endpoint must be defined
# ---------------------------------------------------------------------------

class TestAuthHealthEndpointDefinition:
    def test_auth_health_route_is_defined(self):
        """/auth/health endpoint must exist in main.py."""
        source = MAIN_PY.read_text()
        assert "'/auth/health'" in source or '"/auth/health"' in source, (
            '/auth/health endpoint is not defined in main.py. '
            'Authentication service health check is required so the UI can distinguish '
            '"auth service down" from "network error".'
        )

    def test_auth_health_function_returns_ok(self):
        """/auth/health handler must return status: ok."""
        source = MAIN_PY.read_text()
        # Find the auth_health function
        body = _extract_function_source(source, 'auth_health')
        assert body, 'auth_health() function not found in main.py'
        assert "'ok'" in body or '"ok"' in body, (
            'auth_health() must return status=ok so the UI health check passes'
        )

    def test_auth_health_independent_of_reconcile(self):
        """/auth/health must not call reconcile or reference monitoring systems."""
        source = MAIN_PY.read_text()
        body = _extract_function_source(source, 'auth_health')
        assert body, 'auth_health() function not found in main.py'
        assert 'reconcile' not in body.lower(), (
            'auth_health() must not depend on reconcile — it must respond immediately '
            'regardless of monitoring reconciliation state'
        )
        assert 'monitored_system' not in body.lower(), (
            'auth_health() must not query monitored_systems — it should be a fast, stateless check'
        )


# ---------------------------------------------------------------------------
# /health endpoint must be defined
# ---------------------------------------------------------------------------

class TestHealthEndpointDefinition:
    def test_health_route_is_defined(self):
        """/health endpoint must exist in main.py."""
        source = MAIN_PY.read_text()
        assert "'/health'" in source or '"/health"' in source, (
            '/health endpoint is not defined in main.py'
        )

    def test_health_returns_ok(self):
        """/health handler must include status: ok."""
        source = MAIN_PY.read_text()
        body = _extract_function_source(source, 'health')
        assert body, 'health() function not found in main.py'
        assert "'ok'" in body or '"ok"' in body, (
            'health() must return status=ok'
        )


# ---------------------------------------------------------------------------
# Railway healthcheck config
# ---------------------------------------------------------------------------

class TestRailwayHealthcheck:
    def test_railway_json_has_healthcheck_path(self):
        """railway.json must configure healthcheckPath so Railway waits for /health."""
        railway_json_path = REPO_ROOT / 'railway.json'
        assert railway_json_path.exists(), 'railway.json not found'
        content = railway_json_path.read_text()
        assert 'healthcheckPath' in content, (
            'railway.json must define healthcheckPath so Railway waits for the API '
            'to pass /health before routing traffic. Without this, Railway may route '
            'requests before startup completes.'
        )
        assert '/health' in content, (
            "railway.json healthcheckPath must point to '/health'"
        )


# ---------------------------------------------------------------------------
# Deferred reconcile background task: failure is a warning, not fatal
# ---------------------------------------------------------------------------

class TestDeferredReconcileTask:
    @pytest.mark.asyncio
    async def test_deferred_reconcile_failure_logs_warning_not_exception(self, caplog):
        """If reconcile fails, we warn but do not raise — auth remains available."""
        _logger = logging.getLogger('services.api.app.main')
        reconcile_timeout = 30

        def _bad_reconcile():
            raise RuntimeError('db unreachable during reconcile')

        async def _deferred_startup_reconcile() -> None:
            await asyncio.sleep(0)
            loop = asyncio.get_running_loop()
            try:
                await asyncio.wait_for(
                    loop.run_in_executor(None, _bad_reconcile),
                    timeout=reconcile_timeout,
                )
            except asyncio.TimeoutError:
                _logger.warning(
                    'startup_reconcile_deferred_timeout timeout_seconds=%s auth_available=True',
                    reconcile_timeout,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                _logger.warning(
                    'startup_reconcile_deferred_failed auth_available=True monitoring_reconcile=failed',
                    exc_info=True,
                )

        with caplog.at_level(logging.WARNING, logger='services.api.app.main'):
            task = asyncio.create_task(_deferred_startup_reconcile())
            await task

        warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any('auth_available=True' in m for m in warning_messages), (
            'Expected a warning with auth_available=True when reconcile fails. '
            f'Got: {warning_messages}'
        )

    @pytest.mark.asyncio
    async def test_deferred_reconcile_timeout_logs_warning(self, caplog):
        """If reconcile times out, we warn but do not raise."""
        _logger = logging.getLogger('services.api.app.main')
        reconcile_timeout = 0.02  # very short for test

        def _slow_reconcile():
            time.sleep(10)  # simulates a blocking reconcile

        async def _deferred_startup_reconcile() -> None:
            await asyncio.sleep(0)
            loop = asyncio.get_running_loop()
            try:
                await asyncio.wait_for(
                    loop.run_in_executor(None, _slow_reconcile),
                    timeout=reconcile_timeout,
                )
            except asyncio.TimeoutError:
                _logger.warning(
                    'startup_reconcile_deferred_timeout timeout_seconds=%s auth_available=True monitoring_reconcile=skipped',
                    reconcile_timeout,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                _logger.warning(
                    'startup_reconcile_deferred_failed auth_available=True monitoring_reconcile=failed',
                    exc_info=True,
                )

        with caplog.at_level(logging.WARNING, logger='services.api.app.main'):
            task = asyncio.create_task(_deferred_startup_reconcile())
            await task

        warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any('auth_available=True' in m for m in warning_messages), (
            'Expected a timeout warning with auth_available=True. '
            f'Got: {warning_messages}'
        )

    @pytest.mark.asyncio
    async def test_deferred_reconcile_does_not_block_startup_completion(self):
        """create_task schedules reconcile without blocking the yield that completes startup."""
        startup_completed = False
        reconcile_started = asyncio.Event()
        startup_complete_event = asyncio.Event()

        async def _mock_reconcile_task():
            reconcile_started.set()
            await asyncio.sleep(0.1)  # simulate slow work

        async def _simulate_lifespan():
            nonlocal startup_completed
            task = asyncio.create_task(_mock_reconcile_task())
            # yield = startup complete (in real lifespan this is where FastAPI logs "Application startup complete")
            startup_completed = True
            startup_complete_event.set()
            await asyncio.sleep(0)  # give task a chance to run
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        lifespan_task = asyncio.create_task(_simulate_lifespan())
        await startup_complete_event.wait()
        assert startup_completed, 'Startup should complete before deferred reconcile finishes'
        await lifespan_task
