from __future__ import annotations

import json
from collections import defaultdict

import pytest

from services.api.app.domains import alert_delivery


class Result:
    def __init__(self, rows=None):
        self.rows = rows or []

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self):
        self.outbox = {}
        self.executed = []

    def commit(self):
        self.executed.append(('COMMIT', ()))

    def execute(self, sql, params=()):
        normalized = ' '.join(sql.split())
        self.executed.append((normalized, params))
        if normalized.startswith('INSERT INTO alert_event_outbox'):
            event_id, workspace_id, alert_id, event_type, payload, key, max_attempts = params
            existing = next((row for row in self.outbox.values() if row['idempotency_key'] == key), None)
            if existing:
                return Result([{'id': existing['id']}])
            self.outbox[event_id] = {
                'id': event_id, 'workspace_id': workspace_id, 'alert_id': alert_id,
                'event_type': event_type, 'payload': json.loads(payload), 'idempotency_key': key,
                'status': 'pending', 'attempt_count': 0, 'max_attempts': max_attempts,
                'lease_owner': None,
            }
            return Result([{'id': event_id}])
        if normalized.startswith('UPDATE alert_event_outbox SET status = \'delivered\''):
            self.outbox[str(params[0])]['status'] = 'delivered'
            return Result()
        if normalized.startswith('SELECT attempt_count, max_attempts'):
            row = self.outbox.get(str(params[0]))
            return Result([row] if row else [])
        if normalized.startswith('UPDATE alert_event_outbox SET status = %s, attempt_count'):
            status, attempts, _error, event_id = params
            self.outbox[str(event_id)]['status'] = status
            self.outbox[str(event_id)]['attempt_count'] = attempts
            return Result()
        return Result()


class SharedRedisStreams:
    def __init__(self):
        self.streams = defaultdict(list)
        self.groups = {}
        self.pending = {}
        self.markers = {}
        self.operations = []
        self.fail_delivery_once = False

    def xadd(self, stream, fields, **_kwargs):
        event_id = f'{len(self.streams[stream]) + 1}-0'
        self.streams[stream].append((event_id, dict(fields)))
        self.operations.append(('xadd', stream, event_id))
        return event_id

    def xgroup_create(self, stream, group, id='0-0', mkstream=False):
        key = (stream, group)
        if key in self.groups:
            raise RuntimeError('BUSYGROUP Consumer Group name already exists')
        self.groups[key] = 0
        if mkstream:
            self.streams[stream]

    def xreadgroup(self, group, consumer, streams, count, block):
        stream = next(iter(streams))
        key = (stream, group)
        index = self.groups[key]
        batch = self.streams[stream][index:index + count]
        self.groups[key] += len(batch)
        for message_id, fields in batch:
            self.pending[(stream, group, message_id)] = (consumer, fields)
        return [(stream, batch)] if batch else []

    def xautoclaim(self, stream, group, consumer, min_idle, start, count):
        claimed = []
        for (pending_stream, pending_group, message_id), (_owner, fields) in list(self.pending.items()):
            if pending_stream == stream and pending_group == group:
                self.pending[(pending_stream, pending_group, message_id)] = (consumer, fields)
                claimed.append((message_id, fields))
                if len(claimed) >= count:
                    break
        return ('0-0', claimed, [])

    def eval(self, script, key_count, marker, workspace_stream, max_length, payload, outbox_id):
        self.operations.append(('process', outbox_id))
        if self.fail_delivery_once:
            self.fail_delivery_once = False
            raise ConnectionError('worker terminated during processing')
        if marker in self.markers:
            return self.markers[marker]
        event_id = self.xadd(workspace_stream, {'payload': payload, 'outbox_id': outbox_id})
        self.markers[marker] = event_id
        return event_id

    def xack(self, stream, group, message_id):
        self.operations.append(('ack', message_id))
        self.pending.pop((stream, group, message_id), None)
        return 1


def _seed_event(connection, redis_client, *, outbox_id='outbox-1'):
    connection.outbox[outbox_id] = {
        'id': outbox_id, 'attempt_count': 0, 'max_attempts': 3, 'status': 'published',
    }
    redis_client.xadd(alert_delivery.BUS_STREAM, {
        'outbox_id': outbox_id,
        'workspace_id': 'workspace-a',
        'event_type': 'alert.created',
        'idempotency_key': f'alert:{outbox_id}:created',
        'payload': json.dumps({'type': 'alert', 'alert_id': outbox_id}),
    })


def test_enqueue_is_transactional_and_idempotent_without_touching_redis(monkeypatch):
    connection = FakeConnection()
    monkeypatch.setattr(alert_delivery.alert_stream, '_sync_client', None)

    first = alert_delivery.enqueue_alert_event(
        connection, workspace_id='workspace-a', alert_id='alert-a', event_type='alert.created',
        payload={'type': 'alert'}, idempotency_key='alert:alert-a:created',
    )
    second = alert_delivery.enqueue_alert_event(
        connection, workspace_id='workspace-a', alert_id='alert-a', event_type='alert.created',
        payload={'type': 'alert'}, idempotency_key='alert:alert-a:created',
    )

    assert first == second
    assert len(connection.outbox) == 1
    assert connection.outbox[first]['status'] == 'pending'


def test_named_consumer_group_distributes_once_across_instances(monkeypatch):
    connection = FakeConnection()
    shared = SharedRedisStreams()
    _seed_event(connection, shared)
    monkeypatch.setattr(alert_delivery, '_heartbeat', lambda *args, **kwargs: None)

    first = alert_delivery.consume_bus_batch(connection, consumer_id='api-1', client=shared)
    second = alert_delivery.consume_bus_batch(connection, consumer_id='api-2', client=shared)

    assert first['delivered'] == 1
    assert second['delivered'] == 0
    assert connection.outbox['outbox-1']['status'] == 'delivered'
    assert len(shared.streams[alert_delivery.alert_stream.stream_key('workspace-a')]) == 1
    assert shared.operations.index(('process', 'outbox-1')) < shared.operations.index(('ack', '1-0'))


def test_restart_recovers_unacked_message_and_deduplicates_delivery(monkeypatch):
    connection = FakeConnection()
    shared = SharedRedisStreams()
    _seed_event(connection, shared)
    shared.fail_delivery_once = True
    monkeypatch.setattr(alert_delivery, '_heartbeat', lambda *args, **kwargs: None)

    failed = alert_delivery.consume_bus_batch(connection, consumer_id='terminated-worker', client=shared)
    assert failed['retried'] == 1
    assert shared.pending
    assert not any(operation[0] == 'ack' for operation in shared.operations)

    recovered = alert_delivery.consume_bus_batch(connection, consumer_id='replacement-worker', client=shared)

    assert recovered['recovered'] == 1
    assert recovered['delivered'] == 1
    assert not shared.pending
    assert len(shared.streams[alert_delivery.alert_stream.stream_key('workspace-a')]) == 1


def test_bounded_retry_moves_poison_event_to_dead_letter_and_acks(monkeypatch):
    connection = FakeConnection()
    shared = SharedRedisStreams()
    _seed_event(connection, shared)
    connection.outbox['outbox-1']['attempt_count'] = 2
    connection.outbox['outbox-1']['max_attempts'] = 3
    shared.fail_delivery_once = True
    monkeypatch.setattr(alert_delivery, '_heartbeat', lambda *args, **kwargs: None)

    result = alert_delivery.consume_bus_batch(connection, consumer_id='api-1', client=shared)

    assert result['dead_lettered'] == 1
    assert connection.outbox['outbox-1']['status'] == 'dead_letter'
    assert len(shared.streams[alert_delivery.DEAD_LETTER_STREAM]) == 1
    assert not shared.pending


def test_migration_defines_outbox_leases_idempotency_and_worker_state():
    migration = (alert_delivery.__file__.replace('app/domains/alert_delivery.py', 'migrations/0098_durable_alert_event_bus.sql'))
    content = open(migration, encoding='utf-8').read()

    for required in ('alert_event_outbox', 'idempotency_key', 'lease_expires_at', 'dead_letter', 'alert_event_worker_state', 'trg_alert_created_outbox'):
        assert required in content


def test_system_readiness_blocks_enterprise_when_delivery_workers_are_unavailable(monkeypatch):
    from services.api.app import main as api_main

    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda callback: callback())
    monkeypatch.setattr(api_main, 'get_workspace_readiness', lambda request: {
        'status': 'pass', 'enterprise_procurement_ready': True, 'checks': [],
        'dependency_checks': {}, 'blocking_failures': [], 'blocking_failure_reason_codes': [],
        'enterprise_procurement_blocking_reason_codes': [],
    })
    monkeypatch.setattr(api_main, 'alert_delivery_health', lambda: {
        'ready': False, 'bus': {'connected': True},
        'workers': {'outbox_publisher': True, 'stream_consumer': False},
        'outbox': {'pending': 2, 'published': 1, 'dead_letter': 0},
    })

    result = api_main.system_workspace_readiness(object())

    assert result['status'] == 'fail'
    assert result['enterprise_procurement_ready'] is False
    assert result['dependency_checks']['durable_alert_delivery']['pass'] is False
    assert 'durable_alert_delivery_unavailable' in result['blocking_failure_reason_codes']


def test_ops_monitoring_health_fails_enterprise_gate_when_event_bus_is_unavailable(monkeypatch):
    from services.api.app import main as api_main

    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda callback: callback())
    monkeypatch.setattr(api_main, 'get_monitoring_health', lambda: {'status': 'healthy', 'enterprise_ready': True})
    monkeypatch.setattr(api_main, 'alert_delivery_health', lambda: {
        'ready': False, 'bus': {'connected': False},
        'workers': {'outbox_publisher': True, 'stream_consumer': True},
        'outbox': {'pending': 0, 'published': 0, 'dead_letter': 0},
    })

    result = api_main.ops_monitoring_health()

    assert result['status'] == 'not_ready'
    assert result['enterprise_ready'] is False
    assert result['alert_delivery']['bus']['connected'] is False
