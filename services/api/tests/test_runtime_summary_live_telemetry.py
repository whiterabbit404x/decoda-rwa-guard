"""
Tests verifying that the runtime summary correctly reads persisted
evm_rpc/rpc_polling/live telemetry_events rows, sets last_telemetry_at,
and renders LIMITED COVERAGE instead of OFFLINE when telemetry exists
but the full evidence chain is incomplete.

Root cause fixed:
- telemetry_kind was 'canonical_telemetry_events' (unrecognized by
  build_workspace_monitoring_summary) → last_telemetry_at stayed None.
- last_coverage_telemetry_at was not updated from canonical telemetry_events
  → coverage_fresh=False → evidence_source not 'live'.
- workspace_configured=False with no override → runtime_status='offline'.

After fix:
- telemetry_kind = 'coverage' for evm_rpc/rpc_polling rows.
- last_coverage_telemetry_at falls back to canonical_last_telemetry_at.
- workspace_configured overridden to True when canonical telemetry is recent.
- live_evidence_ready requires the full telemetry→detection→alert→incident→
  response→evidence chain.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.api.app import monitoring_runner


# ---------------------------------------------------------------------------
# Shared test infrastructure
# ---------------------------------------------------------------------------

NOW = datetime.now(timezone.utc)
WORKSPACE_ID = '00000000-0000-0000-0000-000000000001'
TARGET_ID = '00000000-0000-0000-0000-000000000002'
TELEMETRY_WINDOW = 300  # seconds


class _Result:
    def __init__(self, rows=None, row=None):
        self._rows = list(rows or [])
        self._row = row

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._row


class _BaseConn:
    """Minimal connection mock. Subclass to inject specific telemetry state."""

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    # Default: return safe empty responses for all queries.
    def execute(self, q, p=None):
        qn = ' '.join(str(q).split())
        if 'FROM workspaces' in qn and 'slug' in qn:
            return _Result(row={'id': WORKSPACE_ID, 'slug': 'ws'})
        if 'COUNT(*)' in qn:
            return _Result(row={'c': 0})
        if 'MAX(' in qn:
            return _Result(row={'ts': None})
        if 'SELECT' in qn:
            return _Result(rows=[], row={})
        return _Result(row={})


class _LiveTelemetryConn(_BaseConn):
    """
    Returns recent live evm_rpc/rpc_polling rows from telemetry_events.
    Mimics the state where _persist_live_coverage_telemetry has run but
    monitoring_event_receipts and monitored_systems coverage columns
    have not yet been updated (the failing scenario before the fix).

    Optionally injects detection/alert/incident/response/evidence counts
    to test live_evidence_ready.
    """

    def __init__(
        self,
        *,
        telemetry_age_seconds: int = 5,
        detections: int = 0,
        alerts: int = 0,
        incidents: int = 0,
        response_actions: int = 0,
        evidence: int = 0,
    ):
        self.telemetry_ts = NOW - timedelta(seconds=telemetry_age_seconds)
        self._counts = {
            'detections': detections,
            'alerts': alerts,
            'incidents': incidents,
            'response_actions': response_actions,
            'evidence': evidence,
        }

    def execute(self, q, p=None):
        qn = ' '.join(str(q).split())

        # Workspace lookup
        if 'FROM workspaces' in qn and 'slug' in qn:
            return _Result(row={'id': WORKSPACE_ID, 'slug': 'ws'})

        # canonical_last_telemetry_at query (the one we're fixing)
        if (
            'FROM telemetry_events' in qn
            and 'MAX(observed_at) AS ts' in qn
            and "evidence_source = 'live'" in qn
        ):
            return _Result(row={'ts': self.telemetry_ts})

        # canonical_reporting_systems: target has recent telemetry
        if (
            'SELECT DISTINCT te.target_id' in qn
            and 'FROM telemetry_events te' in qn
        ):
            return _Result(rows=[{'target_id': TARGET_ID}])

        # Monitored systems: one enabled row (no coverage timestamp set yet)
        if 'FROM monitored_systems' in qn:
            return _Result(rows=[{
                'id': 's1',
                'target_id': TARGET_ID,
                'asset_id': 'a1',
                'is_enabled': True,
                'last_heartbeat': NOW,
                'last_event_at': None,
                'last_coverage_telemetry_at': None,  # intentionally null
            }])

        # No monitoring_event_receipts (legacy coverage path empty)
        if 'FROM monitoring_event_receipts' in qn:
            return _Result(rows=[])

        # No target_coverage_records
        if 'FROM target_coverage_records' in qn:
            return _Result(rows=[])

        # Detection / alert / incident counts
        if 'FROM response_actions' in qn and 'COUNT' in qn:
            return _Result(row={'c': self._counts['response_actions']})
        if 'FROM evidence' in qn and 'COUNT' in qn:
            return _Result(row={'c': self._counts['evidence']})
        if 'FROM detections' in qn and 'COUNT' in qn:
            return _Result(row={'c': self._counts['detections']})
        if 'alerts' in qn.lower() and 'COUNT' in qn and 'open' in qn.lower():
            return _Result(row={'c': self._counts['alerts']})
        if 'incidents' in qn.lower() and 'COUNT' in qn:
            return _Result(row={'c': self._counts['incidents']})

        # Default: empty / zero
        if 'COUNT(*)' in qn or 'COUNT(' in qn:
            return _Result(row={'c': 0})
        if 'MAX(' in qn:
            return _Result(row={'ts': None})
        if 'SELECT' in qn:
            return _Result(rows=[], row={})
        return _Result(row={})


def _get_payload(monkeypatch, conn: _BaseConn) -> dict:
    monkeypatch.setattr(
        monitoring_runner,
        'resolve_workspace_context_for_request',
        lambda *_a, **_k: (
            {'id': 'u'},
            {'workspace_id': WORKSPACE_ID, 'workspace': {'slug': 'ws'}},
            True,
        ),
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(
        monitoring_runner,
        'ensure_monitoring_runtime_schema_capabilities',
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: conn)
    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'worker_running': True, 'source_type': 'polling', 'ingestion_mode': 'live'},
    )
    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monitoring_runner.RUNTIME_STATUS_WORKSPACE_CACHE.clear()
    monitoring_runner.RUNTIME_STATUS_SUMMARY_CACHE.clear()
    return monitoring_runner.monitoring_runtime_status()


# ---------------------------------------------------------------------------
# 1. Runtime summary reads last_telemetry_at from canonical telemetry_events
# ---------------------------------------------------------------------------

def test_runtime_summary_last_telemetry_at_set_from_live_telemetry_events(monkeypatch):
    """last_telemetry_at must reflect MAX(observed_at) from telemetry_events
    when evm_rpc/rpc_polling/live rows exist."""
    conn = _LiveTelemetryConn(telemetry_age_seconds=5)
    payload = _get_payload(monkeypatch, conn)

    assert payload.get('last_telemetry_at') is not None, (
        'last_telemetry_at must be set from canonical telemetry_events rows; '
        f'got {payload.get("last_telemetry_at")!r}'
    )


def test_runtime_summary_last_telemetry_at_is_isoformat(monkeypatch):
    """last_telemetry_at must be a valid ISO timestamp string."""
    conn = _LiveTelemetryConn(telemetry_age_seconds=10)
    payload = _get_payload(monkeypatch, conn)

    ts = payload.get('last_telemetry_at')
    assert isinstance(ts, str), f'last_telemetry_at must be a string; got {ts!r}'
    try:
        datetime.fromisoformat(ts.replace('Z', '+00:00'))
    except ValueError as exc:
        pytest.fail(f'last_telemetry_at is not a valid ISO timestamp: {ts!r} — {exc}')


def test_runtime_summary_latest_live_telemetry_at_is_set(monkeypatch):
    """latest_live_telemetry_at must be set from canonical telemetry_events MAX."""
    conn = _LiveTelemetryConn(telemetry_age_seconds=3)
    payload = _get_payload(monkeypatch, conn)

    assert payload.get('latest_live_telemetry_at') is not None, (
        'latest_live_telemetry_at must be populated when telemetry_events has live rows'
    )


# ---------------------------------------------------------------------------
# 2. Freshness status is correct when telemetry is recent
# ---------------------------------------------------------------------------

def test_runtime_summary_freshness_not_unavailable_with_recent_telemetry(monkeypatch):
    """freshness_status must not be 'unavailable' when recent telemetry exists."""
    conn = _LiveTelemetryConn(telemetry_age_seconds=5)
    payload = _get_payload(monkeypatch, conn)

    summary = payload.get('workspace_monitoring_summary', {})
    freshness = summary.get('freshness_status') or payload.get('freshness_status')
    assert freshness != 'unavailable', (
        f'freshness_status must not be "unavailable" when telemetry_events has recent live rows; '
        f'got {freshness!r}'
    )


# ---------------------------------------------------------------------------
# 3. Runtime status is not OFFLINE when recent live telemetry exists
# ---------------------------------------------------------------------------

def test_runtime_status_not_offline_when_live_telemetry_exists(monkeypatch):
    """runtime_status must not be 'offline' when telemetry_events has recent
    evm_rpc/rpc_polling/live rows. The workspace may have incomplete
    configuration metadata, but monitoring IS running."""
    conn = _LiveTelemetryConn(telemetry_age_seconds=5)
    payload = _get_payload(monkeypatch, conn)

    summary = payload.get('workspace_monitoring_summary', {})
    runtime_status = summary.get('runtime_status') or payload.get('runtime_status')
    assert runtime_status != 'offline', (
        f'runtime_status must not be "offline" when telemetry_events has recent live rows; '
        f'got {runtime_status!r}. The banner should show LIMITED COVERAGE, not OFFLINE.'
    )


def test_runtime_monitoring_status_not_offline_when_live_telemetry_exists(monkeypatch):
    """monitoring_status must not be 'offline' when recent live telemetry exists."""
    conn = _LiveTelemetryConn(telemetry_age_seconds=5)
    payload = _get_payload(monkeypatch, conn)

    summary = payload.get('workspace_monitoring_summary', {})
    monitoring_status = summary.get('monitoring_status') or payload.get('monitoring_status')
    assert monitoring_status != 'offline', (
        f'monitoring_status must not be "offline" when telemetry_events has live rows; '
        f'got {monitoring_status!r}'
    )


# ---------------------------------------------------------------------------
# 4. live_evidence_ready is False without detection chain
# ---------------------------------------------------------------------------

def test_live_evidence_ready_false_with_telemetry_only(monkeypatch):
    """live_evidence_ready must be False when only telemetry_events rows exist.
    The full chain (detection→alert→incident→response→evidence) is required."""
    conn = _LiveTelemetryConn(telemetry_age_seconds=5)
    payload = _get_payload(monkeypatch, conn)

    assert payload.get('live_evidence_ready') is False, (
        f'live_evidence_ready must be False when only telemetry exists '
        f'(no detection/alert/incident/response/evidence); '
        f'got {payload.get("live_evidence_ready")!r}'
    )


def test_live_evidence_ready_false_with_partial_chain(monkeypatch):
    """live_evidence_ready must be False when telemetry + detection + alert exist
    but incident/response/evidence are missing."""
    conn = _LiveTelemetryConn(
        telemetry_age_seconds=5,
        detections=1,
        alerts=1,
        incidents=0,
        response_actions=0,
        evidence=0,
    )
    payload = _get_payload(monkeypatch, conn)

    assert payload.get('live_evidence_ready') is False, (
        f'live_evidence_ready must be False without incident/response/evidence; '
        f'got {payload.get("live_evidence_ready")!r}'
    )


def test_live_evidence_ready_false_without_telemetry(monkeypatch):
    """live_evidence_ready must be False when no telemetry_events rows exist,
    even if detection/alert/incident/response/evidence rows somehow exist."""
    conn = _BaseConn()
    payload = _get_payload(monkeypatch, conn)

    assert payload.get('live_evidence_ready') is False, (
        f'live_evidence_ready must be False when canonical_last_telemetry_at is None; '
        f'got {payload.get("live_evidence_ready")!r}'
    )


# ---------------------------------------------------------------------------
# 5. live_evidence_ready is True with the full chain
# ---------------------------------------------------------------------------

def test_live_evidence_ready_true_with_full_chain(monkeypatch):
    """live_evidence_ready must be True only when the full evidence chain is
    present: telemetry → detection → alert → incident → response → evidence."""
    conn = _LiveTelemetryConn(
        telemetry_age_seconds=5,
        detections=1,
        alerts=1,
        incidents=1,
        response_actions=1,
        evidence=1,
    )
    payload = _get_payload(monkeypatch, conn)

    assert payload.get('live_evidence_ready') is True, (
        f'live_evidence_ready must be True when the full chain exists; '
        f'got {payload.get("live_evidence_ready")!r}. '
        f'payload runtime_status={payload.get("runtime_status")!r}, '
        f'last_telemetry_at={payload.get("last_telemetry_at")!r}'
    )


# ---------------------------------------------------------------------------
# 6. workspace_monitoring_summary also carries live_evidence_ready
# ---------------------------------------------------------------------------

def test_workspace_monitoring_summary_has_live_evidence_ready(monkeypatch):
    """workspace_monitoring_summary dict must include live_evidence_ready."""
    conn = _LiveTelemetryConn(telemetry_age_seconds=5)
    payload = _get_payload(monkeypatch, conn)

    summary = payload.get('workspace_monitoring_summary', {})
    assert 'live_evidence_ready' in summary, (
        'workspace_monitoring_summary must include live_evidence_ready key'
    )
    assert summary['live_evidence_ready'] is False, (
        f'live_evidence_ready must be False in summary (telemetry only); '
        f'got {summary["live_evidence_ready"]!r}'
    )


def test_workspace_monitoring_summary_has_latest_live_telemetry_at(monkeypatch):
    """workspace_monitoring_summary dict must include latest_live_telemetry_at."""
    conn = _LiveTelemetryConn(telemetry_age_seconds=5)
    payload = _get_payload(monkeypatch, conn)

    summary = payload.get('workspace_monitoring_summary', {})
    assert 'latest_live_telemetry_at' in summary, (
        'workspace_monitoring_summary must include latest_live_telemetry_at key'
    )
    assert summary['latest_live_telemetry_at'] is not None, (
        'latest_live_telemetry_at in summary must be non-null when telemetry exists'
    )


# ---------------------------------------------------------------------------
# 7. Telemetry kind must be 'coverage' for evm_rpc rows (not a string that
#    build_workspace_monitoring_summary does not recognize).
# ---------------------------------------------------------------------------

def test_telemetry_kind_is_coverage_for_canonical_telemetry(monkeypatch):
    """telemetry_kind must be 'coverage' when canonical_last_telemetry_at is
    set from telemetry_events, so build_workspace_monitoring_summary uses it
    to populate last_telemetry_at."""
    import pathlib
    src = (pathlib.Path(__file__).parents[1] / 'app' / 'monitoring_runner.py').read_text(encoding='utf-8')
    # After the fix, 'canonical_telemetry_events' must be replaced with 'coverage'
    assert "'canonical_telemetry_events'" not in src, (
        "telemetry_kind must be 'coverage' (not 'canonical_telemetry_events') "
        "so build_workspace_monitoring_summary recognizes it and sets last_telemetry_at"
    )


# ---------------------------------------------------------------------------
# 8. Stale telemetry (older than telemetry_window) does not override OFFLINE
# ---------------------------------------------------------------------------

def test_stale_canonical_telemetry_does_not_prevent_offline(monkeypatch):
    """When telemetry_events has rows but they are older than telemetry_window,
    workspace_configured override must NOT apply."""
    conn = _LiveTelemetryConn(telemetry_age_seconds=3600)  # 1 hour ago
    payload = _get_payload(monkeypatch, conn)

    # Stale telemetry must not produce 'live' evidence source
    summary = payload.get('workspace_monitoring_summary', {})
    evidence_source = summary.get('evidence_source') or payload.get('evidence_source')
    assert evidence_source not in {'live_provider'}, (
        f'Stale telemetry (1 hour old) must not claim live evidence_source; '
        f'got {evidence_source!r}'
    )


# ---------------------------------------------------------------------------
# 9. Canonical telemetry query mirrors the Target Telemetry page filters
#    (evm_rpc / rpc_polling / live / block_number IS NOT NULL).
# ---------------------------------------------------------------------------

def test_canonical_telemetry_query_filters_match_target_telemetry_page():
    """The runtime summary must read the SAME telemetry_events rows the Target
    Telemetry page already surfaces: workspace-scoped, evidence_source='live',
    evm_rpc/rpc_polling, observed_at present, block_number present."""
    import pathlib
    src = (pathlib.Path(__file__).parents[1] / 'app' / 'monitoring_runner.py').read_text(encoding='utf-8')
    # Locate the canonical_last_telemetry_at query block by its anchor comment.
    anchor = src.find('canonical_last_telemetry_source = \'telemetry_events.observed_at\'')
    assert anchor != -1, 'canonical_last_telemetry_at query block not found'
    query_window = src[anchor:anchor + 1200]
    assert 'FROM telemetry_events' in query_window
    assert "evidence_source = 'live'" in query_window
    assert "event_type IN ('rpc_polling', 'live_provider')" in query_window
    assert "provider_type IN ('evm_rpc', 'live_provider')" in query_window
    assert 'observed_at IS NOT NULL' in query_window
    assert "payload_json->>'block_number'" in query_window, (
        'runtime summary query must require block_number to be present in payload_json '
        'so it counts only telemetry rows that proved chain reachability'
    )


def test_runtime_summary_query_excludes_rows_without_block_number(monkeypatch):
    """When telemetry_events rows lack block_number, MAX(observed_at) returns
    None and the runtime banner must report telemetry as unavailable."""

    class _NoBlockNumberConn(_BaseConn):
        def execute(self, q, p=None):
            qn = ' '.join(str(q).split())
            if 'FROM workspaces' in qn and 'slug' in qn:
                return _Result(row={'id': WORKSPACE_ID, 'slug': 'ws'})
            if (
                'FROM telemetry_events' in qn
                and 'MAX(observed_at) AS ts' in qn
                and "payload_json->>'block_number'" in qn
            ):
                # Block_number filter excludes rows without one -> query returns NULL.
                return _Result(row={'ts': None})
            if 'COUNT(*)' in qn or 'COUNT(' in qn:
                return _Result(row={'c': 0})
            if 'MAX(' in qn:
                return _Result(row={'ts': None})
            if 'SELECT' in qn:
                return _Result(rows=[], row={})
            return _Result(row={})

    payload = _get_payload(monkeypatch, _NoBlockNumberConn())
    assert payload.get('latest_live_telemetry_at') is None, (
        'latest_live_telemetry_at must be None when no telemetry_events rows carry block_number'
    )


# ---------------------------------------------------------------------------
# 10. LIMITED COVERAGE when telemetry exists but evidence chain is incomplete
# ---------------------------------------------------------------------------

def test_monitoring_status_is_limited_when_telemetry_exists_but_chain_incomplete(monkeypatch):
    """Telemetry rows alone must surface as LIMITED COVERAGE on the banner.
    monitoring_status must be 'limited' (not 'live' and not 'offline') so the
    frontend renders LIMITED COVERAGE rather than OFFLINE."""
    conn = _LiveTelemetryConn(telemetry_age_seconds=5)
    payload = _get_payload(monkeypatch, conn)

    summary = payload.get('workspace_monitoring_summary', {})
    monitoring_status = summary.get('monitoring_status') or payload.get('monitoring_status')
    assert monitoring_status == 'limited', (
        f'monitoring_status must be "limited" (LIMITED COVERAGE) when telemetry exists '
        f'but detection/alert/incident/response/evidence chain is incomplete; '
        f'got {monitoring_status!r}'
    )


def test_runtime_status_is_not_live_when_chain_incomplete(monkeypatch):
    """Even with recent live telemetry, runtime_status must NOT be 'live' (or
    'healthy') until the full evidence chain exists. Otherwise we claim full
    monitoring health on telemetry-only state."""
    conn = _LiveTelemetryConn(telemetry_age_seconds=5)
    payload = _get_payload(monkeypatch, conn)

    summary = payload.get('workspace_monitoring_summary', {})
    runtime_status = summary.get('runtime_status') or payload.get('runtime_status')
    assert runtime_status not in {'live', 'healthy'}, (
        f'runtime_status must not be "live"/"healthy" when evidence chain is '
        f'incomplete (telemetry only); got {runtime_status!r}'
    )


def test_reason_codes_flag_limited_coverage_when_chain_incomplete(monkeypatch):
    """The summary must publish a machine-readable reason code so the UI can
    explain LIMITED COVERAGE."""
    conn = _LiveTelemetryConn(telemetry_age_seconds=5)
    payload = _get_payload(monkeypatch, conn)

    summary = payload.get('workspace_monitoring_summary', {})
    reason_codes = summary.get('reason_codes') or []
    assert 'limited_coverage_evidence_chain_incomplete' in reason_codes, (
        f'reason_codes must include limited_coverage_evidence_chain_incomplete when '
        f'live telemetry exists but downstream chain is missing; got {reason_codes!r}'
    )


def test_runtime_status_recovers_to_non_offline_with_full_chain(monkeypatch):
    """When the full chain (telemetry → detection → alert → incident → response
    → evidence) is present, the LIMITED COVERAGE downgrade must NOT trigger,
    so runtime_status is free to escalate to live/healthy."""
    conn = _LiveTelemetryConn(
        telemetry_age_seconds=5,
        detections=1,
        alerts=1,
        incidents=1,
        response_actions=1,
        evidence=1,
    )
    payload = _get_payload(monkeypatch, conn)

    summary = payload.get('workspace_monitoring_summary', {})
    runtime_status = summary.get('runtime_status') or payload.get('runtime_status')
    monitoring_status = summary.get('monitoring_status') or payload.get('monitoring_status')
    reason_codes = summary.get('reason_codes') or []
    assert runtime_status != 'offline', (
        f'runtime_status must not be offline with full evidence chain; got {runtime_status!r}'
    )
    assert monitoring_status != 'offline', (
        f'monitoring_status must not be offline with full evidence chain; got {monitoring_status!r}'
    )
    assert 'limited_coverage_evidence_chain_incomplete' not in reason_codes, (
        f'limited_coverage reason code must NOT be set when chain is complete; got {reason_codes!r}'
    )
