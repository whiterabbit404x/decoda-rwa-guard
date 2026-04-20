from __future__ import annotations

from services.api.app.db_failure import (
    classify_db_error,
    db_error_reason_label,
    extract_db_host_from_dsn,
)


def test_classify_detects_neon_quota_exhaustion() -> None:
    exc = RuntimeError('ERROR: Your account or project has exceeded the compute time quota.')
    assert classify_db_error(exc) == 'quota_exceeded'


def test_classify_prioritizes_quota_over_ipv6_network_noise() -> None:
    try:
        raise RuntimeError('ERROR: Your account or project has exceeded the compute time quota.')
    except RuntimeError as quota_exc:
        try:
            raise RuntimeError('connection to server at "2600:abcd::1", port 5432 failed: Network is unreachable') from quota_exc
        except RuntimeError as combined_exc:
            assert classify_db_error(combined_exc) == 'quota_exceeded'


def test_classify_detects_network_unreachable_patterns() -> None:
    exc = RuntimeError('connection to server at "2600:abcd::1", port 5432 failed: Network is unreachable')
    assert classify_db_error(exc) == 'network_unreachable'

    connect_error = RuntimeError('could not connect to server: Connection refused')
    assert classify_db_error(connect_error) == 'network_unreachable'


def test_classify_detects_auth_patterns() -> None:
    exc = RuntimeError('FATAL: password authentication failed for user "app"')
    assert classify_db_error(exc) == 'auth_error'


def test_classify_detects_db_unavailable_patterns() -> None:
    exc = RuntimeError('database temporarily unavailable')
    assert classify_db_error(exc) == 'db_unavailable'


def test_classify_returns_unknown_for_unmapped_errors() -> None:
    exc = RuntimeError('syntax error at or near "CREATE"')
    assert classify_db_error(exc) == 'unknown_db_error'


def test_reason_label_maps_classifications() -> None:
    assert db_error_reason_label('quota_exceeded') == 'Database quota exhausted'
    assert db_error_reason_label('network_unreachable') == 'Database network unreachable'
    assert db_error_reason_label('db_unavailable') == 'Database temporarily unavailable'
    assert db_error_reason_label('auth_error') == 'Database authentication failed'
    assert db_error_reason_label('unknown_db_error') == 'Unknown database error'


def test_extract_db_host_from_url_dsn() -> None:
    dsn = 'postgresql://user:pass@ep-cool-frog-123456.us-east-2.aws.neon.tech:5432/neondb'
    assert extract_db_host_from_dsn(dsn) == 'ep-cool-frog-123456.us-east-2.aws.neon.tech'


def test_extract_db_host_from_keyword_dsn() -> None:
    dsn = "dbname=app user=postgres host=example.internal port=5432 sslmode=require"
    assert extract_db_host_from_dsn(dsn) == 'example.internal'


def test_extract_db_host_from_dsn_handles_empty_input() -> None:
    assert extract_db_host_from_dsn('') is None
    assert extract_db_host_from_dsn(None) is None
