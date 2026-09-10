"""Screen 9 — GET /exports/history (Export History tab).

Real export records, workspace-scoped, PAGED. What this pins down:

  * the listing is bounded — a client can never request an unbounded history,
  * only real evidence packages are listed (not legacy report/alert exports),
  * a package that was never verified reports NO verification status, never an
    optimistic one,
  * a package sealed before schema 2.0 reports no Merkle root rather than a
    placeholder,
  * signer metadata is truthful and never claims hardware backing,
  * download activity is queried only for the packages on the current page.
"""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

from services.api.app import pilot

WS = 'ws-history-1'


class _Result:
    def __init__(self, rows=None):
        self._rows = rows or []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _HistoryConnection:
    def __init__(self, *, rows, total=None, download_rows=None):
        self._rows = rows
        self._total = len(rows) if total is None else total
        self._download_rows = download_rows or []
        self.history_sql = ''
        self.history_params: tuple = ()
        self.download_params: tuple = ()

    def execute(self, stmt, params=None):
        normalized = ' '.join(str(stmt).split())
        if 'FROM workspace_role_permissions' in normalized:
            return _Result([])
        if 'COUNT(*) AS total FROM export_jobs' in normalized:
            return _Result([{'total': self._total}])
        if 'FROM export_jobs' in normalized:
            self.history_sql = normalized
            self.history_params = params or ()
            return _Result(self._rows)
        if 'FROM audit_logs' in normalized:
            self.download_params = params or ()
            return _Result(self._download_rows)
        raise AssertionError(f'unexpected query: {normalized!r}')

    def commit(self):
        pass


def _row(package_id: str, *, filters: dict | None = None, package_number: str | None = None) -> dict:
    return {
        'id': package_id,
        'export_type': 'proof_bundle',
        'status': 'completed',
        'storage_object_key': f'{WS}/{package_id}.json',
        'size_bytes': 4096,
        'package_number': package_number,
        'requested_by_user_id': 'user-1',
        'signing_key_id': 'decoda-evidence-prod-01',
        'signing_key_version': 'v3',
        'filters': filters if filters is not None else {},
        'created_at': '2026-02-01T10:00:00Z',
        'updated_at': '2026-02-01T10:00:05Z',
    }


SEALED_FILTERS = {
    'incident_id': 'inc-2026-017',
    'merkle_root': 'a' * 64,
    'hash_algorithm': 'SHA-256',
    'artifact_count': 37,
    'manifest_sha256': 'b' * 64,
    'integrity_status': 'verified',
    'signer': {
        'provider': 'aws_secrets_manager',
        'algorithm': 'HMAC-SHA256',
        'key_id': 'decoda-evidence-prod-01',
        'hardware_backed': False,
        'assurance': 'shared_secret_hmac',
    },
    'verification': {'verification_status': 'VERIFIED', 'verified_at': '2026-02-01T11:00:00Z', 'valid': True},
}


def _wire(monkeypatch, connection, *, role='admin'):
    @contextmanager
    def _pg():
        yield connection

    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _pg)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_: {'id': 'user-1'})
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda *_: {'workspace_id': WS, 'role': role})


def _request(**query) -> SimpleNamespace:
    return SimpleNamespace(headers={'x-workspace-id': WS}, query_params=query)


# ── Real records ─────────────────────────────────────────────────────────────

def test_history_returns_real_package_records(monkeypatch):
    connection = _HistoryConnection(rows=[_row('pkg-1', filters=SEALED_FILTERS, package_number='EV-2026-017')])
    _wire(monkeypatch, connection)

    body = pilot.list_evidence_export_history(_request())

    assert body['returned'] == 1
    entry = body['history'][0]
    assert entry['package_number'] == 'EV-2026-017'
    assert entry['incident_short_id'] == 'INC-inc-2026'
    assert entry['artifact_count'] == 37
    assert entry['hash_algorithm'] == 'SHA-256'
    assert entry['merkle_root'] == 'a' * 64
    assert entry['merkle_root_short'].startswith('aaaaaaaaaa')
    assert entry['verification_status'] == 'VERIFIED'
    assert entry['last_verified_at'] == '2026-02-01T11:00:00Z'


def test_only_evidence_package_export_types_are_listed(monkeypatch):
    connection = _HistoryConnection(rows=[])
    _wire(monkeypatch, connection)

    pilot.list_evidence_export_history(_request())

    assert "export_type IN ('proof_bundle', 'incident_report')" in connection.history_sql


def test_listing_is_workspace_scoped(monkeypatch):
    connection = _HistoryConnection(rows=[])
    _wire(monkeypatch, connection)

    pilot.list_evidence_export_history(_request())

    assert 'workspace_id = %s' in connection.history_sql
    assert connection.history_params[0] == WS


# ── Pagination is bounded ────────────────────────────────────────────────────

def test_history_is_paged_and_never_unbounded(monkeypatch):
    connection = _HistoryConnection(rows=[], total=500)
    _wire(monkeypatch, connection)

    body = pilot.list_evidence_export_history(_request())

    assert 'LIMIT %s OFFSET %s' in connection.history_sql
    assert body['limit'] == 25
    assert body['offset'] == 0
    assert body['total'] == 500
    assert body['has_more'] is True


def test_an_oversized_limit_is_clamped(monkeypatch):
    connection = _HistoryConnection(rows=[])
    _wire(monkeypatch, connection)

    body = pilot.list_evidence_export_history(_request(limit='100000'))

    assert body['limit'] == 100
    assert connection.history_params[1] == 100


def test_a_hostile_limit_or_offset_falls_back_to_defaults(monkeypatch):
    connection = _HistoryConnection(rows=[])
    _wire(monkeypatch, connection)

    body = pilot.list_evidence_export_history(_request(limit='DROP TABLE', offset='-5'))

    assert body['limit'] == 25
    assert body['offset'] == 0


def test_offset_paging_is_forwarded(monkeypatch):
    connection = _HistoryConnection(rows=[], total=90)
    _wire(monkeypatch, connection)

    body = pilot.list_evidence_export_history(_request(limit='10', offset='40'))

    assert connection.history_params[1:] == (10, 40)
    assert body['offset'] == 40


# ── Truthfulness ─────────────────────────────────────────────────────────────

def test_an_unverified_package_reports_no_verification_status(monkeypatch):
    """Never an optimistic initial state."""
    connection = _HistoryConnection(rows=[_row('pkg-2', filters={'incident_id': 'inc-9'})])
    _wire(monkeypatch, connection)

    entry = pilot.list_evidence_export_history(_request())['history'][0]

    assert entry['verification_status'] is None
    assert entry['last_verified_at'] is None


def test_a_package_sealed_before_schema_2_reports_no_merkle_root(monkeypatch):
    connection = _HistoryConnection(rows=[_row('pkg-3', filters={'incident_id': 'inc-9', 'manifest_sha256': 'c' * 64})])
    _wire(monkeypatch, connection)

    entry = pilot.list_evidence_export_history(_request())['history'][0]

    assert entry['merkle_root'] is None
    assert entry['merkle_root_short'] is None
    assert entry['hash_algorithm'] is None


def test_signer_metadata_never_claims_hardware_backing_it_does_not_have(monkeypatch):
    connection = _HistoryConnection(rows=[_row('pkg-4', filters=SEALED_FILTERS)])
    _wire(monkeypatch, connection)

    entry = pilot.list_evidence_export_history(_request())['history'][0]

    assert entry['signer_hardware_backed'] is False
    assert entry['signing_provider'] == 'aws_secrets_manager'
    assert entry['signature_algorithm'] == 'HMAC-SHA256'


def test_a_package_number_falls_back_to_a_stable_display_id(monkeypatch):
    connection = _HistoryConnection(rows=[_row('abcdef12-3456', filters={})])
    _wire(monkeypatch, connection)

    entry = pilot.list_evidence_export_history(_request())['history'][0]

    assert entry['package_number'] == 'EV-ABCDEF12'


def test_supersession_lineage_is_reported(monkeypatch):
    connection = _HistoryConnection(
        rows=[_row('pkg-5', filters={'incident_id': 'inc-9', 'supersedes_package_id': 'pkg-4'})]
    )
    _wire(monkeypatch, connection)

    entry = pilot.list_evidence_export_history(_request())['history'][0]

    assert entry['supersedes_package_id'] == 'pkg-4'


# ── Download activity ────────────────────────────────────────────────────────

def test_download_activity_is_queried_only_for_the_current_page(monkeypatch):
    connection = _HistoryConnection(
        rows=[_row('pkg-6', filters={}), _row('pkg-7', filters={})],
        download_rows=[{'entity_id': 'pkg-6', 'last_downloaded_at': '2026-02-02T09:00:00Z', 'download_count': 3}],
    )
    _wire(monkeypatch, connection)

    body = pilot.list_evidence_export_history(_request())

    assert connection.download_params[1] == ['pkg-6', 'pkg-7']
    by_id = {entry['package_id']: entry for entry in body['history']}
    assert by_id['pkg-6']['download_count'] == 3
    assert by_id['pkg-6']['last_downloaded_at'] == '2026-02-02T09:00:00Z'
    # Never downloaded is reported as zero, not as unknown.
    assert by_id['pkg-7']['download_count'] == 0
    assert by_id['pkg-7']['last_downloaded_at'] is None


def test_export_permission_is_reported_for_the_caller(monkeypatch):
    connection = _HistoryConnection(rows=[])
    _wire(monkeypatch, connection, role='viewer')

    assert pilot.list_evidence_export_history(_request())['can_export'] is False

    connection = _HistoryConnection(rows=[])
    _wire(monkeypatch, connection, role='admin')

    assert pilot.list_evidence_export_history(_request())['can_export'] is True
