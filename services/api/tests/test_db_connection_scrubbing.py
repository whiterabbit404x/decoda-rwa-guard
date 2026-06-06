from __future__ import annotations

from services.api.app.db_failure import normalize_db_error_snippet, db_error_classification_context


def test_scrub_postgres_url_with_credentials():
    raw = 'could not connect: postgresql://user:s3cr3t@db.example.com:5432/app?sslmode=require'
    snippet = normalize_db_error_snippet(raw)
    assert snippet is not None
    assert 's3cr3t' not in snippet
    assert '[dsn-redacted]' in snippet


def test_scrub_postgres_url_with_token_password():
    raw = 'FATAL: authentication error postgresql://app:token_abc123@neon.tech/mydb'
    snippet = normalize_db_error_snippet(raw)
    assert snippet is not None
    assert 'token_abc123' not in snippet
    assert '[dsn-redacted]' in snippet


def test_scrub_redis_url_with_credentials():
    raw = 'Connection failed: redis://:password123@cache.internal:6379'
    snippet = normalize_db_error_snippet(raw)
    assert snippet is not None
    assert 'password123' not in snippet


def test_scrub_password_keyword_value():
    raw = "FATAL: password authentication failed password=mysecretpass for user app"
    snippet = normalize_db_error_snippet(raw)
    assert snippet is not None
    assert 'mysecretpass' not in snippet
    assert '[redacted]' in snippet


def test_plain_error_without_credentials_unchanged():
    raw = 'could not connect to server: Connection refused.'
    snippet = normalize_db_error_snippet(raw)
    assert snippet == 'could not connect to server: Connection refused.'


def test_scrub_applied_in_classification_context():
    exc = RuntimeError(
        'ERROR: Your account or project has exceeded the compute time quota. '
        'postgresql://app:topsecret@host.neon.tech/db '
        'connection to server at "2600:abcd::1" failed: Network is unreachable'
    )
    ctx = db_error_classification_context(exc, raw_snippet_limit=200)
    assert ctx.get('classification') == 'quota_exceeded'
    raw_snippet = ctx.get('raw_error_snippet', '')
    assert 'topsecret' not in raw_snippet


def test_url_without_credentials_not_redacted():
    raw = 'could not connect to postgresql://db.example.com:5432/app'
    snippet = normalize_db_error_snippet(raw)
    # No user:pass in the URL, so no redaction
    assert snippet is not None
    assert '[dsn-redacted]' not in snippet


def test_scrub_handles_empty_input():
    assert normalize_db_error_snippet(None) is None
    assert normalize_db_error_snippet('') is None
