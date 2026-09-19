"""Command line interface for the offline Decoda evidence verifier.

    python -m decoda_evidence_verifier verify evidence-package.zip --keyring keys.json
    python -m decoda_evidence_verifier verify evidence-package.zip --json
    python -m decoda_evidence_verifier inspect evidence-package.zip

Exit codes (stable):

    0   verified — integrity verified AND authenticity independently verified
        (or integrity verified with --integrity-only)
    1   FAILED — something did not verify: a hash, the manifest, the Merkle
        root, or the signature
    2   unusable — the package could not be read, or the command was misused
    3   integrity verified, authenticity NOT independently verifiable (a legacy
        HMAC-sealed package, or no usable public key was supplied)

3 is deliberately non-zero: a caller that only checks ``if verify; then`` treats
an unverifiable-authenticity package as not-ok, which is the safe direction. Use
``--integrity-only`` to opt into exit 0 for that case.

This command reads. It never writes to the package, never extracts files, never
touches the network and holds no key that could sign anything.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

if __package__ in (None, ''):  # running as `python cli.py`, not as a module
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from decoda_evidence_verifier.verifier import (  # noqa: E402
        AUTHENTICITY_FAILED, AUTHENTICITY_UNAVAILABLE, AUTHENTICITY_VERIFIED,
        INTEGRITY_VERIFIED, PackageError, VerificationReport, verify_package,
    )
else:
    from .verifier import (
        AUTHENTICITY_FAILED, AUTHENTICITY_UNAVAILABLE, AUTHENTICITY_VERIFIED,
        INTEGRITY_VERIFIED, PackageError, VerificationReport, verify_package,
    )

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_UNUSABLE = 2
EXIT_NOT_INDEPENDENTLY_VERIFIABLE = 3


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='decoda-evidence',
        description='Verify a Decoda RWA Guard evidence package offline.',
        epilog=(
            'Offline by construction: no network, no Decoda account, no Decoda API, '
            'no Decoda database and no Decoda secret.'
        ),
    )
    sub = parser.add_subparsers(dest='command', required=True)

    for name, help_text in (
        ('verify', 'Verify a package and report integrity, authenticity and audit linkage.'),
        ('inspect', 'Describe a package without asserting a verdict.'),
    ):
        command = sub.add_parser(name, help=help_text)
        command.add_argument('package', help='Path to the evidence package .zip')
        command.add_argument('--json', action='store_true', dest='as_json',
                             help='Emit machine-readable JSON instead of text.')
        command.add_argument('--keyring', dest='keyring',
                             help='Path to a Decoda public verification keyring (JSON).')
        command.add_argument('--public-key', dest='public_key',
                             help='A single base64 or PEM Ed25519 PUBLIC key to verify against.')
        command.add_argument('--key-id', dest='public_key_id',
                             help='key_id that --public-key corresponds to.')
        command.add_argument(
            '--allow-bundled-keyring', action='store_true',
            help=(
                'Trust the keyring carried INSIDE the package. Off by default: a key shipped '
                'inside the package it verifies establishes no trust on its own.'
            ),
        )
        if name == 'verify':
            command.add_argument(
                '--integrity-only', action='store_true',
                help='Exit 0 when integrity verifies, even if authenticity cannot be established.',
            )
    return parser


def _status_word(value: str) -> str:
    return {
        'verified': 'VERIFIED',
        'failed': 'FAILED',
        'unavailable': 'NOT INDEPENDENTLY VERIFIABLE',
        'present': 'PRESENT',
    }.get(value, value.upper())


def _tri(value: bool | None) -> str:
    if value is None:
        return 'NOT SEALED IN THIS PACKAGE'
    return 'VALID' if value else 'INVALID'


def _signature_word(report: VerificationReport) -> str:
    """Four distinct outcomes, kept distinct.

    A seal that could not be checked must never print the same word as one that
    checked out, and neither must print the same word as one that failed.
    """
    if not report.seal_present:
        return 'ABSENT'
    if report.authenticity == AUTHENTICITY_VERIFIED:
        return 'VALID'
    if report.authenticity == AUTHENTICITY_FAILED:
        return 'INVALID'
    return 'NOT INDEPENDENTLY VERIFIABLE'


def _render(report: VerificationReport, *, verdict: bool) -> str:
    lines = [
        '',
        'Decoda Evidence Verifier',
        '',
        f'Package ID:         {report.package_id or "—"}',
        f'Package number:     {report.package_number or "—"}',
        f'Manifest schema:    {report.manifest_schema_version or "—"}',
        f'Manifest:           {"VALID" if report.manifest_parsed else "UNREADABLE"}',
        f'Files:              {report.files_verified}/{report.files_total} VALID',
    ]
    if report.files_failed:
        lines.append(f'  Failed:           {report.files_failed}')
    if report.files_missing:
        lines.append(f'  Missing:          {report.files_missing}')
    if report.files_unexpected:
        lines.append(f'  Unexpected:       {report.files_unexpected}')
    lines.extend([
        f'Manifest SHA-256:   {_tri(report.manifest_hash_valid)}',
        f'Merkle root:        {_tri(report.merkle_root_valid)}',
        f'Signature:          {_signature_word(report)}',
    ])
    if report.signature_algorithm:
        lines.append(f'  Algorithm:        {report.signature_algorithm}')
    if report.signature_key_id:
        lines.append(f'  Key ID:           {report.signature_key_id}')
    lines.append(f'  Key source:       {report.key_source}')
    lines.append(f'Audit anchor:       {_status_word(report.audit_linkage)}')
    if verdict:
        lines.extend([
            '',
            f'Integrity:          {_status_word(report.integrity)}',
            f'Authenticity:       {_status_word(report.authenticity)}',
            f'Audit linkage:      {_status_word(report.audit_linkage)}',
        ])
    if report.authenticity_reason:
        lines.extend(['', f'  {report.authenticity_reason}'])
    if report.errors:
        lines.append('')
        lines.append('Findings:')
        # Paths and reasons only — evidence CONTENT is never printed.
        for error in report.errors[:50]:
            lines.append(f'  - {error}')
        if len(report.errors) > 50:
            lines.append(f'  … and {len(report.errors) - 50} more')
    if report.extra_paths:
        lines.append('')
        lines.append(
            f'Note: {len(report.extra_paths)} archive entr'
            f'{"y is" if len(report.extra_paths) == 1 else "ies are"} outside the manifest\'s scope '
            '(package furniture; the manifest makes no claim about them).'
        )
    if verdict and report.integrity == INTEGRITY_VERIFIED and report.authenticity == AUTHENTICITY_UNAVAILABLE:
        lines.extend([
            '',
            'Integrity is proven from this package alone. Authenticity is NOT: nothing here',
            'establishes WHO produced it. Do not present this package as proof of origin.',
        ])
    lines.append('')
    return '\n'.join(lines)


def _run(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if getattr(args, 'public_key', None) and getattr(args, 'keyring', None):
        parser.error('use --keyring or --public-key, not both')

    try:
        report = verify_package(
            args.package,
            keyring_path=args.keyring,
            public_key=args.public_key,
            public_key_id=args.public_key_id,
            allow_bundled_keyring=args.allow_bundled_keyring,
        )
    except PackageError as exc:
        payload: dict[str, Any] = {'package_path': args.package, 'error': str(exc), 'usable': False}
        if args.as_json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(f'\nDecoda Evidence Verifier\n\nPackage could not be read: {exc}\n', file=sys.stderr)
        return EXIT_UNUSABLE

    verdict = args.command == 'verify'
    if args.as_json:
        print(json.dumps(report.as_json(), indent=2, sort_keys=True))
    else:
        print(_render(report, verdict=verdict))

    if not verdict:
        return EXIT_OK
    if report.integrity != INTEGRITY_VERIFIED or report.authenticity == AUTHENTICITY_FAILED:
        return EXIT_FAILED
    if report.authenticity == AUTHENTICITY_VERIFIED:
        return EXIT_OK
    return EXIT_OK if args.integrity_only else EXIT_NOT_INDEPENDENTLY_VERIFIABLE


def main(argv: list[str] | None = None) -> int:
    try:
        return _run(argv)
    except KeyboardInterrupt:  # pragma: no cover
        return EXIT_UNUSABLE


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
