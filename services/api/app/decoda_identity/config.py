"""Shared Decoda identity for RWA Guard — configuration and mode gates.

GUARD_IDENTITY_MODE
  legacy  Guard's own sign-in (passwords, TOTP, workspace OIDC). The default,
          so an existing deployment behaves exactly as before until an
          operator opts in.
  dual    Decoda sign-in (WorkOS AuthKit) is the primary path. A Guard
          password still works ONLY for an account that is not yet linked to
          a Decoda identity, and only until GUARD_LEGACY_PASSWORD_SUNSET.
          No new password accounts are created (sign-up and invitation
          sign-up are gone); new people arrive through Decoda invitations.
  workos  Decoda sign-in only. Every legacy sign-in path answers 410 and
          sessions created by them are refused.

Fail closed: in `dual` / `workos` mode a missing WorkOS or platform setting is
a startup error, never a silent fallback to passwords. Production and staging
additionally require the MFA attestation, the issuer, the BFF secret and (in
`dual`) a sunset date.

Settings are read from the environment on every call (a few getenv calls), so
a test or an operator change never meets a stale cache.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import HTTPException, status

MODES = ('legacy', 'dual', 'workos')
DEFAULT_WEBSITE_URL = 'https://www.decodasecurity.com'


def _env(name: str) -> str:
    return (os.getenv(name) or '').strip().strip('"').strip("'").strip()


def _env_bool(name: str) -> bool:
    return _env(name).lower() in {'1', 'true', 'yes', 'on'}


def production_like() -> bool:
    return (os.getenv('APP_ENV') or os.getenv('APP_MODE') or 'development').strip().lower() in {'production', 'prod', 'staging'}


def _parse_sunset(raw: str) -> datetime | None:
    if not raw:
        return None
    value = datetime.fromisoformat(raw.replace('Z', '+00:00'))
    return value if value.tzinfo else value.replace(tzinfo=UTC)


@dataclass(frozen=True)
class IdentitySettings:
    mode: str
    legacy_password_sunset: datetime | None
    workos_client_id: str
    workos_api_key: str
    workos_issuer: tuple[str, ...]
    platform_database_url: str
    bff_shared_secret: str
    idp_mfa_required: bool
    access_cache_ttl_seconds: int
    website_url: str
    production: bool
    sunset_error: str | None = None

    @property
    def uses_workos(self) -> bool:
        return self.mode in ('dual', 'workos')

    def legacy_passwords_allowed(self, now: datetime | None = None) -> bool:
        if self.mode == 'legacy':
            return True
        if self.mode == 'workos' or self.sunset_error:
            return False
        return self.legacy_password_sunset is None or (now or datetime.now(UTC)) < self.legacy_password_sunset


def load_identity_settings() -> IdentitySettings:
    raw_mode = _env('GUARD_IDENTITY_MODE').lower() or 'legacy'
    sunset_error = None
    try:
        sunset = _parse_sunset(_env('GUARD_LEGACY_PASSWORD_SUNSET'))
    except ValueError:
        sunset, sunset_error = None, 'GUARD_LEGACY_PASSWORD_SUNSET must be an ISO-8601 date or timestamp.'
    try:
        ttl = min(60, max(0, int(_env('DECODA_ACCESS_CACHE_TTL_SECONDS') or 15)))
    except ValueError:
        ttl = 15
    return IdentitySettings(
        mode=raw_mode,
        legacy_password_sunset=sunset,
        workos_client_id=_env('WORKOS_CLIENT_ID'),
        workos_api_key=_env('WORKOS_API_KEY'),
        workos_issuer=tuple(part.strip() for part in _env('WORKOS_ISSUER').split(',') if part.strip()),
        platform_database_url=_env('DECODA_PLATFORM_DATABASE_URL'),
        bff_shared_secret=_env('GUARD_BFF_SHARED_SECRET'),
        idp_mfa_required=_env_bool('DECODA_IDP_MFA_REQUIRED'),
        access_cache_ttl_seconds=ttl,
        website_url=(_env('DECODA_WEBSITE_URL') or DEFAULT_WEBSITE_URL).rstrip('/'),
        production=production_like(),
        sunset_error=sunset_error,
    )


def configuration_errors(settings: IdentitySettings | None = None) -> list[str]:
    """Startup errors for the shared identity. Empty in `legacy` mode."""
    s = settings or load_identity_settings()
    if s.mode not in MODES:
        return [f'GUARD_IDENTITY_MODE must be one of {", ".join(MODES)}.']
    if not s.uses_workos:
        return []
    errors: list[str] = []
    required = {
        'WORKOS_CLIENT_ID': s.workos_client_id,
        'WORKOS_API_KEY': s.workos_api_key,
        'DECODA_PLATFORM_DATABASE_URL': s.platform_database_url,
    }
    if s.production:
        required['WORKOS_ISSUER'] = ','.join(s.workos_issuer)
        required['GUARD_BFF_SHARED_SECRET'] = s.bff_shared_secret
    missing = [name for name, value in required.items() if not value]
    if missing:
        errors.append(f'Shared Decoda identity is not configured: {", ".join(missing)} (required when GUARD_IDENTITY_MODE={s.mode}).')
    if s.production and s.bff_shared_secret and len(s.bff_shared_secret) < 32:
        errors.append('GUARD_BFF_SHARED_SECRET must be at least 32 characters.')
    if s.production and not s.idp_mfa_required:
        errors.append(
            'DECODA_IDP_MFA_REQUIRED=true is required in staging/production: confirm the WorkOS environment enforces MFA for every non-SSO sign-in.'
        )
    if s.sunset_error:
        errors.append(s.sunset_error)
    elif s.mode == 'dual' and s.production and s.legacy_password_sunset is None:
        errors.append('GUARD_LEGACY_PASSWORD_SUNSET is required in dual mode: legacy passwords need an end date.')
    return errors


# ── Mode gates for the legacy (Guard-local) identity paths ──────────────────


def _gone(code: str, message: str, **extra: object) -> HTTPException:
    return HTTPException(status_code=status.HTTP_410_GONE, detail={'code': code, 'message': message, **extra})


def require_legacy_password_sign_in(auth_provider: str | None = None) -> None:
    """Password sign-in: always in `legacy`; in `dual` only for unlinked accounts before the sunset."""
    s = load_identity_settings()
    if s.mode == 'legacy':
        return
    if not s.legacy_passwords_allowed():
        raise _gone('LEGACY_AUTH_DISABLED', 'RWA Guard uses your Decoda account. Sign in with Decoda.')
    if auth_provider == 'workos':
        raise _gone('DECODA_ACCOUNT_LINKED', 'This account signs in with Decoda. Use "Sign in to Decoda".')


def require_new_password_accounts() -> None:
    """Creating a Guard password account (sign-up, invitation sign-up): `legacy` only."""
    s = load_identity_settings()
    if s.mode != 'legacy':
        raise _gone(
            'SIGN_UP_MOVED',
            'Decoda accounts are created by invitation. Request pilot access on the Decoda website.',
            request_access_url=f'{s.website_url}/request-pilot?product=rwa_guard',
        )


def require_local_onboarding() -> None:
    """Guard's own public pilot-request intake: `legacy` only (the platform owns it otherwise)."""
    s = load_identity_settings()
    if s.mode != 'legacy':
        raise _gone(
            'PILOT_REQUESTS_MOVED',
            'Pilot access is requested on the Decoda website.',
            request_access_url=f'{s.website_url}/request-pilot?product=rwa_guard',
        )


def require_local_sign_in_paths() -> None:
    """Workspace OIDC and other Guard-issued sign-ins: not in `workos` mode (they would bypass the platform)."""
    s = load_identity_settings()
    if s.mode == 'workos' or (s.mode == 'dual' and not s.legacy_passwords_allowed()):
        raise _gone('LEGACY_AUTH_DISABLED', 'RWA Guard uses your Decoda account. Sign in with Decoda.')


def require_local_mfa(session_auth_mode: str | None) -> None:
    """Guard TOTP enrollment / step-up: not for Decoda sessions (MFA is enforced by Decoda)."""
    s = load_identity_settings()
    if s.mode == 'workos' or session_auth_mode == 'workos':
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                'code': 'DECODA_REAUTHENTICATION_REQUIRED',
                'message': 'Multi-factor authentication is managed by your Decoda account. Sign in again with Decoda to verify.',
            },
        )
