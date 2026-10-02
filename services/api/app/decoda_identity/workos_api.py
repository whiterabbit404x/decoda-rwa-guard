"""WorkOS-side facts about a sign-in, fetched with the official SDK.

A signed access token proves who the user was when it was minted; it does not
prove the WorkOS session is still alive, how the user authenticated, or that
nobody is impersonating them. At session exchange Guard asks WorkOS directly.
Any failure to reach WorkOS fails closed (the sign-in is refused), never open.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

REQUEST_TIMEOUT_SECONDS = 5.0
MAX_SESSIONS_SCANNED = 500  # far beyond any real user's concurrent sessions


class IdentityProviderUnavailable(Exception):
    """WorkOS could not be reached or answered with an error."""


@dataclass(frozen=True)
class SessionFacts:
    session_id: str
    user_id: str
    status: str  # active | revoked | expired
    auth_method: str  # password | passkey | sso | oauth | magic_code | impersonation | …
    impersonated: bool
    organization_id: str | None
    created_at: datetime | None = None

    @property
    def active(self) -> bool:
        return self.status == 'active'


class SessionLookup(Protocol):
    def find_session(self, user_id: str, session_id: str) -> SessionFacts | None: ...


def _value(raw: object) -> str:
    return str(getattr(raw, 'value', raw))


class WorkOSSessionLookup:
    """Finds one of a user's sessions via ``user_management.list_sessions``."""

    def __init__(self, client: Any, *, timeout_seconds: float = REQUEST_TIMEOUT_SECONDS):
        self._client = client
        self._options = {'timeout': timeout_seconds, 'max_retries': 1}

    def find_session(self, user_id: str, session_id: str) -> SessionFacts | None:
        try:
            page = self._client.user_management.list_sessions(user_id, limit=100, request_options=self._options)
            for seen, item in enumerate(page.auto_paging_iter()):
                if seen >= MAX_SESSIONS_SCANNED:
                    break
                if item.id != session_id:
                    continue
                auth_method = _value(item.auth_method)
                return SessionFacts(
                    session_id=item.id,
                    user_id=item.user_id,
                    status=_value(item.status),
                    auth_method=auth_method,
                    impersonated=item.impersonator is not None or auth_method == 'impersonation',
                    organization_id=item.organization_id,
                    created_at=item.created_at,
                )
        except Exception as exc:  # SDK, transport and deserialization errors alike
            raise IdentityProviderUnavailable('WorkOS session lookup failed.') from exc
        return None


def workos_client(api_key: str, client_id: str) -> Any:
    from workos import WorkOSClient

    return WorkOSClient(api_key=api_key, client_id=client_id)


def jwks_key_source(client: Any, client_id: str) -> Any:
    """PyJWT JWKS client for this application's signing keys (cached, refreshed on unknown kid)."""
    import jwt

    return jwt.PyJWKClient(
        client.user_management.get_jwks_url(client_id),
        cache_keys=True,
        lifespan=600,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
