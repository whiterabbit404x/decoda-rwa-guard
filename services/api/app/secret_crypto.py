from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, status

SECRET_SCHEME_V1 = 'aes256gcm:v1'


def _aesgcm_cls():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError('cryptography package is required for AES-256-GCM secret encryption.') from exc
    return AESGCM


@dataclass(frozen=True)
class EncryptedSecret:
    scheme: str
    key_id: str
    iv_b64: str
    ciphertext_b64: str

    def serialize(self) -> str:
        return f'{self.scheme}:{self.key_id}:{self.iv_b64}:{self.ciphertext_b64}'


def _secret_key_required() -> bytes:
    raw = os.getenv('SECRET_ENCRYPTION_KEY', '').strip()
    if not raw:
        app_mode = os.getenv('APP_MODE', 'local').strip().lower()
        if app_mode in {'production', 'staging'}:
            raise RuntimeError('SECRET_ENCRYPTION_KEY is required in staging/production.')
        return b''
    key_bytes = base64.b64decode(raw.encode('ascii'))
    if len(key_bytes) != 32:
        raise RuntimeError('SECRET_ENCRYPTION_KEY must decode to exactly 32 bytes.')
    return key_bytes


def encryption_ready() -> bool:
    return bool(os.getenv('SECRET_ENCRYPTION_KEY', '').strip())


def encrypt_secret(value: str, *, aad: str = '') -> str:
    key = _secret_key_required()
    if not key:
        # explicit local fallback for developer seeds; never for production/staging
        return f'legacy_b64:{base64.b64encode(value.encode("utf-8")).decode("ascii")}'
    iv = os.urandom(12)
    aesgcm = _aesgcm_cls()(key)
    ciphertext = aesgcm.encrypt(iv, value.encode('utf-8'), aad.encode('utf-8'))
    key_id = os.getenv('SECRET_ENCRYPTION_KEY_ID', 'env-default').strip() or 'env-default'
    payload = EncryptedSecret(
        scheme=SECRET_SCHEME_V1,
        key_id=key_id,
        iv_b64=base64.urlsafe_b64encode(iv).decode('ascii').rstrip('='),
        ciphertext_b64=base64.urlsafe_b64encode(ciphertext).decode('ascii').rstrip('='),
    )
    return payload.serialize()


def _b64url_decode(value: str) -> bytes:
    padding = '=' * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode('ascii'))


def decrypt_secret(value: str, *, aad: str = '') -> str:
    if not value:
        return ''
    if value.startswith('legacy_b64:'):
        return base64.b64decode(value.split(':', 1)[1].encode('ascii')).decode('utf-8')
    parts = value.split(':', 4)
    if len(parts) == 5 and ':'.join(parts[:2]) == SECRET_SCHEME_V1:
        _scheme, _version, _key_id, iv_b64, ciphertext_b64 = parts
        key = _secret_key_required()
        if not key:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='Secret key is not configured for decryption.')
        aesgcm = _aesgcm_cls()(key)
        try:
            plaintext = aesgcm.decrypt(_b64url_decode(iv_b64), _b64url_decode(ciphertext_b64), aad.encode('utf-8'))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail='Stored secret could not be decrypted.') from exc
        return plaintext.decode('utf-8')
    # migration fallback: old unprefixed base64
    try:
        return base64.b64decode(value.encode('ascii')).decode('utf-8')
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail='Stored secret has invalid format.') from exc


def mask_secret(value: str, keep: int = 4) -> str:
    if not value:
        return '****'
    trimmed = value.strip()
    if len(trimmed) <= keep:
        return '*' * len(trimmed)
    return f"{'*' * (len(trimmed) - keep)}{trimmed[-keep:]}"


def validate_encryption_bootstrap() -> dict[str, Any]:
    _secret_key_required()
    return {'configured': encryption_ready(), 'scheme': SECRET_SCHEME_V1}
