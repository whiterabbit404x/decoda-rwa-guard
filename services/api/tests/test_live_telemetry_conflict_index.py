from __future__ import annotations

from datetime import datetime, timezone

from services.api.app import monitoring_runner
from services.api.app.activity_providers import ActivityProviderResult
from services.api.app.paid_launch_readiness import build_live_evidence_proof


class _Result:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class _CatalogConn:
    def __init__(self, *, index_exists: bool):
        self.index_exists = index_exists
        self.queries: list[str] = []

    def execute(self, query, params=None):
        normalized = ' '.join(str(query).split())
        self.queries.append(normalized)
        if 'SELECT EXISTS (' in normalized and 'pg_get_indexdef' in normalized:
            return _Result({'ok': self.index_exists})
        return _Result(None)


class _PersistConn:
    def __init__(self):
        self.telemetry_rows: dict[tuple[str, str, str], dict[str, object]] = {}
        self.telemetry_inserts_attempted = 0

    def execute(self, query, params=None):
        normalized = ' '.join(str(query).split())
        if 'INSERT INTO telemetry_events' in normalized:
            self.telemetry_inserts_attempted += 1
            key = (str(params[1]), str(params[3]), str(params[10]))
            self.telemetry_rows.setdefault(key, {'params': params})
            return _Result(None)
        return _Result(None)



def _live_provider_result(*, latest_block: int = 123, checkpoint: str | None = 'coverage:123') -> ActivityProviderResult:
    return ActivityProviderResult(
        mode='live',
        status='live',
        evidence_state='REAL_EVIDENCE',
        truthfulness_state='NOT_CLAIM_SAFE',
        synthetic=False,
        provider_name='evm_activity_provider',
        provider_kind='rpc',
        evidence_present=True,
        recent_real_event_count=0,
        last_real_event_at=None,
        events=[],
        latest_block=latest_block,
        checkpoint=checkpoint,
        checkpoint_age_seconds=None,
        degraded_reason=None,
        error_code=None,
        source_type='rpc_polling',
        reason_code='LIVE_PROVIDER_OK',
        claim_safe=False,
        detection_outcome='NO_EVIDENCE',
    )


def _target() -> dict[str, str]:
    return {
        'id': '00000000-0000-0000-0000-0000000000a1',
        'workspace_id': '00000000-0000-0000-0000-0000000000b2',
        'asset_id': '00000000-0000-0000-0000-0000000000c3',
        'chain_network': 'ethereum',
        'monitored_system_id': '00000000-0000-0000-0000-0000000000d4',
    }


def test_live_telemetry_partial_unique_index_catalog_guard_query_shape() -> None:
    conn = _CatalogConn(index_exists=True)

    assert monitoring_runner._telemetry_idempotency_index_guard(conn) is True

    catalog_query = ' '.join(conn.queries[-1].split())
    assert 'FROM pg_class c' in catalog_query
    assert "c.relname = 'telemetry_events'" in catalog_query
    assert 'i.indisunique = TRUE' in catalog_query
    assert 'workspace_id, target_id, idempotency_key' in catalog_query
    assert 'WHERE (idempotency_key IS NOT NULL)' in catalog_query


def test_persist_live_coverage_telemetry_idempotency_dedupes_without_crash() -> None:
    conn = _PersistConn()
    observed_at = datetime(2026, 5, 26, 10, 0, 0, tzinfo=timezone.utc)

    monitoring_runner._persist_live_coverage_telemetry(
        conn,
        target=_target(),
        provider_result=_live_provider_result(latest_block=456),
        observed_at=observed_at,
    )
    monitoring_runner._persist_live_coverage_telemetry(
        conn,
        target=_target(),
        provider_result=_live_provider_result(latest_block=456),
        observed_at=observed_at,
    )

    assert conn.telemetry_inserts_attempted == 2
    assert len(conn.telemetry_rows) == 1


def test_partial_unique_index_safety_allows_existing_null_duplicates() -> None:
    rows = [
        {'workspace_id': 'ws-1', 'target_id': 'target-1', 'idempotency_key': None},
        {'workspace_id': 'ws-1', 'target_id': 'target-1', 'idempotency_key': None},
        {'workspace_id': 'ws-1', 'target_id': 'target-1', 'idempotency_key': 'k-1'},
        {'workspace_id': 'ws-1', 'target_id': 'target-1', 'idempotency_key': 'k-2'},
    ]

    null_duplicates = sum(
        1
        for row in rows
        if row['workspace_id'] == 'ws-1' and row['target_id'] == 'target-1' and row['idempotency_key'] is None
    )
    non_null_keys = {
        row['idempotency_key']
        for row in rows
        if row['workspace_id'] == 'ws-1' and row['target_id'] == 'target-1' and row['idempotency_key'] is not None
    }

    assert null_duplicates == 2
    assert non_null_keys == {'k-1', 'k-2'}


def test_live_rpc_poll_persists_telemetry_and_updates_runtime_latest_timestamp() -> None:
    conn = _PersistConn()
    observed_at = datetime(2026, 5, 26, 10, 0, 0, tzinfo=timezone.utc)
    target = _target()

    monitoring_runner._persist_live_coverage_telemetry(
        conn,
        target=target,
        provider_result=_live_provider_result(latest_block=987),
        observed_at=observed_at,
    )

    latest_live_telemetry_at = max(v['params'][6] for v in conn.telemetry_rows.values())

    assert conn.telemetry_inserts_attempted == 1
    assert len(conn.telemetry_rows) == 1
    assert latest_live_telemetry_at == observed_at


def test_telemetry_only_does_not_imply_full_proof_chain_readiness() -> None:
    chain_evidence = {
        'provider_ready': True,
        'evidence_source': 'live',
        'source_type': 'rpc_polling',
        'latest_live_telemetry_at': '2026-05-26T10:00:00+00:00',
        'rpc_polling_telemetry_count': 1,
        'monitoring_checked_count': 1,
        'receipts_written': 1,
        'detections_count': 0,
        'alerts_count': 0,
        'incidents_count': 0,
        'response_actions_count': 0,
        'evidence_count': 0,
        'detection_telemetry_linked': False,
        'alert_detection_linked': False,
        'incident_alert_linked': False,
    }

    result = build_live_evidence_proof(chain_evidence=chain_evidence)

    assert result['live_telemetry_ready'] is False
    assert result['live_evidence_ready'] is False
