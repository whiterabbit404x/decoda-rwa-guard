from __future__ import annotations

import base64
from datetime import datetime, timezone
from pathlib import Path

import pytest

from services.api.app import credential_rotation, managed_keys


def test_rotation_schedule_and_fingerprint_are_deterministic():
    rotated_at = datetime(2026, 6, 8, tzinfo=timezone.utc)
    assert credential_rotation.next_rotation_at('api_key', rotated_at=rotated_at).isoformat() == '2026-09-06T00:00:00+00:00'
    assert credential_rotation.credential_fingerprint('secret') == 'sha256:2bb80d537b1da3e38bd30361aa855686bde0eacd7162fef6a25fe97bf527a25b'


def test_rotation_schedule_rejects_unknown_type():
    with pytest.raises(ValueError, match='Unsupported credential type'):
        credential_rotation.next_rotation_at('database_password')


def test_aws_managed_key_rotation_creates_promoted_version(monkeypatch):
    calls = []

    class Client:
        def put_secret_value(self, **kwargs):
            calls.append(kwargs)
            return {'VersionId': 'new-version'}

        def get_secret_value(self, **kwargs):
            assert kwargs == {'SecretId': 'auth/signing', 'VersionId': 'new-version'}
            return {'SecretString': '{"value":"rotated-material"}', 'VersionId': 'new-version'}

    class Boto:
        @staticmethod
        def client(name, region_name=None):
            assert name == 'secretsmanager'
            return Client()

    monkeypatch.setitem(__import__('sys').modules, 'boto3', Boto)
    monkeypatch.setenv('MANAGED_KEY_PROVIDER', 'aws_secrets_manager')
    monkeypatch.setenv('AUTH_TOKEN_KEY_SECRET_ID', 'auth/signing')
    managed_keys.clear_managed_key_cache()
    key = managed_keys.rotate_managed_key('AUTH')
    assert key.version == 'new-version'
    assert calls[0]['VersionStages'] == ['AWSCURRENT']
    assert calls[0]['SecretId'] == 'auth/signing'


def test_rotation_migration_and_routes_cover_all_secret_classes():
    migration = Path('services/api/migrations/0099_credential_rotation_automation.sql').read_text()
    pilot = Path('services/api/app/pilot.py').read_text()
    main = Path('services/api/app/main.py').read_text()
    for credential_type in ('jwt_signing', 'encryption_key', 'api_key', 'webhook_secret', 'scim_token', 'oidc_client_secret', 'slack_credential'):
        assert credential_type in migration
        assert credential_type in pilot
    for table in ('credential_rotation_policies', 'credential_versions', 'credential_rotation_events'):
        assert f'CREATE TABLE IF NOT EXISTS {table}' in migration
    assert "'/workspace/security/credential-rotation/history'" in main
    assert "'/workspace/security/credentials/{credential_type}/{resource_id}/rotate'" in main
    assert "'/workspace/security/credentials/{credential_type}/{resource_id}/revoke'" in main
    assert 'FOR UPDATE SKIP LOCKED' in pilot
    assert 'require_reauthentication=True' in pilot


def test_secure_release_and_incident_requirements_are_documented():
    operations = Path('docs/OPERATIONS_RUNBOOK.md').read_text()
    recovery = Path('docs/DISASTER_RECOVERY_AND_DATA_GOVERNANCE.md').read_text()
    release = Path('docs/SECURE_RELEASE_REQUIREMENTS.md').read_text()
    workflow = Path('.github/workflows/release-attestation.yml').read_text()
    for term in ('SEV-1 Critical', 'Evidence preservation', 'Customer and regulatory notification', 'Credential compromise procedure'):
        assert term.lower() in operations.lower()
    for term in ('chain of custody', 'Credential-compromise recovery matrix', 'Recovery completion checklist'):
        assert term in recovery
    for term in ('Independent penetration testing', 'Container scanning', 'SBOM', 'Signed artifacts', 'SOC 2', '24 hours', '7 calendar days'):
        assert term in release
    for action in ('aquasecurity/trivy-action', 'anchore/sbom-action', 'sigstore/cosign-installer'):
        assert action in workflow
