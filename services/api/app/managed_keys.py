"""Version-aware managed key loading for production cryptographic material.

Production and staging use a managed secret provider. Environment-backed keys remain
available only for local development and tests. AWS Secrets Manager versions are
addressable so historical evidence can be verified after rotation.
"""
from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any


@dataclass(frozen=True)
class ManagedKey:
    purpose: str
    provider: str
    key_id: str
    version: str
    material: bytes

    @property
    def reference(self) -> dict[str, str]:
        return {'provider': self.provider, 'key_id': self.key_id, 'version': self.version}


def production_like() -> bool:
    app_mode = os.getenv('APP_MODE', 'local').strip().lower()
    app_env = os.getenv('APP_ENV', app_mode).strip().lower()
    return app_mode in {'production', 'staging', 'prod'} or app_env in {'production', 'staging', 'prod'}


def managed_key_provider() -> str:
    return os.getenv('MANAGED_KEY_PROVIDER', 'env').strip().lower() or 'env'


def _purpose_env_prefix(purpose: str) -> str:
    normalized = purpose.strip().upper().replace('-', '_')
    aliases = {
        'AUTH': 'AUTH_TOKEN',
        'ENCRYPTION': 'SECRET_ENCRYPTION',
        'EVIDENCE_SIGNING': 'EVIDENCE_SIGNING',
    }
    return aliases.get(normalized, normalized)


def _decode_secret_value(value: str, *, encoding: str) -> bytes:
    if encoding == 'base64':
        try:
            return base64.b64decode(value.encode('ascii'), validate=True)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError('Managed key value is not valid base64.') from exc
    return value.encode('utf-8')


def _extract_aws_secret(response: dict[str, Any]) -> str:
    if response.get('SecretString') is not None:
        raw = str(response['SecretString'])
    elif response.get('SecretBinary') is not None:
        binary = response['SecretBinary']
        return bytes(binary).decode('utf-8') if isinstance(binary, (bytes, bytearray)) else base64.b64decode(binary).decode('utf-8')
    else:
        raise RuntimeError('Managed secret response did not contain key material.')
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if isinstance(payload, dict):
        for field in ('key', 'secret', 'value', 'material'):
            if payload.get(field):
                return str(payload[field])
    return raw


@lru_cache(maxsize=64)
def _load_key_cached(purpose: str, version: str | None) -> ManagedKey:
    provider = managed_key_provider()
    prefix = _purpose_env_prefix(purpose)
    encoding = os.getenv(f'{prefix}_KEY_ENCODING', 'utf8').strip().lower()

    if provider in {'aws_secrets_manager', 'aws-secrets-manager'}:
        secret_id = os.getenv(f'{prefix}_KEY_SECRET_ID', '').strip()
        if not secret_id:
            raise RuntimeError(f'{prefix}_KEY_SECRET_ID is required for AWS Secrets Manager.')
        import boto3

        client = boto3.client('secretsmanager', region_name=os.getenv('AWS_REGION') or None)
        kwargs: dict[str, str] = {'SecretId': secret_id}
        requested_version = version or os.getenv(f'{prefix}_KEY_VERSION', '').strip()
        if requested_version:
            kwargs['VersionId'] = requested_version
        else:
            kwargs['VersionStage'] = 'AWSCURRENT'
        response = client.get_secret_value(**kwargs)
        material = _decode_secret_value(_extract_aws_secret(response), encoding=encoding)
        resolved_version = str(response.get('VersionId') or requested_version or 'AWSCURRENT')
        return ManagedKey(purpose, 'aws_secrets_manager', secret_id, resolved_version, material)

    if provider != 'env':
        raise RuntimeError(f'Unsupported MANAGED_KEY_PROVIDER: {provider}')
    if production_like():
        raise RuntimeError('MANAGED_KEY_PROVIDER must be a managed provider in staging/production; static environment keys are forbidden.')

    legacy_names = {
        'AUTH': ('AUTH_TOKEN_SECRET', 'JWT_SECRET'),
        'ENCRYPTION': ('SECRET_ENCRYPTION_KEY',),
        'EVIDENCE_SIGNING': ('EXPORT_SIGNING_SECRET', 'EVIDENCE_SIGNING_SECRET'),
    }
    raw = next((os.getenv(name, '').strip() for name in legacy_names.get(purpose.upper(), ()) if os.getenv(name, '').strip()), '')
    if not raw:
        raise RuntimeError(f'No local environment key configured for {purpose}.')
    key_id = os.getenv(f'{prefix}_KEY_ID', os.getenv('EXPORT_SIGNING_KEY_ID', 'env-default')).strip() or 'env-default'
    return ManagedKey(purpose, 'env', key_id, version or 'env-current', _decode_secret_value(raw, encoding=encoding))


def load_managed_key(purpose: str, *, version: str | None = None) -> ManagedKey:
    normalized = purpose.strip().upper()
    # Environment-backed local keys must reflect per-test/per-process changes immediately.
    if managed_key_provider() == 'env':
        return _load_key_cached.__wrapped__(normalized, version or None)
    return _load_key_cached(normalized, version or None)


def managed_keys_ready() -> bool:
    if production_like():
        return managed_key_provider() not in {'', 'env'}
    return True


def clear_managed_key_cache() -> None:
    _load_key_cached.cache_clear()
