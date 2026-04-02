import base64
import json
import os
import secrets
from typing import Any

SCHEME = 'aes256gcm-v1'
LEGACY_SCHEME = 'base64-v0'


def _aesgcm():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except Exception as exc:  # pragma: no cover
        raise RuntimeError('cryptography package is required for secret encryption.') from exc
    return AESGCM


def _master_key_raw() -> bytes:
    raw = os.getenv('SECRETS_MASTER_KEY', '').strip()
    if not raw:
        raise RuntimeError('SECRETS_MASTER_KEY is required.')
    decoded = base64.urlsafe_b64decode(raw + '=' * (-len(raw) % 4))
    if len(decoded) != 32:
        raise RuntimeError('SECRETS_MASTER_KEY must decode to exactly 32 bytes.')
    return decoded


def encrypt_secret(value: str) -> str:
    nonce = secrets.token_bytes(12)
    aes = _aesgcm()(_master_key_raw())
    ciphertext = aes.encrypt(nonce, value.encode('utf-8'), None)
    return json.dumps({'scheme': SCHEME, 'kid': os.getenv('SECRETS_MASTER_KEY_ID', 'default'), 'nonce': base64.urlsafe_b64encode(nonce).decode('ascii').rstrip('='), 'ciphertext': base64.urlsafe_b64encode(ciphertext).decode('ascii').rstrip('=')}, separators=(',', ':'))


def decrypt_secret(value: str) -> tuple[str, str]:
    if not value:
        return '', SCHEME
    try:
        payload: dict[str, Any] = json.loads(value)
    except Exception:
        try:
            return base64.b64decode(value.encode('ascii')).decode('utf-8'), LEGACY_SCHEME
        except Exception:
            return value, LEGACY_SCHEME
    if payload.get('scheme') != SCHEME:
        raise RuntimeError(f"Unsupported secret scheme: {payload.get('scheme')}")
    nonce = base64.urlsafe_b64decode(str(payload.get('nonce', '')) + '=' * (-len(str(payload.get('nonce', ''))) % 4))
    ciphertext = base64.urlsafe_b64decode(str(payload.get('ciphertext', '')) + '=' * (-len(str(payload.get('ciphertext', ''))) % 4))
    aes = _aesgcm()(_master_key_raw())
    return aes.decrypt(nonce, ciphertext, None).decode('utf-8'), SCHEME


def needs_reencrypt(encoded: str) -> bool:
    try:
        return json.loads(encoded).get('scheme') != SCHEME
    except Exception:
        return True


def mask_secret(value: str) -> str:
    trimmed = value.strip()
    return '****' if len(trimmed) <= 4 else f"****{trimmed[-4:]}"
