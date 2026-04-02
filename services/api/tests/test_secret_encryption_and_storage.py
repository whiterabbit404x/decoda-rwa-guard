import pytest
import base64
from pathlib import Path

from services.api.app.secrets_crypto import decrypt_secret, encrypt_secret, mask_secret
from services.api.app.storage_backends import LocalFilesystemStorage


def _key() -> str:
    return base64.urlsafe_b64encode(b'a' * 32).decode('ascii').rstrip('=')


def test_secret_roundtrip(monkeypatch):
    pytest.importorskip('cryptography')
    monkeypatch.setenv('SECRETS_MASTER_KEY', _key())
    encoded = encrypt_secret('https://hooks.slack.com/services/T000/B000/abcd1234')
    decoded, scheme = decrypt_secret(encoded)
    assert decoded.endswith('abcd1234')
    assert scheme == 'aes256gcm-v1'


def test_secret_mask(monkeypatch):
    monkeypatch.setenv('SECRETS_MASTER_KEY', _key())
    assert mask_secret('token-123456') == '****3456'


def test_local_storage_backend(tmp_path: Path):
    backend = LocalFilesystemStorage(tmp_path)
    ref = backend.put_bytes('workspace/export.csv', b'col\n1\n', 'text/csv')
    assert ref.key == 'workspace/export.csv'
    assert backend.read_bytes(ref.key) == b'col\n1\n'
