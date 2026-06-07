from __future__ import annotations

import base64
import importlib
import json
from pathlib import Path

import pytest

from services.api.app import managed_keys
from services.api.app.evidence_signing import build_evidence_manifest, seal_manifest, verify_bundle
from services.api.app.secret_crypto import decrypt_secret, encrypt_secret


@pytest.fixture(autouse=True)
def clear_key_cache():
    managed_keys.clear_managed_key_cache()
    yield
    managed_keys.clear_managed_key_cache()


def test_production_rejects_static_environment_keys(monkeypatch):
    monkeypatch.setenv('APP_MODE', 'production')
    monkeypatch.setenv('MANAGED_KEY_PROVIDER', 'env')
    monkeypatch.setenv('AUTH_TOKEN_SECRET', 'not-allowed')
    with pytest.raises(RuntimeError, match='static environment keys are forbidden'):
        managed_keys.load_managed_key('AUTH')


def test_aws_secret_manager_loads_requested_historical_version(monkeypatch):
    calls = []

    class Client:
        def get_secret_value(self, **kwargs):
            calls.append(kwargs)
            return {'SecretString': json.dumps({'value': 'historical-secret'}), 'VersionId': 'v1'}

    class Boto:
        @staticmethod
        def client(name, region_name=None):
            assert name == 'secretsmanager'
            return Client()

    monkeypatch.setitem(__import__('sys').modules, 'boto3', Boto)
    monkeypatch.setenv('APP_MODE', 'production')
    monkeypatch.setenv('MANAGED_KEY_PROVIDER', 'aws_secrets_manager')
    monkeypatch.setenv('EVIDENCE_SIGNING_KEY_SECRET_ID', 'evidence/key')
    key = managed_keys.load_managed_key('EVIDENCE_SIGNING', version='v1')
    assert key.version == 'v1'
    assert key.material == b'historical-secret'
    assert calls == [{'SecretId': 'evidence/key', 'VersionId': 'v1'}]


def test_encrypted_secret_records_version_and_decrypts_after_rotation(monkeypatch):
    keys = {'v1': b'1' * 32, 'v2': b'2' * 32}

    def fake_load(purpose, version=None):
        selected = version or 'v2'
        return managed_keys.ManagedKey(purpose, 'aws_secrets_manager', 'encryption/key', selected, keys[selected])

    monkeypatch.setattr('services.api.app.secret_crypto.load_managed_key', fake_load)
    monkeypatch.setenv('APP_MODE', 'production')
    encrypted = encrypt_secret('workspace-webhook-secret', aad='workspace-1')
    assert encrypted.startswith('aes256gcm:v2:')
    assert decrypt_secret(encrypted, aad='workspace-1') == 'workspace-webhook-secret'


def test_evidence_verification_resolves_seal_key_version(monkeypatch):
    keys = {'v1': b'old-signing-key', 'v2': b'new-signing-key'}
    active = {'version': 'v1'}

    def fake_load(purpose, version=None):
        selected = version or active['version']
        return managed_keys.ManagedKey(purpose, 'aws_secrets_manager', 'evidence/key', selected, keys[selected])

    monkeypatch.setattr('services.api.app.evidence_signing.load_managed_key', fake_load)
    monkeypatch.setenv('MANAGED_KEY_PROVIDER', 'aws_secrets_manager')
    manifest, _ = build_evidence_manifest(
        export_id='e1', export_type='proof_bundle', workspace_id='w1', generated_at='2026-06-07T00:00:00Z',
        generated_by_user_id='u1', source_resource_type='incident', source_resource_id='i1', storage_backend='s3',
        file_values={'incident.json': {'id': 'i1'}},
    )
    seal = seal_manifest(manifest)
    assert seal['key_version'] == 'v1'
    active['version'] = 'v2'
    managed_keys.clear_managed_key_cache()
    assert verify_bundle({'incident.json': {'id': 'i1'}}, manifest, seal)['valid'] is True


def test_data_governance_migration_and_routes_are_present():
    migration = Path('services/api/migrations/0095_data_governance_and_managed_keys.sql').read_text()
    main = Path('services/api/app/main.py').read_text()
    pilot = Path('services/api/app/pilot.py').read_text()
    for table in ('workspace_retention_policies', 'workspace_legal_holds', 'data_deletion_requests', 'data_deletion_events', 'managed_key_versions'):
        assert f'CREATE TABLE IF NOT EXISTS {table}' in migration
    assert "'/workspace/retention-policies'" in main
    assert "'/workspace/legal-holds'" in main
    assert "'/workspace/deletion-requests'" in main
    assert 'blocking_legal_hold_ids' in pilot
    assert 'require_reauthentication=True' in pilot


def test_recovery_runbook_defines_required_targets_and_drills():
    runbook = Path('docs/DISASTER_RECOVERY_AND_DATA_GOVERNANCE.md').read_text()
    for term in ('RPO', 'RTO', 'PostgreSQL recovery', 'Redis-dependent workload recovery', 'Monitoring checkpoints', 'Evidence exports', 'Webhook and notification queues', 'Regional/provider outage exercise'):
        assert term in runbook
    assert 'validate_backup_restore.py' in runbook
    assert 'RESTORE_VALIDATION_ISOLATED=true' in runbook


def test_audit_chain_verification_accepts_recorded_retention_anchor():
    from services.api.app.evidence_signing import canonical_json, compute_audit_row_hash, verify_audit_chain
    import hashlib

    previous = 'a' * 64
    metadata = {'event': 'after-retention'}
    row = {
        'id': 'row-2', 'workspace_id': 'w1', 'user_id': 'u1', 'action': 'retention.complete',
        'entity_type': 'workspace', 'entity_id': 'w1', 'created_at': '2026-06-07T00:00:00+00:00',
        'metadata': metadata, 'previous_row_hash': previous,
    }
    row['row_hash'] = compute_audit_row_hash(
        row_id=row['id'], workspace_id='w1', user_id='u1', action=row['action'],
        entity_type=row['entity_type'], entity_id='w1', created_at_iso=row['created_at'],
        metadata_sha256=hashlib.sha256(canonical_json(metadata)).hexdigest(), previous_row_hash=previous,
    )
    assert verify_audit_chain([row])['valid'] is False
    assert verify_audit_chain([row], initial_previous_hash=previous)['valid'] is True
