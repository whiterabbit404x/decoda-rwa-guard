from __future__ import annotations

import os
from unittest.mock import MagicMock, patch, call


def test_blacklist_session_token_writes_to_redis(monkeypatch):
    monkeypatch.setenv('REDIS_URL', 'redis://localhost:6379')
    from services.api.app import pilot
    mock_redis = MagicMock()
    pilot._session_blacklist_redis = mock_redis

    pilot._blacklist_session_token('abc123hash', 3600)

    mock_redis.setex.assert_called_once_with('pilot:session:revoked:abc123hash', 3600, '1')


def test_blacklist_enforces_minimum_ttl(monkeypatch):
    monkeypatch.setenv('REDIS_URL', 'redis://localhost:6379')
    from services.api.app import pilot
    mock_redis = MagicMock()
    pilot._session_blacklist_redis = mock_redis

    pilot._blacklist_session_token('myhash', 0)

    args = mock_redis.setex.call_args
    assert args[0][1] >= 60  # minimum 60s TTL


def test_blacklist_is_session_blacklisted_returns_true_when_key_exists(monkeypatch):
    monkeypatch.setenv('REDIS_URL', 'redis://localhost:6379')
    from services.api.app import pilot
    mock_redis = MagicMock()
    mock_redis.exists.return_value = 1
    pilot._session_blacklist_redis = mock_redis

    assert pilot._is_session_blacklisted('some-hash') is True
    mock_redis.exists.assert_called_once_with('pilot:session:revoked:some-hash')


def test_blacklist_is_session_blacklisted_returns_false_when_key_absent(monkeypatch):
    monkeypatch.setenv('REDIS_URL', 'redis://localhost:6379')
    from services.api.app import pilot
    mock_redis = MagicMock()
    mock_redis.exists.return_value = 0
    pilot._session_blacklist_redis = mock_redis

    assert pilot._is_session_blacklisted('nonexistent') is False


def test_is_session_blacklisted_returns_false_without_redis(monkeypatch):
    monkeypatch.delenv('REDIS_URL', raising=False)
    from services.api.app import pilot
    pilot._session_blacklist_redis = None

    result = pilot._is_session_blacklisted('anyhash')
    assert result is False


def test_blacklist_does_not_raise_on_redis_error(monkeypatch):
    monkeypatch.setenv('REDIS_URL', 'redis://localhost:6379')
    from services.api.app import pilot
    mock_redis = MagicMock()
    mock_redis.setex.side_effect = ConnectionError('Redis down')
    pilot._session_blacklist_redis = mock_redis

    # Must not raise
    pilot._blacklist_session_token('hash', 3600)


def test_is_session_blacklisted_returns_false_on_redis_error(monkeypatch):
    monkeypatch.setenv('REDIS_URL', 'redis://localhost:6379')
    from services.api.app import pilot
    mock_redis = MagicMock()
    mock_redis.exists.side_effect = ConnectionError('Redis down')
    pilot._session_blacklist_redis = mock_redis

    assert pilot._is_session_blacklisted('hash') is False


def test_validate_session_rejects_blacklisted_token(monkeypatch):
    monkeypatch.setenv('REDIS_URL', 'redis://localhost:6379')
    monkeypatch.setenv('AUTH_TOKEN_SECRET', 'test-secret-validate')
    from services.api.app import pilot
    from fastapi import HTTPException

    token = 'fake.token'
    token_hash = pilot._auth_token_hash(token)

    mock_redis = MagicMock()
    mock_redis.exists.return_value = 1  # token is blacklisted
    pilot._session_blacklist_redis = mock_redis

    mock_connection = MagicMock()

    with pytest.raises(HTTPException) as exc_info:
        pilot._validate_session(mock_connection, token, {'sub': 'user123', 'sv': 1})
    assert exc_info.value.status_code == 401
    assert 'no longer active' in exc_info.value.detail

    # DB should not be queried when Redis blacklist says revoked
    mock_connection.execute.assert_not_called()


import pytest
