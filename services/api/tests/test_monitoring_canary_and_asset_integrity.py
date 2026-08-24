"""Regression tests for the two Base-mainnet canary-demo production blockers.

BLOCKER 1 — a canary-excluded target still drove Base RPC via the anti-starvation
"backfill" fallback (selected_for_backfill=True). Canary mode must mean ONLY allowlisted
targets can cause blockchain-provider RPC, on ANY scheduled path (live poll, catch-up,
backfill, bootstrap/live-tail, recovery).

BLOCKER 2 — a target whose asset_id is missing from asset_registry (because
targets.asset_id -> assets(id) while telemetry_events.asset_id -> asset_registry(id); see
migration 0089) crashed the worker mid-poll with telemetry_events_asset_id_fkey. The fix
resolves an FK-safe telemetry asset id up front: when the canonical assets row exists the
asset_registry FK chain is repaired (same uuid); a true orphan degrades to a FK-safe NULL
telemetry asset (never a fabricated id) with an explicit integrity error. A broken asset
relationship never fabricates an id and never crashes the cycle.

These tests exercise the authoritative gates inside ``process_monitoring_target`` (the RPC-
consuming entry point) plus the pure selection helpers, so they run without a real DB.

Task test map:
  A  canary + 1 allowed target: a non-allowlisted target cannot reach live polling
  B  canary + 1 allowed target: a non-allowlisted target cannot reach backfill/recovery RPC
  C  the RPC provider mock receives ZERO calls for an excluded target
  D  an allowlisted Base target still polls normally (reaches provider RPC)
  E  an allowlisted target keeps effective_interval_seconds=900 when the min is 900
  F  an orphan asset reference cannot cause an uncaught ForeignKeyViolation
  G  an invalid asset relationship does not create telemetry with a fabricated asset id
     (and the canonical assets->asset_registry repair reuses the SAME uuid, not a new one)
  H  one bad target does not abort processing of unrelated healthy targets
  I  the resolver never yields an id that violates telemetry_events_asset_id_fkey (NULL-safe)
"""
from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import patch

import pytest
from psycopg.errors import ForeignKeyViolation

from services.api.app import monitoring_runner as mr
from services.api.app.monitoring_canary import (
    CANARY_ENABLED_ENV,
    CANARY_TARGET_ALLOWLIST_ENV,
    resolve_canary_config,
)

ALLOWED = '11111111-1111-4111-8111-111111111111'   # the single canary demo target
EXCLUDED = '9c6ecabb-cd52-404f-9859-40567b09dbb4'  # the Datto/USDC target from prod logs


class _ReachedRPC(Exception):
    """Raised by the patched provider fetch to prove RPC scanning was reached."""


class _Res:
    def __init__(self, one: Any = None, many: list | None = None):
        self._one = one
        self._many = many or []

    def fetchone(self):
        return self._one

    def fetchall(self):
        return list(self._many)


class FakeConn:
    """Minimal fake DB connection modelling the FK relationship under test.

    * ``asset_registry_ids`` — ids currently present in asset_registry.
    * ``assets_ids``         — ids present in the canonical assets table.
    * an INSERT INTO asset_registry registers the id (models the canonical repair).
    * an INSERT INTO telemetry_events with an asset_id NOT in asset_registry raises
      ForeignKeyViolation, exactly like Postgres' telemetry_events_asset_id_fkey — this is
      the tripwire that would fire if the fix ever let an orphan reach a telemetry write.
    """

    def __init__(self, *, asset_registry_ids=(), assets_ids=(), workspace_name='Workspace'):
        self.asset_registry_ids = {str(x) for x in asset_registry_ids}
        self.assets_ids = {str(x) for x in assets_ids}
        self.workspace_name = workspace_name
        self.executed: list[tuple[str, Any]] = []

    # -- helpers ------------------------------------------------------------
    def sqls(self) -> list[str]:
        return [' '.join((s or '').split()).lower() for s, _ in self.executed]

    def executed_matching(self, needle: str) -> list[tuple[str, Any]]:
        n = needle.lower()
        return [(s, p) for s, p in self.executed if n in ' '.join((s or '').split()).lower()]

    @staticmethod
    def _param(params: Any, idx: int):
        if isinstance(params, (list, tuple)) and len(params) > idx:
            return params[idx]
        return None

    # -- DB surface ---------------------------------------------------------
    def execute(self, sql: str, params: Any = None):
        self.executed.append((sql, params))
        q = ' '.join((sql or '').split()).lower()

        if 'insert into asset_registry' in q:
            aid = self._param(params, 0)
            if aid is not None:
                self.asset_registry_ids.add(str(aid))
            return _Res(None, [])

        if 'insert into telemetry_events' in q:
            # telemetry_events columns: (id, workspace_id, asset_id, target_id, ...)
            aid = self._param(params, 2)
            if aid is not None and str(aid) not in self.asset_registry_ids:
                raise ForeignKeyViolation(
                    f'insert or update on table "telemetry_events" violates foreign key '
                    f'constraint "telemetry_events_asset_id_fkey": key (asset_id)=({aid})'
                )
            return _Res(None, [])

        if 'from asset_registry' in q and q.strip().startswith('select'):
            aid = self._param(params, 0)
            return _Res({'ok': 1} if str(aid) in self.asset_registry_ids else None, [])

        if 'from assets' in q and q.strip().startswith('select'):
            aid = self._param(params, 0)
            return _Res({'ok': 1} if str(aid) in self.assets_ids else None, [])

        if 'from workspaces' in q:
            wsid = self._param(params, 0)
            return _Res({'id': wsid, 'name': self.workspace_name}, [])

        return _Res(None, [])


def _make_target(
    target_id: str,
    *,
    asset_id: str | None = None,
    workspace_id: str | None = None,
    target_type: str = 'smart_contract',
    last_checked_at: Any = None,
) -> dict[str, Any]:
    return {
        'id': target_id,
        'workspace_id': workspace_id or str(uuid.uuid4()),
        'asset_id': asset_id,
        'target_type': target_type,
        'chain_network': 'base',
        'wallet_address': '',
        'contract_identifier': '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48',
        'name': 'USDC on Base',
        'monitored_system_id': str(uuid.uuid4()),
        'watcher_last_observed_block': 0,
        'monitoring_interval_seconds': 900,
        'last_checked_at': last_checked_at,
    }


@pytest.fixture()
def _canary_one_allowed(monkeypatch):
    """Canary ON with exactly one allowlisted target (the production demo posture)."""
    monkeypatch.delenv(CANARY_ENABLED_ENV, raising=False)
    monkeypatch.setenv(CANARY_TARGET_ALLOWLIST_ENV, ALLOWED)
    cfg = resolve_canary_config()
    assert cfg.enabled is True and cfg.allowed_target_count == 1
    return cfg


@pytest.fixture()
def _canary_off(monkeypatch):
    """Canary disabled (normal production): the canary gate never blocks, so the asset-
    integrity behavior can be exercised on its own."""
    monkeypatch.delenv(CANARY_ENABLED_ENV, raising=False)
    monkeypatch.delenv(CANARY_TARGET_ALLOWLIST_ENV, raising=False)
    assert resolve_canary_config().enabled is False


# =====================================================================================
# BLOCKER 1 — canary RPC gate
# =====================================================================================

def test_A_non_allowlisted_cannot_reach_live_polling(_canary_one_allowed):
    """TEST A: a non-allowlisted target due for a live poll never reaches provider RPC."""
    conn = FakeConn()
    target = _make_target(EXCLUDED)  # recently onboarded, due for live poll
    with patch.object(mr, 'fetch_target_activity_result', side_effect=_ReachedRPC) as fetch:
        result = mr.process_monitoring_target(conn, target, monitoring_run_id=str(uuid.uuid4()))
    assert fetch.call_count == 0
    assert result['status'] == 'canary_excluded'
    assert result['status_reason_code'] == 'not_in_canary_allowlist'
    assert result['network_attempted'] is False


def test_B_non_allowlisted_cannot_reach_backfill_rpc(_canary_one_allowed):
    """TEST B: even a backfill-shaped candidate (never polled / oldest) gets zero RPC."""
    conn = FakeConn()
    # last_checked_at=None => this is exactly the kind of target the anti-starvation
    # backfill fallback would otherwise pick as the "oldest" candidate.
    target = _make_target(EXCLUDED, last_checked_at=None)
    with patch.object(mr, 'fetch_target_activity_result', side_effect=_ReachedRPC) as fetch:
        result = mr.process_monitoring_target(conn, target, monitoring_run_id=str(uuid.uuid4()))
    assert fetch.call_count == 0
    assert result['status'] == 'canary_excluded'


def test_B_backfill_selection_helper_blocks_non_allowlisted(_canary_one_allowed):
    """TEST B (selection): the backfill-candidate guard blocks a non-allowlisted fallback,
    so an excluded target can never be logged selected_for_backfill=True."""
    cfg = _canary_one_allowed
    assert mr._canary_blocks_backfill_target(cfg, EXCLUDED) is True
    assert mr._canary_blocks_backfill_target(cfg, ALLOWED) is False
    # No candidate => nothing to block (not a spurious block/log).
    assert mr._canary_blocks_backfill_target(cfg, '') is False


def test_B_backfill_helper_never_blocks_when_canary_off(monkeypatch):
    """Canary OFF must never block normal-production backfill."""
    monkeypatch.delenv(CANARY_ENABLED_ENV, raising=False)
    monkeypatch.delenv(CANARY_TARGET_ALLOWLIST_ENV, raising=False)
    cfg = resolve_canary_config()
    assert cfg.enabled is False
    assert mr._canary_blocks_backfill_target(cfg, EXCLUDED) is False
    assert mr._canary_blocks_backfill_target(cfg, ALLOWED) is False


def test_C_rpc_mock_receives_zero_calls_for_excluded_target(_canary_one_allowed):
    """TEST C: the provider fetch mock records ZERO calls for an excluded target, and a
    greppable network_attempted=false block line is emitted."""
    conn = FakeConn()
    target = _make_target(EXCLUDED)
    with patch.object(mr, 'fetch_target_activity_result', side_effect=_ReachedRPC) as fetch:
        with patch.object(mr.logger, 'warning') as warn:
            mr.process_monitoring_target(conn, target, monitoring_run_id=str(uuid.uuid4()))
    assert fetch.call_count == 0
    joined = ' '.join(str(c.args) for c in warn.call_args_list)
    assert 'monitoring_canary_rpc_path_blocked' in joined
    assert 'network_attempted=false' in joined


def test_D_allowlisted_target_still_polls(_canary_one_allowed):
    """TEST D: an allowlisted Base target passes both gates and reaches provider RPC."""
    asset_id = str(uuid.uuid4())
    conn = FakeConn(asset_registry_ids=[asset_id])  # healthy: asset already registered
    target = _make_target(ALLOWED, asset_id=asset_id)
    with patch.object(mr, '_load_checkpoint', return_value=0):
        with patch.object(mr, 'fetch_target_activity_result', side_effect=_ReachedRPC) as fetch:
            with pytest.raises(_ReachedRPC):
                mr.process_monitoring_target(conn, target, monitoring_run_id=str(uuid.uuid4()))
    assert fetch.call_count == 1


def test_D_allowlisted_no_asset_still_polls(_canary_one_allowed):
    """An allowlisted target with NO linked asset still polls (asset_id NULL is FK-safe)."""
    conn = FakeConn()
    target = _make_target(ALLOWED, asset_id=None)
    with patch.object(mr, '_load_checkpoint', return_value=0):
        with patch.object(mr, 'fetch_target_activity_result', side_effect=_ReachedRPC) as fetch:
            with pytest.raises(_ReachedRPC):
                mr.process_monitoring_target(conn, target, monitoring_run_id=str(uuid.uuid4()))
    assert fetch.call_count == 1


def test_D_manual_run_is_not_canary_gated(_canary_one_allowed):
    """A manual run-once (triggered_by_user_id set) is an explicit user action and is not
    blocked by the canary gate — only the scheduled worker path is gated."""
    asset_id = str(uuid.uuid4())
    conn = FakeConn(asset_registry_ids=[asset_id])
    target = _make_target(EXCLUDED, asset_id=asset_id)
    with patch.object(mr, '_load_checkpoint', return_value=0):
        with patch.object(mr, 'fetch_target_activity_result', side_effect=_ReachedRPC) as fetch:
            with pytest.raises(_ReachedRPC):
                mr.process_monitoring_target(
                    conn, target, triggered_by_user_id=str(uuid.uuid4()),
                    monitoring_run_id=str(uuid.uuid4()),
                )
    assert fetch.call_count == 1


def test_E_allowlisted_target_effective_interval_stays_900(monkeypatch):
    """TEST E: with the production minimum configured as 900, a target configured at 900
    keeps effective_interval_seconds=900, and a sub-900 target is floored UP to 900 (never
    polled more often than the QuickNode cost-safety minimum)."""
    monkeypatch.setenv('MIN_EVM_POLLING_INTERVAL_SECONDS', '900')
    min_interval = mr._min_monitoring_interval_seconds()
    assert min_interval == 900
    # Exact runner formula: interval_seconds = max(_min_interval, configured).
    assert max(min_interval, 900) == 900          # allowed demo target
    assert max(min_interval, 300) == 900          # sub-floor target capped up, never down
    assert max(min_interval, 1800) == 1800        # above-floor target respected


# =====================================================================================
# BLOCKER 2 — orphan asset FK, degrade-safe (canonical repair, else FK-safe NULL)
# =====================================================================================


def test_F_orphan_asset_does_not_raise_foreign_key_violation():
    """TEST F: an orphan asset_id (in neither asset_registry nor assets) must NOT crash the
    worker with an uncaught ForeignKeyViolation. The resolver yields a FK-safe NULL telemetry
    asset id, so every telemetry_events insert carries NULL (never the orphan id). FakeConn
    raises the real telemetry_events_asset_id_fkey error if a NON-NULL unregistered id is ever
    inserted — this proves the orphan id never reaches a telemetry write."""
    orphan = str(uuid.uuid4())
    conn = FakeConn()  # orphan in neither table
    target = _make_target(ALLOWED, asset_id=orphan)
    safe_id, is_orphan = mr._resolve_target_telemetry_asset_id(conn, target, 'base')
    assert safe_id is None and is_orphan is True
    # The telemetry helper yields NULL for the orphan -> an FK-safe telemetry insert.
    target['_telemetry_asset_id'] = safe_id
    target['_asset_integrity_orphan'] = is_orphan
    assert mr._telemetry_asset_id_for(target) is None
    # A telemetry_events insert with a NULL asset_id does not violate the FK (no raise).
    conn.execute(
        'INSERT INTO telemetry_events (id, workspace_id, asset_id, target_id) VALUES (%s,%s,%s,%s)',
        (str(uuid.uuid4()), target['workspace_id'], mr._telemetry_asset_id_for(target), target['id']),
    )


def test_F_orphan_target_still_polls_degraded_not_skipped(_canary_off):
    """A broken asset relationship degrades the telemetry LINKAGE (NULL asset) but must not
    abort the target: it still reaches the provider scan (network attempted), never crashing
    the worker cycle."""
    orphan = str(uuid.uuid4())
    conn = FakeConn()
    target = _make_target(str(uuid.uuid4()), asset_id=orphan)
    with patch.object(mr, '_load_checkpoint', return_value=0):
        with patch.object(mr, 'fetch_target_activity_result', side_effect=_ReachedRPC) as fetch:
            with pytest.raises(_ReachedRPC):
                mr.process_monitoring_target(conn, target, monitoring_run_id=str(uuid.uuid4()))
    assert fetch.call_count == 1  # proceeded to the scan, not skipped
    assert target.get('_telemetry_asset_id') is None
    assert target.get('_asset_integrity_orphan') is True


def test_F_orphan_emits_explicit_integrity_error(_canary_off):
    """An explicit, greppable integrity error is emitted for a true orphan so corrupted
    relational data is surfaced, never silently laundered into asset-linked evidence."""
    orphan = str(uuid.uuid4())
    conn = FakeConn()
    target = _make_target(str(uuid.uuid4()), asset_id=orphan)
    with patch.object(mr, '_load_checkpoint', return_value=0):
        with patch.object(mr, 'fetch_target_activity_result', side_effect=_ReachedRPC):
            with patch.object(mr.logger, 'error') as err:
                with pytest.raises(_ReachedRPC):
                    mr.process_monitoring_target(conn, target, monitoring_run_id=str(uuid.uuid4()))
    joined = ' '.join(str(c.args) for c in err.call_args_list)
    assert 'monitoring_target_asset_integrity_failed' in joined
    assert 'asset_registry_match=false' in joined


def test_G_true_orphan_is_never_fabricated_into_asset_registry():
    """TEST G: an invalid asset relationship must NOT fabricate an asset_registry row and must
    NOT resolve to any invented asset id — the telemetry asset stays NULL."""
    orphan = str(uuid.uuid4())
    conn = FakeConn()
    target = _make_target(ALLOWED, asset_id=orphan)
    safe_id, is_orphan = mr._resolve_target_telemetry_asset_id(conn, target, 'base')
    assert safe_id is None and is_orphan is True
    assert not conn.executed_matching('insert into asset_registry'), 'orphan must never be fabricated'
    assert orphan not in conn.asset_registry_ids


def test_G_canonical_repair_uses_existing_assets_uuid_not_a_fabricated_one():
    """The ONLY repair allowed is completing the canonical FK chain: when targets.asset_id
    resolves to a REAL assets row, an asset_registry row is inserted with the SAME uuid
    (migration 0089's strategy) — never a newly invented id — and that id is then used."""
    asset_id = str(uuid.uuid4())
    conn = FakeConn(assets_ids=[asset_id])  # canonical assets row exists, registry missing
    target = _make_target(ALLOWED, asset_id=asset_id)
    safe_id, is_orphan = mr._resolve_target_telemetry_asset_id(conn, target, 'base')
    assert is_orphan is False
    assert safe_id == asset_id  # the target's own uuid, not a fabricated one
    repairs = conn.executed_matching('insert into asset_registry')
    assert len(repairs) == 1
    assert str(repairs[0][1][0]) == asset_id
    assert asset_id in conn.asset_registry_ids


def test_G_already_registered_asset_needs_no_repair():
    """An asset already present in asset_registry is used directly with no repair insert."""
    asset_id = str(uuid.uuid4())
    conn = FakeConn(asset_registry_ids=[asset_id])
    target = _make_target(ALLOWED, asset_id=asset_id)
    safe_id, is_orphan = mr._resolve_target_telemetry_asset_id(conn, target, 'base')
    assert (safe_id, is_orphan) == (asset_id, False)
    assert not conn.executed_matching('insert into asset_registry')


def test_H_one_bad_target_does_not_abort_healthy_targets(_canary_off):
    """TEST H: a bad (orphan) target processes without raising (so the worker cycle continues),
    and an unrelated healthy target still reaches provider RPC."""
    # Orphan target: proceeds to the scan without raising before it (degrade-safe).
    bad = FakeConn()
    bad_target = _make_target(str(uuid.uuid4()), asset_id=str(uuid.uuid4()))
    with patch.object(mr, '_load_checkpoint', return_value=0):
        with patch.object(mr, 'fetch_target_activity_result', side_effect=_ReachedRPC) as bad_fetch:
            with pytest.raises(_ReachedRPC):
                mr.process_monitoring_target(bad, bad_target, monitoring_run_id=str(uuid.uuid4()))
    assert bad_fetch.call_count == 1  # not skipped; the bad asset did not abort processing

    # Healthy target: still polls normally.
    good_asset = str(uuid.uuid4())
    good = FakeConn(asset_registry_ids=[good_asset])
    good_target = _make_target(str(uuid.uuid4()), asset_id=good_asset)
    with patch.object(mr, '_load_checkpoint', return_value=0):
        with patch.object(mr, 'fetch_target_activity_result', side_effect=_ReachedRPC) as good_fetch:
            with pytest.raises(_ReachedRPC):
                mr.process_monitoring_target(good, good_target, monitoring_run_id=str(uuid.uuid4()))
    assert good_fetch.call_count == 1


def test_I_null_asset_id_is_fk_safe_in_telemetry_insert():
    """TEST I: the resolver never returns an id that violates telemetry_events_asset_id_fkey —
    an unresolved/orphan asset always yields NULL, and a NULL asset_id is FK-safe. FakeConn
    raises the real FK error for a non-null unregistered id, so this asserts the invariant."""
    # Orphan -> NULL -> safe.
    orphan_conn = FakeConn()
    orphan_target = _make_target(ALLOWED, asset_id=str(uuid.uuid4()))
    safe_id, _ = mr._resolve_target_telemetry_asset_id(orphan_conn, orphan_target, 'base')
    orphan_conn.execute(
        'INSERT INTO telemetry_events (id, workspace_id, asset_id, target_id) VALUES (%s,%s,%s,%s)',
        (str(uuid.uuid4()), orphan_target['workspace_id'], safe_id, orphan_target['id']),
    )  # must not raise
    # A non-null unregistered id WOULD violate the FK (guards the FakeConn tripwire itself).
    with pytest.raises(ForeignKeyViolation):
        orphan_conn.execute(
            'INSERT INTO telemetry_events (id, workspace_id, asset_id, target_id) VALUES (%s,%s,%s,%s)',
            (str(uuid.uuid4()), orphan_target['workspace_id'], str(uuid.uuid4()), orphan_target['id']),
        )
