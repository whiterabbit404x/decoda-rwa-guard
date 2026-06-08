#!/usr/bin/env python3
"""Generate and validate fail-closed release security evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
SECURITY_DIR = REPO_ROOT / 'artifacts' / 'security' / 'latest'
EXCEPTIONS_PATH = REPO_ROOT / 'security' / 'vulnerability-exceptions.json'
MANDATORY_GATES = (
    'sast', 'python_dependency_audit', 'javascript_dependency_audit',
    'secret_scan', 'infrastructure_config_scan', 'api_container_scan',
    'web_container_scan',
)
ALLOWED_EXCEPTION_SCANNERS = {'pip-audit', 'npm-audit', 'trivy-api', 'trivy-web'}
DIGEST_RE = re.compile(r'^sha256:[0-9a-f]{64}$')
MAX_EXCEPTION_DAYS = 30


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(65536), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('timestamp must include a timezone')
    return parsed.astimezone(timezone.utc)


def validate_exceptions(path: Path | None = None, *, now: datetime | None = None) -> tuple[bool, list[str], list[dict[str, Any]]]:
    issues: list[str] = []
    path = path or EXCEPTIONS_PATH
    try:
        document = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return False, [f'exception file unreadable: {exc}'], []
    if document.get('schema_version') != 1 or not isinstance(document.get('exceptions'), list):
        return False, ['exception file must have schema_version=1 and an exceptions array'], []
    current = now or datetime.now(timezone.utc)
    seen_ids: set[str] = set()
    valid: list[dict[str, Any]] = []
    required = ('id', 'scanner', 'vulnerability_id', 'scope', 'justification', 'owner', 'approved_by', 'created_at', 'expires_at')
    for index, item in enumerate(document['exceptions']):
        label = f'exceptions[{index}]'
        if not isinstance(item, dict):
            issues.append(f'{label} must be an object')
            continue
        missing = [field for field in required if not str(item.get(field, '')).strip()]
        if missing:
            issues.append(f'{label} missing required fields: {", ".join(missing)}')
            continue
        if item['id'] in seen_ids:
            issues.append(f'{label} duplicate id {item["id"]}')
        seen_ids.add(item['id'])
        if item['scanner'] not in ALLOWED_EXCEPTION_SCANNERS:
            issues.append(f'{label} scanner {item["scanner"]!r} is not exception-eligible')
        try:
            created = _timestamp(item['created_at'])
            expires = _timestamp(item['expires_at'])
            if expires <= current:
                issues.append(f'{label} expired at {item["expires_at"]}')
            if expires <= created:
                issues.append(f'{label} expires_at must be after created_at')
            if (expires - created).total_seconds() > MAX_EXCEPTION_DAYS * 86400:
                issues.append(f'{label} exceeds the {MAX_EXCEPTION_DAYS}-day maximum')
        except (TypeError, ValueError) as exc:
            issues.append(f'{label} has invalid timestamps: {exc}')
        valid.append(item)
    return not issues, issues, valid


def write_exception_config(output_dir: Path, path: Path | None = None) -> None:
    ok, issues, exceptions = validate_exceptions(path)
    if not ok:
        raise SystemExit('\n'.join(issues))
    output_dir.mkdir(parents=True, exist_ok=True)
    by_scanner = {scanner: [] for scanner in ALLOWED_EXCEPTION_SCANNERS}
    for item in exceptions:
        by_scanner[item['scanner']].append(item['vulnerability_id'])
    (output_dir / 'pip-audit-args.txt').write_text(' '.join(f'--ignore-vuln {v}' for v in by_scanner['pip-audit']))
    (output_dir / 'audit-ci.json').write_text(json.dumps({
        'high': True,
        'allowlist': sorted(by_scanner['npm-audit']),
        'report-type': 'full',
    }, indent=2) + '\n')
    for scanner in ('trivy-api', 'trivy-web'):
        (output_dir / f'{scanner}.ignore').write_text('\n'.join(sorted(by_scanner[scanner])) + ('\n' if by_scanner[scanner] else ''))


def _git_value(*args: str) -> str:
    try:
        return subprocess.check_output(['git', *args], cwd=REPO_ROOT, text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return 'unknown'


def generate_security_proof(directory: Path = SECURITY_DIR) -> dict[str, Any]:
    gates: dict[str, Any] = {}
    blockers: list[str] = []
    for gate in MANDATORY_GATES:
        status_path = directory / 'gates' / f'{gate}.json'
        try:
            status = json.loads(status_path.read_text())
        except (OSError, json.JSONDecodeError):
            status = {'status': 'missing', 'summary': 'mandatory gate result absent'}
        gates[gate] = status
        if status.get('status') != 'pass':
            blockers.append(f'mandatory security gate {gate} status={status.get("status", "missing")}')

    exception_ok, exception_issues, exceptions = validate_exceptions()
    if not exception_ok:
        blockers.extend(f'vulnerability exception invalid: {issue}' for issue in exception_issues)

    artifacts: dict[str, Any] = {}
    specs = {
        'api': {
            'sbom': 'sbom/api.spdx.json', 'digest': 'images/api.digest',
            'signature': 'signatures/api.bundle.json',
            'sbom_attestation': 'attestations/api-sbom.bundle.json',
            'provenance': 'attestations/api-provenance.bundle.json',
        },
        'web': {
            'sbom': 'sbom/web.spdx.json', 'digest': 'images/web.digest',
            'signature': 'signatures/web.bundle.json',
            'sbom_attestation': 'attestations/web-sbom.bundle.json',
            'provenance': 'attestations/web-provenance.bundle.json',
        },
    }
    for component, records in specs.items():
        component_data: dict[str, Any] = {}
        for record, relative in records.items():
            path = directory / relative
            if not path.is_file() or path.stat().st_size == 0:
                blockers.append(f'{component} {record} record missing: {relative}')
                component_data[record] = {'path': relative, 'status': 'missing'}
                continue
            entry = {'path': relative, 'status': 'present', 'sha256': _sha256(path)}
            if record == 'digest':
                digest = path.read_text().strip()
                entry['image_digest'] = digest
                if not DIGEST_RE.fullmatch(digest):
                    blockers.append(f'{component} image digest is invalid')
            component_data[record] = entry
        artifacts[component] = component_data

    proof = {
        'schema_version': 1,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'commit_sha': os.getenv('GITHUB_SHA') or _git_value('rev-parse', 'HEAD'),
        'branch': os.getenv('GITHUB_REF_NAME') or _git_value('rev-parse', '--abbrev-ref', 'HEAD'),
        'release_channel': os.getenv('RELEASE_ENVIRONMENT', 'ci'),
        'policy': {
            'blocking_severities': ['CRITICAL', 'HIGH'],
            'exploitability_rule': 'fixed-version-available or scanner-reported exploitable',
            'ignore_unfixed': True,
            'exception_max_days': MAX_EXCEPTION_DAYS,
        },
        'mandatory_gates': gates,
        'active_exceptions': [{k: v for k, v in item.items() if k not in {'justification'}} for item in exceptions],
        'artifacts': artifacts,
        'overall_status': 'pass' if not blockers else 'fail',
        'blockers': sorted(set(blockers)),
    }
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'summary.json').write_text(json.dumps(proof, indent=2) + '\n')
    return proof


def validate_security_proof(path: Path, *, artifact_root: Path | None = None) -> tuple[bool, list[str]]:
    issues: list[str] = []
    try:
        proof = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return False, [f'security proof missing or unreadable: {exc}']
    if proof.get('schema_version') != 1:
        issues.append('security proof schema_version must be 1')
    gates = proof.get('mandatory_gates') or {}
    for gate in MANDATORY_GATES:
        if (gates.get(gate) or {}).get('status') != 'pass':
            issues.append(f'mandatory security gate {gate} is absent or not pass')
    root = artifact_root or path.parent
    for component in ('api', 'web'):
        records = (proof.get('artifacts') or {}).get(component) or {}
        for record in ('sbom', 'digest', 'signature', 'sbom_attestation', 'provenance'):
            entry = records.get(record) or {}
            relative = entry.get('path')
            target = root / relative if isinstance(relative, str) else None
            if entry.get('status') != 'present' or target is None or not target.is_file():
                issues.append(f'{component} {record} record is absent')
                continue
            if entry.get('sha256') != _sha256(target):
                issues.append(f'{component} {record} digest mismatch')
            if record == 'digest' and not DIGEST_RE.fullmatch(str(entry.get('image_digest', ''))):
                issues.append(f'{component} image digest is invalid')
    if proof.get('overall_status') != 'pass' or proof.get('blockers'):
        issues.append('security proof must pass without blockers')
    return not issues, sorted(set(issues))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('validate-exceptions', 'write-exception-config', 'generate', 'validate'))
    parser.add_argument('--output-dir', type=Path, default=SECURITY_DIR / 'exceptions')
    parser.add_argument('--proof', type=Path, default=SECURITY_DIR / 'summary.json')
    args = parser.parse_args()
    if args.command == 'validate-exceptions':
        ok, issues, _ = validate_exceptions()
        if not ok:
            print('\n'.join(issues))
            return 1
        print('Vulnerability exceptions are valid.')
        return 0
    if args.command == 'write-exception-config':
        write_exception_config(args.output_dir)
        return 0
    if args.command == 'generate':
        proof = generate_security_proof(args.proof.parent)
        print(json.dumps({'overall_status': proof['overall_status'], 'blockers': proof['blockers']}, indent=2))
        return 0 if proof['overall_status'] == 'pass' else 1
    ok, issues = validate_security_proof(args.proof)
    if not ok:
        print('\n'.join(issues))
        return 1
    print('Security release proof is valid.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
