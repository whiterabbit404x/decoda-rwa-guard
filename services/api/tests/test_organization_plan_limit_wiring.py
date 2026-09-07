"""Plan limits are enforced at the WRITE, in the real handlers.

The entitlement engine is tested in isolation elsewhere. What matters here is
where the check sits: a limit that is evaluated after the row is inserted is not
a limit, and a limit evaluated on the idempotent-reuse path would refuse work
that adds nothing.

  1  create_asset gates on monitored_contracts BEFORE inserting.
  2  create_target gates on monitoring_targets BEFORE inserting.
  3  create_workspace_for_user gates on workspaces BEFORE inserting.
  4  Both evidence-package writers gate on evidence_packages, and only when a NEW
     package is minted — reuse is never refused by the cap.
  5  The legacy per-workspace plan row is WIDENED by the organization plan, so
     the two plan systems cannot contradict each other, and the overlay can never
     narrow a capability a paid legacy subscription already granted.

Run:
    python -m pytest services/api/tests/test_organization_plan_limit_wiring.py -q
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from services.api.app import entitlements as ent
from services.api.app import organizations as org_service
from services.api.app import pilot

WS = 'ws-1111-1111-1111-111111111111'
USER = 'user-1'


class _Result:
    def __init__(self, row: Any = None) -> None:
        self._row = row

    def fetchone(self) -> Any:
        return self._row

    def fetchall(self) -> list[Any]:
        return [] if self._row is None else [self._row]


class _RecordingConn:
    """Records writes; every read returns nothing unless a handler needs it."""

    def __init__(self, reads: dict[str, Any] | None = None) -> None:
        self.reads = reads or {}
        self.writes: list[str] = []
        self.committed = False

    def execute(self, query: str, params: Any = None) -> _Result:
        sql = ' '.join(str(query).split())
        lowered = sql.lower()
        if lowered.startswith(('insert', 'update', 'delete')):
            self.writes.append(sql)
            return _Result()
        for needle, row in self.reads.items():
            if needle in lowered:
                return _Result(row)
        return _Result()

    def commit(self) -> None:
        self.committed = True

    def transaction(self):
        @contextmanager
        def _noop():
            yield

        return _noop()


def _request() -> SimpleNamespace:
    return SimpleNamespace(headers={'x-workspace-id': WS}, client=None)


def _limit_error(resource: str) -> HTTPException:
    return HTTPException(
        status_code=403,
        detail={'code': 'PLAN_LIMIT_REACHED', 'resource': resource, 'limit': 5, 'current': 5, 'plan': 'pilot'},
    )


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch):
    """Authenticate every request and capture the enforcement calls."""
    calls: list[str] = []

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)
    context = {
        'id': USER, 'email': 'a@example.com',
        'email_verified_at': '2026-01-01T00:00:00Z', 'mfa_enabled': False,
    }
    workspace_context = {
        'workspace_id': WS, 'role': 'owner',
        'workspace': {'id': WS, 'name': 'W', 'slug': 'w'},
    }
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_a, **_k: dict(context))
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda *_a, **_k: dict(workspace_context))
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda *_a, **_k: (dict(context), dict(workspace_context)))
    monkeypatch.setattr(pilot, '_require_workspace_permission', lambda *_a, **_k: (dict(context), dict(workspace_context)))
    monkeypatch.setattr(pilot, '_workspace_plan', lambda *_a, **_k: {'max_targets': 500, 'exports_enabled': True})
    return SimpleNamespace(calls=calls, user=context, workspace_context=workspace_context)


def _refuse(monkeypatch: pytest.MonkeyPatch, wired, expected_key: str) -> None:
    def _enforce(connection: Any, workspace_id: str, limit_key: str):
        wired.calls.append(limit_key)
        if limit_key == expected_key:
            raise _limit_error(ent.LIMIT_RESOURCES[limit_key])
        return {'available': True}

    monkeypatch.setattr(pilot, 'enforce_plan_creation_limit', _enforce)


def _connection(monkeypatch: pytest.MonkeyPatch, connection: _RecordingConn) -> None:
    @contextmanager
    def _pg():
        yield connection

    monkeypatch.setattr(pilot, 'pg_connection', _pg)


# ── 1 — assets ────────────────────────────────────────────────────────────────

def test_create_asset_refuses_at_the_contract_limit_before_writing(
    monkeypatch: pytest.MonkeyPatch, wired,
) -> None:
    connection = _RecordingConn()
    _connection(monkeypatch, connection)
    _refuse(monkeypatch, wired, ent.LIMIT_MONITORED_CONTRACTS)

    with pytest.raises(HTTPException) as exc_info:
        pilot.create_asset(
            {
                'name': 'ABC Treasury Token', 'asset_type': 'contract',
                'chain_network': 'base-mainnet', 'identifier': '0x' + '1' * 40,
            },
            _request(),
        )
    assert exc_info.value.detail['code'] == 'PLAN_LIMIT_REACHED'
    assert exc_info.value.detail['resource'] == 'monitored_contracts'
    assert wired.calls == [ent.LIMIT_MONITORED_CONTRACTS]
    assert connection.writes == []
    assert connection.committed is False


# ── 2 — monitoring targets ────────────────────────────────────────────────────

def test_create_target_refuses_at_the_target_limit_before_writing(
    monkeypatch: pytest.MonkeyPatch, wired,
) -> None:
    connection = _RecordingConn()
    _connection(monkeypatch, connection)
    _refuse(monkeypatch, wired, ent.LIMIT_MONITORING_TARGETS)

    with pytest.raises(HTTPException) as exc_info:
        pilot.create_target(
            {
                'name': 'Treasury contract', 'target_type': 'contract',
                'chain_network': 'base-mainnet', 'asset_id': '11111111-1111-1111-1111-111111111111',
            },
            _request(),
        )
    assert exc_info.value.detail['resource'] == 'monitoring_targets'
    assert wired.calls == [ent.LIMIT_MONITORING_TARGETS]
    assert connection.writes == []


# ── 3 — workspaces ────────────────────────────────────────────────────────────

def test_create_workspace_refuses_at_the_workspace_limit_before_writing(
    monkeypatch: pytest.MonkeyPatch, wired,
) -> None:
    connection = _RecordingConn(reads={'select current_workspace_id from users': {'current_workspace_id': WS}})
    _connection(monkeypatch, connection)
    monkeypatch.setattr(org_service, 'tenancy_schema_ready', lambda *_: True)
    monkeypatch.setattr(
        org_service, 'ensure_organization_for_workspace',
        lambda *_a, **_k: {'id': 'org-1', 'plan': 'pilot', 'status': 'active',
                           'evaluation_expires_at': None, 'entitlement_overrides': {}},
    )
    monkeypatch.setattr(org_service, 'count_workspaces', lambda *_a, **_k: 1)
    # Membership re-check must succeed so the tenant is resolved, not invented.
    connection.reads['select 1 from workspace_members'] = {'1': 1}

    with pytest.raises(HTTPException) as exc_info:
        pilot.create_workspace_for_user({'name': 'Second workspace'}, _request())
    assert exc_info.value.detail['code'] == 'PLAN_LIMIT_REACHED'
    assert exc_info.value.detail['resource'] == 'workspaces'
    assert exc_info.value.detail['limit'] == 1
    assert connection.writes == []


def test_create_workspace_reuses_the_callers_tenant_not_a_new_one(
    monkeypatch: pytest.MonkeyPatch, wired,
) -> None:
    connection = _RecordingConn(reads={
        'select current_workspace_id from users': {'current_workspace_id': WS},
        'select 1 from workspace_members': {'1': 1},
    })
    _connection(monkeypatch, connection)
    monkeypatch.setattr(org_service, 'tenancy_schema_ready', lambda *_: True)
    monkeypatch.setattr(
        org_service, 'ensure_organization_for_workspace',
        lambda *_a, **_k: {'id': 'org-1', 'plan': 'scale', 'status': 'active',
                           'evaluation_expires_at': None, 'entitlement_overrides': {}},
    )
    monkeypatch.setattr(org_service, 'count_workspaces', lambda *_a, **_k: 1)
    created: list[str] = []
    monkeypatch.setattr(
        org_service, 'create_organization',
        lambda *_a, **_k: created.append('created') or {'id': 'org-new'},
    )

    resolved = pilot._resolve_organization_for_new_workspace(
        connection, user_id=USER, workspace_name='Second workspace',
    )
    assert resolved['id'] == 'org-1'
    assert created == []


def test_create_workspace_ignores_a_stale_current_workspace_without_membership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A current_workspace_id the caller no longer belongs to must not attach a
    new workspace to that tenant."""
    connection = _RecordingConn(reads={'select current_workspace_id from users': {'current_workspace_id': WS}})
    monkeypatch.setattr(org_service, 'tenancy_schema_ready', lambda *_: True)
    monkeypatch.setattr(
        org_service, 'ensure_organization_for_workspace',
        lambda *_a, **_k: pytest.fail('must not resolve a tenant without membership'),
    )
    monkeypatch.setattr(org_service, 'create_organization', lambda *_a, **_k: {'id': 'org-new'})

    resolved = pilot._resolve_organization_for_new_workspace(
        connection, user_id=USER, workspace_name='Fresh workspace',
    )
    assert resolved['id'] == 'org-new'


# ── 4 — evidence packages ─────────────────────────────────────────────────────

def test_evidence_package_writers_both_gate_on_the_evidence_limit() -> None:
    """Both proof-bundle INSERTs are preceded by the evidence-package gate.

    Read from the source rather than by driving the handlers, because what is
    asserted is ORDER: a limit checked after the INSERT is not a limit, and only
    the relative position of the two statements can establish that.
    """
    import inspect

    for handler in (pilot.create_proof_bundle_export, pilot.create_evidence_package_from_response_action):
        source = inspect.getsource(handler)
        insert_at = source.index('INSERT INTO export_jobs')
        assert 'LIMIT_EVIDENCE_PACKAGES' in source[:insert_at], (
            f'{handler.__name__} inserts an evidence package before enforcing the plan limit'
        )


def test_evidence_limit_is_not_applied_on_the_idempotent_reuse_path() -> None:
    """Returning an already-generated package adds nothing, so it must not be
    refused by a cap it does not consume."""
    import inspect

    source = inspect.getsource(pilot.create_proof_bundle_export)
    reuse_branch = source.index('if reuse_existing:')
    gate_at = source.index('LIMIT_EVIDENCE_PACKAGES')
    assert gate_at > reuse_branch


# ── 5 — the two plan systems cannot contradict each other ─────────────────────

def _overlay_connection(monkeypatch: pytest.MonkeyPatch, organization: dict[str, Any] | None) -> _RecordingConn:
    connection = _RecordingConn()
    monkeypatch.setattr(org_service, 'tenancy_schema_ready', lambda *_: organization is not None)
    monkeypatch.setattr(org_service, 'organization_for_workspace', lambda *_a, **_k: organization)
    return connection


def test_organization_plan_widens_the_legacy_target_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    organization = {'id': 'org-1', 'plan': 'scale', 'status': 'active',
                    'evaluation_expires_at': None, 'entitlement_overrides': {}}
    connection = _overlay_connection(monkeypatch, organization)
    plan = pilot._apply_organization_plan_overlay(connection, WS, {'max_targets': 10, 'exports_enabled': False})
    assert plan['max_targets'] == 50          # Scale's ceiling, not the legacy 10
    assert plan['exports_enabled'] is True    # published pricing includes evidence


def test_unlimited_organization_plan_never_becomes_a_zero_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    """max_targets is read as int(value or 0); an unlimited entitlement written
    as NULL there would silently block everything."""
    organization = {'id': 'org-1', 'plan': 'enterprise', 'status': 'active',
                    'evaluation_expires_at': None, 'entitlement_overrides': {}}
    connection = _overlay_connection(monkeypatch, organization)
    plan = pilot._apply_organization_plan_overlay(connection, WS, {'max_targets': 10, 'exports_enabled': True})
    assert plan['max_targets'] == pilot.LEGACY_UNLIMITED_TARGETS
    assert plan['max_targets'] > 0


def test_overlay_never_narrows_a_paid_legacy_entitlement(monkeypatch: pytest.MonkeyPatch) -> None:
    organization = {'id': 'org-1', 'plan': 'pilot', 'status': 'active',
                    'evaluation_expires_at': None, 'entitlement_overrides': {}}
    connection = _overlay_connection(monkeypatch, organization)
    plan = pilot._apply_organization_plan_overlay(connection, WS, {'max_targets': 250, 'exports_enabled': True})
    assert plan['max_targets'] == 250


def test_overlay_leaves_the_legacy_row_alone_before_the_migration(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _overlay_connection(monkeypatch, None)
    plan = pilot._apply_organization_plan_overlay(connection, WS, {'max_targets': 10, 'exports_enabled': False})
    assert plan == {'max_targets': 10, 'exports_enabled': False}


def test_overlay_failure_never_takes_the_plan_read_down(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _RecordingConn()
    monkeypatch.setattr(org_service, 'tenancy_schema_ready', lambda *_: True)

    def _boom(*_a, **_k):
        raise RuntimeError('database unavailable')

    monkeypatch.setattr(org_service, 'organization_for_workspace', _boom)
    plan = pilot._apply_organization_plan_overlay(connection, WS, {'max_targets': 10, 'exports_enabled': False})
    assert plan == {'max_targets': 10, 'exports_enabled': False}
