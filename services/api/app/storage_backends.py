from __future__ import annotations

import io
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class StoredObjectRef:
    backend: str
    key: str


class StorageBackend:
    backend_name = 'local'

    def put_bytes(self, key: str, content: bytes, content_type: str) -> StoredObjectRef:
        raise NotImplementedError

    def read_bytes(self, key: str) -> bytes:
        raise NotImplementedError

    def signed_download_url(self, key: str, ttl_seconds: int) -> str | None:
        return None


class LocalFilesystemStorage(StorageBackend):
    backend_name = 'local'

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put_bytes(self, key: str, content: bytes, content_type: str) -> StoredObjectRef:
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return StoredObjectRef(backend=self.backend_name, key=key)

    def read_bytes(self, key: str) -> bytes:
        path = self.root / key
        return path.read_bytes()


class S3Storage(StorageBackend):
    backend_name = 's3'

    def __init__(self):
        import boto3

        bucket = os.getenv('EXPORTS_S3_BUCKET', '').strip()
        region = os.getenv('EXPORTS_S3_REGION', '').strip() or None
        endpoint = os.getenv('EXPORTS_S3_ENDPOINT', '').strip() or None
        if not bucket:
            raise RuntimeError('EXPORTS_S3_BUCKET is required when EXPORTS_STORAGE_BACKEND=s3')
        self.bucket = bucket
        self.client = boto3.client('s3', region_name=region, endpoint_url=endpoint)

    def put_bytes(self, key: str, content: bytes, content_type: str) -> StoredObjectRef:
        self.client.upload_fileobj(io.BytesIO(content), self.bucket, key, ExtraArgs={'ContentType': content_type})
        return StoredObjectRef(backend=self.backend_name, key=key)

    def read_bytes(self, key: str) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        return response['Body'].read()

    def signed_download_url(self, key: str, ttl_seconds: int) -> str | None:
        return self.client.generate_presigned_url(
            ClientMethod='get_object',
            Params={'Bucket': self.bucket, 'Key': key},
            ExpiresIn=ttl_seconds,
        )


def load_storage_backend() -> StorageBackend:
    mode = os.getenv('EXPORTS_STORAGE_BACKEND', 'local').strip().lower() or 'local'
    if mode == 's3':
        return S3Storage()
    if mode != 'local':
        raise RuntimeError('EXPORTS_STORAGE_BACKEND must be local or s3')
    root = Path(os.getenv('EXPORTS_DIR', '/tmp/decoda-exports'))
    return LocalFilesystemStorage(root)
