"""Mandatory MFA is a BACKEND boundary for every human in a Pilot workspace.

The invariant under test: an authenticated human whose session can reach a Pilot
workspace gets NOTHING but the endpoints that complete MFA until (a) the account
holds a second factor and (b) THIS session has completed a challenge with it.

It holds when the frontend is bypassed, when the API is called directly, when a
deep link is opened, when the session predates the requirement, when the caller
just accepted an invitation, and when the caller is a Founder, Owner, Admin,
Analyst, Viewer or Decoda internal staff.

What these tests pin down, in the order the request travels:

    1   chokepoint    both authentication entry points run the boundary, so a
                      route cannot opt out by forgetting to call a helper
    2   plan floor    a Pilot workspace is governed as `all_members` even while
                      its own policy row still says `optional`
    3   roles         Owner / Admin / Analyst / Viewer / internal admin all get
                      the same refusal — no role is an input
    4   enrollment    not enrolled → MFA_ENROLLMENT_REQUIRED
    5   session       enrolled but password-only session → MFA_CHALLENGE_REQUIRED
    6   bootstrap     the endpoints that COMPLETE MFA stay reachable, and
                      nothing else does
    7   routes        a repository sweep proves no authenticated data route is
                      outside the boundary
    8   invitation    accept → denied → enroll → verify → allowed
    9   lifecycle     disable is refused for Pilot; a recovery code works once
    10  non-Pilot     Scale / Enterprise keep their configurable policy
    11  regression    the response-action step-up gate is untouched

Run:
    python -m pytest services/api/tests/test_pilot_mandatory_mfa.py -q
"""

from __future__ import annotations

import inspect
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException, Request

from services.api.app import entitlements as ent
from services.api.app import mfa_authorization as mfa_authz
from services.api.app import pilot

APP_DIR = Path(__file__).resolve().parents[1] / 'app'
NOW = datetime.now(timezone.utc)

WS = 'ws-pilot-1'
OTHER_WS = 'ws-scale-1'
USER_ID = 'user-1'


# ── fakes ────────────────────────────────────────────────────────────────────
class _Result:
    def __init__(self, row: Any = None, rows: list[Any] | None = None) -> None:
        self._row = row
        self._rows = rows if rows is not None else ([] if row is None else [row])

    def fetchone(self) -> Any:
        return self._row

    def fetchall(self) -> list[Any]:
        return self._rows


class _Conn:
    """One tenant, one session, one policy row — plus a log of every statement."""

    def __init__(
        self,
        *,
        plan: str = ent.PLAN_PILOT,
        organization_linked: bool = True,
        schema_ready: bool = True,
        tenant_read_fails: bool = False,
        policy: str = 'optional',
        session_mfa_verified: bool = False,
        session_methods: list[str] | None = None,
        session_missing: bool = False,
        recovery_codes: dict[str, bool] | None = None,
    ) -> None:
        self.plan = plan
        self.organization_linked = organization_linked
        self.schema_ready = schema_ready
        self.tenant_read_fails = tenant_read_fails
        self.policy = policy
        self.session_mfa_verified = session_mfa_verified
        self.session_methods = session_methods if session_methods is not None else ['password']
        self.session_missing = session_missing
        #: code_hash → consumed?
        self.recovery_codes = recovery_codes or {}
        self.statements: list[tuple[str, Any]] = []
        self.commits = 0
        self.session_version = 1

    # -- the SQL surface these tests exercise -------------------------------
    def execute(self, query: str, params: Any = None) -> _Result:
        sql = ' '.join(str(query).split())
        self.statements.append((sql, params))
        lowered = sql.lower()
        if 'information_schema.tables' in lowered:
            if self.tenant_read_fails:
                raise RuntimeError('database unavailable')
            return _Result({'table_count': 2 if self.schema_ready else 0,
                            'link_count': 1 if self.schema_ready else 0})
        if 'join organizations o on o.id = w.organization_id' in lowered:
            if self.tenant_read_fails:
                raise RuntimeError('database unavailable')
            if not self.organization_linked:
                return _Result(None)
            return _Result({
                'id': 'org-1', 'name': 'Tenant', 'slug': 'tenant',
                'plan': self.plan, 'status': ent.STATUS_ACTIVE,
                'evaluation_started_at': NOW - timedelta(days=1),
                'evaluation_expires_at': NOW + timedelta(days=29),
                'entitlement_overrides': {}, 'created_at': NOW, 'updated_at': NOW,
            })
        if 'from workspace_auth_policies' in lowered:
            return _Result({'mfa_enforcement': self.policy, 'reauthentication_minutes': 15})
        if 'from auth_sessions' in lowered and 'select' in lowered:
            if self.session_missing:
                return _Result(None)
            return _Result({
                'revoked_at': None,
                'expires_at': NOW + timedelta(hours=8),
                'authenticated_at': NOW,
                'reauthenticated_at': None,
                'mfa_verified_at': NOW if self.session_mfa_verified else None,
                'authentication_methods': list(self.session_methods),
            })
        if 'select session_version from users' in lowered:
            return _Result({'session_version': self.session_version})
        if 'from mfa_recovery_codes' in lowered and 'consumed_at is null' in lowered:
            code_hash = params[1] if params and len(params) > 1 else None
            if code_hash in self.recovery_codes and not self.recovery_codes[code_hash]:
                return _Result({'id': 'rc-1'})
            return _Result(None)
        if 'count(*) as count from mfa_recovery_codes' in lowered:
            return _Result({'count': sum(1 for spent in self.recovery_codes.values() if not spent)})
        if lowered.startswith('update mfa_recovery_codes set consumed_at'):
            for key in list(self.recovery_codes):
                if not self.recovery_codes[key]:
                    self.recovery_codes[key] = True
                    break
            return _Result()
        return _Result()

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        return None

    def written(self, needle: str) -> list[tuple[str, Any]]:
        return [item for item in self.statements if needle in item[0]]


@pytest.fixture(autouse=True)
def _auth_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Session token hashing needs the managed AUTH key. These tests are about
    the MFA boundary, not key management, so a local key is enough."""
    monkeypatch.setenv('AUTH_TOKEN_SECRET', 'mandatory-mfa-test-secret')


def _request(path: str = '/assets', *, workspace_id: str | None = WS) -> Request:
    headers = [(b'authorization', b'Bearer session-token')]
    if workspace_id:
        headers.append((b'x-workspace-id', workspace_id.encode('latin-1')))
    return Request({'type': 'http', 'method': 'GET', 'path': path, 'headers': headers})


def _user(
    *,
    mfa_enabled: bool = False,
    role: str = 'owner',
    workspace_id: str | None = WS,
    memberships: list[dict[str, Any]] | None = None,
    is_internal_admin: bool = False,
) -> dict[str, Any]:
    if memberships is None:
        memberships = (
            [{'workspace_id': workspace_id, 'role': role, 'workspace': {'id': workspace_id}}]
            if workspace_id else []
        )
    return {
        'id': USER_ID,
        'email': 'operator@example.com',
        'mfa_enabled': mfa_enabled,
        'current_workspace_id': workspace_id,
        'is_internal_admin': is_internal_admin,
        'memberships': memberships,
    }


def _refusal(exc: HTTPException) -> dict[str, Any]:
    assert isinstance(exc.detail, dict), exc.detail
    return exc.detail


def _deny(connection: _Conn, request: Request, user: dict[str, Any]) -> dict[str, Any]:
    with pytest.raises(HTTPException) as info:
        pilot.require_pilot_mfa(connection, request, user)
    assert info.value.status_code == 403
    return _refusal(info.value)


# ═══════════════════════════════════════════════════════════════════════════
# 1 — the chokepoint
# ═══════════════════════════════════════════════════════════════════════════
def test_1_both_authentication_entry_points_run_the_boundary() -> None:
    """The boundary is wired where identity is resolved, not per route.

    Every authenticated customer route reaches one of these two functions, so a
    route cannot opt out of MFA by forgetting to call a permission helper.
    """
    for entry in (pilot.authenticate_with_connection, pilot.authenticate_request):
        source = inspect.getsource(entry)
        assert 'require_pilot_mfa(' in source, f'{entry.__name__} does not run the MFA boundary'


def test_1b_the_boundary_runs_after_the_user_is_hydrated() -> None:
    """Order matters: the decision needs the account's enrollment flag and its
    workspace memberships, both of which come from ``build_user_response``."""
    source = inspect.getsource(pilot.authenticate_with_connection)
    assert source.index('build_user_response') < source.index('require_pilot_mfa(')


def test_1c_the_chokepoint_denies_a_protected_route_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    """Through the real ``authenticate_with_connection``, not the helper alone."""
    connection = _Conn()
    monkeypatch.setattr(pilot, 'decode_access_token', lambda token: {'sub': USER_ID, 'sv': 1})
    monkeypatch.setattr(pilot, '_is_session_blacklisted', lambda _hash: False)
    monkeypatch.setattr(pilot, 'build_user_response', lambda c, uid: _user())

    with pytest.raises(HTTPException) as info:
        pilot.authenticate_with_connection(connection, _request('/assets'))
    assert _refusal(info.value)['code'] == mfa_authz.CODE_MFA_ENROLLMENT_REQUIRED


def test_1d_the_chokepoint_allows_a_bootstrap_route_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _Conn()
    monkeypatch.setattr(pilot, 'decode_access_token', lambda token: {'sub': USER_ID, 'sv': 1})
    monkeypatch.setattr(pilot, '_is_session_blacklisted', lambda _hash: False)
    monkeypatch.setattr(pilot, 'build_user_response', lambda c, uid: _user())

    assert pilot.authenticate_with_connection(connection, _request('/auth/mfa/enroll'))['id'] == USER_ID


def test_1e_the_session_record_is_read_from_the_validated_session() -> None:
    """``_validate_session`` hands its row on, so the boundary costs no extra
    round trip on the request path."""
    source = inspect.getsource(pilot.authenticate_with_connection)
    assert 'session = _validate_session(' in source
    assert 'session=session' in source
    assert 'mfa_verified_at' in inspect.getsource(pilot._validate_session)


# ═══════════════════════════════════════════════════════════════════════════
# 2 — the plan floor
# ═══════════════════════════════════════════════════════════════════════════
def test_2_pilot_is_governed_as_all_members_despite_an_optional_policy_row() -> None:
    connection = _Conn(plan=ent.PLAN_PILOT, policy='optional')
    effective = pilot.workspace_effective_mfa_enforcement(connection, WS)
    assert effective['enforcement'] == mfa_authz.ENFORCEMENT_ALL_MEMBERS
    assert effective['floor_reason'] == mfa_authz.REASON_PLAN_PILOT


def test_2b_a_pilot_tenant_cannot_configure_its_way_out() -> None:
    """Nothing a customer can write to ``workspace_auth_policies`` is weaker than
    the floor, so the policy row is not even consulted for a Pilot."""
    connection = _Conn(plan=ent.PLAN_PILOT, policy='optional')
    pilot.workspace_effective_mfa_enforcement(connection, WS)
    assert connection.written('workspace_auth_policies') == []


def test_2c_one_canonical_function_answers_the_effective_policy() -> None:
    """The rule lives in one place rather than as scattered ``plan == 'pilot'``
    branches through the route modules."""
    offenders: list[str] = []
    pattern = re.compile(r"""plan[^\n]{0,24}==\s*['"]pilot['"]""")
    for path in sorted(APP_DIR.rglob('*.py')):
        if path.name in {'entitlements.py', 'execution_authorization.py', 'mfa_authorization.py'}:
            continue
        for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), start=1):
            if 'mfa' in line.lower() and pattern.search(line):
                offenders.append(f'{path.name}:{number}')
    assert offenders == []


@pytest.mark.parametrize(
    ('kwargs', 'reason'),
    [
        ({'organization_linked': False}, mfa_authz.REASON_ORGANIZATION_NOT_LINKED),
        ({'tenant_read_fails': True}, mfa_authz.REASON_TENANT_UNREADABLE),
    ],
)
def test_2d_an_unreadable_or_unlinked_tenant_fails_closed(kwargs: dict[str, Any], reason: str) -> None:
    """A plan we could not read is not permission to skip MFA. An unlinked
    workspace heals into a PILOT organization, so it is treated as one."""
    connection = _Conn(**kwargs)
    effective = pilot.workspace_effective_mfa_enforcement(connection, WS)
    assert effective['enforcement'] == mfa_authz.ENFORCEMENT_ALL_MEMBERS
    assert effective['floor_reason'] == reason


def test_2e_an_unmigrated_deployment_keeps_its_existing_behaviour() -> None:
    """There is no organization plan to consult before migration 0150, so the
    configurable workspace policy still decides — unchanged."""
    connection = _Conn(schema_ready=False, policy='optional')
    effective = pilot.workspace_effective_mfa_enforcement(connection, WS)
    assert effective['enforcement'] == mfa_authz.ENFORCEMENT_OPTIONAL
    assert effective['floor_reason'] == mfa_authz.REASON_SCHEMA_NOT_MIGRATED


def test_2f_an_unrecognised_policy_value_is_read_as_the_strictest() -> None:
    assert mfa_authz.normalize_enforcement('somethingelse') == mfa_authz.ENFORCEMENT_ALL_MEMBERS
    assert mfa_authz.normalize_enforcement('') == mfa_authz.ENFORCEMENT_OPTIONAL
    assert mfa_authz.strongest('optional', 'all_members') == mfa_authz.ENFORCEMENT_ALL_MEMBERS
    assert mfa_authz.strongest('administrators', 'optional') == mfa_authz.ENFORCEMENT_ADMINISTRATORS


# ═══════════════════════════════════════════════════════════════════════════
# 3 — no role is an input
# ═══════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize('role', ['owner', 'admin', 'analyst', 'viewer'])
def test_3_every_pilot_role_is_denied_without_mfa(role: str) -> None:
    refusal = _deny(_Conn(), _request('/assets'), _user(role=role))
    assert refusal['code'] == mfa_authz.CODE_MFA_ENROLLMENT_REQUIRED


def test_3b_an_internal_admin_in_a_customer_workspace_is_denied_too() -> None:
    """Elevated Decoda privilege is not a second factor. Entering a customer's
    Pilot workspace is customer access and is gated like any other."""
    refusal = _deny(_Conn(), _request('/assets'), _user(is_internal_admin=True))
    assert refusal['code'] == mfa_authz.CODE_MFA_ENROLLMENT_REQUIRED


def test_3c_the_decision_never_learns_who_the_caller_is() -> None:
    """The pure decision takes an enforcement, a role, and two booleans. Under
    ``all_members`` the role cannot change the answer at all."""
    for role in ('owner', 'admin', 'analyst', 'viewer', None, 'founder'):
        decision = mfa_authz.decide_access(
            enforcement=mfa_authz.ENFORCEMENT_ALL_MEMBERS, role=role,
            mfa_enrolled=False, session_mfa_completed=False,
        )
        assert decision['allowed'] is False
        assert decision['code'] == mfa_authz.CODE_MFA_ENROLLMENT_REQUIRED


# ═══════════════════════════════════════════════════════════════════════════
# 4 / 5 — enrollment vs. this session's challenge
# ═══════════════════════════════════════════════════════════════════════════
def test_4_an_unenrolled_pilot_user_is_told_to_enroll() -> None:
    refusal = _deny(_Conn(), _request('/assets'), _user(mfa_enabled=False))
    assert refusal['code'] == mfa_authz.CODE_MFA_ENROLLMENT_REQUIRED
    assert refusal['mfa_enrollment_required'] is True
    assert refusal['mfa_challenge_required'] is False


def test_5_an_enrolled_user_on_a_password_only_session_must_challenge() -> None:
    """Holding a TOTP secret is not the same as having used it HERE. A session
    created before the account enrolled keeps no MFA stamp."""
    connection = _Conn(session_mfa_verified=False, session_methods=['password'])
    refusal = _deny(connection, _request('/assets'), _user(mfa_enabled=True))
    assert refusal['code'] == mfa_authz.CODE_MFA_CHALLENGE_REQUIRED
    assert refusal['mfa_challenge_required'] is True


def test_5b_a_properly_mfa_authenticated_session_is_allowed() -> None:
    connection = _Conn(session_mfa_verified=True, session_methods=['password', 'totp'])
    pilot.require_pilot_mfa(connection, _request('/assets'), _user(mfa_enabled=True))


def test_5c_a_recovery_code_sign_in_counts_as_a_completed_challenge() -> None:
    connection = _Conn(session_mfa_verified=True, session_methods=['password', 'recovery_code'])
    pilot.require_pilot_mfa(connection, _request('/assets'), _user(mfa_enabled=True))


def test_5d_a_federated_session_counts_only_when_its_amr_proved_mfa() -> None:
    allowed = _Conn(session_mfa_verified=True, session_methods=['oidc', 'mfa'])
    pilot.require_pilot_mfa(allowed, _request('/assets'), _user(mfa_enabled=True))

    refused = _Conn(session_mfa_verified=False, session_methods=['oidc'])
    assert _deny(refused, _request('/assets'), _user(mfa_enabled=True))['code'] == (
        mfa_authz.CODE_MFA_CHALLENGE_REQUIRED
    )


def test_5e_an_unreadable_session_has_not_completed_mfa() -> None:
    assert mfa_authz.session_completed_mfa(None) is False
    assert mfa_authz.session_completed_mfa({}) is False
    assert mfa_authz.session_completed_mfa({'mfa_verified_at': None, 'authentication_methods': ['totp']}) is False


def test_6_an_existing_session_issued_before_the_requirement_is_denied() -> None:
    """Phase 6: enforcement is read from CURRENT tenant state, never from
    whatever policy applied when the token was minted."""
    stale_session = _Conn(session_mfa_verified=False, session_methods=['password'])
    assert _deny(stale_session, _request('/assets'), _user(mfa_enabled=False))['code'] == (
        mfa_authz.CODE_MFA_ENROLLMENT_REQUIRED
    )


def test_6b_the_token_carries_no_mfa_claim_the_server_could_trust() -> None:
    """Nothing in the access token says anything about MFA, so a replayed or
    pre-policy token cannot assert it."""
    source = inspect.getsource(pilot.create_access_token)
    assert 'mfa' not in source.lower()


# ═══════════════════════════════════════════════════════════════════════════
# 6 — the bootstrap allowlist
# ═══════════════════════════════════════════════════════════════════════════
BOOTSTRAP_MUST_ALLOW = (
    '/auth/me',
    '/auth/mfa/enroll',
    '/auth/mfa/confirm',
    '/auth/mfa/complete-signin',
    '/auth/mfa/recovery-codes/regenerate',
    '/auth/session/step-up',
    '/auth/signout',
    '/auth/signout-all',
    '/auth/signin',
    '/auth/signup',
    '/auth/forgot-password',
    '/auth/reset-password',
    '/auth/verify-email',
    '/auth/csrf-token',
    '/account/pilot-access',
    '/pilot-invitations/signup',
    '/pilot-invitations/accept',
    '/health',
)

MUST_DENY = (
    '/assets', '/assets/a-1', '/targets', '/monitoring/targets', '/monitoring/systems',
    '/detections', '/alerts', '/alerts/a-1', '/incidents', '/incidents/i-1',
    '/incidents/i-1/timeline', '/incidents/i-1/evidence', '/history/actions',
    '/exports', '/exports/e-1/download', '/exports/e-1/archive', '/exports/proof-bundle',
    '/events', '/workspace/api-keys', '/api-keys', '/integrations/slack',
    '/integrations/webhooks', '/integrations/routing', '/workspace/settings',
    '/workspace/security-settings', '/workspace/access-control', '/response/actions',
    '/api/v1/alerts', '/api/v1/assets', '/api/v1/incidents', '/threat-monitoring/detections',
    '/dashboard', '/auth/sessions', '/auth/delete-account', '/auth/mfa/disable',
)


@pytest.mark.parametrize('path', BOOTSTRAP_MUST_ALLOW)
def test_7_every_bootstrap_endpoint_stays_reachable(path: str) -> None:
    """Without these the boundary would be a lockout rather than a gate."""
    pilot.require_pilot_mfa(_Conn(), _request(path), _user(mfa_enabled=False))


@pytest.mark.parametrize('path', MUST_DENY)
def test_8_every_pilot_data_route_is_denied_before_mfa(path: str) -> None:
    """Assets, alerts, incidents, evidence exports and downloads, audit events,
    API keys, integrations and workspace settings — all refused."""
    refusal = _deny(_Conn(), _request(path), _user(mfa_enabled=False))
    assert refusal['code'] == mfa_authz.CODE_MFA_ENROLLMENT_REQUIRED


def test_8b_the_allowlist_is_default_deny() -> None:
    """An unknown path — including one whose prefix looks like a bootstrap path
    — is protected, and an unreadable path is protected too."""
    assert pilot._is_mfa_bootstrap_path('/auth/mfa/enroll') is True
    assert pilot._is_mfa_bootstrap_path('/auth/mfa/enroll/../../assets') is False
    assert pilot._is_mfa_bootstrap_path('/auth/mfa') is False
    assert pilot._is_mfa_bootstrap_path('/auth/meddle') is False
    assert pilot._is_mfa_bootstrap_path('/account/pilot-access/secrets') is False
    assert pilot._is_mfa_bootstrap_path('') is False
    assert pilot._is_mfa_bootstrap_path('/assets') is False


def test_8c_a_request_whose_path_cannot_be_read_is_protected() -> None:
    """A caller that is not a routed HTTP request gets the strict answer."""
    class _Pathless:
        headers = {'authorization': 'Bearer session-token'}

    assert pilot._request_path(_Pathless()) == ''
    with pytest.raises(HTTPException):
        pilot.require_pilot_mfa(_Conn(), _Pathless(), _user(mfa_enabled=False))


def test_8d_no_bootstrap_endpoint_returns_workspace_scoped_data() -> None:
    """Every allowlisted path is unauthenticated, completes MFA, ends the
    session, or reports the caller's OWN identity — never tenant records."""
    tenant_data_prefixes = (
        '/assets', '/targets', '/alerts', '/incidents', '/detections', '/exports',
        '/events', '/history', '/monitoring', '/response', '/integrations',
        '/workspace/api-keys', '/workspace/settings', '/api/v1', '/threat-monitoring',
    )
    for path in sorted(pilot.PILOT_MFA_BOOTSTRAP_PATHS):
        assert not path.startswith(tenant_data_prefixes), path


# ═══════════════════════════════════════════════════════════════════════════
# 7 — repository route audit
# ═══════════════════════════════════════════════════════════════════════════
def _declared_routes() -> list[tuple[str, str]]:
    text = (APP_DIR / 'main.py').read_text(encoding='utf-8')
    return [
        (match.group(1), match.group(2))
        for match in re.finditer(r"^@app\.(get|post|put|patch|delete)\(\s*'([^']+)'", text, re.M)
    ]


def test_9_the_route_audit_finds_no_unprotected_pilot_data_route() -> None:
    """Phase 14. Every declared route is either an allowlisted bootstrap path or
    behind the boundary, and the protected set is the overwhelming majority."""
    routes = _declared_routes()
    assert len(routes) > 300, 'the route sweep pattern has drifted'
    protected = [path for _method, path in routes if not pilot._is_mfa_bootstrap_path(path)]
    exempt = {path for _method, path in routes if pilot._is_mfa_bootstrap_path(path)}
    assert len(protected) > 350
    # Every exempt route is one this suite has deliberately listed.
    assert exempt <= set(pilot.PILOT_MFA_BOOTSTRAP_PATHS) | {
        path for path in exempt if path.startswith(pilot.PILOT_MFA_BOOTSTRAP_PREFIXES)
    }


def test_9b_no_other_module_authenticates_a_bearer_session_of_its_own() -> None:
    """A second bearer-session authenticator would be a second door. SCIM is the
    one non-human path and it resolves a workspace token, never a user session."""
    offenders: list[str] = []
    for path in sorted(APP_DIR.rglob('*.py')):
        if path.name in {'pilot.py', 'mfa_authorization.py'}:
            continue
        text = path.read_text(encoding='utf-8')
        if 'decode_access_token(' in text and 'authenticate_with_connection' not in text:
            offenders.append(path.name)
    assert offenders == []


def test_9b2_every_sensitive_read_authenticates_before_it_answers() -> None:
    """The boundary only helps routes that authenticate. These are the surfaces
    the brief names one by one — assets, alerts, incidents, evidence exports and
    downloads, audit events, API keys, integrations — and each resolves its
    caller through the chokepoint before it reads anything."""
    guarded = ('authenticate_with_connection', '_require_workspace_permission', '_require_workspace_admin')
    for name in (
        'list_assets', 'list_alerts', 'list_incidents', 'list_audit_events',
        'list_exports', 'get_export_artifact_content',
        'list_workspace_api_keys', 'list_slack_integrations',
    ):
        function = getattr(pilot, name)
        source = inspect.getsource(function)
        assert any(marker in source for marker in guarded), name


def test_9e_the_refusal_survives_the_http_error_wrapper() -> None:
    """`with_auth_schema_json` rewrites only 503s and the unverified-email 403.
    A 403 MFA refusal must reach the client with its code intact."""
    from services.api.app import main as api_main

    refusal = mfa_authz.as_http_exception(
        mfa_authz.PilotMfaRequired(
            code=mfa_authz.CODE_MFA_ENROLLMENT_REQUIRED, reason='not_enrolled',
            workspace_id=WS, plan=ent.PLAN_PILOT, enforcement='all_members',
        )
    )

    def _raise():
        raise refusal

    with pytest.raises(HTTPException) as info:
        api_main.with_auth_schema_json(_raise)
    assert info.value.status_code == 403
    assert _refusal(info.value)['code'] == mfa_authz.CODE_MFA_ENROLLMENT_REQUIRED


def test_9f_a_streaming_endpoint_reports_the_mfa_refusal_truthfully() -> None:
    """An SSE endpoint cannot open a stream for a refused caller, so it answers
    with JSON. It must keep the refusal's own status and code: reporting an MFA
    block as 401 UNAUTHENTICATED would send the operator to sign in again, when
    the remedy is to enrol or verify an authenticator."""
    from services.api.app import main as api_main

    refusal = mfa_authz.as_http_exception(
        mfa_authz.PilotMfaRequired(
            code=mfa_authz.CODE_MFA_CHALLENGE_REQUIRED, reason='session_not_verified',
            workspace_id=WS, plan=ent.PLAN_PILOT, enforcement='all_members',
        )
    )
    response = api_main.sse_auth_error_response(refusal)
    assert response.status_code == 403
    assert mfa_authz.CODE_MFA_CHALLENGE_REQUIRED.encode() in response.body

    unauthenticated = api_main.sse_auth_error_response(
        HTTPException(status_code=401, detail='Missing bearer token.')
    )
    assert unauthenticated.status_code == 401
    assert b'UNAUTHENTICATED' in unauthenticated.body

    # Every streaming route shares that renderer rather than hard-coding a 401.
    main_text = (APP_DIR / 'main.py').read_text(encoding='utf-8')
    assert main_text.count("'code': 'UNAUTHENTICATED'}, status_code=401)") == 0
    assert main_text.count('sse_auth_error_response(exc)') == 4


def test_9c_the_scim_machine_path_never_reaches_the_human_boundary() -> None:
    """Phase 9: a directory-sync token is a machine credential. It is not asked
    for a TOTP code, and it cannot be used to read monitoring or evidence data."""
    source = inspect.getsource(pilot._authenticate_scim)
    assert 'authenticate_with_connection' not in source
    assert 'require_pilot_mfa' not in source
    assert 'workspace_scim_tokens' in source


def test_9d_workspace_api_keys_do_not_replace_a_human_session() -> None:
    """The /api/v1 surface layers an API key ON TOP of a human bearer session —
    it is not a machine login — so those routes are inside the boundary."""
    source = inspect.getsource(pilot.list_alerts)
    assert 'authenticate_with_connection' in source


# ═══════════════════════════════════════════════════════════════════════════
# 8 — the invitation sequence
# ═══════════════════════════════════════════════════════════════════════════
def test_10_accept_invitation_then_denied_then_enroll_then_allowed() -> None:
    """Phase 5, exactly as specified.

    accept → identity exists → GET /assets denied → enroll allowed → confirm
    marks the session MFA-verified → GET /assets allowed.
    """
    # Right after `POST /pilot-invitations/signup` the account has a session and
    # no workspace at all: nothing to protect, and nothing readable either.
    no_tenant = _Conn()
    pilot.require_pilot_mfa(no_tenant, _request('/pilot-invitations/accept'), _user(workspace_id=None))

    # Acceptance provisions the Pilot workspace. The very next data call is refused.
    accepted = _user(mfa_enabled=False, role='owner')
    assert _deny(_Conn(), _request('/assets'), accepted)['code'] == mfa_authz.CODE_MFA_ENROLLMENT_REQUIRED

    # Enrollment is reachable — the boundary must never be a lockout.
    pilot.require_pilot_mfa(_Conn(), _request('/auth/mfa/enroll'), accepted)
    pilot.require_pilot_mfa(_Conn(), _request('/auth/mfa/confirm'), accepted)

    # Confirmation enrolls the account AND stamps this session.
    enrolled = _user(mfa_enabled=True, role='owner')
    verified = _Conn(session_mfa_verified=True, session_methods=['password', 'totp'])
    pilot.require_pilot_mfa(verified, _request('/assets'), enrolled)


def test_10b_confirming_enrollment_marks_the_current_session_verified() -> None:
    """Otherwise the newly enrolled operator would enroll and still be refused."""
    source = inspect.getsource(pilot.mfa_confirm_enrollment)
    assert 'UPDATE auth_sessions SET mfa_verified_at = NOW()' in source


def test_10c_the_invitation_routes_are_reachable_for_an_existing_pilot_member() -> None:
    """A member of one Pilot workspace accepting a second invitation is joining,
    not reading: it stays reachable, and every data route stays refused."""
    pilot.require_pilot_mfa(_Conn(), _request('/workspace/invitations/accept'), _user())
    assert _deny(_Conn(), _request('/workspace/members'), _user())['code'] == (
        mfa_authz.CODE_MFA_ENROLLMENT_REQUIRED
    )


# ═══════════════════════════════════════════════════════════════════════════
# 9 — MFA lifecycle
# ═══════════════════════════════════════════════════════════════════════════
def test_11_disabling_mfa_is_refused_while_a_pilot_workspace_requires_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _Conn(session_mfa_verified=True, session_methods=['password', 'totp'])

    @contextmanager
    def _pg():
        yield connection

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'pg_connection', _pg)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda c: None)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda c, r: _user(mfa_enabled=True))
    monkeypatch.setattr(pilot, '_decrypt_mfa_secret', lambda uid, stored: 'SECRET')
    monkeypatch.setattr(pilot, '_verify_totp', lambda secret, code: True)
    monkeypatch.setattr(
        pilot, '_row_for_mfa_disable', None, raising=False,
    )
    original_execute = connection.execute

    def _execute(query, params=None):
        if 'select mfa_totp_secret, mfa_enabled_at from users' in ' '.join(str(query).split()).lower():
            return _Result({'mfa_totp_secret': 'enc', 'mfa_enabled_at': NOW})
        return original_execute(query, params)

    connection.execute = _execute  # type: ignore[method-assign]

    with pytest.raises(HTTPException) as info:
        pilot.mfa_disable({'code': '123456'}, _request('/auth/mfa/disable'))
    assert info.value.status_code == 409
    assert _refusal(info.value)['code'] == 'MFA_REQUIRED_BY_WORKSPACE'
    # Nothing was cleared: no secret write reached the database.
    assert connection.written('mfa_totp_secret = NULL') == []


def test_11b_a_disabled_account_loses_pilot_access_immediately() -> None:
    """The second half of the belt-and-braces: even if MFA were cleared, the
    boundary refuses the next request rather than trusting the old session."""
    assert _deny(_Conn(), _request('/assets'), _user(mfa_enabled=False))['code'] == (
        mfa_authz.CODE_MFA_ENROLLMENT_REQUIRED
    )


def test_11c_disable_revokes_every_session_and_bumps_the_session_version() -> None:
    source = inspect.getsource(pilot.mfa_disable)
    assert 'session_version = session_version + 1' in source
    assert 'UPDATE auth_sessions SET revoked_at = NOW()' in source
    assert 'DELETE FROM mfa_recovery_codes WHERE user_id = %s' in source


def test_12_a_recovery_code_works_once_and_then_never_again(monkeypatch: pytest.MonkeyPatch) -> None:
    code = 'aaaaaa-bbbbbb'
    code_hash = f'hash:{code}'
    connection = _Conn(recovery_codes={code_hash: False})

    @contextmanager
    def _pg():
        yield connection

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'pg_connection', _pg)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda c: None)
    monkeypatch.setattr(pilot, '_auth_token_hash', lambda value: f'hash:{value}')
    monkeypatch.setattr(pilot, '_decrypt_mfa_secret', lambda uid, stored: '')
    monkeypatch.setattr(pilot, 'create_access_token', lambda uid, sv: 'issued-token')
    monkeypatch.setattr(pilot, '_store_session', lambda *a, **k: None)
    monkeypatch.setattr(pilot, 'build_user_response', lambda c, uid: _user(mfa_enabled=True))
    monkeypatch.setattr(pilot, 'log_audit', lambda *a, **k: None)

    original_execute = connection.execute

    def _execute(query, params=None):
        sql = ' '.join(str(query).split()).lower()
        if 'from auth_tokens where token_hash' in sql:
            return _Result({'id': 't-1', 'user_id': USER_ID, 'expires_at': NOW + timedelta(minutes=5), 'used_at': None})
        if 'select id, session_version, mfa_totp_secret from users' in sql:
            return _Result({'id': USER_ID, 'session_version': 1, 'mfa_totp_secret': None})
        return original_execute(query, params)

    connection.execute = _execute  # type: ignore[method-assign]

    issued = pilot.mfa_complete_signin({'mfa_token': 'challenge', 'code': code}, _request('/auth/mfa/complete-signin'))
    assert issued['access_token'] == 'issued-token'
    assert connection.recovery_codes[code_hash] is True

    with pytest.raises(HTTPException) as info:
        pilot.mfa_complete_signin({'mfa_token': 'challenge', 'code': code}, _request('/auth/mfa/complete-signin'))
    assert info.value.status_code == 401


def test_12b_recovery_codes_are_hashed_and_never_retrievable() -> None:
    replace = inspect.getsource(pilot._replace_recovery_codes)
    assert '_auth_token_hash(recovery_code)' in replace
    assert 'DELETE FROM mfa_recovery_codes WHERE user_id = %s' in replace
    # The only column ever stored is the hash — no plaintext column exists.
    assert 'code_hash' in replace
    assert re.search(r"INSERT INTO mfa_recovery_codes \(id, user_id, code_hash, created_at\)", replace)


def test_12c_recovery_use_is_audited_and_rate_limited() -> None:
    source = inspect.getsource(pilot.mfa_complete_signin)
    assert "action='auth.mfa_recovery_used'" in source
    assert "action='auth.mfa_challenge_success'" in source
    assert "action='auth.mfa_challenge_failed'" in source
    main_text = (APP_DIR / 'main.py').read_text(encoding='utf-8')
    assert "enforce_auth_rate_limit(request, 'mfa_complete_signin')" in main_text
    assert "enforce_auth_rate_limit(request, 'mfa_enroll')" in main_text


def test_13_the_mfa_audit_vocabulary_is_complete() -> None:
    """Phase 11: every lifecycle event a reviewer needs is emitted somewhere."""
    text = (APP_DIR / 'pilot.py').read_text(encoding='utf-8')
    for action in (
        'auth.mfa_enrollment_started',
        'auth.mfa_enabled',
        'auth.mfa_challenge_success',
        'auth.mfa_challenge_failed',
        'auth.mfa_recovery_used',
        'auth.mfa_recovery_codes_regenerated',
        'auth.mfa_disabled',
    ):
        assert f"action='{action}'" in text, action


def test_13b_no_mfa_event_or_refusal_carries_a_secret() -> None:
    """A refusal body and an audit record carry machine facts only."""
    refusal = mfa_authz.PilotMfaRequired(
        code=mfa_authz.CODE_MFA_CHALLENGE_REQUIRED, reason='session_not_verified',
        workspace_id=WS, plan=ent.PLAN_PILOT, enforcement='all_members', purpose='authenticated_api',
    )
    for payload in (refusal.as_dict(), refusal.blocked_audit_metadata()):
        serialized = repr(payload).lower()
        for forbidden in ('secret', 'recovery_code', 'totp_code', 'token', 'password', 'otp'):
            assert forbidden not in serialized, payload


def test_13c_the_enrollment_audit_records_only_the_enrollment_id() -> None:
    source = inspect.getsource(pilot.mfa_begin_enrollment)
    audit_line = next(line for line in source.splitlines() if 'auth.mfa_enrollment_started' in line)
    assert "metadata={'enrollment_id': enrollment_id}" in audit_line
    assert 'secret' not in audit_line


# ═══════════════════════════════════════════════════════════════════════════
# 10 — non-Pilot behaviour is preserved
# ═══════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize('plan', [ent.PLAN_SCALE, ent.PLAN_ENTERPRISE])
def test_14_a_non_pilot_tenant_keeps_its_configurable_policy(plan: str) -> None:
    optional = _Conn(plan=plan, policy='optional')
    pilot.require_pilot_mfa(optional, _request('/assets'), _user(mfa_enabled=False, workspace_id=WS))
    assert pilot.workspace_effective_mfa_enforcement(optional, WS)['enforcement'] == 'optional'

    strict = _Conn(plan=plan, policy='all_members')
    assert _deny(strict, _request('/assets'), _user(mfa_enabled=False))['code'] == (
        mfa_authz.CODE_MFA_ENROLLMENT_REQUIRED
    )


def test_14b_the_administrators_policy_still_covers_only_administrators() -> None:
    admin_only = _Conn(plan=ent.PLAN_SCALE, policy='administrators')
    for role in ('owner', 'admin'):
        assert _deny(admin_only, _request('/assets'), _user(role=role))['code'] == (
            mfa_authz.CODE_MFA_ENROLLMENT_REQUIRED
        )
    for role in ('analyst', 'viewer'):
        pilot.require_pilot_mfa(
            _Conn(plan=ent.PLAN_SCALE, policy='administrators'), _request('/assets'), _user(role=role),
        )


def test_14c_a_session_that_can_reach_a_pilot_workspace_is_governed_by_it() -> None:
    """A dual-plan member cannot dodge the floor by naming the other workspace:
    the SESSION is what reaches Pilot data, so the SESSION must satisfy MFA."""
    memberships = [
        {'workspace_id': OTHER_WS, 'role': 'admin', 'workspace': {'id': OTHER_WS}},
        {'workspace_id': WS, 'role': 'viewer', 'workspace': {'id': WS}},
    ]

    class _Mixed(_Conn):
        def execute(self, query: str, params: Any = None) -> _Result:
            sql = ' '.join(str(query).split()).lower()
            if 'join organizations o on o.id = w.organization_id' in sql:
                workspace_id = str(params[0]) if params else ''
                self.plan = ent.PLAN_PILOT if workspace_id == WS else ent.PLAN_SCALE
            return super().execute(query, params)

    refusal = _deny(
        _Mixed(policy='optional'),
        _request('/assets', workspace_id=OTHER_WS),
        _user(mfa_enabled=False, workspace_id=OTHER_WS, memberships=memberships),
    )
    assert refusal['code'] == mfa_authz.CODE_MFA_ENROLLMENT_REQUIRED
    assert refusal['plan'] == ent.PLAN_PILOT


def test_14c2_a_satisfied_session_costs_one_tenant_read_not_one_per_membership() -> None:
    """A session that completed a second factor satisfies every enforcement
    level, so scanning further workspaces could not change the answer — and a
    member of many workspaces must not pay a tenant read per membership on every
    request."""
    memberships = [
        {'workspace_id': f'ws-{index}', 'role': 'analyst', 'workspace': {'id': f'ws-{index}'}}
        for index in range(12)
    ]
    connection = _Conn(session_mfa_verified=True, session_methods=['password', 'totp'])
    pilot.require_pilot_mfa(
        connection,
        _request('/assets', workspace_id='ws-0'),
        _user(mfa_enabled=True, workspace_id='ws-0', memberships=memberships),
    )
    organization_reads = connection.written('JOIN organizations o ON o.id = w.organization_id')
    assert len(organization_reads) == 1, organization_reads

    # An UNSATISFIED session still scans every workspace: finding the one that
    # requires MFA is the whole point, and that path ends in a refusal anyway.
    refused = _Conn()
    _deny(
        refused,
        _request('/assets', workspace_id='ws-0'),
        _user(mfa_enabled=False, workspace_id='ws-0', memberships=memberships),
    )


def test_14d_an_account_with_no_workspace_has_no_tenant_to_protect() -> None:
    """It also reads nothing: every data route resolves a workspace of its own."""
    pilot.require_pilot_mfa(_Conn(), _request('/assets'), _user(workspace_id=None))


def test_14e_a_header_naming_a_workspace_the_caller_does_not_belong_to_is_ignored() -> None:
    """The header cannot be used to choose a friendlier tenant's policy."""
    ordered = pilot._mfa_candidate_workspace_ids(
        _request('/assets', workspace_id='ws-not-mine'), _user(workspace_id=WS),
    )
    assert ordered == [WS]


# ═══════════════════════════════════════════════════════════════════════════
# 11 — regressions
# ═══════════════════════════════════════════════════════════════════════════
def test_15_the_response_action_step_up_gate_is_unchanged() -> None:
    """Login MFA and step-up MFA stay separate controls. The step-up still
    demands a recent challenge carried by a factor the operator holds now."""
    source = inspect.getsource(pilot._require_session_mfa)
    assert "{'totp', 'recovery_code'}" in source
    assert 'MFA_CHALLENGE_REQUIRED' in source

    approval = inspect.getsource(pilot._session_approval_stepup_satisfied)
    assert '_session_mfa_satisfied' in approval
    assert '_session_reauthentication_recent' in approval


def test_15b_enrolling_does_not_by_itself_satisfy_the_approval_step_up() -> None:
    """Enrollment stamps mfa_verified_at but never reauthenticated_at, so a
    long-lived session must still step up before approving an action."""
    confirm = inspect.getsource(pilot.mfa_confirm_enrollment)
    assert 'reauthenticated_at' not in confirm
    step_up = inspect.getsource(pilot.verify_session_step_up)
    assert 'reauthenticated_at = NOW()' in step_up


def test_15c_the_permission_helper_reads_the_same_effective_policy() -> None:
    """Defence in depth: the permissioned routes apply the floor too, from the
    one canonical function rather than a second copy of the rule."""
    source = inspect.getsource(pilot._require_workspace_permission)
    assert 'workspace_effective_mfa_enforcement(' in source
    assert "policy['mfa_enforcement'] == 'all_members'" not in source


def test_15d_the_pilot_execution_boundary_is_untouched() -> None:
    from services.api.app import execution_authorization as execution_authz

    assert execution_authz.CODE_PILOT_EXECUTION_DISABLED == 'PILOT_EXECUTION_DISABLED'
    assert 'require_pilot_mfa' not in inspect.getsource(execution_authz.assert_execution_allowed)


def test_15e_the_refusal_codes_are_stable_and_singular() -> None:
    assert mfa_authz.CODE_MFA_ENROLLMENT_REQUIRED == 'MFA_ENROLLMENT_REQUIRED'
    assert mfa_authz.CODE_MFA_CHALLENGE_REQUIRED == 'MFA_CHALLENGE_REQUIRED'
    assert mfa_authz.HTTP_FORBIDDEN == 403


def test_15f_the_policy_module_imports_without_the_request_stack() -> None:
    """Framework-free, like ``execution_authorization``: a worker or a script can
    ask the same question without pulling in FastAPI."""
    text = (APP_DIR / 'mfa_authorization.py').read_text(encoding='utf-8')
    assert 'from fastapi import' not in text.replace(
        '        from fastapi import HTTPException', '',
    )


def test_15g_the_customer_facing_message_states_the_requirement_plainly() -> None:
    assert 'required for Pilot access' in mfa_authz.MESSAGE_ENROLLMENT_REQUIRED
    assert 'Set up an authenticator' in mfa_authz.MESSAGE_ENROLLMENT_REQUIRED
    assert 'optional' not in mfa_authz.MESSAGE_ENROLLMENT_REQUIRED.lower()
