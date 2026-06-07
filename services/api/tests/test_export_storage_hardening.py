"""
P2-5: Export storage hardening tests.

Verifies:
- Production local storage fails closed by default
- EXPORT_DANGEROUS_ALLOW_LOCAL_NON_WORM_STORAGE=true (new flag) is break-glass
- Legacy EXPORT_ALLOW_LOCAL_IN_PRODUCTION=true still accepted (backward compat)
- S3 backend passes in production
- export_storage_warning reflects WORM status
- export_storage_enterprise_ready logic
"""
from __future__ import annotations

import os
import pytest
from unittest.mock import patch


def _load_fresh():
    import importlib
    import services.api.app.export_storage as m
    importlib.reload(m)
    return m


def test_production_local_storage_fails_by_default(monkeypatch, tmp_path):
    monkeypatch.setenv('APP_MODE', 'production')
    monkeypatch.delenv('EXPORT_ALLOW_LOCAL_IN_PRODUCTION', raising=False)
    monkeypatch.delenv('EXPORT_DANGEROUS_ALLOW_LOCAL_NON_WORM_STORAGE', raising=False)
    monkeypatch.setenv('EXPORT_STORAGE_BACKEND', 'local')

    from services.api.app.export_storage import load_export_storage
    with pytest.raises(RuntimeError, match='EXPORT_DANGEROUS_ALLOW_LOCAL_NON_WORM_STORAGE'):
        load_export_storage()


def test_staging_local_storage_fails_by_default(monkeypatch, tmp_path):
    monkeypatch.setenv('APP_MODE', 'staging')
    monkeypatch.delenv('EXPORT_ALLOW_LOCAL_IN_PRODUCTION', raising=False)
    monkeypatch.delenv('EXPORT_DANGEROUS_ALLOW_LOCAL_NON_WORM_STORAGE', raising=False)
    monkeypatch.setenv('EXPORT_STORAGE_BACKEND', 'local')

    from services.api.app.export_storage import load_export_storage
    with pytest.raises(RuntimeError):
        load_export_storage()


def test_new_break_glass_flag_allows_local_in_production(monkeypatch, tmp_path):
    monkeypatch.setenv('APP_MODE', 'production')
    monkeypatch.setenv('EXPORT_DANGEROUS_ALLOW_LOCAL_NON_WORM_STORAGE', 'true')
    monkeypatch.delenv('EXPORT_ALLOW_LOCAL_IN_PRODUCTION', raising=False)
    monkeypatch.setenv('EXPORT_STORAGE_BACKEND', 'local')
    monkeypatch.setenv('EXPORTS_DIR', str(tmp_path))

    from services.api.app.export_storage import load_export_storage, LocalExportStorage
    storage = load_export_storage()
    assert isinstance(storage, LocalExportStorage)


def test_legacy_flag_still_accepted_for_backward_compat(monkeypatch, tmp_path):
    monkeypatch.setenv('APP_MODE', 'production')
    monkeypatch.setenv('EXPORT_ALLOW_LOCAL_IN_PRODUCTION', 'true')
    monkeypatch.delenv('EXPORT_DANGEROUS_ALLOW_LOCAL_NON_WORM_STORAGE', raising=False)
    monkeypatch.setenv('EXPORT_STORAGE_BACKEND', 'local')
    monkeypatch.setenv('EXPORTS_DIR', str(tmp_path))

    from services.api.app.export_storage import load_export_storage, LocalExportStorage
    storage = load_export_storage()
    assert isinstance(storage, LocalExportStorage)


def test_s3_backend_passes_in_production(monkeypatch):
    monkeypatch.setenv('APP_MODE', 'production')
    monkeypatch.setenv('EXPORT_STORAGE_BACKEND', 's3')
    monkeypatch.setenv('EXPORT_S3_BUCKET', 'my-prod-bucket')
    monkeypatch.setenv('EXPORT_S3_REGION', 'us-east-1')
    monkeypatch.delenv('EXPORT_ALLOW_LOCAL_IN_PRODUCTION', raising=False)
    monkeypatch.delenv('EXPORT_DANGEROUS_ALLOW_LOCAL_NON_WORM_STORAGE', raising=False)

    from services.api.app.export_storage import load_export_storage, S3ExportStorage
    storage = load_export_storage()
    assert isinstance(storage, S3ExportStorage)


def test_local_storage_not_enterprise_ready(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_MODE', 'local')
    monkeypatch.setenv('EXPORTS_DIR', str(tmp_path))

    from services.api.app.export_storage import load_export_storage, export_storage_enterprise_ready, export_storage_warning
    storage = load_export_storage()
    assert export_storage_enterprise_ready(storage) is False
    warning = export_storage_warning(storage)
    assert warning is not None
    assert 'WORM' in warning or 'tamper' in warning.lower()


def test_s3_with_object_lock_is_enterprise_ready(monkeypatch):
    monkeypatch.setenv('EXPORT_S3_OBJECT_LOCK_ENABLED', 'true')

    from services.api.app.export_storage import S3ExportStorage, export_storage_enterprise_ready, export_storage_warning
    storage = S3ExportStorage(bucket='b', region='us-east-1', prefix='p')
    assert export_storage_enterprise_ready(storage) is True
    assert export_storage_warning(storage) is None


def test_s3_without_object_lock_not_enterprise_ready(monkeypatch):
    monkeypatch.setenv('EXPORT_S3_OBJECT_LOCK_ENABLED', 'false')

    from services.api.app.export_storage import S3ExportStorage, export_storage_enterprise_ready, export_storage_warning
    storage = S3ExportStorage(bucket='b', region='us-east-1', prefix='p')
    assert export_storage_enterprise_ready(storage) is False
    warning = export_storage_warning(storage)
    assert warning is not None


def test_local_mode_works_without_flags(monkeypatch, tmp_path):
    monkeypatch.setenv('APP_MODE', 'local')
    monkeypatch.setenv('EXPORTS_DIR', str(tmp_path))
    monkeypatch.delenv('EXPORT_ALLOW_LOCAL_IN_PRODUCTION', raising=False)
    monkeypatch.delenv('EXPORT_DANGEROUS_ALLOW_LOCAL_NON_WORM_STORAGE', raising=False)

    from services.api.app.export_storage import load_export_storage, LocalExportStorage
    storage = load_export_storage()
    assert isinstance(storage, LocalExportStorage)
