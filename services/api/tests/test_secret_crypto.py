import importlib.util
import base64

import pytest

from services.api.app.secret_crypto import decrypt_secret, encrypt_secret


pytestmark = pytest.mark.skipif(importlib.util.find_spec('cryptography') is None, reason='cryptography package unavailable in environment')


def test_secret_roundtrip(monkeypatch):
    key = base64.b64encode(b'0' * 32).decode('ascii')
    monkeypatch.setenv('SECRET_ENCRYPTION_KEY', key)
    encrypted = encrypt_secret('super-secret', aad='workspace:1')
    assert encrypted.startswith('aes256gcm:v1:')
    assert decrypt_secret(encrypted, aad='workspace:1') == 'super-secret'


def test_secret_decrypt_wrong_key_fails(monkeypatch):
    key1 = base64.b64encode(b'1' * 32).decode('ascii')
    key2 = base64.b64encode(b'2' * 32).decode('ascii')
    monkeypatch.setenv('SECRET_ENCRYPTION_KEY', key1)
    encrypted = encrypt_secret('hello')
    monkeypatch.setenv('SECRET_ENCRYPTION_KEY', key2)
    with pytest.raises(Exception):
        decrypt_secret(encrypted)
