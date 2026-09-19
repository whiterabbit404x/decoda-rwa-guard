"""Standalone offline verifier for Decoda RWA Guard evidence packages.

Imports nothing from the Decoda application, holds no secret and cannot sign.
See ``verifier.py`` for the verification contract and ``README.md`` for usage.
"""
from __future__ import annotations

__version__ = '1.0.0'

from .verifier import (  # noqa: F401
    PackageError,
    VerificationReport,
    canonical_json,
    compute_merkle_root,
    leaf_hash,
    load_keyring,
    signing_payload,
    verify_package,
)

__all__ = [
    'PackageError',
    'VerificationReport',
    'canonical_json',
    'compute_merkle_root',
    'leaf_hash',
    'load_keyring',
    'signing_payload',
    'verify_package',
    '__version__',
]
