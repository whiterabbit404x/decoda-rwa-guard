from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.security.release_security import (
    MANDATORY_GATES,
    generate_security_proof,
    validate_exceptions,
    validate_security_proof,
)


def _write_complete_inputs(root: Path) -> None:
    for gate in MANDATORY_GATES:
        path = root / 'gates' / f'{gate}.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'status': 'pass', 'summary': 'passed'}))
    for component in ('api', 'web'):
        files = {
            f'sbom/{component}.spdx.json': '{}',
            f'images/{component}.digest': 'sha256:' + ('a' if component == 'api' else 'b') * 64,
            f'signatures/{component}.bundle.json': '{}',
            f'attestations/{component}-sbom.bundle.json': '{}',
            f'attestations/{component}-provenance.bundle.json': '{}',
        }
        for relative, content in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)


def test_security_proof_requires_every_gate_and_supply_chain_record(tmp_path: Path, monkeypatch) -> None:
    exceptions = tmp_path / 'exceptions.json'
    exceptions.write_text('{"schema_version": 1, "exceptions": []}')
    monkeypatch.setattr('scripts.security.release_security.EXCEPTIONS_PATH', exceptions)
    _write_complete_inputs(tmp_path)

    proof = generate_security_proof(tmp_path)
    ok, issues = validate_security_proof(tmp_path / 'summary.json', artifact_root=tmp_path)

    assert proof['overall_status'] == 'pass'
    assert ok, issues
    assert proof['artifacts']['api']['digest']['image_digest'].startswith('sha256:')
    assert proof['artifacts']['web']['sbom']['sha256'] == hashlib.sha256(b'{}').hexdigest()


def test_security_proof_rejects_missing_signature(tmp_path: Path, monkeypatch) -> None:
    exceptions = tmp_path / 'exceptions.json'
    exceptions.write_text('{"schema_version": 1, "exceptions": []}')
    monkeypatch.setattr('scripts.security.release_security.EXCEPTIONS_PATH', exceptions)
    _write_complete_inputs(tmp_path)
    (tmp_path / 'signatures' / 'api.bundle.json').unlink()

    proof = generate_security_proof(tmp_path)

    assert proof['overall_status'] == 'fail'
    assert any('api signature record missing' in blocker for blocker in proof['blockers'])


def test_expired_or_overlong_vulnerability_exception_is_rejected(tmp_path: Path) -> None:
    now = datetime(2026, 6, 8, tzinfo=timezone.utc)
    document = {
        'schema_version': 1,
        'exceptions': [{
            'id': 'RISK-1', 'scanner': 'pip-audit', 'vulnerability_id': 'CVE-2026-1',
            'scope': 'services/api/requirements.txt', 'justification': 'compensating control',
            'owner': 'owner@example.com', 'approved_by': 'approver@example.com',
            'created_at': (now - timedelta(days=40)).isoformat(),
            'expires_at': (now - timedelta(days=1)).isoformat(),
        }],
    }
    path = tmp_path / 'exceptions.json'
    path.write_text(json.dumps(document))

    ok, issues, _ = validate_exceptions(path, now=now)

    assert not ok
    assert any('expired' in issue for issue in issues)
    assert any('30-day maximum' in issue for issue in issues)
