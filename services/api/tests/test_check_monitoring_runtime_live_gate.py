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
        'evidence_source': 'live',
        'freshness_status': 'fresh',
        'configured_systems': 2,
        'reporting_systems': 2,
        'valid_protected_assets': 2,
        'linked_monitored_systems': 2,
        'enabled_configs': 2,
        'valid_link_count': 2,
        'last_telemetry_at': ts,
        'last_coverage_telemetry_at': ts,
        'field_reason_codes': {
            'reporting_systems': [],
            'last_telemetry_at': [],
        },
        'count_reason_codes': {},
        'workspace_monitoring_summary': {
            'monitoring_mode': 'live',
            'runtime_status': 'healthy',
            'evidence_source': 'live',
            'freshness_status': 'fresh',
            'configured_systems': 2,
            'reporting_systems': 2,
            'valid_protected_assets': 2,
            'linked_monitored_systems': 2,
            'enabled_configs': 2,
            'valid_link_count': 2,
            'last_telemetry_at': ts,
            'last_coverage_telemetry_at': ts,
            'field_reason_codes': {
                'reporting_systems': [],
                'last_telemetry_at': [],
            },
            'count_reason_codes': {},
        },
    }


def test_runtime_live_gate_passes_for_fresh_live_payload(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(check_monitoring_runtime_live_gate, 'urlopen', lambda *_a, **_k: _FakeResponse(_healthy_payload()))
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
        'evidence_source': 'live',
        'freshness_status': 'unavailable',
        'status_reason': 'runtime_status_degraded:database_error',
        'configuration_reason': 'runtime_status_unavailable',
        'configured_systems': 2,
        'reporting_systems': 0,
        'valid_protected_assets': 0,
        'linked_monitored_systems': 0,
        'enabled_configs': 0,
        'valid_link_count': 0,
        'last_telemetry_at': stale,
        'last_coverage_telemetry_at': None,
        'field_reason_codes': {'reporting_systems': ['query_failure']},
        'count_reason_codes': {'configured_systems': 'schema_drift'},
        'workspace_monitoring_summary': {},
    }
    monkeypatch.setattr(check_monitoring_runtime_live_gate, 'urlopen', lambda *_a, **_k: _FakeResponse(runtime_payload))

    code = check_monitoring_runtime_live_gate.main()
    assert code == 2

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload['ok'] is False
    failures = '\n'.join(payload['failures'])
    assert 'workspace_id/workspace_slug must both be non-null' in failures
    assert 'status_reason indicates degraded runtime' in failures
    assert 'configuration_reason=runtime_status_unavailable' in failures
    assert 'freshness_status=unavailable while runtime claims live/hybrid mode' in failures
    assert 'query_failure markers' in failures
    assert 'schema_drift markers' in failures


def test_runtime_live_gate_fails_when_evidence_source_is_not_live(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    payload = _healthy_payload()
    payload['evidence_source'] = 'simulator'
    payload['workspace_monitoring_summary']['evidence_source'] = 'simulator'  # type: ignore[index]
    monkeypatch.setattr(check_monitoring_runtime_live_gate, 'urlopen', lambda *_a, **_k: _FakeResponse(payload))

    code = check_monitoring_runtime_live_gate.main()
    assert code == 2

    out = capsys.readouterr().out
    body = json.loads(out)
    assert body['ok'] is False
    assert any('evidence_source must be live' in failure for failure in body['failures'])


def test_runtime_live_gate_fails_when_coverage_telemetry_is_stale(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    payload = _healthy_payload()
    payload['last_coverage_telemetry_at'] = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    payload['workspace_monitoring_summary']['last_coverage_telemetry_at'] = payload['last_coverage_telemetry_at']  # type: ignore[index]
    monkeypatch.setattr(check_monitoring_runtime_live_gate, 'urlopen', lambda *_a, **_k: _FakeResponse(payload))

    code = check_monitoring_runtime_live_gate.main()
    assert code == 2

    out = capsys.readouterr().out
    body = json.loads(out)
    assert body['ok'] is False
    assert any('last_coverage_telemetry_at is stale' in failure for failure in body['failures'])
