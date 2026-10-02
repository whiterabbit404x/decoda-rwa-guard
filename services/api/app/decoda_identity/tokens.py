"""WorkOS access-token verification for the shared Decoda identity.

The Guard API verifies every identity token itself — the web BFF only relays
it. The checks mirror the official WorkOS SDK's session validation
(``workos.session.Session.authenticate``), made stricter:

  * RS256 only, signature verified against the RWA Guard application's JWKS
    (``https://api.workos.com/sso/jwks/<client_id>``, URL built by the SDK);
  * ``exp`` / ``iat`` required, small clock-skew leeway;
  * ``iss`` must equal ``WORKOS_ISSUER`` (required in production);
  * ``sub`` / ``sid`` / ``org_id`` must be WorkOS ids of the right kind;
  * an ``aud`` claim, when present, must name this application.

A valid signature is necessary, not sufficient: the exchange also checks the
session at WorkOS and the organization's entitlement on the Decoda platform.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

import jwt

ALGORITHMS = ['RS256']
DEFAULT_LEEWAY_SECONDS = 30

_USER_ID = re.compile(r'^user_[A-Za-z0-9]{1,120}$')
_SESSION_ID = re.compile(r'^session_[A-Za-z0-9]{1,120}$')
_ORG_ID = re.compile(r'^org_[A-Za-z0-9]{1,120}$')


class IdentityTokenError(Exception):
    """The token is not a valid WorkOS access token for this application."""

    def __init__(self, code: str, message: str = 'The identity token is invalid.'):
        super().__init__(message)
        self.code = code


class IdentityKeysUnavailable(Exception):
    """The signing keys could not be fetched: fail closed, never skip the check."""


@dataclass(frozen=True)
class IdentityClaims:
    user_id: str
    session_id: str
    organization_id: str | None
    issued_at: int
    expires_at: int
    auth_time: int | None
    role: str | None


class SigningKeySource(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> Any: ...


class IdentityTokenVerifier:
    def __init__(
        self,
        *,
        keys: SigningKeySource,
        client_id: str,
        issuer: tuple[str, ...] = (),
        leeway_seconds: int = DEFAULT_LEEWAY_SECONDS,
    ):
        self._keys = keys
        self._client_id = client_id
        self._issuer = list(issuer) if issuer else None
        self._leeway = leeway_seconds

    def verify(self, token: str) -> IdentityClaims:
        if not isinstance(token, str) or token.count('.') != 2 or len(token) > 8192:
            raise IdentityTokenError('IDENTITY_TOKEN_MALFORMED')
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise IdentityTokenError('IDENTITY_TOKEN_MALFORMED') from exc
        if header.get('alg') not in ALGORITHMS:
            # Refuse "none", HS256 (key-confusion) and anything else outright.
            raise IdentityTokenError('IDENTITY_TOKEN_ALGORITHM')
        try:
            signing_key = self._keys.get_signing_key_from_jwt(token)
        except jwt.PyJWKClientConnectionError as exc:
            raise IdentityKeysUnavailable('WorkOS signing keys are unavailable.') from exc
        except jwt.PyJWKClientError as exc:
            raise IdentityTokenError('IDENTITY_TOKEN_KEY_UNKNOWN') from exc
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                signing_key.key,
                algorithms=ALGORITHMS,
                issuer=self._issuer,
                leeway=self._leeway,
                options={'require': ['exp', 'iat', 'sub', 'sid'], 'verify_aud': False},
            )
        except jwt.ExpiredSignatureError as exc:
            raise IdentityTokenError('IDENTITY_TOKEN_EXPIRED') from exc
        except jwt.InvalidIssuerError as exc:
            raise IdentityTokenError('IDENTITY_TOKEN_ISSUER') from exc
        except jwt.PyJWTError as exc:
            raise IdentityTokenError('IDENTITY_TOKEN_INVALID') from exc

        audience = claims.get('aud')
        if audience is not None:
            audiences = audience if isinstance(audience, list) else [audience]
            if self._client_id not in audiences:
                raise IdentityTokenError('IDENTITY_TOKEN_AUDIENCE')

        user_id = str(claims.get('sub') or '')
        session_id = str(claims.get('sid') or '')
        org_id = claims.get('org_id')
        if not _USER_ID.match(user_id) or not _SESSION_ID.match(session_id):
            raise IdentityTokenError('IDENTITY_TOKEN_CLAIMS')
        if org_id is not None and not _ORG_ID.match(str(org_id)):
            raise IdentityTokenError('IDENTITY_TOKEN_CLAIMS')
        auth_time = claims.get('auth_time')
        return IdentityClaims(
            user_id=user_id,
            session_id=session_id,
            organization_id=str(org_id) if org_id else None,
            issued_at=int(claims['iat']),
            expires_at=int(claims['exp']),
            auth_time=int(auth_time) if isinstance(auth_time, int | float) else None,
            role=str(claims['role']) if isinstance(claims.get('role'), str) else None,
        )
