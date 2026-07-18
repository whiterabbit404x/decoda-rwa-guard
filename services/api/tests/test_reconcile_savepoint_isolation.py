"""Reconciliation transaction isolation (Screen 4 part 8).

Root cause: ensure_direct_monitoring_config_for_target swallowed a failing audit /
chain-read DB statement WITHOUT rolling back to a savepoint, leaving the connection
in an aborted state. The surrounding per-target savepoint's RELEASE then failed with
in_failed_sql_transaction, cascading reconcile_direct_monitoring_config_failed ->
target_monitoring_reconcile_target_failed and rolling back the whole connection.

These tests pin the fix using a fake connection that models real psycopg savepoint
semantics:
  * a statement failing inside a transaction marks it aborted;
  * any further statement in an aborted tx fails (in_failed_sql_transaction);
  * ROLLBACK TO SAVEPOINT (exception exit of transaction()) recovers the connection;
  * RELEASE SAVEPOINT (normal exit) FAILS while the tx is aborted.
"""
from __future__ import annotations

import contextlib
import uuid

import pytest

from services.api.app import pilot

WS = '4fffd3f9-d55f-456f-8a7e-8b9ed2083721'
TARGET = '9c6ecabb-cd52-404f-9859-40567b09dbb4'
EXISTING_CONFIG = '6fac55eb-efeb-4081-ad44-025efacab7dd'
BASE_CHAIN = 'base-mainnet'


class _FakeDBError(Exception):
    def __init__(self, name, sqlstate):
        super().__init__(name)
        self.__class__.__name__ = name
        self.sqlstate = sqlstate


def _unique_violation():
    return _FakeDBError('UniqueViolation', '23505')


def _in_failed_txn():
    return _FakeDBError('InFailedSqlTransaction', '25P02')


class _Result:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows if rows is not None else ([] if row is None else [row])

    def fetchone(self):
        return self._row

    def fetchall(self):
        return list(self._rows)


class _SavepointConn:
    """Fake connection modelling psycopg savepoint + aborted-transaction semantics."""

    def __init__(self, *, existing_config_id=None, audit_fails=True):
        self.existing_config_id = existing_config_id
        self.audit_fails = audit_fails
        self.aborted = False
        self.release_failures = 0

    def execute(self, query, params=None):
        q = ' '.join(str(query).split()).upper()
        # In an aborted transaction every statement fails until a rollback.
        if self.aborted:
            raise _in_failed_txn()
        if q.startswith('INSERT INTO AUDIT'):
            # The audit write fails and aborts the transaction (the production trigger).
            if self.audit_fails:
                self.aborted = True
                raise _unique_violation()
            return _Result(None)
        if q.startswith('SELECT ID FROM MONITORING_CONFIGS'):
            rows = [{'id': self.existing_config_id}] if self.existing_config_id else []
            return _Result(rows=rows)
        if q.startswith('SELECT CHAIN_NETWORK FROM TARGETS'):
            return _Result({'chain_network': BASE_CHAIN})
        return _Result(None)

    @contextlib.contextmanager
    def transaction(self):
        """Model SAVEPOINT: rollback-to-savepoint on exception recovers; release fails
        (and raises) if the tx is aborted on a normal exit."""
        try:
            yield
        except Exception:
            # ROLLBACK TO SAVEPOINT recovers the connection.
            self.aborted = False
            raise
        else:
            # RELEASE SAVEPOINT is illegal while the tx is aborted.
            if self.aborted:
                self.release_failures += 1
                raise _in_failed_txn()

    def commit(self):
        pass


@pytest.fixture()
def _audit_writes_sql(monkeypatch):
    """Make log_audit perform a real (failing) SQL write against the fake connection."""
    def _log_audit(connection, **_kw):
        connection.execute('INSERT INTO audit_log (id) VALUES (%s)', (str(uuid.uuid4()),))

    monkeypatch.setattr(pilot, 'log_audit', _log_audit)


# ---------------------------------------------------------------------------
# 13. Existing direct configuration is preserved without transaction abort.
# ---------------------------------------------------------------------------

def test_existing_config_preserved_without_transaction_abort(_audit_writes_sql):
    conn = _SavepointConn(existing_config_id=EXISTING_CONFIG, audit_fails=True)

    result = pilot.ensure_direct_monitoring_config_for_target(
        conn, workspace_id=WS, target_id=TARGET, chain_network=BASE_CHAIN,
        creation_source='runtime_reconcile',
    )

    # The existing row is a successful no-op preserve.
    assert result['config_id'] == EXISTING_CONFIG
    assert result['created'] is False
    # The audit write failed, but its savepoint rolled it back — the connection is NOT
    # left aborted, so the caller's SAVEPOINT RELEASE will succeed (transaction_error=false).
    assert conn.aborted is False, 'audit failure must not leave the transaction aborted'
    assert conn.release_failures == 0


def test_audit_failure_is_swallowed_and_does_not_raise(_audit_writes_sql):
    """Even when the audit write fails, the helper returns normally (never propagates
    a DB error that would fail the whole reconcile connection)."""
    conn = _SavepointConn(existing_config_id=EXISTING_CONFIG, audit_fails=True)
    # Must not raise.
    result = pilot.ensure_direct_monitoring_config_for_target(
        conn, workspace_id=WS, target_id=TARGET, chain_network=BASE_CHAIN,
    )
    assert result['created'] is False


def test_healthy_audit_still_preserves_and_stays_clean(_audit_writes_sql):
    conn = _SavepointConn(existing_config_id=EXISTING_CONFIG, audit_fails=False)
    result = pilot.ensure_direct_monitoring_config_for_target(
        conn, workspace_id=WS, target_id=TARGET, chain_network=BASE_CHAIN,
    )
    assert result['created'] is False
    assert conn.aborted is False


# ---------------------------------------------------------------------------
# 14. Original reconciliation SQL error is captured (safe category / SQLSTATE).
# ---------------------------------------------------------------------------

def test_original_reconcile_sql_error_is_captured_by_category():
    assert pilot._reconcile_target_error_category(_unique_violation()) == 'unique_violation'
    assert pilot._reconcile_target_error_category(_in_failed_txn()) == 'in_failed_sql_transaction'
    # An unmapped error still yields a safe, secret-free SQLSTATE-derived label.
    assert pilot._reconcile_target_error_category(_FakeDBError('SomethingElse', '42P01')).startswith(
        'db_error_sqlstate_'
    ) or pilot._reconcile_target_error_category(_FakeDBError('SomethingElse', '42P01')) == 'undefined_table'
