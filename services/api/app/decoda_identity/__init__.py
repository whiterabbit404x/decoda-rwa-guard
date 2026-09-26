"""Shared Decoda identity for RWA Guard (WorkOS AuthKit + Decoda platform).

    config        GUARD_IDENTITY_MODE (legacy | dual | workos), fail-closed checks, mode gates
    tokens        verify WorkOS access tokens (RS256 / JWKS) — never trust the BFF
    workos_api    ask WorkOS whether a session is live and how it was established
    platform      read the platform's membership / entitlement / revocation contract
    session_gate  per-request checks for every session under the shared identity
    exchange      turn a verified WorkOS sign-in into a Guard session
    context       organizations and products for the switchers

Guard's product RBAC, tenancy, plans, detection and incident semantics are
unchanged: this package only decides who may hold a Guard session.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from .config import load_identity_settings
from .platform import PlatformDirectory
from .tokens import IdentityTokenVerifier
from .workos_api import SessionLookup, WorkOSSessionLookup, jwks_key_source, workos_client


@dataclass(frozen=True)
class IdentityServices:
    verifier: IdentityTokenVerifier
    sessions: SessionLookup
    directory: PlatformDirectory


_services: IdentityServices | None = None
_directory: PlatformDirectory | None = None
_lock = threading.Lock()


def get_directory() -> PlatformDirectory:
    global _directory
    if _directory is None:
        settings = load_identity_settings()
        with _lock:
            if _directory is None:
                _directory = PlatformDirectory(settings.platform_database_url, cache_ttl_seconds=settings.access_cache_ttl_seconds)
    return _directory


def get_identity_services() -> IdentityServices:
    global _services
    if _services is not None:
        return _services
    settings = load_identity_settings()
    with _lock:
        if _services is None:
            client: Any = workos_client(settings.workos_api_key, settings.workos_client_id)
            _services = IdentityServices(
                verifier=IdentityTokenVerifier(
                    keys=jwks_key_source(client, settings.workos_client_id),
                    client_id=settings.workos_client_id,
                    issuer=settings.workos_issuer,
                ),
                sessions=WorkOSSessionLookup(client),
                directory=get_directory(),
            )
    return _services


def set_identity_services(services: IdentityServices | None) -> None:
    """Test hook: install (or clear) the services and the directory they use."""
    global _services, _directory
    with _lock:
        _services = services
        _directory = services.directory if services is not None else None
