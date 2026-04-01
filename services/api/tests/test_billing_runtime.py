from __future__ import annotations

import hashlib
import hmac
import json
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
    def __init__(self, *, event_exists=False):
        self.event_exists = event_exists
        self.commit_called = False
        self.executed: list[tuple[str, tuple]] = []

    def execute(self, query, params=()):
        self.executed.append((query, params))
        if 'FROM billing_events WHERE provider_event_id' in query:
            return FakeResult({'processing_status': 'processed'} if self.event_exists else None)
        if 'FROM plan_entitlements WHERE plan_key' in query:
            return FakeResult({'plan_key': 'pro', 'trial_days': 14})
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


def _paddle_headers(secret: str, payload: dict[str, object]) -> tuple[str, str, bytes]:
    raw = json.dumps(payload, separators=(',', ':')).encode('utf-8')
    timestamp = '1717000000'
    signature = hmac.new(secret.encode('utf-8'), f'{timestamp}:{raw.decode("utf-8")}'.encode('utf-8'), hashlib.sha256).hexdigest()
    return signature, timestamp, raw


def test_paddle_signature_validation_rejects_invalid_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('PADDLE_WEBHOOK_SECRET', 'pdl_secret')
    with pytest.raises(HTTPException) as exc:
        pilot.verify_paddle_webhook_signature(raw_body=b'{}', signature_header='wrong', timestamp_header='1717')
    assert exc.value.status_code == 400


def test_paddle_webhook_replay_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = FakeConnection(event_exists=True)
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda conn: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: fake_pg_connection(connection))
    monkeypatch.setenv('PADDLE_WEBHOOK_SECRET', 'pdl_secret')

    payload = {'event_id': 'evt_1', 'event_type': 'subscription.updated', 'data': {'id': 'sub_1'}}
    signature, timestamp, raw = _paddle_headers('pdl_secret', payload)
    response = pilot.process_paddle_webhook(payload, signature_header=signature, timestamp_header=timestamp, raw_body=raw)

    assert response['duplicate'] is True
    assert connection.commit_called is False


def test_paddle_webhook_reconciliation_upserts_subscription_customer_and_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = FakeConnection(event_exists=False)
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda conn: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: fake_pg_connection(connection))
    monkeypatch.setenv('PADDLE_WEBHOOK_SECRET', 'pdl_secret')

    payload = {
        'event_id': 'evt_2',
        'event_type': 'subscription.created',
        'data': {
            'id': 'sub_123',
            'status': 'active',
            'transaction_id': 'txn_123',
            'customer_id': 'ctm_123',
            'custom_data': {'workspace_id': 'ws-1', 'plan_key': 'pro'},
        },
    }
    signature, timestamp, raw = _paddle_headers('pdl_secret', payload)
    response = pilot.process_paddle_webhook(payload, signature_header=signature, timestamp_header=timestamp, raw_body=raw)

    assert response['duplicate'] is False
    assert connection.commit_called is True
    joined = '\n'.join(query for query, _ in connection.executed)
    assert 'INSERT INTO billing_customers' in joined
    assert 'provider_transaction_id' in joined


def test_checkout_session_returns_paddle_url(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = FakeConnection(event_exists=False)
    _patch_auth(monkeypatch)
    monkeypatch.setenv('BILLING_PROVIDER', 'paddle')
    monkeypatch.setenv('PADDLE_API_KEY', 'pdl_api_123')
    monkeypatch.setenv('PADDLE_WEBHOOK_SECRET', 'pdl_whsec_123')
    monkeypatch.setenv('PADDLE_PRICE_ID_PRO', 'pri_123')
    monkeypatch.setattr(pilot, 'pg_connection', lambda: fake_pg_connection(connection))
    monkeypatch.setattr(pilot, 'log_audit', lambda *args, **kwargs: None)

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"data":{"id":"che_123","url":"https://checkout.paddle.test/session"}}'

    monkeypatch.setattr(pilot, 'urlopen', lambda *args, **kwargs: _Resp())

    response = pilot.create_checkout_session({'plan_key': 'pro'}, SimpleNamespace(headers={}))

    assert response['provider'] == 'paddle'
    assert response['session_id'] == 'che_123'
    assert response['checkout_url'].startswith('https://checkout.paddle.test/')


def test_subscription_status_mapping_handles_pause_and_cancel() -> None:
    assert pilot._paddle_to_subscription_status('subscription.paused', 'paused') == 'past_due'
    assert pilot._paddle_to_subscription_status('subscription.canceled', 'canceled') == 'canceled'
