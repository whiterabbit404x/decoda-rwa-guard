from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from services.api.scripts import check_monitoring_runtime_live_gate


class _FakeResponse:
    def __init__(self, payload: dict[str, object], status: int = 200):
        self.status = status
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode('utf-8')

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None


def _healthy_payload() -> dict[str, object]:
    now = datetime.now(timezone.utc)
    ts = now.isoformat()
    return {
        'workspace_id': 'ws-1',
        'workspace_slug': 'workspace-1',
        'monitoring_mode': 'live',
        'runtime_status': 'healthy',
        'monitoring_status': 'live',
        'evidence_source': 'live',
        'evidence_source_summary': 'live',
        'freshness_status': 'fresh',
        'telemetry_freshness': 'fresh',
        'configured_systems': 2,
        'reporting_systems': 2,
        'valid_protected_assets': 2,
        'linked_monitored_systems': 2,
        'enabled_configs': 2,
        'valid_link_count': 2,
        'last_poll_at': ts,
        'last_heartbeat_at': ts,
        'last_telemetry_at': ts,
        'last_coverage_telemetry_at': ts,
        'guard_flags': [],
        'contradiction_flags': [],
        'db_failure_reason': None,
        'field_reason_codes': {
            'reporting_systems': [],
            'last_telemetry_at': [],
        },
        'count_reason_codes': {},
        'workspace_monitoring_summary': {
            'runtime_status': 'healthy',
            'monitoring_status': 'live',
            'evidence_source_summary': 'live',
            'telemetry_freshness': 'fresh',
            'reporting_systems_count': 2,
            'monitored_systems_count': 2,
            'last_poll_at': ts,
            'last_heartbeat_at': ts,
            'last_telemetry_at': ts,
            'guard_flags': [],
            'contradiction_flags': [],
            'db_failure_reason': None,
        },
    }


def _install_reconcile_then_runtime(monkeypatch: pytest.MonkeyPatch, *, reconcile_payload: dict[str, object], runtime_payload: dict[str, object]) -> None:
    responses = iter([
        _FakeResponse(reconcile_payload),
        _FakeResponse(runtime_payload),
    ])
    monkeypatch.setattr(check_monitoring_runtime_live_gate, 'urlopen', lambda *_a, **_k: next(responses))


def test_runtime_live_gate_passes_for_fresh_live_payload(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _install_reconcile_then_runtime(
        monkeypatch,
        reconcile_payload={
            'reconcile': {'created_or_updated': 2},
            'monitored_systems_count': 2,
        },
        runtime_payload=_healthy_payload(),
    )
    monkeypatch.setenv('API_URL', 'http://127.0.0.1:8000')

    code = check_monitoring_runtime_live_gate.main()
    assert code == 0

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload['ok'] is True
    assert payload['failures'] == []


def test_runtime_live_gate_fails_on_required_degraded_and_unavailable_conditions(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    stale = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    runtime_payload = {
        'workspace_id': None,
        'workspace_slug': None,
        'monitoring_mode': 'live',
        'runtime_status': 'degraded',
        'monitoring_status': 'limited',
        'evidence_source': 'live',
        'evidence_source_summary': 'simulator',
        'freshness_status': 'unavailable',
        'telemetry_freshness': 'stale',
        'status_reason': 'runtime_status_degraded:database_error',
        'configuration_reason': 'runtime_status_unavailable',
        'configured_systems': 2,
        'reporting_systems': 0,
        'valid_protected_assets': 0,
        'linked_monitored_systems': 0,
        'enabled_configs': 0,
        'valid_link_count': 0,
        'last_poll_at': None,
        'last_heartbeat_at': None,
        'last_telemetry_at': stale,
        'last_coverage_telemetry_at': None,
        'guard_flags': ['live_monitoring_without_reporting_systems'],
        'contradiction_flags': ['heartbeat_without_telemetry_timestamp'],
        'db_failure_reason': 'Monitoring persistence unavailable',
        'field_reason_codes': {'reporting_systems': ['query_failure']},
        'count_reason_codes': {'configured_systems': 'schema_drift'},
        'workspace_monitoring_summary': {},
    }
    _install_reconcile_then_runtime(
        monkeypatch,
        reconcile_payload={'reconcile': {'created_or_updated': 0}, 'monitored_systems_count': 0},
        runtime_payload=runtime_payload,
    )

    code = check_monitoring_runtime_live_gate.main()
    assert code == 2

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload['ok'] is False
    failures = '\n'.join(payload['failures'])
    assert 'workspace_id/workspace_slug must both be non-null' in failures
    assert 'status_reason indicates degraded runtime' in failures
    assert 'configuration_reason=runtime_status_unavailable' in failures
    assert 'query_failure markers' in failures
    assert 'schema_drift markers' in failures
    assert 'monitoring_status must be live' in failures
    assert 'evidence_source_summary must be live' in failures
    assert 'telemetry_freshness must be fresh' in failures
    assert 'guard flags must be empty' in failures
    assert 'contradiction flags must be empty' in failures
    assert 'db_failure_reason must be null' in failures


def test_runtime_live_gate_fails_when_coverage_telemetry_is_stale(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    payload = _healthy_payload()
    payload['last_coverage_telemetry_at'] = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    _install_reconcile_then_runtime(
        monkeypatch,
        reconcile_payload={'reconcile': {'created_or_updated': 1}, 'monitored_systems_count': 2},
        runtime_payload=payload,
    )

    code = check_monitoring_runtime_live_gate.main()
    assert code == 2

    out = capsys.readouterr().out
    body = json.loads(out)
    assert body['ok'] is False
    assert any('last_coverage_telemetry_at is stale' in failure for failure in body['failures'])


def test_runtime_live_gate_transitions_same_workspace_from_unconfigured_to_live_after_reconcile(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    first_runtime = {
        **_healthy_payload(),
        'workspace_id': 'ws-shared',
        'workspace_slug': 'workspace-shared',
        'monitoring_status': 'limited',
        'evidence_source_summary': 'none',
        'telemetry_freshness': 'unavailable',
        'configured_systems': 0,
        'reporting_systems': 0,
        'valid_protected_assets': 0,
        'linked_monitored_systems': 0,
        'enabled_configs': 0,
        'valid_link_count': 0,
        'guard_flags': ['workspace_unconfigured_with_coverage'],
        'contradiction_flags': ['workspace_unconfigured_with_coverage'],
    }
    second_runtime = {
        **_healthy_payload(),
        'workspace_id': 'ws-shared',
        'workspace_slug': 'workspace-shared',
    }

    first_responses = iter([
        _FakeResponse({'reconcile': {'created_or_updated': 0}, 'monitored_systems_count': 0}),
        _FakeResponse(first_runtime),
    ])
    monkeypatch.setattr(check_monitoring_runtime_live_gate, 'urlopen', lambda *_a, **_k: next(first_responses))
    first_code = check_monitoring_runtime_live_gate.main()
    assert first_code == 2
    _ = capsys.readouterr()

    second_responses = iter([
        _FakeResponse({'reconcile': {'created_or_updated': 2}, 'monitored_systems_count': 2}),
        _FakeResponse(second_runtime),
    ])
    monkeypatch.setattr(check_monitoring_runtime_live_gate, 'urlopen', lambda *_a, **_k: next(second_responses))
    second_code = check_monitoring_runtime_live_gate.main()
    assert second_code == 0

    final_payload = json.loads(capsys.readouterr().out)
    assert final_payload['ok'] is True
    assert final_payload['workspace_id'] == 'ws-shared'
    assert final_payload['monitoring_status'] == 'live'
