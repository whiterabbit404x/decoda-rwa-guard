from __future__ import annotations

import hashlib
import hmac
import json
from contextlib import contextmanager
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.api.app import pilot


class FakeResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class FakeConnection:
    def __init__(self):
        self.events: list[tuple[str, tuple]] = []

    def execute(self, query, params=()):
        self.events.append((query, params))
        if 'FROM billing_events WHERE provider_event_id' in query:
            return FakeResult(None)
        return FakeResult(None)

    def commit(self):
        return None


@contextmanager
def fake_pg_connection(connection: FakeConnection):
    yield connection


def signed_payload(secret: str, payload: dict[str, object]) -> tuple[str, str, bytes]:
    raw = json.dumps(payload, separators=(',', ':')).encode('utf-8')
    timestamp = '1717000000'
    signature = hmac.new(secret.encode('utf-8'), f'{timestamp}:{raw.decode("utf-8")}'.encode('utf-8'), hashlib.sha256).hexdigest()
    return signature, timestamp, raw


def main() -> None:
    secret = 'pdl_whsec_local'
    payload = {
        'event_id': 'evt_signup_subscribed',
        'event_type': 'subscription.created',
        'data': {
            'id': 'sub_local_123',
            'status': 'active',
            'transaction_id': 'txn_local_123',
            'customer_id': 'ctm_local_123',
            'custom_data': {'workspace_id': 'ws_local_1', 'plan_key': 'pro'},
        },
    }
    signature, timestamp, raw = signed_payload(secret, payload)
    connection = FakeConnection()

    pilot.os.environ['PADDLE_WEBHOOK_SECRET'] = secret
    pilot.require_live_mode = lambda: None
    pilot.ensure_pilot_schema = lambda connection: None
    pilot.pg_connection = lambda: fake_pg_connection(connection)

    result = pilot.process_paddle_webhook(payload, signature_header=signature, timestamp_header=timestamp, raw_body=raw)

    print('Simulated flow: signup -> workspace -> subscribe -> webhook received -> active access')
    print(json.dumps(result, indent=2))
    print(f'SQL statements executed: {len(connection.events)}')


if __name__ == '__main__':
    main()
