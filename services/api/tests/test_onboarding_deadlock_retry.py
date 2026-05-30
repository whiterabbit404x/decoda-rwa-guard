from __future__ import annotations

import inspect

import pytest


def test_deadlock_detected_is_importable():
    """psycopg.errors.DeadlockDetected is importable and is the correct error class."""
    from psycopg import errors as psycopg_errors
    assert hasattr(psycopg_errors, 'DeadlockDetected')
    assert issubclass(psycopg_errors.DeadlockDetected, Exception)


def test_monitoring_runner_imports_sleep():
    """monitoring_runner imports sleep for deadlock retry backoff."""
    import services.api.app.monitoring_runner as runner_module
    source = inspect.getsource(runner_module)
    assert 'from time import' in source
    assert 'sleep' in source.split('from time import', 1)[1].split('\n', 1)[0]


def test_error_handler_guards_failed_update_on_deadlock():
    """Error handler must not set runtime_status='failed' when exc is DeadlockDetected."""
    import services.api.app.monitoring_runner as runner_module
    source = inspect.getsource(runner_module)

    # Locate the 'failed' update inside the error handler section
    failed_marker = "UPDATE monitored_systems SET runtime_status = 'failed'"
    idx = source.index(failed_marker)
    # Look at the ~300 chars before the failed update for the DeadlockDetected guard
    context = source[max(0, idx - 350):idx + 50]
    assert 'DeadlockDetected' in context, (
        "monitored_system 'failed' update must be guarded against DeadlockDetected"
    )


def test_deadlock_retry_loop_present():
    """monitoring_runner must include a retry loop for monitored_system deadlocks."""
    import services.api.app.monitoring_runner as runner_module
    source = inspect.getsource(runner_module)
    assert 'deadlock_retry_exhausted' in source, (
        "monitoring_runner must log deadlock_retry_exhausted when retries are exhausted"
    )


def test_deadlock_retry_uses_sleep():
    """Retry loop must call sleep() for backoff between attempts."""
    import services.api.app.monitoring_runner as runner_module
    source = inspect.getsource(runner_module)

    retry_marker = 'deadlock_retry_exhausted'
    retry_idx = source.index(retry_marker)
    # sleep must appear in the 500 chars before the exhausted log
    context_before = source[max(0, retry_idx - 500):retry_idx]
    assert 'sleep' in context_before, (
        "deadlock retry loop must call sleep() for backoff"
    )


def test_monitored_system_updates_outside_main_transaction():
    """monitored_system updates must be in a separate savepoint from process_monitoring_target."""
    import services.api.app.monitoring_runner as runner_module
    source = inspect.getsource(runner_module)

    process_call_marker = 'result = process_monitoring_target(connection, target)'
    update_status_marker = "UPDATE monitored_systems\n"

    # The process_monitoring_target call must appear before the monitored_systems UPDATE
    process_idx = source.index(process_call_marker)
    # Find the first monitored_systems UPDATE after process_monitoring_target
    update_idx = source.index(update_status_marker, process_idx)

    # The monitored_systems UPDATE (update_idx) must NOT be inside the same
    # connection.transaction() block as process_monitoring_target.
    # We verify by checking that there is a closing `with connection.transaction():` line
    # between process_call_marker and update_status_marker.
    between = source[process_idx:update_idx]
    # After the process call ends the main savepoint, a new savepoint is opened for the update
    assert 'with connection.transaction():' in between, (
        "monitored_system updates must be in a separate savepoint from process_monitoring_target"
    )
