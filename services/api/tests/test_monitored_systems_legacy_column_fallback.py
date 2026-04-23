from __future__ import annotations

from services.api.app import pilot


class _LegacyCompatibleConnection:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, query: str, params: tuple[str, ...]):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError('column "last_coverage_telemetry_at" does not exist')

        class _Result:
            @staticmethod
            def fetchall():
                return [
                    {
                        'id': 'sys-1',
                        'workspace_id': params[0],
                        'asset_id': 'asset-1',
                        'target_id': 'target-1',
                        'chain': 'ethereum',
                        'is_enabled': True,
                        'runtime_status': 'healthy',
                        'status': 'active',
                        'last_heartbeat': None,
                        'last_event_at': None,
                        'last_coverage_telemetry_at': None,
                        'last_error_text': None,
                        'coverage_reason': None,
                        'freshness_status': 'fresh',
                        'confidence_status': 'high',
                        'created_at': None,
                        'monitoring_interval_seconds': 30,
                        'asset_name': 'Asset',
                        'target_name': 'Target',
                    }
                ]

        return _Result()


def test_list_workspace_monitored_system_rows_falls_back_for_legacy_schema():
    connection = _LegacyCompatibleConnection()

    rows = pilot.list_workspace_monitored_system_rows(connection, 'ws-1')

    assert len(rows) == 1
    assert rows[0]['id'] == 'sys-1'
    assert rows[0]['last_coverage_telemetry_at'] is None


class _FreshnessLegacyConnection:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, query: str, params: tuple[str, ...]):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError('column "freshness_status" does not exist')

        class _Result:
            @staticmethod
            def fetchall():
                return [
                    {
                        'id': 'sys-2',
                        'workspace_id': params[0],
                        'asset_id': 'asset-2',
                        'target_id': 'target-2',
                        'chain': 'ethereum',
                        'is_enabled': True,
                        'runtime_status': 'healthy',
                        'status': 'active',
                        'last_heartbeat': None,
                        'last_event_at': None,
                        'last_coverage_telemetry_at': None,
                        'last_error_text': None,
                        'coverage_reason': None,
                        'freshness_status': None,
                        'confidence_status': None,
                        'created_at': None,
                        'monitoring_interval_seconds': 30,
                        'asset_name': 'Asset',
                        'target_name': 'Target',
                    }
                ]

        return _Result()


def test_list_workspace_monitored_system_rows_falls_back_when_new_status_columns_missing():
    connection = _FreshnessLegacyConnection()

    rows = pilot.list_workspace_monitored_system_rows(connection, 'ws-legacy')

    assert len(rows) == 1
    assert rows[0]['id'] == 'sys-2'
    assert rows[0]['freshness_status'] is None
    assert rows[0]['confidence_status'] is None
