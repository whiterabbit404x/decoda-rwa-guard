from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from services.api.app import pilot


class FakeResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class FakeConnection:
    def __init__(self, *, event_exists=False, customer_exists=False):
        self.event_exists = event_exists
        self.customer_exists = customer_exists
        self.commit_called = False
        self.executed: list[tuple[str, tuple]] = []

    def execute(self, query, params=()):
        self.executed.append((query, params))
        if 'FROM billing_events WHERE provider_event_id' in query:
            return FakeResult({'processing_status': 'processed'} if self.event_exists else None)
        if 'FROM billing_customers WHERE workspace_id' in query:
            return FakeResult({'provider_customer_id': 'cus_123'} if self.customer_exists else None)
        if 'FROM plan_entitlements WHERE plan_key' in query:
            return FakeResult({'plan_key': 'pro', 'trial_days': 14, 'stripe_price_id': 'price_123'})
        return FakeResult(None)

    def commit(self):
        self.commit_called = True


@contextmanager
def fake_pg_connection(connection: FakeConnection):
    yield connection


def _patch_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda connection: None)
    monkeypatch.setattr(
        pilot,
        '_require_workspace_admin',
        lambda connection, request: ({'id': 'user-1'}, {'workspace_id': 'ws-1', 'workspace': {'id': 'ws-1', 'name': 'Acme'}}),
    )


def test_webhook_signature_validation_rejects_invalid_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('STRIPE_WEBHOOK_SECRET', 'whsec_expected')
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)

    with pytest.raises(HTTPException) as exc:
        pilot.process_stripe_webhook({'id': 'evt_1', 'type': 'checkout.session.completed'}, signature_header='wrong')

    assert exc.value.status_code == 400
    assert 'Invalid webhook signature.' in str(exc.value.detail)


def test_webhook_replay_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = FakeConnection(event_exists=True)
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda conn: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: fake_pg_connection(connection))

    response = pilot.process_stripe_webhook({'id': 'evt_1', 'type': 'checkout.session.completed'}, signature_header=None)

    assert response['duplicate'] is True
    assert response['status'] == 'processed'
    assert connection.commit_called is False


def test_webhook_reconciliation_upserts_subscription_and_customer(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = FakeConnection(event_exists=False)
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda conn: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: fake_pg_connection(connection))

    payload = {
        'id': 'evt_2',
        'type': 'checkout.session.completed',
        'data': {'object': {'customer': 'cus_123', 'subscription': 'sub_123', 'metadata': {'workspace_id': 'ws-1', 'plan_key': 'pro'}}},
    }
    response = pilot.process_stripe_webhook(payload, signature_header=None)

    assert response['duplicate'] is False
    assert connection.commit_called is True
    joined = '\n'.join(query for query, _ in connection.executed)
    assert 'INSERT INTO billing_customers' in joined
    assert 'INSERT INTO billing_subscriptions' in joined


def test_portal_session_fails_when_customer_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = FakeConnection(customer_exists=False)
    _patch_auth(monkeypatch)
    monkeypatch.setenv('STRIPE_SECRET_KEY', 'sk_test_123')
    monkeypatch.setattr(pilot, 'pg_connection', lambda: fake_pg_connection(connection))

    with pytest.raises(HTTPException) as exc:
        pilot.create_portal_session(SimpleNamespace(headers={}))

    assert exc.value.status_code == 404
    assert 'billing customer' in str(exc.value.detail)


def test_checkout_session_returns_stripe_url(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = FakeConnection(customer_exists=True)
    _patch_auth(monkeypatch)
    monkeypatch.setenv('STRIPE_SECRET_KEY', 'sk_test_123')
    monkeypatch.setattr(pilot, 'pg_connection', lambda: fake_pg_connection(connection))
    monkeypatch.setattr(pilot, 'log_audit', lambda *args, **kwargs: None)

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"id":"cs_123","url":"https://checkout.stripe.test/session"}'

    monkeypatch.setattr(pilot, 'urlopen', lambda *args, **kwargs: _Resp())

    response = pilot.create_checkout_session({'plan_key': 'pro'}, SimpleNamespace(headers={}))

    assert response['session_id'] == 'cs_123'
    assert response['checkout_url'].startswith('https://checkout.stripe.test/')
