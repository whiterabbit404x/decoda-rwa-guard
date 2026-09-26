"""Read-only access to the Decoda platform's product contract (RWA Guard).

The platform database (owned by the Decoda website) is the source of truth for
who belongs to which organization and which products each organization may
use. Guard reads ONLY the versioned ``platform_api`` views, through a role that
can do nothing else, and never writes to the platform.

Every decision fails closed:
  * no membership row / not entitled / suspended / expired → access denied;
  * the WorkOS session appears on the revocation list → session ended;
  * the platform cannot be queried → the request is refused (503), never
    waved through.

Grants — and only grants — are cached for a few seconds so that every Guard
request can be re-checked without a platform round trip each time. A denial or
revocation is never cached, and the cache is bounded.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

PRODUCT = 'rwa_guard'
NO_MEMBERSHIP = 'no_membership'
CACHE_MAX_ENTRIES = 10_000
STATEMENT_TIMEOUT = '3s'

# Shown to a signed-in person who may not use Guard. They name the situation
# without exposing internal identifiers.
DENIAL_MESSAGES: dict[str, str] = {
    NO_MEMBERSHIP: 'Your Decoda account is not a member of this organization.',
    'user_inactive': 'Your Decoda account is not active. Contact your organization administrator.',
    'organization_inactive': 'This organization is not active on Decoda.',
    'membership_inactive': 'Your membership in this organization is not active.',
    'product_unavailable': 'Decoda RWA Guard is not available yet.',
    'not_entitled': 'Decoda RWA Guard is not enabled for your organization.',
    'entitlement_suspended': "Your organization's RWA Guard access is suspended.",
    'entitlement_not_started': "Your organization's RWA Guard access has not started yet.",
    'entitlement_expired': "Your organization's RWA Guard access has expired.",
}


class PlatformUnavailable(Exception):
    """The platform could not be queried: callers refuse the request (503)."""


@dataclass(frozen=True)
class ProductAccess:
    access_state: str
    session_revoked: bool = False
    platform_user_id: str | None = None
    platform_organization_id: str | None = None
    workos_organization_id: str | None = None
    organization_name: str | None = None
    organization_slug: str | None = None
    organization_role: str | None = None
    user_email: str | None = None
    user_name: str | None = None
    entitlement_status: str | None = None
    entitlement_plan: str | None = None
    entitlement_expires_at: datetime | None = None

    @property
    def granted(self) -> bool:
        return self.access_state == 'granted' and not self.session_revoked

    @property
    def denial_message(self) -> str:
        return DENIAL_MESSAGES.get(self.access_state, DENIAL_MESSAGES['not_entitled'])


def _row_to_access(row: dict[str, Any] | None) -> ProductAccess:
    if row is None or row.get('access_state') is None:
        return ProductAccess(access_state=NO_MEMBERSHIP, session_revoked=bool(row and row.get('session_revoked')))
    return ProductAccess(
        access_state=str(row['access_state']),
        session_revoked=bool(row['session_revoked']),
        platform_user_id=str(row['platform_user_id']),
        platform_organization_id=str(row['platform_organization_id']),
        workos_organization_id=row['workos_organization_id'],
        organization_name=row['organization_name'],
        organization_slug=row['organization_slug'],
        organization_role=row['organization_role'],
        user_email=row['user_email'],
        user_name=row['user_name'],
        entitlement_status=row['entitlement_status'],
        entitlement_plan=row['entitlement_plan'],
        entitlement_expires_at=row['entitlement_expires_at'],
    )


_ACCESS_SQL = """
    SELECT a.*,
           EXISTS (SELECT 1 FROM platform_api.session_revocations_v1 r WHERE r.workos_session_id = %(sid)s) AS session_revoked
    FROM (SELECT 1) AS one
    LEFT JOIN platform_api.product_access_v1 a
           ON a.workos_user_id = %(uid)s AND a.workos_organization_id = %(oid)s AND a.product = %(product)s
"""


class PlatformDirectory:
    def __init__(
        self,
        database_url: str,
        *,
        cache_ttl_seconds: int = 15,
        clock: Callable[[], float] = time.monotonic,
        connect_timeout_seconds: int = 5,
    ):
        self._database_url = database_url
        self._connect_timeout = connect_timeout_seconds
        self._ttl = max(0, int(cache_ttl_seconds))
        self._clock = clock
        self._cache: OrderedDict[tuple[str, str, str], tuple[float, ProductAccess]] = OrderedDict()
        self._cache_lock = threading.Lock()

    def _query(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        # Guard opens a connection per unit of work (no pool); the platform
        # reader follows the same model, bounded by short timeouts.
        try:
            import psycopg
            from psycopg.rows import dict_row

            with psycopg.connect(self._database_url, connect_timeout=self._connect_timeout, row_factory=dict_row) as conn:
                with conn.transaction():
                    conn.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'")
                    conn.execute('SET LOCAL transaction_read_only = on')
                    return list(conn.execute(sql, params).fetchall())
        except Exception as exc:  # connection, timeout, permission, anything: fail closed
            raise PlatformUnavailable('The Decoda platform could not be queried.') from exc

    def product_access(
        self, *, workos_user_id: str, workos_organization_id: str, workos_session_id: str, use_cache: bool = True
    ) -> ProductAccess:
        key = (workos_user_id, workos_organization_id, workos_session_id)
        if use_cache and self._ttl:
            with self._cache_lock:
                hit = self._cache.get(key)
                if hit and hit[0] > self._clock():
                    return hit[1]
                if hit:
                    del self._cache[key]
        rows = self._query(
            _ACCESS_SQL, {'uid': workos_user_id, 'oid': workos_organization_id, 'sid': workos_session_id, 'product': PRODUCT}
        )
        access = _row_to_access(rows[0] if rows else None)
        with self._cache_lock:
            if access.granted and self._ttl:
                self._cache[key] = (self._clock() + self._ttl, access)
                self._cache.move_to_end(key)
                while len(self._cache) > CACHE_MAX_ENTRIES:
                    self._cache.popitem(last=False)
            else:
                self._cache.pop(key, None)
        return access

    def invalidate(self, *, workos_session_id: str | None = None) -> None:
        with self._cache_lock:
            if workos_session_id is None:
                self._cache.clear()
                return
            for key in [k for k in self._cache if k[2] == workos_session_id]:
                del self._cache[key]

    def user_organizations(self, workos_user_id: str) -> list[dict[str, Any]]:
        """Organizations the user may switch into, with their RWA Guard access state."""
        return self._query(
            """
            SELECT o.workos_organization_id, o.platform_organization_id, o.organization_name, o.organization_slug,
                   o.organization_role, COALESCE(a.access_state, 'not_entitled') AS product_access_state
            FROM platform_api.user_organizations_v1 o
            LEFT JOIN platform_api.product_access_v1 a
                   ON a.workos_user_id = o.workos_user_id
                  AND a.workos_organization_id = o.workos_organization_id
                  AND a.product = %(product)s
            WHERE o.workos_user_id = %(uid)s
            ORDER BY lower(o.organization_name), o.workos_organization_id
            """,
            {'uid': workos_user_id, 'product': PRODUCT},
        )

    def products_for(self, workos_user_id: str, workos_organization_id: str) -> list[dict[str, Any]]:
        """Every Decoda product and this organization's access state for it (product switcher)."""
        return self._query(
            """
            SELECT product, product_name, product_availability, access_state, entitlement_status
            FROM platform_api.product_access_v1
            WHERE workos_user_id = %(uid)s AND workos_organization_id = %(oid)s
            ORDER BY product
            """,
            {'uid': workos_user_id, 'oid': workos_organization_id},
        )

    def legacy_organization(self, platform_organization_id: str) -> str | None:
        """The legacy RWA Guard organization a reviewed import mapped this platform organization to."""
        rows = self._query(
            """
            SELECT legacy_organization_id
            FROM platform_api.legacy_organization_links_v1
            WHERE product = %(product)s AND platform_organization_id = %(pid)s
            """,
            {'pid': platform_organization_id, 'product': PRODUCT},
        )
        return str(rows[0]['legacy_organization_id']) if rows else None

    def legacy_link(self, workos_user_id: str) -> dict[str, Any] | None:
        """Reviewed legacy-account link for RWA Guard (created from an operator-approved manifest)."""
        rows = self._query(
            """
            SELECT legacy_user_id, workos_user_id, workos_organization_id, platform_organization_id
            FROM platform_api.legacy_identity_links_v1
            WHERE product = %(product)s AND workos_user_id = %(uid)s
            """,
            {'uid': workos_user_id, 'product': PRODUCT},
        )
        return rows[0] if rows else None
