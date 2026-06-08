from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

_log = logging.getLogger(__name__)


class ExportStorage(Protocol):
    backend_name: str

    def write_bytes(self, *, object_key: str, content: bytes) -> str:
        ...

    def read_bytes(self, *, object_key: str) -> bytes:
        ...

    def delete_bytes(self, *, object_key: str) -> None:
        ...

    def object_lock_status(self) -> dict[str, Any]:
        """Return object-lock/WORM metadata for this backend."""
        ...


@dataclass
class LocalExportStorage:
    root_dir: Path
    backend_name: str = 'local'

    def write_bytes(self, *, object_key: str, content: bytes) -> str:
        destination = (self.root_dir / object_key).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        return object_key

    def read_bytes(self, *, object_key: str) -> bytes:
        target = (self.root_dir / object_key).resolve()
        if not target.exists():
            raise FileNotFoundError(f'Export object missing for key {object_key}')
        return target.read_bytes()

    def delete_bytes(self, *, object_key: str) -> None:
        target = (self.root_dir / object_key).resolve()
        if target.is_file():
            target.unlink()

    def object_lock_status(self) -> dict[str, Any]:
        return {
            'object_lock_enabled': False,
            'retention_mode': None,
            'retention_until': None,
            'worm': False,
            'warning': 'Local filesystem storage is not WORM. Evidence may not be durable or tamper-proof.',
        }


@dataclass
class S3ExportStorage:
    bucket: str
    region: str
    prefix: str
    endpoint: str | None = None
    backend_name: str = 's3'
    _object_lock_enabled: bool | None = field(default=None, repr=False)

    def _client(self):
        import boto3

        kwargs = {'region_name': self.region}
        if self.endpoint:
            kwargs['endpoint_url'] = self.endpoint
        return boto3.client('s3', **kwargs)

    def write_bytes(self, *, object_key: str, content: bytes) -> str:
        full_key = f'{self.prefix}/{object_key}'.strip('/')
        self._client().put_object(Bucket=self.bucket, Key=full_key, Body=content)
        return full_key

    def read_bytes(self, *, object_key: str) -> bytes:
        response = self._client().get_object(Bucket=self.bucket, Key=object_key)
        return response['Body'].read()

    def delete_bytes(self, *, object_key: str) -> None:
        self._client().delete_object(Bucket=self.bucket, Key=object_key)

    def object_lock_status(self) -> dict[str, Any]:
        # Use env override if available (avoids live S3 call in tests)
        env_override = os.getenv('EXPORT_S3_OBJECT_LOCK_ENABLED', '').strip().lower()
        if env_override == 'true':
            return {'object_lock_enabled': True, 'retention_mode': None, 'retention_until': None, 'worm': True}
        if env_override == 'false':
            return {'object_lock_enabled': False, 'retention_mode': None, 'retention_until': None, 'worm': False}
        try:
            resp = self._client().get_object_lock_configuration(Bucket=self.bucket)
            cfg = resp.get('ObjectLockConfiguration', {})
            enabled = cfg.get('ObjectLockEnabled') == 'Enabled'
            rule = (cfg.get('Rule') or {}).get('DefaultRetention') or {}
            return {
                'object_lock_enabled': enabled,
                'retention_mode': rule.get('Mode'),
                'retention_until': None,
                'worm': enabled,
            }
        except Exception:
            return {'object_lock_enabled': None, 'retention_mode': None, 'retention_until': None, 'worm': None}


def _is_break_glass_local_allowed() -> bool:
    """Return True if the unmistakably-named break-glass override is set."""
    new_flag = os.getenv('EXPORT_DANGEROUS_ALLOW_LOCAL_NON_WORM_STORAGE', '').strip().lower() == 'true'
    # Accept legacy flag for backward compatibility, but prefer the new name
    legacy_flag = os.getenv('EXPORT_ALLOW_LOCAL_IN_PRODUCTION', '').strip().lower() == 'true'
    return new_flag or legacy_flag


def load_export_storage() -> ExportStorage:
    backend = os.getenv('EXPORT_STORAGE_BACKEND', 'local').strip().lower()
    app_mode = os.getenv('APP_MODE', 'local').strip().lower()
    app_env = os.getenv('APP_ENV', app_mode).strip().lower()
    is_production_like = app_mode in {'production', 'staging'} or app_env in {'production', 'staging', 'prod'}

    if backend == 's3':
        bucket = os.getenv('EXPORT_S3_BUCKET', '').strip()
        region = os.getenv('EXPORT_S3_REGION', '').strip() or 'us-east-1'
        prefix = os.getenv('EXPORT_S3_PREFIX', 'decoda-exports').strip()
        endpoint = os.getenv('EXPORT_S3_ENDPOINT', '').strip() or None
        if not bucket:
            raise RuntimeError('EXPORT_S3_BUCKET is required when EXPORT_STORAGE_BACKEND=s3.')
        return S3ExportStorage(bucket=bucket, region=region, prefix=prefix, endpoint=endpoint)

    if is_production_like:
        if not _is_break_glass_local_allowed():
            raise RuntimeError(
                'Local export storage backend is disabled in staging/production. '
                'Set EXPORT_STORAGE_BACKEND=s3 and configure EXPORT_S3_BUCKET. '
                'To override (NOT WORM, NOT durable — break-glass only), set '
                'EXPORT_DANGEROUS_ALLOW_LOCAL_NON_WORM_STORAGE=true. '
                'enterprise_ready will be false when this override is active.'
            )
        _log.warning(
            'export_storage_local_in_production app_mode=%s '
            'EXPORT_DANGEROUS_ALLOW_LOCAL_NON_WORM_STORAGE=true: '
            'Using non-WORM ephemeral local storage. '
            'Evidence is NOT durable and NOT tamper-proof. '
            'enterprise_ready=false.',
            app_mode,
        )

    export_root = Path(os.getenv('EXPORTS_DIR', '/tmp/decoda-exports')).resolve()
    return LocalExportStorage(root_dir=export_root)


def export_storage_enterprise_ready(storage: ExportStorage) -> bool:
    """Return True only when the storage backend is WORM-capable (S3 with object lock)."""
    lock = storage.object_lock_status()
    return bool(lock.get('worm'))


def export_storage_warning(storage: ExportStorage) -> str | None:
    """Return a warning string if storage is not enterprise-grade, else None."""
    if storage.backend_name == 'local':
        return (
            'Non-WORM local storage active. '
            'Export evidence is NOT durable and NOT tamper-proof. '
            'Configure EXPORT_STORAGE_BACKEND=s3 with Object Lock for production compliance.'
        )
    lock = storage.object_lock_status()
    if not lock.get('worm'):
        return 'S3 Object Lock is not enabled on this bucket. Export evidence may not be tamper-proof.'
    return None
