from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class ExportStorage(Protocol):
    backend_name: str

    def write_bytes(self, *, object_key: str, content: bytes) -> str:
        ...

    def read_bytes(self, *, object_key: str) -> bytes:
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


@dataclass
class S3ExportStorage:
    bucket: str
    region: str
    prefix: str
    endpoint: str | None = None
    backend_name: str = 's3'

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


def load_export_storage() -> ExportStorage:
    backend = os.getenv('EXPORT_STORAGE_BACKEND', 'local').strip().lower()
    app_mode = os.getenv('APP_MODE', 'local').strip().lower()

    if backend == 's3':
        bucket = os.getenv('EXPORT_S3_BUCKET', '').strip()
        region = os.getenv('EXPORT_S3_REGION', '').strip() or 'us-east-1'
        prefix = os.getenv('EXPORT_S3_PREFIX', 'decoda-exports').strip()
        endpoint = os.getenv('EXPORT_S3_ENDPOINT', '').strip() or None
        if not bucket:
            raise RuntimeError('EXPORT_S3_BUCKET is required when EXPORT_STORAGE_BACKEND=s3.')
        return S3ExportStorage(bucket=bucket, region=region, prefix=prefix, endpoint=endpoint)

    if app_mode in {'production', 'staging'}:
        raise RuntimeError('Local export storage backend is disabled in staging/production. Set EXPORT_STORAGE_BACKEND=s3.')

    export_root = Path(os.getenv('EXPORTS_DIR', '/tmp/decoda-exports')).resolve()
    return LocalExportStorage(root_dir=export_root)
