"""Onboarding SSE publishing (Screen 4 part 7).

Regression: the initial session_created / discovery_queued events were dropped with
onboarding_sse_publish_skipped reason=TypeError because a uuid.UUID id reached the
Redis JSON serializer, which cannot encode UUID/datetime. The canonical publisher
(publish_event) now coerces the whole payload to JSON-safe primitives, and
alert_stream.publish_onboarding hardens json.dumps with default=str.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from services.api.app import onboarding_agent as oa
from services.api.app.domains import alert_stream


class _FakeRedis:
    def __init__(self):
        self.xadds: list[tuple] = []

    def xadd(self, key, fields, maxlen=None, approximate=None):
        # Prove the payload is genuinely serializable by round-tripping it here.
        json.loads(fields['payload'])
        self.xadds.append((key, fields))
        return b'1-0'


def _events(caplog):
    return '\n'.join(r.getMessage() for r in caplog.records)


def _run_publish(monkeypatch, caplog, event_type, extra=None, *, session_id, workspace_id):
    redis = _FakeRedis()
    monkeypatch.setattr(alert_stream, '_get_sync_client', lambda: redis)
    with caplog.at_level(logging.INFO):
        oa.publish_event(workspace_id, session_id, event_type, extra)
    return redis


def test_session_created_publishes_with_uuid_ids(monkeypatch, caplog):
    """session_created must publish even when workspace_id / session_id are UUID
    objects — never dropped with onboarding_sse_publish_skipped reason=TypeError."""
    ws = uuid.uuid4()
    session = uuid.uuid4()
    redis = _run_publish(monkeypatch, caplog, 'session_created', session_id=session, workspace_id=ws)

    text = _events(caplog)
    assert 'onboarding_sse_publish_skipped' not in text, text
    assert len(redis.xadds) == 1
    _key, fields = redis.xadds[0]
    payload = json.loads(fields['payload'])
    assert payload['event_type'] == 'session_created'
    assert payload['session_id'] == str(session)
    assert payload['workspace_id'] == str(ws)


def test_discovery_queued_publishes_with_uuid_run_id(monkeypatch, caplog):
    """discovery_queued carries a run id in extra; a UUID run id must not break the
    publish (this was the second dropped event)."""
    ws = uuid.uuid4()
    session = uuid.uuid4()
    run_id = uuid.uuid4()
    redis = _run_publish(
        monkeypatch, caplog, 'discovery_queued', {'run_id': run_id},
        session_id=session, workspace_id=ws,
    )

    text = _events(caplog)
    assert 'onboarding_sse_publish_skipped' not in text, text
    assert len(redis.xadds) == 1
    payload = json.loads(redis.xadds[0][1]['payload'])
    assert payload['event_type'] == 'discovery_queued'
    assert payload['run_id'] == str(run_id)


def test_all_canonical_events_serialize_and_publish(monkeypatch, caplog):
    """The single canonical publisher handles every onboarding event, including
    datetime/UUID payloads, without a serialization skip."""
    ws = uuid.uuid4()
    session = uuid.uuid4()
    cases = [
        ('session_created', None),
        ('discovery_queued', {'run_id': uuid.uuid4()}),
        ('step_update', {'step_key': 'validate_inputs', 'status': 'completed'}),
        ('discovery_failed', {'error_code': 'all_rpc_providers_unavailable'}),
        ('discovery_completed', {'finished_at': datetime(2026, 7, 18, tzinfo=timezone.utc)}),
    ]
    redis = _FakeRedis()
    monkeypatch.setattr(alert_stream, '_get_sync_client', lambda: redis)
    with caplog.at_level(logging.INFO):
        for event_type, extra in cases:
            oa.publish_event(ws, session, event_type, extra)

    assert 'onboarding_sse_publish_skipped' not in _events(caplog)
    published = [json.loads(f['payload'])['event_type'] for _k, f in redis.xadds]
    assert published == [c[0] for c in cases]


def test_publish_onboarding_serializer_tolerates_uuid_datetime(monkeypatch):
    """Defense in depth: publish_onboarding's json.dumps must not raise on a stray
    UUID/datetime even if a future caller forgets to pre-serialize."""
    redis = _FakeRedis()
    monkeypatch.setattr(alert_stream, '_get_sync_client', lambda: redis)
    alert_stream.publish_onboarding(
        str(uuid.uuid4()),
        {'event_type': 'session_created', 'session_id': uuid.uuid4(),
         'at': datetime(2026, 7, 18, tzinfo=timezone.utc)},
    )
    assert len(redis.xadds) == 1
